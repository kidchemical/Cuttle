"""Process-owned background writer. Harness threads only copy and enqueue."""
from __future__ import annotations
import atexit
import copy
import gzip
import json
from uuid import uuid4
import logging
import os
import time
import queue
import threading
from pathlib import Path
from .store import EventStore, state_dir

log = logging.getLogger(__name__)


class EventWriter:
    def __init__(self, root):
        self.root=Path(root)
        self.queue=queue.Queue()
        self.error=None
        self.next_maintenance=time.monotonic()+300
        self.dropped_status=0
        self.thread=threading.Thread(target=self._work,name='agent-events-writer',daemon=True)
        self.thread.start()

    def submit(self, operation, qid, payload):
        if self.queue.qsize()>10_000 and operation=='event' and payload.get('kind')=='status':
            self.dropped_status+=1
            return
        self.queue.put_nowait((operation,str(qid),copy.deepcopy(payload)))

    def flush(self, timeout=10):
        done=threading.Event()
        self.queue.put(('flush',done,None))
        if not done.wait(timeout):
            raise TimeoutError('Agent events writer did not drain')
        if self.error:
            raise RuntimeError('Agent event persistence failed') from self.error

    def call(self,callback,timeout=60):
        done=threading.Event(); result=[]
        self.queue.put(('call',callback,done,result))
        if not done.wait(timeout):
            raise TimeoutError('Database maintenance is still running')
        if isinstance(result[0],Exception):
            raise result[0]
        return result[0]

    def close(self):
        try:
            self.flush()
        finally:
            self.queue.put(('stop',None,None))
            self.thread.join(10)

    def _work(self):
        store=None
        pending=None
        while True:
            try:
                first=pending or self.queue.get(timeout=30)
                pending=None
            except queue.Empty:
                if not os.environ.get('CUTTLE_AGENT_EVENTS_DIR') and time.monotonic()>=self.next_maintenance:
                    try:
                        from .maintenance import prune
                        store=store or EventStore(self.root)
                        prune(store)
                    except Exception:
                        log.exception('Agent events daily maintenance failed')
                    self.next_maintenance=time.monotonic()+86400
                continue
            if first[0]=='stop':
                return
            if first[0]=='call':
                callback,done,result=first[1:]
                try:
                    store=store or EventStore(self.root)
                    result.append(callback(store))
                except Exception as exc:
                    result.append(exc)
                finally:
                    done.set()
                continue
            if first[0]=='flush':
                if self.error:
                    try:
                        store=store or EventStore(self.root)
                        self._replay(store)
                        self.error=None
                        (self.root/'capture_status.json').unlink(missing_ok=True)
                    except Exception:
                        log.exception('Pending event recovery still unavailable')
                first[1].set(); continue
            batch=[first]
            marker=None
            while len(batch)<200:
                try:
                    item=self.queue.get_nowait()
                except queue.Empty:
                    break
                if item[0] in ('call','stop'):
                    pending=item; break
                if item[0]=='flush':
                    marker=item[1]; break
                batch.append(item)
            try:
                store=store or EventStore(self.root)
                merged={}
                for op,qid,body in batch:
                    key=(op,qid,body.get('block_id'))
                    if key in merged and op=='event':
                        merged[key][2].update(body)
                    else:
                        merged[key]=(op,qid,body)
                from api.storage.service import policy
                selected_policy=policy()
                if selected_policy['thinking_mode']=='summary_only':
                    for op,qid,body in merged.values():
                        if op=='event' and body.get('kind')=='thinking':
                            body['summary']=' '.join(str(body.get('text') or '').split())[:300]
                            body.pop('text',None)
                            body['compacted']='summary_only'
                            body['summary_source']='excerpt'
                self._replay(store)
                store.write_batch(list(merged.values()))
                self.error=None
                (self.root/'capture_status.json').unlink(missing_ok=True)
                if self.dropped_status:
                    store.write_batch([('event',batch[-1][1],{'kind':'capture.gap','block_id':uuid4().hex,
                        'summary':f'{self.dropped_status} status previews dropped under queue pressure'})])
                    self.dropped_status=0
            except Exception as exc:
                self.error=exc
                log.exception('Agent event persistence failed; retaining batch for replay')
                self._spool(batch)
            if marker:
                marker.set()
            if not os.environ.get('CUTTLE_AGENT_EVENTS_DIR') and time.monotonic()>=self.next_maintenance:
                try:
                    from .maintenance import prune
                    prune(store)
                except Exception:
                    log.exception('Agent events daily maintenance failed')
                self.next_maintenance=time.monotonic()+86400

    def _spool(self,batch):
        try:
            folder=self.root/'pending';folder.mkdir(parents=True,exist_ok=True)
            (self.root/'capture_status.json').write_text(json.dumps({'error':str(self.error),'at':time.time()}))
            path=folder/f'{time.time_ns()}-{uuid4().hex}.json.gz'
            temporary=path.with_suffix('.tmp')
            with temporary.open('wb') as handle:
                handle.write(gzip.compress(json.dumps(batch,default=str).encode()))
                handle.flush();os.fsync(handle.fileno())
            temporary.replace(path)
        except Exception:
            log.exception('Agent events recovery spool failed; capture is degraded')

    def _replay(self,store):
        for path in sorted((self.root/'pending').glob('*.json.gz')):
            store.write_batch(json.loads(gzip.decompress(path.read_bytes())))
            path.unlink()


_lock=threading.Lock()
_writers={}


def get_writer():
    root=state_dir().resolve()
    with _lock:
        if root not in _writers:
            _writers[root]=EventWriter(root)
        return _writers[root]


def record(operation,qid,payload):
    if qid:
        get_writer().submit(operation,qid,payload)


def flush_all():
    for writer in list(_writers.values()):
        try:
            writer.flush(2)
        except Exception:
            log.exception('Agent event shutdown flush failed')

atexit.register(flush_all)
