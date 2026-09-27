"""Per-model CLI capability overlays for harness connectors.

These describe what *this agent CLI* can do with a model — not provider truth.
CLIs change fast; keep limits in YAML next to the connector, not adapter ifs.

Sources (later overrides earlier for the same capability key on a match)::

1. ``manifest.yaml`` → ``model_capabilities:``
2. ``model_capabilities.yaml`` (optional sibling list)
3. ``models/*.yaml`` (optional one-rule files under the connector folder)

Rule shape (all match fields optional; at least one should be set)::

    - id: openrouter/z-ai/glm-5.3-flash   # exact (case-insensitive)
      contains: glm                        # substring
      prefix: openrouter/z-ai/
      suffix: -flash
      supports_variant: false              # OpenCode ``--variant``
      # or:
      supports:
        variant: false
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import yaml

from api.agent_harness.types import AgentManifest


@dataclass(frozen=True)
class ModelCapabilityRule:
    """One declarative match → capability map for a connector."""

    id: str = ""
    contains: str = ""
    prefix: str = ""
    suffix: str = ""
    supports: Dict[str, bool] = field(default_factory=dict)

    def matches(self, model: str) -> bool:
        mid = (model or "").strip().lower()
        if not mid:
            return False
        has_constraint = False
        if self.id:
            has_constraint = True
            if mid != self.id.strip().lower():
                return False
        if self.contains:
            has_constraint = True
            if self.contains.strip().lower() not in mid:
                return False
        if self.prefix:
            has_constraint = True
            if not mid.startswith(self.prefix.strip().lower()):
                return False
        if self.suffix:
            has_constraint = True
            if not mid.endswith(self.suffix.strip().lower()):
                return False
        # A rule with no match fields would apply to every model — refuse that.
        return has_constraint


def _coerce_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "1", "on"):
            return True
        if low in ("false", "no", "0", "off"):
            return False
    return None


def _supports_from_mapping(data: Mapping[str, Any]) -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    nested = data.get("supports")
    if isinstance(nested, Mapping):
        for key, raw in nested.items():
            name = str(key or "").strip().lower().removeprefix("supports_")
            if not name:
                continue
            coerced = _coerce_bool(raw)
            if coerced is not None:
                out[name] = coerced
    # Flat aliases: supports_variant → variant
    for key, raw in data.items():
        k = str(key or "").strip().lower()
        if not k.startswith("supports_") or k == "supports":
            continue
        name = k[len("supports_") :]
        if not name:
            continue
        coerced = _coerce_bool(raw)
        if coerced is not None:
            out[name] = coerced
    return out


def parse_capability_rule(data: Any) -> Optional[ModelCapabilityRule]:
    if not isinstance(data, Mapping):
        return None
    match = data.get("match")
    match_map: Mapping[str, Any] = match if isinstance(match, Mapping) else data
    rule_id = str(
        match_map.get("id") or match_map.get("model") or data.get("id") or data.get("model") or ""
    ).strip()
    contains = str(match_map.get("contains") or data.get("contains") or "").strip()
    prefix = str(match_map.get("prefix") or data.get("prefix") or "").strip()
    suffix = str(match_map.get("suffix") or data.get("suffix") or "").strip()
    supports = _supports_from_mapping(data)
    if not supports and not (rule_id or contains or prefix or suffix):
        return None
    rule = ModelCapabilityRule(
        id=rule_id,
        contains=contains,
        prefix=prefix,
        suffix=suffix,
        supports=supports,
    )
    # Drop empty-match rules even if they declare supports (too broad / footgun).
    if not (rule.id or rule.contains or rule.prefix or rule.suffix):
        return None
    return rule


def parse_capability_rules(raw: Any) -> Tuple[ModelCapabilityRule, ...]:
    if raw is None:
        return ()
    if isinstance(raw, Mapping):
        # Single rule mapping, or {rules: [...]}
        if "rules" in raw and isinstance(raw.get("rules"), list):
            items = raw["rules"]
        else:
            items = [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        return ()
    out: List[ModelCapabilityRule] = []
    for item in items:
        rule = parse_capability_rule(item)
        if rule is not None:
            out.append(rule)
    return tuple(out)


def _load_yaml(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return None


def load_model_capability_rules(
    agent_dir: Path, *, manifest_raw: Optional[Mapping[str, Any]] = None
) -> Tuple[ModelCapabilityRule, ...]:
    """Merge manifest + optional overlay files for one connector directory."""
    rules: List[ModelCapabilityRule] = []
    if manifest_raw is not None:
        rules.extend(parse_capability_rules(manifest_raw.get("model_capabilities")))

    overlay = _load_yaml(agent_dir / "model_capabilities.yaml")
    if overlay is not None:
        rules.extend(parse_capability_rules(overlay))

    models_dir = agent_dir / "models"
    if models_dir.is_dir():
        for path in sorted(models_dir.glob("*.yaml")) + sorted(models_dir.glob("*.yml")):
            data = _load_yaml(path)
            if data is None:
                continue
            if isinstance(data, Mapping) and not (
                data.get("id") or data.get("model") or data.get("contains")
                or data.get("prefix") or data.get("suffix") or data.get("match")
            ):
                # Filename stem as id when the file only lists supports_* keys.
                # Use `__` as a path separator stand-in (keep single `_` intact).
                stem = path.stem.replace("__", "/")
                merged = dict(data)
                merged.setdefault("id", stem)
                data = merged
            rules.extend(parse_capability_rules(data))

    return tuple(rules)


def resolve_model_supports(
    rules: Sequence[ModelCapabilityRule], model: Optional[str]
) -> Dict[str, bool]:
    """Apply matching rules in order; later matches override earlier keys."""
    mid = (str(model or "").strip())
    if not mid:
        return {}
    out: Dict[str, bool] = {}
    for rule in rules:
        if not rule.matches(mid):
            continue
        out.update(rule.supports)
    return out


def model_supports(
    manifest: AgentManifest,
    model: Optional[str],
    capability: str,
    *,
    default: bool = True,
) -> bool:
    """Whether ``model`` has ``capability`` for this connector (default True)."""
    cap = (capability or "").strip().lower().removeprefix("supports_")
    if not cap:
        return default
    mid = (str(model or "").strip())
    if not mid:
        return default
    resolved = resolve_model_supports(manifest.model_capabilities or (), mid)
    if cap not in resolved:
        return default
    return bool(resolved[cap])


def agent_model_supports(
    agent_id: str,
    model: Optional[str],
    capability: str,
    *,
    default: bool = True,
    project_path: Optional[str] = None,
) -> bool:
    """Catalog lookup helper for adapters."""
    from api.agent_harness.catalog import get_agent

    pair = get_agent(agent_id, project_path)
    if pair is None:
        return default
    return model_supports(pair[0], model, capability, default=default)
