"""Configurable-port behavior: Electron custom-port input, scheme preference, worker fallback.

Attempt 1 (parent CH-001002 followup):
- ``parseHostInput`` preserves the user-typed scheme/port and fails closed on
  malformed ports (no silent default fallback).
- ``probeCuttle`` tries the selected endpoint first; explicit scheme+port
  probes ONLY that endpoint (never an unrelated default, never downgraded).
- ``isCuttleSelfSignedHttpsUrl`` allows exactly the selected endpoint
  host + effective HTTPS port (no literal allowlist).
- ``poolStallHttpTarget`` (chat_page.js) keeps the documented default-mode
  :8080 -> :8000 legacy companion and resolves local paired targets from
  Electron config. Explicit single targets never redirect to HTTP.
- Device-worker ``pick_base_urls`` infers :8000 only in documented
  default-mode (primary exactly https://host:8080, no explicit companion).
- ``python -m api.server_ports`` is the read-only port owner Electron
  consumes (no second dotenv parser in JS).

JS behavior runs in Node with extracted sources + fakes (whole Electron
cannot import hermetically); Python CLI tests use tmp env files only —
never the real <home>/.env, never a daemon spawn, never the network.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SRC_DIR = REPO / "src"
MAIN_JS = REPO / "electron" / "main.js"
CHAT_JS = SRC_DIR / "web" / "js" / "chat/chat_page.js"
SIDECAR = REPO / "electron" / "device-worker" / "cuttle_device_worker.py"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

MAIN_FUNCS = (
    "parsePortNumber",
    "formatHost",
    "parseHostInput",
    "looksLikeCuttle",
    "endpointUrl",
    "isSingleEndpointPolicy",
    "endpointCandidates",
    "candidateUrls",
    "desktopApiGet",
    "pinnedUpdateBase",
    "enrollWorkerWithHost",
    "probeUrlList",
    "probeCuttle",
    "workerCoordinatorEnv",
    "preferredAppUrl",
    "resolveUiBaseUrl",
    "probeAndResolve",
    "localPortError",
    "queryLocalServerPorts",
    "cuttleHttpsUrlPort",
    "isPinnedCuttleCertificate",
    "isCuttleSelfSignedHttpsUrl",
)


def _extract_braced(lines: list[str], start: int) -> tuple[str, int]:
    depth = 0
    buf: list[str] = []
    j = start
    while j < len(lines):
        buf.append(lines[j])
        depth += lines[j].count("{") - lines[j].count("}")
        j += 1
        if depth == 0 and j > start + 1:
            break
    assert depth == 0, "unbalanced braces extracting JS function"
    return "".join(buf), j


def _extract_main_js() -> str:
    text = MAIN_JS.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    found: dict[str, str] = {}
    i = 0
    while i < len(lines):
        m = re.match(r"^(async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", lines[i])
        if m and m.group(2) in MAIN_FUNCS and m.group(2) not in found:
            body, i = _extract_braced(lines, i)
            found[m.group(2)] = body
        else:
            i += 1
    missing = [n for n in MAIN_FUNCS if n not in found]
    assert not missing, f"could not extract from main.js: {missing}"
    consts = (
        "const DEFAULT_HTTP_PORT = 8000;\n"
        "const DEFAULT_HTTPS_PORT = 8080;\n"
    )
    assert "const DEFAULT_HTTP_PORT = 8000;" in text
    assert "const DEFAULT_HTTPS_PORT = 8080;" in text
    return consts + "\n".join(found[n] for n in MAIN_FUNCS)


POOL_FUNCS = ("poolStallSameHost", "poolStallTarget", "poolStallHttpTarget")


def _extract_pool_helper() -> str:
    lines = CHAT_JS.read_text(encoding="utf-8").splitlines(keepends=True)
    found: dict[str, str] = {}
    i = 0
    while i < len(lines):
        m = re.match(r"^    (async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", lines[i])
        if m and m.group(2) in POOL_FUNCS and m.group(2) not in found:
            body, i = _extract_braced(lines, i)
            dedented = "".join(
                ln[4:] if ln.startswith("    ") else ln for ln in body.splitlines(keepends=True)
            )
            found[m.group(2)] = dedented
        else:
            i += 1
    missing = [n for n in POOL_FUNCS if n not in found]
    assert not missing, f"pool helpers missing in chat_page.js: {missing}"
    return "\n".join(found[n] for n in POOL_FUNCS)


def _run_node(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True, encoding="utf-8",
        timeout=60,
        cwd=str(REPO),
    )


NODE_PRELUDE = """
const path = require('path');
const os = require('os');
const tlsTrust = require('./electron/tls-trust.js');
let CLIENT_MODE = true;
let __pinChecks = [];
let __pinError = null;
async function ensureTlsPin(host, port, opts) {
    __pinChecks.push(host + ':' + port + (opts && opts.allowReplace ? ':replace' : ''));
    if (__pinError) throw __pinError;
}
let FLASK_HOST = '127.0.0.1';
let FLASK_HTTP_PORT = 8000;
let FLASK_HTTPS_PORT = 8080;
let __requested = [];
let __script = {};
async function jsonRequest(url, opts) {
    __requested.push(url);
    const s = __script[url];
    if (s instanceof Error) throw s;
    if (s === undefined) throw new Error('connection refused: ' + url);
    return s;
}
let __cfg = {};
function loadDesktopConfig() { return JSON.parse(JSON.stringify(__cfg)); }
function saveDesktopConfig(patch) { __cfg = Object.assign({}, __cfg, patch); return __cfg; }
function resetCfg(next) { __cfg = JSON.parse(JSON.stringify(next || {})); }
let __pyExe = '/fake/python';
function resolvePythonExe() { return __pyExe; }
let __spawnResult = null;
let __spawnThrows = null;
let __spawnCalls = [];
function spawnSync(exe, args, opts) {
    __spawnCalls.push({ exe: exe, args: args });
    if (__spawnThrows) throw __spawnThrows;
    return __spawnResult;
}
function resetSpawn() { __spawnCalls = []; __spawnResult = null; __spawnThrows = null; }
let __failures = [];
function check(name, cond, extra) {
    if (!cond) __failures.push(name + (extra ? ' :: ' + extra : ''));
}
function resetFake() { __requested = []; __script = {}; }
"""


@node_only
def test_parse_host_input_preserves_scheme_and_port():
    script = (
        NODE_PRELUDE
        + _extract_main_js()
        + """
let p = parseHostInput('https://192.168.1.20:8443');
check('explicit-https-host', p.host === '192.168.1.20', JSON.stringify(p));
check('explicit-https-port', p.httpsPort === 8443, JSON.stringify(p));
check('explicit-https-http-untouched', p.httpPort === 8000, JSON.stringify(p));
check('explicit-https-prefer', p.preferredScheme === 'https', JSON.stringify(p));
check('explicit-https-explicit', p.explicitPort === true, JSON.stringify(p));

p = parseHostInput('http://192.168.1.20:8001');
check('explicit-http-port', p.httpPort === 8001, JSON.stringify(p));
check('explicit-http-https-untouched', p.httpsPort === 8080, JSON.stringify(p));
check('explicit-http-prefer', p.preferredScheme === 'http', JSON.stringify(p));

p = parseHostInput('192.168.1.21:9000');
check('bare-host-port-is-http', p.host === '192.168.1.21' && p.httpPort === 9000, JSON.stringify(p));
check('bare-host-port-prefer', p.preferredScheme === 'http', JSON.stringify(p));

p = parseHostInput('192.168.1.21');
check('bare-host-defaults', p.httpPort === 8000 && p.httpsPort === 8080, JSON.stringify(p));
check('bare-host-no-prefer', !p.preferredScheme && p.explicitPort === false, JSON.stringify(p));

p = parseHostInput('https://cuttle.local');
check('scheme-no-port-prefer', p.preferredScheme === 'https' && p.explicitPort === false, JSON.stringify(p));
check('scheme-no-port-443', p.httpsPort === 443, JSON.stringify(p));
check('scheme-no-port-single', p.single === true, JSON.stringify(p));

p = parseHostInput('http://cuttle.local');
check('http-no-port-80', p.httpPort === 80 && p.preferredScheme === 'http' && p.single === true, JSON.stringify(p));

p = parseHostInput('https://cuttle.local:443/x');
check('explicit-443-elided', p.httpsPort === 443 && p.explicitPort === true, JSON.stringify(p));

p = parseHostInput('http://cuttle.local:80/');
check('explicit-80-elided', p.httpPort === 80 && p.explicitPort === true, JSON.stringify(p));

p = parseHostInput('[::1]:8001');
check('ipv6-bracket-parse', p.host === '::1' && p.httpPort === 8001 && p.single === true, JSON.stringify(p));
check('ipv6-format', formatHost(p.host) === '[::1]', formatHost(p.host));
check('ipv4-format', formatHost('192.168.1.20') === '192.168.1.20', formatHost('192.168.1.20'));

for (const bad of ['myhost:abc', 'myhost:0', 'myhost:70000', 'myhost:-1', 'myhost:80x', 'myhost:']) {
    let threw = false;
    try { parseHostInput(bad); } catch (e) { threw = /port 1-65535|host or URL/i.test(e.message); }
    check('malformed-fails-closed:' + bad, threw, 'no throw or wrong error');
}
for (const bad of ['ftp://h/x', 'gopher://h:70/', 'file:///etc/passwd', 'cuttle://h:8080',
    'http://user:h@host/', 'http://user@host/', 'https://u:p@h:8443/',
    '[::1', '[::1]x', '[]', '[]:8000', '[::1]:abc', '::1']) {
    let threw = false;
    try { parseHostInput(bad); } catch (e) { threw = /scheme|credentials|host or URL|host address|port 1-65535/i.test(e.message); }
    check('rejected:' + bad, threw, 'accepted: ' + bad);
}
if (__failures.length) { console.error(__failures.join('\\n')); process.exit(1); }
console.log('ok');
"""
    )
    proc = _run_node(script)
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


@node_only
def test_probe_explicit_https_tries_only_selected_endpoint():
    script = (
        NODE_PRELUDE
        + _extract_main_js()
        + """
(async () => {
const parsed = parseHostInput('https://192.168.1.20:8443');
check('parsed-single-policy', parsed.single === true, JSON.stringify(parsed));
__script['https://192.168.1.20:8443/api/health'] = { status: 200, json: { service: 'cuttle' } };
const r = await probeCuttle(parsed.host, parsed.httpPort, parsed.httpsPort, {
    prefer: parsed.preferredScheme || undefined,
    single: parsed.single && !!parsed.preferredScheme,
});
check('single-ok', r.ok === true, JSON.stringify(r));
check('single-scheme', r.scheme === 'https', JSON.stringify(r));
check('single-uiurl', r.uiUrl === 'https://192.168.1.20:8443/app_shell.html', JSON.stringify(r));
check('single-no-unrelated-probe', __requested.length === 1 && __requested[0].startsWith('https://192.168.1.20:8443/'),
    JSON.stringify(__requested));

// Explicit endpoint down: still no downgrade probe, error names the failure.
resetFake();
__script['https://192.168.1.20:8443/api/health'] = new Error('refused');
const r2 = await probeCuttle(parsed.host, parsed.httpPort, parsed.httpsPort, {
    prefer: 'https', single: true,
});
check('single-down-not-ok', r2.ok === false, JSON.stringify(r2));
check('single-down-no-fallback', __requested.length === 1, JSON.stringify(__requested));

// Legacy bare host keeps http-first order with both candidates.
resetFake();
__script['http://192.168.1.21:8000/api/health'] = { status: 200, json: { status: 'ok' } };
const r3 = await probeCuttle('192.168.1.21', 8000, 8080, {});
check('legacy-http-first', r3.ok && r3.scheme === 'http' && __requested[0].startsWith('http://'), JSON.stringify({ r: r3, q: __requested }));

// Prefer-https without single: https first, http fallback on failure.
resetFake();
__script['https://192.168.1.22:8080/api/health'] = new Error('refused');
__script['http://192.168.1.22:8000/api/health'] = { status: 200, json: { service: 'cuttle' } };
const r4 = await probeCuttle('192.168.1.22', 8000, 8080, { prefer: 'https' });
check('prefer-order', r4.ok && r4.scheme === 'http'
    && __requested.length === 2 && __requested[0].startsWith('https://'), JSON.stringify({ r: r4, q: __requested }));

if (__failures.length) { console.error(__failures.join('\\n')); process.exit(1); }
console.log('ok');
})();
"""
    )
    proc = _run_node(script)
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def _self_signed_pem(cn: str = "localhost") -> str:
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now).not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


@node_only
def test_cert_allowcheck_loopback_only_and_remote_by_pin():
    """Self-signed trust is loopback-only; a remote Host needs its key pin."""
    pinned = _self_signed_pem("host")
    other = _self_signed_pem("impostor")
    script = (
        NODE_PRELUDE
        + _extract_main_js()
        + f"""
const PINNED = {json.dumps(pinned)};
const OTHER = {json.dumps(other)};
"""
        + """
FLASK_HOST = '192.168.1.20';
FLASK_HTTP_PORT = 8001;
FLASK_HTTPS_PORT = 8443;
resetCfg({});
check('deny-remote-hostname-trust', isCuttleSelfSignedHttpsUrl('https://192.168.1.20:8443/app_shell.html') === false);
check('deny-remote-wss-hostname-trust', isCuttleSelfSignedHttpsUrl('wss://192.168.1.20:8443/api/terminal/ws') === false);
check('allow-loopback-selected-port', isCuttleSelfSignedHttpsUrl('https://127.0.0.1:8443/') === true);
check('allow-localhost-selected-port', isCuttleSelfSignedHttpsUrl('wss://localhost:8443/x') === true);
check('deny-loopback-other-port', isCuttleSelfSignedHttpsUrl('https://127.0.0.1:8888/') === false);
check('deny-plain-http', isCuttleSelfSignedHttpsUrl('http://127.0.0.1:8443/') === false);
const cert = { data: PINNED };
check('deny-unpinned-remote', isPinnedCuttleCertificate('https://192.168.1.20:8443/', cert) === false);
resetCfg({ tlsPins: { '192.168.1.20:8443': { spki: tlsTrust.spkiSha256(PINNED) } } });
check('allow-pinned-https', isPinnedCuttleCertificate('https://192.168.1.20:8443/app_shell.html', cert) === true);
check('allow-pinned-wss', isPinnedCuttleCertificate('wss://192.168.1.20:8443/api/terminal/ws', cert) === true);
check('deny-other-key', isPinnedCuttleCertificate('https://192.168.1.20:8443/', { data: OTHER }) === false);
check('deny-other-host', isPinnedCuttleCertificate('https://192.168.1.21:8443/', cert) === false);
check('deny-other-port', isPinnedCuttleCertificate('https://192.168.1.20:8080/', cert) === false);
check('deny-omitted-port-vs-8443', isPinnedCuttleCertificate('https://192.168.1.20/', cert) === false);
check('deny-no-cert', isPinnedCuttleCertificate('https://192.168.1.20:8443/', null) === false);
if (__failures.length) { console.error(__failures.join('\\n')); process.exit(1); }
console.log('ok');
"""
    )
    proc = _run_node(script)
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


@node_only
def test_tls_trust_requests_need_pin_for_remote_hosts():
    pem = _self_signed_pem("host")
    script = f"""
const T = require('./electron/tls-trust.js');
const PEM = {json.dumps(pem)};
"""
    script += """
const fails = [];
const check = (n, c) => { if (!c) fails.push(n); };
check('loopback-self-signed', T.requestOptions('127.0.0.1', 8080, {}).rejectUnauthorized === false);
check('loopback-127-8', T.isLoopbackHost('127.0.0.5') && T.isLoopbackHost('[::1]') && !T.isLoopbackHost('192.168.1.2'));
let code = '';
try { T.requestOptions('192.168.1.20', 8443, {}); } catch (e) { code = e.code; }
check('remote-unpinned-throws', code === 'CUTTLE_TLS_UNPINNED');
const spki = T.spkiSha256(PEM);
const opts = T.requestOptions('192.168.1.20', 8443, { '192.168.1.20:8443': { spki } });
check('remote-pinned-agent', opts.agent instanceof T.PinnedAgent && opts.rejectUnauthorized === undefined);
check('pin-key-normalized', T.pinKey('[FE80::1]', '8443') === 'fe80::1:8443');
check('fingerprint-format', /^([0-9A-F]{2}:){31}[0-9A-F]{2}$/.test(T.formatFingerprint(spki)));
if (fails.length) { console.error(fails.join('\\n')); process.exit(1); }
console.log('ok');
"""
    proc = _run_node(script)
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def test_host_fingerprint_matches_client_pin(monkeypatch, tmp_path):
    """api.tls_cert pins the same SPKI hash the Client computes, and keeps the
    key when the certificate is re-issued for a new LAN IP."""
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))
    sys.path.insert(0, str(SRC_DIR))
    from api import tls_cert

    cert_path, _key = tls_cert.ensure_certificate(lan_ip="192.168.1.20")
    first = tls_cert.current_fingerprint()
    tls_cert.ensure_certificate(lan_ip="192.168.1.30")
    second = tls_cert.current_fingerprint()
    assert first["spki_sha256"] == second["spki_sha256"]
    pem = Path(cert_path).read_text()
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding

    cert = x509.load_pem_x509_certificate(pem.encode())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "192.168.1.30" in {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
    if shutil.which("node"):
        proc = _run_node(
            "const T=require('./electron/tls-trust.js');"
            f"process.stdout.write(T.spkiSha256({json.dumps(pem)}));"
        )
        assert proc.stdout == first["spki_sha256"], proc.stderr
    sidecar = _load_sidecar(monkeypatch)
    assert sidecar.spki_sha256(cert.public_bytes(Encoding.DER)) == first["spki_sha256"]


def test_sidecar_refuses_remote_https_without_pin(monkeypatch):
    mod = _load_sidecar(monkeypatch)
    monkeypatch.delenv("CUTTLE_COORDINATOR_TLS_SPKI_SHA256", raising=False)
    with pytest.raises(RuntimeError, match="no pinned certificate key"):
        mod.http_json("GET", "https://192.168.1.20:8443", "/api/workers/x", token="t", timeout=1)


@node_only
def test_pool_stall_target_never_guesses_unrelated_port():
    script = (
        """
let __failures = [];
function check(name, cond, extra) {
    if (!cond) __failures.push(name + (extra ? ' :: ' + extra : ''));
}
"""
        + _extract_pool_helper()
        + """
(async () => {
const single8443 = {
    host: '192.168.1.20', httpPort: 8000, httpsPort: 8443,
    endpoint: { kind: 'single', scheme: 'https', host: '192.168.1.20', port: 8443,
        companion: { scheme: 'http', host: '192.168.1.20', port: 8001 } },
};
const singleNoComp = {
    host: '192.168.1.20', httpPort: 8000, httpsPort: 8443,
    endpoint: { kind: 'single', scheme: 'https', host: '192.168.1.20', port: 8443, companion: null },
};

// True legacy default-mode (no config, e.g. plain browser) keeps :8000.
let t = await poolStallHttpTarget({ origin: 'https://127.0.0.1:8080', getDesktopConfig: async () => null });
check('default-companion', t && t.destBase === 'http://127.0.0.1:8000' && t.httpPort === 8000, JSON.stringify(t));

// Same default origin WITH a custom companion consults config, not :8000.
t = await poolStallHttpTarget({
    origin: 'https://127.0.0.1:8080',
    getDesktopConfig: async () => ({
        host: '127.0.0.1', httpPort: 8001, httpsPort: 8080,
        endpoint: { kind: 'single', scheme: 'https', host: '127.0.0.1', port: 8080,
            companion: { scheme: 'http', host: '127.0.0.1', port: 8001 } },
    }),
});
check('single-default-no-downgrade', t === null, JSON.stringify(t));

// Explicit single on :8080 WITHOUT a known companion: no downgrade.
t = await poolStallHttpTarget({
    origin: 'https://127.0.0.1:8080',
    getDesktopConfig: async () => ({
        host: '127.0.0.1', httpPort: 8000, httpsPort: 8080,
        endpoint: { kind: 'single', scheme: 'https', host: '127.0.0.1', port: 8080, companion: null },
    }),
});
check('single-8080-no-companion-null', t === null, JSON.stringify(t));

// Custom loopback single policy also refuses an HTTP companion.
t = await poolStallHttpTarget({
    origin: 'https://127.0.0.1:8443',
    getDesktopConfig: async () => ({
        host: '127.0.0.1', httpPort: 8001, httpsPort: 8443,
        endpoint: { kind: 'single', scheme: 'https', host: '127.0.0.1', port: 8443,
            companion: { scheme: 'http', host: '127.0.0.1', port: 8001 } },
    }),
});
check('single-custom-no-downgrade', t === null, JSON.stringify(t));

// Explicit remote refuses HTTP even when old metadata advertises it.
t = await poolStallHttpTarget({
    origin: 'https://192.168.1.20:8443',
    getDesktopConfig: async () => single8443,
});
check('explicit-remote-no-downgrade-with-metadata', t === null, JSON.stringify(t));

// Explicit remote with NO known companion: no downgrade, never guess :8000.
t = await poolStallHttpTarget({
    origin: 'https://192.168.1.20:8443',
    getDesktopConfig: async () => singleNoComp,
});
check('explicit-remote-no-downgrade', t === null, JSON.stringify(t));

// Config selected port differs from the stalled origin: mismatch, no redirect.
t = await poolStallHttpTarget({
    origin: 'https://192.168.1.20:8443',
    getDesktopConfig: async () => ({
        host: '192.168.1.20', httpPort: 8001, httpsPort: 9999,
        endpoint: { kind: 'single', scheme: 'https', host: '192.168.1.20', port: 9999,
            companion: { scheme: 'http', host: '192.168.1.20', port: 8001 } },
    }),
});
check('https-port-mismatch-null', t === null, JSON.stringify(t));

// Mismatched config host: fail closed.
t = await poolStallHttpTarget({
    origin: 'https://192.168.1.20:8443',
    getDesktopConfig: async () => ({
        host: '192.168.1.99', httpPort: 8001, httpsPort: 8443,
        endpoint: { kind: 'single', scheme: 'https', host: '192.168.1.99', port: 8443,
            companion: { scheme: 'http', host: '192.168.1.99', port: 8001 } },
    }),
});
check('mismatched-host-no-redirect', t === null, JSON.stringify(t));

// Local paired mode keeps the configured custom listener pair.
t = await poolStallHttpTarget({
    origin: 'https://127.0.0.1:8443',
    getDesktopConfig: async () => ({ host: '127.0.0.1', httpPort: 8001, httpsPort: 8443,
        endpoint: { kind: 'paired', scheme: 'http', host: '127.0.0.1', port: 8001 } }),
});
check('paired-custom-pool', t && t.destBase === 'http://127.0.0.1:8001', JSON.stringify(t));

// Non-HTTPS origins never redirect.
t = await poolStallHttpTarget({ origin: 'http://192.168.1.20:8001', getDesktopConfig: null });
check('http-origin-null', t === null, JSON.stringify(t));

if (__failures.length) { console.error(__failures.join('\\n')); process.exit(1); }
console.log('ok');
})();
"""
    )
    proc = _run_node(script)
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def _scrubbed_env() -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("CUTTLE_HTTPS_PORT", "CUTTLE_HTTP_PORT", "CUTTLE_PHONE_HTTPS_PORT")
    }
    env["PYTHONPATH"] = str(SRC_DIR)
    return env


def _run_ports_cli(args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "api.server_ports", *args],
        capture_output=True,
        text=True, encoding="utf-8",
        timeout=60,
        cwd=str(REPO),
        env=env,
    )


def test_ports_cli_defaults_from_empty_file(tmp_path):
    env_file = tmp_path / "empty.env"
    env_file.write_text("# nothing set" + chr(10), encoding="utf-8")
    proc = _run_ports_cli(["--env-file", str(env_file)], _scrubbed_env())
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"https": 8080, "http": 8000, "phone_https": 8888}


def test_ports_cli_custom_triple_from_file(tmp_path):
    env_file = tmp_path / "custom.env"
    env_file.write_text(
        chr(10).join(
            [
                "CUTTLE_HTTPS_PORT=8443",
                "CUTTLE_HTTP_PORT=8001",
                "CUTTLE_PHONE_HTTPS_PORT=8890",
                "",
            ]
        ),
        encoding="utf-8",
    )
    proc = _run_ports_cli(["--env-file", str(env_file)], _scrubbed_env())
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"https": 8443, "http": 8001, "phone_https": 8890}


def test_ports_cli_malformed_fails_closed_without_fallback(tmp_path):
    env_file = tmp_path / "bad.env"
    env_file.write_text("CUTTLE_HTTPS_PORT=notaport" + chr(10), encoding="utf-8")
    proc = _run_ports_cli(["--env-file", str(env_file)], _scrubbed_env())
    assert proc.returncode == 2
    assert "notaport" not in (proc.stdout or "")
    assert "invalid listener-port configuration" in (proc.stderr or "")


def test_ports_cli_output_never_exposes_other_keys(tmp_path):
    env_file = tmp_path / "secret.env"
    env_file.write_text(
        chr(10).join(["DISCORD_TOKEN=super-secret", "CUTTLE_HTTPS_PORT=8443", ""]),
        encoding="utf-8",
    )
    proc = _run_ports_cli(["--env-file", str(env_file)], _scrubbed_env())
    assert proc.returncode == 0, proc.stderr
    body = json.loads(proc.stdout)
    assert set(body) == {"https", "http", "phone_https"}
    assert "super-secret" not in (proc.stdout + proc.stderr)


def _load_sidecar(monkeypatch, **env):
    for key in (
        "CUTTLE_DEVICE_WORKERS_COORDINATOR_URL",
        "CUTTLE_DEVICE_WORKERS_COORDINATOR_URL_HTTP",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location("electron_sidecar_ports", str(SIDECAR))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sidecar_explicit_companion_used_verbatim(monkeypatch):
    mod = _load_sidecar(
        monkeypatch,
        CUTTLE_DEVICE_WORKERS_COORDINATOR_URL="https://192.168.1.20:8443",
        CUTTLE_DEVICE_WORKERS_COORDINATOR_URL_HTTP="http://192.168.1.20:8001",
    )
    assert mod.pick_base_urls() == [
        "https://192.168.1.20:8443",
        "http://192.168.1.20:8001",
    ]


def test_sidecar_custom_primary_never_guesses_8000(monkeypatch):
    mod = _load_sidecar(
        monkeypatch,
        CUTTLE_DEVICE_WORKERS_COORDINATOR_URL="https://192.168.1.20:8443",
    )
    assert mod.pick_base_urls() == ["https://192.168.1.20:8443"]


def test_sidecar_default_mode_keeps_documented_pair(monkeypatch):
    mod = _load_sidecar(
        monkeypatch,
        CUTTLE_DEVICE_WORKERS_COORDINATOR_URL="https://192.168.1.20:8080",
    )
    assert mod.pick_base_urls() == [
        "https://192.168.1.20:8080",
        "http://192.168.1.20:8000",
    ]


def test_sidecar_single_policy_suppresses_default_inference(monkeypatch):
    mod = _load_sidecar(
        monkeypatch,
        CUTTLE_DEVICE_WORKERS_COORDINATOR_URL="https://192.168.1.20:8080",
        CUTTLE_ENDPOINT_SINGLE="1",
    )
    assert mod.pick_base_urls() == ["https://192.168.1.20:8080"]


@node_only
def test_endpoint_candidates_follow_policy_order():
    script = (
        NODE_PRELUDE
        + _extract_main_js()
        + """
FLASK_HOST = '192.168.1.20';
FLASK_HTTP_PORT = 8000;
FLASK_HTTPS_PORT = 8443;

// Single policy ignores historical companion metadata.
resetCfg({ endpoint: { kind: 'single', scheme: 'https', host: '192.168.1.20', port: 8443,
    companion: { scheme: 'http', host: '192.168.1.20', port: 8001 } } });
let c = endpointCandidates();
check('single-order', c.length === 1 && c[0].scheme === 'https' && c[0].port === 8443, JSON.stringify(c));
check('single-flag', isSingleEndpointPolicy() === true);
let urls = candidateUrls('/api/health');
check('single-urls', urls.length === 1
    && urls[0] === 'https://192.168.1.20:8443/api/health', JSON.stringify(urls));

// Single without companion: exactly one candidate.
resetCfg({ endpoint: { kind: 'single', scheme: 'https', host: '192.168.1.20', port: 8443, companion: null } });
c = endpointCandidates();
check('single-lonely', c.length === 1 && c[0].port === 8443, JSON.stringify(c));

// Paired local policy: both listeners, HTTP first.
resetCfg({ endpoint: { kind: 'paired', scheme: 'http', host: '127.0.0.1', port: 8001,
    companion: { scheme: 'https', host: '127.0.0.1', port: 8443 } } });
FLASK_HOST = '127.0.0.1'; FLASK_HTTP_PORT = 8001; FLASK_HTTPS_PORT = 8443;
c = endpointCandidates();
check('paired-order', c.length === 2 && c[0].scheme === 'http' && c[0].port === 8001
    && c[1].scheme === 'https' && c[1].port === 8443, JSON.stringify(c));
check('paired-not-single', isSingleEndpointPolicy() === false);

// Legacy config without a policy: live-target pair, HTTP first.
resetCfg({ host: '192.168.1.20' });
FLASK_HOST = '192.168.1.20'; FLASK_HTTP_PORT = 8000; FLASK_HTTPS_PORT = 8080;
c = endpointCandidates();
check('legacy-pair', c.length === 2 && c[0].port === 8000 && c[1].port === 8080, JSON.stringify(c));

// IPv6 authorities are bracketed in candidate URLs.
FLASK_HOST = '::1'; FLASK_HTTP_PORT = 8001; FLASK_HTTPS_PORT = 8443;
resetCfg({});
urls = candidateUrls('/api/health');
check('ipv6-brackets', urls[0] === 'http://[::1]:8001/api/health'
    && urls[1] === 'https://[::1]:8443/api/health', JSON.stringify(urls));

if (__failures.length) { console.error(__failures.join('\\n')); process.exit(1); }
console.log('ok');
"""
    )
    proc = _run_node(script)
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


@node_only
def test_explicit_endpoints_ignore_advertised_companions_on_connect_and_restore():
    script = NODE_PRELUDE + _extract_main_js() + """
(async () => {
FLASK_HOST = '192.168.1.20';
for (const url of ['https://192.168.1.20:8443', 'https://192.168.1.20', 'http://192.168.1.20:8001']) {
    resetFake();
    const parsed = parseHostInput(url);
    const base = parsed.preferredScheme + '://' + parsed.host + ':' +
        (parsed.preferredScheme === 'https' ? parsed.httpsPort : parsed.httpPort);
    __script[base + '/api/health'] = { status: 200, json: { service: 'cuttle' } };
    __script[base + '/api/desktop/electron'] = { status: 200, json: {
        ports: { https: 8443, http: 8001, phone_https: 8890 } } };
    const r = await probeAndResolve(parsed);
    check('selected-policy', r.policy && r.policy.companion === null && r.policy.kind === 'single', JSON.stringify(r));
    check('selected-only-connect', __requested.length === 1 && __requested[0] === base + '/api/health', JSON.stringify(__requested));
    resetCfg({ endpoint: { ...r.policy, companion: { scheme: 'http', port: 8001 } } });
    resetFake();
    __script[base + '/api/health'] = new Error('refused');
    __script['http://192.168.1.20:8001/api/health'] = { status: 200, json: { service: 'cuttle' } };
    if (r.policy.scheme === 'http') __script[base + '/api/health'] = new Error('refused');
    const restored = await probeUrlList(endpointCandidates());
    check('restore-no-fallback', !restored.ok && __requested.length === 1 && __requested[0] === base + '/api/health', JSON.stringify(__requested));
}
if (__failures.length) { console.error(__failures.join('\\n')); process.exit(1); }
})();
"""
    proc = _run_node(script)
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


@node_only
def test_local_port_query_error_codes():
    script = (
        NODE_PRELUDE
        + _extract_main_js()
        + """
resetSpawn();
__spawnResult = { status: 2, stdout: '', stderr: 'invalid listener-port configuration: bad' };
let code = '';
try { queryLocalServerPorts('/repo'); } catch (e) { code = e.code || ''; }
check('malformed-code', code === 'local-port-config', code);
check('malformed-no-spawn-probe', __spawnCalls.length === 1
    && __spawnCalls[0].args.join(' ') === '-m api.server_ports', JSON.stringify(__spawnCalls));

resetSpawn();
__spawnResult = { status: 0, stdout: '{"https": 8443, "http": 8001, "phone_https": 8890}\\n', stderr: '' };
let ports = null;
try { ports = queryLocalServerPorts('/repo'); } catch (e) { check('query-threw', false, e.message); }
check('query-triple', ports && ports.https === 8443 && ports.http === 8001 && ports.phone_https === 8890,
    JSON.stringify(ports));

resetSpawn();
__spawnThrows = new Error('spawn ENOENT');
code = '';
try { queryLocalServerPorts('/repo'); } catch (e) { code = e.code || ''; }
check('spawn-failure-code', code === 'local-port-unavailable', code);

__pyExe = null;
code = '';
try { queryLocalServerPorts('/repo'); } catch (e) { code = e.code || ''; }
check('no-python-code', code === 'local-port-unavailable', code);
__pyExe = '/fake/python';

if (__failures.length) { console.error(__failures.join('\\n')); process.exit(1); }
console.log('ok');
"""
    )
    proc = _run_node(script)
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def _load_client_daemon():
    path = SRC_DIR / "scripts" / "cuttle_client_daemon.py"
    spec = importlib.util.spec_from_file_location("cuttle_client_daemon_ports", str(path))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_client_daemon_bases_follow_single_policy():
    mod = _load_client_daemon()
    cfg = {
        "host": "192.168.1.20",
        "httpsPort": 8443,
        "httpPort": 8000,
        "endpoint": {
            "kind": "single",
            "scheme": "https",
            "host": "192.168.1.20",
            "port": 8443,
            "companion": {"scheme": "http", "host": "192.168.1.20", "port": 8001},
        },
    }
    assert mod.coordinator_bases(cfg, "192.168.1.20") == [
        "https://192.168.1.20:8443",
    ]
    lonely = dict(cfg)
    lonely["endpoint"] = dict(cfg["endpoint"], companion=None)
    assert mod.coordinator_bases(lonely, "192.168.1.20") == ["https://192.168.1.20:8443"]
    # Corrupt/mismatched explicit policy fails closed instead of guessing ports.
    with pytest.raises(ValueError):
        mod.coordinator_bases(cfg, "192.168.1.99")
    assert mod.coordinator_bases({"host": "h", "httpsPort": 8443, "httpPort": 8001}, "h") == [
        "https://h:8443",
        "http://h:8001",
    ]


@node_only
def test_worker_and_ui_orders_come_from_candidates():
    script = (
        NODE_PRELUDE
        + _extract_main_js()
        + """
let __portsUp = {};
async function waitForPort(port) { return !!__portsUp[Number(port)]; }
(async () => {
// Single HTTPS keeps the selected coordinator and ignores old HTTP metadata.
FLASK_HOST = '192.168.1.20'; FLASK_HTTP_PORT = 8000; FLASK_HTTPS_PORT = 8443;
resetCfg({ endpoint: { kind: 'single', scheme: 'https', host: '192.168.1.20', port: 8443,
    companion: { scheme: 'http', host: '192.168.1.20', port: 8001 } } });
let w = workerCoordinatorEnv();
check('worker-single', w.primary === 'https://192.168.1.20:8443'
    && w.https === 'https://192.168.1.20:8443'
    && w.http === '' && w.single === true, JSON.stringify(w));

// Single without companion: no HTTP fallback emitted (never a guess).
resetCfg({ endpoint: { kind: 'single', scheme: 'https', host: '192.168.1.20', port: 8443, companion: null } });
w = workerCoordinatorEnv();
check('worker-single-no-http', w.http === '' && w.primary === 'https://192.168.1.20:8443', JSON.stringify(w));

// UI load over the single policy uses the selected endpoint first.
resetCfg({ endpoint: { kind: 'single', scheme: 'https', host: '192.168.1.20', port: 8443,
    companion: { scheme: 'http', host: '192.168.1.20', port: 8001 } } });
__portsUp = { 8443: true, 8001: false };
let ui = await resolveUiBaseUrl();
check('ui-single-first', ui === 'https://192.168.1.20:8443/app_shell.html', ui);

// Selected down, HTTP up: explicit HTTPS still never changes endpoint.
__portsUp = { 8443: false, 8001: true };
ui = await resolveUiBaseUrl();
check('ui-single-no-downgrade', ui === 'https://192.168.1.20:8443/app_shell.html', ui);

// Paired local policy: HTTP first.
FLASK_HOST = '127.0.0.1'; FLASK_HTTP_PORT = 8001; FLASK_HTTPS_PORT = 8443;
resetCfg({ endpoint: { kind: 'paired', scheme: 'http', host: '127.0.0.1', port: 8001,
    companion: { scheme: 'https', host: '127.0.0.1', port: 8443 } } });
__portsUp = { 8001: true, 8443: true };
ui = await resolveUiBaseUrl();
check('ui-paired-http-first', ui === 'http://127.0.0.1:8001/app_shell.html', ui);
w = workerCoordinatorEnv();
check('worker-paired', w.primary === 'http://127.0.0.1:8001'
    && w.http === 'http://127.0.0.1:8001'
    && w.https === 'https://127.0.0.1:8443' && w.single === false, JSON.stringify(w));

if (__failures.length) { console.error(__failures.join('\\n')); process.exit(1); }
console.log('ok');
})();
"""
    )
    proc = _run_node(script)
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def _run_client_daemon_main(monkeypatch, tmp_path, cfg):
    """Run cuttle_client_daemon.main with private HOME and fake seams."""
    mod = _load_client_daemon()
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Path.home() on Windows
    # main() exports worker settings into os.environ; restore them afterwards.
    for name in ("CUTTLE_DEVICE_WORKERS_ENABLED", "CUTTLE_DEVICE_WORKERS_COORDINATOR_URL",
                 "CUTTLE_DEVICE_WORKERS_COORDINATOR_URL_HTTP", "CUTTLE_DEVICE_WORKERS_TOKEN",
                 "CUTTLE_DEVICE_WORKER_ID", "CUTTLE_CLIENT_DAEMON", "CUTTLE_REPO_ROOT",
                 "CUTTLE_DEVICE_WORKER_LOG"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    conf_dir = tmp_path / "cuttle-desktop"
    conf_dir.mkdir(parents=True, exist_ok=True)
    (conf_dir / "desktop-config.json").write_text(json.dumps(cfg), encoding="utf-8")
    calls: dict = {}

    def fake_enroll(cfg_arg, bases):
        calls["enroll_bases"] = list(bases)
        return {"success": True, "token": "tok", "worker_id": "w1"}

    monkeypatch.setattr(mod, "enroll", fake_enroll)
    import api.device_workers.worker_loop as worker_loop

    def fake_loop(*, should_continue, base_url):
        calls["worker_base"] = base_url
        assert should_continue() is True

    monkeypatch.setattr(worker_loop, "run_remote_worker_loop", fake_loop)
    rc = mod.main()
    assert rc == 0
    status = json.loads((conf_dir / "client-daemon-status.json").read_text(encoding="utf-8"))
    calls["status_coordinator"] = status.get("coordinator")
    return calls


def test_client_daemon_main_keeps_single_http_first(monkeypatch, tmp_path):
    """Regression: main used to promote the HTTPS companion above an
    explicitly selected HTTP endpoint (scheme-preferred pick)."""
    cfg = {
        "host": "192.168.1.20",
        "httpsPort": 8443,
        "httpPort": 80,
        "workerId": "w1",
        "endpoint": {
            "kind": "single",
            "scheme": "http",
            "host": "192.168.1.20",
            "port": 80,
            "companion": {"scheme": "https", "host": "192.168.1.20", "port": 8443},
        },
    }
    calls = _run_client_daemon_main(monkeypatch, tmp_path, cfg)
    assert calls["enroll_bases"] == [
        "http://192.168.1.20:80",
    ]
    assert calls["worker_base"] == "http://192.168.1.20:80"
    assert calls["status_coordinator"] == "http://192.168.1.20:80"


def test_client_daemon_main_keeps_single_https_first(monkeypatch, tmp_path):
    cfg = {
        "host": "192.168.1.20",
        "httpsPort": 8443,
        "httpPort": 8000,
        "workerId": "w1",
        "endpoint": {
            "kind": "single",
            "scheme": "https",
            "host": "192.168.1.20",
            "port": 8443,
            "companion": {"scheme": "http", "host": "192.168.1.20", "port": 8001},
        },
    }
    calls = _run_client_daemon_main(monkeypatch, tmp_path, cfg)
    assert calls["enroll_bases"] == ["https://192.168.1.20:8443"]
    assert calls["worker_base"] == "https://192.168.1.20:8443"
    assert calls["status_coordinator"] == "https://192.168.1.20:8443"




@node_only
def test_explicit_https_api_enrollment_and_download_never_downgrade():
    script = NODE_PRELUDE + _extract_main_js() + """
(async () => {
FLASK_HOST = '192.168.1.20'; FLASK_HTTP_PORT = 8001; FLASK_HTTPS_PORT = 8443;
resetCfg({ endpoint: { kind: 'single', host: FLASK_HOST, scheme: 'https', port: 8443,
    companion: { scheme: 'http', host: FLASK_HOST, port: 8001 } } });
for (const [path, operation] of [
    ['/api/flask/restart/status', () => desktopApiGet('/api/flask/restart/status')],
    ['/api/workers/enroll', () => enrollWorkerWithHost()],
]) {
    resetFake();
    __script['https://192.168.1.20:8443' + path] = new Error('TLS failed');
    __script['http://192.168.1.20:8001' + path] = { status: 200, json: { success: true, token: 'fixture' } };
    let failed = false;
    try { await operation(); } catch (_) { failed = true; }
    check('failure-kept', failed);
    check('selected-only-request', __requested.length === 1 && __requested[0] === 'https://192.168.1.20:8443' + path, JSON.stringify(__requested));
}
// Updates: only the pinned HTTPS endpoint (identity checked first).
__pinChecks = [];
const base = await pinnedUpdateBase();
check('update-base-https', base === 'https://192.168.1.20:8443', base);
check('update-pin-checked', __pinChecks.join(',') === '192.168.1.20:8443', __pinChecks.join(','));
resetCfg({ endpoint: { kind: 'single', host: FLASK_HOST, scheme: 'http', port: 8001 } });
let refused = false;
try { await pinnedUpdateBase(); } catch (_) { refused = true; }
check('update-refuses-http-only', refused);
resetCfg({ endpoint: { kind: 'single', host: FLASK_HOST, scheme: 'https', port: 8443 } });
__pinError = Object.assign(new Error('declined'), { code: 'CUTTLE_TLS_DECLINED' });
refused = false;
try { await pinnedUpdateBase(); } catch (_) { refused = true; }
check('update-refuses-untrusted', refused);
__pinError = null;
FLASK_HOST = '127.0.0.1';
refused = false;
try { await pinnedUpdateBase(); } catch (_) { refused = true; }
check('update-refuses-local-host', refused);
if (__failures.length) { console.error(__failures.join('\\n')); process.exit(1); }
})();
"""
    proc = _run_node(script)
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def test_sidecar_single_ignores_stale_http_fallback(monkeypatch):
    mod = _load_sidecar(
        monkeypatch,
        CUTTLE_DEVICE_WORKERS_COORDINATOR_URL="https://192.168.1.20:8443",
        CUTTLE_DEVICE_WORKERS_COORDINATOR_URL_HTTP="http://192.168.1.20:8001",
        CUTTLE_ENDPOINT_SINGLE="1",
    )
    assert mod.pick_base_urls() == ["https://192.168.1.20:8443"]
