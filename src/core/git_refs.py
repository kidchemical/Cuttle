"""Validation for literal Git branch names shared by registry and Git operations."""
import subprocess


def validate_branch_name(value, *, allow_empty=False):
    if not isinstance(value, str):
        raise ValueError('Default working branch must be text.')
    name = value.strip()
    if not name and allow_empty:
        return ''
    if not name or name.startswith('-') or name == 'HEAD' or len(name) > 240:
        raise ValueError('Use a valid Git branch name (up to 240 characters).')
    try:
        # Validate a literal ref; --branch would expand checkout shortcuts like @{-1}.
        result = subprocess.run(['git', 'check-ref-format', 'refs/heads/' + name],
                                capture_output=True, timeout=4, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('Git branch validation is unavailable.') from exc
    if result.returncode:
        raise ValueError('Use a valid Git branch name.')
    return name
