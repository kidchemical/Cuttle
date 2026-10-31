"""Askpass helper — the program ssh / sudo / git call for a password.

Invoked as ``askpass <prompt>`` through the per-run wrapper written by
:mod:`.deliver` (``SSH_ASKPASS``, ``SUDO_ASKPASS``, ``GIT_ASKPASS``). It prints
the matching secret on stdout and nothing else. Stdlib only; run with
``python -I`` so the caller's environment cannot inject modules.

Rules arrive in ``CUTTLE_ASKPASS_RULES`` (JSON list of
``{"match": regex|"", "var": ENV_NAME}``); each value sits in its own env var
of the child process. Safety:

* host-key / yes-no confirmations are refused unless a rule matches them
  explicitly, so a password is never sent as a "yes";
* each rule answers at most ``CUTTLE_ASKPASS_MAX`` times (default 3) per run,
  so a wrong password cannot be retried into an account lockout.
"""

from __future__ import annotations

import json
import os
import re
import sys

_CONFIRM_RE = re.compile(r"\(yes/no|\[y/n\]|\(y/n\)|continue connecting|fingerprint", re.I)
_SECRET_PROMPT_RE = re.compile(r"pass(word|phrase|code)?|pin\b|token|secret|otp|credential", re.I)
_USERNAME_RE = re.compile(r"user(name)?\b", re.I)


def choose_rule(prompt: str, rules: list) -> dict | None:
    """Pure: the rule that answers ``prompt``, or None to refuse."""
    explicit = [r for r in rules if r.get("match")]
    for rule in explicit:
        try:
            if re.search(str(rule["match"]), prompt, re.I):
                return rule
        except re.error:
            continue
    if _CONFIRM_RE.search(prompt):
        return None
    if _USERNAME_RE.search(prompt) and not _SECRET_PROMPT_RE.search(prompt):
        return None
    defaults = [r for r in rules if not r.get("match")]
    if len(defaults) == 1:
        return defaults[0]
    return None


def _bump(state_dir: str, var: str, limit: int) -> bool:
    if not state_dir:
        return True
    path = os.path.join(state_dir, f"count-{var}")
    try:
        with open(path, "r", encoding="ascii") as fh:
            count = int(fh.read().strip() or 0)
    except (OSError, ValueError):
        count = 0
    if count >= limit:
        return False
    try:
        with open(path, "w", encoding="ascii") as fh:
            fh.write(str(count + 1))
    except OSError:
        pass
    return True


def main(argv: list[str]) -> int:
    prompt = " ".join(argv[1:]) if len(argv) > 1 else ""
    try:
        rules = json.loads(os.environ.get("CUTTLE_ASKPASS_RULES") or "[]")
    except ValueError:
        rules = []
    rule = choose_rule(prompt, rules if isinstance(rules, list) else [])
    if rule is None:
        sys.stderr.write(f"cuttle askpass: no secret configured for prompt: {prompt.strip()[:120]}\n")
        return 1
    var = str(rule.get("var") or "")
    try:
        limit = int(os.environ.get("CUTTLE_ASKPASS_MAX") or 3)
    except ValueError:
        limit = 3
    if not _bump(os.environ.get("CUTTLE_ASKPASS_STATE", ""), var, limit):
        sys.stderr.write("cuttle askpass: retry limit reached; refusing to resend the secret\n")
        return 1
    value = os.environ.get(var)
    if value is None:
        return 1
    sys.stdout.write(value + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
