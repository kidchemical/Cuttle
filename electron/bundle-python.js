/**
 * Bundle a known Python runtime + Cuttle's runtime dependencies for a
 * packaged Host (electron-builder copies python-bundle/ to resources/python).
 *
 *   node bundle-python.js            # build python-bundle/ for this OS/arch
 *   node bundle-python.js --lock     # re-resolve python-constraints.txt
 *
 * The runtime is a python-build-standalone release pinned by SHA-256 in
 * python-runtime.json (download fails closed on a mismatch). Dependencies come
 * from src/requirements/requirements.txt, held to python-constraints.txt,
 * wheels only. The packaged Host never uses system Python or a repo .venv.
 * Build per target OS: pip resolves platform wheels for the running machine.
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const https = require('https');
const crypto = require('crypto');
const { spawnSync } = require('child_process');

const electronDir = __dirname;
const repoRoot = path.join(electronDir, '..');
const lock = JSON.parse(fs.readFileSync(path.join(electronDir, 'python-runtime.json'), 'utf8'));
const requirements = path.join(repoRoot, 'src', 'requirements', 'requirements.txt');
const constraints = path.join(electronDir, 'python-constraints.txt');
const outDir = path.join(electronDir, 'python-bundle');
const cacheDir = process.env.CUTTLE_PYTHON_CACHE || path.join(os.tmpdir(), 'cuttle-python-cache');

function targetKey() {
    return `${process.platform}-${process.arch}`;
}

function fail(message) {
    console.error(`bundle-python: ${message}`);
    process.exit(1);
}

function run(cmd, args, opts = {}) {
    const r = spawnSync(cmd, args, { stdio: 'inherit', ...opts });
    if (r.status !== 0) fail(`${path.basename(cmd)} ${args.slice(0, 3).join(' ')} … exited ${r.status}`);
    return r;
}

function sha256File(file) {
    const h = crypto.createHash('sha256');
    h.update(fs.readFileSync(file));
    return h.digest('hex');
}

function download(url, dest, redirects = 5) {
    return new Promise((resolve, reject) => {
        https.get(url, (res) => {
            if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location && redirects > 0) {
                res.resume();
                const next = new URL(res.headers.location, url);
                if (next.protocol !== 'https:') {
                    reject(new Error(`refusing non-HTTPS redirect to ${next}`));
                    return;
                }
                download(next.toString(), dest, redirects - 1).then(resolve, reject);
                return;
            }
            if (res.statusCode !== 200) {
                res.resume();
                reject(new Error(`GET ${url} → ${res.statusCode}`));
                return;
            }
            const tmp = `${dest}.part`;
            const file = fs.createWriteStream(tmp);
            res.pipe(file);
            file.on('finish', () => file.close(() => {
                fs.renameSync(tmp, dest);
                resolve();
            }));
            file.on('error', reject);
        }).on('error', reject);
    });
}

async function fetchRuntime(asset) {
    fs.mkdirSync(cacheDir, { recursive: true });
    const archive = path.join(cacheDir, asset.file);
    if (!fs.existsSync(archive) || sha256File(archive) !== asset.sha256) {
        const url = `${lock.baseUrl}/${lock.release}/${encodeURIComponent(asset.file)}`;
        console.log(`Downloading ${url}`);
        await download(url, archive);
    }
    const actual = sha256File(archive);
    if (actual !== asset.sha256) {
        fs.rmSync(archive, { force: true });
        fail(`checksum mismatch for ${asset.file}: expected ${asset.sha256}, got ${actual}`);
    }
    return archive;
}

function extractRuntime(archive, dest) {
    fs.rmSync(dest, { recursive: true, force: true });
    const staging = `${dest}-extract`;
    fs.rmSync(staging, { recursive: true, force: true });
    fs.mkdirSync(staging, { recursive: true });
    // bsdtar ships with Windows 10+; GNU tar elsewhere. Archive root is python/.
    run('tar', ['-xzf', archive, '-C', staging]);
    fs.renameSync(path.join(staging, 'python'), dest);
    fs.rmSync(staging, { recursive: true, force: true });
}

function pythonExe(root, asset) {
    return path.join(root, ...asset.executable.split('/'));
}

const PIP_QUIET = ['--disable-pip-version-check', '--no-input', '--no-cache-dir'];

/** Re-resolve exact pins for Linux and Windows (wheels only). */
function writeLock(py) {
    const pins = new Map();
    // pip evaluates markers for the machine it runs on, not --platform, so
    // marker-gated requirements are added explicitly for their target.
    const markerReqs = (marker) => fs.readFileSync(requirements, 'utf8').split(/\r?\n/)
        .filter((line) => line.includes(';') && line.replace(/\s+/g, '').includes(marker))
        .map((line) => line.split(';')[0].trim());
    const targets = [
        { label: 'linux', args: ['--platform', 'manylinux2014_x86_64', '--platform', 'manylinux_2_28_x86_64',
            ...markerReqs('sys_platform=="linux"')] },
        { label: 'windows', args: ['--platform', 'win_amd64', ...markerReqs('sys_platform=="win32"')] },
    ];
    for (const t of targets) {
        const report = path.join(os.tmpdir(), `cuttle-pip-report-${t.label}.json`);
        run(py, ['-m', 'pip', 'install', ...PIP_QUIET, '--dry-run', '--ignore-installed', '--only-binary=:all:',
            '--python-version', lock.pythonVersion.split('.').slice(0, 2).join('.'), '--implementation', 'cp',
            ...t.args, '--target', path.join(os.tmpdir(), 'cuttle-pip-unused'),
            '--report', report, '-r', requirements]);
        const data = JSON.parse(fs.readFileSync(report, 'utf8'));
        for (const item of data.install || []) {
            const name = String(item.metadata.name).toLowerCase().replace(/[-_.]+/g, '-');
            const version = String(item.metadata.version);
            const prev = pins.get(name);
            if (prev && prev !== version) fail(`${name} resolves to ${prev} and ${version} on different platforms`);
            pins.set(name, version);
        }
    }
    const lines = [
        '# Exact versions for the Python bundled into packaged Hosts.',
        '# Generated by `node electron/bundle-python.js --lock` from',
        '# src/requirements/requirements.txt; review and commit changes.',
        ...[...pins.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([n, v]) => `${n}==${v}`),
        '',
    ];
    fs.writeFileSync(constraints, lines.join('\n'));
    console.log(`Wrote ${constraints} (${pins.size} packages)`);
}

function pruneBundle(root) {
    // Only the stdlib test suite and C headers: never prune inside packages.
    const stdlib = process.platform === 'win32'
        ? path.join(root, 'Lib')
        : path.join(root, 'lib', `python${lock.pythonVersion.split('.').slice(0, 2).join('.')}`);
    for (const rel of ['test', path.join('idlelib', 'idle_test')]) {
        fs.rmSync(path.join(stdlib, rel), { recursive: true, force: true });
    }
    fs.rmSync(path.join(root, 'include'), { recursive: true, force: true });
}

async function main() {
    const key = targetKey();
    const asset = lock.assets[key];
    if (!asset) fail(`no pinned Python runtime for ${key}; supported: ${Object.keys(lock.assets).join(', ')}`);
    const archive = await fetchRuntime(asset);

    if (process.argv.includes('--lock')) {
        const scratch = path.join(os.tmpdir(), 'cuttle-python-lock-runtime');
        extractRuntime(archive, scratch);
        writeLock(pythonExe(scratch, asset));
        fs.rmSync(scratch, { recursive: true, force: true });
        return;
    }

    if (!fs.existsSync(constraints)) fail(`missing ${constraints}; run with --lock first`);
    extractRuntime(archive, outDir);
    const py = pythonExe(outDir, asset);
    const env = { ...process.env, PYTHONNOUSERSITE: '1', PIP_REQUIRE_VIRTUALENV: '0' };
    delete env.PYTHONPATH;
    delete env.PYTHONHOME;
    run(py, ['-m', 'pip', 'install', ...PIP_QUIET, '--no-warn-script-location', '--only-binary=:all:',
        '-r', requirements, '-c', constraints], { env });
    pruneBundle(outDir);
    const freeze = spawnSync(py, ['-m', 'pip', 'freeze', '--all'], { encoding: 'utf8', env });
    if (freeze.status !== 0) fail('pip freeze failed');
    const manifest = {
        pythonVersion: lock.pythonVersion,
        runtime: { release: lock.release, file: asset.file, sha256: asset.sha256 },
        target: key,
        builtAt: new Date().toISOString(),
        packages: freeze.stdout.split(/\r?\n/).filter(Boolean),
    };
    fs.writeFileSync(path.join(outDir, 'cuttle-python.json'), JSON.stringify(manifest, null, 2));
    // Smoke: the bundled interpreter imports the daemon's dependencies.
    run(py, ['-I', '-c', 'import flask, cryptography, yaml, psutil, requests, bcrypt, PIL; print("bundled python ok")'], { env });
    console.log(`Bundled Python ${lock.pythonVersion} for ${key} → ${outDir}`);
}

main().catch((err) => fail(err && err.stack ? err.stack : String(err)));
