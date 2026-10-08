#!/usr/bin/env node
/**
 * Packaged Host end-to-end check on a clean environment.
 *
 *   node tests/packaged-host-e2e.cjs --app <unpacked or installed app dir> [--timeout 240]
 *
 * Starts the packaged Host (`--mode=host`) with a throwaway HOME and Cuttle
 * home on non-default ports, then requires:
 *   1. the packaged daemon + Flask come up on the bundled Python runtime
 *      (process executable under <app>/resources/python, never system Python),
 *   2. GET /api/health answers as Cuttle,
 *   3. the Electron window loads app_shell.html (via DevTools).
 * Every process it stops is one it started: Electron, plus processes whose
 * executable lives under this app's bundled runtime.
 *
 * Extra Electron switches (e.g. CI Xvfb: --no-sandbox --ozone-platform=x11)
 * come from CUTTLE_E2E_ELECTRON_ARGS. The app's own sandbox policy is not
 * changed.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const http = require('http');
const https = require('https');
const { spawn, spawnSync } = require('child_process');

function arg(name, fallback) {
    const i = process.argv.indexOf(name);
    return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const appDir = path.resolve(arg('--app', ''));
const timeoutMs = Number(arg('--timeout', '240')) * 1000;
const isWin = process.platform === 'win32';
const PORTS = { https: 18443, http: 18000, phone: 18888, devtools: 19222 };

function log(msg) {
    process.stdout.write(`[packaged-e2e] ${msg}\n`);
}

function fail(msg) {
    log(`FAIL: ${msg}`);
    process.exitCode = 1;
}

function exePath() {
    if (isWin) return path.join(appDir, 'Cuttle.exe');
    for (const name of ['cuttle-desktop', 'cuttle', 'Cuttle']) {
        const p = path.join(appDir, name);
        if (fs.existsSync(p)) return p;
    }
    return path.join(appDir, 'cuttle-desktop');
}

const bundledRoot = path.join(appDir, 'resources', 'python');

function getJson(url, timeout = 4000) {
    return new Promise((resolve, reject) => {
        const lib = url.startsWith('https:') ? https : http;
        // Loopback only: the Host's own self-signed certificate.
        const req = lib.get(url, { rejectUnauthorized: false, timeout }, (res) => {
            let body = '';
            res.on('data', (c) => { body += c; });
            res.on('end', () => {
                try {
                    resolve({ status: res.statusCode, json: JSON.parse(body || '{}'), body });
                } catch (_) {
                    resolve({ status: res.statusCode, json: null, body });
                }
            });
        });
        req.on('timeout', () => req.destroy(new Error('timeout')));
        req.on('error', reject);
    });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(label, fn) {
    const deadline = Date.now() + timeoutMs;
    let last = '';
    while (Date.now() < deadline) {
        try {
            const v = await fn();
            if (v) return v;
        } catch (err) {
            last = err && err.message ? err.message : String(err);
        }
        await sleep(1000);
    }
    throw new Error(`${label} not ready within ${timeoutMs / 1000}s${last ? ` (last error: ${last})` : ''}`);
}

/** [{pid, exe, cmd}] for processes running from this app's bundled runtime. */
function bundledProcesses() {
    const out = [];
    const root = path.resolve(bundledRoot).toLowerCase();
    if (isWin) {
        const ps = spawnSync('powershell', ['-NoProfile', '-Command',
            'Get-CimInstance Win32_Process | Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress'],
        { encoding: 'utf8', windowsHide: true });
        let rows = [];
        try { rows = JSON.parse(ps.stdout || '[]'); } catch (_) {}
        for (const r of Array.isArray(rows) ? rows : [rows]) {
            const exe = String(r.ExecutablePath || '');
            if (exe && path.resolve(exe).toLowerCase().startsWith(root)) {
                out.push({ pid: Number(r.ProcessId), exe, cmd: String(r.CommandLine || '') });
            }
        }
        return out;
    }
    for (const pid of fs.readdirSync('/proc').filter((d) => /^\d+$/.test(d))) {
        try {
            const exe = fs.readlinkSync(`/proc/${pid}/exe`);
            if (path.resolve(exe).toLowerCase().startsWith(root)) {
                const cmd = fs.readFileSync(`/proc/${pid}/cmdline`, 'utf8').split('\0').join(' ').trim();
                out.push({ pid: Number(pid), exe, cmd });
            }
        } catch (_) {}
    }
    return out;
}

function killTree(pid, signal = 'SIGTERM') {
    if (!pid) return;
    try {
        if (isWin) spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { windowsHide: true });
        else process.kill(pid, signal);
    } catch (_) {}
}

/** Stop the Electron we launched (its own process group on POSIX). */
async function stopElectron(child, isExited) {
    if (isWin) {
        killTree(child.pid);
    } else {
        try { process.kill(child.pid, 'SIGTERM'); } catch (_) {}
        for (let i = 0; i < 50 && !isExited(); i++) await sleep(100);
        if (!isExited()) {
            fail('packaged Electron did not exit within 5 seconds after SIGTERM');
            try { process.kill(-child.pid, 'SIGKILL'); } catch (_) {}
        }
    }
    child.unref();
    if (!isWin && isExited()) log('packaged Electron exited after SIGTERM');
}

async function main() {
    if (!appDir || !fs.existsSync(exePath())) throw new Error(`packaged app not found: ${exePath()}`);
    const pyExe = path.join(bundledRoot, ...(isWin ? ['python.exe'] : ['bin', 'python3']));
    if (!fs.existsSync(pyExe)) throw new Error(`bundled Python missing: ${pyExe}`);

    const scratch = path.join(__dirname, '..', '..', 'temp');
    fs.mkdirSync(scratch, { recursive: true });
    const root = fs.mkdtempSync(path.join(scratch, 'packaged-e2e-'));
    const home = path.join(root, 'home');
    const cuttleHome = path.join(root, 'cuttle-home');
    fs.mkdirSync(home, { recursive: true });
    fs.mkdirSync(cuttleHome, { recursive: true });
    fs.writeFileSync(path.join(cuttleHome, '.env'), [
        `CUTTLE_HTTPS_PORT=${PORTS.https}`,
        `CUTTLE_HTTP_PORT=${PORTS.http}`,
        `CUTTLE_PHONE_HTTPS_PORT=${PORTS.phone}`,
        '',
    ].join('\n'));

    // Clean environment: no inherited Python configuration, no user venv on
    // PATH, private HOME/profile dirs and Cuttle home. Preserve XAUTHORITY:
    // xvfb-run stores its display cookie outside the private HOME.
    const env = {};
    for (const key of ['DISPLAY', 'XAUTHORITY', 'WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR', 'SystemRoot', 'SYSTEMROOT', 'windir',
        'ComSpec', 'PATHEXT', 'TEMP', 'TMP', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE']) {
        if (process.env[key]) env[key] = process.env[key];
    }
    env.PATH = isWin
        ? [path.join(process.env.SystemRoot || 'C:\\Windows', 'System32'), process.env.SystemRoot || 'C:\\Windows',
            path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0')].join(';')
        : '/usr/bin:/bin';
    env.HOME = home;
    env.CUTTLE_HOME = cuttleHome;
    env.XDG_CONFIG_HOME = path.join(home, '.config');
    env.XDG_DATA_HOME = path.join(home, '.local', 'share');
    env.XDG_STATE_HOME = path.join(home, '.local', 'state');
    env.XDG_CACHE_HOME = path.join(home, '.cache');
    if (isWin) {
        env.USERPROFILE = home;
        env.APPDATA = path.join(home, 'AppData', 'Roaming');
        env.LOCALAPPDATA = path.join(home, 'AppData', 'Local');
        fs.mkdirSync(env.APPDATA, { recursive: true });
        fs.mkdirSync(env.LOCALAPPDATA, { recursive: true });
    }
    env.CUTTLE_NO_TRAY = '1';
    env.ELECTRON_ENABLE_LOGGING = '1';

    const extra = String(process.env.CUTTLE_E2E_ELECTRON_ARGS || '').split(/\s+/).filter(Boolean);
    const args = ['--mode=host', `--remote-debugging-port=${PORTS.devtools}`, ...extra];
    log(`launching ${exePath()} ${args.join(' ')}`);
    const logFile = path.join(root, 'electron.log');
    const logFd = fs.openSync(logFile, 'a');
    // POSIX: own process group, so teardown reaches Electron's helpers too.
    const child = spawn(exePath(), args, {
        env, stdio: ['ignore', logFd, logFd], windowsHide: true, detached: !isWin,
    });
    let exited = null;
    child.on('exit', (code, signal) => { exited = { code, signal }; });

    const result = { app: appDir, ports: PORTS };
    try {
        const health = await waitFor('/api/health', async () => {
            if (exited) throw new Error(`Electron exited early ${JSON.stringify(exited)}`);
            const r = await getJson(`https://127.0.0.1:${PORTS.https}/api/health`);
            return r.status === 200 && r.json && (r.json.status || r.json.service) ? r.json : null;
        });
        result.health = { status: health.status || health.service };
        log(`health ok: ${JSON.stringify(result.health)}`);

        const procs = bundledProcesses();
        result.bundledProcesses = procs.map((p) => p.cmd.slice(0, 160));
        const daemon = procs.find((p) => /cuttle_daemon\.py/.test(p.cmd));
        const flask = procs.find((p) => /web_chat_api\.py/.test(p.cmd));
        if (!daemon) fail('packaged daemon is not running on the bundled Python runtime');
        if (!flask) fail('packaged Flask backend is not running on the bundled Python runtime');
        log(`bundled runtime processes: ${procs.length}`);

        const page = await waitFor('UI page', async () => {
            const r = await getJson(`http://127.0.0.1:${PORTS.devtools}/json/list`);
            const pages = Array.isArray(r.json) ? r.json : [];
            return pages.find((p) => p.type === 'page' && /\/app_shell\.html/.test(p.url || '')) || null;
        });
        result.uiUrl = page.url;
        log(`UI loaded: ${page.url}`);
        const shell = await getJson(page.url.replace(/[#?].*$/, ''));
        if (shell.status !== 200 || !/<html/i.test(shell.body || '')) fail(`app_shell.html returned ${shell.status}`);
    } catch (err) {
        fail(err && err.message ? err.message : String(err));
    } finally {
        await stopElectron(child, () => !!exited);
        await sleep(1000);
        for (const p of bundledProcesses()) killTree(p.pid);
        await sleep(1500);
        for (const p of bundledProcesses()) killTree(p.pid, 'SIGKILL');
        fs.closeSync(logFd);
        if (process.exitCode) {
            log('--- electron log (tail) ---');
            const text = fs.readFileSync(logFile, 'utf8');
            process.stdout.write(text.slice(-6000));
            const logsDir = path.join(cuttleHome, 'logs');
            if (fs.existsSync(logsDir)) {
                for (const f of fs.readdirSync(logsDir)) {
                    const p = path.join(logsDir, f);
                    if (fs.statSync(p).isFile()) {
                        log(`--- ${f} (tail) ---`);
                        process.stdout.write(fs.readFileSync(p, 'utf8').slice(-3000));
                    }
                }
            }
        }
        log(JSON.stringify(result));
        if (!process.env.CUTTLE_E2E_KEEP) fs.rmSync(root, { recursive: true, force: true });
    }
}

main().catch((err) => {
    fail(err && err.message ? err.message : String(err));
});
