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
