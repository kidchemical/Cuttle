"""Chromium for in-suite browser tests: skip when absent, unless required.

The python CI jobs install no Playwright browsers; the browser job sets
``CUTTLE_REQUIRE_BROWSER=1`` so a missing Chromium fails there instead.
"""
import os

import pytest


def launch_chromium(runtime, **kwargs):
    from playwright.sync_api import Error

    try:
        return runtime.chromium.launch(headless=True, **kwargs)
    except Error as exc:
        if os.environ.get("CUTTLE_REQUIRE_BROWSER") == "1":
            pytest.fail(f"Required Chromium unavailable: {exc}")
        pytest.skip(f"Chromium unavailable: {exc}")
