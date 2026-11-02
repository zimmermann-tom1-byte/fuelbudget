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
}

_SPALTEN = {
    "konten": ["id", "name", "institut", "art", "ertragsart", "risiko", "standard_topf_id",
               "sortierung", "aktiv", "notiz", "stufe", "abgleich_gruppe"],
    "toepfe": ["id", "name", "art", "zielbetrag", "sparrate_monat", "sortierung", "aktiv", "notiz"],
    "zinssaetze": ["id", "konto_id", "gueltig_ab", "satz_pa", "notiz"],
    "verbindlichkeiten": ["id", "name", "betrag_gesamt", "rate_monat", "topf_id", "erledigt", "notiz"],
    "buchungen": ["id", "datum", "konto_id", "topf_id", "betrag", "typ", "kategorie",
                  "beschreibung", "gruppe", "verbindlichkeit_id", "erstellt_am", "tank_id"],
    "fixkosten": ["id", "name", "betrag", "intervall_monate", "naechste_faelligkeit", "konto_id",
                  "kategorie", "aktiv", "notiz"],
    "depot_wochen": ["id", "konto_id", "datum", "performance_pct", "depotwert", "notiz"],
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
    if name == "toepfe":
        for col in ("zielbetrag", "sparrate_monat"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if name == "verbindlichkeiten":
        df["erledigt"] = df["erledigt"].fillna(False).astype(bool)
    if name == "zinssaetze":
        df = b.zinssaetze_normalisieren(df)
    if name == "fixkosten":
        df = b.fixkosten_normalisieren(df)
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


# --- Posten = Konto + eigener Topf -----------------------------------------------------
# Die App zeigt nur Posten. Damit die Daten mit dem frueheren Konto-/Topf-Modell
# kompatibel bleiben, bekommt jeder Posten im Hintergrund einen gleichnamigen
# Topf (Standardtopf), auf den alle seine Buchungen laufen.

def posten_anlegen(werte: dict, ziel: float | None) -> str:
    topf = einfuegen("toepfe", {
        "name": werte["name"], "art": b.STUFE_TOPF_ART[werte["stufe"]], "zielbetrag": ziel,
        "sortierung": werte.get("sortierung", 0), "aktiv": werte.get("aktiv", True),
    })[0]
    konto = einfuegen("konten", {**werte, "standard_topf_id": topf["id"]})[0]
    return konto["id"]


def posten_aendern(konto_id: str, topf_id: str | None, werte: dict, ziel: float | None) -> None:
    if topf_id:
        aendern("toepfe", topf_id, {"name": werte["name"], "zielbetrag": ziel,
                                    "art": b.STUFE_TOPF_ART[werte["stufe"]],
                                    "aktiv": werte.get("aktiv", True)})
        aendern("konten", konto_id, werte)
    else:  # Posten ohne Topf (z. B. aus dem frueheren Modell): Topf nachruesten
        topf = einfuegen("toepfe", {"name": werte["name"], "art": b.STUFE_TOPF_ART[werte["stufe"]],
                                    "zielbetrag": ziel, "aktiv": werte.get("aktiv", True)})[0]
        aendern("konten", konto_id, {**werte, "standard_topf_id": topf["id"]})


def posten_loeschen(konto_id: str, topf_id: str | None) -> None:
    loeschen("konten", [konto_id])  # scheitert bewusst, solange Buchungen existieren
    if topf_id:
        try:
            loeschen("toepfe", [topf_id])
        except Exception:
            pass  # Topf wird noch von alten Buchungen genutzt -> liegen lassen


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


def tank_posten_setzen(konto_id: str | None) -> None:
    """Posten, auf den Tankungen aus FuelBudget automatisch gebucht werden.
    Die Datenbank bucht beim Setzen alle offenen Tankungen ab dem Startbestand
    dieses Postens nach (Trigger)."""
    anmeldung.client().table("fin_einstellungen").upsert(
        {"user_id": _uid(), "tank_konto_id": konto_id}, on_conflict="user_id").execute()
    cache_leeren()


def nach_schreibzugriff(meldung: str) -> None:
    st.session_state["flash"] = meldung
    st.rerun()
