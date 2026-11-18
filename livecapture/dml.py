"""GPU adapters as DirectML sees them (DXGI enumeration order = ORT DmlExecutionProvider device_id).

No dependencies beyond ctypes; used to pick the integrated GPU (AMD Radeon / Intel) for the
"dml" speech engine and to show its name.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import POINTER, byref, c_void_p, wintypes
from dataclasses import dataclass

VENDORS = {0x1002: "AMD", 0x10DE: "NVIDIA", 0x8086: "Intel", 0x1414: "Microsoft"}


class _GUID(ctypes.Structure):
    _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]


class _DESC1(ctypes.Structure):
    _fields_ = [("Description", wintypes.WCHAR * 128), ("VendorId", wintypes.UINT), ("DeviceId", wintypes.UINT),
                ("SubSysId", wintypes.UINT), ("Revision", wintypes.UINT), ("DedicatedVideoMemory", ctypes.c_size_t),
                ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                ("LuidLow", wintypes.DWORD), ("LuidHigh", wintypes.LONG), ("Flags", wintypes.UINT)]


# IID_IDXGIFactory1 {770aae78-f26f-4dba-a829-253c83d1b387}
_IID_FACTORY1 = _GUID(0x770AAE78, 0xF26F, 0x4DBA, (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))


@dataclass(frozen=True)
class Adapter:
    index: int  # DmlExecutionProvider device_id
    name: str
    vendor: str
    dedicated_mb: int
    shared_mb: int


def _method(obj: c_void_p, index: int, restype, *argtypes):
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(vtbl[index])


def adapters() -> list[Adapter]:
    """Hardware GPUs in DXGI order (software/Basic Render adapters skipped, indices kept)."""
    factory = c_void_p()
    if ctypes.windll.dxgi.CreateDXGIFactory1(byref(_IID_FACTORY1), byref(factory)) != 0:
        return []
    out = []
    try:
        enum1 = _method(factory, 12, ctypes.HRESULT, wintypes.UINT, POINTER(c_void_p))  # IDXGIFactory1::EnumAdapters1
        i = 0
        while True:
            ad = c_void_p()
            try:
                enum1(factory, i, byref(ad))
            except OSError:  # DXGI_ERROR_NOT_FOUND: no more adapters
                break
            desc = _DESC1()
            _method(ad, 10, ctypes.HRESULT, POINTER(_DESC1))(ad, byref(desc))  # IDXGIAdapter1::GetDesc1
            _method(ad, 2, wintypes.ULONG)(ad)  # Release
            if not desc.Flags & 2 and desc.VendorId != 0x1414:  # DXGI_ADAPTER_FLAG_SOFTWARE / Basic Render
                out.append(Adapter(i, desc.Description.strip(), VENDORS.get(desc.VendorId, hex(desc.VendorId)),
                                   desc.DedicatedVideoMemory // 2**20, desc.SharedSystemMemory // 2**20))
            i += 1
    finally:
        _method(factory, 2, wintypes.ULONG)(factory)
    return out


def pick() -> Adapter | None:
    """The integrated GPU if there is one (least dedicated memory), else the only/first GPU.

    LIVECAPTURE_DML_DEVICE=<index> overrides (for testing on a machine with several GPUs).
    """
    found = adapters()
    if not found:
        return None
    forced = os.environ.get("LIVECAPTURE_DML_DEVICE")
    if forced is not None:
        return next((a for a in found if a.index == int(forced)), found[0])
    return min(found, key=lambda a: a.dedicated_mb)


if __name__ == "__main__":
    for a in adapters():
        print(a)
    print("pick:", pick())
