"""Isolated browser regression for action-card input state and activity surfaces."""
from pathlib import Path
import pytest

from tests.browser_guard import launch_chromium

WEB = Path(__file__).resolve().parents[2] / 'web'


def _function(name):
    source = (WEB / 'js/chat/chat_page.js').read_text(encoding="utf-8")
    start = source.index('    function ' + name + '(')
    end = source.index('\n    function ', start + 1)
    return source[start:end]


def test_input_card_submit_and_remove_update_title_history_and_space():
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as pw:
        browser = launch_chromium(pw)
        page = browser.new_page()
        page.set_content('''<div id="chatSessionTitle" style="display:flex"><span id="chatSessionUnreadIcon"></span>
          <span id="chatSessionQueuedIcon"></span><span id="chatSessionPausedIcon"></span>
          <span class="chat-session-input-icon" id="chatSessionInputIcon" hidden></span></div>
          <div class="history-section" data-project-key="cuttle"><div class="history-section-title"><span class="history-section-count">1</span></div><div class="chat-history-item" data-session-id="42"></div></div>
          <div id="chatMessages"><div class="cuttle-action-form" data-session-id="42" data-locked="0">
          <button data-action-form-submit="1">Submit</button></div></div>''')
        page.add_style_tag(path=str(WEB / 'css/chat_page.css'))
        for asset in ['js/chat/chat_activity.js', 'js/chat/chat_action_forms.js', 'js/chat/chat_action_cards.js', 'js/spaces/spaces_activity.js']:
            page.add_script_tag(path=str(WEB / asset))
        page.evaluate('''() => { document.getElementById('chatMessages').innerHTML =
          CuttleChatActionForms.renderActionFormCardHtml({spec:{session_id:'42', mode:'choice',
            options:[{id:'yes',label:'Yes'}]}, formId:'question', esc:String}); }''')
        page.add_script_tag(content='''
        const prefs = {}; const currentSessionId = '42';
        const getSessionPrefs = sid => prefs[sid] || null;
        const attentionPrefsFor = getSessionPrefs;
        const serverAttention = {get:()=>null};
        const toAuthDbSessionId = String;
        const updateSessionPrefs = (sid, p) => prefs[sid] = {...prefs[sid], ...p};
        const sessionIdsEqual = CuttleChatActivity.sessionIdsEqual;
        const canonicalizeChatSessionId = CuttleChatActivity.canonicalizeChatSessionId;
        const formAwaitingSessionIds = new Set();
        const sessionHasActiveFollowup = () => false; const sessionHasPausedFollowup = () => false;
        const chatAttentionActive = () => false; const chatAttentionIsError = false;
        const isAuthMode = () => true; const findAuthServerSession = () => null;
        const sessionHistoryAttentionKind = sid => CuttleChatActivity.sessionHistoryAttentionKind({prefs: getSessionPrefs(sid)});
        const syncHistorySubagentAttention = () => {};
        const scheduleChatActivityBroadcast = () => {};
        const syncHistoryRunningIndicators = () => {};
        const ctl = CuttleChatActionCards.mountCards(document.getElementById('chatMessages'), {
          request: () => Promise.reject(), sendAnswerText: () => {}, resumeWithStatus: () => {},
          sendControlCommand: () => {}, context: () => ({sessionId:'42'}), notify: () => {},
          syncMessages: () => {}, publishRestartEvent: () => {}, setHistoryAwaiting: () => {},
          syncHistoryAwaiting: () => syncHistoryFormAwaitingFromDom(), syncComposerStop: () => {},
          paintAssistantMessage: () => {}, escapeHtml: String,
          storage: {getItem:()=>null,setItem:()=>{},removeItem:()=>{}}
        });
        const actionCardsController = () => ctl;
        const formAwaitingSessionIdFromCard = card => ctl.awaitingSessionId(card);
        const spaceKind = () => { CuttleSpaces.setUnreadPrefs(prefs);
          return CuttleSpaces.selectSpaceActivity(['42','43'], id =>
            id === '43' ? {running:true} : CuttleSpaces.lookupSessionActivity(id), true, new Set(['42','43'])); };
        ''')
        for name in ['historyUnreadIconHTML', 'historyQueuedIconHTML', 'historyPausedIconHTML',
                     'historyAttentionIconHTML', 'updateSessionTitleUnreadDot',
                     'syncHistoryProjectAttentionIndicators', 'syncHistoryUnreadIndicators', 'syncHistoryFormAwaitingFromDom']:
            page.add_script_tag(content=_function(name))
        page.evaluate('syncHistoryFormAwaitingFromDom()')
        assert page.evaluate('spaceKind()') == 'input'
        assert page.locator('#chatSessionInputIcon').is_visible()
        assert page.locator('.history-input-icon').count() == 1
        assert page.locator('.history-section-unread-dot.is-input').count() == 1
        page.evaluate("document.querySelector('.cuttle-action-form').setAttribute('data-locked','1')")
        page.wait_for_function("!prefs['42'].awaitingInput")
        assert page.evaluate('spaceKind()') == 'running'
        assert page.locator('#chatSessionInputIcon').is_hidden()
        assert page.locator('.history-input-icon').count() == 0
        # Unlocking makes the input request visible again; pending execution clears blue.
        page.evaluate("document.querySelector('.cuttle-action-form').setAttribute('data-locked','0')")
        page.wait_for_function("prefs['42'].awaitingInput")
        page.evaluate("document.querySelector('.cuttle-action-form').classList.add('is-pending')")
        page.wait_for_function("!prefs['42'].awaitingInput")
        page.evaluate("document.querySelector('.cuttle-action-form').classList.remove('is-pending')")
        page.wait_for_function("prefs['42'].awaitingInput")
        page.evaluate("document.querySelector('.cuttle-action-form').remove()")
        page.wait_for_function("!prefs['42'].awaitingInput")
        browser.close()
