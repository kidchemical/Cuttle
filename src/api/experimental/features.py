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
    id="render_result_attachments", label="Render result attachments",
    description="Automatically save verified render previews and encoded videos to the originating chat for registered batches.",
    default=False, category="power", risk="low", since="0.0.0", needs_restart=False,
))

register_flag(FlagSpec(
    id="completion_notifications", label="Completion notifications",
    description="Opt-in browser OS notifications for long replies and mesh batches. Configure permission and privacy in General settings; requires an open page.",
    default=False, category="chat", risk="low", since="0.0.0", needs_restart=False,
))

register_flag(FlagSpec(
    id="router_selection_chip", label="Router selection chip",
    description="Show the selected agent and routing reason as a compact reply chip, including fallback and escalation.",
    default=False, category="chat", risk="low", since="0.0.0", needs_restart=False,
))

register_flag(FlagSpec(
    id="subagent_fleet_cards", label="Sub-agent fleet cards",
    description="Show one card per sub-agent child chat under the parent reply: who ran it, live status, outcome (done, failed, cancelled, lost) and a one-line result.",
    default=False, category="agents", risk="low", since="0.0.0", needs_restart=False,
))

register_flag(FlagSpec(
    id="progress_grid",
    label="Progress grid",
    description="Show a cell-per-item progress grid (frames, files, tests, shards…) on watch cards whose status includes one, coloured by who did each item.",
    default=False, category="ui", risk="low", since="0.0.0", needs_restart=False,
))

register_flag(FlagSpec(
    id="composer_attach_button", label="Composer attach button",
    description="Show the paperclip button in the chat composer for attaching an image or PDF (described to the agent by the vision pre-pass). It is the only way to attach files from chat.",
    default=False, category="chat", risk="low", since="0.0.0", needs_restart=False,
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
))

register_flag(FlagSpec(
    id="voice_server_stt", label="Voice: server transcription",
    description="Voice mode records continuously and transcribes each phrase on the server (OpenAI) instead of the browser speech recognizer. Removes the Android chime between pauses and works in browsers without Web Speech; phrases appear about a second after you stop talking.",
    default=False, category="chat", risk="low", since="0.0.0", needs_restart=False,
))

register_flag(FlagSpec(
    id="gizmos", label="Gizmos",
    description="Live UI objects outside chat bubbles: pin usage meters (Codex, Claude, Cursor) to the title bar or blade bar, float them over every Space, or pop them out as always-on-top desktop windows. Manage them in Apps → Gizmos.",
    default=False, category="ui", risk="low", since="0.0.0", needs_restart=False,
))

# Achievements: default OFF. Experimental features are opt-in by design; a
# user who does not know the system exists should never have it running.
register_flag(
    FlagSpec(
        id="achievements",
        label="🏆 Achievements",
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
    default=False, category="agents", risk="low", since="0.0.0", needs_restart=False,
))
