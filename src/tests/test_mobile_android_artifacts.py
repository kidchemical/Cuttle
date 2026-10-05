"""Publication uses SDK verification, not loose signing/build metadata."""
import json
import subprocess
import zipfile
from types import SimpleNamespace

import pytest
from api import mobile_android_artifacts as artifacts


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    sdk = tmp_path / 'sdk' / 'build-tools' / '35.0.0'
    (sdk / 'lib').mkdir(parents=True)
    (sdk / 'lib' / 'apksigner.jar').touch()
    (sdk / ('aapt.exe' if artifacts.os.name == 'nt' else 'aapt')).touch()
    monkeypatch.setenv('ANDROID_HOME', str(tmp_path / 'sdk'))
    path = tmp_path / 'app.apk'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('assets/cuttle-mobile-build.json', json.dumps({'hash': 'a' * 20}))
    return path


def test_invalid_signature_is_rejected(candidate, monkeypatch):
    def invalid(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr='DOES NOT VERIFY')
    monkeypatch.setattr(artifacts.subprocess, 'run', invalid)
    with pytest.raises(subprocess.CalledProcessError):
        artifacts.inspect_apk(candidate)


@pytest.mark.parametrize('package,version', [('other.app', 2), ('com.cuttle.mobile', 0), ('com.cuttle.mobile', 2100000001)])
def test_verified_signature_does_not_allow_wrong_package_or_version(candidate, monkeypatch, package, version):
    responses = iter([SimpleNamespace(stdout='Signer #1 certificate SHA-256 digest: ' + 'c' * 64),
                      SimpleNamespace(stdout=f"package: name='{package}' versionCode='{version}' versionName='1.0'")])
    monkeypatch.setattr(artifacts.subprocess, 'run', lambda *a, **kw: next(responses))
    with pytest.raises(ValueError):
        artifacts.inspect_apk(candidate)


def test_missing_build_tools_is_actionable(candidate, monkeypatch, tmp_path):
    monkeypatch.setenv('ANDROID_HOME', str(tmp_path / 'empty-sdk'))
    with pytest.raises(ValueError, match='Build Tools'):
        artifacts.inspect_apk(candidate)


def test_oversized_embedded_metadata_is_rejected(tmp_path):
    path = tmp_path / 'large.apk'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('assets/cuttle-mobile-build.json', ' ' * 17000)
    assert artifacts.baked_hash(path) is None
