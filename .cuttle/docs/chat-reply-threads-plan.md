# Plan — Reply threads in chat (steers, follow-ups, sub-agent replies)

Status: built 2026-11-08 (CH-002161), flag `chat_reply_threads` default on.
The phases below are the original plan; **As built** records where it changed.

## As built

- **Rows never move.** `CH-…-N` is DOM order on the client, so the thread is
  drawn with classes, not a nested list: replies get `thread-reply` (hidden with
  `thread-reply-hidden` while closed), the root gets `thread-root` and the
  "N replies" bar is mounted *inside* the root bubble (after its footer). In
  practice replies already follow their root in id order (the turn row opens at
  turn start), so nothing has to move.
- **One observer, no call sites.** `chat_threads.js` re-derives threads from the
  transcript on every DOM change (MutationObserver): history load, sync, the
  optimistic steer bubble, the live bubble turning into the saved reply,
  sub-agent cards updating. Structural changes settle before paint; text-only
  changes (streaming) settle next frame. Its own bar edits are dropped
  (`takeRecords`).
- **Root / reply rules** (pure, `chat_thread_model.js`): first assistant row of
  a turn id = root; later assistant rows of that turn = follow-ups (follow-up
  rows don't store `background_followup`; the shared turn id is enough); steers
  by `turn_id`, else (older rows, the unsaved optimistic bubble) the current
  turn's root if it is above them. A steer above its root, or naming a
  different turn, stays a plain row.
- **Server**: `steer.steer` returns the open turn row's id; `steer_detached`
  overrides it with the detached run's first-reply query id;
  `chat_coordinator.steered_user_meta` writes `{steered, steered_agent, turn_id}`
  for both `/api/chat-steer` and background steers.
- **Open state**: your open/close is saved per chat (localStorage, capped); a
  live thread (or one you just steered into) opens by itself; nothing closes a
  thread except you. The open state carries across the live bubble → saved
  reply swap (new key inherits from the rows it shares).
- **Reload mid-turn**: the in-progress turn row is not in history, so the
  working bubble is appended below steers already saved into its turn. Once
  live status stamps its turn row (`data-turn-message-id`) the controller puts
  it back above them (it is not a CH-…-N record, so nothing renumbers).
- **Rows on screen never vanish**: a thread that forms around rows you already
  saw (its root arriving later, a reload mid-turn) opens instead of hiding them,
  unless you closed that thread yourself.
- **Answer reveal**: when the live bubble turns into the answer above an open
  thread and the replies pushed it mostly out of view (< 40 % visible) while
  you were following the bottom, the answer's start scrolls into view.
- **Unread**: only agent replies (follow-ups, finished sub-agents) — your own
  steers never. A reply arriving while the thread is closed, or newer than your
  last visit's watermark, shows "● N new" + a one-line preview until opened;
  unread keys survive reloads and lazy history pages.
- **Sub-agents**: only *finished* children are replies (name, outcome, the
  fleet `detail`, child `CH-` link) — no extra fetch or field, the fleet entry
  already carries it. Running children show as "N working" on the bar and
  never open the thread (fleet cards stay the progress view).
- **Reveal**: nav jumps and find-in-chat dispatch a bubbling `cuttle:reveal`
  on the target row; the controller opens its thread first.
- **Flag**: the client only turns threads off when the server lists the flag
  off or the kill switch is on (an older Flask that doesn't list it keeps the
  default, so nothing flashes on then off).
- Tests: `src/tests/chat/test_chat_thread_model.py` (node),
  `src/tests/e2e/test_chat_reply_threads.py` (browser), steer `turn_id` in
  `harness/test_agent_steer.py` + `harness/test_claude_background_detach.py`.

## Problem

A steer is saved as a new user row at the end of the transcript, but the
assistant row for that turn was opened at turn start (`api.chat_turn_row.open_row`)
and is filled in place when the turn ends. What you see:

```
[you]    Fix the login bug
[agent]  ← final answer lands here, later
[you]    (steer) also check the token refresh   ← sits below the answer
```

So you scroll back up to read the answer, and old chats read out of order.
Claude's detached background follow-ups add more assistant bubbles later on,
which makes it worse. The `steer` badge (`steerBadgeHtml`, `chat_page.js`)
labels the odd position but doesn't fix it.

## Goal

Render each agent turn like a comment thread (YouTube / Facebook style):

```
[you]    Fix the login bug
[agent]  Final answer…
         ▸ 3 replies · 2 steers · 1 follow-up      Latest: "Tests pass now…"
             [you · Steer]       also check the token refresh
             [you · Steer]       don't touch the db schema
             [agent · Follow-up] Background test run finished: …
```

- The turn's main bubble stays where it is and always shows its answer.
- Steers, background follow-ups and (in phase 4) sub-agent replies sit in a
  collapsible **Replies** section under that bubble.
- The thread only changes how the transcript is drawn: the stored rows,
  `CH-…-N` numbering, `api.chat_cli` / `api.panes_cli` output, exports and
  voice all stay in time order, as they are today.

## Non-goals

- No change to sub-agent child chats or fleet cards (`chat_subagent_fleet.js`).
- No change to how steering runs (`api.agent_harness.steer`, `background_wait`).
- No threading for ordinary user/assistant turns, supervised check-ins or
  chat_ops notices (maybe later; see "Later").
- No new route families. The thread is built from the rows the messages GET
  already returns.

## Decisions (recommended; confirm before building)

| Question | Recommendation |
|---|---|
| Label | **"N replies"**, with a count per kind (`2 steers · 1 follow-up`) and a tag on each row (Steer / Follow-up / Sub-agent). "Steers" alone is too narrow once follow-ups and sub-agents join. |
| Open or closed by default | **Open** while the turn is live or its detached run is working; **closed** when history loads. A thread you opened or closed stays that way for the session (per-session prefs, keyed by turn id). |
| When a turn ends | Leave the thread as it is (never collapse while you may be reading). It loads closed on the next reload. |
| Answer that arrives as a follow-up | The closed bar shows a one-line preview of the **latest reply**, plus an unread dot when a reply arrived while it was closed. |
| Rollout | Experimental flag `chat_reply_threads`, default off; off means today's flat rendering. |

## Thread key (data)

A thread is **one turn id** (`api.chat_turn_id`, the turn's query id).

| Row | Joins the thread of | Has the key today? |
|---|---|---|
| Turn's main assistant row (root) | its own `metadata.turn_id` | Yes (`AuthDatabase` stamps every assistant write) |
| Background follow-up bubble | the original turn: `background_wait` opens its row with the base `query_id` | Yes (`turn_id` + `background_followup` job id) — verify |
| Steer into a live turn (`POST /api/chat-steer`) | the live turn | **No**: only `{steered, steered_agent}` |
| Steer into a detached run (`chat_coordinator._steer_into_background`) | the detached run's original turn | **No**; and `ambient()` is empty there because the turn row is closed |
| Sub-agent child final reply | the spawning turn (`batch.turn_id`, cards already bound by `bind_turn_cards`) | Yes, on the batch |
| Sub-agent handback notice (`api.subagents.handback`) | the spawning turn | Check; add if missing |

Root = the earliest assistant row with that turn id that is not a follow-up.
Rows from before this change (no key on steers) use a fallback: a `steered`
user row attaches to the nearest assistant row above it from the same agent.
If there is no such row, it stays flat.

## Phases

### Phase 1 — Stamp the thread key on steered rows (server)

1. Move the steer persist out of `web_chat_api.py:1662` into an owned helper,
   `chat_turn_persist.persist_steered_user(db, sid, text, *, agent, turn_id)`,
   so the root doesn't grow. It writes `{steered, steered_agent, turn_id}`.
2. Live steer: take the turn id from `chat_turn_id.ambient(sid)` (the open turn
   row) at steer time.
3. Detached steer: `steer_detached` returns the wait's base `query_id` as
   `turn_id` in its info dict; `_steer_into_background` passes it to
   `persist_steered_user`.
4. Undelivered steers: the reply already lists them (`undelivered_steers`).
   The model matches them to steer rows by text. No new storage.
5. Confirm follow-up rows carry the original turn id (add a test either way).

Tests: extend `src/tests/harness/test_agent_steer.py` and
`src/tests/harness/test_claude_background_detach.py` to assert `turn_id` on
the steered user row and on follow-up rows.

### Phase 2 — Pure grouping model (client)

New `src/web/js/chat/chat_thread_model.js` → `CuttleChatThreadModel`, Cuttle-free:

- `normalize` in `chat_messages.js` passes through `turn_id`,
  `background_followup`, and the root's `undelivered_steers` (`turn_id` is not
  passed through today).
- `group(messages) → items[]`, each item either `{kind:'row', msg}` or
  `{kind:'thread', root, replies[], counts, latest, undelivered}`. Rows keep
  their original index, so `CH-…-N` and transcript nav still resolve.
- `place(items, msg)`: where a new live row goes (into an open thread or at the end).
- Legacy fallback heuristic as above.

Tests: a Node/pure-JS suite in the style of `test_chat_timeline_notice.py`,
covering live steer, detached steer + follow-up, multiple steers, an
undelivered steer, legacy rows without a key, Stop / interrupted root, and a
thread whose root is still `in_progress`.

### Phase 3 — Thread controller + rendering

New `src/web/js/chat/chat_threads.js` → `CuttleChatThreads.create(host)`:

- Draws the replies bar under the root bubble: counts, latest-reply preview,
  unread dot, expand/collapse. Reply rows reuse the normal bubble renderer
  (`host.renderBubble`), in compact style, with their tag.
- Live path: the steer path in `chat_followup_queue.js` and the follow-up
  bubbles arriving over SSE/poll add their row through `place()`, so they go
  into the live turn's thread, not the end of the transcript.
- Open/closed state lives in session prefs (same store as `stickyCleared`),
  keyed by turn id.
- Deep links and transcript nav to a reply open its thread first, then scroll.
  Find-in-chat opens threads that contain a match.
- `chat_page.js` gets only the call site: when the flag is on, history render
  goes through `model.group()` + `threads.render()`. The page is at 13,071 of
  its 13,405-line ceiling, so the call site must stay a few lines.
- `steerBadgeHtml` becomes the in-thread "Steer" tag; the old badge stays for
  flag-off rendering.
- CSS goes in the chat stylesheet beside the existing bubble styles: indent,
  thread rail, compact bubbles, same look on mobile.

Tests: `e2e/test_chat_reply_threads.py`: steer during a live Codex/Claude
turn → the steer appears in the open thread under the running bubble, and the
final answer lands in the root without scrolling. Reload → the thread is
closed with correct counts. A detached follow-up → the preview updates and the
unread dot shows. Also add a check to `test_composition_root_delegation.py`
for the new owner.

### Phase 4 — Sub-agent replies

- The fleet card stays exactly as it is; it is the progress view.
- The replies section of the spawning turn's root lists each child's **final
  reply**: profile name/avatar, short excerpt, its `CH-` handle, and a click
  that opens the child chat. No progress, no duplication of card content.
- Data: the cards already on the root row (`metadata.subagents`, written by
  `subagents.service._write_cards`) gain a `reply_excerpt` (+ `reply_at`) when
  a child round finishes. No new route.
- Handback notices for that batch join the same thread.
- Counts gain `· N sub-agents`.

Tests: `src/tests/subagents/` for `reply_excerpt` on card writes; an e2e case
with a two-child `--collect all` batch.

### Phase 5 — Docs and graduation

- `docs/architecture/chat-inline-surfaces.md`: add reply threads as a
  rendering of the transcript (not a fourth inline surface), owner
  `chat_threads.js`.
- AGENTS.md steering paragraph: one line saying steers and follow-ups show in
  the turn's thread.
- `.cuttle/docs/experimental-features.md`: flag entry.
- Graduate (default on) after a week of daily use; then remove the
  flag-off path and the old badge.

## Risks / things to check first

- **Handle numbering**: confirm every place that computes `CH-…-N` on the
  client uses the original message index, not DOM order.
- **Root still in progress**: a thread must render while its root row is
  `in_progress` (readers skip those rows unless asked; the live bubble is the
  root then).
- **Several assistant rows in one turn** (fallback chain, Stop then interrupted
  card): pick the root by rule, show the others as flat rows, not replies.
- **Other clients** (Android, LAN browsers) render the same web UI, so they
  get the change too. API consumers see no change.
- **Performance**: grouping is O(n) over the loaded page; paging older
  history must re-group across the page boundary (a thread split across pages).

## Later (out of scope)

- Thread supervised check-ins and chat_ops `notice` turns under the turn they
  refer to.
- "Reply to this bubble" from the thread bar (an explicit steer/follow-up into
  that turn).
