"""Rechenlogik der Finanzuebersicht – ohne Streamlit und ohne Datenbank,
damit sie sich mit Unit-Tests pruefen laesst.

Grundidee (Hauptbuch):
- Geld liegt in "Posten" (z. B. Autoruecklagen bei BBVA). Jeder Posten hat eine
  Stufe, die sagt, wie verfuegbar das Geld ist (frei, Fixkosten, Ruecklage,
  langfristig, Verbindlichkeit).
- Jede Geldbewegung ist eine Buchung auf einem Posten. Saldo = Summe aller
  Buchungen des Postens.
- Technisch ist ein Posten ein Konto mit genau einem Standardtopf (Datenbank
  fin_konten + fin_toepfe). So bleiben die Daten mit dem frueheren
  Konto-/Topf-Modell kompatibel.
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
        "tank_id": pd.Series(dtype="object"),
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


@dataclass
class Ueberblick:
    posten: pd.DataFrame        # je Posten: Salden, Satz, Tagesertrag, Ziel, Stufe
    stufen: pd.Series           # Summe je Stufe (Reihenfolge wie STUFEN)
    brutto: float
    verbindlichkeiten_offen: float
    netto: float
    frei: float
    ertrag_tag_zins: float      # Zinsen, recht sicher
    ertrag_tag_dividende: float  # erwartete Ausschuettungen
    ertrag_tag_markt: float     # erwartete Kursrendite (gemessen oder Annahme)


def ueberblick(posten: pd.DataFrame, buchungen: pd.DataFrame, zinssaetze: pd.DataFrame,
               verbindlichkeiten: pd.DataFrame | None, heute, ziele: dict | None = None) -> Ueberblick:
    """posten = fin_konten (mit Spalte stufe). ziele: konto_id -> Zielbetrag."""
    heute = _datum(heute)
    b = buchungen[buchungen["datum"] <= heute]

    gebucht = gebuchte_salden(b, heute)
    zinsen = aufgelaufene_zinsen(b, posten, zinssaetze, heute)

    p = posten.copy()
    if "stufe" not in p.columns:
        p["stufe"] = "frei"
    p["stufe"] = p["stufe"].fillna("frei")
    p["gebucht"] = p["id"].map(gebucht).fillna(0.0)
    p["aufgelaufen"] = p["id"].map(zinsen).fillna(0.0)
    p["saldo"] = p["gebucht"] + p["aufgelaufen"]
    p["satz_pa"] = [satz_am(zinssaetze, kid, heute) for kid in p["id"]]
    p["ertrag_tag"] = p["saldo"].clip(lower=0) * p["satz_pa"] / 100 / TAGE_PRO_JAHR
    letzte_bestaetigung = b[b["typ"].isin(ZINS_RESET_TYPEN)].groupby("konto_id")["datum"].max()
    # ueber dict mappen: bei leerer Postenliste scheitert pandas sonst am Datumstyp
    p["bestaetigt_am"] = pd.to_datetime(p["id"].map(letzte_bestaetigung.to_dict()))
    p["ziel"] = pd.to_numeric(p["id"].map(ziele or {}), errors="coerce")

    stufen = p.groupby("stufe")["saldo"].sum().reindex(list(STUFEN), fill_value=0.0)

    v = verbindlichkeiten_offen(verbindlichkeiten, b)
    offen = float(v["offen"].sum()) if not v.empty else 0.0
    brutto = float(p["saldo"].sum())

    def ertrag(art):
        return float(p.loc[p["ertragsart"] == art, "ertrag_tag"].sum())

    return Ueberblick(
        posten=p, stufen=stufen, brutto=brutto, verbindlichkeiten_offen=offen,
        netto=brutto - offen, frei=float(stufen["frei"]),
        ertrag_tag_zins=ertrag("zins"), ertrag_tag_dividende=ertrag("dividende"),
        ertrag_tag_markt=ertrag("markt"),
    )


def stufen_verlauf(buchungen: pd.DataFrame, posten: pd.DataFrame, bis) -> pd.DataFrame:
    """Taeglicher gebuchter Saldo je Stufe (Spalten in STUFEN-Reihenfolge)."""
    verlauf = saldo_verlauf(buchungen, bis)
    if verlauf.empty:
        return verlauf
    stufe = posten.set_index("id")["stufe"].fillna("frei")
    gruppen = verlauf.T.groupby(lambda kid: stufe.get(kid, "frei")).sum().T
    return gruppen.reindex(columns=[s for s in STUFEN if s in gruppen.columns])


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

def fixkosten_normalisieren(df: pd.DataFrame) -> pd.DataFrame:
    spalten = ["id", "name", "betrag", "intervall_monate", "naechste_faelligkeit", "konto_id",
               "kategorie", "aktiv", "notiz"]
    if df is None or df.empty:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in spalten})
    df = df.copy()
    df["betrag"] = pd.to_numeric(df["betrag"], errors="coerce").fillna(0.0).astype(float)
    df["intervall_monate"] = pd.to_numeric(df["intervall_monate"], errors="coerce").fillna(1).astype(int)
    df["naechste_faelligkeit"] = pd.to_datetime(df["naechste_faelligkeit"]).dt.normalize()
    df["aktiv"] = df["aktiv"].fillna(True).astype(bool)
    return df.sort_values("naechste_faelligkeit").reset_index(drop=True)


def monate_weiter(datum, monate: int) -> pd.Timestamp:
    """Datum + n Monate (31.01. + 1 Monat = 28./29.02.)."""
    return _datum(datum) + pd.DateOffset(months=int(monate))


def monatsende(datum) -> pd.Timestamp:
    return _datum(datum) + pd.offsets.MonthEnd(0)


def soll_heute(betrag: float, intervall: int, faellig, heute) -> float:
    """Wie viel fuer diese Fixkosten heute zurueckgelegt sein sollte.

    - Monatlich: der volle Betrag, solange er in diesem Monat noch nicht
      bezahlt ist (Faelligkeit spaetestens am Monatsende), sonst 0.
    - Alle n Monate: anteilig nach verstrichener Zeit seit der letzten
      Faelligkeit (linear), am Faelligkeitstag der volle Betrag."""
    heute, faellig = _datum(heute), _datum(faellig)
    if faellig <= heute:
        return float(betrag)
    if intervall <= 1:
        return float(betrag) if faellig <= monatsende(heute) else 0.0
    vorher = monate_weiter(faellig, -intervall)
    anteil = (heute - vorher).days / max((faellig - vorher).days, 1)
    return float(betrag) * min(max(anteil, 0.0), 1.0)


def fixkosten_soll(fixkosten: pd.DataFrame, heute) -> pd.DataFrame:
    """Je aktiver Fixkostenposition: Monatsanteil und heutiges Soll."""
    f = fixkosten[fixkosten["aktiv"]].copy() if not fixkosten.empty else fixkosten.copy()
    if f.empty:
        return f.assign(pro_monat=pd.Series(dtype=float), soll=pd.Series(dtype=float))
    f["pro_monat"] = f["betrag"] / f["intervall_monate"]
    f["soll"] = [soll_heute(r["betrag"], r["intervall_monate"], r["naechste_faelligkeit"], heute)
                 for _, r in f.iterrows()]
    return f


def fixkosten_je_posten(fixkosten: pd.DataFrame, salden: pd.Series, heute) -> pd.DataFrame:
    """Soll/Ist je Posten, aus dem Fixkosten bezahlt werden.
    Spalten: konto_id, soll, ist, differenz (Ist - Soll), pro_monat."""
    f = fixkosten_soll(fixkosten, heute)
    f = f[f["konto_id"].notna()] if not f.empty else f
    if f.empty:
        return pd.DataFrame(columns=["konto_id", "soll", "ist", "differenz", "pro_monat"])
    g = f.groupby("konto_id").agg(soll=("soll", "sum"), pro_monat=("pro_monat", "sum")).reset_index()
    g["ist"] = g["konto_id"].map(salden).fillna(0.0)
    g["differenz"] = g["ist"] - g["soll"]
    return g


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
