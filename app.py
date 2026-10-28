"""Einstieg: Anmeldung, Seitenleiste und Navigation.

Die eigentlichen Seiten liegen in seiten/:
- fuelbudget.py      Spritkosten, Verbrauch, Oelwechsel (fuer alle Nutzer)
- vermoegen.py       Finanzuebersicht (nur fuer freigeschaltete Nutzer, mit 2FA)
- buchungen.py       Buchungen, Umbuchungen, Kontoabgleich
- verwaltung.py      Konten, Toepfe, Zinssaetze, Verbindlichkeiten, Startbestand
"""

import importlib

import streamlit as st

import anmeldung
import forecast
from finanzen import berechnung, daten, zwei_faktor

# Streamlit Cloud laedt nach einem git-Update nur app.py neu; bereits
# importierte Hilfsmodule bleiben sonst in alter Version im Speicher
# (-> ImportError bei neuen Funktionen). Reload kostet praktisch nichts.
# Reihenfolge: erst Module ohne Abhaengigkeiten, dann die, die sie nutzen.
for _modul in (forecast, anmeldung, berechnung, daten, zwei_faktor):
    importlib.reload(_modul)

# --- PAGE CONFIGURATION (muss der erste Streamlit-Aufruf sein) ---
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
    div[data-testid="stDataFrame"] { background-color: #0f172a; border-radius: 12px; }
</style>
""", unsafe_allow_html=True)


# --- ANMELDUNG ---
anmeldung.link_weiterleitung()
nutzer = anmeldung.aktueller_nutzer()
if nutzer is None:
    anmeldung.login_seite()          # beendet den Lauf
if st.session_state.get("passwort_setzen"):
    anmeldung.passwort_setzen_seite()  # beendet den Lauf
anmeldung.cookie_aktualisieren()

if "flash" in st.session_state:
    st.toast(st.session_state.pop("flash"), icon="✅")


# --- NAVIGATION ---
fuelbudget = st.Page("seiten/fuelbudget.py", title="FuelBudget", icon="⛽", url_path="fuelbudget")

if anmeldung.finanzen_freigeschaltet():
    seiten = {
        "Finanzen": [
            st.Page("seiten/vermoegen.py", title="Vermögen", icon="💶", url_path="vermoegen", default=True),
            st.Page("seiten/buchungen.py", title="Buchungen", icon="🧾", url_path="buchungen"),
            st.Page("seiten/verwaltung.py", title="Konten & Töpfe", icon="🗂️", url_path="verwaltung"),
        ],
        "Auto": [fuelbudget],
    }
else:
    # Ohne Freischaltung sieht man nur FuelBudget, wie bisher.
    seiten = [st.Page("seiten/fuelbudget.py", title="FuelBudget", icon="⛽", url_path="fuelbudget",
                      default=True)]

seite = st.navigation(seiten)

with st.sidebar:
    konto_col, abmelden_col = st.columns([3, 2], vertical_alignment="center")
    konto_col.caption(f"👤 {nutzer['email']}")
    if abmelden_col.button("Abmelden", width="stretch"):
        anmeldung.abmelden()
    anmeldung.passwort_aendern_bereich()
    st.markdown("---")

seite.run()
