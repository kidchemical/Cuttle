import json
import time
import pytest
from api.agent_events.store import EventStore
from api.storage.service import DEFAULTS,validate


def complete(store,qid,age,kind='thinking',block='a'):
    stamp=time.time()-age*86400
    text='secret long thinking '*2000
    store.write_batch([('event',qid,{'kind':kind,'block_id':block,'text':text,'t':stamp}),
                       ('run',qid,{'finished_at':stamp,'success':True,'harness':{'chat_session_id':qid}})])
    with store.connection() as conn:
        conn.execute('UPDATE events SET ts_end=? WHERE query_id=?',(stamp,qid))
    return text


def test_thinking_expires_and_search_for_full_text_is_removed(tmp_path):
    store=EventStore(tmp_path)
    text=complete(store,'one',15)
    complete(store,'two',10,kind='tool')
    result=store.maintain(DEFAULTS)
    assert result['compacted']==1
    thought=store.events('one',full=True)[0]
    assert thought['compacted']=='thinking_expired'
    assert 'text' not in thought
    assert len(thought['summary'])<=300
    assert store.events('two',full=True)[0]['text']==text
    with store.connection() as conn:
        row=conn.execute('SELECT text FROM events_fts WHERE rowid=?',(thought['id'],)).fetchone()
    assert row[0]==''
    assert len(list((tmp_path/'blobs').rglob('*.gz')))==1 # shared blob still referenced by tool


def test_starred_defaults_and_override(tmp_path):
    store=EventStore(tmp_path)
    complete(store,'starred',100,kind='tool')
    store.maintain({**DEFAULTS,'starred_retention_days':180},starred=['starred'])
    assert store.events('starred',full=True)[0]['text']
    store.maintain(DEFAULTS,starred=['starred'])
    assert store.events('starred',full=True)[0]['compacted']=='retention'


def test_active_runs_are_protected(tmp_path):
    store=EventStore(tmp_path)
    store.write_batch([('event','active',{'kind':'thinking','block_id':'a','text':'live'})])
    store.maintain({**DEFAULTS,'thinking_mode':'summary_only'})
    assert store.events('active',full=True)[0]['text']=='live'
    with pytest.raises(ValueError):store.reset()


def test_policy_validation():
    assert validate({})['quota_mb']==5000
    for raw in ({'quota_mb':True},{'unknown':1},{'thinking_full_days':91},{'thinking_mode':'invent'}):
        with pytest.raises(ValueError):validate(raw)


def test_quota_compacts_oldest_completed_payload_and_protects_active(tmp_path):
    import os
    store=EventStore(tmp_path)
    payload=os.urandom(1_000_000).hex()
    store.write_batch([('event','old',{'kind':'tool','block_id':'a','text':payload}),
                       ('run','old',{'finished_at':time.time(),'success':True}),
                       ('event','active',{'kind':'tool','block_id':'a','text':'still working'})])
    assert store.stats()['bytes']>1_000_000
    result=store.maintain({**DEFAULTS,'quota_mb':1})
    assert result['bytes']<1_000_000
    assert store.events('old',full=True)[0]['compacted']=='quota'
    assert store.events('active',full=True)[0]['text']=='still working'
