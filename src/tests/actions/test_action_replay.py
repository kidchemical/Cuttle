"""Concurrent/recovered dispatch and explicit reusable action policy."""
import html
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

from api import project_actions as actions
from api.action_replay import claim, key


def recipe(tmp_path, replay=""):
    folder = tmp_path / '.cuttle' / 'actions'
    folder.mkdir(parents=True)
    (folder / 'test.yaml').write_text('name: test.run\ntype: shell\nrun: echo fake\n' + replay)


def token(tmp_path, **extra):
    return actions.encode_inline_action_payload(action_name='test.run', project_path=str(tmp_path),
                                               params={}, session_id='db_session_1', **extra)


def test_atomic_claim_across_connections():
    receipt = key('parallel', 'test', 'project', 'session')
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(lambda _: claim(receipt), range(16))) == 1


def test_inflight_replay_and_ambiguous_failure_keep_claim(tmp_path):
    recipe(tmp_path)
    signed = token(tmp_path)
    entered, finish = Event(), Event()
    def dispatch(*args, **kwargs):
        entered.set()
        assert finish.wait(3)
        return {'success': False, 'error': 'unknown remote result'}
    with patch.object(actions, '_execute_shell', side_effect=dispatch) as run:
        with ThreadPoolExecutor() as pool:
            first = pool.submit(actions.execute_inline_action, signed, session_id='1')
            assert entered.wait(3)
            assert actions.execute_inline_action(signed, session_id='1')['already_used']
            finish.set()
            assert not first.result()['success']
        # Losing the in-memory registry does not lose the durable receipt.
        actions.clear_pending_for_tests()
        assert actions.execute_inline_action(signed, session_id='1')['already_used']
        assert run.call_count == 1


def test_pending_and_fallback_share_receipt(tmp_path):
    recipe(tmp_path)
    rewritten, _ = actions.rewrite_cuttle_confirms('<cuttle_confirm action="test.run">hi</cuttle_confirm>',
                                                  session_id='1', project_path=str(tmp_path))
    pending = re.search(r'id="([^"]+)"', rewritten).group(1)
    fallback = html.unescape(re.search(r'fallback="([^"]+)"', rewritten).group(1))
    with patch.object(actions, '_execute_shell', return_value={'success': True}) as run:
        assert actions.execute_pending_action(pending, session_id='1')['success']
        assert actions.execute_inline_action(fallback, session_id='1')['already_used']
        assert run.call_count == 1


def test_cancel_and_wrong_session(tmp_path):
    recipe(tmp_path)
    signed = token(tmp_path)
    with patch.object(actions, '_execute_shell') as run:
        assert not actions.execute_inline_action(signed, session_id='2', cancel=True)['success']
        assert actions.execute_inline_action(signed, session_id='1', cancel=True)['success']
        assert actions.execute_inline_action(signed, session_id='1')['already_used']
        run.assert_not_called()


def test_reusable_policy_and_separate_fresh_confirmations(tmp_path):
    recipe(tmp_path, 'replay:\n  default: once\n  modes:\n    status: reusable\n')
    readonly = actions.encode_inline_action_payload(action_name='test.run', project_path=str(tmp_path),
                                                   params={'mode':'status'}, session_id='1')
    with patch.object(actions, '_execute_shell', return_value={'success': True}) as run:
        assert actions.execute_inline_action(readonly, session_id='1')['success']
        assert actions.execute_inline_action(readonly, session_id='1')['success']
        assert actions.execute_inline_action(token(tmp_path), session_id='1')['success']
        assert actions.execute_inline_action(token(tmp_path), session_id='1')['success']
        assert run.call_count == 4


def test_form_receipt_survives_new_synthetic_token(tmp_path):
    from api.action_forms import _run_one
    recipe(tmp_path)
    with patch.object(actions, '_execute_shell', return_value={'success': True}) as run:
        assert _run_one(str(tmp_path), 'test.run', {}, '1', receipt_id='form:stable')['success']
        assert _run_one(str(tmp_path), 'test.run', {'changed':True}, '1', receipt_id='form:stable')['already_used']
        assert run.call_count == 1


def test_reusable_card_cannot_override_once_recipe(tmp_path, monkeypatch):
    from api.action_forms import normalize_action_form_spec, encode_form_fallback, execute_action_form_submission
    recipe(tmp_path)
    spec = normalize_action_form_spec({'id':'replay-test-card', 'mode':'choice', 'reusable':True,
        'lock':'none', 'options':[{'id':'run','label':'Run','action':'test.run'}]}, project_path=str(tmp_path))
    signed = encode_form_fallback(spec)
    monkeypatch.setattr('api.action_forms.read_action_form_lock_from_history', lambda *a: None)
    with patch.object(actions, '_execute_shell', return_value={'success':True}) as run:
        first = execute_action_form_submission(form_token=signed, selection={'option':'run'}, session_id='1')
        second = execute_action_form_submission(form_token=signed, selection={'option':'run'}, session_id='1')
        assert first['success'], first
        assert not second['success'], second
        assert run.call_count == 1
