-- Posten-Modell, Stufen, Fixkosten und Depot-Rendite (rein additiv).
--
-- Die App fasst Konto und Topf zu einem "Posten" zusammen. In der Datenbank
-- bleibt beides bestehen: Jeder Posten (fin_konten) hat weiterhin seinen
-- Standardtopf, und Buchungen tragen beide IDs. Dadurch funktioniert auch die
-- vorherige App-Version unveraendert mit denselben Daten (Rueckweg offen).

-- Stufe: wie verfuegbar ist das Geld?
alter table public.fin_konten
    add column stufe text not null default 'frei'
        check (stufe in ('frei', 'fixkosten', 'ruecklage', 'langfristig', 'verbindlichkeit')),
    -- Posten mit gleicher Gruppe liegen im selben Produkt (z. B. ein
    -- Geldmarktfonds) und werden gemeinsam abgeglichen.
    add column abgleich_gruppe text check (abgleich_gruppe is null or length(trim(abgleich_gruppe)) between 1 and 60);

-- Regelmaessige Fixkosten ------------------------------------------------------
create table public.fin_fixkosten (
    id                  uuid primary key default gen_random_uuid(),
    user_id             uuid not null default auth.uid() references auth.users on delete cascade,
    name                text not null check (length(trim(name)) between 1 and 60),
    betrag              numeric(12, 2) not null check (betrag > 0),
    intervall_monate    smallint not null default 1 check (intervall_monate in (1, 2, 3, 4, 6, 12)),
    naechste_faelligkeit date not null,
    konto_id            uuid,       -- Posten, aus dem bezahlt wird
    kategorie           text,
    aktiv               boolean not null default true,
    notiz               text,
    erstellt_am         timestamptz not null default now(),
    foreign key (konto_id, user_id) references public.fin_konten (id, user_id)
        on delete set null (konto_id)
);

-- Woechentliche Depot-Performance ------------------------------------------------
create table public.fin_depot_wochen (
    id              uuid primary key default gen_random_uuid(),
    user_id         uuid not null default auth.uid() references auth.users on delete cascade,
    konto_id        uuid not null,
    datum           date not null,
    performance_pct numeric(8, 4) not null check (performance_pct > -100 and performance_pct < 1000),
    depotwert       numeric(14, 2) check (depotwert is null or depotwert >= 0),
    notiz           text,
    erstellt_am     timestamptz not null default now(),
    unique (konto_id, datum),
    foreign key (konto_id, user_id) references public.fin_konten (id, user_id) on delete cascade
);

create index fin_fixkosten_user on public.fin_fixkosten (user_id);
create index fin_fixkosten_konto_user on public.fin_fixkosten (konto_id, user_id);
create index fin_depot_wochen_user on public.fin_depot_wochen (user_id);
create index fin_depot_wochen_konto_user on public.fin_depot_wochen (konto_id, user_id);

-- Gleiches Sicherheitsmodell wie die uebrigen fin_* Tabellen
do $$
declare t text;
begin
    foreach t in array array['fin_fixkosten', 'fin_depot_wochen'] loop
        execute format('alter table public.%I enable row level security', t);
        execute format('revoke all on public.%I from anon', t);
        execute format('grant select, insert, update, delete on public.%I to authenticated', t);
        execute format($p$
            create policy "Eigene Zeilen" on public.%I
            for all to authenticated
            using ((select auth.uid()) = user_id)
            with check ((select auth.uid()) = user_id)
        $p$, t);
        execute format($p$
            create policy "Nur mit 2FA und Freischaltung" on public.%I
            as restrictive
            for all to authenticated
            using (
                (select auth.jwt() ->> 'aal') = 'aal2'
                and coalesce(((select auth.jwt()) -> 'app_metadata' ->> 'finanzen')::boolean, false)
            )
            with check (
                (select auth.jwt() ->> 'aal') = 'aal2'
                and coalesce(((select auth.jwt()) -> 'app_metadata' ->> 'finanzen')::boolean, false)
            )
        $p$, t);
    end loop;
end $$;

-- Sicherung vor dem Umbau liegt im nicht oeffentlichen Schema "sicherung"
-- (Tabellen *_20261101). Wiederherstellen z. B.:
--   delete from public.fin_buchungen; insert into public.fin_buchungen select * from sicherung.fin_buchungen_20261101;
