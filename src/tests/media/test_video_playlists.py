"""Video wallpaper playlists (settings.json → video_background)."""

from __future__ import annotations

from api.video_playlists import (
    apply_video_background_update,
    normalize_video_background,
)

A = 'https://www.youtube.com/watch?v=aaa'
B = 'https://www.youtube.com/watch?v=bbb'
C = 'https://www.youtube.com/watch?v=ccc'


def test_legacy_urls_migrate_to_default_playlist():
    vb = normalize_video_background({'urls': [A, ' ', B], 'duration': 600, 'opacity': 74, 'enabled': False})
    assert vb['playlists'] == {'default': [A, B]}
    assert vb['active_playlist'] == 'default'
    assert vb['urls'] == [A, B]
    assert (vb['duration'], vb['opacity'], vb['enabled']) == (600, 74, False)


def test_empty_settings_have_empty_default():
    vb = normalize_video_background(None)
    assert vb['playlists'] == {'default': []}
    assert vb['urls'] == []
    assert vb['enabled'] is True


def test_urls_mirror_active_playlist_and_unknown_active_falls_back():
    raw = {'playlists': {'default': [A], 'anime': [B, C]}, 'active_playlist': 'anime', 'urls': [A]}
    assert normalize_video_background(raw)['urls'] == [B, C]
    raw['active_playlist'] = 'gone'
    vb = normalize_video_background(raw)
    assert vb['active_playlist'] == 'default'
    assert vb['urls'] == [A]


def test_playlist_names_are_cleaned_and_deduped():
    vb = normalize_video_background({'playlists': {'  Anime   Loops ': [A], 'anime loops': [B], '': [C]}})
    assert vb['playlists'] == {'Anime Loops': [A]}


def test_legacy_post_urls_edits_active_playlist_only():
    current = {'playlists': {'default': [A], 'anime': [B]}, 'active_playlist': 'anime'}
    vb = apply_video_background_update(current, {'urls': [B, C]})
    assert vb['playlists'] == {'default': [A], 'anime': [B, C]}
    assert vb['urls'] == [B, C]


def test_switch_and_create_playlist():
    current = {'playlists': {'default': [A], 'anime': [B]}, 'active_playlist': 'default'}
    vb = apply_video_background_update(current, {'active_playlist': 'ANIME'})
    assert vb['active_playlist'] == 'anime'
    assert vb['urls'] == [B]
    vb = apply_video_background_update(vb, {'active_playlist': 'lofi'})
    assert vb['active_playlist'] == 'lofi'
    assert vb['playlists']['lofi'] == []
    assert vb['playlists']['anime'] == [B]


def test_replace_playlists_keeps_prefs_and_repairs_active():
    current = {'urls': [A], 'duration': 120, 'opacity': 30, 'enabled': False}
    vb = apply_video_background_update(current, {'playlists': {'anime': [B]}})
    assert vb['playlists'] == {'anime': [B]}
    assert vb['active_playlist'] == 'anime'
    assert (vb['duration'], vb['opacity'], vb['enabled']) == (120, 30, False)


def test_invalid_numbers_keep_previous_values():
    vb = apply_video_background_update({'duration': 120, 'opacity': 30}, {'duration': 'x', 'opacity': 999})
    assert vb['duration'] == 120
    assert vb['opacity'] == 100
