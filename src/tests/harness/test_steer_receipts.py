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
