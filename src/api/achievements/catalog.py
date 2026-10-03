"""Achievement catalog — the vocabulary of the achievements system.

Pure data: no DB, no Flask, no settings. Each entry names a **metric** from
:mod:`api.achievements.evaluator` plus a **threshold**; the evaluator decides
how that number is computed. Adding an achievement is therefore one row here
plus (only if the number is genuinely new) one metric in the evaluator.

Design rules for this catalog:

* **Progressive** — every family has a visible ladder: 10 → 100 → 1,000.
  The first rung of a family is `common`, the last is `legendary`/`mythic`.
* **Single-turn vs lifetime** are deliberately separate metrics. ``100M`` as a
  lifetime total is `mythic`; 100M **in one message** (subagents excluded) is
  the headline achievement and is its own metric.
* **Hidden** entries expose a ``hint`` but not a description, so the grid can
  render `???`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Celebration tiers. Order is the celebration intensity order.
RARITY_ORDER = ("common", "rare", "epic", "legendary", "mythic")

RARITY_META: Dict[str, Dict[str, Any]] = {
    "common": {"label": "Common", "color": "#8b949e", "sfx": False, "confetti": 0},
    "rare": {"label": "Rare", "color": "#58a6ff", "sfx": True, "confetti": 0},
    "epic": {"label": "Epic", "color": "#a371f7", "sfx": True, "confetti": 60},
    "legendary": {"label": "Legendary", "color": "#d29922", "sfx": True, "confetti": 120},
    "mythic": {"label": "Mythic", "color": "#f85149", "sfx": True, "confetti": 200},
}

# Grid grouping (display only; independent of rarity).
CATEGORY_ORDER = (
    "milestones",
    "tokens",
    "marathon",
    "variety",
    "router",
    "swarm",
    "time",
    "projects",
    "economy",
)

CATEGORY_LABELS = {
    "milestones": "Milestones",
    "tokens": "Token Ocean",
    "marathon": "Marathon",
    "variety": "Variety",
    "router": "The Router",
    "swarm": "Swarm",
    "time": "Clockwork",
    "projects": "Many Rooms",
    "economy": "The Wallet",
}


@dataclass(frozen=True)
class Achievement:
    id: str
    title: str
    icon: str
    metric: str
    threshold: float
    description: str
    rarity: str = "common"
    category: str = "milestones"
    hidden: bool = False
    hint: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def tier(self) -> int:
        try:
            return RARITY_ORDER.index(self.rarity)
        except ValueError:
            return 0

    def to_dict(self, progress: Optional[float] = None, unlocked_at: Optional[float] = None,
                seen_at: Optional[float] = None) -> Dict[str, Any]:
        unlocked = unlocked_at is not None
        # Hidden achievements show only their hint until earned; the description
        # is revealed afterwards, so the trophy still says what it was.
        reveal = (not self.hidden) or unlocked
        pct = 0.0 if not self.threshold else min(1.0, float(progress or 0.0) / float(self.threshold))
        payload: Dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "icon": self.icon,
            "description": self.description if reveal else "",
            "hint": "" if reveal else self.hint,
            "rarity": self.rarity,
            "rarity_label": RARITY_META.get(self.rarity, RARITY_META["common"])["label"],
            "category": self.category,
            "category_label": CATEGORY_LABELS.get(self.category, self.category),
            "hidden": self.hidden,
            "metric": self.metric,
            "threshold": self.threshold,
            "progress": float(progress or 0.0),
            "percent": round(pct * 100.0, 2),
            "unlocked": unlocked,
            "unlocked_at": unlocked_at,
            "seen": seen_at is not None,
        }
        if self.extra:
            payload.update(self.extra)
        return payload


def _a(
    id: str,
    title: str,
    icon: str,
    metric: str,
    threshold: float,
    description: str,
    rarity: str = "common",
    category: str = "milestones",
    hidden: bool = False,
    hint: str = "",
    **extra: Any,
) -> Achievement:
    return Achievement(
        id=id,
        title=title,
        icon=icon,
        metric=metric,
        threshold=threshold,
        description=description,
        rarity=rarity,
        category=category,
        hidden=hidden,
        hint=hint,
        extra=extra,
    )


# ---------------------------------------------------------------------------
# THE CATALOG
# ---------------------------------------------------------------------------
_ACHIEVEMENTS: List[Achievement] = [
    # ---- Getting started -----------------------------------------------------
    _a("first_dive", "First Dive", "🐙", "turns_total", 1,
       "Send Cuttle its very first agent turn.", "common", "milestones"),
    _a("ten_tides", "Getting Comfy", "🌊", "turns_total", 10,
       "Run ten agent turns.", "common", "milestones"),
    _a("century_tides", "Centurion", "💯", "turns_total", 100,
       "Run one hundred agent turns.", "rare", "milestones"),
    _a("thousand_tides", "Leviathan", "🦈", "turns_total", 1_000,
       "Run a thousand agent turns.", "epic", "milestones"),
    _a("first_blood", "First Catch", "🎣", "turns_completed", 1,
       "Get your first successful agent reply.", "common", "milestones"),
    _a("steady_hand", "Steady Hand", "🤝", "turns_completed", 50,
       "Fifty successful replies in a row of effort.", "common", "milestones"),
    _a("unbroken", "Unbroken", "🛡️", "turns_completed", 250,
       "Two hundred fifty successful replies.", "rare", "milestones"),
    _a("chatty", "Chatterbox", "💬", "user_messages", 250,
       "Send 250 messages to Cuttle.", "common", "milestones"),
    _a("conversationalist", "Conversationalist", "🗣️", "user_messages", 2_500,
       "Send 2,500 messages to Cuttle.", "epic", "milestones"),
    _a("many_rooms", "Many Rooms", "🚪", "sessions_with_turns", 25,
       "Use agent turns in 25 different chats.", "common", "projects"),
    _a("hall_of_chats", "Hall of Chats", "🏛️", "sessions_with_turns", 100,
       "Use agent turns in 100 different chats.", "rare", "projects"),

    # ---- Lifetime tokens -----------------------------------------------------
    _a("tokens_1m", "A Million", "🔢", "lifetime_total_tokens", 1_000_000,
       "Push one million tokens through Cuttle.", "common", "tokens"),
    _a("tokens_10m", "Ten Million", "🔟", "lifetime_total_tokens", 10_000_000,
       "Push ten million tokens through Cuttle.", "rare", "tokens"),
    _a("tokens_50m", "Fifty Million", "🧮", "lifetime_total_tokens", 50_000_000,
       "Push fifty million tokens through Cuttle.", "epic", "tokens"),
    _a("tokens_100m", "Ocean", "🌊", "lifetime_total_tokens", 100_000_000,
       "Push 100,000,000 tokens through Cuttle over your lifetime.", "legendary", "tokens"),
    _a("tokens_500m", "Abyss", "🕳️", "lifetime_total_tokens", 500_000_000,
       "Push half a billion tokens through Cuttle.", "legendary", "tokens"),
    _a("tokens_1b", "Supernova", "💥", "lifetime_total_tokens", 1_000_000_000,
       "Push one billion tokens through Cuttle.", "mythic", "tokens"),
    _a("reader", "The Reader", "📖", "lifetime_input_tokens", 100_000_000,
       "Feed Cuttle 100M input tokens.", "epic", "tokens"),
    _a("writer", "The Writer", "🖋️", "lifetime_output_tokens", 10_000_000,
       "Get 10M tokens of agent output.", "epic", "tokens"),
    _a("cache_hit", "Thrift Class", "🪙", "lifetime_cached_tokens", 50_000_000,
       "Reuse 50M cached tokens instead of re-sending them.", "rare", "tokens",
       sfx="achievement-unlock.wav"),

    # ---- Single-message tokens (the headline ladder) -------------------------
    _a("one_turn_1m", "Heavy Lifting", "🏋️", "max_single_turn_tokens", 1_000_000,
       "Burn a million tokens in one single message.", "common", "tokens"),
    _a("one_turn_10m", "One Turn, Ten Million", "🎯", "max_single_turn_tokens", 10_000_000,
       "Burn ten million tokens in one single message.", "rare", "tokens"),
    _a("one_turn_50m", "Single Stroke", "🖌️", "max_single_turn_tokens", 50_000_000,
       "Burn fifty million tokens in one single message.", "epic", "tokens"),
    _a("one_turn_100m", "Kraken", "🐙", "max_single_turn_tokens", 100_000_000,
       "Burn 100,000,000 tokens in ONE message. Sub-agent turns do not count "
       "toward this — the parent message alone must do it.", "legendary", "tokens"),
    _a("one_turn_250m", "Devourer", "🦑", "max_single_turn_tokens", 250_000_000,
       "Burn a quarter of a billion tokens in a single message.", "mythic", "tokens"),
    _a("one_turn_1b", "Singularity", "🌌", "max_single_turn_tokens", 1_000_000_000,
       "Burn a billion tokens in one message. Physics may not allow this.",
       "mythic", "tokens", hidden=True, hint="That is one message. One."),
    _a("one_turn_input_50m", "Deep Reader", "🔬", "max_single_turn_input_tokens", 50_000_000,
       "Send 50M input tokens in one message.", "legendary", "tokens"),

    # ---- Long turns ----------------------------------------------------------
    _a("turn_10min", "Warm Up", "⏱️", "max_turn_latency_ms", 10 * 60_000,
       "Run a single agent turn for ten minutes.", "common", "marathon"),
    _a("turn_1h", "Hour Glass", "⏳", "max_turn_latency_ms", 3_600_000,
       "Run a single agent turn for a full hour.", "rare", "marathon"),
    _a("turn_6h", "Night Shift", "🌙", "max_turn_latency_ms", 6 * 3_600_000,
       "Run a single agent turn for six hours.", "epic", "marathon"),
    _a("turn_24h", "Long Haul", "🛣️", "max_turn_latency_ms", 24 * 3_600_000,
       "Run a single agent turn for twenty-four hours.", "legendary", "marathon"),
    _a("turn_72h", "Never Sleeping", "🏔️", "max_turn_latency_ms", 72 * 3_600_000,
       "Run one agent turn for three whole days.", "mythic", "marathon",
       hidden=True, hint="Go to sleep. It will still be running."),
    _a("hands_off_10h", "Ten Hours Offline", "🫱", "agent_hours", 10,
       "Accumulate ten hours of agent time.", "common", "marathon"),
    _a("hands_off_100h", "Century of Hands-Off", "🏦", "agent_hours", 100,
       "Accumulate one hundred hours of agent time.", "rare", "marathon"),
    _a("hands_off_500h", "Full-Time Offload", "🧑‍💻", "agent_hours", 500,
       "Accumulate five hundred hours of agent time. That is three full weeks.",
       "epic", "marathon"),
    _a("hands_off_2000h", "Digital Drudge", "⛏️", "agent_hours", 2_000,
       "Accumulate two thousand hours of agent time.", "legendary", "marathon"),
    _a("marathons_10", "Marathoner", "🏃", "long_turns_10min", 10,
       "Finish ten turns that each ran at least ten minutes.", "common", "marathon"),
    _a("marathons_100", "Ultra", "🏅", "long_turns_10min", 100,
       "Finish one hundred ten-minute-or-longer turns.", "epic", "marathon"),
    _a("marathons_500", "Ultra Ultra", "🎽", "long_turns_10min", 500,
       "Finish five hundred long turns.", "legendary", "marathon"),
    _a("impatient", "Impatient", "🔪", "cancelled_turns", 25,
       "Stop twenty-five turns before they finished.", "common", "marathon",
       hidden=True, hint="Sometimes the machine is wrong."),

    # ---- Harness / model variety --------------------------------------------
    _a("trio", "Trio", "🎭", "distinct_harnesses", 3,
       "Use three different agent harnesses.", "common", "variety"),
    _a("full_bench", "Full Bench", "🧰", "distinct_harnesses", 5,
       "Use five different agent harnesses.", "rare", "variety"),
    _a("every_seat", "Every Seat", "🪑", "distinct_harnesses", 8,
       "Use eight different agent harnesses.", "epic", "variety"),
    _a("model_collector", "Model Collector", "📦", "distinct_models", 10,
       "Use ten distinct models.", "common", "variety"),
    _a("vintage", "Vintage Collection", "📚", "distinct_models", 25,
       "Use twenty-five distinct models.", "rare", "variety"),
    _a("polyglot", "Polyglot", "🗣️", "distinct_models", 50,
       "Use fifty distinct models.", "epic", "variety"),
    _a("polyhedron", "Polyhedron", "🔮", "distinct_models", 100,
       "Use one hundred distinct models.", "legendary", "variety"),

    # ---- Router / escalation -------------------------------------------------
    _a("first_handoff", "Patience Pays", "🤝", "escalations", 1,
       "Let the router escalate a failed attempt to another target.", "common", "router"),
    _a("chain_of_command", "Chain of Command", "⛓️", "escalations", 25,
       "Let the router escalate twenty-five times.", "rare", "router"),
    _a("delegator", "Delegator Supreme", "👑", "escalations", 100,
       "Let the router escalate one hundred times.", "epic", "router"),
    _a("escalation_king", "Escalation King", "🫡", "escalations", 500,
       "Five hundred escalations. You are the fallback's fallback.", "legendary", "router"),

    # ---- Sub-agents / swarm --------------------------------------------------
    _a("spawner", "Spawner", "🥚", "subagent_children", 1,
       "Spawn your first sub-agent child chat.", "common", "swarm"),
    _a("hive_mind", "Hive Mind", "🐝", "subagent_children", 25,
       "Spawn twenty-five sub-agent child chats.", "rare", "swarm"),
    _a("swarm_mother", "Swarm Mother", "👾", "subagent_children", 250,
       "Spawn two hundred and fifty sub-agent child chats.", "legendary", "swarm"),
    _a("legion", "Legion", "🎖️", "subagent_children", 1_000,
       "Spawn a thousand sub-agent child chats.", "mythic", "swarm"),

    # ---- Clockwork -----------------------------------------------------------
    _a("after_hours", "After Hours", "🦉", "night_turns", 1,
       "Run an agent turn between midnight and 5am.", "common", "time"),
    _a("nocturnal", "Nocturnal", "🌜", "night_turns", 30,
       "Run thirty turns between midnight and 5am.", "rare", "time"),
    _a("vampire_shift", "Vampire Shift", "🧛", "night_turns", 200,
       "Run two hundred turns between midnight and 5am.", "epic", "time"),
    _a("dawn_patrol", "Dawn Patrol", "🌅", "dawn_turns", 1,
       "Run an agent turn between 5am and 7am.", "common", "time"),
    _a("weekend_warrior", "Weekend Warrior", "🎉", "weekend_turns", 25,
       "Run twenty-five turns on a Saturday or Sunday.", "common", "time"),
    _a("weekend_warrior_100", "Weekend Warrior (Hardcore)", "🏖️", "weekend_turns", 200,
       "Run two hundred weekend turns.", "epic", "time"),
    _a("around_the_clock", "Around the Clock", "🕛", "active_hours_distinct", 24,
       "Run turns in all twenty-four hours of the day.", "legendary", "time"),
    _a("three_tides", "Three Tides in a Row", "🌊", "streak_days", 3,
       "Use Cuttle three days in a row.", "common", "time"),
    _a("week_streak", "Week Streak", "📅", "streak_days", 7,
       "Use Cuttle seven days in a row.", "rare", "time"),
    _a("month_of_cuttle", "Month of Cuttle", "🗓️", "streak_days", 30,
       "Use Cuttle thirty days in a row.", "epic", "time"),
    _a("century_streak", "Century Streak", "💯", "streak_days", 100,
       "Use Cuttle one hundred days in a row. Do not take a day off.", "legendary", "time"),
    _a("thirty_tides", "Thirty Tides", "🌊", "active_days", 30,
       "Use Cuttle on thirty distinct days.", "common", "time"),
    _a("year_of_the_cuttle", "Year of the Cuttle", "🦑", "active_days", 365,
       "Use Cuttle on three hundred and sixty-five distinct days.", "mythic", "time"),

    # ---- Projects ------------------------------------------------------------
    _a("polyglot_dev", "Polyglot Dev", "🧱", "distinct_projects", 5,
       "Run turns in five different projects.", "common", "projects"),
    _a("nomad", "Nomad", "🎒", "distinct_projects", 20,
       "Run turns in twenty different projects.", "rare", "projects"),
    _a("everywhere", "Everywhere At Once", "🗺️", "distinct_projects", 50,
       "Run turns in fifty different projects.", "epic", "projects"),

    # ---- The wallet ----------------------------------------------------------
    _a("investment", "Investment", "💵", "lifetime_cost", 1,
       "Spend one US dollar of tokens through Cuttle.", "common", "economy"),
    _a("venture_capital", "Venture Capital", "🏦", "lifetime_cost", 100,
       "Spend one hundred US dollars of tokens.", "rare", "economy"),
    _a("tycoon", "Tycoon", "🤑", "lifetime_cost", 1_000,
       "Spend a thousand US dollars of tokens.", "legendary", "economy"),
    _a("broke", "Broke", "🕳️", "lifetime_cost", 10_000,
       "Spend ten thousand US dollars of tokens. Ouch.", "mythic", "economy",
       hidden=True, hint="Maybe route some of that."),
]

BY_ID: Dict[str, Achievement] = {a.id: a for a in _ACHIEVEMENTS}

# Sanity: a typo in a metric name must fail at import, not silently never unlock.
REQUIRED_METRICS = {
    "turns_total", "turns_completed", "user_messages", "sessions_with_turns",
    "lifetime_total_tokens", "lifetime_input_tokens", "lifetime_output_tokens",
    "lifetime_cached_tokens", "max_single_turn_tokens",
    "max_single_turn_input_tokens", "max_turn_latency_ms", "agent_hours",
    "long_turns_10min", "cancelled_turns", "distinct_harnesses",
    "distinct_models", "escalations", "subagent_children", "night_turns",
    "dawn_turns", "weekend_turns", "active_hours_distinct", "streak_days",
    "active_days", "distinct_projects", "lifetime_cost",
}

_missing_metrics = sorted({a.metric for a in _ACHIEVEMENTS} - REQUIRED_METRICS)
if _missing_metrics:  # pragma: no cover - developer guard
    raise RuntimeError(
        "catalog references unknown achievement metrics: "
        + ", ".join(_missing_metrics)
        + " (add them to api.achievements.evaluator)"
    )


def all_achievements() -> List[Achievement]:
    return list(_ACHIEVEMENTS)


def get(achievement_id: Any) -> Optional[Achievement]:
    return BY_ID.get(str(achievement_id or "").strip().lower())


def catalog_payload() -> List[Dict[str, Any]]:
    ordered = sorted(
        _ACHIEVEMENTS,
        key=lambda a: (CATEGORY_ORDER.index(a.category) if a.category in CATEGORY_ORDER else 99,
                       a.tier, a.threshold),
    )
    return [a.to_dict() for a in ordered]


def summary() -> Dict[str, Any]:
    by_rarity = {r: 0 for r in RARITY_ORDER}
    by_category = {c: 0 for c in CATEGORY_ORDER}
    for a in _ACHIEVEMENTS:
        by_rarity[a.rarity] = by_rarity.get(a.rarity, 0) + 1
        by_category[a.category] = by_category.get(a.category, 0) + 1
    return {"total": len(_ACHIEVEMENTS), "by_rarity": by_rarity, "by_category": by_category}