"""Startseite der Finanzuebersicht: Vermoegen nach Stufen, Ertraege, Verlauf."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from finanzen import berechnung as b
from finanzen import daten, zwei_faktor

zwei_faktor.sicherstellen()

TZ = ZoneInfo("Europe/Berlin")
heute = pd.Timestamp(datetime.now(TZ).date())
eur = b.eur

d = daten.alles_aktuell(heute)
konten, toepfe = d["konten"], d["toepfe"]

st.title("Vermögen 💶")

# --- DATENPRUEFUNG: Buchungen vor dem Startbestand zaehlen doppelt ---------------
falsch = b.vor_startbestand(d["buchungen"])
if not falsch.empty:
    namen = dict(zip(konten["id"], konten["name"]))
    liste = "\n".join(f"- {r['datum']:%d.%m.%Y} · {namen.get(r['konto_id'], '?')} · {eur(r['betrag'])} "
                      f"({r['typ']})" for _, r in falsch.iterrows())
    st.error(
        f"**Datenprüfung: {len(falsch)} Buchung(en) liegen vor dem Startbestand ihres Kontos** und "
        f"werden dadurch doppelt gezählt (zusammen {eur(float(falsch['betrag'].sum()))}).\n\n{liste}"
    )
    if st.button("🧹 Diese Buchungen entfernen", type="primary", key="datencheck_bereinigen"):
        try:
            daten.loeschen("buchungen", list(falsch["id"]))
        except Exception as exc:
            st.error(daten.fehlertext(exc))
        else:
            daten.nach_schreibzugriff(f"{len(falsch)} Buchung(en) entfernt. Die Summen stimmen wieder.")

if konten.empty:
    st.info("Noch keine Konten angelegt. Unter **Konten & Töpfe** führt dich der Tab „Start“ durch die Einrichtung.")
    st.page_link("seiten/verwaltung.py", label="Zu Konten & Töpfe", icon="🗂️")
    st.stop()

u = b.ueberblick(konten, toepfe, d["buchungen"], d["zinssaetze"], d["verbindlichkeiten"], heute, d["kurse"])
fix = d["fixkosten"]
fix_monat = float(sum(b.pro_monat(r) for _, r in fix[fix["aktiv"]].iterrows())) if not fix.empty else 0.0

# --- KENNZAHLEN ---------------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)
c1.metric("Bruttovermögen", eur(u.brutto),
          help="Summe aller Konten. Fonds zum aktuellen Kurs, Tagesgeld inkl. geschätzter, noch nicht gebuchter Zinsen.")
c2.metric("Nettovermögen", eur(u.netto),
          delta=f"− {eur(u.verbindlichkeiten_offen)} Verbindlichkeiten" if u.verbindlichkeiten_offen else None,
          delta_color="off", help="Bruttovermögen minus offene Schulden (z. B. Geldbuße). "
                                  "Fixkosten sind keine Schulden und werden nicht abgezogen.")
c3.metric("Frei verfügbar", eur(u.frei), help=b.STUFE_ERKLAERUNG["frei"])
c4.metric("Ausgezahltes Einkommen / Monat", eur(u.ausgezahlt_tag_prognose * 365 / 12),
          delta=f"{eur(u.ausgezahlt_tag_prognose, 3)} / Tag", delta_color="off",
          help="Nur Erträge, die tatsächlich ausgezahlt werden (Tagesgeld, Monefit, Dividenden), nach heutigem "
               "Bestand. Buchgewinne der Geldmarktfonds sind nicht enthalten.")

# --- STUFEN ---------------------------------------------------------------------
st.markdown("### Wie verfügbar ist dein Geld?")
stufen = u.stufen[u.stufen.abs() > 0.005]
if u.brutto > 0 and not stufen.empty:
    bar = go.Figure()
    for stufe, wert in stufen.items():
        anteil = wert / u.brutto * 100
        bar.add_trace(go.Bar(
            y=[""], x=[wert], orientation="h", name=b.STUFEN[stufe],
            marker=dict(color=b.STUFE_FARBEN[stufe], line=dict(color="#020617", width=2)),
            text=f"{anteil:.0f} %" if anteil >= 6 else "", textposition="inside",
            insidetextanchor="middle", textfont=dict(color="#020617", size=13),
            hovertemplate=f"{b.STUFEN[stufe]}: {eur(wert)} ({anteil:.1f} %)<extra></extra>",
        ))
    bar.update_layout(
        barmode="stack", template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", height=120, margin=dict(l=0, r=0, t=10, b=0),
        xaxis=dict(visible=False), yaxis=dict(visible=False), bargap=0.15,
        legend=dict(orientation="h", yanchor="top", y=-0.05, xanchor="left", x=0),
    )
    st.plotly_chart(bar, width="stretch", config={"displayModeBar": False})

t_all = u.toepfe
for stufe in b.STUFEN:
    teil = t_all[(t_all["stufe"] == stufe) & ((t_all["topf_aktiv"] & t_all["konto_aktiv"]) | (t_all["wert"].abs() > 0.005))]
    if teil.empty:
        continue
    with st.expander(f"**{b.STUFEN[stufe]}** — {eur(float(teil['wert'].sum()))}", expanded=False):
        st.caption(b.STUFE_ERKLAERUNG[stufe])
        for konto_name, gruppe in teil.groupby("konto_name", sort=False):
            st.markdown(f"**{konto_name}** — {eur(float(gruppe['wert'].sum()))}")
            for _, row in gruppe.sort_values("wert", ascending=False).iterrows():
                zusatz = ""
                if row["bewertung"] == "anteile" and abs(row["aufgelaufen"]) >= 0.005:
                    zusatz = f" (davon {eur(row['aufgelaufen'])} Buchgewinn)"
                ziel = row["ziel"]
                if pd.notna(ziel) and ziel > 0:
                    anteil = max(min(row["wert"] / ziel, 1.0), 0.0)
                    st.progress(anteil, text=f"{row['topf_name']} — {eur(row['wert'])} von {eur(ziel)}{zusatz}")
                else:
                    st.caption(f"• {row['topf_name']} — {eur(row['wert'])}{zusatz}")

v = b.verbindlichkeiten_offen(d["verbindlichkeiten"], d["buchungen"])
v = v[v["offen"] > 0.005] if not v.empty else v
if not v.empty:
    st.markdown("### Offene Verbindlichkeiten")
    for _, row in v.iterrows():
        anteil = min(row["getilgt"] / row["betrag_gesamt"], 1.0) if row["betrag_gesamt"] else 0.0
        st.progress(anteil, text=f"**{row['name']}** — noch {eur(row['offen'])} offen, "
                                 f"{eur(row['getilgt'])} von {eur(row['betrag_gesamt'])} getilgt")

# --- ERTRAEGE -------------------------------------------------------------------
st.markdown("### Was dein Geld erwirtschaftet")
gesamt_tag = u.ertrag_tag_zins + u.ertrag_tag_dividende
buch_tag = gesamt_tag - u.ausgezahlt_tag_prognose
e1, e2, e3 = st.columns(3)
e1.metric("Rechnerisch / Tag", eur(gesamt_tag, 3), delta=f"≈ {eur(gesamt_tag * 365 / 12)} / Monat",
          delta_color="off", help="Alle Zinsen und Ausschüttungen inkl. Buchgewinne der Geldmarktfonds, vor Steuern. "
                                  "Ohne Kursentwicklung des Depots.")
e2.metric("Davon ausgezahlt / Tag", eur(u.ausgezahlt_tag_prognose, 3),
          delta=f"≈ {eur(u.ausgezahlt_tag_prognose * 365 / 12)} / Monat", delta_color="off",
          help="Erträge der Konten, die tatsächlich auszahlen. Nur dieses Geld kann Fixkosten bezahlen.")
e3.metric("Davon Buchgewinne / Tag", eur(buch_tag, 3), delta=f"≈ {eur(buch_tag * 365 / 12)} / Monat",
          delta_color="off", help="Bleiben im Fonds liegen (z. B. für Aufrundungen und GoodNotes).")

f1, f2, f3 = st.columns(3)
f1.metric("Tatsächlich ausgezahlt (letzte 30 Tage)", eur(u.ausgezahlt_30_tage),
          help="Gebuchte Zins- und Ertragsgutschriften der auszahlenden Konten. Kommt über den Kontoabgleich "
               "unter Buchungen ins Tool.")
if fix_monat > 0:
    deckung_prognose = u.ausgezahlt_tag_prognose * 365 / 12 / fix_monat
    deckung_ist = u.ausgezahlt_30_tage / fix_monat
    f2.metric("Fixkosten gedeckt (tatsächlich)", f"{deckung_ist * 100:.1f} %",
              delta=f"{eur(u.ausgezahlt_30_tage)} von {eur(fix_monat)}", delta_color="off",
              help="Tatsächliche Auszahlungen der letzten 30 Tage geteilt durch deine Fixkosten pro Monat.")
    f3.metric("Fixkosten gedeckt (nach heutigem Bestand)", f"{deckung_prognose * 100:.1f} %",
              delta=f"{eur(u.ausgezahlt_tag_prognose * 365 / 12)} von {eur(fix_monat)}", delta_color="off",
              help="Was die auszahlenden Konten bei heutigem Bestand pro Monat bringen würden. Sinkt, wenn "
                   "z. B. die Autorücklage für eine Reparatur gebraucht wird.")
else:
    f2.metric("Fixkosten gedeckt", "–", help="Erfasse deine Fixkosten auf der Seite Fixkosten.")
st.caption(
    "Warum zwei Werte für die Deckung? Die Auszahlungen hängen vom Bestand ab, und der schwankt "
    "(Reparatur, Einkauf, Nachkauf im Crash). „Nach heutigem Bestand“ zeigt, was gerade zu erwarten ist, "
    "„tatsächlich“ zeigt, was wirklich gekommen ist."
)

tabelle = u.konten[(u.konten["ertragsart"] != "keine") & (u.konten["aktiv"].fillna(True).astype(bool) | (u.konten["wert"].abs() > 0.005))].copy()
tabelle = tabelle.sort_values("ertrag_tag", ascending=False)
st.dataframe(pd.DataFrame({
    "Konto": tabelle["name"],
    "Wert (€)": tabelle["wert"].round(2),
    "Satz % p. a.": tabelle["satz_pa"].round(2),
    "€ / Monat": (tabelle["ertrag_tag"] * 365 / 12).round(2),
    "Ertrag": tabelle["ertrag_ausgezahlt"].map({True: "ausgezahlt", False: "bleibt liegen"}),
    "Ertragsart": tabelle["ertragsart"].map(b.ERTRAGSARTEN),
    "Risiko": tabelle["risiko"],
    "Stand vom": [(k if pd.notna(k) else s).strftime("%d.%m.%Y") if pd.notna(k if pd.notna(k) else s) else "–"
                  for k, s in zip(tabelle["kurs_stand"], tabelle["bestaetigt_am"])],
}), hide_index=True, width="stretch", column_config={
    "Wert (€)": st.column_config.NumberColumn(format="%.2f €"),
    "€ / Monat": st.column_config.NumberColumn(format="%.2f €"),
    "Risiko": st.column_config.ProgressColumn(min_value=0, max_value=5, format="%d / 5"),
})
st.caption("Alle Erträge vor Steuern. „Stand vom“ = letzter Kurs (Fonds) bzw. letzter Abgleich (Konten).")

# --- VERLAUF ---------------------------------------------------------------------
st.markdown("### Verlauf")
bu_bis_heute = d["buchungen"][d["buchungen"]["datum"] <= heute]
verlauf = b.stufen_verlauf(bu_bis_heute, konten, toepfe, heute)
if len(verlauf) < 2:
    st.info("Der Verlauf erscheint, sobald Buchungen an mehr als einem Tag vorliegen.")
else:
    fig = go.Figure()
    for stufe in verlauf.columns:
        name = b.STUFEN[stufe]
        fig.add_trace(go.Scatter(
            x=verlauf.index, y=verlauf[stufe], name=name, mode="lines", stackgroup="eins",
            line=dict(width=0.5, color=b.STUFE_FARBEN[stufe]), fillcolor=b.STUFE_FARBEN[stufe],
            hovertemplate="%{x|%d.%m.%Y}: %{y:,.2f} €<extra>" + name + "</extra>"))
    fig.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=20, r=20, t=30, b=20), height=340, hovermode="x unified",
        xaxis=dict(gridcolor="#1e293b"), yaxis=dict(title="€ (Einstand)", gridcolor="#1e293b"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        separators=",.",
    )
    st.markdown("**Vermögen nach Stufe** (gebuchte Beträge, ohne Buchgewinne und geschätzte Zinsen)")
    st.plotly_chart(fig, width="stretch")
