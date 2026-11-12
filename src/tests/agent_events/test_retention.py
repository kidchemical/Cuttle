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


def test_search_index_text_is_capped_and_still_searchable(tmp_path):
    from api.agent_events.store import FTS_TEXT_CAP
    store=EventStore(tmp_path)
    store.write_batch([('event','q',{'kind':'edit','block_id':'a','text':'needle '+'x'*200_000,'args':{'big':'y'*500_000}})])
    with store.connection() as conn:
        assert conn.execute('SELECT length(text) FROM events_fts').fetchone()[0]<=FTS_TEXT_CAP
    assert store.events(search='needle')[0]['block_id']=='a'
    assert len(store.events('q',full=True)[0]['text'])>200_000  # the payload itself is untouched


def test_shrink_search_index_trims_rows_written_before_the_cap(tmp_path):
    from api.agent_events.store import FTS_TEXT_CAP
    store=EventStore(tmp_path)
    store.write_batch([('event','q',{'kind':'tool','block_id':'a','text':'needle'}),
                       ('event','q',{'kind':'tool','block_id':'b','text':'small'})])
    with store.connection() as conn:
        ident=conn.execute("SELECT id FROM events WHERE block_id='a'").fetchone()[0]
        conn.execute('DELETE FROM events_fts WHERE rowid=?',(ident,))
        conn.execute('INSERT INTO events_fts(rowid,summary,text) VALUES(?,?,?)',(ident,'needle','needle '+'z'*(FTS_TEXT_CAP*3)))
    assert store.shrink_search_index(batch=1)==1
    assert store.shrink_search_index()==0
    with store.connection() as conn:
        assert conn.execute('SELECT max(length(text)) FROM events_fts').fetchone()[0]<=FTS_TEXT_CAP
    assert [e['block_id'] for e in store.events(search='needle')]==['a']


def test_maintenance_does_not_rewrite_the_file_without_space_to_reclaim(tmp_path,monkeypatch):
    import sqlite3
    store=EventStore(tmp_path)
    store.write_batch([('event','done',{'kind':'tool','block_id':'a','text':'kept'}),
                       ('run','done',{'finished_at':time.time(),'success':True})])
    statements=[]
    real=sqlite3.connect
    def spy(*args,**kwargs):
        conn=real(*args,**kwargs)
        conn.set_trace_callback(statements.append)
        return conn
    monkeypatch.setattr(sqlite3,'connect',spy)
    store.maintain(DEFAULTS)
    assert not [s for s in statements if s.strip().upper().startswith('VACUUM')]
    assert store.vacuum(force=False)['events']==1
    assert not [s for s in statements if s.strip().upper().startswith('VACUUM')]
    store.vacuum()
    assert [s for s in statements if s.strip().upper().startswith('VACUUM')]


def test_maintenance_reads_payloads_only_for_rows_it_compacts(tmp_path,monkeypatch):
    store=EventStore(tmp_path)
    old=time.time()-200*86400
    store.write_batch([('event','old',{'kind':'tool','block_id':'a','text':'expired','t':old}),
                       ('event','old',{'kind':'tool','block_id':'b','text':'fresh'}),
                       ('run','old',{'finished_at':old,'success':True})])
    with store.connection() as conn:
        conn.execute("UPDATE events SET ts_end=? WHERE block_id='a'",(old,))
    decoded=[]
    real=store.decode
    monkeypatch.setattr(store,'decode',lambda row:(decoded.append(row['block_id']),real(row))[1])
    assert store.maintain(DEFAULTS)['compacted']==1
    assert decoded==['a']
    events={e['block_id']:e for e in store.events('old',full=True)}
    assert events['a']['compacted']=='retention' and events['b']['text']=='fresh'


def test_daily_maintenance_is_not_repeated_by_a_restart(tmp_path):
    from api.agent_events import maintenance
    now=1_800_000_000.0
    assert maintenance.seconds_until_due(tmp_path,now)==maintenance.MAINTENANCE_START_DELAY_SEC
    maintenance.mark_done(tmp_path,now)
    assert maintenance.seconds_until_due(tmp_path,now+3600)==pytest.approx(86400-3600)
    assert maintenance.seconds_until_due(tmp_path,now+90000)==maintenance.MAINTENANCE_START_DELAY_SEC
    # A clock that moved backwards must not postpone maintenance forever.
    assert maintenance.seconds_until_due(tmp_path,now-10)==maintenance.MAINTENANCE_START_DELAY_SEC


def test_scheduled_maintenance_settles_edits_off_the_calling_thread(tmp_path,monkeypatch):
    import threading
    from api.agent_events import maintenance
    from api.edit_attribution import journal
    store=EventStore(tmp_path)
    release=threading.Event();ran=[]
    def slow(path):
        ran.append(threading.current_thread().name)
        release.wait(5)
    monkeypatch.setattr(journal,'reconcile_commits',slow)
    monkeypatch.setattr(maintenance,'starred_sessions',lambda store:[])
    monkeypatch.setattr(maintenance,'release_refs',lambda store,all_completed=False:None)
    result=maintenance.scheduled(store)      # returns while settlement is still blocked
    assert result['settlement'] is None
    assert (tmp_path/'maintenance_at').exists()
    release.set()
    for _ in range(100):
        if ran:break
        time.sleep(0.02)
    assert ran==['agent-events-settlement']
