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


def test_recorded_claude_and_muse_stream_events():
    import concurrent.futures
    import json
    from pathlib import Path
    from api.agent_harness import steer, steer_receipts

    for agent, name in [('claude', 'claude-tool'), ('muse', 'muse-tool')]:
        fixture = json.loads((Path(__file__).parent / 'fixtures' / (name + '.json')).read_text())
        token = None
        def send(text):
            for event in fixture['events']:
                steer.observe('902', token, event)
            future = concurrent.futures.Future()
            future.set_result((True, None))
            return future
        token = steer.register('902', agent, send)
        info = steer.steer('902', fixture['sent'])
        with steer_receipts._pending_lock:
            tracker = steer_receipts._pending[info['steer_id']]['tracker']
        receipt = tracker.snapshot(info['steer_id'])
        assert receipt['delivered_at']
        assert receipt['reply']['text'] == ('Noted, marigold. The shell command finished: it waited 8 seconds and printed `READY`. I haven\'t edited any files.' if agent == 'claude' else 'Command finished with output `READY`.')
        steer.unregister('902', token)
        steer_receipts.discard(info['steer_id'])


def test_detached_turn_id_is_registered_before_delivery(monkeypatch):
    import concurrent.futures
    from api.agent_harness import steer, steer_receipts
    def send(text):
        f = concurrent.futures.Future()
        f.set_result((True, None))
        return f
    token = steer.register('903', 'claude', send)
    info = steer.steer('903', 'follow up', turn_id='original-turn')
    with steer_receipts._pending_lock:
        tracker = steer_receipts._pending[info['steer_id']]['tracker']
    assert tracker.snapshot(info['steer_id'])['turn_id'] == 'original-turn'
    steer.unregister('903', token)
    steer_receipts.discard(info['steer_id'])


def test_bound_rows_update_for_grouped_reply_and_undelivered(tmp_path):
    import concurrent.futures
    import json
    import sqlite3
    from api.agent_harness import steer, steer_receipts
    from api.chat_coordinator import steered_user_meta
    from api.chat_turn_persist import persist_auth_user_message

    class DB:
        def _get_connection(self):
            conn = sqlite3.connect(tmp_path / 'group.db')
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
    def send(text):
        future = concurrent.futures.Future()
        future.set_result((True, None))
        return future
    token = steer.register('904', 'codex', send)
    infos = []
    for text in ('one', 'two', 'never echoed'):
        info = steer.steer('904', text)
        infos.append(info)
        persist_auth_user_message(db, 904, text, metadata=steered_user_meta(info), project_merge_fn=lambda m, *_: m)
    for i, text in enumerate(('one', 'two')):
        steer.observe('904', token, {'method': 'item/completed', 'params': {'item': {'type': 'userMessage', 'id': f'u{i}', 'content': [{'type': 'text', 'text': text}]}}})
    steer.observe('904', token, {'method': 'item/completed', 'params': {'item': {'type': 'agentMessage', 'text': 'Both done.\n\nMore detail.'}}})
    steer.unregister('904', token)
    conn = db._get_connection()
    records = [json.loads(r['metadata'])['steer'] for r in conn.execute('SELECT metadata FROM chat_messages ORDER BY id')]
    conn.close()
    covers = [infos[0]['steer_id'], infos[1]['steer_id']]
    assert [r['covers'] for r in records[:2]] == [covers, covers]
    assert [r['reply']['text'] for r in records[:2]] == ['Both done.', 'Both done.']
    assert records[2]['state'] == 'undelivered'
    assert records[2]['delivered'] is False and records[2]['reply'] is None
    assert steer_receipts.live('CH-000904')[-1]['state'] == 'undelivered'


def test_turn_completes_during_delivery_before_binding(tmp_path):
    import concurrent.futures
    from api.agent_harness import steer, steer_receipts
    token = None
    def send(text):
        steer.unregister('905', token)
        future = concurrent.futures.Future()
        future.set_result((True, None))
        return future
    token = steer.register('905', 'codex', send)
    info = steer.steer('905', 'too late')
    with steer_receipts._pending_lock:
        tracker = steer_receipts._pending[info['steer_id']]['tracker']
    assert tracker.snapshot(info['steer_id'])['state'] == 'undelivered'
    steer_receipts.discard(info['steer_id'])


def test_failed_persistence_callback_does_not_stop_turn(caplog):
    def fail(_entry):
        raise RuntimeError('storage unavailable')
    tracker = Receipts(fail)
    steer_id = tracker.begin('one', 'turn')
    tracker.accepted(steer_id, True)
    tracker.echo('one', 'one-item')
    tracker.reply('Seen.')
    tracker.finish()
    assert tracker.snapshot(steer_id)['reply']['text'] == 'Seen.'
    assert 'Steer receipt update failed' in caplog.text


def test_failed_write_is_retried_on_live_poll(tmp_path):
    import sqlite3
    from api.agent_harness import steer_receipts

    class DB:
        failures = 1
        def _get_connection(self):
            if self.failures:
                self.failures -= 1
                raise sqlite3.OperationalError('temporary failure')
            conn = sqlite3.connect(tmp_path / 'retry.db')
            conn.row_factory = sqlite3.Row
            return conn
    conn = sqlite3.connect(tmp_path / 'retry.db')
    conn.execute('CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, role TEXT, metadata TEXT)')
    conn.execute("INSERT INTO chat_messages VALUES (1, 'user', '{}')")
    conn.commit()
    conn.close()
    db = DB()
    tracker = Receipts(steer_receipts.changed)
    sid = tracker.begin('one', 'turn')
    steer_receipts.track('906', tracker, sid)
    tracker.accepted(sid, True)
    tracker.echo('one', 'u1')
    steer_receipts.bind(sid, db, 1)
    assert steer_receipts.live('906')[0]['delivered'] is True
    conn = db._get_connection()
    assert '"delivered": true' in conn.execute('SELECT metadata FROM chat_messages').fetchone()['metadata']
    conn.close()
    tracker.finish()


def test_unpersisted_user_message_discards_registration():
    import concurrent.futures
    from api.agent_harness import steer, steer_receipts
    from api.chat_coordinator import steered_user_meta
    from api.chat_turn_persist import persist_auth_user_message
    class BrokenDB:
        def add_message(self, *args, **kwargs):
            raise RuntimeError('insert failed')
    def send(_text):
        f = concurrent.futures.Future()
        f.set_result((True, None))
        return f
    token = steer.register('907', 'codex', send)
    info = steer.steer('907', 'message')
    persist_auth_user_message(BrokenDB(), 907, 'message', metadata=steered_user_meta(info), project_merge_fn=lambda m, *_: m)
    with steer_receipts._pending_lock:
        assert info['steer_id'] not in steer_receipts._pending
    steer.unregister('907', token)


def test_detached_acceptance_survives_bind_and_replay_of_duplicate_text():
    updates = []
    tracker = Receipts(updates.append)
    first = tracker.begin('same', 'turn')
    tracker.accepted(first, True)
    tracker.accepted_detached(first)
    tracker.bind(first, 11)
    assert updates[-1]['state'] == 'delivered'
    assert updates[-1]['delivered_at'] is not None
    assert updates[-1]['reply'] is None
    second = tracker.begin('same', 'turn')
    tracker.accepted(second, True)
    tracker.echo('same', 'first-replay')
    assert tracker.snapshot(second)['delivered_at'] is None
    tracker.echo('same', 'second-replay')
    assert tracker.snapshot(second)['delivered_at'] is not None
    tracker.finish()
    assert tracker.snapshot(first)['state'] == 'delivered'
