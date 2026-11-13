"""Settings → Voice → Speech to text: provider, model, live text and clip length."""
import json
from urllib.parse import urlparse

from playwright.sync_api import expect

from .control_helpers import choose
from .test_settings_voice_providers import VoiceWorld, show_voice
from .test_settings_agents import open_settings
from .test_shared_diff_modal import browser, static_server  # noqa: F401


class SttWorld(VoiceWorld):
    def __init__(self):
        super().__init__()
        self.stt = {'provider': 'openai', 'models': {}, 'local_base_url': '', 'live_text': True, 'max_clip_minutes': 10}
        self.stt_saves = []
        self.stt_error = None

    def models(self, pid, op):
        if op != 'audio_transcription':
            return super().models(pid, op)
        mids = {'openai': ['gpt-4o-mini-transcribe', 'whisper-1'], 'elevenlabs': ['scribe_v2']}.get(pid, [])
        return {'success': True, 'source': 'provider_api', 'models': [
            {'id': m, 'label': m, 'compatibility': 'supported', 'availability': 'available', 'capabilities': {}} for m in mids]}

    def handle(self, route):
        if urlparse(route.request.url).path != '/api/settings/voice-stt':
            return super().handle(route)
        if route.request.method == 'POST':
            self.stt_saves.append(json.loads(route.request.post_data)['voice_stt'])
            if self.stt_error:
                route.fulfill(status=400, content_type='application/json',
                              body=json.dumps({'success': False, 'error': self.stt_error}))
                return
            self.stt = self.stt_saves[-1]
        rows = [
            {'id': 'openai', 'label': 'OpenAI', 'configured': False, 'models': [], 'default_model': 'gpt-4o-mini-transcribe'},
            {'id': 'local', 'label': 'Local', 'configured': True, 'models': [], 'default_model': 'whisper-1', 'local': True},
            {'id': 'elevenlabs', 'label': 'ElevenLabs', 'configured': True, 'models': [{'id': 'scribe_v2'}], 'default_model': 'scribe_v2'},
            {'id': 'browser', 'label': 'Browser speech recognition (no API key)', 'configured': True, 'models': [], 'browser': True},
        ]
        route.fulfill(content_type='application/json', body=json.dumps(
            {'success': True, 'voice_stt': self.stt, 'providers': rows, 'clip_minutes_range': [1, 30]}))


def _pick(page, select, value):
    if select.locator('..').locator('.cuttle-combo-trigger').count():
        choose(page, select, value)
    else:
        select.select_option(value)


def test_speech_to_text_provider_model_live_text_and_clip_length(browser, static_server):
    world = SttWorld()
    page = open_settings(browser, static_server, world)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        show_voice(page)
        group = page.locator('#voiceSttSettings')
        provider = group.locator('select[aria-label="Speech-to-text provider"]')
        model = group.locator('select[aria-label="Speech-to-text model"]')
        model_row = group.locator('.setting-item', has=page.locator('select[aria-label="Speech-to-text model"]'))
        minutes = group.get_by_label('Longest voice clip in minutes', exact=True)
        address = group.get_by_label('Local speech-to-text server address', exact=True)
        live = group.get_by_role('switch', name='Type what I say into the message')
        expect(provider).to_have_value('openai')
        expect(minutes).to_have_value('10')
        expect(live).to_have_attribute('aria-checked', 'true')
        # An unconfigured provider says where its key goes; nothing was saved by loading.
        expect(group).to_contain_text('Add its API key in Settings → Providers first.')
        expect(address).to_be_hidden()
        expect(model_row).to_be_visible()
        assert world.stt_saves == []
        expect(model.locator('option[value="whisper-1"]')).to_have_count(1)
        _pick(page, model, 'whisper-1')
        # A local server: its address appears and audio stays on the user's machine.
        _pick(page, provider, 'local')
        expect(address).to_be_visible()
        expect(group).to_contain_text('Audio goes only to your own server')
        address.fill('http://127.0.0.1:8000/v1')
        # The browser recognizer has no model to pick.
        _pick(page, provider, 'browser')
        expect(model_row).to_be_hidden()
        expect(group).to_contain_text('no API key and nothing passes through this server')
        _pick(page, provider, 'local')
        expect(model_row).to_be_visible()
        live.click()
        minutes.fill('3')
        group.get_by_role('button', name='Save speech to text').click()
        page.wait_for_function('() => document.body.textContent.includes("Speech to text saved")')
        expect(group.get_by_role('button', name='Save speech to text')).to_be_enabled()
        assert world.stt_saves == [{'provider': 'local', 'models': {'openai': 'whisper-1'},
                                    'local_base_url': 'http://127.0.0.1:8000/v1', 'live_text': False, 'max_clip_minutes': 3}]
        # A rejected value is reported, not silently dropped.
        world.stt_error = 'Longest voice clip must be 1 to 30 minutes.'
        minutes.fill('99')
        group.get_by_role('button', name='Save speech to text').click()
        expect(page.locator('body')).to_contain_text('Longest voice clip must be 1 to 30 minutes.')
        assert errors == []
    finally:
        page.close()
