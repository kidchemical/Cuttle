/**
 * Bundle a known Python runtime + Cuttle's runtime dependencies for a
 * packaged Host (electron-builder copies python-bundle/ to resources/python).
 *
 *   node bundle-python.js            # build python-bundle/ for this OS/arch
 *   node bundle-python.js --lock     # re-resolve versions and wheel hashes
 *
 * The runtime is a python-build-standalone release pinned by SHA-256 in
 * python-runtime.json (download fails closed on a mismatch). Dependencies come
 * from src/requirements/requirements.txt, hash-locked in python-requirements.lock,
 * wheels only. The packaged Host never uses system Python or a repo .venv.
 * Build per target OS: pip resolves platform wheels for the running machine.
 */
const fs = require('fs');
const path = require('path');
const https = require('https');
const crypto = require('crypto');
const { spawnSync } = require('child_process');

const electronDir = __dirname;
const repoRoot = path.join(electronDir, '..');
const lock = JSON.parse(fs.readFileSync(path.join(electronDir, 'python-runtime.json'), 'utf8'));
const requirements = path.join(repoRoot, 'src', 'requirements', 'requirements.txt');
const dependencyLock = path.join(electronDir, 'python-requirements.lock');
const scratchDir = path.join(repoRoot, 'temp', 'python-build');
const outDir = path.join(electronDir, 'python-bundle');
const cacheDir = process.env.CUTTLE_PYTHON_CACHE || path.join(repoRoot, 'temp', 'python-cache');

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
    // Git Bash puts GNU tar first on PATH; it treats D:\... archives as remote
    // hosts. Select Windows' native bsdtar explicitly so drive paths stay local.
    const tar = process.platform === 'win32'
        ? path.win32.join(process.env.SystemRoot || process.env.WINDIR || 'C:\\Windows', 'System32', 'tar.exe')
        : 'tar';
    run(tar, ['-xzf', archive, '-C', staging]);
    fs.renameSync(path.join(staging, 'python'), dest);
    fs.rmSync(staging, { recursive: true, force: true });
}

function pythonExe(root, asset) {
    return path.join(root, ...asset.executable.split('/'));
}

const PIP_QUIET = ['--disable-pip-version-check', '--no-input', '--no-cache-dir'];

/** Re-resolve exact pins for Linux and Windows (wheels only). */
async function writeLock(py) {
    fs.mkdirSync(scratchDir, { recursive: true });
    const pins = new Map();
    // pip evaluates markers on the build machine, even with --platform.
    // Select the direct OS dependencies explicitly so --lock also works on Windows.
    const sourceLines = fs.readFileSync(requirements, 'utf8').split(/\r?\n/);
    const targets = [
        { label: 'linux', sysPlatform: 'linux', args: ['--platform', 'manylinux2014_x86_64', '--platform', 'manylinux_2_28_x86_64'] },
        { label: 'windows', sysPlatform: 'win32', args: ['--platform', 'win_amd64'] },
    ];
    for (const t of targets) {
        const report = path.join(scratchDir, `pip-report-${t.label}.json`);
        const targetRequirements = path.join(scratchDir, `requirements-${t.label}.txt`);
        const selected = sourceLines.flatMap(line => {
            const match = line.match(/;\s*sys_platform\s*==\s*["']([^"']+)["']/);
            return match ? (match[1] === t.sysPlatform ? [line.split(';')[0].trim()] : []) : [line];
        });
        fs.writeFileSync(targetRequirements, selected.join('\n'));
        run(py, ['-m', 'pip', 'install', ...PIP_QUIET, '--dry-run', '--ignore-installed', '--only-binary=:all:',
            '--python-version', lock.pythonVersion.split('.').slice(0, 2).join('.'), '--implementation', 'cp',
            ...t.args, '--target', path.join(scratchDir, 'pip-unused'),
            '--report', report, '-r', targetRequirements]);
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
        '# Hash-locked wheel dependencies for packaged Python Hosts.',
        '# Generated by node electron/bundle-python.js --lock; review changes.',
    ];
    for (const [name, version] of [...pins.entries()].sort(([a], [b]) => a.localeCompare(b))) {
        const metadata = path.join(scratchDir, `${name}.json`);
        await download(`https://pypi.org/pypi/${name}/${version}/json`, metadata);
        const release = JSON.parse(fs.readFileSync(metadata, 'utf8'));
        const hashes = [...new Set(release.urls.filter(f => f.packagetype === 'bdist_wheel')
            .map(f => f.digests.sha256))].sort();
        if (!hashes.length || hashes.some(h => !/^[a-f0-9]{64}$/.test(h))) fail(`no wheel hashes for ${name}==${version}`);
        const marker = name === 'pywinpty' ? '; sys_platform == "win32"'
            : name === 'python-xlib' ? '; sys_platform == "linux"' : '';
        lines.push(`${name}==${version}${marker} \\`);
        hashes.forEach((h, i) => lines.push(`    --hash=sha256:${h}${i < hashes.length - 1 ? ' \\' : ''}`));
    }
    fs.writeFileSync(dependencyLock, lines.join('\n') + '\n');
    console.log(`Wrote ${dependencyLock} (${pins.size} packages)`);
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
        const scratch = path.join(scratchDir, 'lock-runtime');
        extractRuntime(archive, scratch);
        await writeLock(pythonExe(scratch, asset));
        fs.rmSync(scratch, { recursive: true, force: true });
        return;
    }

    if (!fs.existsSync(dependencyLock)) fail(`missing ${dependencyLock}; run with --lock first`);
    extractRuntime(archive, outDir);
    const py = pythonExe(outDir, asset);
    const env = { ...process.env, PYTHONNOUSERSITE: '1', PIP_REQUIRE_VIRTUALENV: '0' };
    delete env.PYTHONPATH;
    delete env.PYTHONHOME;
    run(py, ['-m', 'pip', 'install', ...PIP_QUIET, '--no-warn-script-location', '--only-binary=:all:',
        '--require-hashes', '-r', dependencyLock], { env });
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
    // The packaged install tree is read-only: nothing pip-installs at runtime.
    run(py, ['-m', 'pip', 'uninstall', ...PIP_QUIET.filter((a) => a !== '--no-cache-dir'), '-y', 'pip'], { env });
    const stdlib = process.platform === 'win32'
        ? path.join(outDir, 'Lib')
        : path.join(outDir, 'lib', `python${lock.pythonVersion.split('.').slice(0, 2).join('.')}`);
    fs.rmSync(path.join(stdlib, 'ensurepip'), { recursive: true, force: true });
    console.log(`Bundled Python ${lock.pythonVersion} for ${key} → ${outDir}`);
}

module.exports = { extractRuntime };
if (require.main === module) main().catch((err) => fail(err && err.stack ? err.stack : String(err)));
