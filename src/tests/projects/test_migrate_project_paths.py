"""The legacy path repair utility must use the authoritative registry owner."""
import json

import pytest

from scripts.utilities import migrate_project_paths as migration


@pytest.fixture
def registry(tmp_path, monkeypatch):
    from managers.project_manager import ProjectManager

    monkeypatch.setattr(ProjectManager, 'ensure_default_project', lambda self: None)
    manager = ProjectManager(str(tmp_path / 'projects.db'))
    old = tmp_path / 'old'
    new = tmp_path / 'new'
    old.mkdir()
    new.mkdir()
    pid = manager.register_project('Moved project', str(old))
    monkeypatch.setattr(migration, 'PROJECTS_DB', tmp_path / 'projects.db')
    return manager, pid, old, new


def test_registry_remap_updates_ordered_paths_compatibility_and_audit(registry):
    manager, pid, old, new = registry
    manager.update_project(pid, paths=[str(old), 'Z:/foreign/project'], repo_url='https://example.com/project')
    changes = migration.scan_projects([migration.PathRule.from_pair(str(old), str(new))])
    assert migration.apply_projects(changes, dry_run=False) == 1
    project = manager.get_project(pid)
    assert project['path'] == str(new)
    assert project['stored_path'] == str(new)
    assert project['paths'] == [str(new), 'Z:/foreign/project']
    assert project['config']['repo_url'] == 'https://example.com/project'
    assert any(row['details'].get('paths') == project['paths'] for row in manager.get_project_history(pid))


def test_registry_preview_does_not_write_and_preserves_unmapped_locations(registry):
    manager, pid, old, new = registry
    before = manager.get_project(pid)
    changes = migration.scan_projects([migration.PathRule.from_pair(str(old), str(new))])
    assert migration.apply_projects(changes, dry_run=True) == 1
    assert manager.get_project(pid) == before


def test_registry_scan_reads_ordered_list_instead_of_legacy_scalar(registry):
    manager, pid, old, new = registry
    manager.update_project(pid, paths=['Z:/original', str(old)])
    changes = migration.scan_projects([migration.PathRule.from_pair(str(old), str(new))])
    assert len(changes) == 1
    migration.apply_projects(changes, dry_run=False)
    project = manager.get_project(pid)
    assert project['paths'] == ['Z:/original', str(new)]
    assert project['stored_path'] == 'Z:/original'
    with manager.get_db_connection() as conn:
        config = json.loads(conn.execute('SELECT config FROM projects WHERE id = ?', (pid,)).fetchone()[0])
    assert config['paths'] == project['paths']
