"""Minimaler Streamlit-Ersatz fuer Tests ohne Streamlit-Installation.

Fuehrt Seitenskripte aus und liefert fuer Widgets ihre Standardwerte. Ein
Formular kann gezielt "abgeschickt" werden (SUBMIT), um Speicherpfade zu
pruefen. Ziel: Tipp-, Typ- und Logikfehler im Python-Code finden, nicht das
Aussehen pruefen.
"""

from __future__ import annotations

import sys
import types


class Stop(Exception):
    pass


class Rerun(Exception):
    pass


class SessionState(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e

    def __setattr__(self, k, v):
        self[k] = v


class _Ctx:
    """Container (columns, tabs, expander, form, sidebar …)."""

    def __init__(self, mod, form_key=None):
        self._mod, self._form = mod, form_key

    def __enter__(self):
        if self._form:
            self._mod._aktuelles_form.append(self._form)
        return self

    def __exit__(self, *a):
        if self._form:
            self._mod._aktuelles_form.pop()
        return False

    def __getattr__(self, name):
        return getattr(self._mod, name)


class _Auswahl:
    def __init__(self, rows):
        self.selection = types.SimpleNamespace(rows=rows)


def bauen():
    st = types.ModuleType("streamlit")
    st.session_state = SessionState()
    st.query_params = {}
    st._aktuelles_form = []
    st.SUBMIT = set()        # Form-Keys, die als abgeschickt gelten
    st.WERTE = {}            # Widget-Key/Label -> erzwungener Wert
    st.AUSWAHL_ZEILEN = []   # Zeilenauswahl in Tabellen
    st.KNOEPFE = set()       # Button-Labels, die als geklickt gelten
    st.LOG = []

    def _wert(key, label, default):
        if key is not None and key in st.WERTE:
            return st.WERTE[key]
        if label in st.WERTE:
            return st.WERTE[label]
        return default

    def stop():
        raise Stop()

    def rerun():
        raise Rerun()

    st.stop, st.rerun = stop, rerun

    def noop(*a, **k):
        st.LOG.append((a[:1], k.get("key")))
        return None

    for name in ("title", "caption", "markdown", "write", "info", "warning", "error", "success",
                 "toast", "metric", "plotly_chart", "page_link", "latex", "subheader", "header",
                 "code", "progress", "image", "divider", "set_page_config", "html"):
        setattr(st, name, noop)

    def columns(spec, **k):
        n = spec if isinstance(spec, int) else len(spec)
        return [_Ctx(st) for _ in range(n)]

    st.columns = columns
    st.tabs = lambda labels, **k: [_Ctx(st) for _ in labels]
    st.expander = lambda *a, **k: _Ctx(st)
    st.popover = lambda *a, **k: _Ctx(st)
    st.container = lambda *a, **k: _Ctx(st)
    st.sidebar = _Ctx(st)
    st.form = lambda key, **k: _Ctx(st, form_key=key)

    def form_submit_button(label="Submit", **k):
        return bool(st._aktuelles_form) and st._aktuelles_form[-1] in st.SUBMIT

    st.form_submit_button = form_submit_button
    st.button = lambda label, **k: label in st.KNOEPFE or k.get("key") in st.KNOEPFE
    st.toggle = lambda label, value=False, **k: _wert(k.get("key"), label, value)
    st.checkbox = lambda label, value=False, **k: _wert(k.get("key"), label, value)

    def selectbox(label, options, index=0, format_func=str, **k):
        options = list(options)
        for o in options:
            format_func(o)  # Fehler in format_func sichtbar machen
        default = options[index] if options and index is not None else None
        return _wert(k.get("key"), label, default)

    st.selectbox = selectbox
    st.radio = selectbox

    def segmented_control(label, options, default=None, **k):
        return _wert(k.get("key"), label, default)

    st.segmented_control = segmented_control

    def select_slider(label, options=(), value=None, format_func=str, **k):
        for o in options:
            format_func(o)
        return _wert(k.get("key"), label, value if value is not None else list(options)[0])

    st.select_slider = select_slider

    def number_input(label, min_value=None, max_value=None, value="min", step=None, **k):
        if value == "min":
            value = min_value if min_value is not None else 0.0
        return _wert(k.get("key"), label, value)

    st.number_input = number_input
    st.slider = lambda label, min_value=0, max_value=100, value=None, **k: _wert(
        k.get("key"), label, value if value is not None else min_value)
    st.date_input = lambda label, value=None, **k: _wert(k.get("key"), label, value)
    st.text_input = lambda label, value="", **k: _wert(k.get("key"), label, value)

    def dataframe(data, on_select="ignore", **k):
        if hasattr(data, "columns"):
            list(data.columns)
        return _Auswahl(st.AUSWAHL_ZEILEN) if on_select == "rerun" else None

    st.dataframe = dataframe
    st.data_editor = lambda data, **k: data.copy()

    class Page:
        def __init__(self, pfad, title=None, icon=None, url_path=None, default=False):
            self.pfad, self.title, self.default = pfad, title, default

    class Navigation:
        def __init__(self, seiten):
            alle = [p for liste in seiten.values() for p in liste] if isinstance(seiten, dict) else list(seiten)
            self.seiten = alle
            st.NAV = alle

        def run(self):
            import runpy
            ziel = next((p for p in self.seiten if p.default), self.seiten[0])
            if st.NAV_ZIEL:
                ziel = next(p for p in self.seiten if p.pfad.endswith(st.NAV_ZIEL))
            runpy.run_path(ziel.pfad, run_name="__main__")

    st.Page, st.navigation, st.NAV_ZIEL = Page, Navigation, None

    cc = types.SimpleNamespace()
    for name in ("NumberColumn", "ProgressColumn", "TextColumn", "DateColumn", "CheckboxColumn"):
        setattr(cc, name, lambda *a, **k: None)
    st.column_config = cc
    st.components = types.SimpleNamespace(v1=types.SimpleNamespace(html=noop))
    st.context = types.SimpleNamespace(cookies={})
    return st


def plotly_stub():
    plotly = types.ModuleType("plotly")
    go = types.ModuleType("plotly.graph_objects")

    class Figure:
        def __init__(self, *a, **k):
            self.data = []

        def add_trace(self, t):
            self.data.append(t)

        def __getattr__(self, name):
            return lambda *a, **k: None

    go.Figure = Figure
    go.Bar = go.Scatter = lambda *a, **k: dict(k)
    plotly.graph_objects = go
    return {"plotly": plotly, "plotly.graph_objects": go}


def installieren():
    st = bauen()
    comp = types.ModuleType("streamlit.components")
    v1 = types.ModuleType("streamlit.components.v1")
    v1.html = lambda *a, **k: None
    comp.v1 = v1
    st.components = comp
    sys.modules.update({"streamlit": st, "streamlit.components": comp, "streamlit.components.v1": v1})
    for name, mod in plotly_stub().items():
        sys.modules.setdefault(name, mod)
    return st
