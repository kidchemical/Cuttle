"""Live Flask: inbound Discord trigger must not exist. Skips if server is down."""

import os
import sys
from pathlib import Path

import pytest
import requests

src_root = Path(__file__).resolve().parent.parent.parent
if str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))

API_BASE = os.getenv("CUTTLE_API_URL", "https://127.0.0.1:8080")


@pytest.fixture(scope="module")
def server_available():
    try:
        r = requests.get(f"{API_BASE}/api/health", timeout=2, verify=False)
        return r.ok
    except requests.exceptions.RequestException:
        return False


def test_discord_api_health(server_available):
    if not server_available:
        pytest.skip("Cuttle server not running")
    r = requests.get(f"{API_BASE}/api/health", timeout=5, verify=False)
    assert r.status_code == 200


def test_pipeline_trigger_discord_gone(server_available):
    if not server_available:
        pytest.skip("Cuttle server not running")
    st = requests.get(f"{API_BASE}/api/status", timeout=5, verify=False)
    if st.ok:
        connected = (st.json() or {}).get("discord_connected")
        # Current tree always reports False. null/True means a pre-cleanup Flask.
        if connected is not False:
            pytest.skip(
                "Live Flask predates Discord gateway removal "
                f"(discord_connected={connected!r}); restart Flask to verify HTTP 404"
            )
    r = requests.post(
        f"{API_BASE}/api/pipeline-trigger-discord",
        json={"message": "hello"},
        timeout=10,
        verify=False,
    )
    assert r.status_code == 404
