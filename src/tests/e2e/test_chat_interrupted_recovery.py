"""Production chat DOM survives Stop, server sync and reload (fake APIs only)."""
import json

from .test_chat_sync_lifetime import SID_B, SyncWorld, _open, _sync, _msg
from .test_shared_diff_modal import browser, static_server  # noqa: F401


def test_stopped_bubble_is_claimed_and_survives_reload(browser, static_server):
    class World(SyncWorld):
        def handle(self, route):
            if route.request.method == 'POST' and '/api/chat-cancel' in route.request.url:
                route.fulfill(content_type='application/json', body=json.dumps({'success': True}))
                return
            super().handle(route)

    world = World()
    chat = world.scenario[str(SID_B)]
    chat['live'] = {'active': True, 'generating': True, 'status': 'tool 17: Inspect branches',
                    'report_url': '/query_log.html?id=old-query', 'query_id': 'old-query'}
    page, frame, errors = _open(browser, static_server, world, SID_B)
    try:
        frame.locator('#typing-indicator-remote').wait_for(state='visible')
        frame.locator('#stopButton').click()
        frame.locator('.message[data-stopped="true"]').wait_for(state='visible')
        chat['live'] = {'active': False, 'generating': False, 'cancelled': True}
        recovered = _msg(12, 'assistant', '**Agent stopped before completion.**\n\nInvestigated branches.')
        recovered['metadata'] = {'kind': 'interrupted-agent-turn', 'query_id': 'old-query',
                                 'report_url': '/query_log.html?id=old-query',
                                 'interrupted_turn': {'status': 'cancelled'}}
        chat['messages'].append(recovered)
        _sync(page, frame)
        frame.locator('.message[data-message-id="12"]').wait_for(state='visible')
        assert frame.locator('#chatMessages .message.assistant').count() == 1
        assert frame.get_by_text('Investigated branches.').count() == 1
        page.reload(wait_until='domcontentloaded')
        frame.locator('.message[data-message-id="12"]').wait_for(state='visible')
        assert frame.locator('#chatMessages .message.assistant').count() == 1
        assert frame.locator('.message[data-message-id="12"] .message-query-log-link').count() == 1
        assert frame.locator('#typing-indicator-remote').count() == 0
        assert frame.locator('.message[data-message-id="12"]').get_attribute('data-stopped') == 'true'
        # An older missing snapshot recovered during a newer remote turn cannot
        # hide that turn's spinner or masquerade as its assistant completion.
        chat['messages'].append(_msg(13, 'user', 'New task'))
        chat['live'] = {'active': True, 'generating': True, 'status': 'New task is running'}
        old = _msg(14, 'assistant', 'Another old stopped turn')
        old['metadata'] = {**recovered['metadata'], 'query_id': 'another-old'}
        chat['messages'].append(old)
        _sync(page, frame)
        frame.locator('.message[data-message-id="14"]').wait_for(state='visible')
        frame.locator('#typing-indicator-remote').wait_for(state='visible')
        assert errors == []
    finally:
        page.close()
