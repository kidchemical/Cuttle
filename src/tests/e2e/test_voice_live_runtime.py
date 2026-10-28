"""Shell voice survives presenter teardown; no microphone or provider calls."""
from pathlib import Path

from playwright.sync_api import expect
from .test_shared_diff_modal import browser  # noqa: F401

REPO = Path(__file__).resolve().parents[3]


def test_voice_stays_bound_across_space_presenters_and_restores(browser):
    page = browser.new_page()
    try:
        page.set_content('<html><head></head><body><iframe id="chatA"></iframe></body></html>')
        page.evaluate('''() => {
            window.starts=[];window.stops=0;window.captionsA=[];window.captionsB=[];
            window.CuttleChatVoiceLive={create:host=>{
                window.voiceHost=host;let live=false;
                return {start:async context=>{starts.push(context.session_id);live=true;host.onStatus('Listening…');},
                    stop:async()=>{stops++;live=false;host.onStatus('Disconnected');},mute(){},isOpen:()=>live};
            }};
        }''')
        page.add_script_tag(path=str(REPO / 'src/web/js/shell/shell_voice.js'))
        page.evaluate('''async () => {
            window.runtime=CuttleShellVoice.create({win:window});
            window.detachA=runtime.attach(42,{onTranscript:(role,text)=>captionsA.push(text)});
            await runtime.start({session_id:42,engine:'live'});
            voiceHost.onTranscript('user','Inspect my project');
            detachA();document.getElementById('chatA').remove();
            // Space B has a different chat and its own presenter.
            runtime.attach(17,{onTranscript:(role,text)=>captionsB.push(text)});
            voiceHost.onTranscript('assistant','I am checking it.');
        }''')
        assert page.evaluate('stops') == 0
        assert page.evaluate('runtime.isOpen(42)')
        assert not page.evaluate('runtime.isOpen(17)')
        assert page.evaluate('captionsB') == []
        expect(page.locator('.shell-voice-strip')).to_be_visible()
        page.evaluate('''async () => {
            window.restored=[];
            runtime.attach(42,{onTranscript:(role,text)=>restored.push(text)});
            await runtime.start({session_id:42,engine:'live'});
        }''')
        assert page.evaluate('starts') == [42]
        assert page.evaluate('restored') == ['Inspect my project', 'I am checking it.']
        page.get_by_role('button', name='End voice', exact=True).click()
        expect(page.locator('.shell-voice-strip')).to_be_hidden()
        assert page.evaluate('stops') == 1
    finally:
        page.close()


SHELL_FIXTURE = '''<html class="{html_class}"><head></head><body>
<div class="app-shell" id="appShell">
  <div class="split-container split-horizontal" id="splitContainer">
    <div class="split-column"><main class="shell-main" id="shellMain" style="flex:1"></main></div>
  </div>
  <div class="shell-voice-strip" id="shellVoiceStrip" hidden></div>
</div>
<div class="notifications-slide-layer">
  <div class="notifications-popover notifications-popover-open" id="notificationsPopover"></div>
</div>
<div class="notifications-popover-backdrop notifications-popover-backdrop-visible"></div>
<div class="notifications-popover workspace-popover notifications-popover-open" id="workspacePopover"></div>
</body></html>'''


def _open_shell_with_call(page, html_class='', safe_bottom='0px'):
    page.set_content(SHELL_FIXTURE.replace('{html_class}', html_class))
    page.add_style_tag(path=str(REPO / 'src/web/css/safe_area.css'))
    page.add_style_tag(path=str(REPO / 'src/web/css/app_shell.css'))
    page.add_style_tag(path=str(REPO / 'src/web/css/shell_voice.css'))
    page.evaluate('''safeBottom => {
        document.documentElement.style.setProperty('--safe-area-inset-bottom', safeBottom);
        window.stops=0;
        window.CuttleChatVoiceLive={create:host=>{let live=false;return {
            start:async()=>{live=true;host.onStatus('Listening…');},
            stop:async()=>{stops++;live=false;host.onStatus('Disconnected');},
            isOpen:()=>live,mute(){}};}};
    }''', safe_bottom)
    page.add_script_tag(path=str(REPO / 'src/web/js/shell/shell_voice.js'))
    page.add_script_tag(path=str(REPO / 'src/web/js/shared/toast.js'))
    page.evaluate('''async () => {
        window.runtime=CuttleShellVoice.create({win:window,navigate(){}});
        await runtime.start({session_id:7,engine:'live'});
        showToast('Build finished', 'success', {duration: 60000});
    }''')
    page.locator('#shellVoiceStrip').wait_for(state='visible')


def _strip_layout(page):
    return page.evaluate('''() => {
        const rect = sel => document.querySelector(sel).getBoundingClientRect();
        const strip = rect('#shellVoiceStrip');
        const end = document.querySelector('.shell-voice-end').getBoundingClientRect();
        const hit = document.elementFromPoint(end.left + end.width / 2, end.top + end.height / 2);
        return {
            stripTop: strip.top, stripBottom: strip.bottom, viewport: innerHeight,
            stripVar: getComputedStyle(document.documentElement).getPropertyValue('--shell-voice-strip-h').trim(),
            paneBottom: rect('#splitContainer').bottom,
            notifBottom: rect('#notificationsPopover').bottom,
            workspaceBottom: rect('#workspacePopover').bottom,
            toastBottom: rect('.cuttle-toast').bottom,
            endHitIsEnd: !!(hit && hit.closest('.shell-voice-end')),
            state: document.querySelector('#shellVoiceStrip').dataset.state,
        };
    }''')


def test_voice_strip_docks_below_panes_and_overlays_stop_above_it(browser):
    """Side panels, their backdrop and toasts must never cover End voice."""
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    try:
        _open_shell_with_call(page)
        box = _strip_layout(page)
        assert box['stripBottom'] == box['viewport'], box
        assert box['stripVar'] == f"{round(box['stripBottom'] - box['stripTop'])}px", box
        assert box['paneBottom'] <= box['stripTop'] + 0.5, box
        assert box['notifBottom'] <= box['stripTop'] + 0.5, box
        assert box['workspaceBottom'] <= box['stripTop'] + 0.5, box
        assert box['toastBottom'] <= box['stripTop'], box
        assert box['endHitIsEnd'], box
        assert box['state'] == 'live', box

        strip = page.locator('#shellVoiceStrip')
        assert strip.locator('button:visible').count() == 2
        assert strip.inner_text().strip() == '', 'call bar is icon-only: no handle or status text'
        mute = page.get_by_role('button', name='Mute', exact=True)
        for btn in (mute, page.get_by_role('button', name='End voice', exact=True)):
            b = btn.bounding_box()
            assert b['width'] == b['height'] == 56, b
            assert btn.evaluate('el => getComputedStyle(el).borderRadius') == '50%'
        mute.click()
        assert mute.get_attribute('aria-pressed') == 'true'
        assert strip.get_attribute('data-state') == 'muted'

        page.get_by_role('button', name='End voice', exact=True).click()
        expect(page.locator('#shellVoiceStrip')).to_be_hidden()
        assert page.evaluate('stops') == 1
        assert page.evaluate(
            "getComputedStyle(document.documentElement).getPropertyValue('--shell-voice-strip-h').trim()"
        ) == '0px'
        assert page.evaluate(
            "document.getElementById('notificationsPopover').getBoundingClientRect().bottom"
        ) == 800
    finally:
        page.close()


def test_voice_strip_clears_phone_nav_bar_and_stays_reachable(browser):
    """Phone: the strip owns the bottom safe area and stays above full-width panels."""
    page = browser.new_page(viewport={"width": 390, "height": 844})
    try:
        _open_shell_with_call(page, html_class='is-cuttle-mobile', safe_bottom='34px')
        padding = page.evaluate(
            "getComputedStyle(document.getElementById('shellVoiceStrip')).paddingBottom"
        )
        assert padding == '46px', padding
        box = _strip_layout(page)
        assert box['stripBottom'] == box['viewport'], box
        assert box['notifBottom'] <= box['stripTop'] + 0.5, box
        assert box['toastBottom'] <= box['stripTop'], box
        assert box['endHitIsEnd'], box
        notif_pad = page.evaluate(
            "getComputedStyle(document.getElementById('notificationsPopover')).paddingBottom"
        )
        assert notif_pad == '0px', notif_pad
    finally:
        page.close()


def test_live_webrtc_protocol_drains_final_usage_before_transport_close(browser):
    page = browser.new_page()
    try:
        page.set_content('<html><body></body></html>')
        page.evaluate('''() => {
            window.requests=[];window.statuses=[];window.usages=[];window.captions=[];
            window.track={enabled:true,stopped:false,stop(){this.stopped=true;}};
            Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia:async()=>({getTracks:()=>[track]})},configurable:true});
            window.Audio=class{play(){return Promise.resolve();}pause(){}};
            window.RTCPeerConnection=class{
                constructor(){window.peer=this;this.iceGatheringState='complete';}
                addTrack(){}createDataChannel(){return this.dc={readyState:'open',close(){this.closed=true;},send(){}};}
                async createOffer(){return {type:'offer',sdp:'client-offer'};}
                async setLocalDescription(d){this.localDescription=d;}
                async setRemoteDescription(d){this.answer=d;}
                close(){this.closed=true;}
            };
            window.fakeFetch=async(url,opts)=>{
                requests.push({url,opts});return {ok:true,json:async()=>({success:true,session:
                    url.endsWith('/sessions')?{id:'voice123',sdp:'provider-answer'}:
                    {state:'active',seconds:15,cost:.0125,model:'gpt-live-1',transcripts:[]}})};
            };
        }''')
        page.add_script_tag(path=str(REPO / 'src/web/js/chat/chat_voice_live.js'))
        page.evaluate('''async () => {
            window.voice=CuttleChatVoiceLive.create({fetch:fakeFetch,onStatus:s=>statuses.push(s),
                onUsage:u=>usages.push(u),onTranscript:(r,t)=>captions.push([r,t]),onError:e=>{throw Error(e);}});
            await voice.start({session_id:42,prefix:'/codex '});
            window.emit=e=>peer.dc.onmessage({data:JSON.stringify(e)});
            emit({type:'session.started',event_id:'start'});
            emit({type:'session.input_transcript.delta',event_id:'input',delta:'Check this'});
            emit({type:'session.input_transcript.delta',event_id:'input',delta:'Check this'});
            window.stopped=voice.stop();
        }''')
        assert page.evaluate('peer.answer.sdp') == 'provider-answer'
        assert page.evaluate('JSON.parse(requests[0].opts.body).sdp') == 'client-offer'
        assert page.evaluate('requests.every(r=>!r.opts?.headers?.Authorization)')
        assert page.evaluate('captions') == [['user', 'Check this']]
        assert not page.evaluate('track.enabled')
        assert not page.evaluate('!!peer.closed')
        page.evaluate('''async () => {
            emit({type:'session.closed',event_id:'close',usage:{seconds:90}});
            await stopped;
        }''')
        assert page.evaluate('peer.closed && track.stopped && !voice.isOpen()')
        assert page.evaluate('usages.at(-1).cost') == .075
    finally:
        page.close()


def test_usage_outbox_retains_new_events_during_ack_and_recovers(browser):
    page = browser.new_page()
    try:
        page.set_content('<html><body></body></html>')
        # A private object stands in for origin storage, independent of the host account.
        page.evaluate('''() => {
            const items={};Object.defineProperty(window,'localStorage',{value:{
                getItem:k=>items[k]||null,setItem:(k,v)=>items[k]=v},configurable:true});
            window.posts=[];window.fetch=async(url,opts)=>{
                posts.push(JSON.parse(opts.body));return {ok:true,json:async()=>({success:true})};};
        }''')
        page.add_script_tag(path=str(REPO / 'src/web/js/chat/chat_voice_receipts.js'))
        page.evaluate('''async () => {
            const first={connection_id:'conn',responses:[{_usage_id:'a',input_tokens:10}],transcriptions:[]};
            CuttleVoiceReceipts.enqueue(first);
            CuttleVoiceReceipts.enqueue({...first,responses:[{_usage_id:'b',input_tokens:20}]});
            CuttleVoiceReceipts.acknowledge(first);
            await CuttleVoiceReceipts.recover(fetch);
        }''')
        assert page.evaluate('posts.length') == 1
        assert page.evaluate('posts[0].responses.map(e=>e._usage_id)') == ['b']
        assert page.evaluate('JSON.parse(localStorage.getItem("cuttle-realtime-usage-v1"))') == {}
    finally:
        page.close()


def test_live_native_http_setup_uses_bridge_and_drains_final_event(browser):
    page = browser.new_page()
    try:
        page.set_content('<html><body></body></html>')
        page.evaluate('''() => {
            window.requests=[];window.nativeStarts=[];window.nativeStops=0;window.usages=[];window.errors=[];
            window.cuttleMobile={liveVoiceStart:id=>{nativeStarts.push(id);return 'ok';},liveVoiceStop:()=>nativeStops++,liveVoiceMute(){}};
            window.fakeFetch=async(url,opts)=>{requests.push({url,opts});return {ok:true,json:async()=>({success:true,
                session:url.endsWith('/sessions')?{id:'native123'}:{state:'active',seconds:0,cost:0,model:'gpt-live-1'}})};};
        }''')
        page.add_script_tag(path=str(REPO / 'src/web/js/chat/chat_voice_live.js'))
        assert not page.evaluate('isSecureContext')
        page.evaluate('''async () => {
            window.voice=CuttleChatVoiceLive.create({fetch:fakeFetch,onStatus(){},onTranscript(){},onUsage:u=>usages.push(u),onError:e=>errors.push(e)});
            await voice.start({session_id:42});
            dispatchEvent(new CustomEvent('cuttle-live-voice',{detail:{type:'error',event_id:'rejected',error:{message:'client.data_channel is only supported for WebRTC sessions.'}}}));
            window.stopped=voice.stop();window.stoppedAgain=voice.stop();
        }''')
        assert page.evaluate('JSON.parse(requests[0].opts.body).transport') == 'native'
        assert len(page.evaluate('JSON.parse(requests[0].opts.body).request_id')) == 32
        assert page.evaluate('nativeStarts') == ['native123']
        assert page.evaluate('nativeStops') >= 1
        assert page.evaluate('errors') == ['client.data_channel is only supported for WebRTC sessions.']
        assert page.evaluate('stopped === stoppedAgain')
        assert page.evaluate('requests.filter(r=>r.url.endsWith("/close")).length') == 1
        page.evaluate('''async () => {
            dispatchEvent(new CustomEvent('cuttle-live-voice',{detail:{type:'session.closed',event_id:'close',usage:{seconds:60}}}));
            await stopped;
        }''')
        assert not page.evaluate('voice.isOpen()')
        assert page.evaluate('usages.at(-1).cost') == .05
    finally:
        page.close()
