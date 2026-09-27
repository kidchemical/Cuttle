"""Pane-local Electron chat find helpers."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIND_JS = REPO / "src" / "web" / "js" / "chat_find.js"


def _run_node(script: str) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=20,
        cwd=str(REPO),
    )
    need_electron = proc.returncode != 0 and (
        proc.returncode == 127
        or "not recognized" in (proc.stderr or "").lower()
        or "cannot find the path" in (proc.stderr or "").lower()
    )
    if need_electron:
        electron = REPO / "electron" / "node_modules" / "electron" / "dist" / "electron.exe"
        env = os.environ.copy()
        env["ELECTRON_RUN_AS_NODE"] = "1"
        proc = subprocess.run(
            [str(electron), "-e", script],
            capture_output=True,
            text=True,
            timeout=20,
            cwd=str(REPO),
            env=env,
        )
    return proc


def test_chat_find_helpers():
    assert FIND_JS.is_file()
    js_path = json.dumps(str(FIND_JS))
    script = f"""
const f = require({js_path});
if (!f.isElectronApp('Mozilla/5.0 Electron/28.1.0', null)) process.exit(2);
if (f.isElectronApp('Mozilla/5.0 Chrome/120.0.0.0', null)) process.exit(3);
if (!f.isElectronApp('Chrome', {{ isElectron: true }})) process.exit(4);
if (f.actionFromDomEvent({{ key: 'f', ctrlKey: true }}) !== 'open') process.exit(5);
if (f.actionFromDomEvent({{ key: 'f', ctrlKey: true, altKey: true }}) !== null) process.exit(6);
if (f.actionFromDomEvent({{ key: 'F3', shiftKey: true }}) !== 'prev') process.exit(7);
if (f.actionFromElectronInput({{ type: 'keyDown', key: 'f', control: true }}) !== 'open') process.exit(8);
if (f.actionFromElectronInput({{ type: 'keyUp', key: 'f', control: true }}) !== null) process.exit(9);
const hits = f.matchOffsets('Alpha beta ALPHA', 'alpha');
if (hits.length !== 2 || hits[0].start !== 0 || hits[1].start !== 11) process.exit(10);
if (f.stepIndex(0, 1, 3) !== 1) process.exit(11);
if (f.stepIndex(2, 1, 3) !== 0) process.exit(12);
if (f.stepIndex(0, -1, 3) !== 2) process.exit(13);
console.log('ok');
"""
    proc = _run_node(script)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ok" in (proc.stdout or "")
