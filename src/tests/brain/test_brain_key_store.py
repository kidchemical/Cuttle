"""Indexed storage migration, isolation and concurrent read-modify-write."""
import json
from concurrent.futures import ThreadPoolExecutor

from api.cuttle_brain.key_store import KeyStore


def test_legacy_import_once_and_one_key_updates(tmp_path, monkeypatch):
    legacy = tmp_path / 'state.json'
    original = json.dumps({str(i): {'value': i} for i in range(1000)})
    legacy.write_text(original)
    store = KeyStore(legacy)
    assert store.get('99') == {'value': 99}
    store.put('99', {'value': 'updated'})
    assert legacy.read_text() == original
    # Neither restart nor a normal turn reads the installation-wide JSON again.
    from pathlib import Path
    monkeypatch.setattr(Path, 'read_text', lambda *a, **k: (_ for _ in ()).throw(AssertionError('legacy reread')))
    reopened = KeyStore(legacy)
    assert reopened.get('99') == {'value': 'updated'}
    reopened.put('new', {'value': 'new'})
    assert len(reopened.all()) == 1001
    reopened.delete_prefixes(['9'])
    assert reopened.get('99') is None
    assert reopened.get('100') == {'value': 100}


def test_concurrent_updates_preserve_other_agent_cursors(tmp_path):
    path = tmp_path / 'handoff.json'
    def update(agent):
        store = KeyStore(path)
        for i in range(10):
            def change(old):
                entry = old or {'seen': {}}
                entry['seen'][agent] = i
                return entry
            store.update('chat', change)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(update, [f'agent-{i}' for i in range(6)]))
    assert KeyStore(path).get('chat')['seen'] == {f'agent-{i}': 9 for i in range(6)}



def test_malformed_legacy_import_is_retryable_and_preserves_file(tmp_path):
    import pytest
    legacy = tmp_path / 'state.json'
    legacy.write_text('{broken')
    with pytest.raises(json.JSONDecodeError):
        KeyStore(legacy).get('chat')
    assert legacy.read_text() == '{broken'
    legacy.write_text(json.dumps({'chat': {'seen': {'agent': 12}}}))
    assert KeyStore(legacy).get('chat') == {'seen': {'agent': 12}}


def test_separate_process_updates_do_not_lose_cursors(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    script = """
from pathlib import Path
from api.cuttle_brain.key_store import KeyStore
import sys
store = KeyStore(Path(sys.argv[1]))
for i in range(10):
    def change(old):
        entry = old or {'seen': {}}
        entry['seen'][sys.argv[2]] = i
        return entry
    store.update('chat', change)
"""
    path = tmp_path / 'state.json'
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2])}
    def run(agent):
        subprocess.run([sys.executable, '-c', script, str(path), agent], env=env,
                       check=True, capture_output=True, timeout=15)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(run, ['a', 'b', 'c', 'd']))
    assert KeyStore(path).get('chat') == {'seen': {'a': 9, 'b': 9, 'c': 9, 'd': 9}}
