"""Stammdaten: Konten, Toepfe, Zinssaetze, Verbindlichkeiten und Startbestand."""

from datetime import date, datetime
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
konten, toepfe = d["konten"], d["toepfe"]
konto_name = dict(zip(konten["id"], konten["name"]))
topf_name = {None: "– ohne Topf (frei) –", **dict(zip(toepfe["id"], toepfe["name"]))}

st.title("Konten & Töpfe 🗂️")


def _schreiben(aktion, meldung):
    """aktion: Funktion ohne Argumente, die in die Datenbank schreibt."""
    try:
        aktion()
    except Exception as exc:
        st.error(daten.fehlertext(exc))
        return
    daten.nach_schreibzugriff(meldung)


def _zahl_oder_none(x):
    return None if x is None or x == 0 else round(float(x), 2)


def _auswahl_neu_oder(df, label, key):
    """Selectbox: neuen Eintrag anlegen oder vorhandenen bearbeiten."""
    opt = [None] + list(df["id"])
    namen = dict(zip(df["id"], df["name"] + df["aktiv"].map({True: "", False: " (inaktiv)"})))
    return st.selectbox(label, opt, key=key,
                        format_func=lambda i: "➕ Neu anlegen" if i is None else namen.get(i, i))


tab_start, tab_toepfe, tab_konten, tab_zins, tab_verb = st.tabs(
    ["🚀 Start", "🫙 Töpfe", "🏦 Konten", "📈 Zinssätze", "📉 Verbindlichkeiten"]
)

# --- START ------------------------------------------------------------------------
with tab_start:
    schritte = [
        ("Töpfe anlegen", not toepfe.empty,
         "Wofür ist dein Geld da? z. B. Autopuffer, Fixkosten-Rücklage, Geldbuße, Blitzer-Rücklage."),
        ("Konten anlegen", not konten.empty,
         "Wo liegt dein Geld? Je Konto Art, Ertragsart, Risiko und Standardtopf."),
        ("Zinssätze eintragen", not d["zinssaetze"].empty,
         "Für Zinskonten den aktuellen Satz, für Dividenden die erwartete Ausschüttungsrendite, "
         "fürs Depot deine angenommene Rendite."),
        ("Startbestand buchen", (d["buchungen"]["typ"] == "eroeffnung").any(),
         "Je Konto den Stand am Stichtag eintragen, aufgeteilt auf die Töpfe."),
    ]
    st.markdown("**Einrichtung in vier Schritten**")
    for nr, (titel, fertig, text) in enumerate(schritte, 1):
        st.markdown(f"{'✅' if fertig else '⬜'} **{nr}. {titel}** – {text}")

    st.markdown("---")
    st.markdown("### Startbestand je Konto")
    aktive_konten = konten[konten["aktiv"]]
    if aktive_konten.empty:
        st.info("Lege zuerst Konten an.")
    else:
        k_id = st.selectbox("Konto", list(aktive_konten["id"]), format_func=konto_name.get, key="start_konto")
        bu = d["buchungen"]
        vorhanden = bu[(bu["konto_id"] == k_id) & (bu["typ"] == "eroeffnung")]
        if not vorhanden.empty:
            st.warning(
                f"Für dieses Konto gibt es schon einen Startbestand über {eur(vorhanden['betrag'].sum())} "
                f"(Stichtag {vorhanden['datum'].min().strftime('%d.%m.%Y')}). Speichern ersetzt ihn."
            )
        aktive_toepfe = toepfe[toepfe["aktiv"]]
        vorbelegung = (vorhanden.assign(t=vorhanden["topf_id"].where(vorhanden["topf_id"].notna(), "frei"))
                       .groupby("t")["betrag"].sum())
        editor_df = pd.DataFrame({
            "topf_id": ["frei"] + list(aktive_toepfe["id"]),
            "Topf": ["– ohne Topf (frei) –"] + list(aktive_toepfe["name"]),
        })
        editor_df["Betrag (€)"] = editor_df["topf_id"].map(vorbelegung).fillna(0.0).astype(float)
        stichtag_default = vorhanden["datum"].min().date() if not vorhanden.empty else date(2026, 11, 1)
        with st.form(f"startbestand_form_{k_id}"):
            stichtag = st.date_input("Stichtag", value=stichtag_default, format="DD.MM.YYYY")
            bearbeitet = st.data_editor(
                editor_df, hide_index=True, width="stretch", disabled=["Topf"],
                column_order=["Topf", "Betrag (€)"],
                column_config={"Betrag (€)": st.column_config.NumberColumn(format="%.2f", step=0.01)},
                key=f"start_editor_{k_id}",
            )
            ok = st.form_submit_button("Startbestand speichern", type="primary", width="stretch")
        summe = float(pd.to_numeric(bearbeitet["Betrag (€)"], errors="coerce").fillna(0).sum())
        st.caption(f"Summe: {eur(summe)} – sollte dem Kontostand am Stichtag entsprechen.")
        if ok:
            zeilen = []
            for _, r in bearbeitet.iterrows():
                betrag = round(float(r["Betrag (€)"] or 0), 2)
                if abs(betrag) >= 0.005:
                    zeilen.append({"datum": str(stichtag), "konto_id": k_id,
                                   "topf_id": None if r["topf_id"] == "frei" else r["topf_id"],
                                   "betrag": betrag, "typ": "eroeffnung", "beschreibung": "Startbestand"})

            def _start():
                if not vorhanden.empty:
                    daten.loeschen("buchungen", list(vorhanden["id"]))
                if zeilen:
                    daten.einfuegen("buchungen", zeilen)
            _schreiben(_start, f"Startbestand für {konto_name[k_id]} gespeichert ({eur(summe)}).")

# --- TOEPFE ------------------------------------------------------------------------
with tab_toepfe:
    st.caption(
        "Ein Topf ist ein Zweck, kein Ort: Der Topf „Autopuffer“ kann auf mehreren Konten liegen. "
        "Art „frei“ zählt zum frei verfügbaren Geld, alle anderen gelten als verplant."
    )
    t_id = _auswahl_neu_oder(toepfe, "Topf", "topf_auswahl")
    alt = toepfe.set_index("id").loc[t_id] if t_id else None
    arten = list(b.TOPF_ARTEN)
    with st.form(f"topf_form_{t_id}"):
        name = st.text_input("Name", value="" if alt is None else alt["name"], max_chars=60)
        art = st.selectbox("Art", arten, format_func=b.TOPF_ARTEN.get,
                           index=arten.index(alt["art"]) if alt is not None else 1)
        c1, c2 = st.columns(2)
        ziel = c1.number_input("Zielbetrag (€, optional)", min_value=0.0, step=10.0, format="%.2f",
                               value=float(alt["zielbetrag"]) if alt is not None and pd.notna(alt["zielbetrag"]) else 0.0)
        rate = c2.number_input("Monatliche Sparrate (€, optional)", min_value=0.0, step=5.0, format="%.2f",
                               value=float(alt["sparrate_monat"]) if alt is not None and pd.notna(alt["sparrate_monat"]) else 0.0,
                               help="Für Rücklagen, die monatlich heruntergerechnet werden (z. B. Versicherung alle 3 Monate).")
        notiz = st.text_input("Notiz", value="" if alt is None or pd.isna(alt["notiz"]) else alt["notiz"])
        aktiv = st.checkbox("Aktiv", value=True if alt is None else bool(alt["aktiv"]))
        ok = st.form_submit_button("Speichern", type="primary", width="stretch")
    if ok:
        werte = {"name": name.strip(), "art": art, "zielbetrag": _zahl_oder_none(ziel),
                 "sparrate_monat": _zahl_oder_none(rate), "notiz": notiz.strip() or None, "aktiv": aktiv}
        if not werte["name"]:
            st.error("Bitte einen Namen eingeben.")
        elif t_id is None:
            werte["sortierung"] = len(toepfe)
            _schreiben(lambda: daten.einfuegen("toepfe", werte), f"Topf „{werte['name']}“ angelegt.")
        else:
            _schreiben(lambda: daten.aendern("toepfe", t_id, werte), f"Topf „{werte['name']}“ gespeichert.")
    if t_id:
        with st.popover("🗑️ Topf löschen"):
            st.write("Buchungen in diesem Topf werden dann als „ohne Topf“ geführt. Meist ist „Aktiv“ "
                     "abwählen die bessere Wahl.")
            if st.button("Endgültig löschen", key="topf_loeschen"):
                _schreiben(lambda: daten.loeschen("toepfe", [t_id]), "Topf gelöscht.")

# --- KONTEN ------------------------------------------------------------------------
with tab_konten:
    st.caption("Ein Konto ist ein Ort, an dem Geld liegt: Bankkonto, Depot, Geldmarktfonds, Bargeld …")
    k_id = _auswahl_neu_oder(konten, "Konto", "konto_auswahl")
    alt = konten.set_index("id").loc[k_id] if k_id else None
    k_arten, e_arten = list(b.KONTO_ARTEN), list(b.ERTRAGSARTEN)
    topf_opt = [None] + list(toepfe.loc[toepfe["aktiv"], "id"])
    if alt is not None and pd.notna(alt["standard_topf_id"]) and alt["standard_topf_id"] not in topf_opt:
        topf_opt.append(alt["standard_topf_id"])
    with st.form(f"konto_form_{k_id}"):
        c1, c2 = st.columns(2)
        name = c1.text_input("Name", value="" if alt is None else alt["name"], max_chars=60,
                             placeholder="z. B. TR Depot, T212 Geldmarktfonds")
        institut = c2.text_input("Anbieter", value="" if alt is None or pd.isna(alt["institut"]) else alt["institut"],
                                 placeholder="z. B. Trade Republic")
        c3, c4 = st.columns(2)
        art = c3.selectbox("Art", k_arten, format_func=b.KONTO_ARTEN.get,
                           index=k_arten.index(alt["art"]) if alt is not None else 1)
        ertragsart = c4.selectbox("Ertragsart", e_arten, format_func=b.ERTRAGSARTEN.get,
                                  index=e_arten.index(alt["ertragsart"]) if alt is not None else 0,
                                  help="Zinsen werden täglich geschätzt. Ausschüttungen ebenso (als Erwartung). "
                                       "Kursentwicklung wird nur als Annahme angezeigt.")
        risiko = st.select_slider("Risiko", options=list(b.RISIKO_STUFEN), format_func=b.RISIKO_STUFEN.get,
                                  value=int(alt["risiko"]) if alt is not None else 1)
        st_topf = st.selectbox(
            "Standardtopf", topf_opt, format_func=lambda t: topf_name.get(t, "?"),
            index=topf_opt.index(alt["standard_topf_id"]) if alt is not None and alt["standard_topf_id"] in topf_opt else 0,
            help="Hierhin fließen Zinsen und Abgleichsdifferenzen. Wird bei Buchungen vorausgewählt.",
        )
        notiz = st.text_input("Notiz", value="" if alt is None or pd.isna(alt["notiz"]) else alt["notiz"])
        aktiv = st.checkbox("Aktiv", value=True if alt is None else bool(alt["aktiv"]))
        ok = st.form_submit_button("Speichern", type="primary", width="stretch")
    if ok:
        werte = {"name": name.strip(), "institut": institut.strip() or None, "art": art,
                 "ertragsart": ertragsart, "risiko": int(risiko), "standard_topf_id": st_topf,
                 "notiz": notiz.strip() or None, "aktiv": aktiv}
        if not werte["name"]:
            st.error("Bitte einen Namen eingeben.")
        elif k_id is None:
            werte["sortierung"] = len(konten)
            _schreiben(lambda: daten.einfuegen("konten", werte),
                       f"Konto „{werte['name']}“ angelegt. Als Nächstes: Zinssatz und Startbestand.")
        else:
            _schreiben(lambda: daten.aendern("konten", k_id, werte), f"Konto „{werte['name']}“ gespeichert.")
    if k_id:
        with st.popover("🗑️ Konto löschen"):
            st.write("Geht nur, solange keine Buchungen auf dem Konto liegen. Sonst „Aktiv“ abwählen.")
            if st.button("Endgültig löschen", key="konto_loeschen"):
                _schreiben(lambda: daten.loeschen("konten", [k_id]), "Konto gelöscht.")

# --- ZINSSAETZE --------------------------------------------------------------------
with tab_zins:
    st.caption(
        "Sätze ändern sich, deshalb mit Gültigkeitsdatum: Ein neuer Satz gilt ab seinem Datum, "
        "die Vergangenheit bleibt richtig berechnet. Werte in % pro Jahr, vor Steuern."
    )
    ertrag_konten = konten[konten["ertragsart"] != "keine"]
    if ertrag_konten.empty:
        st.info("Kein Konto mit Ertragsart Zinsen, Ausschüttungen oder Kursentwicklung vorhanden.")
    else:
        with st.form("zins_form", clear_on_submit=True):
            c1, c2, c3 = st.columns([2, 1, 1])
            z_konto = c1.selectbox("Konto", list(ertrag_konten["id"]), format_func=konto_name.get)
            z_ab = c2.date_input("Gültig ab", value=heute.date(), format="DD.MM.YYYY")
            z_satz = c3.number_input("Satz % p. a.", step=0.05, format="%.2f", value=0.0)
            z_notiz = st.text_input("Notiz (optional)", placeholder="z. B. Zinssenkung EZB")
            ok = st.form_submit_button("Satz speichern", type="primary", width="stretch")
        if ok:
            _schreiben(lambda: daten.einfuegen("zinssaetze", {
                "konto_id": z_konto, "gueltig_ab": str(z_ab), "satz_pa": round(z_satz, 4),
                "notiz": z_notiz.strip() or None}),
                f"{z_satz:.2f} % für {konto_name[z_konto]} ab {z_ab:%d.%m.%Y} gespeichert.")

        z = d["zinssaetze"]
        if not z.empty:
            z = z.sort_values(["konto_id", "gueltig_ab"], ascending=[True, False]).reset_index(drop=True)
            anzeige = pd.DataFrame({
                "Konto": z["konto_id"].map(konto_name),
                "Gültig ab": z["gueltig_ab"].dt.strftime("%d.%m.%Y"),
                "Satz % p. a.": z["satz_pa"],
                "Notiz": z["notiz"].fillna(""),
            })
            auswahl = st.dataframe(anzeige, hide_index=True, width="stretch", on_select="rerun",
                                   selection_mode="multi-row", key="zins_tabelle",
                                   column_config={"Satz % p. a.": st.column_config.NumberColumn(format="%.2f %%")})
            zeilen = auswahl.selection.rows if auswahl is not None else []
            if zeilen and st.button(f"🗑️ {len(zeilen)} Satz/Sätze löschen"):
                _schreiben(lambda: daten.loeschen("zinssaetze", list(z.loc[zeilen, "id"])), "Gelöscht.")

# --- VERBINDLICHKEITEN -------------------------------------------------------------
with tab_verb:
    st.caption(
        "Schulden oder fest zugesagte Zahlungen (z. B. Geldbuße in Raten). Sie werden vom "
        "Nettovermögen abgezogen. Tilgungen buchst du als Ausgabe mit Verweis auf die Verbindlichkeit."
    )
    vb = d["verbindlichkeiten"]
    v_id = _auswahl_neu_oder(vb.assign(aktiv=~vb["erledigt"]) if not vb.empty else vb.assign(aktiv=True),
                             "Verbindlichkeit", "verb_auswahl")
    alt = vb.set_index("id").loc[v_id] if v_id else None
    topf_opt = [None] + list(toepfe.loc[toepfe["aktiv"], "id"])
    with st.form(f"verb_form_{v_id}"):
        name = st.text_input("Name", value="" if alt is None else alt["name"], max_chars=60)
        c1, c2 = st.columns(2)
        gesamt = c1.number_input("Gesamtbetrag (€)", min_value=0.0, step=10.0, format="%.2f",
                                 value=float(alt["betrag_gesamt"]) if alt is not None else 0.0)
        rate = c2.number_input("Monatsrate (€, optional)", min_value=0.0, step=10.0, format="%.2f",
                               value=float(alt["rate_monat"]) if alt is not None and pd.notna(alt["rate_monat"]) else 0.0)
        v_topf = st.selectbox(
            "Zugehöriger Topf (optional)", topf_opt, format_func=lambda t: topf_name.get(t, "?"),
            index=topf_opt.index(alt["topf_id"]) if alt is not None and alt["topf_id"] in topf_opt else 0,
            help="Der Topf, in dem das Geld dafür bereitliegt.",
        )
        notiz = st.text_input("Notiz", value="" if alt is None or pd.isna(alt["notiz"]) else alt["notiz"])
        erledigt = st.checkbox("Erledigt", value=False if alt is None else bool(alt["erledigt"]))
        ok = st.form_submit_button("Speichern", type="primary", width="stretch")
    if ok:
        werte = {"name": name.strip(), "betrag_gesamt": round(gesamt, 2), "rate_monat": _zahl_oder_none(rate),
                 "topf_id": v_topf, "notiz": notiz.strip() or None, "erledigt": erledigt}
        if not werte["name"] or gesamt <= 0:
            st.error("Bitte Name und einen Gesamtbetrag größer 0 eingeben.")
        elif v_id is None:
            _schreiben(lambda: daten.einfuegen("verbindlichkeiten", werte), "Verbindlichkeit angelegt.")
        else:
            _schreiben(lambda: daten.aendern("verbindlichkeiten", v_id, werte), "Verbindlichkeit gespeichert.")
    offen = b.verbindlichkeiten_offen(vb, d["buchungen"])
    if not offen.empty:
        st.dataframe(pd.DataFrame({
            "Name": offen["name"], "Gesamt": offen["betrag_gesamt"], "Getilgt": offen["getilgt"],
            "Offen": offen["offen"], "Erledigt": offen["erledigt"],
        }), hide_index=True, width="stretch",
            column_config={c: st.column_config.NumberColumn(format="%.2f €") for c in ("Gesamt", "Getilgt", "Offen")})
