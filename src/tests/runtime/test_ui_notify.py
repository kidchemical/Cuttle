"""Shared notification delivery survives removal of the forge worker."""
import json

from api import ui_notify


def test_notifications_keep_tray_and_ui_delivery_separate(tmp_path, monkeypatch):
    tray = tmp_path / 'tray.jsonl'
    ui = tmp_path / 'ui.jsonl'
    monkeypatch.setattr(ui_notify, '_NOTIFY_PATH', tray)
    monkeypatch.setattr(ui_notify, '_UI_TOAST_PATH', ui)
    ui_notify.notify_tray('Work complete', variant='success')
    tray_entry = json.loads(tray.read_text(encoding="utf-8"))
    assert tray_entry == {'title': 'Cuttle — Success', 'message': 'Work complete'}
    entries = ui_notify.pull_ui_toasts()
    assert len(entries) == 1
    assert entries[0]['message'] == 'Work complete'
    assert entries[0]['variant'] == 'success'
    assert entries[0]['ts']
    assert ui_notify.pull_ui_toasts() == []
    assert json.loads(tray.read_text(encoding="utf-8")) == tray_entry


def test_ui_notifications_skip_corrupt_entries(tmp_path, monkeypatch):
    ui = tmp_path / 'ui.jsonl'
    monkeypatch.setattr(ui_notify, '_UI_TOAST_PATH', ui)
    assert ui_notify.pull_ui_toasts() == []
    ui.write_text('bad json\n{}\n{"message": "Valid"}\n', encoding="utf-8")
    assert ui_notify.pull_ui_toasts() == [{'message': 'Valid'}]
    assert ui_notify.pull_ui_toasts() == []
