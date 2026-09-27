"""
E2E-style integration test for Discord pipeline trigger API.
Requires Cuttle server running at http://127.0.0.1:8080.
Skips if server is not available.
"""
import os
import sys
import pytest
import requests
from pathlib import Path

src_root = Path(__file__).resolve().parent.parent.parent
if str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))

API_BASE = os.getenv("CUTTLE_API_URL", "http://127.0.0.1:8080")


@pytest.fixture(scope="module")
def server_available():
    """Check if Cuttle API server is running."""
    try:
        r = requests.get(f"{API_BASE}/api/health", timeout=2)
        return r.ok
    except requests.exceptions.RequestException:
        return False


def test_discord_api_health(server_available):
    """Test that /api/health responds when server is running."""
    if not server_available:
        pytest.skip("Cuttle server not running - start with: cd electron && npm start")
    r = requests.get(f"{API_BASE}/api/health", timeout=5)
    assert r.status_code == 200


def test_discord_pipeline_trigger_endpoint(server_available):
    """Test pipeline-trigger-discord endpoint responds (200 or 404)."""
    if not server_available:
        pytest.skip("Cuttle server not running - start with: cd electron && npm start")

    payload = {
        "message": "hello",
        "user_context": {
            "id": "12345",
            "display_name": "TestUser",
            "channel_id": "67890",
            "channel_type": "dm",
            "guild_id": None,
        },
        "session": {"user_id": "12345", "channel_id": "67890"},
    }
    r = requests.post(
        f"{API_BASE}/api/pipeline-trigger-discord",
        json=payload,
        timeout=30,
    )
    # 200 = success, 404 = no pipeline with Discord trigger (acceptable)
    assert r.status_code in (200, 404, 503), f"Unexpected status: {r.status_code}"
    data = r.json()
    assert "success" in data or "error" in data
