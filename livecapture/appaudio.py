"""The sound of one app instead of a whole output device (Windows "process loopback").

Windows can capture what one process and its child processes play (ActivateAudioInterfaceAsync
with AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK, Windows 10 2004 and later), whichever
output device it plays on. Measured on Windows 11 (2026-11-24, scratch tests with tone
players): the other apps' sound stays out; children started after the capture are included;
grandchildren are NOT (only direct children count as the "tree"); the stream runs on in 10 ms
packets of silence while the app is quiet, and even after it exited.

So an app is captured as several processes, mixed: its top process(es), plus each of its
processes that has children of its own (see targets()). Chrome's main process covers its audio
service, a direct child; a Zoom meeting process is a child of the main Zoom; New Teams plays from
msedgewebview2.exe two levels below ms-teams.exe, so the WebView2 browser process in between is
captured too. Which app a process belongs to (app_root): go up to the topmost ancestor of the same
exe, and past an embedded browser (WebView2 and the like) to the app that hosts it - not further:
a Zoom started from a link in Chrome is still Zoom (seen on a real PC).

The processes are looked up again every second: an app that starts later is picked up, one
that quits stops, and a process it starts later (a call's audio process) is in a capture at once
when its parent is - or within a second when it gets children itself.

comtypes comes with pycaw (a dependency anyway, for following the default devices).
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys
import threading
import time
from ctypes import POINTER, Structure, byref, c_long, c_uint32, c_uint64, c_ulong, c_ushort, c_void_p, wintypes

import numpy as np

from . import SAMPLE_RATE as SR

PREFIX = "app:"  # Settings.speaker = "app:<exe name>" captures that app instead of a device
HELPERS = {"msedgewebview2.exe", "wechatappex.exe", "cefsharp.browsersubprocess.exe", "qtwebengineprocess.exe"}
SHELLS = {"explorer.exe", "svchost.exe", "services.exe", "wininit.exe", "winlogon.exe", "sihost.exe", "userinit.exe"}
REFRESH = 1.0  # s between looks for the app's processes (one Toolhelp snapshot, ~ms)
BLOCK = SR // 50  # 20 ms: blocks handed on, like a device
MAX_SKEW = int(0.3 * SR)  # a part that delivers this much less than the others is padded with silence


def supported() -> bool:
    """Process loopback exists from Windows 10 2004 (build 19041) on."""
    return sys.platform == "win32" and sys.getwindowsversion().build >= 19041


def app_exe(speaker: str) -> str | None:
    """The exe name if this speaker setting means an app ("app:Zoom.exe"), else None."""
    return speaker[len(PREFIX):] if speaker.startswith(PREFIX) and len(speaker) > len(PREFIX) else None


# ------------------------------------------------------------------ processes
# A snapshot is {pid: (exe name lowercased, parent pid)}, from one Toolhelp snapshot (psutil's
# process_iter skipped some live processes here, 2026-11-24).

class _ProcessEntry(Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_k32.Process32FirstW.argtypes = _k32.Process32NextW.argtypes = [wintypes.HANDLE, POINTER(_ProcessEntry)]
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, POINTER(wintypes.DWORD)]
SYNCHRONIZE, QUERY_LIMITED = 0x00100000, 0x1000
INVALID_HANDLE = wintypes.HANDLE(-1).value


def snapshot() -> dict[int, tuple[str, int]]:
    procs = {}
    snap = _k32.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == INVALID_HANDLE:
        return procs
    try:
        e = _ProcessEntry()
        e.dwSize = ctypes.sizeof(e)
        ok = _k32.Process32FirstW(snap, byref(e))
        while ok:
            procs[e.th32ProcessID] = (e.szExeFile.lower(), e.th32ParentProcessID)
            ok = _k32.Process32NextW(snap, byref(e))
    finally:
        _k32.CloseHandle(snap)
    return procs


def exe_path(pid: int) -> str | None:
    h = _k32.OpenProcess(QUERY_LIMITED, False, pid)
    if not h:
        return None
    try:
        buf, n = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
        return buf.value if _k32.QueryFullProcessImageNameW(h, 0, buf, byref(n)) else None
    finally:
        _k32.CloseHandle(h)


def app_root(pid: int, procs: dict) -> int | None:
    """The process that stands for the app `pid` belongs to (see the module notes)."""
    if pid not in procs:
        return None
    seen = set()
    while pid not in seen:
        seen.add(pid)
        name, parent = procs[pid][0], procs[pid][1]
        up = procs.get(parent)
        if up is None or parent in seen or up[0] in SHELLS:
            return pid
        if up[0] == name or name in HELPERS:  # same exe further up, or an embedded browser's host
            pid = parent
            continue
        return pid
    return pid


def targets(exe: str, procs: dict) -> set[int]:
    """The processes to capture for app `exe`. A capture takes a process and its direct
    children, so: the app's top processes, and every process of the app that has children of
    the app - then each of its processes is in one capture, whichever of them plays (also one
    that only starts to play later)."""
    exe = exe.lower()
    members = set()
    for pid in procs:
        root = app_root(pid, procs)
        if root is not None and procs[root][0] == exe:
            members.add(pid)
    return {pid for pid in members if procs[pid][1] not in members} | {procs[pid][1] for pid in members} & members


# ------------------------------------------------------------------ COM: sessions and capture

_lib = None


def _com():
    """Interfaces and functions, defined on first use (comtypes is imported lazily)."""
    global _lib
    if _lib is not None:
        return _lib
    import comtypes
    from comtypes import COMMETHOD, COMObject, GUID, HRESULT, IUnknown
    from pycaw.api.audioclient import IAudioClient
    from pycaw.api.audioclient.depend import WAVEFORMATEX

    class IActivateAudioInterfaceAsyncOperation(IUnknown):
        _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
        _methods_ = [COMMETHOD([], HRESULT, "GetActivateResult",
                               (["out"], POINTER(c_long), "activateResult"),
                               (["out"], POINTER(POINTER(IUnknown)), "activatedInterface"))]

    class IActivateAudioInterfaceCompletionHandler(IUnknown):
        _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
        _methods_ = [COMMETHOD([], HRESULT, "ActivateCompleted",
                               (["in"], POINTER(IActivateAudioInterfaceAsyncOperation), "activateOperation"))]

    class IAgileObject(IUnknown):  # the completion handler must be agile
        _iid_ = GUID("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
        _methods_ = []

    class IAudioCaptureClient(IUnknown):
        _iid_ = GUID("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetBuffer", (["out"], POINTER(c_void_p), "data"),
                      (["out"], POINTER(c_uint32), "frames"), (["out"], POINTER(wintypes.DWORD), "flags"),
                      (["out"], POINTER(c_uint64), "devicePosition"), (["out"], POINTER(c_uint64), "qpcPosition")),
            COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint32, "frames")),
            COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], POINTER(c_uint32), "frames")),
        ]

    class Handler(COMObject):
        _com_interfaces_ = [IActivateAudioInterfaceCompletionHandler, IAgileObject]

        def __init__(self):
            super().__init__()
            self.done = threading.Event()

        def ActivateCompleted(self, this, op):  # noqa: N802 - COM method name
            self.done.set()
            return 0

    class Blob(Structure):
        _fields_ = [("cbSize", c_ulong), ("pBlobData", c_void_p)]

    class PropVariant(Structure):  # just the VT_BLOB case (24 bytes on x64)
        _fields_ = [("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort), ("r3", c_ushort),
                    ("blob", Blob), ("pad", c_void_p)]

    class LoopbackParams(Structure):  # AUDIOCLIENT_ACTIVATION_PARAMS for process loopback
        _fields_ = [("ActivationType", c_ulong), ("TargetProcessId", wintypes.DWORD),
                    ("ProcessLoopbackMode", c_ulong)]

    activate = ctypes.WinDLL("Mmdevapi").ActivateAudioInterfaceAsync
    activate.argtypes = [wintypes.LPCWSTR, POINTER(GUID), POINTER(PropVariant),
                         POINTER(IActivateAudioInterfaceCompletionHandler),
                         POINTER(POINTER(IActivateAudioInterfaceAsyncOperation))]
    activate.restype = HRESULT
    k32 = _k32
    k32.CreateEventW.restype = wintypes.HANDLE
    k32.CreateEventW.argtypes = [c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    k32.WaitForMultipleObjects.argtypes = [wintypes.DWORD, POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD]
    _lib = dict(comtypes=comtypes, IAudioClient=IAudioClient, WAVEFORMATEX=WAVEFORMATEX, Handler=Handler,
                IActivateAudioInterfaceAsyncOperation=IActivateAudioInterfaceAsyncOperation,
                IActivateAudioInterfaceCompletionHandler=IActivateAudioInterfaceCompletionHandler,
                IAudioCaptureClient=IAudioCaptureClient, Blob=Blob, PropVariant=PropVariant,
                LoopbackParams=LoopbackParams, activate=activate, k32=k32)
    return _lib


def session_pids() -> list[int]:
    """Processes with an audio session on any active output device (needs COM on this thread)."""
    import comtypes
    from pycaw.constants import CLSID_MMDeviceEnumerator, DEVICE_STATE, EDataFlow
    from pycaw.pycaw import IAudioSessionControl2, IAudioSessionManager2, IMMDeviceEnumerator

    pids = []
    e = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)
    col = e.EnumAudioEndpoints(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)
    for i in range(col.GetCount()):
        try:
            mgr = col.Item(i).Activate(IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None)
            sessions = mgr.QueryInterface(IAudioSessionManager2).GetSessionEnumerator()
            for j in range(sessions.GetCount()):
                pid = sessions.GetSession(j).QueryInterface(IAudioSessionControl2).GetProcessId()
                if pid and pid not in pids:
                    pids.append(pid)
        except Exception:  # noqa: BLE001, S112 - a device that went away meanwhile
            continue
    return pids


_descriptions: dict[str, str] = {}


def describe(path: str | None, exe: str) -> str:
    """An app's display name: the exe's FileDescription ("Google Chrome"), else the exe name."""
    stem = exe[:-4] if exe.lower().endswith(".exe") else exe
    if not path:
        return _descriptions.get(exe.lower(), stem)
    if path in _descriptions:
        return _descriptions[path]
    name = stem
    try:
        ver = ctypes.WinDLL("version")
        ver.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, c_void_p]
        ver.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, c_void_p]
        ver.VerQueryValueW.argtypes = [c_void_p, wintypes.LPCWSTR, POINTER(c_void_p), POINTER(wintypes.UINT)]
        size = ver.GetFileVersionInfoSizeW(path, None)
        buf = ctypes.create_string_buffer(size)
        p, n = c_void_p(), wintypes.UINT()
        if size and ver.GetFileVersionInfoW(path, 0, size, buf) and \
                ver.VerQueryValueW(buf, r"\VarFileInfo\Translation", byref(p), byref(n)) and n.value >= 4:
            lang, cp = struct.unpack("<HH", ctypes.string_at(p, 4))
            if ver.VerQueryValueW(buf, rf"\StringFileInfo\{lang:04x}{cp:04x}\FileDescription", byref(p), byref(n)) \
                    and n.value:
                name = ctypes.wstring_at(p, n.value).rstrip("\0").strip() or stem
    except OSError:
        pass
    _descriptions[path] = _descriptions[exe.lower()] = name
    return name


def list_apps() -> list[dict]:
    """Apps that have used sound since they started (an audio session), for the dropdown:
    [{"exe": "Zoom.exe", "name": "Zoom Workplace"}], sorted by name; not this app itself."""
    global _listed
    pids = session_pids()
    if _listed and _listed[0] == pids:  # the same sessions: the same apps (spares the snapshot, ~15 ms)
        return _listed[1]
    procs = snapshot()
    own_dir = os.path.dirname(sys.executable).lower()
    apps: dict[str, dict] = {}
    for pid in pids:
        root = app_root(pid, procs)
        if root is None or root == os.getpid() or procs[root][0] in apps:
            continue
        path = exe_path(root)
        if path and os.path.dirname(path).lower() == own_dir:
            continue  # our own interpreter (e.g. the capture keeps a silent stream open)
        exe = os.path.basename(path) if path else procs[root][0]
        apps[procs[root][0]] = {"exe": exe, "name": describe(path, exe)}
    _listed = (pids, sorted(apps.values(), key=lambda a: a["name"].lower()))
    return _listed[1]


_listed: tuple[list[int], list[dict]] | None = None  # the last list_apps() and the sessions it came from


class _Capture:
    """One process tree (the process and its direct children), 16 kHz mono float32."""

    def __init__(self, pid: int):
        lib = _com()
        self.pid = pid
        self.process = _k32.OpenProcess(SYNCHRONIZE, False, pid)  # None: can't tell when it quits
        params = lib["LoopbackParams"](1, pid, 0)  # PROCESS_LOOPBACK, INCLUDE_TARGET_PROCESS_TREE
        pv = lib["PropVariant"](vt=65, blob=lib["Blob"](ctypes.sizeof(params), ctypes.addressof(params)))  # VT_BLOB
        handler = lib["Handler"]()
        op = POINTER(lib["IActivateAudioInterfaceAsyncOperation"])()
        lib["activate"]("VAD\\Process_Loopback", byref(lib["IAudioClient"]._iid_), byref(pv),
                        handler.QueryInterface(lib["IActivateAudioInterfaceCompletionHandler"]), byref(op))
        if not handler.done.wait(5):
            raise TimeoutError("process loopback activation timed out")
        hr, unk = op.GetActivateResult()
        if hr < 0:
            raise OSError(hr, f"process loopback activation failed (0x{hr & 0xFFFFFFFF:08X})")
        self.client = unk.QueryInterface(lib["IAudioClient"])
        fmt = lib["WAVEFORMATEX"](3, 1, SR, SR * 4, 4, 32, 0)  # IEEE float, mono, 16 kHz: Windows converts
        flags = 0x00020000 | 0x00040000 | 0x80000000 | 0x08000000  # loopback, event, autoconvert, good SRC
        self.client.Initialize(0, flags, 2_000_000, 0, byref(fmt), None)  # shared, 200 ms buffer
        self.event = lib["k32"].CreateEventW(None, False, False, None)
        self.client.SetEventHandle(self.event)
        self.cap = self.client.GetService(byref(lib["IAudioCaptureClient"]._iid_)).QueryInterface(
            lib["IAudioCaptureClient"])
        self.client.Start()
        self.glitches = 0

    def read(self) -> np.ndarray:
        parts = []
        while self.cap.GetNextPacketSize():
            data, n, flags, _, _ = self.cap.GetBuffer()
            if flags & 1:  # AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY: we were too slow
                self.glitches += 1
            if flags & 2 or not data:  # AUDCLNT_BUFFERFLAGS_SILENT
                parts.append(np.zeros(n, np.float32))
            else:
                parts.append(np.ctypeslib.as_array(ctypes.cast(data, POINTER(ctypes.c_float)), (n,)).copy())
            self.cap.ReleaseBuffer(n)
        return np.concatenate(parts) if parts else np.zeros(0, np.float32)

    def close(self) -> None:
        try:
            self.client.Stop()
        except Exception:  # noqa: BLE001
            pass
        self.cap = self.client = None
        _k32.CloseHandle(self.event)
        if self.process:
            _k32.CloseHandle(self.process)

    @property
    def gone(self) -> bool:
        """Its process quit (a capture of a gone process just delivers silence)."""
        return bool(self.process) and _k32.WaitForSingleObject(self.process, 0) == 0


class Mixer:
    """Adds up the parts' samples in step; a part that falls behind (it stopped delivering)
    is padded with silence instead of holding the others up."""

    def __init__(self) -> None:
        self.bufs: dict[object, np.ndarray] = {}

    def push(self, key, x: np.ndarray) -> None:
        b = self.bufs.get(key)
        self.bufs[key] = x if b is None or not len(b) else np.concatenate([b, x])

    def remove(self, key) -> None:
        self.bufs.pop(key, None)

    def pull(self) -> np.ndarray:
        if not self.bufs:
            return np.zeros(0, np.float32)
        hi = max(len(b) for b in self.bufs.values())
        for k, b in self.bufs.items():
            if hi - len(b) > MAX_SKEW:
                self.bufs[k] = np.concatenate([np.zeros(hi - MAX_SKEW - len(b), np.float32), b])
        n = min(len(b) for b in self.bufs.values())
        if n == 0:
            return np.zeros(0, np.float32)
        out = np.zeros(n, np.float32)
        for k, b in self.bufs.items():
            out += b[:n]
            self.bufs[k] = b[n:]
        return out


class AppSource:
    """The "other" side from one app (an audio source like audio.LiveSource)."""

    def __init__(self, exe: str, waiting_label=None):
        """waiting_label(name) -> the name shown while the app isn't running."""
        if not supported():
            raise RuntimeError("capturing a single app needs Windows 10 version 2004 or later")
        self.exe = exe
        self.overflows = 0
        self._waiting_label = waiting_label or (lambda n: n)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._caps: dict[int, _Capture] = {}  # by pid
        self._failed: set[int] = set()
        self._named = False

    @property
    def running(self) -> bool:
        return bool(self._caps)

    @property
    def name(self) -> str:
        n = describe(None, self.exe)
        return n if self._caps else self._waiting_label(n)

    def describe(self) -> str:
        return f"{self.name} (app, {len(self._caps)} process(es))"

    def start(self, on_audio) -> None:
        self._thread = threading.Thread(target=self._run, args=(on_audio,), daemon=True, name=f"app-{self.exe}")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def _run(self, on_audio) -> None:
        if "comtypes" not in sys.modules:
            # importing comtypes initializes COM in the importing thread, as single-threaded
            # unless sys.coinit_flags says otherwise - this thread needs the multithreaded kind
            old = getattr(sys, "coinit_flags", None)
            sys.coinit_flags = 0
            try:
                lib = _com()
            finally:
                if old is None:
                    del sys.coinit_flags
                else:
                    sys.coinit_flags = old
        lib = _com()
        lib["comtypes"].CoInitializeEx(lib["comtypes"].COINIT_MULTITHREADED)
        mixer, pending, next_look = Mixer(), [], 0.0
        try:
            while not self._stop.is_set():
                if time.monotonic() >= next_look:
                    next_look = time.monotonic() + REFRESH
                    self._update(mixer)
                caps = list(self._caps.items())
                if not caps:
                    self._stop.wait(0.2)
                    continue
                handles = (wintypes.HANDLE * len(caps))(*(c.event for _, c in caps))
                lib["k32"].WaitForMultipleObjects(len(caps), handles, False, 20)
                for key, c in caps:
                    try:
                        mixer.push(key, c.read())
                        self.overflows += c.glitches
                        c.glitches = 0
                    except Exception:  # noqa: BLE001 - e.g. the audio engine restarted: reopened at the next look
                        self._caps.pop(key, None)
                        mixer.remove(key)
                        try:
                            c.close()
                        except Exception:  # noqa: BLE001
                            pass
                out = mixer.pull()
                if len(out):
                    pending.append(out)
                if sum(len(p) for p in pending) >= BLOCK:
                    block, pending = np.concatenate(pending), []
                    try:
                        on_audio(block)
                    except Exception as e:  # noqa: BLE001 - keep capturing, like a device callback
                        print(f"app capture {self.exe}: on_audio failed: {type(e).__name__}: {e}", flush=True)
        finally:
            for c in self._caps.values():
                c.close()
            self._caps.clear()
            lib["comtypes"].CoUninitialize()

    def _update(self, mixer: Mixer) -> None:
        """Open captures for the app's processes now, close those whose process is gone."""
        try:
            procs = snapshot()
            want = targets(self.exe, procs)
        except Exception as e:  # noqa: BLE001 - try again at the next look
            print(f"app capture {self.exe}: {type(e).__name__}: {e}", flush=True)
            return
        for pid in [p for p, c in self._caps.items() if p not in want or c.gone]:
            self._caps.pop(pid).close()
            mixer.remove(pid)
        self._failed &= want  # a pid that comes back is another process
        for pid in want - set(self._caps) - self._failed:
            try:
                self._caps[pid] = _Capture(pid)
            except Exception as e:  # noqa: BLE001 - don't retry this process every 2 s
                self._failed.add(pid)
                print(f"app capture {self.exe} pid {pid}: {type(e).__name__}: {e}", flush=True)
        if self._caps and not self._named:
            self._named = self._name_from(procs)

    def _name_from(self, procs: dict) -> bool:
        """Remember the display name (FileDescription) while the app runs - from a process of
        the app's own exe, not from an embedded browser it captures too."""
        for pid in self._caps:
            path = exe_path(pid) if procs.get(pid, ("",))[0] == self.exe.lower() else None
            if path:
                describe(path, self.exe)
                return True
        return False
