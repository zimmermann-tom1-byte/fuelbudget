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
