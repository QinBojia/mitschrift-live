import { Store, connect, PENDING, LABEL, LANG, fmtTime, levelPct, escapeHtml, joinText } from "./client.js";

const store = new Store();
const send = connect(store);
const $ = (id) => document.getElementById(id);
const list = $("list");
const nodes = new Map(); // entry key -> element
let query = "";
let follow = true; // auto-scroll while the user is at the bottom

/* ---------------------------------------------------------------- transcript */

function highlight(text) {
  const safe = escapeHtml(text);
  if (!query) return safe;
  const q = escapeHtml(query).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return safe.replace(new RegExp(q, "gi"), (m) => `<mark>${m}</mark>`);
}

function render(e) {
  let el = nodes.get(e.key);
  if (!el) {
    el = document.createElement("div");
    el.innerHTML = `<div class="meta"><span class="who"></span><span class="lang"></span><span class="t"></span></div>
      <div class="bubble"><div class="orig"></div><div class="trans"></div></div>`;
    // group with the previous bubble when the same person keeps talking (within a minute)
    const prev = [...nodes.values()].at(-1);
    el.dataset.cont = prev && prev.dataset.stream === e.stream && e.start - Number(prev.dataset.start) < 60 ? "1" : "";
    el.dataset.stream = e.stream;
    el.dataset.start = e.start;
    nodes.set(e.key, el);
    list.appendChild(el);
    $("empty").style.display = "none";
  }
  const live = !e.final && e.translation !== PENDING;
  el.className = `entry ${e.stream}` + (el.dataset.cont ? " cont" : "") + (live ? " live" : "")
    + (e.final && !e.translation ? " notr" : "");
  el.querySelector(".t").textContent = fmtTime(e.start);
  const who = el.querySelector(".who");
  who.textContent = LABEL[e.stream] ?? e.stream;
  who.className = `who ${e.stream}`;
  el.querySelector(".lang").textContent = LANG[e.src] ?? "";
  const orig = el.querySelector(".orig");
  const sep = e.committed && e.tentative && joinText(e.committed, e.tentative) !== e.committed + e.tentative ? " " : "";
  orig.innerHTML = highlight(e.committed) + (e.tentative ? `${sep}<span class="tent">${highlight(e.tentative)}</span>` : "");
  orig.classList.toggle("caret", live);
  const tr = el.querySelector(".trans");
  if (e.translation === PENDING) { tr.textContent = "翻译中…"; tr.className = "trans pending"; }
  else { tr.innerHTML = e.translation ? highlight(e.translation) : ""; tr.className = "trans"; }
  tr.style.display = e.translation ? "" : "none";
  applyFilter(el, e);
}

function applyFilter(el, e) {
  const hit = !query || `${e.committed} ${e.tentative} ${e.translation ?? ""}`.toLowerCase().includes(query.toLowerCase());
  el.style.display = hit ? "" : "none";
}

function scrollToEnd() { list.scrollTop = list.scrollHeight; }

store.addEventListener("entry", (ev) => {
  render(store.entries.get(ev.detail));
  if (follow && !query) requestAnimationFrame(scrollToEnd);
  updateCount();
});
store.addEventListener("remove", (ev) => {
  nodes.get(ev.detail)?.remove();
  nodes.delete(ev.detail);
  updateCount();
});

list.addEventListener("scroll", () => {
  follow = list.scrollHeight - list.scrollTop - list.clientHeight < 60;
  $("jump").style.display = follow ? "none" : "block";
});
$("jump").onclick = () => { follow = true; scrollToEnd(); };

function updateCount() {
  const n = [...store.entries.values()].filter((e) => e.final).length;
  $("count").textContent = n ? `${n} 句` : "";
}

$("search").addEventListener("input", (ev) => {
  query = ev.target.value.trim();
  for (const e of store.entries.values()) render(e);
});

/* ---------------------------------------------------------------- status bar */

store.addEventListener("status", renderStatus);
renderStatus(); // the demo / first server message may arrive before this listener existed

function renderStatus() {
  const s = store.status;
  $("dot").classList.toggle("live", !!s.running);
  $("run").textContent = s.running ? "暂停" : "开始";
  $("run").classList.toggle("primary", !s.running);
  $("run").disabled = !s.connected || !!s.loading;
  $("run").title = s.connected ? (s.loading ?? "") : "后台未连接";
  for (const b of $("target").children) b.classList.toggle("on", b.dataset.v === s.target);
  $("dev-other").textContent = s.sources?.other ?? "";
  $("dev-me").textContent = s.sources?.me ?? "";
  const lag = s.lag > 1 ? ` · 积压 ${s.lag}s` : "";
  // recording: armed (records on next start) vs. live (writing now, with a timer)
  const rec = $("rec");
  rec.classList.toggle("armed", !!s.record_on && !s.recording);
  rec.classList.toggle("live", !!s.recording);
  const mm = (n) => `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`;
  rec.textContent = s.recording ? `● 录音中 ${mm(s.record_secs || 0)}` : s.record_on ? "● 录音（开始后录）" : "● 录音";
  rec.disabled = !s.connected;
  $("openRec").hidden = !s.last_recording;
  $("stats").innerHTML = s.loading
    ? `<span>${escapeHtml(s.loading)}</span>`
    : s.error
      ? `<span class="error">${escapeHtml(s.error)}</span>`
      : (s.notice ? `<span class="notice">${escapeHtml(s.notice)}</span>` : "") +
        `<span>${escapeHtml(s.engine)} 识别 <b>${s.asr_ms ?? "-"}</b> ms</span>` +
        `<span>${escapeHtml(s.model)}${s.target === "off" ? "" : ` 翻译 <b>${s.mt_ms ?? "-"}</b> ms`}${lag}</span>`;
  if (s.settings) applySettings(s.settings);
  if (s.edition) applyEdition(s);
}

// a packaged edition (e.g. the friends' build) pins some choices: hide what can't change
function applyEdition(s) {
  const ed = s.edition;
  const engine = $("set-engine");
  if (ed.engines) for (const o of engine.options) o.hidden = !ed.engines.includes(o.value);
  engine.closest(".field").hidden = !!ed.lock_engine;
  const model = $("set-model");
  if (ed.models) {
    for (const o of model.options) o.hidden = !ed.models.includes(o.value);
    model.closest(".field").hidden = ed.models.length <= 1;
  }
  $("about").textContent = `随堂字幕 Mitschrift Live ${s.version ?? ""}${ed.name !== "dev" ? ` · ${ed.name}` : ""} · 数据目录 ${s.data_dir ?? ""}`;
}

// reflect the backend's saved settings in the drawer (skip a control while it has focus)
function applySettings(cfg) {
  const setVal = (id, v) => { const el = $(id); if (el && document.activeElement !== el && v !== undefined) el.value = v; };
  setVal("set-engine", cfg.engine);
  setVal("set-model", cfg.model);
  setVal("set-scene", cfg.scene);
  setVal("set-speaker", cfg.speaker ?? "");
  setVal("set-mic", cfg.mic ?? "");
  for (const k of ["aec", "zh"]) document.querySelector(`.switch[data-k="${k}"]`)?.classList.toggle("on", !!cfg[k]);
}

store.addEventListener("devices", () => {
  const fill = (id, names, label) => {
    const el = $(id);
    el.innerHTML = `<option value="">${label}</option>` +
      names.map((n) => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join("");
  };
  fill("set-speaker", store.devices.speakers ?? [], "Windows 默认输出");
  fill("set-mic", store.devices.mics ?? [], "Windows 默认麦克风");
  if (store.status.settings) applySettings(store.status.settings);
});
store.addEventListener("levels", () => {
  $("lv-other").style.width = levelPct(store.levels.other) + "%";
  $("lv-me").style.width = levelPct(store.levels.me) + "%";
});

$("run").onclick = () => send({ type: "control", action: store.status.running ? "stop" : "start" });
$("rec").onclick = () => send({ type: "set", key: "record", value: !store.status.record_on });
$("openRec").onclick = () => api()?.open_recordings?.(store.status.last_recording);
$("target").onclick = (ev) => ev.target.dataset.v && send({ type: "set", key: "target", value: ev.target.dataset.v });

/* ---------------------------------------------------------------- overlay / settings */

const api = () => window.pywebview?.api; // present when running inside the desktop app
$("overlayBtn").onclick = () => api()?.toggle_overlay?.() ?? window.open("overlay.html" + location.search, "overlay", "width=980,height=200");
$("gear").onclick = () => $("drawer").classList.add("open");
$("closeDrawer").onclick = () => $("drawer").classList.remove("open");
document.addEventListener("keydown", (e) => e.key === "Escape" && $("drawer").classList.remove("open"));
for (const sw of document.querySelectorAll(".switch")) {
  sw.onclick = () => {
    sw.classList.toggle("on");
    const on = sw.classList.contains("on");
    if (sw.dataset.k === "clickThrough") api()?.set_click_through?.(on);  // window feature, not backend
    else send({ type: "set", key: sw.dataset.k, value: on });
  };
}
// desktop shell -> page: keep the click-through switch in sync (global hotkey, caption-bar button)
window.__setLock = (on) => document.querySelector('.switch[data-k="clickThrough"]').classList.toggle("on", on);
for (const [id, key] of [["set-engine", "engine"], ["set-model", "model"], ["set-scene", "scene"],
                         ["set-speaker", "speaker"], ["set-mic", "mic"]]) {
  $(id).onchange = (ev) => send({ type: "set", key, value: ev.target.value });
}
// caption-bar look: drawn by the desktop shell, live while dragging the sliders
$("set-font").oninput = (ev) => api()?.set_bar_pref?.("font", Number(ev.target.value));
$("set-text-alpha").oninput = (ev) => api()?.set_bar_pref?.("text_alpha", Number(ev.target.value) / 100);
$("set-bg-alpha").oninput = (ev) => api()?.set_bar_pref?.("bg_alpha", Number(ev.target.value) / 100);
// desktop shell -> page: the bar's look changed (A−/A+ on the bar itself) or on load
window.__setBarPrefs = (p) => {
  const set = (id, v) => { const el = $(id); if (el && document.activeElement !== el) el.value = v; };
  set("set-font", p.font);
  set("set-text-alpha", Math.round(p.text_alpha * 100));
  set("set-bg-alpha", Math.round(p.bg_alpha * 100));
};
window.__setHotkey = (hk) => {
  $("hk").textContent = ` · ${hk}`;
  $("hk-hint").textContent = `穿透开启后字幕条无法点击，按 ${hk} 或在这里关闭。`;
};
window.addEventListener("pywebviewready", async () => {
  const p = await api()?.get_bar_prefs?.();
  if (p) window.__setBarPrefs(p);
  const hk = await api()?.get_hotkey?.();
  if (hk) window.__setHotkey(hk);
});

/* ---------------------------------------------------------------- export */

function exportAs(fmt) {
  const done = [...store.entries.values()].filter((e) => e.final);
  const pad = (n, w = 2) => String(n).padStart(w, "0");
  const t0 = done[0]?.start ?? 0;
  const srtTime = (s) => { const ms = Math.max(0, Math.round((s - t0) * 1000)); return `${pad(Math.floor(ms / 3600000))}:${pad(Math.floor(ms / 60000) % 60)}:${pad(Math.floor(ms / 1000) % 60)},${pad(ms % 1000, 3)}`; };
  let out;
  if (fmt === "srt") {
    out = done.map((e, i) => `${i + 1}\n${srtTime(e.start)} --> ${srtTime(e.end ?? e.start + 3)}\n${e.committed}${e.translation ? "\n" + e.translation : ""}\n`).join("\n");
  } else if (fmt === "md") {
    out = "# 随堂字幕 Mitschrift Live\n\n" + done.map((e) => `**${fmtTime(e.start)} ${LABEL[e.stream]}** ${e.committed}${e.translation ? `  \n> ${e.translation}` : ""}\n`).join("\n");
  } else {
    out = done.map((e) => `[${fmtTime(e.start)}] ${LABEL[e.stream]}: ${e.committed}${e.translation ? `\n    → ${e.translation}` : ""}`).join("\n");
  }
  if (api()?.save_text) return api().save_text(out, fmt);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([out], { type: "text/plain;charset=utf-8" }));
  a.download = `mitschrift-live-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")}.${fmt}`;
  a.click();
}
for (const b of document.querySelectorAll("[data-export]")) b.onclick = () => exportAs(b.dataset.export);

window.__lcReady = true;
