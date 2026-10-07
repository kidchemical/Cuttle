import subprocess
from api.agent_events.snapshots import begin,finish


def git(root,*args):
    return subprocess.run(['git',*args],cwd=root,check=True,capture_output=True).stdout


def test_snapshot_records_lines_and_preserves_index(tmp_path):
    git(tmp_path,'init')
    git(tmp_path,'config','user.name','Test'); git(tmp_path,'config','user.email','test@example.test')
    (tmp_path/'tracked.txt').write_text('one\ntwo\n')
    git(tmp_path,'add','.'); git(tmp_path,'commit','-m','init')
    (tmp_path/'tracked.txt').write_text('one\npreexisting\n')
    index=(tmp_path/'.git/index').read_bytes()
    head=git(tmp_path,'rev-parse','HEAD')
    start=begin(str(tmp_path),'test-query')
    (tmp_path/'tracked.txt').write_text('one\nagent\n')
    (tmp_path/'new.txt').write_text('created\n')
    event=finish(start)
    assert '-preexisting' in event['text'] and '+agent' in event['text']
    assert '+created' in event['text']
    assert (tmp_path/'.git/index').read_bytes()==index
    assert git(tmp_path,'rev-parse','HEAD')==head
    assert not event['ambiguous']


def test_overlap_is_reported(tmp_path):
    git(tmp_path,'init')
    first=begin(str(tmp_path),'first'); second=begin(str(tmp_path),'second')
    assert finish(first)['overlap']==['second']
    assert finish(second)['overlap']==['first']


def test_step_snapshot_keeps_intermediate_edits(tmp_path):
    from api.agent_events.snapshots import record_step
    from api.agent_events.writer import get_writer
    from api.agent_events.store import EventStore,state_dir
    git(tmp_path,'init')
    start=begin(str(tmp_path),'steps')
    (tmp_path/'new.txt').write_text('intermediate\n')
    record_step('steps','tool-one')
    (tmp_path/'new.txt').write_text('final\n')
    record_step('steps','tool-two')
    finish(start)
    get_writer().flush()
    events=EventStore(state_dir()).events('steps',full=True)
    assert '+intermediate' in events[0]['text']
    assert '-intermediate' in events[1]['text'] and '+final' in events[1]['text']


def test_staged_rename_removes_source_from_snapshot(tmp_path):
    git(tmp_path,'init');git(tmp_path,'config','user.name','Test');git(tmp_path,'config','user.email','test@example.test')
    (tmp_path/'old.txt').write_text('same content\n')
    git(tmp_path,'add','.');git(tmp_path,'commit','-m','init')
    start=begin(str(tmp_path),'rename')
    git(tmp_path,'mv','old.txt','new.txt')
    staged=(tmp_path/'.git/index').read_bytes()
    event=finish(start)
    assert 'rename from old.txt' in event['text']
    assert 'rename to new.txt' in event['text']
    assert (tmp_path/'.git/index').read_bytes()==staged
