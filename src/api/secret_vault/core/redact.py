"""Streaming redaction of secret values from child-process output.

Works on bytes so binary output passes through untouched. A tail of
``longest_pattern - 1`` bytes is held back between chunks so a value split
across two reads is still caught.
"""

from __future__ import annotations

import base64
import urllib.parse
from typing import Dict, Iterable, List, Tuple

MIN_REDACT_LEN = 4


def _variants(value: str) -> Iterable[str]:
    yield value
    quoted = urllib.parse.quote(value, safe="")
    if quoted != value:
        yield quoted
    if len(value) >= 8:
        yield base64.b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


class Redactor:
    """Replace every known value (and URL/base64 forms) with ``[secret:NAME]``."""

    def __init__(self, secrets: Dict[str, str], min_len: int = MIN_REDACT_LEN) -> None:
        pats: List[Tuple[bytes, bytes]] = []
        seen = set()
        for name, value in secrets.items():
            if not value or len(value) < min_len:
                continue
            label = f"[secret:{name}]".encode("utf-8")
            for variant in _variants(value):
                raw = variant.encode("utf-8")
                if raw and raw not in seen:
                    seen.add(raw)
                    pats.append((raw, label))
        # Longest first so a value containing another value wins.
        pats.sort(key=lambda p: len(p[0]), reverse=True)
        self._patterns = pats
        self._hold = max((len(p) for p, _ in pats), default=1) - 1
        self._buf = b""
        self.skipped = [n for n, v in secrets.items() if v and len(v) < min_len]

    def _replace(self, data: bytes) -> bytes:
        for raw, label in self._patterns:
            if raw in data:
                data = data.replace(raw, label)
        return data

    def feed(self, chunk: bytes) -> bytes:
        if not self._patterns:
            return chunk
        data = self._replace(self._buf + chunk)
        if self._hold <= 0:
            self._buf = b""
            return data
        # Keep a tail that could be the start of a pattern.
        cut = max(0, len(data) - self._hold)
        out, self._buf = data[:cut], data[cut:]
        return out

    def flush(self) -> bytes:
        data, self._buf = self._replace(self._buf), b""
        return data

    def text(self, text: str) -> str:
        out = self.feed(text.encode("utf-8")) + self.flush()
        return out.decode("utf-8", "replace")
