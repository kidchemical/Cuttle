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
