"""Achievements: catalog integrity, metric math, and unlock semantics.

Three layers are covered separately so a failure names the layer:

* catalog    — ids/rarity/metric integrity + the >=40 requirement
* evaluator  — reads a seeded router_outcomes.db and excludes sub-agent chats
* unlocks    — progress is monotonic, unlocks are idempotent, ack/pending work

Sub-agent exclusion is the load-bearing rule: child-chat turns must never count
toward the single-message ("one_turn_*") ladder.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from api.achievements import catalog, evaluator, store, unlocks

DAY = 86400.0
HOUR = 3_600_000.0
NOW = 1_788_000_000.0  # fixed clock so local-time buckets are deterministic


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------
def test_catalog_has_at_least_forty_achievements():
    assert len(catalog.all_achievements()) >= 40


def test_catalog_ids_are_unique():
    ids = [a.id for a in catalog.all_achievements()]
    assert len(ids) == len(set(ids))


def test_every_achievement_uses_a_known_metric():
    for a in catalog.all_achievements():
        assert a.metric in evaluator.METRIC_NAMES, f"{a.id} -> unknown metric {a.metric}"


def test_every_achievement_has_valid_rarity_and_threshold():
    for a in catalog.all_achievements():
        assert a.rarity in catalog.RARITY_ORDER, a.id
        assert a.threshold > 0, a.id
        assert a.icon and a.title and a.description, a.id


def test_rarity_ladder_spans_every_tier():
    tiers = {a.rarity for a in catalog.all_achievements()}
    assert tiers == set(catalog.RARITY_ORDER)


def test_catalog_categories_are_known():
    for a in catalog.all_achievements():
        assert a.category in catalog.CATEGORY_ORDER, f"{a.id} -> {a.category}"


def test_hidden_achievements_have_a_hint():
    for a in catalog.all_achievements():
        if a.hidden:
            assert a.hint, f"{a.id} is hidden with no hint"


def test_to_dict_hides_description_for_locked_hidden():
    ach = catalog.get("one_turn_1b")
    payload = ach.to_dict(progress=0.0)
    assert payload["hidden"] is True
    assert payload["description"] == ""
    assert payload["hint"]
    unlocked = ach.to_dict(progress=ach.threshold, unlocked_at=1.0)
    assert unlocked["description"]


def test_percent_is_clamped():
    ach = catalog.get("first_dive")
    assert ach.to_dict(progress=99)["percent"] == 100.0
    assert ach.to_dict(progress=0)["percent"] == 0.0


def test_single_message_ladder_excludes_lifetime():
    """The headline achievement must be per-message, not a lifetime total."""
    kraken = catalog.get("one_turn_100m")
    assert kraken.metric == "max_single_turn_tokens"
    assert kraken.threshold == 100_000_000
    assert "sub-agent" in kraken.description.lower()
    assert catalog.get("tokens_100m").metric == "lifetime_total_tokens"


def test_summary_counts_add_up():
    s = catalog.summary()
    assert s["total"] == sum(s["by_rarity"].values())
    assert s["total"] == sum(s["by_category"].values())


# ---------------------------------------------------------------------------
# evaluator fixtures
# ---------------------------------------------------------------------------
def _seed_outcomes(db: Path, rows) -> None:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE router_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at REAL NOT NULL,
            decision_id TEXT NOT NULL,
            attempt_index INTEGER NOT NULL,
            session_id TEXT,
            project_path TEXT,
            task_type TEXT NOT NULL,
            difficulty TEXT NOT NULL,
            strategy TEXT NOT NULL,
            target_agent TEXT NOT NULL,
            target_model TEXT NOT NULL,
            source TEXT NOT NULL,
            success INTEGER NOT NULL,
            failure_kind TEXT NOT NULL,
            reason TEXT,
            latency_ms REAL,
            query_id TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            cached_tokens INTEGER,
            cost REAL,
            user_feedback TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO router_outcomes (recorded_at, decision_id, attempt_index, session_id,"
        " project_path, task_type, difficulty, strategy, target_agent, target_model, source,"
        " success, failure_kind, latency_ms, query_id, prompt_tokens, completion_tokens,"
        " total_tokens, cached_tokens, cost)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def _row(**kw):
    base = dict(
        recorded_at=NOW, decision_id="d1", attempt_index=0, session_id="10",
        project_path="/p/a", task_type="code", difficulty="medium", strategy="direct",
        target_agent="cursor", target_model="auto", source="pinned", success=1,
        failure_kind="none", latency_ms=1000.0, query_id="q1", prompt_tokens=100,
        completion_tokens=50, total_tokens=150, cached_tokens=10, cost=0.5,
    )
    base.update(kw)
    return tuple(base.values())


def _seed_auth(db: Path, child_sessions=(99,)) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE chat_sessions (id INTEGER PRIMARY KEY, parent_session_id INTEGER)"
    )
    conn.execute("CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, role TEXT)")
    conn.execute("CREATE TABLE subagent_children (id TEXT PRIMARY KEY, session_id INTEGER)")
    conn.executemany("INSERT INTO chat_sessions (id, parent_session_id) VALUES (?,?)",
                     [(10, None), (11, None)] + [(cid, 10) for cid in child_sessions])
    conn.executemany("INSERT INTO chat_messages (role) VALUES (?)", [("user",)] * 5)
    conn.executemany("INSERT INTO subagent_children (id, session_id) VALUES (?,?)",
                     [("c1", 99), ("c2", 99)])
    conn.commit()
    conn.close()


@pytest.fixture()
def seeded(tmp_path):
    out_db = tmp_path / "outcomes.db"
    auth_db = tmp_path / "auth.db"
    _seed_outcomes(out_db, [
        _row(),                                                    # plain turn
        _row(decision_id="d2", total_tokens=40_000_000,             # big parent turn
             prompt_tokens=30_000_000, completion_tokens=10_000_000,
             latency_ms=25 * HOUR, target_agent="codex",
             target_model="gpt-6", session_id="11"),
        _row(decision_id="d3", session_id="99",                    # SUB-AGENT child
             total_tokens=500_000_000, prompt_tokens=500_000_000),
        _row(decision_id="d4", attempt_index=1, failure_kind="transport",
             success=0, total_tokens=100),                          # escalation
        _row(decision_id="d5", failure_kind="cancelled", success=0,
             total_tokens=0, latency_ms=10.0),
    ])
    _seed_auth(auth_db)
    return out_db, auth_db


def test_snapshot_reads_token_totals(seeded):
    out_db, auth_db = seeded
    m = evaluator.snapshot(outcomes_db=out_db, auth_db=auth_db)
    # 5 seeded turns, but the sub-agent child (session 99, decision d3) is
    # excluded, so 4 survive the filter.
    assert m["turns_total"] == 4
    assert m["turns_completed"] == 2
    assert m["lifetime_total_tokens"] == 150 + 40_000_000 + 100


def test_snapshot_excludes_subagent_child_sessions(seeded):
    out_db, auth_db = seeded
    m = evaluator.snapshot(outcomes_db=out_db, auth_db=auth_db)
    # Session 99 is a sub-agent child: its 500M must be invisible everywhere.
    assert m["lifetime_total_tokens"] == 40_000_150 + 100
    assert m["max_single_turn_tokens"] == 40_000_000
    assert m["max_single_turn_input_tokens"] == 30_000_000


def test_snapshot_max_latency_and_agent_hours(seeded):
    out_db, auth_db = seeded
    m = evaluator.snapshot(outcomes_db=out_db, auth_db=auth_db)
    assert m["max_turn_latency_ms"] == 25 * HOUR
    # d1 + d2 + d4 only (d3 is a child chat, d5 is 10ms and excluded as a child).
    assert m["agent_hours"] == pytest.approx((1000.0 + 25 * HOUR + 1000.0) / 3_600_000.0)


def test_snapshot_counts_escalations_and_cancellations(seeded):
    out_db, auth_db = seeded
    m = evaluator.snapshot(outcomes_db=out_db, auth_db=auth_db)
    assert m["escalations"] == 1        # d4, attempt_index > 0
    assert m["cancelled_turns"] == 1     # d5, a cancelled turn in the parent chat


def test_snapshot_variety_and_projects(seeded):
    out_db, auth_db = seeded
    m = evaluator.snapshot(outcomes_db=out_db, auth_db=auth_db)
    assert m["distinct_harnesses"] == 2
    assert m["distinct_models"] == 2
    assert m["distinct_projects"] == 1
    assert m["sessions_with_turns"] == 2


def test_snapshot_counts_auth_side_metrics(seeded):
    out_db, auth_db = seeded
    m = evaluator.snapshot(outcomes_db=out_db, auth_db=auth_db)
    assert m["user_messages"] == 5
    assert m["subagent_children"] == 2


def test_max_streak_of_consecutive_days():
    assert evaluator._max_streak([]) == 0
    assert evaluator._max_streak(["2026-10-01", "2026-10-02", "2026-10-03"]) == 3
    assert evaluator._max_streak(["2026-10-01", "2026-10-02", "2026-10-05"]) == 2
    assert evaluator._max_streak(["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]) == 4


def test_snapshot_is_all_zero_when_stores_missing(tmp_path):
    m = evaluator.snapshot(outcomes_db=tmp_path / "nope.db", auth_db=tmp_path / "nope2.db")
    for name in evaluator.METRIC_NAMES:
        assert m[name] == 0.0, name


# ---------------------------------------------------------------------------
# unlocks / store
# ---------------------------------------------------------------------------
@pytest.fixture()
def ach_db(tmp_path):
    return tmp_path / "achievements.db"


def test_record_progress_is_monotonic(ach_db):
    store.record_progress("first_dive", 5.0, 1.0, db_path=ach_db)
    store.record_progress("first_dive", 2.0, 1.0, db_path=ach_db)
    assert store.load_state(ach_db)["first_dive"]["progress"] == 5.0


def test_mark_unlocked_is_idempotent(ach_db):
    assert store.mark_unlocked("first_dive", db_path=ach_db) is True
    assert store.mark_unlocked("first_dive", db_path=ach_db) is False


def test_pending_drains_on_ack(ach_db):
    store.mark_unlocked("first_dive", db_path=ach_db)
    assert len(store.pending_unlocks(catalog.catalog_payload(), db_path=ach_db)) == 1
    assert store.mark_seen("first_dive", db_path=ach_db) is True
    assert store.pending_unlocks(catalog.catalog_payload(), db_path=ach_db) == []
    assert store.mark_seen("first_dive", db_path=ach_db) is False


def test_pending_is_oldest_first(ach_db):
    store.mark_unlocked("first_dive", db_path=ach_db)
    store.mark_unlocked("ten_tides", db_path=ach_db)
    ids = [p["id"] for p in store.pending_unlocks(catalog.catalog_payload(), db_path=ach_db)]
    assert ids == ["first_dive", "ten_tides"]


def test_reset_clears_state(ach_db):
    store.mark_unlocked("first_dive", db_path=ach_db)
    assert store.reset(ach_db) == 1
    assert store.load_state(ach_db) == {}


def test_evaluate_unlocks_only_crossed_achievements(ach_db):
    snap = {"turns_total": 12.0, "first_dive": 0}
    result = unlocks.evaluate(snapshot_fn=lambda: snap, achievements_db=ach_db, force=True)
    ids = {u["id"] for u in result["unlocked"]}
    assert "first_dive" in ids and "ten_tides" in ids
    assert "century_tides" not in ids   # threshold 100
    assert "one_turn_100m" not in ids   # threshold 100M


def test_evaluate_is_idempotent(ach_db):
    snap = {"turns_total": 12.0}
    first = unlocks.evaluate(snapshot_fn=lambda: snap, achievements_db=ach_db, force=True)
    second = unlocks.evaluate(snapshot_fn=lambda: snap, achievements_db=ach_db, force=True)
    assert len(first["unlocked"]) == 2
    assert second["unlocked"] == []
    assert second["progress_updates"] == 0


def test_evaluate_throttles_unless_forced(ach_db):
    snap = {"turns_total": 12.0}
    unlocks.evaluate(snapshot_fn=lambda: snap, achievements_db=ach_db, force=True)
    again = unlocks.evaluate(snapshot_fn=lambda: snap, achievements_db=ach_db)
    assert again["skipped"] == "throttled"


def test_evaluate_never_downgrades_progress(ach_db):
    unlocks.evaluate(snapshot_fn=lambda: {"turns_total": 500.0}, achievements_db=ach_db,
                     force=True)
    unlocks.evaluate(snapshot_fn=lambda: {"turns_total": 3.0}, achievements_db=ach_db,
                     force=True)
    assert store.load_state(ach_db)["century_tides"]["progress"] == 500.0


def test_evaluate_carries_unlock_detail(ach_db):
    unlocks.evaluate(snapshot_fn=lambda: {"turns_total": 1.0}, achievements_db=ach_db,
                     force=True)
    pending = store.pending_unlocks(catalog.catalog_payload(), db_path=ach_db)
    assert pending[0]["detail"]["metric"] == "turns_total"
    assert pending[0]["detail"]["threshold"] == 1.0


def test_status_payload_shape(ach_db):
    unlocks.evaluate(snapshot_fn=lambda: {"turns_total": 7.0}, achievements_db=ach_db,
                     force=True)
    st = unlocks.status(ach_db)
    assert st["total"] == len(catalog.all_achievements())
    assert st["unlocked"] == 1        # 7 turns clears only first_dive (10 clears ten_tides)
    assert st["unseen"] == 1
    first = next(i for i in st["items"] if i["id"] == "first_dive")
    assert first["unlocked"] is True
    assert first["seen"] is False


def test_status_of_empty_install(ach_db):
    st = unlocks.status(ach_db)
    assert st["unlocked"] == 0 and st["unseen"] == 0
    assert len(st["items"]) >= 40


def test_achievement_flag_gate(monkeypatch, ach_db):
    """Every public entry point no-ops while the flag is off."""
    import api.achievements as pkg

    monkeypatch.setattr(pkg, "is_enabled", lambda: False)
    assert pkg.evaluate()["skipped"] == "disabled"
    assert pkg.status()["total"] == 0
    pkg.on_turn_saved(1, {"success": True})  # must not raise or evaluate


def test_on_turn_saved_fires_evaluation(monkeypatch, ach_db):
    import api.achievements as pkg

    fired = []
    monkeypatch.setattr(pkg, "is_enabled", lambda: True)
    monkeypatch.setattr(pkg.unlocks, "evaluate_async",
                        lambda **kw: fired.append(kw))
    pkg.on_turn_saved(922, {"success": True})
    assert fired == [{}]

# ---------------------------------------------------------------------------
# HTTP contract (routes.py)
# ---------------------------------------------------------------------------
def _client(tmp_path, monkeypatch):
    from tests.test_http_authz import _auth_client

    ctx = _auth_client(tmp_path, monkeypatch)
    monkeypatch.setenv("OWNER_USER_EMAIL", "owner@local")
    ctx["client"].set_cookie("session_token", ctx["token"])
    return ctx["client"]


def test_routes_answer_disabled_payload_when_flag_off(tmp_path, monkeypatch):
    """Disabled is a 200 no-op, never a 403 — the client silently skips."""
    from api import web_chat_api as wca
    from api.achievements import routes
    from tests.test_http_authz import LAN

    monkeypatch.setattr(routes, "is_enabled", lambda *_a, **_k: False)
    client = _client(tmp_path, monkeypatch)

    for path in ("/api/achievements", "/api/achievements/pending"):
        res = client.get(path, environ_base=LAN)
        assert res.status_code == 200, path
        body = res.get_json()
        assert body["success"] is False and body["disabled"] is True

    assert client.post("/api/achievements/scan", environ_base=LAN).get_json()["disabled"]
    assert client.post("/api/achievements/first_dive/ack",
                       environ_base=LAN).get_json()["disabled"]


def test_routes_serve_status_when_flag_on(tmp_path, monkeypatch):
    from api import web_chat_api as wca
    from api.achievements import routes
    from tests.test_http_authz import LAN

    monkeypatch.setattr(routes, "is_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr(routes.unlocks, "status", lambda *a, **k: {
        "items": [], "unlocked": 0, "total": len(catalog.all_achievements()),
        "unseen": 0, "rarities": {}, "summary": catalog.summary(),
    })
    client = _client(tmp_path, monkeypatch)

    body = client.get("/api/achievements", environ_base=LAN).get_json()
    assert body["success"] is True
    assert body["total"] >= 40

    pending = client.get("/api/achievements/pending", environ_base=LAN).get_json()
    assert pending == {"success": True, "pending": [], "count": 0}


def test_routes_reject_unknown_achievement_id(tmp_path, monkeypatch):
    from api import web_chat_api as wca
    from api.achievements import routes
    from tests.test_http_authz import LAN

    monkeypatch.setattr(routes, "is_enabled", lambda *_a, **_k: True)
    client = _client(tmp_path, monkeypatch)
    assert client.post("/api/achievements/not_a_real_one/ack",
                       environ_base=LAN).status_code == 400
    assert client.post("/api/achievements/not_a_real_one/grant",
                       environ_base=LAN).status_code == 400


def test_pending_requires_auth():
    from api import web_chat_api as wca
    from tests.test_http_authz import LAN

    anon = wca.app.test_client()
    assert anon.get("/api/achievements", environ_base=LAN).status_code == 401
    assert anon.get("/api/achievements/pending", environ_base=LAN).status_code == 401
    assert anon.post("/api/achievements/scan", environ_base=LAN).status_code == 401


def test_scan_and_grant_are_owner_only(tmp_path, monkeypatch):
    from api import web_chat_api as wca
    from tests.test_http_authz import LAN

    guest = wca.app.test_client()
    assert guest.post("/api/auth/guest", json={}).status_code == 200
    for path in ("/api/achievements/scan", "/api/achievements/reset",
                 "/api/achievements/first_dive/grant"):
        assert guest.post(path, environ_base=LAN).status_code == 403, path


def test_cli_list_reports_disabled_flag(capsys):
    import api.achievements as pkg
    from api.achievements.__main__ import main

    original = pkg.is_enabled
    pkg.is_enabled = lambda: False
    try:
        assert main(["list"]) == 0
    finally:
        pkg.is_enabled = original
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is False
