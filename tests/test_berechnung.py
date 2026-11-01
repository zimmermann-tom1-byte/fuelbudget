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
    ])


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


def test_ueberblick_stufen_brutto_netto():
    v = pd.DataFrame([{"id": "v1", "name": "Geldbuße", "betrag_gesamt": 1200, "erledigt": False}])
    bu = buchungen([
        {"datum": "2026-11-01", "konto_id": "mmf", "betrag": 1200, "typ": "eroeffnung"},
        {"datum": "2026-11-01", "konto_id": "tg", "betrag": 1600, "typ": "eroeffnung"},
        {"datum": "2026-11-01", "konto_id": "giro", "betrag": 300, "typ": "eroeffnung"},
        {"datum": "2026-11-01", "konto_id": "depot", "betrag": 4000, "typ": "eroeffnung"},
        {"datum": "2026-11-01", "konto_id": "fix", "betrag": 583, "typ": "eroeffnung"},
        {"datum": "2026-11-05", "konto_id": "mmf", "betrag": -200, "typ": "ausgabe", "verbindlichkeit_id": "v1"},
    ])
    u = b.ueberblick(posten(), bu, zinssaetze(), v, "2026-11-01", ziele={"tg": 2000})
    assert u.brutto == pytest.approx(7683)
    assert u.verbindlichkeiten_offen == pytest.approx(1200)
    # Fixkosten verringern das Netto NICHT, nur die Verbindlichkeit
    assert u.netto == pytest.approx(7683 - 1200)
    assert u.frei == pytest.approx(300)
    assert list(u.stufen.index) == list(b.STUFEN)
    assert u.stufen["fixkosten"] == pytest.approx(583)
    assert u.stufen["langfristig"] == pytest.approx(4000)
    assert u.posten.set_index("id").at["tg", "ziel"] == 2000

    u = b.ueberblick(posten(), bu, zinssaetze(), v, "2026-11-06")
    assert u.verbindlichkeiten_offen == pytest.approx(1000)
    zinsen_mmf = 1200 * 0.02 / 365 * 4 + 1000 * 0.02 / 365
    assert u.stufen["verbindlichkeit"] == pytest.approx(1000 + zinsen_mmf)
    # Tilgung aendert das Netto nicht (Geld weg, Schuld weg)
    zinsen_tg = 1600 * 0.0365 / 365 * 5
    assert u.netto == pytest.approx(7683 - 1200 + zinsen_mmf + zinsen_tg)


def test_stufen_verlauf():
    bu = buchungen([
        {"datum": "2026-11-01", "konto_id": "giro", "betrag": 100, "typ": "eroeffnung"},
        {"datum": "2026-11-01", "konto_id": "depot", "betrag": 1000, "typ": "eroeffnung"},
        {"datum": "2026-11-02", "konto_id": "giro", "betrag": -40, "typ": "ausgabe"},
    ])
    sv = b.stufen_verlauf(bu, posten(), "2026-11-02")
    assert list(sv.columns) == ["frei", "langfristig"]
    assert list(sv["frei"]) == [100, 60]


def test_tagesertrag_nach_ertragsart():
    bu = buchungen([
        {"datum": "2026-11-01", "konto_id": "mmf", "betrag": 3650, "typ": "eroeffnung"},
        {"datum": "2026-11-01", "konto_id": "depot", "betrag": 3650, "typ": "eroeffnung"},
    ])
    u = b.ueberblick(posten(), bu, zinssaetze(), None, "2026-11-01")
    assert u.ertrag_tag_zins == pytest.approx(3650 * 0.02 / 365)
    assert u.ertrag_tag_markt == pytest.approx(3650 * 0.07 / 365)
    assert u.ertrag_tag_dividende == 0


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


def test_soll_heute_monatlich():
    # Gym 30 €, faellig am 15.11.: im November voll zurueckgelegt
    assert b.soll_heute(30, 1, "2026-11-15", "2026-11-02") == 30
    assert b.soll_heute(30, 1, "2026-11-15", "2026-11-20") == 30  # ueberfaellig
    # bereits bezahlt -> naechste Faelligkeit im Dezember -> heute 0
    assert b.soll_heute(30, 1, "2026-12-15", "2026-11-20") == 0


def test_soll_heute_quartal_linear():
    # 300 € alle 3 Monate, faellig 1.2.2027 -> letzte Faelligkeit 1.11.2026
    assert b.soll_heute(300, 3, "2027-02-01", "2026-11-01") == pytest.approx(0)
    mitte = b.soll_heute(300, 3, "2027-02-01", "2026-12-17")
    assert 145 < mitte < 155
    assert b.soll_heute(300, 3, "2027-02-01", "2027-02-01") == 300


def test_fixkosten_je_posten():
    f = b.fixkosten_normalisieren(pd.DataFrame([
        {"id": "1", "name": "Gym", "betrag": 30, "intervall_monate": 1, "naechste_faelligkeit": "2026-11-15",
         "konto_id": "fix", "aktiv": True},
        {"id": "2", "name": "Kfz", "betrag": 300, "intervall_monate": 3, "naechste_faelligkeit": "2027-02-01",
         "konto_id": "fix", "aktiv": True},
        {"id": "3", "name": "alt", "betrag": 99, "intervall_monate": 1, "naechste_faelligkeit": "2026-11-15",
         "konto_id": "fix", "aktiv": False},
    ]))
    g = b.fixkosten_je_posten(f, pd.Series({"fix": 100.0}), "2026-11-01").set_index("konto_id")
    assert g.at["fix", "soll"] == pytest.approx(30)
    assert g.at["fix", "pro_monat"] == pytest.approx(30 + 100)
    assert g.at["fix", "differenz"] == pytest.approx(70)


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
