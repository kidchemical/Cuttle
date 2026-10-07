"""Shared notification delivery survives removal of the forge worker."""
import json

from api import ui_notify
from api.ui_notify import main as ui_notify_main


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


def _cli_isolated(tmp_path, monkeypatch, capsys, *argv):
    monkeypatch.setattr(ui_notify, '_NOTIFY_PATH', tmp_path / 'tray.jsonl')
    monkeypatch.setattr(ui_notify, '_UI_TOAST_PATH', tmp_path / 'ui.jsonl')
    code = ui_notify_main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_cli_send_queues_tray_and_ui_toast(tmp_path, monkeypatch, capsys):
    code, out = _cli_isolated(tmp_path, monkeypatch, capsys, 'send', 'Build done', '--variant', 'success')
    assert code == 0 and out == {'success': True, 'message': 'Build done', 'variant': 'success'}
    assert json.loads((tmp_path / 'tray.jsonl').read_text(encoding='utf-8'))['message'] == 'Build done'
    assert ui_notify.pull_ui_toasts()[0]['variant'] == 'success'


def test_cli_send_rejects_empty_message_and_bad_variant(tmp_path, monkeypatch, capsys):
    code, out = _cli_isolated(tmp_path, monkeypatch, capsys, 'send', '   ')
    assert code == 2 and out['success'] is False
    code, out = _cli_isolated(tmp_path, monkeypatch, capsys, 'send', 'hi', '--variant', 'loud')
    assert code == 2 and 'variant' in out['error']


def test_cli_pending_drains_and_limits(tmp_path, monkeypatch, capsys):
    _cli_isolated(tmp_path, monkeypatch, capsys, 'send', 'one')
    _cli_isolated(tmp_path, monkeypatch, capsys, 'send', 'two')
    code, out = _cli_isolated(tmp_path, monkeypatch, capsys, 'pending', '--limit', '1')
    assert code == 0 and [t['message'] for t in out['toasts']] == ['one']
    code, out = _cli_isolated(tmp_path, monkeypatch, capsys, 'pending')
    assert code == 0 and out['toasts'] == []
