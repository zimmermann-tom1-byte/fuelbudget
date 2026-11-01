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
posten = d["konten"]
aktive = posten[posten["aktiv"]].copy()
aktive["_o"] = aktive["stufe"].map({s: i for i, s in enumerate(b.STUFEN)})
aktive = aktive.sort_values(["_o", "sortierung", "name"])

st.title("Buchungen 🧾")

if aktive.empty:
    st.info("Lege zuerst unter **Posten** mindestens einen Posten an.")
    st.page_link("seiten/verwaltung.py", label="Zu den Posten", icon="🗂️")
    st.stop()

P_OPT = list(aktive["id"])
posten_name = dict(zip(posten["id"], posten["name"]))
posten_label = {r["id"]: f"{r['name']} · {b.STUFEN[r['stufe']]}" for _, r in posten.iterrows()}
topf_von = {kid: (t if pd.notna(t) else None) for kid, t in zip(posten["id"], posten["standard_topf_id"])}


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
    offene_v = b.verbindlichkeiten_offen(d["verbindlichkeiten"], d["buchungen"])
    offene_v = offene_v[offene_v["offen"] > 0.005] if not offene_v.empty else offene_v
    v_opt = [None] + (list(offene_v["id"]) if not offene_v.empty else [])
    v_name = {None: "– keine –", **({} if offene_v.empty else
                                    {r["id"]: f"{r['name']} (offen {eur(r['offen'])})"
                                     for _, r in offene_v.iterrows()})}

    with st.form("einnahme_ausgabe_form", clear_on_submit=True):
        p_id = st.selectbox("Posten", P_OPT, format_func=posten_label.get, key="ea_posten")
        c1, c2 = st.columns(2)
        datum = c1.date_input("Datum", value=heute.date(), format="DD.MM.YYYY")
        betrag = c2.number_input("Betrag (€)", min_value=0.0, step=0.01, format="%.2f")
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
                "datum": str(datum), "konto_id": p_id, "topf_id": topf_von.get(p_id),
                "betrag": -round(betrag, 2) if ist_ausgabe else round(betrag, 2),
                "typ": "ausgabe" if ist_ausgabe else "einnahme",
                "kategorie": kategorie, "beschreibung": beschreibung.strip() or None,
                "verbindlichkeit_id": verbindlichkeit,
            }, f"{'Ausgabe' if ist_ausgabe else 'Einnahme'} über {eur(betrag)} gespeichert.")

# --- UMBUCHUNG ---------------------------------------------------------------------
with tab_um:
    st.caption("Geld von einem Posten auf einen anderen verschieben, z. B. Lohneingang → Fixkosten 3–6 Monate.")
    with st.form("umbuchung_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        von = c1.selectbox("Von", P_OPT, format_func=posten_label.get, key="um_von")
        nach = c2.selectbox("Nach", P_OPT, format_func=posten_label.get, key="um_nach",
                            index=1 if len(P_OPT) > 1 else 0)
        c3, c4 = st.columns(2)
        datum = c3.date_input("Datum", value=heute.date(), format="DD.MM.YYYY", key="um_datum")
        betrag = c4.number_input("Betrag (€)", min_value=0.0, step=0.01, format="%.2f", key="um_betrag")
        beschreibung = st.text_input("Beschreibung (optional)", key="um_beschreibung")
        ok = st.form_submit_button("Umbuchen", type="primary", width="stretch")
    if ok:
        try:
            zeilen = b.umbuchung(datum, von, topf_von.get(von), nach, topf_von.get(nach), betrag,
                                 beschreibung.strip() or None)
        except ValueError as exc:
            st.error(str(exc))
        else:
            _speichern(zeilen, f"{eur(betrag)} von {posten_name[von]} nach {posten_name[nach]} umgebucht.")

# --- KONTOABGLEICH -----------------------------------------------------------------
with tab_abgleich:
    st.caption(
        "Den echten Stand aus der Bank-App eintragen. Die Differenz wird gebucht: bei Zinsposten als "
        "Zinsertrag, sonst als Wertänderung. Posten mit gleicher Abgleichsgruppe (z. B. ein Geldmarktfonds "
        "für Geldbuße und Strafzettel) gleichst du gemeinsam ab, die Differenz wird anteilig verteilt."
    )
    u = b.ueberblick(posten, d["buchungen"], d["zinssaetze"], d["verbindlichkeiten"], heute)
    up = u.posten[u.posten["aktiv"]].set_index("id")
    # Auswahl: je Gruppe ein Eintrag, Posten ohne Gruppe einzeln
    einheiten = {}
    for kid, r in up.iterrows():
        schluessel = f"gruppe:{r['abgleich_gruppe']}" if pd.notna(r["abgleich_gruppe"]) else f"posten:{kid}"
        einheiten.setdefault(schluessel, []).append(kid)

    def _einheit_label(k):
        ids = einheiten[k]
        if k.startswith("gruppe:"):
            return f"{k[7:]} ({', '.join(posten_name[i] for i in ids)})"
        return posten_name[ids[0]]

    einheit = st.selectbox("Was gleichst du ab?", list(einheiten), format_func=_einheit_label, key="abgl_einheit")
    ids = einheiten[einheit]
    gebucht_sum = float(up.loc[ids, "gebucht"].sum())
    geschaetzt = float(up.loc[ids, "saldo"].sum())
    m1, m2 = st.columns(2)
    m1.metric("Gebucht", eur(gebucht_sum))
    m2.metric("Geschätzt inkl. Zinsen", eur(geschaetzt),
              delta=eur(geschaetzt - gebucht_sum) if abs(geschaetzt - gebucht_sum) > 0.005 else None,
              delta_color="off")
    with st.form("abgleich_form"):
        c1, c2 = st.columns(2)
        datum = c1.date_input("Stand vom", value=heute.date(), format="DD.MM.YYYY", key="abgl_datum")
        ist = c2.number_input("Tatsächlicher Stand (€)", value=float(round(geschaetzt, 2)),
                              step=0.01, format="%.2f", key=f"abgl_ist_{einheit}")
        ok = st.form_submit_button("Abgleichen", type="primary", width="stretch")
    if ok:
        gebucht_am = b.gebuchte_salden(d["buchungen"], datum)
        eintraege = [{"id": kid, "ertragsart": up.at[kid, "ertragsart"],
                      "standard_topf_id": topf_von.get(kid), "gebucht": float(gebucht_am.get(kid, 0.0))}
                     for kid in ids]
        zeilen = b.gruppen_abgleich(eintraege, ist, datum)
        if not zeilen:
            st.success("Stimmt bereits mit den Buchungen überein, nichts zu buchen.")
        else:
            summe = sum(z["betrag"] for z in zeilen)
            art = "Zinsertrag" if all(z["typ"] == "zins" for z in zeilen) else "Wertänderung"
            _speichern(zeilen, f"Abgleich gebucht: {eur(summe)} {art}"
                               + (f", verteilt auf {len(zeilen)} Posten." if len(zeilen) > 1 else "."))

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
        p_filter = f2.selectbox("Posten", [None] + list(posten["id"]),
                                format_func=lambda k: "Alle" if k is None else posten_name.get(k, k))
        sicht = bu
        if monat is not None:
            sicht = sicht[sicht["datum"].dt.to_period("M") == monat]
        if p_filter is not None:
            sicht = sicht[sicht["konto_id"] == p_filter]
        sicht = sicht.reset_index(drop=True)

        anzeige = pd.DataFrame({
            "Datum": sicht["datum"].dt.strftime("%d.%m.%Y"),
            "Posten": sicht["konto_id"].map(posten_name),
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
