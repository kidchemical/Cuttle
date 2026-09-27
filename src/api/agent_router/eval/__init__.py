"""Router evaluation — decision-only (never runs execution agents)."""

from api.agent_router.eval.report import (
    format_batch_preview,
    format_batch_summary,
    format_single_eval,
    save_eval_report,
)
from api.agent_router.eval.runner import evaluate_prompt, run_suite
from api.agent_router.eval.scoring import score_case
from api.agent_router.eval.suites import list_suite_names, load_suite, parse_batch_args

__all__ = [
    "evaluate_prompt",
    "format_batch_preview",
    "format_batch_summary",
    "format_single_eval",
    "list_suite_names",
    "load_suite",
    "parse_batch_args",
    "run_suite",
    "save_eval_report",
    "score_case",
]
