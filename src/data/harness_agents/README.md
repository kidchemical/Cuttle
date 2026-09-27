# Drop-in harness agents

Place third-party / experimental agent packs here (or set `CUTTLE_AGENTS_DIR`).

Each folder must look like a bundled agent:

```
harness_agents/<id>/
  manifest.yaml
  adapter.py      # exports build_adapter() or Adapter
```

Same contract as `src/api/agent_harness/agents/` — see
`src/api/agent_harness/ADDING_AN_AGENT.md`. Bundled ids cannot be overridden from here.

Project-scoped packs go in `{project}/.cuttle/agents/<id>/` instead.

Drop-ins may provide an `install_hint`, but Cuttle deliberately ignores executable installer
metadata from non-bundled folders. Install third-party CLIs separately after reviewing them.
