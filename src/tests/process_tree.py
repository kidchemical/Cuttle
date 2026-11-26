"""End a test's own process tree, deepest first.

The suite's kill guard (conftest) only lets a test kill its descendants, judged
by the live parent chain. Killing a parent first breaks that chain for its
children, and on Windows a venv ``python.exe`` is a launcher with the
interpreter as its child, so every spawn adds a level.
"""

from __future__ import annotations

import psutil


def kill_tree(pid: int) -> None:
    try:
        root = psutil.Process(pid)
        procs = [root, *root.children(recursive=True)]
    except psutil.Error:
        return
    depth = {}
    for p in procs:
        try:
            depth[p.pid] = len(p.parents())
        except psutil.Error:
            depth[p.pid] = 0
    for p in sorted(procs, key=lambda p: depth[p.pid], reverse=True):
        try:
            p.kill()
        except psutil.Error:
            pass


def settled_children(pid: int, *, want: int, timeout: float = 15.0) -> list:
    """Descendants of ``pid`` once at least ``want`` exist and the count holds."""
    import time

    deadline, last, stable_since = time.time() + timeout, -1, time.time()
    kids: list = []
    while time.time() < deadline:
        try:
            kids = psutil.Process(pid).children(recursive=True)
        except psutil.Error:
            return []
        if len(kids) != last:
            last, stable_since = len(kids), time.time()
        elif len(kids) >= want and time.time() - stable_since > 0.5:
            break
        time.sleep(0.05)
    return kids
