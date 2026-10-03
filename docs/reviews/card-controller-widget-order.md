# Card controller and widget ordering review

Reviewed 2026-10-02 against main `5f217773`. C2b and the separate C3 widget
ordering fix are integrated. The widget Python change awaits the normal
user-selected Flask restart; no live service was restarted by the manager.

## C2b: one owner for card effects

`CuttleChatActionCards.mountCards(chatRoot, host)` in
`src/web/js/chat_action_cards.js` owns card submission, dismissal, locks,
watch/restart polling, choice storage, and linked restart recovery. The page
supplies explicit transport, session, send, history, and paint capabilities.
Pure model/render planning stays in `chat_action_forms`; the existing four
`chat_activate` helpers stay unchanged. See the maintained
[owner map](../architecture/repository-map.md#frontend).

Each mounted card owns an abort signal and timers. Removing it disposes that
lifetime; moving it within the same root preserves it. Remount installs a
fresh lifetime, and old continuations cannot paint or affect the successor.
Controller destruction stops root observation and restart polling. Request
ownership is explicit: the canonical follow-up write continues after a card
detaches, while its stale UI continuation becomes inert.

The controller coalesces logical Discord follow-up requests while they are
in flight. Only confirmed server persistence records the existing storage
acknowledgment. Rejected drafts added response caches, retry deadlines, or a
durable pre-request claim; none was integrated. Reload before a server write
can retry without a permanent stale claim. Reload after an unacknowledged
write remains ambiguous and may duplicate it; server-side idempotency is a
separate problem. This is not an exactly-once guarantee across reload,
network failure, or unavailable storage.

An unchanged-code browser control already produced two held requests before
reload and another afterward. The accepted controller produces one before
reload and one ambiguous retry afterward. The pre-write missing-row branch
also passed on unchanged code. These controls distinguish existing delivery
limitations from the controller's in-flight coalescing improvement.

`chat_page.js` decreases from 23,585 to 22,465 lines: **1,120 fewer**. The new
controller has 1,563 lines, including explicit cleanup and ownership. This
improves the working boundary rather than reducing total frontend LOC.
Ordinary card-effect changes now need the controller and its tests; the
page's host adapter remains necessary when changing send/history interfaces.

## C3: fix the demonstrated bug, preserve sound paint paths

The existing append/prepend/update adapters already compose message planning,
activation, navigation, and viewport effects. No measured benefit justified
another message-view owner in this slice. Navigation remains page-owned;
the pure `chat_messages` module does not gain DOM effects.

Investigation reproduced a separate server bug: `apply_widget_tags_to_store`
applied database writes in reverse tag order because string replacement
needed reverse offsets. A base tag followed by a same-id patch in one reply
lost the patch. The fix keeps store operations and returned `touched` rows in
document order, then applies collected text replacements from the end.
Later base tags still intentionally replace earlier state. Unsupported
widget types remain untouched; chip/status/description/scope behavior stays
with the existing widget owner.

Two client fallback defects remain separately recorded: dedupe by bare id
can suppress another session's raw widget tag, and a base plus same-id patch
in one paint suppresses the patch. Naively enabling both writes can replay
non-idempotent `add` operations or overwrite patched state. The current store
keys identity by user and widget id. Those identity/replay semantics need a
separate decision before changing raw history ingestion; moving code alone
would not fix them.

## Verification

- Combined current-main candidate: **39 browser cases passed, zero skips**
  across card effects/controller/render and static/real-shadow Stop/resend.
- Broad offline suite, retaining the existing `unit/` exclusion: **2,558
  passed, 61 explicit skips**; its only failure was the disposable checkout's
  missing venv link. Restoring that test prerequisite and rerunning the
  daemon-helper dry-run test passed. No production fix or guard bypass was
  needed. This is 2,559 successful distinct offline cases across the runs,
  not a claim of one wholly green broad run.
- Widget suite: **24 passed**; workflow/persistence/coordinator neighbors:
  **42 passed**. Five ordering tests failed against the old implementation.
- One additional real-shadow widget journey passed on the combined candidate:
  real Flask, successful SSE response and terminal event, one assistant row,
  correct widget order through HTTP before/after reload, no attempted client
  fallback mutations, zero unexpected guard denials or page errors.

All integrated runtime/test files match the tested candidate hashes. Existing
rename and Brain changes were preserved. The independent-terminal recovery
artifact restores only the three changed existing runtime files and removes
the new controller asset; it touches no configuration or database and never
restarts services. Conflict refusal and restoration were proven in a
disposable checkout. Activation follows
[development-instance safety](../architecture/development-instance-safety.md).
