"""Discover agent folders from bundled + drop-in roots.

Roots (first wins for a given id; bundled always preferred)::

1. ``src/api/agent_harness/agents/<id>/`` — shipped connectors
2. ``CUTTLE_AGENTS_DIR`` (os.pathsep-separated) + legacy ``src/data/harness_agents/``
3. ``{Cuttle}/.cuttle_global/personal/agents/<id>/`` — install-local drop-ins
   + ``{Cuttle}/.cuttle_global/agents/<id>/`` — shared instance drop-ins
4. ``{project}/.cuttle/agents/<id>/`` — project drop-ins (when ``project_path`` given)

Drop-in folders use the same contract as bundled: ``manifest.yaml`` + ``adapter.py``
with ``build_adapter()``. Unknown CLIs never require a Cuttle core fork.

Trust: project drop-ins (``{project}/.cuttle/agents/``) execute third-party
``adapter.py`` ONLY on explicit opt-in (``CUTTLE_ALLOW_PROJECT_ADAPTERS`` /
``agent_harness.allow_project_adapters``). Manifest identity is validated
before import; each external adapter loads once (mtime-keyed) as a
uniquely-namespaced package with no ``sys.path`` mutation. Only relative
sibling imports (``from . import helper``) resolve into the drop-in, loaded
on demand; nothing else in the directory executes, and bare absolute imports
are never aliased or satisfied from the drop-in. See ADDING_AN_AGENT.md
("Project drop-in trust model").
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import os
import re
import sys
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from api.agent_harness.model_capabilities import load_model_capability_rules
from api.agent_harness.types import AgentAdapter, AgentManifest

_AGENTS_ROOT = Path(__file__).resolve().parent / "agents"
_CUTTLE_ROOT = Path(__file__).resolve().parents[3]  # .../Cuttle
_USER_AGENTS_ROOT = _CUTTLE_ROOT / "src" / "data" / "harness_agents"
_INSTANCE_AGENTS_ROOT = _CUTTLE_ROOT / ".cuttle_global" / "agents"
_PERSONAL_AGENTS_ROOT = _CUTTLE_ROOT / ".cuttle_global" / "personal" / "agents"

# (manifest, adapter, agent_dir)
_AgentEntry = Tuple[AgentManifest, AgentAdapter, Path]

# Canonical agent identity: lowercase alnum runs joined by single hyphens.
# Mirrors get_agent() lookup normalization ("my_agent" -> "my-agent") so a
# folder that lookup could never reach is rejected instead of half-working.
_CANONICAL_ID_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_CANONICAL_SLASH_RE = re.compile(r"/[a-z0-9]+(-[a-z0-9]+)*")

# External (non-bundled) adapter loads, keyed by resolved agent dir.
# Value: (source-mtimes, entry or None for a failed load). _discover()
# re-scans project roots on every call (no project_path cache), so without
# this each turn would re-execute adapter.py top-level code.
_EXTERNAL_ADAPTER_CACHE: Dict[str, Tuple[Tuple[int, ...], Optional[_AgentEntry]]] = {}

# Serializes HARNESS drop-in loads only (mtime check, package setup,
# adapter exec). It makes no claim about unrelated Python imports happening
# on other threads.
_EXTERNAL_LOAD_LOCK = threading.Lock()

_EXT_PKG_PREFIX = "cuttle_harness_ext_"


def _canonical_agent_id(folder_name: str) -> str:
    return folder_name.strip().lower().replace("_", "-")


def _canonical_slash(raw_slash: str, agent_id: str) -> str:
    s = (raw_slash or f"/{agent_id}").strip().lower().replace("_", "-")
    if not s.startswith("/"):
        s = "/" + s
    return s


def _identity_error(agent_id: str, slash: str) -> Optional[str]:
    """Validate manifest identity BEFORE the adapter module executes.

    Slash is canonicalized the same way lookup normalizes (``_`` -> ``-``),
    so ``/my_agent`` routes exactly where ``get_agent("my_agent")`` resolves.
    """
    if not _CANONICAL_ID_RE.fullmatch(agent_id):
        return f"non-canonical agent id {agent_id!r}"
    if not _CANONICAL_SLASH_RE.fullmatch(_canonical_slash(slash, agent_id)):
        return f"non-canonical slash {slash!r}"
    return None


def _load_manifest_dict(agent_dir: Path) -> Dict[str, Any]:
    path = agent_dir / "manifest.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Missing manifest: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid manifest (not a mapping): {path}")
    return data


def _manifest_from_dict(
    data: Dict[str, Any],
    *,
    fallback_id: str,
    source: str,
    agent_dir: Optional[Path] = None,
) -> AgentManifest:
    mid = str(data.get("id") or fallback_id).strip()
    models_raw = data.get("models") or []
    if isinstance(models_raw, str):
        models = [models_raw]
    else:
        models = [str(m).strip() for m in models_raw if str(m).strip()]
    executables_raw = data.get("executable_names") or []
    if isinstance(executables_raw, str):
        executable_names = [executables_raw]
    else:
        executable_names = [
            str(name).strip() for name in executables_raw if str(name).strip()
        ]
    efforts_raw = data.get("efforts") or []
    if isinstance(efforts_raw, str):
        efforts = [efforts_raw]
    else:
        efforts = [str(e).strip() for e in efforts_raw if str(e).strip()]
    model_efforts_raw = data.get("model_efforts") or {}
    model_efforts: Dict[str, List[str]] = {}
    if isinstance(model_efforts_raw, dict):
        for model, raw_levels in model_efforts_raw.items():
            model_id = str(model or "").strip()
            if not model_id:
                continue
            if isinstance(raw_levels, str):
                levels = [raw_levels]
            elif isinstance(raw_levels, (list, tuple)):
                levels = [str(level) for level in raw_levels]
            else:
                continue
            model_efforts[model_id] = [
                level.strip().lower() for level in levels if level.strip()
            ]
    try:
        schema_version = int(data.get("schema_version") or 1)
    except (TypeError, ValueError):
        schema_version = 1
    providers_raw = data.get("pricing_providers") or []
    if isinstance(providers_raw, str):
        providers_raw = [providers_raw]
    pricing_providers = [
        str(p).strip().lower() for p in providers_raw if str(p or "").strip()
    ]
    credential_raw = data.get("credential_env") or []
    if isinstance(credential_raw, str):
        credential_raw = [credential_raw]
    credential_env = [
        str(name).strip() for name in credential_raw if str(name or "").strip()
    ]
    pricing: Dict[str, Dict[str, Any]] = {}
    pricing_raw = data.get("pricing") or {}
    if isinstance(pricing_raw, dict):
        for model, spec in pricing_raw.items():
            model_id = str(model or "").strip()
            if model_id and isinstance(spec, dict):
                pricing[model_id] = dict(spec)
    model_caps = ()
    if agent_dir is not None:
        model_caps = load_model_capability_rules(agent_dir, manifest_raw=data)
    return AgentManifest(
        id=mid,
        label=str(data.get("label") or mid).strip(),
        slash=_canonical_slash(str(data.get("slash") or ""), mid),
        requires_cloud=bool(data.get("requires_cloud", True)),
        sticky=bool(data.get("sticky", True)),
        default_model=str(data.get("default_model") or "").strip(),
        smoke_model=str(data.get("smoke_model") or "").strip(),
        supports_model_pin=bool(data.get("supports_model_pin", True)),
        supports_effort=bool(data.get("supports_effort", True)),
        models=models,
        efforts=efforts,
        model_efforts=model_efforts,
        model_capabilities=model_caps,
        pricing_source=str(data.get("pricing_source") or "models_dev").strip().lower(),
        pricing_providers=pricing_providers,
        pricing=pricing,
        pricing_benchmark=str(data.get("pricing_benchmark") or "").strip().lower(),
        resume=bool(data.get("resume", True)),
        capabilities_inject=str(data.get("capabilities_inject") or "once_per_resume").strip(),
        env_profile=str(data.get("env_profile") or "native").strip(),
        activity=str(data.get("activity") or "heartbeat").strip(),
        missing_cli_hint=str(data.get("missing_cli_hint") or "").strip(),
        notes=str(data.get("notes") or "").strip(),
        hint=str(data.get("hint") or "").strip(),
        install_hint=str(data.get("install_hint") or "").strip(),
        install_kind=str(data.get("install_kind") or "").strip(),
        install_package=str(data.get("install_package") or "").strip(),
        install_url_windows=str(data.get("install_url_windows") or "").strip(),
        install_url_posix=str(data.get("install_url_posix") or "").strip(),
        executable_names=executable_names,
        auto_install=bool(data.get("auto_install", False)),
        credential_env=credential_env,
        auth_command=str(data.get("auth_command") or "").strip(),
        schema_version=schema_version,
        source=source,
    )


def _import_bundled_adapter(agent_id: str) -> AgentAdapter:
    mod = importlib.import_module(f"api.agent_harness.agents.{agent_id}.adapter")
    factory = getattr(mod, "build_adapter", None)
    if callable(factory):
        return factory()
    cls = getattr(mod, "Adapter", None)
    if cls is None:
        raise AttributeError(
            f"agents.{agent_id}.adapter must export build_adapter() or Adapter"
        )
    return cls()


def _exec_module_fresh(spec, mod) -> None:
    """exec_module() that cannot reuse a stale ``__pycache__`` entry.

    pyc validation is (mtime-seconds, size): a hot-edit written within the
    same second with the same byte size would otherwise re-execute old code
    even though our mtime-ns cache correctly detected the change. Removing
    the cached pyc forces a recompile from source (which exec then rewrites).
    """
    assert spec.loader is not None
    try:
        if spec.origin:
            os.unlink(importlib.util.cache_from_source(spec.origin))
    except (OSError, ValueError):
        pass
    spec.loader.exec_module(mod)


def _purge_external_modules(pkg_name: str) -> None:
    """Drop this drop-in's namespaced modules (pkg + pkg.*) after a failure
    or when sources changed. Never touches bare names owned by others."""
    for key in [
        k
        for k in sys.modules
        if k == pkg_name or k.startswith(pkg_name + ".")
    ]:
        try:
            del sys.modules[key]
        except KeyError:
            pass


def _source_mtimes(agent_dir: Path) -> Tuple[int, ...]:
    """mtimes of everything one drop-in load executes (adapter + top-level
    sibling .py). Editing any of them invalidates the load-once cache."""
    stamps: List[int] = []
    try:
        entries = sorted(agent_dir.iterdir())
    except OSError:
        return (-1,)
    for child in entries:
        if child.suffix != ".py" or not child.is_file():
            continue
        try:
            stamps.append(child.stat().st_mtime_ns)
        except OSError:
            stamps.append(-1)
    return tuple(stamps) or (-1,)


def _import_external_adapter(agent_dir: Path, agent_id: str) -> AgentAdapter:
    """Load a drop-in as a uniquely-namespaced package; never touch sys.path.

    Import contract (only contract):

    - ``from . import helper`` / ``from .sub.deep import VAL`` — resolved
      on demand by the normal import machinery through the package
      ``__path__``, isolated per drop-in. Files the adapter never imports
      are never executed, and a broken file the adapter never imports
      cannot break the adapter.
    - Bare ``import helper`` is NOT a sibling import: it resolves against
      the ambient environment (stdlib / site-packages / live modules) and
      fails loudly otherwise, so the author migrates to relative imports.
      Nothing is ever aliased, scanned, or pre-loaded to satisfy it.
    """
    agent_dir_str = str(agent_dir.resolve())
    # Unique package name so two drop-ins never share a namespace, even with
    # the same folder name or the same sibling basenames.
    pkg_name = f"{_EXT_PKG_PREFIX}{agent_id}_{abs(hash(agent_dir_str))}"
    adapter_path = agent_dir / "adapter.py"
    pkg = sys.modules.get(pkg_name)
    if pkg is None or getattr(pkg, "__path__", None) != [agent_dir_str]:
        _purge_external_modules(pkg_name)
        pkg = importlib.util.module_from_spec(
            importlib.machinery.ModuleSpec(
                pkg_name, loader=None, is_package=True
            )
        )
        pkg.__path__ = [agent_dir_str]
        sys.modules[pkg_name] = pkg
    spec = importlib.util.spec_from_file_location(
        f"{pkg_name}.adapter", adapter_path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load adapter from {adapter_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{pkg_name}.adapter"] = mod
    _exec_module_fresh(spec, mod)
    factory = getattr(mod, "build_adapter", None)
    if callable(factory):
        return factory()
    cls = getattr(mod, "Adapter", None)
    if cls is None:
        raise AttributeError(
            f"{adapter_path} must export build_adapter() or Adapter"
        )
    return cls()


def _load_external_entry(
    agent_dir: Path, agent_id: str, raw: Dict[str, Any], source: str
) -> Optional[_AgentEntry]:
    """Locked load-once wrapper: mtime-keyed cache, failures cached quiet."""
    try:
        cache_key = str(agent_dir.resolve())
    except OSError:
        cache_key = str(agent_dir)
    want = _source_mtimes(agent_dir)
    with _EXTERNAL_LOAD_LOCK:
        hit = _EXTERNAL_ADAPTER_CACHE.get(cache_key)
        if hit is not None and hit[0] == want:
            return hit[1]
        # Stale sources: purge namespaced modules so the re-exec below (and
        # its relative `from . import ...` lookups) cannot reuse edited code.
        if hit is not None:
            _purge_external_modules(
                f"{_EXT_PKG_PREFIX}{agent_id}_{abs(hash(cache_key))}"
            )
        try:
            raw["id"] = agent_id  # folder name is canonical
            manifest = _manifest_from_dict(
                raw, fallback_id=agent_id, source=source, agent_dir=agent_dir
            )
            adapter = _import_external_adapter(agent_dir, agent_id)
        except Exception as exc:
            _purge_external_modules(
                f"{_EXT_PKG_PREFIX}{agent_id}_{abs(hash(cache_key))}"
            )
            _EXTERNAL_ADAPTER_CACHE[cache_key] = (want, None)
            print(f"[agent_harness] skip {agent_dir}: {exc}", flush=True)
            return None
        entry = (manifest, adapter, agent_dir)
        _EXTERNAL_ADAPTER_CACHE[cache_key] = (want, entry)
        return entry


def _project_adapters_allowed() -> bool:
    env = (os.environ.get("CUTTLE_ALLOW_PROJECT_ADAPTERS") or "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    try:
        from managers.settings_manager import get_settings_manager

        cfg = get_settings_manager().get_setting("agent_harness") or {}
        if isinstance(cfg, dict) and cfg.get("allow_project_adapters"):
            return True
    except Exception:
        pass
    return False


def _load_agent_dir(
    agent_dir: Path, *, source: str, bundled: bool
) -> Optional[_AgentEntry]:
    if not agent_dir.is_dir() or agent_dir.name.startswith(("_", ".")):
        return None
    if not (agent_dir / "manifest.yaml").is_file():
        return None
    if not (agent_dir / "adapter.py").is_file():
        return None
    agent_id = _canonical_agent_id(agent_dir.name)
    try:
        raw = _load_manifest_dict(agent_dir)
    except Exception as exc:
        print(f"[agent_harness] skip {agent_dir}: {exc}", flush=True)
        return None
    # Identity is validated BEFORE the adapter module executes: a malformed
    # manifest (hijack slash, non-canonical id) must neither run third-party
    # code nor enter routing tables.
    problem = _identity_error(agent_id, str(raw.get("slash") or f"/{agent_id}"))
    if problem is not None:
        print(f"[agent_harness] skip {agent_dir}: {problem}", flush=True)
        return None
    if not bundled:
        return _load_external_entry(agent_dir, agent_id, raw, source)
    try:
        raw["id"] = agent_id  # folder name is canonical
        manifest = _manifest_from_dict(
            raw, fallback_id=agent_id, source=source, agent_dir=agent_dir
        )
        return manifest, _import_bundled_adapter(agent_id), agent_dir
    except Exception as exc:
        print(f"[agent_harness] skip {agent_dir}: {exc}", flush=True)
        return None


def _scan_root(root: Path, *, source: str, bundled: bool = False) -> Dict[str, _AgentEntry]:
    found: Dict[str, _AgentEntry] = {}
    if not root.is_dir():
        return found
    for child in sorted(root.iterdir()):
        entry = _load_agent_dir(child, source=source, bundled=bundled)
        if entry is None:
            continue
        aid = entry[0].id
        if aid not in found:
            found[aid] = entry
    return found


def _env_agent_roots() -> List[Path]:
    raw = (os.environ.get("CUTTLE_AGENTS_DIR") or "").strip()
    if not raw:
        return []
    out: List[Path] = []
    for part in raw.split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        try:
            p = Path(part).expanduser().resolve()
        except OSError:
            continue
        if p.is_dir():
            out.append(p)
    return out


def _project_agent_roots(project_path: Optional[str]) -> List[Path]:
    """``{project}/.cuttle/agents`` plus parent walk (same idea as project actions)."""
    if not project_path:
        return []
    try:
        root = Path(project_path).resolve()
    except OSError:
        return []
    if not root.is_dir():
        return []
    dirs: List[Path] = []
    seen = set()
    candidates = [root]
    # Unity-style nested source/
    nested = root / "source"
    if nested.is_dir():
        candidates.append(nested)
    # Walk up a few levels so opening …/Cuttle/src still finds …/Cuttle/.cuttle/agents
    cur = root
    for _ in range(4):
        parent = cur.parent
        if parent == cur:
            break
        candidates.append(parent)
        cur = parent
    for owner in candidates:
        primary = owner / ".cuttle" / "agents"
        try:
            key = str(primary.resolve())
        except OSError:
            continue
        if key in seen:
            continue
        if primary.is_dir():
            seen.add(key)
            dirs.append(primary)
    return dirs


@lru_cache(maxsize=1)
def _discover_global() -> Dict[str, _AgentEntry]:
    """Bundled + user/instance drop-ins (no project path)."""
    found: Dict[str, _AgentEntry] = {}
    # 1) Bundled — always first; later roots cannot override these ids.
    found.update(_scan_root(_AGENTS_ROOT, source="bundled", bundled=True))
    bundled_ids = set(found.keys())

    def _merge_dropins(root: Path, source: str) -> None:
        for aid, entry in _scan_root(root, source=source, bundled=False).items():
            if aid in bundled_ids:
                continue  # never shadow first-party
            if aid not in found:
                found[aid] = entry

    for root in _env_agent_roots():
        _merge_dropins(root, "user")
    _merge_dropins(_USER_AGENTS_ROOT, "user")  # Legacy packs; offline migration consolidates them.
    _merge_dropins(_PERSONAL_AGENTS_ROOT, "user")
    # Instance .cuttle_global/agents — skip if it's the same path we already scanned as project later
    if _INSTANCE_AGENTS_ROOT.resolve() != _AGENTS_ROOT.resolve():
        _merge_dropins(_INSTANCE_AGENTS_ROOT, "user")
    return found


def _discover(project_path: Optional[str] = None) -> Dict[str, _AgentEntry]:
    found = dict(_discover_global())
    bundled_ids = {aid for aid, e in found.items() if e[0].source == "bundled"}
    for root in _project_agent_roots(project_path):
        # Avoid double-counting instance root when project is Cuttle itself.
        try:
            if root.resolve() == _INSTANCE_AGENTS_ROOT.resolve():
                continue
        except OSError:
            pass
        if not _project_adapters_allowed():
            continue
        for aid, entry in _scan_root(root, source="project", bundled=False).items():
            if aid in bundled_ids:
                continue
            # Project drop-ins may override user drop-ins of the same id.
            found[aid] = entry
    return found


def reload_catalog() -> None:
    """Clear discovery cache (tests / hot-add)."""
    _discover_global.cache_clear()
    with _EXTERNAL_LOAD_LOCK:
        _EXTERNAL_ADAPTER_CACHE.clear()
        for key in [
            k for k in sys.modules if k.startswith(_EXT_PKG_PREFIX)
        ]:
            try:
                del sys.modules[key]
            except KeyError:
                pass


def list_agents(project_path: Optional[str] = None) -> List[str]:
    return sorted(_discover(project_path).keys())


def list_agent_manifests(project_path: Optional[str] = None) -> List[AgentManifest]:
    disc = _discover(project_path)
    return [disc[k][0] for k in sorted(disc.keys())]


def get_agent(
    agent_id: str, project_path: Optional[str] = None
) -> Optional[Tuple[AgentManifest, AgentAdapter]]:
    aid = (agent_id or "").strip().lower().replace("_", "-")
    aliases = {
        "open-code": "opencode",
        "open_code": "opencode",
        "dsh": "deepseek",
        "deepseek-harness": "deepseek",
    }
    aid = aliases.get(aid, aid)
    entry = _discover(project_path).get(aid)
    if not entry:
        return None
    return entry[0], entry[1]


def sticky_prefixes_from_harness(project_path: Optional[str] = None) -> List[str]:
    out: List[str] = []
    for manifest in list_agent_manifests(project_path):
        if manifest.sticky:
            p = manifest.slash_prefix()
            if p not in out:
                out.append(p)
    return out


def match_slash_command(
    message: str, project_path: Optional[str] = None
) -> Optional[Tuple[str, str]]:
    """
    If ``message`` starts with a harness agent slash, return ``(agent_id, prompt)``.

    Prompt may be empty (caller should show usage help).
    """
    import re

    raw = (message or "").strip()
    if not raw.startswith("/"):
        return None
    for manifest in list_agent_manifests(project_path):
        token = manifest.slash_prefix().rstrip()
        esc = re.escape(token)
        if re.match(rf"^{esc}(\s|$)", raw, flags=re.I):
            prompt = re.sub(rf"^{esc}\s*", "", raw, count=1, flags=re.I)
            prompt = prompt.strip().strip('"').strip("'")
            return manifest.id, prompt
    return None


def public_catalog(project_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Catalog rows for the palette, Settings, and the OOBE wizard.

    Guest authentication belongs to each CLI. Legacy credential fields remain
    in the wire shape but never consult Cuttle's provider environment.
    """
    rows: List[Dict[str, Any]] = []
    for agent_id in list_agents(project_path):
        pair = get_agent(agent_id, project_path)
        if not pair:
            continue
        manifest, adapter = pair
        try:
            avail = bool(adapter.available())
        except Exception:
            avail = False
        row = manifest.to_public_dict(available=avail)
        row["credential_env"] = []
        row["credential_present"] = False
        # Installed is not a claim that the native login is valid.
        row["ready"] = avail
        rows.append(row)
    return rows


def discovery_roots(project_path: Optional[str] = None) -> List[Dict[str, str]]:
    """Debug/help: where the catalog looks for agent folders."""
    rows: List[Dict[str, str]] = [
        {"source": "bundled", "path": str(_AGENTS_ROOT)},
    ]
    for p in _env_agent_roots():
        rows.append({"source": "user", "path": str(p)})
    rows.append({"source": "user", "path": str(_USER_AGENTS_ROOT)})
    rows.append({"source": "user", "path": str(_PERSONAL_AGENTS_ROOT)})
    rows.append({"source": "user", "path": str(_INSTANCE_AGENTS_ROOT)})
    for p in _project_agent_roots(project_path):
        rows.append({"source": "project", "path": str(p)})
    return rows
