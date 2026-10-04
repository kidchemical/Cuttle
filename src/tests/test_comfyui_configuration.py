"""Optional ComfyUI integration requires explicit roots; no real IO services."""
from pathlib import Path

import pytest

from tools.comfyui import comfyui_client as client


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv('COMFYUI_ROOT', raising=False)
    monkeypatch.delenv('COMFYUI_OUTPUT_DIR', raising=False)
    monkeypatch.delenv('COMFYUI_URL', raising=False)

    def forbidden(*args, **kwargs):
        pytest.fail('Unexpected network or subprocess call')

    monkeypatch.setattr(client.requests, 'get', forbidden)
    monkeypatch.setattr(client.requests, 'post', forbidden)
    monkeypatch.setattr(client.subprocess, 'Popen', forbidden)


def test_unconfigured_status_is_offline_and_reports_unknown_hardware():
    status = client.comfyui_status()
    assert status['configured'] is False
    assert status['root'] is None
    assert status['installed'] is False
    assert status['running'] is False
    assert status['output_dir'] is None
    assert 'COMFYUI_ROOT' in status['configuration_error']
    assert 'unknown' in status['gpu_note']


@pytest.mark.parametrize('operation', [client.comfyui_start, client.comfyui_stop, client.comfyui_list_workflows,
                                      client.comfyui_list_outputs,
                                      lambda: client.comfyui_generate_3d('unused.png')])
def test_unconfigured_operations_fail_before_external_effects(operation):
    with pytest.raises(client.ComfyUIError, match='COMFYUI_ROOT'):
        operation()


@pytest.mark.parametrize('nested', [False, True])
def test_configured_install_and_output_override(monkeypatch, tmp_path, nested):
    root = tmp_path / 'installation'
    comfy = root / 'ComfyUI' if nested else root
    comfy.mkdir(parents=True)
    (comfy / 'main.py').touch()
    python = root / '.venv' / 'bin' / 'python'
    python.parent.mkdir(parents=True)
    python.touch()
    (comfy / 'custom_nodes' / 'ComfyUI-Trellis2').mkdir(parents=True)
    output = tmp_path / 'custom-output'
    output.mkdir()
    (output / 'asset.glb').write_bytes(b'glb')
    monkeypatch.setenv('COMFYUI_ROOT', str(root))
    monkeypatch.setenv('COMFYUI_OUTPUT_DIR', str(output))
    monkeypatch.setenv('COMFYUI_URL', 'http://configured.invalid:8188/')
    monkeypatch.setattr(client, 'is_running', lambda: False)
    status = client.comfyui_status()
    assert status['configured'] is True
    assert status['installed'] is True
    assert status['trellis2_nodes'] is True
    assert status['root'] == str(root)
    assert status['output_dir'] == str(output)
    assert status['url'] == 'http://configured.invalid:8188'
    assert 'unknown' in status['gpu_note']
    assert client.comfyui_list_outputs()[0]['path'] == str(output / 'asset.glb')


def test_upload_keeps_configured_input_path(monkeypatch, tmp_path):
    root = tmp_path / 'comfy'
    root.mkdir()
    image = tmp_path / 'reference.png'
    image.write_bytes(b'reference')
    monkeypatch.setenv('COMFYUI_ROOT', str(root))
    monkeypatch.setattr(client, 'is_running', lambda: False)
    result = client.comfyui_upload_image(str(image))
    assert Path(result['path']).read_bytes() == image.read_bytes()
    assert Path(result['path']).parent == root / 'input'
    assert result['upload'] is None
