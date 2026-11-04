"""Rechenlogik der Finanzuebersicht – ohne Streamlit und ohne Datenbank,
damit sie sich mit Unit-Tests pruefen laesst.

Grundidee (Hauptbuch):
- Konto = Ort, an dem Geld liegt (Volksbank-Fixkostenkonto, T212 Ausgabentopf,
  Geldmarktfonds, Brieftasche ...).
- Topf = Zweck innerhalb genau eines Kontos (KFZ-Versicherung, Gym, Geldbusse ...).
  Die Stufe (frei, Fixkosten, Ruecklage, langfristig, Verbindlichkeit) haengt am Topf.
- Jede Geldbewegung ist eine Buchung auf Konto + Topf. Einstand eines Topfes =
  Summe seiner Buchungen.
- Konten mit Bewertung "saldo": Wert = Einstand + geschaetzte, noch nicht
  gebuchte Zinsen.
- Konten mit Bewertung "anteile" (Fonds): Jede Buchung kauft/verkauft Anteile
  zum Kurs des Tages. Wert eines Topfes = Anteile x aktueller Kurs; die Differenz
  zum Einstand ist der Buchgewinn.
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

STUFEN = {  # Reihenfolge = von "sofort verfuegbar" bis "gebunden"
    "frei": "Frei verfügbar",
    "fixkosten": "Fixkosten reserviert",
    "ruecklage": "Rücklagen & Puffer",
    "langfristig": "Langfristig (nur im Notfall)",
    "verbindlichkeit": "Für Verbindlichkeiten",
}
STUFE_ERKLAERUNG = {
    "frei": "Kann ausgegeben werden, ohne dass ein Plan wackelt.",
    "fixkosten": "Für regelmäßige Kosten zurückgelegt (monatlich und alle paar Monate).",
    "ruecklage": "Zweckgebunden, aber kurzfristig erreichbar (Auto, Strafzettel, Notgroschen).",
    "langfristig": "Angelegt und gedanklich abgeschrieben, nur im absoluten Notfall.",
    "verbindlichkeit": "Liegt für eine Schuld bereit (z. B. Geldbuße).",
}
STUFE_FARBEN = {
    "frei": "#34d399", "fixkosten": "#38bdf8", "ruecklage": "#f59e0b",
    "langfristig": "#a78bfa", "verbindlichkeit": "#f87171",
}
# Art des automatisch angelegten Topfes je Stufe (nur fuer die Kompatibilitaet
# mit dem frueheren Konto-/Topf-Modell)
STUFE_TOPF_ART = {
    "frei": "frei", "fixkosten": "ruecklage", "ruecklage": "puffer",
    "langfristig": "investment", "verbindlichkeit": "ruecklage",
}

INTERVALLE = {1: "monatlich", 2: "alle 2 Monate", 3: "vierteljährlich", 4: "alle 4 Monate",
              6: "halbjährlich", 12: "jährlich"}

# Ab so vielen Wochen wird die gemessene Depot-Rendite automatisch als
# erwartete Rendite des Postens uebernommen. Darunter ist die Hochrechnung
# auf ein Jahr zu zufaellig (eine gute Woche = scheinbar +200 % p. a.).
MIN_WOCHEN_RENDITE = 12
RENDITE_FENSTER_WOCHEN = 52

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
        "tank_id": pd.Series(dtype="object"), "anteile": pd.Series(dtype="float"),
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
    df["anteile"] = pd.to_numeric(df["anteile"], errors="coerce")
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


def kurse_normalisieren(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame({"konto_id": pd.Series(dtype="object"), "datum": pd.Series(dtype="datetime64[ns]"),
                             "kurs": pd.Series(dtype="float"), "wert": pd.Series(dtype="float")})
    df = df.copy()
    df["datum"] = pd.to_datetime(df["datum"]).dt.normalize()
    df["kurs"] = pd.to_numeric(df["kurs"], errors="coerce")
    df["wert"] = pd.to_numeric(df.get("wert"), errors="coerce")
    return df.sort_values(["konto_id", "datum"]).reset_index(drop=True)


def kurs_geschaetzt(kurse: pd.DataFrame, zinssaetze: pd.DataFrame, konto_id, datum) -> tuple[float | None, pd.Timestamp | None]:
    """Kurs eines Fonds-Kontos an einem Tag: letzter eingetragener Kurs,
    seitdem mit dem hinterlegten Satz fortgeschrieben (Geldmarktfonds wachsen
    sehr gleichmaessig). Liefert (Kurs, Datum des letzten echten Kurses)."""
    datum = _datum(datum)
    k = kurse[(kurse["konto_id"] == konto_id) & (kurse["datum"] <= datum)]
    if k.empty:
        k = kurse[kurse["konto_id"] == konto_id]   # nur spaetere Kurse: den ersten nehmen
        if k.empty:
            return None, None
        return float(k["kurs"].iloc[0]), k["datum"].iloc[0]
    kurs, ab = float(k["kurs"].iloc[-1]), k["datum"].iloc[-1]
    tage = (datum - ab).days
    if tage > 0:
        kurs *= (1 + satz_am(zinssaetze, konto_id, datum) / 100) ** (tage / TAGE_PRO_JAHR)
    return kurs, ab


def anteile_fuer(betrag: float, kurs: float | None) -> float | None:
    return None if not kurs else round(float(betrag) / kurs, 8)


def topf_salden(konten: pd.DataFrame, toepfe: pd.DataFrame, buchungen: pd.DataFrame,
                zinssaetze: pd.DataFrame, kurse: pd.DataFrame, heute) -> pd.DataFrame:
    """Je Topf: Einstand, Anteile, Wert, Buchgewinn/aufgelaufene Zinsen, Ertrag pro Tag.
    Buchungen ohne Topf erscheinen als Pseudo-Topf "<konto_id>:ohne"."""
    heute = _datum(heute)
    b = buchungen[buchungen["datum"] <= heute].copy()
    b["topf_key"] = [t if isinstance(t, str) else f"{k}:ohne" for t, k in zip(b["topf_id"], b["konto_id"])]
    g = b.groupby(["konto_id", "topf_key"]).agg(einstand=("betrag", "sum"), anteile=("anteile", "sum")).reset_index()

    # alle (auch leeren) Toepfe aufnehmen
    tp = toepfe[toepfe["konto_id"].notna()] if "konto_id" in toepfe.columns else toepfe.iloc[0:0]
    leer = tp[~tp["id"].isin(g["topf_key"])]
    g = pd.concat([g, pd.DataFrame({"konto_id": leer["konto_id"], "topf_key": leer["id"],
                                    "einstand": 0.0, "anteile": 0.0})], ignore_index=True)

    kinfo = konten.set_index("id")
    saldo_konten = konten[konten.get("bewertung", pd.Series("saldo", index=konten.index)).fillna("saldo") != "anteile"]
    zinsen = aufgelaufene_zinsen(b, saldo_konten, zinssaetze, heute)

    werte, auf, kurs_info = [], [], []
    for _, r in g.iterrows():
        kid = r["konto_id"]
        bewertung = kinfo.at[kid, "bewertung"] if kid in kinfo.index and "bewertung" in kinfo.columns else "saldo"
        if bewertung == "anteile":
            kurs, ab = kurs_geschaetzt(kurse, zinssaetze, kid, heute)
            wert = r["anteile"] * kurs if kurs else r["einstand"]
            werte.append(wert)
            auf.append(wert - r["einstand"])
            kurs_info.append(ab)
        else:
            z = float(zinsen.get(kid, 0.0))
            einst_k = g.loc[g["konto_id"] == kid, "einstand"].clip(lower=0)
            anteil = (max(r["einstand"], 0) / einst_k.sum()) if einst_k.sum() > 0 else 0.0
            werte.append(r["einstand"] + z * anteil)
            auf.append(z * anteil)
            kurs_info.append(None)
    g["wert"], g["aufgelaufen"], g["kurs_stand"] = werte, auf, kurs_info

    tinfo = toepfe.set_index("id") if not toepfe.empty else pd.DataFrame()
    def t_attr(key, spalte, default=None):
        if key in tinfo.index and spalte in tinfo.columns:
            v = tinfo.at[key, spalte]
            return default if v is None or (isinstance(v, float) and np.isnan(v)) else v
        return default

    g["topf_id"] = [k if not k.endswith(":ohne") else None for k in g["topf_key"]]
    g["topf_name"] = [t_attr(k, "name", "ohne Topf") for k in g["topf_key"]]
    g["stufe"] = [t_attr(k, "stufe") or (kinfo.at[kid, "stufe"] if kid in kinfo.index and "stufe" in kinfo.columns else "frei") or "frei"
                  for k, kid in zip(g["topf_key"], g["konto_id"])]
    g["ziel"] = pd.to_numeric(pd.Series([t_attr(k, "zielbetrag") for k in g["topf_key"]]), errors="coerce")
    g["topf_aktiv"] = [bool(t_attr(k, "aktiv", True)) for k in g["topf_key"]]
    for spalte, default in (("name", ""), ("institut", None), ("art", "sonstiges"), ("ertragsart", "keine"),
                            ("risiko", 1), ("ertrag_ausgezahlt", False), ("bewertung", "saldo"), ("aktiv", True)):
        g["konto_" + spalte if spalte in ("name", "aktiv") else spalte] = [
            (kinfo.at[kid, spalte] if kid in kinfo.index and spalte in kinfo.columns else default) for kid in g["konto_id"]]
    g["ertrag_ausgezahlt"] = g["ertrag_ausgezahlt"].fillna(False).astype(bool)
    g["satz_pa"] = [satz_am(zinssaetze, kid, heute) for kid in g["konto_id"]]
    g["ertrag_tag"] = g["wert"].clip(lower=0) * g["satz_pa"] / 100 / TAGE_PRO_JAHR
    return g


@dataclass
class Ueberblick:
    toepfe: pd.DataFrame        # je Topf (siehe topf_salden)
    konten: pd.DataFrame        # je Konto: Wert, Einstand, Satz, Ertrag/Tag, Bestaetigt am
    stufen: pd.Series           # Summe je Stufe (Reihenfolge wie STUFEN)
    brutto: float
    verbindlichkeiten_offen: float
    netto: float
    frei: float
    ertrag_tag_zins: float      # Zinsen (ausgezahlt + Buchgewinne der Geldmarktfonds)
    ertrag_tag_dividende: float  # erwartete Ausschuettungen
    ertrag_tag_markt: float     # erwartete Kursrendite (Depot)
    ausgezahlt_tag_prognose: float   # davon tatsaechlich ausgezahlt (nach heutigem Bestand)
    ausgezahlt_30_tage: float        # tatsaechlich gebuchte Auszahlungen der letzten 30 Tage


def ueberblick(konten: pd.DataFrame, toepfe: pd.DataFrame, buchungen: pd.DataFrame, zinssaetze: pd.DataFrame,
               verbindlichkeiten: pd.DataFrame | None, heute, kurse: pd.DataFrame | None = None) -> Ueberblick:
    heute = _datum(heute)
    kurse = kurse_normalisieren(kurse) if kurse is None or "kurs" not in kurse.columns else kurse
    t = topf_salden(konten, toepfe, buchungen, zinssaetze, kurse, heute)

    b = buchungen[buchungen["datum"] <= heute]
    letzte_bestaetigung = b[b["typ"].isin(ZINS_RESET_TYPEN)].groupby("konto_id")["datum"].max()
    k = t.groupby("konto_id").agg(wert=("wert", "sum"), einstand=("einstand", "sum"),
                                  aufgelaufen=("aufgelaufen", "sum"), ertrag_tag=("ertrag_tag", "sum")).reset_index()
    kinfo = konten.set_index("id")
    for spalte in ("name", "institut", "art", "ertragsart", "risiko", "bewertung", "ertrag_ausgezahlt", "aktiv",
                   "wkn", "standard_topf_id"):
        k[spalte] = [kinfo.at[kid, spalte] if kid in kinfo.index and spalte in kinfo.columns else None for kid in k["konto_id"]]
    k["satz_pa"] = [satz_am(zinssaetze, kid, heute) for kid in k["konto_id"]]
    k["bestaetigt_am"] = pd.to_datetime(k["konto_id"].map(letzte_bestaetigung.to_dict()))
    kurs_stand = t.groupby("konto_id")["kurs_stand"].max()
    k["kurs_stand"] = pd.to_datetime(k["konto_id"].map(kurs_stand.to_dict()))
    k["ertrag_ausgezahlt"] = k["ertrag_ausgezahlt"].fillna(False).astype(bool)

    stufen = t.groupby("stufe")["wert"].sum().reindex(list(STUFEN), fill_value=0.0)
    v = verbindlichkeiten_offen(verbindlichkeiten, b)
    offen = float(v["offen"].sum()) if not v.empty else 0.0
    brutto = float(t["wert"].sum())

    def ertrag(art, nur_ausgezahlt=False):
        m = k["ertragsart"] == art
        if nur_ausgezahlt:
            m &= k["ertrag_ausgezahlt"]
        return float(k.loc[m, "ertrag_tag"].sum())

    ausgezahlt_konten = set(k.loc[k["ertrag_ausgezahlt"], "konto_id"])
    letzte = b[(b["datum"] > heute - pd.Timedelta(days=30)) & b["typ"].isin(["zins", "ertrag"])
               & b["konto_id"].isin(ausgezahlt_konten) & (b["betrag"] > 0)]

    return Ueberblick(
        toepfe=t, konten=k, stufen=stufen, brutto=brutto, verbindlichkeiten_offen=offen,
        netto=brutto - offen, frei=float(stufen["frei"]),
        ertrag_tag_zins=ertrag("zins"), ertrag_tag_dividende=ertrag("dividende"), ertrag_tag_markt=ertrag("markt"),
        ausgezahlt_tag_prognose=ertrag("zins", True) + ertrag("dividende", True),
        ausgezahlt_30_tage=float(letzte["betrag"].sum()),
    )


def saldo_verlauf_je(buchungen: pd.DataFrame, spalte: str, bis) -> pd.DataFrame:
    """Wie saldo_verlauf, aber je beliebiger Spalte (z. B. topf_id)."""
    b = buchungen.copy()
    b["konto_id"] = b[spalte].fillna("ohne")
    return saldo_verlauf(b, bis)


def stufen_verlauf(buchungen: pd.DataFrame, konten: pd.DataFrame, toepfe: pd.DataFrame, bis) -> pd.DataFrame:
    """Taeglicher gebuchter Einstand je Stufe (Stufe kommt vom Topf)."""
    if buchungen.empty:
        return pd.DataFrame(index=pd.DatetimeIndex([], name="datum"))
    stufe_topf = toepfe.set_index("id")["stufe"].to_dict() if "stufe" in toepfe.columns else {}
    stufe_konto = konten.set_index("id")["stufe"].to_dict() if "stufe" in konten.columns else {}
    b = buchungen.copy()
    b["stufe"] = [stufe_topf.get(t) or stufe_konto.get(k) or "frei" for t, k in zip(b["topf_id"], b["konto_id"])]
    verlauf = saldo_verlauf_je(b, "stufe", bis)
    return verlauf.reindex(columns=[s for s in STUFEN if s in verlauf.columns])


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


def gruppen_abgleich(posten: list[dict], ist_gesamt: float, datum) -> list[dict]:
    """Abgleich mehrerer Posten, die im selben Produkt liegen (z. B. ein
    Geldmarktfonds fuer Geldbusse und Strafzettel). Die Differenz zum echten
    Gesamtstand wird im Verhaeltnis der gebuchten Salden verteilt.
    posten: [{"id", "ertragsart", "standard_topf_id", "gebucht"}, ...]"""
    if not posten:
        return []
    gebucht = np.array([float(p["gebucht"]) for p in posten])
    diff = _rund(ist_gesamt - gebucht.sum())
    if abs(diff) < 0.005:
        return []
    gewichte = gebucht.clip(min=0)
    gewichte = gewichte / gewichte.sum() if gewichte.sum() > 0 else np.full(len(posten), 1 / len(posten))
    anteile = [_rund(diff * g) for g in gewichte[:-1]]
    anteile.append(_rund(diff - sum(anteile)))  # Rundungsrest an den letzten Posten
    zeilen = []
    for p, anteil, geb in zip(posten, anteile, gebucht):
        z = abgleich(p, geb + anteil, geb, datum)
        if z is not None:
            zeilen.append(z)
    return zeilen


def vor_startbestand(buchungen: pd.DataFrame) -> pd.DataFrame:
    """Buchungen, die vor dem Startbestand ihres Postens liegen.

    Der Startbestand ist der Anker eines Postens: Alles davor ist schon in ihm
    enthalten. Eine Buchung davor zaehlt doppelt (so geschehen mit einem
    Depot-Abgleich vom 30.10. bei Startbestand 01.11.). Die Datenbank laesst
    solche Buchungen inzwischen nicht mehr zu; diese Pruefung findet Altfaelle."""
    if buchungen.empty:
        return buchungen
    start = buchungen[buchungen["typ"] == "eroeffnung"].groupby("konto_id")["datum"].min()
    b = buchungen[buchungen["typ"] != "eroeffnung"]
    grenze = b["konto_id"].map(start.to_dict())
    return b[grenze.notna() & (b["datum"] < pd.to_datetime(grenze))]


def startdatum(buchungen: pd.DataFrame, konto_id) -> pd.Timestamp | None:
    """Datum des Startbestands eines Postens (None, wenn keiner gebucht ist)."""
    e = buchungen[(buchungen["konto_id"] == konto_id) & (buchungen["typ"] == "eroeffnung")]
    return e["datum"].min() if not e.empty else None


# --- Fixkosten ------------------------------------------------------------------------

FIXKOSTEN_MODI = {
    "direkt": "Direkt (monatlich einzahlen, wird abgebucht)",
    "ansparen": "Ansparen (Rate pro Monat, Auszahlung bei Fälligkeit)",
    "budget": "Budget (wird im Monat nach und nach verbraucht)",
}
REST_REGELN = {
    "rest_topf": "Überschuss/Fehlbetrag über den Rest-Topf ausgleichen",
    "verrechnen": "Differenz bleibt im Topf und wird mit der nächsten Einzahlung verrechnet",
}


def fixkosten_normalisieren(df: pd.DataFrame) -> pd.DataFrame:
    spalten = ["id", "name", "betrag", "intervall_monate", "naechste_faelligkeit", "konto_id", "kategorie",
               "aktiv", "notiz", "topf_id", "modus", "rate_monat", "rest_regel", "rest_topf_id",
               "auto_abbuchen", "prognose_fuelbudget"]
    if df is None or df.empty:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in spalten})
    df = df.copy()
    for c in spalten:
        if c not in df.columns:
            df[c] = None
    df["betrag"] = pd.to_numeric(df["betrag"], errors="coerce").fillna(0.0).astype(float)
    df["intervall_monate"] = pd.to_numeric(df["intervall_monate"], errors="coerce").fillna(1).astype(int)
    df["rate_monat"] = pd.to_numeric(df["rate_monat"], errors="coerce")
    df["rate_monat"] = df["rate_monat"].fillna(df["betrag"] / df["intervall_monate"])
    df["naechste_faelligkeit"] = pd.to_datetime(df["naechste_faelligkeit"]).dt.normalize()
    df["modus"] = df["modus"].fillna("direkt")
    df["rest_regel"] = df["rest_regel"].fillna("rest_topf")
    for c, d in (("aktiv", True), ("auto_abbuchen", False), ("prognose_fuelbudget", False)):
        df[c] = df[c].fillna(d).astype(bool)
    return df.sort_values("naechste_faelligkeit").reset_index(drop=True)


def monate_weiter(datum, monate: int) -> pd.Timestamp:
    """Datum + n Monate (31.01. + 1 Monat = 28./29.02.)."""
    return _datum(datum) + pd.DateOffset(months=int(monate))


def monatsende(datum) -> pd.Timestamp:
    return _datum(datum) + pd.offsets.MonthEnd(0)


def einzahlungen_bis(heute, faellig) -> int:
    """Wie viele Monatsend-Einzahlungen (Lohn) liegen noch vor der Faelligkeit?
    Gezaehlt werden Monatsenden E mit heute <= E < Faelligkeit."""
    heute, faellig = _datum(heute), _datum(faellig)
    n, e = 0, monatsende(heute)
    while e < faellig:
        n += 1
        e = monatsende(e + pd.Timedelta(days=1))
    return n


def faelligkeiten_bis(faellig, intervall: int, bis) -> int:
    """Anzahl Faelligkeiten ab `faellig` (im Intervall) bis einschliesslich `bis`."""
    n, d = 0, _datum(faellig)
    while d <= _datum(bis) and n < 60:
        n += 1
        d = monate_weiter(d, intervall)
    return n


def pro_monat(f: pd.Series | dict) -> float:
    """Monatlicher Anteil einer Fixkostenposition (z. B. 304 € vierteljaehrlich -> 102 € Rate)."""
    return float(f["rate_monat"]) if pd.notna(f.get("rate_monat")) else float(f["betrag"]) / int(f["intervall_monate"])


def fixkosten_status(fix: pd.DataFrame, toepfe_salden: pd.DataFrame, heute) -> pd.DataFrame:
    """Je aktiver Position: Einstand/Wert ihres Topfes, angesparte Raten, Prognose
    zur Faelligkeit (inkl. noch kommender Monatseinzahlungen) und Fehlbetrag."""
    f = fix[fix["aktiv"]].copy() if not fix.empty else fix.copy()
    if f.empty:
        return f.assign(einstand=pd.Series(dtype=float), wert=pd.Series(dtype=float))
    ts = toepfe_salden.set_index("topf_key") if not toepfe_salden.empty else pd.DataFrame()
    f["einstand"] = [float(ts.at[t, "einstand"]) if isinstance(t, str) and t in ts.index else 0.0 for t in f["topf_id"]]
    f["wert"] = [float(ts.at[t, "wert"]) if isinstance(t, str) and t in ts.index else 0.0 for t in f["topf_id"]]
    f["pro_monat"] = [pro_monat(r) for _, r in f.iterrows()]
    f["raten"] = np.where(f["modus"] == "ansparen", f["einstand"] / f["pro_monat"].replace(0, np.nan), np.nan)
    kommend = [einzahlungen_bis(heute, d) if m == "ansparen" else 0 for d, m in zip(f["naechste_faelligkeit"], f["modus"])]
    f["einzahlungen_bis_faellig"] = kommend
    voll = f["pro_monat"] * f["intervall_monate"]
    f["prognose_faellig"] = np.where(
        f["modus"] == "ansparen",
        np.minimum(f["wert"] + f["pro_monat"] * f["einzahlungen_bis_faellig"],
                   np.maximum(voll, f["wert"])),
        f["wert"])
    f["fehlt_bei_faelligkeit"] = np.where(f["modus"] == "ansparen",
                                          (f["betrag"] - f["prognose_faellig"]).clip(lower=0), 0.0)
    return f


def einzahlung_vorschlag(f: pd.Series | dict, einstand: float, heute, prognose_monat: float | None = None) -> float:
    """Was beim naechsten Lohn fuer diese Position eingezahlt werden sollte.

    - direkt:  Betrag x Faelligkeiten bis Ende des naechsten Monats - was schon da ist
    - ansparen, Rest-Topf: volle Rate, bis Rate x Intervall erreicht ist
    - ansparen, verrechnen (ESN): auf die naechste volle Rate auffuellen
      (5 € Rest aus der letzten Bestellung -> nur 24 € statt 29 €)
    - budget (Kraftstoff): Monatsbetrag (ggf. FuelBudget-Prognose) - Rest im Topf"""
    rate = pro_monat(f)
    if f["modus"] == "budget":
        betrag = prognose_monat if (f.get("prognose_fuelbudget") and prognose_monat) else rate
        return _rund(max(betrag - einstand, 0.0))
    if f["modus"] == "direkt":
        bis = monatsende(monatsende(heute) + pd.Timedelta(days=1))
        bedarf = float(f["betrag"]) * faelligkeiten_bis(f["naechste_faelligkeit"], 1, bis)
        if f["rest_regel"] == "rest_topf":
            return _rund(bedarf) if einstand < 0.005 else _rund(max(bedarf - einstand, 0.0))
        return _rund(max(bedarf - einstand, 0.0))
    voll = rate * int(f["intervall_monate"])
    if f["rest_regel"] == "verrechnen":
        ziel = min((np.floor(einstand / rate + 1e-6) + 1) * rate, voll) if rate > 0 else 0.0
        return _rund(max(ziel - einstand, 0.0))
    return _rund(min(rate, max(voll - einstand, 0.0)))


def zahlung_buchen(f: pd.Series | dict, betrag_ist: float, datum, topf_wert: float,
                   konto_bewertung: str = "saldo", kurs: float | None = None, kategorie: str | None = None) -> list[dict]:
    """Buchungen fuer eine bezahlte Fixkostenposition.

    1. Ausgabe aus dem Topf der Position.
    2. Regel "rest_topf": Was danach im Topf bleibt (Aufrundung, Zinsen, guenstigere
       Rechnung), geht in den Rest-Topf; fehlt etwas (GoodNotes 13 € bei 12 x 1 €),
       kommt es von dort. Regel "verrechnen": Rest bleibt im Topf."""
    datum = str(_datum(datum).date())
    fonds = konto_bewertung == "anteile"
    basis = {"datum": datum, "konto_id": f["konto_id"]}
    zeilen = [{**basis, "topf_id": f["topf_id"], "betrag": -_rund(betrag_ist), "typ": "ausgabe",
               "kategorie": kategorie or f.get("kategorie") or "Abos & Verträge", "beschreibung": f["name"],
               **({"anteile": anteile_fuer(-betrag_ist, kurs)} if fonds else {})}]
    rest = _rund(topf_wert - betrag_ist)
    rest_topf = f.get("rest_topf_id")
    if f["rest_regel"] == "rest_topf" and isinstance(rest_topf, str) and abs(rest) >= 0.005:
        von, nach = (f["topf_id"], rest_topf) if rest > 0 else (rest_topf, f["topf_id"])
        gruppe = str(uuid.uuid4())
        text = f"{f['name']}: Überschuss in Rest-Topf" if rest > 0 else f"{f['name']}: Fehlbetrag aus Rest-Topf"
        for topf, vorzeichen in ((von, -1), (nach, 1)):
            zeilen.append({**basis, "topf_id": topf, "betrag": vorzeichen * abs(rest), "typ": "umbuchung",
                           "gruppe": gruppe, "beschreibung": text,
                           **({"anteile": anteile_fuer(vorzeichen * abs(rest), kurs)} if fonds else {})})
    return zeilen


def faellige_abbuchungen(fix: pd.DataFrame, heute) -> list[tuple[dict, pd.Timestamp]]:
    """Automatische Lastschriften, die bis heute faellig waren: [(Position, Datum), ...]."""
    heute = _datum(heute)
    ergebnis = []
    if fix.empty:
        return ergebnis
    for _, f in fix[fix["aktiv"] & fix["auto_abbuchen"] & (fix["modus"] == "direkt")].iterrows():
        d, n = f["naechste_faelligkeit"], 0
        while d <= heute and n < 24:
            ergebnis.append((f.to_dict(), d))
            d, n = monate_weiter(d, f["intervall_monate"]), n + 1
    return sorted(ergebnis, key=lambda x: x[1])


# --- Depot-Rendite ----------------------------------------------------------------------

@dataclass
class Rendite:
    pa: float | None            # hochgerechnete Rendite p. a. in %
    woche_geo: float | None     # geometrisches Mittel pro Woche in %
    n_wochen: int
    uebernehmen: bool           # genug Wochen fuer die automatische Uebernahme


def depot_rendite(performance_pct: pd.Series) -> Rendite:
    """Aus woechentlichen Performance-Werten (in %, zeitlich sortiert) die
    durchschnittliche Wochenrendite (geometrisch) und deren Hochrechnung auf
    ein Jahr. Beruecksichtigt die letzten RENDITE_FENSTER_WOCHEN Wochen."""
    werte = pd.to_numeric(performance_pct, errors="coerce").dropna().tail(RENDITE_FENSTER_WOCHEN)
    n = len(werte)
    if n == 0:
        return Rendite(None, None, 0, False)
    faktor = float(np.prod(1 + werte.to_numpy() / 100))
    woche = faktor ** (1 / n) - 1
    pa = (1 + woche) ** 52 - 1
    return Rendite(pa * 100, woche * 100, n, n >= MIN_WOCHEN_RENDITE)


def depot_index(performance_pct: pd.Series) -> pd.Series:
    """Wertentwicklung von 100 aus, rein aus den Wochenperformances (ohne
    Einzahlungen) – zeigt, wie sich 100 € entwickelt haetten."""
    werte = pd.to_numeric(performance_pct, errors="coerce").fillna(0.0)
    return 100 * (1 + werte / 100).cumprod()


def eur(x: float, nachkomma: int = 2) -> str:
    """Deutsche Schreibweise: 1.234,56 €"""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "–"
    s = f"{x:,.{nachkomma}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".") + " €"
