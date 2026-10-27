"""gzip for text responses + the sessions-list validator (LAN Client load time).

A Client refresh used to move ~13 MB of uncompressed assets (one bundle per
pane: Chromium never caches a self-signed Host) and the activity broker
re-read the ~0.8 MB session list on every change pulse.
"""
import gzip
import json
from pathlib import Path

import pytest
from flask import Flask, Response, jsonify, send_from_directory

from api import http_compression

GZ = {'Accept-Encoding': 'gzip, deflate, br'}
REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def app(tmp_path):
    http_compression.clear_cache()
    (tmp_path / 'big.js').write_text('console.log("cuttle");\n' * 400, encoding='utf-8')
    (tmp_path / 'tiny.js').write_text('x=1', encoding='utf-8')
    (tmp_path / 'clip.mp4').write_bytes(b'\0' * 4096)
    app = Flask(__name__)
    http_compression.install(app)

    @app.route('/assets/<path:name>')
    def static_file(name):
        return send_from_directory(tmp_path, name)

    @app.route('/json')
    def big_json():
        return jsonify({'rows': [{'id': i, 'name': f'chat {i}'} for i in range(300)]})

    @app.route('/sse')
    def sse():
        def gen():
            yield 'event: activity\ndata: ' + 'x' * 2048 + '\n\n'
        return Response(gen(), mimetype='text/event-stream')

    yield app
    http_compression.clear_cache()


def test_static_js_is_gzipped_with_weak_etag_and_revalidates(app):
    client = app.test_client()
    raw = client.get('/assets/big.js')
    assert 'Content-Encoding' not in raw.headers
    r = client.get('/assets/big.js', headers=GZ)
    assert r.headers['Content-Encoding'] == 'gzip'
    assert 'Accept-Encoding' in r.headers['Vary']
    assert gzip.decompress(r.data) == raw.data
    assert len(r.data) < len(raw.data) // 5
    etag = r.headers['ETag']
    assert etag.startswith('W/')
    assert client.get('/assets/big.js', headers={**GZ, 'If-None-Match': etag}).status_code == 304
    # Second hit serves the cached compressed body.
    assert client.get('/assets/big.js', headers=GZ).data == r.data


def test_json_api_is_gzipped(app):
    r = app.test_client().get('/json', headers=GZ)
    assert r.headers['Content-Encoding'] == 'gzip'
    assert len(json.loads(gzip.decompress(r.data))['rows']) == 300


@pytest.mark.parametrize('path,headers', [
    ('/sse', GZ),                                   # streams stay streamed
    ('/assets/tiny.js', GZ),                        # not worth it
    ('/assets/clip.mp4', GZ),                       # not text
    ('/assets/big.js', {}),                         # client did not ask
    ('/assets/big.js', {'Accept-Encoding': 'gzip;q=0'}),
    ('/assets/big.js', {**GZ, 'Range': 'bytes=0-99'}),
])
def test_skipped_responses_are_untouched(app, path, headers):
    r = app.test_client().get(path, headers=headers)
    assert 'Content-Encoding' not in r.headers


def test_installed_before_html_stamping_hook():
    """Flask runs after_request hooks in reverse: compression must be first."""
    src = (REPO / 'src/api/web_chat_api.py').read_text(encoding='utf-8')
    assert src.index('http_compression.install(app)') < src.index('def _no_cache_ui_assets')


def test_sessions_list_answers_304_when_unchanged(owner_session):
    from api.web_chat_api import app as cuttle_app
    client = owner_session.sign_in(cuttle_app.test_client())
    sid = owner_session.db.create_chat_session(owner_session.user_id, 'Validator')
    owner_session.db.add_message(sid, 'user', 'hello')  # empty chats are hidden
    first = client.get('/api/auth/sessions')
    etag = first.headers['ETag']
    assert first.status_code == 200 and etag
    assert any(int(s['id']) == sid for s in first.get_json()['sessions'])
    assert client.get('/api/auth/sessions', headers={'If-None-Match': etag}).status_code == 304
    owner_session.db.rename_chat_session(sid, owner_session.user_id, 'Renamed')
    changed = client.get('/api/auth/sessions', headers={'If-None-Match': etag})
    assert changed.status_code == 200
    assert changed.headers['ETag'] != etag


def test_shell_broker_revalidates_session_list():
    src = (REPO / 'src/web/js/shell/shell_spaces.js').read_text(encoding='utf-8')
    load = src.split('async function loadActivitySessions()', 1)[1].split('/** Not authenticated', 1)[0]
    assert "'If-None-Match': activitySessionsEtag" in load
    assert 'r.status === 304 && activitySessionsRows' in load
    # Another account must never reuse the previous user's rows.
    assert 'user !== activitySessionsUser' in load
