from api.agent_events.store import EventStore, state_dir
from api.agent_events.writer import get_writer
from api.query_events import bind_query_id, reset_query_id, record_agent_text, record_agent_tool
from api.query_tracker import start_query_tracking, get_query_tracker, finish_query_tracking


def test_full_detail_survives_preview_caps_and_restart():
    qid=start_query_tracking('test', {'web_ui':True})
    token=bind_query_id(qid)
    try:
        for i in range(450):
            get_query_tracker(qid).add_event('status', {'text':str(i)})
        text='full text '*10000
        record_agent_text('thinking',text,'thinking-1')
        record_agent_tool('tool-1','edit',phase='started',args={'new_text':text})
        record_agent_tool('tool-1','edit',phase='completed',result=text)
        finish_query_tracking(success=True)
        get_writer().flush()
        store=EventStore(state_dir())
        events=store.events(qid,limit=500,full=True)
        assert len(events)==454
        assert next(e for e in events if e['kind']=='thinking')['text']==text
        tool=next(e for e in events if e['kind']=='tool')
        assert tool['args']['new_text']==text
        assert tool['text']==text and tool['phase']=='completed'
        assert store.run(qid)['status']=='ok'
        assert store.stats()['events']==454
        assert list((state_dir()/'blobs').rglob('*.gz'))
    finally:
        reset_query_id(token)


def test_streaming_items_update_in_place(tmp_path):
    store=EventStore(tmp_path)
    store.write_batch([('event','q',{'kind':'writing','block_id':'a','text':'hello'}),
                       ('event','q',{'kind':'writing','block_id':'b','text':'second'}),
                       ('event','q',{'kind':'writing','block_id':'a','text':'hello world'})])
    rows=store.events('q',full=True)
    assert [e['text'] for e in rows]==['hello world','second']
    assert rows[0]['rev']==2 and rows[0]['seq']==1


def test_writer_recovers_failed_batch_without_losing_identity(tmp_path,monkeypatch):
    import time
    from api.agent_events.writer import EventWriter
    original=EventStore.write_batch
    failed=[]
    def fail_once(store,messages):
        if not failed:
            failed.append(True)
            raise OSError('temporary disk failure')
        return original(store,messages)
    monkeypatch.setattr(EventStore,'write_batch',fail_once)
    writer=EventWriter(tmp_path)
    try:
        writer.submit('event','recover',{'kind':'tool','block_id':'one','text':'retained'})
        # This marker may follow the failed batch or trigger its recovery.
        try:writer.flush()
        except RuntimeError:writer.flush()
        assert EventStore(tmp_path).events('recover',full=True)[0]['text']=='retained'
        assert not list((tmp_path/'pending').glob('*.json.gz'))
    finally:writer.close()


def test_summary_only_mode_never_writes_full_thinking(tmp_path,monkeypatch):
    from api.agent_events.writer import EventWriter
    from api.storage.service import DEFAULTS
    monkeypatch.setattr('api.storage.service.policy',lambda:{**DEFAULTS,'thinking_mode':'summary_only'})
    writer=EventWriter(tmp_path)
    try:
        text='exposed thinking '*1000
        writer.submit('event','q',{'kind':'thinking','block_id':'a','text':text})
        writer.flush()
        event=EventStore(tmp_path).events('q',full=True)[0]
        assert 'text' not in event
        assert len(event['summary'])<=300
        assert event['compacted']=='summary_only'
        assert not list((tmp_path/'blobs').rglob('*.gz'))
    finally:writer.close()
