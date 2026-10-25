"""Anmeldung fuer FuelBudget ueber Supabase Auth.

- Jede Browser-Sitzung bekommt ihren EIGENEN Supabase-Client. Abfragen laufen
  mit dem Login des jeweiligen Nutzers, die Datenbank (Row Level Security)
  gibt dadurch nur dessen eigene Zeilen heraus.
- "Angemeldet bleiben": Der Refresh-Token liegt in einem Browser-Cookie. Beim
  naechsten Oeffnen meldet die App damit im Hintergrund wieder an. Klappt das
  nicht (z. B. Safari-Einschraenkungen), erscheint einfach die Login-Maske.
- Einladungs- und Passwort-Links kommen als ?token_hash=...&type=... an
  (E-Mail-Vorlagen in Supabase entsprechend angepasst), weil Streamlit den
  Teil hinter einer # nicht lesen kann.
"""

from __future__ import annotations

import time
from urllib.parse import quote, unquote

import streamlit as st
import streamlit.components.v1 as components
from supabase import create_client

try:
    from supabase import ClientOptions
except ImportError:  # aeltere/neuere Paketstruktur
    from supabase.lib.client_options import ClientOptions

COOKIE = "fuelbudget_session"
COOKIE_TAGE = 30
MIN_PASSWORT = 8
_LINK_TYPEN = {"invite", "recovery", "magiclink", "signup", "email"}


# --- Client & Sitzung --------------------------------------------------------

def _neuer_client():
    url, key = st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"]
    try:
        # Token-Erneuerung steuern wir selbst (siehe _token_frisch_halten),
        # damit der Cookie immer den aktuellen Refresh-Token enthaelt.
        return create_client(url, key, options=ClientOptions(auto_refresh_token=False, persist_session=False))
    except TypeError:
        return create_client(url, key)


def client():
    """Supabase-Client dieser Browser-Sitzung (nie zwischen Nutzern geteilt)."""
    if "sb" not in st.session_state:
        st.session_state["sb"] = _neuer_client()
    return st.session_state["sb"]


def _uebernehmen(session) -> None:
    if session is None:
        raise ValueError("Keine Sitzung erhalten")
    st.session_state["auth"] = {
        "user_id": str(session.user.id),
        "email": session.user.email,
        "refresh_token": session.refresh_token,
        "expires_at": float(session.expires_at or time.time() + 3600),
    }


def _abmelden_lokal() -> None:
    for key in list(st.session_state.keys()):
        if key not in ("sb",):
            del st.session_state[key]
    st.session_state["sb"] = _neuer_client()
    st.session_state["abgemeldet"] = True


def _token_frisch_halten() -> bool:
    auth = st.session_state.get("auth")
    if not auth:
        return False
    if time.time() < auth["expires_at"] - 120:
        return True
    try:
        res = client().auth.refresh_session(auth["refresh_token"])
        _uebernehmen(res.session)
        return True
    except Exception:
        _abmelden_lokal()
        return False


# --- Cookie ------------------------------------------------------------------

def _cookie_lesen() -> str | None:
    try:
        wert = st.context.cookies.get(COOKIE)
    except Exception:
        return None
    return unquote(wert) if wert else None


def _cookie_js(wert: str | None) -> None:
    """Setzt bzw. loescht den Cookie per JavaScript. Die Komponente wird nur
    neu ausgefuehrt, wenn sich ihr Inhalt (also der Token) aendert."""
    if wert:
        cookie = f"{COOKIE}={quote(wert)}; Max-Age={COOKIE_TAGE * 86400}; Path=/; SameSite=Lax; Secure"
    else:
        cookie = f"{COOKIE}=; Max-Age=0; Path=/; SameSite=Lax; Secure"
    components.html(
        f"<script>try {{ window.parent.document.cookie = {cookie!r}; }} catch (e) {{}}</script>",
        height=0,
    )


# --- Einstieg ----------------------------------------------------------------

def aktueller_nutzer() -> dict | None:
    """Gibt {"user_id", "email"} zurueck oder None, wenn niemand angemeldet ist."""
    s = st.session_state

    # 1) Link aus Einladungs- oder Passwort-Mail
    qp = st.query_params
    if "token_hash" in qp and qp.get("type") in _LINK_TYPEN:
        token_hash, typ = qp.get("token_hash"), qp.get("type")
        st.query_params.clear()
        try:
            res = client().auth.verify_otp({"token_hash": token_hash, "type": typ})
            _uebernehmen(res.session)
            s.pop("abgemeldet", None)
            if typ in ("invite", "recovery"):
                s["passwort_setzen"] = typ
        except Exception:
            s["link_fehler"] = True

    # 2) Bereits angemeldet in dieser Sitzung
    if s.get("auth") and _token_frisch_halten():
        return s["auth"]

    # 3) Gespeicherte Anmeldung aus dem Cookie (einmal pro Sitzung versuchen)
    if not s.get("abgemeldet") and not s.get("cookie_versucht"):
        s["cookie_versucht"] = True
        token = _cookie_lesen()
        if token:
            try:
                res = client().auth.refresh_session(token)
                _uebernehmen(res.session)
                return s["auth"]
            except Exception:
                pass
    return None


def cookie_aktualisieren() -> None:
    """In jedem Lauf mit angemeldetem Nutzer aufrufen."""
    auth = st.session_state.get("auth")
    if auth:
        _cookie_js(auth["refresh_token"])


def abmelden() -> None:
    try:
        client().auth.sign_out({"scope": "local"})
    except Exception:
        pass
    _abmelden_lokal()
    st.rerun()


# --- Oberflaechen ------------------------------------------------------------

def login_seite() -> None:
    """Login-Maske inkl. "Passwort vergessen". Beendet den Skriptlauf."""
    if st.session_state.get("abgemeldet"):
        _cookie_js(None)

    _, mitte, _ = st.columns([1, 2, 1])
    with mitte:
        st.title("FuelBudget 🚗⛽")
        st.caption("Bitte melde dich an.")

        if st.session_state.pop("link_fehler", False):
            st.error(
                "Der Link aus der E-Mail ist ungültig oder abgelaufen. "
                "Fordere unten über „Passwort vergessen“ einen neuen an."
            )
        if "info" in st.session_state:
            st.success(st.session_state.pop("info"))

        with st.form("login_form"):
            email = st.text_input("E-Mail", autocomplete="username")
            passwort = st.text_input("Passwort", type="password", autocomplete="current-password")
            login = st.form_submit_button("Anmelden", type="primary", width="stretch")

        if login:
            erfolg = False
            try:
                res = client().auth.sign_in_with_password({"email": email.strip(), "password": passwort})
                _uebernehmen(res.session)
                erfolg = True
            except Exception:
                st.error("Anmeldung fehlgeschlagen. Bitte E-Mail und Passwort prüfen.")
            if erfolg:
                for key in ("abgemeldet", "cookie_versucht"):
                    st.session_state.pop(key, None)
                st.rerun()

        with st.expander("Passwort vergessen?"):
            with st.form("reset_form"):
                reset_email = st.text_input("E-Mail", key="reset_email", autocomplete="username")
                reset = st.form_submit_button("Link zum Zurücksetzen senden")
            if reset and reset_email.strip():
                try:
                    client().auth.reset_password_for_email(reset_email.strip())
                except Exception:
                    pass  # bewusst keine Auskunft, ob die Adresse existiert
                st.success(
                    "Falls ein Konto mit dieser Adresse existiert, ist eine E-Mail unterwegs. "
                    "Bitte auch im Spam-Ordner nachsehen."
                )

        st.caption("Zugang nur auf Einladung.")
    st.stop()


def passwort_setzen_seite() -> None:
    """Nach Einladungs- oder Passwort-Link: neues Passwort festlegen."""
    typ = st.session_state.get("passwort_setzen")
    _, mitte, _ = st.columns([1, 2, 1])
    with mitte:
        st.title("FuelBudget 🚗⛽")
        st.subheader("Willkommen! Lege dein Passwort fest." if typ == "invite" else "Neues Passwort festlegen")
        st.caption(f"Konto: {st.session_state['auth']['email']}")
        with st.form("passwort_form"):
            pw1 = st.text_input("Neues Passwort", type="password", autocomplete="new-password")
            pw2 = st.text_input("Passwort wiederholen", type="password", autocomplete="new-password")
            ok = st.form_submit_button("Passwort speichern", type="primary", width="stretch")
        if ok:
            if len(pw1) < MIN_PASSWORT:
                st.error(f"Das Passwort muss mindestens {MIN_PASSWORT} Zeichen haben.")
            elif pw1 != pw2:
                st.error("Die beiden Passwörter stimmen nicht überein.")
            else:
                fehler = None
                try:
                    client().auth.update_user({"password": pw1})
                except Exception as exc:
                    fehler = exc
                if fehler is None:
                    st.session_state.pop("passwort_setzen", None)
                    st.session_state["flash"] = "Passwort gespeichert."
                    st.rerun()
                st.error(f"Passwort konnte nicht gespeichert werden: {fehler}")
    st.stop()
