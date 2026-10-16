"""Native command responses must not own a concurrent agent's live bubble.

Real production chat assets, a held browser ReadableStream, and isolated API
history. No daemon, restart, vendor CLI, or model call is involved.
"""
import time

import pytest

from .test_shared_diff_modal import apply_request_guard, browser, static_server  # noqa: F401
from .test_chat_sync_lifetime import SID_A, SyncWorld, _ef, _msg


FETCH_HOOK = r"""
window.__liveReply = { posts: [], aborted: false, control: null, stream: null };
(function () {
  const raw = window.fetch.bind(window);
  window.fetch = function (input, opts) {
    if (String(input) !== '/api/chat' || !opts || opts.method !== 'POST') return raw(input, opts);
    const body = JSON.parse(opts.body);
    const state = window.__liveReply;
    state.posts.push(body);
    if (body.message.startsWith('/restart')) {
      return new Promise((resolve, reject) => {
        state.control = { body, resolve: (data) => resolve(new Response(JSON.stringify(data),
          { headers: { 'content-type': 'application/json' } })), reject };
      });
    }
    const stream = new ReadableStream({ start(controller) { state.stream = controller; } });
    if (opts.signal) opts.signal.addEventListener('abort', () => {
      state.aborted = true;
      try { state.stream.error(new DOMException('aborted', 'AbortError')); } catch (_) {}
    }, { once: true });
    state.emit = (data) => state.stream.enqueue(new TextEncoder().encode('data: ' + JSON.stringify(data) + '\n\n'));
    return Promise.resolve(new Response(stream, { headers: { 'content-type': 'text/event-stream' } }));
  };
})();
"""


@pytest.fixture
def chat_surface(browser, static_server):
    page = browser.new_page(viewport={'width': 1280, 'height': 800})
    apply_request_guard(page.context, static_server)
    world = SyncWorld()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_init_script(FETCH_HOOK)
    page.route(f'{static_server}/api/**', world.handle)
    page.route(f'{static_server}/qa-host.html', lambda route: route.fulfill(
        content_type='text/html', body=f'<iframe style="border:0;width:100%;height:96vh" src="/chat_page.html?chat={SID_A}"></iframe>'))
    page.goto(f'{static_server}/qa-host.html', wait_until='domcontentloaded')
    frame = page.frame_locator('iframe')
    frame.locator('#chatMessages .message.user').first.wait_for(state='visible')
    yield page, frame, world, errors
    page.close()


@pytest.fixture
def live_chat(chat_surface):
    page, frame, world, errors = chat_surface
    frame.locator('#chatInput').fill('Keep working on my task')
    frame.locator('#chatInput').press('Enter')
    _ef(page).wait_for_function('window.__liveReply.stream !== null')
    _ef(page).evaluate("""() => {
      window.__liveReply.original = document.getElementById('typing-indicator');
      window.__liveReply.emit({type: 'status', message: 'Working on the task'});
    }""")
    frame.locator('#typing-indicator .typing-status').filter(has_text='Working on the task').wait_for()
    world.scenario[str(SID_A)]['live'] = {'active': True, 'generating': True, 'status': 'Working on the task'}
    return page, frame, world, errors


def _send_restart(page, frame):
    frame.locator('#chatInput').fill('/restart')
    frame.locator('#chatInput').press('Escape')
    frame.locator('#sendButton').click(timeout=5000)
    _ef(page).wait_for_function('window.__liveReply.control !== null', timeout=5000)


def _resolve_restart(page):
    return _ef(page).evaluate(r"""() => {
      const state = window.__liveReply;
      const response = 'Restart controls\n\n' + window.CuttleSupervised.buildRestartFormHtml();
      const cid = state.control.body.control_request_id;
      state.control.resolve({success: true, response, session_id: 1001,
        type: 'flask_restart', native_command: '/restart', control_request_id: cid});
      return {response, cid};
    }""")


def _assert_live_unchanged(page):
    assert _ef(page).evaluate("""() => {
      const state = window.__liveReply;
      return state.original.isConnected && document.querySelectorAll('#typing-indicator').length === 1
        && document.getElementById('typing-indicator') === state.original && !state.aborted;
    }""")


def test_restart_response_preserves_active_reply(live_chat):
    page, frame, world, errors = live_chat
    _send_restart(page, frame)
    _assert_live_unchanged(page)
    _resolve_restart(page)
    frame.locator('.message.assistant').filter(has_text='Restart controls').wait_for()
    _assert_live_unchanged(page)
    assert _ef(page).evaluate("""() => {
      const live = window.__liveReply.original;
      const card = [...document.querySelectorAll('.message.assistant')]
        .find(el => el.dataset.rawContent?.startsWith('Restart controls'));
      return !!(live.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING);
    }""")
    _ef(page).evaluate("window.__liveReply.emit({type:'status', message:'Still working after the command'})")
    frame.locator('#typing-indicator .typing-status').filter(has_text='Still working after the command').wait_for()
    _finish_agent(page, world)
    frame.locator('.message.assistant').filter(has_text='Original agent completed').wait_for()
    assert _ef(page).evaluate("""() => {
      const replies = [...document.querySelectorAll('.message.assistant')];
      return replies.findIndex(el => el.dataset.rawContent === 'Original agent completed')
        < replies.findIndex(el => el.dataset.rawContent?.startsWith('Restart controls'));
    }""")
    assert not errors


def test_synced_control_response_does_not_complete_agent(live_chat):
    page, frame, world, errors = live_chat
    _send_restart(page, frame)
    cid = _ef(page).evaluate('window.__liveReply.control.body.control_request_id')
    # History can arrive before the control POST returns (another device or poll).
    messages = world.scenario[str(SID_A)]['messages']
    messages += [_msg(3, 'user', 'Keep working on my task'),
                 dict(_msg(4, 'user', '/restart'), metadata={'control_request_id': cid}),
                 dict(_msg(5, 'assistant', 'Restart controls from history'), metadata={'control_request_id': cid})]
    _ef(page).evaluate("window.dispatchEvent(new Event('focus'))")
    frame.locator('.message.assistant').filter(has_text='Restart controls from history').wait_for()
    _assert_live_unchanged(page)
    assert frame.locator('#stopButton').is_visible()
    assert _ef(page).evaluate("""() => !!(window.__liveReply.original.compareDocumentPosition(
      document.querySelector('.message[data-message-id="5"]')) & Node.DOCUMENT_POSITION_FOLLOWING)""")
    assert not errors


def _finish_agent(page, world):
    world.scenario[str(SID_A)]['live'] = {'active': False, 'generating': False}
    _ef(page).evaluate("""() => {
      const state = window.__liveReply;
      state.emit({type:'response', success:true, response:'Original agent completed',
        session_id:1001, response_type:'cursor'});
      state.stream.close();
    }""")


def test_agent_can_finish_before_control_response(live_chat):
    page, frame, world, errors = live_chat
    _send_restart(page, frame)
    _finish_agent(page, world)
    frame.locator('.message.assistant').filter(has_text='Original agent completed').wait_for()
    _resolve_restart(page)
    frame.locator('.message.assistant').filter(has_text='Restart controls').wait_for()
    assert frame.locator('.message.assistant').filter(has_text='Original agent completed').count() == 1
    assert frame.locator('#typing-indicator').count() == 0
    assert not frame.locator('#stopButton').is_visible()
    assert not errors


def test_failed_control_does_not_remove_live_reply(live_chat):
    page, frame, world, errors = live_chat
    _send_restart(page, frame)
    _ef(page).evaluate("window.__liveReply.control.resolve({success:false,response:'Restart unavailable'})")
    frame.locator('.message.assistant').filter(has_text='Restart unavailable').wait_for()
    _assert_live_unchanged(page)
    _ef(page).evaluate("window.__liveReply.emit({type:'status', message:'Agent continues despite command failure'})")
    frame.locator('#typing-indicator .typing-status').filter(has_text='Agent continues despite command failure').wait_for()
    _ef(page).evaluate("window.dispatchEvent(new Event('focus'))")
    _assert_live_unchanged(page)
    assert not errors


def test_control_preserves_remote_waiting_bubble(chat_surface):
    page, frame, world, errors = chat_surface
    sc = world.scenario[str(SID_A)]
    sc['messages'].append(_msg(3, 'user', 'Work started on another device'))
    sc['live'] = {'active': True, 'generating': True, 'status': 'Remote agent working',
                  'updated_at': time.time()}
    _ef(page).evaluate("window.dispatchEvent(new Event('focus'))")
    frame.locator('#typing-indicator-remote').wait_for()
    _ef(page).evaluate("window.__liveReply.remote = document.getElementById('typing-indicator-remote')")
    _send_restart(page, frame)
    resolved = _resolve_restart(page)
    frame.locator('.message.assistant').filter(has_text='Restart controls').wait_for()
    sc['messages'] += [dict(_msg(4, 'user', '/restart'), metadata={'control_request_id': resolved['cid']}),
                       dict(_msg(5, 'assistant', resolved['response']), metadata={'control_request_id': resolved['cid']})]
    _ef(page).evaluate("window.dispatchEvent(new Event('focus'))")
    frame.locator('.message.assistant[data-message-id="5"]').wait_for()
    assert _ef(page).evaluate('window.__liveReply.remote.isConnected')
    assert frame.locator('#typing-indicator').count() == 0
    assert not errors
