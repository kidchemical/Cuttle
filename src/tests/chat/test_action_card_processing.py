"""A Flask-restart card is input while awaiting a click, work only once started.

Regression: a freshly delivered restart controller ("Checking restart status…")
used to count as executing, so the history row showed the running spinner while
it still needed acknowledging. It must read as awaiting input (blue dot); only an
adopted/in-flight restart (locked, tracking a restart id) counts as processing.
"""
from pathlib import Path

import pytest

from tests.browser_guard import launch_chromium

WEB = Path(__file__).resolve().parents[2] / 'web'

_HOST = '''
const ctl = CuttleChatActionCards.mountCards(document.getElementById('chatMessages'), {
  request: () => Promise.reject(), sendAnswerText: () => {}, resumeWithStatus: () => {},
  sendControlCommand: () => {}, context: () => ({sessionId:'42'}), notify: () => {},
  syncMessages: () => {}, publishRestartEvent: () => {}, setHistoryAwaiting: () => {},
  syncHistoryAwaiting: () => {}, syncComposerStop: () => {},
  paintAssistantMessage: () => {}, escapeHtml: String,
  storage: {getItem:()=>null,setItem:()=>{},removeItem:()=>{}}
});
'''


def test_fresh_restart_card_awaits_input_then_processing_once_started():
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as pw:
        browser = launch_chromium(pw)
        page = browser.new_page()
        page.set_content('<div id="chatMessages"></div>')
        page.add_style_tag(path=str(WEB / 'css/chat_page.css'))
        for asset in ['js/chat/chat_action_forms.js', 'js/chat/chat_action_cards.js']:
            page.add_script_tag(path=str(WEB / asset))
        page.evaluate('''() => { document.getElementById('chatMessages').innerHTML =
          CuttleChatActionForms.renderActionFormCardHtml({spec:{
            session_id:'42', mode:'choice', id:'flask-restart-g1', title:'Restart Flask',
            options:[{id:'graceful',label:'Graceful',action:'flask.restart'},
                     {id:'force',label:'Force',action:'flask.restart'}]},
            formId:'flask-restart-g1', esc:String}); }''')
        page.add_script_tag(content=_HOST)

        # Fresh card: controls disabled while it checks status, but it still waits
        # on a person — input, never the running spinner.
        assert page.evaluate('() => ctl.processingSessionIds()') == []
        assert page.evaluate('() => ctl.awaitingInputSessionIds()') == ['42']

        # Acknowledged and in flight (locked, tracking a restart id) ⇒ processing.
        page.evaluate('''() => {
          const c = document.querySelector('.cuttle-action-form');
          c.setAttribute('data-locked', '1');
          c.setAttribute('data-restart-id', 'r1');
          c.classList.add('cuttle-action-form--locked', 'is-pending');
          c.removeAttribute('data-restart-pending-sync');
        }''')
        assert page.evaluate('() => ctl.processingSessionIds()') == ['42']
        assert page.evaluate('() => ctl.awaitingInputSessionIds()') == []

        browser.close()
