import sqlite3
import time
from api.edit_attribution.journal import import_legacy
from api.agent_events.store import EventStore


def test_legacy_import_is_incremental_and_preserves_original(tmp_path):
    source=tmp_path/'legacy.db';target=tmp_path/'new'/'agent_events.sqlite3'
    conn=sqlite3.connect(source)
    conn.executescript('''CREATE TABLE edit_events(id INTEGER PRIMARY KEY,repo_root TEXT,rel_path TEXT,
        ts REAL,agent_id TEXT,model TEXT,query_id TEXT,chat_session_id TEXT,
        digest_before TEXT,digest_after TEXT,commit_sha TEXT);''')
    conn.execute('INSERT INTO edit_events VALUES(1,?,?,?,?,?,?,?,?,?,?)',('/repo','old.py',time.time()-8*86400,'codex','low','q','1','a','b',None));conn.commit()
    assert import_legacy(source,target)=={'imported':1,'stale':1}
    assert import_legacy(source,target)['imported']==0
    conn.execute('INSERT INTO edit_events VALUES(2,?,?,?,?,?,?,?,?,?,?)',('/repo','new.py',time.time(),'codex','low','q','1','b','c',None));conn.commit()
    assert import_legacy(source,target)['imported']==1
    assert conn.execute('SELECT COUNT(*) FROM edit_events').fetchone()[0]==2
    conn.close()
    with EventStore(target.parent).connection() as db:
        assert db.execute('SELECT settlement FROM edit_events WHERE legacy_id=1').fetchone()[0]=='stale'


def test_reset_does_not_resurrect_imported_journal(tmp_path):
    source=tmp_path/'legacy.db';target=tmp_path/'new'/'agent_events.sqlite3'
    conn=sqlite3.connect(source)
    conn.executescript('''CREATE TABLE edit_events(id INTEGER PRIMARY KEY,repo_root TEXT,rel_path TEXT,
        ts REAL,agent_id TEXT,model TEXT,query_id TEXT,chat_session_id TEXT,
        digest_before TEXT,digest_after TEXT,commit_sha TEXT);''')
    conn.execute('INSERT INTO edit_events VALUES(1,?,?,?,?,?,?,?,?,?,?)',('/repo','new.py',time.time(),'codex','low','q','1','a','b',None));conn.commit();conn.close()
    assert import_legacy(source,target)['imported']==1
    EventStore(target.parent).reset()
    assert import_legacy(source,target)['imported']==0
    with EventStore(target.parent).connection() as db:
        assert db.execute('SELECT COUNT(*) FROM edit_events').fetchone()[0]==0
