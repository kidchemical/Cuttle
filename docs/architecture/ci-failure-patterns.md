# CI failure patterns

Read this before a release push and whenever CI fails. Each theme below has
failed CI more than once. It lists what the failure looks like, the fix, and the
local check that catches it before GitHub does. When CI fails for a new reason,
add a line to the [ledger](#ledger) and, once a theme repeats, a check.

Owner of the local checks: `core.prepush_tests`, called by the pre-push hook
(`.cuttle/scripts/git-hooks/pre-push`) after the secret scan.

## Before pushing

| Push | What runs | Your part |
|---|---|---|
| Branch (`dev`, feature) | Hook: `src/tests/quality` plus the tests that touch the changed files, in a scratch worktree of the pushed commit, with `EncodingWarning` as an error (300 s budget; over budget warns, does not block) | Nothing. A failure blocks the push and names the tests. |
| Release tag `vX.Y.Z` | Hook refuses the tag unless that commit has a recorded `release-check` pass | Run `release-check` on the release commit first (below), detached; ~15 min. |

```bash
PYTHONPATH=src .venv/bin/python -m core.prepush_tests release-check HEAD   # full suite, records the pass
PYTHONPATH=src .venv/bin/python -m core.prepush_tests status HEAD          # already checked?
PYTHONPATH=src .venv/bin/python -m core.prepush_tests fast                 # what a branch push runs
```

Windows PowerShell: `$env:PYTHONPATH = "src"; .\.venv\Scripts\python.exe -m core.prepush_tests …`.
When a push's changed sources touch more than 60 test files (a batch of
commits, or a widely imported module), the branch tier drops the related tests
and runs only the changed ones, so a stale pinned test (theme 4) reaches only
`release-check`. Run `release-check` before pushing a large batch. A failed
`release-check` keeps pytest's full output at the `log` path it prints.

Targeted runs by hand need `PYTHONWARNDEFAULTENCODING=1` as well as
`-W error::EncodingWarning`; the flag alone never fires.

`CUTTLE_PREPUSH_TESTS=0` skips the test gate for one push (the secret scan
still runs); say why in the push message.

The release check runs on this machine's OS only. On a Linux Host, Windows
themes below other than encoding still reach CI first; a Windows checkout can
run the same `release-check` before tagging.

## Themes

### 1. Text in the platform default encoding (Windows)

Most common Windows failure. Looks like `UnicodeDecodeError: 'charmap' codec`,
`'utf-8' codec can't decode byte 0x97`, or `…` / `·` / `→` arriving as `�`.

- **Cause:** `open()`, `Path.read_text()`/`write_text()` or
  `subprocess.run(..., text=True)` without `encoding=`. Windows uses cp1252;
  Linux uses UTF-8, so it passes locally.
- **Fix:** pass `encoding="utf-8"` (add `errors="replace"` for tool output you
  only display).
- **Caught by:** both tiers run with `PYTHONWARNDEFAULTENCODING=1 -W
  error::EncodingWarning`, so the missing argument fails on Linux too — but
  only on lines that run. `quality/test_text_encoding.py` checks every call
  statically, including `if os.name == "nt":` branches.

### 2. Line endings (Windows)

Looks like `'new pending edit\r\n' == 'new pending edit\n'` or
`'hunter2-Correct-Horse\r'`.

- **Cause:** a file written in text mode on Windows gets `\r\n`, or a
  subprocess's output keeps `\r`. `.gitattributes` fixes checkouts, not files a
  test writes at runtime.
- **Fix:** write with `newline="\n"` (or bytes) when content is compared
  byte-for-byte; `.rstrip("\r\n")` / `splitlines()` when reading tool output.
- **Caught by:** CI Windows only.

### 3. Paths (Windows)

Looks like `'D:\\a\\…\\wt' in {'D:/a/…'}`, `WinError 206 The filename or
extension is too long`, `Invalid argument` for a name with `\n` or `:`, or mode
bits (`(33206 & 511) == 493`).

- **Fix:** compare `Path(...).resolve()` or normalise with `.as_posix()`; keep
  temp names short; skip POSIX-mode assertions on Windows with a reason.
- **Caught by:** CI Windows only.

### 4. Stale tests elsewhere in the suite

About a third of failures, on every OS. Looks like `ReferenceError: X is not
defined` from a JS harness test, `substring not found` from a source-text test,
or an exact set/list assertion missing a new key.

- **Cause:** a change renames or adds something that a test far from the edited
  file pins. Targeted test runs never reach it.
- **Fix:** update the pinned test in the same commit; prefer asserting the
  behaviour over exact source text or exact key sets.
- **Caught by:** the branch tier when the test mentions the changed module;
  otherwise `release-check`.

### 5. Timing and concurrency

Looks like `assert 2 == 1` in concurrent pollers, `51 == 50` in pruning,
`DID NOT RAISE CancelledError`, `PermissionError` racing a status file, a
`FileNotFoundError` on a `.tmp` rename.

- **Cause:** a test that depends on scheduling, sleeps or a shared file; CI
  runners (Windows especially) are slower and more parallel.
- **Fix:** wait on an event or poll with a deadline instead of sleeping; give
  each writer its own temp name; never assert on wall-clock ordering.
- **Clock:** `time.monotonic()` counts from boot, so a fresh CI runner reads
  small values. Never use `0.0` as "never happened" for a monotonic stamp; use
  `None`, and pin the clock low in the test.
- **Caught by:** sometimes locally. A test that fails once in CI and passes on
  rerun goes in the ledger as flaky and gets fixed, not retried.

### 6. Packaged Host and desktop

Looks like `In --require-hashes mode, all requirements must have their versions
pinned` in *Bundle pinned Python runtime*, or a desktop smoke timeout.

- **Cause:** `electron/python-requirements.lock` missing a dependency for one OS.
  pip evaluates transitive markers on the machine running `--lock`, so a lock
  written on Linux misses Windows-only wheels (and the reverse).
- **Fix:** list the OS-specific dependency in `src/requirements/requirements.txt`
  with its `sys_platform` marker, add it to `WINDOWS_ONLY` / `LINUX_ONLY` in
  `electron/bundle-python.js`, relock, and keep unrelated pin changes out of a
  release.
- **Caught by:** CI packaged-host only.

### 7. CI environment

Looks like `symlink ancestor refused: …/temp`, `File exists: …/temp`, or an
`HTTPError 502` from a download.

- **Cause:** the runner layout differs from a dev checkout (no `temp/`, a
  `--basetemp` inside the repo), or a network blip.
- **Fix:** create scratch dirs with `exist_ok=True` under pytest's `tmp_path`;
  rerun a network failure once and note it.

## Ledger

One line per CI failure that reached GitHub: date, workflow/job, theme, test or
step, fix. Keep entries short; the theme sections hold the explanation.

| Date | Job | Theme | Failure | Fix |
|---|---|---|---|---|
| 2026-11-23 | Quality / python (both) | 5 | `test_auth_preflight_reads_text_not_exit_code`: a `0.0` "never checked" stamp read as fresh because `monotonic()` starts near 0 on a just-booted runner | `None` sentinel; test pins a low clock |
| 2026-11-23 | Quality / python (ubuntu) | 5 | `test_no_bubble_at_all_keeps_the_cards_on_the_strip_until_one_exists` raced the background runner's status write | test waits for the runner |
| 2026-11-23 | Quality / python (windows) | 1 | `test_home_relocation` robocopy `_run` without `encoding=` (Windows-only branch) | `encoding="utf-8"`; static `quality/test_text_encoding.py` |
| 2026-11-23 | Quality / python (windows) | 5 | `test_process_updates_are_serialized` `[Errno 13]` from the settings lock's pad-byte write | lock byte 0 past EOF, no pad write |
| 2026-11-20 | Desktop smoke / packaged-host (windows) | 6 | `pywin32-ctypes` not in lock (keyring on Windows) | OS backends listed with markers; lock fixed |
| 2026-11-20 | Quality / python (both) | 4 | `test_live_status_extras_carries_background` missing `steers` | test updated |
| 2026-11-20 | Quality / python (windows) | 1 | `test_chat_supervision_studio_model` read Node output as cp1252 | `encoding="utf-8"` |
| 2026-11-14 | Quality / python (windows) | 5 | `test_two_concurrent_pollers_resume_the_chat_exactly_once` | — |
| 2026-11-14 | Quality / python (both) | 4 | `stampLiveBubbleTurnRow is not defined` in a JS harness test | harness updated |
| 2026-11-13 | Quality / python (windows) | 1 | `test_wake_line_says_what_starts_the_next_turn` (`charmap`) | — |
| 2026-11-13 | Quality / python (windows) | 5 | `test_the_claim_alone_keeps_it_exactly_once_without_the_lock` | — |
| 2026-11-12 | Quality / python (windows) | 3 | worktree path `\` vs `/` comparison | — |
| 2026-11-09 | Quality / python (ubuntu) | 4 | `test_relative_cuttle_home_is_rejected` | — |
| 2026-11-07 | Quality / python + browser (ubuntu) | 7 | `symlink ancestor refused: …/temp` | — |
| 2026-11-03 | Quality / python (ubuntu) | 5 | `test_card_stop_kills_the_detached_run` (`.tmp` rename race) | — |
| 2026-10-31 | Quality / python (windows) | 1, 2 | `charmap` decode; `'…\r' == '…'` | — |
| 2026-10-20 | Quality / python (windows) | 2 | agent-review hunks `\r\n` vs `\n` | — |
| 2026-10-12 | Quality / python (windows) | 1 | `test_pane_lifecycle` `charmap` encode of `↔` | — |
| 2026-10-11 | Quality / python (windows) | 3 | `WinError 206` filename too long | — |
