"""Display care (System care setting ``display``): keep the display awake + report it off."""

from __future__ import annotations

import pytest

from api.chat_ops import brief, display_care, store
from tests.chat_ops.test_chat_ops import _schedule, _snap, _tick, chats, isolated_home  # noqa: F401
from tests.chat_ops.test_system_care import _new


def _op(state="pending", system="display", seen=None, oid="a"):
    return {"id": oid, "state": state, "payload": {"system": brief.system(system)},
            "result": {"last_seen": {"state": seen}} if seen else {}}


class FakeHold:
    """Stands in for the wakepy mode thread."""

    made = 0

    def __init__(self, error=""):
        type(self).made += 1
        self.error = error
        self.method = "" if error else "fake"
        self.held = not error
        self.release = self

    def set(self):
        self.held = False


@pytest.fixture
def holds(monkeypatch):
    FakeHold.made = 0
    monkeypatch.setattr(display_care, "_Hold", FakeHold)
    yield FakeHold
    display_care.apply(False)


def test_only_live_unfinished_schedules_with_the_setting_want_the_display():
    assert display_care.wanted([_op()]) == ["a"]  # no check yet counts as working
    assert display_care.wanted([_op(seen="busy"), _op(seen="idle_with_work", oid="b")]) == ["a", "b"]
    assert display_care.wanted([_op(seen="idle")]) == []
    assert display_care.wanted([_op(state="paused"), _op(state="done")]) == []
    assert display_care.wanted([_op(system="processes,memory"), _op(system=None)]) == []
    # A schedule stored before the setting existed has no key: off.
    assert display_care.wanted([{"id": "old", "state": "pending", "payload": {"system": {"processes": True}}}]) == []


def test_sync_takes_the_inhibitor_once_and_drops_it_with_the_last_schedule(chats, holds, monkeypatch):
    assert display_care.sync(force=True) == {"held": False, "method": "", "error": ""}
    sched = _schedule(chats, system="processes,display")
    assert display_care.sync(force=True) == {"held": True, "method": "fake", "error": ""}
    assert display_care.sync(force=True)["held"] and holds.made == 1  # held: not taken again
    assert display_care.sync(now=0) is None  # not time yet
    _tick(chats, sched, _snap("idle"), monkeypatch)  # the work finished
    assert display_care.sync(force=True)["held"] is False
    _tick(chats, sched, _snap("busy"), monkeypatch)
    assert display_care.sync(force=True)["held"] and holds.made == 2
    store.set_paused(sched["id"], chats[2], True)
    assert display_care.sync(force=True)["held"] is False


def test_a_failed_take_is_reported_and_retried(chats, monkeypatch):
    monkeypatch.setattr(display_care, "_Hold", lambda: FakeHold("no supported method on this desktop session"))
    sched = _schedule(chats, system="display")
    try:
        assert display_care.sync(force=True) == {"held": False, "method": "",
                                                 "error": "no supported method on this desktop session"}
        text = _new(_tick(chats, sched, _snap("idle_with_work"), monkeypatch))["payload"]["text"]
        assert "**Display not kept awake:**" in text and "no supported method" in text
        monkeypatch.setattr(display_care, "_Hold", FakeHold)
        assert display_care.sync(force=True)["held"] is True
    finally:
        display_care.apply(False)


def test_display_off_wakes_the_supervisor_once_even_mid_turn(chats, monkeypatch):
    sched = _schedule(chats, system="display")
    first = _tick(chats, sched, _snap("busy"), monkeypatch)
    assert first == []  # display cannot be read here: nothing to say
    monkeypatch.setattr(display_care, "_display", lambda: "locked")
    checkins = _tick(chats, sched, _snap("busy"), monkeypatch)
    assert len(checkins) == 1
    text = checkins[0]["payload"]["text"]
    assert "**Display is off** (locked or showing the screen saver)" in text
    assert "keeps the display awake while the chat works" in text  # the setting is part of the job
    store.finish(checkins[0]["id"], "done", {})
    assert len(_tick(chats, sched, _snap("busy"), monkeypatch)) == 1  # same episode: already told
    monkeypatch.setattr(display_care, "_display", lambda: "on")
    assert len(_tick(chats, sched, _snap("busy"), monkeypatch)) == 1
    monkeypatch.setattr(display_care, "_display", lambda: "off")
    assert len(_tick(chats, sched, _snap("busy"), monkeypatch)) == 2  # a new episode


def test_off_reads_nothing_and_a_finished_chat_is_not_an_episode(chats, monkeypatch):
    monkeypatch.setattr(display_care, "_display", lambda: pytest.fail("read with display care off"))
    text = _new(_tick(chats, _schedule(chats), _snap("idle"), monkeypatch))["payload"]["text"]
    assert "Display is off" not in text
    assert display_care.display_state("off", {}, "idle", {})["alert"] is False
    assert display_care.display_state(None, {"error": "x"}, "busy", {})["unheld"] == "x"


def test_focus_that_asks_for_the_display_points_at_the_setting():
    assert any("System care → display" in w for w in brief.focus_warnings("Keep the monitor awake all night."))
    assert not any("display" in w for w in brief.focus_warnings("Optimize the display list builder."))


def test_display_off_section_respects_focus_and_recent_user_permission():
    lines = "\n".join(display_care.section({"off": "off"}))
    assert "Unless Focus or a recent user message to the chat allows it to wake the display itself" in lines
