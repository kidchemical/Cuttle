# Browser chat stream reader review

## D1 behavior correction — 2026-10-03

A read timeout previously abandoned `reader.read()` and started another read.
Late bytes could satisfy the abandoned read and disappear, including a final
reply. Actual production-page Chromium characterization reproduced this;
empty history/parked fixtures prevented recovery from masking stream delivery.
The old-page desired regressions observed three/four native reads instead of two.

The page now retains one read promise across timeout observations. It clears
that handle only after consuming a result or propagating a non-timeout error.
A controlled timeout/settlement ordering test also exposed an intermediate
proposal that cleared the handle too early: the exact helper lost byte 65.
The accepted helper returns byte 65 using one native read. This is controlled
scheduling evidence, not an observed HTTP packet-timing claim.

The eight-second read ticks, iframe/standalone ten/twenty-second hold policy
and existing deadline overshoot remain. Parsing, session adoption, progress
paint, final-result ordering, Stop guards and pending-result recovery remain
unchanged. No new successful-response cancellation or transport retry policy.

`e2e/test_chat_stream_reader.py` drives actual production assets with a native
ReadableStream, explicit abort modeling and observed native read counts.
Thirteen cases pass without skips: delayed single replies in both contexts,
stall/detach, Stop after timeout, split UTF-8/JSON/delimiters, multi-frame reads,
malformed/unknown frames, busy, EOF and disconnect. Background parked polling
and success acknowledgment are normal; fixtures provide no assistant rescue.

Manager evidence: independent old-page baseline **2 passed**; initial proposal
**27 combined browser cases / zero skips** and **86 lifecycle/architecture
checks**; after the settlement correction, **7 targeted read/Stop/real-shadow
cases / zero skips**. Contributor's final full reader file: **13 / zero skips**.
The intermediate proposal is not the integrated source. No live vendor/database
or daemon was used by these tests. Three shadow cases exercise real HTTP,
streaming, cancellation, persistence and reload with deterministic execution.

Behavior-fix runtime delta: page **+18 lines**; new characterization/regression
file added. Extraction is reviewed separately, with existing lifecycle owners
remaining authoritative. Browser byte ownership is still page-side at this checkpoint.

The source-only checksum recovery was proved to reject conflicts before any
write and restore only the page in a disposable copy. Install-local artifact:
`temp/architecture-resume/d1-review/recover.py --apply`, run with the project
venv from an independent terminal. It touches no state/database or service.
Newer overlapping source changes make it refuse; do not apply older recovery
patches blindly. This frontend checkpoint takes effect on normal page reload;
no Flask restart is required.
