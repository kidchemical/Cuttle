"""Local fixture account maintenance; never an HTTP login bypass.

One guest identity per purpose for browser QA and promo capture. Unit tests
continue to use independent temporary databases, not these live identities.
"""
from __future__ import annotations

import argparse
import json
from contextlib import contextmanager
from urllib.parse import urlparse

from api.auth_db import get_auth_db

KINDS = {'test': 'Cuttle Test', 'demo': 'Cuttle Demo'}


def ensure_fixture_account(db, kind: str) -> int:
    """Reuse a reserved, passwordless, non-owner guest fixture account."""
    label = KINDS[kind]
    name = 'cuttle_' + kind
    email = name + '@fixtures.local'
    user = db.get_user_by_username(name)
    if user is None:
        uid = db.create_user(email, label, 'guest', username=name)
        user = db.get_user_by_id(uid) if uid else db.get_user_by_username(name)
    if (not user or user['email'] != email or user['auth_provider'] != 'guest'
            or user.get('password_hash') or user.get('provider_user_id')
            or not user.get('is_active')):
        raise ValueError(f'Reserved fixture identity {name} is occupied or inactive; refusing to reuse it')
    return int(user['id'])


@contextmanager
def fixture_browser_auth(ctx, base: str, kind: str = 'demo', *, db=None):
    """Authenticate local capture/QA contexts; revoke the short-lived token on exit.

    The running server must use this same database. No registration request,
    saved password, public fixture-login endpoint, or owner privilege is added.
    """
    parsed = urlparse(base)
    if parsed.scheme not in ('http', 'https') or parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('Fixture browser auth is local-only')
    db = db or get_auth_db()
    uid = ensure_fixture_account(db, kind)
    token = db.create_auth_session(uid, expires_hours=2)
    try:
        ctx.add_cookies([{'name': 'session_token', 'value': token, 'url': base,
                         'httpOnly': True, 'secure': parsed.scheme == 'https', 'sameSite': 'Lax'}])
        resp = ctx.request.get(base.rstrip('/') + '/api/auth/me')
        if not resp.ok or resp.json().get('user', {}).get('id') != uid:
            raise ValueError('Fixture login failed: server must use the local capture database')
        yield uid
    finally:
        db.delete_auth_session(token)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='verb', required=True)
    sub.add_parser('audit-layout')
    ensure = sub.add_parser('ensure')
    ensure.add_argument('kind', choices=KINDS)
    retire = sub.add_parser('retire-layout')
    retire.add_argument('--ids', required=True, help='Comma-separated audited account ids')
    restore = sub.add_parser('restore-layout')
    restore.add_argument('id', type=int)
    args = parser.parse_args(argv)
    db = get_auth_db()
    if args.verb == 'audit-layout':
        result = db.layout_test_accounts()
    elif args.verb == 'ensure':
        result = {'kind': args.kind, 'user_id': ensure_fixture_account(db, args.kind)}
    elif args.verb == 'retire-layout':
        result = {'retired_ids': db.retire_empty_layout_test_accounts(args.ids.split(','))}
    else:
        result = {'restored': db.restore_layout_test_account(args.id)}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
