"""The dashboard page.

The page is three files in `assets/` (markup, styles, script). They are joined
into one HTML string when the server starts, so the app still serves a single
self-contained page and nothing else has to be hosted.
"""

from pathlib import Path

ASSETS = Path(__file__).parent / "assets"


def build_page() -> str:
    html = (ASSETS / "index.html").read_text(encoding="utf-8")
    css = (ASSETS / "app.css").read_text(encoding="utf-8")
    js = (ASSETS / "app.js").read_text(encoding="utf-8")
    return html.replace("/*__CSS__*/", css).replace("/*__JS__*/", js)


PAGE = build_page()
