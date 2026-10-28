"""Shell-owned voice runtime: a screen lock must not hang up on the phone.

Desktop leaving the tab stops the call; on the phone the microphone foreground
service owns the call lifetime, so ``visibilitychange`` hidden (screen off) must
keep it. The shell used to stop on *any* hidden document, which is why the call
dropped every time the screen turned off even after the native fix.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SHELL_VOICE_JS = REPO / "src/web/js/shell/shell_voice.js"

node_only = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


@node_only
def test_shell_voice_keeps_call_on_mobile_screen_off():
    script = r"""
const assert = require('assert');
function el(){return {hidden:false,className:'',textContent:'',style:{},children:[],
  setAttribute(){},append(...c){this.children.push(...c);},appendChild(c){this.children.push(c);return c;},
  set onclick(f){this._onclick=f;},get onclick(){return this._onclick;}};}
const docListeners={};
const doc={head:el(),body:el(),createElement:()=>el(),addEventListener(t,f){docListeners[t]=f;},hidden:false};
let stopped=0;
const winEvents={};
const win={document:doc,isCuttleMobile:true,
  fetch:async()=>({ok:true,json:async()=>({success:true,flags:[]})}),
  addEventListener(t,f){winEvents[t]=f;},dispatchEvent(){},Event:function(){}};
win.CuttleChatVoiceLive={create:()=>({start:async()=>{},stop:async()=>{stopped++;},isOpen:()=>true,mute(){}})};
globalThis.window=win;
require(process.argv[1]);
const api=win.CuttleShellVoice.create({win,navigate(){}});
(async()=>{
  await api.start({session_id:'7',engine:'live',prefix:'',chat_request:{}});
  assert.ok(api.isOpen('7'));
  // Phone: screen off (hidden) keeps the call alive.
  doc.hidden=true;
  docListeners['visibilitychange']();
  await new Promise(r=>setTimeout(r,0));
  assert.strictEqual(stopped,0,'mobile screen-off must not hang up');
  assert.ok(api.isOpen('7'),'call stays open');
  // Desktop: leaving the tab still stops it.
  win.isCuttleMobile=false;
  docListeners['visibilitychange']();
  await new Promise(r=>setTimeout(r,0));
  assert.strictEqual(stopped,1,'desktop tab-hide stops the call');
})().catch(e=>{console.error(e);process.exit(1);});
"""
    subprocess.run(
        ["node", "-e", script, str(SHELL_VOICE_JS)],
        check=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )


@node_only
def test_shell_voice_mounts_in_strip_slot_and_publishes_height():
    """The call strip docks in the shell slot; overlays read its height var."""
    script = r"""
const assert = require('assert');
function el(){return {hidden:false,className:'',textContent:'',style:{},children:[],attrs:{},
  setAttribute(k,v){this.attrs[k]=v;},append(...c){this.children.push(...c);},appendChild(c){this.children.push(c);return c;},
  getBoundingClientRect(){return {height:this.hidden?0:41.2};}};}
const rootVars={};
const slot=el();
const doc={head:el(),body:el(),createElement:()=>el(),addEventListener(){},hidden:false,
  getElementById:id=>id==='shellVoiceStrip'?slot:null,
  documentElement:{style:{setProperty(k,v){rootVars[k]=v;}}}};
const win={document:doc,fetch:async()=>({ok:true,json:async()=>({success:true,flags:[]})}),
  addEventListener(){},dispatchEvent(){},Event:function(){}};
win.CuttleChatVoiceLive={create:host=>({start:async()=>{host.onStatus('Listening…');},stop:async()=>{},isOpen:()=>true,mute(){}})};
globalThis.window=win;
require(process.argv[1]);
const api=win.CuttleShellVoice.create({win,navigate(){}});
(async()=>{
  assert.strictEqual(doc.body.children.length,0,'must not float a bar on <body>');
  assert.strictEqual(slot.className,'shell-voice-strip');
  assert.ok(slot.hidden,'hidden until a call starts');
  await api.start({session_id:'7',engine:'live'});
  assert.ok(!slot.hidden);
  assert.strictEqual(rootVars['--shell-voice-strip-h'],'42px');
  assert.strictEqual(slot.attrs['data-state'],'live');
  await api.stop();
  assert.ok(slot.hidden);
  assert.strictEqual(rootVars['--shell-voice-strip-h'],'0px');
})().catch(e=>{console.error(e);process.exit(1);});
"""
    subprocess.run(
        ["node", "-e", script, str(SHELL_VOICE_JS)],
        check=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
