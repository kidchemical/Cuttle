/**
 * Pack electron/main.js + preload + connect.html into dist/update/app.asar
 * so LAN clients can replace their packaged shell without a full electron-builder run.
 */
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { spawnSync } = require('child_process');

const electronDir = __dirname;
const HASH_FILES = ['connect.html', 'main.js', 'preload.js', 'device-worker/cuttle_device_worker.py'];
const PACK_FILES = ['connect.html', 'main.js', 'package.json', 'preload.js'];

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

function pack() {
    const hash = sourceHash();
    const staging = path.join(electronDir, 'dist', 'update-staging');
    const outDir = path.join(electronDir, 'dist', 'update');
    fs.rmSync(staging, { recursive: true, force: true });
    fs.mkdirSync(staging, { recursive: true });
    fs.mkdirSync(outDir, { recursive: true });
    for (const name of PACK_FILES) {
        const src = path.join(electronDir, name);
        if (fs.existsSync(src)) fs.copyFileSync(src, path.join(staging, name));
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

    const asarBin = path.join(electronDir, 'node_modules', '@electron', 'asar', 'bin', 'asar.js');
    if (!fs.existsSync(asarBin)) {
        console.error('asar CLI not found at', asarBin);
        process.exit(1);
    }
    const outAsar = path.join(outDir, 'app.asar');
    const r = spawnSync(process.execPath, [asarBin, 'pack', staging, outAsar], {
        stdio: 'inherit',
        env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' },
    });
    if (r.status !== 0) process.exit(r.status || 1);
    const manifest = {
        hash,
        packedAt: new Date().toISOString(),
        size: fs.statSync(outAsar).size,
        filename: 'app.asar',
        packageVersion: pkg.version,
    };
    fs.writeFileSync(path.join(outDir, 'manifest.json'), JSON.stringify(manifest, null, 2));
    process.stdout.write(JSON.stringify(manifest) + '\n');
}

pack();
