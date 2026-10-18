"""Phase 5F: Electron chrome/zoom and the mobile + SSH-approval shell moved
into ``shell/shell_electron.js`` and ``shell/shell_mobile.js``.

The shell (``app_shell.js``) keeps the composition root: it loads after the two
owners, forwards zoom postMessages, calls the per-pane zoom binder, re-arms the
titlebar auto-hide from the space-menu wiring, and delegates the mobile
server-switch click. These are source contracts, matching the rest of the shell
suite; the browser journeys live in ``src/tests/e2e/``.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SHELL = REPO / "src" / "web" / "js" / "shell"
SHELL_JS = SHELL / "app_shell.js"
ELECTRON_JS = SHELL / "shell_electron.js"
MOBILE_JS = SHELL / "shell_mobile.js"
HTML = REPO / "src" / "web" / "app_shell.html"


def test_shell_loads_owners_before_composition_root():
    html = HTML.read_text(encoding="utf-8")
    electron = html.index("/js/shell/shell_electron.js")
    mobile = html.index("/js/shell/shell_mobile.js")
    shell = html.index("/js/shell/app_shell.js")
    assert electron < shell
    assert mobile < shell


def test_electron_owner_holds_chrome_and_zoom():
    owner = ELECTRON_JS.read_text(encoding="utf-8")
    # Frameless titlebar chrome.
    assert "function initElectronTitlebar()" in owner
    assert "document.body.classList.add('is-electron')" in owner
    assert "api.windowControls.onMaximizedChange" in owner
    assert "const isTitlebarPinned = () => {" in owner
    assert "isSpaceCtxOpen()) return true;" in owner
    assert "window.__cuttleShellPollWorkers = refreshWorkersChip;" in owner
    # Zoom HUD + iframe binding.
    assert "function injectElectronZoomHandler(frameEl)" in owner
    assert "function setupShellZoomHud()" in owner
    assert "function handleZoomMessage(data)" in owner
    assert "document.getElementById('shellZoomHud')" in owner
    # Namespace export keeps the page-facing contract explicit.
    assert "root.CuttleShellElectron = {" in owner


def test_electron_owner_persists_zoom_across_reload():
    owner = ELECTRON_JS.read_text(encoding="utf-8")
    # webFrame zoom is frame-scoped and resets on reload; the owner stores it
    # per shell origin and re-applies it on startup.
    assert "const ZOOM_STORAGE_KEY = 'cuttle.shell.zoomPercent';" in owner
    assert "function readPersistedZoomPercent()" in owner
    assert "function persistZoomPercent(pct)" in owner
    assert "function restorePersistedZoom()" in owner
    assert "restorePersistedZoom();" in owner
    assert "persistZoomPercent(resolved);" in owner


def test_shell_delegates_electron_chrome_and_zoom():
    page = SHELL_JS.read_text(encoding="utf-8")
    # The space-menu titlebar hide scheduling moved to the Spaces owner (5C).
    spaces = (SHELL / "shell_spaces.js").read_text(encoding="utf-8")
    assert "CuttleShellElectron.handleZoomMessage(e.data);" in page
    assert "CuttleShellElectron.injectElectronZoomHandler(frameEl);" in page
    assert "CuttleShellElectron.rescheduleTitlebarHide();" in spaces
    assert "CuttleShellElectron.cancelTitlebarHide();" in spaces
    # One owner: the moved bodies are gone from the composition root.
    assert "function initElectronTitlebar(" not in page
    assert "function injectElectronZoomHandler(" not in page
    assert "function setupShellZoomHud(" not in page
    assert "function wireElectronChatFind(" not in page
    assert "cuttleTitlebarRescheduleHide" not in page
    assert "cuttleTitlebarCancelHide" not in page


def test_mobile_owner_holds_detection_update_and_ssh_approval():
    owner = MOBILE_JS.read_text(encoding="utf-8")
    assert "function isCuttleMobileShell()" in owner
    assert "function cuttleIsMobileClient()" in owner
    assert "function cuttleOpenMobileServerSettings()" in owner
    assert "function cuttleSyncMobileServerRows()" in owner
    assert "(function initMobileAppShell() {" in owner
    assert "(function initSshApprovalModal() {" in owner
    assert "id=\"sshApprovalModal\"" not in owner  # modal markup stays in HTML
    assert "'/api/workers/ssh-approval/'" in owner


def test_shell_delegates_mobile_and_keeps_shared_wiring():
    page = SHELL_JS.read_text(encoding="utf-8")
    # Shared click handler + account-panel sync delegate to the owner.
    assert "cuttleOpenMobileServerSettings();" in page
    assert "cuttleSyncMobileServerRows()" in page
    # Frame-retry spine still consumes the (owned) detector.
    panes = (SHELL_JS.parent / "shell_panes.js").read_text(encoding="utf-8")
    assert "host.isCuttleMobileShell() ? 7000" in panes
    # One owner: the mobile/SSH bodies are gone from the composition root.
    assert "function isCuttleMobileShell(" not in page
    assert "function cuttleIsMobileClient(" not in page
    assert "function initMobileAppShell(" not in page
    assert "function initSshApprovalModal(" not in page
    assert "cuttle-apk-update-later-hash" not in page
    assert "sshApprovalModal" not in page


def test_mobile_owner_asks_shell_for_rail_lock_capability():
    owner = MOBILE_JS.read_text(encoding="utf-8")
    page = SHELL_JS.read_text(encoding="utf-8")
    # The APK rail button is created by the mobile owner but the shell owns the
    # rail-edit state; the owner asks through an explicit window capability
    # instead of reading the page's private rail globals.
    assert "window.cuttleRailInteractionLocked" in page
    assert "window.cuttleRailInteractionLocked()" in owner
    assert "railEditing" not in owner
    assert "railSuppressClick" not in owner
