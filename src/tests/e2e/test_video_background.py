"""Wallpaper playback survives removal of the old media overlay page."""
from pathlib import Path
from .test_shared_diff_modal import browser  # noqa: F401
from .test_app_shell_layout import ShellWorld, _open, _load_shell, V1_FLAT
from .test_shared_diff_modal import static_server  # noqa: F401

REPO = Path(__file__).resolve().parents[3]


def test_live_wallpaper_survives_theme_changes_in_split_shell(browser, static_server):
    context, page, errors, _blocked = _open(browser, static_server, ShellWorld(), seed=V1_FLAT)
    try:
        _load_shell(page, static_server)
        page.wait_for_function("""() => document.querySelectorAll('.split-column iframe').length === 2
            && [...document.querySelectorAll('.split-column iframe')].every(f =>
                f.contentWindow.CuttleUiBoot && f.contentWindow.CuttleVideoBackground)""")
        # Start playback after theme boot, as when a wallpaper is first selected.
        page.evaluate("CuttleVideoBackground.playNow('/wallpaper.mp4')")
        page.wait_for_function("""() => [...document.querySelectorAll('.split-column iframe')]
            .every(f => f.contentDocument.body.classList.contains('has-video-background'))""")
        for theme in ('light', 'dark', 'light'):
            page.evaluate("""theme => document.querySelector('.split-column iframe')
                .contentWindow.CuttleUiBoot.applyTheme(theme)""", theme)
            page.wait_for_function("""theme => [...document.querySelectorAll('.split-column iframe')]
                .every(f => f.contentWindow.CuttleUiBoot.currentTheme() === theme)""", arg=theme)
            surfaces = page.evaluate("""() => [document, ...[...document.querySelectorAll(
                '.split-column iframe')].map(f => f.contentDocument)].map(doc => ({
                    active: doc.body.classList.contains('has-video-background'),
                    background: doc.defaultView.getComputedStyle(doc.body).backgroundColor,
                }))""")
            assert all(s['active'] for s in surfaces), (theme, surfaces)
            assert all(s['background'] == 'rgba(0, 0, 0, 0)' for s in surfaces), (theme, surfaces)
        # Disabling playback must also survive later theme changes.
        page.evaluate("CuttleVideoBackground.setEnabled(false); CuttleUiBoot.applyTheme('dark')")
        assert not page.evaluate("document.body.classList.contains('has-video-background')")
        assert not errors
    finally:
        context.close()


def test_wallpaper_init_play_now_and_disable(browser):
    context = browser.new_context()
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('**/*', lambda route: route.fulfill(
        body='<html><body></body></html>' if route.request.resource_type == 'document' else '{}',
        content_type='text/html' if route.request.resource_type == 'document' else 'application/json',
    ))
    try:
        page.goto('http://wallpaper.test/')
        page.evaluate("localStorage.setItem('cuttleVideoBackgroundList', JSON.stringify(['/first.mp4']))")
        page.add_script_tag(path=str(REPO / 'src/web/js/media/video_background.js'))
        page.evaluate('CuttleVideoBackground.init()')
        video = page.locator('#cuttle-video-background video')
        assert video.count() == 1
        assert video.locator('source').get_attribute('src') == '/first.mp4'
        assert video.evaluate('(v) => v.loop && v.muted && v.autoplay')
        assert page.evaluate("CuttleVideoBackground.playNow('/second.webm')")
        assert video.locator('source').get_attribute('src') == '/second.webm'
        page.evaluate('CuttleVideoBackground.setEnabled(false)')
        assert page.locator('#cuttle-video-background').count() == 0
        assert not page.evaluate("document.body.classList.contains('has-video-background')")
        assert not errors
    finally:
        context.close()
