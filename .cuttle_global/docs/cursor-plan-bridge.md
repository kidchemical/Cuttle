# Cursor CreatePlan → Cuttle chat

Cursor IDE Plan mode shows an interactive plan card. Cuttle’s `/cursor` path runs
`agent -p` and only persists assistant text, so a turn that ends on `CreatePlan`
used to look abrupt (`Plan created successfully`) with no plan body.

## What Cuttle does

When stream-json emits a CreatePlan tool call, the Cursor harness extracts
`name` / `overview` / `plan` / `todos` and **bridges** them into the chat reply:

1. Markdown plan (`## Plan: …` + body + todo checklist)
2. A Q&A `<cuttle_action_form>` with **`"resume": true`** (Approve / Want changes —
   **no** side-effect `action`). Click sends the pick as a normal turn
   (`sendMessage({text})`, one user bubble) so the agent actually starts a turn —
   without `resume`, Cuttle only toasts.

Code: `src/api/cursor_plan_bridge.py` (wired from `scripts.utilities.cursor_cli_tool`).

## Agent guidance

- Prefer putting the plan in the **visible reply** when on Cuttle; do not rely on
  the IDE card alone.
- `CreatePlan` is fine — Cuttle will surface it — but still write a short preface
  in the reply when you can.
- After the user approves (tap or reply **go**), implement; if they want changes,
  revise the plan in chat (markdown or another CreatePlan).

## AskQuestion

Same problem, same fix: `AskQuestion` returns "skipped" headless, so the user never
saw the picker. `src/api/cursor_question_bridge.py` turns the tool args into a
`resume: true` action form (one question → `choice` / `multi`; several → `form`
with `radio` / `checkboxes` fields) and appends it unless the reply already has a
form. Agents should still emit the form directly instead of calling AskQuestion.

## Related

- Action forms / Q&A picks → `action-forms.md` (omit `action` for preference)
- Cursor `/plan` mode pin (CLI) is separate from CreatePlan tool bridging
