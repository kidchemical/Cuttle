'use strict';
/**
 * Remote Host TLS trust for the desktop shell (one owner).
 *
 * - Loopback (the local Host) keeps the self-signed exception.
 * - Every other HTTPS host is trusted only when its certificate's public key
 *   (SHA-256 of the DER SubjectPublicKeyInfo, base64) matches the pin saved
 *   for that host:port. Pins are trust-on-first-use: main.js asks the user
 *   before saving one, and never replaces one without an explicit confirm.
 * - No pin, or a mismatch, means no trust. Callers never fall back to
 *   unverified TLS or to plain HTTP.
 *
 * Pinning the key (not the whole certificate) survives the Host re-issuing
 * its certificate for a new LAN IP, because api.tls_cert keeps the key.
 */

const crypto = require('crypto');
const https = require('https');
const tls = require('tls');
const net = require('net');

function normalizeHost(host) {
    return String(host || '').trim().toLowerCase().replace(/^\[|\]$/g, '');
}

function isLoopbackHost(host) {
    const h = normalizeHost(host);
    if (h === 'localhost' || h === '::1') return true;
    // 127.0.0.0/8
    return net.isIPv4(h) && h.split('.')[0] === '127';
}

function pinKey(host, port) {
    return `${normalizeHost(host)}:${Number(port)}`;
}

/** SHA-256 of the DER SubjectPublicKeyInfo, base64. Accepts DER or PEM. */
function spkiSha256(cert) {
    const x509 = new crypto.X509Certificate(cert);
    const spki = x509.publicKey.export({ type: 'spki', format: 'der' });
    return crypto.createHash('sha256').update(spki).digest('base64');
}

/** AB:CD:… display form of a base64 SHA-256 (matches `python -m api.tls_cert fingerprint`). */
function formatFingerprint(b64) {
    const hex = Buffer.from(String(b64 || ''), 'base64').toString('hex').toUpperCase();
    return hex.match(/.{2}/g)?.join(':') || '';
}

function pinFor(pins, host, port) {
    const entry = pins && typeof pins === 'object' ? pins[pinKey(host, port)] : null;
    return entry && typeof entry.spki === 'string' && entry.spki ? entry.spki : '';
}

function tlsError(code, message) {
    const err = new Error(message);
    err.code = code;
    return err;
}

function unpinnedError(host, port) {
    return tlsError(
        'CUTTLE_TLS_UNPINNED',
        `No trusted certificate for ${pinKey(host, port)}. Reconnect to this host to review and trust its certificate.`
    );
}

function pinMismatchError(host, port, expected, actual) {
    const err = tlsError(
        'CUTTLE_TLS_PIN_MISMATCH',
        `The certificate key for ${pinKey(host, port)} changed (expected ${formatFingerprint(expected)}, got ${formatFingerprint(actual)}). `
        + 'Refusing to connect. If the host was reinstalled, reconnect to review the new certificate.'
    );
    err.expected = expected;
    err.actual = actual;
    return err;
}

/**
 * https.Agent that hands a socket to the HTTP client only after the peer's
 * key matched the pin, so no request byte reaches an unpinned peer.
 */
class PinnedAgent extends https.Agent {
    constructor(host, port, expectedSpki) {
        super({ keepAlive: false });
        this.cuttlePin = { host, port, spki: expectedSpki };
    }

    createConnection(options, callback) {
        let done = false;
        const finish = (err, sock) => {
            if (done) return;
            done = true;
            callback(err, sock);
        };
        const { host, port, spki } = this.cuttlePin;
        const socket = tls.connect({
            ...options,
            // Chain/hostname checks are replaced by the key pin below.
            rejectUnauthorized: false,
            servername: net.isIP(normalizeHost(options.host || host)) ? undefined : normalizeHost(options.host || host),
        });
        socket.once('secureConnect', () => {
            let actual = '';
            try {
                actual = spkiSha256(socket.getPeerCertificate().raw);
            } catch (_) {}
            if (actual && actual === spki) {
                finish(null, socket);
                return;
            }
            const err = pinMismatchError(host, port, spki, actual);
            socket.destroy(err);
            finish(err);
        });
        socket.once('error', (err) => finish(err));
        // Callback form: the request waits for finish().
        return undefined;
    }
}

/**
 * Request options for one HTTPS request to host:port.
 * Loopback → local self-signed exception. Remote → PinnedAgent, or throws
 * CUTTLE_TLS_UNPINNED (callers must not retry without verification).
 */
function requestOptions(host, port, pins) {
    if (isLoopbackHost(host)) return { rejectUnauthorized: false };
    const spki = pinFor(pins, host, port);
    if (!spki) throw unpinnedError(host, port);
    return { agent: new PinnedAgent(host, port, spki) };
}

/**
 * Read the certificate a host presents, without sending any request.
 * Resolves { spki, certSha256, subject, issuer, validTo, webpkiAuthorized }.
 */
function fetchPeerCertificate(host, port, { timeoutMs = 5000 } = {}) {
    return new Promise((resolve, reject) => {
        const h = normalizeHost(host);
        const socket = tls.connect({
            host: h,
            port: Number(port),
            servername: net.isIP(h) ? undefined : h,
            rejectUnauthorized: false,
        });
        const timer = setTimeout(() => {
            socket.destroy();
            reject(tlsError('ETIMEDOUT', `Timed out reading the certificate of ${pinKey(host, port)}.`));
        }, timeoutMs);
        socket.once('secureConnect', () => {
            clearTimeout(timer);
            try {
                const peer = socket.getPeerCertificate();
                if (!peer || !peer.raw) throw new Error('Host presented no certificate.');
                resolve({
                    spki: spkiSha256(peer.raw),
                    certSha256: crypto.createHash('sha256').update(peer.raw).digest('hex'),
                    subject: (peer.subject && peer.subject.CN) || '',
                    issuer: (peer.issuer && peer.issuer.CN) || '',
                    validTo: peer.valid_to || '',
                    // True only for a chain the OS trusts that also names this host.
                    webpkiAuthorized: socket.authorized === true,
                });
            } catch (err) {
                reject(err);
            } finally {
                socket.end();
            }
        });
        socket.once('error', (err) => {
            clearTimeout(timer);
            reject(err);
        });
    });
}

/**
 * Chromium certificate-error decision for the selected remote endpoint.
 * `certificate` is Electron's Certificate (PEM in `.data`).
 */
function certificateMatchesPin(pins, host, port, certificate) {
    const spki = pinFor(pins, host, port);
    if (!spki || !certificate || !certificate.data) return false;
    try {
        return spkiSha256(certificate.data) === spki;
    } catch (_) {
        return false;
    }
}

module.exports = {
    normalizeHost,
    isLoopbackHost,
    pinKey,
    pinFor,
    spkiSha256,
    formatFingerprint,
    requestOptions,
    fetchPeerCertificate,
    certificateMatchesPin,
    pinMismatchError,
    unpinnedError,
    PinnedAgent,
};
