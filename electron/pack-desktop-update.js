/**
 * Pack the Electron shell files into <out>/app.asar (out: $CUTTLE_DESKTOP_UPDATE_DIR, else dist/update)
 * so LAN clients can replace their packaged shell without a full electron-builder run.
 */
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { spawnSync } = require('child_process');

const electronDir = __dirname;
const HASH_FILES = [
    'connect.html', 'main.js', 'preload.js', 'tls-trust.js', 'external-link-policy.js',
    'device-worker/cuttle_device_worker.py',
];
const PACK_FILES = ['connect.html', 'main.js', 'package.json', 'preload.js', 'tls-trust.js', 'external-link-policy.js'];

function sourceHash() {
    const h = crypto.createHash('sha256');
    for (const name of HASH_FILES) {
        h.update(name);
        h.update('\0');
        const p = path.join(electronDir, name);
        if (fs.existsSync(p)) h.update(fs.readFileSync(p));
        h.update('\0');
    }
    return h.digest('hex').slice(0, 20);
}

/** Atomic replace. On Windows a rename onto a file another process has open
 *  (a concurrent pack verifying it, a Client downloading it) fails with
 *  EPERM/EACCES/EBUSY for a moment: retry briefly, as graceful-fs does. */
function renameReplacing(from, to) {
    const deadline = Date.now() + 3000;
    for (;;) {
        try {
            fs.renameSync(from, to);
            return;
        } catch (err) {
            const transient = process.platform === 'win32'
                && ['EPERM', 'EACCES', 'EBUSY'].includes(err && err.code);
            if (!transient || Date.now() > deadline) throw err;
            Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 50);
        }
    }
}

function pack() {
    const hash = sourceHash();
    // The Host passes its Cuttle-home cache dir; a manual run keeps dist/.
    const outDir = process.env.CUTTLE_DESKTOP_UPDATE_DIR || path.join(electronDir, 'dist', 'update');
    fs.mkdirSync(outDir, { recursive: true });
    // Every file the shell needs to boot is required: a Client that swaps in an
    // asar without package.json / preload.js / tls-trust.js cannot start again.
    const missing = PACK_FILES.filter((name) => !fs.existsSync(path.join(electronDir, name)));
    if (missing.length) {
        console.error('pack-desktop-update: missing shell files:', missing.join(', '));
        process.exit(1);
    }
    const asarBin = path.join(electronDir, 'node_modules', '@electron', 'asar', 'bin', 'asar.js');
    if (!fs.existsSync(asarBin)) {
        console.error('asar CLI not found at', asarBin);
        process.exit(1);
    }
    // A private staging dir and temp asar per run: concurrent packs once shared
    // `<outDir>-staging`, and one run's rmSync emptied the other's staging mid-pack.
    fs.rmSync(`${outDir}-staging`, { recursive: true, force: true });
    const staging = fs.mkdtempSync(`${outDir}-staging-`);
    const outAsar = path.join(outDir, 'app.asar');
    const tmpAsar = `${outAsar}.${process.pid}.tmp`;
    try {
        for (const name of PACK_FILES) {
            fs.copyFileSync(path.join(electronDir, name), path.join(staging, name));
        }
        const workerSrc = path.join(electronDir, 'device-worker');
        if (fs.existsSync(workerSrc)) {
            fs.cpSync(workerSrc, path.join(staging, 'device-worker'), { recursive: true });
        }
        const assetsSrc = path.join(electronDir, 'assets');
        if (fs.existsSync(assetsSrc)) {
            fs.cpSync(assetsSrc, path.join(staging, 'assets'), { recursive: true });
        }
        const pkg = JSON.parse(fs.readFileSync(path.join(electronDir, 'package.json'), 'utf8'));
        fs.writeFileSync(
            path.join(staging, 'desktop-build.json'),
            JSON.stringify({ hash, packedAt: new Date().toISOString(), packageVersion: pkg.version }, null, 2)
        );

        const r = spawnSync(process.execPath, [asarBin, 'pack', staging, tmpAsar], {
            stdio: 'inherit',
            env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' },
        });
        if (r.status !== 0) throw new Error(`asar pack exited ${r.status}`);
        const asar = require(path.join(electronDir, 'node_modules', '@electron', 'asar'));
        const packed = new Set(asar.listPackage(tmpAsar).map((p) => p.replace(/\\/g, '/').replace(/^\//, '')));
        const lost = PACK_FILES.filter((name) => !packed.has(name));
        if (lost.length) {
            throw new Error(`packed asar is missing: ${lost.join(', ')}`);
        }
        renameReplacing(tmpAsar, outAsar);
        const manifest = {
            hash,
            packedAt: new Date().toISOString(),
            size: fs.statSync(outAsar).size,
            filename: 'app.asar',
            packageVersion: pkg.version,
        };
        const manifestPath = path.join(outDir, 'manifest.json');
        fs.writeFileSync(`${manifestPath}.${process.pid}.tmp`, JSON.stringify(manifest, null, 2));
        renameReplacing(`${manifestPath}.${process.pid}.tmp`, manifestPath);
        process.stdout.write(JSON.stringify(manifest) + '\n');
    } finally {
        fs.rmSync(staging, { recursive: true, force: true });
        fs.rmSync(tmpAsar, { force: true });
    }
}

try {
    pack();
} catch (err) {
    console.error('pack-desktop-update:', (err && err.message) || err);
    process.exit(1);
}
