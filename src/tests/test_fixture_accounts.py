"""Fixture maintenance cannot retire used identities or grant owner access."""
import pytest

from api.auth_db import get_auth_db
from api.fixture_accounts import ensure_fixture_account, fixture_browser_auth
from api.http_authz import is_owner_user


def test_fixture_accounts_are_reused_separate_and_non_owner():
    db = get_auth_db()
    demo = ensure_fixture_account(db, 'demo')
    test = ensure_fixture_account(db, 'test')
    assert demo != test
    assert ensure_fixture_account(db, 'demo') == demo
    assert ensure_fixture_account(db, 'test') == test
    assert not is_owner_user(db.get_user_by_id(demo))
    assert not is_owner_user(db.get_user_by_id(test))


def test_fixture_identity_collision_does_not_reuse_real_account():
    db = get_auth_db()
    db.create_user('real@local', 'Real', 'local', username='cuttle_demo')
    with pytest.raises(ValueError, match='occupied'):
        ensure_fixture_account(db, 'demo')


def test_layout_retirement_rechecks_usage_preserves_owner_and_can_restore():
    db = get_auth_db()
    owner = db.create_user('lo_0000000000@local', 'Oldest', 'local', username='lo_0000000000')
    empty = db.create_user('lo_1111111111@local', 'Empty', 'local', password='password1', username='lo_1111111111')
    used = db.create_user('lp_2222222222@local', 'Used', 'local', username='lp_2222222222')
    linked = db.create_user('lp_3333333333@local', 'Linked', 'local', username='lp_3333333333', provider_user_id='oauth')
    other = db.create_user('normal@local', 'Normal', 'local', username='normal')
    audit = db.layout_test_accounts()
    assert {row['id'] for row in audit} == {owner, empty, used, linked}
    assert all('password_hash' not in row for row in audit)
    # Activity happens after the initial audit; retirement must recheck it.
    sid = db.create_chat_session(used, 'Real work')
    db.add_message(sid, 'user', 'Keep this message')
    token = db.create_auth_session(empty)
    assert db.retire_empty_layout_test_accounts([owner, empty, used, linked, other]) == [empty]
    assert db.verify_auth_session(token) is None
    assert db.verify_password('lo_1111111111', 'password1') is None
    assert db.get_user_by_id(used)['is_active']
    assert db.get_messages(sid)[0]['content'] == 'Keep this message'
    # Even a accidentally issued token cannot authenticate a retired account.
    assert db.verify_auth_session(db.create_auth_session(empty)) is None
    assert db.restore_layout_test_account(empty)
    assert db.verify_password('lo_1111111111', 'password1') == empty
    assert not db.restore_layout_test_account(other)


@pytest.mark.parametrize('failure', [False, True])
def test_fixture_browser_auth_reuses_account_and_revokes_token(failure):
    db = get_auth_db()
    uid = ensure_fixture_account(db, 'demo')
    class Response:
        ok = True
        def json(self):
            return {'user': {'id': uid}}
    class Context:
        request = None
        def __init__(self):
            self.request = self
        def add_cookies(self, cookies):
            self.token = cookies[0]['value']
        def get(self, url):
            assert url == 'http://127.0.0.1:8080/api/auth/me'
            return Response()
    ctx = Context()
    try:
        with fixture_browser_auth(ctx, 'http://127.0.0.1:8080', db=db) as actual:
            assert actual == uid
            assert db.verify_auth_session(ctx.token)['id'] == uid
            if failure:
                raise RuntimeError('capture failed')
    except RuntimeError:
        assert failure
    assert db.verify_auth_session(ctx.token) is None


def test_fixture_browser_auth_rejects_remote_destination():
    with pytest.raises(ValueError, match='local-only'):
        with fixture_browser_auth(None, 'https://example.com'):
            pytest.fail('Remote auth must not start')
