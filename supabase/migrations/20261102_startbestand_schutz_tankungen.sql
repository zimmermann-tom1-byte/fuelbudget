-- 1) Schutz: keine Buchung vor dem Startbestand eines Postens
--
-- Ursache des Fehlers vom 01.11.: Ein Depot-Abgleich mit Datum 30.10. lag vor
-- dem Startbestand (01.11.). Zu diesem Datum war der gebuchte Saldo 0, also
-- wurde der volle Depotwert als "Differenz" gebucht und ab dem 01.11. zusaetzlich
-- zum Startbestand gezaehlt (Altersvorsorge doppelt).
-- Der Startbestand ist der absolute Anker eines Postens. Alles davor ist
-- bereits in ihm enthalten. Diese Regel prueft die Datenbank jetzt selbst,
-- unabhaengig davon, von welcher Seite die Buchung kommt.

create schema if not exists intern;
revoke all on schema intern from public, anon, authenticated;

create or replace function intern.fin_buchung_datum_pruefen()
returns trigger language plpgsql set search_path = '' as $$
declare
    start date;
    erste date;
begin
    if new.typ = 'eroeffnung' then
        select min(datum) into erste from public.fin_buchungen
         where konto_id = new.konto_id and typ <> 'eroeffnung' and id <> new.id;
        if erste is not null and new.datum > erste then
            raise exception 'Der Startbestand (%) liegt nach der ersten Buchung dieses Postens (%).',
                to_char(new.datum, 'DD.MM.YYYY'), to_char(erste, 'DD.MM.YYYY');
        end if;
    else
        select min(datum) into start from public.fin_buchungen
         where konto_id = new.konto_id and typ = 'eroeffnung' and id <> new.id;
        if start is not null and new.datum < start then
            raise exception 'Die Buchung vom % liegt vor dem Startbestand dieses Postens (%). Alles davor ist im Startbestand schon enthalten.',
                to_char(new.datum, 'DD.MM.YYYY'), to_char(start, 'DD.MM.YYYY');
        end if;
    end if;
    return new;
end $$;

create trigger fin_buchungen_datum_pruefen
    before insert or update of datum, typ, konto_id on public.fin_buchungen
    for each row execute function intern.fin_buchung_datum_pruefen();


-- 2) Tankungen aus FuelBudget automatisch als Ausgabe buchen
--
-- fin_einstellungen.tank_konto_id: Posten, von dem Tankungen bezahlt werden.
-- Ohne Einstellung passiert nichts (z. B. fuer Kollegen ohne Finanzteil).
-- Verknuepfung ueber fin_buchungen.tank_id: Tankung geaendert -> Buchung
-- geaendert, Tankung geloescht -> Buchung geloescht (on delete cascade).

alter table public.fin_buchungen
    add column tank_id uuid unique references public.transactions (id) on delete cascade;

create table public.fin_einstellungen (
    user_id       uuid primary key default auth.uid() references auth.users on delete cascade,
    tank_konto_id uuid,
    geaendert_am  timestamptz not null default now(),
    foreign key (tank_konto_id, user_id) references public.fin_konten (id, user_id)
        on delete set null (tank_konto_id)
);
create index fin_einstellungen_tank_konto on public.fin_einstellungen (tank_konto_id, user_id);

alter table public.fin_einstellungen enable row level security;
revoke all on public.fin_einstellungen from anon;
grant select, insert, update, delete on public.fin_einstellungen to authenticated;
create policy "Eigene Zeilen" on public.fin_einstellungen
    for all to authenticated
    using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "Nur mit 2FA und Freischaltung" on public.fin_einstellungen
    as restrictive for all to authenticated
    using ((select auth.jwt() ->> 'aal') = 'aal2'
           and coalesce(((select auth.jwt()) -> 'app_metadata' ->> 'finanzen')::boolean, false))
    with check ((select auth.jwt() ->> 'aal') = 'aal2'
                and coalesce(((select auth.jwt()) -> 'app_metadata' ->> 'finanzen')::boolean, false));

-- Bucht eine Tankung (bzw. alle offenen eines Nutzers) auf den Tank-Posten.
-- Laeuft mit den Rechten des Erstellers, weil FuelBudget ohne zweiten Faktor
-- arbeitet und die Finanztabellen sonst nicht beschreiben duerfte. Sie schreibt
-- ausschliesslich Zeilen fuer den Nutzer, dem die Tankung gehoert, und nur auf
-- dessen eigenen, selbst gewaehlten Posten. Fehler werden abgefangen: Das
-- Speichern einer Tankung darf daran nie scheitern.
create or replace function intern.tankungen_buchen(p_user uuid, p_tank uuid default null)
returns integer language plpgsql security definer set search_path = '' as $$
declare
    k uuid;
    t uuid;
    start date;
    n integer := 0;
begin
    select e.tank_konto_id into k from public.fin_einstellungen e where e.user_id = p_user;
    if k is null then
        return 0;
    end if;
    select standard_topf_id into t from public.fin_konten where id = k and user_id = p_user;
    select min(datum) into start from public.fin_buchungen where konto_id = k and typ = 'eroeffnung';

    insert into public.fin_buchungen (user_id, datum, konto_id, topf_id, betrag, typ, kategorie, beschreibung, tank_id)
    select tx.user_id, tx.date, k, t, -round(tx.amount, 2), 'ausgabe', 'Auto',
           'Tankung (FuelBudget)' || coalesce(' · ' || tx.location, ''), tx.id
      from public.transactions tx
     where tx.user_id = p_user
       and (p_tank is null or tx.id = p_tank)
       and tx.amount > 0
       and (start is null or tx.date >= start)
       and not exists (select 1 from public.fin_buchungen fb where fb.tank_id = tx.id)
    on conflict (tank_id) do nothing;
    get diagnostics n = row_count;
    return n;
end $$;
revoke all on function intern.tankungen_buchen(uuid, uuid) from public, anon, authenticated;

create or replace function intern.tankung_trigger()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
    begin
        if tg_op = 'UPDATE' then
            update public.fin_buchungen
               set datum = new.date, betrag = -round(new.amount, 2),
                   beschreibung = 'Tankung (FuelBudget)' || coalesce(' · ' || new.location, '')
             where tank_id = new.id and new.amount > 0;
            if found then
                return new;
            end if;
        end if;
        perform intern.tankungen_buchen(new.user_id, new.id);
    exception when others then
        raise warning 'Tankung % nicht in Finanzen gebucht: %', new.id, sqlerrm;
    end;
    return new;
end $$;
revoke all on function intern.tankung_trigger() from public, anon, authenticated;

create trigger transactions_in_finanzen
    after insert or update of date, amount, location on public.transactions
    for each row execute function intern.tankung_trigger();

-- Wird der Tank-Posten gesetzt oder geaendert: offene Tankungen ab dessen
-- Startbestand nachbuchen.
create or replace function intern.tank_einstellung_trigger()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
    if new.tank_konto_id is not null then
        perform intern.tankungen_buchen(new.user_id, null);
    end if;
    return new;
end $$;
revoke all on function intern.tank_einstellung_trigger() from public, anon, authenticated;

create trigger fin_einstellungen_tankungen_nachbuchen
    after insert or update of tank_konto_id on public.fin_einstellungen
    for each row execute function intern.tank_einstellung_trigger();
