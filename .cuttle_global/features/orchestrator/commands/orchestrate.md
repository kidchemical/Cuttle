---
name: orchestrate
title: orchestrate
description: Run this chat as an orchestrator under a playbook (org chart, escalation, supervisor defaults)
execute: prompt
---

# Orchestrate

Bind this chat to an Orchestrator playbook and follow its brief for the rest of
the chat. Runbook: `feature/orchestrator/docs/orchestrator.md`.

The user's arguments are the goal. If they start with `@<playbook-id>`, that
word picks the playbook; otherwise use `default`. If there is no goal, ask for
it with one Q&A action form and end the turn.

1. Bind and print the brief (Cuttle venv + `src` on `PYTHONPATH`, from the
   Cuttle checkout — also for guest projects; see `agent-ops-cli.md`):

   ```bash
   PYTHONPATH=src .venv/bin/python -m api.orchestrator start --playbook <id> --goal "<goal>" --json
   ```

   On error (unknown playbook, missing profile), show it and point the user to
   Apps → Orchestrator / Apps → Agents. Do not improvise an org chart.
2. Follow the printed brief. Cuttle re-injects it into every fresh context for
   this chat, including after a harness switch.
3. Draft the high-level plan into a Tasks gizmo. For a large or risky goal,
   send the plan to an expert-tier profile for review before delegating.
