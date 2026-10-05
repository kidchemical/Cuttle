"""Harness adapters for supervised coordination — reuse existing runners."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Protocol

from api.agent_router.supervised.types import CoordinatorProfile, EconomicSource, WorkerProfile

RunnerFn = Callable[..., Dict[str, Any]]


class CoordinatorAdapter(Protocol):
    def invoke(
        self,
        prompt: str,
        *,
        session_id: Any,
        project_path: Optional[str] = None,
        status_queue=None,
    ) -> Dict[str, Any]:
        ...


class WorkerAdapter(Protocol):
    def invoke(
        self,
        prompt: str,
        *,
        session_id: Any,
        project_path: Optional[str] = None,
        status_queue=None,
    ) -> Dict[str, Any]:
        ...


def codex_reasoning_cli_override(level: str) -> str:
    """Map normalized Cuttle reasoning → Codex ``model_reasoning_effort`` value.

    Codex config reference (v0.147 era): minimal | low | medium | high | xhigh.
    Cuttle stores the normalized level; this adapter alone owns the CLI translation.
    """
    from api.agent_router.supervised.types import normalize_reasoning_level

    return normalize_reasoning_level(level, default="low")


class CodexCoordinatorAdapter:
    """Coordinator via existing Codex CLI path + reasoning override."""

    def __init__(self, profile: CoordinatorProfile, runner: Optional[RunnerFn] = None):
        self.profile = profile
        self._runner = runner

    def invoke(
        self,
        prompt: str,
        *,
        session_id: Any,
        project_path: Optional[str] = None,
        status_queue=None,
    ) -> Dict[str, Any]:
        fn = self._runner or _default_codex_runner()
        effort = codex_reasoning_cli_override(self.profile.harness.reasoning or "low")
        return fn(
            prompt,
            session_id,
            status_queue=status_queue,
            project_path=project_path,
            model=self.profile.harness.model or None,
            reasoning_effort=effort,
        )


class CursorWorkerAdapter:
    """Worker via existing Cursor CLI path."""

    def __init__(self, profile: WorkerProfile, runner: Optional[RunnerFn] = None):
        self.profile = profile
        self._runner = runner

    def invoke(
        self,
        prompt: str,
        *,
        session_id: Any,
        project_path: Optional[str] = None,
        status_queue=None,
    ) -> Dict[str, Any]:
        fn = self._runner or _default_cursor_runner()
        return fn(
            prompt,
            session_id,
            status_queue=status_queue,
            project_path=project_path,
            model=self.profile.harness.model or "auto",
        )


def _default_codex_runner() -> RunnerFn:
    from api.agent_router.supervised.test_isolation import guard_external_runner

    guard_external_runner("Codex CLI")
    from api.agent_harness.runners import run_harness_web_command

    def run(prompt, chat_session_id, status_queue=None, project_path=None, model=None, **kw):
        guard_external_runner("Codex CLI")
        effort = kw.get("reasoning_effort")
        return run_harness_web_command(
            "codex",
            prompt,
            chat_session_id,
            status_queue=status_queue,
            project_path=project_path,
            model_override=model,
            execute_kwargs={"reasoning_effort": effort} if effort else None,
        )

    return run


def _default_cursor_runner() -> RunnerFn:
    from api.agent_router.supervised.test_isolation import guard_external_runner

    guard_external_runner("Cursor Agent CLI")
    from api.agent_harness.runners import run_harness_web_command

    def run(prompt, chat_session_id, status_queue=None, project_path=None, model=None, **_kw):
        guard_external_runner("Cursor Agent CLI")
        return run_harness_web_command(
            "cursor",
            prompt,
            chat_session_id,
            status_queue=status_queue,
            project_path=project_path,
            model_override=model,
        )

    return run


def economic_source_for(profile_harness_economic: str, *, paid_ok: bool) -> str:
    src = (profile_harness_economic or EconomicSource.UNKNOWN.value).strip()
    if src == EconomicSource.PAID_REQUIRES_APPROVAL.value and not paid_ok:
        return EconomicSource.PAID_REQUIRES_APPROVAL.value
    if src in (
        EconomicSource.OPENAI_API.value,
        EconomicSource.PAID_REQUIRES_APPROVAL.value,
    ) and not paid_ok:
        return EconomicSource.PAID_REQUIRES_APPROVAL.value
    return src or EconomicSource.UNKNOWN.value
