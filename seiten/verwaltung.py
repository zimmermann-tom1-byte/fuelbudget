"""Stammdaten: Posten, Startbestand, Zinssaetze und Verbindlichkeiten."""

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
posten, toepfe = d["konten"], d["toepfe"]
posten_name = dict(zip(posten["id"], posten["name"]))
topf_ziel = dict(zip(toepfe["id"], toepfe["zielbetrag"]))

st.title("Posten 🗂️")


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


def _text(x):
    return "" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)


tab_start, tab_posten, tab_zins, tab_verb = st.tabs(
    ["🚀 Start", "📦 Posten", "📈 Zinssätze & Renditen", "📉 Verbindlichkeiten"]
)

# --- START ------------------------------------------------------------------------
with tab_start:
    schritte = [
        ("Posten anlegen", not posten.empty,
         "Je Bereich ein Posten: wo liegt das Geld (Anbieter, Produkt) und wie verfügbar ist es (Stufe)."),
        ("Zinssätze eintragen", not d["zinssaetze"].empty,
         "Für Zinsposten den aktuellen Satz, für Dividenden die erwartete Ausschüttung. "
         "Die Depot-Rendite kann die Seite **Depot-Rendite** selbst ermitteln."),
        ("Startbestand buchen", (d["buchungen"]["typ"] == "eroeffnung").any(),
         "Alle Stände am Stichtag in einer Tabelle eintragen (unten)."),
        ("Fixkosten erfassen", not d["fixkosten"].empty,
         "Auf der Seite **Fixkosten**: Name, Betrag, Intervall, nächste Fälligkeit."),
    ]
    st.markdown("**Einrichtung in vier Schritten**")
    for nr, (titel, fertig, text) in enumerate(schritte, 1):
        st.markdown(f"{'✅' if fertig else '⬜'} **{nr}. {titel}** – {text}")

    st.markdown("---")
    st.markdown("### Startbestand aller Posten")
    aktive = posten[posten["aktiv"]]
    if aktive.empty:
        st.info("Lege zuerst Posten an.")
    else:
        bu = d["buchungen"]
        eroeffnung = bu[bu["typ"] == "eroeffnung"]
        vorbelegung = eroeffnung.groupby("konto_id")["betrag"].sum()
        stichtag_default = eroeffnung["datum"].min().date() if not eroeffnung.empty else date(2026, 11, 1)
        editor_df = pd.DataFrame({
            "id": aktive["id"],
            "Posten": aktive["name"],
            "Stufe": aktive["stufe"].map(b.STUFEN),
            "Betrag (€)": aktive["id"].map(vorbelegung).fillna(0.0).astype(float),
        })
        st.caption("Nur geänderte Zeilen werden neu gebucht. Ein vorhandener Startbestand wird dabei ersetzt.")
        with st.form("startbestand_form"):
            stichtag = st.date_input("Stichtag", value=stichtag_default, format="DD.MM.YYYY")
            bearbeitet = st.data_editor(
                editor_df, hide_index=True, width="stretch", disabled=["Posten", "Stufe"],
                column_order=["Posten", "Stufe", "Betrag (€)"],
                column_config={"Betrag (€)": st.column_config.NumberColumn(format="%.2f", step=0.01)},
                key="start_editor",
            )
            ok = st.form_submit_button("Startbestand speichern", type="primary", width="stretch")
        summe = float(pd.to_numeric(bearbeitet["Betrag (€)"], errors="coerce").fillna(0).sum())
        st.caption(f"Summe: {eur(summe)}")
        if ok:
            alt_datum = eroeffnung.groupby("konto_id")["datum"].min()
            geaendert = []
            for _, r in bearbeitet.iterrows():
                neu = round(float(r["Betrag (€)"] or 0), 2)
                alt = round(float(vorbelegung.get(r["id"], 0.0)), 2)
                datum_alt = alt_datum.get(r["id"])
                if neu != alt or (datum_alt is not None and datum_alt.date() != stichtag and neu):
                    geaendert.append((r["id"], neu))
            if not geaendert:
                st.info("Keine Änderung.")
            else:
                topf_von = posten.set_index("id")["standard_topf_id"]

                def _start():
                    ids = [i for i, _ in geaendert]
                    alte = eroeffnung[eroeffnung["konto_id"].isin(ids)]
                    if not alte.empty:
                        daten.loeschen("buchungen", list(alte["id"]))
                    zeilen = [{"datum": str(stichtag), "konto_id": kid,
                               "topf_id": topf_von.get(kid) if pd.notna(topf_von.get(kid)) else None,
                               "betrag": betrag, "typ": "eroeffnung", "beschreibung": "Startbestand"}
                              for kid, betrag in geaendert if abs(betrag) >= 0.005]
                    if zeilen:
                        daten.einfuegen("buchungen", zeilen)
                _schreiben(_start, f"Startbestand für {len(geaendert)} Posten gespeichert.")

# --- POSTEN ------------------------------------------------------------------------
with tab_posten:
    st.caption(
        "Ein Posten ist ein Bereich deines Geldes, z. B. „Autorücklagen bei BBVA“. Die **Stufe** sagt, "
        "wie verfügbar das Geld ist. Posten, die im selben Produkt liegen (z. B. zwei Rücklagen im "
        "selben Geldmarktfonds), bekommen dieselbe **Abgleichsgruppe**: Beim Kontoabgleich trägst du "
        "dann nur den Gesamtstand ein."
    )
    opt = [None] + list(posten["id"])
    namen = dict(zip(posten["id"], posten["name"] + posten["aktiv"].map({True: "", False: " (inaktiv)"})))
    p_id = st.selectbox("Posten", opt, key="posten_auswahl",
                        format_func=lambda i: "➕ Neu anlegen" if i is None else namen.get(i, i))
    alt = posten.set_index("id").loc[p_id] if p_id else None
    alt_topf = alt["standard_topf_id"] if alt is not None and pd.notna(alt["standard_topf_id"]) else None
    k_arten, e_arten, stufen = list(b.KONTO_ARTEN), list(b.ERTRAGSARTEN), list(b.STUFEN)
    gruppen = sorted(g for g in posten["abgleich_gruppe"].dropna().unique())
    anbieter = sorted(a for a in posten["institut"].dropna().unique())

    with st.form(f"posten_form_{p_id}"):
        c1, c2 = st.columns(2)
        name = c1.text_input("Name", value="" if alt is None else alt["name"], max_chars=60,
                             placeholder="z. B. Autorücklagen")
        institut = c2.text_input("Anbieter", value="" if alt is None else _text(alt["institut"]),
                                 placeholder="z. B. Trading 212",
                                 help=("Bisher verwendet: " + ", ".join(anbieter)) if anbieter else None)
        stufe = st.selectbox("Stufe", stufen, format_func=lambda s: f"{b.STUFEN[s]} – {b.STUFE_ERKLAERUNG[s]}",
                             index=stufen.index(alt["stufe"]) if alt is not None else 0)
        c3, c4 = st.columns(2)
        art = c3.selectbox("Produkt", k_arten, format_func=b.KONTO_ARTEN.get,
                           index=k_arten.index(alt["art"]) if alt is not None else 1)
        ertragsart = c4.selectbox("Ertragsart", e_arten, format_func=b.ERTRAGSARTEN.get,
                                  index=e_arten.index(alt["ertragsart"]) if alt is not None else 0,
                                  help="Zinsen und Ausschüttungen werden täglich geschätzt. "
                                       "Kursentwicklung kommt aus der Seite Depot-Rendite.")
        c5, c6 = st.columns(2)
        ziel_alt = topf_ziel.get(alt_topf) if alt_topf else None
        ziel = c5.number_input("Zielbetrag (€, optional)", min_value=0.0, step=50.0, format="%.2f",
                               value=float(ziel_alt) if ziel_alt is not None and pd.notna(ziel_alt) else 0.0)
        gruppe = c6.text_input("Abgleichsgruppe (optional)",
                               value="" if alt is None else _text(alt["abgleich_gruppe"]),
                               placeholder="z. B. T212 Geldmarktfonds Rücklagen",
                               help=("Vorhandene Gruppen: " + ", ".join(gruppen)) if gruppen
                               else "Gleicher Text = gleiche Gruppe.")
        risiko = st.select_slider("Risiko", options=list(b.RISIKO_STUFEN), format_func=b.RISIKO_STUFEN.get,
                                  value=int(alt["risiko"]) if alt is not None else 1)
        notiz = st.text_input("Notiz", value="" if alt is None else _text(alt["notiz"]))
        aktiv = st.checkbox("Aktiv", value=True if alt is None else bool(alt["aktiv"]))
        ok = st.form_submit_button("Speichern", type="primary", width="stretch")
    if ok:
        werte = {"name": name.strip(), "institut": institut.strip() or None, "art": art,
                 "ertragsart": ertragsart, "risiko": int(risiko), "stufe": stufe,
                 "abgleich_gruppe": gruppe.strip() or None, "notiz": notiz.strip() or None, "aktiv": aktiv}
        if not werte["name"]:
            st.error("Bitte einen Namen eingeben.")
        elif p_id is None:
            werte["sortierung"] = len(posten)
            _schreiben(lambda: daten.posten_anlegen(werte, _zahl_oder_none(ziel)),
                       f"Posten „{werte['name']}“ angelegt. Als Nächstes: Zinssatz und Startbestand.")
        else:
            _schreiben(lambda: daten.posten_aendern(p_id, alt_topf, werte, _zahl_oder_none(ziel)),
                       f"Posten „{werte['name']}“ gespeichert.")
    if p_id:
        with st.popover("🗑️ Posten löschen"):
            st.write("Geht nur, solange keine Buchungen auf dem Posten liegen. Sonst „Aktiv“ abwählen.")
            if st.button("Endgültig löschen", key="posten_loeschen"):
                _schreiben(lambda: daten.posten_loeschen(p_id, alt_topf), "Posten gelöscht.")

    if not posten.empty:
        st.markdown("---")
        uebersicht = posten.copy()
        uebersicht["_o"] = uebersicht["stufe"].map({s: i for i, s in enumerate(b.STUFEN)})
        uebersicht = uebersicht.sort_values(["_o", "name"])
        st.dataframe(pd.DataFrame({
            "Posten": uebersicht["name"],
            "Stufe": uebersicht["stufe"].map(b.STUFEN),
            "Anbieter": uebersicht["institut"].fillna(""),
            "Produkt": uebersicht["art"].map(b.KONTO_ARTEN),
            "Abgleichsgruppe": uebersicht["abgleich_gruppe"].fillna(""),
            "Aktiv": uebersicht["aktiv"],
        }), hide_index=True, width="stretch")

# --- ZINSSAETZE --------------------------------------------------------------------
with tab_zins:
    st.caption(
        "Sätze ändern sich, deshalb mit Gültigkeitsdatum: Ein neuer Satz gilt ab seinem Datum, "
        "die Vergangenheit bleibt richtig berechnet. Werte in % pro Jahr, vor Steuern. "
        "Für Depots trägt die Seite Depot-Rendite den gemessenen Wert automatisch ein."
    )
    ertrag_posten = posten[posten["ertragsart"] != "keine"]
    if ertrag_posten.empty:
        st.info("Kein Posten mit Zinsen, Ausschüttungen oder Kursentwicklung vorhanden.")
    else:
        with st.form("zins_form", clear_on_submit=True):
            c1, c2, c3 = st.columns([2, 1, 1])
            z_posten = c1.selectbox("Posten", list(ertrag_posten["id"]), format_func=posten_name.get)
            z_ab = c2.date_input("Gültig ab", value=heute.date(), format="DD.MM.YYYY")
            z_satz = c3.number_input("Satz % p. a.", step=0.05, format="%.2f", value=0.0)
            z_notiz = st.text_input("Notiz (optional)", placeholder="z. B. Zinssenkung EZB")
            ok = st.form_submit_button("Satz speichern", type="primary", width="stretch")
        if ok:
            _schreiben(lambda: daten.einfuegen("zinssaetze", {
                "konto_id": z_posten, "gueltig_ab": str(z_ab), "satz_pa": round(z_satz, 4),
                "notiz": z_notiz.strip() or None}),
                f"{z_satz:.2f} % für {posten_name[z_posten]} ab {z_ab:%d.%m.%Y} gespeichert.")

        z = d["zinssaetze"]
        if not z.empty:
            z = z.sort_values(["konto_id", "gueltig_ab"], ascending=[True, False]).reset_index(drop=True)
            anzeige = pd.DataFrame({
                "Posten": z["konto_id"].map(posten_name),
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
        "Echte Schulden (z. B. Geldbuße in Raten). Nur sie werden vom Nettovermögen abgezogen. "
        "Regelmäßige Kosten gehören auf die Seite **Fixkosten**. Tilgungen buchst du als Ausgabe "
        "mit Verweis auf die Verbindlichkeit."
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
