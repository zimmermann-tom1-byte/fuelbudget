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
}

_SPALTEN = {
    "konten": ["id", "name", "institut", "art", "ertragsart", "risiko", "standard_topf_id",
               "sortierung", "aktiv", "notiz"],
    "toepfe": ["id", "name", "art", "zielbetrag", "sparrate_monat", "sortierung", "aktiv", "notiz"],
    "zinssaetze": ["id", "konto_id", "gueltig_ab", "satz_pa", "notiz"],
    "verbindlichkeiten": ["id", "name", "betrag_gesamt", "rate_monat", "topf_id", "erledigt", "notiz"],
    "buchungen": ["id", "datum", "konto_id", "topf_id", "betrag", "typ", "kategorie",
                  "beschreibung", "gruppe", "verbindlichkeit_id", "erstellt_am"],
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
    if name == "toepfe":
        for col in ("zielbetrag", "sparrate_monat"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if name == "verbindlichkeiten":
        df["erledigt"] = df["erledigt"].fillna(False).astype(bool)
    if name == "zinssaetze":
        df = b.zinssaetze_normalisieren(df)
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


def einfuegen(name: str, zeilen: list[dict] | dict) -> None:
    anmeldung.client().table(TABELLEN[name]).insert(_mit_uid(zeilen)).execute()
    cache_leeren()


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
    if "23503" in t or "foreign key" in t.lower():
        return ("Das geht nicht, weil noch Buchungen darauf verweisen. "
                "Deaktiviere den Eintrag stattdessen.")
    if "23505" in t or "duplicate" in t.lower():
        return "Diesen Eintrag gibt es schon (z. B. zweiter Zinssatz am selben Tag)."
    if "42501" in t or "row-level security" in t.lower():
        return "Keine Berechtigung. Bitte mit Bestätigungscode erneut anmelden."
    if "23514" in t or "check constraint" in t.lower():
        return "Ein Wert ist ungültig (z. B. leerer Name oder Betrag 0)."
    return f"Speichern fehlgeschlagen: {t}"


def nach_schreibzugriff(meldung: str) -> None:
    st.session_state["flash"] = meldung
    st.rerun()
