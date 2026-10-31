"""Secret and backend name rules shared by every surface."""

from __future__ import annotations

import re

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ENV_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def valid_name(name: object) -> bool:
    return isinstance(name, str) and bool(NAME_RE.match(name))


def require_name(name: object, what: str = "secret") -> str:
    if not valid_name(name):
        raise ValueError(
            f"Invalid {what} name {name!r}: use 1-64 letters, digits, '.', '_' or '-'"
            " (starting with a letter or digit).")
    return str(name)


def require_env_var(var: object) -> str:
    if not isinstance(var, str) or not ENV_VAR_RE.match(var):
        raise ValueError(f"Invalid environment variable name {var!r}.")
    return var
