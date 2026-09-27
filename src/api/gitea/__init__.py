"""Agent Gitea issue CLI (`python -m api.gitea`).

Library helpers live in ``api.gitea_client``; this package is the argparse front.
"""

from api.gitea.cli import main

__all__ = ["main"]
