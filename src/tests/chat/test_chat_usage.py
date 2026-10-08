"""Usage footers distinguish inclusive input/cache counts without provider calls."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[3] / "src/web/js/chat/chat_usage.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node unavailable")
def test_usage_footer_cache_conventions_and_cost_provenance():
    script = r"""
const A = require(process.argv[1]);
const escape = s => String(s).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
const muse = {prompt_tokens: 9457534, completion_tokens: 28014, cache_read_tokens: 9413333, cost: .02885, cost_estimated: true};
const meta = {slash_command: {chips: [{category: 'muse'}]}};
const additive = {prompt_tokens: 10000, completion_tokens: 100, cache_read_tokens: 1000, cache_write_tokens: 200, cache_inclusive: false};
const fullyCached = {...additive, prompt_tokens: 0};
const unknown = {prompt_tokens: 10000, completion_tokens: 100, cache_read_tokens: 1000};
process.stdout.write(JSON.stringify({
  muse: A.render(A.normalize(muse, meta), escape),
  additive: A.render(additive, escape),
  fullyCached: A.render(fullyCached, escape),
  unknown: A.render(unknown, escape),
  unknownHasCost: A.render(unknown, escape).includes('message-usage-cost'),
  explicitWins: A.normalize({...muse, cache_inclusive: false}, meta).cache_inclusive,
  zero: A.render({cost: 0, cost_estimated: false}, escape),
  empty: A.render(null, escape),
}));
"""
    result = subprocess.run(["node", "-e", script, str(MODULE)], capture_output=True, text=True, encoding="utf-8", check=True)
    out = json.loads(result.stdout)
    # Compact footer: arrows + numbers only, no text labels (mobile layout).
    # Input shows non-cached tokens only; cache is the separate span.
    assert "↑ 44k</span>" in out["muse"]
    assert "↑ 9.4M</span>" in out["muse"]
    assert "9.5M" not in out["muse"]
    assert "cached (included)" not in out["muse"]
    assert "output</span>" not in out["muse"]
    assert "99.5% of total input" in out["muse"]
    assert "~$0.029" in out["muse"]
    assert "↑ 10k</span>" in out["additive"]
    assert "8.9% of total input" in out["additive"]
    assert "cache write (included)" not in out["additive"]
    assert "↑ 0</span>" in out["fullyCached"]
    assert "83.3% of total input" in out["fullyCached"]
    assert "reported input</span>" not in out["unknown"]
    assert "included)" not in out["unknown"]
    assert out["unknownHasCost"] is False
    assert out["explicitWins"] is False
    assert "$0" in out["zero"]
    assert out["empty"] == ""
