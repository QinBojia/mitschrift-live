import { Store, connect, isDemo, PENDING, LABEL, escapeHtml, joinText } from "./client.js";

const store = new Store();
connect(store);
const $ = (id) => document.getElementById(id);
const SHOW = 2; // newest entries on screen
const api = () => window.pywebview?.api;

const inApp = new URLSearchParams(location.search).has("app"); // desktop window: the window is the panel
if (inApp) document.body.classList.add("app");
if (isDemo() && !inApp) {  // browser preview: draw something that looks like a Zoom call behind it
  document.body.classList.add("demo");
  document.body.insertAdjacentHTML("afterbegin",
    '<div class="zoom"><div><span>Frau Schneider</span></div><div><span>Kursteilnehmer</span></div></div>');
}

function sep(a, b) { return a && b && joinText(a, b) !== a + b ? " " : ""; }

function lineHtml(e, old) {
  const live = !e.final && e.translation !== PENDING;
  const tent = e.tentative ? `${sep(e.committed, e.tentative)}<span class="tent">${escapeHtml(e.tentative)}</span>` : "";
  let tr = "";
  if (e.translation === PENDING) tr = '<div class="tr pending">翻译中…</div>';
  else if (e.translation) tr = `<div class="tr">${escapeHtml(e.translation)}</div>`;
  return `<div class="line${old ? " old" : ""}"><span class="who ${e.stream}">${LABEL[e.stream] ?? ""}</span>`
    + `<div class="orig${live ? " caret" : ""}">${escapeHtml(e.committed)}${tent}</div>${tr}</div>`;
}

function render() {
  const entries = [...store.entries.values()].slice(-SHOW);
  $("lines").innerHTML = entries.length
    ? entries.map((e, i) => lineHtml(e, i < entries.length - 1)).join("")
    : '<div class="idle">等待说话…</div>';
}
store.addEventListener("entry", render);
store.addEventListener("remove", render);

/* ---------------------------------------------------------------- toolbar */

let fs = Number(storageGet("lc.fs") ?? 26);
const setFs = (v) => { fs = Math.max(16, Math.min(48, v)); document.documentElement.style.setProperty("--fs", fs + "px"); storageSet("lc.fs", fs); };
setFs(fs);
$("smaller").onclick = () => setFs(fs - 2);
$("bigger").onclick = () => setFs(fs + 2);
$("trBtn").onclick = (ev) => { ev.target.classList.toggle("on"); document.body.classList.toggle("no-tr", !ev.target.classList.contains("on")); };
$("lock").onclick = (ev) => { ev.target.classList.toggle("on"); api()?.set_click_through?.(ev.target.classList.contains("on")); };
$("close").onclick = () => (api()?.toggle_overlay ? api().toggle_overlay() : window.close());
// called by the desktop shell when click-through changes (e.g. via the global hotkey)
window.__setLock = (on) => { $("lock").classList.toggle("on", on); document.body.classList.toggle("locked", on); };
// called by the main window's font-size slider
window.__setFs = (v) => setFs(Number(v));
// the global hotkey the desktop shell managed to register
window.__setHotkey = (hk) => { $("lock").title = `鼠标穿透：点击会落到下面的窗口（${hk} 切换）`; };
window.addEventListener("pywebviewready", async () => { const hk = await api()?.get_hotkey?.(); if (hk) window.__setHotkey(hk); });

window.__lcReady = true;

function storageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function storageSet(k, v) { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } }
