import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as bg
from datetime import datetime

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

# --- INITIAL DATA (36 Historical Transactions) ---
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

# State Initialization
if "transactions" not in st.session_state:
    st.session_state.transactions = pd.DataFrame(INITIAL_DATA)
    st.session_state.transactions["date"] = pd.to_datetime(st.session_state.transactions["date"])
    st.session_state.transactions["amount"] = st.session_state.transactions["amount"].astype(float)

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
    st.session_state.transactions, calc_mode, k_factor, buffer_percent
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
    st.metric(
        label="Erfasste Wochen / Gesamtausgaben",
        value=f"{len(weekly_df)} KW",
        delta=f"{st.session_state.transactions['amount'].sum():.2f} € Gesamt"
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
            new_row = pd.DataFrame([{
                "date": pd.to_datetime(new_date),
                "amount": float(new_amount),
                "location": new_location.strip() or "Tankstelle"
            }])
            st.session_state.transactions = pd.concat(
                [st.session_state.transactions, new_row], ignore_index=True
            )
            st.success("Erfolgreich hinzugefügt!")
            st.rerun()

with col_list:
    st.subheader("Transaktions-Historie")
    display_df = st.session_state.transactions.sort_values("date", ascending=False).copy()
    display_df["date"] = display_df["date"].dt.strftime("%d.%m.%Y")
    display_df["amount"] = display_df["amount"].map("{:.2f} €".format)
    display_df.rename(columns={"date": "Datum", "amount": "Betrag", "location": "Tankstelle"}, inplace=True)
    
    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        height=300
    )