"""Chat image / file attachments: reaching the agent, and surviving a refresh.

Two defects, one root cause. The vision pre-pass used to run at the bottom of
`/api/chat`, below every slash-agent handler. A turn sent with the `/cursor`
(or `/codex`, `/muse`, `/hermes`, project-command, router) badge therefore:

1. reached the CLI with the raw prompt and no description of the image, so the
   agent answered "no image came through"; and
2. persisted a user row with neither the `[Attached: …]` note nor attachment
   metadata, so the thumbnail vanished on refresh.

Also covers the vision provider chain: a provider that is out of credit must
report that, not degrade into a silent empty result.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api import vision_prepass as vp


PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)


# --------------------------------------------------------------------------
# Digest placement: slash commands must still be at the front of the prompt
# --------------------------------------------------------------------------


@pytest.fixture
def stub_vision(monkeypatch):
    """Deterministic image description; no network."""
    monkeypatch.setattr(
        vp, "describe_image", lambda att: f"description of {att.get('filename')}"
    )


def test_digest_is_appended_so_slash_commands_still_match(stub_vision):
    out = vp.append_attachment_digest(
        "/cursor what is this?", [{"filename": "shot.png", "data": PNG_1PX}]
    )
    assert out.startswith("/cursor what is this?")
    assert vp.DIGEST_HEADER in out
    assert "description of shot.png" in out


def test_digest_carries_the_local_path_for_the_agent(stub_vision):
    out = vp.append_attachment_digest(
        "look", [{"filename": "shot.png", "path": r"E:\uploads\1\shot.png", "data": PNG_1PX}]
    )
    assert r"E:\uploads\1\shot.png" in out


def test_attachment_only_send_still_produces_a_prompt(stub_vision):
    out = vp.append_attachment_digest("", [{"filename": "shot.png", "data": PNG_1PX}])
    assert out.startswith(vp.DIGEST_HEADER)


def test_no_attachments_leaves_the_message_untouched():
    assert vp.append_attachment_digest("/cursor hi", []) == "/cursor hi"


# --------------------------------------------------------------------------
# Vision provider chain
# --------------------------------------------------------------------------


def test_openai_takes_over_when_anthropic_is_out_of_credit(monkeypatch):
    def broke(**_kw):
        raise RuntimeError("credit balance is too low")

    monkeypatch.setattr(vp, "VISION_PROVIDERS", (
        ("Claude", broke),
        ("OpenAI", lambda **_kw: "a red error dialog"),
    ))
    out = vp._describe_image_via_providers(data=PNG_1PX, mime="image/png", filename="x.png")
    assert out == "a red error dialog"


def test_every_provider_down_reports_why(monkeypatch):
    def broke(**_kw):
        raise RuntimeError("credit balance is too low")

    monkeypatch.setattr(vp, "VISION_PROVIDERS", (("Claude", broke), ("OpenAI", broke)))
    out = vp._describe_image_via_providers(data=PNG_1PX, mime="image/png", filename="x.png")
    assert "credit balance is too low" in out
    assert "not analyzed" in out


# --------------------------------------------------------------------------
# History text: digest out, short note in
# --------------------------------------------------------------------------


def test_history_text_drops_the_digest_and_keeps_the_note():
    from api.web_chat_api import _attachment_history_text

    prompt = f"/cursor what is this?\n\n{vp.DIGEST_HEADER}\n• shot.png (image):\nblah"
    assert (
        _attachment_history_text(prompt, "[Attached: shot.png]")
        == "/cursor what is this?\n[Attached: shot.png]"
    )


def test_history_text_is_a_no_op_without_attachments():
    from api.web_chat_api import _attachment_history_text

    assert _attachment_history_text("/cursor hi", "") == "/cursor hi"


# --------------------------------------------------------------------------
# End to end through /api/chat with the /cursor badge
# --------------------------------------------------------------------------


@pytest.fixture
def cursor_chat(tmp_path, monkeypatch, stub_vision, owner_session):
    """`/api/chat` wired to a fake DB + fake Cursor CLI, with a real upload on disk."""
    from api import web_chat_api as wca

    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    uploads = tmp_path / "home" / "output" / "uploads" / "77"
    uploads.mkdir(parents=True)
    image = uploads / "shot.png"
    image.write_bytes(PNG_1PX)

    persisted: list = []

    class FakeDB:
        def add_message(self, sid, role, content, metadata=None):
            persisted.append({"role": role, "content": content, "metadata": metadata})
            return len(persisted)

        def get_chat_session(self, *_a, **_k):
            return {"id": 77}

    seen: dict = {}

    def fake_harness(
        agent_id, prompt, chat_session_id, status_queue=None, project_path=None, **_kw
    ):
        assert agent_id == "cursor"
        seen["prompt"] = prompt
        return {"success": True, "response": "ok", "type": "cursor_cli"}

    monkeypatch.setattr(wca, "actual_project_root", tmp_path)
    monkeypatch.setattr(wca, "get_auth_db", lambda: FakeDB())
    monkeypatch.setattr(
        wca, "_resolve_auth_chat_session", lambda sid: ({"id": 1}, 77, False)
    )
    monkeypatch.setattr(wca, "_resolve_request_project_path", lambda data: str(tmp_path))
    monkeypatch.setattr(wca, "_stamp_auth_session_project", lambda *a, **k: None)
    monkeypatch.setattr(wca, "_run_pinned_harness_turn", fake_harness)
    monkeypatch.setattr(wca, "_make_auth_assistant_saver", lambda *a, **k: None)

    client = owner_session.sign_in(wca.app.test_client())

    def send(message):
        return client.post(
            "/api/chat",
            json={
                "message": message,
                "session_id": 77,
                "stream": False,
                "attachments": [
                    {
                        "filename": "shot.png",
                        "path": str(image),
                        "mime": "image/png",
                        "url": "/output/uploads/77/shot.png",
                    }
                ],
            },
        )

    return send, seen, persisted


def test_cursor_turn_receives_the_image_description(cursor_chat):
    send, seen, _persisted = cursor_chat
    assert send("/cursor what is this?").status_code == 200
    assert "description of shot.png" in seen["prompt"]
    # Prompt is what follows the command, digest included.
    assert seen["prompt"].startswith("what is this?")


def test_cursor_turn_persists_the_attachment_for_refresh(cursor_chat):
    send, _seen, persisted = cursor_chat
    send("/cursor what is this?")

    user_rows = [m for m in persisted if m["role"] == "user"]
    assert len(user_rows) == 1
    row = user_rows[0]
    assert row["content"] == "/cursor what is this?\n[Attached: shot.png]"
    assert vp.DIGEST_HEADER not in row["content"]
    assert row["metadata"]["attachments"] == [
        {"filename": "shot.png", "mime": "image/png", "url": "/output/uploads/77/shot.png"}
    ]


def test_metadata_survives_a_json_round_trip(cursor_chat):
    """auth_db stores metadata as JSON; the chat UI reads attachments back out."""
    send, _seen, persisted = cursor_chat
    send("/cursor what is this?")
    row = [m for m in persisted if m["role"] == "user"][0]
    restored = json.loads(json.dumps(row["metadata"]))
    assert restored["attachments"][0]["url"] == "/output/uploads/77/shot.png"


def test_uploads_outside_the_upload_root_are_rejected(tmp_path):
    outside = tmp_path / "secret.png"
    outside.write_bytes(PNG_1PX)
    root = tmp_path / "uploads"
    root.mkdir()
    assert vp.resolve_upload_refs([{"path": str(outside)}], root) == []
