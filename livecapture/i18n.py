"""Interface language for the text the Python side shows: tray menu, caption bar, status and
notices, translation errors. The page has its own table (ui/i18n.js).

"auto" follows the Windows display language: Chinese for a Chinese Windows, English otherwise.
"""

from __future__ import annotations

import ctypes

LANGS = ("zh", "en")
_lang = "zh"

STRINGS: dict[str, dict[str, str]] = {
    "zh": {
        "app": "随堂字幕",
        "who.other": "对方", "who.me": "我", "who.room": "现场",
        "engine.cuda": "GPU", "engine.npu": "NPU", "engine.dml": "核显", "engine.cpu": "CPU",
        "devices.switched": "已切换到新的默认设备：{list}",
        "loading.npu_first": "首次为 NPU 优化模型（约 1 分钟，只需一次）…",
        "loading.engine": "加载识别模型（{label}）…",
        "loading.zh": "加载中文模型…",
        "loading.audio": "打开音频设备…",
        "loading.stopping": "停止中…",
        "engine.fallback": "{label} 不可用，已改用 CPU 识别",
        "error.load": "模型加载失败: {e}",
        "error.start": "无法开始: {e}",
        "translation.off": "翻译关闭",
        "tray.show": "显示主窗口", "tray.start": "开始", "tray.pause": "暂停",
        "tray.overlay": "悬浮字幕", "tray.lock": "鼠标穿透", "tray.lock_hk": "鼠标穿透（{hk}）",
        "tray.src_other": "听系统声音（对方）", "tray.src_me": "听麦克风（我 / 现场）",
        "tray.target": "译文", "tray.target_off": "关闭翻译", "tray.quit": "退出随堂字幕",
        "tray.running": "正在采集", "tray.paused": "已暂停",
        "tray.hint": "随堂字幕还在托盘里运行。右键托盘图标可以退出。",
        "bar.idle": "等待说话…", "bar.pending": "翻译中…", "bar.lock": "穿透",
        "bar.show.both": "原文+译文", "bar.show.original": "仅原文", "bar.show.translation": "仅译文",
        "mt.no_key": "没有 {provider} API key：在 设置 → 翻译 里填写",
        "mt.bad_key": "{provider} API key 无效或无权限 ({code})",
        "mt.quota": "{provider} 本月的免费额度已用完",
        "mt.no_model": "模型不可用: {model}",
        "mt.unavailable": "翻译不可用",
        "mt.error": "翻译出错: {err}",
        "mt.failed": "⚠ 翻译失败",
        "mt.untranslated": "（未翻译）",
    },
    "en": {
        "app": "Mitschrift Live",
        "who.other": "Them", "who.me": "Me", "who.room": "Room",
        "engine.cuda": "GPU", "engine.npu": "NPU", "engine.dml": "iGPU", "engine.cpu": "CPU",
        "devices.switched": "Switched to the new default device: {list}",
        "loading.npu_first": "Optimizing the model for the NPU (about 1 minute, first time only)…",
        "loading.engine": "Loading the speech model ({label})…",
        "loading.zh": "Loading the Chinese model…",
        "loading.audio": "Opening audio devices…",
        "loading.stopping": "Stopping…",
        "engine.fallback": "{label} not available, using the CPU instead",
        "error.load": "Could not load the models: {e}",
        "error.start": "Could not start: {e}",
        "translation.off": "Translation off",
        "tray.show": "Show window", "tray.start": "Start", "tray.pause": "Pause",
        "tray.overlay": "Caption bar", "tray.lock": "Click-through", "tray.lock_hk": "Click-through ({hk})",
        "tray.src_other": "Listen to system audio (them)", "tray.src_me": "Listen to the microphone (me / room)",
        "tray.target": "Translation", "tray.target_off": "Off", "tray.quit": "Quit Mitschrift Live",
        "tray.running": "capturing", "tray.paused": "paused",
        "tray.hint": "Mitschrift Live keeps running in the tray. Right-click the tray icon to quit.",
        "bar.idle": "Waiting for speech…", "bar.pending": "Translating…", "bar.lock": "Click-through",
        "bar.show.both": "Both", "bar.show.original": "Original", "bar.show.translation": "Translation",
        "mt.no_key": "No {provider} API key: add one in Settings → Translation",
        "mt.bad_key": "{provider} API key invalid or not permitted ({code})",
        "mt.quota": "{provider}: this month's free quota is used up",
        "mt.no_model": "Model not available: {model}",
        "mt.unavailable": "Translation not available",
        "mt.error": "Translation error: {err}",
        "mt.failed": "⚠ translation failed",
        "mt.untranslated": "(not translated)",
    },
}


def system_lang() -> str:
    """'zh' if Windows shows Chinese, else 'en'."""
    try:
        return "zh" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x04 else "en"
    except (AttributeError, OSError):
        return "zh"


def resolve(setting: str) -> str:
    return setting if setting in LANGS else system_lang()


def set_lang(setting: str) -> str:
    global _lang
    _lang = resolve(setting)
    return _lang


def lang() -> str:
    return _lang


def tr(key: str, **kw) -> str:
    text = STRINGS[_lang].get(key) or STRINGS["zh"][key]
    return text.format(**kw) if kw else text
