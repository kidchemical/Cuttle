"""Git credential helper: username/password from env. Prints nothing else."""

from __future__ import annotations

import os
import sys


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action != "get":
        return
    user = (os.environ.get("GIT_ASKPASS_USER") or "git").strip() or "git"
    password = os.environ.get("GIT_ASKPASS_PASSWORD") or ""
    sys.stdout.write(f"username={user}\npassword={password}\n")


if __name__ == "__main__":
    main()
