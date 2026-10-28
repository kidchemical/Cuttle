"""Declarative rows for Cuttle's experimental features.

This file is intentionally the ONLY place inside the generic experimental
package that knows a feature exists. Everything else in
``api/experimental/`` works off the registry it fills.

Teardown of a feature (e.g. Achievements):

1. delete its ``register_flag(FlagSpec(...))`` block below,
2. delete the feature package (e.g. ``src/api/achievements/``),
3. delete its ``src/web/js/<feature>.js`` slices + the ``<script>`` lines,
4. delete its blueprints from the ``web_chat_api`` registration block.

No other change is required — the settings tab, the CLI, and
``api.experimental.is_enabled`` are driven entirely by the registry.
"""

from __future__ import annotations

from api.experimental.flags import FlagSpec, register_flag

register_flag(FlagSpec(
    id="agent_resource_limits", label="Agent resource limits",
    description="Apply Linux memory and process limits to agent CLIs and their descendants, including build and render tools (6 GB soft / 8 GB hard per agent by default). Changes apply to new launches; running scopes keep their limits.",
    default=False, category="agents", risk="medium", since="0.0.0", needs_restart=False,
))

register_flag(FlagSpec(
    id="secret_vault", label="Secret vault",
    description="Let agents use passwords, API keys and other secrets in commands without seeing them. Values are entered in chat secret fields or the Secrets App, kept in the OS keyring or a password-manager CLI, injected through env/stdin/files/askpass (ssh, sudo, git), and redacted from output. Use needs your approval per chat by default.",
    default=False, category="agents", risk="medium", since="0.0.0", needs_restart=False, context_bundle=True,
))

register_flag(FlagSpec(
    id="google_signin", label="Google sign-in",
    description="Show Google sign-in and sign-up options, account linking, and Google photo controls on authentication and account screens.",
    default=False, category="ui", risk="low", since="0.0.0", needs_restart=False,
))

register_flag(FlagSpec(
    id="response_postprocessing", label="Response post-processing",
    description="Run a configurable API or restricted agent CLI pass on completed replies. Configure its own instructions, executor, model, effort and output in Agents settings.",
    default=False, category="chat", risk="medium", since="0.0.0", needs_restart=False,
))

register_flag(FlagSpec(
    id="render_result_attachments", label="Render result attachments",
    description="Automatically save verified render previews and encoded videos to the originating chat for registered batches.",
    default=False, category="power", risk="low", since="0.0.0", needs_restart=False, context_bundle=True,
))

register_flag(FlagSpec(
    id="completion_notifications", label="Completion notifications",
    description="Opt-in browser OS notifications for long replies and mesh batches. Configure permission and privacy in General settings; requires an open page.",
    default=False, category="chat", risk="low", since="0.0.0", needs_restart=False,
))

register_flag(FlagSpec(
    id="router_selection_chip", label="Router selection chip",
    description="Show the selected agent and routing reason as a compact reply chip, including fallback and escalation.",
    # Default on: low-risk UI over a shipped capability.
    default=True, category="chat", risk="low", since="0.0.0", needs_restart=False,
))

register_flag(FlagSpec(
    id="subagent_fleet_cards", label="Sub-agent fleet cards",
    description="Show one card per sub-agent child chat under the parent reply: who ran it, live status, outcome (done, failed, cancelled, lost) and a one-line result.",
    # Default on: low-risk UI over a shipped capability.
    default=True, category="agents", risk="low", since="0.0.0", needs_restart=False,
))

register_flag(FlagSpec(
    id="chat_supervision", label="Chat supervision",
    description="Show chat supervision in the UI: a header chip in the supervisor chat (target, interval, next check, check history, pause/cancel), a \"Supervised by\" badge with branch-rule warnings in the worker chat, queued messages, a \"Supervise this chat…\" menu action, and schedules on the Jobs page. Agent commands (python -m api.chat_cli supervise/send) work regardless.",
    default=False, category="agents", risk="low", since="0.0.0", needs_restart=False,
))

register_flag(FlagSpec(
    id="progress_grid",
    label="Progress grid",
    description="Show a cell-per-item progress grid (frames, files, tests, shards…) on watch cards whose status includes one, coloured by who did each item.",
    default=False, category="ui", risk="low", since="0.0.0", needs_restart=False, context_bundle=True,
))

register_flag(FlagSpec(
    id="composer_attach_button", label="Composer attach button",
    description="Show the paperclip button in the chat composer for attaching an image or PDF (described to the agent by the vision pre-pass). It is the only way to attach files from chat.",
    # Default on: low-risk UI over a shipped capability.
    default=True, category="chat", risk="low", since="0.0.0", needs_restart=False,
))

register_flag(FlagSpec(
    id="composer_prompt_enhance", label="Composer prompt enhancer",
    description="Show the wand button in the chat composer that rewrites your draft prompt with AI (click again to undo).",
    default=False, category="chat", risk="low", since="0.0.0", needs_restart=False,
))

register_flag(FlagSpec(
    id="voice_narrator", label="Voice narrator",
    description="In voice mode, Cuttle speaks a quick acknowledgment as soon as you send, then short spoken updates on what the agent is doing until the reply is ready. Uses gpt-4o-mini plus your chat speech voice (OpenAI key required).",
    default=False, category="chat", risk="low", since="0.0.0", needs_restart=False,
    conflicts=("voice_live", "voice_realtime"),
))

register_flag(FlagSpec(
    id="voice_live", label="Voice: GPT-Live",
    description="Full-duplex GPT-Live conversation that delegates project work to this chat's Cuttle agent. Requires an OpenAI API key. Voice sessions are billed by duration, including silence; agent usage is separate.",
    default=False, category="chat", risk="medium", since="0.0.0", needs_restart=False,
    exclusive_group="voice_input",
))

register_flag(FlagSpec(
    id="voice_realtime", label="Voice: OpenAI Realtime",
    description="Microphone and spoken conversation over OpenAI Realtime, with project requests handed to the selected chat agent. Requires an OpenAI API key and WebRTC microphone support. Uses token-based audio billing.",
    default=False, category="chat", risk="medium", since="0.0.0", needs_restart=False,
    exclusive_group="voice_input",
))

register_flag(FlagSpec(
    id="voice_server_stt", label="Voice: server transcription",
    description="Voice mode records continuously and transcribes each phrase on the server (OpenAI) instead of the browser speech recognizer. Removes the Android chime between pauses and works in browsers without Web Speech; phrases appear about a second after you stop talking.",
    # Default on: the only voice input that works in the Electron app, whose
    # Chromium has no browser speech backend. Without an OpenAI key, voice mode
    # falls back to browser speech. Not in `voice_input`: Live and Realtime are
    # conversation engines that take precedence when chosen in Voice settings.
    default=True, category="chat", risk="low", since="0.0.0", needs_restart=False,
))

register_flag(FlagSpec(
    id="gizmos", label="Gizmos",
    description="Live UI objects outside chat bubbles: pin usage meters (Codex, Claude, Cursor) to the title bar or blade bar, float them over every Space, or pop them out as always-on-top desktop windows. Manage them in Apps → Gizmos.",
    default=False, category="ui", risk="low", since="0.0.0", needs_restart=False, context_bundle=True,
))

register_flag(FlagSpec(
    id="test_lab", label="Test Lab",
    description="Inspect recognized test suites in registered projects, run a selected suite on demand, and review recent results in Apps → Test Lab.",
    default=False, category="ui", risk="medium", since="0.0.0", needs_restart=False,
))

register_flag(FlagSpec(
    id="ide", label="IDE",
    description="A Cursor-style code editor in Apps → IDE: Monaco editing of registered projects with explorer, tabs, quick open, find in files, source control diffs, an integrated terminal, and an AI chat panel bound to the project. Also adds an Editor button to file diffs.",
    # Default on: a core tool. Paths stay confined to allowlisted workspaces;
    # the integrated terminal is localhost-only.
    default=True, category="ui", risk="medium", since="0.0.0", needs_restart=False,
))

register_flag(FlagSpec(
    id="agent_review", label="Agent review",
    description="Cursor-style review of agent edits: each change an agent makes is tracked per hunk against what you had before the turn, so you can Keep or Undo it. Your own edits are never counted as agent changes. Shared by the IDE, Pending Changes, file diffs and chat.",
    default=False, category="ui", risk="medium", since="0.0.0", needs_restart=False,
))

# Achievements: default OFF. Experimental features are opt-in by design; a
# user who does not know the system exists should never have it running.
register_flag(
    FlagSpec(
        id="achievements",
        label="Achievements",
        description=(
            "Unlock Steam-style achievements as you use Cuttle — long turns, huge "
            "prompts, midnight runs, harness marathons. Each unlock plays a toast, "
            "a sound, and (for rare tiers) confetti."
        ),
        default=False,
        category="fun",
        risk="low",
        since="0.0.0",
        needs_restart=False,
    )
)

register_flag(FlagSpec(
    id="agent_feed", label="Agent Feed",
    description="Observe agent tool calls, edits and CLI-exposed thinking across chats. Uses the shared event store.",
    # Default on: read-only view over agent activity.
    default=True, category="agents", risk="low", since="0.0.0", needs_restart=False,
))

register_flag(FlagSpec(
    id="jobs_surface", label="Jobs surface",
    description="Unified Jobs page: every execution Cuttle handles — local agent turns, background helpers and mesh jobs — as one ledger with running-now, recent history, cancel/rerun control and telemetry.",
    # Owner-requested restoration/upgrade of the existing Jobs page (a register
    # on an already-shipped surface over data Cuttle already tracks), not a new
    # opt-in capability, so it ships on by default. Reversible via Settings →
    # Experimental or the CUTTLE_EXPERIMENTAL kill switch.
    default=True, category="ui", risk="low", since="0.0.0", needs_restart=False,
))

register_flag(FlagSpec(
    id="worktrees", label="Worktrees",
    description="Run a chat in its own git worktree and branch instead of the shared checkout, so parallel chats cannot fight over one working tree. Default worktrees live under the Cuttle home; creating and removing one is a deliberate action.",
    # Default on: only offers the option; each worktree is still created on purpose.
    default=True, category="agents", risk="medium", since="0.0.0", needs_restart=False,
))
