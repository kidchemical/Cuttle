"""Pure merge of provider defaults, supplemental facts, live facts, overrides.

Unknown endpoint support stays unknown. models.dev modalities alone do not
prove endpoint compatibility, and structured_output does not mean JSON mode.
"""
from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any

from .types import ProviderManifest


def resolve(manifest: ProviderManifest, model_id: str, *, metadata=None, live=None) -> dict[str, Any]:
    metadata = metadata or {}
    live = live or {}
    caps = dict(manifest.defaults)
    sources = {key: "provider_manifest" for key in caps}

    def merge(values, source):
        for key, value in values.items():
            if value is not None:
                caps[key] = value
                sources[key] = source

    facts = {k: metadata[k] for k in ("temperature", "reasoning", "tool_call", "structured_output")
             if isinstance(metadata.get(k), bool)}
    modalities = metadata.get("modalities") or {}
    if not isinstance(modalities, dict):
        modalities = {}
    for source, dest in (("input", "input_modalities"), ("output", "output_modalities")):
        if isinstance(modalities.get(source), list):
            facts[dest] = modalities[source]
    efforts = []
    for option in metadata.get("reasoning_options") or []:
        if isinstance(option, dict) and option.get("type") == "effort":
            efforts.extend(v for v in option.get("values", []) if isinstance(v, str))
    if efforts:
        facts["reasoning_efforts"] = list(dict.fromkeys(efforts))
    limits = metadata.get("limit") or {}
    if isinstance(limits, dict):
        for source, dest in (("context", "context_tokens"), ("output", "max_output_tokens")):
            if isinstance(limits.get(source), int) and limits[source] > 0:
                facts[dest] = limits[source]
    merge(facts, "models_dev")
    merge(live.get("capabilities") or {}, "provider_api")
    for rule in manifest.model_rules:
        if any(fnmatchcase(model_id.lower(), p.lower()) for p in rule["match"]):
            merge(rule["capabilities"], "manifest_rule")
    declared = next((m for m in manifest.models if m["id"] == model_id), {})
    merge(declared.get("capabilities") or {}, "model_manifest")
    # Native operation and effort declarations override community/fallback data.
    native = live.get("capabilities") or {}
    for key in ("reasoning_efforts", "thinking_types", "operations"):
        if key in native:
            merge({key: native[key]}, "provider_api")
    # Modality restrictions are operation-specific; speech is not text output.
    if any(caps.get(k) and "text" not in caps[k] for k in ("input_modalities", "output_modalities")):
        caps["operations"] = [op for op in caps.get("operations", []) if op != "text_generation"]
        sources["operations"] = "modalities"
    if live.get("lifecycle") == "retired":
        caps["operations"] = []
        sources["operations"] = "provider_api"
    caps["operations"] = [op for op in caps.get("operations", []) if op in manifest.operations]
    caps["temperature_control"] = temperature_control(caps)
    return {**caps, "sources": sources}


def temperature_control(caps, effort=None):
    supported = caps.get("temperature") is True
    reason = "" if supported else "This model does not advertise configurable temperature."
    required = caps.get("temperature_efforts")
    if required is not None:
        effective = effort or next((v for v in ("none", "minimal", "low") if v in caps.get("reasoning_efforts", [])), None)
        if effective not in required:
            supported, reason = False, "Temperature requires reasoning effort: " + ", ".join(required) + "."
    thinking = caps.get("thinking_types") or {}
    if caps.get("temperature_disabled_while_thinking") and (thinking.get("disabled") is False or effort in ("xhigh", "max")):
        supported, reason = False, "Temperature is unavailable with adaptive thinking."
    bounds = caps.get("temperature_range") or [0, 2]
    return {"supported": supported, "min": bounds[0], "max": bounds[1], "reason": reason,
            "required_efforts": required or [], "disabled_while_thinking": bool(caps.get("temperature_disabled_while_thinking")),
            "thinking_required": thinking.get("disabled") is False}


def temperature_value(caps, request):
    control = temperature_control(caps, request.effort)
    if not control["supported"] or request.temperature is None: return None
    return min(control["max"], max(control["min"], float(request.temperature)))
