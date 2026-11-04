import pandas as pd
import pytest

from finanzen import berechnung as b


def posten():
    return pd.DataFrame([
        {"id": "tg", "name": "Tagesgeld", "art": "tagesgeld", "ertragsart": "zins", "risiko": 1,
         "standard_topf_id": "t_tg", "aktiv": True, "stufe": "ruecklage"},
        {"id": "mmf", "name": "Geldmarkt", "art": "geldmarktfonds", "ertragsart": "zins", "risiko": 2,
         "standard_topf_id": "t_mmf", "aktiv": True, "stufe": "verbindlichkeit"},
        {"id": "depot", "name": "Depot", "art": "depot", "ertragsart": "markt", "risiko": 3,
         "standard_topf_id": "t_depot", "aktiv": True, "stufe": "langfristig"},
        {"id": "giro", "name": "Giro", "art": "giro", "ertragsart": "keine", "risiko": 1,
         "standard_topf_id": "t_giro", "aktiv": True, "stufe": "frei"},
        {"id": "fix", "name": "Fixkosten", "art": "giro", "ertragsart": "keine", "risiko": 1,
         "standard_topf_id": "t_fix", "aktiv": True, "stufe": "fixkosten"},
    ]).assign(bewertung="saldo", ertrag_ausgezahlt=False)


def zinssaetze():
    return b.zinssaetze_normalisieren(pd.DataFrame([
        {"konto_id": "tg", "gueltig_ab": "2026-11-01", "satz_pa": 3.65},
        {"konto_id": "tg", "gueltig_ab": "2026-11-11", "satz_pa": 7.30},
        {"konto_id": "mmf", "gueltig_ab": "2026-01-01", "satz_pa": 2.0},
        {"konto_id": "depot", "gueltig_ab": "2026-01-01", "satz_pa": 7.0},
    ]))


def buchungen(rows):
    return b.buchungen_normalisieren(pd.DataFrame(rows))


def test_satz_am_mit_historie():
    z = zinssaetze()
    assert b.satz_am(z, "tg", "2026-10-31") == 0.0
    assert b.satz_am(z, "tg", "2026-11-05") == pytest.approx(3.65)
    assert b.satz_am(z, "tg", "2026-11-11") == pytest.approx(7.30)


def test_saldo_verlauf_und_vorbestand():
    bu = buchungen([
        {"datum": "2026-11-01", "konto_id": "giro", "betrag": 100, "typ": "eroeffnung"},
        {"datum": "2026-11-03", "konto_id": "giro", "betrag": -30, "typ": "ausgabe"},
    ])
    v = b.saldo_verlauf(bu, "2026-11-05")
    assert list(v["giro"]) == [100, 100, 70, 70, 70]
    v2 = b.saldo_verlauf(bu, "2026-11-05", von="2026-11-04")
    assert list(v2["giro"]) == [70, 70]


def test_aufgelaufene_zinsen_genau():
    # 3650 € zu 3,65 % = 0,365 €/Tag; 10 Tage (1.-10.11.) zu 3,65 %, dann 7,30 %
    bu = buchungen([{"datum": "2026-11-01", "konto_id": "tg", "betrag": 3650, "typ": "eroeffnung"}])
    z = b.aufgelaufene_zinsen(bu, posten(), zinssaetze(), "2026-11-11")
    assert z["tg"] == pytest.approx(10 * 0.365)
    z = b.aufgelaufene_zinsen(bu, posten(), zinssaetze(), "2026-11-13")
    assert z["tg"] == pytest.approx(10 * 0.365 + 2 * 0.73)


def test_abgleich_setzt_zinsschaetzung_zurueck():
    bu = buchungen([
        {"datum": "2026-11-01", "konto_id": "tg", "betrag": 3650, "typ": "eroeffnung"},
        {"datum": "2026-11-11", "konto_id": "tg", "betrag": 3.65, "typ": "zins"},
    ])
    z = b.aufgelaufene_zinsen(bu, posten(), zinssaetze(), "2026-11-12")
    assert z["tg"] == pytest.approx(3653.65 * 0.073 / 365)


def test_ertrag_verlauf_waechst_mit_einzahlung():
    bu = buchungen([
        {"datum": "2026-11-01", "konto_id": "mmf", "betrag": 1000, "typ": "eroeffnung"},
        {"datum": "2026-11-03", "konto_id": "mmf", "betrag": 1000, "typ": "einnahme"},
    ])
    ev = b.ertrag_verlauf(bu, posten(), zinssaetze(), "2026-11-04")
    assert ev["zins"].iloc[0] == pytest.approx(1000 * 0.02 / 365)
    assert ev["zins"].iloc[-1] == pytest.approx(2000 * 0.02 / 365)


def test_umbuchung():
    rows = b.umbuchung("2026-11-02", "giro", "t_giro", "mmf", "t_mmf", 50)
    assert [r["betrag"] for r in rows] == [-50, 50]
    assert rows[0]["gruppe"] == rows[1]["gruppe"]
    assert rows[1]["topf_id"] == "t_mmf"
    with pytest.raises(ValueError):
        b.umbuchung("2026-11-02", "mmf", "t_mmf", "mmf", "t_mmf", 20)
    with pytest.raises(ValueError):
        b.umbuchung("2026-11-02", "giro", None, "mmf", None, 0)


def test_abgleich_buchung():
    konto = posten().set_index("id").loc["mmf"].to_dict() | {"id": "mmf"}
    r = b.abgleich(konto, 1402.5, 1400, "2026-11-30")
    assert r["typ"] == "zins" and r["betrag"] == pytest.approx(2.5) and r["topf_id"] == "t_mmf"
    r = b.abgleich(posten().iloc[2].to_dict(), 3800, 4000, "2026-11-30")
    assert r["typ"] == "abgleich" and r["betrag"] == pytest.approx(-200)
    assert b.abgleich(konto, 1400.001, 1400, "2026-11-30") is None


def test_gruppen_abgleich_verteilt_anteilig():
    p = [
        {"id": "busse", "ertragsart": "zins", "standard_topf_id": "t1", "gebucht": 1203.08},
        {"id": "straf", "ertragsart": "zins", "standard_topf_id": "t2", "gebucht": 100.26},
    ]
    zeilen = b.gruppen_abgleich(p, 1305.34, "2026-11-30")
    assert sum(z["betrag"] for z in zeilen) == pytest.approx(2.00)
    anteil = {z["konto_id"]: z["betrag"] for z in zeilen}
    assert anteil["busse"] == pytest.approx(2.00 * 1203.08 / 1303.34, abs=0.01)
    assert all(z["typ"] == "zins" for z in zeilen)
    assert b.gruppen_abgleich(p, 1303.34, "2026-11-30") == []
    # leere Posten -> gleichmaessig
    leer = [{**x, "gebucht": 0} for x in p]
    assert [z["betrag"] for z in b.gruppen_abgleich(leer, 10, "2026-11-30")] == [5, 5]


def test_monate_weiter():
    assert b.monate_weiter("2027-01-31", 1) == pd.Timestamp("2027-02-28")
    assert b.monate_weiter("2026-11-15", 6) == pd.Timestamp("2027-05-15")


def test_depot_rendite():
    r = b.depot_rendite(pd.Series([1.0, -0.5, 0.8]))
    assert r.n_wochen == 3 and not r.uebernehmen
    geo = ((1.01 * 0.995 * 1.008) ** (1 / 3) - 1)
    assert r.woche_geo == pytest.approx(geo * 100)
    assert r.pa == pytest.approx(((1 + geo) ** 52 - 1) * 100)
    r = b.depot_rendite(pd.Series([0.1] * 12))
    assert r.uebernehmen and r.pa == pytest.approx((1.001 ** 52 - 1) * 100)
    # nur die letzten 52 Wochen zaehlen
    r = b.depot_rendite(pd.Series([50.0] * 10 + [0.0] * 52))
    assert r.pa == pytest.approx(0.0) and r.n_wochen == 52
    assert b.depot_rendite(pd.Series([], dtype=float)).pa is None


def test_depot_index():
    idx = b.depot_index(pd.Series([10.0, -10.0]))
    assert list(idx.round(4)) == [110.0, 99.0]


def test_eur_format():
    assert b.eur(1234.5) == "1.234,50 €"
    assert b.eur(-0.123, 3) == "-0,123 €"


def test_vor_startbestand_findet_doppelte_buchung():
    """Regression 01.11.: Depot-Abgleich am 30.10. bei Startbestand am 01.11."""
    bu = buchungen([
        {"id": "s", "datum": "2026-11-01", "konto_id": "depot", "betrag": 4144, "typ": "eroeffnung"},
        {"id": "x", "datum": "2026-10-30", "konto_id": "depot", "betrag": 4144, "typ": "abgleich"},
        {"id": "ok", "datum": "2026-11-02", "konto_id": "depot", "betrag": 10, "typ": "abgleich"},
        {"id": "g", "datum": "2026-10-01", "konto_id": "giro", "betrag": 5, "typ": "einnahme"},  # kein Startbestand
    ])
    assert list(b.vor_startbestand(bu)["id"]) == ["x"]
    assert b.startdatum(bu, "depot") == pd.Timestamp("2026-11-01")
    assert b.startdatum(bu, "giro") is None
    assert b.vor_startbestand(b.leere_buchungen()).empty


# --- Konto/Topf-Modell mit Anteilsrechnung (echte Zahlen vom 04.11.) -----------------

def modell():
    konten = pd.DataFrame([
        {"id": "dbx", "name": "T212 Geldmarktfonds (DBX0AN)", "art": "geldmarktfonds", "ertragsart": "zins",
         "bewertung": "anteile", "ertrag_ausgezahlt": False, "stufe": "fixkosten", "risiko": 2, "aktiv": True},
        {"id": "vb", "name": "Volksbank Fixkostenkonto", "art": "giro", "ertragsart": "keine",
         "bewertung": "saldo", "ertrag_ausgezahlt": False, "stufe": "fixkosten", "risiko": 1, "aktiv": True},
        {"id": "bbva", "name": "BBVA Tagesgeld", "art": "tagesgeld", "ertragsart": "zins",
         "bewertung": "saldo", "ertrag_ausgezahlt": True, "stufe": "ruecklage", "risiko": 1, "aktiv": True},
    ])
    toepfe = pd.DataFrame([
        {"id": "kfz", "name": "KFZ-Versicherung", "konto_id": "dbx", "stufe": "fixkosten", "aktiv": True, "zielbetrag": None},
        {"id": "esn", "name": "ESN", "konto_id": "dbx", "stufe": "fixkosten", "aktiv": True, "zielbetrag": None},
        {"id": "gn", "name": "GoodNotes", "konto_id": "dbx", "stufe": "fixkosten", "aktiv": True, "zielbetrag": None},
        {"id": "zahn", "name": "Zahnarzt", "konto_id": "dbx", "stufe": "fixkosten", "aktiv": True, "zielbetrag": None},
        {"id": "linsen", "name": "Kontaktlinsen", "konto_id": "dbx", "stufe": "fixkosten", "aktiv": True, "zielbetrag": None},
        {"id": "ueb", "name": "Überschuss & Zinsen", "konto_id": "dbx", "stufe": "fixkosten", "aktiv": True, "zielbetrag": None},
        {"id": "gym", "name": "Gym", "konto_id": "vb", "stufe": "fixkosten", "aktiv": True, "zielbetrag": None},
        {"id": "marge", "name": "Sicherheitsmarge", "konto_id": "vb", "stufe": "fixkosten", "aktiv": True, "zielbetrag": None},
        {"id": "auto", "name": "Autorücklagen", "konto_id": "bbva", "stufe": "ruecklage", "aktiv": True, "zielbetrag": 2000},
    ])
    ek = 150.646
    bu = buchungen([
        {"datum": "2026-11-01", "konto_id": "dbx", "topf_id": "kfz", "betrag": 102, "anteile": 102 / ek, "typ": "eroeffnung"},
        {"datum": "2026-11-01", "konto_id": "dbx", "topf_id": "esn", "betrag": 29, "anteile": 29 / ek, "typ": "eroeffnung"},
        {"datum": "2026-11-01", "konto_id": "dbx", "topf_id": "gn", "betrag": 1, "anteile": 1 / ek, "typ": "eroeffnung"},
        {"datum": "2026-11-01", "konto_id": "dbx", "topf_id": "zahn", "betrag": 64, "anteile": 64 / ek, "typ": "eroeffnung"},
        {"datum": "2026-11-01", "konto_id": "dbx", "topf_id": "linsen", "betrag": 14, "anteile": 14 / ek, "typ": "eroeffnung"},
        {"datum": "2026-11-01", "konto_id": "vb", "topf_id": "gym", "betrag": 30, "typ": "eroeffnung"},
        {"datum": "2026-11-01", "konto_id": "vb", "topf_id": "marge", "betrag": 18.64, "typ": "eroeffnung"},
        {"datum": "2026-11-04", "konto_id": "vb", "topf_id": "gym", "betrag": -30, "typ": "ausgabe"},
        {"datum": "2026-11-01", "konto_id": "bbva", "topf_id": "auto", "betrag": 1598.75, "typ": "eroeffnung"},
    ])
    zins = b.zinssaetze_normalisieren(pd.DataFrame([
        {"konto_id": "dbx", "gueltig_ab": "2026-11-01", "satz_pa": 2.5},
        {"konto_id": "bbva", "gueltig_ab": "2026-11-01", "satz_pa": 3.5},
    ]))
    kurse = b.kurse_normalisieren(pd.DataFrame([
        {"konto_id": "dbx", "datum": "2026-11-01", "kurs": 210.04 / 1.394, "wert": 210.04},
        {"konto_id": "dbx", "datum": "2026-11-04", "kurs": 210.07 / 1.394, "wert": 210.07},
    ]))
    return konten, toepfe, bu, zins, kurse


def test_anteilsrechnung_verteilt_buchgewinn_auf_toepfe():
    konten, toepfe, bu, zins, kurse = modell()
    t = b.topf_salden(konten, toepfe, bu, zins, kurse, "2026-11-04").set_index("topf_key")
    gesamt = t.loc[["kfz", "esn", "gn", "zahn", "linsen", "ueb"], "wert"].sum()
    assert gesamt == pytest.approx(210.07, abs=0.005)           # exakt dein Stand
    assert t.loc[["kfz", "esn", "gn", "zahn", "linsen"], "einstand"].sum() == pytest.approx(210.00)
    assert t.at["kfz", "aufgelaufen"] == pytest.approx(0.07 * 102 / 210, abs=0.002)  # Buchgewinn anteilig
    assert t.at["ueb", "wert"] == 0  # leerer Topf ist trotzdem da


def test_kurs_wird_mit_satz_fortgeschrieben():
    konten, toepfe, bu, zins, kurse = modell()
    kurs, ab = b.kurs_geschaetzt(kurse, zins, "dbx", "2026-12-04")
    assert ab == pd.Timestamp("2026-11-04")
    assert kurs == pytest.approx(210.07 / 1.394 * 1.025 ** (30 / 365))


def test_ueberblick_ertraege_ausgezahlt_vs_buchgewinn():
    konten, toepfe, bu, zins, kurse = modell()
    u = b.ueberblick(konten, toepfe, bu, zins, None, "2026-11-04", kurse)
    assert u.brutto == pytest.approx(210.07 + 18.64 + 1598.75 + 1598.75 * 0.035 / 365 * 3, abs=0.01)
    assert u.stufen["fixkosten"] == pytest.approx(210.07 + 18.64, abs=0.01)
    # Zinsen insgesamt: BBVA + Geldmarktfonds; ausgezahlt nur BBVA
    assert u.ertrag_tag_zins > u.ausgezahlt_tag_prognose > 0
    assert u.ausgezahlt_tag_prognose == pytest.approx(u.konten.set_index("konto_id").at["bbva", "ertrag_tag"])
    assert u.ausgezahlt_30_tage == 0
    bu2 = pd.concat([bu, buchungen([{"datum": "2026-11-30", "konto_id": "bbva", "topf_id": "auto", "betrag": 4.6, "typ": "zins"}])])
    u2 = b.ueberblick(konten, toepfe, bu2, zins, None, "2026-11-30", kurse)
    assert u2.ausgezahlt_30_tage == pytest.approx(4.6)


def test_stufen_verlauf_nach_topf():
    konten, toepfe, bu, zins, kurse = modell()
    sv = b.stufen_verlauf(bu, konten, toepfe, "2026-11-04")
    assert list(sv.columns) == ["fixkosten", "ruecklage"]
    assert sv["fixkosten"].iloc[-1] == pytest.approx(210 + 18.64)


def fixpos(**k):
    basis = {"id": "x", "name": "X", "betrag": 30.0, "intervall_monate": 1, "naechste_faelligkeit": "2026-12-01",
             "konto_id": "vb", "topf_id": "gym", "modus": "direkt", "rate_monat": None, "rest_regel": "rest_topf",
             "rest_topf_id": "marge", "auto_abbuchen": True, "prognose_fuelbudget": False, "aktiv": True,
             "kategorie": "Freizeit", "notiz": None}
    return b.fixkosten_normalisieren(pd.DataFrame([{**basis, **k}])).iloc[0]


def test_einzahlung_direkt():
    gym = fixpos(name="Gym")                                   # schon bezahlt, naechste am 01.12.
    assert b.einzahlung_vorschlag(gym, 0.0, "2026-11-30") == 30
    congstar = fixpos(name="Congstar", betrag=22.0, naechste_faelligkeit="2026-11-15")
    assert b.einzahlung_vorschlag(congstar, 22.0, "2026-11-04") == 22   # Nov noch offen + Dez


def test_einzahlung_ansparen():
    kfz = fixpos(name="KFZ", betrag=304.0, rate_monat=102.0, intervall_monate=3, modus="ansparen",
                 naechste_faelligkeit="2027-01-01", konto_id="dbx", topf_id="kfz", rest_topf_id="ueb")
    assert b.einzahlung_vorschlag(kfz, 102.0, "2026-11-30") == 102
    assert b.einzahlung_vorschlag(kfz, 306.0, "2026-12-31") == 0
    esn = fixpos(name="ESN", betrag=87.0, rate_monat=29.0, intervall_monate=3, modus="ansparen", rest_regel="verrechnen")
    assert b.einzahlung_vorschlag(esn, 5.0, "2026-11-30") == 24      # 5 € Rest aus letzter Bestellung
    assert b.einzahlung_vorschlag(esn, 29.0, "2026-11-30") == 29
    assert b.einzahlung_vorschlag(esn, 87.0, "2026-11-30") == 0


def test_einzahlung_budget_mit_prognose():
    kraft = fixpos(name="Kraftstoff", betrag=450.0, modus="budget", rest_regel="verrechnen", prognose_fuelbudget=True)
    assert b.einzahlung_vorschlag(kraft, 20.0, "2026-11-30", prognose_monat=370.83) == pytest.approx(350.83)
    assert b.einzahlung_vorschlag(kraft.to_dict() | {"prognose_fuelbudget": False}, 20.0, "2026-11-30") == 430


def test_fixkosten_status_warnt_zahnarzt():
    konten, toepfe, bu, zins, kurse = modell()
    ts = b.topf_salden(konten, toepfe, bu, zins, kurse, "2026-11-04")
    zahn = fixpos(name="Zahnarzt", betrag=96.0, rate_monat=16.0, intervall_monate=6, modus="ansparen",
                  naechste_faelligkeit="2026-12-01", konto_id="dbx", topf_id="zahn")
    kfz = fixpos(name="KFZ", betrag=304.0, rate_monat=102.0, intervall_monate=3, modus="ansparen",
                 naechste_faelligkeit="2027-01-01", konto_id="dbx", topf_id="kfz")
    st = b.fixkosten_status(pd.DataFrame([zahn, kfz]), ts, "2026-11-04").set_index("name")
    assert st.at["Zahnarzt", "raten"] == pytest.approx(4)
    assert st.at["Zahnarzt", "einzahlungen_bis_faellig"] == 1          # nur noch Ende November
    assert st.at["Zahnarzt", "fehlt_bei_faelligkeit"] == pytest.approx(16, abs=0.05)
    assert st.at["KFZ", "fehlt_bei_faelligkeit"] == 0                    # 102 + 2 x 102 >= 304


def test_zahlung_mit_rest_topf_und_fehlbetrag():
    kfz = fixpos(name="KFZ", betrag=304.0, rate_monat=102.0, intervall_monate=3, modus="ansparen",
                 konto_id="dbx", topf_id="kfz", rest_topf_id="ueb")
    zeilen = b.zahlung_buchen(kfz, 304, "2027-01-02", topf_wert=306.80, konto_bewertung="anteile", kurs=151.0)
    assert zeilen[0]["betrag"] == -304 and zeilen[0]["anteile"] == pytest.approx(-304 / 151)
    rest = [z for z in zeilen if z["typ"] == "umbuchung"]
    assert {(z["topf_id"], z["betrag"]) for z in rest} == {("kfz", -2.8), ("ueb", 2.8)}
    gn = fixpos(name="GoodNotes", betrag=13.0, rate_monat=1.0, intervall_monate=12, modus="ansparen",
                konto_id="dbx", topf_id="gn", rest_topf_id="ueb")
    zeilen = b.zahlung_buchen(gn, 13, "2027-10-08", topf_wert=12.10, konto_bewertung="anteile", kurs=152.0)
    rest = {z["topf_id"]: z["betrag"] for z in zeilen if z["typ"] == "umbuchung"}
    assert rest == {"ueb": -0.9, "gn": 0.9}                              # fehlender Betrag aus dem Rest-Topf
    esn = fixpos(name="ESN", betrag=87.0, modus="ansparen", rest_regel="verrechnen", topf_id="esn")
    assert len(b.zahlung_buchen(esn, 82, "2027-01-05", topf_wert=87.2)) == 1  # Rest bleibt im Topf


def test_faellige_abbuchungen():
    f = b.fixkosten_normalisieren(pd.DataFrame([
        dict(fixpos(name="Claude", betrag=21.0, naechste_faelligkeit="2026-10-15")),
        dict(fixpos(name="ZZV", betrag=5.0, auto_abbuchen=False, naechste_faelligkeit="2026-10-01")),
    ]))
    fae = b.faellige_abbuchungen(f, "2026-11-20")
    assert [(x[0]["name"], x[1].strftime("%d.%m.")) for x in fae] == [("Claude", "15.10."), ("Claude", "15.11.")]


def test_einzahlungen_bis():
    assert b.einzahlungen_bis("2026-11-04", "2026-12-01") == 1
    assert b.einzahlungen_bis("2026-11-04", "2027-01-01") == 2
    assert b.einzahlungen_bis("2026-11-30", "2026-12-01") == 1
