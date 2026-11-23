"""Text I/O names its encoding, including code that only runs on Windows.

The test tiers turn ``EncodingWarning`` into an error, but only for lines that
execute. An ``if os.name == "nt":`` branch never runs on a Linux checkout, so a
robocopy call without ``encoding=`` reached Windows CI first
(``docs/architecture/ci-failure-patterns.md`` theme 1). This static check sees
every branch on every OS.
"""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

SRC = Path(__file__).resolve().parents[2]
SKIP_DIRS = {"node_modules", ".venv", "__pycache__"}
SUBPROCESS = {"run", "Popen", "check_output", "check_call", "call"}
# ``x.open(...)`` on these is not a text-file open.
NOT_FILE_OPEN = {"os", "io", "gzip", "tarfile", "zipfile", "wave", "webbrowser", "request", "urllib"}


def _kw(call: ast.Call, name: str):
    return next((k for k in call.keywords if k.arg == name), None)


def _true(kw) -> bool:
    return kw is not None and not (isinstance(kw.value, ast.Constant) and not kw.value.value)


def _text_open(call: ast.Call, mode_index: int) -> bool:
    mode = _kw(call, "mode")
    node = mode.value if mode else (call.args[mode_index] if len(call.args) > mode_index else None)
    if node is None:
        return True  # default "r"
    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
        return False  # computed mode: cannot tell
    return "b" not in node.value and len(call.args) <= mode_index + 2  # 4th positional is encoding


def _default_encoding_calls(tree: ast.AST):
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or any(k.arg is None for k in call.keywords):
            continue  # **kwargs may carry encoding
        func = call.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if _kw(call, "encoding"):
            continue
        if name in SUBPROCESS and (_true(_kw(call, "text")) or _true(_kw(call, "universal_newlines"))):
            yield call.lineno, f"subprocess.{name}(text=True)"
        elif isinstance(func, ast.Attribute) and name == "read_text" and not call.args:
            yield call.lineno, "read_text()"
        elif isinstance(func, ast.Attribute) and name == "write_text" and len(call.args) < 2:
            yield call.lineno, "write_text()"
        elif name == "open" and isinstance(func, ast.Name) and _text_open(call, 1):
            yield call.lineno, "open()"
        elif (name == "open" and isinstance(func, ast.Attribute)
              and not (isinstance(func.value, ast.Name) and func.value.id in NOT_FILE_OPEN)
              and _text_open(call, 0)):
            yield call.lineno, ".open()"


def test_text_io_names_its_encoding():
    hits = []
    for path in sorted(SRC.rglob("*.py")):
        if SKIP_DIRS & set(path.relative_to(SRC).parts):
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(SRC.parent).as_posix()
        hits += [f"{rel}:{line} {what}" for line, what in _default_encoding_calls(tree)]
    assert not hits, (
        'Pass encoding="utf-8" (add errors="replace" for tool output you only display):\n  '
        + "\n  ".join(hits)
    )


def test_the_check_sees_a_windows_only_branch():
    tree = ast.parse(
        "import os, subprocess\n"
        "if os.name == 'nt':\n"
        "    subprocess.run(['robocopy'], capture_output=True, text=True, errors='replace')\n"
        "open('a.txt', 'w')\n"
        "open('a.bin', 'rb')\n"
        "subprocess.run(['x'], text=True, encoding='utf-8')\n"
    )
    assert sorted(_default_encoding_calls(tree)) == [(3, "subprocess.run(text=True)"), (4, "open()")]
