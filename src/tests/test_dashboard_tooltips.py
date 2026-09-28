"""Dashboard column-chart tooltips: unified hover must skip zero-value groups.

The Cuttle Usage "over time" view stacks one bar trace per group with
``hovermode: 'x unified'``, which lists every trace at the hovered bucket.
Zero buckets therefore have to be nulled so the tooltip stays short.
There is no JS harness in this repo, so this guards the shipped wiring
in ``src/web/js/dashboards_page.js`` directly.
"""

from __future__ import annotations

import re
from pathlib import Path

JS = Path(__file__).resolve().parents[1] / "web" / "js" / "dashboards_page.js"


def _draw_usage_charts() -> str:
    text = JS.read_text(encoding="utf-8")
    start = text.index("function drawUsageCharts()")
    end = text.index("function renderSoon()")
    return text[start:end]


def test_stacked_columns_null_zero_buckets():
    body = _draw_usage_charts()
    assert "hovermode: 'x unified'" in body
    # Time view maps zero / missing bucket values to null (renders as no
    # segment, excluded from the unified hover list) instead of plotting 0.
    assert re.search(
        r"y:\s*\(g\.series\[metric\.id\]\s*\|\|\s*\[\]\)\.map\(\(v\)",
        body,
    ), "stacked-column traces must derive y from the group series"
    assert "? null : v" in body


def test_breakdown_view_stays_single_value_hover():
    body = _draw_usage_charts()
    assert "hovermode: 'closest'" in body
