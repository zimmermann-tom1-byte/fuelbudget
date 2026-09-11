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
    return df

df = load_data()

# --- HEADER & SIDEBAR ---
st.title("FuelBudget 🚗⛽")
st.caption("Präzise Kraftstoffbudgetierung & Verbrauchsanalyse")

st.sidebar.header("Neuer Tankvorgang")
with st.sidebar.form("add_transaction_form", clear_on_submit=True):
    new_date = st.date_input("Datum", value=datetime.today())
    new_amount = st.number_input("Gesamtbetrag (€)", min_value=0.0, step=0.01, format="%.2f")
    submitted = st.form_submit_button("Transaktion speichern")

# --- PROGNOSE-PARAMETER (Sidebar) ---
# Muss VOR jedem moeglichen st.rerun() stehen: wird ein Widget in einem
# Skriptdurchlauf nicht instanziiert (z. B. weil st.rerun() vorher abbricht),
# setzt Streamlit dessen session_state beim naechsten Durchlauf auf den
# Default zurueck - unabhaengig vom key=. Reihenfolge daher bewusst so.
st.sidebar.markdown("---")
st.sidebar.header("Prognose-Parameter")
mode = st.sidebar.selectbox("Berechnungsmodus", ["EMA", "SMA", "WMA"], index=0, key="mode_select")
k_factor = st.sidebar.slider("Glättungsfaktor K (nur EMA)", min_value=0.01, max_value=1.0, value=0.15, step=0.01, key="k_factor_slider")
buffer_pct = st.sidebar.slider("Sicherheitspuffer (%)", min_value=-10, max_value=100, value=10, step=1, key="buffer_pct_slider")

# Neue Transaktion direkt in Supabase speichern
if submitted:
    if new_amount > 0:
        supabase.table("transactions").insert({
            "date": str(new_date),
            "amount": float(new_amount),
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
            supabase.table("transactions").delete().eq("id", row["id"]).execute()
            st.rerun()
