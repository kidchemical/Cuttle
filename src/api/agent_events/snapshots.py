"""Turn worktree snapshots in private Git refs; user's index is untouched."""
from __future__ import annotations
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from core.git_status import parse_status_z

_lock=threading.Lock()
_active={}


def git(root,*args,env=None):
    result=subprocess.run(['git',*args],cwd=root,env=env,capture_output=True,timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8',errors='replace')[:500])
    return result.stdout.decode('utf-8',errors='surrogateescape')


def snapshot(root,qid,phase,limit=2_000_000):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',qid):
        raise ValueError('Invalid snapshot query id')
    root=Path(git(root,'rev-parse','--show-toplevel').strip())
    paths=parse_status_z(git(root,'status','--porcelain=v1','-z','--untracked-files=all'))
    # Rename source must also be removed from the alternate index.
    deleted=git(root,'ls-files','--deleted','-z').split('\0')
    selected=set(paths)|{p for p in deleted if p}
    skipped={}
    # Scratch stays inside this workspace and is excluded explicitly below.
    scratch=root/'temp'/'agent-events'
    scratch.mkdir(parents=True,exist_ok=True)
    fd,index=tempfile.mkstemp(prefix='index-',dir=scratch)
    os.close(fd); Path(index).unlink()
    env={**os.environ,'GIT_INDEX_FILE':index}
    try:
        try:
            head=git(root,'rev-parse','--verify','HEAD').strip()
        except RuntimeError:
            head=None
        git(root,'read-tree',head or '--empty',env=env)
        # A staged rename removes its source from the user's index. Observe
        # deletions against our HEAD-based index too, or the old source survives.
        selected.update(p for p in git(root,'ls-files','--deleted','-z',env=env).split('\0') if p)
        for rel in sorted(selected):
            path=root/rel
            if path==scratch or scratch in path.parents:
                continue
            if path.is_file() and not path.is_symlink():
                if path.stat().st_size>limit:
                    skipped[rel]='size'; continue
                with path.open('rb') as f:
                    if b'\0' in f.read(8192):
                        skipped[rel]='binary'; continue
            git(root,'--literal-pathspecs','add','-A','--',rel,env=env)
        tree=git(root,'write-tree',env=env).strip()
        # Trees can be pinned directly: no synthetic author or commit needed.
        ref=f'refs/cuttle/turns/{qid}/{phase}'
        git(root,'update-ref',ref,tree)
        return {'repo_root':str(root),'tree':tree,'ref':ref,'skipped':skipped}
    finally:
        Path(index).unlink(missing_ok=True)
        Path(index+'.lock').unlink(missing_ok=True)


def begin(cwd,qid):
    baseline=snapshot(cwd,qid,'start')
    root=baseline['repo_root']
    baseline['query_id']=qid
    baseline['overlap']=set()
    with _lock:
        for other in _active.get(root,{}).values():
            baseline['overlap'].add(other['query_id'])
            other['overlap'].add(qid)
        _active.setdefault(root,{})[qid]=baseline
    from .writer import record
    record('snapshot',qid,{'repo_root':root,'start_sha':baseline['tree'],'overlap':sorted(baseline['overlap'])})
    return baseline


def finish(baseline):
    root=baseline['repo_root']; qid=baseline['query_id']
    try:
        end=snapshot(root,qid,'end')
        patch=git(root,'diff','--no-ext-diff','--no-textconv','--binary',baseline['tree'],end['tree'])
        from .writer import record
        record('snapshot',qid,{'repo_root':root,'start_sha':baseline['tree'],'end_sha':end['tree'],'overlap':sorted(baseline['overlap'])})
        files=[]
        for record in git(root,'diff','--no-renames','--numstat','-z',baseline['tree'],end['tree']).split('\0'):
            if not record:continue
            additions,deletions,path=record.split('\t',2)
            files.append({'path':path,'additions':int(additions) if additions.isdigit() else None,
                          'deletions':int(deletions) if deletions.isdigit() else None})
        return {'block_id':'turn-snapshot','summary':'Turn worktree changes',
                'text':patch,'source':'snapshot','repo_root':root,
                'start_sha':baseline['tree'],'end_sha':end['tree'],'files':files,
                'skipped':{**baseline['skipped'],**end['skipped']},
                'overlap':sorted(baseline['overlap']),'ambiguous':bool(baseline['overlap'])}
    finally:
        with _lock:
            _active.get(root,{}).pop(qid,None)


def record_step(qid,tool_id):
    """Best-effort edit-class completion for vendors without native patches."""
    import hashlib
    from .writer import record
    baseline=None
    with _lock:
        for runs in _active.values():
            if qid in runs:
                baseline=runs[qid];break
    if not baseline:
        return
    phase='step-'+hashlib.sha256(str(tool_id).encode()).hexdigest()[:16]
    current=snapshot(baseline['repo_root'],qid,phase)
    previous=baseline.get('step_tree',baseline['tree'])
    baseline['step_tree']=current['tree']
    patch=git(baseline['repo_root'],'diff','--no-ext-diff','--no-textconv',previous,current['tree'])
    if patch:
        record('event',qid,{'kind':'edit','block_id':phase,'summary':'Observed changes after tool completion',
            'text':patch,'source':'snapshot','repo_root':baseline['repo_root'],
            'tool_id':str(tool_id),'overlap':sorted(baseline['overlap']),
            'ambiguous':bool(baseline['overlap']),'skipped':current['skipped']})
