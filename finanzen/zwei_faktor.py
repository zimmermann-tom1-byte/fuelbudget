"""Zwei-Faktor-Anmeldung (TOTP) fuer die Finanzseiten.

Die Datenbank laesst Finanzdaten nur mit einem Token der Stufe aal2 heraus
(restriktive RLS-Policy). Diese Seite sorgt dafuer, dass der Nutzer dorthin
kommt: beim ersten Mal Authenticator-App einrichten, danach Code eingeben.
Ueber den "Angemeldet bleiben"-Cookie bleibt die Stufe aal2 erhalten, der
Code wird also nur nach einer neuen Passwort-Anmeldung wieder abgefragt.
"""

from __future__ import annotations

import base64
import time

import streamlit as st

import anmeldung

ISSUER = "FuelBudget Finanzen"


def _stufe() -> str | None:
    try:
        return anmeldung.client().auth.mfa.get_authenticator_assurance_level().current_level
    except Exception:
        return None


def _faktoren():
    """(verifizierte TOTP-Faktoren, unverifizierte Faktoren)"""
    res = anmeldung.client().auth.mfa.list_factors()
    alle = list(getattr(res, "all", None) or [])
    unverifiziert = [f for f in alle if getattr(f, "status", "") != "verified"]
    return list(res.totp or []), unverifiziert


def _qr_html(qr_code: str) -> str:
    svg = qr_code.split(",", 1)[1] if qr_code.startswith("data:") else qr_code
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return (f'<div style="background:#fff;padding:10px;border-radius:12px;width:fit-content">'
            f'<img src="data:image/svg+xml;base64,{b64}" width="200" height="200" alt="QR-Code"></div>')


def _code_pruefen(factor_id: str, code: str) -> str | None:
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != 6:
        return "Der Code hat 6 Ziffern."
    try:
        anmeldung.client().auth.mfa.challenge_and_verify({"factor_id": factor_id, "code": code})
        anmeldung.sitzung_neu_uebernehmen()
    except Exception as exc:
        t = str(exc).lower()
        if "invalid" in t or "expired" in t:
            return "Code falsch oder abgelaufen. Bitte den aktuellen Code eingeben."
        return f"Prüfung fehlgeschlagen: {exc}"
    return None


def _einrichten() -> None:
    st.subheader("🔐 Zwei-Faktor-Schutz einrichten")
    st.markdown(
        "Deine Finanzdaten sind zusätzlich zum Passwort durch einen **6-stelligen Code** aus einer "
        "Authenticator-App geschützt (z. B. die **Passwörter-App** auf iPhone/iPad, Google oder "
        "Microsoft Authenticator). Das musst du nur einmal einrichten."
    )
    s = st.session_state
    if "mfa_enroll" not in s:
        try:
            _, unverifiziert = _faktoren()
            for f in unverifiziert:  # abgebrochene Versuche aufraeumen
                try:
                    anmeldung.client().auth.mfa.unenroll({"factor_id": f.id})
                except Exception:
                    pass
            res = anmeldung.client().auth.mfa.enroll({
                "factor_type": "totp", "issuer": ISSUER,
                "friendly_name": f"Finanzen {time.strftime('%Y-%m-%d %H:%M:%S')}",
            })
        except Exception as exc:
            st.error(f"Einrichtung konnte nicht gestartet werden: {exc}")
            st.stop()
        s["mfa_enroll"] = {"id": res.id, "qr": res.totp.qr_code, "secret": res.totp.secret,
                           "uri": res.totp.uri}
    e = s["mfa_enroll"]

    st.markdown("**1. Code-Generator hinzufügen**")
    qr_col, text_col = st.columns([1, 2])
    with qr_col:
        st.markdown(_qr_html(e["qr"]), unsafe_allow_html=True)
    with text_col:
        st.markdown(
            "Auf einem **anderen Gerät**: QR-Code mit der Kamera scannen.\n\n"
            "Auf **diesem Gerät**: auf den Link tippen. iPhone/iPad öffnen dann die Passwörter-App."
        )
        st.markdown(f'<a href="{e["uri"]}">🔗 In Authenticator-App öffnen</a>', unsafe_allow_html=True)
        st.caption("Oder den Schlüssel manuell eintragen:")
        st.code(e["secret"], language=None)

    st.markdown("**2. Angezeigten Code eingeben**")
    with st.form("mfa_einrichten_form"):
        code = st.text_input("6-stelliger Code", max_chars=7, autocomplete="one-time-code")
        ok = st.form_submit_button("Bestätigen", type="primary", width="stretch")
    if ok:
        fehler = _code_pruefen(e["id"], code)
        if fehler:
            st.error(fehler)
        else:
            s.pop("mfa_enroll", None)
            s["flash"] = "Zwei-Faktor-Schutz ist eingerichtet."
            st.rerun()
    st.caption(
        "Handy verloren? Der Admin kann den Faktor im Supabase-Dashboard unter "
        "Authentication → Users löschen, danach richtest du ihn neu ein."
    )


def _abfragen(faktoren) -> None:
    st.subheader("🔐 Bestätigungscode")
    st.caption("Für die Finanzseiten brauchst du zusätzlich den Code aus deiner Authenticator-App.")
    with st.form("mfa_code_form"):
        code = st.text_input("6-stelliger Code", max_chars=7, autocomplete="one-time-code")
        ok = st.form_submit_button("Freischalten", type="primary", width="stretch")
    if ok:
        fehler = _code_pruefen(faktoren[0].id, code)
        if fehler:
            st.error(fehler)
        else:
            st.rerun()


def sicherstellen() -> None:
    """Am Anfang jeder Finanzseite aufrufen. Beendet den Lauf, solange der
    Nutzer nicht freigeschaltet ist oder der zweite Faktor fehlt."""
    if not anmeldung.finanzen_freigeschaltet():
        st.info("Die Finanzübersicht ist für dein Konto nicht freigeschaltet.")
        st.stop()
    if _stufe() == "aal2":
        return
    try:
        verifiziert, _ = _faktoren()
    except Exception as exc:
        st.error(f"Zwei-Faktor-Status konnte nicht geladen werden: {exc}")
        st.stop()
    _, mitte, _ = st.columns([1, 3, 1])
    with mitte:
        if verifiziert:
            _abfragen(verifiziert)
        else:
            _einrichten()
    st.stop()
