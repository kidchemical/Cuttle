"""
Gitea REST API client for Cuttle issue triage.

Auth (first match):
  - GITEA_TOKEN in environ / src/.env  (preferred — API token from Gitea UI)
  - GITEA_USERNAME + GITEA_PASSWORD    (basic auth fallback)

Base URL:
  - GITEA_BASE_URL (default http://127.0.0.1:3000)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore


def _read_env_file() -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        env_path = Path(__file__).resolve().parents[1] / ".env"
        if not env_path.is_file():
            return out
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            out[key.strip()] = val.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def load_gitea_config() -> Dict[str, str]:
    """Return base_url, token, username, password (empty strings when unset)."""
    file_env = _read_env_file()
    def _get(key: str) -> str:
        return (os.getenv(key) or file_env.get(key) or "").strip()

    base = _get("GITEA_BASE_URL") or "http://127.0.0.1:3000"
    return {
        "base_url": base.rstrip("/"),
        "token": _get("GITEA_TOKEN"),
        "username": _get("GITEA_USERNAME"),
        "password": _get("GITEA_PASSWORD"),
        "agent_username": _get("GITEA_AGENT_USERNAME") or "cuttle",
    }


def gitea_auth_headers() -> Tuple[Dict[str, str], Optional[Tuple[str, str]]]:
    """Headers + optional requests basic-auth tuple."""
    cfg = load_gitea_config()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if cfg["token"]:
        headers["Authorization"] = f"token {cfg['token']}"
        return headers, None
    if cfg["username"] and cfg["password"]:
        return headers, (cfg["username"], cfg["password"])
    return headers, None


def _api_root() -> str:
    return f"{load_gitea_config()['base_url']}/api/v1"


def _request(
    method: str,
    path: str,
    *,
    json_body: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Tuple[int, Any, str]:
    if requests is None:
        return 0, None, "requests package not installed"
    headers, auth = gitea_auth_headers()
    if not headers.get("Authorization") and not auth:
        return 0, None, (
            "Gitea credentials missing. Set GITEA_TOKEN (preferred) or "
            "GITEA_USERNAME + GITEA_PASSWORD in src/.env"
        )
    url = f"{_api_root()}{path}"
    try:
        r = requests.request(
            method,
            url,
            headers=headers,
            auth=auth,
            json=json_body,
            params=params,
            timeout=timeout,
        )
    except Exception as e:
        return 0, None, str(e)
    try:
        data = r.json() if r.content else None
    except Exception:
        data = r.text
    if r.ok:
        return r.status_code, data, ""
    err = ""
    if isinstance(data, dict):
        err = str(data.get("message") or data.get("error") or "")
    if not err:
        err = (r.text or "")[:400]
    return r.status_code, data, err or f"HTTP {r.status_code}"


def parse_owner_repo(spec: str) -> Tuple[str, str]:
    """``owner/repo`` or alias resolved elsewhere."""
    text = str(spec or "").strip().strip("/")
    if "/" not in text:
        raise ValueError(f"Expected owner/repo, got `{spec}`")
    owner, repo = text.split("/", 1)
    return owner.strip(), repo.strip()


def list_issues(
    owner: str,
    repo: str,
    *,
    state: str = "open",
    labels: Optional[List[str]] = None,
    limit: int = 30,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {"state": state, "limit": limit, "type": "issues"}
    if labels:
        params["labels"] = ",".join(labels)
    code, data, err = _request(
        "GET",
        f"/repos/{quote(owner)}/{quote(repo)}/issues",
        params=params,
    )
    if code == 0 or not (200 <= code < 300):
        raise RuntimeError(err or f"list_issues failed ({code})")
    return list(data or [])


def get_issue(owner: str, repo: str, index: int) -> Dict[str, Any]:
    code, data, err = _request(
        "GET",
        f"/repos/{quote(owner)}/{quote(repo)}/issues/{index}",
    )
    if code == 0 or not (200 <= code < 300):
        raise RuntimeError(err or f"get_issue failed ({code})")
    return dict(data or {})


def list_issue_comments(owner: str, repo: str, index: int) -> List[Dict[str, Any]]:
    code, data, err = _request(
        "GET",
        f"/repos/{quote(owner)}/{quote(repo)}/issues/{index}/comments",
    )
    if code == 0 or not (200 <= code < 300):
        raise RuntimeError(err or f"list_issue_comments failed ({code})")
    return list(data or [])


def add_issue_comment(owner: str, repo: str, index: int, body: str) -> Dict[str, Any]:
    code, data, err = _request(
        "POST",
        f"/repos/{quote(owner)}/{quote(repo)}/issues/{index}/comments",
        json_body={"body": body},
    )
    if code == 0 or not (200 <= code < 300):
        raise RuntimeError(err or f"add_issue_comment failed ({code})")
    return dict(data or {})


def replace_issue_labels(
    owner: str,
    repo: str,
    index: int,
    labels: List[str],
) -> List[Dict[str, Any]]:
    code, data, err = _request(
        "PUT",
        f"/repos/{quote(owner)}/{quote(repo)}/issues/{index}/labels",
        json_body={"labels": labels},
    )
    if code == 0 or not (200 <= code < 300):
        raise RuntimeError(err or f"replace_issue_labels failed ({code})")
    return list(data or [])


def add_issue_labels(
    owner: str,
    repo: str,
    index: int,
    labels: List[str],
) -> List[Dict[str, Any]]:
    code, data, err = _request(
        "POST",
        f"/repos/{quote(owner)}/{quote(repo)}/issues/{index}/labels",
        json_body={"labels": labels},
    )
    if code == 0 or not (200 <= code < 300):
        raise RuntimeError(err or f"add_issue_labels failed ({code})")
    return list(data or [])


def delete_issue_label(
    owner: str,
    repo: str,
    index: int,
    label_id: int,
) -> None:
    code, _, err = _request(
        "DELETE",
        f"/repos/{quote(owner)}/{quote(repo)}/issues/{index}/labels/{label_id}",
    )
    if code == 0 or not (200 <= code < 300):
        raise RuntimeError(err or f"delete_issue_label failed ({code})")


def default_agent_username() -> str:
    """Gitea login for the Cuttle bot account (issue assignee)."""
    return load_gitea_config()["agent_username"]


def patch_issue(
    owner: str,
    repo: str,
    index: int,
    *,
    state: Optional[str] = None,
    assignees: Optional[List[str]] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {}
    if state is not None:
        body["state"] = state
    if assignees is not None:
        body["assignees"] = assignees
    if not body:
        raise ValueError("patch_issue requires at least one field to update")
    code, data, err = _request(
        "PATCH",
        f"/repos/{quote(owner)}/{quote(repo)}/issues/{index}",
        json_body=body,
    )
    if code == 0 or not (200 <= code < 300):
        raise RuntimeError(err or f"patch_issue failed ({code})")
    return dict(data or {})


def create_repo_label(
    owner: str,
    repo: str,
    name: str,
    *,
    color: str = "ededed",
    description: str = "",
) -> Dict[str, Any]:
    code, data, err = _request(
        "POST",
        f"/repos/{quote(owner)}/{quote(repo)}/labels",
        json_body={"name": name, "color": color.lstrip("#"), "description": description},
    )
    if code == 0 or not (200 <= code < 300):
        raise RuntimeError(err or f"create_repo_label failed ({code})")
    return dict(data or {})


def list_repo_labels(owner: str, repo: str) -> List[Dict[str, Any]]:
    code, data, err = _request(
        "GET",
        f"/repos/{quote(owner)}/{quote(repo)}/labels",
    )
    if code == 0 or not (200 <= code < 300):
        raise RuntimeError(err or f"list_repo_labels failed ({code})")
    return list(data or [])


def issue_web_url(owner: str, repo: str, index: int) -> str:
    base = load_gitea_config()["base_url"]
    return f"{base}/{owner}/{repo}/issues/{index}"


def commit_web_url(owner: str, repo: str, sha: str) -> str:
    base = load_gitea_config()["base_url"]
    return f"{base}/{owner}/{repo}/commit/{sha}"


def pull_web_url(owner: str, repo: str, index: int) -> str:
    base = load_gitea_config()["base_url"]
    return f"{base}/{owner}/{repo}/pulls/{index}"


def list_pulls(
    owner: str,
    repo: str,
    *,
    state: str = "open",
    limit: int = 50,
) -> List[Dict[str, Any]]:
    code, data, err = _request(
        "GET",
        f"/repos/{quote(owner)}/{quote(repo)}/pulls",
        params={"state": state, "limit": limit},
    )
    if code == 0 or not (200 <= code < 300):
        raise RuntimeError(err or f"list_pulls failed ({code})")
    return list(data or [])


def create_pull(
    owner: str,
    repo: str,
    *,
    title: str,
    body: str,
    head: str,
    base: str,
) -> Dict[str, Any]:
    code, data, err = _request(
        "POST",
        f"/repos/{quote(owner)}/{quote(repo)}/pulls",
        json_body={
            "title": title,
            "body": body or "",
            "head": head,
            "base": base,
        },
    )
    if code == 0 or not (200 <= code < 300):
        raise RuntimeError(err or f"create_pull failed ({code})")
    return dict(data or {})


def find_open_pull_for_head(
    owner: str,
    repo: str,
    head_branch: str,
) -> Optional[Dict[str, Any]]:
    """Find an open PR whose head ref matches ``head_branch`` (with or without owner prefix)."""
    head_branch = head_branch.strip()
    candidates = {head_branch, f"{owner}:{head_branch}"}
    for pr in list_pulls(owner, repo, state="open", limit=100):
        head = pr.get("head") or {}
        ref = str(head.get("ref") or "").strip()
        label = str(head.get("label") or "").strip()
        if ref in candidates or label in candidates or ref == head_branch:
            return pr
        # Some Gitea builds nest repo name on head.label as owner:branch
        if label.endswith(f":{head_branch}"):
            return pr
    return None
