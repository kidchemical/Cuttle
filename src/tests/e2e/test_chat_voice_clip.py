"""Composer voice clips (chat_voice_clip.js), isolated in a browser with a fake mic."""
from playwright.sync_api import expect

from .test_shared_diff_modal import browser, static_server  # noqa: F401

COMPOSER = '''
<div class="chat-composer-inner" style="width:520px">
  <div class="composer-top-row"><div class="composer-actions">
    <button type="button" class="attach-button mic-button" id="micButton"></button>
    <button type="button" class="send-button" id="sendButton">send</button>
  </div></div>
  <div id="chatAttachChipsRow" class="attach-chips-row" hidden></div>
</div>'''

# The page's side of the contract: a fake mic source, the pending list, chip
# rendering and a Send that asks the owner first.
HOST = '''() => {
    window.staged = []; window.sent = []; window.uploadOk = true; window.toasts = [];
    window.mic = { started: 0, stopped: [], paused: 0, resumed: 0 };
    const recorder = {
        missingSupport: () => window.unsupported || [],
        openSource: () => ({
            start: async () => { if (window.denyMic) { const e = new Error('no'); e.name = 'NotAllowedError'; throw e; } mic.started++; },
            levels: () => [0.05 + 0.1 * Math.random()],
            pause: () => { mic.paused++; }, resume: () => { mic.resumed++; },
            stop: async (keep) => { mic.stopped.push(keep);
                return keep ? { blob: new Blob(['abc'], { type: 'audio/webm;codecs=opus' }), durationMs: 1 } : null; },
        }),
    };
    function render() {
        const row = document.getElementById('chatAttachChipsRow');
        row.innerHTML = ''; row.hidden = !staged.length;
        staged.forEach((att, idx) => {
            const chip = document.createElement('span'); chip.className = 'attach-chip';
            const name = document.createElement('span'); name.className = 'attach-chip-name'; name.textContent = att.filename;
            const remove = document.createElement('button'); remove.className = 'attach-chip-remove'; remove.textContent = '×';
            remove.onclick = () => { window.staged = CuttleChatAttachments.removeAttachmentAt(staged, idx); render(); };
            chip.append(name, remove);
            if (CuttleChatAttachments.isAudioAttachment(att)) CuttleChatVoiceClip.decorateChip(chip, name, att);
            row.appendChild(chip);
        });
    }
    window.clip = CuttleChatVoiceClip.create({
        win: window, recorder,
        toast: (m, k) => toasts.push([m, k]),
        upload: async (file, extra) => {
            if (!uploadOk) return false;
            window.staged = CuttleChatAttachments.addAttachmentsToPending(staged, [Object.assign(
                { filename: file.name, mime: file.type, url: '/output/uploads/1/' + file.name, path: '/x/' + file.name }, extra)]);
            render(); return true;
        },
        composers: [{ button: document.getElementById('micButton'), row: document.getElementById('chatAttachChipsRow') }],
    });
    document.getElementById('sendButton').onclick = async () => {
        if (clip.isActive() && !(await clip.finishForSend())) return;
        sent.push(staged.map((a) => a.filename)); window.staged = []; render();
    };
}'''


def _open(browser, static_server):
    page = browser.new_page(viewport={'width': 700, 'height': 400})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(static_server + '/agent_feed.html')
    page.set_content('<link rel="stylesheet" href="' + static_server + '/css/ui_boot.css">'
                     '<link rel="stylesheet" href="' + static_server + '/css/chat_page.css">' + COMPOSER)
    page.add_script_tag(url=static_server + '/js/chat/chat_attachments.js')
    page.add_script_tag(url=static_server + '/js/chat/chat_voice_clip.js')
    page.evaluate(HOST)
    return page, errors


def test_record_pause_stop_attaches_removable_clips(browser, static_server):
    page, errors = _open(browser, static_server)
    try:
        mic, bar = page.locator('#micButton'), page.locator('.voice-clip-bar')
        chips = page.locator('.attach-chip--voice')
        expect(bar).to_have_count(0)
        mic.click()
        expect(bar).to_have_attribute('data-state', 'recording')
        expect(mic).to_have_class('attach-button mic-button is-recording')
        # The waveform fills in and the timer runs while recording.
        page.wait_for_function("document.querySelector('.voice-clip-time').textContent === '0:01'")
        assert page.evaluate('''() => { const c = document.querySelector('.voice-clip-wave');
            const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            for (let i = 3; i < d.length; i += 4) if (d[i]) return true; return false; }''')
        # Pause holds the timer; resume continues the same clip.
        bar.locator('.voice-clip-pause').click()
        expect(bar).to_have_attribute('data-state', 'paused')
        held = bar.locator('.voice-clip-time').inner_text()
        page.wait_for_timeout(1200)
        assert bar.locator('.voice-clip-time').inner_text() == held
        bar.locator('.voice-clip-pause').click()
        expect(bar).to_have_attribute('data-state', 'recording')
        assert page.evaluate('[mic.paused, mic.resumed]') == [1, 1]
        # Stop attaches it as a chip; the bar goes away.
        bar.locator('.voice-clip-stop').click()
        expect(chips).to_have_count(1)
        expect(bar).to_have_count(0)
        expect(chips.first.locator('.attach-chip-name')).to_contain_text('Voice clip · 0:0')
        expect(chips.first.locator('.attach-chip-play')).to_be_visible()
        # Record → stop again adds a second clip (the mic button also stops).
        mic.click()
        page.wait_for_timeout(700)
        mic.click()
        expect(chips).to_have_count(2)
        # Each clip can be removed on its own.
        chips.first.locator('.attach-chip-remove').click()
        expect(chips).to_have_count(1)
        assert page.evaluate('mic.stopped') == [True, True]
        assert errors == []
    finally:
        page.close()


def test_send_while_recording_sends_the_clip(browser, static_server):
    page, errors = _open(browser, static_server)
    try:
        page.locator('#micButton').click()
        page.wait_for_timeout(700)
        # Two quick presses still send once.
        page.evaluate("() => { const b = document.getElementById('sendButton'); b.click(); b.click(); }")
        page.wait_for_function('sent.length > 0')
        page.wait_for_timeout(200)
        sent = page.evaluate('sent')
        assert len(sent) == 1 and len(sent[0]) == 1 and sent[0][0].startswith('voice-') and sent[0][0].endswith('.webm')
        expect(page.locator('.voice-clip-bar')).to_have_count(0)
        assert errors == []
    finally:
        page.close()


def test_discard_mistap_failed_upload_and_denied_mic(browser, static_server):
    page, errors = _open(browser, static_server)
    try:
        mic, bar = page.locator('#micButton'), page.locator('.voice-clip-bar')
        # Discard drops the audio and attaches nothing.
        mic.click()
        page.wait_for_timeout(600)
        bar.locator('.voice-clip-discard').click()
        expect(bar).to_have_count(0)
        assert page.evaluate('[staged.length, mic.stopped]') == [0, [False]]
        # A mis-tap (too short) attaches nothing.
        mic.click()
        mic.click()
        expect(bar).to_have_count(0)
        assert page.evaluate('staged.length') == 0
        # A failed upload keeps the clip for Retry, and Send does not go out.
        page.evaluate('window.uploadOk = false')
        mic.click()
        page.wait_for_timeout(600)
        page.locator('#sendButton').click()
        expect(bar).to_have_attribute('data-state', 'failed')
        expect(bar.locator('.voice-clip-time')).to_have_text('Upload failed')
        assert page.evaluate('sent.length') == 0
        page.evaluate('window.uploadOk = true')
        bar.locator('.voice-clip-stop').click()
        expect(page.locator('.attach-chip--voice')).to_have_count(1)
        expect(bar).to_have_count(0)
        # A denied microphone says so and leaves the composer as it was.
        page.evaluate('window.denyMic = true')
        mic.click()
        expect(bar).to_have_count(0)
        assert 'permission' in page.evaluate('toasts[toasts.length - 1][0]')
        assert errors == []
    finally:
        page.close()


# ---- Live text: words typed into the composer while recording ----

TEXT_COMPOSER = COMPOSER.replace(
    '<div class="composer-top-row">',
    '<textarea id="chatInput" style="width:100%;height:60px"></textarea><div class="composer-top-row">')

# Real VAD + phrase queue from the recorder; fake mic levels (`loud`) and a
# fake transcription route answering with `phrases` in order.
TEXT_HOST = r"""() => {
    window.staged = []; window.sent = []; window.toasts = []; window.loud = false;
    window.phrases = []; window.posts = []; window.cfg = { engine: 'server', max_clip_ms: 600000 };
    const R = CuttleChatVoiceRecorder;
    const recorder = Object.assign({}, R, {
        missingSupport: () => [], sourceKind: () => 'browser',
        openSource: () => ({
            start: async (opts) => { window.phraseTap = !!(opts && opts.phrases); },
            levels: () => [window.loud ? 0.2 : 0.0001],
            cutPhrase: async (keep) => (keep ? { blob: new Blob(['p'], { type: 'audio/webm' }), durationMs: 900 } : null),
            pause: () => {}, resume: () => {},
            stop: async (keep) => (keep ? { blob: new Blob(['abc'], { type: 'audio/webm;codecs=opus' }), durationMs: 1 } : null),
        }),
    });
    const fakeFetch = async (url, init) => {
        posts.push(init.body.get('purpose'));
        const next = phrases.shift();
        if (next === undefined || next === null) return { ok: false, status: 503, json: async () => ({ success: false, error: 'no key' }) };
        return { ok: true, status: 200, json: async () => ({ success: true, text: next }) };
    };
    const input = document.getElementById('chatInput');
    function render() {
        const row = document.getElementById('chatAttachChipsRow');
        row.innerHTML = ''; row.hidden = !staged.length;
        staged.forEach((att, idx) => {
            const chip = document.createElement('span'); chip.className = 'attach-chip';
            const name = document.createElement('span'); name.className = 'attach-chip-name';
            const remove = document.createElement('button'); remove.className = 'attach-chip-remove'; remove.textContent = '×';
            remove.onclick = () => { window.staged = CuttleChatAttachments.removeAttachmentAt(staged, idx);
                clip.attachmentRemoved(att); render(); };
            chip.append(name, remove);
            CuttleChatVoiceClip.decorateChip(chip, name, att);
            row.appendChild(chip);
        });
    }
    window.clip = CuttleChatVoiceClip.create({
        win: window, recorder, text: CuttleChatVoiceClipText, fetch: fakeFetch, config: async () => window.cfg,
        toast: (m, k) => toasts.push([m, k]),
        upload: async (file, extra) => {
            window.staged = CuttleChatAttachments.addAttachmentsToPending(staged, [Object.assign(
                { filename: file.name, mime: file.type, url: '/output/uploads/1/' + file.name, path: '/x/' + file.name }, extra)]);
            render(); return true;
        },
        composers: [{ button: document.getElementById('micButton'), row: document.getElementById('chatAttachChipsRow'), input }],
    });
    document.getElementById('sendButton').onclick = async () => {
        if (clip.isActive() && !(await clip.finishForSend())) return;
        sent.push({ text: input.value, attachments: CuttleChatAttachments.requestAttachmentPayload(staged) });
        input.value = ''; window.staged = []; render();
    };
    // Say one phrase: loud long enough to count as speech, then a pause.
    window.say = async (text) => {
        phrases.push(text); window.loud = true;
        await new Promise((r) => setTimeout(r, 450));
        window.loud = false;
    };
}"""


def _open_text(browser, static_server):
    page = browser.new_page(viewport={'width': 700, 'height': 400})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(static_server + '/agent_feed.html')
    page.set_content('<link rel="stylesheet" href="' + static_server + '/css/ui_boot.css">'
                     '<link rel="stylesheet" href="' + static_server + '/css/chat_page.css">' + TEXT_COMPOSER)
    for name in ('chat_attachments.js', 'chat_voice_recorder.js', 'chat_voice_clip_text.js', 'chat_voice_clip.js'):
        page.add_script_tag(url=static_server + '/js/chat/' + name)
    page.evaluate(TEXT_HOST)
    return page, errors


def _value(page):
    return page.evaluate("document.getElementById('chatInput').value")


def _wait_value(page, value):
    page.wait_for_function("v => document.getElementById('chatInput').value === v", arg=value, timeout=8000)


def test_words_appear_while_recording_can_be_edited_and_leave_with_the_clip(browser, static_server):
    page, errors = _open_text(browser, static_server)
    try:
        mic, box = page.locator('#micButton'), page.locator('#chatInput')
        chips = page.locator('.attach-chip--voice')
        box.fill('/cursor fix')
        mic.click()
        expect(page.locator('.voice-clip-bar')).to_have_attribute('data-state', 'recording')
        assert page.evaluate('phraseTap') is True
        # Each phrase is typed in after its pause, while still recording.
        page.evaluate("say('the hedder')")
        _wait_value(page, '/cursor fix the hedder')
        # The user fixes a typo mid-recording; the next phrase still lands after it.
        box.fill('/cursor fix the header')
        page.evaluate("say('on mobile')")
        _wait_value(page, '/cursor fix the header on mobile')
        mic.click()
        expect(chips).to_have_count(1)
        first = page.evaluate('staged[0]')
        assert first['transcribed'] is True and first['transcript'] == 'the header on mobile'
        assert page.evaluate('posts') == ['clip', 'clip']
        # The user types more, then records a second clip.
        box.press_sequentially(' please')
        mic.click()
        page.evaluate("say('and the footer')")
        _wait_value(page, '/cursor fix the header on mobile please and the footer')
        mic.click()
        expect(chips).to_have_count(2)
        # Removing a clip takes exactly its words out; typed text stays.
        chips.first.locator('.attach-chip-remove').click()
        assert _value(page) == '/cursor fix please and the footer'
        chips.first.locator('.attach-chip-remove').click()
        assert _value(page) == '/cursor fix please'
        expect(chips).to_have_count(0)
        assert errors == []
    finally:
        page.close()


def test_send_while_recording_waits_for_the_last_words(browser, static_server):
    page, errors = _open_text(browser, static_server)
    try:
        page.locator('#micButton').click()
        page.wait_for_timeout(300)
        # Still mid-phrase (no pause yet) when Send is pressed.
        page.evaluate("() => { phrases.push('ship it'); window.loud = true; }")
        page.wait_for_timeout(500)
        page.locator('#sendButton').click()
        page.wait_for_function('sent.length > 0')
        sent = page.evaluate('sent[0]')
        assert sent['text'] == 'ship it'
        assert len(sent['attachments']) == 1 and sent['attachments'][0]['transcribed'] is True
        expect(page.locator('.voice-clip-bar')).to_have_count(0)
        assert errors == []
    finally:
        page.close()


def test_lost_words_discard_and_the_configured_limit(browser, static_server):
    page, errors = _open_text(browser, static_server)
    try:
        mic, box = page.locator('#micButton'), page.locator('#chatInput')
        # A phrase the provider could not transcribe: the clip is attached
        # unmarked, so the server transcribes it at send.
        mic.click()
        page.evaluate("() => { phrases.push(null); window.loud = true; }")
        page.wait_for_timeout(500)
        mic.click()
        expect(page.locator('.attach-chip--voice')).to_have_count(1)
        assert page.evaluate("'transcribed' in staged[0]") is False
        assert 'transcribed when you send' in page.evaluate('toasts[toasts.length - 1][0]')
        # Discard takes the words heard so far back out.
        box.fill('note:')
        mic.click()
        page.evaluate("say('never mind')")
        _wait_value(page, 'note: never mind')
        page.locator('.voice-clip-discard').click()
        assert _value(page) == 'note:'
        expect(page.locator('.attach-chip--voice')).to_have_count(1)
        # Live text off (or no engine here): record as before, no phrase tap.
        page.evaluate("window.cfg = { engine: 'none', max_clip_ms: 1000 }")
        mic.click()
        # The configured longest clip stops and attaches by itself.
        expect(page.locator('.attach-chip--voice')).to_have_count(2, timeout=5000)
        assert page.evaluate('phraseTap') is False
        assert page.evaluate("'clip_id' in staged[1]") is False
        # ...exactly once (the auto-stop used to re-enter itself and never attach).
        assert page.evaluate("toasts.filter((t) => t[0].includes('reached 0:01')).length") == 1
        assert errors == []
    finally:
        page.close()
