"""Local queue ownership, cancellation and fallback policy (no model calls)."""
from types import SimpleNamespace as NS
import threading

import pytest
from core import local_llm as llm


def response(content='', calls=None):
    return NS(choices=[NS(message=NS(content=content, tool_calls=calls))], usage=None)


def test_queue_timeout_and_stop_do_not_release_another_request(monkeypatch):
    lock = threading.Lock()
    monkeypatch.setattr(llm, '_request_lock', lock)
    lock.acquire()
    try:
        with pytest.raises(llm.LocalQueueTimeout):
            with llm.local_request_slot(timeout=0.01):
                pytest.fail('contended slot entered')
        assert lock.locked()
        stopped = []
        with pytest.raises(llm.LocalRequestCancelled):
            with llm.local_request_slot(cancelled=lambda: bool(stopped), on_wait=lambda: stopped.append(True)):
                pytest.fail('cancelled slot entered')
        assert lock.locked()
    finally:
        lock.release()
    with pytest.raises(RuntimeError):
        with llm.local_request_slot() as waited:
            assert not waited
            raise RuntimeError('completion failed')
    assert not lock.locked()


def test_fallback_deduplicates_models_and_accepts_tools(monkeypatch):
    monkeypatch.setattr(llm, 'list_local_models', lambda: ['first', 'second', 'second'])
    seen = []
    def create(**kwargs):
        seen.append(kwargs['model'])
        return response() if kwargs['model'] == 'first' else response(calls=['tool'])
    client = NS(chat=NS(completions=NS(create=create)))
    got = llm.chat_completion_with_fallback(client, {'temperature': 0}, 'first')
    assert got.choices[0].message.tool_calls
    assert seen == ['first', 'second']


def test_stop_during_call_never_retries(monkeypatch):
    monkeypatch.setattr(llm, 'list_local_models', lambda: ['second'])
    stopped = []
    seen = []
    def create(**kwargs):
        seen.append(kwargs['model'])
        stopped.append(True)
        raise RuntimeError('transport failed')
    client = NS(chat=NS(completions=NS(create=create)))
    with pytest.raises(llm.LocalRequestCancelled):
        llm.chat_completion_with_fallback(client, {}, 'first', cancelled=lambda: bool(stopped))
    assert seen == ['first']


@pytest.mark.parametrize('stop', [True, False])
def test_http_queue_cancel_and_timeout_never_start_model(monkeypatch, stop):
    import inspect
    import openai
    from api import web_chat_api as api, chat_delivery
    lock = threading.Lock()
    monkeypatch.setattr(llm, '_request_lock', lock)
    monkeypatch.setenv('LOCAL_LLM_QUEUE_TIMEOUT_SEC', '0.01')
    monkeypatch.setattr(llm, 'resolve_local_model', lambda *a, **k: 'fixture')
    monkeypatch.setattr(api, '_offer_local_llm_launch_if_needed', lambda *a: None)
    monkeypatch.setattr(api, '_get_combined_openai_tools', lambda *a: None)
    stopped = []
    monkeypatch.setattr(chat_delivery, 'current_turn', lambda sid: 1)
    monkeypatch.setattr(chat_delivery, 'is_stale_turn', lambda *a: False)
    monkeypatch.setattr(chat_delivery, 'is_turn_cancelled', lambda sid: bool(stopped))
    monkeypatch.setattr(api, 'emit_chat_status', lambda *a: stopped.append(True) if stop else None)
    calls = []
    monkeypatch.setattr(openai, 'OpenAI', lambda **kw: NS(chat=NS(completions=NS(create=lambda **kw: calls.append(kw)))))
    lock.acquire()
    try:
        with api.app.test_request_context('/api/llm-request', method='POST', json={
            'nodeType': 'llm-local', 'model': 'fixture', 'prompt': 'hello', 'sessionId': 42,
        }):
            reply, code = inspect.unwrap(api.llm_request)()
        assert code == (409 if stop else 503)
        assert not reply.get_json()['success']
        assert calls == []
        assert lock.locked()
    finally:
        lock.release()


@pytest.mark.parametrize('timeout', ['nan', 'inf', 0, -1])
def test_queue_cannot_be_configured_to_wait_indefinitely(timeout):
    with pytest.raises(ValueError):
        with llm.local_request_slot(timeout=timeout):
            pytest.fail('invalid timeout accepted')
