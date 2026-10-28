-- Finanzuebersicht, Phase 1
-- Neue Tabellen mit Praefix fin_ im Schema public. Die FuelBudget-Tabellen
-- (transactions, oil_change_settings) werden nicht angefasst.
--
-- Sicherheitsmodell:
--   1) Jede Zeile gehoert genau einem Nutzer (user_id), RLS gibt nur eigene Zeilen heraus.
--   2) Zusaetzlich eine RESTRICTIVE-Policy: Zugriff nur mit zweitem Faktor (aal2)
--      UND Freischaltung per app_metadata.finanzen = true (nur per SQL setzbar,
--      nicht vom Nutzer selbst).
--   3) Fremdschluessel ueber (id, user_id): Eine Buchung kann nie auf das Konto
--      eines anderen Nutzers zeigen.

-- Toepfe: wofuer das Geld da ist -------------------------------------------
create table public.fin_toepfe (
    id           uuid primary key default gen_random_uuid(),
    user_id      uuid not null default auth.uid() references auth.users on delete cascade,
    name         text not null check (length(trim(name)) between 1 and 60),
    art          text not null default 'ruecklage'
                 check (art in ('frei', 'ruecklage', 'puffer', 'investment')),
    zielbetrag   numeric(12, 2) check (zielbetrag is null or zielbetrag >= 0),
    sparrate_monat numeric(12, 2) check (sparrate_monat is null or sparrate_monat >= 0),
    sortierung   int not null default 0,
    aktiv        boolean not null default true,
    notiz        text,
    erstellt_am  timestamptz not null default now(),
    unique (id, user_id)
);

-- Konten: wo das Geld liegt ------------------------------------------------
create table public.fin_konten (
    id           uuid primary key default gen_random_uuid(),
    user_id      uuid not null default auth.uid() references auth.users on delete cascade,
    name         text not null check (length(trim(name)) between 1 and 60),
    institut     text,
    art          text not null default 'tagesgeld'
                 check (art in ('giro', 'tagesgeld', 'geldmarktfonds', 'depot', 'dividenden',
                                'p2p', 'bargeld', 'edelmetall', 'sonstiges')),
    ertragsart   text not null default 'keine'
                 check (ertragsart in ('zins', 'dividende', 'markt', 'keine')),
    risiko       smallint not null default 1 check (risiko between 1 and 5),
    standard_topf_id uuid,
    sortierung   int not null default 0,
    aktiv        boolean not null default true,
    notiz        text,
    erstellt_am  timestamptz not null default now(),
    unique (id, user_id),
    foreign key (standard_topf_id, user_id) references public.fin_toepfe (id, user_id)
        on delete set null (standard_topf_id)
);

-- Zinssaetze mit Historie (auch Ausschuettungs- bzw. erwartete Rendite) -----
create table public.fin_zinssaetze (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null default auth.uid() references auth.users on delete cascade,
    konto_id    uuid not null,
    gueltig_ab  date not null,
    satz_pa     numeric(7, 4) not null check (satz_pa between -100 and 100),
    notiz       text,
    unique (konto_id, gueltig_ab),
    foreign key (konto_id, user_id) references public.fin_konten (id, user_id) on delete cascade
);

-- Verbindlichkeiten (z. B. Geldbusse in Raten) -------------------------------
create table public.fin_verbindlichkeiten (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid not null default auth.uid() references auth.users on delete cascade,
    name          text not null check (length(trim(name)) between 1 and 60),
    betrag_gesamt numeric(12, 2) not null check (betrag_gesamt > 0),
    rate_monat    numeric(12, 2) check (rate_monat is null or rate_monat > 0),
    topf_id       uuid,
    erledigt      boolean not null default false,
    notiz         text,
    erstellt_am   timestamptz not null default now(),
    unique (id, user_id),
    foreign key (topf_id, user_id) references public.fin_toepfe (id, user_id)
        on delete set null (topf_id)
);

-- Buchungen: das Hauptbuch. Kontostand = Summe aller Buchungen des Kontos. ---
create table public.fin_buchungen (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null default auth.uid() references auth.users on delete cascade,
    datum       date not null,
    konto_id    uuid not null,
    topf_id     uuid,
    betrag      numeric(12, 2) not null check (betrag <> 0),
    typ         text not null
                check (typ in ('eroeffnung', 'einnahme', 'ausgabe', 'umbuchung',
                               'zins', 'ertrag', 'abgleich')),
    kategorie   text,
    beschreibung text,
    gruppe      uuid,           -- verbindet beide Seiten einer Umbuchung
    verbindlichkeit_id uuid,    -- Tilgung einer Verbindlichkeit
    erstellt_am timestamptz not null default now(),
    foreign key (konto_id, user_id) references public.fin_konten (id, user_id) on delete restrict,
    foreign key (topf_id, user_id) references public.fin_toepfe (id, user_id)
        on delete set null (topf_id),
    foreign key (verbindlichkeit_id, user_id) references public.fin_verbindlichkeiten (id, user_id)
        on delete set null (verbindlichkeit_id)
);

create index fin_buchungen_user_datum on public.fin_buchungen (user_id, datum);
create index fin_buchungen_konto on public.fin_buchungen (konto_id);
create index fin_buchungen_topf on public.fin_buchungen (topf_id);
create index fin_buchungen_gruppe on public.fin_buchungen (gruppe);
create index fin_buchungen_verbindlichkeit on public.fin_buchungen (verbindlichkeit_id);
create index fin_konten_user on public.fin_konten (user_id);
create index fin_konten_standard_topf on public.fin_konten (standard_topf_id);
create index fin_toepfe_user on public.fin_toepfe (user_id);
create index fin_zinssaetze_user on public.fin_zinssaetze (user_id);
create index fin_verbindlichkeiten_user on public.fin_verbindlichkeiten (user_id);
create index fin_verbindlichkeiten_topf on public.fin_verbindlichkeiten (topf_id);

-- Rechte & RLS ---------------------------------------------------------------
do $$
declare t text;
begin
    foreach t in array array['fin_toepfe', 'fin_konten', 'fin_zinssaetze',
                             'fin_verbindlichkeiten', 'fin_buchungen'] loop
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

-- Freischaltung eines Nutzers (einmalig, per SQL; nicht Teil der Migration):
-- update auth.users
--    set raw_app_meta_data = raw_app_meta_data || '{"finanzen": true}'
--  where email = '...';

-- Nachtrag: Indizes fuer die zusammengesetzten Fremdschluessel (Supabase-Advisor)
create index fin_buchungen_konto_user on public.fin_buchungen (konto_id, user_id);
create index fin_buchungen_topf_user on public.fin_buchungen (topf_id, user_id);
create index fin_buchungen_verbindlichkeit_user on public.fin_buchungen (verbindlichkeit_id, user_id);
create index fin_konten_standard_topf_user on public.fin_konten (standard_topf_id, user_id);
create index fin_verbindlichkeiten_topf_user on public.fin_verbindlichkeiten (topf_id, user_id);
create index fin_zinssaetze_konto_user on public.fin_zinssaetze (konto_id, user_id);
