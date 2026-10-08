# UI state synchronization audit — 2026-10-08

Continuation of CH-001118-5 and Cursor's incomplete survey in CH-001118-6.
Audited dev at `0b13b64`, including the detached completion fix `72a9b7d`.
The survey below records the pre-fix behavior. Implementation and verification
are recorded at the end.

## Finding

The recurring problem extends beyond activity indicators to shared preferences,
agent model/effort controls, action-form attention, and follow-up queues. It is
not evidence that every observer or every UI control is defective.

Three different problems recur:

1. **Recoverability:** unread/attention depends on observing a transition or
   mounting a particular DOM tree, instead of reading a durable session fact.
2. **Conflicting copies:** several frames write the same storage map; other
   features mix server snapshots with mutable page caches without a revision.
3. **Lifetime errors:** an asynchronous result for one session mutates the
   current session's state after navigation, or releases another write's guard.

Pure decision owners such as `CuttleChatActivity` and `CuttleSpaces` do not fix
incorrect inputs or page-side persistence/lifetime errors. MutationObserver,
storage events, and polling are delivery mechanisms; their use alone is not
the architectural defect.

## Extent

| Surface | Ownership / recovery at audit time | Assessment |
| --- | --- | --- |
| History green/red dots | Browser prefs plus completion handlers and a limited message/read-time fallback | Highest exposure: missed completions, lost writes, device-local state |
| History/title spinners | Local generation, running-id set, pending-form set, server sessions/live status, pending-result recovery | Several authorities; an old form entry can resist a server idle snapshot |
| Spaces dots and history project aggregates | Derived from chat attention, frame pushes, prefs and server observations | Inherit bad chat inputs; not an independent durable source |
| Blue input dot | Mounted unlocked action cards write `awaitingInput` into browser prefs | DOM observation, not server-owned question state; unreliable across devices without hydration |
| Next-send agent chip | Server `composer_selection` + revision, local sticky state and draft controls | Stronger design, but failed publication has no durable retry and prefs share the vulnerable map |
| Agent model/effort shown in chip | Server pins plus mutable palette supplement, per-provider fetch/set callbacks and local draft pins | Navigation and response-order gaps; a delayed Muse model save demonstrably changes the destination chat's live model |
| Executing agent / message badges | Send-time metadata and live-status execution identity, separately from next-send prefs | Correctly distinct lifetimes; preserve this distinction |
| History pane-number badge | Shell computes a full snapshot from visible columns and reported sessions; frames consume it | Better ownership; no equivalent missed-completion dependency found |
| Follow-up queue and amber/yellow dots | SQLite list, mutable page queue, dirty/take booleans, append/replace/take APIs | Significant exposure: late response can replace another chat's queue; failed writes become clean; multi-client replacements lack revisions |
| Draft text and new-chat controls | Per-chat/per-pane browser keys, local restoration | Local persistence is appropriate; normal scope/restore journeys pass. Controls still share the prefs map |
| Selected project | Server session project plus prefs, hydrated/list caches | Server recovery exists; asynchronous PATCH writes lack ordering/revision protection |
| Spaces layout/order and focus | One shell singleton per window, persisted local tree | No evidence of the activity defect within one shell. Independent windows editing the same tree have no merge/version protocol |
| Completion notifications | One browser broker, persisted starts/delivery IDs and serialized delivery/locks | Better ownership/deduplication; still requires a client to observe completion. It is intentionally not background push |
| Settings | Server SettingsManager for server preferences; explicit device-local preferences elsewhere | Sampled ownership is distinct from chat activity. This audit does not establish that all Settings paths are race-free |

## Confirmed helper-level reproductions

Run `node temp/ui-state-audit/probe.cjs` from the repo root. The script extracts
production page helpers and supplies isolated storage, transport and page
state. No live API calls, real agent executions, or user-data writes occur.
These are deterministic helper-level reproductions, not full browser/API
journeys or a measurement of occurrence rates in the live application.

### Shared prefs can lose unrelated changes

Two frames cache the prefs map. A writes unread for chat 101. Before B receives
the queued storage event, B writes a chip for chat 202 from its old cache. B's
whole-map write deletes A's row. Delivering the storage event afterward only
loads the already damaged map.

Owner: `src/web/js/chat/chat_page.js:1758` (`readSessionPrefsMap`) and `:1797`
(`updateSessionPrefs`). The same map contains sticky chips/removal, draft
controls, project fields, read/unread and `awaitingInput`. The risk therefore
extends to every field in the map, even when the writers are editing different
chats. Cache invalidation improves eventual reads but does not serialize writes.

### An old pending-form spinner survives authoritative idle

With chat 101 in both running and form-awaiting sets while viewing 202, pass a
session snapshot with `generating=false` and `awaiting_action=false`. Chat 101
stays in both sets: the cleanup explicitly preserves form-awaiting entries.

Owner: `chat_page.js:16246` (`applyGeneratingFlagsFromSessions`). DOM scans in
`:16055` can clean this up when a relevant scan occurs; the server snapshot
alone cannot. This reproduces the reconciliation defect, not a claim that all
navigation paths leave such an entry behind.

### Follow-up response crosses sessions

Start `persistFollowupAppend` for A, switch the current session and live queue
to B, then resolve A's successful response. `applyServerFollowups` replaces
B's live queue with A's result and patches B's cached session row.

Owners: `chat_page.js:17807` and `:17793`. The transcript sync path has navigation
guards; this mutation response does not have the same captured-session fence.

### Failed follow-up write loses its dirty guard

Make a queue PUT fail. `persistFollowupPut` still calls `markClean`; subsequently
apply an older empty server snapshot. The unsaved local queue disappears.

Owner: `chat_page.js:17827`. The dirty flag is also a boolean, so overlapping
writes can clear it before all operations have settled. A failure needs an
explicit unsynced state and recovery policy, not unconditional cleanup.

### Failed agent selection can revert on reload

Reject `publishSharedComposerSelection`'s PUT. It warns and releases `pending`
without retaining a durable unsynced selection/retry record. The adoption
predicate accepts the old server revision once pending clears, and a fresh
page can hydrate the previous server chip over the saved local selection.

Owners: `chat_page.js:9438`, `chat_agent_model.js:206`. An already applied
identical revision can be skipped within the current page; reload/new-page
hydration still exposes the mismatch. This is a failure-recovery gap, not
evidence that successful agent picks normally fail to sync.

### Delayed model save mutates the destination chat

Start `persistMuseModelSelection('model-for-A')` in A; switch to B and set B's
live model and dirty flag; resolve A's successful POST. The callback puts
`model-for-A` into the current palette supplement and clears B's dirty flag.

Owner: `chat_page.js:6063`. The callback captured `sid` for the request/key but
does not check that it still owns the active session or selection generation
before updating global supplement state and repersisting the current chip.
This establishes that agent controls have a related lifetime defect too.

## Additional risks established by inspection

- **Unread is not exclusively an edge flag.** `chat_activity.js:100` also
  compares an existing `lastReadAt` with an assistant message timestamp when
  a message-bearing session object is available. This fallback does not provide
  universal recovery: it requires a local watermark and the relevant messages.
  The server session schema/API has no durable read watermark.
- **The shell completion fallback is limited.** `allSpaceChatIds` gathers
  chats in saved Spaces/live panes (capped at 60). `noteServerSnapshot` only
  identifies running → idle after a prior running observation. A completion
  between polls, after restart, or outside that tracked set can be missed.
  Idle can also follow Stop/restart, not necessarily a newly saved reply;
  `markFinishedBackgroundChatsUnread` does not examine the reply/error outcome.
- **Blue input is different from `awaiting_action`.** `auth_api.py` derives
  `awaiting_action` from Flask restart jobs. It is not a durable registry of
  unanswered questions. A device without a mounted card/prefs observation
  cannot recover the blue dot from that field. Old local blue flags can remain
  until the chat is hydrated after another device answers.
- **Local-only chip writes are not all missing publications.** Many of Cursor's
  counted calls follow model/effort endpoint saves or label repaint, while
  explicit agent selection, removal and send publish the shared selection.
  Counting local calls alone overstates the defect. Model pins and selection
  also use separate save paths; their coherence deserves explicit tests.
- **Follow-up replacement lacks concurrency control.** `set_followup_queue`
  replaces the entire SQLite list without a revision, so a stale device can
  erase another device's appended item. The pre-drain PUT can reintroduce
  already taken items from a stale local copy.
- **The purported atomic queue take needs review.** `append_followup` and
  `take_followup_queue` perform SELECT followed by UPDATE without beginning a
  write transaction before SELECT. The default sqlite connection does not
  reserve a write transaction for that SELECT; WAL/busy_timeout do not turn
  it into an exclusive claim. Concurrent append/take behavior was not
  reproduced in this audit and should get a separate concurrency test.
- **Project saves are unordered.** `persistProjectForCurrentSession` starts
  fire-and-forget PATCH requests with no per-session serialization or expected
  revision. Out-of-order rapid picks can leave server and local choices
  different. This was inspected, not reproduced here.
- **Pane ownership is local to a window.** History numbering uses the shell's
  live DOM order, not the server's last-writer `/api/shell/panes` snapshot.
  That server snapshot is for agent inspection. Do not turn it into the
  authority for every browser window's local pane numbering.

## Verification and missing coverage

- **66 passed:** activity/model/composer/queue/draft/chip, shared composer API,
  Spaces activity, shell panes, input-card DOM, and completion broker suites.
- **25 passed:** real browser draft-agent/draft-control and shell-layout suites,
  using production assets with isolated fake APIs.
- **Six targeted probes reproduced the gaps above.** Their assertions currently
  verify the observed defect, so they are investigation evidence, not permanent
  passing regression tests that bless broken behavior. The scratch script is
  gitignored. Promote each scenario to a test expecting correct behavior when
  fixing its owner.

Needed regressions: concurrent prefs writers before storage-event delivery;
question answer on another device; switch while model save/queue append/take
is in flight; overlapping and failed writes; cross-device queue edits and
concurrent claims; completion entirely between polls/reload; failed selection
save then reopen. Normal sequential roundtrips passing does not test these.

## Recommended fix order

1. **Fence asynchronous mutations immediately.** Capture session + operation
   generation for model/pin and queue callbacks. Results update their own
   session's state; only the active matching session paints. Keep unsynced
   mutations pending on failure, and make write ownership explicit.
2. **Remove whole-map preference races.** Separate records by canonical session
   and state category, and serialize/transactionally merge writes across
   frames. A fresh reread before writing reduces cache losses but does not
   provide atomic cross-context updates by itself.
3. **Make chat attention recoverable on the server.** Use a last eligible
   assistant-message sequence/id and a per-user read watermark, rather than
   relying only on client clocks. Define whether reads synchronize across
   devices and what counts as actually viewed. Give manual unread an explicit
   policy. Represent active unanswered questions separately from action-job
   execution; do not infer them from every historical unlocked card.
4. **Make queue operations transactional and revisioned.** Prefer item-level
   operations and an actual exclusive claim for take. Add conflict handling
   before removing client fallback paths; document failure/retry semantics.
5. **Consolidate activity delivery after ownership is settled.** One shell
   broker can fetch/reconcile snapshots and fan out, with local optimistic
   state only for its active turn. `/api/activity/stream` already exists as a
   live-status change-counter notification. It is not consumed by the shell
   here and does not supply a durable unread/question snapshot or replay log;
   reuse/extend its owner rather than claiming SSE alone solves recovery.
   Preserve reconnect snapshot recovery and reduce duplicate polling.

Keep pane numbering, device-local layout, and draft text scoped to their
existing owners. A universal store or replacing every MutationObserver is
not required by the evidence. The useful redesign is explicit authority,
durable recovery where needed, and captured lifetimes at asynchronous seams.


## Implemented correction

The confirmed races are corrected in the owning slices. Delivery mechanisms
remain useful; durable snapshots and explicit mutation lifetimes now determine
which state they deliver.

| State | Owner and policy |
| --- | --- |
| Unread / error / question attention | `api.chat_attention`, in the auth database. A persisted assistant row advances `reply_id`; displaying the current transcript advances a monotonic `read_id`. Reads share across devices for the session's owner. System notices do not advance reply attention. The latest assistant card requires input only until a later user message or a persisted card lock/dismissal. |
| Running work | Existing delivery/live-status owners; running flags are not stored permanently in attention. Authoritative idle removes stale background form entries. Mounted pending work in the open chat may still supplement live status. |
| Manual unread | Explicit server flag; ordinary viewing clears it. The existing active-viewport hold prevents immediate accidental clearing after the manual action. |
| Device preferences / drafts | `CuttleSessionPrefs`, with separate atomic keys per session **and field**. Legacy whole-map preferences are read-only fallback. Clearing a record leaves tombstones, preventing legacy resurrection. Different fields cannot overwrite one another's storage records. Concurrent edits to the same field use storage's last-write policy. |
| Next-send chip | Existing revisioned server composer selection; the page serializes publication and keeps a durable local outbox until success. A failed save blocks old snapshot adoption and retries on session hydration. Executing-turn metadata retains its separate lifetime. |
| Model / effort / project mutations | `CuttleChatMutations` orders writes per session/resource. Model/effort reads and saves capture session, navigation sequence and selection generation. Catalog hydration stays available for drafts, while delayed defaults cannot replace a newer pin. Project PATCH writes are serialized within a page. |
| Follow-up queue | `api.chat_followups` begins `BEGIN IMMEDIATE` before reading/mutating. Revisioned PUT/take reject stale snapshots with HTTP 409. Client rebasing preserves remote additions/removals and only applies local field edits. Unsynced queue edits survive failed requests/reload. Retry records are scoped per pane/tab consumer so two views cannot erase each other's failed edits/claim. State objects belong to a captured session, including restoration when navigation/editing races a claim. First-turn id assignment transfers early queued prompts from the draft. |
| Activity delivery | One `CuttleActivityBroker` per shell coalesces session-list reads, consumes `/api/activity/stream` wakeups with full reads capped to one per two seconds during progress pulses, and fans full snapshots to frames/Spaces. Account changes fence cached/in-flight snapshots; reconnect/open and the existing poll heartbeat recover missed wakes. Embedded detached completion watchers are retired; standalone chat keeps its existing recovery loop. |

Upgrade policy: existing replies start at a read baseline so installing the
change does not mark the entire chat history unread. Existing browser unread
flags can import once. Server read/manual actions fence subsequent legacy
imports. New replies persist their attention even with no browser watching.
Clean snapshots require no per-chat migration writes, avoiding a storage-write
storm on a large first session list.

The schema is additive in the existing Cuttle-home auth database. The queue
API keeps legacy no-revision callers compatible; the updated UI uses revisions.
Old clients should reload to adopt the new protocol. Claim receipts retain a
nonempty taken batch so a lost HTTP response can be retried without claiming a
new batch; retries return fresh remaining-queue state. This is a claim receipt,
not a guarantee of exactly-once agent execution after a browser crash between
claim receipt and dispatch. That execution handoff remains separately owned by
the turn coordinator. Device-local layouts retain their existing last-write
policy across independent windows.

## Implementation verification

Permanent regressions replace the six scratch defect probes. Coverage includes
interleaved field writes, A→B→A navigation, successful pin writes followed by a
late catalog read, failed composer/read/queue writes, queue claims racing
navigation, a lost claim response after reload, draft id assignment, and
coalesced snapshot reads. Real SQLite threads test simultaneous appends/takes
and stale replacements; attention tests cover migration, stale reads, errors,
persisted form locks, ownership and API acknowledgments.

Real browser journeys against an isolated production Flask child prove:

- Background completion changes a history spinner to green without refresh.
- A completion with no browser open is recovered on a fresh device and reload.
- Displaying that reply on another device clears the first device's unread dot.
- An unmounted question produces blue attention and a remote answer clears it.

The child uses a private database and deterministic executor; live user chats,
vendor CLIs and the hosting Flask are not used for these journeys. Browser
catalog/draft/pane tests also use production assets with isolated APIs. Final
verification: 1,165 scoped tests passed (8 optional skips); 58 browser journeys
passed across the full matrix. After the final preference/broker cleanup, all
28 directly affected browser journeys passed; 993 chat/shell/boundary tests
passed and the remaining obsolete source assertion passed after being updated
to the new preference owner. The Node suite covers 17 race/recovery scenarios.
No hosting Flask restart or user-chat execution was performed.
