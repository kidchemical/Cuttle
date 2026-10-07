"""Runtime state resolves through the Cuttle home, never the install tree.

A packaged install may be read-only (Program Files, an AppImage mount) and is
replaced on upgrade, so a module that joins state onto ``__file__`` or the
checkout loses or cannot write user data. Paths come from ``core.runtime_paths``.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2]

# Joins that reach a state location inside the checkout.
_FORBIDDEN = [
    re.compile(r"""parents\[\d\]\s*/\s*['"](data|output|settings\.json|\.env)['"]"""),
    re.compile(r"""['"]src['"]\s*/\s*['"](data|output|settings\.json|\.env)['"]"""),
    re.compile(r"""['"]web['"]\s*/\s*['"]logs['"]"""),
    re.compile(r"""['"]personal['"]\s*/\s*['"]secrets['"]"""),
    re.compile(r"""['"]\.cuttle_global['"]\s*/\s*['"]personal['"]"""),
    re.compile(r"""Path\.home\(\)\s*/\s*['"]cuttle_logs['"]"""),
]
# The one-time mover names the old locations on purpose.
_ALLOWED = {Path("core/runtime_data.py")}


def test_no_module_writes_state_into_the_install_tree():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC)
        if rel.parts[0] in ("tests", "web") or rel in _ALLOWED or ".venv" in rel.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(pattern.search(line) for pattern in _FORBIDDEN):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert not offenders, "Resolve state via core.runtime_paths:\n" + "\n".join(offenders)
