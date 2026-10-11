"""Owner-only Router editor previews and recorded activity. No task execution."""
from flask import Blueprint, jsonify, request

from api.http_authz import owner_required
from api.agent_router.engine import build_context, preview_decision
from api.agent_router.outcomes import recent_decisions

router_editor_bp = Blueprint("router_editor", __name__, url_prefix="/api/router")


@router_editor_bp.post("/preview")
@owner_required
def preview():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(success=False, error="Expected a JSON object"), 400
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12000:
        return jsonify(success=False, error="Enter a prompt between 1 and 12,000 characters"), 400
    if "consult_brain" in data and not isinstance(data["consult_brain"], bool):
        return jsonify(success=False, error="consult_brain must be a boolean"), 400
    decision, meta = preview_decision(
        build_context(prompt.strip()), consult_brain=data.get("consult_brain", False)
    )
    return jsonify(success=True, decision=decision.to_dict(), target=decision.target.to_dict(),
                   meta=meta, executed=False)


@router_editor_bp.get("/decisions")
@owner_required
def decisions():
    return jsonify(success=True, decisions=recent_decisions())


agent_router_bp = Blueprint("agent_router_api", __name__)
"""Agent router HTTP routes."""
from flask import Blueprint, jsonify, request
from api.http_authz import owner_required
import time

agent_router_bp = Blueprint("agent_router_api", __name__)
@agent_router_bp.route('/api/agent-router/options', methods=['GET'])
@owner_required
def get_agent_router_options_api():
    """Modes, models and agents for the Router editor page.

    ``?live=1`` (the Router page's call) unions each agent's live CLI/API
    catalog — the same store the chat palette uses — so target model dropdowns
    match what is actually available. Plain calls stay static/cheap and spawn no
    CLI. Owner-only: the payload includes the live ``current`` provider config
    (endpoints, targets, fallbacks), same sensitivity class as
    ``GET /api/router/config``.
    """
    try:
        from api.agent_router.config import load_router_config
        from api.agent_router.registry import (
            CURSOR_KNOWN_MODELS,
            KNOWN_AGENTS,
            OPENAI_ROUTER_MODELS,
        )
        from api.agent_router.types import VALID_MODES
        from scripts.utilities.muse_cli_tool import MUSE_KNOWN_MODELS

        cfg = load_router_config()
        static_models = {
            'cursor': sorted(CURSOR_KNOWN_MODELS),
            'codex': ['gpt-5-codex', 'gpt-5', 'o4-mini'],
            'claude': ['sonnet', 'opus', 'haiku'],
            'opencode': [],
            'muse': [m['id'] for m in MUSE_KNOWN_MODELS],
            'hermes': ['default'],
        }
        agent_models = dict(static_models)
        agent_efforts = {}
        manifests = []
        try:
            from api.agent_harness.catalog import list_agent_manifests

            # Union (not override) — manifest lists are often shorter than the
            # static known-model sets (e.g. Cursor's manifest lists 4 of 12).
            manifests = list(list_agent_manifests())
            for m in manifests:
                merged = set(agent_models.get(m.id, [])) | set(m.models or [])
                agent_models[m.id] = sorted(merged)
                # Effort picker per target: Cursor bakes effort into the model id.
                agent_efforts[m.id] = {
                    'supported': bool(m.supports_effort),
                    'levels': list(m.efforts or []),
                    'model_levels': {k: list(v) for k, v in (m.model_efforts or {}).items()},
                }
        except Exception:
            pass
        # The Router page opts in (`?live=1`) to the same live CLI/API catalog
        # the chat palette and Settings read, so its model dropdowns no longer
        # drift from what is actually available. Plain calls stay static/cheap
        # (auth probes, tests) and skip every CLI.
        live = (request.args.get('live') or '').strip().lower() in (
            '1', 'true', 'yes', 'live',
        )
        if live:
            try:
                from api.agent_harness.agent_models import enrich_router_models

                fallbacks = {m.id: list(m.models or []) for m in manifests}
                for aid, ids in static_models.items():
                    fallbacks.setdefault(aid, list(ids))
                enrich_router_models(agent_models, agent_efforts, fallbacks)
            except Exception as exc:  # noqa: BLE001 — keep the static/manifest sets
                print(f"[api/agent-router/options] live model enrichment failed: {exc}", flush=True)
        return jsonify({
            'success': True,
            'modes': sorted(VALID_MODES),
            'api_models': sorted(set(OPENAI_ROUTER_MODELS) | {'jev', 'jev-latest'}),
            'agents': [
                {'id': 'jev', 'label': 'Jev (TypeSafe System One)'},
            ] + [
                {'id': aid, 'label': info.get('label') or aid}
                for aid, info in sorted(KNOWN_AGENTS.items())
            ],
            'agent_models': {**agent_models, 'jev': ['jev-latest']},
            'agent_efforts': agent_efforts,
            'brains': {
                'jev': sorted(m for m in {'jev', 'jev-latest'}),
                'openai': sorted(OPENAI_ROUTER_MODELS),
            },
            'current': {
                'mode': cfg.provider.mode,
                'api_model': cfg.provider.api_model,
                'local_model': cfg.provider.local_model,
                'local_endpoint': cfg.provider.local_endpoint,
                'agent_id': cfg.provider.agent_id,
                'agent_model': cfg.provider.agent_model,
                'default_target': cfg.default_target.to_dict(),
                'escalation_target': cfg.escalation_target.to_dict(),
                'fallbacks': [t.to_dict() for t in cfg.fallbacks.ordered],
            },
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@agent_router_bp.route('/api/router/config', methods=['GET'])
@owner_required
def get_router_config_api():
    """Full router state for the Router editor page (config + use cases + demotions + rage)."""
    try:
        from api.agent_router.config import load_router_config
        from api.agent_router.drift import active_demotions
        from api.agent_router.frustration import rage_config_to_dict
        from api.agent_router.use_cases import load_use_cases

        from api.agent_router.budget import accounts_payload, budget_settings
        from api.agent_router.classify import classifier_settings

        cfg = load_router_config()
        # Live plan usage is fetched only when budget awareness is on or the
        # page asks (?accounts=1 / refresh=1) — it calls vendor usage APIs.
        want = request.args.get('accounts') == '1' or budget_settings()['enabled']
        refresh = request.args.get('refresh') == '1'
        return jsonify({
            'success': True,
            'config': cfg.to_dict(),
            'use_cases': load_use_cases(),
            'demotions': active_demotions(),
            'rage': rage_config_to_dict(),
            'classifier': classifier_settings(),
            'budget': budget_settings(),
            'accounts': accounts_payload(wait=True, force=refresh) if (want or refresh) else [],
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@agent_router_bp.route('/api/router/config', methods=['PUT'])
@owner_required
def put_router_config_api():
    """Update router config and/or the use-case table (same settings the agents edit)."""
    try:
        from api.agent_router.config import load_router_config, update_router_config
        from api.agent_router.drift import active_demotions
        from api.agent_router.frustration import rage_config_to_dict
        from api.agent_router.use_cases import load_use_cases, save_use_cases

        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify({'success': False, 'error': 'Body must be a JSON object'}), 400

        allowed = (
            'mode', 'api_model', 'local_model', 'local_endpoint',
            'agent_id', 'agent_model', 'default_target', 'escalation_target', 'fallbacks',
        )
        updates = {k: body[k] for k in allowed if k in body}

        # Full-config saves (page / agents) always carry every provider field.
        # Drop the empty ones that belong to a *different* mode than the one
        # being saved, or validation would demand e.g. a local model while
        # saving mode=api.
        try:
            active_mode = str(updates.get('mode') or '').strip().lower()
            if not active_mode:
                active_mode = str(load_router_config().provider.mode or 'off').strip().lower()
            mode_fields = {
                'api': ('api_model',),
                'local': ('local_endpoint', 'local_model'),
                'agent': ('agent_id', 'agent_model'),
            }
            for m, fields in mode_fields.items():
                if m == active_mode:
                    continue
                for f in fields:
                    if f in updates and not str(updates[f] or '').strip():
                        del updates[f]
        except Exception:
            pass

        error = None
        if updates:
            _cfg, error = update_router_config(**updates)

        use_cases = body.get('use_cases')
        if error is None and use_cases is not None:
            if not isinstance(use_cases, list):
                error = 'use_cases must be a list'
            else:
                _saved, uc_err = save_use_cases(use_cases)
                error = uc_err

        if error is None and body.get('rage') is not None:
            from api.agent_router.frustration import save_rage_config

            _saved, rage_err = save_rage_config(body.get('rage'))
            error = rage_err

        if error is None and body.get('classifier') is not None:
            from api.agent_router.classify import save_classifier_settings

            _saved, error = save_classifier_settings(body.get('classifier'))

        if error is None and body.get('budget') is not None:
            from api.agent_router.budget import save_budget_settings

            _saved, error = save_budget_settings(body.get('budget'))

        from api.agent_router.budget import accounts_payload, budget_settings
        from api.agent_router.classify import classifier_settings

        cfg = load_router_config()
        return jsonify({
            'success': error is None,
            'error': error,
            'config': cfg.to_dict(),
            'use_cases': load_use_cases(),
            'demotions': active_demotions(),
            'rage': rage_config_to_dict(),
            'classifier': classifier_settings(),
            'budget': budget_settings(),
            'accounts': accounts_payload(wait=True) if budget_settings()['enabled'] else [],
        }), (200 if error is None else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def _router_health_payload(report):
    from api.agent_router.outcomes import metrics_summary

    demoted = report.get('demotions') if isinstance(report, dict) else None
    return {
        'success': True,
        'metrics': metrics_summary(days=7),
        'report': report,
        'demotions': demoted or {},
        'generated_at': int(time.time()),
    }


@agent_router_bp.route('/api/router/health', methods=['GET'])
@owner_required
def get_router_health_api():
    """7d metrics + current drift report + active demotions (read-only)."""
    try:
        from api.agent_router.drift import active_demotions, compute_drift

        return jsonify(_router_health_payload({
            'report': compute_drift(),
            'demotions': active_demotions(),
            'changes': [],
        }))
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@agent_router_bp.route('/api/router/health/refresh', methods=['POST'])
@owner_required
def refresh_router_health_api():
    """Re-evaluate drift and apply/clear temporary demotions."""
    try:
        from api.agent_router.drift import evaluate_drift

        report = evaluate_drift()
        return jsonify(_router_health_payload(report))
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@agent_router_bp.route('/api/router/demotion/clear', methods=['POST'])
@owner_required
def clear_router_demotion_api():
    """Manually re-promote a demoted target."""
    try:
        from api.agent_router.drift import clear_demotion

        body = request.get_json(silent=True) or {}
        agent = str(body.get('agent') or '').strip()
        model = str(body.get('model') or '').strip()
        if not agent:
            return jsonify({'success': False, 'error': 'agent is required'}), 400
        removed = clear_demotion(agent, model)
        return jsonify({'success': True, 'removed': removed})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
