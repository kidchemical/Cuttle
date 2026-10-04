"""Chat message/badge metadata for persistence (P4-3 owned module).

Single purpose-specific owner for shaping the metadata stored alongside
chat bubbles: assistant usage normalization, user send-time agent
badges, and model labels. Moved verbatim out of the Flask entry module
so subagent builders depend on this module instead of reverse importing
that entry module.

Explicit inputs only (result/message/session/identity); per-agent
session stores and starred defaults are read through the same lazy
leaf imports as before. No Flask routes, no persistence writes, no
delivery orchestration here.
"""
from __future__ import annotations

from typing import Optional

def muse_model_label(model) -> str:
    """Human label used by Muse reply badges."""
    from scripts.utilities.muse_cli_tool import muse_model_label
    return muse_model_label(model)


def usage_meta_from_assistant_result(res: Optional[dict]) -> Optional[dict]:
    """Normalize CLI usage (+ models.dev estimate) for chat bubble footers."""
    if not isinstance(res, dict):
        return None
    raw = res.get('usage') if isinstance(res.get('usage'), dict) else {}
    cursor_run = res.get('cursor_run') if isinstance(res.get('cursor_run'), dict) else {}
    if not raw and isinstance(cursor_run.get('usage'), dict):
        raw = cursor_run.get('usage') or {}
    # Cursor stores full camelCase usage on cursor_run even when the top-level
    # usage blob was stripped to prompt/completion only — merge cache fields.
    elif isinstance(cursor_run.get('usage'), dict):
        cu = cursor_run.get('usage') or {}
        merged = dict(raw)
        for src, dst in (
            ('cacheReadTokens', 'cache_read_tokens'),
            ('cache_read_tokens', 'cache_read_tokens'),
            ('cacheWriteTokens', 'cache_write_tokens'),
            ('cache_write_tokens', 'cache_write_tokens'),
            ('context_tokens', 'context_tokens'),
            ('peak_context_tokens', 'peak_context_tokens'),
        ):
            if merged.get(dst) is not None:
                continue
            if cu.get(src) is None:
                continue
            try:
                n = int(cu.get(src) or 0)
            except (TypeError, ValueError):
                continue
            if n > 0:
                merged[dst] = n
        raw = merged
    model = str(
        res.get('agent_model')
        or res.get('model')
        or res.get('muse_model')
        or res.get('hermes_model')
        or res.get('opencode_model')
        or res.get('codex_model')
        or ''
    ).strip()
    if not model and cursor_run:
        model = str(
            cursor_run.get('reported_model')
            or cursor_run.get('requested_model')
            or ''
        ).strip()
    merged = dict(raw) if isinstance(raw, dict) else {}
    if res.get('cost') is not None and merged.get('cost') is None:
        merged['cost'] = res.get('cost')
    try:
        from api.model_pricing import enrich_usage_for_display
        return enrich_usage_for_display(merged, model=model or None)
    except Exception as exc:
        print(f"[CHAT] usage meta enrich failed: {exc}", flush=True)
        return None


def user_badge_metadata(message_text: str, session_id, identity: Optional[dict] = None) -> Optional[dict]:
    """Snapshot the agent badge (model + effort) for a user turn at send time.

    History renders user bubbles from this stored chip. Without it the
    frontend re-derives badges from the *current* session pins, so changing
    the effort pin later rewrites every older bubble on refresh.

    ``identity`` is the frozen send-time tuple (same values the CLI gets).
    When present it wins over a second store/starred lookup.
    """
    import re as _re

    text = str(message_text or '').lstrip()
    m = _re.match(r'^/(muse|hermes|opencode|codex|claude|cursor)\b', text, _re.IGNORECASE)
    if not m:
        return None
    agent = m.group(1).lower()
    sid = str(session_id) if session_id is not None else ''
    ident = identity if isinstance(identity, dict) else None
    if ident and str(ident.get('agent') or '').strip().lower() != agent:
        ident = None
    ident_model = str((ident or {}).get('model') or '').strip() if ident else ''
    ident_effort = str((ident or {}).get('effort') or '').strip() if ident else ''

    def _starred(model_fn=None, effort_fn=None, aid=''):
        sm = se = ''
        try:
            from api.agent_harness.agent_defaults import (
                get_starred_effort,
                get_starred_model,
            )
            if aid:
                sm = str(get_starred_model(aid) or '').strip()
                se = str(get_starred_effort(aid) or '').strip()
        except Exception:
            pass
        return sm, se

    if agent == 'muse':
        model = effort = ''
        if ident is not None:
            model, effort = ident_model, ident_effort
        else:
            try:
                from scripts.utilities.muse_cli_session_store import (
                    load_muse_effort,
                    load_muse_model,
                )
                if sid:
                    model = str(load_muse_model(sid) or '').strip()
                    effort = str(load_muse_effort(sid) or '').strip()
            except Exception:
                pass
            if not model or not effort:
                sm, se = _starred(aid='muse')
                model = model or sm
                effort = effort or se
        if not model:
            try:
                from scripts.utilities.muse_cli_tool import resolve_muse_default_model
                model = str(resolve_muse_default_model() or '').strip()
            except Exception:
                model = ''
        label_model = model
        try:
            label_model = muse_model_label(model) if model else model
        except Exception:
            pass
        label = 'Muse Code' + (f' - {label_model}' if label_model else '')
        meta = '/muse' + (f' · model {model}' if model else '')
        if effort:
            label += f' · {effort}'
            meta += f' · effort {effort}'
        return {'slash_command': {'chips': [{'label': label, 'meta': meta, 'category': 'muse'}]}}
    if agent == 'hermes':
        model = effort = ''
        if ident is not None:
            model, effort = ident_model, ident_effort
        else:
            try:
                from scripts.utilities.hermes_cli_session_store import (
                    load_hermes_effort,
                    load_hermes_model,
                )
                if sid:
                    model = str(load_hermes_model(sid) or '').strip()
                    effort = str(load_hermes_effort(sid) or '').strip()
            except Exception:
                pass
            if not model or not effort:
                sm, se = _starred(aid='hermes')
                model = model or sm
                effort = effort or se
        label_model = model
        try:
            from scripts.utilities.hermes_cli_tool import hermes_model_label
            label_model = hermes_model_label(model) if model else model
        except Exception:
            pass
        label = 'Hermes' + (f' - {label_model}' if label_model else '')
        meta = '/hermes' + (f' · model {model}' if model else '')
        if effort:
            label += f' · {effort}'
            meta += f' · effort {effort}'
        return {'slash_command': {'chips': [{'label': label, 'meta': meta, 'category': 'hermes'}]}}
    if agent == 'opencode':
        model = effort = ''
        if ident is not None:
            model, effort = ident_model, ident_effort
        else:
            try:
                from api.agent_harness.agents.opencode.session_store import (
                    load_opencode_effort,
                    load_opencode_model,
                )
                if sid:
                    model = str(load_opencode_model(sid) or '').strip()
                    effort = str(load_opencode_effort(sid) or '').strip()
            except Exception:
                pass
            if not model or not effort:
                sm, se = _starred(aid='opencode')
                model = model or sm
                effort = effort or se
        label_model = model
        try:
            from api.agent_harness.agents.opencode.adapter import opencode_model_label
            label_model = opencode_model_label(model) if model else model
        except Exception:
            pass
        label = 'OpenCode' + (f' - {label_model}' if label_model else '')
        meta = '/opencode' + (f' · model {model}' if model else '')
        if effort:
            label += f' · {effort}'
            meta += f' · effort {effort}'
        return {'slash_command': {'chips': [{'label': label, 'meta': meta, 'category': 'opencode'}]}}
    if agent == 'codex':
        model = effort = ''
        if ident is not None:
            model, effort = ident_model, ident_effort
        else:
            try:
                from scripts.utilities.codex_cli_session_store import (
                    load_codex_effort,
                    load_codex_model,
                )
                if sid:
                    model = str(load_codex_model(sid) or '').strip()
                    effort = str(load_codex_effort(sid) or '').strip()
            except Exception:
                pass
            if not model or not effort:
                sm, se = _starred(aid='codex')
                model = model or sm
                effort = effort or se
        label_model = model
        try:
            from api.agent_harness.agents.codex.model_catalog import codex_model_label
            label_model = codex_model_label(model) if model else model
        except Exception:
            pass
        label = 'Codex' + (f' - {label_model}' if label_model else '')
        meta = '/codex' + (f' · model {model}' if model else '')
        if effort:
            label += f' · {effort}'
            meta += f' · effort {effort}'
        return {'slash_command': {'chips': [{'label': label, 'meta': meta, 'category': 'codex'}]}}
    if agent == 'claude':
        model = effort = ''
        if ident is not None:
            model, effort = ident_model, ident_effort
        else:
            try:
                from scripts.utilities.claude_cli_session_store import (
                    load_claude_effort,
                    load_claude_model,
                )
                if sid:
                    model = str(load_claude_model(sid) or '').strip()
                    effort = str(load_claude_effort(sid) or '').strip()
            except Exception:
                pass
            if not model or not effort:
                sm, se = _starred(aid='claude')
                model = model or sm
                effort = effort or se
        label_model = model
        try:
            from api.agent_harness.agents.claude.model_catalog import claude_model_label
            label_model = claude_model_label(model) if model else model
        except Exception:
            pass
        label = 'Claude Code' + (f' - {label_model}' if label_model else '')
        meta = '/claude' + (f' · model {model}' if model else '')
        if effort:
            label += f' · {effort}'
            meta += f' · effort {effort}'
        return {'slash_command': {'chips': [{'label': label, 'meta': meta, 'category': 'claude'}]}}
    # Cursor effort is baked into the model id.
    model = ident_model if ident is not None else ''
    try:
        if not model and sid:
            from scripts.utilities.cursor_cli_session_store import load_cursor_agent_options
            model = str((load_cursor_agent_options('', sid) or {}).get('model') or '').strip()
    except Exception:
        pass
    if not model:
        sm, _ = _starred(aid='cursor')
        model = sm or 'auto'
    pretty = model
    if not model or model.lower() in ('auto', 'default'):
        pretty = 'Auto'
    else:
        try:
            from api.cursor_agent_commands import list_cursor_agent_models
            for _m in list_cursor_agent_models() or []:
                if str(_m.get('id') or '').lower() == model.lower() and _m.get('label'):
                    pretty = str(_m['label']).strip() or model
                    break
        except Exception:
            pass
        if pretty == model:
            text = model[7:] if model.lower().startswith('cursor-') else model
            pretty = text.replace('-', ' ').replace('_', ' ').title()
    return {'slash_command': {'chips': [{
        'label': f'Cursor - {pretty}',
        'meta': f'/cursor · requested {pretty}',
        'category': 'cursor',
    }]}}
