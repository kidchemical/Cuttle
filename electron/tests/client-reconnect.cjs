/* Run the real main process in a VM with no Electron, network, or children. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../main.js'), 'utf8');

function harness(args, config = {}) {
    const files = new Map([[path.join('/resources', 'app.asar'), 'fixture']]);
    const calls = [];
    const handlers = new Map();
    let ready;
    let profile = '/default-profile';
    const app = {
        isPackaged: true, commandLine: { appendSwitch() {} },
        getPath: name => name === 'userData' ? profile : '/installed/Cuttle.exe',
        setPath: (name, value) => { if (name === 'userData') profile = value; },
        setName() {}, getVersion: () => '0.2.26', on() {},
        requestSingleInstanceLock: () => true,
        whenReady: () => ({ then: callback => { ready = callback; } }),
        relaunch: options => calls.push(['relaunch', options.args]),
        exit: code => calls.push(['exit', code]), quit: () => calls.push(['quit']),
    };
    const fakeFs = {
        readFileSync: file => { if (!files.has(file)) throw new Error('No fixture: ' + file); return files.get(file); },
        writeFileSync: (file, value) => { files.set(file, value); calls.push(['write', file, value]); },
        existsSync: file => files.has(file), mkdirSync() {}, unlinkSync: file => files.delete(file),
    };
    const denied = () => { throw new Error('Forbidden external operation'); };
    const child = {
        spawn: (...argv) => { calls.push(['spawn', ...argv]); return { unref() {} }; },
        spawnSync: denied,
    };
    const modules = {
        electron: { app, ipcMain: { handle: (name, fn) => handlers.set(name, fn), on() {} },
            BrowserWindow: { getAllWindows: () => [] } },
        child_process: child, path, fs: fakeFs,
        os: { homedir: () => '/home/fixture', tmpdir: () => '/scratch', hostname: () => 'laptop' },
        net: {}, http: {}, https: {}, crypto: require('node:crypto'), './tls-trust.js': {},
    };
    const context = vm.createContext({
        require: name => { if (!(name in modules)) throw new Error('Denied require: ' + name); return modules[name]; },
        __dirname: path.join(__dirname, '..'),
        process: { argv: ['electron', ...args], platform: 'win32', resourcesPath: '/resources', env: {}, on() {} },
        console: { log() {}, warn() {}, error() {} },
        URL, Buffer, setTimeout: () => 0, clearTimeout() {}, __calls: calls,
    });
    vm.runInContext(source, context, { filename: 'main.js' });
    files.set(path.join(profile, 'desktop-config.json'), JSON.stringify(config));
    vm.runInContext(`
        createTray = ensureDesktopShortcuts = () => {};
        createWindow = async (opts) => __calls.push(['window', opts || {}]);
        startWorkerSidecar = async () => __calls.push(['worker']);
        waitForFlask = startDaemon = queryLocalServerPorts = () => { throw new Error('Client touched local Host'); };
        probeUrlList = async (candidates) => { __calls.push(['probe', candidates]); return {ok:true}; };
    `, context);
    return { context, calls, handlers, files, app, profile, ready,
        evaluate: code => vm.runInContext(code, context),
        config: () => JSON.parse(files.get(path.join(profile, 'desktop-config.json'))) };
}
const saved = {
    mode: 'client', host: '192.0.2.20', httpPort: 7000, httpsPort: 7443, scheme: 'https',
    endpoint: { kind: 'single', scheme: 'https', host: '192.0.2.20', port: 7443 },
    workerMode: false, lastError: 'connect ECONNREFUSED 127.0.0.1:8080',
};
(async () => {
    for (const flags of [['--mode=client'], ['--client'], []]) {
        const h = harness(flags, saved);
        await h.ready();
        const candidates = h.calls.find(row => row[0] === 'probe')[1];
        assert.equal(candidates.length, 1);
        assert.equal(candidates[0].host, saved.host);
        assert.equal(candidates[0].port, 7443);
        assert.equal(candidates[0].scheme, 'https');
        assert.equal(h.calls.find(row => row[0] === 'window')[1].connect, undefined);
        assert.equal(h.config().host, saved.host);
        assert.equal(h.config().lastError, '');
        if (!flags.includes('--mode=client')) assert.equal(h.profile, '/default-profile');
    }
    const fresh = harness(['--client']);
    await fresh.ready();
    assert.equal(fresh.calls.find(row => row[0] === 'window')[1].connect, true);
    assert(!fresh.calls.some(row => row[0] === 'probe'));
    const offline = harness(['--mode=client'], saved);
    offline.evaluate("probeUrlList = async () => ({ok:false,error:'offline'});");
    await offline.ready();
    assert.equal(offline.config().host, saved.host);
    assert.equal(offline.calls.find(row => row[0] === 'window')[1].connect, true);
    assert.equal(offline.evaluate('publicDesktopConfig().recentHosts[0].host'), saved.host);

    for (const flags of [['--mode=client', '--host=127.0.0.1'], ['--host', '127.0.0.1']]) {
        const h = harness(flags, saved);
        h.evaluate("applyDesktopTarget({host:'192.0.2.20', clientMode:true});");
        h.handlers.get('desktop-relaunch-app')();
        const relaunched = h.calls.find(row => row[0] === 'relaunch')[1];
        assert(!relaunched.some(arg => arg.startsWith('--host') || arg === '127.0.0.1'));
        assert(relaunched.includes(flags[0] === '--mode=client' ? '--mode=client' : '--client'));
    }
    // Exercise the actual packaged app.asar updater, including its cmd script.
    for (const flags of [['--mode=client'], ['--client']]) {
        const h = harness(flags, saved);
        h.evaluate(`
            applyDesktopTarget({host:'192.0.2.20', clientMode:true});
            pinnedUpdateBase = async () => 'https://192.0.2.20:7443';
            jsonRequest = async () => ({json:{artifactSha256:'a'.repeat(64)}});
            downloadToFile = async () => {};
            fileSha256 = async () => 'a'.repeat(64);
        `);
        const result = await h.evaluate('applyDesktopUpdate()');
        assert.equal(result.ok, true);
        const script = h.calls.find(row => row[0] === 'write' && row[1].endsWith('.cmd'))[2];
        assert(script.includes(`start "" "/installed/Cuttle.exe" "${flags[0]}"`));
        assert(!script.includes('--host'));
        assert.equal(h.calls.filter(row => row[0] === 'spawn').length, 1);
        assert.equal(h.calls.find(row => row[0] === 'spawn')[1], 'cmd.exe');
    }
    const selected = harness(['--client'], { ...saved, recentHosts: [null,
        { host: '192.0.2.30', scheme: 'http', port: 9000, httpPort: 9000, httpsPort: 9443,
          endpoint: {kind:'paired'} }] });
    const entries = selected.evaluate('publicDesktopConfig().recentHosts');
    assert.equal(entries.length, 2);
    assert.equal(entries[1].index, 2); // Invalid row filtered, original identifier retained.
    selected.evaluate('connectDesktopTarget = async parsed => ({ok:true, parsed});');
    const result = await selected.handlers.get('desktop-connect-saved-host')(null, entries[1].index);
    assert.equal(result.parsed.host, '192.0.2.30');
    assert.equal(result.parsed.httpPort, 9000);
    assert.equal(result.parsed.httpsPort, 9443);
    assert.equal(result.parsed.single, false);
    console.log('Client restore, profile preservation, packaged update, and saved-server selection passed');
})().catch(err => { console.error(err); process.exitCode = 1; });
