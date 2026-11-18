// Interface strings for the page (Chinese / English); the Python side has livecapture/i18n.py.
// Elements carry data-i18n (text), data-i18n-html (trusted markup from this table),
// data-i18n-title and data-i18n-ph (placeholder); applyI18n() fills them in.

const STRINGS = {
  zh: {
    "app": "随堂字幕",
    "who.other": "对方", "who.me": "我",
    "lang.zh": "中", "lang.en": "EN", "lang.de": "DE",
    "run.start": "开始", "run.pause": "暂停", "run.offline": "后台未连接",
    "rec": "● 录音", "rec.armed": "● 录音（开始后录）", "rec.live": "● 录音中 {t}",
    "rec.title": "录音：保存双方的音频和字幕（含译文）到 文档\\Mitschrift Live",
    "target": "译文", "target.title": "把对方和你说的话翻译成哪种语言；“关”= 只显示字幕", "target.off": "关",
    "overlay": "悬浮字幕", "overlay.title": "显示/隐藏悬浮字幕", "settings": "设置",
    "empty": "还没有字幕。<br>点“开始”后，对方说的话和你自己说的话会出现在这里。",
    "jump": "↓ 回到最新", "search": "搜索原文或译文…", "export.txt": "导出 TXT",
    "openRec": "打开录音文件夹", "openRec.title": "打开保存录音的文件夹",
    "count": "{n} 句", "pending": "翻译中…",
    "stats.asr": "{engine} 识别 <b>{ms}</b> ms", "stats.mt": " 翻译 <b>{ms}</b> ms", "stats.lag": " · 积压 {s}s",
    "sec.ui": "界面", "ui.lang": "界面语言", "ui.auto": "自动（跟随 Windows）",
    "sec.audio": "音频", "dev.other": "对方（系统声音）", "dev.me": "我（麦克风）",
    "dev.default_out": "Windows 默认输出", "dev.default_in": "Windows 默认麦克风", "aec": "回声消除（外放时打开）",
    "sec.asr": "识别", "engine": "引擎", "engine.cuda": "NVIDIA GPU（最快）", "engine.npu": "Intel NPU（省电）",
    "engine.dml": "核显 DirectML（AMD / Intel）", "engine.cpu": "CPU", "zh": "识别中文",
    "sec.mt": "翻译", "model": "模型", "model.fast": "（最快）", "model.best": "（质量最好，较慢）",
    "scene": "场景", "scene.hint": "场景会告诉翻译模型这是什么场合，影响用词和语气。",
    "key.deepseek": "DeepSeek API key", "key.anthropic": "Claude (Anthropic) API key",
    "key.ph": "粘贴 key，例如 sk-…", "key.save": "保存", "key.clear": "删除",
    "key.env": "正在使用环境变量里的 key（…{hint}）", "key.saved": "已保存（…{hint}），只在这台电脑上为当前用户加密保存",
    "key.bundled": "正在使用安装包内置的 key", "key.user_env": "正在使用 Windows 用户环境变量里的 key（…{hint}）",
    "key.none": "还没有 key。没有 key 时只显示字幕，不翻译。",
    "sec.bar": "悬浮字幕", "bar.font": "字号", "bar.text": "文字不透明度", "bar.bg": "背景不透明度",
    "bar.hint": "背景调低后能透过字幕条看到 Zoom；鼠标移到字幕条上时会暂时显示背景，方便拖动。",
    "lock": "鼠标穿透（点到下面的 Zoom）",
    "lock.hint": "穿透开启后字幕条无法点击，可用全局快捷键或这里关闭。",
    "lock.hint_hk": "穿透开启后字幕条无法点击，按 {hk} 或在这里关闭。",
    "sec.about": "关于", "about.data": "数据目录",
    "credits": "语音识别：NVIDIA Parakeet TDT 0.6B v3（CC BY 4.0）· 中文识别：FunAudioLLM SenseVoice Small（FunASR 模型协议）· 语音检测：Silero VAD（MIT）· 回声消除：WebRTC 音频处理（经 LiveKit）。其余开源组件的许可证见安装目录下的 licenses 文件夹。",
    "conn.connecting": "正在连接随堂字幕后台…", "conn.lost": "未连接到随堂字幕后台（请用 ui.bat 启动），重连中…",
    "boot": "界面脚本没有加载。直接双击打开 HTML 文件时浏览器会拦截脚本。<br>请用 <b>ui.bat</b> 启动桌面版，或打开 <b>http://localhost:17860/main.html?demo</b> 看演示。",
  },
  en: {
    "app": "Mitschrift Live",
    "who.other": "Them", "who.me": "Me",
    "lang.zh": "ZH", "lang.en": "EN", "lang.de": "DE",
    "run.start": "Start", "run.pause": "Pause", "run.offline": "Not connected to the backend",
    "rec": "● Record", "rec.armed": "● Record (starts with capture)", "rec.live": "● Recording {t}",
    "rec.title": "Record: save both sides' audio and the captions (with translations) to Documents\\Mitschrift Live",
    "target": "Translate to", "target.title": "Language to translate both sides into; “Off” = captions only", "target.off": "Off",
    "overlay": "Caption bar", "overlay.title": "Show / hide the floating caption bar", "settings": "Settings",
    "empty": "No captions yet.<br>Press “Start”: what the other side says and what you say will appear here.",
    "jump": "↓ Back to latest", "search": "Search captions and translations…", "export.txt": "Export TXT",
    "openRec": "Open recordings folder", "openRec.title": "Open the folder with the recordings",
    "count": "{n} sentences", "pending": "Translating…",
    "stats.asr": "{engine} speech <b>{ms}</b> ms", "stats.mt": " translation <b>{ms}</b> ms", "stats.lag": " · behind {s}s",
    "sec.ui": "Interface", "ui.lang": "Language", "ui.auto": "Automatic (follow Windows)",
    "sec.audio": "Audio", "dev.other": "Them (system audio)", "dev.me": "Me (microphone)",
    "dev.default_out": "Windows default output", "dev.default_in": "Windows default microphone",
    "aec": "Echo cancellation (turn on when using speakers)",
    "sec.asr": "Speech recognition", "engine": "Engine", "engine.cuda": "NVIDIA GPU (fastest)",
    "engine.npu": "Intel NPU (power-saving)", "engine.dml": "Integrated GPU, DirectML (AMD / Intel)", "engine.cpu": "CPU",
    "zh": "Recognize Chinese",
    "sec.mt": "Translation", "model": "Model", "model.fast": " (fastest)", "model.best": " (best quality, slower)",
    "scene": "Scene", "scene.hint": "Tells the translation model what kind of situation this is, which shapes wording and tone.",
    "key.deepseek": "DeepSeek API key", "key.anthropic": "Claude (Anthropic) API key",
    "key.ph": "Paste the key, e.g. sk-…", "key.save": "Save", "key.clear": "Remove",
    "key.env": "Using the key from an environment variable (…{hint})",
    "key.saved": "Saved (…{hint}), encrypted for your Windows user on this PC only",
    "key.bundled": "Using the key bundled with this installer",
    "key.user_env": "Using the key from your Windows user environment (…{hint})",
    "key.none": "No key yet. Without one you get captions but no translation.",
    "sec.bar": "Caption bar", "bar.font": "Font size", "bar.text": "Text opacity", "bar.bg": "Background opacity",
    "bar.hint": "With a lower background the call shows through; hovering the bar shows the background again, to drag it.",
    "lock": "Click-through (clicks go to the window below)",
    "lock.hint": "While click-through is on the bar can't be clicked; turn it off with the global hotkey or here.",
    "lock.hint_hk": "While click-through is on the bar can't be clicked; press {hk} or turn it off here.",
    "sec.about": "About", "about.data": "data folder",
    "credits": "Speech: NVIDIA Parakeet TDT 0.6B v3 (CC BY 4.0) · Chinese: FunAudioLLM SenseVoice Small (FunASR model license) · Voice activity: Silero VAD (MIT) · Echo cancellation: WebRTC audio processing (via LiveKit). Licenses of the other open-source parts are in the licenses folder of the installation.",
    "conn.connecting": "Connecting to the backend…", "conn.lost": "Not connected to the backend (start it with ui.bat), retrying…",
    "boot": "The page scripts did not load: browsers block them for HTML files opened directly.<br>Start the desktop app with <b>ui.bat</b>, or open <b>http://localhost:17860/main.html?demo</b> for a demo.",
  },
};

let lang = (navigator.language || "").toLowerCase().startsWith("zh") ? "zh" : "en";

export function t(key, vars) {
  let s = STRINGS[lang][key] ?? STRINGS.zh[key] ?? key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, v);
  return s;
}

export const getLang = () => lang;

export function applyI18n(root = document) {
  for (const el of root.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
  for (const el of root.querySelectorAll("[data-i18n-html]")) el.innerHTML = t(el.dataset.i18nHtml);
  for (const el of root.querySelectorAll("[data-i18n-title]")) el.title = t(el.dataset.i18nTitle);
  for (const el of root.querySelectorAll("[data-i18n-ph]")) el.placeholder = t(el.dataset.i18nPh);
  document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
}

/** Switch language (from the backend's status); returns true if it changed. */
export function setLang(l) {
  if (!STRINGS[l] || l === lang) return false;
  lang = l;
  applyI18n();
  return true;
}

export const KEYS = { zh: Object.keys(STRINGS.zh), en: Object.keys(STRINGS.en) };  // for a completeness check
