"""Regression for the fail-closed ``pytest_configure`` isolation gate.

``pytest_configure`` must abort the run (not print-and-continue) when the
supervised isolation service cannot be imported, activation raises, or the
service reports inactive afterwards. The kill guard stays installed on the
success path only.
"""

import os
import sys

import pytest

from tests.conftest import pytest_configure as _hook


def _configure():
    return _hook(object())


def test_configure_succeeds_when_isolation_active():
    assert _configure() is None


def test_configure_aborts_when_isolation_import_fails(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "api.agent_router.supervised.test_isolation", None
    )
    with pytest.raises(RuntimeError, match="isolation unavailable"):
        _configure()


def test_configure_aborts_when_activation_fails(monkeypatch):
    import api.agent_router.supervised.test_isolation as isolation

    def _boom(*, reason):
        raise OSError("simulated activation failure")

    monkeypatch.setattr(isolation, "activate_test_isolation", _boom)
    with pytest.raises(RuntimeError, match="activation failed"):
        _configure()


def test_configure_aborts_when_isolation_reports_inactive(monkeypatch):
    import api.agent_router.supervised.test_isolation as isolation

    monkeypatch.setattr(isolation, "is_test_isolation_active", lambda: False)
    with pytest.raises(RuntimeError, match="inactive after activation"):
        _configure()


def test_configure_hard_overrides_inherited_auto_rebuild(monkeypatch):
    """Configured env beats an inherited CUTTLE_MOBILE_AUTO_REBUILD=1."""
    monkeypatch.setenv("CUTTLE_MOBILE_AUTO_REBUILD", "1")
    assert _configure() is None
    assert os.environ.get("CUTTLE_MOBILE_AUTO_REBUILD") == "0"


def test_collection_style_register_routes_cannot_launch_rebuild(monkeypatch, tmp_path):
    """Collection-time route registration must not start a thread/Gradle.

    Imports are deferred to test body on purpose: nothing in this file may
    import api.mobile_android_update before pytest_configure sets the flag.
    Owned seams only (env + module attrs); no subprocess/Gradle touched,
    and the real published artifact is never pointed at.
    """
    assert _configure() is None
    assert os.environ.get("CUTTLE_MOBILE_AUTO_REBUILD") == "0"
    # Model collection faithfully: pytest sets PYTEST_CURRENT_TEST only
    # around test phases, never during collection imports. Without this,
    # the production per-test guard could mask a hook regression.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    import api.mobile_android_update as apk

    started_threads = []

    def _recording_thread(*args, **kwargs):
        started_threads.append(kwargs.get("target", args[0] if args else None))
        raise AssertionError("rebuild thread must not be created when disabled")

    class _FakeApp:
        def route(self, *args, **kwargs):
            def _deco(fn):
                return fn

            return _deco

    monkeypatch.setattr(apk.threading, "Thread", _recording_thread)
    # Even where a rebuild looks possible, the configured env wins.
    monkeypatch.setattr(apk, "can_rebuild", lambda: True)
    # Force the stale-APK branch so register definitely reaches kick:
    # missing manifest plus an APK path that can never be current.
    monkeypatch.setattr(apk, "_read_manifest", lambda: None)
    monkeypatch.setattr(apk, "UPDATE_APK", tmp_path / "missing.apk")
    apk.register_mobile_android_update_routes(_FakeApp())
    assert started_threads == []

    info = apk.kick_apk_rebuild("deadbeef000000000001")
    assert info == {"started": False, "reason": "auto_rebuild_disabled", "building": False}
