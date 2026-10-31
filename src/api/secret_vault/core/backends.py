"""Storage backends: where a secret value lives.

Every backend stores or reads a value by *reference* (``ref``) — usually the
secret's name, or an external item path for read-only managers such as
1Password (``op://Vault/item/password``). Values are returned to the caller
only; backends never log or print them.

Kinds:

* ``keyring`` — the Python ``keyring`` package (MIT): Windows Credential
  Manager, macOS Keychain, Secret Service (GNOME Keyring / KeePassXC) or
  KWallet. Any third-party keyring backend (e.g. ``keyrings.cryptfile`` for
  headless machines) can be chosen with ``options.keyring_backend``.
* ``command`` — any CLI password manager through argv templates
  (``{ref}`` placeholder); values move on stdin/stdout, never argv.
* ``memory`` — process-local, for tests and throwaway sessions.
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, Tuple, runtime_checkable


class BackendError(RuntimeError):
    """A backend could not read, write, or delete a value (message is safe to show)."""


@runtime_checkable
class Backend(Protocol):
    id: str
    kind: str
    label: str

    @property
    def writable(self) -> bool: ...

    def available(self) -> Tuple[bool, str]: ...

    def get(self, ref: str) -> Optional[str]: ...

    def set(self, ref: str, value: str) -> None: ...

    def delete(self, ref: str) -> None: ...


# ── keyring ────────────────────────────────────────────────────────────────


class KeyringBackend:
    kind = "keyring"

    def __init__(self, id: str = "keyring", label: str = "OS keyring", *,
                 service: str = "cuttle", keyring_backend: str = "") -> None:
        self.id = id
        self.label = label
        self.service = service or "cuttle"
        self.keyring_backend = keyring_backend or ""
        self._kr = None

    @property
    def writable(self) -> bool:
        return True

    def _keyring(self):
        if self._kr is not None:
            return self._kr
        try:
            import keyring  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on install
            raise BackendError("The Python 'keyring' package is not installed "
                               "(pip install keyring).") from exc
        if self.keyring_backend:
            module_name, _, cls_name = self.keyring_backend.rpartition(".")
            try:
                cls = getattr(importlib.import_module(module_name), cls_name)
                self._kr = cls()
            except Exception as exc:
                raise BackendError(f"Keyring backend {self.keyring_backend!r} "
                                   f"could not be loaded: {exc}") from exc
        else:
            self._kr = keyring.get_keyring()
        return self._kr

    def describe(self) -> str:
        try:
            kr = self._keyring()
        except BackendError as exc:
            return str(exc)
        return f"{type(kr).__module__}.{type(kr).__name__}"

    def available(self) -> Tuple[bool, str]:
        try:
            kr = self._keyring()
        except BackendError as exc:
            return False, str(exc)
        name = f"{type(kr).__module__}.{type(kr).__name__}"
        if "fail" in name.lower() or "null" in name.lower():
            return False, ("No usable OS keyring was found. On Linux install and unlock "
                           "GNOME Keyring/KWallet/KeePassXC (Secret Service), or choose "
                           "another keyring backend or a command backend.")
        return True, name

    def get(self, ref: str) -> Optional[str]:
        try:
            return self._keyring().get_password(self.service, ref)
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(f"Keyring read failed: {type(exc).__name__}") from exc

    def set(self, ref: str, value: str) -> None:
        try:
            self._keyring().set_password(self.service, ref, value)
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(f"Keyring write failed: {type(exc).__name__}: {exc}") from exc

    def delete(self, ref: str) -> None:
        try:
            self._keyring().delete_password(self.service, ref)
        except BackendError:
            raise
        except Exception as exc:
            if type(exc).__name__ == "PasswordDeleteError":
                return
            raise BackendError(f"Keyring delete failed: {type(exc).__name__}") from exc


# ── external command ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class CommandPreset:
    id: str
    label: str
    get: Tuple[str, ...]
    set: Tuple[str, ...] = ()
    delete: Tuple[str, ...] = ()
    first_line: bool = False
    ref_hint: str = ""
    help: str = ""
    platforms: Tuple[str, ...] = ("linux", "darwin", "win32")


# Presets cover common managers; users can add any CLI with custom argv.
COMMAND_PRESETS: Dict[str, CommandPreset] = {p.id: p for p in (
    CommandPreset(
        id="pass", label="pass (password-store)",
        get=("pass", "show", "{ref}"),
        set=("pass", "insert", "--multiline", "--force", "{ref}"),
        delete=("pass", "rm", "--force", "{ref}"),
        first_line=True, ref_hint="cuttle/<name>",
        help="GPG-encrypted password store (gopass is compatible: replace 'pass' with 'gopass').",
        platforms=("linux", "darwin")),
    CommandPreset(
        id="secret-tool", label="secret-tool (libsecret)",
        get=("secret-tool", "lookup", "service", "cuttle", "account", "{ref}"),
        set=("secret-tool", "store", "--label=Cuttle {ref}", "service", "cuttle", "account", "{ref}"),
        delete=("secret-tool", "clear", "service", "cuttle", "account", "{ref}"),
        ref_hint="<name>", help="Secret Service via libsecret's CLI.",
        platforms=("linux",)),
    CommandPreset(
        id="1password", label="1Password CLI (op)",
        get=("op", "read", "--no-newline", "{ref}"),
        ref_hint="op://<vault>/<item>/<field>",
        help="Read-only reference. Sign in with 'op signin' or the desktop app integration."),
    CommandPreset(
        id="bitwarden", label="Bitwarden CLI (bw)",
        get=("bw", "get", "password", "{ref}"),
        ref_hint="<item name or id>",
        help="Read-only reference. Requires an unlocked session (BW_SESSION in the Cuttle .env)."),
    CommandPreset(
        id="keepassxc", label="KeePassXC CLI",
        get=("keepassxc-cli", "show", "--quiet", "--show-protected", "--attributes", "Password",
             "--no-password", "--key-file", "{keyfile}", "{database}", "{ref}"),
        ref_hint="<entry path>",
        help="Read-only. Edit the argv to point at your database and key file "
             "(Secret Service integration also works through the keyring backend)."),
)}


def _fill(template: Tuple[str, ...] | List[str], ref: str) -> List[str]:
    return [str(part).replace("{ref}", ref) for part in template]


class CommandBackend:
    kind = "command"

    def __init__(self, id: str, label: str = "", *, get: List[str] | Tuple[str, ...],
                 set: List[str] | Tuple[str, ...] = (), delete: List[str] | Tuple[str, ...] = (),
                 first_line: bool = False, timeout: float = 30.0,
                 env: Optional[Dict[str, str]] = None) -> None:
        if not get:
            raise ValueError("A command backend needs a 'get' command.")
        self.id = id
        self.label = label or id
        self.get_argv = list(get)
        self.set_argv = list(set or [])
        self.delete_argv = list(delete or [])
        self.first_line = bool(first_line)
        self.timeout = float(timeout)
        self.env = env

    @classmethod
    def from_preset(cls, id: str, preset: str, label: str = "") -> "CommandBackend":
        p = COMMAND_PRESETS[preset]
        return cls(id, label or p.label, get=p.get, set=p.set, delete=p.delete,
                   first_line=p.first_line)

    @property
    def writable(self) -> bool:
        return bool(self.set_argv)

    def available(self) -> Tuple[bool, str]:
        exe = self.get_argv[0]
        found = shutil.which(exe)
        if not found:
            return False, f"'{exe}' was not found on PATH."
        return True, found

    def _run(self, argv: List[str], stdin: Optional[str] = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                argv, input=(stdin.encode("utf-8") if stdin is not None else None),
                stdin=None if stdin is not None else subprocess.DEVNULL,
                capture_output=True, timeout=self.timeout, env=self.env, check=False)
        except FileNotFoundError as exc:
            raise BackendError(f"'{argv[0]}' was not found on PATH.") from exc
        except subprocess.TimeoutExpired as exc:
            raise BackendError(f"'{argv[0]}' timed out after {self.timeout:.0f}s "
                               "(is it waiting for an unlock prompt?).") from exc

    @staticmethod
    def _err(proc: subprocess.CompletedProcess, verb: str, exe: str) -> BackendError:
        # stderr from password managers is diagnostic text, not the secret.
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        return BackendError(f"'{exe}' {verb} failed (exit {proc.returncode}) {tail[0][:200]}".strip())

    def get(self, ref: str) -> Optional[str]:
        proc = self._run(_fill(self.get_argv, ref))
        if proc.returncode != 0:
            raise self._err(proc, "read", self.get_argv[0])
        text = proc.stdout.decode("utf-8", "replace")
        if self.first_line:
            text = text.split("\n", 1)[0].removesuffix("\r")
        if text.endswith("\r\n"):
            text = text[:-2]
        elif text.endswith("\n"):
            text = text[:-1]
        return text

    def set(self, ref: str, value: str) -> None:
        if not self.set_argv:
            raise BackendError(f"Backend '{self.id}' is read-only: store the value in "
                               "the password manager and reference it here.")
        proc = self._run(_fill(self.set_argv, ref), stdin=value + "\n")
        if proc.returncode != 0:
            raise self._err(proc, "write", self.set_argv[0])

    def delete(self, ref: str) -> None:
        if not self.delete_argv:
            return
        proc = self._run(_fill(self.delete_argv, ref))
        if proc.returncode != 0:
            raise self._err(proc, "delete", self.delete_argv[0])


# ── memory ─────────────────────────────────────────────────────────────────


@dataclass
class MemoryBackend:
    id: str = "memory"
    label: str = "Memory (this process only)"
    kind: str = "memory"
    values: Dict[str, str] = field(default_factory=dict)

    @property
    def writable(self) -> bool:
        return True

    def available(self) -> Tuple[bool, str]:
        return True, "process memory"

    def get(self, ref: str) -> Optional[str]:
        return self.values.get(ref)

    def set(self, ref: str, value: str) -> None:
        self.values[ref] = value

    def delete(self, ref: str) -> None:
        self.values.pop(ref, None)


def build_backend(spec: Dict[str, object]) -> Backend:
    """Instantiate a backend from a plain config row ``{id, kind, label, options}``."""
    kind = str(spec.get("kind") or "")
    bid = str(spec.get("id") or kind)
    label = str(spec.get("label") or "")
    options = spec.get("options") if isinstance(spec.get("options"), dict) else {}
    assert isinstance(options, dict)
    if kind == "keyring":
        return KeyringBackend(bid, label or "OS keyring",
                              service=str(options.get("service") or "cuttle"),
                              keyring_backend=str(options.get("keyring_backend") or ""))
    if kind == "command":
        def _argv(key: str) -> List[str]:
            raw = options.get(key)
            return [str(x) for x in raw] if isinstance(raw, list) else []
        return CommandBackend(bid, label, get=_argv("get"), set=_argv("set"),
                              delete=_argv("delete"), first_line=bool(options.get("first_line")),
                              timeout=float(options.get("timeout") or 30))
    if kind == "memory":
        return MemoryBackend(bid, label or "Memory")
    raise ValueError(f"Unknown backend kind {kind!r}.")
