"""Knowledge-graph protocol, registry, isolation, and (real) Graphify provider."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from api.knowledge_graph import catalog, service
from api.knowledge_graph import resolve as resolve_mod
from api.knowledge_graph.manifest import load_manifest
from api.knowledge_graph.store import checkout_key, project_key, store_root_path

FAKE_ADAPTER = '''
class FakeProvider:
    id = "fakekg"
    label = "Fake KG"
    def available(self): return True
    def is_indexed(self, store): return (store.root / "fake.json").is_file()
    def artifact(self, store):
        p = store.root / "fake.json"
        return str(p) if p.is_file() else None
    def index(self, store, *, force=False):
        from api.knowledge_graph.protocol import IndexResult
        store.root.mkdir(parents=True, exist_ok=True)
        (store.root / "fake.json").write_text("{}")
        return IndexResult(ok=True, artifact=self.artifact(store), meta={"nodes": 1})
    def update(self, store): return self.index(store)
    def query(self, store, question, *, budget=100):
        return f"fake answer for: {question}"
    def explain(self, store, node, *, all_connections=False): return f"fake explain {node}"
    def node_path(self, store, source, target): return f"fake path {source}->{target}"
    def export(self, store, target_dir):
        from api.knowledge_graph.protocol import ExportResult
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "fake.md").write_text("fake")
        return ExportResult(ok=True, project_dir=str(target_dir.parent), graph_dir=str(target_dir))

def build_provider():
    return FakeProvider()
'''


def _fake_projects(monkeypatch, projects, checkouts=()):
    monkeypatch.setattr(resolve_mod, "_projects", lambda: list(projects))
    monkeypatch.setattr(resolve_mod, "_checkout_rows", lambda: [dict(c) for c in checkouts])


def _project(pid, name, path, **extra):
    return {"id": pid, "name": name, "path": str(path), "resolved_path": str(path),
            "available": True, **extra}


def _write_fake_provider(base: Path, pid: str = "fakekg") -> Path:
    d = base / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.yaml").write_text(
        f"id: {pid}\nlabel: Fake KG\nstore_root: fakekg\n"
        "capabilities: [index, query]\nexecutable_names: [fake]\n"
    )
    (d / "adapter.py").write_text(FAKE_ADAPTER)
    return d


# ---------------------------------------------------------------------------
# identity / resolution
# ---------------------------------------------------------------------------

def test_project_key_stable_and_collision_free():
    a = _project(4, "Cuttle", "/tmp/a")
    b = _project(99, "Cuttle", "/tmp/b")
    assert project_key(a) == project_key(a)
    assert project_key(a) != project_key(b)


def test_checkout_key_primary_and_validation():
    assert checkout_key(None) == "primary"
    assert checkout_key("wt-x") == "wt-x"
    with pytest.raises(ValueError):
        checkout_key("../../etc")


def test_is_indexable(tmp_path, monkeypatch):
    assert service.is_indexable(str(Path.home())) is False
    repo = tmp_path / "r"
    (repo / ".git").mkdir(parents=True)
    assert service.is_indexable(str(repo)) is True
    plain = tmp_path / "p"
    plain.mkdir()
    (plain / "a.png").write_bytes(b"\x89PNG")
    assert service.is_indexable(str(plain)) is False
    code = tmp_path / "c"
    code.mkdir()
    (code / "m.py").write_text("x=1\n")
    assert service.is_indexable(str(code)) is True


def test_resolve_prefix_and_worktree_isolation(tmp_path, monkeypatch):
    proj = _project(7, "Demo", tmp_path / "primary")
    wt = tmp_path / "worktrees" / "demo" / "wt-abc"
    wt.mkdir(parents=True)
    _fake_projects(monkeypatch, [proj], checkouts=[
        {"checkout_uid": "wt-abc", "project_id": 7, "path": str(wt)}])
    primary = resolve_mod.resolve(str(tmp_path / "primary"))
    worktree = resolve_mod.resolve(str(wt / "src"))
    assert primary.checkout_uid is None
    assert worktree.checkout_uid == "wt-abc"
    assert worktree.project["id"] == 7


def test_resolve_unknown(tmp_path, monkeypatch):
    _fake_projects(monkeypatch, [_project(1, "A", tmp_path / "a")])
    assert resolve_mod.resolve(str(tmp_path / "elsewhere")) is None


# ---------------------------------------------------------------------------
# protocol / registry
# ---------------------------------------------------------------------------

def test_manifest_validation(tmp_path):
    good = tmp_path / "ok" / "manifest.yaml"
    good.parent.mkdir()
    good.write_text("id: ok\nstore_root: ok\ncapabilities: [index, query]\n")
    info = load_manifest(good, expect_id="ok")
    assert info.id == "ok" and info.store_root == "ok"

    with pytest.raises(ValueError):
        load_manifest(good, expect_id="other")  # id/dir mismatch

    bad_caps = tmp_path / "bad" / "manifest.yaml"
    bad_caps.parent.mkdir()
    bad_caps.write_text("id: bad\ncapabilities: [query]\n")  # missing index
    with pytest.raises(ValueError):
        load_manifest(bad_caps)


def test_drop_in_provider_is_discovered_and_selectable(tmp_path, monkeypatch):
    ext = tmp_path / "providers"
    _write_fake_provider(ext)
    monkeypatch.setenv("CUTTLE_KG_PROVIDERS_DIR", str(ext))
    monkeypatch.setenv("CUTTLE_KG_PROVIDER", "fakekg")
    catalog.reset_cache()
    try:
        ids = [p["id"] for p in catalog.provider_status()]
        assert "fakekg" in ids and "graphify" in ids
        assert catalog.default_provider_id() == "fakekg"
        provider = catalog.get_provider("fakekg")
        assert provider is not None and provider.available()
    finally:
        catalog.reset_cache()
        monkeypatch.delenv("CUTTLE_KG_PROVIDERS_DIR", raising=False)
        monkeypatch.delenv("CUTTLE_KG_PROVIDER", raising=False)


def test_facade_delegates_to_selected_provider(tmp_path, monkeypatch):
    ext = tmp_path / "providers"
    _write_fake_provider(ext)
    monkeypatch.setenv("CUTTLE_KG_PROVIDERS_DIR", str(ext))
    catalog.reset_cache()
    try:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        _fake_projects(monkeypatch, [_project(7, "Demo", repo)])
        ctx = resolve_mod.resolve(str(repo))

        out = service.index(ctx, provider_id="fakekg")
        assert out["ok"] and out["meta"]["nodes"] == 1
        store = service.store_for(ctx, "fakekg")
        assert store_root_path(catalog.get_provider_info("fakekg")).name == "fakekg"
        assert store.root.parent.name == project_key(ctx.project)

        assert "fake answer" in service.query(ctx, "hi", provider_id="fakekg")
        assert "fake path" in service.node_path(ctx, "A", "B", provider_id="fakekg")
    finally:
        catalog.reset_cache()
        monkeypatch.delenv("CUTTLE_KG_PROVIDERS_DIR", raising=False)


def test_unknown_provider_raises(tmp_path, monkeypatch):
    _fake_projects(monkeypatch, [_project(7, "Demo", tmp_path)])
    with pytest.raises(service.KnowledgeGraphError):
        service.query(resolve_mod.resolve(str(tmp_path)), "q", provider_id="nope")


# ---------------------------------------------------------------------------
# bundled Graphify provider (real CLI; skipped when absent)
# ---------------------------------------------------------------------------

def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "pkg").mkdir(exist_ok=True)
    (path / "pkg" / "a.py").write_text(
        "# NOTE: entry\nfrom pkg import util\nclass Service:\n    def run(self):\n        return util.helper()\n")
    (path / "pkg" / "util.py").write_text("def helper():\n    return 42\n")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"], cwd=path, check=True)


def _graphify_available() -> bool:
    provider = catalog.get_provider("graphify")
    return bool(provider and provider.available())


@pytest.mark.skipif(not _graphify_available(), reason="graphify CLI not installed")
def test_graphify_provider_roundtrip(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _fake_projects(monkeypatch, [_project(7, "Demo", repo)])
    monkeypatch.setenv("CUTTLE_OBSIDIAN_VAULT", str(tmp_path / "vault"))

    ctx = resolve_mod.resolve(str(repo))
    out = service.index(ctx)
    assert out["ok"] and out["meta"]["nodes"] > 0
    store = service.store_for(ctx)
    assert Path(service.provider_for().artifact(store)).is_file()
    assert "Service" in service.query(ctx, "how does Service run", budget=800)

    again = service.index(ctx)
    assert again["meta"]["incremental"] is True

    labels = {n.get("community_name") for n in service.provider_for().graph_data(store)["nodes"]}
    assert labels and not any(str(v).startswith("Community ") for v in labels)

    vault = tmp_path / "vault" / "Demo"
    export = service.export_obsidian(ctx)
    assert export["ok"] is True and export["vault"] == str(vault)
    assert (vault / ".obsidian").is_dir()                       # its own vault
    assert (vault / "Architecture" / "Overview.md").is_file()
    assert [p.name for p in (vault / "Subsystems").iterdir()] == ["Demo (root).md"]  # 2 files roll up
    assert not (vault / "Graphify").exists()
    manual = vault / "Notes" / "n.md"
    manual.write_text("# mine\n")
    service.export_obsidian(ctx)
    assert manual.read_text() == "# mine\n"


def test_export_archives_legacy_per_node_export(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    project = tmp_path / "vault" / "Demo"
    (project / "Graphify").mkdir(parents=True)
    (project / "Graphify" / "finalize().md").write_text("x")
    (project / "Architecture").mkdir()
    (project / "Architecture" / "Demo Graph Report.md").write_text("report")
    (project / "Architecture" / "Mine.md").write_text("# mine\n")

    dest = service._archive_legacy_export(project)
    assert dest and (tmp_path / "home" / "archive") in Path(dest).parents
    assert not (project / "Graphify").exists()
    assert not (project / "Architecture" / "Demo Graph Report.md").exists()
    assert (project / "Architecture" / "Mine.md").read_text() == "# mine\n"


@pytest.mark.skipif(not _graphify_available(), reason="graphify CLI not installed")
def test_two_projects_isolated(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    _init_repo(a)
    _init_repo(b)
    _fake_projects(monkeypatch, [_project(1, "A", a), _project(2, "B", b)])
    ctx_a, ctx_b = resolve_mod.resolve(str(a)), resolve_mod.resolve(str(b))
    service.index(ctx_a)
    service.index(ctx_b)
    store_a, store_b = service.store_for(ctx_a), service.store_for(ctx_b)
    assert store_a.root != store_b.root
