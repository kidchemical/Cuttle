# Chat inline surfaces — what to call them, who owns them

Anything that shows up in the transcript and is not a user or assistant
bubble is one of three things. Pick the family first, then use its owner;
do not hand-roll a fourth look.

| Family | Use when | Owner | Saved? |
|---|---|---|---|
| **Timeline notice** | A fact happened: project / worktree / checkout changed, Stopped generating, model changed, context compacted. One line, centered. | `src/web/js/chat/chat_timeline_notice.js` (`CuttleChatTimelineNotice`), page call site `addSystemMessage(spec, opts)` | Yes, `role=system` row with readable `content` + `metadata.timeline_notice` |
| **Live status card** | Work is still running for this chat with no turn generating: detached Claude waits, watch jobs, supervision schedules, leftover processes. Pinned last, updates in place. One line per item, graphics over prose: step pips, bars, a countdown ring to the next supervision check, the age of a queued send (amber once it is stuck); details open from the row. When nothing is live any more it shrinks to one line (`settled`), like a locked form. While a detached run is actually *working* (answering a steer, following up on a finished task) it publishes live status flagged `followup`, so the ordinary typing bubble takes over and the card hides until the run is only waiting again. | `src/web/js/chat/chat_background_work.js` (`CuttleChatBackgroundWork`) | No, it is derived from live status |
| **Action card** | The agent asks, confirms, watches or reports inside its reply (`<cuttle_action_form>`). | `chat_action_forms.js` (pure) + `chat_action_cards.js` (effects); runbook `.cuttle_global/docs/action-forms.md` | Part of the assistant message |

**Reply threads** are not a fourth surface: they regroup existing rows.
Steers, a turn's later follow-up bubbles and finished sub-agent children
fold under the turn's bubble behind an "N replies" bar
(`chat_threads.js` + pure `chat_thread_model.js`, keyed by `turn_id`). Rows
keep their place, so `CH-…-N` numbering is unchanged. A row that should join
a thread carries its turn's `turn_id` in metadata; never hand-roll a reply list.

Rows Cuttle sends into a chat as a turn (chat_ops check-ins, relayed sends,
notices; `metadata.origin == 'chat_ops'`, `chat_op_kind`) stay user-role
bubbles because the agent must read them, but never look typed: the header
badge comes from `CuttleChatSupervisionModel.chatOpBadge` and
`[data-chat-op]` drops the user's accent fill (`chat_supervision.css`).
A check-in shows as one line of facts read from its own text (`checkinFacts`:
chat, state, alerts, Tasks bar) with the briefing folded behind it; a long
relayed send or notice folds to a two-line preview (`foldHtml`). The stored
text is untouched, so the agent always reads all of it.

Sub-agent fleet cards (`chat_subagent_fleet.js`) and supervised activity rows
(`supervised_control.js`) are specialized renderings inside a parent reply,
not general-purpose surfaces.

## Timeline notices

A notice is a spec, not a string:

```js
addSystemMessage({ type: 'worktree', label: `Worktree: ${branch}` }, { persist: true });
addSystemMessage({ type: 'stop', label: 'Stopped generating.' }, { persist: true, kind: 'generation-stop' });
```

- `type` (`project`, `worktree`, `checkout`, `model`, `stop`, `compact`,
  `supervision`, `notice`) picks the icon and styling. The host saves some
  rows itself (`role=system` + `metadata.timeline_notice`), e.g.
  `api.chat_ops.service.record_schedule_change` for a supervision settings edit. Add a row to `TYPES` for a new kind of
  fact; do not put emoji in the label.
- `state: 'running'` shows a spinner and elapsed time, never fades and is never
  persisted. Settle it with `update(el, spec, { persist: true })` to `done`, or
  to `error`. The context-gauge Compact is the reference flow.
- `opts.kind` is the machine key other code matches on (`generation-stop`,
  `model-change`, `context-compact`); with `replace` (the default) it updates
  this turn's latest row of that kind instead of stacking a new one.
- Without `persist` or `sticky`, a notice fades after 3 s (a transient hint).
- Rows saved before structured metadata existed (emoji + text) parse through
  `fromText`, so old history renders the same way.

Tests: `src/tests/chat/test_chat_timeline_notice.py`,
`src/tests/e2e/test_chat_background_work.py::test_timeline_notice_running_settles_into_saved_row`.
