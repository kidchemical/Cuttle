"""Session-kill safety: never treat PID 1 / ancestors as agent process trees."""

from __future__ import annotations

import os

from api.chat_run_registry import kill_pid_tree, list_descendant_pids
from api.process_kill_safety import is_forbidden_kill_target, may_kill_pid


def test_kill_pid_tree_refuses_pid_1():
    assert kill_pid_tree(1) is False
    assert kill_pid_tree(True) is False  # bool True == 1
    assert list_descendant_pids(1) == []


def test_kill_pid_tree_refuses_parent_process():
    ppid = os.getppid()
    assert is_forbidden_kill_target(ppid) is True
    assert kill_pid_tree(ppid) is False
    assert list_descendant_pids(ppid) == []


def test_kill_pid_tree_refuses_self():
    assert kill_pid_tree(os.getpid()) is False


def test_may_kill_pid_rejects_init_and_allows_only_descendants():
    assert may_kill_pid(1) is False
    assert may_kill_pid(os.getpid()) is False
    assert may_kill_pid(os.getppid()) is False
    assert is_forbidden_kill_target(1) is True
