"""Wallpaper playback survives removal of the old media overlay page."""
from pathlib import Path
from .test_shared_diff_modal import browser  # noqa: F401

REPO = Path(__file__).resolve().parents[3]


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
        page.add_script_tag(path=str(REPO / 'src/web/js/video_background.js'))
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
