"""Shared chat media staging + TTL purge."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from api import shared_media as sm

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)


@pytest.fixture
def project(tmp_path: Path):
    (tmp_path / "src" / "output").mkdir(parents=True)
    return tmp_path


def test_stage_file_copies_into_shared(project: Path):
    src = project / "shot.png"
    src.write_bytes(PNG_1PX)
    result = sm.stage_file(src, project_root=project, preferred_name="shot.png")
    assert result["success"] is True
    assert result["kind"] == "image"
    assert result["url"].startswith("/output/shared/")
    dest = project / "src" / "output" / "shared" / result["filename"]
    assert dest.is_file()
    assert dest.read_bytes() == PNG_1PX


def test_stage_rejects_non_media(project: Path):
    src = project / "notes.txt"
    src.write_text("hi", encoding="utf-8")
    result = sm.stage_file(src, project_root=project)
    assert result["success"] is False


def test_purge_expired_by_mtime(project: Path, monkeypatch):
    root = sm.shared_media_root(project)
    root.mkdir(parents=True)
    keep = root / "keep-aaaaaaaaaa.png"
    drop = root / "old-bbbbbbbbbb.png"
    keep.write_bytes(PNG_1PX)
    drop.write_bytes(PNG_1PX)
    now = time.time()
    # Force old mtime
    old = now - (10 * 86400)
    import os

    os.utime(drop, (old, old))
    os.utime(keep, (now, now))

    result = sm.purge_expired(project_root=project, ttl=7, now=now)
    assert result["success"] is True
    assert result["deleted"] == 1
    assert drop.name in result["files"]
    assert keep.is_file()
    assert not drop.exists()


def test_rewrite_local_markdown_image(project: Path):
    src = project / "temp" / "a.png"
    src.parent.mkdir(parents=True)
    src.write_bytes(PNG_1PX)
    text = f"See ![cap]({src.as_posix()}) and keep ![remote](/output/uploads/x/a.png)"
    out, n = sm.rewrite_local_media_refs(text, project_root=project)
    assert n == 1
    assert "/output/shared/" in out
    assert "![remote](/output/uploads/x/a.png)" in out
    assert str(src) not in out or "/output/shared/" in out


def test_rewrite_media_tag_local_src(project: Path):
    src = project / "clip.mp4"
    # Minimal fake mp4 bytes — extension gates staging, not content
    src.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    text = f'<media type="video" src="{src.as_posix()}" title="Clip"/>'
    out, n = sm.rewrite_local_media_refs(text, project_root=project)
    assert n == 1
    assert '/output/shared/' in out
    assert 'type="video"' in out


def test_rewrite_preserves_markdown_description(project: Path):
    src = project / "temp" / "a.png"
    src.parent.mkdir(parents=True)
    src.write_bytes(PNG_1PX)
    text = f'![cap]({src.as_posix()} "After safe-area fix on S22")'
    out, n = sm.rewrite_local_media_refs(text, project_root=project)
    assert n == 1
    assert "/output/shared/" in out
    assert 'After safe-area fix on S22' in out
    assert out.startswith("![cap](")


def test_split_md_image_dest():
    url, title = sm.split_md_image_dest('/output/shared/x.png "hello there"')
    assert url == "/output/shared/x.png"
    assert title == "hello there"
    url2, title2 = sm.split_md_image_dest("C:/Projects/shot.png")
    assert url2 == "C:/Projects/shot.png"
    assert title2 is None


def test_looks_like_local_path():
    assert sm.looks_like_local_path(r"C:\Projects\shot.png")
    assert sm.looks_like_local_path("file:///C:/Projects/shot.png")
    assert not sm.looks_like_local_path("/output/shared/x.png")
    assert not sm.looks_like_local_path("https://example.com/a.png")


def test_stage_writes_original_path_meta(project: Path):
    src = project / "temp" / "hero.png"
    src.parent.mkdir(parents=True)
    src.write_bytes(PNG_1PX)
    result = sm.stage_file(src, project_root=project, preferred_name="hero.png")
    assert result["success"] is True
    assert result["original_name"] == "hero.png"
    assert "temp/hero.png" in result["original_path"].replace("\\", "/")
    meta = sm.read_stage_meta(result["url"], project_root=project)
    assert meta["success"] is True
    assert meta["meta"]["original_name"] == "hero.png"
    assert meta["meta"]["original_path"].endswith("temp/hero.png") or "temp/hero.png" in meta["meta"]["original_path"]


def test_meta_filename_convention():
    assert sm.meta_filename_for("clip-abc123.mp4") == "clip-abc123.meta.json"
    assert sm.meta_filename_for("clip-abc123.poster.jpg") == "clip-abc123.meta.json"


def test_stage_video_makes_midframe_poster(project: Path, monkeypatch):
    """Stage a tiny 'video' and stub ffmpeg mid-frame extract."""
    src = project / "clip.mp4"
    src.write_bytes(b"\x00\x00\x00\x18ftypmp42fake")

    def fake_extract(video_path, dest_jpg):
        dest = Path(dest_jpg)
        dest.write_bytes(b"\xff\xd8\xff\xd9" + b"poster")
        return {
            "success": True,
            "path": str(dest),
            "bytes": dest.stat().st_size,
            "seek_seconds": 1.0,
            "duration_seconds": 2.0,
        }

    monkeypatch.setattr(sm, "extract_midframe_poster", fake_extract)
    monkeypatch.setattr(sm, "find_ffmpeg", lambda: "ffmpeg")
    result = sm.stage_file(src, project_root=project, preferred_name="clip.mp4")
    assert result["success"] is True
    assert result["kind"] == "video"
    assert result.get("poster_url", "").endswith(".poster.jpg")
    poster = project / "src" / "output" / "shared" / Path(result["poster_url"]).name
    assert poster.is_file()


def test_ensure_poster_sidecar_lazy(project: Path, monkeypatch):
    shared = sm.shared_media_root(project)
    shared.mkdir(parents=True)
    video = shared / "demo-aaaaaaaaaa.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    def fake_extract(video_path, dest_jpg):
        dest = Path(dest_jpg)
        dest.write_bytes(b"\xff\xd8\xff\xd9xx")
        return {"success": True, "path": str(dest), "bytes": 6, "seek_seconds": 0.5}

    monkeypatch.setattr(sm, "extract_midframe_poster", fake_extract)
    path = sm.ensure_poster_sidecar_file("demo-aaaaaaaaaa.poster.jpg", project_root=project)
    assert path is not None
    assert path.is_file()
    assert path.name == "demo-aaaaaaaaaa.poster.jpg"
