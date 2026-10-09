"""Validate bundled provider manifests before loading their adapters.

The folder is the provider identity. New providers need no settings-page or
completion-executor branches. Arbitrary project Python drop-ins are not loaded.
"""
from __future__ import annotations

import importlib
import re
from functools import lru_cache
from pathlib import Path

import yaml

from .types import ProviderManifest, ProviderAdapter

ROOT = Path(__file__).parent


def _validate_capabilities(values, operations, provider_id):
    if not isinstance(values, dict):
        raise ValueError(f"Invalid capabilities: {provider_id}")
    for key in ("temperature", "json_object", "reasoning", "tool_call", "structured_output", "temperature_disabled_while_thinking"):
        if key in values and not isinstance(values[key], bool):
            raise ValueError(f"Invalid {key}: {provider_id}")
    for key in ("operations", "reasoning_efforts", "input_modalities", "output_modalities"):
        if key in values and (not isinstance(values[key], list) or not all(
                isinstance(v, str) and v for v in values[key])):
            raise ValueError(f"Invalid {key}: {provider_id}")
    if any(op not in operations for op in values.get("operations", [])):
        raise ValueError(f"Undeclared operation: {provider_id}")
    if "transport" in values and values["transport"] not in ("chat_completions", "responses", "messages"):
        raise ValueError(f"Invalid transport: {provider_id}")
    if "token_parameter" in values and values["token_parameter"] not in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
        raise ValueError(f"Invalid token parameter: {provider_id}")
    bounds = values.get("temperature_range")
    if bounds is not None and (not isinstance(bounds, list) or len(bounds) != 2 or
            any(type(v) not in (int, float) for v in bounds) or not 0 <= bounds[0] <= bounds[1] <= 2):
        raise ValueError(f"Invalid temperature range: {provider_id}")
    if "temperature_efforts" in values and (not isinstance(values["temperature_efforts"], list) or
            not all(isinstance(v, str) for v in values["temperature_efforts"])):
        raise ValueError(f"Invalid temperature effort condition: {provider_id}")
    for key in ("context_tokens", "max_output_tokens"):
        if key in values and (type(values[key]) is not int or values[key] <= 0):
            raise ValueError(f"Invalid {key}: {provider_id}")
    if "thinking_types" in values and (not isinstance(values["thinking_types"], dict) or
            not all(isinstance(v, bool) for v in values["thinking_types"].values())):
        raise ValueError(f"Invalid thinking types: {provider_id}")


def load_manifest(folder: Path) -> ProviderManifest:
    raw = yaml.safe_load((folder / "manifest.yaml").read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError(f"Unsupported provider manifest: {folder.name}")
    if raw.get("id") != folder.name or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", folder.name):
        raise ValueError(f"Invalid provider identity: {folder.name}")
    operations = raw.get("operations")
    if not isinstance(operations, list) or not operations or not all(isinstance(v, str) for v in operations):
        raise ValueError(f"Invalid operations: {folder.name}")
    defaults = raw.get("defaults", {})
    rules = raw.get("model_rules", [])
    models = raw.get("models", [])
    if not isinstance(defaults, dict) or not isinstance(rules, list) or not isinstance(models, list):
        raise ValueError(f"Invalid capabilities: {folder.name}")
    _validate_capabilities(defaults, operations, folder.name)
    for rule in rules:
        if (not isinstance(rule, dict) or not isinstance(rule.get("match"), list)
                or not rule["match"] or not all(isinstance(p, str) and p for p in rule["match"])
                or not isinstance(rule.get("capabilities"), dict)):
            raise ValueError(f"Invalid model rule: {folder.name}")
        _validate_capabilities(rule["capabilities"], operations, folder.name)
    for model in models:
        if not isinstance(model, dict) or not isinstance(model.get("id"), str) or not model["id"]:
            raise ValueError(f"Invalid model record: {folder.name}")
        _validate_capabilities(model.get("capabilities", {}), operations, folder.name)
    if len({m["id"] for m in models}) != len(models):
        raise ValueError(f"Duplicate model record: {folder.name}")
    if type(raw.get("needs_credential", True)) is not bool or type(raw.get("priority", 100)) is not int:
        raise ValueError(f"Invalid provider options: {folder.name}")
    speech = raw.get("speech", {})
    if not isinstance(speech, dict):
        raise ValueError(f"Invalid speech manifest: {folder.name}")
    if "speech_generation" in operations:
        if not isinstance(speech.get("default_model"), str) or not speech["default_model"] or not isinstance(speech.get("voices"), list) or not isinstance(speech.get("models", []), list):
            raise ValueError(f"Invalid speech defaults: {folder.name}")
        for row in speech.get("voices", []) + speech.get("models", []):
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
                raise ValueError(f"Invalid speech record: {folder.name}")
        for flag in ("fetch_voices", "supports_stability"):
            if flag in speech and not isinstance(speech[flag], bool):
                raise ValueError(f"Invalid speech option: {folder.name}")
        speed = speech.get("speed_range")
        if speed is not None and (not isinstance(speed, list) or len(speed) != 2 or
                any(type(v) not in (int, float) for v in speed) or not 0 < speed[0] <= speed[1]):
            raise ValueError(f"Invalid speech speed: {folder.name}")
    env = raw.get("credential_env", [])
    if not isinstance(env, list) or not all(isinstance(v, str) and v for v in env):
        raise ValueError(f"Invalid credentials: {folder.name}")
    return ProviderManifest(
        id=raw["id"], label=str(raw.get("label") or raw["id"]),
        operations=tuple(operations), credential_env=tuple(env),
        model_env=str(raw.get("model_env") or ""), default_model=str(raw.get("default_model") or ""),
        needs_credential=bool(raw.get("needs_credential", True)), priority=int(raw.get("priority", 100)),
        models_dev_provider=str(raw.get("models_dev_provider") or ""), defaults=defaults,
        model_rules=tuple(rules), models=tuple(models), speech=raw.get("speech", {}),
    )


@lru_cache(maxsize=1)
def manifests() -> tuple[ProviderManifest, ...]:
    rows = [load_manifest(p.parent) for p in sorted(ROOT.glob("*/manifest.yaml"))]
    return tuple(sorted(rows, key=lambda m: (m.priority, m.id)))


def get_provider(provider_id: str | None) -> ProviderManifest | None:
    wanted = str(provider_id or "").strip().lower()
    return next((p for p in manifests() if p.id == wanted), None)


@lru_cache(maxsize=None)
def get_adapter(provider_id: str) -> ProviderAdapter:
    manifest = get_provider(provider_id)
    if manifest is None:
        raise ValueError("Unknown model provider")
    module = importlib.import_module(f"api.model_providers.{manifest.id}.adapter")
    adapter = module.build_adapter()
    methods = ["discovery_identity", "list_models"]
    if "text_generation" in manifest.operations:
        methods.extend(["complete_text", "generate_text"])
    for operation, method in (("speech_generation", "synthesize_speech"), ("audio_transcription", "transcribe"), ("structured_decision", "decide")):
        if operation in manifest.operations:
            methods.append(method)
    if "speech_generation" in manifest.operations:
        methods.append("list_voices")
    for method in methods:
        if not callable(getattr(adapter, method, None)):
            raise ValueError(f"Provider {manifest.id} lacks {method}")
    return adapter
