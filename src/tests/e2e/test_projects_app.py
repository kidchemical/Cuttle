"""Actual Projects page in Chromium with a private static server and fake API."""
import copy
import json
from urllib.parse import urlparse

import pytest
from .test_shared_diff_modal import static_server, browser, apply_request_guard


PROJECT = {'id':7, 'name':'Demo', 'description':'A game workspace', 'tags':['game'],
           'path':'/host/demo', 'paths':['D:/Windows/Demo','/host/demo','/host/alternate'],
           'resolved_path':'/host/demo', 'available':True, 'archived':False, 'config':{},
           'path_checks':[{'path':'D:/Windows/Demo','status':'different_os','selected':False},
                          {'path':'/host/demo','status':'available','selected':True},
                          {'path':'/host/alternate','status':'available','selected':False}]}


class FakeAPI:
    def __init__(self, can_edit=True):
        self.projects = [copy.deepcopy(PROJECT)]
        self.calls = []
        self.can_edit = can_edit
        self.invalid_metadata = False
        self.branch = "main"

    def handle(self, route):
        req = route.request
        path = urlparse(req.url).path
        body = json.loads(req.post_data or '{}')
        self.calls.append((req.method, path, body))
        response = {'success':True}
        status = 200
        if path == '/api/projects/app/access': response['can_edit'] = self.can_edit
        elif path == '/api/projects': response['data'] = self.projects
        elif path.endswith('/overview'):
            project = next(p for p in self.projects if p['id'] == int(path.split('/')[3]))
            response['data'] = {'project':project, 'repository':{'available':True,'branch':self.branch,'branch_count':2,'branches':['main','dev'],'root':'/host/demo','remote':'https://git.example/demo'}, 'configuration':[{'label':'Project','path':'/host/demo/.cuttle','present':True,'files':['rules/00-core.md','GLOBAL.ini']}], 'changes':[]}
        elif path.endswith('/branch'):
            project = next(p for p in self.projects if p['id'] == int(path.split('/')[3]))
            self.branch = project['default_branch']
            response['data'] = {'message': 'Switched to branch: ' + self.branch}
        elif path.endswith('/activity'):
            response['data'] = {'stats':{'chats':2,'messages':8,'last_activity':'2026-10-04 18:00:00'}, 'messages':[{'id':4,'session_id':984,'session_name':'Build chat','role':'user','content':'Literal <script>window.pwned=true</script> text','timestamp':'2026-10-04 18:00:00'}], 'next_before_id':None}
        elif path.startswith('/api/projects/') and req.method == 'PUT':
            project = next(p for p in self.projects if p['id'] == int(path.split('/')[3]))
            if self.invalid_metadata:
                status=400; response={'success':False,'error':'Duplicate project name'}
            else:
                project.update(body)
                if 'paths' in body:
                    project['path_checks'] = [{'path':p,'status':'available','selected':i == 0} for i,p in enumerate(body['paths'])]
                    project['resolved_path'] = body['paths'][0]
        elif path == '/api/projects/paths/check':
            response['data'] = {'path_checks':[{'path':p,'status':'available','selected':i == 0} for i,p in enumerate(body['paths'])], 'available':True,'resolved_path':body['paths'][0]}
        elif path == '/api/projects/register':
            project = copy.deepcopy(PROJECT); project.update(body); project['id']=8
            self.projects.append(project); response['project_id']=8
        elif path.endswith('/remove'): self.projects=[]
        elif path.endswith('/chat'): response['session_id']=985
        elif path == '/api/auth/me': response.update(authenticated=True, user={'id':1,'display_name':'Owner'})
        elif path.startswith('/api/'): response['data']=[]
        route.fulfill(status=status, content_type='application/json', body=json.dumps(response))


@pytest.fixture
def page_ctx(browser, static_server):
    context = browser.new_context(viewport={'width':1200, 'height':950})
    apply_request_guard(context, static_server)
    page = context.new_page()
    errors=[]
    page.on('pageerror', lambda error: errors.append(str(error)))
    yield page, context, errors
    context.close()


def open_page(page, origin, api):
    page.route('**/api/**', api.handle)
    page.goto(origin + '/projects_page.html')
    page.locator('#metadata-form').wait_for()


def test_paths_arrows_drag_test_and_save(page_ctx, static_server):
    page, _, errors = page_ctx
    api = FakeAPI(); open_page(page, static_server, api)
    page.get_by_role('button', name='Locations', exact=True).click()
    assert page.locator('#paths input').nth(0).input_value() == 'D:/Windows/Demo'
    page.get_by_role('button', name='Move path 2 up', exact=True).click()
    assert page.locator('#paths input').nth(0).input_value() == '/host/demo'
    page.get_by_role('button', name='Drag path 3 to reorder').drag_to(page.locator('.path-row').nth(0))
    assert page.locator('#paths input').nth(0).input_value() == '/host/alternate'
    page.get_by_role('button', name='Test on host', exact=True).click()
    page.get_by_role('button', name='Save paths', exact=True).click()
    page.get_by_text('Saved order', exact=True).wait_for()
    save = next(call for call in api.calls if call[:2] == ('PUT','/api/projects/7'))
    assert save[2]['paths'] == ['/host/alternate','/host/demo','D:/Windows/Demo']
    assert not errors
    page.screenshot(path='temp/projects-locations.png', full_page=True)


def test_activity_config_and_typed_remove(page_ctx, static_server):
    page, _, errors = page_ctx
    api=FakeAPI(); open_page(page, static_server, api)
    page.get_by_role('button', name='Activity', exact=True).click()
    assert '<script>' in page.locator('.feed-item p').inner_text()
    assert page.evaluate('window.pwned') is None
    page.get_by_role('button', name='Configuration', exact=True).click()
    assert 'rules/00-core.md' in page.locator('#tab-content').inner_text()
    page.get_by_role('button', name='Overview', exact=True).click()
    page.get_by_role('button', name='Remove from Cuttle…', exact=True).click()
    assert page.locator('#confirm-remove').is_disabled()
    page.locator('#confirm-name').fill('Wrong')
    assert page.locator('#confirm-remove').is_disabled()
    page.locator('#confirm-name').fill('Demo')
    page.locator('#confirm-remove').click()
    page.get_by_text('Add your first project to get started.', exact=True).wait_for()
    assert ('POST','/api/projects/7/remove',{'confirm_name':'Demo'}) in api.calls
    assert not errors


def test_register_archive_readonly_and_mobile(page_ctx, static_server):
    page, _, errors = page_ctx
    api=FakeAPI(); open_page(page, static_server, api)
    page.get_by_role('button', name='Add project', exact=True).click()
    page.locator('#register-form [name="name"]').fill('Second')
    page.locator('#register-form [name="path"]').fill('/host/second')
    page.get_by_role('button', name='Register project', exact=True).click()
    page.locator('#register-dialog').wait_for(state='hidden')
    page.locator('#metadata-form').wait_for()
    assert any(call[1]=='/api/projects/register' for call in api.calls)
    page.get_by_role('heading', name='Second', exact=True).wait_for()
    page.get_by_role('button', name='Archive project', exact=True).click()
    page.get_by_role('button', name='Unarchive project', exact=True).wait_for()
    assert api.projects[1]['archived'] is True
    page.locator('#show-archived').check()
    assert page.locator('[data-project="8"]').count() == 1
    page.get_by_role('button', name='Unarchive project', exact=True).click()
    page.get_by_role('button', name='Archive project', exact=True).wait_for()
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path='temp/projects-mobile.png', full_page=True)
    page.get_by_role('button', name='Archive project', exact=True).scroll_into_view_if_needed()
    page.screenshot(path='temp/projects-mobile-bottom.png')
    assert not errors


def test_readonly_and_validation_preserves_draft(page_ctx, static_server):
    page, _, errors = page_ctx
    api=FakeAPI(can_edit=False); open_page(page, static_server, api)
    assert page.locator('#add-project').is_disabled()
    assert page.locator('#metadata-form input').first.is_disabled()
    page.get_by_role('button', name='Locations', exact=True).click()
    assert page.get_by_role('button', name='Save paths', exact=True).is_disabled()
    api.can_edit=True
    page.reload(); page.locator('#metadata-form').wait_for()
    api.invalid_metadata=True
    page.locator('#metadata-form [name="name"]').fill('Existing project')
    page.get_by_role('button', name='Save details', exact=True).click()
    page.get_by_text('Duplicate project name', exact=True).wait_for()
    assert page.locator('#metadata-form [name="name"]').input_value() == 'Existing project'
    api.invalid_metadata=False
    page.get_by_role('button', name='Save details', exact=True).click()
    page.get_by_text('Project saved.', exact=True).wait_for()
    assert api.projects[0]['name'] == 'Existing project'
    assert page.get_by_role('button', name='Save details', exact=True).is_enabled()
    assert not errors


def test_working_branch_saves_without_checkout_and_switches_explicitly(page_ctx, static_server):
    page, _, errors = page_ctx
    api = FakeAPI(); open_page(page, static_server, api)
    page.get_by_role('button', name='Repository', exact=True).click()
    page.get_by_label('Default working branch', exact=True).fill('dev')
    page.get_by_role('button', name='Save default branch', exact=True).click()
    page.get_by_text('Project saved.', exact=True).wait_for()
    assert ('PUT', '/api/projects/7', {'default_branch':'dev'}) in api.calls
    assert not any(call[1].endswith('/branch') for call in api.calls)
    assert 'Current branch: main' in page.locator('#tab-content').inner_text()
    page.get_by_role('button', name='Switch / create default branch', exact=True).click()
    page.get_by_text('Switched to branch: dev', exact=True).wait_for()
    assert ('POST', '/api/projects/7/branch', {}) in api.calls
    assert 'Current branch: dev' in page.locator('#tab-content').inner_text()
    page.set_viewport_size({'width':390, 'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path='temp/projects-working-branch.png', full_page=True)
    assert not errors


def test_working_branch_readonly_and_save_error_preserves_draft(page_ctx, static_server):
    page, _, errors = page_ctx
    api = FakeAPI(can_edit=False); open_page(page, static_server, api)
    page.get_by_role('button', name='Repository', exact=True).click()
    assert page.get_by_label('Default working branch', exact=True).is_disabled()
    assert page.get_by_role('button', name='Switch / create default branch', exact=True).is_disabled()
    api.can_edit=True
    page.reload(); page.locator('#metadata-form').wait_for()
    page.get_by_role('button', name='Repository', exact=True).click()
    api.invalid_metadata=True
    page.get_by_label('Default working branch', exact=True).fill('feature/test')
    page.get_by_role('button', name='Save default branch', exact=True).click()
    page.get_by_text('Duplicate project name', exact=True).wait_for()
    assert page.get_by_label('Default working branch', exact=True).input_value() == 'feature/test'
    assert page.get_by_role('button', name='Save default branch', exact=True).is_enabled()
    assert not errors
