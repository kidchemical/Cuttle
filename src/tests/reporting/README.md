# Optional developer test reporting

All test tooling and generated artifacts live under `src/tests/`.
Normal verification is `.venv/bin/python -m pytest src/tests/` from the repo root.
Generated reports, browser traces, and test-history databases belong in the
ignored `src/tests/results/` directory. HTMLTestReporter defaults to that directory,
regardless of the launch cwd.

`history.py` stores local historical test sessions; `history_api.py` and
`start_history_server.py` are optional legacy analytics on port 5000, independent
of the production daemon/API on port 8080. Start explicitly with:

```bash
PYTHONPATH=src .venv/bin/python -m tests.reporting.history_api
```

`run_with_logging.py` is a legacy manual runner that can contact providers; it is
not part of offline pytest verification. Prefer pytest for current coverage.
The fabricated sample-report generator and sample-history main program were removed.
