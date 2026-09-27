"""Live-smoke budget policy.

Live CLI turns cost real money. Adding an agent must *ask* which model and
which tests to run — never default to every installed agent on a premium id.

Env (all optional; empty = conservative defaults documented in ADDING_AN_AGENT.md):

* ``CUTTLE_AGENT_SMOKE=1`` — enable live tests at all
* ``CUTTLE_AGENT_SMOKE_AGENTS`` — comma ids, e.g. ``deepseek,muse`` (default: catalog)
* ``CUTTLE_AGENT_SMOKE_SCOPE`` — ``one_shot``, ``envelope``, ``resume``,
  ``long_pad``, ``stop_followup`` (comma list). Default ``one_shot,envelope,resume``
  for back-compat of the existing suite. When *adding* an agent, the doc requires
  asking first and typically running ``one_shot`` only on the new id.
  ``stop_followup`` is CH-000513 (plant secret → cancel mid-turn → recall on
  the same resume; no orphans). 3 prompts/agent.
* ``CUTTLE_AGENT_SMOKE_MODEL`` — override for every agent in this run
* ``CUTTLE_AGENT_SMOKE_MODEL_<ID>`` — per-agent override (``MUSE``, ``DEEPSEEK``, …)

Model resolution order: per-agent env → global env → ``manifest.smoke_model``
→ ``manifest.default_model``.
"""

from __future__ import annotations

import os
from typing import Iterable, List, Optional, Sequence

from api.agent_harness.types import AgentManifest

_VALID_SCOPES = frozenset(
    {"one_shot", "envelope", "resume", "long_pad", "stop_followup"}
)
_DEFAULT_SCOPE = frozenset({"one_shot", "envelope", "resume"})


def _split_csv(raw: str) -> List[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def live_agent_ids(catalog_ids: Sequence[str]) -> List[str]:
    wanted = [aid.lower().replace("_", "-") for aid in _split_csv(os.environ.get("CUTTLE_AGENT_SMOKE_AGENTS") or "")]
    catalog = [str(aid).strip() for aid in catalog_ids if str(aid).strip()]
    if not wanted:
        return list(catalog)
    catalog_set = {aid.lower() for aid in catalog}
    missing = [aid for aid in wanted if aid not in catalog_set]
    if missing:
        raise AssertionError(
            "CUTTLE_AGENT_SMOKE_AGENTS names unknown harness ids: "
            + ", ".join(missing)
            + f". Known: {', '.join(catalog)}"
        )
    return [aid for aid in catalog if aid.lower() in set(wanted)]


def live_scopes() -> frozenset:
    raw = (os.environ.get("CUTTLE_AGENT_SMOKE_SCOPE") or "").strip().lower()
    if not raw or raw in ("default", "compat"):
        return _DEFAULT_SCOPE
    if raw in ("min", "minimal", "cheap"):
        return frozenset({"one_shot"})
    if raw in ("full", "all"):
        return frozenset(_VALID_SCOPES)
    parts = {p.replace("-", "_") for p in _split_csv(raw)}
    unknown = parts - _VALID_SCOPES
    if unknown:
        raise AssertionError(
            "CUTTLE_AGENT_SMOKE_SCOPE has unknown tokens: "
            + ", ".join(sorted(unknown))
            + f". Valid: {', '.join(sorted(_VALID_SCOPES))} (or min/full)"
        )
    return frozenset(parts)


def scope_enabled(name: str) -> bool:
    return name in live_scopes()


def live_model(agent_id: str, manifest: Optional[AgentManifest] = None) -> Optional[str]:
    aid = (agent_id or "").strip().lower().replace("-", "_")
    per = (os.environ.get(f"CUTTLE_AGENT_SMOKE_MODEL_{aid.upper()}") or "").strip()
    if per:
        return per
    global_m = (os.environ.get("CUTTLE_AGENT_SMOKE_MODEL") or "").strip()
    if global_m:
        return global_m
    if manifest is not None:
        cheap = (manifest.smoke_model or "").strip()
        if cheap:
            return cheap
        default = (manifest.default_model or "").strip()
        if default:
            return default
    return None


def parametrize_ids(catalog_ids: Iterable[str]) -> List[str]:
    ids = live_agent_ids(list(catalog_ids) or ["cursor"])
    return ids or ["cursor"]
