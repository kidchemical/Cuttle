"""Secret vault: core (redaction, delivery, askpass, backends), service
(metadata, approvals, scope), agent CLI, HTTP routes, and chat-form answers.

Values must never surface: CLI output is redacted, routes never return a
value, and form answers only say a value was stored.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from api.experimental import flags
from api.secret_vault import __main__ as cli
from api.secret_vault import cards, service
from api.secret_vault.core import askpass, backends, deliver
from api.secret_vault.core.redact import Redactor

PY = sys.executable
SECRET = "hunter2-Correct-Horse"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CUTTLE_EXPERIMENTAL", raising=False)
    monkeypatch.delenv("CUTTLE_CHAT_SESSION_ID", raising=False)
    service.reset_backend_cache()
    yield
    service.reset_backend_cache()


@pytest.fixture()
def vault():
    """Enabled flag + an in-process memory backend as the default store."""
    flags.set_enabled("secret_vault", True)
    with service._state(write=True) as data:
        data["backends"].append({"id": "mem", "kind": "memory", "label": "Memory", "options": {}})
        data["config"]["default_backend"] = "mem"
    yield
    flags.set_enabled("secret_vault", False)


# ── core: redaction ────────────────────────────────────────────────────────


def test_redactor_replaces_values_across_chunk_boundaries():
    r = Redactor({"pw": SECRET})
    data = f"before {SECRET} after {SECRET}".encode()
    out = b"".join(r.feed(data[i:i + 3]) for i in range(0, len(data), 3)) + r.flush()
    assert out == b"before [secret:pw] after [secret:pw]"


def test_redactor_catches_url_and_base64_forms_and_skips_tiny_values():
    import base64
    import urllib.parse
    value = "p@ss w0rd/xyz"
    r = Redactor({"pw": value, "tiny": "ab"})
    text = (f"{urllib.parse.quote(value, safe='')} "
            f"{base64.b64encode(value.encode()).decode()} ab")
    out = r.text(text)
    assert value not in out and "[secret:pw]" in out
    assert out.endswith(" ab") and r.skipped == ["tiny"]


# ── core: planning ─────────────────────────────────────────────────────────


def test_parse_spec_and_plan_validation():
    assert deliver.parse_spec("env", "API_KEY=openai") == deliver.Injection("env", "openai", var="API_KEY")
    assert deliver.parse_spec("askpass", "gh=Password") == deliver.Injection("askpass", "gh", match="Password")
    with pytest.raises(ValueError):
        deliver.parse_spec("env", "openai")
    with pytest.raises(ValueError):
        deliver.parse_spec("env", "1BAD=x")
    with pytest.raises(ValueError):
        deliver.parse_spec("stdin", "../etc")
    with pytest.raises(ValueError, match="prompt regex"):
        deliver.plan(["ssh", "h"], [deliver.Injection("askpass", "a"), deliver.Injection("askpass", "b")])
    with pytest.raises(ValueError):
        deliver.plan([], [deliver.Injection("env", "a", var="X")])


def test_plan_adds_sudo_askpass_flag_once():
    inj = [deliver.Injection("askpass", "root")]
    assert deliver.plan(["sudo", "ls"], inj, platform="linux").argv == ["sudo", "-A", "ls"]
    assert deliver.plan(["sudo", "-S", "ls"], inj, platform="linux").argv == ["sudo", "-S", "ls"]
    assert deliver.plan(["ls"], inj, platform="linux").argv == ["ls"]


# ── core: askpass rule choice ──────────────────────────────────────────────


@pytest.mark.parametrize("prompt,expected", [
    ("deploy@prod's password: ", "V0"),
    ("Enter passphrase for key '/home/u/.ssh/id_ed25519': ", "V0"),
    ("[sudo] password for u: ", "V0"),
    ("Are you sure you want to continue connecting (yes/no/[fingerprint])? ", None),
    ("Username for 'https://github.com': ", None),
])
def test_askpass_default_rule_refuses_confirmations_and_usernames(prompt, expected):
    rule = askpass.choose_rule(prompt, [{"match": "", "var": "V0"}])
    assert (rule or {}).get("var") == expected


def test_askpass_explicit_rules_win():
    rules = [{"match": "Username", "var": "U"}, {"match": "Password", "var": "P"}]
    assert askpass.choose_rule("Username for 'https://x':", rules)["var"] == "U"
    assert askpass.choose_rule("Password for 'https://u@x':", rules)["var"] == "P"
    assert askpass.choose_rule("Something else", rules) is None


# ── core: end-to-end delivery ──────────────────────────────────────────────


def _run(plan, values, tmp_path, **kw):
    out, err = tmp_path / "out.bin", tmp_path / "err.bin"
    with out.open("wb") as o, err.open("wb") as e:
        code = deliver.run(plan, values.__getitem__, stdout=o, stderr=e, **kw)
    return code, out.read_text(), err.read_text()


def test_env_file_and_stdin_delivery_with_redaction(tmp_path):
    script = textwrap.dedent("""
        import os, sys
        print("env", os.environ["TOKEN"])
        path = os.environ["KEYFILE"]
        print("file", open(path).read(), oct(os.stat(path).st_mode & 0o777))
        print("stdin", sys.stdin.readline().strip())
        print("argv-clean", all("hunter" not in a for a in sys.argv))
    """)
    p = deliver.plan([PY, "-c", script], [
        deliver.Injection("env", "tok", var="TOKEN"),
        deliver.Injection("file", "key", var="KEYFILE"),
        deliver.Injection("stdin", "pin"),
    ])
    code, out, _ = _run(p, {"tok": SECRET, "key": "KEY-MATERIAL-123", "pin": "9876-pin"}, tmp_path)
    assert code == 0
    assert SECRET not in out and "KEY-MATERIAL-123" not in out and "9876-pin" not in out
    assert "env [secret:tok]" in out and "file [secret:key]" in out and "stdin [secret:pin]" in out
    if os.name != "nt":
        assert "0o600" in out
    assert "argv-clean True" in out


def test_redaction_can_be_disabled(tmp_path):
    p = deliver.plan([PY, "-c", "import os;print(os.environ['T'])"], [deliver.Injection("env", "t", var="T")])
    _, out, _ = _run(p, {"t": SECRET}, tmp_path, redact=False)
    assert out.strip() == SECRET


def test_temp_files_are_removed_after_run(tmp_path):
    p = deliver.plan([PY, "-c", "import os;print(os.environ['F'])"], [deliver.Injection("file", "k", var="F")])
    _, out, _ = _run(p, {"k": SECRET}, tmp_path, redact=False)
    assert not Path(out.strip()).exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX askpass wrapper")
def test_askpass_answers_password_prompt_and_refuses_hostkey(tmp_path):
    fake_ssh = tmp_path / "fake-ssh"
    fake_ssh.write_text(textwrap.dedent(f"""\
        #!{PY}
        import os, subprocess
        ask = os.environ["SSH_ASKPASS"]
        assert os.environ["SSH_ASKPASS_REQUIRE"] == "force"
        host = subprocess.run([ask, "Are you sure you want to continue connecting (yes/no)? "],
                              capture_output=True, text=True)
        print("hostkey-exit", host.returncode, repr(host.stdout))
        pw = subprocess.run([ask, "deploy@prod's password: "], capture_output=True, text=True)
        print("password", pw.stdout.strip())
        for _ in range(3):
            again = subprocess.run([ask, "deploy@prod's password: "], capture_output=True, text=True)
        print("after-limit", again.returncode, repr(again.stdout))
    """))
    fake_ssh.chmod(fake_ssh.stat().st_mode | stat.S_IXUSR)
    p = deliver.plan([str(fake_ssh)], [deliver.Injection("askpass", "prod")])
    code, out, _ = _run(p, {"prod": SECRET}, tmp_path, askpass_max=3)
    assert code == 0, out
    assert "hostkey-exit 1 ''" in out
    assert "password [secret:prod]" in out
    assert "after-limit 1 ''" in out
    assert SECRET not in out


def test_missing_command_returns_127(tmp_path):
    p = deliver.plan(["definitely-not-a-real-cmd-xyz"], [deliver.Injection("env", "a", var="A")])
    code, _, err = _run(p, {"a": SECRET}, tmp_path)
    assert code == 127 and "command not found" in err


# ── core: backends ─────────────────────────────────────────────────────────


def test_command_backend_round_trip_with_a_fake_manager(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    mgr = tmp_path / "mgr.py"
    mgr.write_text(textwrap.dedent(f"""\
        import sys, pathlib
        root = pathlib.Path({str(store)!r})
        verb, ref = sys.argv[1], sys.argv[2]
        f = root / ref
        if verb == "get":
            if not f.exists():
                sys.stderr.write("not found\\n"); sys.exit(1)
            sys.stdout.write(f.read_text() + "\\nnotes line\\n")
        elif verb == "set":
            f.write_text(sys.stdin.read().rstrip("\\n"))
        elif verb == "rm":
            f.unlink(missing_ok=True)
    """))
    b = backends.CommandBackend("fake", get=[PY, str(mgr), "get", "{ref}"],
                                set=[PY, str(mgr), "set", "{ref}"],
                                delete=[PY, str(mgr), "rm", "{ref}"], first_line=True)
    assert b.available()[0] and b.writable
    b.set("db", SECRET)
    assert b.get("db") == SECRET
    b.delete("db")
    with pytest.raises(backends.BackendError, match="not found"):
        b.get("db")


def test_read_only_command_backend_and_presets():
    b = backends.CommandBackend.from_preset("op", "1password")
    assert not b.writable
    with pytest.raises(backends.BackendError, match="read-only"):
        b.set("x", "y")
    assert {"pass", "secret-tool", "1password", "bitwarden", "keepassxc"} <= set(backends.COMMAND_PRESETS)
    for p in backends.COMMAND_PRESETS.values():
        assert any("{ref}" in part for part in p.get)


def test_build_backend_rejects_unknown_kind():
    with pytest.raises(ValueError):
        backends.build_backend({"id": "x", "kind": "nope"})
    assert isinstance(backends.build_backend({"id": "k", "kind": "keyring"}), backends.KeyringBackend)


# ── service ────────────────────────────────────────────────────────────────


def test_flag_registered_default_off():
    spec = flags.get_flag("secret_vault")
    assert spec is not None and spec.default is False and spec.context_bundle
    assert not service.is_enabled()
    with pytest.raises(service.VaultError, match="Experimental"):
        service.require_enabled()


def test_metadata_file_never_contains_values(vault):
    service.put_secret("prod-ssh", value=SECRET, description="ssh pw")
    raw = service.vault_path().read_text()
    assert SECRET not in raw
    assert service.resolve_value("prod-ssh") == SECRET
    row = service.get_secret("prod-ssh")
    assert row["stored"] and row["backend"] == "mem" and "value" not in row


def test_delete_removes_value_and_grants(vault):
    service.put_secret("a", value=SECRET, grant_session="CH-000042")
    assert service.list_grants("42")
    service.delete_secret("a")
    assert service.get_secret("a") is None and not service.list_grants()
    with pytest.raises(service.VaultError):
        service.resolve_value("a")


def test_per_chat_policy_and_once_grants(vault):
    service.put_secret("a", value=SECRET)
    assert service.missing_approvals("42", ["a"]) == ["a"]
    assert service.missing_approvals("", ["a"]) == ["a"]
    service.grant("CH-000042", ["a"], mode="once")
    assert service.missing_approvals("42", ["a"]) == []
    service.consume_once("42", ["a"])
    assert service.missing_approvals("42", ["a"]) == ["a"]
    service.grant("42", ["a"], mode="chat")
    service.consume_once("42", ["a"])
    assert service.missing_approvals("42", ["a"]) == []
    assert service.missing_approvals("43", ["a"]) == ["a"]


def test_every_use_and_never_policies(vault):
    service.put_secret("a", value=SECRET)
    service.update_config({"approval": "every_use"})
    service.grant("42", ["a"], mode="chat")
    assert service.missing_approvals("42", ["a"]) == ["a"]
    service.grant("42", ["a"], mode="once")
    assert service.missing_approvals("42", ["a"]) == []
    service.update_config({"approval": "never"})
    assert service.missing_approvals("", ["a"]) == []
    with pytest.raises(service.VaultError):
        service.update_config({"approval": "sometimes"})


def test_project_scope(vault, tmp_path):
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    service.put_secret("a", value=SECRET, project=str(proj))
    assert service.scope_violations(["a"], str(proj / "sub")) == []
    assert service.scope_violations(["a"], str(tmp_path)) == [("a", str(proj))]


def test_backend_config_presets_and_guards(vault):
    service.save_backend({"id": "pw", "kind": "command", "preset": "pass"})
    rows = {b["id"]: b for b in service.list_backends()}
    assert rows["pw"]["options"]["get"][:2] == ["pass", "show"] and rows["pw"]["writable"]
    assert rows["keyring"]["builtin"]
    with pytest.raises(service.VaultError):
        service.save_backend({"id": "bad", "kind": "command", "options": {"get": "pass show"}})
    with pytest.raises(service.VaultError):
        service.delete_backend("keyring")
    service.put_secret("x", backend="mem", value=SECRET)
    with pytest.raises(service.VaultError, match="still holds"):
        service.delete_backend("mem")
    service.delete_backend("pw")


# ── CLI ────────────────────────────────────────────────────────────────────


def test_cli_refuses_when_flag_off(capfd):
    assert cli.main(["list"]) == cli.EXIT_USAGE
    assert "Experimental" in capfd.readouterr().err


def test_cli_has_no_value_printing_verb():
    verbs = set(cli.build_parser()._subparsers._group_actions[0].choices)
    assert verbs == {"status", "list", "request", "run", "grants"}


def test_cli_run_missing_secret_prints_request_card(vault, capfd, monkeypatch):
    monkeypatch.setenv("CUTTLE_CHAT_SESSION_ID", "CH-000042")
    code = cli.main(["run", "--env", "T=newkey", "--", PY, "-c", "pass"])
    out = capfd.readouterr().out
    assert code == cli.EXIT_MISSING
    spec = json.loads(out.split("<cuttle_action_form>")[1].split("</cuttle_action_form>")[0])
    assert spec["fields"][0] == {"id": "secret_newkey", "type": "secret", "secret": "newkey",
                                 "label": "Value for newkey", "required": True}
    assert spec["resume"] and spec["session_id"] == "42"


def test_cli_run_needs_approval_then_runs_redacted(vault, capfd, monkeypatch):
    monkeypatch.setenv("CUTTLE_CHAT_SESSION_ID", "CH-000042")
    service.put_secret("api", value=SECRET)
    argv = ["run", "--env", "API_KEY=api", "--", PY, "-c", "import os;print('key', os.environ['API_KEY'])"]
    assert cli.main(argv) == cli.EXIT_APPROVAL
    out = capfd.readouterr().out
    spec = json.loads(out.split("<cuttle_action_form>")[1].split("</cuttle_action_form>")[0])
    field = spec["fields"][0]
    assert field["type"] == "secret_approval" and field["secrets"] == ["api"]
    assert "API_KEY" in spec["description"] and SECRET not in out
    service.grant("42", ["api"], mode="chat")
    assert cli.main(argv) == 0
    captured = capfd.readouterr()
    assert "key [secret:api]" in captured.out and SECRET not in captured.out + captured.err
    assert service.get_secret("api")["use_count"] == 1


def test_cli_run_without_session_and_no_tty_refuses(vault, capfd, monkeypatch):
    service.put_secret("api", value=SECRET)
    monkeypatch.setattr(sys, "stdin", open(os.devnull))
    assert cli.main(["run", "--env", "K=api", "--", PY, "-c", "pass"]) == cli.EXIT_APPROVAL
    assert "no chat session" in capfd.readouterr().err


def test_cli_list_and_request(vault, capfd):
    service.put_secret("api", value=SECRET, description="OpenAI")
    assert cli.main(["list", "--json"]) == 0
    rows = json.loads(capfd.readouterr().out)
    assert rows[0]["name"] == "api" and SECRET not in json.dumps(rows)
    assert cli.main(["request", "db", "--label", "db=Postgres password"]) == 0
    assert '"label": "Postgres password"' in capfd.readouterr().out
    assert cli.main(["request", "../bad"]) == cli.EXIT_USAGE


# ── chat form answers ──────────────────────────────────────────────────────


def test_normalized_secret_fields_keep_their_keys_and_drop_prefill():
    from api.action_forms import normalize_action_form_spec, qa_form_answers
    spec = normalize_action_form_spec({
        "title": "t", "resume": True,
        "fields": [{"id": "s", "type": "secret", "secret": "db", "value": "leak", "label": "DB"},
                   {"id": "a", "type": "secret_approval", "secrets": ["db"], "command": "psql"}]})
    s, a = spec["fields"]
    assert s["secret"] == "db" and s["value"] == ""
    assert a["secrets"] == ["db"] and a["command"] == "psql"
    answers = dict(qa_form_answers(spec, {"s": "stored", "a": "granted_once"}))
    assert answers == {"DB": "saved to the vault as secret:db", "a": "allowed once (db)"}
    # A raw value that slipped through is never echoed to the agent.
    assert dict(qa_form_answers(spec, {"s": SECRET, "a": SECRET})) == {
        "DB": "(not provided)", "a": "(no answer)"}


def test_cards_render_round_trip():
    spec = cards.approval_card(["a"], ["ssh", "u@h"], session="7", deliveries=["prompts ← a"])
    text = cards.render(spec)
    assert text.startswith("<cuttle_action_form>") and "ssh u@h" in text


# ── routes ─────────────────────────────────────────────────────────────────


def _client(tmp_path, monkeypatch):
    from tests.auth.test_http_authz import _auth_client
    ctx = _auth_client(tmp_path, monkeypatch)
    ctx["client"].set_cookie("session_token", ctx["token"])
    return ctx


def test_routes_disabled_when_flag_off(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)["client"]
    resp = client.get("/api/secret-vault/secrets")
    assert resp.status_code == 403 and resp.get_json()["disabled"]
    assert client.get("/secrets_page.html").status_code == 403
    assert client.put("/api/secret-vault/secrets/x", json={"value": SECRET}).status_code == 403


def test_routes_require_owner(tmp_path, monkeypatch, vault):
    ctx = _client(tmp_path, monkeypatch)
    ctx["client"].set_cookie("session_token", ctx["other_token"])
    assert ctx["client"].get("/api/secret-vault/secrets").status_code == 403
    assert ctx["client"].put("/api/secret-vault/secrets/x", json={"value": SECRET}).status_code == 403


def test_routes_store_grant_and_never_return_values(tmp_path, monkeypatch, vault):
    ctx = _client(tmp_path, monkeypatch)
    client, sid = ctx["client"], ctx["sid"]
    put = client.put("/api/secret-vault/secrets/db", json={"value": SECRET, "session_id": str(sid)})
    assert put.status_code == 200 and SECRET not in put.get_data(as_text=True)
    assert service.resolve_value("db") == SECRET
    assert service.missing_approvals(str(sid), ["db"]) == []
    for path in ("/api/secret-vault/secrets", "/api/secret-vault/status", "/api/secret-vault/grants"):
        body = client.get(path)
        assert body.status_code == 200 and SECRET not in body.get_data(as_text=True)
    assert client.put("/api/secret-vault/secrets/..", json={"value": "x"}).status_code in (400, 404)
    assert client.put("/api/secret-vault/secrets/db", json={"value": ""}).status_code == 400
    granted = client.post("/api/secret-vault/grants", json={"session_id": "99", "secrets": ["db"], "mode": "once"})
    assert granted.status_code == 200
    assert client.delete("/api/secret-vault/grants?session_id=99").get_json()["removed"] == 1
    assert client.put("/api/secret-vault/config", json={"approval": "every_use"}).get_json()["config"]["approval"] == "every_use"
    assert client.delete("/api/secret-vault/secrets/db").status_code == 200
    assert client.get("/secrets_page.html").status_code == 200
