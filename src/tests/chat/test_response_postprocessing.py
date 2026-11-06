"""Final-response passes use fake executors and private settings only."""
from types import SimpleNamespace

import pytest

from api import background_helpers as bg, response_postprocessing as pp
from api import chat_preferences as cp
from api.model_providers.types import TextResult


@pytest.fixture
def settings(monkeypatch, tmp_path):
    from managers import settings_manager as sm
    private = sm.SettingsManager(str(tmp_path / 'settings.json'))
    monkeypatch.setattr(sm, '_settings_manager', private)
    private.set_setting('response_postprocessing', {'enabled': True, 'prompt': 'Give me the TLDR', 'mode': 'append', 'chain': [{'kind': 'api', 'target': 'fake'}]})
    private.set_setting('experimental_flags', {'response_postprocessing': True})
    monkeypatch.delenv('CUTTLE_EXPERIMENTAL', raising=False)
    private.set_setting('background_helpers', {'chain': [{'kind': 'api', 'target': 'fake'}], 'tasks': {}})
    monkeypatch.setattr(bg, 'generate', lambda *a, **k: pytest.fail('Unexpected executor call'))
    return private


@pytest.mark.parametrize('mode', ['append', 'replace'])
def test_success_passes_config_and_preserves_original(settings, monkeypatch, mode):
    settings.set_setting('response_postprocessing', {**cp.processor_preferences(), 'mode': mode})
    seen = []
    def generate(task, request, **kwargs):
        seen.append((task, request, kwargs))
        return TextResult('Short answer', {'output_tokens': 8})
    monkeypatch.setattr(bg, 'generate', generate)
    body = {'success': True, 'response': 'Long answer'}
    pp.process_result(body)
    assert body['response'] == ('<cuttle_original_response>"Long answer"</cuttle_original_response>\n\nShort answer' if mode == 'append' else 'Short answer')
    assert body['postprocessing']['original_response'] == 'Long answer'
    assert seen[0][0] == 'postprocessing'
    assert seen[0][1].user == 'Long answer'
    assert 'Give me the TLDR' in seen[0][1].system
    assert seen[0][2]['chain'] == cp.processor_preferences()['chain']
    pp.process_result(body)
    assert len(seen) == 1  # same terminal body cannot be processed twice


@pytest.mark.parametrize('extra', [
    {'response': 'ready'},  # short replies are processed too
    {'success': False, 'response': 'The build failed: missing dep'},
    {'response': 'Prose <cuttle_action_form>{"unclosed": true}'},
    {'response': 'x' * (pp.MAX_RESPONSE_CHARS + 1)},
])
def test_short_failed_unpaired_and_long_replies_are_processed(settings, monkeypatch, extra):
    seen = []
    monkeypatch.setattr(bg, 'generate', lambda t, req, **kw: seen.append(req.user) or TextResult('Done'))
    body = {'success': True, **extra}
    original = body['response']
    pp.process_result(body)
    assert body['postprocessing']['status'] == 'completed'
    assert body['postprocessing']['original_response'] == original
    assert len(seen[0]) <= pp.MAX_RESPONSE_CHARS + 60
    if '<cuttle_action_form' in original:
        # The stray card markup is never shown to the model and rides through verbatim.
        assert seen == ['Prose']
        assert body['response'].endswith('Done\n\n<cuttle_action_form>{"unclosed": true}')


@pytest.mark.parametrize('mode', ['append', 'replace'])
def test_leading_thinking_stays_first_and_is_not_processed(settings, monkeypatch, mode):
    settings.set_setting('response_postprocessing', {**cp.processor_preferences(), 'mode': mode})
    seen = []
    monkeypatch.setattr(bg, 'generate', lambda t, req, **kw: seen.append(req.user) or TextResult('TLDR'))
    body = {'success': True, 'response': '<think>\nThe real answer\n</think>\n\nClosing line'}
    pp.process_result(body)
    assert seen == ['Closing line']
    assert body['response'].startswith('<think>\nThe real answer\n</think>\n\n')
    rest = body['response'].split('</think>\n\n', 1)[1]
    assert rest == ('<cuttle_original_response>"Closing line"</cuttle_original_response>\n\nTLDR'
                    if mode == 'append' else 'TLDR')


@pytest.mark.parametrize('disable', ['flag', 'kill_switch', 'preference', 'no_executor'])
def test_disabled_never_executes(settings, monkeypatch, disable):
    if disable == 'flag': settings.set_setting('experimental_flags', {})
    elif disable == 'kill_switch': monkeypatch.setenv('CUTTLE_EXPERIMENTAL', '0')
    elif disable == 'preference': cp.save_processor({'enabled': False, 'prompt': 'TLDR', 'mode': 'append'})
    else: settings.set_setting('response_postprocessing', {**cp.processor_preferences(), 'chain': []})
    body = {'success': True, 'response': 'Original'}
    pp.process_result(body)
    assert body['response'] == 'Original'


@pytest.mark.parametrize('extra', [
    {'response': ''}, {'response': '[CANCELLED] stopped'},
    {'ui': 'system'}, {'skip_history_persist': True}, {'coordinator_response_message_id': 8},
    {'response': '<cuttle_action_form>{}</cuttle_action_form>'},
])
def test_non_chat_and_interactive_outputs_never_execute(settings, extra):
    body = {'success': True, 'response': 'Original', **extra}
    original = body['response']
    pp.process_result(body)
    assert body['response'] == original


@pytest.mark.parametrize('mode', ['append', 'replace'])
def test_interactive_cards_ride_through_after_processed_prose(settings, monkeypatch, mode):
    settings.set_setting('response_postprocessing', {**cp.processor_preferences(), 'mode': mode})
    form = '<cuttle_action_form>{"id": "f1", "options": [{"label": "a > b"}]}</cuttle_action_form>'
    confirm = '<cuttle_confirm action="git.push" />'
    seen = []
    def generate(task, request, **kw):
        seen.append(request.user)
        return TextResult('TLDR')
    monkeypatch.setattr(bg, 'generate', generate)
    body = {'success': True, 'response': 'Long answer\n\n' + form + '\n\nMore detail\n' + confirm}
    original = body['response']
    pp.process_result(body)
    assert seen == ['Long answer\n\nMore detail']
    assert body['response'].endswith('TLDR\n\n' + form + '\n\n' + confirm)
    if mode == 'append':
        assert body['response'].startswith('<cuttle_original_response>"Long answer\\n\\nMore detail"')
    assert body['postprocessing']['status'] == 'completed'
    assert body['postprocessing']['original_response'] == original


@pytest.mark.parametrize('outcome', [None, TextResult(''), TextResult('<cuttle_confirm action="x">'), RuntimeError('secret')])
def test_failure_keeps_original(settings, monkeypatch, outcome):
    def generate(*a, **kw):
        if isinstance(outcome, Exception): raise outcome
        return outcome
    monkeypatch.setattr(bg, 'generate', generate)
    body = {'success': True, 'response': 'Original'}
    pp.process_result(body)
    assert body['response'] == 'Original'
    assert body['postprocessing']['status'] == 'failed'
    assert 'secret' not in str(body)


def test_cancellation_before_and_during_pass(settings, monkeypatch):
    body = {'success': True, 'response': 'Original'}
    pp.process_result(body, cancelled=lambda: True)
    cancelled = [False]
    def generate(*args, **kw):
        cancelled[0] = True
        assert kw['cancel_event'].is_set()
        return TextResult('Too late')
    monkeypatch.setattr(bg, 'generate', generate)
    pp.process_result(body, cancelled=lambda: cancelled[0])
    assert body == {'success': True, 'response': 'Original'}


def test_independent_chain_is_used_instead_of_background_configuration(settings, monkeypatch):
    chain = [{'kind': 'cli', 'target': 'fake', 'model': 'chosen', 'effort': 'high'}]
    settings.set_setting('background_helpers', {'chain': [{'kind': 'api', 'target': 'other'}], 'tasks': {'postprocessing': chain}})
    settings.set_setting('response_postprocessing', {**cp.processor_preferences(), 'chain': chain})
    def generate(task, request, **kw):
        assert kw['chain'] == chain
        return TextResult('Summary')
    monkeypatch.setattr(bg, 'generate', generate)
    body = {'success': True, 'response': 'Original'}
    pp.process_result(body)
    assert body['postprocessing']['status'] == 'completed'


@pytest.mark.parametrize('stream', [False, True])
def test_workflow_delivers_and_saves_processed_reply(settings, monkeypatch, stream):
    from api import chat_delivery as delivery, chat_turn_workflow as workflow
    from api.chat_turn_persist import make_assistant_saver
    monkeypatch.setattr(bg, 'generate', lambda *a, **kw: TextResult('Summary'))
    saved, notified = [], []
    sid = 'processor-workflow-' + str(stream)
    saver = make_assistant_saver(chat_session_id=sid,
        db=SimpleNamespace(add_message=lambda *a, **kw: saved.append((a, kw))),
        resolve_project=lambda d: '', assistant_meta_fn=lambda r: {},
        project_merge_fn=lambda meta, *a: meta, schedule_autoname=lambda sid: None, project_root='')
    run = lambda *a: {'success': True, 'response': 'Original'}
    if stream:
        events = list(workflow.run_agent_stream_turn(sid, delivery=delivery,
            persist_user=lambda: None, run=run, make_saver=lambda: saver,
            notify_mobile=notified.append))
        result = events[-1][1]
        assert events[-1][0] == 'done'
        assert delivery.take_result(sid)['response'] == result['response']
    else:
        def after(result):
            saver(result)
            notified.append(result)
        result, status = workflow.run_agent_sync_turn(sid, delivery=delivery,
            persist_user=lambda: None, run=run, after_run=after)
        assert status == 200
    assert saved[0][0][2] == result['response'] == notified[0]['response']
    assert result['response'].endswith('Summary')
    assert saved[0][1]['metadata']['postprocessing']['original_response'] == 'Original'


@pytest.mark.parametrize('stream', [False, True])
def test_stop_during_processing_cannot_save_or_notify(settings, monkeypatch, stream):
    from api import chat_delivery as delivery, chat_turn_workflow as workflow
    sid = 'processor-cancel-' + str(stream)
    saved = []
    def generate(*a, **kw):
        delivery.cancel_current_turn(sid)
        assert kw['cancel_event'].is_set()
        return TextResult('Late')
    monkeypatch.setattr(bg, 'generate', generate)
    if stream:
        list(workflow.run_agent_stream_turn(sid, delivery=delivery, persist_user=lambda: None,
            run=lambda q: {'success': True, 'response': 'Original'},
            make_saver=lambda: saved.append, notify_mobile=saved.append))
        assert delivery.take_result(sid) is None
    else:
        workflow.run_agent_sync_turn(sid, delivery=delivery, persist_user=lambda: None,
            run=lambda: {'success': True, 'response': 'Original'}, after_run=saved.append)
    assert saved == []


@pytest.mark.parametrize('trigger,text,runs', [
    ({'unit': 'words', 'min': 3}, 'one two three', False),
    ({'unit': 'words', 'min': 3}, 'one two three four', True),
    ({'unit': 'chars', 'min': 5}, 'abcde', False),
    ({'unit': 'chars', 'min': 5}, 'abcdef', True),
    # Reasoning and cards do not count toward the trigger.
    ({'unit': 'words', 'min': 2}, '<think>a b c d e</think>hi <cuttle_action_form>{"t": 1}</cuttle_action_form>', False),
])
def test_length_trigger_gates_the_pass(settings, monkeypatch, trigger, text, runs):
    settings.set_setting('response_postprocessing', {**cp.processor_preferences(), 'trigger': trigger})
    seen = []
    monkeypatch.setattr(bg, 'generate', lambda t, req, **kw: seen.append(req.user) or TextResult('TLDR'))
    body = {'success': True, 'response': text}
    pp.process_result(body)
    assert bool(seen) is runs
    if not runs:
        assert body['response'] == text
        assert body['postprocessing'] == {'status': 'skipped', 'reason': 'below_trigger'}
