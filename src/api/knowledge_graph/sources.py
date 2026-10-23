"""Read facts straight from a checkout: module docstrings, symbol source, doc sections.

Graphify stores docstrings as node labels truncated to ~80 characters, so the
wiki reads the real text here. Everything is read-only, stdlib-only and
best-effort (unreadable or unparsable files yield nothing).
"""

from __future__ import annotations

import ast
import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

MAX_FILE_BYTES = 1_000_000
_JS_BLOCK = re.compile(r"^\s*/\*\*?(.*?)\*/", re.S)
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def read_text(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def file_sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


@lru_cache(maxsize=4096)
def _python_tree(path: str, mtime: float) -> Optional[ast.Module]:
    try:
        return ast.parse(read_text(Path(path)))
    except (SyntaxError, ValueError):
        return None


def _tree(path: Path) -> Optional[ast.Module]:
    try:
        return _python_tree(str(path), path.stat().st_mtime)
    except OSError:
        return None


def module_docstring(path: Path) -> str:
    """The module docstring (Python) or leading block comment (JS/TS/C#/…)."""
    if path.suffix in (".py", ".pyi"):
        tree = _tree(path)
        return (ast.get_docstring(tree) or "").strip() if tree else ""
    head = read_text(path)[:4000]
    match = _JS_BLOCK.match(head)
    if match:
        lines = [re.sub(r"^\s*\*\s?", "", ln) for ln in match.group(1).splitlines()]
        return "\n".join(lines).strip()
    lines = []
    for line in head.splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            lines.append(stripped.lstrip("/").strip())
        elif stripped or lines:
            break
    return "\n".join(lines).strip()


def module_docstrings(root: Path, files: Iterable[str]) -> Dict[str, str]:
    out = {}
    for rel in files:
        doc = module_docstring(root / rel)
        if doc:
            out[rel] = doc
    return out


def first_paragraph(text: str, limit: int = 700) -> str:
    para = " ".join(re.split(r"\n\s*\n", str(text).strip(), maxsplit=1)[0].split())
    return _cut(para, limit)


def first_sentence(text: str, limit: int = 220) -> str:
    para = first_paragraph(text, 10_000)
    match = re.match(r"(.+?[.!?])(\s|$)", para)
    return _cut(match.group(1) if match else para, limit)


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    stop = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[: stop + 1] if stop > limit // 2 else cut.rstrip() + "…")


def symbol_source(path: Path, start_line: int, *, max_lines: int = 70) -> Tuple[int, int, str]:
    """``(start, end, text)`` of the def/class starting at ``start_line``.

    Python uses the AST for the exact end; other languages take a fixed window.
    The text is numbered (``123| code``) so citations can point at real lines.
    """
    lines = read_text(path).splitlines()
    if not lines or not (1 <= start_line <= len(lines)):
        return start_line, start_line, ""
    end = min(len(lines), start_line + max_lines - 1)
    tree = _tree(path) if path.suffix == ".py" else None
    if tree:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                first = min([node.lineno] + [d.lineno for d in node.decorator_list])
                if start_line in (node.lineno, first):
                    end = node.end_lineno or end
                    break
    shown = min(end, start_line + max_lines - 1)
    body = [f"{n}| {lines[n - 1]}" for n in range(start_line, shown + 1)]
    if shown < end:
        body.append(f"…| ({end - shown} more lines to L{end})")
    return start_line, end, "\n".join(body)


def doc_sections(text: str) -> List[Tuple[str, int, str]]:
    """``(heading, level, body)`` per Markdown section; fenced code is not a heading."""
    sections: List[Tuple[str, int, str]] = []
    heading, level, buf, fenced = "", 0, [], False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        match = None if fenced else _HEADING.match(line)
        if match:
            if buf or heading:
                sections.append((heading, level, "\n".join(buf).strip()))
            heading, level, buf = match.group(2).strip(), len(match.group(1)), []
        else:
            buf.append(line)
    if buf or heading:
        sections.append((heading, level, "\n".join(buf).strip()))
    return sections


def line_count(path: Path) -> int:
    return len(read_text(path).splitlines())


def _symbols(path: Path) -> List[Tuple[str, int, int]]:
    """``(qualname, start, end)`` for every def/class (Python AST; else none)."""
    tree = _tree(path) if path.suffix == ".py" else None
    if not tree:
        return []
    out: List[Tuple[str, int, int]] = []

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                out.append((name, start, child.end_lineno or child.lineno))
                walk(child, name + ".")
    walk(tree, "")
    return out


def enclosing_symbol(path: Path, line: int) -> Optional[Tuple[str, int, int]]:
    """Innermost def/class containing ``line``."""
    best = None
    for name, start, end in _symbols(path):
        if start <= line <= end and (best is None or start >= best[1]):
            best = (name, start, end)
    return best


def find_symbol(path: Path, qualname: str) -> Optional[Tuple[int, int]]:
    for name, start, end in _symbols(path):
        if name == qualname:
            return start, end
    return None


def lines_of(path: Path) -> List[str]:
    return read_text(path).splitlines()
