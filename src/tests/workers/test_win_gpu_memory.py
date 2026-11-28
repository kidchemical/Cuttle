"""Windows per-process VRAM attribution (``nvidia-smi`` prints [N/A] under WDDM)."""

from __future__ import annotations

from api.device_workers import admission, win_gpu_memory as wgm

NV, IGPU = (0, 0x12E05), (0, 0x9A31)
GPU = {"index": 0, "uuid": "GPU-5050", "name": "RTX 5050", "vram_used_mb": 7148, "vram_total_mb": 8151}
MB = 1024 ** 2


def test_instance_names_parse_and_garbage_does_not():
    assert wgm.parse_instance("pid_4200_luid_0x00000000_0x00012e05_phys_0") == (4200, NV)
    assert wgm.parse_instance("pid_4200_luid_0x00000001_0x00012E05_phys_1") == (4200, (1, 0x12E05))
    assert wgm.parse_instance("_total") is None and wgm.parse_instance("") is None


def test_only_the_nvidia_adapters_memory_is_attributed():
    usage = {(4200, NV): 5270 * MB, (4200, IGPU): 300 * MB, (77, NV): 90 * MB, (88, NV): 1000}
    rows = wgm.attribute([GPU], [NV], usage)
    assert rows == [{"gpu_uuid": "GPU-5050", "pid": 77, "used_mb": 90},
                    {"gpu_uuid": "GPU-5050", "pid": 4200, "used_mb": 5270}]


def test_an_adapter_it_cannot_tell_apart_gives_no_rows():
    usage = {(4200, NV): 5270 * MB}
    assert wgm.attribute([GPU], [], usage) == []  # DXGI found no NVIDIA adapter
    assert wgm.attribute([GPU], [NV, (0, 0x777)], usage) == []  # two NVIDIA adapters
    assert wgm.attribute([GPU, dict(GPU, index=1, uuid="GPU-b")], [NV], usage) == []  # two GPUs
    assert wgm.attribute([GPU], [NV, NV], usage) != []  # one adapter listed twice is still one


def test_a_failing_counter_read_is_unknown_not_zero():
    def broken():
        raise OSError("pdh.dll missing")
    assert wgm.apps([GPU], read_luids=lambda: [NV], read_usage=broken) == []
    assert wgm.apps([], read_luids=lambda: [NV], read_usage=dict) == []


def _laptop(service_vram):
    return {"ram_total_gb": 31.6, "ram_available_gb": 18.0, "gpus": [GPU],
            "services": {"comfyui": {"pids": [4200], "rss_mb": 2000,
                                     "vram_mb": {"0": service_vram} if service_vram else {}}}}


def test_resident_comfyui_models_are_credited_once_to_its_next_job():
    need = admission.requested({"resources": {"ram_mb": 9000, "vram_mb": 5000, "service": "comfyui"}})
    # Models resident (7.1 GB of 8 GB used, 5.3 GB of it ComfyUI's): the next ComfyUI job fits.
    res, why = admission.admit(need, _laptop(5270), [], telemetry_age=5)
    assert why == "" and res["vram_mb"] == 5000
    # Unattributed (WDDM without counters, or a restarted server): not credited, refused.
    res, why = admission.admit(need, _laptop(0), [], telemetry_age=5)
    assert res is None and "VRAM" in why
    # A job for another service gets no credit for ComfyUI's memory.
    other = admission.requested({"resources": {"vram_mb": 5000}})
    assert admission.admit(other, _laptop(5270), [], telemetry_age=5)[0] is None
