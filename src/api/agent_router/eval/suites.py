"""Router evaluation suite loading."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SUITES_DIR = Path(__file__).resolve().parent.parent / "suites"
SUITE_VERSION_DEFAULT = "1"


@dataclass
class EvalTestCase:
    id: str
    category: str
    prompt: str
    allowed_task_types: List[str] = field(default_factory=list)
    allowed_difficulties: List[str] = field(default_factory=list)
    allowed_targets: List[Dict[str, str]] = field(default_factory=list)
    preferred_target: Optional[Dict[str, str]] = None
    manual_review: bool = False
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "prompt": self.prompt,
            "allowed_task_types": list(self.allowed_task_types),
            "allowed_difficulties": list(self.allowed_difficulties),
            "allowed_targets": list(self.allowed_targets),
            "preferred_target": dict(self.preferred_target) if self.preferred_target else None,
            "manual_review": self.manual_review,
            "notes": self.notes,
        }


@dataclass
class EvalSuite:
    name: str
    version: str
    description: str
    cases: List[EvalTestCase]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "cases": [c.to_dict() for c in self.cases],
        }


def suites_dir() -> Path:
    return SUITES_DIR


def list_suite_names() -> List[str]:
    if not SUITES_DIR.is_dir():
        return []
    return sorted(p.stem for p in SUITES_DIR.glob("*.json"))


def _parse_case(raw: Dict[str, Any], idx: int) -> EvalTestCase:
    cid = str(raw.get("id") or f"case_{idx}").strip()
    prompt = str(raw.get("prompt") or "").strip()
    if not prompt:
        raise ValueError(f"Suite case {cid!r} missing prompt")
    preferred = raw.get("preferred_target")
    if preferred is not None and not isinstance(preferred, dict):
        preferred = None
    return EvalTestCase(
        id=cid,
        category=str(raw.get("category") or "other").strip() or "other",
        prompt=prompt,
        allowed_task_types=[str(x).strip().lower() for x in (raw.get("allowed_task_types") or []) if str(x).strip()],
        allowed_difficulties=[str(x).strip().lower() for x in (raw.get("allowed_difficulties") or []) if str(x).strip()],
        allowed_targets=[
            {"agent": str(t.get("agent") or "").strip().lower(), "model": str(t.get("model") or "").strip()}
            for t in (raw.get("allowed_targets") or [])
            if isinstance(t, dict) and str(t.get("agent") or "").strip()
        ],
        preferred_target=(
            {
                "agent": str(preferred.get("agent") or "").strip().lower(),
                "model": str(preferred.get("model") or "").strip(),
            }
            if isinstance(preferred, dict) and str(preferred.get("agent") or "").strip()
            else None
        ),
        manual_review=bool(raw.get("manual_review")),
        notes=str(raw.get("notes") or "").strip(),
    )


def load_suite(name: str = "baseline") -> EvalSuite:
    suite_name = (name or "baseline").strip() or "baseline"
    path = SUITES_DIR / f"{suite_name}.json"
    if not path.is_file():
        known = ", ".join(list_suite_names()) or "(none)"
        raise FileNotFoundError(
            f"Unknown eval suite `{suite_name}`. Available: {known}"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Suite {suite_name} must be a JSON object")
    cases_raw = raw.get("cases")
    if not isinstance(cases_raw, list) or not cases_raw:
        raise ValueError(f"Suite {suite_name} has no cases")
    cases = [_parse_case(c, i) for i, c in enumerate(cases_raw) if isinstance(c, dict)]
    if not cases:
        raise ValueError(f"Suite {suite_name} has no valid cases")
    return EvalSuite(
        name=str(raw.get("name") or suite_name),
        version=str(raw.get("version") or SUITE_VERSION_DEFAULT),
        description=str(raw.get("description") or "").strip(),
        cases=cases,
    )


def parse_batch_args(args: str) -> Tuple[str, int, int, bool]:
    """Parse ``batch [<suite>] [--concurrency N] [--repeat N] [--yes]``.

    Returns (suite_name, concurrency, repeats, confirmed).
    """
    tokens = (args or "").split()
    suite = "baseline"
    concurrency = 3
    repeats = 1
    confirmed = False
    i = 0
    if tokens and not tokens[0].startswith("-"):
        suite = tokens[0]
        i = 1
    while i < len(tokens):
        t = tokens[i].lower()
        if t in ("--yes", "-y", "yes", "confirm"):
            confirmed = True
            i += 1
            continue
        if t in ("--concurrency", "-c") and i + 1 < len(tokens):
            try:
                concurrency = max(1, min(8, int(tokens[i + 1])))
            except ValueError:
                pass
            i += 2
            continue
        if t.startswith("--concurrency="):
            try:
                concurrency = max(1, min(8, int(t.split("=", 1)[1])))
            except ValueError:
                pass
            i += 1
            continue
        if t in ("--repeat", "--repeats", "-r") and i + 1 < len(tokens):
            try:
                repeats = max(1, min(20, int(tokens[i + 1])))
            except ValueError:
                pass
            i += 2
            continue
        if t.startswith("--repeat=") or t.startswith("--repeats="):
            try:
                repeats = max(1, min(20, int(t.split("=", 1)[1])))
            except ValueError:
                pass
            i += 1
            continue
        i += 1
    return suite, concurrency, repeats, confirmed
