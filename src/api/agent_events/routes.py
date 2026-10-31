"""Owner-only fleet log transport. Persistence and retention stay in services."""
import json
import sqlite3
import time
import zlib
from pathlib import Path
from flask import Blueprint,Response,jsonify,request,send_from_directory,stream_with_context
from api.http_authz import owner_required
from .store import EventStore,state_dir

bp=Blueprint('agent_events',__name__)


def store():
    return EventStore(state_dir())


@bp.get('/agent_feed.html')
@owner_required
def page():
    from api.experimental import is_enabled
    if not is_enabled('agent_feed'):
        return jsonify(error='Enable Agent Feed in Experimental settings'),403
    return send_from_directory(Path(__file__).resolve().parents[2]/'web','agent_feed.html')


@bp.get('/api/agent-events')
@owner_required
def events():
    try:
        db=store()
        # Read the tail cursor BEFORE history. Events committed during the history
        # read may be delivered twice, and are deduplicated by identity/revision.
        with db.connection() as conn:
            stream_cursor=conn.execute('SELECT COALESCE(MAX(id),0) FROM event_changes').fetchone()[0]
        rows=db.events(request.args.get('query_id'),after=int(request.args.get('after',0)),
                            limit=int(request.args.get('limit',100)),agent=request.args.get('agent'),
                            kind=request.args.get('kind'),search=request.args.get('search'),
                            descending=request.args.get('direction')=='desc',before=int(request.args.get('before',0)),
                            model=request.args.get('model'),project=request.args.get('project'),chat=request.args.get('chat'))
        if request.args.get('search'):
            for row in rows:row['search_match']=True
        return jsonify(events=rows,next_cursor=rows[-1]['id'] if rows else None,stream_cursor=stream_cursor)
    except (ValueError, sqlite3.OperationalError) as exc:
        return jsonify(error=str(exc)),400


@bp.get('/api/agent-events/active')
@owner_required
def active():
    from .writer import get_writer
    return jsonify(runs=get_writer().active_runs())


@bp.get('/api/agent-events/events/<int:ident>')
@owner_required
def event(ident):
    row=store().event(ident)
    return (jsonify(row),200) if row else (jsonify(error='Event not found'),404)


@bp.get('/api/agent-events/runs/<qid>')
@owner_required
def run(qid):
    found=store().run(qid)
    return (jsonify(found),200) if found else (jsonify(error='Run not found'),404)


@bp.get('/api/agent-events/runs/<qid>/changes')
@owner_required
def run_changes(qid):
    """Run-wide Changes: every file the turn changed, reconciled with reports."""
    from .reconcile import reconcile
    db=store()
    found=db.run(qid)
    if not found:
        return jsonify(error='Run not found'),404
    edits=db.run_edits(qid)
    model=reconcile(edits,found.get('agent_id'))
    model.update(query_id=qid,status=found['status'],compacted=any(e.get('compacted') for e in edits))
    return jsonify(model)


@bp.get('/api/agent-events/stream')
@owner_required
def stream():
    from api.experimental import is_enabled
    if not is_enabled('agent_feed'):
        return jsonify(error='Enable Agent Feed in Experimental settings'),403
    try:
        cursor=int(request.headers.get('Last-Event-ID') or request.args.get('after',0))
    except ValueError:
        return jsonify(error='Invalid cursor'),400
    filters={key:request.args.get(key) for key in ('agent','kind','model','project','chat','search')}
    @stream_with_context
    def generate():
        nonlocal cursor
        db=store()
        while is_enabled('agent_feed'):
            payload=db.changes(cursor)
            cursor=payload['cursor']
            if payload['events'] and any(filters.values()):
                payload['events']=db.events(event_ids=[r['id'] for r in payload['events']],limit=200,**filters)
                for row in payload['events']:row['search_match']=bool(filters.get('search'))
            if payload['events']:
                # Stream only row headers; full detail is loaded on expansion.
                for event in payload['events']:
                    event.pop('payload',None);event.pop('payload_ref',None);event.pop('detail',None)
                yield f'id: {cursor}\ndata: {json.dumps(payload)}\n\n'
            else:
                yield ': keepalive\n\n'
            time.sleep(1)
    return Response(generate(),mimetype='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})


def _meta_rev(raw):
    return format(zlib.crc32(raw.encode('utf-8')),'08x')


def _log_row(row):
    return {**row,'t':row['ts'],'detail_id':row['id']}


@bp.get('/api/agent-events/runs/<qid>/log')
@owner_required
def run_log(qid):
    """One page of a run's event headers. Run metadata (Brain, sent prompt,
    tool tallies) rides along only when the caller's ``meta_rev`` is stale."""
    db=store()
    try:
        after=int(request.args.get('after',0))
        seq=int(request.args.get('seq',0))
    except ValueError as exc:
        return jsonify(error=str(exc)),400
    # Cursor before rows: changes committed in between replay on the stream.
    stream_cursor=db.change_cursor()
    state=db.run_state(qid)
    if not state:
        return jsonify(error='Run not found'),404
    status,raw=state
    if seq:
        with db.connection() as conn:
            hit=conn.execute('SELECT id FROM events WHERE query_id=? AND seq>=? ORDER BY seq LIMIT 1',(qid,seq)).fetchone()
        if hit:
            after=hit['id']-1
    rows=db.events(qid,after=after,limit=101)
    more=len(rows)>100
    rows=rows[:100]
    rev=_meta_rev(raw)
    if request.args.get('meta_rev')==rev:
        data={'meta_omitted':True}
    else:
        data=json.loads(raw)
    data.update(query_id=qid,executing=status=='running',events=[_log_row(row) for row in rows],
                event_store=True,has_more=more,next_cursor=rows[-1]['id'] if rows else after,
                stream_cursor=stream_cursor,meta_rev=rev)
    return jsonify(data)


LOG_STREAM_POLL_SEC=1.0
LOG_STREAM_KEEPALIVE_SEC=15.0
LOG_STREAM_MAX_SEC=600.0


@bp.get('/api/agent-events/runs/<qid>/log/stream')
@owner_required
def run_log_stream(qid):
    """Live tail of one run for the query log inspector (SSE).

    Each message carries only that run's revised event headers since the
    cursor, plus ``meta`` when the run metadata changed. Silent while the run
    is idle; ends with an ``end`` event once the run is no longer running.
    The connection is recycled after LOG_STREAM_MAX_SEC (the browser resumes
    from Last-Event-ID)."""
    db=store()
    try:
        cursor=int(request.headers.get('Last-Event-ID') or request.args.get('after',0))
    except ValueError:
        return jsonify(error='Invalid cursor'),400
    if not db.run_state(qid):
        return jsonify(error='Run not found'),404
    client_rev=request.args.get('meta_rev','')

    @stream_with_context
    def generate():
        nonlocal cursor
        meta_rev=client_rev
        executing=None
        started=last_sent=time.monotonic()
        while time.monotonic()-started<LOG_STREAM_MAX_SEC:
            state=db.run_state(qid)
            if not state:
                yield 'event: end\ndata: {}\n\n'
                return
            # Status before changes, so the final scan sees every event the
            # run committed before it finished.
            live=state[0]=='running'
            changes=db.run_changes(qid,cursor)
            cursor=changes['cursor']
            rev=_meta_rev(state[1])
            msg={}
            if changes['events']:
                msg['events']=[_log_row(row) for row in changes['events']]
            if rev!=meta_rev:
                msg['meta']=json.loads(state[1])
                meta_rev=rev
            if msg or live!=executing:
                executing=live
                msg.update(executing=live,meta_rev=rev,cursor=cursor)
                yield f'id: {cursor}\ndata: {json.dumps(msg)}\n\n'
                last_sent=time.monotonic()
            if not live:
                yield 'event: end\ndata: {}\n\n'
                return
            if time.monotonic()-last_sent>=LOG_STREAM_KEEPALIVE_SEC:
                yield ': keepalive\n\n'
                last_sent=time.monotonic()
            time.sleep(LOG_STREAM_POLL_SEC)
    return Response(generate(),mimetype='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})
