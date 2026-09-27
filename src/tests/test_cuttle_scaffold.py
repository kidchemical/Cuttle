"""Tests for managers.cuttle_scaffold.ensure_cuttle_scaffold."""
from __future__ import annotations

from pathlib import Path

from managers.cuttle_scaffold import CUTTLE_SUBDIRS, PROJECT_TEMP_DIR, ensure_cuttle_scaffold


def test_ensure_cuttle_scaffold_creates_tree(tmp_path: Path):
    root = tmp_path / "My Game"
    root.mkdir()
    created = ensure_cuttle_scaffold(root, project_name="My Game")
    assert (root / ".cuttle").is_dir()
    for name in CUTTLE_SUBDIRS:
        assert (root / ".cuttle" / name).is_dir()
    assert (root / ".cuttle" / "README.md").is_file()
    assert (root / ".cuttle" / "rules" / "00-core.md").is_file()
    assert (root / ".cuttle" / "commands" / "README.md").is_file()
    assert (root / PROJECT_TEMP_DIR).is_dir()
    gi = (root / ".gitignore").read_text(encoding="utf-8")
    assert "/temp/" in gi
    assert ".cuttle/personal/" in gi
    assert (root / ".cuttle" / "personal" / "README.md").is_file()
    assert (root / ".cuttle" / "personal" / "docs").is_dir()
    core = (root / ".cuttle" / "rules" / "00-core.md").read_text(encoding="utf-8")
    assert "My Game" in core
    assert "temp/" in core
    assert created  # first run creates paths


def test_ensure_cuttle_scaffold_idempotent(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    first = ensure_cuttle_scaffold(root, project_name="proj")
    rules = root / ".cuttle" / "rules" / "00-core.md"
    rules.write_text("# custom\n", encoding="utf-8")
    second = ensure_cuttle_scaffold(root, project_name="proj")
    assert rules.read_text(encoding="utf-8") == "# custom\n"
    assert second == []  # nothing new
    assert first  # first pass created paths


def test_ensure_temp_gitignore_appends_existing(tmp_path: Path):
    root = tmp_path / "HasIgnore"
    root.mkdir()
    (root / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    ensure_cuttle_scaffold(root, project_name="HasIgnore")
    text = (root / ".gitignore").read_text(encoding="utf-8")
    assert "node_modules/" in text
    assert "/temp/" in text


def test_add_local_project_scaffolds(tmp_path: Path):
    from managers.project_manager import ProjectManager

    root = tmp_path / "LocalProj"
    root.mkdir()
    db = tmp_path / "projects.db"
    # Avoid ensure_default_project writing into cwd:
    # ProjectManager.__init__ calls ensure_default_project which no-ops if projects exist
    # after first add. First init may try to add cwd — use isolated db and monkeypatch.
    pm = ProjectManager(db_path=str(db))
    # If default project was added for cwd, still fine; add our local
    ok = pm.add_local_project("LocalProj", str(root), description="test", tags=["t"])
    assert ok is True
    assert (root / ".cuttle" / "rules" / "00-core.md").is_file()
    assert (root / PROJECT_TEMP_DIR).is_dir()
