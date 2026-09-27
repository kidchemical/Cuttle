"""Typed System One questions / answers (HTTP shape, no SDK required)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


def noul(instructions: str, *, true: str = "", false: str = "") -> Dict[str, Any]:
    q: Dict[str, Any] = {"type": "noul", "instructions": instructions}
    criteria: Dict[str, str] = {}
    if true:
        criteria["true"] = true
    if false:
        criteria["false"] = false
    if criteria:
        q["criteria"] = criteria
    return q


def choice(instructions: str, criteria: Dict[str, Optional[str]]) -> Dict[str, Any]:
    return {
        "type": "choice",
        "instructions": instructions,
        "criteria": {k: (v if v is not None else None) for k, v in criteria.items()},
    }


def score(instructions: str, criteria: List[str]) -> Dict[str, Any]:
    return {"type": "score", "instructions": instructions, "criteria": list(criteria)}


@dataclass
class Answer:
    type: str
    noul: Optional[float] = None
    choice: Optional[str] = None
    score: Optional[float] = None
    probabilities: Dict[str, float] = field(default_factory=dict)
    confidence: Optional[float] = None
    legend: Dict[str, str] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"type": self.type}
        if self.noul is not None:
            d["noul"] = self.noul
        if self.choice is not None:
            d["choice"] = self.choice
        if self.score is not None:
            d["score"] = self.score
        if self.probabilities:
            d["probabilities"] = dict(self.probabilities)
        if self.confidence is not None:
            d["confidence"] = self.confidence
        if self.legend:
            d["legend"] = dict(self.legend)
        return d


def parse_answer(raw: Any) -> Answer:
    if not isinstance(raw, dict):
        return Answer(type="unknown", raw={})
    kind = str(raw.get("type") or "").strip().lower()
    probs_raw = raw.get("probabilities") or {}
    probs: Dict[str, float] = {}
    if isinstance(probs_raw, dict):
        for k, v in probs_raw.items():
            try:
                probs[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
    conf = None
    try:
        if raw.get("confidence") is not None:
            conf = float(raw.get("confidence"))
    except (TypeError, ValueError):
        conf = None
    noul_v = None
    try:
        if raw.get("noul") is not None:
            noul_v = float(raw.get("noul"))
    except (TypeError, ValueError):
        noul_v = None
    score_v = None
    try:
        if raw.get("score") is not None:
            score_v = float(raw.get("score"))
    except (TypeError, ValueError):
        score_v = None
    legend = raw.get("legend") if isinstance(raw.get("legend"), dict) else {}
    return Answer(
        type=kind or "unknown",
        noul=noul_v,
        choice=str(raw.get("choice")).strip() if raw.get("choice") is not None else None,
        score=score_v,
        probabilities=probs,
        confidence=conf,
        legend={str(k): str(v) for k, v in legend.items()},
        raw=dict(raw),
    )


@dataclass
class SystemOneResult:
    model: str
    answers: Dict[str, Answer]
    usage: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    def get(self, key: str) -> Answer:
        return self.answers.get(key) or Answer(type="missing")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "answers": {k: v.to_dict() for k, v in self.answers.items()},
            "usage": dict(self.usage),
        }


QuestionMap = Dict[str, Dict[str, Any]]
State = Union[str, Dict[str, Any], List[Any]]
