"""OpenAI model discovery and text request translation (Chat / Responses)."""
from __future__ import annotations

from api.model_providers.capabilities import temperature_value

import os
import base64

from ..types import ProviderManifest, TextRequest, SpeechResult, text_result


def client(manifest: ProviderManifest, timeout: float, key=None):
    from openai import OpenAI
    return OpenAI(api_key=key or manifest.api_key(), timeout=timeout, max_retries=0,
                  base_url=os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1")


class Adapter:
    def discovery_identity(self, manifest):
        return "|".join((os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                         os.getenv("OPENAI_ORG_ID", ""), os.getenv("OPENAI_PROJECT_ID", ""),
                         manifest.api_key()))

    def list_models(self, manifest):
        with client(manifest, 8) as sdk:
            return [{"id": m.id, "label": m.id, "shutdown_date": getattr(m, "shutdown_date", None)}
                    for m in sdk.models.list()]

    def check_key(self, manifest, key):
        with client(manifest, 10, key) as sdk:
            sdk.models.list()

    def complete_text(self, manifest, request, capabilities):
        result = self.generate_text(manifest, request, capabilities)
        return result.text if result else None

    def generate_text(self, manifest: ProviderManifest, request: TextRequest, capabilities):
        if not manifest.configured():
            return None
        messages = []
        if request.system.strip():
            messages.append({"role": "system", "content": request.system.strip()})
        content = request.user
        if request.images:
            content = [{"type": "text", "text": request.user}]
            for image in request.images:
                url = image.url if image.data is None else "data:" + image.mime + ";base64," + base64.b64encode(image.data).decode("ascii")
                content.append({"type": "image_url", "image_url": {"url": url}})
        messages.append({"role": "user", "content": content})
        limit = request.max_tokens
        if capabilities.get("reasoning"):
            # Reasoning consumes the same output budget; a 200-token title
            # allowance alone can finish without any visible text.
            limit = max(limit, 4096)
        maximum = capabilities.get("max_output_tokens")
        if isinstance(maximum, int) and maximum > 0:
            limit = min(limit, maximum)
        kwargs = {"model": request.model}
        temp = temperature_value(capabilities, request)
        if temp is not None:
            kwargs["temperature"] = temp
        efforts = capabilities.get("reasoning_efforts") or []
        preferred = request.effort or next((e for e in ("none", "minimal", "low") if e in efforts), None)
        with client(manifest, request.timeout) as sdk:
            if capabilities.get("transport") == "responses":
                if request.images:
                    messages[-1]["content"] = [{"type": "input_text", "text": request.user}] + [
                        {"type": "input_image", "image_url": item["image_url"]["url"]} for item in content[1:]]
                kwargs.update(input=messages, max_output_tokens=limit)
                if request.json_object:
                    kwargs["text"] = {"format": {"type": "json_object"}}
                if preferred:
                    kwargs["reasoning"] = {"effort": preferred}
                response = sdk.responses.create(**kwargs)
                return text_result(response.output_text, response, request.model)
            kwargs.update(messages=messages)
            kwargs[capabilities.get("token_parameter") or "max_completion_tokens"] = limit
            if request.json_object:
                kwargs["response_format"] = {"type": "json_object"}
            if preferred:
                kwargs["reasoning_effort"] = preferred
            response = sdk.chat.completions.create(**kwargs)
            return text_result(response.choices[0].message.content if response.choices else "", response, request.model)

    def synthesize_speech(self, manifest, request, capabilities):
        kwargs = dict(model=request.model, voice=request.voice, input=request.text[:4096], response_format="mp3")
        if request.speed is not None:
            kwargs["speed"] = request.speed
        with client(manifest, request.timeout) as sdk:
            response = sdk.audio.speech.create(**kwargs)
            data = getattr(response, "content", None)
            if data is None:
                data = response.read()
        if not data:
            raise RuntimeError("TTS returned empty audio")
        return SpeechResult(bytes(data))

    def transcribe(self, manifest, request):
        kwargs = dict(model=request.model, file=(request.filename, request.audio, request.mime))
        if request.prompt:
            kwargs["prompt"] = request.prompt
        if request.language:
            kwargs["language"] = request.language
        with client(manifest, request.timeout) as sdk:
            return sdk.audio.transcriptions.create(**kwargs).text or ""

    def list_voices(self, manifest, model=""):
        voices = manifest.speech.get("voices", [])
        if model in ("tts-1", "tts-1-hd"):
            voices = [v for v in voices if v["id"] in ("alloy", "echo", "fable", "onyx", "nova", "shimmer")]
        return [dict(v) for v in voices]


def build_adapter():
    return Adapter()
