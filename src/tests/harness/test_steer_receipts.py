from api.agent_harness.steer_receipts import Receipts


def test_rapid_echoes_buffer_until_bound_and_share_clipped_reply():
    changes = []
    tracker = Receipts(changes.append)
    a = tracker.begin('one', 'turn')
    b = tracker.begin('two', 'turn')
    tracker.echo('one', 'item1')
    tracker.echo('two', 'item2')
    tracker.reply('x' * 300 + '\n\nignored')
    tracker.accepted(a, True)
    tracker.accepted(b, True)
    tracker.bind(a, 10)
    tracker.bind(b, 11)
    assert changes[-2]['message_id'] == 10
    assert changes[-1]['message_id'] == 11
    assert changes[-1]['reply'] == {'text': 'x' * 280, 'at': changes[-1]['reply']['at'], 'covers': [a, b]}
    assert tracker.snapshot(a)['reply'] == tracker.snapshot(b)['reply']


def test_completion_before_delivery_response_marks_undelivered():
    tracker = Receipts()
    a = tracker.begin('late', 'turn')
    tracker.finish()
    tracker.accepted(a, True)
    assert tracker.snapshot(a)['state'] == 'undelivered'
    assert tracker.snapshot(a)['reply'] is None


def test_recorded_codex_rapid_steers():
    import json
    from pathlib import Path

    events = json.loads((Path(__file__).parent / 'fixtures/codex_rapid_steers.json').read_text())
    tracker = Receipts()
    ids = [tracker.begin(event['text'], 'turn') for event in events if event['kind'] == 'echo']
    for event in events:
        if event['kind'] == 'echo':
            tracker.echo(event['text'], event['item_id'])
        else:
            tracker.reply(event['text'])
    assert tracker.snapshot(ids[0])['reply']['covers'] == ids
    assert tracker.snapshot(ids[1])['reply']['text'].startswith('Acknowledged: amber and cobalt')


def test_registry_buffers_echo_and_reply_before_row_insert(tmp_path):
    import concurrent.futures
    import json
    import sqlite3
    from api.agent_harness import steer, steer_receipts
    from api.chat_coordinator import steered_user_meta
    from api.chat_turn_persist import persist_auth_user_message

    class DB:
        def _get_connection(self):
            conn = sqlite3.connect(tmp_path / 'test.db')
            conn.row_factory = sqlite3.Row
            return conn
        def add_message(self, sid, role, text, metadata):
            conn = self._get_connection()
            cur = conn.execute('INSERT INTO chat_messages (role, metadata) VALUES (?, ?)', (role, json.dumps(metadata)))
            conn.commit()
            conn.close()
            return cur.lastrowid

    db = DB()
    conn = db._get_connection()
    conn.execute('CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, role TEXT, metadata TEXT)')
    conn.commit()
    conn.close()
    token = None
    def send(text):
        steer.observe('901', token, {'method': 'item/completed', 'params': {'item': {'type': 'userMessage', 'id': 'u1', 'content': [{'type': 'text', 'text': text}]}}})
        steer.observe('901', token, {'method': 'item/completed', 'params': {'item': {'type': 'agentMessage', 'text': 'Acknowledged.\n\nFurther text.'}}})
        f = concurrent.futures.Future()
        f.set_result((True, None))
        return f
    token = steer.register('901', 'codex', send)
    info = steer.steer('901', 'do it')
    persist_auth_user_message(db, 901, 'do it', metadata=steered_user_meta(info), project_merge_fn=lambda m, *_: m)
    conn = db._get_connection()
    meta = json.loads(conn.execute('SELECT metadata FROM chat_messages').fetchone()['metadata'])
    conn.close()
    assert meta['steer']['reply']['text'] == 'Acknowledged.'
    assert meta['steer']['covers'] == [info['steer_id']]
    assert meta['steer']['delivered'] is True
    assert steer_receipts.live('901')[-1]['message_id'] == 1
    steer.unregister('901', token)


def test_no_ack_and_accepted_after_turn_end():
    tracker = Receipts()
    first = tracker.begin('read', 'turn')
    tracker.accepted(first, True)
    tracker.echo('read', 'one')
    late = tracker.begin('late', 'turn')
    tracker.finish()
    tracker.accepted(late, True)
    assert tracker.snapshot(first)['reply'] is None
    assert tracker.snapshot(first)['state'] == 'delivered'
    assert tracker.snapshot(late)['state'] == 'undelivered'
