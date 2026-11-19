// Mitschrift Live UI client: state store fed by the Python core over a local WebSocket,
// or by a scripted demo (?demo) for designing the UI without the backend.
//
// Server -> client messages:
//   {type:"caption", stream, utt, start, committed, tentative, final, src, translation}
//       src: "de" | "en" | "zh";  translation: null (none needed) | "…" (pending) | text
//   {type:"status", connected, running, loading, engine, device, model, target, scene,
//        settings:{...}, sources:{other,me}, asr_ms, mt_ms, error, lag}
//   {type:"levels", other, me}            (dBFS, ~10 Hz)
//   {type:"devices", speakers:[name], mics:[name]}
// The page URL carries ?port=&token= from the desktop shell (livecapture/server.py).
// Client -> server:
//   {type:"control", action:"start"|"stop"}   {type:"set", key, value}
import { t } from "./i18n.js";

export const PENDING = "…";

export class Store extends EventTarget {
  constructor() {
    super();
    this.entries = new Map(); // key -> entry, insertion order = display order
    this.status = { running: false, target: "zh", sources: {}, engine: "", model: "", error: null };
    this.levels = { other: -100, me: -100 };
    this.devices = {};
  }
  key(m) { return `${m.stream}:${m.utt}`; }
  apply(m) {
    if (m.type === "caption") {
      const k = this.key(m);
      const removed = m.final && !m.committed;
      if (removed) {
        if (this.entries.delete(k)) this.emit("remove", k);
        return;
      }
      this.entries.set(k, { ...this.entries.get(k), ...m, key: k });
      this.emit("entry", k);
    } else if (m.type === "status") {
      Object.assign(this.status, m);
      this.emit("status");
    } else if (m.type === "levels") {
      Object.assign(this.levels, m);
      this.emit("levels");
    } else if (m.type === "devices") {
      this.devices = m;
      this.emit("devices");
    }
  }
  emit(type, detail) { this.dispatchEvent(new CustomEvent(type, { detail })); }
}

export function isDemo() { return new URLSearchParams(location.search).has("demo"); }

export function connect(store, onSend) {
  if (isDemo()) return runDemo(store);
  const params = new URLSearchParams(location.search);
  const port = params.get("port") || "8765";
  const token = encodeURIComponent(params.get("token") || "");
  let ws;
  const open = () => {
    ws = new WebSocket(`ws://127.0.0.1:${port}/ws?token=${token}`);
    ws.onopen = () => store.apply({ type: "status", connected: true, error: null });
    ws.onmessage = (e) => store.apply(JSON.parse(e.data));
    ws.onclose = () => {
      store.apply({ type: "status", connected: false, running: false,
        error: t("conn.lost") });
      setTimeout(open, 1500);
    };
  };
  store.apply({ type: "status", connected: false, error: t("conn.connecting") });
  open();
  return (msg) => ws && ws.readyState === 1 && ws.send(JSON.stringify(msg));
}

/* ------------------------------------------------------------------ demo */

const SCRIPT = [
  { stream: "other", src: "de", text: "Guten Morgen zusammen, danke, dass ihr euch heute Zeit genommen habt.",
    zh: "大家早上好，感谢你们今天抽出时间。", en: "Good morning everyone, thank you for taking the time today." },
  { stream: "other", src: "de", text: "Wir haben die zwei Texte gelesen, jetzt gucken wir mal in das Kursbuch.",
    zh: "我们已经读了那两篇课文，现在我们来看一下课本。", en: "We've read the two texts, now let's take a look at the course book." },
  { stream: "other", src: "zh", text: "下面请大家打开课本第52页，做一下第二题。",
    zh: null, en: "Next, everyone please open your textbook to page 52 and do question 2.",
    de: "Schlagt jetzt bitte alle das Lehrbuch auf Seite 52 auf und macht Aufgabe 2." },
  { stream: "me", src: "en", text: "Sorry, I was on mute. Could you repeat the page number?",
    zh: "抱歉，我刚才开着静音。你能再说一下页码吗？", en: null,
    de: "Entschuldigung, ich war stummgeschaltet. Können Sie die Seitenzahl wiederholen?" },
  { stream: "other", src: "de", text: "Seite zweiundfünfzig. Achtung, aufmachen ist ein trennbares Verb.",
    zh: "第五十二页。注意，aufmachen 是一个可分动词。", en: "Page fifty-two. Note: aufmachen is a separable verb." },
  { stream: "other", src: "de", text: "Sehr gut, aber denken Sie bitte an den Akkusativ.",
    zh: "很好，但请注意第四格。", en: "Very good, but please remember the accusative." },
  { stream: "me", src: "de", text: "Ich mache das Fenster auf, weil es hier sehr warm ist.",
    zh: "我把窗户打开，因为这里很热。", en: "I'm opening the window because it's very warm in here." },
  { stream: "other", src: "de", text: "Genau, und im Nebensatz steht das Verb am Ende: weil es hier warm ist.",
    zh: "没错，在从句里动词放在最后：weil es hier warm ist。", en: "Exactly, and in the subordinate clause the verb goes at the end: weil es hier warm ist." },
];

function runDemo(store) {
  let utt = 0;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  store.apply({
    type: "status", connected: true, running: true, engine: "NPU", device: "Intel AI Boost (fp16)", model: "deepseek-flash",
    target: "zh", scene: "a German language class", asr_ms: 152, mt_ms: 731, error: null,
    sources: { other: "Speakers (Realtek Audio)", me: "Microphone (USB Audio)" },
  });
  let speaking = null;
  setInterval(() => {
    const lv = (s) => (speaking === s ? -30 + Math.random() * 18 : -62 + Math.random() * 6);
    store.apply({ type: "levels", other: lv("other"), me: lv("me") });
  }, 90);

  const whileStopped = async () => { while (!store.status.running) { speaking = null; await sleep(200); } };
  (async () => {
    await sleep(600);
    for (let round = 0; ; round++) {
      for (const line of SCRIPT) {
        await whileStopped();
        const id = ++utt, start = Date.now() / 1000;
        const target = store.status.target;
        const isCjk = line.src === "zh";
        const tokens = isCjk ? [...line.text] : line.text.split(" ");
        const joiner = isCjk ? "" : " ";
        speaking = line.stream;
        const step = isCjk ? 4 : 2;
        for (let i = step; i < tokens.length; i += step) {
          await whileStopped();
          speaking = line.stream;
          store.apply({ type: "caption", stream: line.stream, utt: id, start, committed: "",
            tentative: tokens.slice(0, i).join(joiner), final: false, src: line.src, translation: null });
          await sleep(isCjk ? 300 : 420);
        }
        speaking = null;
        const tr = line[target] ?? null;
        store.apply({ type: "caption", stream: line.stream, utt: id, start, committed: line.text, tentative: "",
          final: tr === null, src: line.src, translation: tr === null ? null : PENDING });
        if (tr !== null) {
          await sleep(650 + Math.random() * 250);
          store.apply({ type: "caption", stream: line.stream, utt: id, start, committed: line.text,
            tentative: "", final: true, src: line.src, translation: line[store.status.target] ?? null });
        }
        await sleep(900);
      }
    }
  })();
  return (msg) => { // demo "backend": honour target switches
    if (msg.type === "set") store.apply({ type: "status", [msg.key]: msg.value });
    if (msg.type === "control") store.apply({ type: "status", running: msg.action === "start" });
  };
}

/* ------------------------------------------------------------------ helpers */

export const LABEL = { other: "对方", me: "我" };
export const LANG = { de: "DE", en: "EN", zh: "中" };

export function fmtTime(epoch) {
  const d = new Date(epoch * 1000);
  return d.toTimeString().slice(0, 8);
}

export function levelPct(db) { return Math.max(0, Math.min(100, ((db + 60) / 60) * 100)); }

export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

export function joinText(a, b) {
  if (!a || !b) return a || b || "";
  const cjk = /[㐀-鿿　-〿＀-￯]/;
  return a + (cjk.test(a.at(-1)) || cjk.test(b[0]) ? "" : " ") + b;
}
