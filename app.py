import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as bg
from datetime import datetime
from supabase import create_client

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
    </style>
""", unsafe_allow_html=True)

# --- HISTORISCHE STAMM-DATEN (36 Tankvorgänge) ---
INITIAL_DATA = [
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

# --- SUPABASE CLIENT INITIALIZATION ---
url = st.secrets["connections"]["supabase"]["SUPABASE_URL"]
key = st.secrets["connections"]["supabase"]["SUPABASE_KEY"]
supabase = create_client(url, key)

# --- AUTOMATISCHER INITIAL-UPLOAD IN SUPABASE ---
def seed_initial_data_if_empty():
    res = supabase.table("transactions").select("id", count="exact").execute()
    if res.count == 0 or len(res.data) == 0:
        supabase.table("transactions").insert(INITIAL_DATA).execute()

seed_initial_data_if_empty()

# --- DATA FETCHING FROM SUPABASE ---
def load_data():
    response = supabase.table("transactions").select("*").execute()
    df = pd.DataFrame(response.data)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df["amount"] = df["amount"].astype(float)
    else:
        df = pd.DataFrame(columns=["date", "amount", "location"])
    return df

transactions_df = load_data()

# --- HEADER ---
st.title("⛽ FuelBudget")
st.caption("Prädiktive Kraftstoffkosten-Budgetierung mittels statistischer Glättungsmodelle.")

# --- SIDEBAR / CONTROLS ---
st.sidebar.header("⚙️ Modell-Konfiguration")

calc_mode = st.sidebar.selectbox(
    "Berechnungs-Modus",
    ["EMA", "SMA", "WMA"],
    help="SMA: Einfacher Durchschnitt | WMA: Linear gewichtet | EMA: Exponentiell geglättet"
)

k_factor = st.sidebar.slider(
    "EMA-Glättungsfaktor (K)",
    min_value=0.01,
    max_value=1.00,
    value=0.15,
    step=0.01,
    disabled=(calc_mode != "EMA")
)

buffer_percent = st.sidebar.slider(
    "Sicherheits-Puffer (%)",
    min_value=0,
    max_value=50,
    value=10,
    step=1
)

# --- MATHEMATICAL AGGREGATION ENGINE ---
def compute_budget(df, mode, k, buffer):
    if df.empty:
        return pd.DataFrame(), 0.0, 0.0

    df_sorted = df.sort_values("date").copy()
    
    # 1. Weekly Grouping (Monday to Sunday)
    df_sorted["year_week"] = df_sorted["date"].dt.to_period("W-SUN")
    weekly_aggregated = df_sorted.groupby("year_week")["amount"].sum().reset_index()
    weekly_aggregated.rename(columns={"amount": "actual"}, inplace=True)

    # 2. Reindex to fill missing weeks with 0.00 €
    full_period_range = pd.period_range(
        start=weekly_aggregated["year_week"].min(),
        end=weekly_aggregated["year_week"].max(),
        freq="W-SUN"
    )
    weekly_full = weekly_aggregated.set_index("year_week").reindex(full_period_range, fill_value=0.0).reset_index()
    weekly_full.rename(columns={"index": "year_week"}, inplace=True)
    weekly_full["kw_label"] = weekly_full["year_week"].astype(str)

    actual_series = weekly_full["actual"].values
    n_weeks = len(actual_series)
    budget_base = np.zeros(n_weeks)

    # 3. Model Calculations
    if mode == "SMA":
        for i in range(n_weeks):
            budget_base[i] = np.mean(actual_series[:i+1])
        next_week_base = np.mean(actual_series)

    elif mode == "WMA":
        for i in range(n_weeks):
            weights = np.arange(1, i + 2)
            budget_base[i] = np.sum(actual_series[:i+1] * weights) / np.sum(weights)
        weights_all = np.arange(1, n_weeks + 1)
        next_week_base = np.sum(actual_series * weights_all) / np.sum(weights_all)

    elif mode == "EMA":
        initial_sma = np.mean(actual_series)
        running_ema = initial_sma
        for i in range(n_weeks):
            if i == 0:
                running_ema = initial_sma
            else:
                running_ema = (k * actual_series[i]) + ((1 - k) * running_ema)
            budget_base[i] = running_ema
        next_week_base = (k * actual_series[-1]) + ((1 - k) * budget_base[-1])

    # 4. Apply Buffer & Skimmed Calculations
    weekly_full["budget_base"] = budget_base
    weekly_full["budget_buffered"] = budget_base * (1 + buffer / 100.0)
    weekly_full["skimmed"] = np.maximum(0.0, weekly_full["budget_buffered"] - weekly_full["actual"])

    next_week_budget = next_week_base * (1 + buffer / 100.0)
    total_skimmed = weekly_full["skimmed"].sum()

    return weekly_full, next_week_budget, total_skimmed

weekly_df, next_budget, total_skimmed = compute_budget(
    transactions_df, calc_mode, k_factor, buffer_percent
)

# --- DASHBOARD METRICS ---
col1, col2, col3 = st.columns(3)

# Hochrechnung der monatlichen Kosten: Wochenbudget * 52 Wochen / 12 Monate
monthly_budget = (next_budget * 52) / 12

with col1:
    st.metric(
        label="Budget Kommende KW (inkl. Puffer)",
        value=f"{next_budget:.2f} €",
        delta=f"+{buffer_percent}% Puffer"
    )

with col2:
    st.metric(
        label="Kosten pro Monat (Hochrechnung)",
        value=f"{monthly_budget:.2f} €",
        delta="Basis: (KW-Budget × 52) / 12"
    )

with col3:
    total_sum = transactions_df["amount"].sum() if not transactions_df.empty else 0.0
    st.metric(
        label="Erfasste Wochen / Gesamtausgaben",
        value=f"{len(weekly_df)} KW",
        delta=f"{total_sum:.2f} € Gesamt"
    )

st.markdown("---")

# --- CHART SECTION ---
st.subheader("Wöchentlicher Ausgaben- & Budgetverlauf")

if not weekly_df.empty:
    fig = bg.Figure()

    # Actual Expenses Line
    fig.add_trace(bg.Scatter(
        x=weekly_df["kw_label"],
        y=weekly_df["actual"],
        mode="lines+markers",
        name="Reale Ausgaben",
        line=dict(color="#94a3b8", width=2, dash="dash")
    ))

    # Buffered Budget Line
    fig.add_trace(bg.Scatter(
        x=weekly_df["kw_label"],
        y=weekly_df["budget_buffered"],
        mode="lines+markers",
        name=f"Budget ({calc_mode})",
        line=dict(color="#10b981", width=3)
    ))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0f172a",
        plot_bgcolor="#0f172a",
        margin=dict(l=20, r=20, t=30, b=20),
        xaxis_title="Kalenderwoche",
        yaxis_title="Betrag (€)",
        hovermode="x unified"
    )

    st.plotly_chart(fig, use_container_width=True)

# --- INPUT FORM & TRANSACTION LIST ---
col_form, col_list = st.columns([1, 2])

with col_form:
    st.subheader("Neues Tank-Ereignis")
    with st.form("add_tx_form", clear_on_submit=True):
        new_date = st.date_input("Datum", datetime.now())
        new_amount = st.number_input("Betrag (€)", min_value=0.01, step=0.01, format="%.2f")
        new_location = st.text_input("Tankstelle / Ort", placeholder="z. B. JET Tankstelle")
        submit_btn = st.form_submit_button("Transaktion Speichern")

        if submit_btn:
            payload = {
                "date": str(new_date),
                "amount": float(new_amount),
                "location": new_location.strip() or "Tankstelle"
            }
            supabase.table("transactions").insert(payload).execute()
            st.success("Erfolgreich in Supabase gespeichert!")
            st.rerun()

with col_list:
    st.subheader("Transaktions-Historie")
    if not transactions_df.empty:
        display_df = transactions_df.sort_values("date", ascending=False).copy()
        display_df["date"] = display_df["date"].dt.strftime("%d.%m.%Y")
        display_df["amount"] = display_df["amount"].map("{:.2f} €".format)
        
        cols_to_show = [col for col in ["date", "amount", "location"] if col in display_df.columns]
        display_df = display_df[cols_to_show]
        display_df.rename(columns={"date": "Datum", "amount": "Betrag", "location": "Tankstelle"}, inplace=True)
        
        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            height=300
        )
    else:
        st.info("Noch keine Transaktionen in Supabase vorhanden.")
