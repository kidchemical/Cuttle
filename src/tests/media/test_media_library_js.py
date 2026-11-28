"""Media App client: pure model (node) + shell registration."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / "src" / "web"
MODEL = WEB / "js" / "media_library" / "media_library_model.js"

HARNESS = r"""
const assert = require('assert');
const M = require(process.env.MODEL);
const now = Date.parse('2026-11-17T12:00:00Z');
const it = (id, o) => Object.assign({ id, url: '/output/shared/' + id + '.png', kind: 'image', name: id + '.png',
  title: '', source: 'agent', message_id: 1, handle: 'CH-000001-1', chat_id: 1, chat_title: 'A',
  project: 'P', chat_archived: false, available: true, size: 10, shared_at: '2026-11-17T10:00:00Z' }, o);
const items = [
  it('a', { message_id: 5, shared_at: '2026-11-17T11:00:00Z', size: 5 }),
  it('b', { message_id: 4, kind: 'video', url: '/output/shared/b.mp4', name: 'b.mp4', shared_at: '2026-11-16T11:00:00Z', size: 50, chat_id: 2, chat_title: 'Bravo' }),
  it('c', { message_id: 3, source: 'upload', shared_at: '2026-11-01T11:00:00Z', size: null, project: '' }),
  it('d', { message_id: 2, available: false, shared_at: '2026-11-15T11:00:00Z' }),
  it('e', { message_id: 1, chat_archived: true, title: 'Glowing gem', shared_at: '2025-01-01T00:00:00Z', size: 1 }),
  it('f', { message_id: 6, kind: 'audio', url: '/output/shared/f.mp3', name: 'f.mp3', shared_at: '2026-11-17T11:30:00Z', size: 20 }),
];
const ids = (xs) => xs.map((x) => x.id);

// Defaults: expired hidden, archived shown, newest first; unknown prefs dropped.
const p = M.normalizePrefs({ sort: 'bogus', kind: 'video', showExpired: 'yes', chat: 7 });
assert.equal(p.sort, 'newest'); assert.equal(p.kind, 'video'); assert.equal(p.showExpired, false); assert.equal(p.chat, '7');
assert.equal(M.normalizePrefs({ kind: 'audio' }).kind, 'audio');
assert.deepEqual(ids(M.sortItems(M.filterItems(items, {}, now), 'newest')), ['f', 'a', 'b', 'c', 'e']);
assert.deepEqual(ids(M.filterItems(items, { showExpired: true }, now)), ['a', 'b', 'c', 'd', 'e', 'f']);
assert.deepEqual(ids(M.filterItems(items, { showArchived: false }, now)), ['a', 'b', 'c', 'f']);
assert.deepEqual(ids(M.filterItems(items, { kind: 'video' }, now)), ['b']);
assert.deepEqual(ids(M.filterItems(items, { kind: 'audio' }, now)), ['f']);
assert.deepEqual(ids(M.filterItems(items, { source: 'mine' }, now)), ['c']);
assert.deepEqual(ids(M.filterItems(items, { source: 'agents' }, now)), ['a', 'b', 'e', 'f']);
assert.deepEqual(ids(M.filterItems(items, { chat: '2' }, now)), ['b']);
assert.deepEqual(ids(M.filterItems(items, { project: '-' }, now)), ['c']);
assert.deepEqual(ids(M.filterItems(items, { range: 'week' }, now)), ['a', 'b', 'f']);
assert.deepEqual(ids(M.filterItems(items, { query: 'GEM glowing' }, now)), ['e']);
assert.deepEqual(ids(M.filterItems(items, { query: 'bravo' }, now)), ['b']);

// Sorts (unknown sizes last when smallest first).
const shown = M.filterItems(items, {}, now);
assert.deepEqual(ids(M.sortItems(shown, 'oldest')), ['e', 'c', 'b', 'a', 'f']);
assert.deepEqual(ids(M.sortItems(shown, 'size-desc')), ['b', 'f', 'a', 'e', 'c']);
assert.deepEqual(ids(M.sortItems(shown, 'size-asc')), ['e', 'a', 'f', 'b', 'c']);
assert.deepEqual(ids(M.sortItems(shown, 'chat')), ['f', 'a', 'c', 'e', 'b']);
assert.deepEqual(ids(M.sortItems(shown, 'name-desc')), ['f', 'e', 'c', 'b', 'a']);

// Groups keep sort order; chat groups carry the chat handle.
const byChat = M.groupItems(M.sortItems(shown, 'newest'), 'chat', now);
assert.deepEqual(byChat.map((g) => [g.label, ids(g.items)]), [['A', ['f', 'a', 'c', 'e']], ['Bravo', ['b']]]);
assert.equal(byChat[0].chat.handle, 'CH-000001');
const byDay = M.groupItems(M.sortItems(shown, 'newest'), 'day', now);
assert.equal(byDay[0].label, 'Today'); assert.equal(byDay[1].label, 'Yesterday');
assert.deepEqual(M.groupItems(shown, 'none', now).map((g) => g.items.length), [5]);
assert.deepEqual(M.groupItems([], 'none', now), []);

// Gallery: expired skipped, captions name the chat, uploads downloadable.
// Order follows the filtered input (not re-sorted).
const gallery = M.galleryItems(M.filterItems(items, { showExpired: true }, now));
assert.deepEqual(gallery.map((g) => g.id), ['a', 'b', 'c', 'e', 'f']);
assert.equal(gallery[1].kind, 'video');
assert.equal(gallery[4].kind, 'audio');
assert.equal(gallery[3].description, 'Glowing gem — A · CH-000001-1');
assert.equal(gallery[0].download, '/output/shared/a.png?download=1');

// Filter counting + clearing keeps the view (sort / group / size).
const busy = { query: 'x', kind: 'image', showExpired: true, sort: 'oldest', group: 'day', size: 'large' };
assert.equal(M.activeFilterCount(busy), 3);
const cleared = M.clearedFilters(busy);
assert.equal(M.activeFilterCount(cleared), 0);
assert.deepEqual([cleared.sort, cleared.group, cleared.size], ['oldest', 'day', 'large']);

assert.deepEqual(M.projectOptions(items).map((o) => o.value), ['P', '-']);
assert.equal(M.formatSize(1536), '1.5 KB'); assert.equal(M.formatSize(null), '');
console.log(JSON.stringify({ ok: true }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_media_model_filters_sorts_groups_and_builds_gallery():
    proc = subprocess.run(["node", "-e", HARNESS], env={**os.environ, "MODEL": str(MODEL)},
                          capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == {"ok": True}


def test_media_app_is_registered_stashed_in_apps():
    html = (WEB / "app_shell.html").read_text(encoding="utf-8")
    rail = (WEB / "js" / "shell" / "shell_rail.js").read_text(encoding="utf-8")
    shell = (WEB / "js" / "shell" / "app_shell.js").read_text(encoding="utf-8")
    assert 'id="nav-media"' in html and 'data-page="/media_page.html"' in html
    assert "'nav-media'" in rail.split("const CANONICAL_RAIL_ITEM_ORDER")[1].split("];")[0]
    assert "'nav-media'" in rail.split("const DEFAULT_RAIL_HIDDEN =")[1].split(";")[0]
    # Read-only view over existing chat data: shipped without an experimental gate.
    assert "'nav-media'" not in rail.split("const EXPERIMENTAL_RAIL_APPS =")[1].split("};")[0]
    assert "'/media_page.html': 'Media'" in shell
    page = (WEB / "media_page.html").read_text(encoding="utf-8")
    assert page.index("chat_media.js") < page.index("media_library_model.js") < page.index("media_library_page.js")
    assert "chat_lightbox.css" in page and 'data-page="media"' in page
