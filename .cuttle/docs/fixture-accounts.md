# Browser QA and promo accounts

`api.fixture_accounts` owns local maintenance for two reserved guest identities:
`cuttle_test` (browser QA) and `cuttle_demo` (README / website captures). They are
passwordless, non-owner accounts. The local capture helper issues a two-hour
cookie, checks `/api/auth/me` against the local database, and revokes the token
on exit, including failed captures. There is no public fixture login endpoint.

```bash
PYTHONPATH=src .venv/bin/python -m api.fixture_accounts ensure test
PYTHONPATH=src .venv/bin/python -m api.fixture_accounts ensure demo
PYTHONPATH=src .venv/bin/python -m api.fixture_accounts audit-layout
PYTHONPATH=src .venv/bin/python -m api.fixture_accounts retire-layout --ids 81,82
PYTHONPATH=src .venv/bin/python -m api.fixture_accounts restore-layout 81
```

Audit before retiring exact ids. Retirement only disables empty local accounts
whose names match the old `lo_` / `lp_` layout tests; linked identities, accounts
with messages/widgets/subagent batches, and the oldest active account are
protected. Identity/content are checked again inside the write transaction.
User/chat records remain for recovery; login tokens are revoked. Restore
reactivates the account without reviving old tokens.

The README screenshot, GIF, and promo-video utilities share
`fixture_browser_auth(ctx, base, 'demo', db=db)`, so repeated runs reuse the
demo identity. Existing `--keep` still retains capture chats; normal captures
soft-delete only their own seeded chats. Older anonymous guests are not safe
to identify as promo leftovers, so maintenance does not retire them by name.

Unit/layout tests use separate temporary databases through the autouse fixture
in `src/tests/conftest.py`. Never redirect those tests to the live fixture
accounts. The isolated Chromium terminal-activity test also mocks its API and
uses no real account. A running-server QA capture may use `cuttle_test` through
the same local auth helper, seed only fake content, and clean up its own chats.
