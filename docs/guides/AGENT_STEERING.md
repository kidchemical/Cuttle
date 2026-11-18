# Mid-turn steering and background detach

Moved out of `AGENTS.md` so the brief stays an index. This is the behavior
contract for steering a live agent turn and for detached Claude Code runs.

- **Mid-turn steering** (`src/api/agent_harness/steer.py`): Codex turns run on `codex app-server` and Muse Code turns on `muse serve`, so a follow-up sent while they work goes into the live turn (`turn/steer`) through `POST /api/chat-steer` instead of waiting in the follow-up queue. The composer tries steer first and queues on `steered: false` (no steerable run, other slash command, attachments, or turn already finished). The server persists the steered user row (`metadata.steered` + the live turn's `metadata.turn_id`; a message steered into a detached run that was only waiting is also `steer_idle` and shows as a new message, not a thread reply); the transcript folds it, with that turn's later follow-up bubbles and finished sub-agents, under the turn's bubble as a reply thread (`chat_threads.js`, flag `chat_reply_threads`). Either runner returns `fallback` if it can't start a turn, and the adapter reruns on `exec`. Off switch: `settings.json` → `agent_steer: {"codex": false}` or env `CUTTLE_AGENT_STEER=0`. Codex reads steer input only at its next sampling step: the status strip shows `steer queued: …`, and a steer the turn never echoed back is listed in the reply (`undelivered_steers`) instead of vanishing. While a Codex turn is live, `/api/agent-context` uses the runner's token snapshot and never resumes the busy thread from a second app-server. Claude Code turns run `claude -p --input-format stream-json --replay-user-messages` with stdin open: a steer is another stdin user line, folded in at the next tool boundary (or answered as an extra turn after the first `result`, merged into one reply); stdin closes at the first root `result` unless the CLI still lists background tasks (`system/background_tasks_changed`) or a steer reached stdin it has not read yet — that steer starts an extra turn, and the run stays steerable through it (EOF after `_UNREAD_STEER_GRACE_SEC` if the CLI never reads it). Then the turn **detaches** (`api.agent_harness.background_wait`): the reply is saved as a finished bubble carrying a standard watch card (`api.job_watch` status: task labels, elapsed, Stop) and the chat stops being busy, while the CLI keeps running on its own thread with stdin open. Each later root `result` (a task's completion follow-up, or an answer to a steer) is persisted as a **new** assistant bubble by the saver that stored the first one (`chat_turn_persist` binds it), and the card ends done/failed. While it waits the steer token stays registered: `/api/chat-steer` and a plain `/api/chat` harness send (`chat_coordinator._steer_into_background`, returns `no_reply`) both write into the live run instead of starting a second `claude --resume`. The detached run has two phases: *waiting* (only background tasks run; the chat is idle and shows the live status card) and *working* (answering a steer or following up on a finished task): its activity lines are published as live status flagged `followup`, so the ordinary typing bubble with the status line shows on every device until the answer lands as its own bubble, and the card hides meanwhile. The pinned card names the run ("Claude Code still running") and says messages go into it; the background phase (steers, follow-up answers, its end) is appended to the first reply's run log. The process stays in `chat_run_registry` and the job is bound to the chat, so Stop, chat deletion and the card's Stop kill it; another agent's turn in the chat also ends it (one live CLI per chat). Cap `CLAUDE_BACKGROUND_WAIT_SEC` per wait — it restarts at each root `result` — default 3000s, `0` disables waiting; at the cap stdin closes, a CLI still running after `CLAUDE_BACKGROUND_EXIT_GRACE_SEC` (30s) is reaped, but background shells still running are spared (`kill_process_tree(keep=…)`) and handed off (`api.agent_harness.background_handoff`): a detached watcher queues a Cuttle `notice` turn on the chat (chat_ops outbox) with each task's label and output tail when it ends, and `api.subagents` `--wait` runs hand their batch back to the parent themselves when their launching CLI is gone (`api.subagents.handback`). The closing bubble says what is still running (card done) and what ended with the turn. Tests: `src/tests/harness/test_background_handoff.py`, `src/tests/subagents/test_subagents_handback.py`. Known gap: edit attribution / query tracking close with the first bubble. Off switches: `agent_background_detach: {"claude": false}` or env `CUTTLE_BACKGROUND_DETACH=0` restores the single-bubble wait (follow-ups merge into one reply, chat busy until done); `agent_steer: {"claude": false}` restores the plain one-shot `-p`. Cursor `-p` has no mid-turn input channel. Tests: `src/tests/harness/test_agent_steer.py`, `src/tests/harness/test_claude_background_detach.py`.

## Receipt and acknowledgement: preliminary spike

An isolated direct Codex app-server turn with `gpt-5.6-sol` at low effort
produced two accepted and echoed steers sent one second apart. Echoes appeared
2.244 s and 1.241 s after their respective sends. Both were followed by the
same agent message: “Acknowledged: amber and cobalt. I’m continuing the
requested read-only inspection.” It addresses both steers, but does not
establish that a separate acknowledgement can be attributed to each one.
A third send after completion was rejected as having no steerable run. The
initial `gpt-5.1-codex-mini` attempt failed because the account did not support
that model. The event stream identifies the turn and each user and agent item;
the test's send text correlates an echo, but production `steer_id` and saved
user message id are not present in the vendor event. They would need explicit
registration before delivery and an unambiguous matching strategy.

This initial run was preliminary; subsequent boundary trials are recorded below.
No receipt or acknowledgement persistence is implemented by these experiments.

### Follow-up boundary trials

These are isolated CLI runs, with a short acknowledgement request. Times are
monotonic send-to-first-echo, not acknowledgement latency. A grouped reply is
acceptable for all steers echoed since the previous agent message.

| Harness / boundary | Echo | First subsequent agent message and judgment |
|---|---:|---|
| Codex, two rapid sends | 2.244 s / 1.241 s | “Acknowledged: amber and cobalt.” Both addressed by one item. |
| Codex, tool running | 0.008 s | “Steer acknowledged — I’ll include ‘marigold’...” Addressed. |
| Codex, text streaming | 1.739 s | “Acknowledged—marigold.” Addressed. |
| Codex, final item completed | 0.019 s | “Acknowledged—marigold.” Addressed in another item. |
| Claude, tool running | 8.127 s | “Noted, marigold.” Addressed in the next assistant block. |
| Claude, text streaming | 6.971 s | “Acknowledged, marigold.” Addressed in the next assistant block. |
| Claude, result | — | Send rejected: no steerable run. |
| Muse, tool running | 8.021 s | “Command finished with output `READY`.” followed by acknowledgement in the same message; addressed. |
| Muse, text streaming | 8.544 s | “Acknowledged — continuing the prior task, with marigold included as requested.” Addressed. |
| Muse, final item completed | — | Accepted but reported undelivered at turn end; no echo. |

All observed echoed steers were addressed by the first subsequent completed
agent item or assistant block. This supports implementing capture for the three
harnesses, not assuming that accepted means delivered. The small samples do not
establish a general reliability rate; timeout, reconnect, identical concurrent
steers and Stop/resend still need explicit coverage. Capture must register the
steer and its turn before the delivery wait: an echo can precede the delivery
response and the user-row insert. The vendor item IDs and turn/session IDs do
not themselves identify Cuttle's steer ID or persisted user row. Buffer events
until the row is bound and preserve distinct IDs for identical text.

### Receipt contract (`metadata.steer`)

A steered user row carries `metadata.steer`, written **in full at insert**
(`api.chat_coordinator.steered_user_meta`) and from then on only ever replaced
wholesale by the receipt owner, `api.agent_harness.steer_receipts`:

```json
{
  "v": 1,
  "steer_id": "hex",
  "state": "pending" | "delivered" | "undelivered",
  "delivered_at": "2026-11-18T12:00:00+00:00" | null,
  "mode": "live" | "detached",
  "reply": {"text": "…", "at": "…"} | null,
  "covers": [message_id, …]
}
```

- `v` is the contract version. A row **without** `v` is an older, unknown
  shape from the spike (`{steer_id, reply, covers, delivered}`): treat it as
  unknown, never default it into v1.
- `state` is the **only** status field. There is no separate `delivered` bool;
  a reader derives it as `state == "delivered"`. The shape never grows a key
  mid-life, so a client can render a row without defaulting anything.
  - `pending` — accepted by the transport, no vendor echo yet. **Not a
    terminal state**, and not a promise of one: a `pending` row whose turn is
    no longer the chat's live turn renders as *not confirmed*, never as a
    spinner that waits forever. (The owner writes one terminal state when a
    turn is superseded, and gives up after bounded write retries, so a row can
    also be left `pending` by a database that stayed locked.)
  - `delivered` — **the agent read it**: the vendor echoed that exact send back
    (`delivered_at` is when). Reserved for a confirmed echo; nothing else sets
    it.
  - `undelivered` — *ended without confirmation*. Usually the turn ended with
    no echo, but ambiguity between identical concurrent sends can leave a
    message unconfirmed even when the agent did read it, so the copy must not
    claim the agent ignored it. The reply also lists it
    (`undelivered_steers`); both read the same matcher, so they agree, and
    neither asks the user to send it again — a resend would let the agent act
    on the same request twice.
- `mode` distinguishes a steer into a live turn from input accepted by an idle
  detached wait, which the copy must not describe the same way.
  - `mode: "detached"` — input accepted into a background run that was only
    *waiting*. It stays `pending` ("Accepted into the background run") until
    the CLI replays it, then becomes `delivered`; a run that exits before the
    replay leaves it `undelivered`, matching the run's closing "unread" notice.
  - `mode: "live"` — everything else, **including** a steer into a detached run
    that is already working. Copy must not assume `detached` covers every send
    into a background run.
- `reply` is the first paragraph (clipped to 280 characters) of the first agent
  output item that **started after** this steer's echo. Label it **Agent
  response excerpt**: it is the agent's response, not a confirmed
  acknowledgement (in one recorded Muse trial the excerpt was `Command
  finished with output \`READY\`.` while the acknowledgement came in the
  message's second paragraph). The full message is in the main reply either
  way. `reply` stays `null` when there is no output item whose start was
  observed after the echo — there is no completion-time fallback — and so does
  a detached run's acceptance until the CLI replays the follow-up, so an
  unrelated background completion can never claim it.
- A row that also carries `steer_idle` (sent while the chat looked idle, into a
  waiting detached run) **never renders the excerpt**: that answer arrives as
  its own assistant bubble just below.
- `covers` lists the message ids of the rows that share one grouped reply
  (several steers echoed before the same agent message).
  - `covers: []` means **no reply yet** — not "covers nothing".
  - A single ungrouped steer that has a reply carries `[own_id]`.
  - The reply itself is carried by the **last** covered row only, so a client
    renders it once; the earlier rows carry the full `covers` with
    `reply: null`. Binding a later row moves the reply to it and updates every
    member's `covers`; the owner keeps a group writable until every member has
    bound, so there is never more than one excerpt holder.

`/api/chat-live-status` extras include `steers`: the same objects plus
`message_id` and `turn_id`, for the **current turn only**, so an open thread can
match each update to its user row while the turn runs. That array is transient;
after a reload the user row is the only source of truth.

### Receipt mechanics (what the owner guarantees)

- **No database on the reader path.** The tracker mutates memory only. Vendor
  reader threads (`_on_line`), `POST /api/chat-steer` and turn teardown never
  open SQLite and never wait on it: one daemon writer thread persists the
  latest snapshot per `steer_id`, batching a turn's rows into a single
  `AuthDatabase.patch_message_metadata` transaction, with bounded retries. A
  busy database therefore cannot stall an agent event loop or time a steer out
  into a double delivery.
- **Correlation by id, not by text.** Each send carries its `steer_id` into the
  runner, whose own matcher (`api.agent_harness.steer_delivery.SteerDelivery`)
  reports `note_echo(steer_id)` when the vendor echoes that exact send. There is
  one matcher per run. The turn's own initial input is excluded before the
  matcher sees it, and identical concurrent steers stay unconfirmed while an
  echo could belong to either — a receipt is never attributed by a guess. A
  held echo is evidence **scoped to the sends outstanding when it arrived**: it
  can never confirm a send made later, identical sends are confirmed only when
  each can be paired with a distinct echo that could have been its own, and a
  send the transport then rejects claims a held echo from its own scope (the
  agent read it, so the client must not re-send it).
- **A delivery timeout after the echo still reports `steered: true`.** The echo
  proves the agent read the text; reporting failure would make the client send
  it again.
- **Stop/resend fence.** Each registration pins its run identity (the turn's
  query id and the `chat_delivery` guard token) at `register()`, and `steer()`
  never reads the chat's ambient turn afterwards. Once a newer turn owns the
  chat the tracker **freezes**: no vendor event after that point records a new
  fact, nothing more is published to live status, and the user's own row gets
  exactly one terminal state carrying the facts earned while the run still
  owned the chat. Receipts never write a reply, so the turn guard is untouched.
  A detached send targets the wait's **own** registration token, so it cannot
  reach a replacement CLI.
- **Retention.** A receipt is released once it has ended and been saved — or
  given up on after bounded write retries; a group is held until every member
  has bound. One that no row will ever carry (unauthenticated steer path, a
  failed insert, a turn that ended during delivery) is dropped, and anything
  still retained is purged a grace period after its turn ended, by the writer
  itself, so cleanup never depends on later traffic. The writer exits when
  there is nothing queued and nothing retained, restarts on demand, and drains
  what it has at interpreter exit. Live status keeps only the current turn's,
  and is a transient overlay: after it disappears, reconcile against refreshed
  history, where the user row is the only source of truth.

Tests: `src/tests/harness/test_steer_receipts.py`,
`src/tests/harness/test_agent_steer.py`,
`src/tests/harness/test_muse_steer_delivery.py`,
`src/tests/harness/test_claude_background_detach.py`.
