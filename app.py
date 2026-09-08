"""
FuelBudget - Minimalversion (Schritt 1)
Berechnet SMA-, WMA- und EMA-Budget (+10% Puffer) fuer die Folgewoche
auf Basis wochenweise aggregierter Tankausgaben.
"""

from datetime import datetime

K = 0.15          # Glaettungsfaktor fuer EMA
PUFFER = 0.10      # 10% Sicherheitspuffer
KW_START, KW_END = 7, 33   # Betrachteter Wochenbereich (KW 07 - KW 33)

# ---------------------------------------------------------------------------
# 1. Datenbasis
# ---------------------------------------------------------------------------
TRANSAKTIONEN = [
    {"date": "11.02.2026", "amount": 34.39},
    {"date": "13.02.2026", "amount": 26.99},
    {"date": "17.02.2026", "amount": 57.40},
    {"date": "22.02.2026", "amount": 3.54},
    {"date": "27.02.2026", "amount": 9.90},
    {"date": "28.02.2026", "amount": 57.25},
    {"date": "28.02.2026", "amount": 20.15},
    {"date": "04.03.2026", "amount": 29.72},
    {"date": "09.03.2026", "amount": 57.03},
    {"date": "15.03.2026", "amount": 46.77},
    {"date": "22.03.2026", "amount": 30.01},
    {"date": "27.03.2026", "amount": 86.79},
    {"date": "05.04.2026", "amount": 62.92},
    {"date": "10.04.2026", "amount": 4.49},
    {"date": "12.04.2026", "amount": 5.25},
    {"date": "12.04.2026", "amount": 3.99},
    {"date": "17.04.2026", "amount": 57.89},
    {"date": "29.04.2026", "amount": 71.83},
    {"date": "30.04.2026", "amount": 3.74},
    {"date": "06.05.2026", "amount": 36.11},
    {"date": "08.05.2026", "amount": 26.35},
    {"date": "16.05.2026", "amount": 61.09},
    {"date": "27.05.2026", "amount": 68.25},
    {"date": "07.06.2026", "amount": 30.33},
    {"date": "10.06.2026", "amount": 16.08},
    {"date": "15.06.2026", "amount": 49.95},
    {"date": "17.06.2026", "amount": 16.01},
    {"date": "27.06.2026", "amount": 63.63},
    {"date": "30.06.2026", "amount": 64.81},
    {"date": "03.07.2026", "amount": 26.88},
    {"date": "05.07.2026", "amount": 5.23},
    {"date": "13.07.2026", "amount": 73.39},
    {"date": "20.07.2026", "amount": 50.00},
    {"date": "30.07.2026", "amount": 69.70},
    {"date": "01.08.2026", "amount": 39.84},
    {"date": "12.08.2026", "amount": 60.97},
    {"date": "19.08.2026", "amount": 46.50},
    {"date": "02.09.2026", "amount": 69.18},
]


# ---------------------------------------------------------------------------
# 2. Wochen-Aggregation (Montag-Sonntag, KW 07 - KW 33)
# ---------------------------------------------------------------------------
def aggregiere_wochen(transaktionen, kw_start, kw_end):
    summen = {kw: 0.0 for kw in range(kw_start, kw_end + 1)}
    for t in transaktionen:
        try:
            datum = datetime.strptime(t["date"], "%d.%m.%Y")
            betrag = float(t["amount"])
        except (ValueError, KeyError, TypeError):
            continue  # defensiv: fehlerhafte Eintraege ueberspringen
        kw = datum.isocalendar()[1]
        if kw_start <= kw <= kw_end:
            summen[kw] += betrag
    # geordnete Liste KW07..KW33
    return [round(summen[kw], 2) for kw in range(kw_start, kw_end + 1)]


# ---------------------------------------------------------------------------
# 3. Berechnungslogik
# ---------------------------------------------------------------------------
def berechne_sma(wochenwerte):
    return sum(wochenwerte) / len(wochenwerte)


def berechne_wma(wochenwerte):
    n = len(wochenwerte)
    gewichte = range(1, n + 1)  # aelteste=1, neueste=n
    zaehler = sum(w * g for w, g in zip(wochenwerte, gewichte))
    nenner = sum(gewichte)
    return zaehler / nenner


def berechne_ema(wochenwerte, k):
    budget = berechne_sma(wochenwerte)  # Startwert = SMA
    for ausgabe in wochenwerte:
        budget = (k * ausgabe) + ((1 - k) * budget)
    return budget


# ---------------------------------------------------------------------------
# 4. Ausgabe
# ---------------------------------------------------------------------------
def main():
    wochenwerte = aggregiere_wochen(TRANSAKTIONEN, KW_START, KW_END)
    naechste_kw = KW_END + 1

    sma = berechne_sma(wochenwerte)
    wma = berechne_wma(wochenwerte)
    ema = berechne_ema(wochenwerte, K)

    sma_budget = sma * (1 + PUFFER)
    wma_budget = wma * (1 + PUFFER)
    ema_budget = ema * (1 + PUFFER)

    print(f"Analysierte Wochen: KW{KW_START:02d}-KW{KW_END:02d} ({len(wochenwerte)} Wochen)")
    print(f"Budget fuer KW{naechste_kw:02d} (inkl. {int(PUFFER*100)}% Puffer):\n")
    print(f"  SMA-Budget: {sma_budget:>7.2f} EUR   (Rohwert: {sma:6.2f} EUR)")
    print(f"  WMA-Budget: {wma_budget:>7.2f} EUR   (Rohwert: {wma:6.2f} EUR)")
    print(f"  EMA-Budget: {ema_budget:>7.2f} EUR   (Rohwert: {ema:6.2f} EUR, K={K})")


if __name__ == "__main__":
    main()
