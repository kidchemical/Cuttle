"""Per-process VRAM on Windows, where ``nvidia-smi`` reports ``[N/A]`` per process.

Under WDDM the driver does not attribute memory to processes, so a ComfyUI
server holding its models looks like anonymous "GPU used" and admission cannot
credit it back to ComfyUI jobs. Windows itself does attribute it: the
``GPU Process Memory(pid_<pid>_luid_<hi>_<lo>_phys_<n>)\\Dedicated Usage``
performance counter (read here through ``pdh.dll``). The LUID names the adapter;
DXGI says which adapter is NVIDIA's (vendor ``0x10DE``).

Attribution is all-or-nothing: exactly one NVIDIA adapter and exactly one
``nvidia-smi`` GPU, or no rows at all. An unknown is safer than another GPU's
memory credited to the wrong card. No dependency beyond ``ctypes``.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

NVIDIA_VENDOR = 0x10DE
COUNTER = "\\GPU Process Memory(*)\\Dedicated Usage"
_INSTANCE = re.compile(r"pid_(\d+)_luid_(0x[0-9a-f]+)_(0x[0-9a-f]+)_phys_\d+", re.I)
_LUID_TTL = 300.0  # LUIDs hold for a boot; re-read now and then in case the driver restarted

Luid = Tuple[int, int]  # (HighPart, LowPart), the order instance names print them in
_luid_cache: Tuple[float, List[Luid]] = (0.0, [])


def parse_instance(name: str) -> Optional[Tuple[int, Luid]]:
    """``(pid, luid)`` from a ``GPU Process Memory`` instance name, else None."""
    match = _INSTANCE.fullmatch(str(name or "").strip())
    if not match:
        return None
    return int(match.group(1)), (int(match.group(2), 16), int(match.group(3), 16))


def attribute(gpus: List[Dict[str, Any]], nvidia_luids: List[Luid],
              usage: Dict[Tuple[int, Luid], int]) -> List[Dict[str, Any]]:
    """``parse_nvidia_apps``-shaped rows for the one NVIDIA GPU, or ``[]`` when the
    adapter cannot be told apart (several GPUs, several or no NVIDIA adapters)."""
    luids = sorted(set(nvidia_luids))
    if len(gpus) != 1 or len(luids) != 1:
        return []
    by_pid: Dict[int, int] = {}
    for (pid, luid), used in usage.items():
        if luid == luids[0] and used > 0:
            by_pid[pid] = by_pid.get(pid, 0) + int(used)
    return [{"gpu_uuid": gpus[0]["uuid"], "pid": pid, "used_mb": used // (1024 ** 2)}
            for pid, used in sorted(by_pid.items()) if used >= 1024 ** 2]


def apps(gpus: List[Dict[str, Any]], *, read_luids: Optional[Callable[[], List[Luid]]] = None,
         read_usage: Optional[Callable[[], Dict[Tuple[int, Luid], int]]] = None) -> List[Dict[str, Any]]:
    """Per-process VRAM rows for ``gpus`` from Windows counters; ``[]`` on any failure."""
    if not gpus:
        return []
    try:
        return attribute(gpus, (read_luids or _nvidia_luids_cached)(), (read_usage or dedicated_usage)())
    except Exception:
        return []


def _nvidia_luids_cached() -> List[Luid]:
    global _luid_cache
    now = time.monotonic()
    if now - _luid_cache[0] > _LUID_TTL:
        _luid_cache = (now, nvidia_luids())
    return _luid_cache[1]


def nvidia_luids() -> List[Luid]:
    """LUIDs of the NVIDIA adapters DXGI enumerates."""
    import ctypes
    from ctypes import wintypes

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class DESC1(ctypes.Structure):  # DXGI_ADAPTER_DESC1
        _fields_ = [("Description", wintypes.WCHAR * 128), ("VendorId", wintypes.UINT),
                    ("DeviceId", wintypes.UINT), ("SubSysId", wintypes.UINT), ("Revision", wintypes.UINT),
                    ("DedicatedVideoMemory", ctypes.c_size_t), ("DedicatedSystemMemory", ctypes.c_size_t),
                    ("SharedSystemMemory", ctypes.c_size_t), ("AdapterLuid", LUID), ("Flags", wintypes.UINT)]

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    iid = GUID(0x770AAE78, 0xF26F, 0x4DBA, (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))
    factory = ctypes.c_void_p()
    if ctypes.WinDLL("dxgi").CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(factory)) != 0:
        return []

    def method(obj: ctypes.c_void_p, index: int, *argtypes: Any) -> Callable[..., int]:
        table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(table[index])

    found: List[Luid] = []
    try:
        for i in range(16):
            adapter = ctypes.c_void_p()
            # IDXGIFactory1::EnumAdapters1 (vtable 12); fails with DXGI_ERROR_NOT_FOUND past the last.
            if method(factory, 12, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))(
                    factory, i, ctypes.byref(adapter)) != 0:
                break
            try:
                desc = DESC1()
                # IDXGIAdapter1::GetDesc1 (vtable 10)
                if method(adapter, 10, ctypes.POINTER(DESC1))(adapter, ctypes.byref(desc)) == 0 \
                        and desc.VendorId == NVIDIA_VENDOR:
                    found.append((desc.AdapterLuid.HighPart & 0xFFFFFFFF, desc.AdapterLuid.LowPart))
            finally:
                method(adapter, 2)(adapter)  # Release
    finally:
        method(factory, 2)(factory)
    return found


def dedicated_usage() -> Dict[Tuple[int, Luid], int]:
    """Bytes of dedicated VRAM per ``(pid, adapter luid)`` from one PDH sample."""
    import ctypes
    from ctypes import wintypes

    class VALUE(ctypes.Union):
        _fields_ = [("longValue", wintypes.LONG), ("doubleValue", ctypes.c_double),
                    ("largeValue", ctypes.c_longlong), ("wideString", ctypes.c_wchar_p)]

    class FMT(ctypes.Structure):  # PDH_FMT_COUNTERVALUE
        _fields_ = [("CStatus", wintypes.DWORD), ("value", VALUE)]

    class ITEM(ctypes.Structure):  # PDH_FMT_COUNTERVALUE_ITEM_W
        _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", FMT)]

    pdh = ctypes.WinDLL("pdh")
    pdh.PdhGetFormattedCounterArrayW.restype = ctypes.c_long
    more_data, fmt_large = 0x800007D2, 0x00000400
    query, counter = ctypes.c_void_p(), ctypes.c_void_p()
    if pdh.PdhOpenQueryW(None, None, ctypes.byref(query)) != 0:
        return {}
    try:
        if pdh.PdhAddEnglishCounterW(query, ctypes.c_wchar_p(COUNTER), None, ctypes.byref(counter)) != 0 \
                or pdh.PdhCollectQueryData(query) != 0:
            return {}
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        status = pdh.PdhGetFormattedCounterArrayW(counter, fmt_large, ctypes.byref(size), ctypes.byref(count), None)
        if status & 0xFFFFFFFF != more_data or not size.value:
            return {}
        buf = (ctypes.c_byte * size.value)()
        if pdh.PdhGetFormattedCounterArrayW(counter, fmt_large, ctypes.byref(size), ctypes.byref(count), buf) != 0:
            return {}
        items = ctypes.cast(buf, ctypes.POINTER(ITEM))
        out: Dict[Tuple[int, Luid], int] = {}
        for i in range(count.value):
            parsed = parse_instance(items[i].szName)
            if parsed and items[i].FmtValue.CStatus in (0, 1):  # PDH_CSTATUS_VALID_DATA / NEW_DATA
                out[parsed] = out.get(parsed, 0) + int(items[i].FmtValue.value.largeValue)
        return out
    finally:
        pdh.PdhCloseQuery(query)
