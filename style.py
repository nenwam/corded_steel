"""Look and feel for Corded Steel: black, steel, hazard tape, heavy type.

Everything injected here is static — no application data is interpolated into
these strings, which is what keeps the app's XSS posture intact while still
using raw CSS. The two text helpers escape their argument anyway.

Why the colours are not in `.streamlit/config.toml`: Streamlit treats any
`[theme]` entry as an authoritative custom theme and then refuses to honour the
viewer's own theme choice, which would break the Blackout/Daylight button. So
the base palette comes from Streamlit's Dark preset (a near-black that the
editable grid's canvas also picks up, which CSS could never reach) and this
module layers the aggression on top of whichever preset is active.
"""

from __future__ import annotations

import json
from html import escape

import streamlit as st
import streamlit.components.v1 as components

# Where Streamlit persists the viewer's theme choice. Storing a preset name and
# reloading is the only per-viewer switch that also re-themes the grid canvas;
# custom payloads written to this key are validated away and ignored.
THEME_STORAGE_KEY = "stActiveTheme-/-v1"

ACCENT = "#e01b24"
HAZARD = "#f5b400"

FONTS = (
    "https://fonts.googleapis.com/css2"
    "?family=Anton&family=Barlow+Condensed:wght@400;500;600;700&display=swap"
)

_CSS = """
@import url('%(fonts)s');

:root {
  --steel-accent: %(accent)s;
  --steel-hazard: %(hazard)s;
}

/* Condensed industrial type everywhere, including inside widgets. */
html, body, .stApp, button, input, select, textarea,
[class^="st-"], [class*=" st-"], [data-testid] {
  font-family: 'Barlow Condensed', 'Oswald', 'Arial Narrow', sans-serif;
}

/* ...but not the icons. Streamlit draws those as ligatures, so overriding the
   font makes them render as the literal word "keyboard_arrow_right". */
[data-testid="stIconMaterial"] {
  font-family: 'Material Symbols Rounded' !important;
}

h1, h2, h3, h4 {
  font-family: 'Anton', 'Barlow Condensed', sans-serif !important;
  text-transform: uppercase;
  letter-spacing: 0.02em;
  font-weight: 400 !important;
}
h1 { font-size: clamp(2.6rem, 7vw, 4.6rem) !important; line-height: 0.9 !important; }
h2 { font-size: clamp(1.5rem, 3vw, 2.2rem) !important; }
h3 { font-size: 1.35rem !important; }

/* Nothing in a gym is rounded. */
button, input, select, textarea,
[data-baseweb="input"], [data-baseweb="select"], [data-baseweb="base-input"],
[data-testid="stDataFrame"], [data-testid="stMetric"], [data-testid="stExpander"],
[data-testid="stNotification"], [data-testid="stForm"] {
  border-radius: 0 !important;
}

/* Buttons read like stamped metal plates. */
.stButton button, .stFormSubmitButton button, .stDownloadButton button {
  text-transform: uppercase;
  letter-spacing: 0.12em;
  font-weight: 700;
  border: 2px solid var(--steel-accent) !important;
  transition: transform 0.04s ease, background-color 0.12s ease, color 0.12s ease;
}
.stButton button:hover, .stFormSubmitButton button:hover,
.stDownloadButton button:hover {
  background: var(--steel-accent) !important;
  color: #ffffff !important;
}
.stButton button:active, .stFormSubmitButton button:active {
  transform: translateY(1px);
}
button[kind="primary"], button[kind="primaryFormSubmit"] {
  background: var(--steel-accent) !important;
  color: #ffffff !important;
}

/* Radios become a row of hard-edged selectors. */
[role="radiogroup"] label {
  text-transform: uppercase;
  letter-spacing: 0.09em;
  font-weight: 600;
}

/* Captions and small print: quiet, spaced, technical. */
[data-testid="stCaptionContainer"], .stCaption {
  text-transform: uppercase;
  letter-spacing: 0.14em;
  font-size: 0.72rem !important;
  opacity: 0.72;
}

/* Metric tiles: big numerals, stencil labels. */
[data-testid="stMetricValue"] {
  font-family: 'Anton', sans-serif;
  font-size: 2rem !important;
}
[data-testid="stMetricLabel"] {
  text-transform: uppercase;
  letter-spacing: 0.16em;
  font-size: 0.74rem !important;
  opacity: 0.75;
}

/* The grid is the point of the app — give it a heavier frame. */
[data-testid="stDataFrame"] { border: 2px solid var(--steel-accent) !important; }

/* Expander header, uppercase like everything else. */
[data-testid="stExpander"] summary { text-transform: uppercase; letter-spacing: 0.12em; font-weight: 700; }

/* Hazard tape, used as a section rule. */
.steel-tape {
  height: 9px;
  margin: 0.3rem 0 1.3rem 0;
  background: repeating-linear-gradient(
    -45deg,
    var(--steel-hazard) 0 12px,
    rgba(10,10,10,0.88) 12px 24px
  );
}

.steel-kicker {
  text-transform: uppercase;
  letter-spacing: 0.4em;
  font-size: 0.78rem;
  font-weight: 700;
  color: var(--steel-accent);
  margin-bottom: -0.35rem;
}

/* Widget labels get the same stencilled treatment as everything else. */
[data-testid="stWidgetLabel"] p {
  text-transform: uppercase;
  letter-spacing: 0.12em;
  font-weight: 600;
  font-size: 0.78rem;
}
.steel-creed {
  text-transform: uppercase;
  letter-spacing: 0.18em;
  font-size: 0.9rem;
  font-weight: 600;
  opacity: 0.85;
  margin-top: 0.1rem;
}

/* Enough headroom that the red kicker above the title is never clipped. */
.block-container { padding-top: 3.5rem; }
hr { border-color: var(--steel-accent) !important; opacity: 0.4; }
"""


def inject() -> None:
    """Apply the stylesheet. Identical in both modes — it rides on top of
    whichever Streamlit preset is active, so it never fights the base colours."""
    st.html(
        "<style>%s</style>"
        % (_CSS % {"fonts": FONTS, "accent": ACCENT, "hazard": HAZARD})
    )


def tape() -> None:
    st.html('<div class="steel-tape"></div>')


def kicker(text: str) -> None:
    st.html(f'<div class="steel-kicker">{escape(text)}</div>')


def creed(text: str) -> None:
    st.html(f'<div class="steel-creed">{escape(text)}</div>')


def current_mode() -> str:
    """Whichever theme the viewer is actually looking at."""
    try:
        return st.context.theme.type or "dark"
    except Exception:
        return "dark"


def _run(script: str) -> None:
    components.html(
        f"""<script>
        (function() {{
            try {{ const w = window.parent; {script} }}
            catch (err) {{ console.error('corded steel theme:', err); }}
        }})();
        </script>""",
        height=0,
    )


def ensure_default_dark() -> None:
    """Open black on a browser that has never chosen a theme.

    Without this the app would inherit the viewer's OS preference and could come
    up white, which is not the intended first impression. It writes the key and
    reloads exactly once — on the next load the key exists, so there is no loop.
    Called before login, so the reload costs nobody their session.
    """
    _run(
        f"""
        const key = {json.dumps(THEME_STORAGE_KEY)};
        if (!w.localStorage.getItem(key)) {{
            w.localStorage.setItem(key, {json.dumps(json.dumps({'name': 'Dark'}))});
            w.location.reload();
        }}
        """
    )


def apply_theme(mode: str) -> None:
    """Switch this viewer's theme and reload so Streamlit picks it up.

    Per-browser, so one person going light does not drag everyone else with them.
    """
    preset = "Light" if mode == "light" else "Dark"
    _run(
        f"""
        w.localStorage.setItem({json.dumps(THEME_STORAGE_KEY)},
                               {json.dumps(json.dumps({'name': preset}))});
        w.location.reload();
        """
    )
