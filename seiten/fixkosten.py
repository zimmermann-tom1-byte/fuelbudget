"""Fixkosten: Lohn-Assistent, Positionen nach Konto, Faelligkeiten, Pflege.

Arten:
- direkt:   Monatlich einzahlen, wird abgebucht (Volksbank, Ausgabentopf, Bargeld)
- ansparen: Rate pro Monat im Geldmarktfonds, Auszahlung bei Faelligkeit
- budget:   Monatsbetrag, der nach und nach verbraucht wird (Kraftstoff, Tankungen
            kommen automatisch aus FuelBudget)
"""

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

d = daten.alles_aktuell(heute)
konten, toepfe, fix = d["konten"], d["toepfe"], d["fixkosten"]
konto_name = dict(zip(konten["id"], konten["name"]))
topf_name = dict(zip(toepfe["id"], toepfe["name"]))
topf_konto = dict(zip(toepfe["id"], toepfe["konto_id"]))
bewertung = dict(zip(konten["id"], konten["bewertung"]))


def topf_label(tid):
    return f"{konto_name.get(topf_konto.get(tid), '?')} › {topf_name.get(tid, '?')}"


def _schreiben(aktion, meldung):
    try:
        aktion()
    except Exception as exc:
        st.error(daten.fehlertext(exc))
        return
    daten.nach_schreibzugriff(meldung)


u = b.ueberblick(konten, toepfe, d["buchungen"], d["zinssaetze"], d["verbindlichkeiten"], heute, d["kurse"])
ts = u.toepfe
werte = ts.set_index("topf_key")["wert"].to_dict()
einstand = ts.set_index("topf_key")["einstand"].to_dict()
status = b.fixkosten_status(fix, ts, heute)
aktiv = status
einst = daten.einstellungen()
prognose = daten.kraftstoff_prognose(heute) if not fix.empty and fix["prognose_fuelbudget"].any() else None

st.title("Fixkosten 📆")

# --- LOHN-ASSISTENT ---------------------------------------------------------------------
s = st.session_state
kopf_l, kopf_r = st.columns([3, 1], vertical_alignment="center")
zuletzt = einst.get("lohn_assistent_seit")
kopf_l.caption("Sobald dein Lohn da ist: Der Assistent rechnet aus, was in welchen Topf muss, und bucht alles "
               "mit einem Klick." + (f" Zuletzt genutzt am {pd.Timestamp(zuletzt):%d.%m.%Y}." if zuletzt else ""))
if kopf_r.button("💶 Lohn ist da", type="primary", width="stretch", key="lohn_button"):
    s["lohn_assistent"] = True

if s.get("lohn_assistent") and not aktiv.empty:
    with st.container(border=True):
        st.subheader("💶 Lohn-Assistent")
        lohn_topf = einst.get("lohn_topf_id")
        t_opt = list(toepfe.loc[toepfe["aktiv"] & toepfe["konto_id"].notna(), "id"])
        c1, c2, c3 = st.columns([2, 1, 1])
        quelle = c1.selectbox("Lohn geht ein auf", t_opt, format_func=topf_label,
                              index=t_opt.index(lohn_topf) if lohn_topf in t_opt else 0, key="lohn_quelle")
        lohn = c2.number_input("Lohn netto (€)", min_value=0.0, step=10.0, format="%.2f", value=None,
                               key="lohn_betrag", help="Leer lassen, wenn der Lohn schon gebucht ist.")
        datum = c3.date_input("Datum", value=heute.date(), format="DD.MM.YYYY", key="lohn_datum")
        if prognose:
            st.caption(f"⛽ Kraftstoff laut FuelBudget-Prognose: **{eur(prognose['monat'])} / Monat** "
                       f"({prognose['label']}).")

        zeilen = []
        for _, f in aktiv.iterrows():
            if not isinstance(f["topf_id"], str):
                continue
            vorschlag = b.einzahlung_vorschlag(f, einstand.get(f["topf_id"], 0.0), pd.Timestamp(datum),
                                               prognose["monat"] if prognose else None)
            hinweis = ""
            if f["modus"] == "ansparen":
                hinweis = f"{f['raten']:.1f} von {f['intervall_monate']} Raten".replace(".", ",")
                if vorschlag == 0:
                    hinweis += " · voll, wartet auf Rechnung"
                elif f["rest_regel"] == "verrechnen" and abs(vorschlag - f["pro_monat"]) >= 0.005:
                    hinweis += f" · {eur(f['pro_monat'] - vorschlag)} verrechnet"
            elif f["modus"] == "budget":
                hinweis = f"noch {eur(einstand.get(f['topf_id'], 0.0))} im Topf"
            zeilen.append({"id": f["id"], "Position": f["name"], "Konto": konto_name.get(f["konto_id"], "?"),
                           "Vorschlag": vorschlag, "Betrag (€)": vorschlag, "Hinweis": hinweis,
                           "topf_id": f["topf_id"], "konto_id": f["konto_id"]})
        plan = pd.DataFrame(zeilen)
        if plan.empty:
            st.info("Keiner Position ist ein Topf zugeordnet.")
        else:
            bearbeitet = st.data_editor(
                plan, hide_index=True, width="stretch", key="lohn_plan",
                disabled=["Position", "Konto", "Vorschlag", "Hinweis"],
                column_order=["Position", "Konto", "Vorschlag", "Betrag (€)", "Hinweis"],
                column_config={"Vorschlag": st.column_config.NumberColumn(format="%.2f €"),
                               "Betrag (€)": st.column_config.NumberColumn(format="%.2f", step=0.01, min_value=0.0)},
            )
            bearbeitet["Betrag (€)"] = pd.to_numeric(bearbeitet["Betrag (€)"], errors="coerce").fillna(0.0)
            je_konto = bearbeitet.groupby("konto_id")["Betrag (€)"].sum()
            je_konto = je_konto[je_konto > 0.005]
            gesamt = float(je_konto.sum())
            st.markdown("**Das musst du jetzt verteilen:**")
            for kid, summe in je_konto.items():
                art = konten.set_index("id").at[kid, "art"]
                verb = "abheben" if art == "bargeld" else ("in den Fonds einzahlen" if bewertung.get(kid) == "anteile"
                                                         else "überweisen")
                st.markdown(f"- **{konto_name[kid]}**: {eur(summe)} {verb}")
            verfuegbar = (lohn or 0.0) + werte.get(quelle, 0.0)
            rest = verfuegbar - gesamt
            st.caption(f"Zusammen {eur(gesamt)}. Auf {topf_label(quelle)} bleiben danach {eur(rest)}"
                       + (" – das reicht nicht!" if rest < -0.005 else "."))
            b1, b2 = st.columns(2)
            if b1.button("✅ Lohn & Einzahlungen buchen", type="primary", width="stretch", key="lohn_buchen"):
                def _buchen():
                    tag = str(pd.Timestamp(datum).date())
                    neu = []
                    if lohn:
                        neu.append({"datum": tag, "konto_id": topf_konto[quelle], "topf_id": quelle,
                                    "betrag": round(lohn, 2), "typ": "einnahme", "kategorie": "Gehalt",
                                    "beschreibung": "Lohn"})
                    for _, r in bearbeitet.iterrows():
                        if r["Betrag (€)"] >= 0.005 and r["topf_id"] != quelle:
                            neu.extend(b.umbuchung(tag, topf_konto[quelle], quelle, r["konto_id"], r["topf_id"],
                                                   float(r["Betrag (€)"]), f"Monatseinzahlung {r['Position']}"))
                    daten.konto_buchungen_speichern(neu, d["kurse"], d["zinssaetze"], konten)
                    if prognose:
                        for _, f in aktiv[aktiv["prognose_fuelbudget"]].iterrows():
                            monat = round(prognose["monat"], 2)
                            daten.aendern("fixkosten", f["id"], {"betrag": monat, "rate_monat": monat})
                    daten.einstellungen_setzen({"lohn_assistent_seit": tag, "lohn_topf_id": quelle})
                    s.pop("lohn_assistent", None)
                _schreiben(_buchen, f"Gebucht: {eur(gesamt)} verteilt" + (f", Lohn {eur(lohn)}" if lohn else "") + ".")
            if b2.button("Abbrechen", width="stretch", key="lohn_abbrechen"):
                s.pop("lohn_assistent", None)
                st.rerun()

if fix.empty:
    st.info("Noch keine Fixkosten erfasst. Lege unten die erste an.")

# --- UEBERBLICK -------------------------------------------------------------------------
if not aktiv.empty:
    pro_monat = float(aktiv["pro_monat"].sum())
    k1, k2, k3 = st.columns(3)
    k1.metric("Fixkosten pro Monat", eur(pro_monat),
              help="Alles auf einen Monat umgerechnet (Raten), z. B. KFZ 304 € vierteljährlich = 102 € Rate.")
    for spalte, modus, titel in ((k2, "ansparen", "davon angespart"), (k3, "budget", "davon Budget")):
        spalte.metric(titel, eur(float(aktiv.loc[aktiv["modus"] == modus, "pro_monat"].sum())))
    if prognose:
        kraft = aktiv[aktiv["prognose_fuelbudget"]]
        if not kraft.empty and abs(float(kraft["pro_monat"].sum()) - prognose["monat"]) >= 1:
            st.caption(f"⛽ FuelBudget erwartet aktuell {eur(prognose['monat'])} / Monat für Kraftstoff, eingeplant sind "
                       f"{eur(float(kraft['pro_monat'].sum()))}. Der Lohn-Assistent übernimmt die Prognose.")

    warnungen = aktiv[aktiv["fehlt_bei_faelligkeit"] > 0.005]
    for _, f in warnungen.iterrows():
        st.warning(
            f"**{f['name']}**: Fällig am {f['naechste_faelligkeit']:%d.%m.%Y} sind {eur(f['betrag'])} nötig. "
            f"Mit {f['raten']:.0f} von {f['intervall_monate']} Raten und {f['einzahlungen_bis_faellig']} weiteren "
            f"Einzahlung(en) bis dahin fehlen voraussichtlich **{eur(f['fehlt_bei_faelligkeit'])}**. "
            "Stimmt die Fälligkeit? Sonst kommt der Rest aus dem Rest-Topf."
        )

    st.markdown("### Nach Konto")
    for kid, gruppe in aktiv.groupby("konto_id", sort=False):
        kw = float(ts.loc[ts["konto_id"] == kid, "wert"].sum())
        with st.expander(f"**{konto_name.get(kid, 'ohne Konto')}** — {eur(float(gruppe['pro_monat'].sum()))} / Monat "
                         f"· Bestand {eur(kw)}", expanded=False):
            for _, f in gruppe.iterrows():
                w = werte.get(f["topf_id"], 0.0)
                if f["modus"] == "ansparen":
                    info = (f"{f['raten']:.1f} von {f['intervall_monate']} Raten".replace(".", ",")
                            + f" · {eur(w)} von {eur(f['betrag'])} · fällig {f['naechste_faelligkeit']:%d.%m.%Y}")
                    st.progress(max(min(w / f["betrag"], 1.0), 0.0) if f["betrag"] else 0.0,
                                text=f"**{f['name']}** — {eur(f['pro_monat'])} / Monat")
                    st.caption(info)
                elif f["modus"] == "budget":
                    st.progress(max(min(w / f["pro_monat"], 1.0), 0.0) if f["pro_monat"] else 0.0,
                                text=f"**{f['name']}** — noch {eur(w)} von {eur(f['pro_monat'])}")
                    st.caption("Tankungen aus FuelBudget werden automatisch abgezogen." if f["prognose_fuelbudget"] else "")
                else:
                    auto = "automatisch" if f["auto_abbuchen"] else "per Klick"
                    st.markdown(f"**{f['name']}** — {eur(f['betrag'])} · nächste Abbuchung "
                                f"{f['naechste_faelligkeit']:%d.%m.%Y} ({auto}) · im Topf {eur(w)}")
            rest_toepfe = {t for t in gruppe["rest_topf_id"].dropna()}
            for rt in rest_toepfe:
                st.caption(f"↳ Rest-Topf **{topf_name.get(rt, '?')}**: {eur(werte.get(rt, 0.0))}")

    # --- FAELLIGKEITEN ------------------------------------------------------------------
    st.markdown("### Nächste Fälligkeiten (60 Tage)")
    bald = aktiv[(aktiv["modus"] != "budget") & (aktiv["naechste_faelligkeit"] <= heute + pd.Timedelta(days=60))]
    if bald.empty:
        st.caption("Nichts fällig in den nächsten 60 Tagen.")
    for _, f in bald.sort_values("naechste_faelligkeit").iterrows():
        tage = (f["naechste_faelligkeit"] - heute).days
        wann = "überfällig" if tage < 0 else ("heute" if tage == 0 else f"in {tage} Tagen")
        c1, c2 = st.columns([3, 1], vertical_alignment="center")
        c1.markdown(f"**{f['name']}** — {eur(f['betrag'])} · {f['naechste_faelligkeit']:%d.%m.%Y} ({wann}) · "
                    f"{topf_label(f['topf_id']) if isinstance(f['topf_id'], str) else 'kein Topf'}")
        if f["auto_abbuchen"]:
            c2.caption("🔁 wird automatisch gebucht")
            continue
        with c2.popover("✅ Bezahlt", width="stretch"):
            with st.form(f"bezahlt_{f['id']}"):
                ist = st.number_input("Tatsächlicher Betrag (€)", min_value=0.0, value=float(f["betrag"]),
                                      step=0.01, format="%.2f", key=f"ist_{f['id']}")
                bdat = st.date_input("Datum", value=heute.date(), format="DD.MM.YYYY", key=f"bdat_{f['id']}")
                ok = st.form_submit_button("Buchen", type="primary", width="stretch")
            if ok:
                if not isinstance(f["topf_id"], str):
                    st.error("Bitte der Position zuerst einen Topf zuordnen.")
                else:
                    kurs, _ = b.kurs_geschaetzt(d["kurse"], d["zinssaetze"], f["konto_id"], bdat)
                    neu_dat = b.monate_weiter(f["naechste_faelligkeit"], f["intervall_monate"])

                    def _bezahlt(f=f, ist=ist, bdat=bdat, kurs=kurs, neu_dat=neu_dat):
                        zeilen = b.zahlung_buchen(f, ist, bdat, werte.get(f["topf_id"], 0.0),
                                                  bewertung.get(f["konto_id"], "saldo"), kurs)
                        daten.einfuegen("buchungen", zeilen)
                        daten.aendern("fixkosten", f["id"], {"naechste_faelligkeit": str(neu_dat.date())})
                    _schreiben(_bezahlt, f"{f['name']} gebucht, nächste Fälligkeit {neu_dat:%d.%m.%Y}.")

    with st.expander("So rechnet das Tool"):
        st.markdown(
            "- **Direkt** (Volksbank, Ausgabentopf, Bargeld): Der Lohn-Assistent zahlt ein, was bis Ende des nächsten "
            "Monats abgebucht wird, abzüglich dessen, was noch im Topf liegt. Lastschriften mit „automatisch“ bucht "
            "das Tool am Fälligkeitstag selbst. Zahlst du weniger (z. B. ZZV unter 5 €), geht der Rest in die "
            "Sicherheitsmarge.\n"
            "- **Ansparen** (Geldmarktfonds): jeden Monat die Rate. Bei „Rest-Topf“ (KFZ, Linsen, Zahnarzt, GoodNotes) "
            "wandern Aufrundung und Zinsen nach der Zahlung in „Überschuss & Zinsen“, und ein Fehlbetrag kommt von "
            "dort. Bei „verrechnen“ (ESN) bleibt die Differenz im Topf, und die nächste Rate wird entsprechend kleiner.\n"
            "- **Budget** (Kraftstoff): Der Assistent füllt auf die FuelBudget-Prognose auf, Tankungen ziehen automatisch ab.\n"
            "- **Warnung:** Reichen angesparte Raten plus die noch möglichen Einzahlungen bis zur Fälligkeit nicht, "
            "zeigt das Tool den Fehlbetrag an."
        )

# --- PFLEGE ------------------------------------------------------------------------------
st.markdown("### Position anlegen oder bearbeiten")
f_opt = [None] + list(fix["id"])
f_namen = dict(zip(fix["id"], fix["name"] + fix["aktiv"].map({True: "", False: " (inaktiv)"}))) if not fix.empty else {}
f_id = st.selectbox("Position", f_opt, key="fix_auswahl",
                    format_func=lambda i: "➕ Neu anlegen" if i is None else f_namen.get(i, i))
alt = fix.set_index("id").loc[f_id] if f_id else None

t_opt = list(toepfe.loc[toepfe["aktiv"] & toepfe["konto_id"].notna(), "id"])
if alt is not None and isinstance(alt["topf_id"], str) and alt["topf_id"] not in t_opt:
    t_opt.append(alt["topf_id"])
NEUER_TOPF = "__neu__"
k_opt = list(konten.loc[konten["aktiv"], "id"])
modi, regeln, intervalle, kategorien = list(b.FIXKOSTEN_MODI), list(b.REST_REGELN), list(b.INTERVALLE), b.AUSGABE_KATEGORIEN

with st.form(f"fix_form_{f_id}"):
    name = st.text_input("Name", value="" if alt is None else alt["name"], max_chars=60, placeholder="z. B. Kfz-Versicherung")
    modus = st.selectbox("Art", modi, format_func=b.FIXKOSTEN_MODI.get,
                         index=modi.index(alt["modus"]) if alt is not None else 0)
    c1, c2, c3 = st.columns(3)
    betrag = c1.number_input("Rechnungsbetrag (€)", min_value=0.0, step=1.0, format="%.2f",
                             value=float(alt["betrag"]) if alt is not None else 0.0)
    intervall = c2.selectbox("Intervall", intervalle, format_func=b.INTERVALLE.get,
                             index=intervalle.index(int(alt["intervall_monate"])) if alt is not None else 0)
    rate = c3.number_input("Rate pro Monat (€, leer = Betrag ÷ Intervall)", min_value=0.0, step=1.0, format="%.2f",
                           value=float(alt["rate_monat"]) if alt is not None and pd.notna(alt["rate_monat"]) else None,
                           help="z. B. 102 € statt 101,33 € (aufgerundet)")
    c4, c5 = st.columns(2)
    faellig = c4.date_input("Nächste Fälligkeit", format="DD.MM.YYYY",
                            value=alt["naechste_faelligkeit"].date() if alt is not None else heute.date())
    kat_alt = alt["kategorie"] if alt is not None and pd.notna(alt["kategorie"]) else "Abos & Verträge"
    kategorie = c5.selectbox("Kategorie", kategorien, index=kategorien.index(kat_alt) if kat_alt in kategorien else 0)
    topf_opt = t_opt + [NEUER_TOPF]
    topf = st.selectbox("Topf", topf_opt,
                        format_func=lambda t: "➕ Neuer Topf (Name der Position) im Konto unten" if t == NEUER_TOPF else topf_label(t),
                        index=topf_opt.index(alt["topf_id"]) if alt is not None and alt["topf_id"] in topf_opt
                        else len(topf_opt) - 1)
    neues_konto = st.selectbox("Konto für den neuen Topf", k_opt, format_func=konto_name.get,
                               help="Nur relevant bei „Neuer Topf“.")
    c6, c7 = st.columns(2)
    regel = c6.selectbox("Überschuss / Fehlbetrag", regeln, format_func=b.REST_REGELN.get,
                         index=regeln.index(alt["rest_regel"]) if alt is not None else 0)
    rest_opt = [None] + t_opt
    rest_topf = c7.selectbox("Rest-Topf", rest_opt,
                             format_func=lambda t: "– keiner –" if t is None else topf_label(t),
                             index=rest_opt.index(alt["rest_topf_id"]) if alt is not None and alt["rest_topf_id"] in rest_opt else 0,
                             help="z. B. Sicherheitsmarge (Volksbank) oder Überschuss & Zinsen (Geldmarktfonds)")
    c8, c9 = st.columns(2)
    auto = c8.checkbox("Lastschrift automatisch buchen", value=False if alt is None else bool(alt["auto_abbuchen"]),
                       help="Nur bei Art „Direkt“ und festem Betrag. Das Tool bucht am Fälligkeitstag selbst.")
    prog = c9.checkbox("Betrag aus FuelBudget-Prognose", value=False if alt is None else bool(alt["prognose_fuelbudget"]),
                       help="Nur für Kraftstoff (Art „Budget“).")
    notiz = st.text_input("Notiz", value="" if alt is None or pd.isna(alt["notiz"]) else alt["notiz"])
    aktiv_cb = st.checkbox("Aktiv", value=True if alt is None else bool(alt["aktiv"]))
    ok = st.form_submit_button("Speichern", type="primary", width="stretch")
if ok:
    if not name.strip() or betrag <= 0:
        st.error("Bitte Name und einen Betrag größer 0 eingeben.")
    elif rest_topf is not None and topf != NEUER_TOPF and topf_konto.get(rest_topf) != topf_konto.get(topf):
        st.error("Der Rest-Topf muss im selben Konto liegen wie der Topf der Position.")
    else:
        def _speichern():
            tid = topf
            if tid == NEUER_TOPF:
                tid = daten.topf_anlegen({"name": name.strip(), "stufe": "fixkosten", "konto_id": neues_konto})
            werte_fix = {"name": name.strip(), "betrag": round(betrag, 2), "intervall_monate": int(intervall),
                         "rate_monat": round(rate, 2) if rate else None, "naechste_faelligkeit": str(faellig),
                         "topf_id": tid, "konto_id": topf_konto.get(tid, neues_konto), "modus": modus,
                         "rest_regel": regel, "rest_topf_id": rest_topf, "auto_abbuchen": bool(auto and modus == "direkt"),
                         "prognose_fuelbudget": bool(prog), "kategorie": kategorie,
                         "notiz": notiz.strip() or None, "aktiv": aktiv_cb}
            if f_id is None:
                daten.einfuegen("fixkosten", werte_fix)
            else:
                daten.aendern("fixkosten", f_id, werte_fix)
        _schreiben(_speichern, f"„{name.strip()}“ gespeichert.")
if f_id:
    with st.popover("🗑️ Position löschen"):
        st.write("Bereits gebuchte Zahlungen und der Topf bleiben erhalten.")
        if st.button("Endgültig löschen", key="fix_loeschen"):
            _schreiben(lambda: daten.loeschen("fixkosten", [f_id]), "Position gelöscht.")

if not fix.empty:
    alle = b.fixkosten_status(fix.assign(aktiv=True), ts, heute)
    st.dataframe(pd.DataFrame({
        "Name": alle["name"],
        "Art": alle["modus"].map({"direkt": "Direkt", "ansparen": "Ansparen", "budget": "Budget"}),
        "Betrag": alle["betrag"],
        "Intervall": alle["intervall_monate"].map(b.INTERVALLE),
        "Rate / Monat": alle["pro_monat"],
        "Nächste Fälligkeit": alle["naechste_faelligkeit"].dt.strftime("%d.%m.%Y"),
        "Topf": [topf_label(t) if isinstance(t, str) else "–" for t in alle["topf_id"]],
        "Im Topf": alle["wert"],
        "Auto": alle["auto_abbuchen"],
        "Aktiv": fix.set_index("id").loc[alle["id"], "aktiv"].values,
    }), hide_index=True, width="stretch", column_config={
        "Betrag": st.column_config.NumberColumn(format="%.2f €"),
        "Rate / Monat": st.column_config.NumberColumn(format="%.2f €"),
        "Im Topf": st.column_config.NumberColumn(format="%.2f €"),
    })
