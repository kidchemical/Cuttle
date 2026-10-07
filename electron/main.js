const { app, BrowserWindow, Menu, dialog, Tray, ipcMain, nativeImage, screen } = require('electron');
const { spawn, spawnSync } = require('child_process');
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
    desktopUpdateAvailable = require(path.join(__dirname, '..', 'src', 'web', 'js', 'shell', 'desktop_update_policy.js')).desktopUpdateAvailable;
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

/** Probe and preserve the explicitly selected endpoint. */
async function probeAndResolve(parsed) {
    const probe = await probeCuttle(parsed.host, parsed.httpPort, parsed.httpsPort, {
        prefer: parsed.preferredScheme || undefined,
        single: parsed.single && !!parsed.preferredScheme,
    });
    if (!probe.ok) return { probe };
    const selPort = probe.scheme === 'https' ? parsed.httpsPort : parsed.httpPort;
    const httpPort = parsed.httpPort;
    const httpsPort = parsed.httpsPort;
    return {
        probe,
        httpPort,
        httpsPort,
        policy: {
            kind: parsed.single ? 'single' : 'paired',
            scheme: probe.scheme,
            host: parsed.host,
            port: selPort,
            companion: null,
        },
    };
}

/** Apply a connected target and persist its endpoint policy in one place. */
function applyConnectTarget({ host, httpPort, httpsPort, clientMode, policy }) {
    applyDesktopTarget({ host, httpPort, httpsPort, clientMode });
    saveDesktopConfig({
        mode: clientMode ? 'client' : 'local',
        host,
        httpPort,
        httpsPort,
        scheme: policy.scheme,
        endpoint: policy,
        lastError: '',
        workerMode: loadDesktopConfig().workerMode !== false,
    });
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

function parsePortNumber(raw, what) {
    const text = String(raw == null ? '' : raw).trim();
    if (!text) throw new Error(`${what} is missing.`);
    if (!/^[0-9]+$/.test(text)) throw new Error(`${what} must be a port 1-65535, got ${JSON.stringify(text)}.`);
    const p = Number(text);
    if (!Number.isSafeInteger(p) || p < 1 || p > 65535) {
        throw new Error(`${what} must be a port 1-65535, got ${JSON.stringify(text)}.`);
    }
    return p;
}

/** Bracket an IPv6 literal for URL authorities; pass anything else through. */
function formatHost(host) {
    const h = String(host || '');
    if (!h) return h;
    if (h.startsWith('[')) return h;
    return h.includes(':') ? `[${h}]` : h;
}

function parseHostInput(raw) {
    const text = String(raw || '').trim();
    if (!text) throw new Error('Enter a host address.');
    let host = text;
    let httpPort = DEFAULT_HTTP_PORT;
    let httpsPort = DEFAULT_HTTPS_PORT;
    // preferredScheme preserves the protocol the user selected so probes try
    // the selected endpoint first instead of guessing an unrelated default.
    // explicitPort records whether the user supplied a port at all.
    // schemeExplicit records whether the user typed a scheme (vs bare host).
    let preferredScheme = '';
    let explicitPort = false;
    let schemeExplicit = false;
    try {
        if (text.includes('://') || text.includes('/')) {
            schemeExplicit = text.includes('://');
            const u = new URL(text.includes('://') ? text : `http://${text}`);
            if (u.protocol !== 'http:' && u.protocol !== 'https:') {
                throw new Error(`Unsupported URL scheme ${JSON.stringify(u.protocol)} — use http:// or https://.`);
            }
            if (u.username || u.password) {
                throw new Error('URLs with credentials are not supported — use a bare host or host:port.');
            }
            host = u.hostname;
            if (!host) throw new Error('Enter a host address.');
            preferredScheme = u.protocol === 'https:' ? 'https' : 'http';
            // Explicit http(s) URL without a port means the protocol default
            // (80/443) — never the Cuttle listener defaults. URL elides
            // :80/:443 to '', so consult the raw authority for explicitness.
            const authority = text.includes('://')
                ? text.split('://', 2)[1].split(/[/?#]/, 1)[0]
                : '';
            const explicitAuthorityPort = /:(\d+)$/.exec(authority) && !authority.endsWith(']');
            if (u.port) {
                const p = parsePortNumber(u.port, 'Port');
                explicitPort = true;
                if (u.protocol === 'https:') httpsPort = p;
                else httpPort = p;
            } else if (schemeExplicit) {
                explicitPort = !!explicitAuthorityPort;
                if (u.protocol === 'https:') httpsPort = 443;
                else httpPort = 80;
            }
        } else if (text.startsWith('[')) {
            // Bracketed IPv6 literal, optional :port suffix.
            const close = text.indexOf(']');
            if (close < 0) throw new Error('That does not look like a host or URL.');
            host = text.slice(1, close);
            const rest = text.slice(close + 1);
            if (rest.startsWith(':') && rest.length > 1) {
                // Bare host:port keeps the documented HTTP meaning.
                httpPort = parsePortNumber(rest.slice(1), 'Port');
                explicitPort = true;
                preferredScheme = 'http';
            } else if (rest && rest !== '') {
                throw new Error('That does not look like a host or URL.');
            }
            if (!host) throw new Error('Enter a host address.');
        } else if (text.includes(':')) {
            const parts = text.split(':');
            if (parts.length !== 2) throw new Error('That does not look like a host or URL.');
            host = parts[0];
            // Bare host:port keeps the documented HTTP meaning.
            httpPort = parsePortNumber(parts[1], 'Port');
            explicitPort = true;
            preferredScheme = 'http';
        }
    } catch (err) {
        if (/port 1-65535|host or URL|host address|scheme|credentials/i.test(err && err.message || '')) throw err;
        throw new Error('That does not look like a host or URL.');
    }
    host = (host || '').replace(/^\[|\]$/g, '').trim();
    if (!host) throw new Error('Enter a host address.');
    if (host.includes(':') && !/^[0-9a-fA-F:.]+$/.test(host)) {
        throw new Error('That does not look like a host or URL.');
    }
    // An explicit scheme selection (typed :// URL, or documented bare
    // host:port HTTP) is a single-endpoint policy: use ONLY it.
    const single = schemeExplicit || (explicitPort && preferredScheme === 'http' && !text.includes('/'));
    return { host, httpPort, httpsPort, preferredScheme, explicitPort, schemeExplicit, single };
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

// Selected-endpoint policy (single owner for candidate ordering).
// Persisted as `endpoint` in desktop-config.json:
//   { kind: 'single'|'paired', scheme, host, port,
//     companion: { scheme, host, port } | null }
// - 'single': an explicitly selected endpoint (typed :// URL, documented
//   bare host:port HTTP). ONLY the selected endpoint is used. Ignore old
//   companion metadata: an explicit selection never changes protocol/port.
// - 'paired': both listeners are known (local Host via api.server_ports,
//   or legacy bare-host defaults). HTTP first (pool-wedge default).
function endpointUrl(c) {
    return `${c.scheme}://${formatHost(c.host)}:${c.port}`;
}

function isSingleEndpointPolicy() {
    try {
        const cfg = loadDesktopConfig() || {};
        const ep = cfg.endpoint || null;
        return !!(ep && ep.kind === 'single' && ep.host === FLASK_HOST);
    } catch (_) {
        return false;
    }
}

/** Ordered endpoint candidates for the live target. Single policy yields
 *  only the selected endpoint; paired/unknown yields both listeners, HTTP first. */
function endpointCandidates() {
    const host = FLASK_HOST;
    try {
        const cfg = loadDesktopConfig() || {};
        const ep = cfg.endpoint || null;
        if (ep && ep.kind === 'single') {
            const port = Number(ep.port);
            if (ep.host !== host || !['http', 'https'].includes(ep.scheme)
                || !Number.isSafeInteger(port) || port < 1 || port > 65535) {
                throw new Error('Invalid saved endpoint; reconnect with a valid URL.');
            }
            return [{ scheme: ep.scheme, host, port }];
        }
    } catch (err) { throw err; }
    return [
        { scheme: 'http', host, port: FLASK_HTTP_PORT },
        { scheme: 'https', host, port: FLASK_HTTPS_PORT },
    ];
}

/** Ordered full URLs for a route path, from the candidate owner. */
function candidateUrls(pathname) {
    const pathPart = pathname.startsWith('/') ? pathname : `/${pathname}`;
    return endpointCandidates().map((c) => `${endpointUrl(c)}${pathPart}`);
}

/** Worker coordinator endpoints from the candidate owner: primary is the
 *  first candidate; https/http are the known candidates of that scheme
 *  (http is '' when no HTTP endpoint is known — never a guessed default).
 *  single mirrors the persisted single-endpoint policy for the sidecar. */
function workerCoordinatorEnv() {
    const cands = endpointCandidates();
    const primary = endpointUrl(cands[0]);
    const httpsCand = cands.find((c) => c.scheme === 'https');
    const httpCand = cands.find((c) => c.scheme === 'http');
    return {
        primary,
        https: httpsCand ? endpointUrl(httpsCand) : primary,
        http: httpCand ? endpointUrl(httpCand) : '',
        single: isSingleEndpointPolicy(),
    };
}

async function probeUrlList(list) {
    let lastError = 'No Cuttle server responded at that address.';
    for (const c of list) {
        try {
            const r = await jsonRequest(`${endpointUrl(c)}/api/health`);
            if (r.status >= 200 && r.status < 500 && looksLikeCuttle(r.json)) {
                return { ok: true, host: c.host, scheme: c.scheme, port: c.port, uiUrl: `${endpointUrl(c)}/app_shell.html` };
            }
        } catch (err) {
            lastError = (err && err.message) || 'Could not reach Cuttle on that host.';
        }
    }
    return { ok: false, error: lastError };
}

async function probeCuttle(host, httpPort, httpsPort, opts = {}) {
    // opts.prefer: 'https' | 'http' — try the user-selected scheme first.
    // opts.single: probe ONLY the preferred endpoint (explicit selection);
    // never touch an unrelated default port or downgrade the protocol.
    const prefer = opts.prefer === 'https' ? 'https' : opts.prefer === 'http' ? 'http' : '';
    const candidates = prefer === 'https'
        ? ['https', 'http']
        : prefer === 'http'
            ? ['http', 'https']
            : ['http', 'https'];
    const order = opts.single && prefer ? [prefer] : candidates;
    const list = order.map((scheme) => ({
        scheme, host, port: scheme === 'https' ? httpsPort : httpPort,
    }));
    const r = await probeUrlList(list);
    if (!r.ok) return r;
    return { ok: true, host, httpPort, httpsPort, scheme: r.scheme, uiUrl: r.uiUrl };
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
        scheme: cfg.scheme || '',
        // Selected-endpoint policy for web clients (pool-switch companion
        // resolution): kind/single/companion-known. Absent on legacy configs.
        endpoint: cfg.endpoint || null,
        packaged: app.isPackaged,
        hash: localDesktopHash(),
        packageVersion,
        lastError: cfg.lastError || '',
    };
}

/** Preferred UI URL: first candidate of the endpoint owner. */
function preferredAppUrl(pathname = '/app_shell.html') {
    const pathPart = pathname.startsWith('/') ? pathname : `/${pathname}`;
    const cands = endpointCandidates();
    const c = cands[0] || { scheme: 'http', host: FLASK_HOST, port: FLASK_HTTP_PORT };
    return `${endpointUrl(c)}${pathPart}`;
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

/**
 * Local-Host listener ports, read-only from the Python owner
 * (api.server_ports: env over <Cuttle home>/.env, defaults 8080/8000/8888).
 * Electron never parses .env itself — single parser stays in Python.
 * Returns { https, http, phone_https } or throws fail-closed (malformed
 * config must not silently fall back: the defaults may be live elsewhere).
 * Remote targets never call this; their ports come from desktop-config.json.
 */
function localPortError(code, message) {
    const err = new Error(message);
    err.code = code;
    return err;
}

/**
 * Local-Host listener ports, read-only from the Python owner
 * (api.server_ports: env over <Cuttle home>/.env, defaults 8080/8000/8888).
 * Electron never parses .env itself — single parser stays in Python.
 * Returns { https, http, phone_https }.
 * Throws code 'local-port-config' on malformed config (fail closed: callers
 * must NOT probe defaults or spawn after this) or 'local-port-unavailable'
 * when no host Python exists to ask.
 * Remote targets never call this; their ports come from desktop-config.json.
 */
function queryLocalServerPorts(projectRoot) {
    const pythonExe = resolvePythonExe(projectRoot);
    if (!pythonExe) {
        throw localPortError('local-port-unavailable', 'No usable Python found — cannot read local listener ports.');
    }
    let r;
    try {
        r = spawnSync(pythonExe, ['-m', 'api.server_ports'], {
            cwd: projectRoot,
            env: { ...process.env, PYTHONPATH: path.join(projectRoot, 'src') },
            encoding: 'utf8',
            timeout: 20000,
        });
    } catch (err) {
        throw localPortError('local-port-unavailable', `Local port query failed: ${err.message || err}`);
    }
    const out = String((r && r.stdout) || '').trim();
    if (!r || r.status !== 0) {
        const detail = String((r && r.stderr) || '').trim().split('\n').pop();
        throw localPortError('local-port-config',
            `Invalid listener-port configuration${detail ? `: ${detail}` : ''}. ` +
            'Fix CUTTLE_HTTPS_PORT/CUTTLE_HTTP_PORT/CUTTLE_PHONE_HTTPS_PORT in the Cuttle home .env, then cold-restart the daemon.'
        );
    }
    let ports;
    try {
        ports = JSON.parse(out);
    } catch (_) {
        throw localPortError('local-port-config', 'Local port query returned non-JSON output.');
    }
    for (const key of ['https', 'http', 'phone_https']) {
        const p = ports ? ports[key] : null;
        if (!Number.isSafeInteger(p) || p < 1 || p > 65535) {
            throw localPortError('local-port-config', `Local port query returned bad ${key}: ${JSON.stringify(p)}.`);
        }
    }
    if (new Set([ports.https, ports.http, ports.phone_https]).size !== 3) {
        throw localPortError('local-port-config', 'Local listener ports must be distinct.');
    }
    return ports;
}

/** Local-Host desktop target from the Python owner. Throws fail-closed. */
function localDesktopTarget(projectRoot) {
    const ports = queryLocalServerPorts(projectRoot);
    return { host: '127.0.0.1', httpPort: ports.http, httpsPort: ports.https, clientMode: false };
}

/** Apply the local-Host target with its paired endpoint policy (both ports
 *  known from the Python owner, HTTP preferred). Throws like the query. */
function applyLocalTarget(projectRoot) {
    const local = localDesktopTarget(projectRoot);
    applyConnectTarget({
        host: local.host,
        httpPort: local.httpPort,
        httpsPort: local.httpsPort,
        clientMode: false,
        policy: {
            kind: 'paired',
            scheme: 'http',
            host: local.host,
            port: local.httpPort,
            companion: { scheme: 'https', host: local.host, port: local.httpsPort },
        },
    });
    return local;
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
    for (const url of candidateUrls('/api/shared-media/purge')) {
        try {
            const r = await jsonRequest(url, { method: 'POST', timeoutMs: 8000 });
            if (r.status >= 200 && r.status < 300 && r.json && r.json.success) {
                const n = r.json.deleted || 0;
                if (n) console.log(`Shared media purge: deleted ${n} expired file(s)`);
                return;
            }
        } catch (err) {
            console.warn(`Shared media purge via ${url} failed:`, err && err.message ? err.message : err);
        }
    }
}

async function resolveUiBaseUrl() {
    // Ordered candidates from the endpoint owner: single policy tries ONLY
    // the selected endpoint;
    // paired/legacy keeps HTTP-first (Chromium TLS/SSE pool wedge).
    const cands = endpointCandidates();
    for (const c of cands) {
        const label = c.scheme === 'https' ? 'HTTPS portal' : 'HTTP portal';
        const up = await waitForPort(c.port, 5, 400, label);
        if (up) {
            console.log(`Using ${label} ${endpointUrl(c)}`);
            return `${endpointUrl(c)}/app_shell.html`;
        }
        console.warn(`${label} ${endpointUrl(c)} not up`);
    }
    // No candidate answered yet — return the preferred URL and let the
    // window show the loading/error page (existing did-fail-load path).
    return preferredAppUrl('/app_shell.html');
}

// Spawn the Cuttle daemon (detached so it survives Electron being closed).
// The daemon manages Flask, cron, hot-reload, and its own tray icon.
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
    // Enroll over the endpoint-owner candidates in order: a single-endpoint
    // policy never touches a guessed other-protocol URL.
    const bases = endpointCandidates().map(endpointUrl);
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

    let lastErr = null;
    for (const base of bases) {
        try {
            return await tryEnroll(base);
        } catch (err) {
            console.warn(`Worker enroll via ${base} failed:`, (err && err.message) || err);
            lastErr = err;
        }
    }
    throw lastErr || new Error('Worker enroll failed.');
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

    // Coordinator URLs come from the endpoint owner (workerCoordinatorEnv):
    // the selected endpoint only in single mode. CUTTLE_ENDPOINT_SINGLE
    // tells the sidecar to suppress legacy default-pair inference.
    const coord = workerCoordinatorEnv();
    const primary = coord.primary;
    const coordinatorHttp = coord.http;
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
        CUTTLE_DEVICE_WORKERS_COORDINATOR_URL: primary,
        CUTTLE_DEVICE_WORKERS_COORDINATOR_URL_HTTP: coordinatorHttp,
        CUTTLE_ENDPOINT_SINGLE: isSingleEndpointPolicy() ? '1' : '0',
        CUTTLE_DEVICE_WORKERS_TOKEN: String(workerToken),
        CUTTLE_DEVICE_WORKER_ID: String(workerId || os.hostname()),
        CUTTLE_DEVICE_WORKER_LOG: logPath,
        CUTTLE_PACKAGE_VERSION: desktopVersion,
        CUTTLE_REPO_ROOT: projectRoot,
        CUTTLE_FLASK_HOST: String(FLASK_HOST || ''),
    };

    console.log('Starting device worker sidecar →', primary, '(http fallback', coordinatorHttp + ')');
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
            { label: 'Refresh window', click: () => { if (mainWindow) reloadWindowFresh(mainWindow); else createWindow(); } },
            { label: 'Unfreeze window (restart renderer)', click: () => { unfreezeWindow(mainWindow); } },
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

/** GET a desktop API path over the endpoint-owner candidates, in order. */
async function desktopApiGet(pathname, timeoutMs = 6000) {
    const urls = candidateUrls(pathname);
    let lastErr = null;
    for (const url of urls) {
        try {
            return await jsonRequest(url, { timeoutMs });
        } catch (err) {
            lastErr = err;
        }
    }
    throw lastErr || new Error('Desktop API unreachable.');
}

/** Download a desktop path over the endpoint-owner candidates, in order. */
async function desktopDownload(pathname, dest) {
    const urls = candidateUrls(pathname);
    let lastErr = null;
    for (const url of urls) {
        try {
            await downloadToFile(url, dest);
            return;
        } catch (err) {
            lastErr = err;
        }
    }
    throw lastErr || new Error('Failed to download desktop update.');
}

async function checkDesktopUpdate() {
    try {
        const r = await desktopApiGet('/api/desktop/electron', 6000);
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

function fileSha256(file) {
    return new Promise((resolve, reject) => {
        const h = crypto.createHash('sha256');
        fs.createReadStream(file)
            .on('data', (chunk) => h.update(chunk))
            .on('end', () => resolve(h.digest('hex')))
            .on('error', reject);
    });
}

async function applyDesktopUpdate() {
    if (!app.isPackaged) {
        return { ok: false, error: 'Unpackaged dev builds already run the source shell.' };
    }
    // The swap below is a cmd.exe script; AppImage resources are read-only anyway.
    if (process.platform !== 'win32') {
        return { ok: false, error: 'In-place desktop updates are Windows-only. Download the new Cuttle release for this platform.' };
    }
    const asarPath = path.join(process.resourcesPath, 'app.asar');
    if (!fs.existsSync(asarPath)) {
        return { ok: false, error: 'This build has no app.asar to replace.' };
    }
    let expected = '';
    try {
        const manifest = (await desktopApiGet('/api/desktop/electron', 6000)).json || {};
        expected = String(manifest.artifactSha256 || '').toLowerCase();
    } catch (_) {}
    if (!/^[0-9a-f]{64}$/.test(expected)) {
        return { ok: false, error: 'Host did not publish an update checksum; update the host first.' };
    }
    const tmp = path.join(process.resourcesPath, 'app.asar.new');
    try {
        await desktopDownload('/api/desktop/electron/app.asar', tmp);
        const actual = await fileSha256(tmp);
        if (actual !== expected) {
            throw new Error('Downloaded desktop update failed checksum verification.');
        }
    } catch (err) {
        try { fs.unlinkSync(tmp); } catch (_) {}
        return { ok: false, error: (err && err.message) || 'Failed to download desktop update.' };
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

/**
 * Restore the last window position, but only if it is still on a connected
 * display. Otherwise fall back to a centered default so the window never
 * opens straddling two monitors after a display layout change.
 */
function resolveWindowBounds() {
    const fallback = { width: 1400, height: 900, center: true, maximized: false };
    let saved = null;
    try {
        saved = loadDesktopConfig().windowBounds || null;
    } catch (_) {
        saved = null;
    }
    let displays = [];
    try {
        displays = screen.getAllDisplays() || [];
    } catch (_) {
        displays = [];
    }
    const primary = (() => {
        try {
            return screen.getPrimaryDisplay();
        } catch (_) {
            return displays[0] || null;
        }
    })();
    const clampTo = (area, w, h) => ({
        width: Math.max(800, Math.min(Number(w) || fallback.width, area ? area.width : 1400)),
        height: Math.max(600, Math.min(Number(h) || fallback.height, area ? area.height : 900)),
    });
    if (saved && Number.isFinite(saved.width) && Number.isFinite(saved.height) &&
        Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
        // Visible if any display workArea contains the saved top-left corner
        // (with a small tolerance so title-bar grabs still work).
        const visible = displays.some((d) => {
            const a = d.workArea || d.bounds;
            if (!a) return false;
            return saved.x >= a.x - 50 && saved.x <= a.x + a.width - 50 &&
                saved.y >= a.y - 50 && saved.y <= a.y + a.height - 50;
        });
        if (visible) {
            const hostArea = (() => {
                const host = displays.find((d) => {
                    const a = d.workArea || d.bounds;
                    return saved.x >= a.x && saved.x < a.x + a.width &&
                        saved.y >= a.y && saved.y < a.y + a.height;
                });
                return host ? (host.workArea || host.bounds) : null;
            })();
            const size = clampTo(hostArea, saved.width, saved.height);
            return { x: Math.round(saved.x), y: Math.round(saved.y), ...size, maximized: !!saved.maximized };
        }
    }
    const area = primary ? (primary.workArea || primary.bounds) : null;
    const size = clampTo(area, fallback.width, fallback.height);
    return { ...size, center: true, maximized: false };
}

let _saveBoundsTimer = null;
/** Persist normal (unmaximized) bounds, debounced, so reopen restores position. */
function scheduleSaveWindowBounds() {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    if (_saveBoundsTimer) clearTimeout(_saveBoundsTimer);
    _saveBoundsTimer = setTimeout(() => {
        _saveBoundsTimer = null;
        try {
            if (!mainWindow || mainWindow.isDestroyed()) return;
            if (mainWindow.isMinimized() || mainWindow.isFullScreen()) return;
            const maximized = mainWindow.isMaximized();
            // getBounds while maximized reports the fullscreen rect — keep the
            // last normal bounds and just remember the maximized flag.
            const prev = loadDesktopConfig().windowBounds || {};
            const patch = maximized
                ? { ...prev, maximized: true }
                : { ...mainWindow.getBounds(), maximized: false };
            saveDesktopConfig({ windowBounds: patch });
        } catch (_) {}
    }, 400);
}

// Create the main application window
async function createWindow(opts = {}) {
    if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.show();
        if (opts.connect) mainWindow.loadURL(connectPageUrl());
        return;
    }

    const bounds = resolveWindowBounds();
    mainWindow = new BrowserWindow({
        ...(Number.isFinite(bounds.x) ? { x: bounds.x } : {}),
        ...(Number.isFinite(bounds.y) ? { y: bounds.y } : {}),
        width: bounds.width,
        height: bounds.height,
        ...(bounds.center ? { center: true } : {}),
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

    // Links to other sites open in the OS default browser instead of a bare
    // Electron window. Same-origin app pages (query log inspector, reports)
    // keep opening in-app.
    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
        const { routeWindowOpen } = require('./external-link-policy.js');
        const route = routeWindowOpen(url, mainWindow._cuttleUiUrl || preferredAppUrl('/app_shell.html'));
        if (route === 'external') {
            try {
                const opened = require('electron').shell.openExternal(url);
                if (opened && typeof opened.catch === 'function') opened.catch(() => {});
            } catch (_) {}
            return { action: 'deny' };
        }
        return { action: route === 'in-app' ? 'allow' : 'deny' };
    });

    // F11 toggles true OS fullscreen (covers the Windows taskbar).
    // Ctrl+F is intercepted so Chromium's find-in-page cannot highlight
    // every split chat iframe at once; the shell routes it to one pane.
    let chatFindActions = null;
    try {
        chatFindActions = require(path.join(__dirname, '..', 'src', 'web', 'js', 'chat', 'chat_find.js'));
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

    mainWindow.once('ready-to-show', () => {
        mainWindow.show();
        if (bounds.maximized && !mainWindow.isMaximized()) {
            try { mainWindow.maximize(); } catch (_) {}
        }
    });
    mainWindow.on('resize', scheduleSaveWindowBounds);
    mainWindow.on('move', scheduleSaveWindowBounds);
    mainWindow.on('maximize', scheduleSaveWindowBounds);
    mainWindow.on('unmaximize', scheduleSaveWindowBounds);

    // Minimize to tray on close instead of quitting
    // (flush bounds synchronously — the debounced saver may not fire on quit)
    mainWindow.on('close', (e) => {
        try {
            if (_saveBoundsTimer) { clearTimeout(_saveBoundsTimer); _saveBoundsTimer = null; }
            if (!mainWindow.isMinimized() && !mainWindow.isFullScreen()) {
                const maximized = mainWindow.isMaximized();
                const prev = loadDesktopConfig().windowBounds || {};
                saveDesktopConfig({
                    windowBounds: maximized
                        ? { ...prev, maximized: true }
                        : { ...mainWindow.getBounds(), maximized: false },
                });
            }
        } catch (_) {}
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
                mainWindow.loadURL(mainWindow._cuttleUiUrl || preferredAppUrl('/app_shell.html'));
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
    reloadWindowFresh(BrowserWindow.fromWebContents(event.sender));
});

function reloadWindowFresh(win) {
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
                win.loadURL(win._cuttleUiUrl || preferredAppUrl('/app_shell.html'));
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
}

// A renderer stuck in a JS loop never commits a reload (same-site navigation
// reuses the busy process). Killing it fires render-process-gone, which reloads.
function unfreezeWindow(win) {
    if (!win || win.isDestroyed()) {
        createWindow();
        return;
    }
    win.show();
    try {
        win.webContents.forcefullyCrashRenderer();
    } catch (err) {
        console.error('forcefullyCrashRenderer failed:', err);
        win.loadURL(win._cuttleUiUrl || preferredAppUrl('/app_shell.html'));
    }
}

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

/**
 * Allowlisted SFX WAV names. The renderer may only ask for one of these —
 * never a path — so a compromised page cannot make the main process play
 * arbitrary files from disk.
 */
const NATIVE_SFX_FILES = {
    'completion-chirp': 'completion-chirp.wav',
    'achievement-unlock': 'achievement-unlock.wav',
};

/** Resolve an allowlisted SFX name to a WAV path (works minimized/in-tray). */
function resolveSfxWavPath(name) {
    const file = NATIVE_SFX_FILES[String(name || 'completion-chirp')];
    if (!file) return null;
    const candidates = [
        path.join(__dirname, 'assets', file),
        // Packaged: extraResources copies src → resources/app/src
        path.join(process.resourcesPath || '', 'app', 'src', 'web', 'sounds', file),
        path.join(PROJECT_ROOT, 'src', 'web', 'sounds', file),
    ];
    for (const p of candidates) {
        try {
            if (p && fs.existsSync(p)) return p;
        } catch (_) {}
    }
    return null;
}

/** Short two-tone WAV played when a chat reply finishes. */
function resolveChirpWavPath() {
    return resolveSfxWavPath('completion-chirp');
}

let _chirpPlaying = false;
let _lastNativeChirpAt = 0;
const NATIVE_CHIRP_DEBOUNCE_MS = 2500;
/**
 * Play an allowlisted SFX natively. Needed because in-page <audio> is silent
 * (or autoplay-blocked) whenever the window is hidden, minimized, or in tray —
 * which is exactly when a long-running-turn achievement unlocks.
 */
function playNativeSfx(name, debounceMs) {
    if (process.platform !== 'win32') {
        try {
            require('electron').shell.beep();
        } catch (_) {}
        return;
    }
    const now = Date.now();
    const minGap = Number(debounceMs) > 0 ? Number(debounceMs) : NATIVE_CHIRP_DEBOUNCE_MS;
    if (_chirpPlaying || (now - _lastNativeChirpAt) < minGap) return;
    const wav = resolveSfxWavPath(name);
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
    playNativeSfx('completion-chirp', NATIVE_CHIRP_DEBOUNCE_MS);
});

// Achievement unlocks (experimental feature — achievements.js asks by name).
ipcMain.on('play-sfx', (event, name) => {
    if (!NATIVE_SFX_FILES[String(name || '')]) return; // allowlist gate
    playNativeSfx(name, 1200);
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
        const resolved = await probeAndResolve(parsed);
        if (!resolved.probe.ok) return resolved.probe;
        applyConnectTarget({
            host: parsed.host,
            httpPort: resolved.httpPort,
            httpsPort: resolved.httpsPort,
            clientMode: true,
            policy: resolved.policy,
        });
        await loadCuttleUi();
        await startWorkerSidecar();
        return { ok: true, host: parsed.host, scheme: resolved.policy.scheme };
    } catch (err) {
        return { ok: false, error: err.message || String(err) };
    }
});
ipcMain.handle('desktop-use-local', async () => {
    stopWorkerSidecar();
    // Local-Host ports come from the Python owner (api.server_ports), never
    // hardcoded defaults. Malformed config fails closed here — do not spawn
    // a daemon the health check could mistake for another instance.
    try {
        const isDev = !app.isPackaged;
        const projectRoot = isDev
            ? path.join(__dirname, '..')
            : path.join(process.resourcesPath, 'app');
        applyLocalTarget(projectRoot);
    } catch (err) {
        return { ok: false, error: err.message || String(err) };
    }
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

// ── Gizmo pop-outs: always-on-top desktop windows (experimental `gizmos`) ──
// The shell sends the full wanted set; main opens/closes to match. Only ids
// cross IPC — the URL is built here so a page cannot open arbitrary windows.
const GIZMO_ID_RE = /^[a-z0-9][a-z0-9_-]{0,47}$/;
const gizmoPopouts = new Map();
const _gizmoBoundsTimers = new Map();

function saveGizmoPopoutBounds(id, win) {
    clearTimeout(_gizmoBoundsTimers.get(id));
    _gizmoBoundsTimers.set(id, setTimeout(() => {
        _gizmoBoundsTimers.delete(id);
        if (!win || win.isDestroyed()) return;
        try {
            const all = { ...(loadDesktopConfig().gizmoPopouts || {}) };
            all[id] = win.getBounds();
            saveDesktopConfig({ gizmoPopouts: all });
        } catch (_) {}
    }, 400));
}

function savedGizmoPopoutBounds(id) {
    const saved = (loadDesktopConfig().gizmoPopouts || {})[id];
    if (!saved || !Number.isFinite(saved.width) || !Number.isFinite(saved.height)) return null;
    const out = { width: Math.max(160, Math.min(800, saved.width)), height: Math.max(56, Math.min(400, saved.height)) };
    if (Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
        const visible = (screen.getAllDisplays() || []).some((d) => {
            const a = d.workArea || d.bounds;
            return a && saved.x >= a.x - 40 && saved.x <= a.x + a.width - 40
                && saved.y >= a.y - 20 && saved.y <= a.y + a.height - 20;
        });
        if (visible) { out.x = saved.x; out.y = saved.y; }
    }
    return out;
}

function openGizmoPopout(id, title) {
    if (gizmoPopouts.has(id)) return;
    const base = (mainWindow && !mainWindow.isDestroyed() && mainWindow._cuttleUiUrl) || preferredAppUrl('/app_shell.html');
    const url = new URL('/gizmo_popout.html', base);
    url.searchParams.set('id', id);
    const bounds = savedGizmoPopoutBounds(id) || { width: 260, height: 86 };
    const win = new BrowserWindow({
        ...bounds,
        minWidth: 160,
        minHeight: 56,
        frame: false,
        alwaysOnTop: true,
        skipTaskbar: true,
        resizable: true,
        minimizable: false,
        maximizable: false,
        fullscreenable: false,
        show: false,
        backgroundColor: '#0d1117',
        title: String(title || 'Gizmo').slice(0, 60),
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
            webSecurity: true,
            backgroundThrottling: false,
        },
    });
    win.removeMenu();
    try { win.setAlwaysOnTop(true, 'floating'); } catch (_) {}
    try { win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true }); } catch (_) {}
    win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    win.webContents.on('will-navigate', (event, target) => {
        try {
            if (new URL(target).pathname !== '/gizmo_popout.html') event.preventDefault();
        } catch (_) { event.preventDefault(); }
    });
    win.once('ready-to-show', () => { if (!win.isDestroyed()) win.showInactive(); });
    win.on('move', () => saveGizmoPopoutBounds(id, win));
    win.on('resize', () => saveGizmoPopoutBounds(id, win));
    win.on('closed', () => {
        gizmoPopouts.delete(id);
        if (win._cuttleSyncClose || app.isQuitting) return;
        if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send('gizmo-popout-closed', id);
        }
    });
    gizmoPopouts.set(id, win);
    win.loadURL(url.toString());
}

ipcMain.handle('gizmo-popouts-sync', (event, list) => {
    if (!mainWindow || mainWindow.isDestroyed() || event.sender !== mainWindow.webContents) {
        return { ok: false, error: 'Only the Cuttle window manages gizmo pop-outs.' };
    }
    const wanted = new Map();
    (Array.isArray(list) ? list : []).slice(0, 24).forEach((item) => {
        const id = String((item && item.id) || '');
        if (GIZMO_ID_RE.test(id)) wanted.set(id, String((item && item.title) || ''));
    });
    gizmoPopouts.forEach((win, id) => {
        if (wanted.has(id)) return;
        win._cuttleSyncClose = true;
        if (!win.isDestroyed()) win.close();
    });
    wanted.forEach((title, id) => openGizmoPopout(id, title));
    return { ok: true, open: Array.from(wanted.keys()) };
});

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
        // self-signed cert exception. Loopback plus the selected endpoint
        // host, on the effective HTTPS port only — no literal allowlist.
        // An omitted port is the protocol default (443), compared always.
        if (u.protocol !== 'https:' && u.protocol !== 'wss:') return false;
        const port = u.port ? Number(u.port) : 443;
        if (!Number.isSafeInteger(port) || port !== FLASK_HTTPS_PORT) return false;
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
            const resolved = await probeAndResolve(parsed);
            if (resolved.probe.ok) {
                applyConnectTarget({
                    host: parsed.host,
                    httpPort: resolved.httpPort,
                    httpsPort: resolved.httpsPort,
                    clientMode: true,
                    policy: resolved.policy,
                });
                await createWindow();
                await startWorkerSidecar();
                app.on('activate', activate);
                return;
            }
            saveDesktopConfig({ lastError: resolved.probe.error || 'Could not reach --host.' });
        } catch (err) {
            saveDesktopConfig({ lastError: err.message || String(err) });
        }
        await createWindow({ connect: true });
        app.on('activate', activate);
        return;
    }

    const saved = loadDesktopConfig();
    if (LAUNCH_MODE === 'host') {
        // Host-mode local ports come from the Python owner, not defaults.
        // Malformed config fails closed with a connect page: no probing of
        // defaults, no daemon spawn against the wrong ports.
        try {
            const isDev = !app.isPackaged;
            const projectRoot = isDev
                ? path.join(__dirname, '..')
                : path.join(process.resourcesPath, 'app');
            applyLocalTarget(projectRoot);
        } catch (err) {
            saveDesktopConfig({ lastError: err.message || String(err) });
            await createWindow({ connect: true });
            app.on('activate', activate);
            return;
        }
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
        // Restore probes the persisted endpoint policy in order: a single
        // policy touches ONLY the selected endpoint; legacy paired restores
        // probe both defaults.
        let probe;
        try {
            probe = await probeUrlList(endpointCandidates());
        } catch (err) {
            probe = { ok: false, error: err.message || String(err) };
        }
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

    // No saved mode: local ports still come from the Python owner when
    // available, so a custom-port Flask is recognized as already running.
    // Malformed config fails closed with a connect error: no probing of
    // defaults, no daemon spawn against the wrong ports.
    try {
        const isDev = !app.isPackaged;
        const projectRoot = isDev
            ? path.join(__dirname, '..')
            : path.join(process.resourcesPath, 'app');
        applyLocalTarget(projectRoot);
    } catch (err) {
        saveDesktopConfig({ lastError: err.message || String(err) });
        await createWindow({ connect: true });
        app.on('activate', activate);
        return;
    }
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
    let r = null;
    try {
        r = await desktopApiGet('/api/flask/restart/status', 3000);
    } catch (_) {
        return -1;
    }
    try {
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
