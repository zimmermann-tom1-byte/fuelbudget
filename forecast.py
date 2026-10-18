"""Reine Rechenlogik fuer FuelBudget (ohne Streamlit/Supabase, daher testbar).

Alle Prognosen laufen auf WOCHENSUMMEN (Montag-Sonntag), nicht auf einzelnen
Tankvorgaengen. Nur so fliesst ein, wie OFT getankt wird: Wer mehr faehrt,
tankt haeufiger, aber nicht unbedingt mehr pro Tankvorgang.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def week_start(dates: pd.Series) -> pd.Series:
    """Montag der jeweiligen Kalenderwoche (normalisiert auf 00:00)."""
    return (dates - pd.to_timedelta(dates.dt.weekday, unit="D")).dt.normalize()


def weekly_amounts(df: pd.DataFrame, today: pd.Timestamp) -> pd.Series:
    """Ausgaben pro Kalenderwoche von der ersten Tankung bis zur aktuellen Woche.

    Wochen ohne Tankvorgang werden mit 0 aufgefuellt - auch die Wochen NACH
    der letzten Tankung bis heute, damit eine Tankpause die Prognose senkt.
    """
    if df.empty:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    ws = week_start(df["date"])
    sums = df.assign(week_start=ws).groupby("week_start")["amount"].sum()
    current_week = week_start(pd.Series([pd.Timestamp(today).normalize()])).iloc[0]
    end = max(sums.index.max(), current_week)
    all_weeks = pd.date_range(sums.index.min(), end, freq="7D")
    return sums.reindex(all_weeks, fill_value=0.0).astype(float)


def complete_weeks(weekly: pd.Series, today: pd.Timestamp) -> pd.Series:
    """Nur abgeschlossene Wochen. Die laufende Woche ist noch unvollstaendig und
    wuerde die Prognose sonst kuenstlich nach unten ziehen."""
    today = pd.Timestamp(today).normalize()
    return weekly[weekly.index + pd.Timedelta(days=7) <= today]


def _wma(values: np.ndarray, window: int | None) -> np.ndarray:
    """Gewichteter gleitender Durchschnitt (Gewichte 1..m), vektorisiert ueber
    kumulative Summen statt einer Python-Funktion pro Zeitpunkt."""
    n = len(values)
    pos = np.arange(1, n + 1)                       # 1-basierte Position t
    s1 = np.concatenate([[0.0], np.cumsum(values)])  # S1[t] = sum x_1..x_t
    s2 = np.concatenate([[0.0], np.cumsum(pos * values)])
    w = n if window is None else window
    start = np.maximum(1, pos - w + 1)              # Fensterbeginn s
    m = pos - start + 1                             # Fensterlaenge
    num = (s2[pos] - s2[start - 1]) - (start - 1) * (s1[pos] - s1[start - 1])
    return num / (m * (m + 1) / 2)


def forecast_trend(weekly: pd.Series, mode: str, window: int | None, k: float) -> pd.Series:
    """Prognosewert je Woche, wie ihn der Modus mit den bis dahin bekannten
    Wochen geliefert haette. window=None bedeutet: gesamte Historie."""
    if weekly.empty:
        return weekly
    if mode == "SMA":
        if window is None:
            return weekly.expanding().mean()
        return weekly.rolling(window, min_periods=1).mean()
    if mode == "WMA":
        return pd.Series(_wma(weekly.to_numpy(dtype=float), window), index=weekly.index)
    return weekly.ewm(alpha=k, adjust=False).mean()


def verbrauch_intervalle(df: pd.DataFrame, fenster_km: float = 1000.0) -> pd.DataFrame:
    """Verbrauch je Intervall zwischen zwei km-Staenden plus gleitender Verbrauch.

    - Liter von Tankungen OHNE km_stand werden dem naechsten Intervall
      zugeschlagen (vorher gingen sie verloren -> Verbrauch zu niedrig).
    - Fehlt in einem Intervall ein Preis, ist dessen Literzahl unbekannt und
      das Intervall wird verworfen.
    - Einzelwerte schwanken stark, sobald nicht jedes Mal vollgetankt wird.
      Der gleitende Wert (Summe Liter / Summe km ueber mind. fenster_km)
      gleicht Teilbetankungen aus.
    """
    cols = ["date", "km_stand", "liter", "gefahrene_km", "verbrauch", "verbrauch_gleitend"]
    d = df.sort_values(["date", "km_stand"], na_position="first").reset_index(drop=True)
    has_preis = d["preis_pro_liter"].notna() & (d["preis_pro_liter"] > 0)
    d["liter"] = np.where(has_preis, d["amount"] / d["preis_pro_liter"], np.nan)

    rows, letzter_km, liter_acc, ok = [], None, 0.0, True
    for _, r in d.iterrows():
        if letzter_km is not None:
            if pd.isna(r["liter"]):
                ok = False
            else:
                liter_acc += r["liter"]
        if pd.notna(r["km_stand"]):
            if letzter_km is not None:
                km = r["km_stand"] - letzter_km
                if ok and km > 0:
                    rows.append({"date": r["date"], "km_stand": r["km_stand"], "liter": liter_acc, "gefahrene_km": km})
            letzter_km, liter_acc, ok = r["km_stand"], 0.0, True
    out = pd.DataFrame(rows, columns=cols[:4])
    if out.empty:
        return pd.DataFrame(columns=cols)
    out["verbrauch"] = out["liter"] / out["gefahrene_km"] * 100

    cum_l = np.concatenate([[0.0], out["liter"].cumsum().to_numpy()])
    cum_km = np.concatenate([[0.0], out["gefahrene_km"].cumsum().to_numpy()])
    gleitend = []
    for i in range(1, len(out) + 1):
        # fruehester Start j, sodass die km von j..i mindestens fenster_km ergeben
        j = np.searchsorted(cum_km, cum_km[i] - fenster_km, side="right") - 1
        j = max(j, 0)
        gleitend.append((cum_l[i] - cum_l[j]) / (cum_km[i] - cum_km[j]) * 100)
    out["verbrauch_gleitend"] = gleitend
    return out


def km_conflict(df: pd.DataFrame, date: pd.Timestamp, km: float | None, exclude_id=None) -> str | None:
    """Prueft, ob ein Kilometerstand zur zeitlichen Reihenfolge passt.

    Verglichen wird mit Eintraegen VOR bzw. NACH dem Datum - nicht pauschal mit
    dem hoechsten Stand. So lassen sich auch vergessene, aeltere Tankungen
    nachtragen.
    """
    if km is None or df.empty:
        return None
    date = pd.Timestamp(date).normalize()
    other = df[df["km_stand"].notna()]
    if exclude_id is not None:
        other = other[other["id"] != exclude_id]
    before = other.loc[other["date"] < date, "km_stand"]
    after = other.loc[other["date"] > date, "km_stand"]
    if not before.empty and km < before.max():
        return (
            f"Kilometerstand ({km:.0f} km) liegt unter einem früheren Eintrag "
            f"({before.max():.0f} km). Ein Tachostand kann nicht sinken."
        )
    if not after.empty and km > after.min():
        return (
            f"Kilometerstand ({km:.0f} km) liegt über einem späteren Eintrag "
            f"({after.min():.0f} km). Bitte Datum oder Kilometerstand prüfen."
        )
    return None


# --- AUTOMATIK: Parameterwahl per Backtesting ---------------------------------

AUTO_FENSTER = [4, 8, 12, 26, None]          # SMA/WMA, None = alle Wochen
AUTO_K = [0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50]
AUTO_MIN_TRAIN = 8      # Wochen "Anlauf", bevor eine Prognose bewertet wird
AUTO_HORIZONT = 4       # bewertet wird die Summe ueber 4 Wochen (~ 1 Monat)
AUTO_ZIEL = 0.80        # Budget soll in 80 % der 4-Wochen-Zeitraeume reichen
AUTO_HALBWERTSZEIT = 8  # Gewicht eines Testzeitraums halbiert sich alle 8 Wochen


def _gewichtetes_quantil(werte: np.ndarray, gewichte: np.ndarray, q: float) -> float:
    order = np.argsort(werte)
    w, g = werte[order], gewichte[order]
    kum = np.cumsum(g) / g.sum()
    return float(w[np.searchsorted(kum, q)])


def kandidaten() -> list[dict]:
    out = []
    for mode in ("SMA", "WMA"):
        for w in AUTO_FENSTER:
            out.append({"mode": mode, "window": w, "k": None,
                        "label": f"{mode}, {'alle' if w is None else w} Wochen"})
    for k in AUTO_K:
        out.append({"mode": "EMA", "window": None, "k": k, "label": f"EMA, K = {k:.2f}"})
    return out


def _fenster_auswertung(weekly: pd.Series, pred: pd.Series, min_train: int, horizont: int) -> pd.DataFrame:
    """Fuer jeden Startpunkt t: Prognose (nur Daten VOR t) mal horizont gegen
    die tatsaechliche Summe der Wochen t .. t+horizont-1."""
    rows = []
    for t in range(min_train, len(weekly) - horizont + 1):
        prognose = float(pred.iloc[t]) * horizont
        ist = float(weekly.iloc[t:t + horizont].sum())
        rows.append({"start": weekly.index[t], "prognose": prognose, "ist": ist, "fehler": ist - prognose})
    return pd.DataFrame(rows)


def auto_select(weekly: pd.Series, min_train: int = AUTO_MIN_TRAIN,
                horizont: int = AUTO_HORIZONT, ziel: float = AUTO_ZIEL,
                halbwertszeit: float = AUTO_HALBWERTSZEIT) -> dict | None:
    """Testet alle Kandidaten rueckwirkend und waehlt den mit dem kleinsten
    gewichteten mittleren absoluten Fehler (MAE) der 4-Wochen-Prognose.
    Juengere Testzeitraeume zaehlen mehr (Halbwertszeit), damit sich die
    Automatik an veraendertes Fahrverhalten anpasst. Der Puffer wird so gesetzt,
    dass das Budget in `ziel` der (gewichteten) Testzeitraeume gereicht haette.

    Gibt None zurueck, wenn zu wenige abgeschlossene Wochen vorliegen.
    """
    if len(weekly) < min_train + horizont:
        return None

    ergebnisse = []
    for c in kandidaten():
        trend = forecast_trend(weekly, c["mode"], c["window"], c["k"] or 0.0)
        pred = trend.shift(1)  # Prognose fuer Woche t kennt nur Wochen < t
        f = _fenster_auswertung(weekly, pred, min_train, horizont)
        alter = np.arange(len(f))[::-1]               # 0 = juengster Testzeitraum
        f["gewicht"] = 0.5 ** (alter / halbwertszeit)
        ergebnisse.append({
            **c,
            "mae": float(np.average(f["fehler"].abs(), weights=f["gewicht"])),
            "rmse": float(np.sqrt(np.average(f["fehler"] ** 2, weights=f["gewicht"]))),
            "bias": float(np.average(f["fehler"], weights=f["gewicht"])),
            "aktuell": float(trend.iloc[-1]),
            "_fenster": f,
        })

    ranking = pd.DataFrame(ergebnisse).sort_values(["mae", "rmse"]).reset_index(drop=True)
    best = ranking.iloc[0]
    fenster = best["_fenster"]

    gueltig = fenster[fenster["prognose"] > 0]
    verhaeltnis = (gueltig["ist"] / gueltig["prognose"]).to_numpy()
    gew = gueltig["gewicht"].to_numpy()
    if len(verhaeltnis) == 0:
        q, puffer, reicht = 1.0, 0, float("nan")
    else:
        q = _gewichtetes_quantil(verhaeltnis, gew, ziel)
        puffer = int(np.clip(np.ceil((q - 1) * 100 - 1e-9), -10, 100))
        reicht = float(np.average(verhaeltnis <= 1 + puffer / 100, weights=gew))

    return {
        "mode": best["mode"],
        "window": None if pd.isna(best["window"]) else int(best["window"]),
        "k": None if pd.isna(best["k"]) else float(best["k"]),
        "label": best["label"],
        "mae": float(best["mae"]),
        "rmse": float(best["rmse"]),
        "bias": float(best["bias"]),
        "puffer": puffer,
        "quantil": q,
        "reicht_quote": reicht,
        "verhaeltnisse": verhaeltnis,
        "fenster": fenster,
        "ranking": ranking.drop(columns="_fenster"),
        "n_wochen": len(weekly),
        "n_tests": len(fenster),
        "min_train": min_train,
        "horizont": horizont,
        "ziel": ziel,
        "halbwertszeit": halbwertszeit,
    }
