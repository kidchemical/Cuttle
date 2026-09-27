"""Tests for native job-watch status files."""

import json

from api.job_watch import (
    cancel_job,
    pids_from_status,
    read_status,
    sanitize_bars,
    sanitize_job_id,
    stamp_watch_run,
    status_url,
    watch_form_spec,
    watch_snapshot_from_status,
    write_status,
)


def test_write_status_keeps_discord_form_extra(tmp_path, monkeypatch):
    import api.job_watch as jw

    monkeypatch.setattr(jw, "output_dir", lambda: tmp_path)
    form = {"title": "Post Discord update", "mode": "form"}
    path = write_status(
        "ep-release",
        state="done",
        percent=100,
        label="Uploaded",
        extra={"discord_form": form, "build_id": "42"},
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["state"] == "done"
    assert data["discord_form"]["title"] == "Post Discord update"
    assert data["build_id"] == "42"


def test_status_url_and_write(tmp_path, monkeypatch):
    import api.job_watch as jw

    monkeypatch.setattr(jw, "output_dir", lambda: tmp_path)
    path = write_status("ep-release", state="running", percent=40, label="Compiling")
    assert path.name == "ep-release-status.json"
    text = path.read_text(encoding="utf-8")
    assert '"state": "running"' in text
    assert '"percent": 40' in text
    assert status_url("ep-release") == "/output/ep-release-status.json"


def test_write_status_with_bars(tmp_path, monkeypatch):
    import api.job_watch as jw

    monkeypatch.setattr(jw, "output_dir", lambda: tmp_path)
    bars = [
        {"id": "overall", "label": "Overall", "percent": 72, "kind": "primary", "detail": "188/263"},
        {"id": "kcstower", "label": "kcstower", "percent": 80, "kind": "worker"},
        {"id": "kcslaptop", "label": "kcslaptop", "percent": 64, "kind": "worker"},
    ]
    path = write_status(
        "mesh-bake",
        state="running",
        percent=72,
        label="Mesh bake",
        bars=bars,
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["percent"] == 72
    assert len(data["bars"]) == 3
    assert data["bars"][0]["kind"] == "primary"
    assert data["bars"][1]["kind"] == "worker"
    assert data["bars"][0]["detail"] == "188/263"


def test_sanitize_bars_clamps_and_defaults_kind():
    out = sanitize_bars(
        [
            {"label": "Overall", "percent": 150},
            {"id": "w1", "label": "w1", "percent": -5, "kind": "worker"},
            "skip-me",
            {"percent": "x"},
        ]
    )
    assert out is not None
    assert len(out) == 2
    assert out[0]["kind"] == "primary"
    assert out[0]["percent"] == 100
    assert out[1]["percent"] == 0
    assert out[1]["kind"] == "worker"


def test_stamp_watch_run_binds_started_at():
    watch = stamp_watch_run(
        {"id": "ep-release", "url": "/output/ep-release-status.json"},
        {
            "started_at": "2026-08-19T16:07:00-07:00",
            "action": "build",
            "state": "running",
            "run_id": "abc123",
        },
    )
    assert watch["started_at"] == "2026-08-19T16:07:00-07:00"
    assert watch["action"] == "build"
    assert watch["run_id"] == "abc123"
    assert watch["url"] == "/output/ep-release-status.json"


def test_stamp_watch_run_ignores_terminal_status():
    watch = stamp_watch_run(
        {"id": "ep-release", "url": "/output/ep-release-status.json"},
        {
            "started_at": "2026-08-19T16:07:00-07:00",
            "action": "build",
            "state": "done",
            "percent": 100,
        },
    )
    assert "started_at" not in watch
    assert "action" not in watch


def test_watch_snapshot_from_status_running():
    snap = watch_snapshot_from_status(
        {
            "state": "running",
            "percent": 12,
            "label": "Compiling scripts",
            "started_at": "2026-08-19T16:07:00-07:00",
            "run_id": "abc123",
            "bars": [
                {"id": "overall", "label": "Overall", "percent": 12, "kind": "primary"},
                {"id": "w", "label": "w", "percent": 40, "kind": "worker"},
            ],
        }
    )
    assert snap["state"] == "running"
    assert snap["percent"] == 12
    assert snap["label"] == "Compiling scripts"
    assert snap["run_id"] == "abc123"
    assert snap["bars"][0]["kind"] == "primary"
    assert snap["bars"][1]["id"] == "w"


def test_watch_snapshot_from_status_terminal_is_starting():
    snap = watch_snapshot_from_status(
        {
            "state": "done",
            "percent": 100,
            "label": "Private build 0.8.106",
            "started_at": "2026-08-19T16:07:00-07:00",
        }
    )
    assert snap == {"state": "running", "percent": 0, "label": "Starting…"}


def test_watch_form_spec_stamps_status_run(tmp_path, monkeypatch):
    from api.project_commands import _watch_form_spec_for_command

    monkeypatch.setattr(
        "api.job_watch.read_status_reconciled",
        lambda _id, persist=True: {
            "started_at": "2026-08-19T16:07:00-07:00",
            "action": "deploy",
            "state": "running",
            "percent": 3,
            "label": "Starting /deploy…",
            "run_id": "run-1",
        },
    )
    spec = _watch_form_spec_for_command(
        {
            "name": "deploy",
            "title": "deploy",
            "watch": {"id": "ep-release", "url": "/output/ep-release-status.json"},
        }
    )
    assert spec["watch"]["started_at"] == "2026-08-19T16:07:00-07:00"
    assert spec["watch"]["action"] == "deploy"
    assert spec["watch"]["run_id"] == "run-1"
    assert spec["watch"]["snapshot"]["state"] == "running"
    assert spec["watch"]["snapshot"]["percent"] == 3


def test_watch_form_spec_matches_ui_contract():
    spec = watch_form_spec("trellis-download", title="TRELLIS.2 download")
    assert spec["watch"]["url"] == "/output/trellis-download-status.json"
    assert spec["watch"]["id"] == "trellis-download"
    actions = {o["action"] for o in spec["options"]}
    assert actions == {"__watch_resume__", "__watch_park__", "__watch_cancel__"}


def test_reject_bad_id():
    try:
        sanitize_job_id("../etc/passwd")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_cancel_job_kills_recorded_pids(tmp_path, monkeypatch):
    import api.job_watch as jw

    monkeypatch.setattr(jw, "output_dir", lambda: tmp_path)
    killed = []
    monkeypatch.setattr(jw, "kill_status_pids", lambda pids: killed.extend(list(pids)) or len(pids))
    write_status(
        "ep-release",
        state="running",
        percent=40,
        label="Compiling",
        extra={"pid": 111, "unity_pid": 222},
    )
    assert pids_from_status(read_status("ep-release")) == [111, 222]
    info = cancel_job("ep-release")
    assert info["ok"] is True
    assert killed == [111, 222]
    data = read_status("ep-release")
    assert data["state"] == "failed"
    assert data.get("cancelled") is True
    assert "Cancel" in (data.get("label") or "")


def test_cancel_job_missing_status(tmp_path, monkeypatch):
    import api.job_watch as jw

    monkeypatch.setattr(jw, "output_dir", lambda: tmp_path)
    info = cancel_job("no-such-job")
    assert info["ok"] is False
    assert info.get("error") == "no status"


def test_attach_job_survives_end_run():
    from api import chat_run_registry as reg

    reg._runs.clear()
    reg._session_jobs.clear()
    sid = "job_bind_test"
    reg.begin_run(sid)
    reg.attach_job(sid, "ep-release")
    reg.end_run(sid)
    assert "ep-release" in reg.session_job_ids(sid)
    reg.clear_session_jobs(sid)
    assert reg.session_job_ids(sid) == []
