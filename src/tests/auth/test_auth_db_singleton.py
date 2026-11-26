"""Concurrent first callers share one AuthDatabase (schema setup runs once)."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from api import auth_db


def test_concurrent_first_calls_build_one_instance(monkeypatch):
    built = []
    lock = threading.Lock()

    class SlowDatabase:
        def __init__(self, path):
            time.sleep(0.05)  # widen the window two unsynchronized callers would share
            with lock:
                built.append(path)

    monkeypatch.setattr(auth_db, "_db_instance", None)
    monkeypatch.setattr(auth_db, "AuthDatabase", SlowDatabase)
    with ThreadPoolExecutor(max_workers=8) as pool:
        instances = list(pool.map(lambda _: auth_db.get_auth_db(), range(16)))
    assert len(built) == 1
    assert len({id(i) for i in instances}) == 1
