"""Discover installed/served models; never start, load, or download a server."""
from __future__ import annotations

from api.model_providers.capabilities import temperature_value

import time

from ..types import text_result

from core.local_llm import (get_local_api_key, get_local_backend, get_local_base_url,
                            get_local_native_base_url, local_reachable, resolve_local_model)


class Adapter:
    def discovery_identity(self, manifest):
        return "|".join((get_local_backend(), get_local_base_url(), get_local_api_key()))

    def list_models(self, manifest):
        import requests
        headers = {"Authorization": "Bearer " + get_local_api_key()}
        if get_local_backend() == "ollama":
            response = requests.get(get_local_native_base_url() + "/api/tags", headers=headers, timeout=5)
            if response.ok:
                rows = []
                deadline = time.monotonic() + 8
                for item in response.json().get("models", []):
                    mid = item.get("name") or item.get("model")
                    if not mid:
                        continue
                    row = {"id": mid, "label": mid}
                    if time.monotonic() < deadline:
                        try:
                            show = requests.post(get_local_native_base_url() + "/api/show", headers=headers,
                                                 json={"model": mid}, timeout=min(2, max(.1, deadline - time.monotonic())))
                            supported = show.json().get("capabilities") if show.ok else None
                            if isinstance(supported, list):
                                row["capabilities"] = {
                                    "operations": ["text_generation"] if "completion" in supported else [],
                                    "reasoning": "thinking" in supported,
                                    "tool_call": "tools" in supported,
                                    "input_modalities": ["text", "image"] if "vision" in supported else ["text"],
                                    "output_modalities": ["text"],
                                }
                        except (requests.RequestException, ValueError):
                            pass
                    rows.append(row)
                return rows
        response = requests.get(get_local_base_url() + "/models", headers=headers, timeout=5)
        response.raise_for_status()
        return [{"id": m["id"], "label": m["id"]} for m in response.json().get("data", []) if m.get("id")]

    def complete_text(self, manifest, request, capabilities):
        result = self.generate_text(manifest, request, capabilities)
        return result.text if result else None

    def generate_text(self, manifest, request, capabilities):
        from openai import OpenAI
        if not local_reachable(timeout=1.5):
            return None
        messages = []
        if request.system.strip():
            messages.append({"role": "system", "content": request.system.strip()})
        messages.append({"role": "user", "content": request.user})
        kwargs = {"model": request.model or resolve_local_model(None), "messages": messages,
                  "max_tokens": request.max_tokens}
        temp = temperature_value(capabilities, request)
        if temp is not None:
            kwargs["temperature"] = temp
        if request.effort:
            kwargs["reasoning_effort"] = request.effort
        if request.images:
            raise ValueError("Local image input is not implemented")
        if request.json_object:
            kwargs["response_format"] = {"type": "json_object"}
        with OpenAI(base_url=get_local_base_url(), api_key=get_local_api_key(),
                    timeout=max(request.timeout, .1), max_retries=0) as sdk:
            response = sdk.chat.completions.create(**kwargs)
        return text_result(response.choices[0].message.content if response.choices else "", response, request.model)


def build_adapter():
    return Adapter()
