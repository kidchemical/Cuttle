"""Real private SQLite receipts, worker completion hooks, staging, and retries."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image

from api.device_workers import render_results as rr


@pytest.fixture
def env(tmp_path, monkeypatch):
    from api import auth_db, shared_media
    from api.device_workers.store import DeviceWorkerStore
    db = auth_db.AuthDatabase(tmp_path / 'chat.db')
    uid = db.create_user('render@example.test', 'Render', 'local')
    sid = db.create_chat_session(uid, 'Render')
    monkeypatch.setattr(auth_db, 'get_auth_db', lambda: db)
    monkeypatch.setattr(shared_media, 'shared_media_root', lambda project_root=None: tmp_path / 'shared')
    from api.experimental import set_enabled
    set_enabled('render_result_attachments', True)
    store = DeviceWorkerStore(tmp_path / 'workers.db')
    root = tmp_path / 'frames'
    root.mkdir()
    for frame in (1, 2):
        Image.new('RGB', (4, 4)).save(root / f'frame_{frame:04d}.png')
    store.submit_job(job_type='blender_render', job_id='render',
                     params={'batch_id': 'batch', 'frame_start': 1, 'frame_end': 2,
                             'output_dir': str(root)})
    store.claim_jobs(worker_id='worker', capabilities={'blender': True})
    return store, db, sid, root


def finish(env):
    store, db, sid, root = env
    assert store.complete_job('render', 'worker', result={'frames_written': 2})
    return rr.register('batch', sid, str(root), store=store)


def messages(db, sid):
    conn = db._get_connection()
    try:
        return [dict(r) for r in conn.execute('SELECT * FROM chat_messages WHERE chat_session_id = ?', (sid,))]
    finally:
        conn.close()


def test_worker_completion_without_browser_saves_attachment(env):
    store, db, sid, root = env
    assert rr.register('batch', sid, str(root), store=store)['state'] == 'waiting_for_frames'
    assert store.complete_job('render', 'worker', result={})
    rows = messages(db, sid)
    assert len(rows) == 1
    assert 'representative frame' in rows[0]['content']
    assert '/output/shared/' in rows[0]['content']
    assert len(list((root.parent / 'shared').glob('*.png'))) == 1


def test_reconcile_concurrently_and_after_reopen_is_once(env):
    store, db, sid, root = env
    finish(env)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: rr.reconcile('batch', store=store), range(4)))
    from api.device_workers.store import DeviceWorkerStore
    rr.reconcile('batch', store=DeviceWorkerStore(store.db_path))
    assert len(messages(db, sid)) == 1
    assert len(list((root.parent / 'shared').glob('*.png'))) == 1
    assert all(r['attachments'][0]['message_id'] == results[0]['attachments'][0]['message_id'] for r in results)


def test_missing_frame_retries_after_copy_without_false_success(env):
    store, db, sid, root = env
    (root / 'frame_0002.png').unlink()
    assert finish(env)['state'] == 'waiting_for_frames'
    assert not messages(db, sid)
    Image.new('RGB', (4, 4)).save(root / 'frame_0002.png')
    assert rr.reconcile('batch', store=store)['state'] == 'delivered'
    assert len(messages(db, sid)) == 1


def test_zero_length_and_escaped_symlink_not_verified(env):
    store, db, sid, root = env
    (root / 'frame_0002.png').write_bytes(b'')
    assert finish(env)['state'] == 'waiting_for_frames'
    (root / 'frame_0002.png').unlink()
    outside = root.parent / 'outside.png'
    Image.new('RGB', (4, 4)).save(outside)
    (root / 'frame_0002.png').symlink_to(outside)
    assert rr.reconcile('batch', store=store)['state'] == 'waiting_for_frames'


def test_video_requires_successful_encoding_then_delivers_separate_stage(env, monkeypatch):
    store, db, sid, root = env
    finish(env)
    store.submit_job(job_type='shell', job_id='encode', params={'command': 'unused', 'batch_id': 'batch'})
    video = root / 'render.mp4'
    video.write_bytes(b'encoded-test-fixture')
    from api import shared_media
    monkeypatch.setattr(shared_media, 'ensure_video_poster', lambda *a, **k: {'success': False})
    rr.register('batch', sid, str(root), video_path=str(video), encode_job_id='encode', store=store)
    assert len(messages(db, sid)) == 1  # Existing file alone is never encode success.
    store.claim_jobs(worker_id='worker', capabilities={'shell': True})
    assert store.complete_job('encode', 'worker', result={})
    assert len(messages(db, sid)) == 2
    assert 'encoding complete' in messages(db, sid)[1]['content']
    rr.reconcile('batch', store=store)
    assert len(messages(db, sid)) == 2


def test_disabled_and_cancelled_never_attach(env):
    store, db, sid, root = env
    from api.experimental import set_enabled
    set_enabled('render_result_attachments', False)
    assert finish(env)['state'] == 'disabled'
    assert not messages(db, sid)
    set_enabled('render_result_attachments', True)
    from api.device_workers.store import _connect
    conn = _connect(store.db_path)
    conn.execute("UPDATE jobs SET status='cancelled' WHERE id='render'")
    conn.commit()
    conn.close()
    assert rr.reconcile('batch', store=store)['state'] == 'waiting_for_frames'
    assert not messages(db, sid)


def test_binding_cannot_be_redirected_or_escape_directory(env):
    store, db, sid, root = env
    finish(env)
    with pytest.raises(ValueError, match='already bound'):
        rr.register('batch', sid, str(root.parent), store=store)
    store.submit_job(job_type='shell', job_id='encode', params={})
    with pytest.raises(ValueError, match='inside output_dir'):
        rr.register('batch', sid, str(root), video_path=str(root.parent / 'outside.mp4'),
                    encode_job_id='encode', store=store)
    with pytest.raises(ValueError, match='different chat'):
        rr.reconcile('batch', store=store, session_id=sid+1)


def test_corrupt_preview_remains_retryable(env):
    store, db, sid, root = env
    (root / 'frame_0002.png').write_bytes(b'not an image')
    assert finish(env)['state'] == 'waiting_for_frames'
    assert not messages(db, sid)
    Image.new('RGB', (4, 4)).save(root / 'frame_0002.png')
    assert rr.reconcile('batch', store=store)['state'] == 'delivered'


def test_deleted_chat_is_not_recreated(env):
    store, db, sid, root = env
    finish(env)
    conn = db._get_connection()
    conn.execute('DELETE FROM chat_messages WHERE chat_session_id = ?', (sid,))
    conn.execute('DELETE FROM chat_sessions WHERE id = ?', (sid,))
    conn.commit()
    conn.close()
    assert rr.reconcile('batch', store=store)['state'] == 'chat_deleted'
    assert not messages(db, sid)


def test_staging_failure_can_retry(env, monkeypatch):
    store, db, sid, root = env
    from api import shared_media
    original = shared_media.stage_file
    monkeypatch.setattr(shared_media, 'stage_file', lambda *a, **k: {'success': False, 'error': 'unavailable'})
    assert finish(env)['state'] == 'output_unavailable'
    assert not messages(db, sid)
    monkeypatch.setattr(shared_media, 'stage_file', original)
    assert rr.reconcile('batch', store=store)['state'] == 'delivered'


def test_atomic_chat_receipt_concurrent(env):
    _, db, sid, _ = env
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: db.add_message_once(sid, 'Done', delivery_key='one'), range(8)))
    assert len(set(ids)) == 1
    assert len(messages(db, sid)) == 1


def test_http_binding_requires_operator_and_chat_access(env, monkeypatch):
    from flask import Flask, jsonify
    from api.device_workers import routes
    from api import http_authz
    store, db, sid, root = env
    monkeypatch.setattr('api.device_workers.store.get_store', lambda: store)
    # render_results has a local imported binding too.
    monkeypatch.setattr(rr, 'get_store', lambda: store)
    app = Flask(__name__)
    app.register_blueprint(routes.workers_bp)
    client = app.test_client()
    monkeypatch.setattr(routes, 'require_ui_operator',
                        lambda: (None, (jsonify(error='Owner required'), 403)))
    response = client.post('/api/workers/jobs/batch/batch/attachment',
                           json={'session_id': sid, 'output_dir': str(root)},
                           headers={'Authorization': 'Bearer worker-token'})
    assert response.status_code == 403
    monkeypatch.setattr(routes, 'require_ui_operator', lambda: ({'id': 1}, None))
    monkeypatch.setattr(http_authz, 'require_chat_session_access',
                        lambda value: (None, None, (jsonify(error='Chat denied'), 404)))
    assert client.post('/api/workers/jobs/batch/batch/attachment',
                       json={'session_id': sid, 'output_dir': str(root)}).status_code == 404
    monkeypatch.setattr(http_authz, 'require_chat_session_access', lambda value: ({'id': 1}, sid, None))
    assert client.post('/api/workers/jobs/batch/batch/attachment',
                       json={'session_id': sid, 'output_dir': str(root)}).get_json()['state'] == 'waiting_for_frames'
    assert store.complete_job('render', 'worker', result={})
    response = client.post('/api/workers/jobs/batch/batch/attachment',
                           json={'session_id': sid, 'reconcile': True})
    assert response.status_code == 200
    assert len(messages(db, sid)) == 1
