"""Durable batch-to-chat deliveries. Worker reports never choose host files/chats.

An operator registers the host-visible output directory and destination once.
Completion, watch reconciliation, or the CLI can retry safely without an LLM
or an open browser. Chat messages are the atomic delivery receipts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
from pathlib import Path
import re

from api.device_workers.store import _connect, _row_job, get_store

_FRAME = re.compile(r'^frame_(\d+)\.[A-Za-z0-9]+$', re.I)


def register(batch_id, session_id, output_dir, *, video_path='', encode_job_id='', store=None):
    store = store or get_store()
    from api.auth_db import get_auth_db
    if not batch_id or not any(j['type'] == 'blender_render' for j in store.list_batch_jobs(batch_id)):
        raise ValueError('Unknown render batch')
    session_id = int(str(session_id).removeprefix('CH-').removeprefix('db_session_'))
    if not get_auth_db().get_chat_session_by_id(session_id):
        raise ValueError('Chat no longer exists')
    if not output_dir or not Path(output_dir).is_absolute():
        raise ValueError('output_dir must be an absolute host-visible directory')
    root = Path(output_dir).resolve()
    video = Path(video_path).resolve() if video_path else None
    if bool(video) != bool(encode_job_id):
        raise ValueError('video_path and encode_job_id must be supplied together')
    if video:
        from api.shared_media import media_kind
        if not video.is_relative_to(root) or media_kind(video.name) != 'video':
            raise ValueError('Video must be inside output_dir and use a supported video extension')
        if not store.get_job(encode_job_id):
            raise ValueError('Unknown encoding job')
    spec = dict(session_id=session_id, output_dir=str(root),
                video_path=str(video) if video else '', encode_job_id=encode_job_id)
    conn = _connect(store.db_path)
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT spec_json FROM render_deliveries WHERE batch_id = ?',
                           (batch_id,)).fetchone()
        if row:
            previous = json.loads(row[0])
            if previous != spec and not (
                    previous['session_id'] == session_id and previous['output_dir'] == str(root)
                    and not previous['video_path'] and video):
                raise ValueError('Batch already bound; use a new batch for different outputs or chat')
        conn.execute('INSERT INTO render_deliveries VALUES (?, ?) '
                     'ON CONFLICT(batch_id) DO UPDATE SET spec_json = excluded.spec_json',
                     (batch_id, json.dumps(spec)))
        conn.commit()
    finally:
        conn.close()
    return reconcile(batch_id, store=store)


def _verified_frames(jobs, root):
    """Require a complete, nonempty host inventory; failed old shards may be repaired."""
    jobs = [j for j in jobs if j['type'] == 'blender_render']
    if not jobs or any(j['status'] not in ('succeeded', 'failed') for j in jobs):
        return None
    expected = set()
    for job in jobs:
        params = job['params']
        start, end = int(params['frame_start']), int(params['frame_end'])
        if end < start or end - start > 1000000:
            raise ValueError('Invalid render frame range')
        expected.update(range(start, end + 1))
    if not expected or not root.is_dir():
        return None
    from api.shared_media import media_kind
    frames = {}
    for path in sorted(root.iterdir()):
        match = _FRAME.fullmatch(path.name)
        if (match and path.is_file() and path.resolve().is_relative_to(root)
                and path.stat().st_size > 0 and media_kind(path.name) == 'image'):
            frame = int(match[1])
            if frame in expected:
                frames[frame] = path
    if not expected.issubset(frames):
        return None
    ordered = sorted(expected)
    preview = frames[ordered[len(ordered) // 2]]
    # Verify the selected image can actually decode before claiming a preview.
    from PIL import Image
    try:
        with Image.open(preview) as image:
            image.verify()
    except (OSError, ValueError):
        return None
    return preview


def reconcile(batch_id, *, store=None, session_id=None):
    from api.experimental import is_enabled
    if not is_enabled('render_result_attachments'):
        return {'success': True, 'state': 'disabled', 'attachments': []}
    store = store or get_store()
    conn = _connect(store.db_path)
    try:
        # Serialize staging across Flask/CLI processes; chat insert has its own
        # atomic receipt, so crashing before this transaction commits is safe.
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT spec_json FROM render_deliveries WHERE batch_id = ?',
                           (batch_id,)).fetchone()
        if not row:
            return {'success': True, 'state': 'unregistered', 'attachments': []}
        spec = json.loads(row[0])
        if session_id is not None and spec['session_id'] != session_id:
            raise ValueError('Attachment binding belongs to a different chat')
        from api.auth_db import get_auth_db
        db = get_auth_db()
        if not db.get_chat_session_by_id(spec['session_id']):
            return {'success': True, 'state': 'chat_deleted', 'attachments': []}
        root = Path(spec['output_dir'])
        # Use this transaction's reads, without acquiring the store's Python
        # lock while holding a SQLite write lock (completion uses the reverse).
        jobs = [_row_job(r) for r in conn.execute(
            "SELECT * FROM jobs WHERE json_extract(params_json, '$.batch_id') = ?",
            (batch_id,))]
        preview = _verified_frames(jobs, root)
        if preview is None:
            return {'success': True, 'state': 'waiting_for_frames', 'attachments': []}
        candidates = [('frames', preview, 'Render frames complete — representative frame')]
        video = Path(spec['video_path']) if spec['video_path'] else None
        encode_row = conn.execute('SELECT * FROM jobs WHERE id = ?',
                                  (spec['encode_job_id'],)).fetchone() if video else None
        encode = _row_job(encode_row) if encode_row else None
        if (encode and encode['status'] == 'succeeded' and video.is_file()
                and video.resolve().is_relative_to(root) and video.stat().st_size > 0):
            candidates.append(('video', video, 'Video encoding complete'))
        attachments = []
        from api.shared_media import stage_file
        for stage, source, label in candidates:
            key = 'render:' + hashlib.sha256(batch_id.encode()).hexdigest() + ':' + stage
            # Read only through the chat owner; staged URL is recovered on retry.
            receipt = db.get_completion_message(spec['session_id'], key)
            if receipt:
                attachments.append({'message_id': receipt['id'], 'stage': stage})
                continue
            staged = stage_file(source)
            if not staged.get('success'):
                return {'success': False, 'state': 'output_unavailable',
                        'error': staged.get('error'), 'attachments': attachments}
            metadata = {'render_result': {'batch_id': batch_id, 'stage': stage},
                        'attachments': [{'url': staged['url'], 'filename': source.name,
                                         'mime': mimetypes.guess_type(source.name)[0]}]}
            message_id = db.add_message_once(spec['session_id'],
                label + '\n\n![Render result](' + staged['url'] + ')',
                delivery_key=key, metadata=metadata)
            if message_id:
                attachments.append({'message_id': message_id, 'stage': stage})
        return {'success': True, 'state': 'delivered' if len(candidates) == 2 or not video else 'waiting_for_encode',
                'attachments': attachments}
    finally:
        conn.close()


def reconcile_job(store, job):
    """Called after a committed completion; workers cannot alter registered specs."""
    conn = _connect(store.db_path)
    try:
        rows = conn.execute(
            "SELECT batch_id FROM render_deliveries WHERE batch_id = ? "
            "OR json_extract(spec_json, '$.encode_job_id') = ?",
            ((job.get('params') or {}).get('batch_id', ''), job['id'])).fetchall()
    finally:
        conn.close()
    for row in rows:
        reconcile(row[0], store=store)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('verb', choices=('register', 'reconcile'))
    parser.add_argument('--batch-id', required=True)
    parser.add_argument('--session')
    parser.add_argument('--output-dir')
    parser.add_argument('--video-path', default='')
    parser.add_argument('--encode-job-id', default='')
    args = parser.parse_args()
    try:
        result = (register(args.batch_id, args.session, args.output_dir,
                           video_path=args.video_path, encode_job_id=args.encode_job_id)
                  if args.verb == 'register' else reconcile(args.batch_id))
    except (ValueError, TypeError, OSError) as exc:
        result = {'success': False, 'error': str(exc)}
    print(json.dumps(result))
    return 0 if result.get('success') else 1


if __name__ == '__main__':
    raise SystemExit(main())
