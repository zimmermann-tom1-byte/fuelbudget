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

d = daten.alles()
posten, toepfe = d["konten"], d["toepfe"]

st.title("Vermögen 💶")

if posten.empty:
    st.info("Noch keine Posten angelegt. Unter **Posten** führt dich der Tab „Start“ durch die Einrichtung.")
    st.page_link("seiten/verwaltung.py", label="Zu den Posten", icon="🗂️")
    st.stop()

ziele = {kid: topf_ziel for kid, topf_ziel in zip(
    posten["id"], posten["standard_topf_id"].map(dict(zip(toepfe["id"], toepfe["zielbetrag"]))))
    if pd.notna(topf_ziel) and topf_ziel > 0}
u = b.ueberblick(posten, d["buchungen"], d["zinssaetze"], d["verbindlichkeiten"], heute, ziele)

# --- KENNZAHLEN ---------------------------------------------------------------
ertrag_tag = u.ertrag_tag_zins + u.ertrag_tag_dividende
c1, c2, c3, c4 = st.columns(4)
c1.metric("Bruttovermögen", eur(u.brutto),
          help="Summe aller Posten inkl. geschätzter, noch nicht gebuchter Zinsen.")
c2.metric("Nettovermögen", eur(u.netto),
          delta=f"− {eur(u.verbindlichkeiten_offen)} Verbindlichkeiten" if u.verbindlichkeiten_offen else None,
          delta_color="off", help="Bruttovermögen minus offene Schulden (z. B. Geldbuße). "
                                  "Fixkosten sind keine Schulden und werden nicht abgezogen.")
c3.metric("Frei verfügbar", eur(u.frei), help=b.STUFE_ERKLAERUNG["frei"])
c4.metric("Passives Einkommen / Tag", eur(ertrag_tag, 3),
          delta=f"≈ {eur(ertrag_tag * 365 / 12)} / Monat", delta_color="off",
          help="Zinsen plus erwartete Ausschüttungen, vor Steuern. Ohne Kursentwicklung.")

aufgelaufen = u.posten["aufgelaufen"].sum()
if aufgelaufen > 0.005:
    st.caption(
        f"Enthält {eur(aufgelaufen)} geschätzte Zinsen seit dem letzten Abgleich. "
        "Mit einem Kontoabgleich unter **Buchungen** werden sie zu echten Zahlen."
    )

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

for stufe in b.STUFEN:
    teil = u.posten[(u.posten["stufe"] == stufe) & (u.posten["aktiv"] | (u.posten["saldo"].abs() > 0.005))]
    if teil.empty:
        continue
    summe = float(teil["saldo"].sum())
    with st.expander(f"**{b.STUFEN[stufe]}** — {eur(summe)}", expanded=False):
        st.caption(b.STUFE_ERKLAERUNG[stufe])
        for _, row in teil.sort_values("saldo", ascending=False).iterrows():
            wo = " · ".join(x for x in (row["institut"] if pd.notna(row["institut"]) else None,
                                        b.KONTO_ARTEN.get(row["art"])) if x)
            ziel = row["ziel"]
            if pd.notna(ziel) and ziel > 0:
                anteil = max(min(row["saldo"] / ziel, 1.0), 0.0)
                st.progress(anteil, text=f"**{row['name']}** — {eur(row['saldo'])} von {eur(ziel)}")
                rest = f"Es fehlen {eur(ziel - row['saldo'])}" if row["saldo"] < ziel else "Ziel erreicht"
                st.caption(f"{wo} · {rest}")
            else:
                st.markdown(f"**{row['name']}** — {eur(row['saldo'])}")
                st.caption(wo)

v = b.verbindlichkeiten_offen(d["verbindlichkeiten"], d["buchungen"])
v = v[v["offen"] > 0.005] if not v.empty else v
if not v.empty:
    st.markdown("### Offene Verbindlichkeiten")
    for _, row in v.iterrows():
        anteil = min(row["getilgt"] / row["betrag_gesamt"], 1.0) if row["betrag_gesamt"] else 0.0
        st.progress(anteil, text=f"**{row['name']}** — noch {eur(row['offen'])} offen, "
                                 f"{eur(row['getilgt'])} von {eur(row['betrag_gesamt'])} getilgt")

# --- ERTRAEGE -------------------------------------------------------------------
st.markdown("### Was dein Geld täglich erwirtschaftet")
fix = b.fixkosten_soll(d["fixkosten"], heute)
fix_monat = float(fix["pro_monat"].sum()) if not fix.empty else 0.0
e1, e2, e3, e4 = st.columns(4)
e1.metric("Zinsen / Tag", eur(u.ertrag_tag_zins, 3), delta=f"{eur(u.ertrag_tag_zins * 365)} / Jahr",
          delta_color="off")
e2.metric("Ausschüttungen / Tag (erwartet)", eur(u.ertrag_tag_dividende, 3),
          delta=f"{eur(u.ertrag_tag_dividende * 365)} / Jahr", delta_color="off")
e3.metric("Kursrendite / Tag (erwartet)", eur(u.ertrag_tag_markt, 3),
          delta=f"{eur(u.ertrag_tag_markt * 365)} / Jahr", delta_color="off",
          help="Aus der gemessenen Depot-Rendite (Seite Depot-Rendite) bzw. deiner Annahme. "
               "Tatsächlich schwankt der Depotwert stark.")
if fix_monat > 0:
    deckung = ertrag_tag * 365 / 12 / fix_monat
    e4.metric("Fixkosten gedeckt durch passives Einkommen", f"{deckung * 100:.1f} %",
              delta=f"{eur(ertrag_tag * 365 / 12)} von {eur(fix_monat)} / Monat", delta_color="off",
              help="Zinsen und Ausschüttungen pro Monat geteilt durch deine Fixkosten pro Monat.")
else:
    e4.metric("Fixkosten gedeckt", "–", help="Erfasse deine Fixkosten auf der Seite Fixkosten.")

tabelle = u.posten[(u.posten["ertragsart"] != "keine") & (u.posten["aktiv"] | (u.posten["saldo"].abs() > 0.005))].copy()
tabelle = tabelle.sort_values("ertrag_tag", ascending=False)
st.dataframe(pd.DataFrame({
    "Posten": tabelle["name"],
    "Stufe": tabelle["stufe"].map(b.STUFEN),
    "Saldo (€)": tabelle["saldo"].round(2),
    "Satz % p. a.": tabelle["satz_pa"].round(2),
    "€ / Tag": tabelle["ertrag_tag"].round(4),
    "€ / Monat": (tabelle["ertrag_tag"] * 365 / 12).round(2),
    "Ertragsart": tabelle["ertragsart"].map(b.ERTRAGSARTEN),
    "Risiko": tabelle["risiko"],
    "Bestätigt am": tabelle["bestaetigt_am"].dt.strftime("%d.%m.%Y").fillna("–"),
}), hide_index=True, width="stretch", column_config={
    "Saldo (€)": st.column_config.NumberColumn(format="%.2f €"),
    "€ / Tag": st.column_config.NumberColumn(format="%.4f €"),
    "€ / Monat": st.column_config.NumberColumn(format="%.2f €"),
    "Risiko": st.column_config.ProgressColumn(min_value=0, max_value=5, format="%d / 5"),
})
st.caption(
    "Alle Erträge vor Steuern. Risiko: 1 = Einlagensicherung/Bargeld … 5 = ohne Sicherung (z. B. P2P). "
    "„Bestätigt am“ = letzter Startbestand, Zins- oder Abgleichsbuchung."
)

# --- VERLAUF ---------------------------------------------------------------------
st.markdown("### Verlauf")
bu_bis_heute = d["buchungen"][d["buchungen"]["datum"] <= heute]
verlauf = b.stufen_verlauf(bu_bis_heute, posten, heute)
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
        xaxis=dict(gridcolor="#1e293b"), yaxis=dict(title="€ (gebucht)", gridcolor="#1e293b"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        separators=",.",
    )
    st.markdown("**Vermögen nach Stufe** (gebuchte Salden, ohne geschätzte Zinsen)")
    st.plotly_chart(fig, width="stretch")

    ev = b.ertrag_verlauf(bu_bis_heute, posten, d["zinssaetze"], heute)
    if not ev.empty:
        efig = go.Figure()
        farben = {"zins": "#38bdf8", "dividende": "#34d399", "markt": "#a78bfa"}
        for art in ("zins", "dividende", "markt"):
            if art in ev.columns:
                efig.add_trace(go.Scatter(
                    x=ev.index, y=ev[art], name=b.ERTRAGSARTEN[art], mode="lines",
                    line=dict(color=farben[art], width=2.5, dash="dot" if art == "markt" else "solid"),
                    hovertemplate="%{x|%d.%m.%Y}: %{y:.3f} €/Tag<extra></extra>",
                ))
        efig.update_layout(
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=20, r=20, t=30, b=20), height=300,
            xaxis=dict(gridcolor="#1e293b"), yaxis=dict(title="€ pro Tag", gridcolor="#1e293b"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            separators=",.",
        )
        st.markdown("**Passives Einkommen pro Tag** (gepunktet = erwartete Kursrendite)")
        st.plotly_chart(efig, width="stretch")
