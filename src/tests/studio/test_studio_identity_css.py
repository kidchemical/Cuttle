"""Studio agent identity CSS (L4): scoped to Studio and styles the classes the helper emits."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / "src" / "web"
IDENTITY_CSS = WEB / "css" / "studio" / "identity.css"
IDENTITY_JS = WEB / "js" / "shared" / "agent_identity.js"


def _selectors(css: str) -> list[str]:
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [s.strip() for s in re.findall(r"([^{}]+)\{", body) if s.strip()]


def test_every_identity_selector_is_studio_scoped():
    selectors = _selectors(IDENTITY_CSS.read_text(encoding="utf-8"))
    assert selectors, "identity.css has no rules"
    for sel in selectors:
        for part in sel.split(","):
            assert part.strip().startswith(":root.cuttle-studio "), sel


def test_identity_css_styles_the_classes_the_helper_emits():
    js = IDENTITY_JS.read_text(encoding="utf-8")
    css = IDENTITY_CSS.read_text(encoding="utf-8")
    for cls in ("agent-dot", "agent-identity", "agent-identity-name"):
        assert re.search(rf'class="{cls}"', js), f"helper no longer emits {cls}"
        assert re.search(rf"\.{cls}\b", css), f"identity.css does not style {cls}"
