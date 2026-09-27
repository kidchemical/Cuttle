"""Tests for dynamic <cuttle_action_form> (no-LLM inline action cards)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from api.action_forms import (
    build_runs_from_submission,
    clear_forms_for_tests,
    encode_form_fallback,
    execute_action_form_submission,
    normalize_action_form_spec,
    rewrite_action_forms,
)
from api.project_actions import prepare_assistant_text_for_actions


def _write_action(root: Path, name: str, body: str) -> None:
    d = root / ".cuttle" / "actions"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.yaml").write_text(body, encoding="utf-8")


def test_normalize_preserves_watch(tmp_path: Path):
    spec = normalize_action_form_spec(
        {
            "mode": "choice",
            "title": "Job",
            "watch": {
                "id": "trellis-download",
                "url": "/output/trellis-download-status.json",
                "interval_ms": 4000,
                "done_states": ["done"],
                "resume_message": "Continue.",
            },
            "options": [
                {"id": "continue", "label": "Continue", "action": "__watch_resume__"},
                {"id": "later", "label": "Later", "action": "__watch_park__"},
            ],
        },
        project_path=str(tmp_path),
    )
    assert spec
    assert spec["watch"]["url"] == "/output/trellis-download-status.json"
    assert spec["watch"]["id"] == "trellis-download"

    dropped = normalize_action_form_spec(
        {
            "mode": "choice",
            "watch": {"url": "https://evil.example/x"},
            "options": [{"id": "a", "label": "A", "action": "__watch_park__"}],
        },
        project_path=str(tmp_path),
    )
    assert dropped
    assert "watch" not in dropped


def test_normalize_preserves_watch_run_bind_and_snapshot(tmp_path: Path):
    spec = normalize_action_form_spec(
        {
            "mode": "choice",
            "title": "Job",
            "watch": {
                "id": "ep-release",
                "url": "/output/ep-release-status.json",
                "started_at": "2026-08-19T16:07:00-07:00",
                "action": "build",
                "terminal": True,
                "snapshot": {
                    "state": "done",
                    "percent": 100,
                    "label": "Private build 0.8.100",
                    "started_at": "2026-08-19T16:07:00-07:00",
                    "action": "build",
                },
            },
            "options": [
                {"id": "continue", "label": "Continue", "action": "__watch_resume__"},
            ],
        },
        project_path=str(tmp_path),
    )
    assert spec["watch"]["started_at"] == "2026-08-19T16:07:00-07:00"
    assert spec["watch"]["action"] == "build"
    assert spec["watch"]["terminal"] is True
    assert spec["watch"]["snapshot"]["state"] == "done"
    assert spec["watch"]["snapshot"]["percent"] == 100


def test_merge_watch_snapshot_stamps_started_at():
    from api.action_forms import merge_watch_snapshot_into_spec

    spec = merge_watch_snapshot_into_spec(
        {"watch": {"id": "ep-release", "url": "/output/ep-release-status.json"}},
        snapshot={
            "state": "done",
            "percent": 100,
            "label": "Private build",
            "started_at": "T1",
            "action": "build",
        },
        terminal=True,
        toast="Private build",
    )
    assert spec["watch"]["started_at"] == "T1"
    assert spec["watch"]["action"] == "build"
    assert spec["watch"]["terminal"] is True
    assert spec["toast"] == "Private build"


def test_normalize_choice_spec_merges_content(tmp_path: Path):
    spec = normalize_action_form_spec(
        {
            "mode": "choice",
            "content": "**hi**",
            "options": [
                {
                    "id": "fu",
                    "label": "FU",
                    "action": "discord.post",
                    "params": {"channel": "feature-updates"},
                }
            ],
        },
        project_path=str(tmp_path),
    )
    assert spec
    assert spec["options"][0]["params"]["content"] == "**hi**"
    assert spec["options"][0]["params"]["channel"] == "feature-updates"


def test_rewrite_flask_restart_forms_share_generation_id(tmp_path: Path, monkeypatch):
    """All Flask-restart cards in any chat share flask-restart-gN until restart."""
    clear_forms_for_tests()
    monkeypatch.setenv("CUTTLE_FLASK_GENERATION", "7")
    card = (
        "<cuttle_action_form>\n"
        '{"title":"Restart Flask (daemon-owned)","mode":"choice","options":['
        '{"id":"graceful","label":"Graceful","action":"flask.restart","params":{"mode":"graceful"}}'
        "]}\n"
        "</cuttle_action_form>\n"
    )
    out_a, n_a = rewrite_action_forms(
        card, session_id="db_session_a", project_path=str(tmp_path)
    )
    out_b, n_b = rewrite_action_forms(
        card, session_id="db_session_b", project_path=str(tmp_path)
    )
    assert n_a == 1 and n_b == 1
    assert 'id="flask-restart-g7"' in out_a
    assert 'id="flask-restart-g7"' in out_b
    assert '"restartFormGroup": "flask-restart-g7"' in out_a


def test_flask_restart_soft_ignored_lock_is_not_consumed(tmp_path: Path, monkeypatch):
    """Follow-up 'Ignored' soft-lock must not block a later Graceful click."""
    from api.action_forms import read_action_form_lock_from_history

    form_id = "flask-restart-g14"

    class _Msg:
        def __init__(self, toast: str):
            self._d = {
                "id": 1,
                "content": (
                    f'<cuttle_action_form_pending id="{form_id}" locked="1">'
                    f'{{"locked": true, "toast": "{toast}", "id": "{form_id}"}}'
                    f"</cuttle_action_form_pending>"
                ),
                "metadata": {
                    "action_form_result": {
                        "form_id": form_id,
                        "selected": [],
                        "toast": toast,
                        "locked": True,
                    }
                },
            }

        def get(self, k, default=None):
            return self._d.get(k, default)

    class _DB:
        def __init__(self, toast: str):
            self._toast = toast

        def find_messages_containing(self, *_a, **_k):
            return [_Msg(self._toast)]

    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: _DB("Ignored"))
    assert read_action_form_lock_from_history("db_session_463", form_id) is None

    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: _DB("Restarting Flask…"))
    got = read_action_form_lock_from_history("db_session_463", form_id)
    assert got is not None
    assert "Restarting" in got["toast"]

    # Inline spec poisoned with soft Ignored must still run (not already_locked).
    _write_action(
        tmp_path,
        "flask-restart",
        "name: flask.restart\ntype: shell\nrun: echo hi\nworkdir: .\n",
    )
    spec = normalize_action_form_spec(
        {
            "id": form_id,
            "restartFormGroup": form_id,
            "title": "Restart Flask (daemon-owned)",
            "mode": "choice",
            "lock": "form",
            "silent": True,
            "locked": True,
            "toast": "Ignored",
            "options": [
                {
                    "id": "graceful",
                    "label": "Graceful",
                    "action": "flask.restart",
                    "params": {"mode": "graceful"},
                }
            ],
        },
        project_path=str(tmp_path),
    )
    token = encode_form_fallback(spec)
    monkeypatch.setattr(
        "api.action_forms.read_action_form_lock_from_history",
        lambda *_a, **_k: None,
    )
    with patch("api.action_forms.execute_inline_action") as mock_exec:
        mock_exec.return_value = {
            "success": True,
            "response": "Restarting…",
            "flask_restart": {"restart_id": "abc"},
        }
        res = execute_action_form_submission(
            form_token=token,
            selection={"option": "graceful"},
            session_id="db_session_463",
            form_id_hint=form_id,
        )
    assert res.get("already_locked") is not True
    assert res.get("success") is True
    mock_exec.assert_called()


def test_flask_restart_force_runs_while_waiting_toast_locks_card(tmp_path: Path, monkeypatch):
    """CH-000545-10: Force on a when-idle card must not return already_locked."""
    clear_forms_for_tests()
    form_id = "flask-restart-g11"
    _write_action(
        tmp_path,
        "flask-restart",
        "name: flask.restart\ntype: shell\nrun: echo hi\nworkdir: .\n",
    )
    spec = normalize_action_form_spec(
        {
            "id": form_id,
            "restartFormGroup": form_id,
            "title": "Restart Flask (daemon-owned)",
            "mode": "choice",
            "lock": "form",
            "silent": True,
            "locked": True,
            "toast": "Waiting for 1 active task(s) to finish (CH-000560)…",
            "options": [
                {
                    "id": "force",
                    "label": "Force",
                    "action": "flask.restart",
                    "params": {"mode": "force"},
                }
            ],
        },
        project_path=str(tmp_path),
    )
    token = encode_form_fallback(spec)
    monkeypatch.setattr(
        "api.action_forms.read_action_form_lock_from_history",
        lambda *_a, **_k: {
            "selected": ["when-idle"],
            "toast": "Waiting for 1 active task(s) to finish (CH-000560)…",
        },
    )
    with patch("api.action_forms.execute_inline_action") as mock_exec:
        mock_exec.return_value = {
            "success": True,
            "response": "Force restart…",
            "flask_restart": {"restart_id": "forced"},
        }
        res = execute_action_form_submission(
            form_token=token,
            selection={"option": "force"},
            session_id="db_session_545",
            form_id_hint=form_id,
        )
    assert res.get("already_locked") is not True, res
    assert res.get("success") is True
    mock_exec.assert_called()


def test_rewrite_ignores_prose_tag_mention(tmp_path: Path):
    """Bare tag mentions in prose must not swallow a later real form."""
    clear_forms_for_tests()
    text = (
        "You can emit more than one `<cuttle_action_form>` in a reply.\n\n"
        "<cuttle_action_form>\n"
        '{"title":"Restart Flask (daemon-owned)","mode":"choice","options":['
        '{"id":"graceful","label":"Graceful","action":"flask.restart","params":{"mode":"graceful"}}'
        "]}\n"
        "</cuttle_action_form>\n"
    )
    out, n = rewrite_action_forms(
        text, session_id="db_session_prose", project_path=str(tmp_path)
    )
    assert n == 1
    assert "cuttle_action_form_pending" in out
    assert "Restart Flask (daemon-owned)" in out
    # Prose mention left alone (still visible as text / code).
    assert "`<cuttle_action_form>`" in out
    assert out.index("`<cuttle_action_form>`") < out.index("cuttle_action_form_pending")


def test_rewrite_multiple_forms_in_one_message(tmp_path: Path):
    """Headless retries may append the same card several times — each must rewrite."""
    clear_forms_for_tests()
    card = (
        "<cuttle_action_form>\n"
        '{"title":"Restart Flask","mode":"choice","options":['
        '{"id":"graceful","label":"Graceful","action":"flask.restart","params":{"mode":"graceful"}}'
        "]}\n"
        "</cuttle_action_form>\n"
    )
    text = "Summary text.\n\n" + (card * 3)
    out, n = rewrite_action_forms(
        text, session_id="db_session_multi", project_path=str(tmp_path)
    )
    assert n == 3
    assert out.count('<cuttle_action_form_pending id=') == 3
    assert "<cuttle_action_form>" not in out


def test_rewrite_action_forms_pending(tmp_path: Path):
    clear_forms_for_tests()
    text = (
        "Pick:\n"
        "<cuttle_action_form>\n"
        '{"title":"T","mode":"choice","options":['
        '{"id":"a","label":"A","action":"discord.post","params":{"channel":"feature-updates"}}'
        "]}\n"
        "</cuttle_action_form>\n"
    )
    out, n = rewrite_action_forms(
        text, session_id="db_session_1", project_path=str(tmp_path)
    )
    assert n == 1
    assert "cuttle_action_form_pending" in out
    assert 'id="' in out
    # Body JSON is the durable copy — do NOT stuff the whole spec into
    # fallback="inline.<base64…>" (bloated attrs; client rebuilds from data-spec).
    assert 'fallback="inline.' not in out
    assert '"title": "T"' in out or '"title":"T"' in out


def test_prepare_rewrites_both_confirm_and_form(tmp_path: Path):
    clear_forms_for_tests()
    text = (
        '<cuttle_confirm action="discord.post" channel="feature-updates">x</cuttle_confirm>\n'
        '<cuttle_action_form>{"mode":"choice","options":[{"id":"c","label":"C","action":null}]}'
        "</cuttle_action_form>"
    )
    out = prepare_assistant_text_for_actions(
        text, session_id="db_session_9", project_path=str(tmp_path)
    )
    assert "cuttle_confirm_pending" in out
    assert "cuttle_action_form_pending" in out


def test_choice_submission_runs_action(tmp_path: Path):
    clear_forms_for_tests()
    _write_action(
        tmp_path,
        "discord-post",
        (
            "name: discord.post\n"
            "type: discord.post\n"
            "guild_id: '1'\n"
            "channels:\n"
            "  feature-updates: '111'\n"
        ),
    )
    spec = normalize_action_form_spec(
        {
            "mode": "choice",
            "silent": True,
            "lock": "form",
            "content": "**bug**",
            "options": [
                {
                    "id": "fu",
                    "label": "Feature Updates",
                    "action": "discord.post",
                    "params": {"channel": "feature-updates"},
                }
            ],
        },
        project_path=str(tmp_path),
    )
    token = encode_form_fallback(spec)
    with patch("api.action_forms.execute_inline_action") as mock_exec:
        mock_exec.return_value = {
            "success": True,
            "response": "Posted",
            "url": "https://discord.com/channels/1/111/9",
        }
        res = execute_action_form_submission(
            form_token=token,
            selection={"option": "fu"},
            session_id="db_session_1",
        )
    assert res["success"] is True
    assert res["silent"] is True
    assert res["lock"] == "form"
    mock_exec.assert_called_once()


def test_multi_builds_multiple_runs(tmp_path: Path):
    spec = normalize_action_form_spec(
        {
            "mode": "multi",
            "options": [
                {"id": "a", "label": "A", "action": "shell.a", "params": {}},
                {"id": "b", "label": "B", "action": "shell.b", "params": {}},
            ],
        },
        project_path=str(tmp_path),
    )
    runs = build_runs_from_submission(spec, {"options": ["a", "b"]})
    assert len(runs) == 2
    assert {r["action"] for r in runs} == {"shell.a", "shell.b"}


def test_form_checkboxes_compose_discord_content(tmp_path: Path):
    spec = normalize_action_form_spec(
        {
            "mode": "form",
            "fields": [
                {"id": "title", "type": "text", "value": "**🐛 Ship**"},
                {
                    "id": "tv",
                    "type": "checkbox",
                    "label": "TV loop",
                    "value": True,
                    "line": "• TV waits for the loop clip",
                },
                {
                    "id": "halo",
                    "type": "checkbox",
                    "label": "Halo",
                    "value": True,
                    "line": "• Halo grants after Steam stats load",
                },
                {
                    "id": "skip",
                    "type": "checkbox",
                    "label": "Skip me",
                    "value": True,
                    "line": "• should not post",
                },
                {
                    "id": "channel",
                    "type": "radio",
                    "value": "feature-updates",
                    "options": [
                        {"value": "feature-updates", "label": "Feature Updates"},
                    ],
                },
            ],
            "submit": {
                "action": "discord.post",
                "paramMap": {"channel": "channel"},
            },
        },
        project_path=str(tmp_path),
    )
    runs = build_runs_from_submission(
        spec,
        {
            "fields": {
                "title": "**🐛 Ship**",
                "tv": True,
                "halo": True,
                "skip": False,
                "channel": "feature-updates",
            }
        },
    )
    assert len(runs) == 1
    assert runs[0]["action"] == "discord.post"
    assert runs[0]["params"]["channel"] == "feature-updates"
    content = runs[0]["params"]["content"]
    assert content.startswith("**🐛 Ship**")
    assert "• TV waits for the loop clip" in content
    assert "• Halo grants after Steam stats load" in content
    assert "should not post" not in content


def test_form_line_fields_compose_without_checkbox_type(tmp_path: Path):
    spec = normalize_action_form_spec(
        {
            "mode": "form",
            "fields": [
                {"id": "title", "type": "text", "value": "**👻 Ghost**"},
                {"id": "b1", "label": "Ghost", "line": "• Whisping ghost spawns at the Wendigo"},
                {
                    "id": "channel",
                    "type": "radio",
                    "value": "feature-updates",
                    "options": [{"value": "feature-updates", "label": "FU"}],
                },
            ],
            "submit": {
                "action": "discord.post",
                "paramMap": {"channel": "channel"},
            },
        },
        project_path=str(tmp_path),
    )
    runs = build_runs_from_submission(
        spec,
        {"fields": {"title": "**👻 Ghost**", "b1": True, "channel": "feature-updates"}},
    )
    assert len(runs) == 1
    assert "• Whisping ghost spawns at the Wendigo" in runs[0]["params"]["content"]


def test_form_client_assembled_content_is_kept(tmp_path: Path):
    spec = normalize_action_form_spec(
        {
            "mode": "form",
            "fields": [
                {"id": "title", "type": "text", "value": "**👻 Ghost**"},
                {"id": "channel", "type": "radio", "value": "feature-updates"},
            ],
            "submit": {
                "action": "discord.post",
                "paramMap": {"channel": "channel"},
            },
        },
        project_path=str(tmp_path),
    )
    body = "**👻 Ghost**\n\n• Clouds drop near fireflies"
    runs = build_runs_from_submission(
        spec,
        {
            "fields": {
                "title": "**👻 Ghost**",
                "channel": "feature-updates",
                "content": body,
            }
        },
    )
    assert len(runs) == 1
    assert runs[0]["params"]["content"] == body


def test_form_checkboxes_none_selected_is_empty(tmp_path: Path):
    spec = normalize_action_form_spec(
        {
            "mode": "form",
            "fields": [
                {"id": "title", "type": "text", "value": "**🐛 Ship**"},
                {"id": "a", "type": "checkbox", "label": "A", "line": "• A"},
            ],
            "submit": {"action": "discord.post"},
        },
        project_path=str(tmp_path),
    )
    runs = build_runs_from_submission(spec, {"fields": {"title": "**🐛 Ship**", "a": False}})
    assert runs == []


def test_form_checkbox_without_line_is_blocked(tmp_path: Path):
    """Labels are UI summaries — posting them sent the card's bullets to Discord."""
    spec = normalize_action_form_spec(
        {
            "mode": "form",
            "fields": [
                {"id": "title", "type": "text", "value": "**🎃 Halo**"},
                {"id": "a", "type": "checkbox", "label": "How to open console", "value": True},
                {
                    "id": "b",
                    "type": "checkbox",
                    "label": "Cause",
                    "value": True,
                    "line": "• The 2024 halo never wrote to Steam stats",
                },
            ],
            "submit": {"action": "discord.post", "params": {"channel": "feature-updates"}},
        },
        project_path=str(tmp_path),
    )
    runs = build_runs_from_submission(
        spec, {"fields": {"title": "**🎃 Halo**", "a": True, "b": True}}
    )
    assert len(runs) == 1
    assert runs[0]["action"] is None
    assert "How to open console" in runs[0]["blocked"]


def test_blocked_form_does_not_run_action(tmp_path: Path):
    spec = normalize_action_form_spec(
        {
            "mode": "form",
            "fields": [
                {"id": "a", "type": "checkbox", "label": "Console help", "value": True},
            ],
            "submit": {"action": "discord.post", "params": {"channel": "feature-updates"}},
        },
        project_path=str(tmp_path),
    )
    token = encode_form_fallback(spec)
    with patch("api.action_forms.execute_inline_action") as mock_exec:
        res = execute_action_form_submission(
            form_token=token,
            selection={"fields": {"a": True}},
            session_id="db_session_1",
        )
    mock_exec.assert_not_called()
    assert res["success"] is False
    assert res.get("blocked") is True
    assert "No post text for" in res["toast"]


def test_form_trusts_client_composed_lines(tmp_path: Path):
    """Client that composed from real `line` bodies keeps working when the
    server-side spec lost them (truncated / older payload)."""
    spec = normalize_action_form_spec(
        {
            "mode": "form",
            "fields": [
                {"id": "a", "type": "checkbox", "label": "Cause", "value": True},
            ],
            "submit": {"action": "discord.post", "params": {"channel": "feature-updates"}},
        },
        project_path=str(tmp_path),
    )
    body = "**🎃 Halo**\n\n• The 2024 halo never wrote to Steam stats"
    runs = build_runs_from_submission(
        spec,
        {"fields": {"a": True, "content": body}, "contentSource": "lines"},
    )
    assert len(runs) == 1
    assert runs[0]["action"] == "discord.post"
    assert runs[0]["params"]["content"] == body


def test_cancel_is_silent(tmp_path: Path):
    spec = normalize_action_form_spec(
        {
            "mode": "choice",
            "options": [{"id": "cancel", "label": "Cancel", "action": None}],
        },
        project_path=str(tmp_path),
    )
    token = encode_form_fallback(spec)
    res = execute_action_form_submission(
        form_token=token,
        selection={"cancel": True},
        session_id="x",
    )
    assert res["success"] is True
    assert res["silent"] is True
    assert "Cancelled" in (res.get("toast") or "")


def test_qa_choice_without_action_is_selected_not_cancelled(tmp_path: Path):
    """Preference cards omit action on purpose — must toast Selected, not Cancelled."""
    spec = normalize_action_form_spec(
        {
            "mode": "choice",
            "title": "Follow-up queue drain",
            "options": [
                {"id": "sequential-mixed", "label": "Sequential when agents differ"},
                {"id": "always-sequential", "label": "Always one-by-one"},
                {"id": "leave", "label": "Leave as-is for now"},
            ],
        },
        project_path=str(tmp_path),
    )
    token = encode_form_fallback(spec)
    res = execute_action_form_submission(
        form_token=token,
        selection={"option": "sequential-mixed", "options": ["sequential-mixed"]},
        session_id="x",
    )
    assert res["success"] is True
    assert res["selected"] == ["sequential-mixed"]
    assert res.get("toast") == "Selected: Sequential when agents differ"
    assert "Cancelled" not in (res.get("toast") or "")


def test_invented_none_action_treated_as_qa_pick(tmp_path: Path):
    """Agents inventing action:'none' must not toast Unknown action."""
    spec = normalize_action_form_spec(
        {
            "mode": "choice",
            "title": "Fix oneshot?",
            "options": [
                {"id": "patch", "label": "Yes — patch", "action": "none"},
                {"id": "defer", "label": "Not now", "action": "noop"},
            ],
        },
        project_path=str(tmp_path),
    )
    assert spec is not None
    assert all(o.get("action") is None for o in spec["options"])
    token = encode_form_fallback(spec)
    res = execute_action_form_submission(
        form_token=token,
        selection={"option": "patch", "options": ["patch"]},
        session_id="x",
    )
    assert res["success"] is True
    assert res["selected"] == ["patch"]
    assert res.get("toast") == "Selected: Yes — patch"
    assert "Unknown action" not in (res.get("toast") or "")


def test_qa_choice_mistagged_cancel_flag_still_selects(tmp_path: Path):
    """Legacy client sent cancel:true for every no-action option — honor the id."""
    spec = normalize_action_form_spec(
        {
            "mode": "choice",
            "options": [
                {"id": "sequential-mixed", "label": "Sequential when agents differ"},
                {"id": "cancel", "label": "Cancel", "action": None},
            ],
        },
        project_path=str(tmp_path),
    )
    token = encode_form_fallback(spec)
    res = execute_action_form_submission(
        form_token=token,
        selection={
            "cancel": True,
            "option": "sequential-mixed",
            "options": ["sequential-mixed"],
        },
        session_id="x",
    )
    assert res["success"] is True
    assert res["selected"] == ["sequential-mixed"]
    assert res.get("toast") == "Selected: Sequential when agents differ"


def test_rewrite_stamps_owning_session_into_card(tmp_path: Path):
    """The card must carry its own chat so a click can't be misrouted."""
    clear_forms_for_tests()
    text = (
        "<cuttle_action_form>\n"
        '{"mode":"choice","options":[{"id":"a","label":"A","action":"flask.restart",'
        '"params":{"mode":"when-idle"}}]}\n'
        "</cuttle_action_form>"
    )
    out, n = rewrite_action_forms(
        text, session_id="db_session_134", project_path=str(tmp_path)
    )
    assert n == 1
    assert '"session_id": "db_session_134"' in out or '"session_id":"db_session_134"' in out


def test_submission_runs_in_the_card_session_not_the_client_one(tmp_path: Path):
    """A stale client session must not steer the action's side effects."""
    clear_forms_for_tests()
    spec = normalize_action_form_spec(
        {
            "mode": "choice",
            "session_id": "db_session_134",
            "options": [
                {
                    "id": "when-idle",
                    "label": "When idle",
                    "action": "flask.restart",
                    "params": {"mode": "when-idle"},
                }
            ],
        },
        project_path=str(tmp_path),
    )
    assert spec["session_id"] == "db_session_134"
    _write_action(
        tmp_path,
        "flask-restart",
        "name: flask.restart\ntype: shell\nrun: echo hi\nworkdir: .\n",
    )
    token = encode_form_fallback(spec)
    with patch("api.action_forms.execute_inline_action") as mock_exec:
        mock_exec.return_value = {"success": True, "response": "Restarting Flask…"}
        res = execute_action_form_submission(
            form_token=token,
            selection={"option": "when-idle"},
            session_id="db_session_133",
        )
    assert res["success"] is True
    assert res["session_id"] == "db_session_134"
    assert mock_exec.call_args.kwargs["session_id"] == "db_session_134"


def test_watch_park_locks_without_project_action(tmp_path: Path):
    spec = normalize_action_form_spec(
        {
            "mode": "choice",
            "title": "Job",
            "lock": "form",
            "silent": True,
            "watch": {
                "id": "ep-release",
                "url": "/output/ep-release-status.json",
            },
            "options": [
                {"id": "later", "label": "I'll reply", "action": "__watch_park__"},
            ],
        },
        project_path=str(tmp_path),
    )
    token = encode_form_fallback(spec)
    with patch("api.action_forms.execute_inline_action") as mock_exec:
        res = execute_action_form_submission(
            form_token=token,
            selection={"option": "later"},
            session_id="db_session_194",
        )
    mock_exec.assert_not_called()
    assert res["success"] is True
    assert res["lock"] == "form"
    assert res["selected"] == ["later"]
    assert res["actions"] == ["__watch_park__"]
    assert "Locked" in (res.get("toast") or "")


def test_watch_park_already_locked_from_history(tmp_path: Path):
    spec = normalize_action_form_spec(
        {
            "mode": "choice",
            "lock": "form",
            "watch": {"id": "ep-release", "url": "/output/ep-release-status.json"},
            "options": [
                {"id": "later", "label": "I'll reply", "action": "__watch_park__"},
            ],
        },
        project_path=str(tmp_path),
    )
    token = encode_form_fallback(spec)
    with patch(
        "api.action_forms.read_action_form_lock_from_history",
        return_value={"selected": ["later"], "toast": "Locked — reply here when it's done."},
    ):
        res = execute_action_form_submission(
            form_token=token,
            selection={"option": "continue"},
            session_id="db_session_194",
            form_id_hint="abc123",
        )
    assert res["already_locked"] is True
    assert res["success"] is False
    assert res["selected"] == ["later"]


def test_watch_cancel_stops_job(tmp_path: Path, monkeypatch):
    cancelled = []

    def fake_cancel(job_id):
        cancelled.append(job_id)
        return {"ok": True, "id": job_id}

    monkeypatch.setattr("api.job_watch.cancel_job", fake_cancel)
    spec = normalize_action_form_spec(
        {
            "mode": "choice",
            "lock": "form",
            "watch": {"id": "ep-release", "url": "/output/ep-release-status.json"},
            "options": [
                {"id": "stop", "label": "Stop job", "action": "__watch_cancel__"},
            ],
        },
        project_path=str(tmp_path),
    )
    token = encode_form_fallback(spec)
    res = execute_action_form_submission(
        form_token=token,
        selection={"option": "stop"},
        session_id="db_session_194",
    )
    assert res["success"] is True
    assert cancelled == ["ep-release"]
    assert res["actions"] == ["__watch_cancel__"]
    assert "stopped" in (res.get("toast") or "").lower()


def test_resolve_action_across_projects():
    from api.project_actions import find_project_action_resolved

    # No channel hint → legacy EP preference for Cuttle-chip chats
    action, path = find_project_action_resolved(
        r"C:\Projects\Cuttle\src", "discord.post"
    )
    if action is None:
        pytest.skip("Escape-Purgatory / Epochs dogfood projects not mounted")
    assert "escape-purgatory" in path.lower().replace("_", "-")

    # Channel alias routes to the project that allowlists it
    epochs_action, epochs_path = find_project_action_resolved(
        r"C:\Projects\Cuttle", "discord.post", channel="general"
    )
    assert epochs_action is not None
    assert "epochs" in epochs_path.lower()
    assert "general" in (epochs_action.get("channels") or {})

    ep_action, ep_path = find_project_action_resolved(
        r"C:\Projects\Cuttle", "discord.post", channel="feature-updates"
    )
    assert ep_action is not None
    assert "escape-purgatory" in ep_path.lower().replace("_", "-")
    assert "feature-updates" in (ep_action.get("channels") or {})


def test_qa_form_resume_flag():
    clear_forms_for_tests()
    spec = normalize_action_form_spec(
        {"mode": "choice", "title": "Pick one", "resume": True, "options": [
            {"id": "a", "label": "Alpha"},
            {"id": "b", "label": "Beta"},
            {"id": "c", "label": "Gamma"},
        ]},
        project_path=r"C:\Projects\Cuttle",
    )
    assert spec and spec.get("resume") is True
    token = encode_form_fallback(spec)
    res = execute_action_form_submission(form_token=token, selection={"option": "b"}, session_id="db_session_1")
    assert res["success"] is True
    assert res["resume"] is True
    assert res["selected"] == ["b"]

    # Without resume flag, stays silent
    spec2 = normalize_action_form_spec(
        {"mode": "choice", "title": "Pick", "options": [{"id": "a", "label": "A"}]},
        project_path=r"C:\Projects\Cuttle",
    )
    assert not spec2.get("resume")
    token2 = encode_form_fallback(spec2)
    res2 = execute_action_form_submission(form_token=token2, selection={"option": "a"}, session_id="db_session_1")
    assert not res2.get("resume")


def test_qa_multi_submits_all_picks_and_resumes(tmp_path: Path):
    spec = normalize_action_form_spec(
        {
            "mode": "multi",
            "title": "Which?",
            "resume": True,
            "options": [
                {"id": "a", "label": "Alpha"},
                {"id": "b", "label": "Beta"},
                {"id": "c", "label": "Gamma"},
            ],
        },
        project_path=str(tmp_path),
    )
    assert spec["submitLabel"] == "Submit"
    token = encode_form_fallback(spec)
    res = execute_action_form_submission(
        form_token=token, selection={"options": ["a", "c"]}, session_id="x"
    )
    assert res["success"] is True
    assert res["selected"] == ["a", "c"]
    assert res["toast"] == "Selected: Alpha, Gamma"
    assert res["resume"] is True
    assert res["answer_text"] == "[form-selection] Alpha (a), Gamma (c)"


def test_qa_form_returns_answers_without_line_bodies(tmp_path: Path):
    spec = normalize_action_form_spec(
        {
            "mode": "form",
            "resume": True,
            "fields": [
                {
                    "id": "fix",
                    "label": "Fix?",
                    "type": "radio",
                    "options": [{"value": "yes", "label": "Yes please"}, {"value": "no", "label": "No"}],
                },
                {
                    "id": "areas",
                    "label": "Areas",
                    "type": "checkboxes",
                    "options": [{"value": "ui", "label": "UI"}, {"value": "api", "label": "API"}],
                },
            ],
        },
        project_path=str(tmp_path),
    )
    token = encode_form_fallback(spec)
    res = execute_action_form_submission(
        form_token=token,
        selection={"fields": {"fix": "yes", "areas": ["ui", "api"]}},
        session_id="x",
    )
    assert res["success"] is True
    assert res["resume"] is True
    assert res["answer_text"] == "[form-answers]\n- Fix?: Yes please\n- Areas: UI, API"
    assert "blocked" not in res


def test_qa_form_reports_unanswered_fields(tmp_path: Path):
    """Every question comes back — a skipped field must not silently vanish."""
    spec = normalize_action_form_spec(
        {
            "mode": "form",
            "resume": True,
            "fields": [
                {"id": "a", "label": "Pick one", "type": "radio",
                 "options": [{"value": "x", "label": "X"}]},
                {"id": "b", "label": "Pick any", "type": "checkboxes",
                 "options": [{"value": "y", "label": "Y"}]},
            ],
        },
        project_path=str(tmp_path),
    )
    res = execute_action_form_submission(
        form_token=encode_form_fallback(spec),
        selection={"fields": {"a": "x"}},
        session_id="x",
    )
    assert res["answer_text"] == "[form-answers]\n- Pick one: X\n- Pick any: (none)"


def _form_bodies(out: str) -> list:
    return [
        json.loads(m)
        for m in re.findall(
            r"<cuttle_action_form_pending[^>]*>\s*(\{[\s\S]*?\})\s*</cuttle_action_form_pending>",
            out,
        )
    ]


def test_rewrite_merges_multiple_qa_resume_cards(tmp_path: Path):
    """Two resume cards in one reply → one form; the first click can't orphan the second."""
    clear_forms_for_tests()
    text = (
        "Intro.\n\n"
        '<cuttle_action_form>{"mode":"choice","title":"Trait","resume":true,"options":['
        '{"id":"camo","label":"Camouflage"},{"id":"hearts","label":"Three hearts"}]}'
        "</cuttle_action_form>\n\n"
        '<cuttle_action_form>{"mode":"multi","title":"Features","resume":true,"options":['
        '{"id":"chat","label":"Web chat"},{"id":"workers","label":"Workers"}]}'
        "</cuttle_action_form>\n"
    )
    out, n = rewrite_action_forms(text, session_id="db_session_merge", project_path=str(tmp_path))
    assert n == 1
    assert "Intro." in out
    (spec,) = _form_bodies(out)
    assert spec["mode"] == "form"
    assert spec["resume"] is True
    assert [(f["label"], f["type"]) for f in spec["fields"]] == [
        ("Trait", "radio"),
        ("Features", "checkboxes"),
    ]
    fids = [f["id"] for f in spec["fields"]]
    res = execute_action_form_submission(
        form_token=encode_form_fallback(spec),
        selection={"fields": {fids[0]: "hearts", fids[1]: ["chat", "workers"]}},
        session_id="x",
    )
    assert res["resume"] is True
    assert res["answer_text"] == (
        "[form-answers]\n- Trait: Three hearts\n- Features: Web chat, Workers"
    )


def test_rewrite_merge_keeps_side_effect_cards_separate(tmp_path: Path):
    clear_forms_for_tests()
    qa = (
        '<cuttle_action_form>{"mode":"choice","title":"Q{n}","resume":true,"options":['
        '{"id":"a","label":"A"}]}</cuttle_action_form>\n'
    )
    restart = (
        '<cuttle_action_form>{"mode":"choice","title":"Restart","options":['
        '{"id":"g","label":"Graceful","action":"flask.restart","params":{"mode":"graceful"}}]}'
        "</cuttle_action_form>\n"
    )
    text = qa.replace("{n}", "1") + restart + qa.replace("{n}", "2")
    out, n = rewrite_action_forms(text, session_id="db_session_mix", project_path=str(tmp_path))
    assert n == 2
    specs = _form_bodies(out)
    assert [s["title"] for s in specs] == ["Questions", "Restart"]
    assert [f["label"] for f in specs[0]["fields"]] == ["Q1", "Q2"]


def test_rewrite_single_qa_card_is_untouched(tmp_path: Path):
    clear_forms_for_tests()
    text = (
        '<cuttle_action_form>{"mode":"choice","title":"Only","resume":true,"options":['
        '{"id":"a","label":"A"}]}</cuttle_action_form>'
    )
    out, n = rewrite_action_forms(text, session_id="db_session_one", project_path=str(tmp_path))
    assert n == 1
    (spec,) = _form_bodies(out)
    assert spec["mode"] == "choice"
    assert spec["title"] == "Only"


def test_form_resume_has_a_single_bubble_writer():
    """The run route must not persist the answer; the client resume send owns it."""
    root = Path(__file__).resolve().parents[1]
    api = (root / "api" / "web_chat_api.py").read_text(encoding="utf-8")
    start = api.index("def api_action_form_run")
    route = api[start : api.index("@app.route", start)]
    assert "add_message" not in route
    assert "injected_user_message" in route
    js = (root / "web" / "js" / "chat_page.js").read_text(encoding="utf-8")
    assert "send({ text: String(data.injected_user_message) })" in js
    assert "inp.value = String(data.injected_user_message)" not in js


def test_qa_cancel_does_not_resume(tmp_path: Path):
    spec = normalize_action_form_spec(
        {"mode": "multi", "resume": True, "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]},
        project_path=str(tmp_path),
    )
    token = encode_form_fallback(spec)
    res = execute_action_form_submission(form_token=token, selection={"cancel": True}, session_id="x")
    assert res["success"] is True
    assert res["selected"] == ["cancel"]
    assert res["resume"] is False
