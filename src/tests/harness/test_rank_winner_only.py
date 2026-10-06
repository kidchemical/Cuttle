"""Winner-only ranked injection (Jev runners-up correction).

Offline policy tests with deterministic fake judges: selection behavior is
proven from fake distributions, never from semantic Jev accuracy. Skills are
stubbed out so only project/global docs are candidates.
"""

from __future__ import annotations

import pytest

from api.jev.client import FakeJevClient


@pytest.fixture(autouse=True)
def _docs_only(monkeypatch):
    import api.jev.rank as rank

    monkeypatch.setattr(rank, "_skill_summaries", lambda project_path=None: [])
    yield


def _guest(tmp_path, bodies: dict, router_ini: str | None = None):
    guest = tmp_path / "guest"
    docs = guest / ".cuttle" / "docs"
    docs.mkdir(parents=True)
    for name, body in bodies.items():
        (docs / name).write_text(body, encoding="utf-8")
    if router_ini is not None:
        (guest / ".cuttle" / "GLOBAL.ini").write_text(router_ini, encoding="utf-8")
    return guest


def _judge(captured=None, *, needs=0.9, winner="doc:b.md", conf=0.8, probs=None):
    probs = (
        probs
        if probs is not None
        else {"doc:b.md": 0.45, "doc:a.md": 0.35, "doc:c.md": 0.20}
    )

    def _handler(state, questions):
        if captured is not None:
            captured["state"] = state
            captured["questions"] = questions
        return {
            "needs_extra": {"type": "noul", "noul": needs},
            "pick": {
                "type": "choice",
                "choice": winner,
                "confidence": conf,
                "probabilities": dict(probs),
            },
        }

    return FakeJevClient(handler=_handler)


_BODIES = {
    "a.md": "# Alpha runbook\nAlpha procedure details here.",
    "b.md": "# Beta runbook\nBeta procedure details here.",
    "c.md": "# Gamma runbook\nGamma procedure details here.",
}


def _rank(tmp_path, client, **kwargs):
    from api.jev.rank import rank_context

    guest = _guest(tmp_path, _BODIES)
    return rank_context(
        "do the beta thing",
        project_path=str(guest),
        inventory={"docs": ["a.md", "b.md", "c.md"]},
        client=client,
        **kwargs,
    )


def test_winner_only_ignores_high_probability_runners_up(tmp_path):
    ranked = _rank(tmp_path, _judge())
    assert [i["id"] for i in ranked["items"]] == ["doc:b.md"]
    assert ranked["meta"]["injected"] == ["doc:b.md"]
    # Distribution is preserved as evidence, not as injection.
    assert ranked["meta"]["probabilities"]["doc:a.md"] == 0.35


def test_winner_only_ignores_low_probability_runners_up(tmp_path):
    client = _judge(probs={"doc:b.md": 0.94, "doc:a.md": 0.04, "doc:c.md": 0.02})
    ranked = _rank(tmp_path, client)
    assert [i["id"] for i in ranked["items"]] == ["doc:b.md"]


def test_no_extra_needed_injects_nothing(tmp_path):
    ranked = _rank(tmp_path, _judge(needs=0.1))
    assert ranked["items"] == []


def test_low_confidence_injects_nothing(tmp_path):
    ranked = _rank(tmp_path, _judge(conf=0.2))
    assert ranked["items"] == []


def test_max_items_zero_injects_nothing(tmp_path):
    ranked = _rank(tmp_path, _judge(), max_items=0)
    assert ranked["items"] == []
    assert ranked["meta"]["injected"] == []


def test_max_items_zero_never_calls_judge(tmp_path):
    from api.jev.rank import rank_context

    called = {"n": 0}

    def _handler(state, questions):
        called["n"] += 1
        return {
            "needs_extra": {"type": "noul", "noul": 0.9},
            "pick": {
                "type": "choice",
                "choice": "doc:b.md",
                "confidence": 0.9,
                "probabilities": {"doc:b.md": 1.0},
            },
        }

    guest = _guest(tmp_path, _BODIES)
    ranked = rank_context(
        "do the beta thing",
        project_path=str(guest),
        inventory={"docs": ["a.md", "b.md", "c.md"]},
        client=FakeJevClient(handler=_handler),
        max_items=0,
    )
    assert called["n"] == 0
    assert ranked["items"] == []


def test_missing_winner_injects_nothing(tmp_path):
    ranked = _rank(tmp_path, _judge(winner="", probs={}))
    assert ranked["items"] == []


def test_unknown_winner_injects_nothing(tmp_path):
    ranked = _rank(
        tmp_path,
        _judge(winner="doc:nope.md", probs={"doc:nope.md": 0.9}),
    )
    assert ranked["items"] == []
    assert ranked["meta"]["injected"] == []


def test_client_exception_fails_open(tmp_path):
    from api.jev.rank import rank_context

    def _boom(state, questions):
        raise RuntimeError("judge down")

    guest = _guest(tmp_path, _BODIES)
    ranked = rank_context(
        "do the beta thing",
        project_path=str(guest),
        inventory={"docs": ["a.md", "b.md", "c.md"]},
        client=FakeJevClient(handler=_boom),
    )
    assert ranked["items"] == []
    assert ranked["meta"]["skipped"] is True


def test_unavailable_judge_skips(tmp_path, monkeypatch):
    import api.jev.rank as rank

    monkeypatch.setattr(rank, "jev_available", lambda: False)
    guest = _guest(tmp_path, _BODIES)
    ranked = rank.rank_context(
        "do the beta thing",
        project_path=str(guest),
        inventory={"docs": ["a.md", "b.md", "c.md"]},
        client=None,
    )
    assert ranked["items"] == []
    assert ranked["meta"]["skipped"] is True


def test_project_body_overrides_global_twin(tmp_path, monkeypatch):
    import api.cuttle_brain.context_compiler as compiler
    from api.jev.rank import rank_context

    global_root = tmp_path / "cuttle_global"
    (global_root / "docs").mkdir(parents=True)
    (global_root / "docs" / "a.md").write_text(
        "# Global alpha\nGlobal procedure.", encoding="utf-8"
    )
    monkeypatch.setattr(compiler, "_cuttle_global_config", lambda: global_root)

    guest = _guest(tmp_path, {"a.md": "# Project alpha\nProject procedure."})
    captured = {}
    ranked = rank_context(
        "alpha work",
        project_path=str(guest),
        inventory={"docs": ["a.md"]},
        client=_judge(captured, winner="doc:a.md", probs={"doc:a.md": 1.0}),
    )
    assert "Project procedure." in ranked["items"][0]["body"]
    criteria = captured["questions"]["pick"]["criteria"]
    assert "Project alpha" in criteria["doc:a.md"]


def test_docs_off_blocks_global_fallback(tmp_path, monkeypatch):
    import api.cuttle_brain.context_compiler as compiler
    from api.jev.rank import rank_context

    global_root = tmp_path / "cuttle_global"
    (global_root / "docs").mkdir(parents=True)
    (global_root / "docs" / "g.md").write_text(
        "# Global golf\nGlobal procedure.", encoding="utf-8"
    )
    monkeypatch.setattr(compiler, "_cuttle_global_config", lambda: global_root)

    guest = _guest(tmp_path, {"a.md": "# Project alpha\nProject procedure."},
                   router_ini="[global]\ndocs = off\n")
    personal = guest / ".cuttle" / "personal" / "docs"
    personal.mkdir(parents=True)
    (personal / "a.md").write_text("Local machine alias note.", encoding="utf-8")
    captured = {}
    ranked = rank_context(
        "alpha work",
        project_path=str(guest),
        inventory={"docs": ["a.md"]},
        client=_judge(captured, winner="doc:a.md", probs={"doc:a.md": 1.0}),
    )
    # No global-only IDs reach the judge when docs are off.
    criteria = captured["questions"]["pick"]["criteria"]
    assert "doc:g.md" not in criteria
    # Bounded overlay summary: title plus first intro, not the whole delta.
    assert "Project alpha" in criteria["doc:a.md"]
    assert "Project procedure." in criteria["doc:a.md"]
    assert len(criteria["doc:a.md"]) <= 400
    # Injection still carries the full merged body including the overlay.
    assert "Project procedure." in ranked["items"][0]["body"]
    assert "Local machine alias note." in ranked["items"][0]["body"]
    assert "Global procedure." not in ranked["items"][0]["body"]


def test_personal_overlay_injected_and_summarized(tmp_path):
    from api.jev.rank import rank_context

    guest = _guest(tmp_path, {"a.md": "# Project alpha\nTracked procedure."})
    personal = guest / ".cuttle" / "personal" / "docs"
    personal.mkdir(parents=True)
    (personal / "a.md").write_text("Local machine alias note.", encoding="utf-8")
    ranked = rank_context(
        "alpha work",
        project_path=str(guest),
        inventory={"docs": ["a.md"]},
        client=_judge(winner="doc:a.md", probs={"doc:a.md": 1.0}),
    )
    assert "Local machine alias note." in ranked["items"][0]["body"]


def test_truncated_body_marked_excerpt_request_preserved(tmp_path):
    from api.jev import thresholds as T
    from api.jev.rank import format_ranked_block, rank_context

    big = "# Big beta\n" + "procedure line\n" * 800
    guest = _guest(tmp_path, {"b.md": big})
    captured = {}
    ranked = rank_context(
        "do the beta thing",
        project_path=str(guest),
        inventory={"docs": ["b.md"]},
        client=_judge(captured, winner="doc:b.md", probs={"doc:b.md": 1.0}),
    )
    body = ranked["items"][0]["body"]
    assert "(excerpt" in body
    assert len(body) <= T.PER_ITEM_CHARS
    assert len(body) <= T.MAX_INJECT_CHARS
    assert ranked["items"][0]["excerpt"] is True
    assert ranked["meta"]["excerpt"] is True
    assert captured["state"]["user_request"] == "do the beta thing"
    block = format_ranked_block(ranked["items"])
    assert "before acting on a procedure" in block


def test_small_budgets_keep_body_within_limits(tmp_path, monkeypatch):
    from api.jev import thresholds as T
    from api.jev.rank import rank_context

    monkeypatch.setattr(T, "PER_ITEM_CHARS", 100)
    monkeypatch.setattr(T, "MAX_INJECT_CHARS", 120)
    guest = _guest(tmp_path, {"b.md": "# Big beta\n" + "procedure line\n" * 50})
    ranked = rank_context(
        "do the beta thing",
        project_path=str(guest),
        inventory={"docs": ["b.md"]},
        client=_judge(winner="doc:b.md", probs={"doc:b.md": 1.0}),
    )
    body = ranked["items"][0]["body"]
    assert len(body) <= 100
    assert "(excerpt" in body
    assert ranked["items"][0]["excerpt"] is True


def test_tiny_budget_reports_excerpt_without_marker(tmp_path, monkeypatch):
    from api.jev import thresholds as T
    from api.jev.rank import rank_context

    monkeypatch.setattr(T, "PER_ITEM_CHARS", 20)
    monkeypatch.setattr(T, "MAX_INJECT_CHARS", 20)
    guest = _guest(tmp_path, {"b.md": "# Big beta\n" + "procedure line\n" * 50})
    ranked = rank_context(
        "do the beta thing",
        project_path=str(guest),
        inventory={"docs": ["b.md"]},
        client=_judge(winner="doc:b.md", probs={"doc:b.md": 1.0}),
    )
    body = ranked["items"][0]["body"]
    assert len(body) <= 20
    assert ranked["items"][0]["excerpt"] is True
    assert ranked["meta"]["excerpt"] is True
    from api.jev.rank import format_ranked_block

    assert "(excerpt" in format_ranked_block(ranked["items"])


def test_long_request_preserved_through_compile(tmp_path):
    """compile_context keeps the entire long request; the judge preview stays bounded."""
    from api.cuttle_brain.context_compiler import compile_context
    from api.jev.client import FakeJevClient, set_client_override

    guest = _guest(tmp_path, _BODIES)
    (guest / ".cuttle" / "rules").mkdir(parents=True)
    (guest / ".cuttle" / "rules" / "01.md").write_text("Be brief.", encoding="utf-8")
    prompt = "REQUEST-HEAD " + "x" * 4000 + " REQUEST-TAIL"
    captured = {}
    override = _judge(captured)
    set_client_override(override)
    try:
        compiled = compile_context(
            prompt, project_path=str(guest), inject_capabilities=False
        )
    finally:
        set_client_override(None)
    assert "ranked_context" in compiled.layers_used
    assert compiled.user_prompt == prompt
    assert "REQUEST-TAIL" in compiled.prompt
    # The judge only ever sees the bounded preview, not the full request.
    assert captured["state"]["user_request"] == prompt[:1500]
    assert "REQUEST-TAIL" not in captured["state"]["user_request"]


def test_project_runbook_survives_saturated_global_catalog(tmp_path, monkeypatch):
    import api.cuttle_brain.context_compiler as compiler
    from api.jev.rank import rank_context

    global_root = tmp_path / "cuttle_global"
    (global_root / "docs").mkdir(parents=True)
    for n in range(45):
        (global_root / "docs" / f"g{n:02d}.md").write_text(
            f"# Global {n}\nGlobal body {n}.", encoding="utf-8"
        )
    (global_root / "docs" / "a.md").write_text(
        "# Global alpha\nGlobal body.", encoding="utf-8"
    )
    monkeypatch.setattr(compiler, "_cuttle_global_config", lambda: global_root)

    guest = _guest(
        tmp_path,
        {
            "local.md": "# Local only\nLocal procedure.",
            "a.md": "# Project alpha\nProject procedure.",
        },
    )
    captured = {}
    ranked = rank_context(
        "local work",
        project_path=str(guest),
        inventory={"docs": ["local.md", "a.md"]},
        client=_judge(captured, winner="doc:local.md", probs={"doc:local.md": 1.0}),
    )
    criteria = captured["questions"]["pick"]["criteria"]
    assert "doc:local.md" in criteria
    assert len(criteria) <= 40
    assert list(criteria).count("doc:a.md") == 1
    assert "Project alpha" in criteria["doc:a.md"]
    assert "Local procedure." in ranked["items"][0]["body"]

    twin = rank_context(
        "alpha work",
        project_path=str(guest),
        inventory={"docs": ["local.md", "a.md"]},
        client=_judge(winner="doc:a.md", probs={"doc:a.md": 1.0}),
    )
    assert "Project procedure." in twin["items"][0]["body"]


def test_block_labels_excerpt_from_flag_without_marker():
    from api.jev.rank import format_ranked_block

    block = format_ranked_block(
        [{"kind": "doc", "name": "b.md", "body": "short body", "excerpt": True}]
    )
    assert "(excerpt" in block
    plain = format_ranked_block(
        [{"kind": "doc", "name": "b.md", "body": "short body", "excerpt": False}]
    )
    assert "(excerpt" not in plain


def test_deterministic_injected_char_count(tmp_path):
    bodies = {"a.md": "A" * 100, "b.md": "B" * 200, "c.md": "C" * 300}
    guest = _guest(tmp_path, bodies)
    from api.jev.rank import rank_context

    ranked = rank_context(
        "do the beta thing",
        project_path=str(guest),
        inventory={"docs": ["a.md", "b.md", "c.md"]},
        client=_judge(),
    )
    assert [i["id"] for i in ranked["items"]] == ["doc:b.md"]
    assert sum(len(i["body"]) for i in ranked["items"]) == 200
