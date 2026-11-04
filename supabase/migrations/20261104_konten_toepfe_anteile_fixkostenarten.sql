-- Konten und Toepfe klar getrennt, Anteilsrechnung fuer Fonds, Fixkosten-Arten
--
-- Modell:
--   Konto = Ort (Volksbank-Fixkostenkonto, T212 Ausgabentopf, Geldmarktfonds, Brieftasche ...)
--   Topf  = Zweck innerhalb genau eines Kontos (KFZ-Versicherung, Gym, Geldbusse ...)
--   Die Stufe (frei, Fixkosten, ...) haengt am Topf.
-- Fonds-Konten (bewertung = 'anteile'): Jede Buchung kauft/verkauft rechnerisch
-- Anteile zum Kurs des Tages. Wert eines Topfes = Anteile x aktueller Kurs.
-- So verteilen sich Buchgewinne exakt auf die Toepfe, und der sich aendernde
-- Einstandskurs ist automatisch beruecksichtigt.

alter table public.fin_toepfe
    add column konto_id uuid,
    add column stufe text check (stufe in ('frei', 'fixkosten', 'ruecklage', 'langfristig', 'verbindlichkeit')),
    add foreign key (konto_id, user_id) references public.fin_konten (id, user_id) on delete cascade;
create index fin_toepfe_konto_user on public.fin_toepfe (konto_id, user_id);

alter table public.fin_konten
    add column bewertung text not null default 'saldo' check (bewertung in ('saldo', 'anteile')),
    add column wkn text check (wkn is null or length(wkn) between 1 and 20),
    -- Werden Zinsen/Ausschuettungen tatsaechlich ausgezahlt (true) oder bleiben sie als Buchgewinn liegen?
    add column ertrag_ausgezahlt boolean not null default false;

alter table public.fin_buchungen
    add column anteile numeric(18, 8);

-- Kurse bzw. Gesamtwert eines Fonds-Kontos zu einem Datum (manuell, z. B. Monatsanfang)
create table public.fin_kurse (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null default auth.uid() references auth.users on delete cascade,
    konto_id    uuid not null,
    datum       date not null,
    kurs        numeric(14, 6) not null check (kurs > 0),
    wert        numeric(14, 2),
    notiz       text,
    erstellt_am timestamptz not null default now(),
    unique (konto_id, datum),
    foreign key (konto_id, user_id) references public.fin_konten (id, user_id) on delete cascade
);
create index fin_kurse_user on public.fin_kurse (user_id);
create index fin_kurse_konto_user on public.fin_kurse (konto_id, user_id);

-- Fixkosten-Arten
--   direkt:   monatlich einzahlen, wird abgebucht (Volksbank, Ausgabentopf, Bargeld)
--   ansparen: Rate pro Monat ansparen, bei Faelligkeit auszahlen (Geldmarktfonds)
--   budget:   monatlicher Betrag, wird nach und nach verbraucht (Kraftstoff)
-- rest_regel:
--   rest_topf:  Ueberschuss (Aufrundung, Zinsen, guenstigere Rechnung) geht in den Rest-Topf
--               (Sicherheitsmarge bzw. "Ueberschuss & Zinsen"); fehlt etwas, kommt es von dort
--   verrechnen: Differenz bleibt im Topf und verringert die naechste Einzahlung (ESN, Kraftstoff)
alter table public.fin_fixkosten
    add column topf_id uuid,
    add column modus text not null default 'direkt' check (modus in ('direkt', 'ansparen', 'budget')),
    add column rate_monat numeric(12, 2) check (rate_monat is null or rate_monat > 0),
    add column rest_regel text not null default 'rest_topf' check (rest_regel in ('rest_topf', 'verrechnen')),
    add column rest_topf_id uuid,
    add column auto_abbuchen boolean not null default false,
    add column prognose_fuelbudget boolean not null default false,
    add foreign key (topf_id, user_id) references public.fin_toepfe (id, user_id) on delete set null (topf_id),
    add foreign key (rest_topf_id, user_id) references public.fin_toepfe (id, user_id) on delete set null (rest_topf_id);
create index fin_fixkosten_topf on public.fin_fixkosten (topf_id, user_id);
create index fin_fixkosten_rest_topf on public.fin_fixkosten (rest_topf_id, user_id);

alter table public.fin_einstellungen
    add column tank_topf_id uuid,
    add column lohn_topf_id uuid,
    add column lohn_assistent_seit date,
    add foreign key (tank_topf_id, user_id) references public.fin_toepfe (id, user_id) on delete set null (tank_topf_id),
    add foreign key (lohn_topf_id, user_id) references public.fin_toepfe (id, user_id) on delete set null (lohn_topf_id);
create index fin_einstellungen_tank_topf on public.fin_einstellungen (tank_topf_id, user_id);
create index fin_einstellungen_lohn_topf on public.fin_einstellungen (lohn_topf_id, user_id);

-- RLS fuer fin_kurse wie bei den uebrigen fin_* Tabellen
alter table public.fin_kurse enable row level security;
revoke all on public.fin_kurse from anon;
grant select, insert, update, delete on public.fin_kurse to authenticated;
create policy "Eigene Zeilen" on public.fin_kurse
    for all to authenticated
    using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy "Nur mit 2FA und Freischaltung" on public.fin_kurse
    as restrictive for all to authenticated
    using ((select auth.jwt() ->> 'aal') = 'aal2'
           and coalesce(((select auth.jwt()) -> 'app_metadata' ->> 'finanzen')::boolean, false))
    with check ((select auth.jwt() ->> 'aal') = 'aal2'
                and coalesce(((select auth.jwt()) -> 'app_metadata' ->> 'finanzen')::boolean, false));

-- Pruefregel erweitert: Topf muss zum Konto der Buchung gehoeren
create or replace function intern.fin_buchung_datum_pruefen()
returns trigger language plpgsql set search_path = '' as $$
declare
    start date;
    erste date;
    topf_konto uuid;
begin
    if new.topf_id is not null then
        select konto_id into topf_konto from public.fin_toepfe where id = new.topf_id;
        if topf_konto is not null and topf_konto <> new.konto_id then
            raise exception 'Der Topf gehört zu einem anderen Konto als die Buchung.';
        end if;
    end if;
    if new.typ = 'eroeffnung' then
        select min(datum) into erste from public.fin_buchungen
         where konto_id = new.konto_id and typ <> 'eroeffnung' and id <> new.id;
        if erste is not null and new.datum > erste then
            raise exception 'Der Startbestand (%) liegt nach der ersten Buchung dieses Kontos (%).',
                to_char(new.datum, 'DD.MM.YYYY'), to_char(erste, 'DD.MM.YYYY');
        end if;
    else
        select min(datum) into start from public.fin_buchungen
         where konto_id = new.konto_id and typ = 'eroeffnung' and id <> new.id;
        if start is not null and new.datum < start then
            raise exception 'Die Buchung vom % liegt vor dem Startbestand dieses Kontos (%). Alles davor ist im Startbestand schon enthalten.',
                to_char(new.datum, 'DD.MM.YYYY'), to_char(start, 'DD.MM.YYYY');
        end if;
    end if;
    return new;
end $$;

create or replace trigger fin_buchungen_datum_pruefen
    before insert or update of datum, typ, konto_id, topf_id on public.fin_buchungen
    for each row execute function intern.fin_buchung_datum_pruefen();

-- Tankungen: Ziel ist jetzt ein Topf (z. B. Kraftstoff im T212 Ausgabentopf).
-- Bei Fonds-Konten werden die Anteile zum letzten Kurs mitgebucht.
create or replace function intern.tankungen_buchen(p_user uuid, p_tank uuid default null)
returns integer language plpgsql security definer set search_path = '' as $$
declare
    k uuid;
    t uuid;
    start date;
    n integer := 0;
begin
    select tp.konto_id, tp.id into k, t
      from public.fin_einstellungen e join public.fin_toepfe tp on tp.id = e.tank_topf_id
     where e.user_id = p_user and tp.user_id = p_user;
    if k is null then  -- frueheres Modell: nur Konto gewaehlt
        select e.tank_konto_id, kk.standard_topf_id into k, t
          from public.fin_einstellungen e join public.fin_konten kk on kk.id = e.tank_konto_id
         where e.user_id = p_user;
    end if;
    if k is null then
        return 0;
    end if;
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

create or replace trigger fin_einstellungen_tankungen_nachbuchen
    after insert or update of tank_konto_id, tank_topf_id on public.fin_einstellungen
    for each row execute function intern.tank_einstellung_trigger();

create or replace function intern.tank_einstellung_trigger()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
    if new.tank_topf_id is not null or new.tank_konto_id is not null then
        perform intern.tankungen_buchen(new.user_id, null);
    end if;
    return new;
end $$;
