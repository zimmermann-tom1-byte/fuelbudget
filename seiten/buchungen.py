"""Buchungen erfassen: Einnahmen/Ausgaben, Umbuchungen, Kontoabgleich, Liste."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from finanzen import berechnung as b
from finanzen import daten, zwei_faktor

zwei_faktor.sicherstellen()

TZ = ZoneInfo("Europe/Berlin")
heute = pd.Timestamp(datetime.now(TZ).date())
eur = b.eur

d = daten.alles()
konten = d["konten"]
toepfe = d["toepfe"]
aktive_konten = konten[konten["aktiv"]]
aktive_toepfe = toepfe[toepfe["aktiv"]]

st.title("Buchungen 🧾")

if aktive_konten.empty:
    st.info("Lege zuerst unter **Konten & Töpfe** mindestens ein Konto an.")
    st.page_link("seiten/verwaltung.py", label="Zu Konten & Töpfe", icon="🗂️")
    st.stop()

KONTO_OPT = list(aktive_konten["id"])
konto_name = dict(zip(konten["id"], konten["name"]))
TOPF_OPT = [None] + list(aktive_toepfe["id"])
topf_name = {None: "– ohne Topf (frei) –", **dict(zip(toepfe["id"], toepfe["name"]))}


def _standard_topf(konto_id):
    """Vorauswahl Topf: Standardtopf des Kontos, falls aktiv."""
    st_topf = konten.set_index("id").at[konto_id, "standard_topf_id"]
    return TOPF_OPT.index(st_topf) if st_topf in TOPF_OPT else 0


def _speichern(zeilen, meldung):
    try:
        daten.einfuegen("buchungen", zeilen)
    except Exception as exc:
        st.error(daten.fehlertext(exc))
        return
    daten.nach_schreibzugriff(meldung)


tab_ein, tab_um, tab_abgleich, tab_liste = st.tabs(
    ["➕ Einnahme / Ausgabe", "🔁 Umbuchung", "🎯 Kontoabgleich", "📋 Alle Buchungen"]
)

# --- EINNAHME / AUSGABE -------------------------------------------------------------
with tab_ein:
    richtung = st.segmented_control("Art", ["Ausgabe", "Einnahme"], default="Ausgabe",
                                    key="buchung_richtung", label_visibility="collapsed")
    ist_ausgabe = richtung != "Einnahme"
    konto = st.selectbox("Konto", KONTO_OPT, format_func=konto_name.get, key="ea_konto")
    offene_v = b.verbindlichkeiten_offen(d["verbindlichkeiten"], d["buchungen"])
    offene_v = offene_v[offene_v["offen"] > 0.005] if not offene_v.empty else offene_v
    v_opt = [None] + (list(offene_v["id"]) if not offene_v.empty else [])
    v_name = {None: "– keine –", **({} if offene_v.empty else
                                    {r["id"]: f"{r['name']} (offen {eur(r['offen'])})"
                                     for _, r in offene_v.iterrows()})}

    with st.form("einnahme_ausgabe_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        datum = c1.date_input("Datum", value=heute.date(), format="DD.MM.YYYY")
        betrag = c2.number_input("Betrag (€)", min_value=0.0, step=0.01, format="%.2f")
        topf = st.selectbox("Topf", TOPF_OPT, index=_standard_topf(konto), format_func=topf_name.get,
                            help="Aus welchem Topf geht das Geld ab bzw. wofür ist es da? "
                                 "Ohne Topf = frei verfügbar.")
        kategorien = b.AUSGABE_KATEGORIEN if ist_ausgabe else b.EINNAHME_KATEGORIEN
        kategorie = st.selectbox("Kategorie", kategorien)
        beschreibung = st.text_input("Beschreibung (optional)", placeholder="z. B. REWE, Gehalt Oktober")
        verbindlichkeit = None
        if ist_ausgabe and len(v_opt) > 1:
            verbindlichkeit = st.selectbox("Tilgt Verbindlichkeit", v_opt, format_func=v_name.get)
        ok = st.form_submit_button(f"{'Ausgabe' if ist_ausgabe else 'Einnahme'} speichern",
                                   type="primary", width="stretch")
    if ok:
        if betrag <= 0:
            st.error("Bitte einen Betrag größer 0 eingeben.")
        else:
            _speichern({
                "datum": str(datum), "konto_id": konto, "topf_id": topf,
                "betrag": -round(betrag, 2) if ist_ausgabe else round(betrag, 2),
                "typ": "ausgabe" if ist_ausgabe else "einnahme",
                "kategorie": kategorie, "beschreibung": beschreibung.strip() or None,
                "verbindlichkeit_id": verbindlichkeit,
            }, f"{'Ausgabe' if ist_ausgabe else 'Einnahme'} über {eur(betrag)} gespeichert.")

# --- UMBUCHUNG ---------------------------------------------------------------------
with tab_um:
    st.caption(
        "Geld zwischen Konten verschieben (z. B. Gehalt → Geldmarktfonds) oder innerhalb eines Kontos "
        "einem anderen Topf zuordnen (Umtopfen, z. B. 50 € frei → Autopuffer)."
    )
    with st.form("umbuchung_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Von**")
            von_konto = st.selectbox("Konto", KONTO_OPT, format_func=konto_name.get, key="um_von_konto")
            von_topf = st.selectbox("Topf", TOPF_OPT, format_func=topf_name.get, key="um_von_topf")
        with c2:
            st.markdown("**Nach**")
            nach_konto = st.selectbox("Konto", KONTO_OPT, format_func=konto_name.get, key="um_nach_konto")
            nach_topf = st.selectbox("Topf", TOPF_OPT, format_func=topf_name.get, key="um_nach_topf")
        c3, c4 = st.columns(2)
        datum = c3.date_input("Datum", value=heute.date(), format="DD.MM.YYYY", key="um_datum")
        betrag = c4.number_input("Betrag (€)", min_value=0.0, step=0.01, format="%.2f", key="um_betrag")
        beschreibung = st.text_input("Beschreibung (optional)", key="um_beschreibung")
        ok = st.form_submit_button("Umbuchen", type="primary", width="stretch")
    if ok:
        try:
            zeilen = b.umbuchung(datum, von_konto, von_topf, nach_konto, nach_topf, betrag,
                                 beschreibung.strip() or None)
        except ValueError as exc:
            st.error(str(exc))
        else:
            _speichern(zeilen, f"{eur(betrag)} umgebucht.")

# --- KONTOABGLEICH -----------------------------------------------------------------
with tab_abgleich:
    st.caption(
        "Den echten Kontostand aus der Bank-App eintragen. Die Differenz wird gebucht: bei Zinskonten "
        "als Zinsertrag, sonst als Wertänderung (z. B. Depot). Danach rechnet das Tool ab hier weiter."
    )
    u = b.ueberblick(konten, toepfe, d["buchungen"], d["zinssaetze"], d["verbindlichkeiten"], heute)
    k_abgl = st.selectbox("Konto", KONTO_OPT, format_func=konto_name.get, key="abgl_konto")
    zeile = u.konten.set_index("id").loc[k_abgl]
    m1, m2 = st.columns(2)
    m1.metric("Gebucht", eur(zeile["gebucht"]))
    m2.metric("Geschätzt inkl. Zinsen", eur(zeile["saldo"]),
              delta=eur(zeile["aufgelaufen"]) if zeile["aufgelaufen"] else None, delta_color="off")
    with st.form("abgleich_form"):
        c1, c2 = st.columns(2)
        datum = c1.date_input("Stand vom", value=heute.date(), format="DD.MM.YYYY", key="abgl_datum")
        ist = c2.number_input("Tatsächlicher Kontostand (€)", value=float(round(zeile["saldo"], 2)),
                              step=0.01, format="%.2f", key=f"abgl_ist_{k_abgl}")
        ok = st.form_submit_button("Abgleichen", type="primary", width="stretch")
    if ok:
        gebucht_am = b.gebuchte_salden(d["buchungen"], datum).get(k_abgl, 0.0)
        konto_row = konten.set_index("id").loc[k_abgl].to_dict() | {"id": k_abgl}
        zeile_neu = b.abgleich(konto_row, ist, gebucht_am, datum)
        if zeile_neu is None:
            st.success("Stimmt bereits mit den Buchungen überein, nichts zu buchen.")
        else:
            art = "Zinsertrag" if zeile_neu["typ"] == "zins" else "Wertänderung"
            _speichern(zeile_neu, f"Abgleich gebucht: {eur(zeile_neu['betrag'])} {art}.")

# --- LISTE -------------------------------------------------------------------------
with tab_liste:
    bu = d["buchungen"]
    if bu.empty:
        st.info("Noch keine Buchungen.")
    else:
        f1, f2 = st.columns(2)
        monate = sorted(bu["datum"].dt.to_period("M").unique(), reverse=True)
        monat = f1.selectbox("Monat", [None] + monate,
                             format_func=lambda p: "Alle" if p is None else p.strftime("%m/%Y"))
        konto_filter = f2.selectbox("Konto", [None] + list(konten["id"]),
                                    format_func=lambda k: "Alle" if k is None else konto_name.get(k, k))
        sicht = bu
        if monat is not None:
            sicht = sicht[sicht["datum"].dt.to_period("M") == monat]
        if konto_filter is not None:
            sicht = sicht[sicht["konto_id"] == konto_filter]
        sicht = sicht.reset_index(drop=True)

        anzeige = pd.DataFrame({
            "Datum": sicht["datum"].dt.strftime("%d.%m.%Y"),
            "Konto": sicht["konto_id"].map(konto_name),
            "Topf": sicht["topf_id"].map(lambda t: topf_name.get(t, "?") if isinstance(t, str) else "frei"),
            "Betrag": sicht["betrag"],
            "Typ": sicht["typ"],
            "Kategorie": sicht["kategorie"].fillna(""),
            "Beschreibung": sicht["beschreibung"].fillna(""),
        })
        ein = sicht.loc[sicht["typ"] == "einnahme", "betrag"].sum()
        aus = sicht.loc[sicht["typ"] == "ausgabe", "betrag"].sum()
        s1, s2, s3 = st.columns(3)
        s1.metric("Einnahmen", eur(ein))
        s2.metric("Ausgaben", eur(-aus))
        s3.metric("Saldo", eur(ein + aus))

        auswahl = st.dataframe(
            anzeige, hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row",
            key="buchungen_tabelle",
            column_config={"Betrag": st.column_config.NumberColumn(format="%.2f €")},
        )
        zeilen = auswahl.selection.rows if auswahl is not None else []
        if zeilen:
            ids = list(sicht.loc[zeilen, "id"])
            hat_gruppe = sicht.loc[zeilen, "gruppe"].notna().any()
            st.warning(f"{len(ids)} Buchung(en) ausgewählt"
                       + (" – Umbuchungen werden mitsamt Gegenbuchung gelöscht." if hat_gruppe else "."))
            if st.button("🗑️ Ausgewählte löschen", type="primary"):
                try:
                    n = daten.buchungsgruppe_loeschen(ids)
                except Exception as exc:
                    st.error(daten.fehlertext(exc))
                else:
                    daten.nach_schreibzugriff(f"{n} Buchung(en) gelöscht.")
        else:
            st.caption("Zum Löschen Zeilen links anhaken.")
