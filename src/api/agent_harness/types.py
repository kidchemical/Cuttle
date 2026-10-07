"""Shared types for the Agent Harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable


def normalize_chat_session_id(chat_session_id: Any) -> Optional[str]:
    """Opaque string key for resume maps / adapter protocol.

    DB ids are ints; callers also pass ``db_session_*`` strings. The kernel
    coerces once at the boundary so adapters can treat ``Optional[str]`` as honest.
    """
    if chat_session_id is None:
        return None
    s = str(chat_session_id).strip()
    return s or None


@dataclass(frozen=True)
class AgentManifest:
    """Declarative agent record (loaded from each agent's manifest.yaml)."""

    id: str
    label: str
    slash: str
    requires_cloud: bool = True
    sticky: bool = True
    default_model: str = ""
    # Cheap id for live smoke. If empty, smoke uses default_model. Never silently
    # pick a premium/frontier id when a cheaper sibling exists.
    smoke_model: str = ""
    # CLI capabilities the palette + starred-defaults API gate on. Cursor bakes
    # effort into the model id, so it declares supports_effort=false and the
    # palette never renders effort rows for it.
    supports_model_pin: bool = True
    supports_effort: bool = True
    # Discrete reasoning-effort levels the CLI accepts. Empty = the agent has
    # no such flag (Cursor bakes effort into the model id). Unpinned/"none" is
    # never a level: unpinned turns omit the flag.
    efforts: List[str] = field(default_factory=list)
    models: List[str] = field(default_factory=list)
    # Per-model reasoning effort values reported/accepted by this connector.
    # Empty/missing model entries mean Cuttle has no verified level list.
    model_efforts: Dict[str, List[str]] = field(default_factory=dict)
    # Per-model CLI limits for THIS connector (see model_capabilities.py).
    # Declared in manifest.yaml and/or optional model_capabilities.yaml /
    # models/*.yaml under the agent folder — never adapter hardcodes.
    model_capabilities: Tuple[Any, ...] = field(default_factory=tuple)
    # ``/cost`` pricing: ``cli`` when the harness reports per-model rates
    # itself (OpenCode), else ``models_dev``. ``pricing_providers`` are the
    # models.dev provider ids this harness bills through, tried first so a
    # Codex model resolves to OpenAI list price, not a reseller row.
    # ``pricing`` holds per-model USD-per-1M overrides for ids no catalog lists.
    # ``pricing_benchmark`` prefers one measured $/task source
    # (``cursorbench`` / ``deepswe`` / ``swebench``) when it covers any row.
    pricing_source: str = "models_dev"
    pricing_providers: List[str] = field(default_factory=list)
    pricing: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    pricing_benchmark: str = ""
    resume: bool = True
    capabilities_inject: str = "once_per_resume"  # once_per_resume | always | never
    env_profile: str = "native"  # native | wsl | either
    activity: str = "heartbeat"  # none | heartbeat | jsonl | line_hints
    missing_cli_hint: str = ""
    notes: str = ""
    # Optional palette / UX
    hint: str = ""
    install_hint: str = ""
    # Optional single-glyph tile for Settings (emoji or symbol, e.g. "◉").
    # Empty = frontend falls back to the label initial. Never a logo file:
    # no trademarked paths, no network, theme-neutral text glyphs only.
    icon: str = ""
    # Trusted, declarative CLI bootstrap. Only bundled connectors may use it.
    install_kind: str = ""  # npm_global | script_url
    install_package: str = ""
    install_url_windows: str = ""
    install_url_posix: str = ""
    executable_names: List[str] = field(default_factory=list)
    auto_install: bool = False
    # Credential env vars this CLI needs, most-preferred first. Declarative so
    # Settings can say "add GEMINI_API_KEY" without parsing
    # install_hint prose, and so drop-in adapters get it for free. Empty means
    # the CLI owns its own auth (e.g. `claude auth login`) or needs none.
    credential_env: List[str] = field(default_factory=list)
    # Command the user runs to establish auth when no env var is wanted.
    auth_command: str = ""
    schema_version: int = 1
    # Discovery provenance: bundled | user | project
    source: str = "bundled"

    def slash_prefix(self) -> str:
        s = (self.slash or f"/{self.id}").strip()
        if not s.startswith("/"):
            s = "/" + s
        if not s.endswith(" "):
            s += " "
        return s

    def to_public_dict(self, *, available: Optional[bool] = None) -> Dict[str, Any]:
        install = self.install_hint or self.missing_cli_hint
        hint = self.hint or self.notes
        out: Dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "slash": self.slash_prefix().rstrip(),
            "prefix": self.slash_prefix(),
            "requires_cloud": bool(self.requires_cloud),
            "sticky": bool(self.sticky),
            "stickySession": bool(self.sticky),
            "default_model": self.default_model,
            "smoke_model": self.smoke_model or self.default_model,
            "supports_model_pin": bool(self.supports_model_pin),
            "supports_effort": bool(self.supports_effort),
            "models": list(self.models or []),
            "efforts": list(self.efforts or []),
            "model_efforts": {
                str(model): list(levels or [])
                for model, levels in (self.model_efforts or {}).items()
            },
            "resume": bool(self.resume),
            "env_profile": self.env_profile,
            "activity": self.activity,
            "hint": hint,
            "install_hint": install,
            "icon": self.icon or "",
            "installable": bool(self.install_kind),
            "auto_install": bool(self.auto_install),
            "notes": self.notes,
            "credential_env": list(self.credential_env or []),
            "auth_command": self.auth_command or "",
            "harness": True,
            "schema_version": int(self.schema_version or 1),
            "source": self.source or "bundled",
        }
        if available is not None:
            out["available"] = bool(available)
            out["status"] = "ready" if available else "missing_cli"
            # Palette-facing hint: when CLI is missing, surface the install line.
            if not available and install:
                out["hint"] = install
        return out


@dataclass
class AgentResult:
    """Normalized adapter result consumed by the shared kernel."""

    success: bool
    output: str = ""
    error: Optional[str] = None
    usage: Dict[str, Any] = field(default_factory=dict)
    session_id: Optional[str] = None  # CLI-native resume id
    model: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AgentAdapter(Protocol):
    """Thin CLI-specific surface. Everything else lives in the kernel.

    ``resolve_cwd`` may refine the chip path (WSL conversion). The kernel
    already resolved the project directory and will reject a different repo.
    """

    def available(self) -> bool: ...

    def resolve_cwd(self, project_path: str) -> str: ...

    def load_resume(self, cwd: str, chat_session_id: Optional[str]) -> Optional[str]: ...

    def save_resume(
        self, cwd: str, chat_session_id: Optional[str], cli_session_id: Optional[str]
    ) -> None: ...

    def clear_resume(self, cwd: str, chat_session_id: Optional[str]) -> None: ...

    async def execute(
        self,
        prompt: str,
        *,
        cwd: str,
        resume: Optional[str],
        model: Optional[str],
        status_queue: Any = None,
        chat_session_id: Optional[str] = None,
        timeout: float = 600.0,
    ) -> AgentResult: ...
