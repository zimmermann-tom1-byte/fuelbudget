import calendar
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from supabase import create_client

import importlib

import forecast

# Streamlit Cloud laedt nach einem git-Update nur app.py neu; bereits
# importierte Hilfsmodule bleiben sonst in alter Version im Speicher
# (-> ImportError bei neuen Funktionen). Reload kostet bei dieser Dateigroesse
# praktisch nichts.
importlib.reload(forecast)
from forecast import complete_weeks, forecast_trend, km_conflict, verbrauch_intervalle, weekly_amounts  # noqa: E402

# --- PAGE CONFIGURATION (muss der erste Streamlit-Aufruf sein) ---
st.set_page_config(
    page_title="FuelBudget",
    page_icon="⛽",
    layout="wide"
)

TZ = ZoneInfo("Europe/Berlin")  # Streamlit Cloud laeuft in UTC
heute = pd.Timestamp(datetime.now(TZ).date())

# Custom Dark Theme Styling
st.markdown("""
<style>
    .main { background-color: #020617; color: #f8fafc; }
    .stMetric { background-color: #0f172a; border: 1px solid #1e293b; padding: 15px; border-radius: 12px; }
    div[data-testid="stForm"] { background-color: #0f172a; border: 1px solid #1e293b; }
    div[data-testid="stDataFrame"] { background-color: #0f172a; border-radius: 12px; }
</style>
""", unsafe_allow_html=True)


# --- SUPABASE VERBINDUNG ---
# Client nur einmal pro Server-Prozess erzeugen statt bei jedem Rerun.
@st.cache_resource
def get_supabase():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


supabase = get_supabase()


# Streamlit fuehrt das GESAMTE Skript bei jeder Widget-Aenderung neu aus (jeder
# Slider-Zug, jede Modus-Auswahl). Ohne Cache ging dabei jedes Mal eine Abfrage
# an Supabase raus - das war der Hauptgrund, warum sich die Prognose-Parameter
# traege anfuehlten. Nach jedem Schreibzugriff wird der Cache geleert.
@st.cache_data(ttl=600, show_spinner=False)
def load_data():
    response = supabase.table("transactions").select("*").execute()
    df = pd.DataFrame(response.data)
    # Leere Tabelle -> DataFrame ohne Spalten; Spalten trotzdem anlegen,
    # sonst stuerzt die App spaeter mit KeyError ab.
    for col in ("id", "date", "amount", "km_stand", "preis_pro_liter"):
        if col not in df.columns:
            df[col] = pd.Series(dtype="object")
    df["date"] = pd.to_datetime(df["date"])
    for col in ("amount", "km_stand", "preis_pro_liter"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # Bei mehreren Tankungen am selben Tag nach km_stand sortieren, damit die
    # km-Differenzen in der Verbrauchsberechnung stimmen.
    return df.sort_values(["date", "km_stand"], ascending=False, na_position="last").reset_index(drop=True)


@st.cache_data(ttl=600, show_spinner=False)
def load_oil_settings():
    try:
        response = supabase.table("oil_change_settings").select("*").limit(1).execute()
        return {"ok": True, "data": response.data[0] if response.data else {}}
    except Exception as exc:  # Tabelle fehlt oder Verbindung gestoert
        return {"ok": False, "error": str(exc)}


def nach_schreibzugriff(meldung: str):
    """Cache leeren, Meldung fuer den naechsten Durchlauf merken, neu laden.
    (Ein st.success() direkt vor st.rerun() ist nie zu sehen.)"""
    load_data.clear()
    load_oil_settings.clear()
    st.session_state["flash"] = meldung
    st.rerun()


df = load_data()

if "flash" in st.session_state:
    st.toast(st.session_state.pop("flash"), icon="✅")

# --- HEADER & SIDEBAR ---
st.title("FuelBudget 🚗⛽")
st.caption("Präzise Kraftstoffbudgetierung & Verbrauchsanalyse")

st.sidebar.header("Neuer Tankvorgang")
with st.sidebar.form("add_transaction_form", clear_on_submit=True):
    new_date = st.date_input("Datum", value=heute.date(), format="DD.MM.YYYY")
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
**Grundlage:** Gerechnet wird mit deinen Ausgaben **pro Kalenderwoche** – nicht pro Tankvorgang. Fährst du mehr, tankst du öfter, und genau das soll die Prognose sehen. Die laufende Woche zählt erst mit, wenn sie abgeschlossen ist.

**SMA – Simple Moving Average:** Jede Woche im Betrachtungszeitraum zählt gleich viel. → Robuste, schwankungsarme Referenz, wenn dein Fahrverhalten stabil ist.

**WMA – Weighted Moving Average:** Innerhalb des Betrachtungszeitraums zählen neuere Wochen linear mehr als ältere. → Kompromiss: reagiert schneller als SMA, ohne alte Wochen ganz zu verwerfen.

**EMA – Exponential Moving Average:** Neuere Wochen werden exponentiell stärker gewichtet, gesteuert über K. → Die beste Wahl, wenn sich dein Fahrverhalten öfter ändert und die Prognose zeitnah mitziehen soll.
""")

window_option = st.sidebar.select_slider(
    "Betrachtungszeitraum (Wochen, nur SMA/WMA)",
    options=[4, 8, 12, 26, 52, "Alle"], value=12, key="window_slider",
    disabled=(mode == "EMA"),
)
with st.sidebar.popover("ℹ️ Warum ein Zeitraum?", width="stretch"):
    st.markdown("""
**Das Grundproblem:** Bei einem Durchschnitt über *alle* Wochen hat jede neue Woche immer weniger Gewicht. Nach einem Jahr verschiebt eine einzelne Woche die Prognose kaum noch – die Prognose wird mit jeder Woche träger.

**Kurzer Zeitraum (4–8 Wochen):** Zieht schnell mit, wenn du mehr oder weniger fährst, schwankt aber stärker.

**Langer Zeitraum (26 Wochen bis „Alle“):** Sehr ruhig, merkt Veränderungen aber erst spät.
""")

k_factor = st.sidebar.slider(
    "Glättungsfaktor K (nur EMA)", min_value=0.01, max_value=1.0, value=0.15, step=0.01,
    key="k_factor_slider", disabled=(mode != "EMA"),
)
with st.sidebar.popover("ℹ️ Was steuert dieser Regler?", width="stretch"):
    st.markdown("""
**Das Grundproblem:** Bei EMA muss festgelegt werden, *wie schnell* "schnell reagieren" genau bedeutet – genau das steuert K. Er bestimmt die Balance zwischen Stabilität und Reaktionsgeschwindigkeit.

**Hoher K-Wert (nah an 1,0):** Fast nur die letzte Woche zählt. Die Prognose "springt" stark mit – reagiert sofort auf Veränderungen, ist aber anfällig für einzelne ungewöhnliche Wochen.

**Niedriger K-Wert (nah an 0,01):** Die Prognose ändert sich nur sehr langsam, auch wenn sich das tatsächliche Fahrverhalten schon geändert hat – dafür ist sie robust gegen Ausreißer.

Faustregel: K ≈ 2 / (Anzahl Wochen + 1). K = 0,15 entspricht also grob den letzten 12 Wochen.
""")

buffer_pct = st.sidebar.slider("Sicherheitspuffer (%)", min_value=-10, max_value=100, value=10, step=1, key="buffer_pct_slider")
with st.sidebar.popover("ℹ️ Wozu dient dieser Puffer?", width="stretch"):
    st.markdown("""
**Das Grundproblem:** Der berechnete SMA-/WMA-/EMA-Wert ist eine reine statistische Erwartung – es ist kein Sicherheitsspielraum für Unvorhergesehenes eingerechnet. Der Puffer erlaubt dir, diesen Erwartungswert bewusst nach oben oder unten zu verschieben.

**Positiver Puffer:** Schafft eine Sicherheitsmarge nach oben – sinnvoll, wenn du lieber etwas mehr Budget einplanst, falls unerwartet mehr gefahren wird oder die Spritpreise steigen, statt am Monatsende negativ überrascht zu werden.

**Negativer Puffer:** Setzt bewusst ein knapperes Budget an, als der statistische Durchschnitt nahelegt – sinnvoll als Sparziel, oder wenn du weißt, dass du künftig strukturell weniger fahren wirst (z. B. Fahrgemeinschaft, Homeoffice), die historischen Daten das aber noch nicht widerspiegeln.
""")

window = None if window_option == "Alle" else int(window_option)

# Neue Transaktion direkt in Supabase speichern
vorhandene_km = df["km_stand"].dropna()
letzter_km_stand = vorhandene_km.max() if not vorhandene_km.empty else None

if submitted:
    # Realistische Kraftstoffpreise liegen deutlich unter 5 €/l - typischer
    # Fehler ist die Eingabe des Tankstellen-Anzeigewerts in Cent (z. B. 175
    # statt 1.75), was die Verbrauchsberechnung um den Faktor 100 verfaelscht.
    km_fehler = km_conflict(df, pd.Timestamp(new_date), new_km)
    if new_preis_pro_liter is not None and new_preis_pro_liter > 5:
        st.sidebar.error(
            f"Preis pro Liter von {new_preis_pro_liter:.3f} € wirkt unrealistisch hoch. "
            "Bitte in Euro angeben (z. B. 1.75 statt 175)."
        )
    elif km_fehler:
        st.sidebar.error(km_fehler)
    elif new_amount > 0:
        supabase.table("transactions").insert({
            "date": str(new_date),
            "amount": float(new_amount),
            "km_stand": float(new_km) if new_km is not None else None,
            "preis_pro_liter": float(new_preis_pro_liter) if new_preis_pro_liter is not None else None,
        }).execute()
        nach_schreibzugriff("Transaktion dauerhaft in der Cloud gespeichert!")
    else:
        st.sidebar.error("Bitte einen Betrag größer 0 eingeben.")

# --- BERECHNUNG DER PROGNOSEMODELLE (auf Wochenbasis) ---
amounts = df["amount"]
n_samples = len(amounts)

weekly = weekly_amounts(df, heute)
basis = complete_weeks(weekly, heute)
if basis.empty:
    # Noch keine abgeschlossene Woche: notgedrungen mit der laufenden rechnen
    basis = weekly

trend = forecast_trend(basis, mode, window, k_factor)
base_forecast = float(trend.iloc[-1]) if not trend.empty else 0.0

final_weekly_forecast = base_forecast * (1 + buffer_pct / 100.0)
final_monthly_projection = (final_weekly_forecast * 52) / 12

# --- DASHBOARD METRIKEN ---
col1, col2, col3 = st.columns(3)
puffer_text = f"{buffer_pct:+d}% Puffer" if buffer_pct != 0 else "Kein Puffer"
modus_text = mode if mode == "EMA" else f"{mode}, {window_option if window else 'alle'} Wo."

with col1:
    st.metric(label=f"Budget kommende KW ({modus_text})", value=f"{final_weekly_forecast:.2f} €", delta=puffer_text)

with col2:
    st.metric(label=f"Hochrechnung / Monat ({modus_text})", value=f"{final_monthly_projection:.2f} €", delta=puffer_text)

with col3:
    st.metric(label="Gesamtausgaben", value=f"{amounts.sum():.2f} €", delta=f"{n_samples} Tankungen")

# --- VISUALISIERUNG (PLOTLY CHART) ---
st.markdown("### Ausgabenverlauf & Prognosemodelle")

fig = go.Figure()

if not weekly.empty:
    laufend = ~weekly.index.isin(basis.index)
    fig.add_trace(go.Bar(
        x=weekly.index,
        y=weekly.values,
        name="Reale Ausgaben pro KW (€)",
        marker=dict(color=np.where(laufend, "rgba(56, 189, 248, 0.35)", "#38bdf8")),
        hovertemplate="KW ab %{x|%d.%m.%Y}: %{y:.2f} €<extra></extra>",
    ))

    # Prognose-Trend: exakt dieselbe Berechnung wie die Metrik oben
    fig.add_trace(go.Scatter(
        x=trend.index,
        y=trend.values,
        mode="lines",
        name=f"Prognose-Trend ({modus_text})",
        line=dict(color="#f59e0b", width=2.5, shape="spline")
    ))

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

st.plotly_chart(fig, width="stretch")
if not weekly.empty and laufend.any():
    st.caption("Heller Balken = laufende Woche, fließt erst nach Wochenende in die Prognose ein.")

# --- KILOMETER & VERBRAUCH ---
st.markdown("### Kilometer & Verbrauch")

if df.empty:
    st.info("Keine Transaktionen vorhanden.")
else:
    km_chart_df = df.dropna(subset=["km_stand"]).sort_values("date")
    verbrauch_chart_df = verbrauch_intervalle(df, fenster_km=1000)

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
            st.plotly_chart(km_fig, width="stretch")

    with verbrauch_col:
        st.markdown("**Verbrauch (L/100km)**")
        if verbrauch_chart_df.empty:
            st.info("Noch nicht genug Daten für eine Verbrauchsberechnung (Preis/Liter und mind. zwei Kilometerstände nötig).")
        else:
            gesamt_verbrauch = verbrauch_chart_df["liter"].sum() / verbrauch_chart_df["gefahrene_km"].sum() * 100
            verbrauch_fig = go.Figure()
            # Einzelwerte nur blass: wer nicht jedes Mal volltankt, bekommt hier
            # zwangslaeufig Ausreisser (zu viel/zu wenig Liter fuer die Strecke).
            verbrauch_fig.add_trace(go.Bar(
                x=verbrauch_chart_df["date"],
                y=verbrauch_chart_df["verbrauch"],
                name="Pro Tankvorgang",
                marker=dict(color="rgba(245, 158, 11, 0.35)"),
                customdata=verbrauch_chart_df[["gefahrene_km", "liter"]].to_numpy(),
                hovertemplate="%{x|%d.%m.%Y}: %{y:.2f} L/100km<br>%{customdata[0]:.0f} km, %{customdata[1]:.1f} L<extra></extra>",
            ))
            verbrauch_fig.add_trace(go.Scatter(
                x=verbrauch_chart_df["date"],
                y=verbrauch_chart_df["verbrauch_gleitend"],
                mode="lines+markers",
                name="Gleitend (≥ 1000 km)",
                line=dict(color="#f59e0b", width=2.5),
                marker=dict(size=5),
                hovertemplate="%{x|%d.%m.%Y}: %{y:.2f} L/100km (gleitend)<extra></extra>",
            ))
            verbrauch_fig.add_hline(
                y=gesamt_verbrauch, line_dash="dash", line_color="#64748b", line_width=1.5,
                annotation_text=f"⌀ {gesamt_verbrauch:.2f}", annotation_position="top left",
                annotation_font_color="#94a3b8",
            )
            verbrauch_fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=20, r=20, t=20, b=20),
                xaxis=dict(title="Datum", gridcolor="#1e293b"),
                yaxis=dict(title="L/100km", gridcolor="#1e293b", rangemode="tozero"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            st.plotly_chart(verbrauch_fig, width="stretch")

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

monat_df = df[
    (df["date"].dt.year == heute.year) & (df["date"].dt.month == heute.month)
].sort_values("date").reset_index(drop=True)
monatsname = f"{MONATSNAMEN[heute.month - 1]} {heute.year}"

# Restprognose nur fuer die VERBLEIBENDEN Tage des Monats (heute eingeschlossen),
# nicht "volle Monatshochrechnung minus bisher ausgegeben".
tage_im_monat = calendar.monthrange(heute.year, heute.month)[1]
resttage = tage_im_monat - heute.day + 1
prognostizierter_rest = final_weekly_forecast / 7 * resttage
ausgegeben_monat = monat_df["amount"].sum()
erwartet_monat = ausgegeben_monat + prognostizierter_rest

monat_chart_col, monat_info_col = st.columns([1, 3])

with monat_chart_col:
    monat_fig = go.Figure()
    for i, row in monat_df.iterrows():
        monat_fig.add_trace(go.Bar(
            x=[monatsname],
            y=[row["amount"]],
            marker=dict(color=SEGMENT_FARBEN[i % len(SEGMENT_FARBEN)]),
            hovertemplate=f"{row['date'].strftime('%d.%m.%Y')}<br>{row['amount']:.2f} €<extra></extra>"
        ))

    # Ausgegrauter Teil: was laut Prognose bis Monatsende noch dazukommt.
    if prognostizierter_rest > 0:
        monat_fig.add_trace(go.Bar(
            x=[monatsname],
            y=[prognostizierter_rest],
            marker=dict(color="rgba(148, 163, 184, 0.35)"),
            hovertemplate=f"Prognostizierter Rest ({resttage} Tage): {prognostizierter_rest:.2f} €<extra></extra>"
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
    st.plotly_chart(monat_fig, width="stretch")

with monat_info_col:
    st.metric(f"Bisher ausgegeben im {monatsname}", f"{ausgegeben_monat:.2f} €", delta=f"{len(monat_df)} Tankungen")
    st.caption(
        f"Grau = prognostizierter Rest für die verbleibenden {resttage} Tage ({modus_text}). "
        f"Erwartet für den ganzen Monat: {erwartet_monat:.2f} €."
    )

# --- ÖLWECHSEL-TRACKER ---
st.markdown("### Ölwechsel-Tracker")

oil_result = load_oil_settings()

if not oil_result["ok"]:
    st.info(
        "Die Ölwechsel-Einstellungen konnten nicht geladen werden. Entweder fehlt die Tabelle "
        "`oil_change_settings` in Supabase, oder die Verbindung ist gestört."
    )
    st.caption(f"Fehler: {oil_result['error']}")
elif not oil_result["data"]:
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
            nach_schreibzugriff("Ölwechsel-Einstellungen gespeichert.")
else:
    oil_settings = oil_result["data"]
    interval_km = float(oil_settings["interval_km"])
    last_oil_change_km = float(oil_settings["last_oil_change_km"])
    naechster_wechsel_km = last_oil_change_km + interval_km

    if letzter_km_stand is None:
        st.info("Noch kein Kilometerstand erfasst - Fortschritt kann nicht berechnet werden.")
    else:
        gefahren_seit_wechsel = max(letzter_km_stand - last_oil_change_km, 0)
        rest_km_roh = naechster_wechsel_km - letzter_km_stand
        rest_km = max(rest_km_roh, 0)
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

        if rest_km_roh < 0:
            st.warning(f"Der Ölwechsel ist seit {-rest_km_roh:.0f} km überfällig!")
        elif rest_km_roh == 0:
            st.warning("Der Ölwechsel ist jetzt fällig!")
        else:
            # Datum/KW-Prognose anhand der durchschnittlichen Fahrleistung. Gleitendes
            # km-Fenster in Groesse des Oelwechselintervalls (statt der kompletten
            # Historie), damit altes Fahrverhalten die Rate nicht dauerhaft verzerrt.
            km_fenster_start = max(letzter_km_stand - interval_km, 0)
            km_history = df.dropna(subset=["km_stand"])
            km_history = km_history[km_history["km_stand"] >= km_fenster_start].sort_values("date")
            if len(km_history) >= 2:
                zeitspanne_tage = (km_history["date"].iloc[-1] - km_history["date"].iloc[0]).days
                km_differenz = km_history["km_stand"].iloc[-1] - km_history["km_stand"].iloc[0]
                if zeitspanne_tage > 0 and km_differenz > 0:
                    km_pro_tag = km_differenz / zeitspanne_tage
                    tage_bis_wechsel = rest_km / km_pro_tag
                    # Ab dem Datum des letzten km-Stands rechnen, nicht ab heute -
                    # seitdem gefahrene km sind ja noch nicht erfasst.
                    prognose_datum = km_history["date"].iloc[-1] + pd.Timedelta(days=tage_bis_wechsel)
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
                nach_schreibzugriff("Ölwechsel-Einstellungen aktualisiert.")

# --- HISTORIE: PRO ZEILE EINZELN BEARBEITBAR ODER LÖSCHBAR ---
# Ein st.expander rendert seinen Inhalt auch im zugeklappten Zustand. Bei
# 6 Widgets pro Zeile waren das schon bei 40 Tankungen ~240 Widgets, die bei
# JEDEM Slider-Zug neu aufgebaut wurden. Per Toggle wird die Tabelle nur
# gebaut, wenn sie wirklich angezeigt werden soll.
st.markdown("---")
history_df = df  # bereits absteigend nach Datum sortiert

if not st.toggle(f"**Historie aller Transaktionen ({len(history_df)}) bearbeiten**", key="show_history"):
    pass
elif history_df.empty:
    st.info("Keine Transaktionen vorhanden.")
else:
    header_cols = st.columns([2, 2, 2, 2, 1, 1])
    for col, titel in zip(header_cols, ["Datum", "Betrag", "Kilometerstand", "Preis/l", "Speichern", "Löschen"]):
        col.markdown(f"**{titel}**")

    for _, row in history_df.iterrows():
        row_id = row["id"]
        with st.form(f"edit_row_{row_id}", border=False):
            c1, c2, c3, c4, c5, c6 = st.columns([2, 2, 2, 2, 1, 1])
            edit_date = c1.date_input(
                "Datum", value=row["date"].date(), key=f"date_{row_id}",
                label_visibility="collapsed", format="DD.MM.YYYY"
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
            km_fehler = km_conflict(df, pd.Timestamp(edit_date), edit_km, exclude_id=row_id)
            if edit_preis is not None and edit_preis > 5:
                st.error(
                    f"Preis pro Liter von {edit_preis:.3f} € wirkt unrealistisch hoch. "
                    "Bitte in Euro angeben (z. B. 1.75 statt 175)."
                )
            elif km_fehler:
                st.error(km_fehler)
            elif edit_amount <= 0:
                st.error("Betrag muss größer als 0 sein.")
            else:
                supabase.table("transactions").update({
                    "date": str(edit_date),
                    "amount": float(edit_amount),
                    "km_stand": float(edit_km) if edit_km is not None else None,
                    "preis_pro_liter": float(edit_preis) if edit_preis is not None else None,
                }).eq("id", row_id).execute()
                nach_schreibzugriff("Eintrag aktualisiert.")

        if delete_clicked:
            supabase.table("transactions").delete().eq("id", row_id).execute()
            nach_schreibzugriff("Eintrag gelöscht.")
