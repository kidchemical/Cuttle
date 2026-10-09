"""ElevenLabs account models, voice library, and speech transport."""
import os
from urllib.parse import quote
import requests
from ..types import SpeechResult


def endpoint():
    return (os.getenv('ELEVENLABS_BASE_URL') or 'https://api.elevenlabs.io').rstrip('/')


class Adapter:
    def discovery_identity(self, manifest):
        return endpoint() + '|' + manifest.api_key()

    def _get(self, manifest, path, key=None):
        response = requests.get(endpoint() + path, headers={'xi-api-key': key or manifest.api_key()}, timeout=10)
        response.raise_for_status()
        return response.json()

    def check_key(self, manifest, key):
        if len(key) < 20:
            raise ValueError('Invalid API key format')
        self._get(manifest, '/v1/user', key)

    def list_models(self, manifest):
        return [{'id': m['model_id'], 'label': m.get('name') or m['model_id'],
                 'capabilities': {'operations': ['speech_generation'] if m.get('can_do_text_to_speech') else [],
                                  'max_input_characters': m.get('maximum_text_length_per_request')}}
                for m in self._get(manifest, '/v1/models') if m.get('model_id')]

    def list_voices(self, manifest, model=''):
        return [{'id': v['voice_id'], 'label': v.get('name') or v['voice_id']}
                for v in self._get(manifest, '/v1/voices').get('voices', []) if v.get('voice_id')]

    def synthesize_speech(self, manifest, request, capabilities):
        limit = capabilities.get('max_input_characters') or 5000
        body = {'text': request.text[:limit], 'model_id': request.model}
        settings = {}
        if request.speed is not None:
            settings['speed'] = request.speed
        if request.stability is not None:
            settings['stability'] = request.stability
        if settings:
            body['voice_settings'] = settings
        response = requests.post(endpoint() + '/v1/text-to-speech/' + quote(request.voice, safe=''),
                                 headers={'xi-api-key': manifest.api_key(), 'Content-Type': 'application/json'},
                                 params={'output_format': 'mp3_44100_128'}, json=body, timeout=request.timeout)
        response.raise_for_status()
        if not response.content:
            raise RuntimeError('TTS returned empty audio')
        return SpeechResult(bytes(response.content))


def build_adapter():
    return Adapter()
