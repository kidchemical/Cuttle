"""Lossless Git porcelain-v1 -z path parsing shared by worktree observers."""
from __future__ import annotations


def parse_status_z(output: str) -> dict[str, str]:
    """Map destination paths to XY status; rename/copy source follows destination.

    Git emits literal paths with -z, including whitespace, newlines and arrows.
    Never strip or unquote a path. Separators are already '/' on every platform.
    """
    records = iter(output.split('\0'))
    paths = {}
    for record in records:
        if not record:
            continue
        if len(record) < 4 or record[2] != ' ':
            raise ValueError('Malformed git porcelain-v1 -z record')
        code, path = record[:2], record[3:]
        paths[path] = code
        if 'R' in code or 'C' in code:
            source = next(records, None)
            if not source:
                raise ValueError('Missing rename/copy source')
    return paths
