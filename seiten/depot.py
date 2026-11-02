"""Depot-Rendite: woechentliche Performance erfassen, daraus die erwartete
Jahresrendite berechnen und automatisch in die Finanzuebersicht uebernehmen."""

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
posten = d["konten"]
kandidaten = posten[posten["aktiv"] & (posten["art"].isin(["depot", "dividenden"])
                                       | posten["ertragsart"].isin(["markt", "dividende"]))]

st.title("Depot-Rendite 📈")
st.caption(
    "Trage einmal pro Woche (z. B. freitags) die Wochenperformance aus der App ein und, wenn du magst, "
    f"den aktuellen Depotwert. Ab {b.MIN_WOCHEN_RENDITE} Wochen übernimmt das Tool die daraus berechnete "
    "Jahresrendite automatisch als erwartete Rendite des Postens."
)

if kandidaten.empty:
    st.info("Kein Depot-Posten vorhanden. Lege unter **Posten** einen Posten mit Produkt „Depot“ an.")
    st.stop()

posten_name = dict(zip(posten["id"], posten["name"]))
k_opt = list(kandidaten.sort_values(by="art", key=lambda s: s != "depot")["id"])
konto_id = st.selectbox("Posten", k_opt, format_func=posten_name.get, key="depot_posten")
konto = posten.set_index("id").loc[konto_id].to_dict() | {"id": konto_id}

wochen = d["depot_wochen"]
wochen = wochen[wochen["konto_id"] == konto_id].sort_values("datum").reset_index(drop=True)
r = b.depot_rendite(wochen["performance_pct"])
satz_jetzt = b.satz_am(d["zinssaetze"], konto_id, heute)


def _rendite_uebernehmen(perf: pd.Series, datum) -> str:
    """Schreibt die berechnete Rendite als Satz des Postens (ab `datum`)."""
    rr = b.depot_rendite(perf)
    if not rr.uebernehmen:
        return ""
    daten.einfuegen_oder_ersetzen("zinssaetze", {
        "konto_id": konto_id, "gueltig_ab": str(pd.Timestamp(datum).date()), "satz_pa": round(rr.pa, 4),
        "notiz": f"Depot-Rendite aus {rr.n_wochen} Wochen",
    }, konflikt="konto_id,gueltig_ab")
    return f" Rendite {rr.pa:.2f} % p. a. übernommen."


# --- KENNZAHLEN ------------------------------------------------------------------
k1, k2, k3, k4 = st.columns(4)
k1.metric("Rendite p. a. (hochgerechnet)", "–" if r.pa is None else f"{r.pa:.2f} %",
          help=f"Aus den letzten bis zu {b.RENDITE_FENSTER_WOCHEN} Wochen, geometrisch gemittelt.")
k2.metric("Ø pro Woche", "–" if r.woche_geo is None else f"{r.woche_geo:.3f} %")
k3.metric("Erfasste Wochen", r.n_wochen,
          delta="wird übernommen" if r.uebernehmen else f"noch {b.MIN_WOCHEN_RENDITE - r.n_wochen} bis zur Übernahme",
          delta_color="off")
k4.metric("Aktuell im Tool hinterlegt", f"{satz_jetzt:.2f} % p. a.")

# --- ERFASSEN ---------------------------------------------------------------------
st.markdown("### Woche eintragen")
letzter_freitag = heute - pd.Timedelta(days=(heute.weekday() - 4) % 7)
start = b.startdatum(d["buchungen"], konto_id)
# Vorschlag nie vor dem Startbestand: Ein Depotwert davor wuerde doppelt zaehlen
vorschlag = max(letzter_freitag, start) if start is not None else letzter_freitag
with st.form("depot_woche_form", clear_on_submit=True):
    c1, c2, c3 = st.columns(3)
    datum = c1.date_input("Datum", value=vorschlag.date(), format="DD.MM.YYYY")
    perf = c2.number_input("Wochenperformance (%)", value=0.0, step=0.01, format="%.2f",
                           help="Wie in der App angezeigt (1 Woche), z. B. 1,25 oder −0,80.")
    wert = c3.number_input("Depotwert gesamt (€, optional)", min_value=0.0, value=None, step=1.0, format="%.2f",
                           help="Aktueller Gesamtwert des Depots. Wird als Kontostand des Postens übernommen.")
    notiz = st.text_input("Notiz (optional)", placeholder="z. B. Nachkauf nach Rücksetzer")
    ok = st.form_submit_button("Woche speichern", type="primary", width="stretch")
st.caption(
    "Tipp: Sparplan-Ausführungen oder Nachkäufe vorher unter **Buchungen** als Umbuchung erfassen "
    "(z. B. Trade Republic Cash → Altersvorsorge). Sonst zählt der Depotwert-Abgleich sie als Wertänderung."
)
if ok:
    if (wochen["datum"] == pd.Timestamp(datum)).any():
        st.error("Für dieses Datum gibt es schon einen Eintrag. Lösche ihn unten, um ihn zu ersetzen.")
    else:
        meldung = [f"Woche {datum:%d.%m.%Y} gespeichert."]

        def _speichern():
            daten.einfuegen("depot_wochen", {
                "konto_id": konto_id, "datum": str(datum), "performance_pct": round(perf, 4),
                "depotwert": round(wert, 2) if wert is not None else None, "notiz": notiz.strip() or None,
            })
            if wert is not None and start is not None and pd.Timestamp(datum) < start:
                meldung.append(f"Depotwert nicht gebucht: Das Datum liegt vor dem Startbestand "
                               f"({start:%d.%m.%Y}), der diesen Wert schon enthält.")
            elif wert is not None:
                gebucht = float(b.gebuchte_salden(d["buchungen"], datum).get(konto_id, 0.0))
                zeile = b.abgleich(konto, wert, gebucht, datum)
                if zeile is not None:
                    daten.einfuegen("buchungen", zeile)
                    meldung.append(f"Depotwert übernommen ({eur(zeile['betrag'])} Wertänderung).")
            neu = pd.concat([wochen[["datum", "performance_pct"]],
                             pd.DataFrame({"datum": [pd.Timestamp(datum)], "performance_pct": [perf]})])
            meldung.append(_rendite_uebernehmen(neu.sort_values("datum")["performance_pct"], datum).strip())
        try:
            _speichern()
        except Exception as exc:
            st.error(daten.fehlertext(exc))
        else:
            daten.nach_schreibzugriff(" ".join(m for m in meldung if m))

# --- VERLAUF ---------------------------------------------------------------------
if wochen.empty:
    st.info("Noch keine Wochen erfasst.")
    st.stop()

st.markdown("### Verlauf")
idx = b.depot_index(wochen["performance_pct"])
fig = go.Figure()
fig.add_trace(go.Scatter(
    x=wochen["datum"], y=idx, mode="lines+markers", name="Wertentwicklung (100 = Start)",
    line=dict(color="#a78bfa", width=2.5), marker=dict(size=6),
    hovertemplate="%{x|%d.%m.%Y}: %{y:.2f}<extra></extra>",
))
fig.add_hline(y=100, line_dash="dash", line_color="#64748b", line_width=1)
mit_wert = wochen.dropna(subset=["depotwert"])
if not mit_wert.empty:
    fig.add_trace(go.Bar(
        x=mit_wert["datum"], y=mit_wert["depotwert"], name="Depotwert (€)", yaxis="y2",
        marker=dict(color="rgba(56, 189, 248, 0.30)"),
        hovertemplate="%{x|%d.%m.%Y}: %{y:,.2f} €<extra></extra>",
    ))
fig.update_layout(
    template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=20, r=20, t=30, b=20), height=320, separators=",.",
    xaxis=dict(gridcolor="#1e293b"), yaxis=dict(title="Index", gridcolor="#1e293b"),
    yaxis2=dict(title="€", overlaying="y", side="right", showgrid=False, rangemode="tozero"),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)
st.plotly_chart(fig, width="stretch")
st.caption("Die Linie zeigt, wie sich 100 € ohne Ein- und Auszahlungen entwickelt hätten.")

with st.expander("🔍 So wird die Jahresrendite berechnet"):
    st.markdown(
        "Aus den Wochenperformances $p_1, \\dots, p_n$ (in %) wird zuerst die **durchschnittliche "
        "Wochenrendite** berechnet – geometrisch, weil sich Gewinne und Verluste multiplizieren "
        "(−10 % und +10 % ergeben nicht 0, sondern −1 %):"
    )
    st.latex(r"\bar r_{\text{Woche}} = \left(\prod_{i=1}^{n}\left(1 + \tfrac{p_i}{100}\right)\right)^{1/n} - 1")
    st.markdown("Diese wird auf 52 Wochen hochgerechnet:")
    st.latex(r"r_{\text{Jahr}} = \left(1 + \bar r_{\text{Woche}}\right)^{52} - 1")
    if r.pa is not None:
        st.latex(rf"= (1 + {r.woche_geo / 100:.5f})^{{52}} - 1 = {r.pa:.2f}\,\%")
    st.markdown(
        f"Berücksichtigt werden die letzten {b.RENDITE_FENSTER_WOCHEN} Wochen. Übernommen wird der Wert "
        f"erst ab {b.MIN_WOCHEN_RENDITE} Wochen: Mit wenigen Wochen ist die Hochrechnung sehr zufällig "
        "(eine Woche mit +2 % entspräche rechnerisch +180 % im Jahr)."
    )

st.markdown("### Erfasste Wochen")
anzeige = wochen.sort_values("datum", ascending=False).reset_index(drop=True)
auswahl = st.dataframe(pd.DataFrame({
    "Datum": anzeige["datum"].dt.strftime("%d.%m.%Y"),
    "Performance": anzeige["performance_pct"],
    "Depotwert": anzeige["depotwert"],
    "Notiz": anzeige["notiz"].fillna(""),
}), hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row", key="depot_tabelle",
    column_config={"Performance": st.column_config.NumberColumn(format="%.2f %%"),
                   "Depotwert": st.column_config.NumberColumn(format="%.2f €")})
zeilen = auswahl.selection.rows if auswahl is not None else []
if zeilen:
    st.caption("Abgleichsbuchungen zum Depotwert bleiben bestehen und lassen sich unter Buchungen löschen.")
    if st.button(f"🗑️ {len(zeilen)} Woche(n) löschen"):
        ids = list(anzeige.loc[zeilen, "id"])
        rest = anzeige[~anzeige["id"].isin(ids)].sort_values("datum")

        def _loeschen():
            daten.loeschen("depot_wochen", ids)
            if not rest.empty:
                _rendite_uebernehmen(rest["performance_pct"], rest["datum"].iloc[-1])
        try:
            _loeschen()
        except Exception as exc:
            st.error(daten.fehlertext(exc))
        else:
            daten.nach_schreibzugriff(f"{len(ids)} Woche(n) gelöscht.")
