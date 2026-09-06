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
    div[data-testid="stForm"] { background-color: #0f172a; border: 1px solid #1e293b; border-radius: 12px; }
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
        df = df.sort_values("date").reset_index(drop=True)
    return df

df = load_data()

# --- INITIAL DATA SEEDING (falls Datenbank leer ist) ---
if df.empty:
    initial_data = [
        {"date": "2024-01-05", "amount": 65.20, "km": 142000, "price_per_liter": 1.759, "liters": 37.07},
        {"date": "2024-01-18", "amount": 58.90, "km": 142650, "price_per_liter": 1.729, "liters": 34.07},
        {"date": "2024-02-02", "amount": 62.10, "km": 143300, "price_per_liter": 1.789, "liters": 34.71},
        {"date": "2024-02-15", "amount": 70.40, "km": 144050, "price_per_liter": 1.819, "liters": 38.70},
        {"date": "2024-03-01", "amount": 55.00, "km": 144650, "price_per_liter": 1.749, "liters": 31.45},
        {"date": "2024-03-14", "amount": 68.30, "km": 145400, "price_per_liter": 1.799, "liters": 37.97},
        {"date": "2024-03-28", "amount": 61.50, "km": 146050, "price_per_liter": 1.769, "liters": 34.77},
        {"date": "2024-04-10", "amount": 64.80, "km": 146750, "price_per_liter": 1.809, "liters": 35.82},
        {"date": "2024-04-24", "amount": 59.20, "km": 147380, "price_per_liter": 1.739, "liters": 34.04},
        {"date": "2024-05-07", "amount": 66.70, "km": 148100, "price_per_liter": 1.779, "liters": 37.49},
        {"date": "2024-05-21", "amount": 63.00, "km": 148780, "price_per_liter": 1.759, "liters": 35.82},
        {"date": "2024-06-04", "amount": 67.50, "km": 149500, "price_per_liter": 1.789, "liters": 37.73},
        {"date": "2024-06-18", "amount": 57.80, "km": 150120, "price_per_liter": 1.719, "liters": 33.62},
        {"date": "2024-07-02", "amount": 71.00, "km": 150900, "price_per_liter": 1.829, "liters": 38.82},
        {"date": "2024-07-16", "amount": 60.40, "km": 151550, "price_per_liter": 1.749, "liters": 34.53},
        {"date": "2024-07-30", "amount": 65.90, "km": 152260, "price_per_liter": 1.799, "liters": 36.63},
        {"date": "2024-08-12", "amount": 62.50, "km": 152930, "price_per_liter": 1.769, "liters": 35.33},
        {"date": "2024-08-26", "amount": 69.10, "km": 153680, "price_per_liter": 1.809, "liters": 38.20},
        {"date": "2024-09-09", "amount": 56.40, "km": 154300, "price_per_liter": 1.729, "liters": 32.62},
        {"date": "2024-09-23", "amount": 64.30, "km": 155000, "price_per_liter": 1.779, "liters": 36.14},
        {"date": "2024-10-07", "amount": 67.80, "km": 155740, "price_per_liter": 1.819, "liters": 37.27},
        {"date": "2024-10-21", "amount": 59.90, "km": 156390, "price_per_liter": 1.749, "liters": 34.25},
        {"date": "2024-11-04", "amount": 63.60, "km": 157080, "price_per_liter": 1.769, "liters": 35.95},
        {"date": "2024-11-18", "amount": 68.00, "km": 157820, "price_per_liter": 1.799, "liters": 37.80},
        {"date": "2024-12-02", "amount": 61.20, "km": 158480, "price_per_liter": 1.739, "liters": 35.19},
        {"date": "2024-12-16", "amount": 72.50, "km": 159280, "price_per_liter": 1.839, "liters": 39.42},
        {"date": "2024-12-30", "amount": 65.00, "km": 159980, "price_per_liter": 1.789, "liters": 36.33},
        {"date": "2025-01-12", "amount": 58.30, "km": 160600, "price_per_liter": 1.719, "liters": 33.91},
        {"date": "2025-01-26", "amount": 66.10, "km": 161310, "price_per_liter": 1.779, "liters": 37.16},
        {"date": "2025-02-09", "amount": 69.40, "km": 162060, "price_per_liter": 1.819, "liters": 38.15},
        {"date": "2025-02-23", "amount": 60.80, "km": 162720, "price_per_liter": 1.749, "liters": 34.76},
        {"date": "2025-03-08", "amount": 64.50, "km": 163420, "price_per_liter": 1.769, "liters": 36.46},
        {"date": "2025-03-22", "amount": 67.00, "km": 164150, "price_per_liter": 1.799, "liters": 37.24},
        {"date": "2025-04-05", "amount": 62.90, "km": 164830, "price_per_liter": 1.759, "liters": 35.76},
        {"date": "2025-04-19", "amount": 70.10, "km": 165600, "price_per_liter": 1.829, "liters": 38.33},
        {"date": "2025-05-03", "amount": 65.40, "km": 166310, "price_per_liter": 1.779, "liters": 36.76}
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
