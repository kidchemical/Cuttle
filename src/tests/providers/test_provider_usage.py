"""Provider-call ledger: record/read roundtrip plus service-layer hooks."""

from api.model_providers import service, usage
from api.model_providers.types import TextRequest, TextResult


def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTTLE_PROVIDER_CALLS_DB", str(tmp_path / "provider_calls.db"))


def test_record_and_read_roundtrip(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    assert usage.record_call(
        provider="OpenAI", operation="text_generation", model="gpt-4o-mini",
        task="background:titles", success=True, failure_kind="none",
        latency_ms=120.0, usage={"prompt_tokens": 100, "completion_tokens": 20,
                                 "total_tokens": 120}, cost=0.001,
    ) is True
    rows = usage.all_calls()
    assert len(rows) == 1
    row = rows[0]
    assert row["provider"] == "openai"
    assert row["task"] == "background:titles"
    assert row["prompt_tokens"] == 100
    assert row["completion_tokens"] == 20
    assert row["total_tokens"] == 120
    assert row["cost"] == 0.001


def test_record_derives_total_and_rejects_blank_provider(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    assert usage.record_call(provider="typesafe", operation="structured_decision",
                             model="jev-latest", task="jev:label",
                             usage={"input_tokens": 50, "output_tokens": 5}) is True
    assert usage.record_call(provider="", operation="text_generation") is False
    rows = usage.all_calls()
    assert len(rows) == 1
    assert rows[0]["total_tokens"] == 55
    assert rows[0]["cost"] is None


def test_record_failure_row(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    assert usage.record_call(provider="openai", operation="text_generation",
                             model="gpt-4o-mini", success=False,
                             failure_kind="transport") is True
    rows = usage.all_calls()
    assert rows[0]["success"] == 0
    assert rows[0]["failure_kind"] == "transport"


def test_generate_text_records_success_and_failure(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    calls = {"fail": False}

    class FakeAdapter:
        def discovery_identity(self, manifest):
            return "test-identity"

        def generate_text(self, manifest, request, caps):
            if calls["fail"]:
                raise RuntimeError("boom")
            return TextResult("hi", {"prompt_tokens": 10, "completion_tokens": 3,
                                     "total_tokens": 13})

    monkeypatch.setattr(service, "get_adapter", lambda _pid: FakeAdapter())
    out = service.generate_text("openai", TextRequest("hello", model="gpt-4o-mini"),
                                usage_task="background:titles")
    assert out is not None and out.text == "hi"
    try:
        calls["fail"] = True
        service.generate_text("openai", TextRequest("hello", model="gpt-4o-mini"))
        assert False, "expected the adapter error to propagate"
    except RuntimeError:
        pass
    rows = usage.all_calls()
    assert len(rows) == 2
    ok, failed = rows[1], rows[0]  # newest first
    assert ok["success"] == 1 and ok["task"] == "background:titles"
    assert ok["prompt_tokens"] == 10 and ok["total_tokens"] == 13
    assert failed["success"] == 0 and failed["failure_kind"] == "transport"


def test_decide_records_jev_call(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)

    class FakeAdapter:
        def decide(self, manifest, request):
            return {"answers": {}, "usage": {"input_tokens": 40, "output_tokens": 8,
                                             "total_tokens": 48}}

    monkeypatch.setattr(service, "get_adapter", lambda _pid: FakeAdapter())
    from api.model_providers.types import DecisionRequest

    data = service.decide("typesafe", DecisionRequest({"s": 1}, {"q": {}}, "jev-latest",
                                                      "k", "https://x.test", 5.0),
                          usage_task="jev:label")
    assert data == {"answers": {}, "usage": {"input_tokens": 40, "output_tokens": 8,
                                             "total_tokens": 48}}
    rows = usage.all_calls()
    assert len(rows) == 1
    assert rows[0]["provider"] == "typesafe"
    assert rows[0]["operation"] == "structured_decision"
    assert rows[0]["model"] == "jev-latest"
    assert rows[0]["task"] == "jev:label"
    assert rows[0]["total_tokens"] == 48
