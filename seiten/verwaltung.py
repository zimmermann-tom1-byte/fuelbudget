"""Stammdaten: Konten (Orte), Toepfe (Zwecke), Startbestand, Zinssaetze,
Verbindlichkeiten und Verknuepfungen (FuelBudget, Lohn)."""

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

d = daten.alles_aktuell(heute)
konten, toepfe = d["konten"], d["toepfe"]
konto_name = dict(zip(konten["id"], konten["name"]))
topf_name = dict(zip(toepfe["id"], toepfe["name"]))
topf_konto = dict(zip(toepfe["id"], toepfe["konto_id"]))


def topf_label(tid):
    return f"{konto_name.get(topf_konto.get(tid), '?')} › {topf_name.get(tid, '?')}"


st.title("Konten & Töpfe 🗂️")
st.caption(
    "**Konto** = Ort, an dem Geld liegt (Volksbank-Fixkostenkonto, T212 Ausgabentopf, Geldmarktfonds, Brieftasche …). "
    "**Topf** = Zweck innerhalb eines Kontos (KFZ-Versicherung, Gym, Geldbuße …). Die **Stufe** hängt am Topf."
)


def _schreiben(aktion, meldung):
    try:
        aktion()
    except Exception as exc:
        st.error(daten.fehlertext(exc))
        return
    daten.nach_schreibzugriff(meldung)


def _zahl_oder_none(x):
    return None if x is None or x == 0 else round(float(x), 2)


def _text(x):
    return "" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)


tab_start, tab_konten, tab_toepfe, tab_zins, tab_verb, tab_verkn = st.tabs(
    ["🚀 Start", "🏦 Konten", "🫙 Töpfe", "📈 Zinssätze", "📉 Verbindlichkeiten", "🔗 FuelBudget & Lohn"]
)

# --- START: Startbestand je Topf ----------------------------------------------------
with tab_start:
    st.markdown("### Startbestand")
    st.caption(
        "Der Startbestand ist der Anker jedes Kontos: Alles davor ist darin enthalten. Nur geänderte Zeilen werden "
        "neu gebucht. Bei Fonds-Konten werden die Anteile über den Einstandskurs (siehe Konten) berechnet."
    )
    aktive_t = toepfe[toepfe["aktiv"] & toepfe["konto_id"].isin(konten.loc[konten["aktiv"], "id"])]
    if aktive_t.empty:
        st.info("Lege zuerst ein Konto an.")
    else:
        bu = d["buchungen"]
        eroeffnung = bu[bu["typ"] == "eroeffnung"]
        vorbelegung = eroeffnung.groupby("topf_id")["betrag"].sum()
        stichtag_default = eroeffnung["datum"].min().date() if not eroeffnung.empty else date(2026, 11, 1)
        editor_df = pd.DataFrame({
            "id": aktive_t["id"],
            "Konto": aktive_t["konto_id"].map(konto_name),
            "Topf": aktive_t["name"],
            "Betrag (€)": aktive_t["id"].map(vorbelegung).fillna(0.0).astype(float),
        }).sort_values(["Konto", "Topf"])
        with st.form("startbestand_form"):
            stichtag = st.date_input("Stichtag (für neue Einträge)", value=stichtag_default, format="DD.MM.YYYY")
            bearbeitet = st.data_editor(
                editor_df, hide_index=True, width="stretch", disabled=["Konto", "Topf"],
                column_order=["Konto", "Topf", "Betrag (€)"],
                column_config={"Betrag (€)": st.column_config.NumberColumn(format="%.2f", step=0.01)},
                key="start_editor",
            )
            ok = st.form_submit_button("Startbestand speichern", type="primary", width="stretch")
        if ok:
            geaendert = []
            for _, r in bearbeitet.iterrows():
                neu = round(float(r["Betrag (€)"] or 0), 2)
                alt = round(float(vorbelegung.get(r["id"], 0.0)), 2)
                if neu != alt:
                    geaendert.append((r["id"], neu))
            if not geaendert:
                st.info("Keine Änderung.")
            else:
                def _start():
                    ids = [t for t, _ in geaendert]
                    alte = eroeffnung[eroeffnung["topf_id"].isin(ids)]
                    datum_je_konto = eroeffnung.groupby("konto_id")["datum"].min()
                    if not alte.empty:
                        daten.loeschen("buchungen", list(alte["id"]))
                    zeilen = []
                    for tid, betrag in geaendert:
                        if abs(betrag) < 0.005:
                            continue
                        kid = topf_konto[tid]
                        tag = datum_je_konto.get(kid)
                        zeilen.append({"datum": str((tag.date() if tag is not None else stichtag)), "konto_id": kid,
                                       "topf_id": tid, "betrag": betrag, "typ": "eroeffnung",
                                       "beschreibung": "Startbestand"})
                    if zeilen:
                        daten.konto_buchungen_speichern(zeilen, d["kurse"], d["zinssaetze"], konten)
                _schreiben(_start, f"Startbestand für {len(geaendert)} Topf/Töpfe gespeichert.")

# --- KONTEN ------------------------------------------------------------------------
with tab_konten:
    opt = [None] + list(konten["id"])
    namen = dict(zip(konten["id"], konten["name"] + konten["aktiv"].map({True: "", False: " (inaktiv)"})))
    k_id = st.selectbox("Konto", opt, key="konto_auswahl",
                        format_func=lambda i: "➕ Neu anlegen" if i is None else namen.get(i, i))
    alt = konten.set_index("id").loc[k_id] if k_id else None
    k_arten, e_arten, stufen = list(b.KONTO_ARTEN), list(b.ERTRAGSARTEN), list(b.STUFEN)
    with st.form(f"konto_form_{k_id}"):
        c1, c2 = st.columns(2)
        name = c1.text_input("Name (Ort)", value="" if alt is None else alt["name"], max_chars=60,
                             placeholder="z. B. Volksbank Fixkostenkonto")
        institut = c2.text_input("Anbieter", value="" if alt is None else _text(alt["institut"]),
                                 placeholder="z. B. Volksbank")
        c3, c4 = st.columns(2)
        art = c3.selectbox("Produkt", k_arten, format_func=b.KONTO_ARTEN.get,
                           index=k_arten.index(alt["art"]) if alt is not None else 0)
        ertragsart = c4.selectbox("Ertragsart", e_arten, format_func=b.ERTRAGSARTEN.get,
                                  index=e_arten.index(alt["ertragsart"]) if alt is not None else 3)
        c5, c6 = st.columns(2)
        bewertung = c5.selectbox("Bewertung", ["saldo", "anteile"],
                                 format_func={"saldo": "Kontostand (Saldo)", "anteile": "Fonds (Anteile × Kurs)"}.get,
                                 index=1 if alt is not None and alt["bewertung"] == "anteile" else 0,
                                 help="Fonds: Jede Buchung kauft/verkauft Anteile, Buchgewinne verteilen sich exakt auf die Töpfe.")
        wkn = c6.text_input("WKN (optional)", value="" if alt is None else _text(alt["wkn"]), max_chars=20)
        ausgezahlt = st.checkbox("Erträge werden tatsächlich ausgezahlt",
                                 value=False if alt is None else bool(alt["ertrag_ausgezahlt"]),
                                 help="Nur ausgezahlte Erträge zählen für die Fixkostendeckung. Thesaurierende Fonds: nein.")
        risiko = st.select_slider("Risiko", options=list(b.RISIKO_STUFEN), format_func=b.RISIKO_STUFEN.get,
                                  value=int(alt["risiko"]) if alt is not None else 1)
        einstandskurs = None
        if alt is None or alt["bewertung"] == "anteile":
            einstandskurs = st.number_input("Einstandskurs je Anteil (nur Fonds, für den Startbestand)",
                                            min_value=0.0, value=None, step=0.001, format="%.3f")
        erster_name, erste_stufe = None, None
        if alt is None:
            st.markdown("**Erster Topf**")
            c7, c8 = st.columns(2)
            erster_name = c7.text_input("Name des ersten Topfes", placeholder="z. B. Autorücklagen")
            erste_stufe = c8.selectbox("Stufe", stufen, format_func=b.STUFEN.get, key="erste_stufe")
        notiz = st.text_input("Notiz", value="" if alt is None else _text(alt["notiz"]))
        aktiv = st.checkbox("Aktiv", value=True if alt is None else bool(alt["aktiv"]))
        ok = st.form_submit_button("Speichern", type="primary", width="stretch")
    if ok:
        werte = {"name": name.strip(), "institut": institut.strip() or None, "art": art, "ertragsart": ertragsart,
                 "risiko": int(risiko), "bewertung": bewertung, "wkn": wkn.strip() or None,
                 "ertrag_ausgezahlt": ausgezahlt, "notiz": notiz.strip() or None, "aktiv": aktiv}
        if not werte["name"]:
            st.error("Bitte einen Namen eingeben.")
        elif k_id is None:
            if not (erster_name or "").strip():
                st.error("Bitte einen Namen für den ersten Topf eingeben.")
            else:
                werte["sortierung"] = len(konten)

                def _neu():
                    kid = daten.konto_anlegen(werte, {"name": erster_name.strip(), "stufe": erste_stufe})
                    if bewertung == "anteile" and einstandskurs:
                        daten.einfuegen("kurse", {"konto_id": kid, "datum": str(heute.date()),
                                                  "kurs": einstandskurs, "notiz": "Einstandskurs"})
                _schreiben(_neu, f"Konto „{werte['name']}“ angelegt. Als Nächstes: Startbestand.")
        else:
            def _aendern():
                daten.aendern("konten", k_id, werte)
                if bewertung == "anteile" and einstandskurs:
                    erste = d["buchungen"][(d["buchungen"]["konto_id"] == k_id) & (d["buchungen"]["typ"] == "eroeffnung")]
                    tag = erste["datum"].min() if not erste.empty else heute
                    daten.einfuegen_oder_ersetzen("kurse", {"konto_id": k_id, "datum": str(tag.date()),
                                                            "kurs": einstandskurs, "notiz": "Einstandskurs"},
                                                  konflikt="konto_id,datum")
            _schreiben(_aendern, f"Konto „{werte['name']}“ gespeichert.")
    if k_id:
        with st.popover("🗑️ Konto löschen"):
            st.write("Geht nur, solange keine Buchungen auf dem Konto liegen. Sonst „Aktiv“ abwählen.")
            if st.button("Endgültig löschen", key="konto_loeschen"):
                _schreiben(lambda: daten.loeschen("konten", [k_id]), "Konto gelöscht.")

    if not konten.empty:
        st.markdown("---")
        st.dataframe(pd.DataFrame({
            "Konto": konten["name"],
            "Anbieter": konten["institut"].fillna(""),
            "Produkt": konten["art"].map(b.KONTO_ARTEN),
            "Bewertung": konten["bewertung"].map({"saldo": "Saldo", "anteile": "Fonds"}),
            "Ertrag": konten["ertrag_ausgezahlt"].map({True: "ausgezahlt", False: "bleibt liegen"}),
            "Töpfe": konten["id"].map(toepfe[toepfe["aktiv"]].groupby("konto_id").size()).fillna(0).astype(int),
            "Aktiv": konten["aktiv"],
        }), hide_index=True, width="stretch")

# --- TOEPFE ------------------------------------------------------------------------
with tab_toepfe:
    zugeordnet = toepfe[toepfe["konto_id"].notna()]
    opt = [None] + list(zugeordnet["id"])
    t_id = st.selectbox("Topf", opt, key="topf_auswahl",
                        format_func=lambda i: "➕ Neu anlegen" if i is None else
                        topf_label(i) + ("" if bool(toepfe.set_index("id").at[i, "aktiv"]) else " (inaktiv)"))
    alt = toepfe.set_index("id").loc[t_id] if t_id else None
    k_opt = list(konten.loc[konten["aktiv"], "id"])
    if alt is not None and alt["konto_id"] not in k_opt:
        k_opt.append(alt["konto_id"])
    stufen = list(b.STUFEN)
    with st.form(f"topf_form_{t_id}"):
        konto = st.selectbox("Konto", k_opt, format_func=konto_name.get,
                             index=k_opt.index(alt["konto_id"]) if alt is not None else 0,
                             disabled=alt is not None,
                             help="Ein Topf bleibt bei seinem Konto. Geld verschiebst du per Umbuchung.")
        name = st.text_input("Name (Zweck)", value="" if alt is None else alt["name"], max_chars=60)
        stufe = st.selectbox("Stufe", stufen, format_func=lambda s: f"{b.STUFEN[s]} – {b.STUFE_ERKLAERUNG[s]}",
                             index=stufen.index(alt["stufe"]) if alt is not None and alt["stufe"] in stufen else 0)
        ziel = st.number_input("Zielbetrag (€, optional)", min_value=0.0, step=50.0, format="%.2f",
                               value=float(alt["zielbetrag"]) if alt is not None and pd.notna(alt["zielbetrag"]) else 0.0)
        standard = st.checkbox("Standardtopf des Kontos (hierhin fließen Zinsen und Abgleichsdifferenzen)",
                               value=alt is not None and konten.set_index("id").at[alt["konto_id"], "standard_topf_id"] == t_id)
        notiz = st.text_input("Notiz", value="" if alt is None else _text(alt["notiz"]))
        aktiv = st.checkbox("Aktiv", value=True if alt is None else bool(alt["aktiv"]))
        ok = st.form_submit_button("Speichern", type="primary", width="stretch")
    if ok:
        werte = {"name": name.strip(), "stufe": stufe, "zielbetrag": _zahl_oder_none(ziel),
                 "art": b.STUFE_TOPF_ART[stufe], "notiz": notiz.strip() or None, "aktiv": aktiv}
        if not werte["name"]:
            st.error("Bitte einen Namen eingeben.")
        else:
            def _topf():
                tid = t_id
                if tid is None:
                    tid = daten.topf_anlegen({**werte, "konto_id": konto})
                else:
                    daten.aendern("toepfe", tid, werte)
                if standard:
                    daten.aendern("konten", konto if t_id is None else alt["konto_id"], {"standard_topf_id": tid})
            _schreiben(_topf, f"Topf „{werte['name']}“ gespeichert.")
    if t_id:
        with st.popover("🗑️ Topf löschen"):
            st.write("Geht nur ohne Buchungen. Sonst „Aktiv“ abwählen.")
            if st.button("Endgültig löschen", key="topf_loeschen"):
                belegt = (d["buchungen"]["topf_id"] == t_id).any()
                if belegt:
                    st.error("Auf diesem Topf liegen Buchungen. Bitte stattdessen deaktivieren.")
                else:
                    _schreiben(lambda: daten.loeschen("toepfe", [t_id]), "Topf gelöscht.")

# --- ZINSSAETZE --------------------------------------------------------------------
with tab_zins:
    st.caption(
        "Sätze mit Gültigkeitsdatum, in % pro Jahr vor Steuern. Bei Fonds dient der Satz nur zum Fortschreiben "
        "zwischen zwei eingetragenen Werten; maßgeblich ist der Wert laut App. Für Depots trägt die Seite "
        "Depot-Rendite den gemessenen Wert automatisch ein."
    )
    ertrag_konten = konten[konten["ertragsart"] != "keine"]
    if ertrag_konten.empty:
        st.info("Kein Konto mit Ertrag vorhanden.")
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
            auswahl = st.dataframe(pd.DataFrame({
                "Konto": z["konto_id"].map(konto_name),
                "Gültig ab": z["gueltig_ab"].dt.strftime("%d.%m.%Y"),
                "Satz % p. a.": z["satz_pa"],
                "Notiz": z["notiz"].fillna(""),
            }), hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row", key="zins_tabelle",
                column_config={"Satz % p. a.": st.column_config.NumberColumn(format="%.2f %%")})
            zeilen = auswahl.selection.rows if auswahl is not None else []
            if zeilen and st.button(f"🗑️ {len(zeilen)} Satz/Sätze löschen"):
                _schreiben(lambda: daten.loeschen("zinssaetze", list(z.loc[zeilen, "id"])), "Gelöscht.")

# --- VERBINDLICHKEITEN -------------------------------------------------------------
with tab_verb:
    st.caption(
        "Echte Schulden (z. B. Geldbuße in Raten). Nur sie werden vom Nettovermögen abgezogen. "
        "Tilgungen buchst du als Ausgabe mit Verweis auf die Verbindlichkeit."
    )
    vb = d["verbindlichkeiten"]
    v_opt = [None] + list(vb["id"])
    v_namen = dict(zip(vb["id"], vb["name"] + vb["erledigt"].map({True: " (erledigt)", False: ""})))
    v_id = st.selectbox("Verbindlichkeit", v_opt, key="verb_auswahl",
                        format_func=lambda i: "➕ Neu anlegen" if i is None else v_namen.get(i, i))
    alt = vb.set_index("id").loc[v_id] if v_id else None
    with st.form(f"verb_form_{v_id}"):
        name = st.text_input("Name", value="" if alt is None else alt["name"], max_chars=60)
        c1, c2 = st.columns(2)
        gesamt = c1.number_input("Gesamtbetrag (€)", min_value=0.0, step=10.0, format="%.2f",
                                 value=float(alt["betrag_gesamt"]) if alt is not None else 0.0)
        rate = c2.number_input("Monatsrate (€, optional)", min_value=0.0, step=10.0, format="%.2f",
                               value=float(alt["rate_monat"]) if alt is not None and pd.notna(alt["rate_monat"]) else 0.0)
        notiz = st.text_input("Notiz", value="" if alt is None else _text(alt["notiz"]))
        erledigt = st.checkbox("Erledigt", value=False if alt is None else bool(alt["erledigt"]))
        ok = st.form_submit_button("Speichern", type="primary", width="stretch")
    if ok:
        werte = {"name": name.strip(), "betrag_gesamt": round(gesamt, 2), "rate_monat": _zahl_oder_none(rate),
                 "notiz": notiz.strip() or None, "erledigt": erledigt}
        if not werte["name"] or gesamt <= 0:
            st.error("Bitte Name und einen Gesamtbetrag größer 0 eingeben.")
        elif v_id is None:
            _schreiben(lambda: daten.einfuegen("verbindlichkeiten", werte), "Verbindlichkeit angelegt.")
        else:
            _schreiben(lambda: daten.aendern("verbindlichkeiten", v_id, werte), "Verbindlichkeit gespeichert.")
    if v_id:
        with st.popover("🗑️ Verbindlichkeit löschen"):
            st.write("Tilgungsbuchungen bleiben erhalten, verlieren aber den Verweis.")
            if st.button("Endgültig löschen", key="verb_loeschen"):
                _schreiben(lambda: daten.loeschen("verbindlichkeiten", [v_id]), "Verbindlichkeit gelöscht.")
    offen = b.verbindlichkeiten_offen(vb, d["buchungen"])
    if not offen.empty:
        st.dataframe(pd.DataFrame({
            "Name": offen["name"], "Gesamt": offen["betrag_gesamt"], "Getilgt": offen["getilgt"],
            "Offen": offen["offen"], "Erledigt": offen["erledigt"],
        }), hide_index=True, width="stretch",
            column_config={c: st.column_config.NumberColumn(format="%.2f €") for c in ("Gesamt", "Getilgt", "Offen")})

# --- VERKNUEPFUNGEN -----------------------------------------------------------------
with tab_verkn:
    einst = daten.einstellungen()
    t_opt = [None] + list(toepfe.loc[toepfe["aktiv"] & toepfe["konto_id"].notna(), "id"])
    for schluessel in ("tank_topf_id", "lohn_topf_id"):
        if einst.get(schluessel) and einst[schluessel] not in t_opt:
            t_opt.append(einst[schluessel])
    st.caption(
        "**Tankungen:** Jede Tankung aus FuelBudget wird automatisch als Ausgabe auf diesem Topf gebucht "
        "(Ändern/Löschen in FuelBudget zieht mit). **Lohn:** Hier landet der Lohn, und von hier verteilt der "
        "Lohn-Assistent auf der Seite Fixkosten."
    )
    with st.form("verkn_form"):
        tank = st.selectbox("Tankungen buchen auf", t_opt,
                            index=t_opt.index(einst.get("tank_topf_id")) if einst.get("tank_topf_id") in t_opt else 0,
                            format_func=lambda k: "– aus –" if k is None else topf_label(k))
        lohn = st.selectbox("Lohn geht ein auf", t_opt,
                            index=t_opt.index(einst.get("lohn_topf_id")) if einst.get("lohn_topf_id") in t_opt else 0,
                            format_func=lambda k: "– nicht festgelegt –" if k is None else topf_label(k))
        ok = st.form_submit_button("Speichern", type="primary", width="stretch")
    if ok:
        _schreiben(lambda: daten.einstellungen_setzen({"tank_topf_id": tank, "lohn_topf_id": lohn}),
                   "Gespeichert. Offene Tankungen ab dem Startbestand wurden nachgebucht." if tank else "Gespeichert.")
    verknuepft = d["buchungen"][d["buchungen"]["tank_id"].notna()]
    if einst.get("tank_topf_id"):
        st.caption(f"Bisher {len(verknuepft)} Tankung(en) übernommen, zusammen {eur(-float(verknuepft['betrag'].sum()))}.")
