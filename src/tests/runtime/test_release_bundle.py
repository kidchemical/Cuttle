"""Release builds ship product files, never this install's state or overlays."""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _resources():
    pkg = json.loads((REPO / "electron" / "package.json").read_text(encoding="utf-8"))
    return {r["from"]: r for r in pkg["build"]["extraResources"]}


def test_src_bundle_excludes_runtime_state_and_personal_settings():
    filters = set(_resources()["../src"]["filter"])
    for pattern in ("!data/**", "!settings.json", "!**/.env", "!**/*.db", "!**/output/**", "!**/logs/**"):
        assert pattern in filters, pattern


def test_global_config_ships_without_personal_overlay():
    res = _resources()["../.cuttle_global"]
    assert res["to"] == "app/.cuttle_global"
    assert "!personal/**" in res["filter"]


def test_windows_bundle_extraction_uses_native_tar_despite_git_bash_path(tmp_path):
    import subprocess
    # Execute the actual extractor with a Windows process and a stub extraction
    # boundary. Filesystem staging/rename/cleanup remain real and no downloads run.
    script = r'''
    const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
    const source = process.argv[1], dest = process.argv[2];
    const calls = [], sandboxModule = {exports:{}};
    const fakeProcess = {platform:'win32', env:{SystemRoot:'D:\\Windows', PATH:'C:\\Program Files\\Git\\usr\\bin'},
        exit(code) {throw new Error('Unexpected build failure: ' + code);}};
    function isolatedRequire(id) {
        if (id === 'child_process') return {spawnSync(cmd, args) {
            calls.push({cmd,args});
            fs.mkdirSync(path.join(args[3], 'python'), {recursive:true});
            fs.writeFileSync(path.join(args[3], 'python', 'python.exe'), 'runtime');
            return {status:0};
        }};
        if (id === 'https') throw new Error('Network is forbidden');
        return require(id);
    }
    // https is imported but its network methods must remain unused.
    const guardedRequire = id => id === 'https' ? {get(){throw new Error('Network is forbidden');}} : isolatedRequire(id);
    vm.runInNewContext(fs.readFileSync(source,'utf8'), {
        require:guardedRequire, module:sandboxModule, __dirname:path.dirname(source), process:fakeProcess, console
    });
    sandboxModule.exports.extractRuntime('D:\\cache\\python-runtime.tar.gz', dest);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].cmd, 'D:\\Windows\\System32\\tar.exe');
    assert.equal(calls[0].args[1], 'D:\\cache\\python-runtime.tar.gz');
    assert.equal(fs.readFileSync(path.join(dest,'python.exe'),'utf8'), 'runtime');
    assert.equal(fs.existsSync(dest + '-extract'), false);
    '''
    result = subprocess.run(['node', '-e', script, str(REPO / 'electron' / 'bundle-python.js'),
                             str(tmp_path / 'bundle')], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_bundle_extractor_unpacks_local_archive_without_running_build(tmp_path):
    import subprocess
    import tarfile
    source = tmp_path / 'source' / 'python' / 'lib'
    source.mkdir(parents=True)
    (source / 'fixture.txt').write_text('Bundled runtime fixture')
    archive = tmp_path / 'python.tar.gz'
    with tarfile.open(archive, 'w:gz') as packed:
        packed.add(source.parent, arcname='python')
    dest = tmp_path / 'installed'
    result = subprocess.run(['node', '-e',
        'require(process.argv[1]).extractRuntime(process.argv[2], process.argv[3]);',
        str(REPO / 'electron' / 'bundle-python.js'), str(archive), str(dest)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (dest / 'lib' / 'fixture.txt').read_text() == 'Bundled runtime fixture'
    assert not Path(str(dest) + '-extract').exists()
