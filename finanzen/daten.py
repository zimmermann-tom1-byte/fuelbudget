"""Datenzugriff der Finanzuebersicht ueber den Supabase-Client der Sitzung.

- Alle Abfragen laufen mit dem Login des Nutzers. Die Datenbank (RLS) gibt nur
  eigene Zeilen heraus und verlangt zusaetzlich den zweiten Faktor (aal2).
- Zwischenspeicher pro Browser-Sitzung (session_state), nie global, damit
  nichts zwischen Nutzern durchrutschen kann.
"""

from __future__ import annotations

import time

import pandas as pd
import streamlit as st

import anmeldung
from finanzen import berechnung as b

CACHE_SEKUNDEN = 600
_PREFIX = "_fin_cache_"

TABELLEN = {
    "konten": "fin_konten",
    "toepfe": "fin_toepfe",
    "zinssaetze": "fin_zinssaetze",
    "verbindlichkeiten": "fin_verbindlichkeiten",
    "buchungen": "fin_buchungen",
    "fixkosten": "fin_fixkosten",
    "depot_wochen": "fin_depot_wochen",
    "kurse": "fin_kurse",
}

_SPALTEN = {
    "konten": ["id", "name", "institut", "art", "ertragsart", "risiko", "standard_topf_id",
               "sortierung", "aktiv", "notiz", "stufe", "abgleich_gruppe", "bewertung", "wkn",
               "ertrag_ausgezahlt"],
    "toepfe": ["id", "name", "art", "zielbetrag", "sparrate_monat", "sortierung", "aktiv", "notiz",
               "konto_id", "stufe"],
    "zinssaetze": ["id", "konto_id", "gueltig_ab", "satz_pa", "notiz"],
    "verbindlichkeiten": ["id", "name", "betrag_gesamt", "rate_monat", "topf_id", "erledigt", "notiz"],
    "buchungen": ["id", "datum", "konto_id", "topf_id", "betrag", "typ", "kategorie",
                  "beschreibung", "gruppe", "verbindlichkeit_id", "erstellt_am", "tank_id", "anteile"],
    "fixkosten": ["id", "name", "betrag", "intervall_monate", "naechste_faelligkeit", "konto_id",
                  "kategorie", "aktiv", "notiz", "topf_id", "modus", "rate_monat", "rest_regel",
                  "rest_topf_id", "auto_abbuchen", "prognose_fuelbudget"],
    "depot_wochen": ["id", "konto_id", "datum", "performance_pct", "depotwert", "notiz"],
    "kurse": ["id", "konto_id", "datum", "kurs", "wert", "notiz"],
}


def _uid() -> str:
    return st.session_state["auth"]["user_id"]


def _laden(name: str) -> pd.DataFrame:
    sb = anmeldung.client()
    alle, start, seite = [], 0, 1000
    while True:  # PostgREST liefert hoechstens 1000 Zeilen pro Abfrage
        res = (sb.table(TABELLEN[name]).select("*").eq("user_id", _uid())
               .range(start, start + seite - 1).execute())
        alle.extend(res.data or [])
        if len(res.data or []) < seite:
            break
        start += seite
    df = pd.DataFrame(alle)
    for col in _SPALTEN[name]:
        if col not in df.columns:
            df[col] = pd.Series(dtype="object")
    return df


def laden(name: str) -> pd.DataFrame:
    key = _PREFIX + name
    eintrag = st.session_state.get(key)
    if eintrag and time.time() - eintrag[0] < CACHE_SEKUNDEN:
        return eintrag[1]
    df = _laden(name)
    if name in ("konten", "toepfe"):
        df["sortierung"] = pd.to_numeric(df["sortierung"], errors="coerce").fillna(0)
        df["aktiv"] = df["aktiv"].fillna(True).astype(bool)
        df = df.sort_values(["sortierung", "name"]).reset_index(drop=True)
    if name == "konten":
        df["risiko"] = pd.to_numeric(df["risiko"], errors="coerce").fillna(1).astype(int)
        df["stufe"] = df["stufe"].fillna("frei")
        df["bewertung"] = df["bewertung"].fillna("saldo")
        df["ertrag_ausgezahlt"] = df["ertrag_ausgezahlt"].fillna(False).astype(bool)
    if name == "toepfe":
        for col in ("zielbetrag", "sparrate_monat"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if name == "verbindlichkeiten":
        df["erledigt"] = df["erledigt"].fillna(False).astype(bool)
    if name == "zinssaetze":
        df = b.zinssaetze_normalisieren(df)
    if name == "fixkosten":
        df = b.fixkosten_normalisieren(df)
    if name == "kurse":
        df = b.kurse_normalisieren(df)
    if name == "depot_wochen":
        df["datum"] = pd.to_datetime(df["datum"]).dt.normalize()
        df["performance_pct"] = pd.to_numeric(df["performance_pct"], errors="coerce")
        df["depotwert"] = pd.to_numeric(df["depotwert"], errors="coerce")
        df = df.sort_values("datum").reset_index(drop=True)
    if name == "buchungen":
        df = b.buchungen_normalisieren(df).sort_values(["datum"], ascending=False).reset_index(drop=True)
    st.session_state[key] = (time.time(), df)
    return df


def alles() -> dict[str, pd.DataFrame]:
    return {name: laden(name) for name in TABELLEN}


def cache_leeren() -> None:
    for key in [k for k in st.session_state.keys() if str(k).startswith(_PREFIX)]:
        del st.session_state[key]


def _mit_uid(zeilen: list[dict] | dict) -> list[dict]:
    if isinstance(zeilen, dict):
        zeilen = [zeilen]
    return [{**z, "user_id": _uid()} for z in zeilen]


def einfuegen(name: str, zeilen: list[dict] | dict) -> list[dict]:
    """Fuegt ein und gibt die gespeicherten Zeilen (mit id) zurueck."""
    res = anmeldung.client().table(TABELLEN[name]).insert(_mit_uid(zeilen)).execute()
    cache_leeren()
    return res.data or []


def einfuegen_oder_ersetzen(name: str, zeile: dict, konflikt: str) -> None:
    """Upsert, z. B. Zinssatz je Posten und Datum (konflikt="konto_id,gueltig_ab")."""
    anmeldung.client().table(TABELLEN[name]).upsert(_mit_uid(zeile), on_conflict=konflikt).execute()
    cache_leeren()


# --- Konten und Toepfe -------------------------------------------------------------------
# Jeder Topf gehoert genau einem Konto. Ein neues Konto bekommt sofort einen ersten
# Topf (Standardtopf), damit es auch ohne weitere Aufteilung direkt nutzbar ist.

def konto_anlegen(werte: dict, erster_topf: dict) -> str:
    konto = einfuegen("konten", werte)[0]
    topf = einfuegen("toepfe", {"art": b.STUFE_TOPF_ART[erster_topf["stufe"]], **erster_topf,
                                "konto_id": konto["id"]})[0]
    aendern("konten", konto["id"], {"standard_topf_id": topf["id"]})
    return konto["id"]


def topf_anlegen(werte: dict) -> str:
    return einfuegen("toepfe", {"art": b.STUFE_TOPF_ART[werte["stufe"]], **werte})[0]["id"]


def konto_buchungen_speichern(zeilen: list[dict], kurse: pd.DataFrame, zinssaetze: pd.DataFrame,
                              konten: pd.DataFrame) -> None:
    """Buchungen speichern; bei Fonds-Konten die Anteile zum Tageskurs ergaenzen."""
    bewertung = konten.set_index("id")["bewertung"].to_dict()
    for z in zeilen:
        if bewertung.get(z["konto_id"]) == "anteile" and z.get("anteile") is None:
            kurs, _ = b.kurs_geschaetzt(kurse, zinssaetze, z["konto_id"], z["datum"])
            z["anteile"] = b.anteile_fuer(z["betrag"], kurs)
    einfuegen("buchungen", zeilen)


def aendern(name: str, zeilen_id: str, werte: dict) -> None:
    (anmeldung.client().table(TABELLEN[name]).update(werte)
     .eq("id", zeilen_id).eq("user_id", _uid()).execute())
    cache_leeren()


def loeschen(name: str, ids: list[str]) -> None:
    if not ids:
        return
    (anmeldung.client().table(TABELLEN[name]).delete()
     .in_("id", list(ids)).eq("user_id", _uid()).execute())
    cache_leeren()


def buchungsgruppe_loeschen(ids: list[str]) -> int:
    """Loescht Buchungen inkl. ihrer Gegenbuchung (gleiche Gruppe)."""
    df = laden("buchungen")
    auswahl = df[df["id"].isin(ids)]
    gruppen = [g for g in auswahl["gruppe"].dropna().unique()]
    alle_ids = set(auswahl["id"]) | set(df[df["gruppe"].isin(gruppen)]["id"])
    loeschen("buchungen", list(alle_ids))
    return len(alle_ids)


def fehlertext(exc: Exception) -> str:
    """Datenbankfehler in verstaendliche Saetze uebersetzen."""
    t = str(exc)
    meldung = getattr(exc, "message", None) or t
    if "Startbestand" in meldung:  # Pruefregel der Datenbank (Buchung vor dem Startbestand)
        return meldung
    if "23503" in t or "foreign key" in t.lower():
        return ("Das geht nicht, weil noch Buchungen darauf verweisen. "
                "Deaktiviere den Eintrag stattdessen.")
    if "23505" in t or "duplicate" in t.lower():
        return "Diesen Eintrag gibt es schon (z. B. zweiter Eintrag am selben Tag)."
    if "42501" in t or "row-level security" in t.lower():
        return "Keine Berechtigung. Bitte mit Bestätigungscode erneut anmelden."
    if "23514" in t or "check constraint" in t.lower():
        return "Ein Wert ist ungültig (z. B. leerer Name oder Betrag 0)."
    return f"Speichern fehlgeschlagen: {t}"


# --- Einstellungen (eine Zeile je Nutzer) -----------------------------------------------

def einstellungen() -> dict:
    key = _PREFIX + "einstellungen"
    eintrag = st.session_state.get(key)
    if eintrag and time.time() - eintrag[0] < CACHE_SEKUNDEN:
        return eintrag[1]
    res = (anmeldung.client().table("fin_einstellungen").select("*")
           .eq("user_id", _uid()).limit(1).execute())
    wert = (res.data or [{}])[0]
    st.session_state[key] = (time.time(), wert)
    return wert


def einstellungen_setzen(werte: dict) -> None:
    """z. B. tank_topf_id (Tankungen aus FuelBudget -> Topf; die Datenbank bucht
    beim Setzen offene Tankungen nach) oder lohn_topf_id (Quelle des Lohn-Assistenten)."""
    anmeldung.client().table("fin_einstellungen").upsert(
        {"user_id": _uid(), **werte}, on_conflict="user_id").execute()
    cache_leeren()


# --- FuelBudget-Prognose fuer das Kraftstoff-Budget -------------------------------------

def kraftstoff_prognose(heute) -> dict:
    """Monatsprognose aus den Tankungen (Tabelle transactions, wie FuelBudget)."""
    import forecast
    key = _PREFIX + "kraftstoff_prognose"
    eintrag = st.session_state.get(key)
    if eintrag and time.time() - eintrag[0] < CACHE_SEKUNDEN:
        return eintrag[1]
    res = (anmeldung.client().table("transactions").select("date,amount")
           .eq("user_id", _uid()).execute())
    df = pd.DataFrame(res.data or [])
    if df.empty:
        wert = {"monat": 0.0, "woche": 0.0, "label": "keine Tankungen"}
    else:
        df["date"] = pd.to_datetime(df["date"])
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
        wert = forecast.monatsprognose(df, pd.Timestamp(heute))
    st.session_state[key] = (time.time(), wert)
    return wert


# --- Automatische Lastschriften -------------------------------------------------------------

def lastschriften_buchen(d: dict, heute) -> int:
    """Bucht faellige automatische Lastschriften (z. B. Gym, Claude) und schiebt
    die Faelligkeit weiter. Wird beim Oeffnen der Finanzseiten ausgefuehrt."""
    faellig = b.faellige_abbuchungen(d["fixkosten"], heute)
    if not faellig:
        return 0
    u = b.ueberblick(d["konten"], d["toepfe"], d["buchungen"], d["zinssaetze"], d["verbindlichkeiten"],
                     heute, d["kurse"])
    werte = u.toepfe.set_index("topf_key")["wert"].to_dict()
    bewertung = d["konten"].set_index("id")["bewertung"].to_dict()
    neue_faelligkeit = {}
    zeilen = []
    for f, datum in faellig:
        if not isinstance(f.get("topf_id"), str):
            continue
        start = b.startdatum(d["buchungen"], f["konto_id"])
        if start is not None and datum < start:
            # vor dem Startbestand: ist dort schon enthalten -> nur Faelligkeit weiterschieben
            neue_faelligkeit[f["id"]] = b.monate_weiter(datum, f["intervall_monate"])
            continue
        kurs, _ = b.kurs_geschaetzt(d["kurse"], d["zinssaetze"], f["konto_id"], datum)
        neu = b.zahlung_buchen(f, f["betrag"], datum, werte.get(f["topf_id"], 0.0),
                               bewertung.get(f["konto_id"], "saldo"), kurs)
        for z in neu:
            z["beschreibung"] = f"{f['name']} (Lastschrift, automatisch)" if z["typ"] == "ausgabe" else z["beschreibung"]
            werte[z["topf_id"]] = werte.get(z["topf_id"], 0.0) + z["betrag"]
        zeilen.extend(neu)
        neue_faelligkeit[f["id"]] = b.monate_weiter(datum, f["intervall_monate"])
    if zeilen:
        einfuegen("buchungen", zeilen)
    for fid, datum in neue_faelligkeit.items():
        aendern("fixkosten", fid, {"naechste_faelligkeit": str(datum.date())})
    return len([z for z in zeilen if z["typ"] == "ausgabe"])


def nach_schreibzugriff(meldung: str) -> None:
    st.session_state["flash"] = meldung
    st.rerun()


def alles_aktuell(heute) -> dict[str, pd.DataFrame]:
    """Alle Daten laden und vorher (einmal pro Sitzung und Tag) faellige
    automatische Lastschriften buchen. Meldet gebuchte Lastschriften per Toast."""
    d = alles()
    marker = f"_lastschriften_{pd.Timestamp(heute).date()}"
    if not st.session_state.get(marker):
        st.session_state[marker] = True
        try:
            n = lastschriften_buchen(d, heute)
        except Exception as exc:
            st.warning(f"Automatische Lastschriften konnten nicht gebucht werden: {fehlertext(exc)}")
            n = 0
        if n:
            st.toast(f"{n} fällige Lastschrift(en) automatisch gebucht.", icon="🔁")
            d = alles()
    return d
