"""SQLite event index and content-addressed payloads; no harness dependencies."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from core.runtime_paths import runtime_state_path

SCHEMA = '''
CREATE TABLE IF NOT EXISTS state_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
 query_id TEXT PRIMARY KEY, started_at REAL NOT NULL, finished_at REAL,
 agent_id TEXT, model TEXT, chat_session_id TEXT, project_root TEXT,
 status TEXT NOT NULL DEFAULT 'running', metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, query_id TEXT NOT NULL,
 seq INTEGER NOT NULL, block_id TEXT NOT NULL, kind TEXT NOT NULL,
 ts REAL NOT NULL, ts_end REAL NOT NULL, rev INTEGER NOT NULL DEFAULT 1,
 summary TEXT, payload TEXT, payload_ref TEXT, compacted TEXT, failed INTEGER NOT NULL DEFAULT 0,
 UNIQUE(query_id, block_id), FOREIGN KEY(query_id) REFERENCES runs(query_id)
);
CREATE TABLE IF NOT EXISTS event_changes (
 id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
 FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS events_run_seq ON events(query_id,seq);
CREATE INDEX IF NOT EXISTS events_time ON events(ts);
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(summary,text);
CREATE TABLE IF NOT EXISTS edit_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, repo_root TEXT NOT NULL, rel_path TEXT NOT NULL,
 ts REAL NOT NULL, agent_id TEXT NOT NULL, model TEXT, query_id TEXT, chat_session_id TEXT,
 digest_before TEXT, digest_after TEXT, commit_sha TEXT, legacy_id INTEGER UNIQUE,
 settlement TEXT NOT NULL DEFAULT 'open', ambiguous INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_edit_open ON edit_events(repo_root,rel_path);
CREATE INDEX IF NOT EXISTS idx_edit_query ON edit_events(query_id);
CREATE TABLE IF NOT EXISTS snapshots (
 query_id TEXT PRIMARY KEY, repo_root TEXT, start_sha TEXT, end_sha TEXT,
 overlap TEXT NOT NULL DEFAULT '[]', error TEXT
);
'''


def state_dir() -> Path:
    override = os.environ.get('CUTTLE_AGENT_EVENTS_DIR')
    if os.environ.get('PYTEST_CURRENT_TEST') and not override:
        raise RuntimeError('Tests require an isolated agent events directory')
    return Path(override) if override else runtime_state_path('agent_events')


class EventStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'agent_events.sqlite3'
        with self.connection() as conn:
            conn.executescript(SCHEMA)
            if "failed" not in {r[1] for r in conn.execute("PRAGMA table_info(events)")}:
                conn.execute("ALTER TABLE events ADD COLUMN failed INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def encode(self, payload):
        raw = json.dumps(payload, ensure_ascii=False, default=str).encode('utf-8')
        if len(raw) <= 8192:
            return raw.decode('utf-8'), None
        digest = hashlib.sha256(raw).hexdigest()
        path = self.root / 'blobs' / digest[:2] / (digest[2:] + '.gz')
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            # Only the writer owns mutations; replace keeps crash readers safe.
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(gzip.compress(raw))
            temporary.replace(path)
        return None, digest

    def decode(self, row):
        if row['payload']:
            return json.loads(row['payload'])
        digest = row['payload_ref']
        if digest and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest):
            path = self.root / 'blobs' / digest[:2] / (digest[2:] + '.gz')
            return json.loads(gzip.decompress(path.read_bytes()))
        return {}

    def write_batch(self, messages):
        with self.connection() as conn:
            for operation, qid, body in messages:
                conn.execute('INSERT OR IGNORE INTO runs(query_id,started_at) VALUES (?,?)', (qid,time.time()))
                if operation == 'run':
                    harness = body.get('harness') or {}
                    context = body.get('user_context') or {}
                    status = 'running' if not body.get('finished_at') else ('ok' if body.get('success') else 'failed')
                    if body.get('cancelled') or '[CANCELLED]' in str(body.get('error_message') or ''):
                        status = 'cancelled'
                    conn.execute('''UPDATE runs SET agent_id=?,model=?,chat_session_id=?,project_root=?,
                        finished_at=?,status=?,metadata=? WHERE query_id=?''', (
                        harness.get('agent_id'), harness.get('model'),
                        str(harness.get('chat_session_id') or context.get('chat_session_id') or context.get('session_id') or ''),
                        harness.get('cwd'),body.get('finished_at'),status,
                        json.dumps(body,ensure_ascii=False,default=str),qid))
                elif operation == 'snapshot':
                    conn.execute("""INSERT INTO snapshots(query_id,repo_root,start_sha,end_sha,overlap,error)
                        VALUES(?,?,?,?,?,?) ON CONFLICT(query_id) DO UPDATE SET
                        end_sha=COALESCE(excluded.end_sha,snapshots.end_sha),overlap=excluded.overlap,error=excluded.error""",
                        (qid,body.get('repo_root'),body.get('start_sha'),body.get('end_sha'),json.dumps(body.get('overlap',[])),body.get('error')))
                elif operation == 'event':
                    block = body['block_id']
                    old = conn.execute('SELECT * FROM events WHERE query_id=? AND block_id=?',(qid,block)).fetchone()
                    merged = self.decode(old) if old else {}
                    merged.update(body)
                    inline, ref = self.encode(merged)
                    seq = old['seq'] if old else conn.execute('SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE query_id=?',(qid,)).fetchone()[0]
                    summary = str(merged.get('summary') or merged.get('text') or merged.get('kind') or '')[:500]
                    conn.execute('''INSERT INTO events(query_id,seq,block_id,kind,ts,ts_end,summary,payload,payload_ref,failed,compacted)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(query_id,block_id) DO UPDATE SET
                        ts_end=excluded.ts_end,rev=events.rev+1,summary=excluded.summary,
                        payload=excluded.payload,payload_ref=excluded.payload_ref,failed=excluded.failed,compacted=excluded.compacted''',
                        (qid,seq,block,merged['kind'],merged.get('t',time.time()),time.time(),summary,inline,ref,int(bool(merged.get("failed") or merged.get("success") is False)),merged.get("compacted")))
                    row = conn.execute('SELECT id FROM events WHERE query_id=? AND block_id=?',(qid,block)).fetchone()
                    conn.execute('INSERT INTO event_changes(event_id) VALUES(?)',(row['id'],))
                    conn.execute('DELETE FROM events_fts WHERE rowid=?',(row['id'],))
                    conn.execute('INSERT INTO events_fts(rowid,summary,text) VALUES(?,?,?)',
                                 (row['id'],summary,str(merged.get('text') or '')+' '+json.dumps(merged.get('args') or {},ensure_ascii=False)))

    def events(self, qid=None, *, after=0, limit=100, agent=None, kind=None, search=None, full=False, descending=False, before=0, model=None, project=None, chat=None, event_ids=None):
        clauses, args = ['e.id>?'], [after]
        if kind=='failure':
            clauses.append('e.failed=1');kind=None
        if event_ids is not None:
            if not event_ids:return []
            clauses.append('e.id IN ('+','.join('?' for _ in event_ids)+')');args.extend(event_ids)
        for expression, value in [('e.query_id',qid),('r.agent_id',agent),('e.kind',kind)]:
            if value:
                clauses.append(expression+'=?'); args.append(value)
        for expression,value in [('r.model',model),('r.project_root',project)]:
            if value:
                clauses.append(expression+' LIKE ?');args.append('%'+value+'%')
        if chat:
            clauses.append('r.chat_session_id=?');args.append(str(int(str(chat).removeprefix('CH-'))))
        if search:
            clauses.append('e.id IN (SELECT rowid FROM events_fts WHERE events_fts MATCH ?)'); args.append(search)
        if before:
            clauses.append('e.id<?');args.append(before)
        with self.connection() as conn:
            rows = conn.execute('''SELECT e.*,r.agent_id,r.model,r.chat_session_id,r.project_root
                FROM events e JOIN runs r USING(query_id) WHERE '''+' AND '.join(clauses)+
                (' ORDER BY e.id DESC LIMIT ?' if descending else ' ORDER BY e.id LIMIT ?'),(*args,min(max(int(limit),1),500))).fetchall()
        result=[]
        for row in rows:
            item=dict(row)
            item.pop('payload'); item.pop('payload_ref')
            if full:
                item.update(self.decode(row))
            result.append(item)
        return result

    def event(self, ident):
        with self.connection() as conn:
            row=conn.execute('SELECT e.*,r.agent_id,r.model,r.chat_session_id,r.project_root FROM events e JOIN runs r USING(query_id) WHERE e.id=?',(ident,)).fetchone()
        if not row:return None
        out=dict(row);out.pop('payload');out.pop('payload_ref')
        return {**out,'detail':self.decode(row)}

    def run_edits(self, qid, limit=5000):
        """Every edit event of one run with its payload merged (for reconciliation)."""
        with self.connection() as conn:
            rows=conn.execute("SELECT * FROM events WHERE query_id=? AND kind='edit' ORDER BY seq LIMIT ?",(qid,limit)).fetchall()
        return [{**self.decode(r),'id':r['id'],'seq':r['seq'],'block_id':r['block_id'],'compacted':r['compacted']} for r in rows]

    def run(self, qid):
        with self.connection() as conn:
            row=conn.execute('SELECT * FROM runs WHERE query_id=?',(qid,)).fetchone()
        return {**dict(row),'metadata':json.loads(row['metadata'])} if row else None

    def stats(self):
        with self.connection() as conn:
            count=conn.execute('SELECT COUNT(*),MIN(ts),MAX(ts) FROM events').fetchone()
            runs=conn.execute('SELECT COUNT(*) FROM runs').fetchone()[0]
        size=sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file())
        status={}
        try:
            status=json.loads((self.root/'capture_status.json').read_text())
        except (OSError,ValueError):
            pass
        return dict(bytes=size,events=count[0],oldest=count[1],newest=count[2],runs=runs,
                    capture_error=status.get('error'),pending_batches=len(list((self.root/'pending').glob('*.json.gz'))))

    def maintain(self, policy, *, now=None, starred=()):
        """Compact completed items, preserving identity and attribution metadata."""
        now=time.time() if now is None else now
        starred=set(str(s) for s in starred)
        changed=0
        with self.connection() as conn:
            rows=conn.execute('''SELECT e.*,r.chat_session_id FROM events e JOIN runs r USING(query_id)
                WHERE r.status!='running' ORDER BY e.ts_end,e.id''').fetchall()
            for row in rows:
                age=(now-row['ts_end'])/86400
                days=policy['starred_retention_days'] if row['chat_session_id'] in starred else policy['retention_days']
                if days==-1:days=policy['retention_days']
                reason='retention' if days and age>days else None
                thinking=row['kind']=='thinking' and (policy['thinking_mode']=='summary_only' or age>policy['thinking_full_days'])
                if (reason or thinking) and not row['compacted']:
                    self._compact(conn,row,reason or 'thinking_expired')
                    changed+=1
            runs=conn.execute("SELECT * FROM runs WHERE status!='running'").fetchall()
            for run in runs:
                days=policy['starred_retention_days'] if run['chat_session_id'] in starred else policy['retention_days']
                if days==-1:days=policy['retention_days']
                if days and run['finished_at'] and now-run['finished_at']>days*86400:
                    self._compact_run(conn,run['query_id'])
            # Physical DB pages are reclaimed by vacuum separately. Use current
            # referenced payload size rather than repeatedly measuring stale blobs.
        self.collect_blobs()
        quota=policy['quota_mb']*1_000_000
        if self.stats()['bytes']>quota:
            self.vacuum()
        while self.stats()['bytes']>quota:
            with self.connection() as conn:
                rows=conn.execute("""SELECT e.* FROM events e JOIN runs r USING(query_id)
                    WHERE r.status!='running' AND (e.compacted IS NULL OR e.compacted='thinking_expired')
                    ORDER BY e.ts_end,e.id LIMIT 100""").fetchall()
                for row in rows:
                    self._compact(conn,row,'quota')
                    self._compact_run(conn,row['query_id'])
                    changed+=1
            if not rows:
                break
            self.collect_blobs()
            self.vacuum()
        return {'compacted':changed,**self.stats()}

    def _compact(self,conn,row,reason):
        payload=self.decode(row)
        summary=str(payload.get('summary') or payload.get('text') or row['summary'] or '')
        summary=' '.join(summary.split())[:300]
        skeleton={k:payload[k] for k in ('kind','block_id','source','path','change','overlap','ambiguous','skipped','repo_root','start_sha','end_sha','phase','failed','files') if k in payload}
        skeleton.update(summary=summary,compacted=reason,summary_source='excerpt')
        conn.execute('UPDATE events SET payload=?,payload_ref=NULL,summary=?,compacted=?,rev=rev+1 WHERE id=?',
                     (json.dumps(skeleton),summary,reason,row['id']))
        conn.execute('INSERT INTO event_changes(event_id) VALUES(?)',(row['id'],))
        conn.execute('DELETE FROM events_fts WHERE rowid=?',(row['id'],))
        conn.execute('INSERT INTO events_fts(rowid,summary,text) VALUES(?,?,?)',(row['id'],summary,''))

    def _compact_run(self,conn,qid):
        row=conn.execute('SELECT metadata FROM runs WHERE query_id=?',(qid,)).fetchone()
        original=json.loads(row['metadata'])
        retained={key:original[key] for key in ('query_id','harness','success','error_message','finished_at','total_tokens','total_cost','total_execution_time') if key in original}
        retained['compacted']=True
        conn.execute('UPDATE runs SET metadata=? WHERE query_id=?',(json.dumps(retained),qid))

    def collect_blobs(self):
        with self.connection() as conn:
            refs={r[0] for r in conn.execute('SELECT DISTINCT payload_ref FROM events WHERE payload_ref IS NOT NULL')}
        for path in (self.root/'blobs').glob('*/*.gz'):
            if path.parent.name+path.stem not in refs:
                path.unlink(missing_ok=True)

    def vacuum(self):
        conn=sqlite3.connect(self.path,timeout=10)
        try:
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            conn.execute('VACUUM')
        finally:
            conn.close()
        return self.stats()

    def reset(self):
        with self.connection() as conn:
            if conn.execute("SELECT 1 FROM runs WHERE status='running' LIMIT 1").fetchone():
                raise ValueError('Wait for active turns to finish before resetting agent events')
            conn.executescript('DELETE FROM events_fts; DELETE FROM event_changes; DELETE FROM events; DELETE FROM edit_events; DELETE FROM snapshots; DELETE FROM runs;')
        self.collect_blobs()
        return self.vacuum()

    def changes(self, after=0, limit=200):
        with self.connection() as conn:
            changes=conn.execute('SELECT id,event_id FROM event_changes WHERE id>? ORDER BY id LIMIT ?', (after,limit)).fetchall()
        ids=list(dict.fromkeys(row['event_id'] for row in changes))
        rows=[]
        if ids:
            with self.connection() as conn:
                rows=conn.execute('SELECT e.id,e.query_id,e.seq,e.kind,e.ts,e.ts_end,e.rev,e.summary,e.compacted,e.failed,r.agent_id,r.model,r.chat_session_id,r.project_root FROM events e JOIN runs r USING(query_id) WHERE e.id IN ('+','.join('?' for _ in ids)+')',ids).fetchall()
        return {'events':[dict(r) for r in rows], 'cursor':changes[-1]['id'] if changes else after}
