"""Provider declarations and operation-specific contracts."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderManifest:
    id: str
    label: str
    operations: tuple[str, ...]
    credential_env: tuple[str, ...] = ()
    model_env: str = ""
    default_model: str = ""
    needs_credential: bool = True
    priority: int = 100
    models_dev_provider: str = ""
    defaults: dict[str, Any] = field(default_factory=dict)
    model_rules: tuple[dict[str, Any], ...] = ()
    models: tuple[dict[str, Any], ...] = ()
    speech: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    def api_key(self) -> str:
        return next((os.getenv(n, "").strip() for n in self.credential_env
                     if os.getenv(n, "").strip()), "")

    def credential_present(self) -> bool:
        return bool(self.api_key())

    def configured(self) -> bool:
        return not self.needs_credential or self.credential_present()

    @property
    def suggested_models(self) -> tuple[str, ...]:
        return tuple(m["id"] for m in self.models)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "label": self.label, "operations": list(self.operations),
            "credential_env": list(self.credential_env), "model_env": self.model_env,
            "default_model": self.default_model,
            "suggested_models": list(self.suggested_models),
            "needs_credential": self.needs_credential,
            "credential_present": self.credential_present(), "configured": self.configured(),
            "capabilities": dict(self.defaults),
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True)
class TextRequest:
    user: str
    system: str = ""
    model: str = ""
    max_tokens: int = 200
    temperature: float = 0.3
    timeout: float = 45.0
    json_object: bool = False
    effort: str | None = None
    images: tuple[ImageInput, ...] = ()


class ProviderAdapter(Protocol):
    def discovery_identity(self, manifest: ProviderManifest) -> str: ...
    def list_models(self, manifest: ProviderManifest) -> list[dict[str, Any]]: ...


class TextProviderAdapter(ProviderAdapter, Protocol):
    def generate_text(self, manifest: ProviderManifest, request: TextRequest,
                      capabilities: dict[str, Any]) -> TextResult | None: ...


class SpeechProviderAdapter(ProviderAdapter, Protocol):
    def synthesize_speech(self, manifest: ProviderManifest, request: SpeechRequest,
                          capabilities: dict[str, Any]) -> SpeechResult: ...
    def list_voices(self, manifest: ProviderManifest, model: str = "") -> list[dict[str, str]]: ...


@dataclass(frozen=True)
class ImageInput:
    data: bytes | None = field(default=None, repr=False)
    mime: str = "image/png"
    url: str | None = None


@dataclass(frozen=True)
class TextResult:
    text: str
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SpeechRequest:
    text: str
    model: str
    voice: str
    speed: float | None = None
    stability: float | None = None
    timeout: float = 60


@dataclass(frozen=True)
class SpeechResult:
    audio: bytes = field(repr=False)
    mime: str = "audio/mpeg"
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TranscriptionRequest:
    audio: bytes = field(repr=False)
    model: str
    filename: str = "phrase.webm"
    mime: str = "audio/webm"
    prompt: str = ""
    language: str = ""
    timeout: float = 30


def text_result(text, response, model):
    usage = getattr(response, "usage", None)
    def count(*names):
        for name in names:
            value = getattr(usage, name, None)
            if type(value) is int:
                return value
        return 0
    inp, out = count("prompt_tokens", "input_tokens"), count("completion_tokens", "output_tokens")
    return TextResult(text or "", {"model": model, "prompt_tokens": inp,
                      "completion_tokens": out, "total_tokens": inp + out})


@dataclass(frozen=True)
class DecisionRequest:
    state: Any
    questions: dict[str, Any]
    model: str
    api_key: str = field(repr=False)
    base_url: str = 'https://api.typesafe.ai'
    timeout: float = 12


class ProviderError(RuntimeError):
    def __init__(self, message, *, status=0, retryable=True):
        super().__init__(message)
        self.status = self.status_code = status
        self.retryable = retryable
