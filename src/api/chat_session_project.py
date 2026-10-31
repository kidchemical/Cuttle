"""Persisted chat project + bound worktree checkout stamping (owner).

Writes a chat's session-row project fields from an explicit client chip. Kept
out of the Flask composition root: the ingress passes the resolved ``db`` so
the decision is testable without Flask or the request context.
"""

from __future__ import annotations

from typing import Callable


def _default_checkout_belongs(checkout_uid: str, project_id: int) -> bool:
    from api.worktrees.binding import checkout_belongs_to_project

    return checkout_belongs_to_project(checkout_uid, project_id)


def stamp_auth_session_project(
    user,
    session_id,
    data,
    *,
    db,
    checkout_belongs: Callable[[str, int], bool] = _default_checkout_belongs,
) -> None:
    """Write project/checkout onto an auth chat from an explicit client chip.

    Fills an empty session on first send; updates on a different project or
    checkout (``/project`` or a worktree rebind). Fields the client omits keep
    their stored value. Never invents Cuttle when the body omitted project fields.
    A checkout belongs to one project: one registered to another project (sent
    by the client or left over from before a project switch) is stored as None.
    """
    if not user or session_id is None:
        return
    sid = int(session_id)
    stored = db.get_session_project(sid) or {}
    data = data or {}
    pid = data.get('project_id')
    pname = data.get('project_name') or data.get('projectName')
    ppath = data.get('project_path') or data.get('projectPath')
    cuid = str(data.get('checkout_uid') or data.get('checkoutUid') or '').strip() or None
    if pid is None and not (pname or ppath) and not cuid:
        return  # only stamp what the client actually sent

    def _norm(p):
        return str(p or '').strip().replace('\\', '/').rstrip('/').lower()

    def _int(v):
        try:
            return int(v) if v not in (None, '') else None
        except (TypeError, ValueError):
            return None

    cur = (
        stored.get('project_id'), stored.get('project_name'),
        stored.get('project_path'), stored.get('checkout_uid'),
    )
    nxt = (
        _int(pid) if pid is not None else _int(cur[0]),
        (str(pname).strip() or cur[1]) if pname is not None else cur[1],
        (str(ppath).strip() or cur[2]) if ppath is not None else cur[2],
        cuid if cuid is not None else cur[3],
    )
    if nxt[3] and nxt[0] is not None and not checkout_belongs(nxt[3], nxt[0]):
        nxt = (nxt[0], nxt[1], nxt[2], None)
    unchanged = (
        nxt[0] == _int(cur[0])
        and (nxt[1] or '') == (cur[1] or '')
        and _norm(nxt[2]) == _norm(cur[2])
        and (nxt[3] or None) == (cur[3] or None)
    )
    if unchanged:
        return
    db.set_session_project(
        sid, user['id'],
        project_id=nxt[0], project_name=nxt[1],
        project_path=nxt[2], checkout_uid=nxt[3] or None,
    )
