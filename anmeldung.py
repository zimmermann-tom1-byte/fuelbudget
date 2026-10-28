"""Anmeldung fuer FuelBudget ueber Supabase Auth.

- Jede Browser-Sitzung bekommt ihren EIGENEN Supabase-Client. Abfragen laufen
  mit dem Login des jeweiligen Nutzers, die Datenbank (Row Level Security)
  gibt dadurch nur dessen eigene Zeilen heraus.
- "Angemeldet bleiben": Der Refresh-Token liegt in einem Browser-Cookie. Beim
  naechsten Oeffnen meldet die App damit im Hintergrund wieder an. Klappt das
  nicht (z. B. Safari-Einschraenkungen), erscheint einfach die Login-Maske.
- Einladungs- und Passwort-Links: Supabase leitet nach dem Klick auf
  https://fuelbudget.streamlit.app/#access_token=...&refresh_token=... weiter.
  Den Teil hinter der # sieht nur der Browser, nicht der Streamlit-Server.
  link_weiterleitung() liest ihn deshalb per JavaScript aus und laedt die Seite
  mit ?sb_at=...&sb_rt=... neu, was die App lesen kann. (Alternativ, mit
  eigenem SMTP und angepassten Vorlagen: ?token_hash=...&type=...)
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
        # app_metadata kann nur der Admin (per SQL) setzen, nicht der Nutzer.
        # "finanzen": true schaltet die Finanzseiten frei.
        "app_metadata": dict(getattr(session.user, "app_metadata", None) or {}),
    }


def sitzung_neu_uebernehmen() -> None:
    """Nach einem 2FA-Code hat der Client eine neue Sitzung (aal2) mit neuem
    Refresh-Token. Diese uebernehmen, damit Cookie und Token-Erneuerung stimmen."""
    _uebernehmen(client().auth.get_session())


def finanzen_freigeschaltet() -> bool:
    auth = st.session_state.get("auth") or {}
    return bool((auth.get("app_metadata") or {}).get("finanzen"))


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


# --- Links aus E-Mails (Einladung / Passwort zuruecksetzen) -------------------

_HASH_JS = """
<script>
(function () {
  function lesen(w) { try { return [w, w.location.hash || ""]; } catch (e) { return null; } }
  var ziel = lesen(window.top);
  if (!ziel || ziel[1].length < 2) { ziel = lesen(window.parent); }
  if (!ziel || ziel[1].length < 2) { return; }
  var w = ziel[0], p = new URLSearchParams(ziel[1].substring(1)), q = new URLSearchParams();
  if (p.get("access_token") && p.get("refresh_token")) {
    q.set("sb_at", p.get("access_token"));
    q.set("sb_rt", p.get("refresh_token"));
    q.set("sb_type", p.get("type") || "");
  } else if (p.get("error") || p.get("error_code")) {
    q.set("sb_error", p.get("error_code") || p.get("error"));
  } else { return; }
  var url = w.location.origin + w.location.pathname + "?" + q.toString();
  // Die Komponente selbst ist "sandboxed" und darf die Seite nicht neu laden.
  // Deshalb fuehrt ein Skript-Element im Hauptfenster die Weiterleitung aus.
  try {
    var sc = w.document.createElement("script");
    sc.textContent = "window.location.replace(" + JSON.stringify(url) + ");";
    w.document.body.appendChild(sc);
  } catch (e) {
    try { w.location.replace(url); } catch (e2) {}
  }
})();
</script>
"""


def link_weiterleitung() -> None:
    """In JEDEM Lauf aufrufen (auch wenn schon jemand angemeldet ist), damit
    ein Passwort-Link immer verarbeitet wird."""
    components.html(_HASH_JS, height=0)


# --- Einstieg ----------------------------------------------------------------

def aktueller_nutzer() -> dict | None:
    """Gibt {"user_id", "email"} zurueck oder None, wenn niemand angemeldet ist."""
    s = st.session_state

    # 1) Link aus Einladungs- oder Passwort-Mail
    qp = st.query_params
    if "sb_at" in qp and "sb_rt" in qp:
        access, refresh, typ = qp.get("sb_at"), qp.get("sb_rt"), qp.get("sb_type", "")
        st.query_params.clear()
        try:
            res = client().auth.set_session(access, refresh)
            _uebernehmen(res.session)
            s.pop("abgemeldet", None)
            if typ in ("invite", "recovery"):
                s["passwort_setzen"] = typ
            return s["auth"]
        except Exception:
            s["link_fehler"] = True
    elif "sb_error" in qp:
        st.query_params.clear()
        s["link_fehler"] = True
    elif "token_hash" in qp and qp.get("type") in _LINK_TYPEN:
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
                limit = False
                try:
                    client().auth.reset_password_for_email(reset_email.strip())
                except Exception as exc:
                    # Andere Fehler bewusst verschweigen (keine Auskunft, ob die
                    # Adresse existiert). Das Mail-Limit gilt fuers ganze Projekt
                    # und verraet daher nichts ueber einzelne Adressen.
                    limit = "rate limit" in str(exc).lower() or "429" in str(exc)
                if limit:
                    st.warning(
                        "Es wurden gerade zu viele E-Mails verschickt (Limit des kostenlosen "
                        "Mail-Versands). Bitte in etwa einer Stunde erneut versuchen."
                    )
                else:
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


def passwort_aendern_bereich() -> None:
    """Passwort im angemeldeten Zustand aendern (ohne E-Mail). Gedacht fuer die
    Seitenleiste, z. B. nachdem ein Nutzer mit Start-Passwort angelegt wurde."""
    with st.expander("🔑 Passwort ändern"):
        with st.form("passwort_aendern_form", clear_on_submit=True):
            pw1 = st.text_input("Neues Passwort", type="password", autocomplete="new-password", key="pw_neu_1")
            pw2 = st.text_input("Passwort wiederholen", type="password", autocomplete="new-password", key="pw_neu_2")
            ok = st.form_submit_button("Speichern", width="stretch")
        if ok:
            if len(pw1) < MIN_PASSWORT:
                st.error(f"Mindestens {MIN_PASSWORT} Zeichen.")
            elif pw1 != pw2:
                st.error("Die Passwörter stimmen nicht überein.")
            else:
                fehler = None
                try:
                    client().auth.update_user({"password": pw1})
                except Exception as exc:
                    fehler = exc
                if fehler is None:
                    st.success("Passwort geändert.")
                elif "different from the old" in str(fehler).lower() or "same_password" in str(fehler).lower():
                    st.error("Das neue Passwort muss sich vom alten unterscheiden.")
                else:
                    st.error(f"Passwort konnte nicht geändert werden: {fehler}")
