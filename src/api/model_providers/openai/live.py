"""OpenAI Live transport. Credentials and SDK details stay with the provider."""
import os
from contextlib import contextmanager

from api.model_providers.catalog import get_provider
from api.model_providers.openai.adapter import client

MODEL = "gpt-live-1"
VOICES = tuple("alloy ash ballad beacon bossa brise cedar cinder coral delta echo flitz gleam harema juni marin meridian nira noeul nuri quartz ripple sage shida shimmer sillage stone tempo verse vesper willow".split())


@contextmanager
def live_client():
    provider = get_provider("openai")
    if not provider or not provider.configured():
        raise RuntimeError("Configure an OpenAI API key in Settings → Providers.")
    if (os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/") != "https://api.openai.com/v1":
        raise RuntimeError("GPT-Live requires the official OpenAI API endpoint.")
    with client(provider, 25) as sdk:
        yield sdk


def create_webrtc(config, sdp):
    with live_client() as sdk:
        result = sdk.live.create(session=config, transport={"type": "webrtc", "sdp": sdp})
        return result.session.id, result.transport.sdp


@contextmanager
def attach(session_id):
    with live_client() as sdk:
        with sdk.live.sideband.connect(session_id=session_id, max_retries=0) as connection:
            yield connection


@contextmanager
def primary(config):
    with live_client() as sdk:
        with sdk.live.connect(max_retries=0) as connection:
            connection.session.start(session=config)
            yield connection


def hangup(session_id):
    with live_client() as sdk:
        sdk.live.sessions.hangup(session_id)
