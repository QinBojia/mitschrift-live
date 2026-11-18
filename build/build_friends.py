"""Build the friends' editions of Mitschrift Live (随堂字幕): DeepSeek translation with a bundled key, models
included, speech recognition on the CPU (--edition cpu), on an Intel NPU (--edition npu) or on
integrated graphics via DirectML (--edition amd), the last two with CPU fallback.
Produces a self-contained folder, then (optionally) the installer.

Layout of the output folder (BUILD_ROOT/LiveCapture-CPU or -NPU):
  python/            official embeddable Python 3.12 + site-packages + VC++ runtime DLLs
  app/livecapture/   code          app/ui/  pages          app/models/  ASR + VAD models
  app/edition.json   edition (allowed engines, deepseek-flash, obfuscated key)
  live-capture.ico   licenses/   使用说明.txt

Usage (from the dev venv):
  python build/build_friends.py --edition cpu              build the folder
  python build/build_friends.py --edition npu --installer  ... and compile build/installer.iss
  python build/build_friends.py --patch                    small code-only update (build/update.iss)
Bump livecapture.__version__ first; a patch only works while packages and models are unchanged.

The DeepSeek key comes from FRIENDS_DEEPSEEK_KEY, else DEEPSEEK_API_KEY (process or user
environment). It is obfuscated in edition.json and never printed.
"""

from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from livecapture import __version__  # noqa: E402
from livecapture.edition import hide  # noqa: E402
from livecapture.translate import _user_env  # noqa: E402
from livecapture.tray import save_app_icon  # noqa: E402

BUILD_ROOT = Path(os.environ.get("LIVECAPTURE_BUILD", Path.home() / "LiveCaptureBuild"))
CACHE = BUILD_ROOT / "cache"
DIST = BUILD_ROOT / "LiveCapture-CPU"  # set from --edition in main()
PY_VERSION = "3.12.10"  # newest 3.12 with an official embeddable build
PY_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
DEV_MODELS = Path.home() / ".livecapture" / "models"

# runtime packages of the windowed app (no CUDA / OpenVINO / Claude / CLI extras);
# versions are pinned to what the dev venv runs, so the build behaves like the tests
PACKAGES = {
    "onnxruntime": "onnxruntime-gpu",  # CPU build, same version as the dev GPU build
    "onnx-asr": "onnx-asr", "numpy": "numpy", "soundfile": "soundfile", "soxr": "soxr",
    "pyaudiowpatch": "pyaudiowpatch", "livekit": "livekit", "pywebview": "pywebview",
    "pystray": "pystray", "pillow": "pillow", "pycaw": "pycaw", "websockets": "websockets",
    "openai": "openai", "kaldi-native-fbank": "kaldi-native-fbank",
}
EDITIONS = {
    "cpu": {"engines": ["cpu"], "engine": "cpu", "packages": {}, "models": {}},
    "npu": {"engines": ["npu", "cpu"], "engine": "npu",  # falls back to CPU without a usable NPU
            "packages": {"openvino": "openvino"},
            "models": {"parakeet-ov": ["encoder-int8q.xml", "encoder-int8q.bin"]}},
    # integrated graphics (AMD Radeon, also Intel) through DirectML; falls back to CPU.
    # onnxruntime-directml replaces onnxruntime (same module name; it also has the CPU EP).
    # An "==x" value pins a package that is not in the dev venv; None drops one.
    "amd": {"engines": ["dml", "cpu"], "engine": "dml",
            "packages": {"onnxruntime": None, "onnxruntime-directml": "==1.24.4"},
            "models": {"parakeet-tdt-0.6b-v3-onnx": ["encoder-model.fp16.onnx"]}},  # bench/to_fp16.py
}
MODEL_FILES = {
    "parakeet-tdt-0.6b-v3-onnx": ["config.json", "vocab.txt", "nemo128.onnx",
                                  "encoder-model.int8.onnx", "decoder_joint-model.int8.onnx"],
    "sensevoice-small-2024-07-17": ["model.int8.onnx", "tokens.txt"],
    "silero-vad": ["silero_vad.onnx"],
}
VC_RUNTIME = ["msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll",
              "msvcp140_codecvt_ids.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll"]
MODEL_NOTICE = """Models bundled with Mitschrift Live (随堂字幕)
=============================================

NVIDIA Parakeet TDT 0.6B v3 (speech recognition, int8 ONNX export by istupakov/onnx-asr)
  https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3  -  license: CC BY 4.0
  https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx
  (the NPU edition's int8 OpenVINO encoder and the AMD edition's fp16 encoder are conversions
   of the same model made for Mitschrift Live)

SenseVoice Small (Chinese recognition + language ID, FunAudioLLM / Alibaba, ONNX by k2-fsa)
  https://huggingface.co/FunAudioLLM/SenseVoiceSmall
  https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17
  license: FunASR Model Open Source License 1.1, Copyright (C) 2023-2028 Alibaba Group
  (full text: FunASR-MODEL_LICENSE.txt in this folder)

Silero VAD (voice activity detection)
  https://github.com/snakers4/silero-vad  -  license: MIT
"""


def step(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def deepseek_key() -> str:
    key = os.environ.get("FRIENDS_DEEPSEEK_KEY") or os.environ.get("DEEPSEEK_API_KEY") or _user_env("DEEPSEEK_API_KEY")
    if not key:
        sys.exit("No DeepSeek key: set FRIENDS_DEEPSEEK_KEY (or DEEPSEEK_API_KEY) first")
    return key


def fetch_python() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    z = CACHE / Path(PY_URL).name
    if not z.exists():
        step(f"downloading {PY_URL}")
        urllib.request.urlretrieve(PY_URL, z)  # noqa: S310 - fixed python.org URL
    py = DIST / "python"
    with zipfile.ZipFile(z) as f:
        f.extractall(py)
    # module search path of the embeddable build: stdlib zip, site-packages, our app
    (py / "python312._pth").write_text("python312.zip\n.\nLib\\site-packages\n..\\app\nimport site\n", encoding="ascii")
    for dll in VC_RUNTIME:  # app-local VC++ runtime: no separate redistributable install needed
        src = Path(os.environ["SystemRoot"]) / "System32" / dll
        if src.exists():
            shutil.copy2(src, py / dll)


def install_packages(edition: str) -> None:
    reqs = []
    for name, src in {**PACKAGES, **EDITIONS[edition]["packages"]}.items():
        if src is not None:
            reqs.append(name + src if src.startswith("==") else f"{name}=={md.version(src)}")
    step("installing: " + " ".join(reqs))
    target = DIST / "python" / "Lib" / "site-packages"
    subprocess.run(["uv", "pip", "install", "--target", str(target), "--python-version", "3.12",
                    "--python-platform", "x86_64-pc-windows-msvc", "--quiet", *reqs],
                   check=True)
    for junk in ("tests", "test"):  # a little lighter
        for d in target.glob(f"*/{junk}"):
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)


def copy_app(edition: str) -> None:
    app = DIST / "app"
    shutil.copytree(ROOT / "livecapture", app / "livecapture", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "ui", app / "ui")
    extra = EDITIONS[edition]["models"]
    for model, files in {m: MODEL_FILES.get(m, []) + extra.get(m, []) for m in {*MODEL_FILES, *extra}}.items():
        (app / "models" / model).mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(DEV_MODELS / model / f, app / "models" / model / f)
    ed = EDITIONS[edition]
    info = {"name": f"friends-{edition}", "engines": ed["engines"], "engine": ed["engine"],
            "models": ["deepseek-flash"], "default_model": "deepseek-flash", "deepseek_key": hide(deepseek_key())}
    (app / "edition.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    save_app_icon(DIST / "live-capture.ico")
    shutil.copy2(ROOT / "build" / "使用说明.txt", DIST / "使用说明.txt")


def collect_licenses() -> None:
    lic = DIST / "licenses"
    lic.mkdir(exist_ok=True)
    (lic / "MODELS.txt").write_text(MODEL_NOTICE, encoding="utf-8")
    shutil.copy2(ROOT / "build" / "licenses" / "FunASR-MODEL_LICENSE.txt", lic / "FunASR-MODEL_LICENSE.txt")
    shutil.copy2(DIST / "python" / "LICENSE.txt", lic / "Python-LICENSE.txt")
    for info in (DIST / "python" / "Lib" / "site-packages").glob("*.dist-info"):
        files = [p for p in info.rglob("*") if p.is_file() and p.name.upper().startswith(("LICEN", "COPYING", "NOTICE", "AUTHORS"))]
        if files:
            dest = lic / info.name.split("-")[0]
            dest.mkdir(exist_ok=True)
            for p in files:
                shutil.copy2(p, dest / p.name)


def precompile() -> None:
    py = DIST / "python" / "python.exe"
    subprocess.run([str(py), "-m", "compileall", "-q", "-j", "0", str(DIST / "app"),
                    str(DIST / "python" / "Lib" / "site-packages")], check=False)


def _iscc() -> Path:
    iscc = next((p for p in [Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6" / "ISCC.exe",
                             Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 6" / "ISCC.exe",
                             Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"]
                 if p.exists()), None)
    if not iscc:
        sys.exit("Inno Setup 6 not found (winget install JRSoftware.InnoSetup)")
    return iscc


def build_installer(edition: str) -> None:
    step("compiling installer")
    subprocess.run([str(_iscc()), f"/DAppVersion={__version__}", f"/DEdition={edition.upper()}",
                    f"/DSourceDir={DIST}", f"/DOutputDir={BUILD_ROOT}",
                    str(ROOT / "build" / "installer.iss")], check=True)


def build_patch() -> None:
    """MitschriftLive-<version>-Update.exe: only app/livecapture + app/ui, for every edition."""
    stage = BUILD_ROOT / "LiveCapture-Update"
    if stage.exists():
        shutil.rmtree(stage)
    skip = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "livecapture", stage / "app" / "livecapture", ignore=skip)
    shutil.copytree(ROOT / "ui", stage / "app" / "ui", ignore=skip)
    shutil.copy2(ROOT / "build" / "使用说明.txt", stage / "使用说明.txt")
    save_app_icon(stage / "live-capture.ico")
    step(f"compiling update {__version__}")
    subprocess.run([str(_iscc()), f"/DAppVersion={__version__}", f"/DSourceDir={stage}",
                    f"/DOutputDir={BUILD_ROOT}", str(ROOT / "build" / "update.iss")], check=True)


def main() -> None:
    global DIST
    ap = argparse.ArgumentParser()
    ap.add_argument("--edition", choices=sorted(EDITIONS), default="cpu")
    ap.add_argument("--installer", action="store_true", help="also compile the Inno Setup installer")
    ap.add_argument("--installer-only", action="store_true", help="only compile the installer from the existing folder")
    ap.add_argument("--patch", action="store_true", help="only build the small code update (all editions)")
    args = ap.parse_args()
    if args.patch:
        return build_patch()
    DIST = BUILD_ROOT / f"LiveCapture-{args.edition.upper()}"
    if not args.installer_only:
        if DIST.exists():
            shutil.rmtree(DIST)
        DIST.mkdir(parents=True)
        step("embeddable Python");  fetch_python()      # noqa: E702
        step("packages");           install_packages(args.edition)  # noqa: E702
        step("app, ui, models");    copy_app(args.edition)          # noqa: E702
        step("licenses");           collect_licenses()  # noqa: E702
        step("precompile");         precompile()        # noqa: E702
        size = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file()) / 2**20
        step(f"folder ready: {DIST} ({size:.0f} MB)")
    if args.installer or args.installer_only:
        build_installer(args.edition)


if __name__ == "__main__":
    main()
