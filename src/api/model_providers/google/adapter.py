"""Gemini speech model/voice discovery and audio generation."""
import base64
import io
import os
import re
import wave
from urllib.parse import quote
import requests
from ..types import SpeechResult


def endpoint():
    return (os.getenv('GEMINI_BASE_URL') or 'https://generativelanguage.googleapis.com/v1beta').rstrip('/')


class Adapter:
    def discovery_identity(self, manifest):
        return endpoint() + '|' + manifest.api_key()

    def _get(self, manifest, path, key=None, **params):
        response = requests.get(endpoint() + path, headers={'x-goog-api-key': key or manifest.api_key()},
                                params=params, timeout=10)
        response.raise_for_status()
        return response.json()

    def check_key(self, manifest, key):
        self._get(manifest, '/models', key, pageSize=1)

    def _pages(self, manifest, path, field):
        token = ''
        seen = set()
        while True:
            data = self._get(manifest, path, **({('page_token' if field == 'voices' else 'pageToken'): token} if token else {}))
            yield from data.get(field, [])
            token = data.get('nextPageToken') or data.get('next_page_token') or ''
            if not token:
                return
            if token in seen:
                raise ValueError('Repeated catalog page token')
            seen.add(token)

    def list_models(self, manifest):
        rows = []
        for model in self._pages(manifest, '/models', 'models'):
            mid = str(model.get('name') or '').removeprefix('models/')
            if not mid:
                continue
            # Native method alone doesn't prove audio output. The model's TTS
            # family plus generateContent are known speech support.
            speech = 'tts' in mid.lower() and ('generateContent' in model.get('supportedGenerationMethods', []) or mid.startswith('gemini-3.8-'))
            rows.append({'id': mid, 'label': model.get('displayName') or mid,
                         'capabilities': {'operations': ['speech_generation'] if speech else []}})
        return rows

    def list_voices(self, manifest, model=''):
        voices = [{'id': v['id'], 'label': v.get('display_name') or v.get('displayName') or v['id']}
                  for v in self._pages(manifest, '/voices', 'voices') if v.get('id')]
        # Legacy models support the original 30 prebuilt voices, not the newer
        # library or custom voices. New models retain the native account list.
        if model.startswith(('gemini-2.5-', 'gemini-3.1-')):
            allowed = {v['id'] for v in manifest.speech['voices']}
            voices = [v for v in voices if v['id'] in allowed]
        return voices

    def synthesize_speech(self, manifest, request, capabilities):
        if request.voice.startswith(('voice_', 'voicekey_')):
            voice_config = {'voice': request.voice}
        else:
            voice_config = {'prebuiltVoiceConfig': {'voiceName': request.voice}}
        if capabilities.get('speech_transport') == 'interactions':
            return self._interactions(manifest, request)
        response = requests.post(endpoint() + '/models/' + quote(request.model, safe='') + ':generateContent',
                                 headers={'x-goog-api-key': manifest.api_key()},
                                 json={'contents': [{'parts': [{'text': request.text[:8000]}]}],
                                       'generationConfig': {'responseModalities': ['AUDIO'],
                                                            'speechConfig': {'voiceConfig': voice_config}}},
                                 timeout=request.timeout)
        response.raise_for_status()
        data = response.json()
        for candidate in data.get('candidates', []):
            for part in (candidate.get('content') or {}).get('parts', []):
                inline = part.get('inlineData') or {}
                if not inline.get('data'):
                    continue
                audio = base64.b64decode(inline['data'], validate=True)
                mime = str(inline.get('mimeType') or 'audio/wav')
                if mime.lower().startswith(('audio/l16', 'audio/pcm')):
                    rate = re.search(r'rate=(\d+)', mime)
                    output = io.BytesIO()
                    with wave.open(output, 'wb') as wav:
                        wav.setnchannels(1)
                        wav.setsampwidth(2)
                        wav.setframerate(int(rate.group(1)) if rate else 24000)
                        wav.writeframes(audio)
                    audio, mime = output.getvalue(), 'audio/wav'
                use = data.get('usageMetadata') or {}
                return SpeechResult(audio, mime.split(';')[0],
                                    {'input_tokens': int(use.get('promptTokenCount') or 0),
                                     'output_tokens': int(use.get('candidatesTokenCount') or 0)})
        raise RuntimeError('Google TTS returned no audio')

    def _interactions(self, manifest, request):
        response = requests.post(endpoint() + '/interactions', headers={'x-goog-api-key': manifest.api_key()},
                                 json={'model': request.model, 'input': request.text[:8000],
                                       'response_format': {'type': 'audio', 'mime_type': 'audio/wav'},
                                       'generation_config': {'speech_config': [{'voice': request.voice}]},
                                       'store': False}, timeout=request.timeout)
        response.raise_for_status()
        data = response.json()
        for step in reversed(data.get('steps', [])):
            if step.get('type') != 'model_output':
                continue
            for content in step.get('content', []):
                if content.get('type') == 'audio' and content.get('data'):
                    usage = data.get('usage') or {}
                    return SpeechResult(base64.b64decode(content['data'], validate=True),
                                        content.get('mime_type') or 'audio/wav',
                                        {'input_tokens': int(usage.get('total_input_tokens') or 0),
                                         'output_tokens': int(usage.get('total_output_tokens') or 0)})
        raise RuntimeError('Google TTS returned no audio')


def build_adapter():
    return Adapter()
