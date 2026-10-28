import pandas as pd
import pytest

from finanzen import berechnung as b


def konten():
    return pd.DataFrame([
        {"id": "tg", "name": "Tagesgeld", "art": "tagesgeld", "ertragsart": "zins", "risiko": 1,
         "standard_topf_id": None, "aktiv": True},
        {"id": "mmf", "name": "Geldmarkt", "art": "geldmarktfonds", "ertragsart": "zins", "risiko": 2,
         "standard_topf_id": "frei_topf", "aktiv": True},
        {"id": "depot", "name": "Depot", "art": "depot", "ertragsart": "markt", "risiko": 3,
         "standard_topf_id": None, "aktiv": True},
        {"id": "giro", "name": "Giro", "art": "giro", "ertragsart": "keine", "risiko": 1,
         "standard_topf_id": None, "aktiv": True},
    ])


def toepfe():
    return pd.DataFrame([
        {"id": "auto", "name": "Autopuffer", "art": "puffer", "zielbetrag": 1600},
        {"id": "busse", "name": "Geldbuße", "art": "ruecklage", "zielbetrag": None},
        {"id": "frei_topf", "name": "Freies Geld MMF", "art": "frei", "zielbetrag": None},
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
    z = b.aufgelaufene_zinsen(bu, konten(), zinssaetze(), "2026-11-11")
    assert z["tg"] == pytest.approx(10 * 0.365)
    z = b.aufgelaufene_zinsen(bu, konten(), zinssaetze(), "2026-11-13")
    assert z["tg"] == pytest.approx(10 * 0.365 + 2 * 0.73)


def test_abgleich_setzt_zinsschaetzung_zurueck():
    bu = buchungen([
        {"datum": "2026-11-01", "konto_id": "tg", "betrag": 3650, "typ": "eroeffnung"},
        {"datum": "2026-11-11", "konto_id": "tg", "betrag": 3.65, "typ": "zins"},
    ])
    z = b.aufgelaufene_zinsen(bu, konten(), zinssaetze(), "2026-11-12")
    # ab 11.11.: Saldo 3653,65 zu 7,30 % fuer einen Tag
    assert z["tg"] == pytest.approx(3653.65 * 0.073 / 365)


def test_ueberblick_brutto_netto_frei():
    v = pd.DataFrame([{"id": "v1", "name": "Geldbuße", "betrag_gesamt": 1200, "erledigt": False}])
    bu = buchungen([
        {"datum": "2026-11-01", "konto_id": "mmf", "topf_id": "busse", "betrag": 1200, "typ": "eroeffnung"},
        {"datum": "2026-11-01", "konto_id": "mmf", "topf_id": "frei_topf", "betrag": 200, "typ": "eroeffnung"},
        {"datum": "2026-11-01", "konto_id": "giro", "topf_id": "auto", "betrag": 1600, "typ": "eroeffnung"},
        {"datum": "2026-11-01", "konto_id": "giro", "betrag": 300, "typ": "eroeffnung"},
        {"datum": "2026-11-01", "konto_id": "depot", "betrag": 4000, "typ": "eroeffnung"},
        # Rate der Geldbusse
        {"datum": "2026-11-05", "konto_id": "mmf", "topf_id": "busse", "betrag": -200, "typ": "ausgabe",
         "verbindlichkeit_id": "v1"},
    ])
    u = b.ueberblick(konten(), toepfe(), bu, zinssaetze(), v, "2026-11-01")
    assert u.brutto == pytest.approx(7300)
    assert u.verbindlichkeiten_offen == pytest.approx(1200)
    assert u.netto == pytest.approx(6100)
    assert u.frei == pytest.approx(200 + 300 + 4000)  # Depot ohne Topf zaehlt als frei

    u = b.ueberblick(konten(), toepfe(), bu, zinssaetze(), v, "2026-11-06")
    zinsen_mmf = 1400 * 0.02 / 365 * 4 + 1200 * 0.02 / 365  # 1.-4.11. 1400 €, 5.11. 1200 €
    assert u.verbindlichkeiten_offen == pytest.approx(1000)
    assert u.brutto == pytest.approx(7100 + zinsen_mmf)
    assert u.netto == pytest.approx(6100 + zinsen_mmf)  # Tilgung aendert Netto nicht
    # Zinsen landen im Standardtopf des Kontos
    assert u.matrix.loc["mmf", "frei_topf"] == pytest.approx(200 + zinsen_mmf)
    t = u.toepfe.set_index("id")["stand"]
    assert t["busse"] == pytest.approx(1000)
    assert t["auto"] == pytest.approx(1600)


def test_tagesertrag_nach_ertragsart():
    bu = buchungen([
        {"datum": "2026-11-01", "konto_id": "mmf", "betrag": 3650, "typ": "eroeffnung"},
        {"datum": "2026-11-01", "konto_id": "depot", "betrag": 3650, "typ": "eroeffnung"},
    ])
    u = b.ueberblick(konten(), toepfe(), bu, zinssaetze(), None, "2026-11-01")
    assert u.ertrag_tag_zins == pytest.approx(3650 * 0.02 / 365)
    assert u.ertrag_tag_markt == pytest.approx(3650 * 0.07 / 365)
    assert u.ertrag_tag_dividende == 0


def test_ertrag_verlauf_waechst_mit_einzahlung():
    bu = buchungen([
        {"datum": "2026-11-01", "konto_id": "mmf", "betrag": 1000, "typ": "eroeffnung"},
        {"datum": "2026-11-03", "konto_id": "mmf", "betrag": 1000, "typ": "einnahme"},
    ])
    ev = b.ertrag_verlauf(bu, konten(), zinssaetze(), "2026-11-04")
    assert ev["zins"].iloc[0] == pytest.approx(1000 * 0.02 / 365)
    assert ev["zins"].iloc[-1] == pytest.approx(2000 * 0.02 / 365)


def test_umbuchung_und_umtopfen():
    rows = b.umbuchung("2026-11-02", "giro", None, "mmf", "auto", 50)
    assert [r["betrag"] for r in rows] == [-50, 50]
    assert rows[0]["gruppe"] == rows[1]["gruppe"]
    rows = b.umbuchung("2026-11-02", "mmf", "busse", "mmf", None, 20)
    assert rows[1]["topf_id"] is None
    with pytest.raises(ValueError):
        b.umbuchung("2026-11-02", "mmf", None, "mmf", None, 20)
    with pytest.raises(ValueError):
        b.umbuchung("2026-11-02", "giro", None, "mmf", None, 0)


def test_abgleich_buchung():
    konto = konten().set_index("id").loc["mmf"].to_dict() | {"id": "mmf"}
    r = b.abgleich(konto, 1402.5, 1400, "2026-11-30")
    assert r["typ"] == "zins" and r["betrag"] == pytest.approx(2.5) and r["topf_id"] == "frei_topf"
    r = b.abgleich(konten().iloc[2].to_dict(), 3800, 4000, "2026-11-30")
    assert r["typ"] == "abgleich" and r["betrag"] == pytest.approx(-200)
    assert b.abgleich(konto, 1400.001, 1400, "2026-11-30") is None


def test_eur_format():
    assert b.eur(1234.5) == "1.234,50 €"
    assert b.eur(-0.123, 3) == "-0,123 €"
