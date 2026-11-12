"""Timer-scheduled detached jobs (``systemd-run --on-calendar``) under a unit watch.

systemd owns the schedule; the watch reads the unit's ``.timer``: while it waits
the job is *scheduled* (not finished, not running), the card shows its start time
with Start now, and Stop disarms the timer. Regression: a lane parked behind a
``sleep 8118`` in its script showed "Background job running" for 2 h and could
not be started early.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from api import job_watch as jw

WEB = Path(__file__).resolve().parents[2] / "web" / "js"
STARTS = 1794268320.0  # 2026-11-09 15:52 PST


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CUTTLE_CHAT_SESSION_ID", raising=False)
    (tmp_path / "home" / "output").mkdir(parents=True, exist_ok=True)


class FakeSystemd:
    """``systemctl --user`` for one service + its timer."""

    def __init__(self):
        self.service = {"ActiveState": "inactive", "SubState": "dead", "Result": "success",
                        "ExecMainStatus": "0", "MainPID": "0"}
        self.timer = {"LoadState": "loaded", "ActiveState": "active", "SubState": "waiting",
                      "NextElapseUSecRealtime": f"@{int(STARTS)}"}
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        verb, unit = args[0], next((a for a in args[1:] if not a.startswith("-")), "")
        if verb == "show":
            props = self.timer if unit.endswith(".timer") else self.service
            return 0, "\n".join(f"{k}={v}" for k, v in props.items())
        if verb == "start":
            self.service.update(ActiveState="active", SubState="running", MainPID="777")
            return 0, ""
        if verb == "stop" and unit.endswith(".timer"):
            self.timer.update(ActiveState="inactive", SubState="dead", NextElapseUSecRealtime="")
            return 0, ""
        return 0, ""


@pytest.fixture
def systemd(monkeypatch):
    fake = FakeSystemd()
    monkeypatch.setattr(jw, "_systemctl", fake)
    return fake


def _register():
    return jw.register_unit_watch(unit="ch1821-p6d", chat_session_id="CH-001821", job_id="ch1821-p6d",
                                  label="Bots P6d lane")


def test_waiting_timer_is_scheduled_not_finished(systemd):
    st = jw.unit_state("ch1821-p6d")
    assert st["running"] and st["scheduled"] and st["starts_at"] == STARTS
    assert st["sub_state"] == "scheduled" and st["timer"] == "ch1821-p6d.timer"
    info = _register()  # a scheduled unit can be watched (it is not dead)
    rec = jw.unit_watch_records()[0]
    assert jw.check_unit_watch(rec) is None  # still pending: no resume fired
    assert info["ok"]


def test_no_timer_inactive_unit_is_still_finished(systemd):
    systemd.timer.update(LoadState="not-found", ActiveState="inactive", SubState="dead")
    st = jw.unit_state("ch1821-p6d")
    assert not st["running"] and not st["scheduled"]


def test_start_now_starts_service_and_disarms_timer(systemd):
    _register()
    out = jw.start_scheduled_now("ch1821-p6d")
    assert out["ok"] and out["was_scheduled_for"] == STARTS
    assert ["start", "--no-block", "ch1821-p6d"] in systemd.calls
    assert ["stop", "ch1821-p6d.timer"] in systemd.calls
    st = jw.unit_state("ch1821-p6d")
    assert st["running"] and not st["scheduled"]
    with pytest.raises(ValueError, match="not waiting on a timer"):
        jw.start_scheduled_now("ch1821-p6d")
    with pytest.raises(LookupError):
        jw.start_scheduled_now("nope")


def test_stop_disarms_a_scheduled_job(systemd):
    _register()
    out = jw.cancel_job("ch1821-p6d")
    assert out["disarmed_timer"] == "ch1821-p6d.timer"
    assert not jw.timer_state("ch1821-p6d")["waiting"]


def test_work_tree_reports_scheduled_watch(systemd):
    from api.jobs import work_tree

    work_tree.invalidate()
    _register()
    w = work_tree._unit_watches()["1821"][0]
    assert w["scheduled"] is True and w["starts_at"] == STARTS and w["sub_state"] == "scheduled"


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_card_says_scheduled_with_start_now():
    driver = r"""
require(process.env.WEB + '/shared/progress_model.js');
require(process.env.WEB + '/chat/chat_background_work.js');
const B = globalThis.CuttleChatBackgroundWork;
const unit = { job_id: 'ch1821-p6d', kind: 'unit', label: 'Bots P6d lane', unit: 'ch1821-p6d',
  sub_state: 'scheduled', scheduled: true, starts_at: %STARTS%, started_at: %STARTS% - 8000, state: 'running' };
const p = B.plan({ watches: [unit], waits: [], schedules: [], process_count: 0 });
const running = B.plan({ watches: [{ ...unit, scheduled: false, sub_state: 'running' }], waits: [], schedules: [] });
console.log(JSON.stringify({
  title: p.title, state: p.state, started: p.started, startNow: p.startNow,
  row: { scheduled: p.watches[0].scheduled, indeterminate: p.watches[0].indeterminate, startsAt: p.watches[0].startsAt },
  health: B.jobView(unit, %STARTS% - 600).health, starts: B.startsText(%STARTS%, %STARTS% - 2820),
  runningTitle: running.title, runningState: running.state, runningStartNow: running.startNow,
}));
""".replace("%STARTS%", str(int(STARTS)))
    out = subprocess.run(["node", "-e", driver], capture_output=True, text=True, timeout=30,
                         env={"WEB": str(WEB), "PATH": "/usr/bin:/bin:/usr/local/bin", "TZ": "America/Los_Angeles"})
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["title"] == "Background job scheduled" and got["state"] == "scheduled"
    assert got["started"] is None and got["startNow"] == ["ch1821-p6d"]
    assert got["row"] == {"scheduled": True, "indeterminate": False, "startsAt": STARTS}
    assert got["health"].startswith("Not started yet. Starts ") and "in 10m" in got["health"]
    assert got["starts"].startswith("Starts 3:52") and got["starts"].endswith("in 47m")
    assert got["runningTitle"] == "Background job running" and got["runningState"] == "working"
    assert got["runningStartNow"] == []
