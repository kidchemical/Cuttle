"""Tests for Gitea PR head matching used by @cuttle solve."""

from __future__ import annotations

from unittest.mock import patch

from api import gitea_client


def test_find_open_pull_for_head_matches_ref():
    pulls = [
        {"number": 7, "head": {"ref": "other", "label": "acme:other"}},
        {"number": 18, "head": {"ref": "cuttle/issue-42", "label": "acme:cuttle/issue-42"}},
    ]
    with patch.object(gitea_client, "list_pulls", return_value=pulls):
        pr = gitea_client.find_open_pull_for_head("acme", "demo-game", "cuttle/issue-42")
    assert pr is not None
    assert pr["number"] == 18


def test_find_open_pull_for_head_none():
    with patch.object(gitea_client, "list_pulls", return_value=[]):
        assert (
            gitea_client.find_open_pull_for_head("acme", "demo-game", "cuttle/issue-99")
            is None
        )
