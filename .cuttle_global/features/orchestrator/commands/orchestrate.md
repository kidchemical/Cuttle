---
name: orchestrate
title: orchestrate
description: Run this chat as an orchestrator under a playbook (org chart, escalation, supervisor defaults)
execute: prompt
host_step: orchestrator.bind
---

# Orchestrate

Cuttle has already bound this chat to the playbook named at the end of this
message, before your turn (`host_step: orchestrator.bind`). Do **not** run
`api.orchestrator start`: the binding, the playbook pick and the goal are host
state, and the user changes them from the chat's **Orchestrating** header chip.
Runbook: `feature/orchestrator/docs/orchestrator.md`.

1. Follow the playbook brief in your context (reprint it with
   `PYTHONPATH=src .venv/bin/python -m api.orchestrator brief --json` if you
   need it). Cuttle re-injects it into every fresh context for this chat,
   including after a harness switch, and sends the new one when the user
   switches playbook.
2. Draft the high-level plan into a Tasks gizmo. For a large or risky goal,
   send the plan to an expert-tier profile for review before delegating.
