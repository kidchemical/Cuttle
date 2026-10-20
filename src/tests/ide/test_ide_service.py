"""IDE App backend: workspace confinement, file semantics, search, git, HTTP gate.

Layers are tested separately so a failure names the layer:

* service — path confinement (``..``, absolute, symlink escape, ``.git``),
  read/write with etag conflicts and encodings, entries, list/search/replace,
  committed content for the diff editor, completion post-processing
* routes  — flag gate (403 disabled), owner-only access, conflict payloads
* flag    — registered default-off; the kill switch disables it
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from api.experimental import flags
from api.ide import service

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_bytes(b"def hello():\n    return 'hi'\n")
    (root / "README.md").write_bytes(b"# Demo\nhello world\n")
    (root / "build").mkdir()
    (root / "build" / "out.txt").write_text("hello build\n", encoding="utf-8")
    (root / ".gitignore").write_text("build/\n", encoding="utf-8")
    if shutil.which("git"):
        _git(root, "init", "-q")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "init")
    project = {"id": 7, "name": "Demo", "path": str(root), "available": True}
    monkeypatch.setattr(service, "_registered", lambda: ([project], project))
    return root.resolve()


# ---------------------------------------------------------------------------
# workspace + confinement
# ---------------------------------------------------------------------------
def test_workspace_must_be_registered(ws, tmp_path):
    assert service.resolve_workspace(str(ws)) == ws
    with pytest.raises(service.IdeError):
        service.resolve_workspace(str(tmp_path))
    with pytest.raises(service.IdeError):
        service.resolve_workspace("")
    assert service.list_workspaces()[0] == {"id": 7, "name": "Demo", "path": str(ws), "available": True, "current": True}


@pytest.mark.parametrize("bad", ["../x", "src/../../x", "/etc/passwd", "C:/Windows", "a\x00b"])
def test_paths_cannot_leave_the_workspace(ws, bad):
    with pytest.raises(service.IdeError):
        service.safe_path(ws, bad, must_exist=False)


def test_symlink_escape_is_rejected(ws, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (ws / "link.txt").symlink_to(outside)
    with pytest.raises(service.IdeError, match="outside"):
        service.read_file(ws, "link.txt")
    with pytest.raises(service.IdeError, match="outside"):
        service.write_file(ws, "link.txt", "pwned", etag=None, force=True)
    assert outside.read_text(encoding="utf-8") == "secret"
    # Deleting the link removes the link only.
    service.delete_entry(ws, "link.txt")
    assert outside.exists() and not (ws / "link.txt").exists()


def test_git_internals_are_read_only(ws):
    if not (ws / ".git").exists():
        pytest.skip("git unavailable")
    for call in (lambda: service.write_file(ws, ".git/config", "x", create=True),
                 lambda: service.create_entry(ws, ".git/hooks/evil", "file"),
                 lambda: service.delete_entry(ws, ".git"),
                 lambda: service.rename_entry(ws, "README.md", ".git/README.md")):
        with pytest.raises(service.IdeError, match="read-only"):
            call()


# ---------------------------------------------------------------------------
# read / write
# ---------------------------------------------------------------------------
def test_read_reports_text_metadata(ws):
    f = service.read_file(ws, "src/app.py")
    assert f["content"].startswith("def hello")
    assert f["encoding"] == "utf-8" and f["eol"] == "lf" and not f["binary"]
    assert f["etag"] == service.etag_for((ws / "src/app.py").read_bytes())


def test_binary_large_and_image_files_are_not_text(ws, monkeypatch):
    (ws / "blob.bin").write_bytes(b"\x00\x01\x02binary")
    (ws / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    assert service.read_file(ws, "blob.bin")["binary"] is True
    pic = service.read_file(ws, "pic.png")
    assert pic["image"] and pic["binary"]
    assert service.raw_image(ws, "pic.png")[1] == "image/png"
    with pytest.raises(service.IdeError):
        service.raw_image(ws, "README.md")
    monkeypatch.setattr(service, "MAX_TEXT_BYTES", 4)
    big = service.read_file(ws, "README.md")
    assert big["too_large"] and big["content"] is None


def test_write_requires_matching_etag(ws):
    f = service.read_file(ws, "README.md")
    saved = service.write_file(ws, "README.md", "# New\n", etag=f["etag"])
    assert (ws / "README.md").read_text(encoding="utf-8") == "# New\n"
    assert saved["etag"] != f["etag"]
    # An agent rewrote the file after we loaded it: the stale etag conflicts.
    (ws / "README.md").write_bytes(b"agent edit\n")
    with pytest.raises(service.Conflict) as info:
        service.write_file(ws, "README.md", "mine\n", etag=saved["etag"])
    assert info.value.current["etag"] == service.etag_for(b"agent edit\n")
    assert (ws / "README.md").read_text(encoding="utf-8") == "agent edit\n"
    service.write_file(ws, "README.md", "mine\n", etag=None, force=True)
    assert (ws / "README.md").read_text(encoding="utf-8") == "mine\n"


def test_write_preserves_mode_encoding_and_never_creates_silently(ws):
    script = ws / "run.sh"
    script.write_bytes(b"\xef\xbb\xbfecho hi\r\n")
    script.chmod(0o755)
    # Windows exposes different permission bits; preserve what the OS supports.
    original_mode = script.stat().st_mode & 0o777
    f = service.read_file(ws, "run.sh")
    assert f["encoding"] == "utf-8-bom" and f["eol"] == "crlf" and f["content"] == "echo hi\r\n"
    service.write_file(ws, "run.sh", "echo bye\r\n", etag=f["etag"], encoding=f["encoding"])
    assert script.read_bytes() == b"\xef\xbb\xbfecho bye\r\n"
    assert script.stat().st_mode & 0o777 == original_mode
    with pytest.raises(service.NotFound):
        service.write_file(ws, "new.txt", "x")
    service.write_file(ws, "new.txt", "x", create=True)
    with pytest.raises(service.Conflict):
        service.write_file(ws, "new.txt", "y", create=True)
    assert not [p for p in ws.iterdir() if p.name.endswith(".cuttle-tmp")]


def test_latin1_round_trip(ws):
    (ws / "legacy.txt").write_bytes("caf\xe9\n".encode("latin-1"))
    f = service.read_file(ws, "legacy.txt")
    assert f["encoding"] == "latin-1" and f["content"] == "café\n"
    service.write_file(ws, "legacy.txt", "café!\n", etag=f["etag"], encoding="latin-1")
    assert (ws / "legacy.txt").read_bytes() == "café!\n".encode("latin-1")


def test_stat_reports_changes_and_deletions(ws):
    before = service.stat_files(ws, ["README.md", "gone.txt"])
    assert before["README.md"]["exists"] and before["gone.txt"] == {"exists": False}
    (ws / "README.md").write_text("changed\n", encoding="utf-8")
    assert service.stat_files(ws, ["README.md"])["README.md"]["etag"] != before["README.md"]["etag"]


# ---------------------------------------------------------------------------
# entries
# ---------------------------------------------------------------------------
def test_create_rename_delete(ws):
    service.create_entry(ws, "pkg", "dir")
    service.create_entry(ws, "pkg/mod.py", "file")
    with pytest.raises(service.Conflict):
        service.create_entry(ws, "pkg/mod.py", "file")
    with pytest.raises(service.NotFound):
        service.create_entry(ws, "missing/dir/x.py", "file")
    assert service.rename_entry(ws, "pkg", "lib") == {"from": "pkg", "to": "lib"}
    assert (ws / "lib" / "mod.py").is_file()
    with pytest.raises(service.IdeError, match="itself"):
        service.rename_entry(ws, "lib", "lib/inner")
    with pytest.raises(service.Conflict):
        service.rename_entry(ws, "README.md", "src/app.py")
    assert service.delete_entry(ws, "lib")["type"] == "dir"
    assert not (ws / "lib").exists()
    with pytest.raises(service.IdeError):
        service.delete_entry(ws, "")


def test_list_dir_sorts_hides_git_and_marks_ignored(ws):
    names = [e["name"] for e in service.list_dir(ws)["entries"]]
    assert ".git" not in names
    assert names[:2] == ["build", "src"]  # folders first
    if (ws / ".git").exists():
        build = next(e for e in service.list_dir(ws)["entries"] if e["name"] == "build")
        assert build["ignored"] is True


def test_list_files_respects_gitignore(ws):
    files = service.list_files(ws)["files"]
    assert "src/app.py" in files and "README.md" in files
    if (ws / ".git").exists():
        assert "build/out.txt" not in files


# ---------------------------------------------------------------------------
# search / replace (both engines)
# ---------------------------------------------------------------------------
@pytest.fixture(params=["rg", "python"])
def engine(request, monkeypatch):
    if request.param == "rg" and not shutil.which("rg"):
        pytest.skip("ripgrep unavailable")
    if request.param == "python":
        monkeypatch.setattr(service, "_search_rg", lambda *a, **k: None)
    return request.param


def test_search_finds_matches_with_columns(ws, engine):
    data = service.search(ws, "hello")
    assert data["engine"] == engine
    paths = [r["path"] for r in data["results"]]
    assert "README.md" in paths and "src/app.py" in paths
    if (ws / ".git").exists():
        assert "build/out.txt" not in paths
    app = next(r for r in data["results"] if r["path"] == "src/app.py")
    assert app["matches"][0] == {"line": 1, "col": 5, "length": 5, "preview": "def hello():", "preview_offset": 0}


def test_search_options(ws, engine):
    (ws / "x.txt").write_text("Hello hello helloworld ünï hello\n", encoding="utf-8")
    assert service.search(ws, "Hello", case=True, include="x.txt")["matches"] == 1
    assert service.search(ws, "hello", word=True, include="x.txt")["matches"] == 3
    assert service.search(ws, r"hel+o\w+", regex=True, include="*.txt")["matches"] == 1
    # Columns are characters, not UTF-8 bytes, after non-ASCII text.
    last = service.search(ws, "hello", include="x.txt")["results"][0]["matches"][-1]
    assert last["col"] == len("Hello hello helloworld ünï ") + 1
    assert service.search(ws, "hello", exclude="*.md, src/")["files"] == 1


def test_search_rejects_bad_input(ws):
    with pytest.raises(service.IdeError):
        service.search(ws, "")
    with pytest.raises(service.IdeError, match="regular expression"):
        service.compile_query("(", regex=True, case=False, word=False)


def test_replace_supports_vscode_groups(ws):
    (ws / "a.py").write_text("foo_bar = 1\nfoo_baz = 2\n", encoding="utf-8")
    out = service.replace(ws, r"foo_(\w+)", "$1_foo", regex=True, paths=["a.py"])
    assert out["replacements"] == 2
    assert (ws / "a.py").read_text(encoding="utf-8") == "bar_foo = 1\nbaz_foo = 2\n"
    service.replace(ws, "1", r"\1$", paths=["a.py"])  # literal: no group expansion
    assert (ws / "a.py").read_text(encoding="utf-8").startswith("bar_foo = \\1$")


# ---------------------------------------------------------------------------
# git content
# ---------------------------------------------------------------------------
def test_git_original_returns_committed_text(ws):
    if not (ws / ".git").exists():
        pytest.skip("git unavailable")
    (ws / "README.md").write_text("changed\n", encoding="utf-8")
    original = service.git_original(ws, "README.md")
    assert original["tracked"] and original["content"] == "# Demo\nhello world\n"
    (ws / "fresh.txt").write_text("new\n", encoding="utf-8")
    assert service.git_original(ws, "fresh.txt")["tracked"] is False
    with pytest.raises(service.IdeError):
        service.git_original(ws, "README.md", ref="--upload-pack=x")


def test_completion_cleanup():
    assert service.strip_completion("```py\nreturn 1\n```", "def f():\n    ", "") == "return 1"
    assert service.strip_completion("    x = compute()", "    x = ", "\n") == "compute()"
    assert service.strip_completion("a + b)\nprint(x)", "f(", ")\nprint(x)") == "a + b"
    assert service.strip_completion(None, "", "") == ""


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
def _client(tmp_path, monkeypatch):
    from tests.auth.test_http_authz import _auth_client

    ctx = _auth_client(tmp_path, monkeypatch)
    ctx["client"].set_cookie("session_token", ctx["token"])
    return ctx


def test_flag_registered_default_on(monkeypatch):
    monkeypatch.delenv("CUTTLE_EXPERIMENTAL", raising=False)
    spec = next(f for f in flags.flags_payload()["flags"] if f["id"] == "ide")
    assert spec["default"] is True and spec["enabled"] is True


def test_routes_disabled_while_flag_off(ws, tmp_path, monkeypatch):
    monkeypatch.delenv("CUTTLE_EXPERIMENTAL", raising=False)
    flags.set_enabled("ide", False)
    client = _client(tmp_path, monkeypatch)["client"]
    res = client.get("/api/ide/tree", query_string={"root": str(ws)})
    assert res.status_code == 403 and res.get_json()["disabled"] is True
    assert client.put("/api/ide/file", json={"root": str(ws), "file": "README.md", "content": "x"}).status_code == 403
    assert client.get("/ide_page.html").status_code == 403
    assert (ws / "README.md").read_text(encoding="utf-8") == "# Demo\nhello world\n"


def test_kill_switch_disables_routes(ws, tmp_path, monkeypatch):
    flags.set_enabled("ide", True)
    monkeypatch.setenv("CUTTLE_EXPERIMENTAL", "0")
    client = _client(tmp_path, monkeypatch)["client"]
    assert client.get("/api/ide/workspaces").status_code == 403


def test_routes_are_owner_only(ws, tmp_path, monkeypatch):
    monkeypatch.delenv("CUTTLE_EXPERIMENTAL", raising=False)
    flags.set_enabled("ide", True)
    ctx = _client(tmp_path, monkeypatch)
    ctx["client"].set_cookie("session_token", ctx["other_token"])
    assert ctx["client"].get("/api/ide/workspaces").status_code == 403
    ctx["client"].delete_cookie("session_token")
    assert ctx["client"].get("/api/ide/file", query_string={"root": str(ws), "file": "README.md"}).status_code == 401


def test_routes_read_write_conflict_and_search(ws, tmp_path, monkeypatch):
    monkeypatch.delenv("CUTTLE_EXPERIMENTAL", raising=False)
    flags.set_enabled("ide", True)
    client = _client(tmp_path, monkeypatch)["client"]
    assert client.get("/ide_page.html").status_code == 200
    assert client.get("/api/ide/workspaces").get_json()["workspaces"][0]["path"] == str(ws)
    tree = client.get("/api/ide/tree", query_string={"root": str(ws), "dir": "src"}).get_json()
    assert [e["path"] for e in tree["entries"]] == ["src/app.py"]
    f = client.get("/api/ide/file", query_string={"root": str(ws), "file": "src/app.py"}).get_json()["file"]
    ok = client.put("/api/ide/file", json={"root": str(ws), "file": "src/app.py", "content": "x = 1\n", "etag": f["etag"]})
    assert ok.status_code == 200 and (ws / "src/app.py").read_text(encoding="utf-8") == "x = 1\n"
    stale = client.put("/api/ide/file", json={"root": str(ws), "file": "src/app.py", "content": "y\n", "etag": f["etag"]})
    body = stale.get_json()
    assert stale.status_code == 409 and body["conflict"] is True and body["current"]["etag"] == ok.get_json()["file"]["etag"]
    outside = client.get("/api/ide/file", query_string={"root": str(ws), "file": "../secret"})
    assert outside.status_code == 400
    unregistered = client.get("/api/ide/tree", query_string={"root": str(tmp_path)})
    assert unregistered.status_code == 400
    found = client.post("/api/ide/search", json={"root": str(ws), "query": "hello"}).get_json()
    assert found["success"] and found["matches"] >= 1
    bad = client.post("/api/ide/search", json={"root": str(ws), "query": "x", "max_results": "lots"})
    assert bad.status_code == 400
    created = client.post("/api/ide/entry", json={"root": str(ws), "path": "notes.md", "kind": "file"})
    assert created.status_code == 200 and (ws / "notes.md").exists()
    assert client.post("/api/ide/rename", json={"root": str(ws), "from": "notes.md", "to": "docs.md"}).status_code == 200
    assert client.post("/api/ide/delete", json={"root": str(ws), "path": "docs.md"}).status_code == 200
    assert not (ws / "docs.md").exists()


def test_raw_image_route_is_sandboxed(ws, tmp_path, monkeypatch):
    monkeypatch.delenv("CUTTLE_EXPERIMENTAL", raising=False)
    flags.set_enabled("ide", True)
    (ws / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    client = _client(tmp_path, monkeypatch)["client"]
    res = client.get("/api/ide/raw", query_string={"root": str(ws), "file": "pic.png"})
    assert res.status_code == 200 and res.mimetype == "image/png"
    assert "sandbox" in res.headers["Content-Security-Policy"]
    assert client.get("/api/ide/raw", query_string={"root": str(ws), "file": "README.md"}).status_code == 400
