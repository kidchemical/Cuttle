"""Run a child command with secrets injected, never through argv.

Delivery kinds (``Injection.kind``):

* ``env``     — ``VAR=<value>`` in the child's environment.
* ``file``    — value written to a 0600 temp file; ``VAR=<path>``; deleted after.
* ``stdin``   — value + newline written to the child's stdin, then closed.
* ``askpass`` — answers password prompts from ssh/scp/sftp/rsync-over-ssh
  (``SSH_ASKPASS`` + ``SSH_ASKPASS_REQUIRE=force``), ``sudo -A``
  (``SUDO_ASKPASS``) and git (``GIT_ASKPASS``). ``match`` optionally maps a
  prompt regex to this secret when several are in play.

Child stdout/stderr pass through :class:`~.redact.Redactor` unless disabled.
Planning (:func:`plan`) is pure; :func:`run` performs the effects.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Callable, Dict, List, Optional

from .names import require_env_var, require_name
from .redact import Redactor

KINDS = ("env", "file", "stdin", "askpass")
_ASKPASS_SCRIPT = Path(__file__).with_name("askpass.py")


@dataclass(frozen=True)
class Injection:
    kind: str
    secret: str
    var: str = ""
    match: str = ""

    def validate(self) -> "Injection":
        if self.kind not in KINDS:
            raise ValueError(f"Unknown delivery {self.kind!r} (use {', '.join(KINDS)}).")
        require_name(self.secret)
        if self.kind in ("env", "file"):
            require_env_var(self.var)
        return self


@dataclass
class Plan:
    argv: List[str]
    env_names: Dict[str, str] = field(default_factory=dict)   # VAR -> secret
    file_names: Dict[str, str] = field(default_factory=dict)  # VAR -> secret
    stdin_secret: str = ""
    askpass: List[Dict[str, str]] = field(default_factory=list)  # {match, secret, var}
    notes: List[str] = field(default_factory=list)

    @property
    def secrets(self) -> List[str]:
        names = list(self.env_names.values()) + list(self.file_names.values())
        names += [r["secret"] for r in self.askpass]
        if self.stdin_secret:
            names.append(self.stdin_secret)
        return sorted(set(names))


def parse_spec(kind: str, raw: str) -> Injection:
    """CLI form: env/file ``VAR=secret``; stdin ``secret``; askpass ``secret[=regex]``."""
    raw = str(raw or "").strip()
    if kind in ("env", "file"):
        var, sep, name = raw.partition("=")
        if not sep:
            raise ValueError(f"--{kind} expects VAR=secret-name, got {raw!r}.")
        return Injection(kind, name.strip(), var=var.strip()).validate()
    if kind == "askpass":
        name, _, match = raw.partition("=")
        return Injection(kind, name.strip(), match=match.strip()).validate()
    return Injection(kind, raw).validate()


def plan(argv: List[str], injections: List[Injection], *, platform: str = sys.platform) -> Plan:
    if not argv:
        raise ValueError("No command given (put it after --).")
    out = Plan(argv=list(argv))
    for inj in injections:
        inj.validate()
        if inj.kind == "env":
            out.env_names[inj.var] = inj.secret
        elif inj.kind == "file":
            out.file_names[inj.var] = inj.secret
        elif inj.kind == "stdin":
            if out.stdin_secret and out.stdin_secret != inj.secret:
                raise ValueError("Only one secret can be written to stdin.")
            out.stdin_secret = inj.secret
        else:
            out.askpass.append({"match": inj.match, "secret": inj.secret,
                                "var": f"CUTTLE_ASKPASS_V{len(out.askpass)}"})
    defaults = [r for r in out.askpass if not r["match"]]
    if len(defaults) > 1:
        raise ValueError("Several --askpass secrets need a prompt regex each "
                         "(--askpass name=REGEX) so the right one answers.")
    exe = os.path.basename(out.argv[0]).lower()
    if exe.endswith(".exe"):
        exe = exe[:-4]
    if out.askpass and exe == "sudo" and platform != "win32" \
            and not any(a in ("-A", "--askpass", "-S", "--stdin") for a in out.argv[1:]):
        out.argv.insert(1, "-A")
        out.notes.append("added -A so sudo asks the askpass helper")
    return out


def _write_askpass_wrapper(tmp: Path, python: str, platform: str) -> Path:
    if platform == "win32":
        path = tmp / "askpass.cmd"
        path.write_text(f'@"{python}" -I "{_ASKPASS_SCRIPT}" %*\r\n', encoding="utf-8")
    else:
        path = tmp / "askpass.sh"
        q = lambda s: "'" + s.replace("'", "'\\''") + "'"  # noqa: E731
        path.write_text(f"#!/bin/sh\nexec {q(python)} -I {q(str(_ASKPASS_SCRIPT))} \"$@\"\n",
                        encoding="utf-8")
        path.chmod(0o700)
    return path


def _pump(src: BinaryIO, dst: BinaryIO, redactor: Optional[Redactor]) -> None:
    fd = src.fileno()
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        data = redactor.feed(chunk) if redactor else chunk
        if data:
            dst.write(data)
            dst.flush()
    if redactor:
        tail = redactor.flush()
        if tail:
            dst.write(tail)
            dst.flush()


def run(p: Plan, resolve: Callable[[str], str], *, cwd: Optional[str] = None,
        base_env: Optional[Dict[str, str]] = None, redact: bool = True,
        timeout: Optional[float] = None, inherit_stdin: bool = False,
        stdout: Optional[BinaryIO] = None, stderr: Optional[BinaryIO] = None,
        python: Optional[str] = None, platform: str = sys.platform,
        askpass_max: int = 3) -> int:
    """Execute ``p``; returns the child's exit code (124 on timeout, 127 if not found)."""
    values = {name: resolve(name) for name in p.secrets}
    env = dict(os.environ if base_env is None else base_env)
    stdout = stdout or sys.stdout.buffer
    stderr = stderr or sys.stderr.buffer
    tmp = Path(tempfile.mkdtemp(prefix="cuttle-sv-"))
    try:
        os.chmod(tmp, 0o700)
        for var, name in p.env_names.items():
            env[var] = values[name]
        for i, (var, name) in enumerate(p.file_names.items()):
            fpath = tmp / f"secret-{i}"
            fd = os.open(fpath, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(values[name])
            env[var] = str(fpath)
        if p.askpass:
            wrapper = str(_write_askpass_wrapper(tmp, python or sys.executable, platform))
            env["CUTTLE_ASKPASS_RULES"] = json.dumps(
                [{"match": r["match"], "var": r["var"]} for r in p.askpass])
            env["CUTTLE_ASKPASS_STATE"] = str(tmp)
            env["CUTTLE_ASKPASS_MAX"] = str(int(askpass_max))
            for r in p.askpass:
                env[r["var"]] = values[r["secret"]]
            env.update({"SSH_ASKPASS": wrapper, "SSH_ASKPASS_REQUIRE": "force",
                        "SUDO_ASKPASS": wrapper, "GIT_ASKPASS": wrapper,
                        "GIT_TERMINAL_PROMPT": "0"})
            if platform != "win32":
                env.setdefault("DISPLAY", ":0")
        redactor_out = Redactor(values) if redact else None
        redactor_err = Redactor(values) if redact else None
        if p.stdin_secret:
            stdin_arg = subprocess.PIPE
        else:
            stdin_arg = None if inherit_stdin else subprocess.DEVNULL
        exe = shutil.which(p.argv[0], path=env.get("PATH")) or p.argv[0]
        try:
            proc = subprocess.Popen([exe] + p.argv[1:], cwd=cwd, env=env, stdin=stdin_arg,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except FileNotFoundError:
            stderr.write(f"cuttle secrets: command not found: {p.argv[0]}\n".encode())
            return 127
        pumps = [threading.Thread(target=_pump, args=(proc.stdout, stdout, redactor_out), daemon=True),
                 threading.Thread(target=_pump, args=(proc.stderr, stderr, redactor_err), daemon=True)]
        for t in pumps:
            t.start()
        if p.stdin_secret and proc.stdin:
            try:
                proc.stdin.write((values[p.stdin_secret] + "\n").encode("utf-8"))
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            code = 124
        except KeyboardInterrupt:
            proc.terminate()
            proc.wait()
            code = 130
        for t in pumps:
            t.join(timeout=5)
        return code
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
