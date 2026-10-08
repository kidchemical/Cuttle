# Progress grid

### Progress grid (experimental, any job)

Use it when a job has many discrete items and seeing *which* items are done
matters: frames, files in a migration, tests in a suite, pages crawled, shards.
Not useful for one long opaque step — keep a bar there. Gate:
Settings → Experimental → **Progress grid** (`progress_grid`); off drops the
grid from new status files and bars keep working.

```json
"grid": {
  "unit": "test", "title": "Test suite", "marked_label": "retried",
  "total": 3, "groups": ["shard-1", "shard-2"],
  "cells": [
    {"key": "test_login", "state": "completed", "group": "shard-1"},
    {"key": "test_logout", "state": "running", "group": "shard-2", "marked": true},
    {"key": "test_admin", "state": "failed", "note": "AssertionError"}
  ]
}
```

- `key`: int or short string, unique. `state`: `pending` / `running` /
  `completed` / `failed` / `missing` / `cancelled` / `skipped`.
- `group` (optional) gets a stable colour; matching `bars[].id` share it.
- `marked` draws an outline; `marked_label` says what it means.
- `inventory` (optional): `verified` (host checked output) or `reported`
  (producer's claim). Omit when neither applies.
- First 2,048 cells render; `total` drives the "N more" note.
- Write: `python -m api.job_watch write … --grid-json '{…}'` or `write_status(..., grid={…})`.
- Mesh frame batches emit one automatically → `cuttle-workers.md`.


### Frame grid (Progress grid producer)

`batch-watch` emits the generic watch `grid` (`action-forms.md` → Progress grid)
with `unit: "frame"`, one group per worker, and `marked` = gap-fill. It needs
the `progress_grid` experimental flag. Worker bars include advertised Blender GPU labels.

Local output inventory takes precedence over shard success (`verified`). With
inaccessible output the grid is `reported`; successful shard spans provide
completion evidence. Reclaimed or overlapping chunks without clear producer
evidence show completed frames with no group. `running` cells mean a worker is
active on the containing chunk, not proof that Blender is processing that frame.
Terminal snapshots retain the grid. Teardown of this producer:
`build_batch_frame_grid` and its guarded call in `device_workers.platform`.
