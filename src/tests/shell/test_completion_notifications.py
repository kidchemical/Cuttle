"""Permission, privacy, and exactly-once completion delivery without OS effects."""
import json
import subprocess
from pathlib import Path
import pytest
import shutil

MODULE = Path(__file__).resolve().parents[2] / 'web/js/shared/completion_notifications.js'

@pytest.mark.skipif(not shutil.which('node'), reason='Node required')
def test_completion_delivery():
    script = r'''
const assert = require('node:assert/strict');
const {plan,createBroker} = require(process.argv[1]);
(async () => {
 let now = 1000, permission = 'granted', flag = true, master = true;
 const saved = new Map(), shown = [];
 let seq = 0;
 const host = {storage:{getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)},
  now:()=>now,id:()=>String(++seq),permission:()=>permission,masterEnabled:()=>master,
  flagEnabled:async()=>flag,show:async p=>shown.push(p)};
 let b = createBroker(host);
 b.begin('42'); now += 61000;
 assert.equal(await b.finishTurn('42','done','secret title'),false); // opt-in required
 b.configure(true);
 b.begin('42'); now += 1000;
 assert.equal(await b.finishTurn('42','done'),false); // short reply
 b.begin(null); now += 61000; b.adopt(null,'db_session_42');
 assert.equal(await b.finishTurn('42','done','secret title'),true);
 assert.equal(shown[0].body,'Open the chat to view the result.');
 assert.equal(shown[0].sessionId,'42');
 assert.equal(await b.finishTurn('42','done'),false);
 const mesh = {id:'batch:one',sessionId:'CH-000042',kind:'mesh',outcome:'failed',label:'private file'};
 await Promise.all([b.complete(mesh),b.complete(mesh)]);
 assert.equal(shown.length,2);
 assert.equal(shown[1].title,'Cuttle — Work failed');
 b = createBroker(host); // reload retains delivery history
 assert.equal(await b.complete(mesh),false);
 b.configure(true,true);
 assert.equal(await b.complete({...mesh,id:'two',outcome:'cancelled'}),true);
 assert.equal(shown.at(-1).body,'private file');
 permission='denied'; assert.equal(await b.complete({...mesh,id:'denied'}),false);
 permission='granted';flag=false; assert.equal(await b.complete({...mesh,id:'off'}),false);
 flag=true;master=false; assert.equal(await b.complete({...mesh,id:'master'}),false);
 master=true;
 b.begin('43');now+=61000;b=createBroker(host);
 assert.equal(await b.finishTurn('43','failed'),true); // reload of observed running turn
 b.begin('43');now+=61000;assert.equal(await b.cancel('43'),true);
 assert.equal(await b.finishTurn('43','done'),false); // cancelled cannot later look successful
 assert.equal(plan({...mesh,outcome:'running'},b.prefs(),now),null);
 assert.equal(plan({...mesh,sessionId:null},b.prefs(),now),null);
 assert.equal(await b.complete({...mesh,id:'missing-start',kind:'job'}),false);
 const broken = createBroker({...host,show:()=>{throw Error('unsupported client')}});
 assert.equal(await broken.complete({...mesh,id:'unsupported'}),false);
 console.log(JSON.stringify({shown:shown.length}));
})().catch(e=>{console.error(e);process.exit(1)});
'''
    result = subprocess.run(['node', '-e', script, str(MODULE)], capture_output=True, text=True, encoding="utf-8", check=True)
    assert json.loads(result.stdout)['shown'] == 5
