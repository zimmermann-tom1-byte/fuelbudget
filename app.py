import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime
import uuid

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
    df = pd.DataFrame(st.session_state.transactions)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date", ascending=False).reset_index(drop=True)
    return df

# --- LOKALER DATENSPEICHER (Platzhalter, bis Supabase wieder angebunden ist) ---
if "transactions" not in st.session_state:
    st.session_state.transactions = []

# Absicherung: aeltere Eintraege ohne "id" (aus frueheren Versionen) nachtraeglich ergaenzen
for t in st.session_state.transactions:
    if "id" not in t:
        t["id"] = str(uuid.uuid4())

df = load_data()
# --- INITIAL DATA SEEDING (falls Datenbank leer ist) ---
if df.empty:
    initial_data = [
        {"date": "2026-02-11", "amount": 34.39, "location": "SB Tank 9893"},
        {"date": "2026-02-13", "amount": 26.99, "location": "SB Tank 9893"},
        {"date": "2026-02-17", "amount": 57.40, "location": "JET Tankstelle"},
        {"date": "2026-02-27", "amount": 9.90, "location": "SB Tank 9893"},
        {"date": "2026-02-28", "amount": 57.25, "location": "TANKSTELLE P. BECKER"},
        {"date": "2026-02-28", "amount": 20.15, "location": "TANKSTELLE P. BECKER"},
        {"date": "2026-03-04", "amount": 29.72, "location": "AVIA"},
        {"date": "2026-03-09", "amount": 57.03, "location": "JET Tankstelle"},
        {"date": "2026-03-15", "amount": 46.77, "location": "AVIA"},
        {"date": "2026-03-22", "amount": 30.01, "location": "JET Tankstelle"},
        {"date": "2026-03-27", "amount": 86.79, "location": "TANKSTELLE P. BECKER"},
        {"date": "2026-04-05", "amount": 62.92, "location": "JET Tankstelle"},
        {"date": "2026-04-17", "amount": 57.89, "location": "TANKSTELLE P. BECKER"},
        {"date": "2026-04-29", "amount": 71.83, "location": "CALPAM TANKAUTOMAT"},
        {"date": "2026-05-06", "amount": 36.11, "location": "CALPAM TANKAUTOMAT"},
        {"date": "2026-05-08", "amount": 26.35, "location": "SB Tank 9893"},
        {"date": "2026-05-16", "amount": 61.09, "location": "Raiffeisen Westfalen Mitte"},
        {"date": "2026-05-27", "amount": 68.25, "location": "SB Tank 9893"},
        {"date": "2026-06-07", "amount": 30.33, "location": "TANKSTELLE P. BECKER"},
        {"date": "2026-06-10", "amount": 16.08, "location": "JET Tankstelle"},
        {"date": "2026-06-15", "amount": 49.95, "location": "JET Tankstelle"},
        {"date": "2026-06-17", "amount": 16.01, "location": "SB Tank 9893"},
        {"date": "2026-06-27", "amount": 63.63, "location": "CALPAM TANKAUTOMAT"},
        {"date": "2026-06-30", "amount": 64.81, "location": "SB Tank 9893"},
        {"date": "2026-07-03", "amount": 26.88, "location": "CALPAM TANKAUTOMAT"},
        {"date": "2026-07-13", "amount": 73.39, "location": "Tankstelle"},
        {"date": "2026-07-20", "amount": 50.00, "location": "Tankstelle"},
        {"date": "2026-07-30", "amount": 69.70, "location": "Tankstelle"},
        {"date": "2026-08-01", "amount": 39.84, "location": "Tankstelle"},
        {"date": "2026-08-12", "amount": 60.97, "location": "Tankstelle"},
        {"date": "2026-08-19", "amount": 46.50, "location": "Tankstelle"},
        {"date": "2026-09-02", "amount": 69.18, "location": "Tankstelle"}
    ]
    for t in initial_data:
        t["id"] = str(uuid.uuid4())
    st.session_state.transactions = initial_data
    df = load_data()

# --- HEADER & SIDEBAR ---
st.title("FuelBudget 🚗⛽")
st.caption("Präzise Kraftstoffbudgetierung & Verbrauchsanalyse")

st.sidebar.header("Neuer Tankvorgang")
with st.sidebar.form("add_transaction_form", clear_on_submit=True):
    new_date = st.date_input("Datum", value=datetime.today())
    new_amount = st.number_input("Gesamtbetrag (€)", min_value=0.0, step=0.01, format="%.2f")
    submitted = st.form_submit_button("Transaktion speichern")
            
# Neue Transaktion direkt in Supabase speichern
if submitted:
    if new_amount > 0:
        st.session_state.transactions.append({
            "id": str(uuid.uuid4()),
            "date": str(new_date),
            "amount": float(new_amount),
        })
        st.success("Transaktion gespeichert (nur für diese Sitzung, noch nicht dauerhaft).")
        st.rerun()
    else:
        st.error("Bitte erst alle Felder gültig ausfüllen.")

# --- PROGNOSE-PARAMETER (Sidebar) ---
st.sidebar.markdown("---")
st.sidebar.header("Prognose-Parameter")
mode = st.sidebar.selectbox("Berechnungsmodus", ["EMA", "SMA", "WMA"], index=0)
k_factor = st.sidebar.slider("Glättungsfaktor K (nur EMA)", min_value=0.01, max_value=1.0, value=0.15, step=0.01)
buffer_pct = st.sidebar.slider("Sicherheitspuffer (%)", min_value=-10, max_value=100, value=10, step=1)

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

# Reale Ausgaben
fig.add_trace(go.Scatter(
    x=df["date"], 
    y=df["amount"],
    mode="lines+markers",
    name="Reale Ausgaben (€)",
    line=dict(color="#38bdf8", width=3),
    marker=dict(size=6)
))

# SMA Linie
df["SMA"] = amounts.rolling(window=window).mean()
fig.add_trace(go.Scatter(
    x=df["date"], 
    y=df["SMA"],
    mode="lines",
    name="SMA (Gleitender Durchschnitt)",
    line=dict(color="#f59e0b", width=1.5, dash="dash")
))

fig.update_layout(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=20, r=20, t=30, b=20),
    xaxis=dict(title="Datum", gridcolor="#1e293b"),
    yaxis=dict(title="Betrag (€)", gridcolor="#1e293b"),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
)

st.plotly_chart(fig, use_container_width=True)

# --- HISTORIE TABELLE MIT LÖSCH-BUTTON PRO ZEILE ---
st.markdown("### Historie aller Transaktionen")

history_df = df.sort_values("date", ascending=False).reset_index(drop=True)

if history_df.empty:
    st.info("Keine Transaktionen vorhanden.")
else:
    header_col1, header_col2, header_col3 = st.columns([3, 2, 1])
    header_col1.markdown("**Datum**")
    header_col2.markdown("**Betrag**")
    header_col3.markdown("**Löschen**")

    for _, row in history_df.iterrows():
        col1, col2, col3 = st.columns([3, 2, 1])
        col1.write(row["date"].strftime("%d.%m.%Y"))
        col2.write(f"{row['amount']:.2f} €")
        if col3.button("🗑️", key=f"delete_{row['id']}"):
            st.session_state.transactions = [
                t for t in st.session_state.transactions if t["id"] != row["id"]
            ]
            st.rerun()
