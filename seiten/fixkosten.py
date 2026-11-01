"""Regelmaessige Fixkosten: Liste, Soll/Ist der Fixkosten-Posten, Faelligkeiten."""

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
posten, fixkosten = d["konten"], d["fixkosten"]
posten_name = dict(zip(posten["id"], posten["name"]))
topf_von = {kid: (t if pd.notna(t) else None) for kid, t in zip(posten["id"], posten["standard_topf_id"])}

st.title("Fixkosten 📆")
st.caption(
    "Alle regelmäßigen Kosten mit Intervall und nächster Fälligkeit. Daraus rechnet das Tool aus, "
    "wie viel heute in deinen Fixkosten-Posten liegen sollte, und warnt, wenn es zu wenig ist."
)


def _schreiben(aktion, meldung):
    try:
        aktion()
    except Exception as exc:
        st.error(daten.fehlertext(exc))
        return
    daten.nach_schreibzugriff(meldung)


soll = b.fixkosten_soll(fixkosten, heute)
u = b.ueberblick(posten, d["buchungen"], d["zinssaetze"], d["verbindlichkeiten"], heute)
salden = u.posten.set_index("id")["saldo"]

# --- KENNZAHLEN ------------------------------------------------------------------
if soll.empty:
    st.info("Noch keine Fixkosten erfasst. Lege unten die erste an.")
else:
    pro_monat = float(soll["pro_monat"].sum())
    monatlich = float(soll.loc[soll["intervall_monate"] == 1, "pro_monat"].sum())
    in_30 = soll[soll["naechste_faelligkeit"] <= heute + pd.Timedelta(days=30)]
    k1, k2, k3 = st.columns(3)
    k1.metric("Fixkosten pro Monat", eur(pro_monat),
              help="Alles auf einen Monat umgerechnet, z. B. 300 € vierteljährlich = 100 € / Monat.")
    k2.metric("davon monatlich fällig", eur(monatlich),
              delta=f"{eur(pro_monat - monatlich)} umgelegt", delta_color="off")
    k3.metric("Fällig in den nächsten 30 Tagen", eur(float(in_30["betrag"].sum())),
              delta=f"{len(in_30)} Position(en)", delta_color="off")

    # --- SOLL / IST JE POSTEN --------------------------------------------------------
    st.markdown("### Reicht das Geld in den Fixkosten-Posten?")
    je_posten = b.fixkosten_je_posten(fixkosten, salden, heute)
    if je_posten.empty:
        st.caption("Ordne deinen Fixkosten einen Posten zu, aus dem sie bezahlt werden.")
    for _, r in je_posten.iterrows():
        name = posten_name.get(r["konto_id"], "?")
        if r["differenz"] < -1:
            st.error(f"**{name}**: {eur(r['ist'])} vorhanden, {eur(r['soll'])} sollten es heute sein. "
                     f"Es fehlen **{eur(-r['differenz'])}**.")
        else:
            st.success(f"**{name}**: {eur(r['ist'])} vorhanden, Soll heute {eur(r['soll'])} "
                       f"→ {eur(r['differenz'])} Spielraum.")
        st.caption(f"Monatlich einzahlen: {eur(r['pro_monat'])} (Summe aller zugeordneten Fixkosten pro Monat)")
    ohne = soll[soll["konto_id"].isna()]
    if not ohne.empty:
        st.warning(f"{len(ohne)} Position(en) ohne Posten: {', '.join(ohne['name'])}. "
                   "Sie zählen nicht in den Soll/Ist-Vergleich.")
    with st.expander("So wird das Soll berechnet"):
        st.markdown(
            "- **Monatliche Kosten** (z. B. Gym): voller Betrag, solange sie in diesem Monat noch nicht "
            "bezahlt sind. Nach „Als bezahlt buchen“ springt die Fälligkeit in den nächsten Monat, das "
            "Soll wird 0.\n"
            "- **Alle n Monate** (z. B. Versicherung vierteljährlich): anteilig nach der Zeit seit der "
            "letzten Fälligkeit. Bei 300 € vierteljährlich sind nach anderthalb Monaten 150 € Soll, "
            "am Fälligkeitstag 300 €."
        )

    # --- FAELLIGKEITEN ----------------------------------------------------------------
    st.markdown("### Nächste Fälligkeiten (60 Tage)")
    bald = soll[soll["naechste_faelligkeit"] <= heute + pd.Timedelta(days=60)]
    if bald.empty:
        st.caption("Nichts fällig in den nächsten 60 Tagen.")
    for _, r in bald.iterrows():
        tage = (r["naechste_faelligkeit"] - heute).days
        wann = "überfällig" if tage < 0 else ("heute" if tage == 0 else f"in {tage} Tagen")
        c1, c2 = st.columns([3, 1], vertical_alignment="center")
        c1.markdown(f"**{r['name']}** — {eur(r['betrag'])} · {r['naechste_faelligkeit']:%d.%m.%Y} ({wann}) · "
                    f"{posten_name.get(r['konto_id'], 'kein Posten')}")
        if c2.button("✅ Als bezahlt buchen", key=f"bezahlt_{r['id']}", width="stretch"):
            if pd.isna(r["konto_id"]):
                st.error("Bitte der Position zuerst einen Posten zuordnen.")
            else:
                neu = b.monate_weiter(r["naechste_faelligkeit"], r["intervall_monate"])

                def _bezahlt(r=r, neu=neu):
                    daten.einfuegen("buchungen", {
                        "datum": str(heute.date()), "konto_id": r["konto_id"],
                        "topf_id": topf_von.get(r["konto_id"]), "betrag": -round(float(r["betrag"]), 2),
                        "typ": "ausgabe", "kategorie": r["kategorie"] if pd.notna(r["kategorie"]) else "Abos & Verträge",
                        "beschreibung": r["name"],
                    })
                    daten.aendern("fixkosten", r["id"], {"naechste_faelligkeit": str(neu.date())})
                _schreiben(_bezahlt, f"{r['name']} gebucht, nächste Fälligkeit {neu:%d.%m.%Y}.")

    # --- TABELLE ---------------------------------------------------------------------
    st.markdown("### Alle Fixkosten")
    alle = fixkosten.copy()
    alle["pro_monat"] = alle["betrag"] / alle["intervall_monate"]
    st.dataframe(pd.DataFrame({
        "Name": alle["name"],
        "Betrag": alle["betrag"],
        "Intervall": alle["intervall_monate"].map(b.INTERVALLE),
        "Pro Monat": alle["pro_monat"],
        "Nächste Fälligkeit": alle["naechste_faelligkeit"].dt.strftime("%d.%m.%Y"),
        "Posten": alle["konto_id"].map(posten_name).fillna("–"),
        "Kategorie": alle["kategorie"].fillna(""),
        "Aktiv": alle["aktiv"],
    }), hide_index=True, width="stretch", column_config={
        "Betrag": st.column_config.NumberColumn(format="%.2f €"),
        "Pro Monat": st.column_config.NumberColumn(format="%.2f €"),
    })

# --- ANLEGEN / BEARBEITEN ---------------------------------------------------------------
st.markdown("### Fixkosten anlegen oder bearbeiten")
f_opt = [None] + list(fixkosten["id"])
f_namen = dict(zip(fixkosten["id"], fixkosten["name"] + fixkosten["aktiv"].map({True: "", False: " (inaktiv)"})))
f_id = st.selectbox("Position", f_opt, key="fix_auswahl",
                    format_func=lambda i: "➕ Neu anlegen" if i is None else f_namen.get(i, i))
alt = fixkosten.set_index("id").loc[f_id] if f_id else None

# Fixkosten-Posten zuerst anbieten
p_sortiert = posten[posten["aktiv"]].assign(_o=lambda x: (x["stufe"] != "fixkosten").astype(int)).sort_values(["_o", "name"])
p_opt = [None] + list(p_sortiert["id"])
if alt is not None and pd.notna(alt["konto_id"]) and alt["konto_id"] not in p_opt:
    p_opt.append(alt["konto_id"])
intervalle = list(b.INTERVALLE)
kategorien = b.AUSGABE_KATEGORIEN

with st.form(f"fix_form_{f_id}"):
    name = st.text_input("Name", value="" if alt is None else alt["name"], max_chars=60,
                         placeholder="z. B. Kfz-Versicherung")
    c1, c2, c3 = st.columns(3)
    betrag = c1.number_input("Betrag (€)", min_value=0.0, step=1.0, format="%.2f",
                             value=float(alt["betrag"]) if alt is not None else 0.0)
    intervall = c2.selectbox("Intervall", intervalle, format_func=b.INTERVALLE.get,
                             index=intervalle.index(int(alt["intervall_monate"])) if alt is not None else 0)
    faellig = c3.date_input("Nächste Fälligkeit", format="DD.MM.YYYY",
                            value=alt["naechste_faelligkeit"].date() if alt is not None else heute.date())
    c4, c5 = st.columns(2)
    konto = c4.selectbox("Bezahlt aus Posten", p_opt,
                         format_func=lambda k: "– kein Posten –" if k is None else posten_name.get(k, k),
                         index=p_opt.index(alt["konto_id"]) if alt is not None and alt["konto_id"] in p_opt
                         else (1 if len(p_opt) > 1 else 0))
    kat_alt = alt["kategorie"] if alt is not None and pd.notna(alt["kategorie"]) else "Abos & Verträge"
    kategorie = c5.selectbox("Kategorie", kategorien,
                             index=kategorien.index(kat_alt) if kat_alt in kategorien else 0)
    notiz = st.text_input("Notiz", value="" if alt is None or pd.isna(alt["notiz"]) else alt["notiz"],
                          placeholder="z. B. läuft über T212 wegen Cashback")
    aktiv = st.checkbox("Aktiv", value=True if alt is None else bool(alt["aktiv"]))
    ok = st.form_submit_button("Speichern", type="primary", width="stretch")
if ok:
    werte = {"name": name.strip(), "betrag": round(betrag, 2), "intervall_monate": int(intervall),
             "naechste_faelligkeit": str(faellig), "konto_id": konto, "kategorie": kategorie,
             "notiz": notiz.strip() or None, "aktiv": aktiv}
    if not werte["name"] or betrag <= 0:
        st.error("Bitte Name und einen Betrag größer 0 eingeben.")
    elif f_id is None:
        _schreiben(lambda: daten.einfuegen("fixkosten", werte), f"„{werte['name']}“ angelegt.")
    else:
        _schreiben(lambda: daten.aendern("fixkosten", f_id, werte), f"„{werte['name']}“ gespeichert.")
if f_id:
    with st.popover("🗑️ Position löschen"):
        st.write("Bereits gebuchte Zahlungen bleiben erhalten.")
        if st.button("Endgültig löschen", key="fix_loeschen"):
            _schreiben(lambda: daten.loeschen("fixkosten", [f_id]), "Position gelöscht.")
