import subprocess
import sys
from api.agent_events.snapshots import begin,finish


def git(root,*args):
    return subprocess.run(['git',*args],cwd=root,check=True,capture_output=True).stdout


def test_snapshot_records_lines_and_preserves_index(tmp_path):
    git(tmp_path,'init')
    git(tmp_path,'config','user.name','Test'); git(tmp_path,'config','user.email','test@example.test')
    (tmp_path/'tracked.txt').write_text('one\ntwo\n', encoding="utf-8")
    git(tmp_path,'add','.'); git(tmp_path,'commit','-m','init')
    (tmp_path/'tracked.txt').write_text('one\npreexisting\n', encoding="utf-8")
    index=(tmp_path/'.git/index').read_bytes()
    head=git(tmp_path,'rev-parse','HEAD')
    start=begin(str(tmp_path),'test-query')
    (tmp_path/'tracked.txt').write_text('one\nagent\n', encoding="utf-8")
    (tmp_path/'new.txt').write_text('created\n', encoding="utf-8")
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
    (tmp_path/'new.txt').write_text('intermediate\n', encoding="utf-8")
    record_step('steps','tool-one')
    (tmp_path/'new.txt').write_text('final\n', encoding="utf-8")
    record_step('steps','tool-two')
    finish(start)
    get_writer().flush()
    events=EventStore(state_dir()).events('steps',full=True)
    assert '+intermediate' in events[0]['text']
    assert '-intermediate' in events[1]['text'] and '+final' in events[1]['text']


def test_staged_rename_removes_source_from_snapshot(tmp_path):
    git(tmp_path,'init');git(tmp_path,'config','user.name','Test');git(tmp_path,'config','user.email','test@example.test')
    (tmp_path/'old.txt').write_text('same content\n', encoding="utf-8")
    git(tmp_path,'add','.');git(tmp_path,'commit','-m','init')
    start=begin(str(tmp_path),'rename')
    git(tmp_path,'mv','old.txt','new.txt')
    staged=(tmp_path/'.git/index').read_bytes()
    event=finish(start)
    assert 'rename from old.txt' in event['text']
    assert 'rename to new.txt' in event['text']
    assert (tmp_path/'.git/index').read_bytes()==staged


def test_file_vanishing_between_status_and_add_is_skipped(tmp_path, monkeypatch):
    import api.agent_events.snapshots as snapshots

    git(tmp_path,'init')
    git(tmp_path,'config','user.name','Test'); git(tmp_path,'config','user.email','test@example.test')
    (tmp_path/'kept.txt').write_text('kept\n', encoding="utf-8")
    for i in range(128):
        (tmp_path/f'file-{i}.txt').write_text('keep\n', encoding="utf-8")
    (tmp_path/'db.sqlite3-shm').write_text('transient\n', encoding="utf-8")
    real = snapshots.git
    adds = []
    def racing_git(root, *args, env=None, input=None):
        if '--pathspec-from-file=-' in args:
            adds.append(args)
            (tmp_path/'db.sqlite3-shm').unlink(missing_ok=True)
        return real(root, *args, env=env, input=input)
    monkeypatch.setattr(snapshots, 'git', racing_git)
    out = snapshots.snapshot(str(tmp_path), 'race', 'start')
    assert out['skipped'] == {'db.sqlite3-shm': 'vanished'}
    assert 'kept.txt' in real(tmp_path, 'ls-tree', '--name-only', out['tree'])
    assert len(adds) <= 17


def test_snapshot_stages_dirty_paths_in_one_git_call(tmp_path, monkeypatch):
    import api.agent_events.snapshots as snapshots

    git(tmp_path,'init')
    git(tmp_path,'config','user.name','Test'); git(tmp_path,'config','user.email','test@example.test')
    for i in range(25):
        (tmp_path/f'f{i}.txt').write_text(f'{i}\n', encoding="utf-8")
    special = 'literal[1] file.txt' if sys.platform == 'win32' else 'literal[1]\nfile.txt'
    (tmp_path/special).write_text('literal\n', encoding="utf-8")
    real = snapshots.git
    adds = []
    def counting_git(root, *args, env=None, input=None):
        if args[:1] == ('--literal-pathspecs',) and 'add' in args:
            adds.append(args)
        return real(root, *args, env=env, input=input)
    monkeypatch.setattr(snapshots, 'git', counting_git)
    out = snapshots.snapshot(str(tmp_path), 'batch', 'start')
    assert len(adds) == 1
    staged = real(tmp_path, 'ls-tree', '-r', '-z', '--name-only', out['tree']).split('\0')[:-1]
    assert len(staged) == 26
    assert special in staged


def test_file_vanishing_during_add_is_skipped_not_fatal(tmp_path, monkeypatch):
    """A SQLite -wal can be checkpointed away after git matched it (CI race)."""
    from api.agent_events import snapshots

    git(tmp_path,'init')
    (tmp_path/'kept.txt').write_text('kept\n', encoding="utf-8")
    (tmp_path/'db.sqlite3-wal').write_text('wal\n', encoding="utf-8")
    real=snapshots.git

    def flaky(root,*args,env=None,input=None):
        if 'add' in args and input and b'db.sqlite3-wal' in input:
            raise RuntimeError("fatal: unable to stat 'db.sqlite3-wal': No such file or directory")
        return real(root,*args,env=env,input=input)

    monkeypatch.setattr(snapshots,'git',flaky)
    result=snapshots.snapshot(str(tmp_path),'vanish','start')
    assert result['skipped']=={'db.sqlite3-wal':'vanished'}
    assert 'kept.txt' in real(tmp_path,'ls-tree','--name-only',result['tree'])


def test_file_vanishing_before_add_is_skipped_not_fatal(tmp_path, monkeypatch):
    """Gone between git status and the size/binary probe (the other half of the race)."""
    from pathlib import Path
    from api.agent_events import snapshots

    git(tmp_path,'init')
    (tmp_path/'kept.txt').write_text('kept\n', encoding="utf-8")
    (tmp_path/'db.sqlite3-wal').write_text('wal\n', encoding="utf-8")
    real_open=Path.open

    def flaky_open(self, *a, **k):
        if self.name=='db.sqlite3-wal':
            raise FileNotFoundError(2, 'No such file or directory', str(self))
        return real_open(self, *a, **k)

    monkeypatch.setattr(Path,'open',flaky_open)
    result=snapshots.snapshot(str(tmp_path),'vanish2','start')
    assert result['skipped']=={'db.sqlite3-wal':'vanished'}
    assert 'kept.txt' in snapshots.git(tmp_path,'ls-tree','--name-only',result['tree'])
