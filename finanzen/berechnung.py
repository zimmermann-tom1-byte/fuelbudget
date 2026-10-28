"""Rechenlogik der Finanzuebersicht – ohne Streamlit und ohne Datenbank,
damit sie sich mit Unit-Tests pruefen laesst.

Grundidee (Hauptbuch):
- Jede Geldbewegung ist eine Buchung mit Konto (wo liegt das Geld) und
  optional Topf (wofuer ist es da). Ohne Topf = frei verfuegbar.
- Kontostand = Summe aller Buchungen des Kontos. Topfstand = Summe aller
  Buchungen des Topfes. Konto x Topf = Summe je Kombination.
- Zinsen, die seit der letzten Zins-/Abgleichsbuchung aufgelaufen sind, werden
  taeglich geschaetzt (Saldo x Satz / 365) und erst beim naechsten Abgleich als
  echte Buchung festgeschrieben.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import numpy as np
import pandas as pd

TAGE_PRO_JAHR = 365

# Buchungstypen, nach denen die Zinsschaetzung neu beginnt: Ab hier ist der
# Kontostand durch eine echte Zahl bestaetigt.
ZINS_RESET_TYPEN = {"eroeffnung", "zins", "ertrag", "abgleich"}

KONTO_ARTEN = {
    "giro": "Girokonto",
    "tagesgeld": "Tagesgeld / Sparkonto",
    "geldmarktfonds": "Geldmarktfonds",
    "depot": "Depot (ETF/Aktien)",
    "dividenden": "Dividenden-Depot",
    "p2p": "P2P / Kreditplattform",
    "bargeld": "Bargeld",
    "edelmetall": "Edelmetall",
    "sonstiges": "Sonstiges",
}

ERTRAGSARTEN = {
    "zins": "Zinsen (fest/variabel)",
    "dividende": "Ausschüttungen",
    "markt": "Kursentwicklung",
    "keine": "Kein Ertrag",
}

TOPF_ARTEN = {
    "frei": "Frei verfügbar",
    "ruecklage": "Rücklage (zweckgebunden)",
    "puffer": "Puffer / Notgroschen",
    "investment": "Investment",
}

RISIKO_STUFEN = {
    1: "1 – Einlagensicherung / Bargeld",
    2: "2 – Geldmarkt, sehr gering",
    3: "3 – Breit gestreute ETFs",
    4: "4 – Einzeltitel, Edelmetall",
    5: "5 – Ohne Sicherung (z. B. P2P)",
}

AUSGABE_KATEGORIEN = [
    "Lebensmittel", "Freizeit", "Auto", "Wohnen", "Abos & Verträge", "Versicherungen",
    "Gesundheit", "Kleidung", "Bildung", "Geschenke", "Investment", "Sonstiges",
]
EINNAHME_KATEGORIEN = ["Gehalt", "Zinsen & Dividenden", "Erstattung", "Verkauf", "Sonstiges"]

FREI = "frei"  # Platzhalter fuer "kein Topf" in Tabellen


# --- Hilfen ---------------------------------------------------------------------

def _datum(x) -> pd.Timestamp:
    return pd.Timestamp(x).normalize()


def leere_buchungen() -> pd.DataFrame:
    return pd.DataFrame({
        "id": pd.Series(dtype="object"), "datum": pd.Series(dtype="datetime64[ns]"),
        "konto_id": pd.Series(dtype="object"), "topf_id": pd.Series(dtype="object"),
        "betrag": pd.Series(dtype="float"), "typ": pd.Series(dtype="object"),
        "kategorie": pd.Series(dtype="object"), "beschreibung": pd.Series(dtype="object"),
        "gruppe": pd.Series(dtype="object"), "verbindlichkeit_id": pd.Series(dtype="object"),
    })


def buchungen_normalisieren(df: pd.DataFrame) -> pd.DataFrame:
    """Typen vereinheitlichen (Supabase liefert Zahlen als String/Decimal)."""
    basis = leere_buchungen()
    if df is None or df.empty:
        return basis
    df = df.copy()
    for col in basis.columns:
        if col not in df.columns:
            df[col] = None
    df["datum"] = pd.to_datetime(df["datum"]).dt.normalize()
    df["betrag"] = pd.to_numeric(df["betrag"], errors="coerce").fillna(0.0).astype(float)
    df["topf_id"] = df["topf_id"].where(df["topf_id"].notna(), None)
    return df


def zinssaetze_normalisieren(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame({"konto_id": pd.Series(dtype="object"),
                             "gueltig_ab": pd.Series(dtype="datetime64[ns]"),
                             "satz_pa": pd.Series(dtype="float")})
    df = df.copy()
    df["gueltig_ab"] = pd.to_datetime(df["gueltig_ab"]).dt.normalize()
    df["satz_pa"] = pd.to_numeric(df["satz_pa"], errors="coerce").fillna(0.0).astype(float)
    return df.sort_values(["konto_id", "gueltig_ab"])


# --- Zinssaetze -------------------------------------------------------------------

def satz_am(zinssaetze: pd.DataFrame, konto_id, datum) -> float:
    """Gueltiger Satz (in % p. a.) eines Kontos an einem Tag. Vor dem ersten
    hinterlegten Satz: 0."""
    z = zinssaetze[zinssaetze["konto_id"] == konto_id]
    z = z[z["gueltig_ab"] <= _datum(datum)]
    return float(z["satz_pa"].iloc[-1]) if not z.empty else 0.0


def satz_reihe(zinssaetze: pd.DataFrame, konto_id, tage: pd.DatetimeIndex) -> pd.Series:
    """Satz (% p. a.) fuer jeden Tag in `tage`."""
    z = zinssaetze[zinssaetze["konto_id"] == konto_id]
    if z.empty or len(tage) == 0:
        return pd.Series(0.0, index=tage)
    s = z.set_index("gueltig_ab")["satz_pa"]
    s = s[~s.index.duplicated(keep="last")]
    return s.reindex(s.index.union(tage)).sort_index().ffill().reindex(tage).fillna(0.0)


# --- Salden ---------------------------------------------------------------------

def saldo_verlauf(buchungen: pd.DataFrame, bis, von=None) -> pd.DataFrame:
    """Taeglicher gebuchter Saldo je Konto (Zeilen: Tage, Spalten: konto_id),
    jeweils am Tagesende. Ohne geschaetzte Zinsen."""
    bis = _datum(bis)
    if buchungen.empty:
        return pd.DataFrame(index=pd.DatetimeIndex([], name="datum"))
    start = _datum(von) if von is not None else buchungen["datum"].min()
    start = min(start, bis)
    tage = pd.date_range(start, bis, freq="D", name="datum")
    taeglich = buchungen.pivot_table(index="datum", columns="konto_id", values="betrag",
                                     aggfunc="sum", fill_value=0.0)
    # Buchungen vor dem Startdatum als Anfangsbestand mitnehmen
    vorher = taeglich[taeglich.index < start].sum()
    taeglich = taeglich.reindex(taeglich.index.union(tage), fill_value=0.0).sort_index()
    taeglich = taeglich[(taeglich.index >= start) & (taeglich.index <= bis)]
    verlauf = taeglich.cumsum() + vorher
    return verlauf.reindex(tage).ffill().fillna(0.0)


def gebuchte_salden(buchungen: pd.DataFrame, stichtag) -> pd.Series:
    """Gebuchter Saldo je Konto am Ende des Stichtags."""
    b = buchungen[buchungen["datum"] <= _datum(stichtag)]
    return b.groupby("konto_id")["betrag"].sum()


def aufgelaufene_zinsen(buchungen: pd.DataFrame, konten: pd.DataFrame,
                        zinssaetze: pd.DataFrame, heute) -> pd.Series:
    """Geschaetzte, noch nicht gebuchte Zinsen/Ausschuettungen je Konto.

    Fuer jeden Tag nach der letzten bestaetigten Buchung (Eroeffnung, Zins,
    Ertrag, Abgleich) bis einschliesslich gestern: Tagesend-Saldo x Satz / 365.
    Heute zaehlt noch nicht, weil der Tag nicht vorbei ist."""
    heute = _datum(heute)
    ergebnis = {}
    for _, k in konten.iterrows():
        if k["ertragsart"] not in ("zins", "dividende"):
            continue
        kb = buchungen[buchungen["konto_id"] == k["id"]]
        if kb.empty:
            continue
        resets = kb[kb["typ"].isin(ZINS_RESET_TYPEN) & (kb["datum"] <= heute)]
        ab = resets["datum"].max() if not resets.empty else kb["datum"].min()
        tage = pd.date_range(ab, heute - pd.Timedelta(days=1), freq="D")
        if len(tage) == 0:
            ergebnis[k["id"]] = 0.0
            continue
        saldo = saldo_verlauf(kb, tage[-1], von=tage[0])[k["id"]].reindex(tage).ffill().fillna(0.0)
        saetze = satz_reihe(zinssaetze, k["id"], tage)
        ergebnis[k["id"]] = float((saldo.clip(lower=0) * saetze / 100 / TAGE_PRO_JAHR).sum())
    return pd.Series(ergebnis, dtype=float)


# --- Ueberblick ---------------------------------------------------------------------

@dataclass
class Ueberblick:
    konten: pd.DataFrame        # je Konto: Salden, Satz, Tagesertrag
    toepfe: pd.DataFrame        # je Topf (inkl. "frei"): Stand, Ziel
    matrix: pd.DataFrame        # Konto x Topf
    brutto: float
    verbindlichkeiten_offen: float
    netto: float
    frei: float
    zweckgebunden: float
    ertrag_tag_zins: float      # Zinsen, recht sicher
    ertrag_tag_dividende: float  # erwartete Ausschuettungen
    ertrag_tag_markt: float     # erwartete Kursrendite (Annahme)


def verbindlichkeiten_offen(verbindlichkeiten: pd.DataFrame, buchungen: pd.DataFrame) -> pd.DataFrame:
    """Offener Betrag je Verbindlichkeit = Gesamtbetrag - Tilgungen (Ausgaben
    mit Verweis auf die Verbindlichkeit)."""
    if verbindlichkeiten is None or verbindlichkeiten.empty:
        return pd.DataFrame(columns=["id", "name", "betrag_gesamt", "getilgt", "offen", "erledigt"])
    v = verbindlichkeiten.copy()
    v["betrag_gesamt"] = pd.to_numeric(v["betrag_gesamt"], errors="coerce").fillna(0.0)
    tilg = (-buchungen[buchungen["verbindlichkeit_id"].notna()]
            .groupby("verbindlichkeit_id")["betrag"].sum())
    v["getilgt"] = v["id"].map(tilg).fillna(0.0)
    v["offen"] = (v["betrag_gesamt"] - v["getilgt"]).clip(lower=0)
    v.loc[v["erledigt"].fillna(False).astype(bool), "offen"] = 0.0
    return v


def ueberblick(konten: pd.DataFrame, toepfe: pd.DataFrame, buchungen: pd.DataFrame,
               zinssaetze: pd.DataFrame, verbindlichkeiten: pd.DataFrame, heute) -> Ueberblick:
    heute = _datum(heute)
    b = buchungen[buchungen["datum"] <= heute]

    gebucht = gebuchte_salden(b, heute)
    zinsen = aufgelaufene_zinsen(b, konten, zinssaetze, heute)

    k = konten.copy()
    k["gebucht"] = k["id"].map(gebucht).fillna(0.0)
    k["aufgelaufen"] = k["id"].map(zinsen).fillna(0.0)
    k["saldo"] = k["gebucht"] + k["aufgelaufen"]
    k["satz_pa"] = [satz_am(zinssaetze, kid, heute) for kid in k["id"]]
    k["ertrag_tag"] = k["saldo"].clip(lower=0) * k["satz_pa"] / 100 / TAGE_PRO_JAHR
    letzte_bestaetigung = (b[b["typ"].isin(ZINS_RESET_TYPEN)].groupby("konto_id")["datum"].max())
    k["bestaetigt_am"] = k["id"].map(letzte_bestaetigung)

    # Konto x Topf: gebuchte Betraege + aufgelaufene Zinsen in den Standardtopf
    m = b.assign(topf=b["topf_id"].fillna(FREI)).groupby(["konto_id", "topf"])["betrag"].sum()
    for _, row in k[k["aufgelaufen"] != 0].iterrows():
        ziel = row["standard_topf_id"] if pd.notna(row["standard_topf_id"]) else FREI
        m.loc[(row["id"], ziel)] = m.get((row["id"], ziel), 0.0) + row["aufgelaufen"]
    matrix = m.unstack(fill_value=0.0) if not m.empty else pd.DataFrame()

    topf_staende = matrix.sum(axis=0) if not matrix.empty else pd.Series(dtype=float)
    t = toepfe.copy()
    t["stand"] = t["id"].map(topf_staende).fillna(0.0)
    frei_ohne_topf = float(topf_staende.get(FREI, 0.0))
    frei_toepfe = float(t.loc[t["art"] == "frei", "stand"].sum())
    frei = frei_ohne_topf + frei_toepfe

    v = verbindlichkeiten_offen(verbindlichkeiten, b)
    offen = float(v["offen"].sum()) if not v.empty else 0.0
    brutto = float(k["saldo"].sum())

    def ertrag(art):
        return float(k.loc[k["ertragsart"] == art, "ertrag_tag"].sum())

    return Ueberblick(
        konten=k, toepfe=t, matrix=matrix, brutto=brutto, verbindlichkeiten_offen=offen,
        netto=brutto - offen, frei=frei, zweckgebunden=brutto - frei,
        ertrag_tag_zins=ertrag("zins"), ertrag_tag_dividende=ertrag("dividende"),
        ertrag_tag_markt=ertrag("markt"),
    )


def ertrag_verlauf(buchungen: pd.DataFrame, konten: pd.DataFrame, zinssaetze: pd.DataFrame,
                   bis) -> pd.DataFrame:
    """Taeglicher (erwarteter) Ertrag je Ertragsart auf Basis des gebuchten
    Saldos: zeigt, wie das passive Einkommen pro Tag waechst."""
    verlauf = saldo_verlauf(buchungen, bis)
    if verlauf.empty:
        return pd.DataFrame()
    spalten = {}
    for _, k in konten.iterrows():
        if k["id"] not in verlauf.columns or k["ertragsart"] == "keine":
            continue
        saetze = satz_reihe(zinssaetze, k["id"], verlauf.index)
        tag = verlauf[k["id"]].clip(lower=0) * saetze / 100 / TAGE_PRO_JAHR
        spalten.setdefault(k["ertragsart"], []).append(tag)
    return pd.DataFrame({art: sum(reihen) for art, reihen in spalten.items()}, index=verlauf.index)


# --- Buchungen erzeugen ---------------------------------------------------------------

def _rund(x: float) -> float:
    return float(np.round(x + 0.0, 2))


def umbuchung(datum, von_konto, von_topf, nach_konto, nach_topf, betrag: float,
              beschreibung: str | None = None) -> list[dict]:
    """Zwei Gegenbuchungen mit gemeinsamer Gruppe. Gleiches Konto, anderer Topf
    = Umtopfen (Geld bleibt liegen, bekommt nur einen anderen Zweck)."""
    if betrag <= 0:
        raise ValueError("Betrag muss größer als 0 sein.")
    if von_konto == nach_konto and (von_topf or None) == (nach_topf or None):
        raise ValueError("Quelle und Ziel sind identisch.")
    gruppe = str(uuid.uuid4())
    basis = {"datum": str(_datum(datum).date()), "typ": "umbuchung", "gruppe": gruppe,
             "beschreibung": beschreibung or None}
    return [
        {**basis, "konto_id": von_konto, "topf_id": von_topf or None, "betrag": -_rund(betrag)},
        {**basis, "konto_id": nach_konto, "topf_id": nach_topf or None, "betrag": _rund(betrag)},
    ]


def abgleich(konto: dict | pd.Series, ist_saldo: float, gebuchter_saldo: float, datum) -> dict | None:
    """Buchung, die den gebuchten Saldo auf den tatsaechlichen Kontostand
    bringt. Bei Zinskonten ist die Differenz Zinsertrag, sonst Wertaenderung.
    Gebucht wird in den Standardtopf des Kontos. None, wenn alles stimmt
    (die Buchung setzt die Zinsschaetzung trotzdem zurueck -> dann eine
    0-Buchung waere ungueltig; Aufrufer legt in dem Fall nichts an)."""
    diff = _rund(ist_saldo - gebuchter_saldo)
    if abs(diff) < 0.005:
        return None
    typ = "zins" if konto["ertragsart"] == "zins" and diff > 0 else "abgleich"
    topf = konto.get("standard_topf_id") if hasattr(konto, "get") else None
    if topf is not None and pd.isna(topf):
        topf = None
    return {"datum": str(_datum(datum).date()), "konto_id": konto["id"], "topf_id": topf,
            "betrag": diff, "typ": typ,
            "beschreibung": f"Abgleich auf {ist_saldo:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")}


def eur(x: float, nachkomma: int = 2) -> str:
    """Deutsche Schreibweise: 1.234,56 €"""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "–"
    s = f"{x:,.{nachkomma}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".") + " €"
