"""Tests for project-local .cuttle/actions and cuttle_confirm flow."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from api.project_actions import (
    clear_pending_for_tests,
    execute_pending_action,
    find_project_action,
    handle_project_action_button,
    list_project_actions,
    parse_project_action_button,
    resolve_action_run,
    rewrite_cuttle_confirms,
    rewrite_posix_shell_recipe,
)


def _write_action(root: Path, name: str, body: str) -> None:
    d = root / ".cuttle" / "actions"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.yaml").write_text(body, encoding="utf-8")


def test_list_and_find_project_actions(tmp_path: Path):
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
    actions = list_project_actions(str(tmp_path))
    assert len(actions) == 1
    assert actions[0]["name"] == "discord.post"
    found = find_project_action(str(tmp_path), "discord.post")
    assert found and found["channels"]["feature-updates"] == "111"


def test_actions_found_from_nested_workspace(tmp_path: Path):
    """Chat chip on …/Cuttle/src should still see …/Cuttle/.cuttle/actions."""
    _write_action(
        tmp_path,
        "flask-restart",
        (
            "name: flask.restart\n"
            "type: shell\n"
            "run: echo ok\n"
            "workdir: .\n"
        ),
    )
    nested = tmp_path / "src"
    nested.mkdir()
    found = find_project_action(str(nested), "flask.restart")
    assert found is not None
    assert found["name"] == "flask.restart"
    assert Path(found["project_path"]).resolve() == tmp_path.resolve()


def test_rewrite_cuttle_confirms_registers_pending(tmp_path: Path):
    clear_pending_for_tests()
    text = (
        "Draft:\n\n"
        '<cuttle_confirm action="discord.post" channel="feature-updates" '
        'confirm_label="Post it" cancel_label="Nope">\n'
        "**🐛 Title**\n\n• One\n"
        "</cuttle_confirm>\n"
    )
    out, n = rewrite_cuttle_confirms(
        text, session_id="db_session_1", project_path=str(tmp_path)
    )
    assert n == 1
    assert "cuttle_confirm_pending" in out
    assert 'confirm_label="Post it"' in out
    assert "<cuttle_confirm " not in out.lower() or "cuttle_confirm_pending" in out
    # Extract id
    import re

    m = re.search(r'cuttle_confirm_pending id="([a-f0-9]+)"', out)
    assert m
    action_id = m.group(1)
    parsed = parse_project_action_button(f"[button:project-action-confirm] {action_id}")
    assert parsed == ("confirm", action_id)


def test_cancel_pending_action(tmp_path: Path):
    clear_pending_for_tests()
    text = (
        '<cuttle_confirm action="discord.post" channel="feature-updates">'
        "hi"
        "</cuttle_confirm>"
    )
    out, n = rewrite_cuttle_confirms(
        text, session_id="sess", project_path=str(tmp_path)
    )
    assert n == 1
    import re

    action_id = re.search(r'id="([a-f0-9]+)"', out).group(1)
    reply = handle_project_action_button(
        f"[button:project-action-cancel] {action_id}",
        session_id="sess",
    )
    assert reply and reply.get("success") is True
    assert "Cancelled" in reply.get("response", "")


def test_discord_post_allowlist_and_mock_http(tmp_path: Path):
    clear_pending_for_tests()
    _write_action(
        tmp_path,
        "discord-post",
        (
            "name: discord.post\n"
            "type: discord.post\n"
            "guild_id: '99'\n"
            "channels:\n"
            "  feature-updates: '555'\n"
        ),
    )
    text = (
        '<cuttle_confirm action="discord.post" channel="feature-updates">'
        "**🔧 Hi**\n\n• x"
        "</cuttle_confirm>"
    )
    out, _ = rewrite_cuttle_confirms(
        text, session_id="sess", project_path=str(tmp_path)
    )
    import re

    action_id = re.search(r'id="([a-f0-9]+)"', out).group(1)

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "msg1"}

    with patch("api.discord_ops.token.load_discord_bot_token", return_value="tok"):
        with patch("requests.post", return_value=mock_resp) as post:
            result = execute_pending_action(action_id, session_id="sess")
    assert result["success"] is True
    assert "Posted" in result["response"]
    assert "msg1" in result["response"]
    assert post.called
    args, kwargs = post.call_args
    assert "555" in args[0]
    assert kwargs["json"]["content"].startswith("**🔧 Hi**")


def test_unknown_channel_rejected(tmp_path: Path):
    clear_pending_for_tests()
    _write_action(
        tmp_path,
        "discord-post",
        (
            "name: discord.post\n"
            "type: discord.post\n"
            "channels:\n"
            "  feature-updates: '555'\n"
        ),
    )
    text = (
        '<cuttle_confirm action="discord.post" channel="not-a-channel">'
        "x"
        "</cuttle_confirm>"
    )
    out, _ = rewrite_cuttle_confirms(
        text, session_id="sess", project_path=str(tmp_path)
    )
    import re

    action_id = re.search(r'id="([a-f0-9]+)"', out).group(1)
    with patch("api.discord_ops.token.load_discord_bot_token", return_value="tok"):
        result = execute_pending_action(action_id, session_id="sess")
    assert result["success"] is False
    assert "Unknown Discord channel" in result["response"]


def test_discord_post_rejects_arbitrary_numeric_channel(tmp_path: Path):
    clear_pending_for_tests()
    _write_action(
        tmp_path,
        "discord-post",
        (
            "name: discord.post\n"
            "type: discord.post\n"
            "channels:\n"
            "  feature-updates: '555'\n"
        ),
    )
    text = (
        '<cuttle_confirm action="discord.post" channel="999000111222">'
        "x"
        "</cuttle_confirm>"
    )
    out, _ = rewrite_cuttle_confirms(
        text, session_id="sess", project_path=str(tmp_path)
    )
    import re

    action_id = re.search(r'id="([a-f0-9]+)"', out).group(1)
    with patch("api.discord_ops.token.load_discord_bot_token", return_value="tok"):
        with patch("requests.post") as post:
            result = execute_pending_action(action_id, session_id="sess")
    assert result["success"] is False
    assert "Unknown Discord channel" in result["response"]
    assert not post.called


def test_inline_confirm_payload_roundtrip(tmp_path: Path):
    from api.project_actions import (
        encode_inline_action_payload,
        decode_inline_action_payload,
    )

    _write_action(
        tmp_path,
        "discord-post",
        (
            "name: discord.post\n"
            "type: discord.post\n"
            "guild_id: '1'\n"
            "channels:\n"
            "  prompt-lab: '222'\n"
        ),
    )
    token = encode_inline_action_payload(
        action_name="discord.post",
        project_path=str(tmp_path),
        params={"content": "**🧪 test**", "channel": "prompt-lab"},
    )
    assert token.startswith("inline.")
    decoded = decode_inline_action_payload(token)
    assert decoded and decoded["action"] == "discord.post"
    assert decoded["params"]["channel"] == "prompt-lab"

    with patch("api.project_actions._execute_discord_post") as mock_post:
        mock_post.return_value = {
            "success": True,
            "response": "Posted",
            "url": "https://discord.com/channels/1/222/9",
        }
        res = handle_project_action_button(
            f"[button:project-action-confirm] {token}",
            session_id="db_session_1",
        )
    assert res and res["success"] is True
    mock_post.assert_called_once()


def _shell_action(root: Path) -> None:
    _write_action(
        root,
        "flask-restart",
        (
            "name: flask.restart\n"
            "type: shell\n"
            "run: echo ok\n"
            "workdir: .\n"
        ),
    )


def _patched_run():
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = "ok"
    proc.stderr = ""
    return patch("api.project_actions.subprocess.run", return_value=proc)


def test_shell_action_exports_params_and_session(tmp_path: Path):
    from api.project_actions import encode_inline_action_payload, execute_inline_action

    _shell_action(tmp_path)
    token = encode_inline_action_payload(
        action_name="flask.restart",
        project_path=str(tmp_path),
        params={"mode": "when-idle", "confirm": True, "retry count": 2},
    )
    with _patched_run() as run:
        res = execute_inline_action(token, session_id="db_session_7")
    assert res["success"] is True
    env = run.call_args.kwargs["env"]
    assert env["CUTTLE_PARAM_MODE"] == "when-idle"
    assert env["CUTTLE_PARAM_CONFIRM"] == "true"
    assert env["CUTTLE_PARAM_RETRY_COUNT"] == "2"
    assert env["CUTTLE_SESSION_ID"] == "db_session_7"


def test_action_form_click_passes_mode_to_shell(tmp_path: Path):
    from api.action_forms import execute_action_form_submission, encode_form_fallback
    from api.action_forms import normalize_action_form_spec

    _shell_action(tmp_path)
    spec = normalize_action_form_spec(
        {
            "mode": "choice",
            "options": [
                {
                    "id": "idle",
                    "label": "Restart when idle",
                    "action": "flask.restart",
                    "params": {"mode": "when-idle"},
                }
            ],
        },
        project_path=str(tmp_path),
    )
    with _patched_run() as run:
        res = execute_action_form_submission(
            form_token=encode_form_fallback(spec),
            selection={"option": "idle"},
            session_id="sess",
        )
    assert res["success"] is True
    env = run.call_args.kwargs["env"]
    assert env["CUTTLE_PARAM_MODE"] == "when-idle"
    assert env["CUTTLE_SESSION_ID"] == "sess"


def test_gitea_issue_comment_mock(tmp_path: Path):
    clear_pending_for_tests()
    _write_action(
        tmp_path,
        "gitea-issue",
        (
            "name: gitea.issue\n"
            "type: gitea.issue\n"
            "repos:\n"
            "  ep: acme/demo-game\n"
        ),
    )
    text = (
        '<cuttle_confirm action="gitea.issue" repo="ep" issue="12" labels_add="in-progress">'
        "Starting work on spawn loop."
        "</cuttle_confirm>"
    )
    out, _ = rewrite_cuttle_confirms(
        text, session_id="sess", project_path=str(tmp_path)
    )
    import re

    action_id = re.search(r'id="([a-f0-9]+)"', out).group(1)

    with patch("api.gitea_client.add_issue_comment") as comment:
        with patch("api.gitea_client.add_issue_labels") as labels:
            with patch(
                "api.gitea_client.issue_web_url",
                return_value="http://gitea/acme/demo-game/issues/12",
            ):
                result = execute_pending_action(action_id, session_id="sess")
    assert result["success"] is True
    assert "Gitea issue updated" in result["response"]
    comment.assert_called_once_with(
        "acme", "demo-game", 12, "Starting work on spawn loop."
    )
    labels.assert_called_once_with(
        "acme", "demo-game", 12, ["in-progress"]
    )


def test_rewrite_includes_signed_inline_fallback(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CUTTLE_ACTION_HMAC_SECRET", "confirm-hmac-test")
    import api.project_actions as pa

    pa._hmac_secret_cache = None
    clear_pending_for_tests()
    text = (
        '<cuttle_confirm action="discord.post" channel="feature-updates">'
        "hi"
        "</cuttle_confirm>"
    )
    out, n = rewrite_cuttle_confirms(
        text, session_id="db_session_3", project_path=str(tmp_path)
    )
    assert n == 1
    assert 'fallback="inline.' in out
    import re

    from api.project_actions import decode_inline_action_payload

    fb = re.search(r'fallback="([^"]+)"', out)
    assert fb
    decoded = decode_inline_action_payload(fb.group(1))
    assert decoded and decoded["action"] == "discord.post"
    assert decoded["session_id"] == "db_session_3"


def test_unsigned_inline_does_not_override_pending_id(tmp_path: Path):
    unsigned = "inline." + __import__("base64").urlsafe_b64encode(
        b'{"action":"flask.restart","params":{}}'
    ).decode("ascii").rstrip("=")
    parsed = parse_project_action_button(
        f"[button:project-action-confirm] abcdef123456 {unsigned}"
    )
    assert parsed == ("confirm", "abcdef123456")


def test_confirm_recovers_hmac_from_history_after_pending_flush(tmp_path, monkeypatch):
    from unittest.mock import patch

    from api import auth_db as auth_db_mod
    from api.project_actions import clear_pending_for_tests, handle_project_action_button

    monkeypatch.setenv("CUTTLE_ACTION_HMAC_SECRET", "confirm-hist-hmac")
    import api.project_actions as pa

    pa._hmac_secret_cache = None
    db_path = tmp_path / "confirm.db"
    monkeypatch.setattr(auth_db_mod, "DB_PATH", db_path)
    auth_db_mod._db_instance = None
    db = auth_db_mod.AuthDatabase(db_path)
    auth_db_mod._db_instance = db
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    owner = db.create_user("o@x", "O", "local", password="x")
    sid = db.create_chat_session(owner, "c")
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
    clear_pending_for_tests()
    text = (
        '<cuttle_confirm action="discord.post" channel="feature-updates">'
        "hello"
        "</cuttle_confirm>"
    )
    out, n = rewrite_cuttle_confirms(
        text, session_id=f"db_session_{sid}", project_path=str(tmp_path)
    )
    assert n == 1
    db.add_message(sid, "assistant", out)
    clear_pending_for_tests()
    import re

    action_id = re.search(r'id="([a-f0-9]+)"', out).group(1)
    unsigned = "inline." + __import__("base64").urlsafe_b64encode(
        b'{"action":"flask.restart","project_path":"/evil","params":{}}'
    ).decode("ascii").rstrip("=")
    with patch("api.project_actions._execute_discord_post") as mock_post:
        mock_post.return_value = {"success": True, "response": "Posted", "url": "u"}
        res = handle_project_action_button(
            f"[button:project-action-confirm] {action_id} {unsigned}",
            session_id=f"db_session_{sid}",
        )
    assert res and res["success"] is True
    mock_post.assert_called_once()
    assert mock_post.call_args[0][1].get("channel") == "feature-updates"


def test_rewrite_posix_shell_recipe_maps_powershell_flask_restart():
    repo = Path(__file__).resolve().parents[2]
    py = repo / ".venv" / "bin" / "python3"
    if not py.is_file():
        py = Path("/usr/bin/python3")
    out = rewrite_posix_shell_recipe(
        r"powershell -NoProfile -ExecutionPolicy Bypass -File .cuttle\scripts\restart-flask.ps1",
        repo,
        py,
    )
    assert "powershell" not in out.lower()
    assert "restart-flask.py" in out
    assert str(py) in out or py.name in out


def test_resolve_action_run_prefers_run_posix(tmp_path: Path):
    _write_action(
        tmp_path,
        "demo",
        (
            "name: demo.shell\n"
            "type: shell\n"
            "run: powershell -File nope.ps1\n"
            "run_posix: echo posix-ok\n"
            "workdir: .\n"
        ),
    )
    action = find_project_action(str(tmp_path), "demo.shell")
    assert action is not None
    recipe = resolve_action_run(action)
    assert recipe is not None
    assert "posix-ok" in recipe
    assert "powershell" not in recipe.lower()


def test_project_actions_source_has_no_lab_checkout_paths():
    src = Path(__file__).resolve().parents[1] / "api" / "project_actions.py"
    text = src.read_text(encoding="utf-8")
    assert "Escape-Purgatory" not in text
    assert "E:\\Game Dev" not in text
    assert "E:/Game Dev" not in text
