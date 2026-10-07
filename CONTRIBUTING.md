# Contributing to Cuttle

Thanks for helping. Bug reports, fixes, docs and new agent adapters are all
welcome. Using coding agents to contribute is welcome too; they are what
Cuttle is for.

## Before you start

- **Bugs and features:** open an issue first for anything larger than a small
  fix, so we can agree on the approach before you write it.
- **Security problems:** do not open a public issue; see [SECURITY.md](SECURITY.md).

## Development setup

Follow [Quick start](README.md#quick-start) to get a working `.venv`, then
install the development dependencies and run the tests.

POSIX:

```bash
.venv/bin/python -m pip install -r src/requirements/requirements.txt -r src/requirements/requirements-dev.txt
.venv/bin/python -m pytest src/tests/
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r src/requirements/requirements.txt -r src/requirements/requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest src/tests/
```

Install Node to run the frontend tests; without it they skip. The
[Quality workflow](.github/workflows/quality.yml) runs the suite on Linux and
Windows, plus browser and Android build jobs, on every push and pull request.

Never run a second Cuttle against your real data while developing risky
runtime changes; read
[development instance safety](docs/architecture/development-instance-safety.md).

## Where code goes

Every area has one owner, and tests enforce that. Read these before editing,
in order:

1. [`AGENTS.md`](AGENTS.md): the brief for humans and agents, including the
   ownership table
2. [`docs/architecture/ARCHITECTURE_PRINCIPLES.md`](docs/architecture/ARCHITECTURE_PRINCIPLES.md)
3. [`docs/architecture/repository-map.md`](docs/architecture/repository-map.md)
   for the slice you are changing
4. [`docs/architecture/extension-boundaries.md`](docs/architecture/extension-boundaries.md)
   to decide whether something is a project command, action, adapter or core change

Some rules that trip up first contributions:

- Owned modules (`agent_harness`, `agent_router`, `chat_coordinator`, …) never
  import `web_chat_api.py`. `src/tests/quality/test_architecture_boundaries.py` fails if
  they do.
- New settings routes go in `src/api/settings_routes.py`, not `web_chat_api.py`.
- New experimental features are one `FlagSpec` row in
  `src/api/experimental/features.py`.
- New vendor CLIs are adapters under `src/api/agent_harness/agents/<id>/`.
  Cuttle discovers CLIs; it never installs them.
- Runtime state and secrets live in the per-user Cuttle home (`<home>/secrets/`,
  `<home>/.env`), never in the repository; see `docs/architecture/cuttle-home.md`
  or the repo.

## Pull requests

- Keep each PR to one concern, and add or update tests for behavior changes.
- Run the full suite locally. Note in the PR anything you could not test
  (for example Windows, Android, or a vendor CLI you do not have).
- Update docs that the change makes wrong.
- Commit messages follow the existing style: `fix(scope): what changed`,
  `feat(scope): …`, `test: …`, `docs: …`. The body says why.
- New shell scripts must be tracked as executable:
  `git update-index --chmod=+x path/to/script.sh` (a test checks this).
  `.bat`/`.cmd` files check out with CRLF and everything else with LF, per
  `.gitattributes`.
- Never commit a `.env`, databases, certificates, keystores, browser
  profiles or anything under `personal/`.

## License

Cuttle is [MIT-licensed](LICENSE.txt). By contributing, you agree that your
contributions are licensed under the same terms.
