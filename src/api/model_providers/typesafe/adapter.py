"""TypeSafe System One transport. Jev owns question/answer interpretation."""
import json
import os
import urllib.error
import urllib.request
from ..types import ProviderError


class Adapter:
    def discovery_identity(self, manifest):
        return (os.getenv('CUTTLE_JEV_BASE_URL') or 'https://api.typesafe.ai') + '|' + manifest.api_key()

    def list_models(self, manifest):
        raise RuntimeError('This provider has no model discovery endpoint')

    def decide(self, manifest, request):
        payload = {'state': request.state, 'model': request.model, 'questions': request.questions}
        req = urllib.request.Request(request.base_url.rstrip('/') + '/v1/systemone',
                                     data=json.dumps(payload).encode('utf-8'), method='POST',
                                     headers={'Authorization': 'Bearer ' + request.api_key,
                                              'Content-Type': 'application/json', 'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=request.timeout) as response:
                raw = response.read().decode('utf-8', errors='replace')
                status = int(getattr(response, 'status', 200) or 200)
        except urllib.error.HTTPError as exc:
            raise ProviderError(f'Jev HTTP {exc.code}', status=exc.code,
                                retryable=exc.code in (408, 409, 429) or exc.code >= 500) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderError('Jev transport unavailable', retryable=True) from exc
        if status >= 400:
            raise ProviderError(f'Jev HTTP {status}', status=status, retryable=status in (408, 409, 429) or status >= 500)
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise ProviderError('Jev response was not JSON') from exc
        if not isinstance(data, dict):
            raise ProviderError('Jev response was not an object')
        return data


def build_adapter():
    return Adapter()
