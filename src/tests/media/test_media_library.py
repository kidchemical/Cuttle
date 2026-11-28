"""Media App backend: ref extraction, user-scoped index, file checks, route, CLI.

Owner: api.media_library; client model in test_media_library_js.py.
"""
import json

import pytest

from api.auth_db import AuthDatabase
from api.media_library import __main__ as cli, index, service


def test_extract_refs_reads_attachments_markdown_and_media_tags():
    meta = {"attachments": [
        {"filename": "shot.png", "mime": "image/png", "url": "/output/uploads/7/shot.png"},
        {"filename": "notes.pdf", "mime": "application/pdf", "url": "/output/uploads/7/notes.pdf"},
    ]}
    refs = index.extract_refs("user", "look ![mine](/output/shared/a-1a2b3c4d5e.png)", meta)
    assert [(r["url"], r["source"], r["name"]) for r in refs] == [
        ("/output/uploads/7/shot.png", "upload", "shot.png"),
        ("/output/shared/a-1a2b3c4d5e.png", "user", "a.png"),  # staging hash dropped
    ]
    body = (
        '![Before and after](/output/shared/cmp-99874da650.png "Left old, right new")\n'
        '<media src="/output/shared/clip-abc12345.mp4" />\n'
        '![Narration](/output/shared/talk-deadbeef01.mp3)\n'
        "![dup](/output/shared/cmp-99874da650.png)\n"
        "![local](/mnt/disk/temp/never-staged.png)\n"          # not hosted: skipped
        "![poster](/output/shared/clip-abc12345.poster.jpg)\n"  # derived thumb: skipped
        "```md\n![example](/output/shared/doc-example.png)\n```\n"  # code: skipped
        "`![inline](/output/shared/inline.png)`\n"
        "![ext](https://example.com/cat.webp)"
    )
    refs = index.extract_refs("assistant", body, None)
    assert [(r["url"], r["kind"], r["source"]) for r in refs] == [
        ("/output/shared/cmp-99874da650.png", "image", "agent"),
        ("/output/shared/clip-abc12345.mp4", "video", "agent"),
        ("/output/shared/talk-deadbeef01.mp3", "audio", "agent"),
        ("https://example.com/cat.webp", "image", "agent"),
    ]
    assert refs[0]["title"] == "Before and after"


@pytest.fixture
def world(tmp_path, monkeypatch):
    import api.auth_db as auth
    db = AuthDatabase(tmp_path / "auth.db")
    monkeypatch.setattr(auth, "_db_instance", db)
    out = tmp_path / "output"
    (out / "shared").mkdir(parents=True)
    (out / "shared" / "live-1a2b3c4d5e.png").write_bytes(b"x" * 10)
    (out / "shared" / "clip-abc12345.mp4").write_bytes(b"v" * 4)
    (out / "uploads" / "1").mkdir(parents=True)
    monkeypatch.setattr(service, "_output_root", lambda: out.resolve())
    uid = db.create_user("media-owner@local", "Owner", "local", password="x")
    other = db.create_user("media-other@local", "Other", "local", password="x")
    game = db.create_chat_session(uid, "Game art")
    old = db.create_chat_session(uid, "Old chat")
    foreign = db.create_chat_session(other, "Not yours")
    (out / "uploads" / str(game)).mkdir(parents=True, exist_ok=True)
    (out / "uploads" / str(game) / "ref.png").write_bytes(b"u" * 3)
    db.add_message(game, "user", "[Attached: ref.png] match this", {"attachments": [
        {"filename": "ref.png", "mime": "image/png", "url": f"/output/uploads/{game}/ref.png"}]})
    db.add_message(game, "system", "Stopped")  # not a share-index bubble
    db.add_message(game, "assistant", "Done ![New icon](/output/shared/live-1a2b3c4d5e.png)")
    db.add_message(game, "assistant", "Again ![New icon](/output/shared/live-1a2b3c4d5e.png)\n"
                                       '<media src="/output/shared/clip-abc12345.mp4"/>')
    db.add_message(old, "assistant", "![gone](/output/shared/expired-0011223344.png)")
    db.add_message(foreign, "assistant", "![theirs](/output/shared/live-1a2b3c4d5e.png)")
    db.set_session_archived(old, uid, True)
    return db, uid, other, game, old, foreign


def test_list_media_is_user_scoped_newest_first_with_handles_and_files(world):
    db, uid, other, game, old, foreign = world
    data = service.list_media(uid, db=db)
    items = data["items"]
    assert [it["name"] for it in items] == ["expired.png", "clip.mp4", "live.png", "ref.png"]
    by = {it["name"]: it for it in items}
    # Share index counts user+assistant rows only (system row skipped).
    assert by["ref.png"]["handle"] == f"CH-{game:06d}-1" and by["ref.png"]["source"] == "upload"
    assert by["live.png"]["handle"] == f"CH-{game:06d}-2" and by["live.png"]["refs"] == 2
    assert by["clip.mp4"]["share_index"] == 3 and by["clip.mp4"]["kind"] == "video"
    assert by["live.png"]["available"] is True and by["live.png"]["size"] == 10
    assert by["expired.png"]["available"] is False and by["expired.png"]["chat_archived"] is True
    assert by["live.png"]["chat_title"] == "Game art"
    assert by["live.png"]["shared_at"].endswith("Z")
    assert data["totals"] == {
        "items": 4, "images": 3, "videos": 1, "audios": 0, "available": 3,
    }
    assert [(c["id"], c["count"]) for c in data["chats"]] == [(old, 1), (game, 3)]
    assert [it["name"] for it in service.list_media(uid, db=db, chat_id=game)["items"]] == [
        "clip.mp4", "live.png", "ref.png"]
    assert [it["chat_id"] for it in service.list_media(other, db=db)["items"]] == [foreign]


def test_deleted_and_in_progress_rows_are_not_listed(world):
    db, uid, other, game, old, foreign = world
    db.add_message(game, "assistant", "![wip](/output/shared/wip.png)", {"in_progress": True})
    assert "wip.png" not in [it["name"] for it in service.list_media(uid, db=db)["items"]]
    db.delete_chat_session(game, uid)
    assert [it["chat_id"] for it in service.list_media(uid, db=db)["items"]] == [old]


def test_file_info_stays_inside_output_root(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "secret.png").write_bytes(b"s")
    info = service.file_info_for(tmp_path / "out")
    assert info("/output/../secret.png") == {"available": False, "size": None}
    assert info("/output/%2e%2e/secret.png") == {"available": False, "size": None}
    assert info("https://example.com/x.png") == {"available": None, "size": None}


def test_route_requires_sign_in_and_scopes_to_caller(world):
    from flask import Flask
    from api.media_library.routes import media_library_bp
    db, uid, other, game, old, foreign = world
    app = Flask(__name__)
    app.register_blueprint(media_library_bp)
    client = app.test_client()
    assert client.get("/api/media-library").status_code == 401
    client.set_cookie("session_token", db.create_auth_session(uid))
    resp = client.get("/api/media-library")
    assert resp.status_code == 200 and resp.headers["Cache-Control"] == "no-store"
    assert resp.get_json()["totals"]["items"] == 4
    assert len(client.get(f"/api/media-library?chat={game}").get_json()["items"]) == 3
    client.set_cookie("session_token", db.create_auth_session(other))
    assert [it["chat_id"] for it in client.get("/api/media-library").get_json()["items"]] == [foreign]
    page = client.get("/media_page.html")
    assert page.status_code == 200 and b"media_library_page.js" in page.data


def test_cli_lists_filtered_json(world, capsys):
    db, uid, *_ = world
    assert cli.main(["list", "--user-id", str(uid), "--available", "--kind", "image", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [it["name"] for it in out["items"]] == ["live.png", "ref.png"]
