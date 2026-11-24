// Interface strings for the page (Chinese / English); the Python side has livecapture/i18n.py.
// Elements carry data-i18n (text), data-i18n-html (trusted markup from this table),
// data-i18n-title and data-i18n-ph (placeholder); applyI18n() fills them in.

const STRINGS = {
  zh: {
    "app": "随堂字幕",
    "who.other": "对方", "who.me": "我", "who.room": "现场",
    "src.off": "已关闭", "src.title": "点击开启或关闭这一路。关闭后不识别、不显示、不录音。",
    "src.none": "两路都已关闭，现在没有在听任何声音。",
    "lang.zh": "中", "lang.en": "EN", "lang.de": "DE",
    "run.start": "开始", "run.pause": "暂停", "run.offline": "后台未连接",
    "rec": "● 录音", "rec.armed": "● 录音（开始后录）", "rec.live": "● 录音中 {t}", "rec.paused": "● 录音（已暂停）{t}",
    "rec.title": "录音：保存双方的音频和字幕（含译文）到设置里选的文件夹（默认 文档\\Mitschrift Live）。开始录音之前的字幕也会存进去；暂停不会把录音分成两段，再点一次才结束。",
    "sec.rec": "录音", "rec.dir": "保存位置", "rec.dir.pick": "选择…", "rec.dir.reset": "恢复默认",
    "rec.dir.hint": "每次录音在这里建一个以开始时间命名的文件夹。改了以后从下一次录音开始生效，已有的录音不会移动。",
    "target": "译文", "target.title": "把对方和你说的话翻译成哪种语言；“关”= 只显示字幕", "target.off": "关",
    "overlay": "悬浮字幕", "overlay.title": "显示/隐藏悬浮字幕", "settings": "设置",
    "empty": "还没有字幕。<br>点“开始”后，对方说的话和你自己说的话会出现在这里。",
    "jump": "↓ 回到最新", "search": "搜索原文或译文…", "export.txt": "导出 TXT",
    "openRec": "打开录音文件夹", "openRec.title": "打开保存录音的文件夹",
    "clear": "清空", "clear.title": "清空这里和悬浮字幕条里的字幕。已经录下的录音不受影响。",
    "clear.confirm": "清空这 {n} 句字幕？\n\n这里和悬浮字幕条都会清空。已经录下的录音不受影响；之后开始的录音只带清空之后的字幕。",
    "count": "{n} 句", "pending": "翻译中…",
    "stats.asr": "{engine} 识别 <b>{ms}</b> ms", "stats.mt": " 翻译 <b>{ms}</b> ms", "stats.lag": " · 积压 {s}s",
    "sec.ui": "界面", "ui.lang": "界面语言", "ui.auto": "自动（跟随 Windows）",
    "sec.audio": "音频", "dev.other": "对方（系统声音或某个应用）", "dev.me": "我（麦克风）",
    "dev.default_out": "Windows 默认输出", "dev.outputs": "输出设备的全部声音", "dev.apps": "只录这个应用的声音",
    "dev.app_off": "{name}（未运行）", "dev.default_in": "Windows 默认麦克风", "aec": "回声消除（外放时打开）",
    "sec.asr": "识别", "engine": "引擎", "engine.cuda": "NVIDIA GPU（最快）", "engine.npu": "Intel NPU（省电）",
    "engine.dml": "核显 DirectML（AMD / Intel）", "engine.cpu": "CPU", "zh": "识别中文",
    "sec.mt": "翻译", "model": "模型", "model.fast": "（最快）", "model.best": "（质量最好，较慢）",
    "scene": "场景", "scene.hint": "场景会告诉翻译模型这是什么场合，影响用词和语气。",
    "key.deepseek": "DeepSeek API key", "key.anthropic": "Claude (Anthropic) API key",
    "key.azure": "Microsoft Translator key（Azure 翻译资源）", "key.ph.azure": "粘贴 Azure 门户里“密钥和终结点”的密钥",
    "key.region": "区域（Location/Region）", "key.region.ph": "例如 eastasia；资源区域是“全局”就留空",
    "model.azure": "Microsoft Translator（机器翻译，最快，不看上下文）",
    "key.ph": "粘贴 key，例如 sk-…", "key.save": "保存", "key.clear": "删除",
    "key.env": "正在使用环境变量里的 key（…{hint}）", "key.saved": "已保存（…{hint}），只在这台电脑上为当前用户加密保存",
    "key.bundled": "正在使用安装包内置的 key", "key.user_env": "正在使用 Windows 用户环境变量里的 key（…{hint}）",
    "key.none": "还没有 key。没有 key 时只显示字幕，不翻译。",
    "sec.bar": "悬浮字幕", "bar.font": "字号", "bar.text": "文字不透明度", "bar.bg": "背景不透明度",
    "bar.unsure": "未确定文字的亮度",
    "bar.unsure.hint": "说话过程中句尾还可能改的词、选“仅译文”时等待译文的原文、“翻译中…”。拉到最右，就和确定的文字一样。",
    "bar.show": "显示内容", "bar.show.both": "原文 + 译文", "bar.show.original": "仅原文", "bar.show.translation": "仅译文",
    "bar.show.hint": "选“仅译文”时，译文出来之前先用较淡的颜色显示原文；没有译文的句子显示原文。",
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
    "who.other": "Them", "who.me": "Me", "who.room": "Room",
    "src.off": "off", "src.title": "Click to turn this side on or off. When off it is not recognized, shown or recorded.",
    "src.none": "Both sides are off: nothing is being listened to.",
    "lang.zh": "ZH", "lang.en": "EN", "lang.de": "DE",
    "run.start": "Start", "run.pause": "Pause", "run.offline": "Not connected to the backend",
    "rec": "● Record", "rec.armed": "● Record (starts with capture)", "rec.live": "● Recording {t}", "rec.paused": "● Recording (paused) {t}",
    "rec.title": "Record: save both sides' audio and the captions (with translations) to the folder chosen in the settings (default Documents\\Mitschrift Live). The captions from before you press it are saved too; pausing doesn't split the recording, pressing it again ends it.",
    "sec.rec": "Recording", "rec.dir": "Save to", "rec.dir.pick": "Choose…", "rec.dir.reset": "Default",
    "rec.dir.hint": "Each recording gets a folder named after its start time in here. A change applies from the next recording on; recordings already made stay where they are.",
    "target": "Translate to", "target.title": "Language to translate both sides into; “Off” = captions only", "target.off": "Off",
    "overlay": "Caption bar", "overlay.title": "Show / hide the floating caption bar", "settings": "Settings",
    "empty": "No captions yet.<br>Press “Start”: what the other side says and what you say will appear here.",
    "jump": "↓ Back to latest", "search": "Search captions and translations…", "export.txt": "Export TXT",
    "openRec": "Open recordings folder", "openRec.title": "Open the folder with the recordings",
    "clear": "Clear", "clear.title": "Clear the captions here and in the caption bar. Recordings already made keep them.",
    "clear.confirm": "Clear these {n} sentences?\n\nThis window and the caption bar are cleared. Recordings already made keep them; a recording started later begins after this point.",
    "count": "{n} sentences", "pending": "Translating…",
    "stats.asr": "{engine} speech <b>{ms}</b> ms", "stats.mt": " translation <b>{ms}</b> ms", "stats.lag": " · behind {s}s",
    "sec.ui": "Interface", "ui.lang": "Language", "ui.auto": "Automatic (follow Windows)",
    "sec.audio": "Audio", "dev.other": "Them (system audio or one app)", "dev.me": "Me (microphone)",
    "dev.default_out": "Windows default output", "dev.outputs": "Everything on an output device", "dev.apps": "Only this app's sound",
    "dev.app_off": "{name} (not running)", "dev.default_in": "Windows default microphone",
    "aec": "Echo cancellation (turn on when using speakers)",
    "sec.asr": "Speech recognition", "engine": "Engine", "engine.cuda": "NVIDIA GPU (fastest)",
    "engine.npu": "Intel NPU (power-saving)", "engine.dml": "Integrated GPU, DirectML (AMD / Intel)", "engine.cpu": "CPU",
    "zh": "Recognize Chinese",
    "sec.mt": "Translation", "model": "Model", "model.fast": " (fastest)", "model.best": " (best quality, slower)",
    "scene": "Scene", "scene.hint": "Tells the translation model what kind of situation this is, which shapes wording and tone.",
    "key.deepseek": "DeepSeek API key", "key.anthropic": "Claude (Anthropic) API key",
    "key.azure": "Microsoft Translator key (Azure Translator resource)", "key.ph.azure": "Paste a key from “Keys and Endpoint” in the Azure portal",
    "key.region": "Region (Location/Region)", "key.region.ph": "e.g. eastasia; leave empty for a global resource",
    "model.azure": "Microsoft Translator (machine translation, fastest, no context)",
    "key.ph": "Paste the key, e.g. sk-…", "key.save": "Save", "key.clear": "Remove",
    "key.env": "Using the key from an environment variable (…{hint})",
    "key.saved": "Saved (…{hint}), encrypted for your Windows user on this PC only",
    "key.bundled": "Using the key bundled with this installer",
    "key.user_env": "Using the key from your Windows user environment (…{hint})",
    "key.none": "No key yet. Without one you get captions but no translation.",
    "sec.bar": "Caption bar", "bar.font": "Font size", "bar.text": "Text opacity", "bar.bg": "Background opacity",
    "bar.unsure": "Brightness of unsettled text",
    "bar.unsure.hint": "The last words of a sentence being spoken (they can still change), the original standing in for its translation with “Translation only”, and “Translating…”. All the way right: like settled text.",
    "bar.show": "Show", "bar.show.both": "Original + translation", "bar.show.original": "Original only",
    "bar.show.translation": "Translation only",
    "bar.show.hint": "With “Translation only”, the original is shown dimmed until its translation arrives; sentences without a translation show the original.",
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
