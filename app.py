import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime
import uuid
from supabase import create_client

# --- SUPABASE VERBINDUNG ---
supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

# --- PAGE CONFIGURATION ---
st.set_page_config(
    page_title="FuelBudget",
    page_icon="⛽",
    layout="wide"
)

# Custom Dark Theme Styling
st.markdown("""
<style>
    .main { background-color: #020617; color: #f8fafc; }
    .stMetric { background-color: #0f172a; border: 1px solid #1e293b; padding: 15px; border-radius: 12px; }
    div[data-testid="stForm"] { background-color: #0f172a; border: 1px solid #1e293b; }
    div[data-testid="stDataFrame"] { background-colour: #0f172a; border-radius: 12px; }
</style>
""", unsafe_allow_html=True)

def load_data():
    response = supabase.table("transactions").select("*").execute()
    df = pd.DataFrame(response.data)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date", ascending=False).reset_index(drop=True)
        # Robust gegenueber DBs, auf denen die ALTER-TABLE-Migration fuer
        # km_stand/preis_pro_liter noch nicht ausgefuehrt wurde.
        for col in ("km_stand", "preis_pro_liter"):
            if col not in df.columns:
                df[col] = np.nan
            else:
                df[col] = pd.to_numeric(df[col], errors="coerce")
    return df

df = load_data()

# --- HEADER & SIDEBAR ---
st.title("FuelBudget 🚗⛽")
st.caption("Präzise Kraftstoffbudgetierung & Verbrauchsanalyse")

st.sidebar.header("Neuer Tankvorgang")
with st.sidebar.form("add_transaction_form", clear_on_submit=True):
    new_date = st.date_input("Datum", value=datetime.today())
    new_amount = st.number_input("Gesamtbetrag (€)", min_value=0.0, step=0.01, format="%.2f")
    new_km = st.number_input("Kilometerstand (optional)", min_value=0.0, step=1.0, format="%.0f", value=None)
    new_preis_pro_liter = st.number_input(
        "Preis pro Liter (€/l, optional)", min_value=0.0, step=0.001, format="%.3f", value=None,
        help="In Euro angeben, z. B. 1,75 – nicht in Cent (die Tankstellenanzeige zeigt oft 175,0 Cent)."
    )
    submitted = st.form_submit_button("Transaktion speichern")

# --- PROGNOSE-PARAMETER (Sidebar) ---
# Muss VOR jedem moeglichen st.rerun() stehen: wird ein Widget in einem
# Skriptdurchlauf nicht instanziiert (z. B. weil st.rerun() vorher abbricht),
# setzt Streamlit dessen session_state beim naechsten Durchlauf auf den
# Default zurueck - unabhaengig vom key=. Reihenfolge daher bewusst so.
st.sidebar.markdown("---")
st.sidebar.header("Prognose-Parameter")

mode = st.sidebar.selectbox("Berechnungsmodus", ["EMA", "SMA", "WMA"], index=0, key="mode_select")
with st.sidebar.popover("ℹ️ Was bedeutet das & welchen Modus wähle ich?", width="stretch"):
    st.markdown("""
**Das Grundproblem:** Ein reiner Durchschnitt über alle Tankvorgänge wird mit wachsender Datenmenge immer träger. Fährst du nach einer ruhigen Phase (z. B. Winter, Homeoffice) plötzlich wieder mehr, würde ein einfacher Durchschnitt das erst nach sehr vielen Wochen "merken". Die drei Modi sind unterschiedliche Antworten auf die Frage, wie stark neuere Tankvorgänge gegenüber älteren zählen sollen.

**SMA – Simple Moving Average:** Jeder Tankvorgang zählt gleich viel, egal ob er heute oder vor einem Jahr war. → Sinnvoll, wenn dein Fahrverhalten insgesamt stabil ist und du eine robuste, schwankungsarme Langzeit-Referenz willst.

**WMA – Weighted Moving Average:** Neuere Tankvorgänge zählen linear mehr als ältere. → Ein Kompromiss: reagiert etwas schneller als SMA auf Veränderungen, ohne alte Daten komplett zu verwerfen.

**EMA – Exponential Moving Average:** Reagiert am schnellsten auf aktuelle Änderungen, weil neuere Werte exponentiell stärker gewichtet werden. → Die beste Wahl, wenn sich dein Fahrverhalten öfter ändert (Jobwechsel, Saisonalität, mal mehr/weniger unterwegs) und die Prognose zeitnah mitziehen soll.
""")

k_factor = st.sidebar.slider("Glättungsfaktor K (nur EMA)", min_value=0.01, max_value=1.0, value=0.15, step=0.01, key="k_factor_slider")
with st.sidebar.popover("ℹ️ Was steuert dieser Regler?", width="stretch"):
    st.markdown("""
**Das Grundproblem:** Bei EMA muss festgelegt werden, *wie schnell* "schnell reagieren" genau bedeutet – genau das steuert K. Er bestimmt die Balance zwischen Stabilität und Reaktionsgeschwindigkeit.

**Hoher K-Wert (nah an 1,0):** Fast nur der letzte Tankvorgang zählt. Die Prognose "springt" stark bei jeder einzelnen Tankung mit – reagiert sofort auf Veränderungen, ist aber auch anfällig dafür, von einem einzelnen ungewöhnlich hohen oder niedrigen Betrag verzerrt zu werden.

**Niedriger K-Wert (nah an 0,01):** Die Prognose ändert sich nur sehr langsam, auch wenn sich das tatsächliche Fahrverhalten schon geändert hat – dafür ist sie robust gegen einzelne Ausreißer (z. B. eine ungewöhnlich teure oder günstige Tankfüllung).

Es geht also um die Frage: Vertraust du eher dem langfristigen Muster oder den letzten paar Tankvorgängen?
""")

buffer_pct = st.sidebar.slider("Sicherheitspuffer (%)", min_value=-10, max_value=100, value=10, step=1, key="buffer_pct_slider")
with st.sidebar.popover("ℹ️ Wozu dient dieser Puffer?", width="stretch"):
    st.markdown("""
**Das Grundproblem:** Der berechnete SMA-/WMA-/EMA-Wert ist eine reine statistische Erwartung – es ist kein Sicherheitsspielraum für Unvorhergesehenes eingerechnet. Der Puffer erlaubt dir, diesen Erwartungswert bewusst nach oben oder unten zu verschieben.

**Positiver Puffer:** Schafft eine Sicherheitsmarge nach oben – sinnvoll, wenn du lieber etwas mehr Budget einplanst, falls unerwartet mehr gefahren wird oder die Spritpreise steigen, statt am Monatsende negativ überrascht zu werden.

**Negativer Puffer:** Setzt bewusst ein knapperes Budget an, als der statistische Durchschnitt nahelegt – sinnvoll als Sparziel, oder wenn du weißt, dass du künftig strukturell weniger fahren wirst (z. B. Fahrgemeinschaft, Homeoffice), die historischen Daten das aber noch nicht widerspiegeln.
""")

# Neue Transaktion direkt in Supabase speichern
vorhandene_km = df["km_stand"].dropna()
letzter_km_stand = vorhandene_km.max() if not vorhandene_km.empty else None

if submitted:
    # Realistische Kraftstoffpreise liegen deutlich unter 5 €/l - typischer
    # Fehler ist die Eingabe des Tankstellen-Anzeigewerts in Cent (z. B. 175
    # statt 1.75), was die Verbrauchsberechnung um den Faktor 100 verfaelscht.
    if new_preis_pro_liter is not None and new_preis_pro_liter > 5:
        st.error(
            f"Preis pro Liter von {new_preis_pro_liter:.3f} € wirkt unrealistisch hoch. "
            "Bitte in Euro angeben (z. B. 1.75 statt 175)."
        )
    elif new_km is not None and letzter_km_stand is not None and new_km < letzter_km_stand:
        st.error(
            f"Kilometerstand ({new_km:.0f} km) liegt unter dem zuletzt erfassten Stand "
            f"({letzter_km_stand:.0f} km). Ein Tachostand kann nicht sinken - bitte prüfen "
            "oder den fehlerhaften Eintrag in der Historie korrigieren."
        )
    elif new_amount > 0:
        supabase.table("transactions").insert({
            "date": str(new_date),
            "amount": float(new_amount),
            "km_stand": float(new_km) if new_km is not None else None,
            "preis_pro_liter": float(new_preis_pro_liter) if new_preis_pro_liter is not None else None,
        }).execute()
        st.success("Transaktion dauerhaft in der Cloud gespeichert!")
        st.rerun()
    else:
        st.error("Bitte erst alle Felder gültig ausfüllen.")

# --- BERECHNUNG DER PROGNOSEMODELLE ---
amounts = df.sort_values("date", ascending=True)["amount"].reset_index(drop=True)
n_samples = len(amounts)

# Alle bisherigen Transaktionen einbeziehen (kein festes Mini-Fenster mehr)
window = n_samples

# 1. Simple Moving Average (SMA)
sma = amounts.rolling(window=window).mean().iloc[-1]

# 2. Weighted Moving Average (WMA)
weights = np.arange(1, window + 1)
wma = np.average(amounts.tail(window), weights=weights)

# 3. Exponential Moving Average (EMA) - Glaettungsfaktor ueber Slider einstellbar
ema = amounts.ewm(alpha=k_factor, adjust=False).mean().iloc[-1]

# Prognose je nach gewaehltem Modus
if mode == "SMA":
    base_forecast = sma
elif mode == "WMA":
    base_forecast = wma
else:
    base_forecast = ema

final_weekly_forecast = base_forecast * (1 + buffer_pct / 100.0)
final_monthly_projection = (final_weekly_forecast * 52) / 12

# --- DASHBOARD METRIKEN ---
col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        label=f"Budget Kommende KW ({mode})",
        value=f"{final_weekly_forecast:.2f} €",
        delta=f"{buffer_pct:+d}% Puffer" if buffer_pct != 0 else "Kein Puffer"
    )

with col2:
    st.metric(
        label=f"Hochrechnung / Monat ({mode})",
        value=f"{final_monthly_projection:.2f} €",
        delta=f"{buffer_pct:+d}% Puffer" if buffer_pct != 0 else "Kein Puffer"
    )

with col3:
    st.metric(
        label="Gesamtausgaben",
        value=f"{amounts.sum():.2f} €",
        delta=f"{n_samples} Tankungen"
    )

# --- VISUALISIERUNG (PLOTLY CHART) ---
st.markdown("### Ausgabenverlauf & Prognosemodelle")

fig = go.Figure()

if not df.empty:
    # Transaktionen zu Kalenderwochen (Montag-Sonntag) zusammenfassen, damit
    # einzelne Tankbetraege nicht mehr als zackige Linie erscheinen. Wochen
    # ohne Tankvorgang werden explizit mit 0 aufgefuellt, damit im Chart
    # keine Luecke entsteht und die Trendlinie nicht ueber sie hinwegspringt.
    week_start = (df["date"] - pd.to_timedelta(df["date"].dt.weekday, unit="D")).dt.normalize()
    weekly_amounts = df.assign(week_start=week_start).groupby("week_start")["amount"].sum()
    all_weeks = pd.date_range(weekly_amounts.index.min(), weekly_amounts.index.max(), freq="7D")
    weekly_amounts = weekly_amounts.reindex(all_weeks, fill_value=0.0)

    # Expandierende Prognose-Trendlinie: fuer jede Woche wird der Wert
    # berechnet, den der gewaehlte Modus mit allen BIS DAHIN bekannten Wochen
    # geliefert haette - kein fixes Rolling-Window wie zuvor.
    if mode == "SMA":
        trend = weekly_amounts.expanding().mean()
    elif mode == "WMA":
        def _expanding_wma(values):
            w = np.arange(1, len(values) + 1)
            return np.average(values, weights=w)
        trend = weekly_amounts.expanding().apply(_expanding_wma, raw=True)
    else:
        trend = weekly_amounts.ewm(alpha=k_factor, adjust=False).mean()

    # Balken: reale Ausgaben pro Kalenderwoche
    fig.add_trace(go.Bar(
        x=weekly_amounts.index,
        y=weekly_amounts.values,
        name="Reale Ausgaben pro KW (€)",
        marker=dict(color="#38bdf8")
    ))

    # Glatte Linie: expandierender Prognose-Trend passend zum Sidebar-Modus
    fig.add_trace(go.Scatter(
        x=trend.index,
        y=trend.values,
        mode="lines",
        name=f"Prognose-Trend ({mode})",
        line=dict(color="#f59e0b", width=2.5, shape="spline")
    ))

    # Referenzlinie: aktuelles Wochenbudget inkl. Puffer
    fig.add_hline(
        y=final_weekly_forecast,
        line_dash="dash",
        line_color="#64748b",
        line_width=1.5,
        annotation_text="Aktuelles Wochenbudget",
        annotation_position="top left",
        annotation_font_color="#94a3b8"
    )

fig.update_layout(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=20, r=20, t=30, b=20),
    xaxis=dict(title="Kalenderwoche (Montag)", gridcolor="#1e293b"),
    yaxis=dict(title="Betrag (€)", gridcolor="#1e293b"),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
)

st.plotly_chart(fig, use_container_width=True)

# --- KILOMETER & VERBRAUCH ---
st.markdown("### Kilometer & Verbrauch")

if df.empty:
    st.info("Keine Transaktionen vorhanden.")
else:
    # Chronologische Kopie fuer die Verbrauchsberechnung.
    verbrauch_df = df.sort_values("date", ascending=True).reset_index(drop=True)

    # Liter pro Tankvorgang, nur wenn ein gueltiger Preis/Liter vorliegt.
    has_preis = verbrauch_df["preis_pro_liter"].notna() & (verbrauch_df["preis_pro_liter"] > 0)
    verbrauch_df["liter"] = np.where(has_preis, verbrauch_df["amount"] / verbrauch_df["preis_pro_liter"], np.nan)

    # Gefahrene km seit der chronologisch vorherigen Tankung MIT km_stand
    # (Tankungen ohne km_stand werden dabei uebersprungen, nicht als 0 gewertet).
    km_mask = verbrauch_df["km_stand"].notna()
    km_diffs = verbrauch_df.loc[km_mask, "km_stand"].diff()
    verbrauch_df["gefahrene_km"] = np.nan
    verbrauch_df.loc[km_diffs.index, "gefahrene_km"] = km_diffs

    # Verbrauch in L/100km, nur wenn Liter und gefahrene km vorhanden und > 0.
    has_verbrauch = verbrauch_df["liter"].notna() & verbrauch_df["gefahrene_km"].notna() & (verbrauch_df["gefahrene_km"] > 0)
    verbrauch_df["verbrauch_l_100km"] = np.where(
        has_verbrauch, (verbrauch_df["liter"] / verbrauch_df["gefahrene_km"]) * 100, np.nan
    )

    km_chart_df = verbrauch_df.dropna(subset=["km_stand"])
    verbrauch_chart_df = verbrauch_df.dropna(subset=["verbrauch_l_100km"])

    km_col, verbrauch_col = st.columns(2)

    with km_col:
        st.markdown("**Kilometerstand über die Zeit**")
        if km_chart_df.empty:
            st.info("Noch keine Kilometerstände erfasst.")
        else:
            km_fig = go.Figure()
            km_fig.add_trace(go.Scatter(
                x=km_chart_df["date"],
                y=km_chart_df["km_stand"],
                mode="lines+markers",
                name="Kilometerstand",
                line=dict(color="#38bdf8", width=2.5),
                marker=dict(size=6)
            ))
            km_fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=20, r=20, t=20, b=20),
                xaxis=dict(title="Datum", gridcolor="#1e293b"),
                yaxis=dict(title="Kilometerstand (km)", gridcolor="#1e293b"),
                showlegend=False
            )
            st.plotly_chart(km_fig, use_container_width=True)

    with verbrauch_col:
        st.markdown("**Verbrauch (L/100km) pro Tankvorgang**")
        if verbrauch_chart_df.empty:
            st.info("Noch nicht genug Daten für eine Verbrauchsberechnung (Preis/Liter und mind. zwei Kilometerstände nötig).")
        else:
            verbrauch_fig = go.Figure()
            verbrauch_fig.add_trace(go.Bar(
                x=verbrauch_chart_df["date"],
                y=verbrauch_chart_df["verbrauch_l_100km"],
                name="Verbrauch (L/100km)",
                marker=dict(color="#f59e0b")
            ))
            verbrauch_fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=20, r=20, t=20, b=20),
                xaxis=dict(title="Datum", gridcolor="#1e293b"),
                yaxis=dict(title="L/100km", gridcolor="#1e293b"),
                showlegend=False
            )
            st.plotly_chart(verbrauch_fig, use_container_width=True)

# --- AUSGABEN AKTUELLER MONAT (EIN BALKEN, EIN SEGMENT PRO TANKVORGANG) ---
st.markdown("### Ausgaben aktueller Monat")

MONATSNAMEN = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember"
]
SEGMENT_FARBEN = [
    "#38bdf8", "#f59e0b", "#a78bfa", "#34d399", "#f472b6",
    "#facc15", "#60a5fa", "#fb923c", "#4ade80", "#c084fc"
]

heute = datetime.today()
monat_df = df[
    (df["date"].dt.year == heute.year) & (df["date"].dt.month == heute.month)
].sort_values("date").reset_index(drop=True)
monatsname = f"{MONATSNAMEN[heute.month - 1]} {heute.year}"

monat_chart_col, monat_info_col = st.columns([1, 3])

with monat_chart_col:
    if monat_df.empty:
        st.info(f"Noch keine Tankvorgänge im {monatsname}.")
    else:
        ausgegeben_monat = monat_df["amount"].sum()

        monat_fig = go.Figure()
        for i, row in monat_df.iterrows():
            monat_fig.add_trace(go.Bar(
                x=[monatsname],
                y=[row["amount"]],
                marker=dict(color=SEGMENT_FARBEN[i % len(SEGMENT_FARBEN)]),
                hovertemplate=f"{row['date'].strftime('%d.%m.%Y')}<br>{row['amount']:.2f} €<extra></extra>"
            ))

        # Ausgegrauter Teil: Rest der Monats-Hochrechnung (Sidebar-Modus + Puffer),
        # der laut Prognose diesen Monat noch dazukommen duerfte.
        prognostizierter_rest = max(final_monthly_projection - ausgegeben_monat, 0)
        if prognostizierter_rest > 0:
            monat_fig.add_trace(go.Bar(
                x=[monatsname],
                y=[prognostizierter_rest],
                marker=dict(color="rgba(148, 163, 184, 0.35)"),
                hovertemplate=f"Prognostizierter Rest ({mode}): {prognostizierter_rest:.2f} €<extra></extra>"
            ))

        monat_fig.update_layout(
            barmode="stack",
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis=dict(visible=False),
            yaxis=dict(title="€", gridcolor="#1e293b"),
            showlegend=False,
            bargap=0.55,
            height=320
        )
        st.plotly_chart(monat_fig, use_container_width=True)

with monat_info_col:
    if not monat_df.empty:
        st.metric(f"Bisher ausgegeben im {monatsname}", f"{ausgegeben_monat:.2f} €", delta=f"{len(monat_df)} Tankungen")
        if prognostizierter_rest > 0:
            st.caption(
                f"Grau = prognostizierter Rest bis Monatsende ({mode}-Hochrechnung: "
                f"{final_monthly_projection:.2f} € gesamt)."
            )
        else:
            st.caption(f"Bereits über der {mode}-Monats-Hochrechnung von {final_monthly_projection:.2f} €.")

# --- ÖLWECHSEL-TRACKER ---
st.markdown("### Ölwechsel-Tracker")

def load_oil_settings():
    try:
        response = supabase.table("oil_change_settings").select("*").limit(1).execute()
        return response.data[0] if response.data else {}
    except Exception:
        return None

oil_settings = load_oil_settings()

if oil_settings is None:
    st.info(
        "Für den Ölwechsel-Tracker wird eine zusätzliche Tabelle in Supabase benötigt, die noch "
        "nicht existiert. Bitte das SQL-Snippet aus dem Chat in Supabase ausführen und die Seite "
        "danach neu laden."
    )
elif not oil_settings:
    st.write("Noch keine Ölwechsel-Einstellungen hinterlegt.")
    with st.form("oil_setup_form"):
        setup_interval = st.number_input(
            "Ölwechselintervall (km)", min_value=1000.0, step=500.0, value=15000.0, format="%.0f"
        )
        setup_vor_km = st.number_input(
            "Letzter Ölwechsel war vor wie vielen km? (0 = gerade gemacht)",
            min_value=0.0, step=100.0, value=0.0, format="%.0f"
        )
        setup_submitted = st.form_submit_button("Einstellungen speichern")

    if setup_submitted:
        if letzter_km_stand is None:
            st.error("Es ist noch kein Kilometerstand erfasst. Bitte zuerst einen Tankvorgang mit Kilometerstand eintragen.")
        else:
            supabase.table("oil_change_settings").insert({
                "id": 1,
                "interval_km": float(setup_interval),
                "last_oil_change_km": float(letzter_km_stand - setup_vor_km),
            }).execute()
            st.success("Ölwechsel-Einstellungen gespeichert.")
            st.rerun()
else:
    interval_km = oil_settings["interval_km"]
    last_oil_change_km = oil_settings["last_oil_change_km"]
    naechster_wechsel_km = last_oil_change_km + interval_km

    if letzter_km_stand is None:
        st.info("Noch kein Kilometerstand erfasst - Fortschritt kann nicht berechnet werden.")
    else:
        gefahren_seit_wechsel = max(letzter_km_stand - last_oil_change_km, 0)
        rest_km = max(naechster_wechsel_km - letzter_km_stand, 0)
        fortschritt_pct = min(gefahren_seit_wechsel / interval_km, 1.0) if interval_km > 0 else 0.0

        st.progress(fortschritt_pct)

        anzeige_modus = st.radio(
            "Anzeige", ["Relativ (%)", "Gesamt-km beim nächsten Wechsel", "Verbleibende km"],
            horizontal=True, key="oil_anzeige_modus", label_visibility="collapsed"
        )
        if anzeige_modus == "Relativ (%)":
            st.metric("Fortschritt bis zum nächsten Ölwechsel", f"{fortschritt_pct * 100:.0f} %")
        elif anzeige_modus == "Gesamt-km beim nächsten Wechsel":
            st.metric("Nächster Ölwechsel fällig bei", f"{naechster_wechsel_km:.0f} km")
        else:
            st.metric("Noch verbleibende km bis zum Ölwechsel", f"{rest_km:.0f} km")

        if rest_km <= 0:
            st.warning("Der Ölwechsel ist überfällig!")

        # Datum/KW-Prognose anhand der durchschnittlichen Fahrleistung aus der Historie.
        km_history = df.dropna(subset=["km_stand"]).sort_values("date")
        if len(km_history) >= 2:
            zeitspanne_tage = (km_history["date"].iloc[-1] - km_history["date"].iloc[0]).days
            km_differenz = km_history["km_stand"].iloc[-1] - km_history["km_stand"].iloc[0]
            if zeitspanne_tage > 0 and km_differenz > 0:
                km_pro_tag = km_differenz / zeitspanne_tage
                tage_bis_wechsel = rest_km / km_pro_tag
                prognose_datum = heute + pd.Timedelta(days=tage_bis_wechsel)
                prognose_kw = prognose_datum.isocalendar()[1]
                st.caption(
                    f"Voraussichtlich fällig: ca. {prognose_datum.strftime('%d.%m.%Y')} (KW {prognose_kw}), "
                    f"basierend auf ⌀ {km_pro_tag:.1f} km/Tag aus der Fahrhistorie."
                )
            else:
                st.caption("Noch nicht genug Fahrdaten für eine Datums-Prognose.")
        else:
            st.caption("Noch nicht genug Fahrdaten für eine Datums-Prognose (mind. zwei Kilometerstände nötig).")

    with st.expander("Ölwechsel-Einstellungen ändern"):
        with st.form("oil_update_form"):
            edit_interval = st.number_input(
                "Ölwechselintervall (km)", min_value=1000.0, step=500.0,
                value=float(interval_km), format="%.0f"
            )
            edit_vor_km = st.number_input(
                "Neuen Ölwechsel eintragen: vor wie vielen km? (leer lassen = keine Änderung)",
                min_value=0.0, step=100.0, value=None, format="%.0f"
            )
            edit_submitted = st.form_submit_button("Speichern")

        if edit_submitted:
            if edit_vor_km is not None and letzter_km_stand is None:
                st.error("Es ist noch kein Kilometerstand erfasst.")
            else:
                update_payload = {"interval_km": float(edit_interval)}
                if edit_vor_km is not None:
                    update_payload["last_oil_change_km"] = float(letzter_km_stand - edit_vor_km)
                supabase.table("oil_change_settings").update(update_payload).eq("id", 1).execute()
                st.success("Aktualisiert.")
                st.rerun()

# --- HISTORIE TABELLE: EINKLAPPBAR, PRO ZEILE EINZELN BEARBEITBAR ODER LÖSCHBAR ---
history_df = df.sort_values("date", ascending=False).reset_index(drop=True)

with st.expander(f"**Historie aller Transaktionen ({len(history_df)})**", expanded=False):
    if history_df.empty:
        st.info("Keine Transaktionen vorhanden.")
    else:
        header_col1, header_col2, header_col3, header_col4, header_col5, header_col6 = st.columns([2, 2, 2, 2, 1, 1])
        header_col1.markdown("**Datum**")
        header_col2.markdown("**Betrag**")
        header_col3.markdown("**Kilometerstand**")
        header_col4.markdown("**Preis/l**")
        header_col5.markdown("**Speichern**")
        header_col6.markdown("**Löschen**")

        for _, row in history_df.iterrows():
            row_id = row["id"]
            with st.form(f"edit_row_{row_id}", border=False):
                c1, c2, c3, c4, c5, c6 = st.columns([2, 2, 2, 2, 1, 1])
                edit_date = c1.date_input(
                    "Datum", value=row["date"].date(), key=f"date_{row_id}", label_visibility="collapsed"
                )
                edit_amount = c2.number_input(
                    "Betrag", value=float(row["amount"]), min_value=0.0, step=0.01, format="%.2f",
                    key=f"amount_{row_id}", label_visibility="collapsed"
                )
                edit_km = c3.number_input(
                    "Kilometerstand",
                    value=(float(row["km_stand"]) if pd.notna(row["km_stand"]) else None),
                    min_value=0.0, step=1.0, format="%.0f",
                    key=f"km_{row_id}", label_visibility="collapsed"
                )
                edit_preis = c4.number_input(
                    "Preis/l",
                    value=(float(row["preis_pro_liter"]) if pd.notna(row["preis_pro_liter"]) else None),
                    min_value=0.0, step=0.001, format="%.3f",
                    key=f"preis_{row_id}", label_visibility="collapsed"
                )
                save_clicked = c5.form_submit_button("💾")
                delete_clicked = c6.form_submit_button("🗑️")

            if save_clicked:
                if edit_preis is not None and edit_preis > 5:
                    st.error(
                        f"Preis pro Liter von {edit_preis:.3f} € wirkt unrealistisch hoch. "
                        "Bitte in Euro angeben (z. B. 1.75 statt 175)."
                    )
                elif edit_amount <= 0:
                    st.error("Betrag muss größer als 0 sein.")
                else:
                    supabase.table("transactions").update({
                        "date": str(edit_date),
                        "amount": float(edit_amount),
                        "km_stand": float(edit_km) if edit_km is not None else None,
                        "preis_pro_liter": float(edit_preis) if edit_preis is not None else None,
                    }).eq("id", row_id).execute()
                    st.success("Eintrag aktualisiert.")
                    st.rerun()

            if delete_clicked:
                supabase.table("transactions").delete().eq("id", row_id).execute()
                st.rerun()
