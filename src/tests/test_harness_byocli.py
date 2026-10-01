"""Phase 6 P6-A: BYO-CLI boundary — discovery yes, executable installers no.

Manifest-level pins: bundled manifests declare install guidance and carry
no auto-install flags or install recipes. (Module-absence and no-shell-out
pins live in test_agent_harness.py alongside the retired installer tests.)
"""

from __future__ import annotations


def test_bundled_manifests_declare_guidance_not_auto_install():
    from api.agent_harness.catalog import list_agents, get_agent

    for agent_id in list_agents():
        pair = get_agent(agent_id)
        assert pair is not None
        manifest, _adapter = pair
        if manifest.source != "bundled":
            continue
        assert manifest.auto_install is False, agent_id
        if manifest.executable_names:
            assert (manifest.missing_cli_hint or manifest.install_hint), agent_id


def test_bundled_manifests_carry_no_install_recipes():
    from api.agent_harness.catalog import list_agents, get_agent

    for agent_id in list_agents():
        pair = get_agent(agent_id)
        manifest = pair[0]
        if manifest.source != "bundled":
            continue
        assert not manifest.install_kind, agent_id
        assert not manifest.install_package, agent_id
        assert not manifest.install_url_windows, agent_id
        assert not manifest.install_url_posix, agent_id
        assert not manifest.install_sha256_windows, agent_id
        assert not manifest.install_sha256_posix, agent_id
