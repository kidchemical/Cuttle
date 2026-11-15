"""Spawn / wait / cancel / message for Cuttle sub-agent child chats."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

import api.subagents.extras as extras
import api.subagents.store as store
from api.subagents.profiles import apply_profile
from api.subagents.spec import apply_router, parse_children, public_launcher
from api.subagents.turns import TurnRunner, run_child_turn, run_child_turn_async
from api.subagents.types import (
    COLLECT_ALL,
    COLLECT_FIRST,
    COLLECT_SERIAL,
    DEFAULT_POLL_SEC,
    DEFAULT_TIMEOUT_SEC,
    LIFETIME_CONVERSATIONAL,
    MAX_CHILDREN,
    MAX_DEPTH,
    STATUS_CANCELLED,
    STATUS_CLOSED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    TERMINAL_BATCH,
    TERMINAL_CHILD,
    BatchRecord,
    ChildRecord,
    ChildSpec,
    chat_handle,
    normalize_collect,
    normalize_lifetime,
)


class SubagentError(ValueError):
    pass


def _open_db(db=None):
    if db is not None:
        return db
    from api.auth_db import get_auth_db

    return get_auth_db()


def _bare_sid(session_id: Any) -> Optional[int]:
    try:
        from api.cuttle_ui_capabilities import numeric_chat_session_id

        return numeric_chat_session_id(session_id)
    except Exception:
        try:
            return int(str(session_id).replace("db_session_", "").split("-")[0])
        except (TypeError, ValueError):
            return None


def _parent_row(db, parent_session_id: int) -> Dict[str, Any]:
    row = db.get_chat_session_by_id(int(parent_session_id))
    if not row:
        raise SubagentError(f"parent session {parent_session_id} not found")
    return row


def _execution_path_for_parent(parent: Dict[str, Any]) -> str:
    """cwd for child turns: the parent's bound worktree checkout, else its path.

    The child's stored ``project_path`` stays the project identity; only the
    execution cwd follows the parent's checkout.
    """
    project_path = str(parent.get("project_path") or "")
    checkout_uid = str(parent.get("checkout_uid") or "")
    if not checkout_uid:
        return project_path
    try:
        from api.worktrees import binding
        pid = parent.get("project_id")
        try:
            pid_int = int(pid) if pid not in (None, "") else None
        except (TypeError, ValueError):
            pid_int = None
        return binding.resolve_bound_checkout_path(checkout_uid, pid_int) or project_path
    except Exception:
        return project_path


def _refresh(db, batch_id: str) -> BatchRecord:
    batch = store.get_batch(db, batch_id)
    if not batch:
        raise SubagentError(f"batch {batch_id} not found")
    return batch


def _maybe_extras(db, batch: BatchRecord) -> None:
    extras.write_watch(batch)
    extras.patch_parent_tasks(db, batch)


def _reconcile(db, batch: Optional[BatchRecord] = None) -> None:
    """Flip children whose owning process died to terminal, then re-read.

    A child turn lives inside the process that spawned it. When that host dies
    the row is left saying ``running`` with no agent behind it, so the pane
    spins forever and the batch never finalizes. Every observation point calls
    this; ``batch`` short-circuits when nothing is outstanding.
    """
    if batch is not None and all(c.status in TERMINAL_CHILD for c in batch.children):
        return
    try:
        orphans = store.reconcile_orphan_children(db)
    except Exception:
        return
    try:
        close_stale_external_rounds(db)
    except Exception:
        pass
    # A batch whose last child just died must close too, or it reads as running.
    for batch_id in {c.batch_id for c in map(lambda cid: store.get_child(db, cid), orphans) if c}:
        try:
            _finalize_if_complete(db, _refresh(db, batch_id))
        except Exception:
            pass


def find_turn_row(db, parent_session_id: int, turn_id: str) -> Optional[Dict[str, Any]]:
    """The parent's assistant row of turn ``turn_id`` (its live row first, else the newest)."""
    if not turn_id:
        return None
    try:
        conn = db._get_connection()
    except Exception:
        return None
    try:
        rows = conn.execute(
            "SELECT id, content, metadata FROM chat_messages WHERE chat_session_id = ? AND role = 'assistant' "
            "AND json_valid(metadata) AND json_extract(metadata, '$.turn_id') = ? ORDER BY id DESC",
            (int(parent_session_id), str(turn_id))).fetchall()
    except Exception:
        return None
    finally:
        conn.close()
    found = [dict(r) for r in rows]
    for row in found:
        try:
            row["metadata"] = json.loads(row["metadata"]) if row.get("metadata") else {}
        except ValueError:
            row["metadata"] = {}
    live = [r for r in found if r["metadata"].get("in_progress")]
    return (live or found or [None])[0]


def _write_cards(db, message_id: int, children: List[ChildRecord], *, batch_id: str = "") -> None:
    """Merge these children's cards into a saved row's metadata (once each)."""
    from api.subagents import fleet

    row = db.get_message_by_id(int(message_id)) or {}
    meta = row.get("metadata")
    meta = json.loads(meta) if isinstance(meta, str) and meta else dict(meta or {})
    if meta.get("in_progress"):
        return  # the reply will carry them when it lands (attach_batches_to_assistant_meta)
    cards = [c for c in (meta.get("subagents") or []) if isinstance(c, dict)]
    live_ids = {str(c.get("id") or "") for c in cards if not c.get("frozen")}
    card = fleet.fleet_entry if fleet.fleet_enabled() else (lambda ch: public_launcher(ch.public()))
    added = False
    for child in children:
        if child.id not in live_ids:
            cards.append(card(child))
            added = True
    if not added:
        return
    meta["subagents"] = cards
    if batch_id:
        meta.setdefault("subagent_batch_id", batch_id)
    db.update_message_content(int(message_id), str(row.get("content") or ""), metadata=meta)


def _latest_parent_bubble(db, parent_session_id: int) -> Optional[int]:
    try:
        recent = db.get_messages(int(parent_session_id), limit=40, include_in_progress=True)
    except Exception:
        return None
    # A missing environment is not evidence that this is an idle manual run.
    # systemd-run drops the caller's environment: never hide its cards on an
    # older reply while a newer user request / in-progress reply is outstanding.
    conversation = [m for m in recent or [] if m.get("role") in ("user", "assistant")]
    last = conversation[-1] if conversation else None
    if last and (last.get("role") != "assistant" or (last.get("metadata") or {}).get("in_progress")):
        return None
    return int(last["id"]) if last else None


def _card_target(db, parent_session_id: int, *, turn_id: str = "",
                 anchor_message_id: Optional[int] = None) -> Optional[int]:
    """The parent bubble new cards belong on, harness-agnostic:

    the explicit anchor; else the in-progress bubble of the turn that launched
    them (it exists from turn start) — when that turn has no bubble in
    progress (its reply was saved before the spawn), None: the first reply of
    that turn saved after the spawn binds them (``bind_turn_cards``), never a
    reply saved before it; else — a manual run with no turn — the parent's
    latest bubble."""
    if anchor_message_id:
        return int(anchor_message_id)
    if turn_id:
        row = find_turn_row(db, int(parent_session_id), turn_id)
        return int(row["id"]) if row and row["metadata"].get("in_progress") else None
    return _latest_parent_bubble(db, int(parent_session_id))


def repair_batch_cards(batch_id: str, turn_id: str, *, db=None) -> Dict[str, Any]:
    """Explicitly recover a detached launch's lost parent identity, without rerunning work.

    The caller must supply the evidenced originating turn. Never infer it from
    the currently running turn, which may have started after this batch.
    """
    db = _open_db(db)
    batch = store.get_batch(db, batch_id)
    if not batch:
        raise SubagentError("batch not found")
    row = find_turn_row(db, batch.parent_session_id, turn_id)
    if not row:
        raise SubagentError("turn has no assistant row in this parent chat")
    if batch.turn_id and batch.turn_id != turn_id:
        raise SubagentError("batch already belongs to a different turn")
    if any(c.card_message_id or c.card_pending or c.card_turn_id for c in batch.children):
        raise SubagentError("batch has follow-up card rounds; repair those separately")
    target = int(row["id"])
    # Write the new location before removing the old snapshot.
    _write_cards(db, target, batch.children, batch_id=batch.id)
    old_id = batch.attach_message_id
    store.update_batch(db, batch.id, attach_message_id=target, turn_id=turn_id)
    if old_id and int(old_id) != target:
        old = db.get_message_by_id(int(old_id)) or {}
        meta = old.get("metadata") or {}
        meta = json.loads(meta) if isinstance(meta, str) else dict(meta)
        ids = {c.id for c in batch.children}
        meta["subagents"] = [c for c in meta.get("subagents", []) if c.get("id") not in ids]
        if meta.get("subagent_batch_id") == batch.id:
            meta.pop("subagent_batch_id", None)
        db.update_message_content(int(old_id), old.get("content") or "", metadata=meta)
    return {"ok": True, "batch": batch.id, "turn_id": turn_id, "message_id": target}


def bind_new_batch(db, batch: BatchRecord, *, anchor_message_id: Optional[int] = None) -> Optional[int]:
    """At spawn: bind to the bubble of the turn that launched it (``_card_target``).

    The turn's row exists from turn start (api.chat_turn_row), so this is
    normally immediate and the cards ride that live bubble. With no row for
    the turn yet, the batch stays unbound (pinned background card); the first assistant
    row saved with that turn id binds it (``bind_turn_cards``)."""
    if batch.attach_message_id:
        return None
    mid = _card_target(db, int(batch.parent_session_id), turn_id=batch.turn_id,
                       anchor_message_id=anchor_message_id)
    if not mid:
        return None
    store.update_batch(db, batch.id, attach_message_id=mid)
    batch.attach_message_id = mid
    try:
        _write_cards(db, mid, _refresh(db, batch.id).children, batch_id=batch.id)
    except Exception as exc:
        print(f"[SUBAGENTS] spawn card binding to row {mid} failed: {exc}", flush=True)
    return mid


def bind_turn_cards(db, parent_session_id: int, message_id: int, turn_id: str) -> List[str]:
    """An assistant row of turn ``turn_id`` was saved: bind that turn's waiting cards to it.

    Called by ``AuthDatabase`` on every assistant write, whichever path saved
    it. Returns the bound batch / child ids."""
    if not turn_id:
        return []
    bound: List[str] = []
    for batch in store.list_batches_for_parent(db, int(parent_session_id)):
        if batch.attach_message_id == int(message_id):
            # Bound already (at spawn, to this turn's row): whichever path just
            # rewrote the row, its cards stay on it — except a child whose
            # current round's card lives on a later bubble (its card here is frozen).
            mine = [c for c in batch.children if not c.card_pending
                    and (not c.card_message_id or int(c.card_message_id) == int(message_id))]
            _write_cards(db, int(message_id), mine, batch_id=batch.id)
            continue
        if batch.attach_message_id or batch.turn_id != turn_id:
            continue
        store.update_batch(db, batch.id, attach_message_id=int(message_id))
        _write_cards(db, int(message_id), batch.children, batch_id=batch.id)
        bound.append(batch.id)
    rounds = [c for c in store.pending_card_children(db, int(parent_session_id)) if c.card_turn_id == turn_id]
    for child in rounds:
        store.set_card_round(db, child.id, message_id=int(message_id), pending=False)
    if rounds:
        _write_cards(db, int(message_id), [store.get_child(db, c.id) or c for c in rounds])
        bound += [c.id for c in rounds]
    return bound


def leave_cards_on_last_bubble(db, batch: BatchRecord) -> Optional[int]:
    """A batch that ended with no parent reply to carry it stays on the last one.

    The parent never saved another reply to carry it (a manual run with no
    turn or anchor, or its turn died before a row existed): the
    live cards would vanish with the batch. Bind it to the parent's newest
    saved assistant bubble and write the cards there, so history keeps them."""
    if batch.attach_message_id:
        return None
    if batch.turn_id:
        # Only that turn's saver may bind these; completion is not permission
        # to move them to another turn's reply.
        return None
    mid = _latest_parent_bubble(db, int(batch.parent_session_id))
    if not mid:
        return None
    store.update_batch(db, batch.id, attach_message_id=mid)
    batch.attach_message_id = mid
    try:
        _write_cards(db, mid, _refresh(db, batch.id).children, batch_id=batch.id)
    except Exception as exc:
        print(f"[SUBAGENTS] leaving cards on CH bubble {mid} failed: {exc}", flush=True)
    return mid


def _finalize_if_complete(db, batch: BatchRecord) -> BatchRecord:
    batch = _refresh(db, batch.id)
    if batch.status in TERMINAL_BATCH:
        return batch
    kids = batch.children
    if not kids:
        return batch
    if batch.collect == COLLECT_FIRST:
        winner = next((c for c in kids if c.status == STATUS_DONE), None)
        if winner:
            for c in kids:
                if c.id != winner.id and c.status not in TERMINAL_CHILD:
                    _cancel_child(db, c.session_id)
            store.update_batch(db, batch.id, status=STATUS_DONE, finished=True)
            return _ended(db, batch.id)
        if all(c.status in TERMINAL_CHILD for c in kids):
            failed = all(c.status != STATUS_DONE for c in kids)
            store.update_batch(
                db,
                batch.id,
                status=STATUS_FAILED if failed else STATUS_DONE,
                finished=True,
            )
            return _ended(db, batch.id)
        return batch
    if all(c.status in TERMINAL_CHILD for c in kids):
        failed = any(c.status == STATUS_FAILED for c in kids) and not any(
            c.status == STATUS_DONE for c in kids
        )
        cancelled = all(c.status == STATUS_CANCELLED for c in kids)
        status = STATUS_CANCELLED if cancelled else (STATUS_FAILED if failed else STATUS_DONE)
        if batch.lifetime == LIFETIME_CONVERSATIONAL and status == STATUS_DONE:
            # One round finished; keep the batch open for follow-ups. Cards no
            # running turn carries stay on the parent's last bubble.
            try:
                leave_cards_on_last_bubble(db, batch)
            except Exception as exc:
                print(f"[SUBAGENTS] round-end card binding failed: {exc}", flush=True)
            return batch
        store.update_batch(db, batch.id, status=status, finished=True)
        return _ended(db, batch.id)
    return batch


def _ended(db, batch_id: str) -> BatchRecord:
    batch = _refresh(db, batch_id)
    try:
        leave_cards_on_last_bubble(db, batch)
    except Exception as exc:
        print(f"[SUBAGENTS] end-of-batch card binding failed: {exc}", flush=True)
    return batch


def _cancel_child(db, session_id: int) -> None:
    child = store.get_child_by_session(db, int(session_id))
    if child and child.status not in TERMINAL_CHILD:
        store.update_child(
            db, child.id, status=STATUS_CANCELLED, finished=True
        )
    if child:
        # The CLI belongs to the process running the turn (often the spawn
        # CLI, not this one); its stop watch kills it on this request.
        try:
            store.request_stop(db, child.id)
        except Exception:
            pass
    try:
        from api.chat_run_registry import cancel_session_runs

        cancel_session_runs(session_id)
    except Exception:
        pass
    if child and child.pid:
        _kill_pid(int(child.pid))


def _kill_pid(pid: int) -> None:
    if pid <= 0:
        return
    try:
        import os
        import signal

        os.kill(pid, signal.SIGTERM)
    except Exception:
        try:
            import subprocess

            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=8,
                check=False,
            )
        except Exception:
            pass


def _preflight_pins(specs: List[ChildSpec]) -> None:
    """Reject a child whose harness/model/effort cannot start, before any row exists.

    A bad pin used to create a child chat that failed with "was not started";
    agents then resent the identical spawn. Routed children pick their own pin.
    """
    from api.agent_harness.agent_models import check_agent_pin
    from api.agent_harness.catalog import get_agent

    catalogs: Dict[str, Any] = {}
    for index, spec in enumerate(specs, start=1):
        if spec.route:
            continue
        pair = get_agent(spec.agent)
        # Harnesses without an effort flag (Cursor) bake effort into the model
        # id at launch (identity.resolve_child_model_id); check the base pin.
        effort = spec.effort if pair is None or pair[0].supports_effort else ""
        error = check_agent_pin(spec.agent, spec.model, effort, catalogs=catalogs)
        if error:
            raise SubagentError(
                f"child {index} ({spec.title}): {error} "
                f"(agent={spec.agent!r} model={spec.model!r} effort={spec.effort!r}). "
                "No chat was created; fix the spec or choose another profile."
            )


def _resolve_tiers(specs: List[ChildSpec], parent_session_id: int, db) -> List[Dict[str, Any]]:
    """Turn ``"tier"`` children into the tier member that can run (budget-aware).

    Mutates ``specs`` in place; returns one budget note per tier child for the
    spawn payload. Errors before any chat exists."""
    from dataclasses import replace

    from api.orchestrator.service import OrchestratorError, pick_tier_member

    notes: List[Dict[str, Any]] = []
    accounts = None
    for i, spec in enumerate(specs):
        if not spec.tier:
            continue
        if accounts is None:
            from api.agent_router.budget import cached_snapshot

            accounts = cached_snapshot()
        try:
            pick = pick_tier_member(parent_session_id, spec.tier, db=db, accounts=accounts)
        except OrchestratorError as exc:
            raise SubagentError(f"child {i + 1} ({spec.title or spec.tier}): {exc}") from None
        prof = pick["profile"]
        if isinstance(prof, str):
            specs[i] = replace(spec, profile_id=prof)
        else:  # temporary agent: the inline pin, as the brief would have sent it
            specs[i] = replace(
                spec, agent=prof.get("agent") or spec.agent, model=prof.get("model") or "",
                effort=prof.get("effort") or "", agent_explicit=True, model_explicit=True,
                effort_explicit=True, ephemeral_profile=True,
                display_name=spec.display_name or str(prof.get("name") or ""),
                title=spec.title or str(prof.get("name") or ""))
        notes.append({"child": i + 1, "tier": pick["tier"], "picked": pick["name"], "skipped": pick["skipped"],
                      "_pinned": bool(pick.get("pinned"))})
    return notes


def _preflight_budget(specs: List[ChildSpec], parent_session_id: int, db, *, tier_checked: set) -> None:
    """Refuse a pinned child whose account is out of usage, before any row exists.

    Uses what Cuttle already knows (recent out-of-usage failures, and the cached
    plan-usage snapshot) — no vendor call here. Tier children were already picked
    budget-aware. Inside an orchestrator chat the error names the tier's runnable
    agent, so the next spawn goes there instead of failing on the same account."""
    from api.agent_router.availability import target_state
    from api.agent_router.budget import snapshot
    from api.agent_router.types import ExecutionTarget

    accounts = None
    for index, spec in enumerate(specs, start=1):
        if spec.route or index - 1 in tier_checked:
            continue
        if accounts is None:
            accounts = snapshot()
        state, why = target_state(ExecutionTarget(spec.agent, spec.model), accounts=accounts, verbose=True)
        if state != "blocked":
            continue
        from api.orchestrator.service import runnable_alternative

        alt = runnable_alternative(parent_session_id, profile_id=spec.profile_id, agent=spec.agent,
                                   model=spec.model, effort=spec.effort, db=db, accounts=accounts)
        hint = (f'the {alt["tier_label"]} tier can run {alt["name"]} — spawn with "tier": "{alt["tier"]}".'
                if alt else "choose another agent or wait for the reset.")
        raise SubagentError(
            f"child {index} ({spec.title}): {spec.agent}{' ' + spec.model if spec.model else ''} "
            f"is out of budget ({why}). No chat was created; {hint}")


def spawn(
    *,
    parent_session_id: int,
    children: List[Any],
    collect: str = COLLECT_ALL,
    lifetime: str = "one_shot",
    wait: bool = True,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    route: bool = False,
    watch: bool = False,
    tasks: bool = False,
    detached_pid: int = 0,
    runner: Optional[TurnRunner] = None,
    db=None,
    turn_id: Optional[str] = None,
    anchor_message_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Start a batch of child chats.

    ``turn_id`` is the parent turn spawning it (default: the calling turn,
    ``CUTTLE_TURN_ID``; api.chat_turn_id) — its cards bind to that turn's row.
    ``anchor_message_id`` pins them to one parent bubble instead (manual CLI
    runs with no turn: ``--anchor CH-…-N``)."""
    db = _open_db(db)
    parent = _parent_row(db, int(parent_session_id))
    try:
        from api.orchestrator.control import held as _held
        hold = _held(int(parent_session_id))
    except Exception:
        hold = ""
    if hold:
        raise SubagentError(
            f"this orchestration is {hold} by the user: no sub-agents are started until they resume it. "
            "Do not retry; report and wait."
        )
    from api.chat_turn_id import for_spawn, origin

    calling = origin(int(parent_session_id))
    turn_id = "" if anchor_message_id else for_spawn(int(parent_session_id), turn_id)
    turn_from = calling if turn_id and turn_id == calling.get("turn_id") else {}
    user_id = int(parent["user_id"])
    depth = db.session_nesting_depth(int(parent_session_id))
    if depth >= MAX_DEPTH:
        raise SubagentError(
            f"sub-agent nesting is capped at {MAX_DEPTH} (this chat is already depth {depth})"
        )
    specs = parse_children(children)
    if not specs:
        raise SubagentError("spawn requires at least one child")
    if len(specs) > MAX_CHILDREN:
        raise SubagentError(f"at most {MAX_CHILDREN} sub-agents per batch")
    budget_notes = _resolve_tiers(specs, int(parent_session_id), db)
    specs = [apply_profile(s, db=db, user_id=user_id) for s in specs]
    from dataclasses import replace as _replace

    for note in budget_notes:
        # The tier pick chose this harness for its budget: per-CLI child defaults must not swap it.
        if note.pop("_pinned"):
            i = note["child"] - 1
            specs[i] = _replace(specs[i], agent_explicit=True, model_explicit=True, effort_explicit=True)
    # Apply per-CLI child defaults only to fields the child did not choose.
    # "native" means no Cuttle-imposed subagent preference.
    try:
        import re
        from api.auth_db import get_auth_db
        from api.agent_harness.catalog import list_agents
        from managers.settings_manager import get_settings_manager
        known = {str(a).lower() for a in list_agents()}
        recent = get_auth_db().get_messages(int(parent_session_id), limit=40)
        parent_agent = ''
        for msg in reversed(recent if isinstance(recent, list) else []):
            if msg.get('role') != 'user': continue
            match = re.match(r'^/([a-z0-9_-]+)(?:\s|$)', str(msg.get('content') or '').strip(), re.I)
            if match and match.group(1).lower() in known:
                parent_agent = match.group(1).lower(); break
        prefs = get_settings_manager().get_setting('agent_subagent_defaults') or {}
        pref = prefs.get(parent_agent, {}) if parent_agent and isinstance(prefs, dict) else {}
        if pref and str(pref.get('agent') or 'native').lower() != 'native':
            for spec in specs:
                if not spec.agent_explicit:
                    spec.agent = str(pref['agent'])
                    if not spec.model_explicit: spec.model = str(pref.get('model') or '')
                    if not spec.effort_explicit: spec.effort = str(pref.get('effort') or '')
    except Exception:
        pass
    _preflight_pins(specs)
    _preflight_budget(specs, int(parent_session_id), db,
                      tier_checked={n["child"] - 1 for n in budget_notes})
    collect_n = normalize_collect(collect)
    lifetime_n = normalize_lifetime(lifetime)
    project_path = str(parent.get("project_path") or "")
    project_id = parent.get("project_id")
    project_name = parent.get("project_name") or ""
    checkout_uid = str(parent.get("checkout_uid") or "")
    run_path = _execution_path_for_parent(parent)

    watch_id = ""
    widget_id = ""
    batch_stub = store.new_id()
    if watch:
        watch_id = f"subagents-{batch_stub[:12]}"
    if tasks:
        widget_id = f"subagents-{batch_stub[:12]}"

    batch = store.insert_batch(
        db,
        parent_session_id=int(parent_session_id),
        user_id=user_id,
        collect=collect_n,
        lifetime=lifetime_n,
        watch_id=watch_id,
        widget_id=widget_id,
        metadata={"route": bool(route), "cards": store.CARDS_BY_TURN,
                  # The spawn CLI's pid when a service manager runs it (no listener):
                  # chat_ops reports the parent as waiting on it while it lives.
                  **({"detached_cli_pid": int(detached_pid)} if detached_pid else {}),
                  # Which parent message started the turn that launched this batch.
                  "turn_message_id": turn_from.get("message_id"),
                  "turn_message": turn_from.get("message") or None},
        turn_id=turn_id,
    )
    # Re-key watch/widget to the real batch id when we generated from a stub.
    if watch_id:
        watch_id = f"subagents-{batch.id[:12]}"
        store.update_batch(db, batch.id, watch_id=watch_id)
        batch.watch_id = watch_id
    if widget_id:
        widget_id = f"subagents-{batch.id[:12]}"
        store.update_batch(db, batch.id, widget_id=widget_id)
        batch.widget_id = widget_id

    created: List[Dict[str, Any]] = []
    for i, spec in enumerate(specs):
        if route or spec.route:
            spec = apply_router(
                spec, session_id=parent_session_id, project_path=run_path
            )
        sid = db.create_subagent_chat_session(
            user_id,
            parent_session_id=int(parent_session_id),
            session_name=spec.title,
            project_id=project_id,
            project_name=project_name,
            project_path=project_path,
            checkout_uid=checkout_uid,
            display_name=spec.display_name or spec.title,
            avatar=spec.avatar,
            agent_profile_id=spec.profile_id or None,
        )
        prompt = spec.message
        child = store.insert_child(
            db,
            batch_id=batch.id,
            session_id=sid,
            sort_index=i,
            spec=spec,
            prompt=prompt,
        )
        created.append({"spec": spec, "child": child})

    batch = _refresh(db, batch.id)
    # Bind to the row of the turn that spawned it (it exists from turn start,
    # api.chat_turn_row) or the manual anchor: the cards ride that bubble now.
    # Nothing to bind to yet → the pinned background card until a row of that turn is saved.
    bind_new_batch(db, batch, anchor_message_id=anchor_message_id)
    extras.ensure_parent_tasks(db, batch)
    extras.write_watch(batch)

    timed_out = False
    if collect_n == COLLECT_SERIAL:
        if wait:
            _run_serial(db, batch, created, runner=runner, project_path=run_path)
            waited = wait_batch(batch.id, timeout=timeout, db=db, runner=None, start=False)
            timed_out = bool(waited.get("timed_out"))
        else:
            # Start only the first; wait/message will continue.
            spec0, child0 = created[0]["spec"], created[0]["child"]
            run_child_turn_async(
                db, child0, spec0, project_path=run_path, runner=runner
            )
    else:
        for item in created:
            run_child_turn_async(
                db,
                item["child"],
                item["spec"],
                project_path=run_path,
                runner=runner,
            )
        if wait:
            waited = wait_batch(batch.id, timeout=timeout, db=db, runner=runner, start=False)
            timed_out = bool(waited.get("timed_out"))

    batch = _refresh(db, batch.id)
    _maybe_extras(db, batch)
    payload = batch.public()
    payload["ok"] = True
    payload["timed_out"] = timed_out
    if budget_notes:
        payload["tier_picks"] = budget_notes
    return payload


def _serial_stopped(batch: BatchRecord) -> bool:
    """A serial chain stops at a cancelled step; later steps never start."""
    if batch.status in TERMINAL_BATCH:
        return True
    return any(c.status == STATUS_CANCELLED for c in batch.children)


def _cancel_serial_rest(db, batch: BatchRecord, *, after: int) -> None:
    """Cancel the pending steps after ``after`` (sort index) of a serial batch.

    The batch then reads ``cancelled``, not ``done``, so the parent's ``wait``
    does not report a stopped chain as finished.
    """
    for child in batch.children:
        if child.sort_index > after and child.status == STATUS_PENDING:
            store.update_child(db, child.id, status=STATUS_CANCELLED, finished=True)
    batch = _refresh(db, batch.id)
    if batch.status not in TERMINAL_BATCH and all(
        c.status in TERMINAL_CHILD for c in batch.children
    ):
        store.update_batch(db, batch.id, status=STATUS_CANCELLED, finished=True)


def _run_serial(
    db,
    batch: BatchRecord,
    created: List[Dict[str, Any]],
    *,
    runner: Optional[TurnRunner],
    project_path: str,
) -> None:
    for item in created:
        if _serial_stopped(_refresh(db, batch.id)):
            break
        child = store.get_child(db, item["child"].id)
        if not child:
            break
        if child.status != STATUS_PENDING:
            continue
        run_child_turn(
            db,
            child,
            item["spec"],
            project_path=project_path,
            runner=runner,
        )
        ended = store.get_child(db, child.id)
        if ended and ended.status == STATUS_CANCELLED:
            _cancel_serial_rest(db, _refresh(db, batch.id), after=ended.sort_index)
        batch = _finalize_if_complete(db, batch)
        _maybe_extras(db, batch)
        if batch.collect == COLLECT_FIRST and batch.status in TERMINAL_BATCH:
            break


def wait_batch(
    batch_id: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    poll: float = DEFAULT_POLL_SEC,
    db=None,
    runner: Optional[TurnRunner] = None,
    start: bool = False,
    advance: bool = True,
) -> Dict[str, Any]:
    """Poll a batch round to terminal-or-timeout, reporting ``timed_out``.

    With ``start=True`` pending children are kicked in this process; serial
    batches additionally advance one pending child per poll (``advance=True``,
    the default that preserves in-process supervision). Observation-only
    callers pass ``start=False, advance=False``: rows are read and finalized
    but never started, so a ``wait`` on stale pending rows cannot revive
    execution nobody owns.
    """
    db = _open_db(db)
    deadline = time.time() + max(1.0, float(timeout))
    batch = _refresh(db, batch_id)
    if start:
        _kick_pending(db, batch, runner=runner)
    while time.time() < deadline:
        batch = _refresh(db, batch_id)
        _reconcile(db, batch)
        batch = _refresh(db, batch_id)
        if advance and batch.collect == COLLECT_SERIAL:
            _kick_next_serial(db, batch, runner=runner)
        batch = _finalize_if_complete(db, batch)
        _maybe_extras(db, batch)
        if batch.collect == COLLECT_FIRST:
            if any(c.status == STATUS_DONE for c in batch.children):
                batch = _finalize_if_complete(db, batch)
                _maybe_extras(db, batch)
                break
        if batch.lifetime == LIFETIME_CONVERSATIONAL:
            if all(c.status in TERMINAL_CHILD for c in batch.children):
                break
        elif batch.status in TERMINAL_BATCH:
            break
        elif all(c.status in TERMINAL_CHILD for c in batch.children):
            break
        time.sleep(max(0.05, float(poll)))
    batch = _finalize_if_complete(db, batch)
    _maybe_extras(db, batch)
    payload = batch.public()
    payload["ok"] = True
    payload["timed_out"] = batch.status not in TERMINAL_BATCH and not (
        batch.lifetime == LIFETIME_CONVERSATIONAL
        and all(c.status in TERMINAL_CHILD for c in batch.children)
    )
    return payload


def _kick_pending(db, batch: BatchRecord, *, runner: Optional[TurnRunner]) -> None:
    parent = _parent_row(db, batch.parent_session_id)
    project_path = _execution_path_for_parent(parent)
    if batch.collect == COLLECT_SERIAL:
        _kick_next_serial(db, batch, runner=runner)
        return
    for child in batch.children:
        if child.status == "pending":
            spec = ChildSpec(
                title=child.label,
                message=child.prompt,
                agent=child.agent,
                model=child.model,
                effort=child.effort,
                profile_id=child.profile_id,
                display_name=child.display_name,
                avatar=child.avatar,
            )
            run_child_turn_async(
                db, child, spec, project_path=project_path, runner=runner
            )


def _kick_next_serial(db, batch: BatchRecord, *, runner: Optional[TurnRunner]) -> None:
    if any(c.status == STATUS_RUNNING for c in batch.children):
        return
    if _serial_stopped(batch):
        return
    parent = _parent_row(db, batch.parent_session_id)
    project_path = _execution_path_for_parent(parent)
    for child in batch.children:
        if child.status == "pending":
            spec = ChildSpec(
                title=child.label,
                message=child.prompt,
                agent=child.agent,
                model=child.model,
                effort=child.effort,
                profile_id=child.profile_id,
                display_name=child.display_name,
                avatar=child.avatar,
            )
            run_child_turn_async(
                db, child, spec, project_path=project_path, runner=runner
            )
            return


def message_child(
    session_id: int,
    text: str,
    *,
    wait: bool = True,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    runner: Optional[TurnRunner] = None,
    db=None,
    turn_id: Optional[str] = None,
    anchor_message_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Send a follow-up round to a child; its new card binds to the parent turn
    that sent it (``turn_id``, default ``CUTTLE_TURN_ID``) or ``anchor_message_id``."""
    db = _open_db(db)
    child = store.get_child_by_session(db, int(session_id))
    if not child:
        raise SubagentError(
            f"session {session_id} is not a sub-agent chat; "
            f"use python -m api.chat_cli send CH-{int(session_id):06d} --text … "
            "for an existing chat (status/snapshot inspect it)"
        )
    if child.status == STATUS_CANCELLED:
        raise SubagentError("that sub-agent was cancelled")
    from api.chat_turn_id import for_spawn

    owner_batch = store.get_batch(db, child.batch_id)
    round_turn = "" if anchor_message_id else for_spawn(
        owner_batch.parent_session_id if owner_batch else None, turn_id)
    _start_card_round(db, child, turn_id=round_turn, anchor_message_id=anchor_message_id)
    # This process runs the turn below, so record it as the owner now: a host
    # death between here and the turn start must not leave an ownerless
    # pending row nobody can reconcile.
    store.update_child(
        db, child.id, status="pending", result="", error="", owner_pid=os.getpid()
    )
    spec = ChildSpec(
        title=child.label,
        message=text,
        agent=child.agent,
        model=child.model,
        effort=child.effort,
        profile_id=child.profile_id,
        display_name=child.display_name,
        avatar=child.avatar,
    )
    parent = store.get_batch(db, child.batch_id)
    project_path = ""
    if parent:
        prow = db.get_chat_session_by_id(parent.parent_session_id) or {}
        project_path = _execution_path_for_parent(prow)
        if parent.status in TERMINAL_BATCH:
            store.update_batch(db, parent.id, status=STATUS_RUNNING)
    if wait:
        result = run_child_turn(
            db, child, spec, project_path=project_path, runner=runner
        )
        batch = _finalize_if_complete(db, _refresh(db, child.batch_id))
        _maybe_extras(db, batch)
        return {
            "ok": True,
            "child": store.get_child(db, child.id).public(),
            "batch": batch.public(),
            "response": (result or {}).get("response") or "",
        }
    run_child_turn_async(db, child, spec, project_path=project_path, runner=runner)
    return {
        "ok": True,
        "child": store.get_child(db, child.id).public(),
        "started": True,
    }


def cancel_batch(batch_id: str, *, db=None) -> Dict[str, Any]:
    db = _open_db(db)
    batch = _refresh(db, batch_id)
    for child in batch.children:
        if child.status not in TERMINAL_CHILD:
            _cancel_child(db, child.session_id)
    store.update_batch(db, batch.id, status=STATUS_CANCELLED, finished=True)
    batch = _refresh(db, batch.id)
    _maybe_extras(db, batch)
    payload = batch.public()
    payload["ok"] = True
    return payload


def cancel_session(session_id: int, *, db=None) -> Dict[str, Any]:
    """Cancel one child, or every running child of a parent chat."""
    db = _open_db(db)
    child = store.get_child_by_session(db, int(session_id))
    if child:
        _cancel_child(db, int(session_id))
        batch = _refresh(db, child.batch_id)
        if batch.collect == COLLECT_SERIAL and child.status not in TERMINAL_CHILD:
            # Ending the running step ends the chain. Otherwise the spawn
            # process starts the next step and the agent "comes back".
            _cancel_serial_rest(db, batch, after=child.sort_index)
        batch = _finalize_if_complete(db, _refresh(db, child.batch_id))
        _maybe_extras(db, batch)
        return {"ok": True, "cancelled": "child", "batch": batch.public()}
    batches = store.list_running_for_parent(db, int(session_id))
    out = []
    for batch in batches:
        out.append(cancel_batch(batch.id, db=db))
    return {"ok": True, "cancelled": "parent", "batches": out}


def close_batch(batch_id: str, *, db=None) -> Dict[str, Any]:
    db = _open_db(db)
    batch = _refresh(db, batch_id)
    store.update_batch(db, batch.id, status=STATUS_CLOSED, finished=True)
    batch = _refresh(db, batch.id)
    _maybe_extras(db, batch)
    payload = batch.public()
    payload["ok"] = True
    return payload


def status_payload(batch_id: Optional[str] = None, parent_session_id: Optional[int] = None, *, db=None) -> Dict[str, Any]:
    db = _open_db(db)
    if batch_id:
        batch = store.get_batch(db, batch_id)
        if not batch:
            return {"ok": False, "error": "not_found"}
        _reconcile(db, batch)
        batch = _refresh(db, batch_id)
        _finalize_if_complete(db, batch)
        return {"ok": True, **batch.public()}
    if parent_session_id is None:
        return {"ok": False, "error": "batch or parent required"}
    for batch in store.list_batches_for_parent(db, int(parent_session_id)):
        _reconcile(db, batch)
    batches = [b.public() for b in store.list_batches_for_parent(db, int(parent_session_id))]
    return {
        "ok": True,
        "parent_session_id": int(parent_session_id),
        "parent_handle": chat_handle(int(parent_session_id)),
        "batches": batches,
    }


def attach_batches_to_assistant_meta(
    parent_session_id: int,
    meta: Optional[Dict[str, Any]],
    *,
    message_id: Optional[int] = None,
    db=None,
    turn_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Merge launcher payloads onto the parent assistant bubble metadata.

    Cards belong to the row of the turn they were started in (``turn_id``,
    default: the row's own, api.chat_turn_id): another turn's waiting batch is
    never pulled onto this reply, and a manual run's (no turn) was bound at spawn."""
    from api.chat_turn_id import ambient, turn_id_of

    db = _open_db(db)
    out = dict(meta or {})
    turn_id = turn_id or turn_id_of(out) or ambient(parent_session_id)
    bound = []
    if message_id is not None:
        bound = store.bind_unattached_batches(db, int(parent_session_id), int(message_id), turn_id)
        # Batches bound to this bubble already (at spawn, to the running turn's
        # row: api.chat_turn_row) carry their cards into the saved reply too.
        ids = {b.id for b in bound}
        bound += [b for b in store.list_batches_for_parent(db, int(parent_session_id))
                  if b.attach_message_id == int(message_id) and b.id not in ids]
    else:
        bound = [
            b
            for b in store.list_batches_for_parent(db, int(parent_session_id))
            if not b.attach_message_id and turn_id and b.turn_id == turn_id
        ]
        # Don't persist attach without an id; still expose launchers this turn.
    launchers: List[Dict[str, Any]] = list(out.get("subagents") or [])
    seen = {str(x.get("handle") or x.get("session_id")) for x in launchers}
    card = _launcher_fn()
    for batch in bound:
        for child in batch.children:
            pub = card(child)
            key = str(pub.get("handle") or pub.get("session_id"))
            if key in seen:
                continue
            seen.add(key)
            launchers.append(pub)
        if batch.id and "subagent_batch_id" not in out:
            out["subagent_batch_id"] = batch.id
    # Follow-up rounds sent to existing children since the last reply (this
    # turn's, or with no turn of their own).
    pending = [c for c in store.pending_card_children(db, int(parent_session_id))
               if turn_id and c.card_turn_id == turn_id]
    for child in pending:
        pub = card(child)
        key = str(pub.get("handle") or pub.get("session_id"))
        if key in seen:
            continue
        seen.add(key)
        launchers.append(pub)
    if message_id is not None:
        for child in pending:
            store.set_card_round(db, child.id, message_id=int(message_id), pending=False)
    if launchers:
        out["subagents"] = launchers
    return out


def note_external_round(session_id: int, *, turn_id: Optional[str] = None, db=None) -> bool:
    """A follow-up reached a sub-agent child without ``subagents message``
    (``chat_cli send``): give it a card round on the parent like ``message``
    does — bound to the turn that sent it — and show the child working.

    False when ``session_id`` is not a live sub-agent child."""
    db = _open_db(db)
    child = store.get_child_by_session(db, int(session_id))
    if not child or child.status == STATUS_CANCELLED:
        return False
    batch = store.get_batch(db, child.batch_id)
    if not batch:
        return False
    if child.status in (STATUS_PENDING, STATUS_RUNNING):
        return True  # already working: this message joins that round
    from api.chat_turn_id import for_spawn

    _start_card_round(db, child, turn_id=for_spawn(batch.parent_session_id, turn_id))
    store.begin_external_round(db, child.id)
    if batch.status in TERMINAL_BATCH:
        store.update_batch(db, batch.id, status=STATUS_RUNNING)
    return True


def external_round_undelivered(session_id, *, db=None) -> bool:
    """Whether a sub-agent child only *looks* busy: its external round is marked
    running (``note_external_round``, at send time) but no turn runs behind it.

    The chat-ops outbox asks this before treating the child as busy. Without it
    the send that opened the round waits for the round to end, and the round
    waits for the send — a chat that says "running" for hours with nothing
    behind it (incident 2026-11-10, CH-002385). Flask process only: turn
    ownership (``chat_delivery``, live status) is in-process state."""
    try:
        sid = int(_bare_sid(session_id))
        child = store.get_child_by_session(_open_db(db), sid)
    except Exception:
        return False
    if not child or child.status != STATUS_RUNNING or child.live_turn_id != store.EXTERNAL_ROUND:
        return False
    try:
        from api import chat_delivery
        from api.chat_live_status import get_live_status

        return not chat_delivery.is_busy(sid) and not get_live_status(sid).get("active")
    except Exception:
        return False


def on_child_turns_ended(session_ids, *, db=None) -> List[str]:
    """Close external rounds (``note_external_round``) whose child turn just ended.

    Called with the chats whose turn ended (``turn_end``); the round's outcome
    is the child's newest reply. Rounds run by ``run_child_turn`` record their
    own end and are left alone."""
    ids = [int(s) for s in session_ids or () if str(s).isdigit()]
    if not ids:
        return []
    db = _open_db(db)
    closed: List[str] = []
    for sid in ids:
        child = store.get_child_by_session(db, sid)
        if not child or child.status != STATUS_RUNNING or child.live_turn_id != store.EXTERNAL_ROUND:
            continue
        try:
            from api import chat_delivery

            if chat_delivery.is_busy(sid):
                continue  # a newer turn of the child is already running
        except Exception:
            pass
        _close_external_round(db, child)
        closed.append(child.id)
    return closed


def _close_external_round(db, child: ChildRecord) -> None:
    """End an external round with the child's newest reply as its outcome."""
    sid = int(child.session_id)
    text = store.latest_assistant_text(db, sid)
    from api.chat_attention import is_error

    failed = not text.strip() or is_error(text, {})
    store.update_child(db, child.id, status=STATUS_FAILED if failed else STATUS_DONE,
                       result=text, error="No reply" if not text.strip() else "", finished=True)
    batch = store.get_batch(db, child.batch_id)
    if batch:
        _finalize_if_complete(db, batch)


# An external round is marked running when ``chat_cli send`` is called, before
# the message is delivered. The grace covers the gap until its turn starts.
EXTERNAL_ROUND_GRACE_SEC = 120


def _utc_epoch(stamp: str) -> Optional[float]:
    import calendar

    try:
        return float(calendar.timegm(time.strptime(stamp[:19], "%Y-%m-%d %H:%M:%S")))
    except (TypeError, ValueError):
        return None


def close_stale_external_rounds(db, *, now: Optional[float] = None) -> List[str]:
    """Close external rounds that never got (or no longer have) a turn behind them.

    ``on_child_turns_ended`` closes a round when the child's turn ends. A send
    that is cancelled or fails before delivery starts no turn, so that close
    never comes and the card says "running" forever. A round is stale when the
    child chat is not busy, no send to it is still waiting in the chat-ops
    outbox, and it started more than the grace period ago."""
    now = time.time() if now is None else now
    closed: List[str] = []
    for child in store.running_external_children(db):
        started = _utc_epoch(child.started_at)
        if started is not None and now - started < EXTERNAL_ROUND_GRACE_SEC:
            continue
        sid = int(child.session_id)
        try:
            from api import chat_delivery

            if chat_delivery.is_busy(sid):
                continue
        except Exception:
            continue
        try:
            from api.chat_ops import store as ops_store

            waiting = [o for o in ops_store.involving(
                sid, states=("pending", "dispatching", "verifying", "steering"))
                if str(o.get("target")) == str(sid) and o.get("kind") in ("send", "notice")]
        except Exception:
            continue  # unknown outbox state: leave the round alone
        if waiting:
            continue
        _close_external_round(db, child)
        closed.append(child.id)
    return closed


def _start_card_round(db, child: ChildRecord, *, turn_id: str = "",
                      anchor_message_id: Optional[int] = None) -> None:
    """A follow-up is a new round: freeze the old card, mint a new one later.

    The bubble that showed the previous round keeps that round's outcome
    (snapshot marked ``frozen``; history hydration leaves it alone), and the
    parent's next saved reply gets a fresh live card for this round.
    """
    from api.subagents import fleet

    if not fleet.fleet_enabled() or child.card_pending:
        return
    bubble = store.card_bubble_id(db, child)
    if not bubble:
        return  # never shown yet: the batch's first bubble will carry it
    batch = _refresh(db, child.batch_id)
    target = _card_target(db, int(batch.parent_session_id), turn_id=turn_id,
                          anchor_message_id=anchor_message_id)
    if target and int(target) == int(bubble):
        return  # same bubble: its card simply goes on (history keeps it current)
    # Mark the new round first: the freeze below rewrites the old bubble, and
    # that write must already see the live card belonging elsewhere.
    store.set_card_round(db, child.id, pending=True, turn_id=turn_id)
    try:
        row = db.get_message_by_id(bubble) or {}
        meta = json.loads(row.get("metadata") or "{}") if isinstance(row.get("metadata"), str) else dict(row.get("metadata") or {})
        cards = meta.get("subagents")
        if isinstance(cards, list):
            snap = dict(fleet.fleet_entry(child), frozen=True)
            meta["subagents"] = [
                snap if isinstance(e, dict) and str(e.get("id") or "") == child.id and not e.get("frozen") else e
                for e in cards
            ]
            db.update_message_content(bubble, str(row.get("content") or ""), metadata=meta)
    except Exception as exc:
        print(f"[SUBAGENTS] freeze fleet card failed: {exc}", flush=True)
    # The round's card belongs to the turn that sent it (or the anchor / the
    # latest bubble for a manual run): bind now when that bubble exists.
    if target:
        store.set_card_round(db, child.id, message_id=target, pending=False)
        try:
            _write_cards(db, target, [store.get_child(db, child.id) or child])
        except Exception as exc:
            print(f"[SUBAGENTS] round card binding to row {target} failed: {exc}", flush=True)


def on_session_cancelled(session_id: Any) -> None:
    sid = _bare_sid(session_id)
    if sid is None:
        return
    try:
        cancel_session(int(sid))
    except Exception:
        pass


def public_parent_subagents(session_id: Any, *, db=None) -> List[Dict[str, Any]]:
    sid = _bare_sid(session_id)
    if sid is None:
        return []
    try:
        db = _open_db(db)
        # A batch whose owning process died must not keep painting orbs on the
        # parent typing indicator. Reconcile orphans + finalize before listing,
        # so a dead-owner run stops bleeding into later turns.
        live = store.list_live_for_parent(db, int(sid))
        if any(
            c.status not in TERMINAL_CHILD and (c.owner_pid or c.live_turn_id == store.EXTERNAL_ROUND)
            for b in live for c in b.children
        ):
            _reconcile(db)
            live = store.list_live_for_parent(db, int(sid))
        card = _launcher_fn()
        launchers: List[Dict[str, Any]] = []
        for batch in live:
            for child in batch.children:
                entry = dict(card(child))
                # Which parent bubble owns this card (the running turn's row
                # from spawn, or a saved reply): the page shows it there, on
                # the live bubble, or in the pinned background card — once, never twice.
                # A new follow-up round is unbound until the next reply.
                bound = None if child.card_pending else (
                    child.card_message_id or batch.attach_message_id)
                entry["bound_message_id"] = int(bound) if bound else None
                # The turn this card belongs to (api.chat_turn_id): the page
                # shows it on the live bubble of that turn.
                entry["turn_id"] = (child.card_turn_id if child.card_pending else batch.turn_id) or None
                launchers.append(entry)
        return launchers
    except Exception:
        return []


def _launcher_fn():
    """Fleet cards when ``subagent_fleet_cards`` is on, else slim launchers."""
    from api.subagents import fleet

    if fleet.fleet_enabled():
        return fleet.fleet_entry
    return lambda child: public_launcher(child.public())


def hydrate_parent_fleet(db, messages: List[Dict[str, Any]]) -> None:
    """Refresh fleet cards on saved parent bubbles from the child rows.

    Metadata holds the launcher snapshot taken when the reply was saved;
    the child row is the source of truth, so reload shows the current
    outcome (a conversational follow-up, a later cancel, a lost host).
    """
    from api.subagents import fleet

    if not messages or not fleet.fleet_enabled():
        return
    targets = []
    for msg in messages:
        meta = msg.get("metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (TypeError, ValueError):
                continue
        if msg.get("role") == "assistant" and isinstance(meta, dict) and isinstance(meta.get("subagents"), list):
            targets.append((msg, meta))
    if not targets:
        return
    children = {}
    for _, meta in targets:
        for entry in meta["subagents"]:
            cid = str((entry or {}).get("id") or "") if isinstance(entry, dict) else ""
            if cid and cid not in children:
                children[cid] = store.get_child(db, cid)
    if any(c and c.status not in TERMINAL_CHILD and (c.owner_pid or c.live_turn_id == store.EXTERNAL_ROUND)
           for c in children.values()):
        _reconcile(db)
        children = {cid: store.get_child(db, cid) for cid in children}
    for msg, meta in targets:
        meta = dict(meta)
        meta["subagents"] = [
            fleet.fleet_entry(children[str(e.get("id"))])
            if isinstance(e, dict) and not e.get("frozen") and children.get(str(e.get("id") or "")) else e
            for e in meta["subagents"]
        ]
        msg["metadata"] = meta


def child_live_status(session_id: Any, *, db=None) -> Optional[Dict[str, Any]]:
    """If this session is a sub-agent, return overlay flags for live-status."""
    sid = _bare_sid(session_id)
    if sid is None:
        return None
    try:
        db = _open_db(db)
        child = store.get_child_by_session(db, int(sid))
    except Exception:
        return None
    if not child:
        return None
    # Repair before reporting: a non-terminal row whose host died must not be
    # reported as generating, or the pane spins forever with no agent behind it.
    # Gated on an actual outstanding child so ordinary live-status polls for
    # non-sub-agent chats stay a single indexed read.
    if child.status in (STATUS_PENDING, STATUS_RUNNING) and (
            child.owner_pid or child.live_turn_id == store.EXTERNAL_ROUND):
        _reconcile(db)
        child = store.get_child_by_session(db, int(sid)) or child
    generating = child.status in ("pending", STATUS_RUNNING)
    report_url = (
        f"/query_log.html?id={child.query_id}" if child.query_id else None
    )
    # The child turn's reply row (api.chat_turn_row): the child pane's live bubble id.
    message_id = None
    if generating:
        try:
            rows = db.in_progress_messages(int(sid)) or []
            message_id = int(rows[-1]["id"]) if rows else None
        except Exception:
            message_id = None
    # The child chat's only user row is the parent's brief (speaker_kind
    # parent), which the working bubble rightly ignores, so without this the
    # live bubble had no agent chip until the reply landed. Same chip the
    # finished reply gets (spec/record pins; never live session pins).
    slash_command = None
    if generating:
        try:
            from api.subagents.identity import child_slash_command

            slash_command = child_slash_command(child=child, session_id=int(sid))
        except Exception:
            slash_command = None
    return {
        "generating": generating,
        "slash_command": slash_command,
        "status": (child.live_status if child.status == STATUS_RUNNING and child.live_status
                   else f"{child.label or 'Subagent'} — {child.status}"),
        "subagent": child.public(),
        # Carried so the child pane's query-log button gets a real id while the
        # sub-agent is still working, not only after its reply is persisted.
        "query_id": child.query_id or None,
        "report_url": report_url,
        "message_id": message_id,
    }
