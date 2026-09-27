"""Named Jev thresholds — keep policy numbers in one file for review."""

# Router brain
NEEDS_EXECUTION_NOUL = 0.45
TARGET_CONFIDENCE_FLOOR = 0.42
ESCALATE_DIFFICULTY_SCORE = 1.45  # Score rubric: 0=low, 1=medium, 2=high

# Rage / silent-failure investigator
SAME_ISSUE_NOUL = 0.55
ATTEMPT_FAILED_NOUL = 0.62

# Context / skill ranking
NEEDS_EXTRA_CONTEXT_NOUL = 0.48
RANK_CONFIDENCE_FLOOR = 0.38
MAX_INJECT_ITEMS = 3
MAX_INJECT_CHARS = 8000
PER_ITEM_CHARS = 2800

# Turn labels (dashboard)
LABEL_CONFIDENCE_FLOOR = 0.40

# Hourly regress watcher
REGRESS_NOUL = 0.58
SUITE_CONFIDENCE_FLOOR = 0.35
REGRESS_INTERVAL_S = 3600
REGRESS_LOOKBACK_S = 3600
PYTEST_TIMEOUT_S = 180
