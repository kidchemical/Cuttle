"""Cuttle Usage dashboard: date ranges, buckets, group-by breakdowns."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from flask import Flask

from api.agent_router.outcomes import all_outcomes, record_turn
from api.dashboards import usage
from api.dashboards.routes import dashboards_bp

NOW = datetime(2026, 9, 26, 18, 0, tzinfo=timezone.utc).timestamp()
DAY = 86400.0


def _seed(db: Path) -> None:
    turns = [
        ("a", "cursor", "auto", NOW - 1 * DAY, {"usage": {"input_tokens": 900, "output_tokens": 100}}),
        ("b", "cursor", "cursor-grok-4.6-high", NOW - 1 * DAY, {"usage": {"total_tokens": 50}}),
        ("c", "codex", "gpt-6-luna", NOW, {"cost": 1.5, "usage": {"input_tokens": 10, "output_tokens": 5}}),
        ("d", "codex", "gpt-6-luna", NOW - 20 * DAY, {"cost": 2.0}),
        ("e", "muse", "default", NOW - 400 * DAY, {"usage": {"total_tokens": 7}}),
    ]
    for decision, agent, model, stamp, result in turns:
        record_turn(
            decision_id=decision, target_agent=agent, target_model=model, source="pinned",
            failure_kind="none", latency_ms=1_800_000, result=result, recorded_at=stamp, db_path=db,
        )


def test_seven_day_range_by_harness(tmp_path: Path):
    db = tmp_path / "outcomes.db"
    _seed(db)
    out = usage.cuttle_usage(range_id="7d", group_by="harness", tz_offset_minutes=0, db_path=db, now=NOW)
    assert out["selected_range"] == "7d"
    assert out["bucket_interval"] == "day"
    assert out["start"] == "2026-09-20" and out["end"] == "2026-09-26"
    assert len(out["buckets"]) == 7
    by_key = {g["key"]: g for g in out["groups"]}
    assert set(by_key) == {"cursor", "codex"}
    assert by_key["cursor"]["totals"]["turns"] == 2
    assert by_key["cursor"]["totals"]["total_tokens"] == 1050
    assert by_key["cursor"]["series"]["turns"][5] == 2
    assert by_key["codex"]["series"]["cost_usd"][6] == 1.5
    assert out["totals"]["cost_usd"] == 1.5
    assert out["totals"]["agent_hours"] == 1.5
    assert out["stats"]["with_cost"] == 1


def test_model_grouping_strips_effort_and_tags_ambiguous_models(tmp_path: Path):
    db = tmp_path / "outcomes.db"
    _seed(db)
    out = usage.cuttle_usage(range_id="all", group_by="model", tz_offset_minutes=0, db_path=db, now=NOW)
    labels = {g["label"] for g in out["groups"]}
    assert labels == {"cursor · auto", "grok-4.6", "gpt-6-luna", "muse · default"}
    assert out["bucket_interval"] == "month"
    assert out["start"] == "2025-08-22"
    assert out["totals"]["turns"] == 5


def test_custom_range_overrides_preset_and_swaps_order(tmp_path: Path):
    db = tmp_path / "outcomes.db"
    _seed(db)
    out = usage.cuttle_usage(
        range_id="7d", start="2026-09-10", end="2026-09-01", group_by="none",
        tz_offset_minutes=0, db_path=db, now=NOW,
    )
    assert out["selected_range"] == "custom"
    assert (out["start"], out["end"]) == ("2026-09-01", "2026-09-10")
    assert out["groups"][0]["label"] == "All turns"
    assert out["totals"]["cost_usd"] == 2.0


def test_week_buckets_start_monday_and_overflow_groups(tmp_path: Path):
    db = tmp_path / "outcomes.db"
    for i in range(usage.MAX_GROUPS + 2):
        record_turn(
            decision_id=f"m{i}", target_agent="opencode", target_model=f"model-{i}", source="pinned",
            failure_kind="none", recorded_at=NOW - i * 3600, db_path=db,
        )
    out = usage.cuttle_usage(range_id="90d", interval="week", tz_offset_minutes=0, db_path=db, now=NOW)
    assert out["bucket_interval"] == "week"
    assert datetime.fromisoformat(out["buckets"][0]).weekday() == 0
    assert len(out["groups"]) == usage.MAX_GROUPS
    other = out["groups"][-1]
    assert other["key"] == "__other__"
    assert other["totals"]["turns"] == 3
    assert len(other["members"]) == 3


def test_overflow_never_folds_a_costly_low_turn_group(tmp_path: Path):
    db = tmp_path / "outcomes.db"
    for i in range(usage.MAX_GROUPS + 2):
        for t in range(3):
            record_turn(
                decision_id=f"m{i}-{t}", target_agent="opencode", target_model=f"model-{i}", source="pinned",
                failure_kind="none", recorded_at=NOW - i * 60 - t, db_path=db,
            )
    record_turn(
        decision_id="pricey", target_agent="codex", target_model="gpt-pricey", source="pinned",
        failure_kind="none", result={"cost": 18.0}, recorded_at=NOW - 30, db_path=db,
    )
    out = usage.cuttle_usage(range_id="7d", tz_offset_minutes=0, db_path=db, now=NOW)
    labels = [g["label"] for g in out["groups"]]
    assert len(labels) == usage.MAX_GROUPS
    assert "gpt-pricey" in labels
    other = out["groups"][-1]
    assert other["key"] == "__other__"
    assert "gpt-pricey" not in other["members"]


def test_usage_route(tmp_path: Path, monkeypatch, owner_session):
    db = tmp_path / "outcomes.db"
    _seed(db)
    real = usage.cuttle_usage
    monkeypatch.setattr(usage, "cuttle_usage", lambda **kw: real(**kw, db_path=db, now=NOW))
    app = Flask(__name__)
    app.register_blueprint(dashboards_bp)
    body = owner_session.sign_in(app.test_client()).get("/api/dashboards/cuttle-usage?range=14d&group=harness&tz=0").get_json()
    assert body["success"] is True
    assert body["selected_group_by"] == "harness"
    assert len(body["buckets"]) == 14
    assert body["totals"]["turns"] == 3


def test_cached_input_tokens_metric(tmp_path: Path):
    db = tmp_path / "outcomes.db"
    record_turn(
        decision_id="c1", target_agent="muse", target_model="m", source="pinned",
        failure_kind="none", recorded_at=NOW,
        result={"usage": {"input_tokens": 1000, "cached_tokens": 800, "output_tokens": 10}},
        db_path=db,
    )
    record_turn(
        decision_id="c2", target_agent="codex", target_model="m", source="pinned",
        failure_kind="none", recorded_at=NOW,
        result={"usage": {"input_tokens": 500, "cached_input_tokens": 100}},
        db_path=db,
    )
    record_turn(
        decision_id="c3", target_agent="cursor", target_model="m", source="pinned",
        failure_kind="none", recorded_at=NOW,
        result={"usage": {"input_tokens": 200, "cacheReadTokens": 50, "output_tokens": 5}},
        db_path=db,
    )
    record_turn(
        decision_id="c4", target_agent="muse", target_model="m", source="pinned",
        failure_kind="none", recorded_at=NOW,
        result={"usage": {"input_tokens": 300, "output_tokens": 5}},
        db_path=db,
    )
    stored = {r["decision_id"]: r for r in all_outcomes(db_path=db)}
    assert stored["c1"]["cached_tokens"] == 800
    assert stored["c2"]["cached_tokens"] == 100
    assert stored["c3"]["cached_tokens"] == 50
    assert stored["c4"]["cached_tokens"] is None
    out = usage.cuttle_usage(range_id="7d", group_by="none", tz_offset_minutes=0, db_path=db, now=NOW)
    assert "cached_input_tokens" in [m["id"] for m in out["metrics"]]
    assert out["totals"]["cached_input_tokens"] == 950
    assert out["stats"]["with_cached"] == 3


def _seed_chat_rows(db: Path) -> None:
    rows = [
        ("p1", "muse", "m", "777", "q-alpha", NOW - 100),
        ("p2", "muse", "m", "777", "q-beta", NOW - 90),
        ("p3", "codex", "m", "778", "q-gamma", NOW - 80),
        ("p4", "codex", "m", None, None, NOW - 70),
    ]
    for decision, agent, model, sid, qid, stamp in rows:
        record_turn(
            decision_id=decision, target_agent=agent, target_model=model, source="pinned",
            failure_kind="none", recorded_at=stamp, session_id=sid, query_id=qid, db_path=db,
        )


def _seed_auth_db(path: Path) -> None:
    import json
    import sqlite3

    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, chat_session_id TEXT, role TEXT, metadata TEXT)")
    bubbles = [
        (777, "user", {}),
        (777, "assistant", {"query_id": "q-alpha"}),
        (777, "user", {}),
        (777, "assistant", {"query_id": "q-beta"}),
        (778, "user", {}),
        (778, "assistant", {"query_id": "q-gamma"}),
    ]
    for sid, role, meta in bubbles:
        conn.execute(
            "INSERT INTO chat_messages (chat_session_id, role, metadata) VALUES (?, ?, ?)",
            (str(sid), role, json.dumps(meta)),
        )
    conn.commit()
    conn.close()


def test_group_by_chat(tmp_path: Path):
    db = tmp_path / "outcomes.db"
    _seed_chat_rows(db)
    out = usage.cuttle_usage(range_id="7d", group_by="chat", tz_offset_minutes=0, db_path=db, now=NOW)
    by_key = {g["key"]: g for g in out["groups"]}
    assert by_key["chat:777"]["label"] == "CH-000777"
    assert by_key["chat:777"]["totals"]["turns"] == 2
    assert by_key["chat:778"]["label"] == "CH-000778"
    assert by_key["chat:none"]["label"] == "No chat"


def test_group_by_prompt_resolves_handles(tmp_path: Path):
    db = tmp_path / "outcomes.db"
    auth = tmp_path / "auth.db"
    _seed_chat_rows(db)
    _seed_auth_db(auth)
    out = usage.cuttle_usage(
        range_id="7d", group_by="prompt", tz_offset_minutes=0, db_path=db, auth_db_path=auth, now=NOW,
    )
    labels = {g["label"] for g in out["groups"]}
    assert labels == {"CH-000777-2", "CH-000777-4", "CH-000778-2", "No chat · p4"}
    assert out["totals"]["turns"] == 4


def test_group_by_prompt_falls_back_without_auth_db(tmp_path: Path):
    db = tmp_path / "outcomes.db"
    _seed_chat_rows(db)
    out = usage.cuttle_usage(
        range_id="7d", group_by="prompt", tz_offset_minutes=0, db_path=db,
        auth_db_path=tmp_path / "missing.db", now=NOW,
    )
    labels = {g["label"] for g in out["groups"]}
    assert "CH-000777 · q-alpha" in labels


def test_one_and_three_day_ranges(tmp_path: Path):
    db = tmp_path / "outcomes.db"
    _seed(db)
    ids = [r["id"] for r in usage.RANGES]
    assert ids[:3] == ["1d", "3d", "7d"]
    one = usage.cuttle_usage(range_id="1d", tz_offset_minutes=0, db_path=db, now=NOW)
    assert one["selected_range"] == "1d"
    assert one["start"] == one["end"] == "2026-09-26"
    assert len(one["buckets"]) == 1
    assert one["stats"]["turns"] == 1
    three = usage.cuttle_usage(range_id="3d", tz_offset_minutes=0, db_path=db, now=NOW)
    assert three["selected_range"] == "3d"
    assert (three["start"], three["end"]) == ("2026-09-24", "2026-09-26")
    assert len(three["buckets"]) == 3
    assert three["stats"]["turns"] == 3
