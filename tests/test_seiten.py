"""Laesst die Seiten mit Streamlit-Ersatz und einer Fake-Datenbank laufen."""

import copy
import runpy
import sys
import types
import uuid
from datetime import date
from pathlib import Path

import pytest

from tests import stub_streamlit

ROOT = Path(__file__).resolve().parents[1]
UID = "user-1"


# --- Fake-Supabase ----------------------------------------------------------------

class FakeQuery:
    def __init__(self, db, table):
        self.db, self.table, self.op, self.payload, self.filters = db, table, "select", None, []

    def select(self, *_):
        self.op = "select"
        return self

    def insert(self, rows):
        self.op, self.payload = "insert", rows
        return self

    def update(self, werte):
        self.op, self.payload = "update", werte
        return self

    def upsert(self, rows, on_conflict=""):
        self.op, self.payload, self.konflikt = "upsert", rows, [c.strip() for c in on_conflict.split(",")]
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val)
        return self

    def in_(self, col, vals):
        self.filters.append(lambda r: r.get(col) in set(vals))
        return self

    def range(self, a, b):
        return self

    def limit(self, n):
        return self

    def execute(self):
        rows = self.db.setdefault(self.table, [])
        treffer = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "select":
            return types.SimpleNamespace(data=copy.deepcopy(treffer))
        if self.op == "insert":
            neu = self.payload if isinstance(self.payload, list) else [self.payload]
            gespeichert = []
            for r in neu:
                assert r.get("user_id") == UID, "user_id fehlt beim Einfuegen"
                zeile = {"id": str(uuid.uuid4()), **r}
                rows.append(zeile)
                gespeichert.append(copy.deepcopy(zeile))
            self.db.setdefault("_log", []).append(("insert", self.table, neu))
            return types.SimpleNamespace(data=gespeichert)
        if self.op == "upsert":
            neu = self.payload if isinstance(self.payload, list) else [self.payload]
            for r in neu:
                vorhanden = [x for x in rows if all(x.get(c) == r.get(c) for c in self.konflikt)]
                if vorhanden:
                    vorhanden[0].update(r)
                else:
                    rows.append({"id": str(uuid.uuid4()), **r})
            self.db.setdefault("_log", []).append(("upsert", self.table, neu))
            return types.SimpleNamespace(data=neu)
        if self.op == "update":
            for r in treffer:
                r.update(self.payload)
            self.db.setdefault("_log", []).append(("update", self.table, self.payload))
            return types.SimpleNamespace(data=treffer)
        if self.op == "delete":
            self.db[self.table] = [r for r in rows if r not in treffer]
            self.db.setdefault("_log", []).append(("delete", self.table, len(treffer)))
            return types.SimpleNamespace(data=treffer)


class FakeMFA:
    def __init__(self, stufe):
        self.stufe = stufe

    def get_authenticator_assurance_level(self):
        return types.SimpleNamespace(current_level=self.stufe)

    def list_factors(self):
        return types.SimpleNamespace(all=[], totp=[], phone=[])

    def enroll(self, params):
        return types.SimpleNamespace(id="f1", totp=types.SimpleNamespace(
            qr_code="data:image/svg+xml;utf-8,<svg/>", secret="ABC", uri="otpauth://totp/x"))


class FakePostgrest:
    def __init__(self):
        self.token = None

    def auth(self, token):
        self.token = token


class FakeClient:
    def __init__(self, db, stufe="aal2"):
        self.db = db
        self.auth = types.SimpleNamespace(
            mfa=FakeMFA(stufe),
            get_session=lambda: types.SimpleNamespace(access_token="nutzer-token"))
        self.postgrest = FakePostgrest()
        self.options = types.SimpleNamespace(headers={"Authorization": "Bearer anon-key"})

    def table(self, name):
        return FakeQuery(self.db, name)


@pytest.fixture
def umgebung(monkeypatch):
    st = stub_streamlit.installieren()
    sb = types.ModuleType("supabase")
    sb.create_client = lambda *a, **k: None
    sb.ClientOptions = lambda **k: None
    monkeypatch.setitem(sys.modules, "supabase", sb)
    monkeypatch.syspath_prepend(str(ROOT))
    for m in [m for m in sys.modules if m in ("anmeldung", "forecast") or m.startswith("finanzen")]:
        del sys.modules[m]
    import anmeldung
    db = {}
    client = FakeClient(db)
    monkeypatch.setattr(anmeldung, "client", lambda: client)
    st.session_state["sb"] = client  # ueberlebt das importlib.reload in app.py
    st.session_state["auth"] = {"user_id": UID, "email": "t@example.org", "refresh_token": "r",
                                "expires_at": 9e12, "app_metadata": {"finanzen": True}}
    return types.SimpleNamespace(st=st, db=db, client=client)


def ausfuehren(seite):
    try:
        runpy.run_path(str(ROOT / "seiten" / seite), run_name="__main__")
    except (stub_streamlit.Stop, stub_streamlit.Rerun) as e:
        return type(e).__name__
    return "fertig"


def beispieldaten(db):
    """Auszug aus den echten Daten vom 04.11. im Konto/Topf-Modell."""
    def konto(kid, name, art, ertrag, bewertung="saldo", ausgezahlt=False, std=None, stufe="frei"):
        return {"id": kid, "user_id": UID, "name": name, "institut": None, "art": art, "ertragsart": ertrag,
                "risiko": 2, "standard_topf_id": std, "sortierung": 0, "aktiv": True, "notiz": None,
                "stufe": stufe, "abgleich_gruppe": None, "bewertung": bewertung, "wkn": None,
                "ertrag_ausgezahlt": ausgezahlt}

    def topf(tid, name, kid, stufe="fixkosten", ziel=None):
        return {"id": tid, "user_id": UID, "name": name, "art": "ruecklage", "zielbetrag": ziel,
                "sparrate_monat": None, "sortierung": 0, "aktiv": True, "notiz": None, "konto_id": kid, "stufe": stufe}

    db["fin_konten"] = [
        konto("lohn", "Volksbank Girokonto", "giro", "keine", std="t_lohn"),
        konto("vb", "Volksbank Fixkostenkonto", "giro", "keine", std="t_marge", stufe="fixkosten"),
        konto("aus", "T212 Ausgabentopf", "giro", "keine", std="t_kraft", stufe="fixkosten"),
        konto("bar", "Bargeld / Brieftasche", "bargeld", "keine", std="t_reserve"),
        konto("dbx", "T212 Geldmarktfonds (DBX0AN)", "geldmarktfonds", "zins", "anteile", std="t_ueb", stufe="fixkosten"),
        konto("lyx", "T212 Geldmarktfonds (LYX0WM)", "geldmarktfonds", "zins", "anteile", std="t_busse"),
        konto("bbva", "BBVA Tagesgeld", "tagesgeld", "zins", ausgezahlt=True, std="t_auto", stufe="ruecklage"),
        konto("tr", "Trade Republic Depot", "depot", "markt", std="t_tr", stufe="langfristig"),
    ]
    db["fin_toepfe"] = [
        topf("t_lohn", "Lohneingang", "lohn", "frei"),
        topf("t_marge", "Sicherheitsmarge", "vb"), topf("t_gym", "Gym", "vb"), topf("t_zzv", "ZZV", "vb"),
        topf("t_congstar", "Congstar + Spotify", "vb"),
        topf("t_kraft", "Kraftstoff", "aus"), topf("t_claude", "Claude", "aus"),
        topf("t_reserve", "Notreserve", "bar", "langfristig"), topf("t_friseur", "Friseur", "bar"),
        topf("t_kfz", "KFZ-Versicherung", "dbx"), topf("t_esn", "ESN", "dbx"), topf("t_zahn", "Zahnarzt", "dbx"),
        topf("t_ueb", "Überschuss & Zinsen", "dbx"),
        topf("t_busse", "Geldbuße", "lyx", "verbindlichkeit"), topf("t_straf", "Strafzettelrücklage", "lyx", "ruecklage"),
        topf("t_auto", "Autorücklagen", "bbva", "ruecklage", "2000"),
        topf("t_tr", "Altersvorsorge", "tr", "langfristig"),
    ]
    db["fin_zinssaetze"] = [
        {"id": f"z{i}", "user_id": UID, "konto_id": k, "gueltig_ab": "2026-11-01", "satz_pa": s, "notiz": None}
        for i, (k, s) in enumerate([("dbx", "2.5"), ("lyx", "2.5"), ("bbva", "3.5"), ("tr", "0")])
    ]
    db["fin_verbindlichkeiten"] = [
        {"id": "v1", "user_id": UID, "name": "Geldbuße", "betrag_gesamt": "1200", "rate_monat": "200",
         "topf_id": "t_busse", "erledigt": False, "notiz": None},
    ]
    ek_dbx, ek_lyx = 150.646, 110.12

    def bu(**k):
        return {"id": str(uuid.uuid4()), "user_id": UID, "kategorie": None, "beschreibung": None, "gruppe": None,
                "verbindlichkeit_id": None, "tank_id": None, "anteile": None, "typ": "eroeffnung",
                "datum": "2026-11-01", **k}
    db["fin_buchungen"] = [
        bu(konto_id="lohn", topf_id="t_lohn", betrag="116"),
        bu(konto_id="vb", topf_id="t_marge", betrag="18.64"), bu(konto_id="vb", topf_id="t_gym", betrag="30"),
        bu(konto_id="vb", topf_id="t_zzv", betrag="5"), bu(konto_id="vb", topf_id="t_congstar", betrag="22"),
        bu(konto_id="vb", topf_id="t_gym", betrag="-30", typ="ausgabe", datum="2026-11-04"),
        bu(konto_id="aus", topf_id="t_kraft", betrag="450"), bu(konto_id="aus", topf_id="t_claude", betrag="21"),
        bu(konto_id="aus", topf_id="t_kraft", betrag="-63.84", typ="ausgabe", datum="2026-11-02", tank_id="tx1"),
        bu(konto_id="bar", topf_id="t_reserve", betrag="35"), bu(konto_id="bar", topf_id="t_friseur", betrag="20"),
        bu(konto_id="dbx", topf_id="t_kfz", betrag="102", anteile=str(102 / ek_dbx)),
        bu(konto_id="dbx", topf_id="t_esn", betrag="5", anteile=str(5 / ek_dbx)),
        bu(konto_id="dbx", topf_id="t_zahn", betrag="64", anteile=str(64 / ek_dbx)),
        bu(konto_id="lyx", topf_id="t_busse", betrag="1200.07", anteile="10.89781838"),
        bu(konto_id="lyx", topf_id="t_straf", betrag="100.01", anteile="0.90818162"),
        bu(konto_id="bbva", topf_id="t_auto", betrag="1598.75"),
        bu(konto_id="tr", topf_id="t_tr", betrag="4144"),
    ]
    anteile_dbx = (102 + 5 + 64) / ek_dbx
    db["fin_kurse"] = [
        {"id": "k1", "user_id": UID, "konto_id": "dbx", "datum": "2026-11-01", "kurs": str(ek_dbx), "wert": None, "notiz": None},
        {"id": "k2", "user_id": UID, "konto_id": "lyx", "datum": "2026-11-01", "kurs": str(1303.34 / 11.806), "wert": None, "notiz": None},
    ]

    def fx(fid, name, betrag, topf_id, konto_id, modus="direkt", intervall=1, rate=None, faellig="2027-03-01",
           regel="rest_topf", rest=None, auto=False, prog=False):
        return {"id": fid, "user_id": UID, "name": name, "betrag": str(betrag), "intervall_monate": intervall,
                "naechste_faelligkeit": faellig, "konto_id": konto_id, "kategorie": "Abos & Verträge", "aktiv": True,
                "notiz": None, "topf_id": topf_id, "modus": modus, "rate_monat": rate, "rest_regel": regel,
                "rest_topf_id": rest, "auto_abbuchen": auto, "prognose_fuelbudget": prog}
    db["fin_fixkosten"] = [
        fx("f_gym", "Gym", 30, "t_gym", "vb", faellig="2026-12-01", rest="t_marge", auto=True),
        fx("f_zzv", "ZZV", 5, "t_zzv", "vb", faellig="2026-11-20", rest="t_marge"),
        fx("f_congstar", "Congstar + Spotify", 22, "t_congstar", "vb", faellig="2027-03-15", rest="t_marge", auto=True),
        fx("f_claude", "Claude", 21, "t_claude", "aus", faellig="2027-03-15", regel="verrechnen", auto=True),
        fx("f_kraft", "Kraftstoff", 450, "t_kraft", "aus", modus="budget", regel="verrechnen", prog=True, faellig="2027-03-01"),
        fx("f_friseur", "Friseur", 20, "t_friseur", "bar", faellig="2026-11-30", regel="verrechnen"),
        fx("f_kfz", "KFZ-Versicherung", 304, "t_kfz", "dbx", modus="ansparen", intervall=3, rate=102,
           faellig="2027-03-01", rest="t_ueb"),
        fx("f_esn", "ESN", 87, "t_esn", "dbx", modus="ansparen", intervall=3, rate=29, faellig="2027-03-01",
           regel="verrechnen", rest="t_ueb"),
        fx("f_zahn", "Zahnarzt", 96, "t_zahn", "dbx", modus="ansparen", intervall=6, rate=16, faellig="2026-12-01",
           rest="t_ueb"),
    ]
    db["fin_einstellungen"] = [{"user_id": UID, "tank_topf_id": "t_kraft", "lohn_topf_id": "t_lohn",
                                "lohn_assistent_seit": None, "tank_konto_id": None}]
    db["transactions"] = [
        {"id": f"tx{i}", "user_id": UID, "date": f"2026-{m:02d}-{t:02d}", "amount": 60.0}
        for i, (m, t) in enumerate([(mm, tt) for mm in range(6, 11) for tt in (3, 10, 17, 24)])
    ]
    db["fin_depot_wochen"] = [
        {"id": f"w{i}", "user_id": UID, "konto_id": "tr", "datum": f"2026-{8 + i // 4:02d}-{1 + (i % 4) * 7:02d}",
         "performance_pct": "0.2", "depotwert": None, "notiz": None}
        for i in range(11)
    ]
    return anteile_dbx


SEITEN = ["vermoegen.py", "buchungen.py", "verwaltung.py", "fixkosten.py", "depot.py"]


@pytest.mark.parametrize("seite", SEITEN)
def test_seiten_leer(umgebung, seite):
    assert ausfuehren(seite) in ("fertig", "Stop")


@pytest.mark.parametrize("seite", SEITEN)
def test_seiten_mit_daten(umgebung, seite):
    beispieldaten(umgebung.db)
    assert ausfuehren(seite) == "fertig"


def test_ohne_zweiten_faktor_kein_datenzugriff(umgebung):
    beispieldaten(umgebung.db)
    umgebung.client.auth.mfa.stufe = "aal1"
    assert ausfuehren("vermoegen.py") == "Stop"
    assert "mfa_enroll" in umgebung.st.session_state


def test_ohne_freischaltung(umgebung):
    umgebung.st.session_state["auth"]["app_metadata"] = {}
    assert ausfuehren("vermoegen.py") == "Stop"


def _letzter_insert(db, tabelle):
    eintraege = [e for e in db.get("_log", []) if e[0] in ("insert", "upsert") and e[1] == tabelle]
    return eintraege[-1][2] if eintraege else None


def _alle_inserts(db, tabelle):
    return [z for e in db.get("_log", []) if e[0] == "insert" and e[1] == tabelle for z in e[2]]


def _text_im_log(st, teil):
    return any(teil in str(a) for a, _ in st.LOG)


def test_ausgabe_auf_topf_bucht_konto_mit(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("einnahme_ausgabe_form")
    st.WERTE.update({"ea_topf": "t_auto", "Betrag (€)": 12.5, "Kategorie": "Auto"})
    assert ausfuehren("buchungen.py") == "Rerun"
    z = _letzter_insert(umgebung.db, "fin_buchungen")[0]
    assert (z["betrag"], z["konto_id"], z["topf_id"], z.get("anteile")) == (-12.5, "bbva", "t_auto", None)


def test_umbuchung_in_fonds_kauft_anteile(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("umbuchung_form")
    st.WERTE.update({"um_von": "t_lohn", "um_nach": "t_kfz", "um_betrag": 102.0, "um_datum": date(2026, 11, 1)})
    assert ausfuehren("buchungen.py") == "Rerun"
    zeilen = {z["topf_id"]: z for z in _letzter_insert(umgebung.db, "fin_buchungen")}
    assert zeilen["t_lohn"].get("anteile") is None and zeilen["t_lohn"]["betrag"] == -102
    assert zeilen["t_kfz"]["konto_id"] == "dbx"
    assert zeilen["t_kfz"]["anteile"] == pytest.approx(102 / 150.646, abs=1e-6)


def test_fonds_wert_uebernehmen_setzt_kurs(umgebung):
    anteile = beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("kurs_form")
    st.WERTE.update({"abgl_konto": "dbx", "kurs_wert_dbx": 171.10, "kurs_datum": date(2026, 11, 4)})
    assert ausfuehren("buchungen.py") == "Rerun"
    k = _letzter_insert(umgebung.db, "fin_kurse")[0]
    assert k["kurs"] == pytest.approx(171.10 / anteile, abs=1e-5) and k["datum"] == "2026-11-04"
    assert not _alle_inserts(umgebung.db, "fin_buchungen")   # Buchgewinn = keine Buchung


def test_abgleich_tagesgeld_bucht_zins_in_standardtopf(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("abgleich_form")
    st.WERTE.update({"abgl_konto": "bbva", "abgl_ist_bbva": 1603.35, "abgl_datum": date(2026, 11, 30)})
    assert ausfuehren("buchungen.py") == "Rerun"
    z = _letzter_insert(umgebung.db, "fin_buchungen")[0]
    assert (z["typ"], z["topf_id"], z["betrag"]) == ("zins", "t_auto", pytest.approx(4.60))


def test_loeschen_mit_auswahl(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.AUSWAHL_ZEILEN = [0]
    st.KNOEPFE.add("🗑️ Ausgewählte löschen")
    vorher = len(umgebung.db["fin_buchungen"])
    assert ausfuehren("buchungen.py") == "Rerun"
    assert len(umgebung.db["fin_buchungen"]) == vorher - 1


def test_startbestand_je_topf(umgebung, monkeypatch):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("startbestand_form")
    original = st.data_editor

    def editor(df, **k):
        df = original(df, **k)
        df.loc[df["id"] == "t_lohn", "Betrag (€)"] = 150.0
        return df
    monkeypatch.setattr(st, "data_editor", editor)
    assert ausfuehren("verwaltung.py") == "Rerun"
    lohn = [r for r in umgebung.db["fin_buchungen"] if r["typ"] == "eroeffnung" and r["topf_id"] == "t_lohn"]
    assert len(lohn) == 1 and float(lohn[0]["betrag"]) == 150 and lohn[0]["datum"] == "2026-11-01"


def test_konto_anlegen_mit_erstem_topf(umgebung):
    st = umgebung.st
    st.SUBMIT.add("konto_form_None")
    st.WERTE.update({"Name (Ort)": "C24 Tagesgeld", "Anbieter": "C24", "Name des ersten Topfes": "Notgroschen",
                     "erste_stufe": "ruecklage"})
    assert ausfuehren("verwaltung.py") == "Rerun"
    konto = umgebung.db["fin_konten"][0]
    topf = umgebung.db["fin_toepfe"][0]
    assert topf["konto_id"] == konto["id"] and topf["stufe"] == "ruecklage"
    assert konto["standard_topf_id"] == topf["id"]


def test_topf_anlegen_im_konto(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("topf_form_None")
    st.WERTE.update({"Konto": "vb", "Name (Zweck)": "Netflix", "Stufe": "fixkosten"})
    assert ausfuehren("verwaltung.py") == "Rerun"
    neu = _letzter_insert(umgebung.db, "fin_toepfe")[0]
    assert (neu["konto_id"], neu["stufe"], neu["name"]) == ("vb", "fixkosten", "Netflix")


def test_lohn_assistent_verteilt_und_verrechnet(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.KNOEPFE.update({"lohn_button", "lohn_buchen"})
    st.WERTE.update({"lohn_betrag": 1200.0, "lohn_datum": date(2026, 11, 30)})
    assert ausfuehren("fixkosten.py") == "Rerun"
    zeilen = _alle_inserts(umgebung.db, "fin_buchungen")
    lohn = [z for z in zeilen if z["typ"] == "einnahme"]
    assert len(lohn) == 1 and lohn[0]["betrag"] == 1200 and lohn[0]["topf_id"] == "t_lohn"
    ein = {z["topf_id"]: z["betrag"] for z in zeilen if z["typ"] == "umbuchung" and z["betrag"] > 0}
    assert ein["t_esn"] == pytest.approx(24)        # 5 € Rest verrechnet
    assert ein["t_kfz"] == pytest.approx(102)
    assert ein["t_zahn"] == pytest.approx(16)
    assert ein["t_gym"] == pytest.approx(30)
    assert ein["t_friseur"] == pytest.approx(20)    # Friseur: 20 € noch da, Dez braucht neue 20 €
    assert zeilen[[i for i, z in enumerate(zeilen) if z["topf_id"] == "t_kfz"][0]]["anteile"] > 0
    kraft = next(f for f in umgebung.db["fin_fixkosten"] if f["id"] == "f_kraft")
    prognose = float(kraft["betrag"])
    assert 0 < prognose < 450                          # Prognose uebernommen
    # aufgefuellt auf die Prognose (Rest 386,16 € wird verrechnet)
    assert ein.get("t_kraft", 0.0) == pytest.approx(max(prognose - 386.16, 0.0), abs=0.01)
    assert umgebung.db["fin_einstellungen"][0]["lohn_assistent_seit"] == "2026-11-30"


def test_bezahlt_mit_abweichendem_betrag_rest_in_marge(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("bezahlt_f_zzv")
    st.WERTE.update({"ist_f_zzv": 4.20, "bdat_f_zzv": date(2026, 11, 20)})
    assert ausfuehren("fixkosten.py") == "Rerun"
    zeilen = _letzter_insert(umgebung.db, "fin_buchungen")
    assert zeilen[0]["betrag"] == -4.2 and zeilen[0]["topf_id"] == "t_zzv"
    rest = {z["topf_id"]: z["betrag"] for z in zeilen[1:]}
    assert rest == {"t_zzv": -0.8, "t_marge": 0.8}
    zzv = next(f for f in umgebung.db["fin_fixkosten"] if f["id"] == "f_zzv")
    assert zzv["naechste_faelligkeit"] == "2026-12-20"


def test_lastschriften_werden_automatisch_gebucht(umgebung):
    beispieldaten(umgebung.db)
    claude = next(f for f in umgebung.db["fin_fixkosten"] if f["id"] == "f_claude")
    claude["naechste_faelligkeit"] = "2026-11-02"
    congstar = next(f for f in umgebung.db["fin_fixkosten"] if f["id"] == "f_congstar")
    congstar["naechste_faelligkeit"] = "2026-10-15"     # vor dem Startbestand (01.11.): nicht buchen
    assert ausfuehren("vermoegen.py") == "fertig"
    ausgaben = [z for z in _alle_inserts(umgebung.db, "fin_buchungen") if z["typ"] == "ausgabe"]
    assert [(z["topf_id"], z["betrag"], z["datum"]) for z in ausgaben] == [("t_claude", -21, "2026-11-02")]
    assert claude["naechste_faelligkeit"] == "2026-12-02"
    assert pd_ts(congstar["naechste_faelligkeit"]) > pd_ts("2026-11-01")
    # zweiter Aufruf am selben Tag bucht nicht erneut
    n = len(_alle_inserts(umgebung.db, "fin_buchungen"))
    ausfuehren("vermoegen.py")
    assert len(_alle_inserts(umgebung.db, "fin_buchungen")) == n


def pd_ts(x):
    import pandas as pd
    return pd.Timestamp(x)


def test_zahnarzt_warnung(umgebung):
    beispieldaten(umgebung.db)
    assert ausfuehren("fixkosten.py") == "fertig"
    assert _text_im_log(umgebung.st, "Zahnarzt")


def test_fixkosten_position_mit_neuem_topf(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("fix_form_None")
    st.WERTE.update({"Name": "Netflix", "Rechnungsbetrag (€)": 13.99, "Topf": "__neu__",
                     "Konto für den neuen Topf": "aus", "Nächste Fälligkeit": date(2026, 11, 20)})
    assert ausfuehren("fixkosten.py") == "Rerun"
    topf = umgebung.db["fin_toepfe"][-1]
    f = umgebung.db["fin_fixkosten"][-1]
    assert topf["name"] == "Netflix" and topf["konto_id"] == "aus"
    assert f["konto_id"] == "aus" and f["topf_id"] == topf["id"]


def test_verknuepfungen_setzen(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("verkn_form")
    st.WERTE.update({"Tankungen buchen auf": "t_claude", "Lohn geht ein auf": "t_lohn"})
    assert ausfuehren("verwaltung.py") == "Rerun"
    assert umgebung.db["fin_einstellungen"][0]["tank_topf_id"] == "t_claude"


def test_depot_woche_ab_12_wochen_rendite_uebernehmen(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("depot_woche_form")
    st.WERTE.update({"Datum": date(2026, 11, 6), "Wochenperformance (%)": 0.2,
                     "Depotwert gesamt (€, optional)": 4200.0})
    assert ausfuehren("depot.py") == "Rerun"
    abgl = _letzter_insert(umgebung.db, "fin_buchungen")[0]
    assert abgl["konto_id"] == "tr" and abgl["typ"] == "abgleich" and abgl["betrag"] == pytest.approx(56)
    satz = [z for z in umgebung.db["fin_zinssaetze"] if z["konto_id"] == "tr" and z["gueltig_ab"] == "2026-11-06"]
    assert len(satz) == 1 and satz[0]["satz_pa"] == pytest.approx((1.002 ** 52 - 1) * 100, abs=1e-3)


def test_depot_wert_vor_startbestand_wird_nicht_gebucht(umgebung):
    """Regression 01.11.: Wocheneintrag am 30.10. (vor Startbestand 01.11.) mit Depotwert."""
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("depot_woche_form")
    st.WERTE.update({"Datum": date(2026, 10, 30), "Wochenperformance (%)": -0.28,
                     "Depotwert gesamt (€, optional)": 4144.0})
    assert ausfuehren("depot.py") == "Rerun"
    assert not _alle_inserts(umgebung.db, "fin_buchungen")
    assert "vor dem Startbestand" in st.session_state["flash"]


def test_datenpruefung_bereinigt_buchung_vor_startbestand(umgebung):
    beispieldaten(umgebung.db)
    umgebung.db["fin_buchungen"].append({
        "id": "falsch", "user_id": UID, "datum": "2026-10-30", "konto_id": "tr", "topf_id": "t_tr",
        "betrag": "4144", "typ": "abgleich", "kategorie": None, "beschreibung": None, "gruppe": None,
        "verbindlichkeit_id": None, "tank_id": None, "anteile": None})
    st = umgebung.st
    st.KNOEPFE.add("datencheck_bereinigen")
    assert ausfuehren("vermoegen.py") == "Rerun"
    assert not [r for r in umgebung.db["fin_buchungen"] if r["id"] == "falsch"]


def test_zinssatz_anlegen(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("zins_form")
    st.WERTE.update({"Satz % p. a.": 2.0, "Gültig ab": date(2026, 12, 1)})
    assert ausfuehren("verwaltung.py") == "Rerun"
    z = _letzter_insert(umgebung.db, "fin_zinssaetze")[0]
    assert z["satz_pa"] == 2.0 and z["gueltig_ab"] == "2026-12-01"


def _tankungen(db):
    db["transactions"] = [
        {"id": str(uuid.uuid4()), "user_id": UID, "date": f"2026-{m:02d}-{t:02d}", "amount": 50 + t,
         "km_stand": 100000 + m * 1500 + t * 40, "preis_pro_liter": 1.9, "location": None}
        for m in range(3, 11) for t in (3, 12, 24)
    ]
    db["oil_change_settings"] = [{"id": 1, "user_id": UID, "interval_km": 15000, "last_oil_change_km": 104000}]


def _app_starten(umgebung, monkeypatch, ziel=None):
    import anmeldung
    monkeypatch.setattr(anmeldung, "aktueller_nutzer", lambda: umgebung.st.session_state["auth"])
    monkeypatch.setattr(anmeldung, "link_weiterleitung", lambda: None)
    monkeypatch.setattr(anmeldung, "cookie_aktualisieren", lambda: None)
    umgebung.st.NAV_ZIEL = ziel
    monkeypatch.chdir(ROOT)
    try:
        runpy.run_path(str(ROOT / "app.py"), run_name="__main__")
    except (stub_streamlit.Stop, stub_streamlit.Rerun) as e:
        return type(e).__name__
    return "fertig"


def test_app_mit_freischaltung_startet_auf_vermoegen(umgebung, monkeypatch):
    beispieldaten(umgebung.db)
    assert _app_starten(umgebung, monkeypatch) == "fertig"
    titel = [p.title for p in umgebung.st.NAV]
    assert titel == ["Vermögen", "Buchungen", "Fixkosten", "Depot-Rendite", "Konten & Töpfe", "FuelBudget"]


def test_app_ohne_freischaltung_nur_fuelbudget(umgebung, monkeypatch):
    _tankungen(umgebung.db)
    umgebung.st.session_state["auth"]["app_metadata"] = {}
    assert _app_starten(umgebung, monkeypatch) == "fertig"
    assert [p.title for p in umgebung.st.NAV] == ["FuelBudget"]


def test_fuelbudget_seite_laeuft(umgebung, monkeypatch):
    _tankungen(umgebung.db)
    assert _app_starten(umgebung, monkeypatch, ziel="fuelbudget.py") == "fertig"


def test_finanzseite_setzt_nutzer_token(umgebung):
    """Regression: supabase-py setzte nach dem 2FA-Code den anon-Key (401)."""
    beispieldaten(umgebung.db)
    assert ausfuehren("vermoegen.py") == "fertig"
    assert umgebung.client.postgrest.token == "nutzer-token"
    assert umgebung.client.options.headers["Authorization"] == "Bearer nutzer-token"


def test_fehlertext_gibt_startbestand_regel_weiter(umgebung):
    from finanzen import daten
    exc = Exception("{'code': 'P0001'}")
    exc.message = "Die Buchung vom 30.10.2026 liegt vor dem Startbestand dieses Kontos (01.11.2026)."
    assert daten.fehlertext(exc) == exc.message
