"""Build Windows installers of Mitschrift Live (随堂字幕): a self-contained folder (official
embeddable Python, packages, models) plus an Inno Setup installer, per edition: speech
recognition on the CPU (--edition cpu), an Intel NPU (--edition npu) or integrated graphics
via DirectML (--edition amd), the last two with CPU fallback.

Public builds (default) contain no API key: users enter their own in the app.
--friends builds a private variant for people you trust: DeepSeek with a key bundled from
FRIENDS_DEEPSEEK_KEY (else DEEPSEEK_API_KEY), plus Claude Haiku with the user's own key. The key is only obfuscated - anyone with
the installer can extract it - so use a separate key with a spending limit and never
publish such builds.

Layout of the output folder (BUILD_ROOT/Public-<ED>, or LiveCapture-<ED> for --friends):
  python/            official embeddable Python 3.12 + site-packages + VC++ runtime DLLs
  app/livecapture/   code          app/ui/  pages          app/models/  ASR + VAD models
  app/edition.json   edition: allowed engines (friends: model, key, language)
  live-capture.ico   licenses/   QuickStart.txt (friends: 使用说明.txt)

Usage (from the dev venv):
  python build/build_installer.py --edition cpu --installer    public CPU installer
  python build/build_installer.py --edition amd --friends --installer
  python build/build_installer.py --patch                      code-only update, any edition
Installers land in BUILD_ROOT/public (public) or BUILD_ROOT (friends); the update in BUILD_ROOT.
Bump livecapture.__version__ first. A patch carries code only: it must run with the packages
and models of every version it may land on (0.3.0 changed the models; older installs keep
their encoders and the code falls back to them, see models.parakeet_int8 / npu_encoder).
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
from livecapture.keys import user_env  # noqa: E402
from livecapture.tray import save_app_icon  # noqa: E402

BUILD_ROOT = Path(os.environ.get("LIVECAPTURE_BUILD", Path.home() / "LiveCaptureBuild"))
CACHE = BUILD_ROOT / "cache"
DIST = BUILD_ROOT / "LiveCapture-CPU"  # set from --edition in main()
PY_VERSION = "3.12.10"  # newest 3.12 with an official embeddable build
PY_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
DEV_MODELS = Path.home() / ".livecapture" / "models"

# runtime packages of the windowed app (no CUDA / OpenVINO / CLI extras);
# versions are pinned to what the dev venv runs, so the build behaves like the tests
PACKAGES = {
    "onnxruntime": "onnxruntime-gpu",  # CPU build, same version as the dev GPU build
    "onnx-asr": "onnx-asr", "numpy": "numpy", "soundfile": "soundfile", "soxr": "soxr",
    "pyaudiowpatch": "pyaudiowpatch", "livekit": "livekit", "pywebview": "pywebview",
    "pystray": "pystray", "pillow": "pillow", "pycaw": "pycaw", "websockets": "websockets",
    "openai": "openai", "anthropic": "anthropic", "kaldi-native-fbank": "kaldi-native-fbank",
}
EDITIONS = {
    "cpu": {"engines": ["cpu"], "engine": "cpu", "packages": {}, "models": {}},
    "npu": {"engines": ["npu", "cpu"], "engine": "npu",  # falls back to CPU without a usable NPU
            "packages": {"openvino": "openvino"},
            "models": {"parakeet-tdt-0.6b-v3-onnx": ["encoder-model.fp16.onnx"]}},  # bench/to_fp16.py
    # integrated graphics (AMD Radeon, also Intel) through DirectML; falls back to CPU.
    # onnxruntime-directml replaces onnxruntime (same module name; it also has the CPU EP).
    # An "==x" value pins a package that is not in the dev venv; None drops one.
    "amd": {"engines": ["dml", "cpu"], "engine": "dml",
            "packages": {"onnxruntime": None, "onnxruntime-directml": "==1.24.4"},
            "models": {"parakeet-tdt-0.6b-v3-onnx": ["encoder-model.fp16.onnx"]}},  # bench/to_fp16.py
}
MODEL_FILES = {
    # bench/to_int8.py: int8 encoder with the fragile convolutions in fp32, int8 decoder
    "parakeet-cpu": ["config.json", "vocab.txt", "nemo128.onnx",
                     "encoder-model.int8.onnx", "decoder_joint-model.int8.onnx"],
    "sensevoice-small-2024-07-17": ["model.int8.onnx", "tokens.txt"],
    "silero-vad": ["silero_vad.onnx"],
}
VC_RUNTIME = ["msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll",
              "msvcp140_codecvt_ids.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll"]
MODEL_NOTICE = """Models bundled with Mitschrift Live (随堂字幕)
=============================================

NVIDIA Parakeet TDT 0.6B v3 (speech recognition, ONNX export by istupakov/onnx-asr)
  https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3  -  license: CC BY 4.0
  https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx
  (the int8 encoder, and the fp16 encoder of the NPU and AMD editions, are conversions of
   that export made for Mitschrift Live: bench/to_int8.py, bench/to_fp16.py)

SenseVoice Small (Chinese recognition + language ID, FunAudioLLM / Alibaba, ONNX by k2-fsa)
  https://huggingface.co/FunAudioLLM/SenseVoiceSmall
  https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17
  license: FunASR Model Open Source License 1.1, Copyright (C) 2023-2028 Alibaba Group
  (full text: FunASR-MODEL_LICENSE.txt in this folder)

Silero VAD (voice activity detection)
  https://github.com/snakers4/silero-vad  -  license: MIT
"""


def clean_env() -> dict[str, str]:
    """Environment for child processes without the variables uv's venv launcher sets for this
    interpreter: inherited by uv or the embedded Python they make another Python load this
    one's standard library ("SRE module mismatch")."""
    return {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH", "UV_INTERNAL__PYTHONHOME")}


def step(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def deepseek_key() -> str:
    key = os.environ.get("FRIENDS_DEEPSEEK_KEY") or os.environ.get("DEEPSEEK_API_KEY") or user_env("DEEPSEEK_API_KEY")
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
                   check=True, env=clean_env())
    for junk in ("tests", "test"):  # a little lighter
        for d in target.glob(f"*/{junk}"):
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)


def copy_app(edition: str, friends: bool) -> None:
    app = DIST / "app"
    shutil.copytree(ROOT / "livecapture", app / "livecapture", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "ui", app / "ui")
    extra = EDITIONS[edition]["models"]
    for model, files in {m: MODEL_FILES.get(m, []) + extra.get(m, []) for m in {*MODEL_FILES, *extra}}.items():
        (app / "models" / model).mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(DEV_MODELS / model / f, app / "models" / model / f)
    ed = EDITIONS[edition]
    info = {"name": f"{'friends' if friends else 'public'}-{edition}", "engines": ed["engines"], "engine": ed["engine"]}
    if friends:
        # DeepSeek with the bundled key; Claude Haiku and Microsoft Translator with the user's
        # own key (edition.ALWAYS_MODELS)
        info.update(models=["deepseek-flash", "claude-haiku-5-5", "azure-translator"], default_model="deepseek-flash",
                    deepseek_key=hide(deepseek_key()), ui_lang="zh")
    (app / "edition.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    save_app_icon(DIST / "live-capture.ico")
    shutil.copy2(ROOT / "build" / notes_file(friends), DIST / notes_file(friends))


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
                    str(DIST / "python" / "Lib" / "site-packages")], check=False, env=clean_env())


def _iscc() -> Path:
    iscc = next((p for p in [Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6" / "ISCC.exe",
                             Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 6" / "ISCC.exe",
                             Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"]
                 if p.exists()), None)
    if not iscc:
        sys.exit("Inno Setup 6 not found (winget install JRSoftware.InnoSetup)")
    return iscc


def notes_file(friends: bool) -> str:
    return "使用说明.txt" if friends else "QuickStart.txt"


def build_installer(edition: str, friends: bool) -> None:
    step("compiling installer")
    out = BUILD_ROOT if friends else BUILD_ROOT / "public"
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(_iscc()), f"/DAppVersion={__version__}", f"/DEdition={edition.upper()}",
                    f"/DSourceDir={DIST}", f"/DOutputDir={out}", f"/DNotes={notes_file(friends)}",
                    *(["/DFriends"] if friends else []), str(ROOT / "build" / "installer.iss")], check=True)


def build_patch() -> None:
    """MitschriftLive-<version>-Update.exe: only app/livecapture + app/ui, for every edition."""
    stage = BUILD_ROOT / "LiveCapture-Update"
    if stage.exists():
        shutil.rmtree(stage)
    skip = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "livecapture", stage / "app" / "livecapture", ignore=skip)
    shutil.copytree(ROOT / "ui", stage / "app" / "ui", ignore=skip)
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
    ap.add_argument("--friends", action="store_true", help="private build with a bundled DeepSeek key (see above)")
    ap.add_argument("--patch", action="store_true", help="only build the small code update (all editions)")
    args = ap.parse_args()
    if args.patch:
        return build_patch()
    DIST = BUILD_ROOT / (f"LiveCapture-{args.edition.upper()}" if args.friends else f"Public-{args.edition.upper()}")
    if not args.installer_only:
        if DIST.exists():
            shutil.rmtree(DIST)
        DIST.mkdir(parents=True)
        step("embeddable Python");  fetch_python()      # noqa: E702
        step("packages");           install_packages(args.edition)  # noqa: E702
        step("app, ui, models");    copy_app(args.edition, args.friends)  # noqa: E702
        step("licenses");           collect_licenses()  # noqa: E702
        step("precompile");         precompile()        # noqa: E702
        size = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file()) / 2**20
        step(f"folder ready: {DIST} ({size:.0f} MB)")
    if args.installer or args.installer_only:
        build_installer(args.edition, args.friends)


if __name__ == "__main__":
    main()
