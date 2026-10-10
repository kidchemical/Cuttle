"""Checkout registry: identity, binding, states, audit, fail-closed reconcile."""

from __future__ import annotations

import sqlite3

import pytest

from api.worktrees import registry


def _db(tmp_path):
    return tmp_path / "projects.db"


def test_create_and_get(tmp_path):
    db = _db(tmp_path)
    created = registry.create_checkout(
        1, str(tmp_path / "wt" / "myrepo"), base_branch="main",
        branch="cuttle/abc", checkout_uid="uid1", db_path_override=db,
    )
    assert created.checkout_uid == "uid1"
    assert created.kind == "worktree"
    assert created.state == "ready"
    assert registry.get_checkout("uid1", db_path_override=db) == created
    assert registry.get_checkout("missing", db_path_override=db) is None


def test_binding_by_path_and_project(tmp_path):
    db = _db(tmp_path)
    path = tmp_path / "wt" / "myrepo"
    registry.create_checkout(7, str(path), checkout_uid="u2", db_path_override=db)
    bound = registry.checkout_for_path(str(path), db_path_override=db)
    assert bound is not None and bound.checkout_uid == "u2"
    assert registry.checkout_for_path(str(tmp_path / "primary"), db_path_override=db) is None
    assert registry.checkout_belongs_to("u2", 7, db_path_override=db)
    assert not registry.checkout_belongs_to("u2", 8, db_path_override=db)


def test_list_remove_and_states(tmp_path):
    db = _db(tmp_path)
    registry.create_checkout(1, str(tmp_path / "a"), checkout_uid="a", db_path_override=db)
    registry.create_checkout(1, str(tmp_path / "b"), branch="x", checkout_uid="b", db_path_override=db)
    registry.create_checkout(2, str(tmp_path / "c"), checkout_uid="c", db_path_override=db)
    assert {c.checkout_uid for c in registry.list_checkouts(1, db_path_override=db)} == {"a", "b"}
    assert [c.checkout_uid for c in registry.list_checkouts(db_path_override=db)] == ["a", "b", "c"]

    registry.mark_state("b", "broken", db_path_override=db)
    assert registry.get_checkout("b", db_path_override=db).state == "broken"
    registry.touch_checkout("a", db_path_override=db)
    assert registry.remove_checkout("a", db_path_override=db) is True
    assert registry.remove_checkout("a", db_path_override=db) is False


def test_duplicate_path_rejected(tmp_path):
    db = _db(tmp_path)
    registry.create_checkout(1, str(tmp_path / "wt"), checkout_uid="a", db_path_override=db)
    with pytest.raises(sqlite3.IntegrityError):
        registry.create_checkout(1, str(tmp_path / "wt"), checkout_uid="b", db_path_override=db)


def test_reconcile_marks_missing_then_recovers(tmp_path):
    db = _db(tmp_path)
    live = tmp_path / "live"
    gone = tmp_path / "gone"
    live.mkdir()
    registry.create_checkout(1, str(live), checkout_uid="live", db_path_override=db)
    registry.create_checkout(1, str(gone), checkout_uid="gone", db_path_override=db)

    out = registry.reconcile_checkouts(db_path_override=db)
    assert "gone" in out["marked_missing"]
    assert registry.get_checkout("gone", db_path_override=db).state == "missing"
    assert registry.get_checkout("live", db_path_override=db).state == "ready"

    gone.mkdir()
    registry.reconcile_checkouts(db_path_override=db)
    assert registry.get_checkout("gone", db_path_override=db).state == "ready"


def test_audit_history_logged_when_projects_table_present(tmp_path):
    db = _db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE project_history (id INTEGER PRIMARY KEY, project_id INTEGER, "
        "action TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, details TEXT)"
    )
    conn.commit()
    conn.close()

    registry.create_checkout(5, str(tmp_path / "wt"), checkout_uid="h", db_path_override=db)
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT action, details FROM project_history ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row[0] == "checkout_created"
    assert "h" in row[1]


def test_flag_registered_and_off_by_default():
    from api.experimental import get_flag
    from api.worktrees import is_worktrees_enabled

    assert get_flag("worktrees") is not None
    assert is_worktrees_enabled() is False
