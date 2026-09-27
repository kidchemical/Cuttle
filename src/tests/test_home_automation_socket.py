"""Smoke tests for the home-automation provider socket."""

from api.home_automation_socket import get_provider, list_provider_ids, list_providers


def test_registry_includes_govee_and_nest():
    ids = set(list_provider_ids())
    assert "govee" in ids
    assert "nest" in ids


def test_nest_is_stub_unavailable():
    nest = get_provider("nest")
    assert nest is not None
    assert nest.available() is False
    assert nest.status() == "stub"
    assert nest.list_devices() == []


def test_list_providers_shapes():
    rows = list_providers()
    by_id = {p.id: p for p in rows}
    assert by_id["nest"].available is False
    assert "cameras" in by_id["nest"].capabilities
    assert "lights" in by_id["govee"].capabilities
