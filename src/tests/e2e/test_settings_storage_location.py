"""Storage move controls through Settings with isolated location and database APIs."""
import json

from playwright.sync_api import expect

from .test_settings_agents import AgentWorld, go_to_tab, open_settings
from .test_shared_diff_modal import browser, static_server  # noqa: F401


class StorageWorld(AgentWorld):
    def __init__(self):
        super().__init__()
        self.location = {'version': 1, 'current': '/home/owner/Cuttle', 'pending': None,
                         'error': None, 'available': True, 'reason': None}
        self.fail_get = False
        self.fail_post = False
        self.posts = []

    def handle(self, route):
        path = route.request.url.split('?')[0]
        if path.endswith('/api/settings/storage/location'):
            if route.request.method == 'POST':
                body = json.loads(route.request.post_data)
                self.posts.append(body)
                if self.fail_post:
                    route.fulfill(status=400, content_type='application/json',
                                  body=json.dumps({'success': False, 'error': 'Destination parent does not exist'}))
                    return
                self.location['pending'] = None if body.get('cancel') else body['destination']
            elif self.fail_get:
                route.fulfill(status=403, content_type='application/json',
                              body=json.dumps({'success': False, 'error': 'Owner access required'}))
                return
            route.fulfill(content_type='application/json', body=json.dumps({'success': True, 'location': self.location}))
        elif path.endswith('/api/settings/storage'):
            route.fulfill(status=503, content_type='application/json',
                          body=json.dumps({'success': False, 'error': 'Databases unavailable'}))
        else:
            super().handle(route)


def test_schedule_cancel_and_failed_post(browser, static_server):
    world = StorageWorld()
    page = open_settings(browser, static_server, world)
    try:
        go_to_tab(page, 'Data')
        card = page.locator('#storageLocation')
        expect(card.locator('#storageLocationCurrent')).to_have_text('/home/owner/Cuttle')
        expect(page.locator('#storageStores')).to_have_text('Databases unavailable')
        expect(card).to_contain_text('Exit from the Cuttle system tray')
        expect(card).to_contain_text('cold start')
        field = card.get_by_label('New data folder')
        field.fill('/mnt/data/New Cuttle')
        world.fail_post = True
        card.get_by_role('button', name='Schedule move').click()
        expect(card.get_by_role('status')).to_have_text('Destination parent does not exist')
        expect(field).to_have_value('/mnt/data/New Cuttle')
        world.fail_post = False
        card.get_by_role('button', name='Schedule move').click()
        expect(card.locator('#storageLocationPending')).to_have_text('Scheduled destination: /mnt/data/New Cuttle')
        assert world.posts[-1] == {'destination': '/mnt/data/New Cuttle'}
        page.reload(wait_until='domcontentloaded')
        go_to_tab(page, 'Data')
        expect(card.locator('#storageLocationPending')).to_be_visible()
        card.get_by_role('button', name='Cancel scheduled move').click()
        expect(card.locator('#storageLocationPending')).to_be_hidden()
        assert world.posts[-1] == {'cancel': True}
    finally:
        page.close()


def test_load_failure_retry_and_unavailable_state(browser, static_server):
    world = StorageWorld()
    world.fail_get = True
    page = open_settings(browser, static_server, world)
    try:
        go_to_tab(page, 'Data')
        card = page.locator('#storageLocation')
        expect(card.get_by_role('status')).to_have_text('Owner access required')
        expect(card.get_by_role('button', name='Schedule move')).to_be_disabled()
        world.fail_get = False
        world.location.update(available=False, reason='Managed by the host', error='Previous move failed', pending='/mnt/new')
        card.get_by_role('button', name='Retry').click()
        expect(card.get_by_role('status')).to_have_text('Previous move failed Managed by the host')
        expect(card.locator('#storageLocationPending')).to_contain_text('/mnt/new')
        expect(card.get_by_role('button', name='Cancel scheduled move')).to_be_disabled()
    finally:
        page.close()
