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
            for r in neu:
                assert r.get("user_id") == UID, "user_id fehlt beim Einfuegen"
                rows.append({"id": str(uuid.uuid4()), **r})
            self.db.setdefault("_log", []).append(("insert", self.table, neu))
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
    db["fin_toepfe"] = [
        {"id": "t_auto", "user_id": UID, "name": "Autopuffer", "art": "puffer", "zielbetrag": "1600",
         "sparrate_monat": None, "sortierung": 0, "aktiv": True, "notiz": None},
        {"id": "t_busse", "user_id": UID, "name": "Geldbuße", "art": "ruecklage", "zielbetrag": None,
         "sparrate_monat": None, "sortierung": 1, "aktiv": True, "notiz": None},
        {"id": "t_vers", "user_id": UID, "name": "Versicherung", "art": "ruecklage", "zielbetrag": "300",
         "sparrate_monat": "50", "sortierung": 2, "aktiv": True, "notiz": None},
    ]
    db["fin_konten"] = [
        {"id": "k_tr", "user_id": UID, "name": "TR Depot", "institut": "Trade Republic", "art": "depot",
         "ertragsart": "markt", "risiko": 3, "standard_topf_id": None, "sortierung": 0, "aktiv": True, "notiz": None},
        {"id": "k_mmf", "user_id": UID, "name": "T212 Geldmarkt", "institut": "Trading 212", "art": "geldmarktfonds",
         "ertragsart": "zins", "risiko": 2, "standard_topf_id": None, "sortierung": 1, "aktiv": True, "notiz": None},
        {"id": "k_bbva", "user_id": UID, "name": "BBVA", "institut": "BBVA", "art": "tagesgeld",
         "ertragsart": "zins", "risiko": 1, "standard_topf_id": "t_auto", "sortierung": 2, "aktiv": True, "notiz": None},
        {"id": "k_mone", "user_id": UID, "name": "Monefit", "institut": "Monefit", "art": "p2p",
         "ertragsart": "zins", "risiko": 5, "standard_topf_id": None, "sortierung": 3, "aktiv": True, "notiz": None},
    ]
    db["fin_zinssaetze"] = [
        {"id": "z1", "user_id": UID, "konto_id": "k_mmf", "gueltig_ab": "2026-10-01", "satz_pa": "2.10", "notiz": None},
        {"id": "z2", "user_id": UID, "konto_id": "k_mone", "gueltig_ab": "2026-10-01", "satz_pa": "7.5", "notiz": None},
        {"id": "z3", "user_id": UID, "konto_id": "k_tr", "gueltig_ab": "2026-10-01", "satz_pa": "7", "notiz": None},
    ]
    db["fin_verbindlichkeiten"] = [
        {"id": "v1", "user_id": UID, "name": "Geldbuße", "betrag_gesamt": "1200", "rate_monat": "200",
         "topf_id": "t_busse", "erledigt": False, "notiz": None},
    ]
    b = lambda **k: {"id": str(uuid.uuid4()), "user_id": UID, "topf_id": None, "kategorie": None,
                     "beschreibung": None, "gruppe": None, "verbindlichkeit_id": None, **k}
    db["fin_buchungen"] = [
        b(datum="2026-10-01", konto_id="k_tr", betrag="4000", typ="eroeffnung"),
        b(datum="2026-10-01", konto_id="k_mmf", topf_id="t_busse", betrag="1200", typ="eroeffnung"),
        b(datum="2026-10-01", konto_id="k_mmf", topf_id="t_vers", betrag="100", typ="eroeffnung"),
        b(datum="2026-10-01", konto_id="k_mmf", betrag="100", typ="eroeffnung"),
        b(datum="2026-10-01", konto_id="k_bbva", topf_id="t_auto", betrag="1600", typ="eroeffnung"),
        b(datum="2026-10-01", konto_id="k_mone", betrag="400", typ="eroeffnung"),
        b(datum="2026-10-15", konto_id="k_mmf", topf_id="t_busse", betrag="-200", typ="ausgabe",
          verbindlichkeit_id="v1", kategorie="Sonstiges"),
        b(datum="2026-10-20", konto_id="k_bbva", topf_id="t_auto", betrag="-89.90", typ="ausgabe", kategorie="Auto"),
    ]


SEITEN = ["vermoegen.py", "buchungen.py", "verwaltung.py"]


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


def test_ausgabe_speichern(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("einnahme_ausgabe_form")
    st.WERTE.update({"Betrag (€)": 12.5, "Kategorie": "Lebensmittel"})
    assert ausfuehren("buchungen.py") == "Rerun"
    zeile = _letzter_insert(umgebung.db, "fin_buchungen")[0]
    assert zeile["betrag"] == -12.5 and zeile["typ"] == "ausgabe" and zeile["konto_id"] == "k_tr"


def test_umbuchung_speichern(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("umbuchung_form")
    st.WERTE.update({"um_von_konto": "k_mmf", "um_nach_konto": "k_mmf", "um_von_topf": None,
                     "um_nach_topf": "t_vers", "um_betrag": 50.0})
    assert ausfuehren("buchungen.py") == "Rerun"
    zeilen = _letzter_insert(umgebung.db, "fin_buchungen")
    assert sorted(z["betrag"] for z in zeilen) == [-50, 50]


def test_abgleich_bucht_zinsen(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("abgleich_form")
    st.WERTE.update({"abgl_konto": "k_mmf", "abgl_ist_k_mmf": 1203.10, "abgl_datum": date(2026, 10, 31)})
    assert ausfuehren("buchungen.py") == "Rerun"
    zeile = _letzter_insert(umgebung.db, "fin_buchungen")
    assert zeile[0]["typ"] == "zins" and zeile[0]["betrag"] == pytest.approx(3.10)


def test_loeschen_mit_auswahl(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.AUSWAHL_ZEILEN = [0]
    st.KNOEPFE.add("🗑️ Ausgewählte löschen")
    vorher = len(umgebung.db["fin_buchungen"])
    assert ausfuehren("buchungen.py") == "Rerun"
    assert len(umgebung.db["fin_buchungen"]) == vorher - 1


def test_startbestand_ersetzen(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("startbestand_form_k_tr")
    assert ausfuehren("verwaltung.py") == "Rerun"
    eroeffnung_tr = [r for r in umgebung.db["fin_buchungen"]
                     if r["konto_id"] == "k_tr" and r["typ"] == "eroeffnung"]
    assert len(eroeffnung_tr) == 1 and float(eroeffnung_tr[0]["betrag"]) == 4000


def test_konto_und_topf_anlegen(umgebung):
    st = umgebung.st
    st.SUBMIT.update({"topf_form_None"})
    st.WERTE.update({"Name": "Notgroschen"})
    assert ausfuehren("verwaltung.py") == "Rerun"
    assert umgebung.db["fin_toepfe"][0]["name"] == "Notgroschen"
    st.SUBMIT.clear()
    st.SUBMIT.add("konto_form_None")
    st.WERTE.update({"Name": "Sparkasse Giro", "Anbieter": "Sparkasse"})
    for k in [k for k in list(st.session_state) if str(k).startswith("_fin_cache_")]:
        del st.session_state[k]
    assert ausfuehren("verwaltung.py") == "Rerun"
    assert umgebung.db["fin_konten"][0]["institut"] == "Sparkasse"


def test_zinssatz_anlegen(umgebung):
    beispieldaten(umgebung.db)
    st = umgebung.st
    st.SUBMIT.add("zins_form")
    st.WERTE.update({"Satz % p. a.": 2.0, "Gültig ab": date(2026, 11, 1)})
    assert ausfuehren("verwaltung.py") == "Rerun"
    z = _letzter_insert(umgebung.db, "fin_zinssaetze")[0]
    assert z["satz_pa"] == 2.0 and z["gueltig_ab"] == "2026-11-01"


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
    assert titel == ["Vermögen", "Buchungen", "Konten & Töpfe", "FuelBudget"]


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
