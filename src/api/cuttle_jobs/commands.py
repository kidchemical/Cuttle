"""Parse ``@cuttle`` job commands."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_VERB_RE = re.compile(
    r"(?is)(?:^|\s)@cuttle\s+(investigate|solve)(?:\s+(.*))?$",
)
_AFFIRM_RE = re.compile(
    r"(?is)^\s*(?:"
    r"yes|y|yeah|yep|sure|ok|okay|"
    r"please\s+do|please\s+implement|please\s+fix|"
    r"go\s+ahead|implement(?:\s+it)?|do\s+it|fix\s+it|ship\s+it"
    r")\b(?:\s+.*)?\s*$"
)
_DECLINE_RE = re.compile(
    r"(?is)^\s*(?:no|nope|nah|cancel|never\s*mind|nevermind|not\s+now|don'?t)"
    r"\b(?:\s+.*)?\s*$"
)
_MENTION_RE = re.compile(r"(?is)(?:^|\s)@cuttle(?:\s+(.*))?$")

SUPPORTED_COMMANDS = frozenset({"investigate", "solve", "converse", "decline"})

BOT_COMMENT_MARKER = "<!-- cuttle-bot -->"


@dataclass(frozen=True)
class ParsedCommand:
    command: str
    instructions: str
    raw_match: str


def _rest_after_mention(line: str) -> Optional[tuple[str, str]]:
    m = _MENTION_RE.search(line.strip())
    if not m:
        return None
    return (m.group(1) or "").strip(), line.strip()


def parse_cuttle_command(comment_body: str) -> Optional[ParsedCommand]:
    """Return the first supported ``@cuttle …`` command, or None.

    Supported:
      - ``@cuttle investigate [instructions]``
      - ``@cuttle solve [instructions]``
      - ``@cuttle yes|go ahead|implement…`` → ``solve``
      - ``@cuttle no|cancel|not now…`` → ``decline``
      - ``@cuttle`` or ``@cuttle <free text>`` → ``converse``
    """
    text = (comment_body or "").strip()
    if not text or "@cuttle" not in text.lower():
        return None

    candidates: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and "@cuttle" in stripped.lower():
            candidates.append(stripped)
    if not candidates:
        candidates = [text]

    for stripped in candidates:
        m = _VERB_RE.search(stripped)
        if m:
            verb = m.group(1).lower()
            if verb in ("investigate", "solve"):
                return ParsedCommand(
                    command=verb,
                    instructions=(m.group(2) or "").strip(),
                    raw_match=stripped,
                )

        parsed_rest = _rest_after_mention(stripped)
        if not parsed_rest:
            continue
        rest, raw = parsed_rest

        if not rest:
            return ParsedCommand(command="converse", instructions="", raw_match=raw)
        if _AFFIRM_RE.match(rest):
            return ParsedCommand(command="solve", instructions=rest, raw_match=raw)
        if _DECLINE_RE.match(rest):
            return ParsedCommand(command="decline", instructions=rest, raw_match=raw)
        return ParsedCommand(command="converse", instructions=rest, raw_match=raw)

    m = _VERB_RE.search(text)
    if m:
        verb = m.group(1).lower()
        if verb in ("investigate", "solve"):
            return ParsedCommand(
                command=verb,
                instructions=(m.group(2) or "").strip(),
                raw_match=text,
            )
    return None


def cuttle_issue_branch(issue_number: int) -> str:
    return f"cuttle/issue-{int(issue_number)}"


def with_bot_marker(body: str) -> str:
    text = (body or "").rstrip()
    if BOT_COMMENT_MARKER in text:
        return text
    return f"{text}\n\n{BOT_COMMENT_MARKER}\n"
