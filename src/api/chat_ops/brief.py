"""The supervisor's standing brief, built from a schedule's structured settings.

Pure (no Cuttle imports): the check-in states the job and every setting the
host enforces, so the free-text **Focus** (payload key ``instructions``) only
carries what is specific to the job. Settings always win over Focus; the user
changes them with the Supervise dialog's fields or the CLI flags, never by
rewording Focus. ``focus_warnings`` flags Focus text that restates a setting.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional

# Who the supervisor may pull in. Default: only for what needs a person.
INVOLVE = {
    'never': 'Never involve the user. Decide from the goal, and answer its question cards yourself.',
    'blocked': 'Involve the user only for what needs a person (logins, payments, irreversible steps); '
               'decide everything else from the goal, its question cards included.',
    'questions': 'Answer its question cards from the goal when you can; tell the user about the ones you cannot.',
}
DEFAULT_INVOLVE = 'blocked'

# How an idle chat with open Tasks items is sent on. ``template``: the host
# sends the next open item itself, no supervisor turn; anything that needs
# judgment (errors, questions, stalls, rule violations, checkpoints) still wakes it.
NUDGE = ('agent', 'template')
DEFAULT_NUDGE = 'agent'
# Template nudges in a row with no Tasks progress before the supervisor is woken instead.
TEMPLATE_NUDGE_LIMIT = 2

CHECKPOINT_LABELS = {'milestone': 'Tasks milestones', 'tests': 'test runs', 'repeat_failures': 'repeated failures',
                     'watched_paths': 'watched files'}

FOCUS_MAX = 2000


def involve(value: Any) -> str:
    value = str(value or '').strip().lower()
    if value and value not in INVOLVE:
        raise ValueError(f"involve_user must be one of {', '.join(INVOLVE)}")
    return value or DEFAULT_INVOLVE


def nudge(value: Any) -> str:
    value = str(value or '').strip().lower()
    if value and value not in NUDGE:
        raise ValueError(f"nudge must be one of {', '.join(NUDGE)}")
    return value or DEFAULT_NUDGE


def focus(value: Any) -> str:
    text = str(value or '').strip()
    if len(text) > FOCUS_MAX:
        raise ValueError(f'Focus is limited to {FOCUS_MAX} characters')
    return text


def _do_text(do: Iterable[str]) -> str:
    out = []
    for entry in do or ['wake']:
        if entry == 'stop':
            out.append('the chat is stopped')
        elif entry.startswith('supervisor:'):
            out.append(f"reviewer `{entry.split(':', 1)[1]}` checks it")
        elif entry.startswith('action:'):
            out.append(f"action `{entry.split(':', 1)[1]}` runs")
    return f" ({', '.join(out)})" if out else ''


def settings_lines(cfg: Mapping[str, Any], snap: Mapping[str, Any]) -> List[str]:
    """Check-in lines: the job, then each host-enforced setting."""
    handle = snap.get('handle') or 'the chat'
    lines = ['', f'**Your job:** keep {handle} moving until its work is done. When it stalls, waits on a '
             'finished job, or stops after a failure, send it one concrete next step. While it works '
             'fine, send nothing.',
             '', '**Settings** (the user sets these in the Supervise dialog; the host enforces them, and they '
             'win over Focus):']
    tasks = [t.get('title') for t in snap.get('tasks') or [] if t.get('title')]
    if cfg.get('task_ids') is not None and not cfg.get('task_ids') and not tasks:
        lines.append('- Tasks: none followed; judge progress from its replies.')
    elif tasks:
        lines.append('- Tasks: ' + ', '.join(f'"{t}"' for t in tasks) + ' (done when every item is checked).')
    else:
        lines.append('- Tasks: every list the chat makes (none yet).')
    rules = []
    if cfg.get('required_branch'):
        rules.append(f"must stay on `{cfg['required_branch']}`")
    if cfg.get('forbidden_branches'):
        rules.append('never touch ' + ', '.join(f'`{b}`' for b in cfg['forbidden_branches']))
    if rules:
        lines.append('- Branches: ' + '; '.join(rules) + '. The host checks these and reports a violation.')
    lines.append('- User: ' + INVOLVE[involve(cfg.get('involve_user'))])
    points = cfg.get('checkpoints') or {}
    if points:
        lines.append('- Also wakes you on: ' + ', '.join(
            CHECKPOINT_LABELS.get(k, k) + _do_text((v or {}).get('do')) for k, v in points.items()) + '.')
    if nudge(cfg.get('nudge')) == 'template':
        lines.append('- Idle with open Tasks items: the host sends the next item itself; you are woken for '
                     'anything else.')
    text = str(cfg.get('instructions') or '').strip()  # stored before the Focus cap: never raise here
    if text:
        lines += ['', "**Focus** (the user's notes for this job):", text]
    return lines


def _open_items(items, trail=()):
    for item in items or []:
        if not item.get('done'):
            kids = list(_open_items(item.get('children') or [], trail + (item.get('text') or '',)))
            yield from kids or [trail + (item.get('text') or '',)]


def template_nudge(snap: Mapping[str, Any]) -> Optional[str]:
    """The host's own nudge for an idle chat with open Tasks items, or None."""
    for task in snap.get('tasks') or []:
        for path in _open_items(task.get('items') or []):
            item = ' › '.join(p for p in path if p)
            if item:
                return (f"Supervision: \"{task.get('title') or 'Tasks'}\" still has open items. "
                        f'Continue with the next one: **{item}**. If it is blocked, say what blocks it; '
                        'if it is already done, check it off.')
    return None


def tasks_signature(snap: Mapping[str, Any]) -> str:
    return json.dumps([(t.get('id'), t.get('done'), t.get('total')) for t in snap.get('tasks') or []])


# Focus that restates a setting: (pattern, what to use instead).
_LINT = (
    (re.compile(r'\b(opus|sonnet|haiku|fable|gpt-?\d[\w.-]*|gemini[\w.-]*|grok[\w.-]*|deepseek[\w.-]*'
                r'|composer[\w.-]*|qwen[\w.-]*|llama[\w.-]*)\b|\b(use|run|switch\w*|pin\w*)\b[^.]{0,30}\bmodels?\b',
                re.I),
     'names models: pin the supervisor with the Model/Effort fields (--model/--effort) and worker/expert '
     'models with Advanced → model guidance (--model-tiers)'),
    (re.compile(r'\b(push|merge|commit)\w*\b[^.]{0,60}\b(branch|main|master|prod\w*)\b|\bprotected branch', re.I),
     'states branch rules: use Branch rules (--require-branch / --forbid-branch); the host checks them'),
    (re.compile(r'\b(involv|ask|bother|ping|notify|tell)\w*\s+(the\s+)?(user|them|me)\b|\buser is not involved\b', re.I),
     'states when to involve the user: use the "Involve me" setting (--involve)'),
    (re.compile(r'\b(check|wake|look)\w*(\s+(in|on\s+it))?\s+every\s+\d+\s*(s|sec|seconds?|m|min|minutes?|h|hours?)\b'
                r'|\bcheck-?in interval\b', re.I),
     'states an interval: use "Check every" (--every)'),
    (re.compile(r'`python -m |\.sh\b|`[^`]*\b(kill|pkill|end-process)\b', re.I),
     'embeds a procedure (commands to run on every check-in): put it in a project/personal rule or runbook, '
     'which every check-in already loads'),
    (re.compile(r'\breview\w*\b[^.]{0,60}\bmilestone|\bmilestone\w*\b[^.]{0,60}\breview', re.I),
     'asks for milestone reviews: turn on the "Tasks item or list completed" checkpoint and pick a reviewer '
     '(--checkpoints \'{"milestone":{"do":["supervisor:<profile>","wake"]}}\')'),
)
FOCUS_SOFT_MAX = 600


def focus_warnings(text: Any) -> List[str]:
    """Why ``text`` restates settings or carries more than job notes ([] when fine)."""
    text = str(text or '')
    out = [f'Focus {why}.' for rx, why in _LINT if rx.search(text)]
    if len(text) > FOCUS_SOFT_MAX:
        out.append(f'Focus is {len(text)} characters; keep it to a few job-specific notes.')
    return out


def settings_body(cfg: Mapping[str, Any]) -> Dict[str, Any]:
    """The normalized policy fields of a schedule payload."""
    return {'involve_user': involve(cfg.get('involve_user')), 'nudge': nudge(cfg.get('nudge'))}
