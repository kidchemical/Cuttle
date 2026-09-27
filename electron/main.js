const { app, BrowserWindow, Menu, dialog, Tray, ipcMain, nativeImage } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');
const net = require('net');
const http = require('http'); // Keep http for non-Cuttle requests if any, though likely will convert all to https
const https = require('https'); // Import https module
const crypto = require('crypto');

// Chromium: allow invalid certs for localhost only (covers WSS; certificate-error
// alone is unreliable for WebSockets on some Electron builds).
app.commandLine.appendSwitch('allow-insecure-localhost');

let mainWindow;
let tray;
let daemonProcess = null;
let spawnedDaemonPid = null;
// Only tray Exit may stop the daemon. Signals, updates, and relaunches close the
// UI and leave the daemon (and every running agent turn) alone.
let stopDaemonOnQuit = false;
let workerSidecarProcess = null;
// HTTPS :8080 is the primary Flask listener. Electron used to load that directly,
// but Chromium's HTTP/1.1 ~6-connection-per-host limit + long chat SSE streams
// regularly wedges the desktop app while LAN/browser (separate pool) still works.
// Prefer plain HTTP :8000 (same Flask app) so Electron stays off the TLS pool.
let FLASK_HTTPS_PORT = 8080;
let FLASK_HTTP_PORT = 8000;
let FLASK_HOST = '127.0.0.1';
let CLIENT_MODE = false;
const PROJECT_ROOT = path.join(__dirname, '..');
const SRC_ROOT = path.join(PROJECT_ROOT, 'src');
// package.json is omitted: electron-builder rewrites it inside app.asar, which
// made the host always look "stale" even after a fresh build.
const DESKTOP_HASH_FILES = ['connect.html', 'main.js', 'preload.js'];
const DEFAULT_HTTP_PORT = 8000;
const DEFAULT_HTTPS_PORT = 8080;

let desktopUpdateAvailable;
try {
    desktopUpdateAvailable = require(path.join(__dirname, '..', 'src', 'web', 'js', 'desktop_update_policy.js')).desktopUpdateAvailable;
} catch (_) {
    desktopUpdateAvailable = function (remote, localHash, opts) {
        const o = opts || {};
        const h = String(o.host || '').toLowerCase().replace(/^\[|\]$/g, '');
        const loopback = h === '127.0.0.1' || h === 'localhost' || h === '::1' || h === '';
        return !!(
            o.packaged
            && o.clientMode
            && !loopback
            && remote
            && remote.ok
            && remote.hash
            && remote.artifact
            && remote.hash !== localHash
        );
    };
}

function configPath() {
    return path.join(app.getPath('userData'), 'desktop-config.json');
}

function loadDesktopConfig() {
    try {
        const raw = fs.readFileSync(configPath(), 'utf8');
        const data = JSON.parse(raw);
        return data && typeof data === 'object' ? data : {};
    } catch (_) {
        return {};
    }
}

function saveDesktopConfig(patch) {
    const next = { ...loadDesktopConfig(), ...patch, updatedAt: new Date().toISOString() };
    fs.mkdirSync(app.getPath('userData'), { recursive: true });
    fs.writeFileSync(configPath(), JSON.stringify(next, null, 2));
    return next;
}

function applyDesktopTarget({ host, httpPort, httpsPort, clientMode }) {
    FLASK_HOST = host || '127.0.0.1';
    FLASK_HTTP_PORT = Number(httpPort) || DEFAULT_HTTP_PORT;
    FLASK_HTTPS_PORT = Number(httpsPort) || DEFAULT_HTTPS_PORT;
    CLIENT_MODE = !!clientMode;
}

function localDesktopHash() {
    const h = crypto.createHash('sha256');
    for (const name of DESKTOP_HASH_FILES) {
        h.update(name);
        h.update('\0');
        try {
            h.update(fs.readFileSync(path.join(__dirname, name)));
        } catch (_) {}
        h.update('\0');
    }
    return h.digest('hex').slice(0, 20);
}

function parseArgValue(flag) {
    const argv = process.argv.slice(1);
    const eq = argv.find((a) => a.startsWith(`${flag}=`));
    if (eq) return eq.slice(flag.length + 1);
    const idx = argv.indexOf(flag);
    if (idx >= 0 && argv[idx + 1] && !argv[idx + 1].startsWith('-')) return argv[idx + 1];
    return null;
}

function desktopLaunchMode() {
    const m = String(parseArgValue('--mode') || '').toLowerCase();
    if (m === 'host' || m === 'local') return 'host';
    if (m === 'client') return 'client';
    return '';
}

function parseHostInput(raw) {
    const text = String(raw || '').trim();
    if (!text) throw new Error('Enter a host address.');
    let host = text;
    let httpPort = DEFAULT_HTTP_PORT;
    let httpsPort = DEFAULT_HTTPS_PORT;
    try {
        if (text.includes('://') || text.includes('/')) {
            const u = new URL(text.includes('://') ? text : `http://${text}`);
            host = u.hostname;
            if (u.port) {
                const p = Number(u.port);
                if (u.protocol === 'https:') httpsPort = p;
                else httpPort = p;
            }
        } else if (text.includes(':')) {
            const parts = text.split(':');
            host = parts[0];
            const p = Number(parts[1]);
            if (Number.isFinite(p) && p > 0) httpPort = p;
        }
    } catch (err) {
        throw new Error('That does not look like a host or URL.');
    }
    host = (host || '').replace(/^\[|\]$/g, '').trim();
    if (!host) throw new Error('Enter a host address.');
    return { host, httpPort, httpsPort };
}

function jsonRequest(url, { timeoutMs = 4000, method = 'GET', body = null, headers = null } = {}) {
    return new Promise((resolve, reject) => {
        let parsed;
        try {
            parsed = new URL(url);
        } catch (err) {
            reject(err);
            return;
        }
        const lib = parsed.protocol === 'https:' ? https : http;
        const payload = body == null ? null : (typeof body === 'string' ? body : JSON.stringify(body));
        const reqHeaders = Object.assign(
            {},
            headers || {},
            payload != null ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) } : {}
        );
        const req = lib.request(
            {
                hostname: parsed.hostname,
                port: parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
                path: `${parsed.pathname}${parsed.search}`,
                method: method || 'GET',
                headers: reqHeaders,
                rejectUnauthorized: false,
                timeout: timeoutMs,
            },
            (res) => {
                let buf = '';
                res.on('data', (c) => { buf += c; });
                res.on('end', () => {
                    try {
                        resolve({ status: res.statusCode || 0, json: JSON.parse(buf || '{}') });
                    } catch (err) {
                        reject(new Error('Host did not return Cuttle JSON.'));
                    }
                });
            }
        );
        req.on('timeout', () => {
            req.destroy();
            reject(new Error('Timed out reaching host.'));
        });
        req.on('error', reject);
        if (payload != null) req.write(payload);
        req.end();
    });
}

function looksLikeCuttle(json) {
    if (!json || typeof json !== 'object') return false;
    return json.service === 'cuttle' || json.service === 'cuttle-desktop' || json.status === 'healthy' || json.status === 'ok' || json.ok === true;
}

async function probeCuttle(host, httpPort, httpsPort) {
    const httpUrl = `http://${host}:${httpPort}/api/health`;
    try {
        const r = await jsonRequest(httpUrl);
        if (r.status >= 200 && r.status < 500 && looksLikeCuttle(r.json)) {
            return { ok: true, host, httpPort, httpsPort, uiUrl: `http://${host}:${httpPort}/app_shell.html` };
        }
    } catch (_) {}
    const httpsUrl = `https://${host}:${httpsPort}/api/health`;
    try {
        const r = await jsonRequest(httpsUrl);
        if (r.status >= 200 && r.status < 500 && looksLikeCuttle(r.json)) {
            return { ok: true, host, httpPort, httpsPort, uiUrl: `https://${host}:${httpsPort}/app_shell.html` };
        }
    } catch (err) {
        return { ok: false, error: err.message || 'Could not reach Cuttle on that host.' };
    }
    return { ok: false, error: 'No Cuttle server responded at that address.' };
}

function connectPageUrl() {
    return `file://${path.join(__dirname, 'connect.html').replace(/\\/g, '/')}`;
}

function resolvePackageVersion() {
    // Live checkout beats packaged asar. Host hub runs dist/win-unpacked/Cuttle.exe
    // whose app.asar package.json is frozen at last electron-builder pack.
    const envVer = String(process.env.CUTTLE_PACKAGE_VERSION || '').trim();
    if (envVer && envVer !== '0.0.0') return envVer;

    const candidates = [];
    try {
        const exe = String(process.execPath || '');
        if (/dist[\\/]+win-unpacked/i.test(exe) || /dist[\\/]+linux-unpacked/i.test(exe)) {
            // .../electron/dist/{win,linux}-unpacked/Cuttle → .../electron/package.json
            candidates.push(path.join(path.dirname(exe), '..', '..', 'package.json'));
        }
    } catch (_) {}
    try {
        candidates.push(path.join(PROJECT_ROOT, 'electron', 'package.json'));
    } catch (_) {}
    try {
        candidates.push(path.join(__dirname, 'package.json'));
    } catch (_) {}

    for (const pkgPath of candidates) {
        try {
            if (!pkgPath || !fs.existsSync(pkgPath)) continue;
            const ver = String(JSON.parse(fs.readFileSync(pkgPath, 'utf8')).version || '').trim();
            if (ver) return ver;
        } catch (_) {}
    }
    try {
        return String(app.getVersion() || '0.0.0');
    } catch (_) {
        return '0.0.0';
    }
}

function publicDesktopConfig() {
    const cfg = loadDesktopConfig();
    const packageVersion = resolvePackageVersion();
    // Effective mesh id: persisted enroll id, else hostname (same rule as sidecar).
    // Never leave this empty in Client mode — Jobs "you" must not fall back to host.
    let workerId = String(cfg.workerId || '').trim();
    if (!workerId) {
        workerId = String(os.hostname() || '').toLowerCase().replace(/\s+/g, '-');
    }
    return {
        mode: CLIENT_MODE ? 'client' : 'local',
        clientMode: CLIENT_MODE,
        workerMode: cfg.workerMode !== false,
        workerId,
        hostname: String(os.hostname() || '').trim(),
        host: FLASK_HOST,
        httpPort: FLASK_HTTP_PORT,
        httpsPort: FLASK_HTTPS_PORT,
        packaged: app.isPackaged,
        hash: localDesktopHash(),
        packageVersion,
        lastError: cfg.lastError || '',
    };
}

function cuttleAppUrl(pathname = '/app_shell.html') {
    const pathPart = pathname.startsWith('/') ? pathname : `/${pathname}`;
    return `http://${FLASK_HOST}:${FLASK_HTTP_PORT}${pathPart}`;
}

function cuttleHttpsAppUrl(pathname = '/app_shell.html') {
    const pathPart = pathname.startsWith('/') ? pathname : `/${pathname}`;
    return `https://${FLASK_HOST}:${FLASK_HTTPS_PORT}${pathPart}`;
}

// Resolve a Python executable that can be spawned as a subprocess.
// Priority: project .venv > Cuttle dev .venv (packaged fallback) > python3.13 > python
function _isUsablePythonExe(exePath) {
    if (!exePath || !fs.existsSync(exePath)) return false;
    const lower = String(exePath).toLowerCase();
    // Windows Store stub aliases exit immediately with code 1
    if (lower.includes('\\windowsapps\\')) return false;
    return true;
}

function resolvePythonExe(projectRoot) {
    const candidates = [];
    const isWin = process.platform === 'win32';
    // 1. Project-local venv (Windows Scripts/ vs POSIX bin/)
    if (isWin) {
        candidates.push(path.join(projectRoot, '.venv', 'Scripts', 'python.exe'));
    } else {
        candidates.push(
            path.join(projectRoot, '.venv', 'bin', 'python3'),
            path.join(projectRoot, '.venv', 'bin', 'python'),
        );
    }
    // 2. When packaged, walk up from exe dir to find the repo .venv
    if (app.isPackaged) {
        const exeDir = path.dirname(app.getPath('exe'));
        if (isWin) {
            candidates.push(
                path.join(exeDir, '..', '..', '..', '..', '.venv', 'Scripts', 'python.exe'),
                path.join(exeDir, '..', '..', '..', '.venv', 'Scripts', 'python.exe'),
            );
        } else {
            candidates.push(
                path.join(exeDir, '..', '..', '..', '..', '.venv', 'bin', 'python3'),
                path.join(exeDir, '..', '..', '..', '.venv', 'bin', 'python3'),
            );
        }
    }
    if (isWin) {
        // 3. Common Windows installs (never WindowsApps stubs)
        const local = process.env.LOCALAPPDATA || '';
        const pf = process.env.ProgramFiles || 'C:\\Program Files';
        for (const ver of ['Python311', 'Python312', 'Python313', 'Python310', 'Python39']) {
            candidates.push(path.join(local, 'Programs', 'Python', ver, 'python.exe'));
            candidates.push(path.join(pf, ver, 'python.exe'));
        }
    } else {
        candidates.push('/usr/bin/python3', '/usr/local/bin/python3');
    }
    for (const cand of candidates) {
        const norm = path.normalize(cand);
        if (_isUsablePythonExe(norm)) {
            console.log('Using Python:', norm);
            return norm;
        }
    }
    // 4. Last resort: launcher / PATH
    try {
        const { spawnSync } = require('child_process');
        if (isWin) {
            const r = spawnSync('py', ['-3', '-c', 'import sys; print(sys.executable)'], {
                encoding: 'utf8',
                windowsHide: true,
                timeout: 5000,
            });
            const exe = String(r.stdout || '').trim().split(/\r?\n/).pop();
            if (r.status === 0 && _isUsablePythonExe(exe)) {
                console.log('Using Python via py -3:', exe);
                return exe;
            }
        } else {
            const r = spawnSync('python3', ['-c', 'import sys; print(sys.executable)'], {
                encoding: 'utf8',
                timeout: 5000,
            });
            const exe = String(r.stdout || '').trim().split(/\r?\n/).pop();
            if (r.status === 0 && _isUsablePythonExe(exe)) {
                console.log('Using Python via python3:', exe);
                return exe;
            }
        }
    } catch (_) {}
    console.error('No usable Python found for daemon / device worker sidecar');
    return null;
}

/** Copy asar/dev device-worker script into userData so Python can execute it. */
function materializeDeviceWorkerScript() {
    const destDir = path.join(app.getPath('userData'), 'device-worker');
    const destScript = path.join(destDir, 'cuttle_device_worker.py');
    const sources = [
        path.join(__dirname, 'device-worker', 'cuttle_device_worker.py'),
        path.join(__dirname, '..', 'src', 'scripts', 'cuttle_device_worker.py'),
    ];
    let src = null;
    for (const cand of sources) {
        try {
            if (fs.existsSync(cand)) {
                src = cand;
                break;
            }
        } catch (_) {}
    }
    if (!src) {
        return { ok: false, error: 'device-worker script missing from Electron bundle' };
    }
    try {
        fs.mkdirSync(destDir, { recursive: true });
        const body = fs.readFileSync(src);
        let previous = null;
        try { previous = fs.readFileSync(destScript); } catch (_) {}
        if (!previous || Buffer.compare(previous, body) !== 0) {
            fs.writeFileSync(destScript, body);
        }
        return { ok: true, scriptPath: destScript, source: src };
    } catch (err) {
        return { ok: false, error: err.message || String(err) };
    }
}

// Poll for a TCP listener. Resolves true when up, false after all retries exhausted.
function waitForPort(port, retries = 45, intervalMs = 1000, label = 'service', host = FLASK_HOST) {
    return new Promise((resolve) => {
        let attempts = 0;
        function tryConnect() {
            const socket = new net.Socket();
            let resolved = false;
            socket.setTimeout(800);
            socket.on('connect', () => {
                if (!resolved) { resolved = true; socket.destroy(); resolve(true); }
            });
            socket.on('error', () => {
                if (!resolved) {
                    resolved = true;
                    socket.destroy();
                    attempts++;
                    if (attempts < retries) {
                        console.log(`${label} not ready yet (attempt ${attempts}/${retries})...`);
                        setTimeout(tryConnect, intervalMs);
                    } else {
                        resolve(false);
                    }
                }
            });
            socket.on('timeout', () => {
                if (!resolved) {
                    resolved = true;
                    socket.destroy();
                    attempts++;
                    if (attempts < retries) {
                        setTimeout(tryConnect, intervalMs);
                    } else {
                        resolve(false);
                    }
                }
            });
            socket.connect(port, host);
        }
        tryConnect();
    });
}

function waitForFlask(retries = 45, intervalMs = 1000) {
    // HTTPS comes up with the main listener; HTTP portal is preferred for the UI.
    return waitForPort(FLASK_HTTPS_PORT, retries, intervalMs, 'Flask');
}

/** Host-only: ask Flask to drop expired /output/shared/ media (7d TTL). */
async function purgeSharedMediaExpired() {
    if (CLIENT_MODE) return;
    try {
        const httpsUrl = `https://${FLASK_HOST}:${FLASK_HTTPS_PORT}/api/shared-media/purge`;
        const r = await jsonRequest(httpsUrl, { method: 'POST', timeoutMs: 8000 });
        if (r.status >= 200 && r.status < 300 && r.json && r.json.success) {
            const n = r.json.deleted || 0;
            if (n) console.log(`Shared media purge: deleted ${n} expired file(s)`);
            return;
        }
    } catch (err) {
        console.warn('Shared media purge (HTTPS) failed:', err && err.message ? err.message : err);
    }
    try {
        const httpUrl = `http://${FLASK_HOST}:${FLASK_HTTP_PORT}/api/shared-media/purge`;
        const r = await jsonRequest(httpUrl, { method: 'POST', timeoutMs: 8000 });
        if (r.status >= 200 && r.status < 300 && r.json && r.json.success) {
            const n = r.json.deleted || 0;
            if (n) console.log(`Shared media purge (HTTP): deleted ${n} expired file(s)`);
        }
    } catch (err2) {
        console.warn('Shared media purge skipped:', err2 && err2.message ? err2.message : err2);
    }
}

async function resolveUiBaseUrl() {
    const httpUp = await waitForPort(FLASK_HTTP_PORT, 5, 400, 'HTTP portal');
    if (httpUp) {
        console.log(`Using HTTP portal http://${FLASK_HOST}:${FLASK_HTTP_PORT}`);
        return cuttleAppUrl('/app_shell.html');
    }
    console.warn(`HTTP :${FLASK_HTTP_PORT} not up — falling back to HTTPS :${FLASK_HTTPS_PORT}`);
    return cuttleHttpsAppUrl('/app_shell.html');
}

// Spawn the Cuttle daemon (detached so it survives Electron being closed).
// The daemon manages Flask, Discord bot, cron, hot-reload, and its own tray icon.
function startDaemon() {
    if (process.env.CUTTLE_HOSTED_BY_DAEMON === '1') {
        console.log('Already launched from the Cuttle daemon — not spawning another Python process.');
        return;
    }

    const isDev = !app.isPackaged;
    const projectRoot = isDev
        ? path.join(__dirname, '..')
        : path.join(process.resourcesPath, 'app');

    const pythonExe = resolvePythonExe(projectRoot);
    if (!pythonExe) {
        console.error('Cannot start Cuttle daemon: no usable Python executable.');
        return;
    }
    const scriptPath = path.join(projectRoot, 'src', 'scripts', 'cuttle_daemon.py');
    if (!fs.existsSync(scriptPath)) {
        console.error('Daemon script missing, not spawning Python:', scriptPath);
        return;
    }

    console.log('Starting Cuttle daemon:', scriptPath);

    const env = {
        ...process.env,
        PYTHONPATH: path.join(projectRoot, 'src'),
        // Host/Client already created the window + tray. Do not spawn a second
        // Electron (different userData → second instance lock) or pystray icon.
        CUTTLE_NO_UI: '1',
        CUTTLE_NO_TRAY: '1',
    };

    // detached + unref: daemon outlives Electron (survives window close)
    // windowsHide: suppress the extra blank python console on Windows.
    try {
        daemonProcess = spawn(pythonExe, [scriptPath], {
            cwd: projectRoot,
            env,
            detached: true,
            stdio: 'ignore',
            windowsHide: true,
        });
        spawnedDaemonPid = daemonProcess.pid;
        daemonProcess.unref();
        daemonProcess = null; // we no longer own stdio; PID is kept for host Exit
        console.log('Daemon spawned (detached, pid=' + spawnedDaemonPid + '). Waiting for Flask...');
    } catch (err) {
        console.error('Failed to spawn Cuttle daemon:', err && err.message ? err.message : err);
        daemonProcess = null;
    }
}

/**
 * Client-mode device worker sidecar (CUTTLE_WORKERS.md).
 * Host/local mode already runs a local worker inside cuttle_daemon — do not double-spawn.
 * Default: enabled when Client unless desktop-config workerMode === false.
 *
 * Auth: auto-enroll with the host (same trust as Client UI on LAN). No manual token.
 */
async function enrollWorkerWithHost() {
    const cfg = loadDesktopConfig();
    const workerId = String(cfg.workerId || os.hostname() || 'cuttle-client').toLowerCase().replace(/\s+/g, '-');
    const coordinatorHttps = `https://${FLASK_HOST}:${FLASK_HTTPS_PORT}`;
    const coordinatorHttp = `http://${FLASK_HOST}:${FLASK_HTTP_PORT}`;
    const body = { worker_id: workerId, hostname: os.hostname() };
    const headers = {};
    if (cfg.workerToken) {
        headers.Authorization = `Bearer ${cfg.workerToken}`;
    }

    async function tryEnroll(base) {
        const r = await jsonRequest(`${base}/api/workers/enroll`, {
            method: 'POST',
            body,
            headers,
            timeoutMs: 8000,
        });
        if (r.status >= 200 && r.status < 300 && r.json && r.json.success && r.json.token) {
            return r.json;
        }
        const err = (r.json && r.json.error) || `HTTP ${r.status}`;
        throw new Error(err);
    }

    try {
        return await tryEnroll(coordinatorHttps);
    } catch (httpsErr) {
        console.warn('Worker enroll via HTTPS failed, trying HTTP:', httpsErr.message || httpsErr);
        return await tryEnroll(coordinatorHttp);
    }
}

async function startWorkerSidecar() {
    if (!CLIENT_MODE) return;
    const cfg = loadDesktopConfig();
    if (cfg.workerMode === false) {
        console.log('Device worker sidecar disabled (workerMode=false).');
        return;
    }

    // Prefer cuttle_client_daemon when it is already owning the worker loop
    // (safe self-update / git pull benchmark — Electron must not double-claim).
    try {
        const statusFile = path.join(app.getPath('userData'), 'client-daemon-status.json');
        if (fs.existsSync(statusFile)) {
            const st = JSON.parse(fs.readFileSync(statusFile, 'utf8'));
            const age = Date.now() / 1000 - Number(st.updated_at || 0);
            if (st.state === 'running' && age >= 0 && age < 45 && st.owns_worker) {
                console.log(
                    'Client daemon owns device worker (skip Electron sidecar). pid=',
                    st.pid
                );
                return;
            }
        }
    } catch (err) {
        console.warn('client-daemon status check failed:', err.message || err);
    }

    let workerToken = cfg.workerToken || '';
    let workerId = cfg.workerId || '';
    try {
        const enrolled = await enrollWorkerWithHost();
        workerToken = enrolled.token;
        workerId = enrolled.worker_id || workerId;
        saveDesktopConfig({
            workerToken,
            workerId,
            workerEnrolledAt: Date.now(),
        });
        console.log('Device worker enrolled with host as', workerId);
    } catch (err) {
        console.error('Device worker auto-enroll failed:', err.message || err);
        if (!workerToken) {
            console.error('No enrolled token — sidecar not started. Reconnect Client to retry.');
            return;
        }
        console.warn('Using previously saved worker token.');
    }

    if (workerSidecarProcess && !workerSidecarProcess.killed) {
        try { workerSidecarProcess.kill(); } catch (_) {}
        workerSidecarProcess = null;
    }

    const isDev = !app.isPackaged;
    const projectRoot = isDev
        ? path.join(__dirname, '..')
        : path.join(process.resourcesPath, 'app');
    const pythonExe = resolvePythonExe(projectRoot);
    if (!pythonExe) {
        console.error(
            'Device worker sidecar needs a real Python install ' +
            '(e.g. python.org 3.11+). Windows Store python stubs will not work.'
        );
        return;
    }

    const materialized = materializeDeviceWorkerScript();
    if (!materialized.ok) {
        console.error('Device worker script missing:', materialized.error);
        return;
    }
    const scriptPath = materialized.scriptPath;
    console.log('Device worker script:', scriptPath, '(from', materialized.source + ')');

    const coordinatorHttps = `https://${FLASK_HOST}:${FLASK_HTTPS_PORT}`;
    const coordinatorHttp = `http://${FLASK_HOST}:${FLASK_HTTP_PORT}`;
    const logPath = path.join(app.getPath('userData'), 'device-worker.log');
    let desktopVersion = '';
    try {
        desktopVersion = String(publicDesktopConfig().packageVersion || '');
    } catch (_) {}
    const env = {
        ...process.env,
        PYTHONUTF8: '1',
        PYTHONIOENCODING: 'utf-8',
        CUTTLE_DEVICE_WORKERS_ENABLED: '1',
        CUTTLE_DEVICE_WORKERS_COORDINATOR_URL: coordinatorHttps,
        CUTTLE_DEVICE_WORKERS_COORDINATOR_URL_HTTP: coordinatorHttp,
        CUTTLE_DEVICE_WORKERS_TOKEN: String(workerToken),
        CUTTLE_DEVICE_WORKER_ID: String(workerId || os.hostname()),
        CUTTLE_DEVICE_WORKER_LOG: logPath,
        CUTTLE_PACKAGE_VERSION: desktopVersion,
        CUTTLE_REPO_ROOT: projectRoot,
        CUTTLE_FLASK_HOST: String(FLASK_HOST || ''),
    };

    console.log('Starting device worker sidecar →', coordinatorHttps, '(http fallback', coordinatorHttp + ')');
    console.log('Device worker log:', logPath);

    // Use an opened fd — createWriteStream is not valid for spawn stdio until 'open'
    // (fd is null), which throws ERR_INVALID_ARG_VALUE on Electron/Node.
    let logFd = null;
    try {
        fs.mkdirSync(path.dirname(logPath), { recursive: true });
        logFd = fs.openSync(logPath, 'a');
        fs.writeSync(
            logFd,
            `\n---- sidecar spawn ${new Date().toISOString()} python=${pythonExe} ----\n`
        );
        workerSidecarProcess = spawn(pythonExe, [scriptPath], {
            cwd: path.dirname(scriptPath),
            env,
            stdio: ['ignore', logFd, logFd],
            windowsHide: true,
        });
    } catch (err) {
        console.error('Device worker sidecar spawn failed:', err.message || err);
        if (logFd != null) {
            try { fs.closeSync(logFd); } catch (_) {}
        }
        workerSidecarProcess = null;
        return;
    }

    workerSidecarProcess.on('exit', (code, signal) => {
        console.log(`Device worker sidecar exited code=${code} signal=${signal}`);
        if (code && code !== 0) {
            console.error(`See worker log: ${logPath}`);
        }
        if (logFd != null) {
            try { fs.closeSync(logFd); } catch (_) {}
            logFd = null;
        }
        workerSidecarProcess = null;
    });
    workerSidecarProcess.on('error', (err) => {
        console.error('Device worker sidecar spawn error:', err);
        if (logFd != null) {
            try { fs.closeSync(logFd); } catch (_) {}
            logFd = null;
        }
        workerSidecarProcess = null;
    });
}

function stopWorkerSidecar() {
    if (!workerSidecarProcess) return;
    try {
        workerSidecarProcess.kill();
    } catch (_) {}
    workerSidecarProcess = null;
}

// Graph pipelines removed — chat uses slash agents + the router.
function autoStartPipeline() {}

// Create system tray icon — created immediately on startup, independent of Flask status
function createTray() {
    // Daemon already shows the lifecycle tray when it launched us.
    if (process.env.CUTTLE_HOSTED_BY_DAEMON === '1') {
        console.log('Tray skipped — hosted by daemon.');
        return;
    }
    try {
        const imgRoot = app.isPackaged
            ? path.join(process.resourcesPath, 'app', 'src', 'img')
            : path.join(__dirname, '..', 'src', 'img');
        const hostMascot = path.join(imgRoot, 'cuttle-mascot_square_host.png');
        const mascot = path.join(imgRoot, 'cuttle-mascot_square.png');
        const iconCandidates = process.platform === 'win32'
            ? [path.join(imgRoot, 'cuttle_logo.ico'), mascot]
            : [
                (LAUNCH_MODE === 'host' && fs.existsSync(hostMascot)) ? hostMascot : mascot,
                mascot,
                path.join(imgRoot, 'cuttle-logo.png'),
                path.join(imgRoot, 'cuttle_logo.ico'),
            ];
        const iconPath = iconCandidates.find((p) => p && fs.existsSync(p));
        if (!iconPath) {
            console.warn('Tray icon not found in', imgRoot);
            return;
        }
        let trayImage = nativeImage.createFromPath(iconPath);
        if (trayImage.isEmpty()) {
            console.warn('Tray icon failed to decode:', iconPath);
            return;
        }
        // AppIndicator turns a huge PNG into a black square. Panel size is ~22px.
        if (process.platform === 'linux') {
            trayImage = trayImage.resize({ width: 24, height: 24, quality: 'best' });
        }
        tray = new Tray(trayImage);
        tray.setToolTip(LAUNCH_MODE === 'host' ? 'Cuttle Host' : 'Cuttle Desktop');
        const contextMenu = Menu.buildFromTemplate([
            { label: 'Open Cuttle', click: () => { if (mainWindow) mainWindow.show(); else createWindow(); } },
            { label: 'Change Cuttle host…', click: () => { showConnectPage(); } },
            { type: 'separator' },
            { label: 'Exit', click: () => { requestHostExit(); } }
        ]);
        tray.setContextMenu(contextMenu);
        tray.on('double-click', () => { if (mainWindow) mainWindow.show(); else createWindow(); });
        console.log('Tray icon created.');
    } catch (err) {
        console.warn('Tray icon failed (app will still run):', err.message);
    }
}

// Show a loading/error page inside the window when Flask isn't ready
function loadingPageHTML(message, isError) {
    const color = isError ? '#e74c3c' : '#3498db';
    const spinner = isError ? '' : `
        <style>
            @keyframes spin { to { transform: rotate(360deg); } }
            .spinner { width:40px;height:40px;border:4px solid #333;border-top-color:${color};
                border-radius:50%;animation:spin 0.8s linear infinite;margin:0 auto 20px; }
        </style>
        <div class="spinner"></div>`;
    return `data:text/html,<!DOCTYPE html><html><body style="background:%231a1a1a;color:%23ccc;
        font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center;max-width:400px">${spinner}
        <h2 style="color:${color};margin-bottom:10px">Cuttle</h2>
        <p>${encodeURIComponent(message).replace(/%20/g,' ').replace(/%0A/g,'<br>')}</p>
        ${isError ? '<p style="font-size:12px;color:#666">Use the tray icon to manage Cuttle.</p>' : ''}
        </div></body></html>`;
}

function showConnectPage() {
    if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.show();
        mainWindow.loadURL(connectPageUrl());
        return;
    }
    createWindow({ connect: true });
}

async function loadCuttleUi() {
    const uiUrl = await resolveUiBaseUrl();
    if (!mainWindow || mainWindow.isDestroyed()) {
        await createWindow();
        return;
    }
    mainWindow._cuttleUiUrl = uiUrl;
    mainWindow.loadURL(uiUrl);
    mainWindow.show();
    setTimeout(() => { checkDesktopUpdate(); }, 1200);
}

function downloadToFile(url, dest) {
    return new Promise((resolve, reject) => {
        let parsed;
        try {
            parsed = new URL(url);
        } catch (err) {
            reject(err);
            return;
        }
        const lib = parsed.protocol === 'https:' ? https : http;
        const req = lib.get(
            {
                hostname: parsed.hostname,
                port: parsed.port || (parsed.protocol === 'https:' ? 443 : 80),
                path: `${parsed.pathname}${parsed.search}`,
                rejectUnauthorized: false,
                timeout: 120000,
            },
            (res) => {
                if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
                    res.resume();
                    downloadToFile(res.headers.location, dest).then(resolve, reject);
                    return;
                }
                if (res.statusCode !== 200) {
                    res.resume();
                    reject(new Error(`Download failed (${res.statusCode})`));
                    return;
                }
                const file = fs.createWriteStream(dest);
                res.pipe(file);
                file.on('finish', () => file.close((err) => (err ? reject(err) : resolve())));
                file.on('error', reject);
            }
        );
        req.on('error', reject);
    });
}

async function checkDesktopUpdate() {
    try {
        const r = await jsonRequest(cuttleAppUrl('/api/desktop/electron'), { timeoutMs: 6000 });
        const remote = r.json || {};
        const local = localDesktopHash();
        // Laptop clients only. The host already runs live Flask UI; hashing
        // packaged Cuttle.exe against electron/ source is not an "update".
        const available = desktopUpdateAvailable(remote, local, {
            packaged: app.isPackaged,
            clientMode: CLIENT_MODE,
            host: FLASK_HOST,
        });
        const payload = {
            available,
            localHash: local,
            remoteHash: remote.hash || '',
            packaged: app.isPackaged,
        };
        if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send('desktop-update-available', payload);
        }
        return payload;
    } catch (err) {
        console.warn('Desktop update check failed:', err.message || err);
        return { available: false };
    }
}

async function applyDesktopUpdate() {
    if (!app.isPackaged) {
        return { ok: false, error: 'Unpackaged dev builds already run the source shell.' };
    }
    const asarPath = path.join(process.resourcesPath, 'app.asar');
    if (!fs.existsSync(asarPath)) {
        return { ok: false, error: 'This build has no app.asar to replace.' };
    }
    const tmp = path.join(process.resourcesPath, 'app.asar.new');
    try {
        await downloadToFile(cuttleAppUrl('/api/desktop/electron/app.asar'), tmp);
    } catch (err) {
        return { ok: false, error: err.message || 'Failed to download desktop update.' };
    }
    const exe = app.getPath('exe');
    const bat = path.join(os.tmpdir(), `cuttle-update-${Date.now()}.cmd`);
    const lines = [
        '@echo off',
        'ping 127.0.0.1 -n 4 >nul',
        `move /Y "${tmp}" "${asarPath}"`,
        `start "" "${exe}"`,
        'del "%~f0"',
        '',
    ];
    fs.writeFileSync(bat, lines.join('\r\n'));
    app.isQuitting = true;
    spawn('cmd.exe', ['/c', bat], { detached: true, stdio: 'ignore', windowsHide: true }).unref();
    app.quit();
    return { ok: true };
}

// Create the main application window
async function createWindow(opts = {}) {
    if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.show();
        if (opts.connect) mainWindow.loadURL(connectPageUrl());
        return;
    }

    mainWindow = new BrowserWindow({
        width: 1400,
        height: 900,
        minWidth: 800,
        minHeight: 600,
        frame: false,
        fullscreenable: true,
        autoHideMenuBar: true,
        icon: app.isPackaged
            ? path.join(process.resourcesPath, 'app', 'src', 'img', 'cuttle_logo.ico')
            : path.join(__dirname, '..', 'src', 'img', 'cuttle_logo.ico'),
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
            webSecurity: true,
            // Keep audio/timers alive when minimized or hidden to tray
            backgroundThrottling: false
        },
        show: false,
        backgroundColor: '#1a1a1a',
        title: 'Cuttle - AI Agent Framework'
    });

    // Remove the native window menu entirely on Windows/Linux so it
    // doesn't appear while fullscreening the frameless shell.
    mainWindow.removeMenu();
    Menu.setApplicationMenu(null);

    // F11 toggles true OS fullscreen (covers the Windows taskbar).
    // Ctrl+F is intercepted so Chromium's find-in-page cannot highlight
    // every split chat iframe at once; the shell routes it to one pane.
    let chatFindActions = null;
    try {
        chatFindActions = require(path.join(__dirname, '..', 'src', 'web', 'js', 'chat_find.js'));
    } catch (_) {}
    mainWindow.webContents.on('before-input-event', (event, input) => {
        if (input.type !== 'keyDown') return;
        if (input.key === 'F11') {
            event.preventDefault();
            setWindowFullscreen(mainWindow, !mainWindow.isFullScreen());
            return;
        }
        const key = String(input.key || '');
        const ctrl = !!(input.control || input.meta);
        // Own Ctrl+/- / Ctrl+0 so Chromium's built-in page zoom cannot fight
        // the renderer zoom ladder (that desync was showing bogus %).
        if (ctrl && !input.alt) {
            let zoomAction = null;
            if (key === '+' || key === '=' || key === 'Add') zoomAction = 'in';
            else if (key === '-' || key === '_' || key === 'Subtract') zoomAction = 'out';
            else if (key === '0') zoomAction = 'reset';
            if (zoomAction) {
                event.preventDefault();
                try {
                    mainWindow.webContents.send('shell-zoom-shortcut', {
                        action: zoomAction,
                        repeat: !!input.isAutoRepeat,
                    });
                } catch (_) {}
                return;
            }
        }
        let findAction = null;
        if (chatFindActions && typeof chatFindActions.actionFromElectronInput === 'function') {
            findAction = chatFindActions.actionFromElectronInput(input);
        } else {
            if (ctrl && !input.alt && !input.shift && (key === 'f' || key === 'F')) findAction = 'open';
            else if (ctrl && !input.alt && (key === 'g' || key === 'G')) findAction = input.shift ? 'prev' : 'next';
            else if (key === 'F3') findAction = input.shift ? 'prev' : 'next';
        }
        if (!findAction) return;
        event.preventDefault();
        try {
            mainWindow.webContents.send('chat-find-shortcut', { action: findAction });
        } catch (_) {}
    });

    if (opts && opts.connect) {
        mainWindow.loadURL(connectPageUrl());
    } else {
        const uiUrl = await resolveUiBaseUrl();
        mainWindow._cuttleUiUrl = uiUrl;
        mainWindow.loadURL(uiUrl);
        setTimeout(() => { checkDesktopUpdate(); }, 1500);
    }

    // Right-click edit menu (Electron only). Skip empty/canvas clicks so
    // pages with their own menus (e.g. node editor) keep working.
    mainWindow.webContents.on('context-menu', (event, params) => {
        const items = [];
        if (params.isEditable) {
            items.push(
                { role: 'cut', enabled: params.editFlags.canCut },
                { role: 'copy', enabled: params.editFlags.canCopy },
                { role: 'paste', enabled: params.editFlags.canPaste },
                { type: 'separator' },
                { role: 'selectAll', enabled: params.editFlags.canSelectAll },
            );
        } else if (params.selectionText && params.selectionText.trim()) {
            items.push({ role: 'copy', enabled: params.editFlags.canCopy });
        }
        if (items.length === 0) return;
        Menu.buildFromTemplate(items).popup({ window: mainWindow });
    });

    mainWindow.on('maximize', () => {
        if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send('window-maximized', true);
        }
    });
    mainWindow.on('unmaximize', () => {
        if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send('window-maximized', false);
        }
    });
    mainWindow.on('enter-full-screen', () => {
        if (mainWindow && !mainWindow.isDestroyed()) {
            // Keep above the taskbar for the duration of fullscreen on Windows.
            if (process.platform === 'win32') {
                mainWindow.setAlwaysOnTop(true, 'screen-saver');
            }
            mainWindow.webContents.send('window-fullscreen', true);
        }
    });
    mainWindow.on('leave-full-screen', () => {
        if (mainWindow && !mainWindow.isDestroyed()) {
            if (process.platform === 'win32') {
                mainWindow.setAlwaysOnTop(false);
            }
            mainWindow.webContents.send('window-fullscreen', false);
        }
    });

    mainWindow.once('ready-to-show', () => { mainWindow.show(); });

    // Minimize to tray on close instead of quitting
    mainWindow.on('close', (e) => {
        if (!app.isQuitting) {
            e.preventDefault();
            mainWindow.hide();
        }
    });
    mainWindow.on('closed', () => { mainWindow = null; });

    // On main-frame load failure: show an error page (don't quit — tray stays alive)
    mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription, validatedURL, isMainFrame) => {
        // Only handle main frame failures — inner iframe failures must NOT replace the shell.
        if (!isMainFrame) return;
        // Ignore aborted navigations (user navigated away, errorCode -3)
        if (errorCode === 0 || errorCode === -3) return; // 0=OK, -3=ABORTED
        if ((validatedURL || '').startsWith('file:')) return;
        console.error(`Failed to load (${errorCode}): ${errorDescription} — ${validatedURL}`);
        const msg = errorCode === -102
            ? 'Cannot connect to Cuttle server.\nFlask may still be starting up.\nDouble-click the tray icon to retry.'
            : `Failed to load Cuttle UI.\nError ${errorCode}: ${errorDescription}\n\nMake sure the Cuttle daemon is running.`;
        mainWindow.loadURL(loadingPageHTML(msg, true));
    });

    // Renderer died (OOM / GPU / crash) — shell chrome can stay painted while the
    // page goes black and stops accepting clicks. Reload instead of leaving a zombie window.
    mainWindow.webContents.on('render-process-gone', (_event, details) => {
        console.error('Renderer process gone:', details && details.reason, details && details.exitCode);
        if (!mainWindow || mainWindow.isDestroyed()) return;
        const reason = (details && details.reason) || 'unknown';
        if (reason === 'clean-exit') return;
        setTimeout(() => {
            if (!mainWindow || mainWindow.isDestroyed()) return;
            try {
                mainWindow.webContents.reload();
            } catch (err) {
                console.error('Failed to reload after renderer crash:', err);
                mainWindow.loadURL(mainWindow._cuttleUiUrl || cuttleAppUrl('/app_shell.html'));
            }
        }, 250);
    });

    mainWindow.webContents.on('unresponsive', () => {
        console.warn('Renderer became unresponsive');
    });
    mainWindow.webContents.on('responsive', () => {
        console.log('Renderer responsive again');
    });
}

// App lifecycle
app.isQuitting = false;

/**
 * True OS fullscreen. On Windows, briefly raise z-order to screen-saver
 * level so the shell covers the taskbar (plain setFullScreen often does not).
 */
function setWindowFullscreen(win, enabled) {
    if (!win || win.isDestroyed()) return;
    const want = !!enabled;
    if (want === win.isFullScreen()) return;
    if (want && process.platform === 'win32') {
        win.setAlwaysOnTop(true, 'screen-saver');
    }
    win.setFullScreen(want);
    if (!want && process.platform === 'win32') {
        win.setAlwaysOnTop(false);
    }
}

ipcMain.on('window-minimize', (event) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (win) win.minimize();
});
ipcMain.on('window-maximize', (event) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (!win) return;
    if (win.isMaximized()) win.unmaximize();
    else win.maximize();
});
ipcMain.on('window-close', (event) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (win) win.close(); // existing close handler hides to tray
});
ipcMain.on('window-reload', (event) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (!win || win.isDestroyed()) return;
    // Chat lives in an iframe; reloadIgnoringCache alone often leaves stale
    // chat_page.html / js in Chromium's HTTP cache. Clear session cache first,
    // but never block reload if clearCache hangs (seen under saturated HTTPS).
    const reload = () => {
        if (win.isDestroyed()) return;
        try {
            win.webContents.reloadIgnoringCache();
        } catch (err) {
            console.warn('reloadIgnoringCache failed, falling back to loadURL:', err);
            try {
                win.loadURL(win._cuttleUiUrl || cuttleAppUrl('/app_shell.html'));
            } catch (err2) {
                console.error('loadURL fallback failed:', err2);
            }
        }
    };
    let finished = false;
    const done = () => {
        if (finished) return;
        finished = true;
        reload();
    };
    const timer = setTimeout(done, 1500);
    Promise.resolve()
        .then(() => win.webContents.session.clearCache())
        .catch((err) => console.warn('clearCache before reload failed:', err))
        .finally(() => {
            clearTimeout(timer);
            done();
        });
});
ipcMain.on('window-toggle-fullscreen', (event) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (!win || win.isDestroyed()) return;
    setWindowFullscreen(win, !win.isFullScreen());
});
ipcMain.handle('window-is-maximized', (event) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    return win ? win.isMaximized() : false;
});
ipcMain.handle('window-is-fullscreen', (event) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    return win ? win.isFullScreen() : false;
});

/** Short two-tone WAV played when a chat reply finishes (works while minimized/tray). */
function resolveChirpWavPath() {
    const candidates = [
        path.join(__dirname, 'assets', 'completion-chirp.wav'),
        // Packaged: extraResources copies src → resources/app/src
        path.join(process.resourcesPath || '', 'app', 'src', 'web', 'sounds', 'completion-chirp.wav'),
        path.join(PROJECT_ROOT, 'src', 'web', 'sounds', 'completion-chirp.wav'),
    ];
    for (const p of candidates) {
        try {
            if (p && fs.existsSync(p)) return p;
        } catch (_) {}
    }
    return null;
}

let _chirpPlaying = false;
let _lastNativeChirpAt = 0;
const NATIVE_CHIRP_DEBOUNCE_MS = 2500;
function playCompletionChirpNative() {
    if (process.platform !== 'win32') {
        try {
            require('electron').shell.beep();
        } catch (_) {}
        return;
    }
    const now = Date.now();
    if (_chirpPlaying || (now - _lastNativeChirpAt) < NATIVE_CHIRP_DEBOUNCE_MS) return;
    const wav = resolveChirpWavPath();
    if (!wav) {
        try {
            require('electron').shell.beep();
        } catch (_) {}
        return;
    }
    _chirpPlaying = true;
    _lastNativeChirpAt = now;
    const escaped = wav.replace(/'/g, "''");
    const ps = spawn(
        'powershell.exe',
        [
            '-NoProfile',
            '-NonInteractive',
            '-WindowStyle',
            'Hidden',
            '-Command',
            `Add-Type -AssemblyName System.Media; ` +
                `$p = New-Object System.Media.SoundPlayer '${escaped}'; ` +
                `$p.PlaySync()`,
        ],
        { windowsHide: true, stdio: 'ignore' }
    );
    const clear = () => { _chirpPlaying = false; };
    ps.on('exit', clear);
    ps.on('error', clear);
}

ipcMain.on('play-chirp', () => {
    playCompletionChirpNative();
});
ipcMain.handle('window-is-obscured', (event) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (!win || win.isDestroyed()) return true;
    return !win.isVisible() || win.isMinimized();
});

ipcMain.handle('desktop-get-config', () => publicDesktopConfig());
ipcMain.handle('desktop-show-connect', () => {
    showConnectPage();
    return { ok: true };
});
ipcMain.handle('desktop-connect', async (_event, raw) => {
    try {
        const parsed = parseHostInput(raw);
        const probe = await probeCuttle(parsed.host, parsed.httpPort, parsed.httpsPort);
        if (!probe.ok) return probe;
        applyDesktopTarget({
            host: parsed.host,
            httpPort: parsed.httpPort,
            httpsPort: parsed.httpsPort,
            clientMode: true,
        });
        saveDesktopConfig({
            mode: 'client',
            host: parsed.host,
            httpPort: parsed.httpPort,
            httpsPort: parsed.httpsPort,
            lastError: '',
            workerMode: loadDesktopConfig().workerMode !== false,
        });
        await loadCuttleUi();
        await startWorkerSidecar();
        return { ok: true, host: parsed.host };
    } catch (err) {
        return { ok: false, error: err.message || String(err) };
    }
});
ipcMain.handle('desktop-use-local', async () => {
    stopWorkerSidecar();
    applyDesktopTarget({
        host: '127.0.0.1',
        httpPort: DEFAULT_HTTP_PORT,
        httpsPort: DEFAULT_HTTPS_PORT,
        clientMode: false,
    });
    saveDesktopConfig({
        mode: 'local',
        host: '127.0.0.1',
        httpPort: DEFAULT_HTTP_PORT,
        httpsPort: DEFAULT_HTTPS_PORT,
        lastError: '',
    });
    const already = await waitForFlask(3, 400);
    if (!already) {
        startDaemon();
        const ready = await waitForFlask(45, 1000);
        if (!ready) {
            return { ok: false, error: 'Flask did not start within 45 seconds on this PC.' };
        }
    }
    await loadCuttleUi();
    autoStartPipeline();
    return { ok: true };
});
ipcMain.handle('desktop-update-status', () => checkDesktopUpdate());
ipcMain.handle('desktop-apply-update', () => applyDesktopUpdate());

function isCuttleTrustedHost(hostname) {
    const h = (hostname || '').toLowerCase();
    const target = (FLASK_HOST || '').toLowerCase();
    return (
        h === '127.0.0.1'
        || h === 'localhost'
        || h === '[::1]'
        || h === '::1'
        || (target && h === target)
    );
}

function isCuttleSelfSignedHttpsUrl(url) {
    try {
        const u = new URL(url);
        // HTTPS page loads and WSS (web terminal PTY) both need the same
        // self-signed cert exception. Loopback plus the configured client host.
        if (u.protocol !== 'https:' && u.protocol !== 'wss:') return false;
        const port = u.port || '';
        if (port && port !== String(FLASK_HTTPS_PORT) && port !== '8080' && port !== '8888') return false;
        return isCuttleTrustedHost(u.hostname);
    } catch (_) {
        return false;
    }
}

// NOTE: Do NOT use session.setCertificateVerifyProc for loopback trust.
// It can deadlock the renderer when a framed page opens wss://127.0.0.1
// (terminal PTY) — Chromium waits on the UI thread for the verify callback.
// allow-insecure-localhost (top of file) + certificate-error below are enough.

app.on('certificate-error', (event, webContents, url, error, certificate, callback) => {
    // Trust Cuttle's self-signed cert for loopback (127.0.0.1, localhost, ::1) on our port
    if (isCuttleSelfSignedHttpsUrl(url)) {
        event.preventDefault();
        callback(true);
    } else {
        callback(false);
    }
});

// Host vs Client dock shortcuts use separate userData + WM_CLASS so both can run.
const LAUNCH_MODE = desktopLaunchMode();
if (LAUNCH_MODE === 'host') {
    app.setName('Cuttle Host');
    app.setPath('userData', path.join(os.homedir(), '.config', 'cuttle-desktop-host'));
    app.commandLine.appendSwitch('class', 'CuttleHost');
} else if (LAUNCH_MODE === 'client') {
    app.setName('Cuttle Client');
    app.setPath('userData', path.join(os.homedir(), '.config', 'cuttle-desktop-client'));
    app.commandLine.appendSwitch('class', 'CuttleClient');
}

// Single-instance lock: if another instance is already running, focus it and quit this one.
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
    console.log('Another Cuttle instance is already running. Exiting.');
    app.quit();
    process.exit(0);
}
app.on('second-instance', () => {
    // Someone tried to run a second instance — focus our window instead.
    if (mainWindow && !mainWindow.isDestroyed()) {
        if (mainWindow.isMinimized()) mainWindow.restore();
        mainWindow.show();
        mainWindow.focus();
        return;
    }
    createWindow();
});

app.whenReady().then(async () => {
    createTray();

    const activate = () => {
        if (BrowserWindow.getAllWindows().length === 0) createWindow();
    };

    const cliHost = parseArgValue('--host');
    if (LAUNCH_MODE === 'client' && !cliHost) {
        await createWindow({ connect: true });
        app.on('activate', activate);
        return;
    }
    if (cliHost) {
        try {
            const parsed = parseHostInput(cliHost);
            const probe = await probeCuttle(parsed.host, parsed.httpPort, parsed.httpsPort);
            if (probe.ok) {
                applyDesktopTarget({
                    host: parsed.host,
                    httpPort: parsed.httpPort,
                    httpsPort: parsed.httpsPort,
                    clientMode: true,
                });
                saveDesktopConfig({
                    mode: 'client',
                    host: parsed.host,
                    httpPort: parsed.httpPort,
                    httpsPort: parsed.httpsPort,
                    lastError: '',
                    workerMode: loadDesktopConfig().workerMode !== false,
                });
                await createWindow();
                await startWorkerSidecar();
                app.on('activate', activate);
                return;
            }
            saveDesktopConfig({ lastError: probe.error || 'Could not reach --host.' });
        } catch (err) {
            saveDesktopConfig({ lastError: err.message || String(err) });
        }
    }

    const saved = loadDesktopConfig();
    if (LAUNCH_MODE === 'host') {
        applyDesktopTarget({
            host: '127.0.0.1',
            httpPort: DEFAULT_HTTP_PORT,
            httpsPort: DEFAULT_HTTPS_PORT,
            clientMode: false,
        });
        saveDesktopConfig({
            mode: 'local',
            host: '127.0.0.1',
            httpPort: DEFAULT_HTTP_PORT,
            httpsPort: DEFAULT_HTTPS_PORT,
            lastError: '',
        });
        const alreadyRunning = await waitForFlask(3, 500);
        if (!alreadyRunning) {
            startDaemon();
            const flaskReady = await waitForFlask(45, 1000);
            if (!flaskReady) console.error('Flask did not start within 45 seconds.');
            if (flaskReady) await purgeSharedMediaExpired();
            await createWindow();
            if (flaskReady) autoStartPipeline();
        } else {
            console.log('Flask already running — skipping daemon spawn.');
            await purgeSharedMediaExpired();
            await createWindow();
            autoStartPipeline();
        }
        app.on('activate', activate);
        return;
    }

    if (saved.mode === 'client' && saved.host) {
        applyDesktopTarget({
            host: saved.host,
            httpPort: saved.httpPort,
            httpsPort: saved.httpsPort,
            clientMode: true,
        });
        const probe = await probeCuttle(FLASK_HOST, FLASK_HTTP_PORT, FLASK_HTTPS_PORT);
        if (probe.ok) {
            await createWindow();
            await startWorkerSidecar();
            app.on('activate', activate);
            return;
        }
        saveDesktopConfig({ lastError: probe.error || 'Saved host is unreachable.' });
        await createWindow({ connect: true });
        app.on('activate', activate);
        return;
    }

    applyDesktopTarget({
        host: '127.0.0.1',
        httpPort: DEFAULT_HTTP_PORT,
        httpsPort: DEFAULT_HTTPS_PORT,
        clientMode: false,
    });
    const alreadyRunning = await waitForFlask(3, 500);
    if (alreadyRunning) {
        console.log('Flask already running — skipping daemon spawn.');
        if (!saved.mode) saveDesktopConfig({ mode: 'local', host: '127.0.0.1' });
        await purgeSharedMediaExpired();
        await createWindow();
        autoStartPipeline();
        app.on('activate', activate);
        return;
    }

    if (saved.mode === 'local') {
        startDaemon();
        const flaskReady = await waitForFlask(45, 1000);
        if (!flaskReady) console.error('Flask did not start within 45 seconds.');
        if (flaskReady) await purgeSharedMediaExpired();
        await createWindow();
        if (flaskReady) autoStartPipeline();
        app.on('activate', activate);
        return;
    }

    // Unknown machine / first run: ask instead of spawning a second daemon on a laptop.
    await createWindow({ connect: true });
    app.on('activate', activate);
});

app.on('window-all-closed', () => {
    if (!app.isQuitting) return; // keep alive in tray
    if (tray) { tray.destroy(); tray = null; }
    app.quit();
});

function stopSpawnedDaemon() {
    if (!spawnedDaemonPid) return;
    const pid = spawnedDaemonPid;
    spawnedDaemonPid = null;
    try {
        process.kill(pid, 'SIGTERM');
    } catch (_) {}
    if (process.platform !== 'win32') {
        try {
            process.kill(-pid, 'SIGTERM');
        } catch (_) {}
    }
}

/** Running agent turns / jobs on this host, or -1 when Flask cannot say. */
async function fetchActiveWorkCount() {
    try {
        const url = `https://${FLASK_HOST}:${FLASK_HTTPS_PORT}/api/flask/restart/status`;
        const r = await jsonRequest(url, { timeoutMs: 3000 });
        const work = r.json && r.json.active_work;
        if (!work || work.enumeration_failed) return -1;
        return Number(work.active_count) || 0;
    } catch (_) {
        return -1;
    }
}

async function requestHostExit() {
    if (LAUNCH_MODE !== 'host' || !spawnedDaemonPid) {
        app.isQuitting = true;
        app.quit();
        return;
    }
    const busy = await fetchActiveWorkCount();
    let stop = true;
    if (busy !== 0) {
        const message = busy > 0
            ? `${busy} agent turn${busy === 1 ? ' is' : 's are'} still running.`
            : 'Could not check whether agent turns are still running.';
        const { response } = await dialog.showMessageBox({
            type: 'warning',
            title: 'Cuttle is busy',
            message,
            detail: 'Stopping Cuttle ends running turns and their replies are lost. '
                + '"Keep Cuttle running" closes the desktop app only — the daemon keeps working '
                + 'and the next launch reconnects to it.',
            buttons: ['Keep Cuttle running', 'Stop Cuttle anyway', 'Cancel'],
            defaultId: 0,
            cancelId: 2,
            noLink: true,
        });
        if (response === 2) return;
        stop = response === 1;
    }
    stopDaemonOnQuit = stop;
    app.isQuitting = true;
    app.quit();
}

app.on('before-quit', () => {
    stopWorkerSidecar();
    if (LAUNCH_MODE === 'host' && spawnedDaemonPid && stopDaemonOnQuit) {
        console.log('Cuttle Host exiting — stopping daemon pid', spawnedDaemonPid);
        stopSpawnedDaemon();
        return;
    }
    console.log('Cuttle UI exiting. Daemon continues running independently.');
    // Note: daemon was spawned detached, so it keeps running after Electron exits
    // unless the user picked tray Exit → stop (requestHostExit).
    // Device worker sidecar is Electron-owned and stops with the UI.
});

process.on('uncaughtException', (error) => {
    console.error('Uncaught Exception:', error);
});

process.on('SIGINT', () => { app.isQuitting = true; app.quit(); });
process.on('SIGTERM', () => { app.isQuitting = true; app.quit(); });
