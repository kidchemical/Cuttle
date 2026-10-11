"""Anthropic account model discovery and Messages text requests."""
from __future__ import annotations

from api.model_providers.capabilities import temperature_value

import os
import base64

from ..types import text_result


def client(manifest, timeout, key=None):
    from anthropic import Anthropic
    return Anthropic(api_key=key or manifest.api_key(), timeout=timeout, max_retries=0,
                     base_url=os.getenv("ANTHROPIC_BASE_URL") or "https://api.anthropic.com")


class Adapter:
    def discovery_identity(self, manifest):
        return "|".join((os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
                         os.getenv("ANTHROPIC_CUSTOM_HEADERS", ""), manifest.api_key()))

    def list_models(self, manifest):
        rows = []
        with client(manifest, 8) as sdk:
            # SDK iterator follows has_more / after_id; never discard later pages.
            for model in sdk.models.list(limit=100):
                data = model.model_dump()
                caps = data.get("capabilities") or {}
                normalized = {"operations": ["text_generation"]}
                thinking = caps.get("thinking") or {}
                if isinstance(thinking, dict) and isinstance(thinking.get("supported"), bool):
                    normalized["reasoning"] = thinking["supported"]
                    types = thinking.get("types") or {}
                    normalized["thinking_types"] = {
                        key: detail["supported"] for key, detail in types.items()
                        if isinstance(detail, dict) and isinstance(detail.get("supported"), bool)
                    }
                effort = caps.get("effort") or {}
                if effort.get("supported") is False:
                    normalized["reasoning_efforts"] = []
                if effort.get("supported"):
                    normalized["reasoning_efforts"] = [
                        key for key, detail in effort.items() if key != "supported"
                        and isinstance(detail, dict) and detail.get("supported") is True
                    ]
                for key in ("structured_outputs", "temperature", "tool_call"):
                    detail = caps.get(key)
                    if isinstance(detail, dict) and isinstance(detail.get("supported"), bool):
                        normalized["structured_output" if key == "structured_outputs" else key] = detail["supported"]
                for source, dest in (("max_input_tokens", "context_tokens"), ("max_tokens", "max_output_tokens")):
                    if isinstance(data.get(source), int) and data[source] > 0:
                        normalized[dest] = data[source]
                rows.append({"id": model.id, "label": data.get("display_name") or model.id,
                             "lifecycle": data.get("lifecycle"), "capabilities": normalized})
        return rows

    def check_key(self, manifest, key):
        with client(manifest, 10, key) as sdk:
            sdk.models.list(limit=1)

    def complete_text(self, manifest, request, capabilities):
        result = self.generate_text(manifest, request, capabilities)
        return result.text if result else None

    def generate_text(self, manifest, request, capabilities):
        if not manifest.configured() or request.json_object:
            return None
        kwargs = {"model": request.model, "max_tokens": request.max_tokens,
                  "messages": [{"role": "user", "content": request.user}]}
        if request.images:
            content = [{"type": "text", "text": request.user}]
            for image in request.images:
                source = ({"type": "url", "url": image.url} if image.data is None else
                          {"type": "base64", "media_type": image.mime, "data": base64.b64encode(image.data).decode("ascii")})
                content.append({"type": "image", "source": source})
            kwargs["messages"][0]["content"] = content
        if request.effort:
            kwargs["output_config"] = {"effort": request.effort}
        temp = temperature_value(capabilities, request)
        if temp is not None:
            kwargs["temperature"] = temp
        if request.system.strip():
            kwargs["system"] = request.system.strip()
        thinking = capabilities.get("thinking_types") or {}
        if thinking.get("disabled") is False or request.effort in ("xhigh", "max"):
            if not thinking.get("adaptive"):
                return None  # No supported cheap text configuration is known.
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs.pop("temperature", None)
            kwargs["max_tokens"] = max(request.max_tokens, 4096)
            if not request.effort and "low" in capabilities.get("reasoning_efforts", []):
                kwargs["output_config"] = {"effort": "low"}
        limit = capabilities.get("max_output_tokens")
        if isinstance(limit, int) and limit > 0:
            kwargs["max_tokens"] = min(kwargs["max_tokens"], limit)
        with client(manifest, request.timeout) as sdk:
            response = sdk.messages.create(**kwargs)
        return text_result("".join(block.text for block in response.content if getattr(block, "type", "") == "text"), response, request.model)


def build_adapter():
    return Adapter()
