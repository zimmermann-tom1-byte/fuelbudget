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
    def topf(tid, name, ziel=None):
        return {"id": tid, "user_id": UID, "name": name, "art": "ruecklage", "zielbetrag": ziel,
                "sparrate_monat": None, "sortierung": 0, "aktiv": True, "notiz": None}

    def posten(pid, name, art, ertrag, stufe, gruppe=None, institut="Trading 212"):
        return {"id": pid, "user_id": UID, "name": name, "institut": institut, "art": art, "ertragsart": ertrag,
                "risiko": 2, "standard_topf_id": "t_" + pid, "sortierung": 0, "aktiv": True, "notiz": None,
                "stufe": stufe, "abgleich_gruppe": gruppe}

    db["fin_toepfe"] = [topf("t_lohn", "Lohneingangskonto"), topf("t_fix", "Fixkosten monatlich"),
                        topf("t_fix36", "Fixkosten 3-6 Monate"), topf("t_auto", "Autorücklagen"),
                        topf("t_straf", "Strafzettelrücklage"), topf("t_busse", "Geldbuße"),
                        topf("t_tr", "Altersvorsorge", "10000"), topf("t_trcash", "Trade Republic Cash", "800")]
    db["fin_konten"] = [
        posten("lohn", "Lohneingangskonto", "giro", "keine", "frei", institut="Volksbank"),
        posten("fix", "Fixkosten monatlich", "giro", "keine", "fixkosten", institut="Volksbank"),
        posten("fix36", "Fixkosten 3-6 Monate", "geldmarktfonds", "zins", "fixkosten"),
        posten("auto", "Autorücklagen", "tagesgeld", "zins", "ruecklage", institut="BBVA"),
        posten("straf", "Strafzettelrücklage", "geldmarktfonds", "zins", "ruecklage", "T212 MMF Rücklagen"),
        posten("busse", "Geldbuße", "geldmarktfonds", "zins", "verbindlichkeit", "T212 MMF Rücklagen"),
        posten("tr", "Altersvorsorge", "depot", "markt", "langfristig", institut="Trade Republic"),
        posten("trcash", "Trade Republic Cash", "tagesgeld", "zins", "langfristig", institut="Trade Republic"),
    ]
    db["fin_zinssaetze"] = [
        {"id": f"z{i}", "user_id": UID, "konto_id": k, "gueltig_ab": "2026-11-01", "satz_pa": s, "notiz": None}
        for i, (k, s) in enumerate([("fix36", "2.5"), ("auto", "3.5"), ("straf", "2.5"), ("busse", "2.5"),
                                    ("tr", "0"), ("trcash", "2.5")])
    ]
    db["fin_verbindlichkeiten"] = [
        {"id": "v1", "user_id": UID, "name": "Geldbuße", "betrag_gesamt": "1200", "rate_monat": "200",
         "topf_id": "t_busse", "erledigt": False, "notiz": None},
    ]

    def bu(**k):
        return {"id": str(uuid.uuid4()), "user_id": UID, "topf_id": "t_" + k["konto_id"], "kategorie": None,
                "beschreibung": None, "gruppe": None, "verbindlichkeit_id": None, "typ": "eroeffnung", **k}
    db["fin_buchungen"] = [
        bu(datum="2026-11-01", konto_id="lohn", betrag="116"),
        bu(datum="2026-11-01", konto_id="fix", betrag="583"),
        bu(datum="2026-11-01", konto_id="fix36", betrag="210.04"),
        bu(datum="2026-11-01", konto_id="auto", betrag="1598.75"),
        bu(datum="2026-11-01", konto_id="straf", betrag="100.26"),
        bu(datum="2026-11-01", konto_id="busse", betrag="1203.08"),
        bu(datum="2026-11-01", konto_id="tr", betrag="4144"),
        bu(datum="2026-11-01", konto_id="trcash", betrag="744"),
        bu(datum="2026-11-02", konto_id="auto", betrag="-89.90", typ="ausgabe", kategorie="Auto"),
    ]
    db["fin_fixkosten"] = [
        {"id": "f1", "user_id": UID, "name": "Gym", "betrag": "35", "intervall_monate": 1,
         "naechste_faelligkeit": "2026-11-15", "konto_id": "fix", "kategorie": "Freizeit", "aktiv": True, "notiz": None},
        {"id": "f2", "user_id": UID, "name": "Kfz-Versicherung", "betrag": "240", "intervall_monate": 3,
         "naechste_faelligkeit": "2027-01-01", "konto_id": "fix36", "kategorie": "Versicherungen", "aktiv": True,
         "notiz": None},
    ]
    db["fin_depot_wochen"] = [
        {"id": f"w{i}", "user_id": UID, "konto_id": "tr", "datum": f"2026-{8 + i // 4:02d}-{1 + (i % 4) * 7:02d}",
         "performance_pct": "0.2", "depotwert": None, "notiz": None}
        for i in range(11)
    ]


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
    assert "mfa_enroll" in umgebung.st.session_state  # Einrichtung wurde gestartet


def test_ohne_freischaltung(umgebung):
    umgebung.st.session_state["auth"]["app_metadata"] = {}
    assert ausfuehren("vermoegen.py") == "Stop"


def _letzter_insert(db, tabelle):
    eintraege = [e for e in db.get("_log", []) if e[0] == "insert" and e[1] == tabelle]
    return eintraege[-1][2] if eintraege else None


def _cache_leeren(st):
    for k in [k for k in list(st.session_state) if str(k).startswith("_fin_cache_")]:
        del st.session_state[k]


def test_ausgabe_speichern_bucht_auf_posten_und_topf(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("einnahme_ausgabe_form")
    st.WERTE.update({"ea_posten": "auto", "Betrag (€)": 12.5, "Kategorie": "Auto"})
    assert ausfuehren("buchungen.py") == "Rerun"
    zeile = _letzter_insert(umgebung.db, "fin_buchungen")[0]
    assert zeile["betrag"] == -12.5 and zeile["konto_id"] == "auto" and zeile["topf_id"] == "t_auto"


def test_umbuchung_zwischen_posten(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("umbuchung_form")
    st.WERTE.update({"um_von": "lohn", "um_nach": "fix36", "um_betrag": 80.0})
    assert ausfuehren("buchungen.py") == "Rerun"
    zeilen = {z["konto_id"]: z for z in _letzter_insert(umgebung.db, "fin_buchungen")}
    assert zeilen["lohn"]["betrag"] == -80 and zeilen["fix36"]["betrag"] == 80
    assert zeilen["fix36"]["topf_id"] == "t_fix36"


def test_gruppenabgleich_geldmarktfonds(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("abgleich_form")
    einheit = "gruppe:T212 MMF Rücklagen"
    st.WERTE.update({"abgl_einheit": einheit, f"abgl_ist_{einheit}": 1305.34, "abgl_datum": date(2026, 11, 30)})
    assert ausfuehren("buchungen.py") == "Rerun"
    zeilen = _letzter_insert(umgebung.db, "fin_buchungen")
    assert {z["konto_id"] for z in zeilen} == {"busse", "straf"}
    assert sum(z["betrag"] for z in zeilen) == pytest.approx(2.0)


def test_loeschen_mit_auswahl(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.AUSWAHL_ZEILEN = [0]
    st.KNOEPFE.add("🗑️ Ausgewählte löschen")
    vorher = len(umgebung.db["fin_buchungen"])
    assert ausfuehren("buchungen.py") == "Rerun"
    assert len(umgebung.db["fin_buchungen"]) == vorher - 1


def test_startbestand_nur_geaenderte_posten(umgebung, monkeypatch):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("startbestand_form")
    st.WERTE["Stichtag"] = date(2026, 11, 1)
    original = st.data_editor

    def editor(df, **k):
        df = original(df, **k)
        df.loc[df["id"] == "lohn", "Betrag (€)"] = 150.0
        return df
    monkeypatch.setattr(st, "data_editor", editor)
    assert ausfuehren("verwaltung.py") == "Rerun"
    eroeffnung = [r for r in umgebung.db["fin_buchungen"] if r["typ"] == "eroeffnung"]
    lohn = [r for r in eroeffnung if r["konto_id"] == "lohn"]
    assert len(lohn) == 1 and float(lohn[0]["betrag"]) == 150 and lohn[0]["topf_id"] == "t_lohn"
    assert len(eroeffnung) == 8  # die anderen bleiben unveraendert


def test_posten_anlegen_legt_topf_mit_an(umgebung):
    st = umgebung.st
    st.SUBMIT.add("posten_form_None")
    st.WERTE.update({"Name": "Notgroschen", "Anbieter": "Volksbank", "Stufe": "ruecklage",
                     "Zielbetrag (€, optional)": 1000.0})
    assert ausfuehren("verwaltung.py") == "Rerun"
    konto = umgebung.db["fin_konten"][0]
    topf = umgebung.db["fin_toepfe"][0]
    assert konto["standard_topf_id"] == topf["id"] and konto["stufe"] == "ruecklage"
    assert topf["name"] == "Notgroschen" and topf["zielbetrag"] == 1000 and topf["art"] == "puffer"


def test_posten_aendern_haelt_topf_synchron(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("posten_form_auto")
    st.WERTE.update({"posten_auswahl": "auto", "Name": "Auto-Puffer", "Zielbetrag (€, optional)": 2000.0})
    assert ausfuehren("verwaltung.py") == "Rerun"
    topf = next(t for t in umgebung.db["fin_toepfe"] if t["id"] == "t_auto")
    assert topf["name"] == "Auto-Puffer" and topf["zielbetrag"] == 2000


def test_fixkosten_anlegen(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("fix_form_None")
    st.WERTE.update({"Name": "Kontaktlinsen", "Betrag (€)": 120.0, "Intervall": 6,
                     "Nächste Fälligkeit": date(2027, 3, 1), "Bezahlt aus Posten": "fix36"})
    assert ausfuehren("fixkosten.py") == "Rerun"
    f = _letzter_insert(umgebung.db, "fin_fixkosten")[0]
    assert f["intervall_monate"] == 6 and f["naechste_faelligkeit"] == "2027-03-01" and f["konto_id"] == "fix36"


def test_fixkosten_als_bezahlt_buchen(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.KNOEPFE.add("bezahlt_f1")
    assert ausfuehren("fixkosten.py") == "Rerun"
    zeile = _letzter_insert(umgebung.db, "fin_buchungen")[0]
    assert zeile["betrag"] == -35 and zeile["konto_id"] == "fix" and zeile["beschreibung"] == "Gym"
    gym = next(f for f in umgebung.db["fin_fixkosten"] if f["id"] == "f1")
    assert gym["naechste_faelligkeit"] == "2026-12-15"


def test_depot_woche_ab_12_wochen_rendite_uebernehmen(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("depot_woche_form")
    st.WERTE.update({"Datum": date(2026, 11, 6), "Wochenperformance (%)": 0.2,
                     "Depotwert gesamt (€, optional)": 4200.0})
    assert ausfuehren("depot.py") == "Rerun"
    # Depotwert -> Abgleichsbuchung
    abgl = _letzter_insert(umgebung.db, "fin_buchungen")[0]
    assert abgl["konto_id"] == "tr" and abgl["typ"] == "abgleich" and abgl["betrag"] == pytest.approx(56)
    # 12. Woche -> Rendite als Satz uebernommen (0,2 %/Woche = 10,95 % p. a.)
    satz = [z for z in umgebung.db["fin_zinssaetze"] if z["konto_id"] == "tr" and z["gueltig_ab"] == "2026-11-06"]
    assert len(satz) == 1 and satz[0]["satz_pa"] == pytest.approx((1.002 ** 52 - 1) * 100, abs=1e-3)


def test_depot_unter_12_wochen_keine_uebernahme(umgebung):
    beispieldaten(umgebung.db)
    umgebung.db["fin_depot_wochen"] = umgebung.db["fin_depot_wochen"][:5]
    st = umgebung.st
    st.SUBMIT.add("depot_woche_form")
    st.WERTE.update({"Datum": date(2026, 11, 6), "Wochenperformance (%)": 1.0})
    assert ausfuehren("depot.py") == "Rerun"
    assert not [e for e in umgebung.db.get("_log", []) if e[0] == "upsert"]
    assert not [e for e in umgebung.db.get("_log", []) if e[1] == "fin_buchungen"]


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
    assert titel == ["Vermögen", "Buchungen", "Fixkosten", "Depot-Rendite", "Posten", "FuelBudget"]


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
