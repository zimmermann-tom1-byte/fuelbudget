"""Startseite der Finanzuebersicht: Vermoegen, Ertraege, Toepfe, Verlauf."""

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
konten, toepfe = d["konten"], d["toepfe"]

st.title("Vermögen 💶")

if konten.empty:
    st.info(
        "Noch keine Konten angelegt. Lege unter **Konten & Töpfe** zuerst deine Töpfe und Konten an "
        "und trage dort den Startbestand ein."
    )
    st.page_link("seiten/verwaltung.py", label="Zu Konten & Töpfe", icon="🗂️")
    st.stop()

u = b.ueberblick(konten, toepfe, d["buchungen"], d["zinssaetze"], d["verbindlichkeiten"], heute)
topf_namen = dict(zip(toepfe["id"], toepfe["name"]))
topf_namen[b.FREI] = "Frei (ohne Topf)"
konto_namen = dict(zip(konten["id"], konten["name"]))

# --- KENNZAHLEN ---------------------------------------------------------------
ertrag_tag = u.ertrag_tag_zins + u.ertrag_tag_dividende
c1, c2, c3, c4 = st.columns(4)
c1.metric("Bruttovermögen", eur(u.brutto),
          help="Summe aller Konten inkl. geschätzter, noch nicht gebuchter Zinsen.")
c2.metric("Nettovermögen", eur(u.netto),
          delta=f"− {eur(u.verbindlichkeiten_offen)} Verbindlichkeiten" if u.verbindlichkeiten_offen else None,
          delta_color="off", help="Bruttovermögen minus offene Verbindlichkeiten (z. B. Geldbuße).")
c3.metric("Frei verfügbar", eur(u.frei), delta=f"{eur(u.zweckgebunden)} verplant", delta_color="off",
          help="Geld ohne Topf oder in Töpfen der Art „frei“. Verplant = Rücklagen, Puffer, Investment-Töpfe.")
c4.metric("Passives Einkommen / Tag", eur(ertrag_tag, 3),
          delta=f"≈ {eur(ertrag_tag * 365 / 12)} / Monat", delta_color="off",
          help="Zinsen plus erwartete Ausschüttungen, vor Steuern. Ohne Kursentwicklung.")

aufgelaufen = u.konten["aufgelaufen"].sum()
if aufgelaufen > 0.005:
    st.caption(
        f"Enthält {eur(aufgelaufen)} geschätzte Zinsen seit dem letzten Abgleich. "
        "Mit einem Kontoabgleich unter **Buchungen** werden sie zu echten Zahlen."
    )

# --- ERTRAEGE -------------------------------------------------------------------
st.markdown("### Was dein Geld täglich erwirtschaftet")
e1, e2, e3 = st.columns(3)
e1.metric("Zinsen / Tag", eur(u.ertrag_tag_zins, 3), delta=f"{eur(u.ertrag_tag_zins * 365)} / Jahr",
          delta_color="off")
e2.metric("Ausschüttungen / Tag (erwartet)", eur(u.ertrag_tag_dividende, 3),
          delta=f"{eur(u.ertrag_tag_dividende * 365)} / Jahr", delta_color="off")
e3.metric("Kursrendite / Tag (Annahme)", eur(u.ertrag_tag_markt, 3),
          delta=f"{eur(u.ertrag_tag_markt * 365)} / Jahr", delta_color="off",
          help="Rein rechnerisch aus deiner angenommenen Rendite. Tatsächlich schwankt der Depotwert stark.")

tabelle = u.konten[u.konten["aktiv"] | (u.konten["saldo"].abs() > 0.005)].copy()
tabelle["Art"] = tabelle["art"].map(b.KONTO_ARTEN)
tabelle["Ertrag"] = tabelle["ertragsart"].map(b.ERTRAGSARTEN)
tabelle["Bestätigt am"] = tabelle["bestaetigt_am"].dt.strftime("%d.%m.%Y").fillna("–")
anzeige = pd.DataFrame({
    "Konto": tabelle["name"],
    "Art": tabelle["Art"],
    "Saldo (€)": tabelle["saldo"].round(2),
    "Satz % p. a.": tabelle["satz_pa"].round(2),
    "€ / Tag": tabelle["ertrag_tag"].round(4),
    "€ / Monat": (tabelle["ertrag_tag"] * 365 / 12).round(2),
    "Ertragsart": tabelle["Ertrag"],
    "Risiko": tabelle["risiko"],
    "Bestätigt am": tabelle["Bestätigt am"],
})
st.dataframe(
    anzeige, hide_index=True, width="stretch",
    column_config={
        "Saldo (€)": st.column_config.NumberColumn(format="%.2f €"),
        "€ / Tag": st.column_config.NumberColumn(format="%.4f €"),
        "€ / Monat": st.column_config.NumberColumn(format="%.2f €"),
        "Risiko": st.column_config.ProgressColumn(min_value=0, max_value=5, format="%d / 5"),
    },
)
st.caption(
    "Alle Erträge vor Steuern. Risiko: 1 = Einlagensicherung/Bargeld … 5 = ohne Sicherung (z. B. P2P). "
    "„Bestätigt am“ = letzter Startbestand, Zins- oder Abgleichsbuchung."
)

# --- TOEPFE ---------------------------------------------------------------------
st.markdown("### Töpfe – wofür dein Geld da ist")
t = u.toepfe[u.toepfe["aktiv"] | (u.toepfe["stand"].abs() > 0.005)].copy()
frei_stand = float(u.matrix[b.FREI].sum()) if b.FREI in u.matrix.columns else 0.0

spalten = st.columns(2)
for i, (_, row) in enumerate(t.iterrows()):
    with spalten[i % 2]:
        ziel = row["zielbetrag"] if pd.notna(row["zielbetrag"]) and row["zielbetrag"] > 0 else None
        if row["id"] in u.matrix.columns:
            verteilung = u.matrix[row["id"]]
            verteilung = verteilung[verteilung.abs() > 0.005]
            wo = ", ".join(f"{konto_namen.get(k, '?')} {eur(v)}" for k, v in verteilung.items())
        else:
            wo = "noch leer"
        kopf = f"**{row['name']}** · {b.TOPF_ARTEN.get(row['art'], row['art'])}"
        if ziel:
            anteil = max(min(row["stand"] / ziel, 1.0), 0.0)
            st.progress(anteil, text=f"{kopf} — {eur(row['stand'])} von {eur(ziel)}")
            if row["stand"] < ziel:
                st.caption(f"Es fehlen {eur(ziel - row['stand'])}. Liegt bei: {wo}")
            else:
                st.caption(f"Ziel erreicht. Liegt bei: {wo}")
        else:
            st.markdown(f"{kopf} — {eur(row['stand'])}")
            st.caption(f"Liegt bei: {wo}")
if abs(frei_stand) > 0.005:
    with spalten[len(t) % 2]:
        st.markdown(f"**Ohne Topf** · frei — {eur(frei_stand)}")
        verteilung = u.matrix[b.FREI]
        verteilung = verteilung[verteilung.abs() > 0.005]
        st.caption("Liegt bei: " + ", ".join(f"{konto_namen.get(k, '?')} {eur(v)}" for k, v in verteilung.items()))

if not u.matrix.empty:
    with st.expander("Konto × Topf – wo liegt welches Geld?"):
        m = u.matrix.copy()
        m.index = [konto_namen.get(k, k) for k in m.index]
        m.columns = [topf_namen.get(c, c) for c in m.columns]
        m["Summe"] = m.sum(axis=1)
        st.dataframe(m.round(2), width="stretch",
                     column_config={c: st.column_config.NumberColumn(format="%.2f €") for c in m.columns})

v = b.verbindlichkeiten_offen(d["verbindlichkeiten"], d["buchungen"])
v = v[v["offen"] > 0.005] if not v.empty else v
if not v.empty:
    st.markdown("### Offene Verbindlichkeiten")
    for _, row in v.iterrows():
        anteil = min(row["getilgt"] / row["betrag_gesamt"], 1.0) if row["betrag_gesamt"] else 0.0
        st.progress(anteil, text=f"**{row['name']}** — noch {eur(row['offen'])} offen, "
                                 f"{eur(row['getilgt'])} von {eur(row['betrag_gesamt'])} getilgt")

# --- VERLAUF ---------------------------------------------------------------------
st.markdown("### Verlauf")
verlauf = b.saldo_verlauf(d["buchungen"][d["buchungen"]["datum"] <= heute], heute)
if len(verlauf) < 2:
    st.info("Der Verlauf erscheint, sobald Buchungen an mehr als einem Tag vorliegen.")
else:
    reihen = {}
    arten = konten.set_index("id")["art"]
    for kid in verlauf.columns:
        name = b.KONTO_ARTEN.get(arten.get(kid, "sonstiges"), "Sonstiges")
        reihen[name] = reihen.get(name, 0) + verlauf[kid]
    fig = go.Figure()
    for name, reihe in reihen.items():
        fig.add_trace(go.Scatter(x=reihe.index, y=reihe.values, name=name, mode="lines",
                                 stackgroup="eins", line=dict(width=0.5),
                                 hovertemplate="%{x|%d.%m.%Y}: %{y:,.2f} €<extra>" + name + "</extra>"))
    fig.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=20, r=20, t=30, b=20), height=340, hovermode="x unified",
        xaxis=dict(gridcolor="#1e293b"), yaxis=dict(title="€ (gebucht)", gridcolor="#1e293b"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        separators=",.",
    )
    st.markdown("**Vermögen nach Anlageart** (gebuchte Salden, ohne geschätzte Zinsen)")
    st.plotly_chart(fig, width="stretch")

    ev = b.ertrag_verlauf(d["buchungen"][d["buchungen"]["datum"] <= heute], konten, d["zinssaetze"], heute)
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
        st.markdown("**Passives Einkommen pro Tag** (gepunktet = Annahme Kursrendite)")
        st.plotly_chart(efig, width="stretch")
