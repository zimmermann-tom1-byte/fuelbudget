import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime
from st_supabase_connection import SupabaseConnection

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

# --- SUPABASE DATABASE CONNECTION ---
from supabase import create_client

url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
st_supabase = create_client(url, key)

def load_data():
    response = st_supabase.table("transactions").select("*").execute()
    df = pd.DataFrame(response.data)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date", ascending=False).reset_index(drop=True)
    return df

df = load_data()

# --- INITIAL DATA SEEDING (falls Datenbank leer ist) ---
if df.empty:
    initial_data = [
    {"date": "2026-02-11", "amount": 34.39, "location": "SB Tank 9893"},
    {"date": "2026-02-13", "amount": 26.99, "location": "SB Tank 9893"},
    {"date": "2026-02-17", "amount": 57.40, "location": "JET Tankstelle"},
    {"date": "2026-02-22", "amount": 3.54, "location": "Aral"},
    {"date": "2026-02-27", "amount": 9.90, "location": "SB Tank 9893"},
    {"date": "2026-02-28", "amount": 57.25, "location": "TANKSTELLE P. BECKER"},
    {"date": "2026-02-28", "amount": 20.15, "location": "TANKSTELLE P. BECKER"},
    {"date": "2026-03-04", "amount": 29.72, "location": "AVIA"},
    {"date": "2026-03-09", "amount": 57.03, "location": "JET Tankstelle"},
    {"date": "2026-03-15", "amount": 46.77, "location": "AVIA"},
    {"date": "2026-03-22", "amount": 30.01, "location": "JET Tankstelle"},
    {"date": "2026-03-27", "amount": 86.79, "location": "TANKSTELLE P. BECKER"},
    {"date": "2026-04-05", "amount": 62.92, "location": "JET Tankstelle"},
    {"date": "2026-04-10", "amount": 4.49, "location": "DE PANJERD"},
    {"date": "2026-04-12", "amount": 5.25, "location": "TotalEnergies"},
    {"date": "2026-04-12", "amount": 3.99, "location": "Esso"},
    {"date": "2026-04-17", "amount": 57.89, "location": "TANKSTELLE P. BECKER"},
    {"date": "2026-04-29", "amount": 71.83, "location": "CALPAM TANKAUTOMAT"},
    {"date": "2026-04-30", "amount": 3.74, "location": "Aral"},
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
    {"date": "2026-07-05", "amount": 5.23, "location": "JET Tankstelle"},
    {"date": "2026-07-13", "amount": 73.39, "location": "Tankstelle"},
    {"date": "2026-07-20", "amount": 50.00, "location": "Tankstelle"},
    {"date": "2026-07-30", "amount": 69.70, "location": "Tankstelle"},
    {"date": "2026-08-01", "amount": 39.84, "location": "Tankstelle"},
    {"date": "2026-08-12", "amount": 60.97, "location": "Tankstelle"}
]
    st_supabase.table("transactions").insert(initial_data).execute()
    df = load_data()

# --- HEADER & SIDEBAR ---
st.title("FuelBudget 🚗⛽")
st.caption("Präzise Kraftstoffbudgetierung & Verbrauchsanalyse")

st.sidebar.header("Neuer Tankvorgang")
with st.sidebar.form("add_transaction_form", clear_on_submit=True):
    new_date = st.date_input("Datum", value=datetime.today())
    new_amount = st.number_input("Gesamtbetrag (€)", min_value=0.0, step=0.01, format="%.2f")
    new_km = st.number_input("Kilometerstand", min_value=0, step=1)
    new_ppl = st.number_input("Preis pro Liter (€)", min_value=0.0, step=0.001, format="%.3f")
    
    submitted = st.form_submit_button("Transaktion speichern")
    
    if submitted:
        if new_ppl > 0 and new_amount > 0:
            calc_liters = round(new_amount / new_ppl, 2)
            
            # Neue Transaktion direkt in Supabase speichern
            st_supabase.table("transactions").insert([{
                "date": str(new_date),
                "amount": float(new_amount),
                "km": int(new_km),
                "price_per_liter": float(new_ppl),
                "liters": float(calc_liters)
            }]).execute()
            
            st.success("Erfolgreich in der Cloud gespeichert!")
            st.rerun()
        else:
            st.error("Bitte erst alle Felder gültig ausfüllen.")

# --- BERECHNUNG DER PROGNOSEMODELLE ---
amounts = df["amount"]
n_samples = len(amounts)

# Gleitende Durchschnitte (Window = 4)
window = min(n_samples, 4)

# 1. Simple Moving Average (SMA)
sma = amounts.rolling(window=window).mean().iloc[-1]

# 2. Weighted Moving Average (WMA)
weights = np.arange(1, window + 1)
wma = np.average(amounts.tail(window), weights=weights)

# 3. Exponential Moving Average (EMA)
ema = amounts.ewm(span=window, adjust=False).mean().iloc[-1]

# Kombinierte Basis-Prognose
base_forecast = (sma + wma + ema) / 3

# Einstellbarer Sicherheitspuffer in der Sidebar
st.sidebar.markdown("---")
st.sidebar.header("Prognose-Parameter")
buffer_pct = st.sidebar.slider("Sicherheitspuffer (%)", min_value=0, max_value=20, value=5, step=1)

final_weekly_forecast = base_forecast * (1 + buffer_pct / 100.0)
final_monthly_projection = (final_weekly_forecast * 52) / 12

# --- DASHBOARD METRIKEN ---
col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        label="Budget Kommende KW",
        value=f"{final_weekly_forecast:.2f} €",
        delta=f"+{buffer_pct}% Puffer" if buffer_pct > 0 else "Kein Puffer"
    )

with col2:
    st.metric(
        label="Hochrechnung / Monat",
        value=f"{final_monthly_projection:.2f} €"
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

# --- HISTORIE TABELLE ---
st.markdown("### Historie aller Transaktionen")
st.dataframe(
    df[["date", "amount", "km", "price_per_liter", "liters"]].sort_values("date", ascending=False),
    use_container_width=True,
    column_config={
        "date": st.column_config.DateColumn("Datum", format="DD.MM.YYYY"),
        "amount": st.column_config.NumberColumn("Gesamtbetrag", format="%.2f €"),
        "km": st.column_config.NumberColumn("Kilometerstand", format="%d km"),
        "price_per_liter": st.column_config.NumberColumn("Preis / Liter", format="%.3f €"),
        "liters": st.column_config.NumberColumn("Liter", format="%.2f L")
    }
)
