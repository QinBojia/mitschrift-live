import { Store, connect, PENDING, fmtTime, levelPct, escapeHtml, joinText } from "./client.js";
import { t, setLang, applyI18n } from "./i18n.js";

applyI18n();  // first guess: the WebView's language; the backend's setting follows in the first status

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

// the microphone alone listens to the room (a lecture): shown like the other side, not as "me"
const sideOf = (e) => (e.who === "room" ? "other" : e.stream);
const whoOf = (e) => t(e.who === "room" ? "who.room" : `who.${e.stream}`);

function render(e) {
  let el = nodes.get(e.key);
  if (!el) {
    el = document.createElement("div");
    el.innerHTML = `<div class="meta"><span class="who"></span><span class="lang"></span><span class="t"></span></div>
      <div class="bubble"><div class="orig"></div><div class="trans"></div></div>`;
    // group with the previous bubble when the same person keeps talking (within a minute)
    const prev = [...nodes.values()].at(-1);
    el.dataset.cont = prev && prev.dataset.stream === sideOf(e) && e.start - Number(prev.dataset.start) < 60 ? "1" : "";
    el.dataset.stream = sideOf(e);
    el.dataset.start = e.start;
    nodes.set(e.key, el);
    list.appendChild(el);
    $("empty").style.display = "none";
  }
  const live = !e.final && e.translation !== PENDING;
  el.className = `entry ${sideOf(e)}` + (el.dataset.cont ? " cont" : "") + (live ? " live" : "")
    + (e.final && !e.translation ? " notr" : "");
  el.querySelector(".t").textContent = fmtTime(e.start);
  const who = el.querySelector(".who");
  who.textContent = whoOf(e);
  who.className = `who ${sideOf(e)}`;
  el.querySelector(".lang").textContent = e.src ? t(`lang.${e.src}`) : "";
  const orig = el.querySelector(".orig");
  const sep = e.committed && e.tentative && joinText(e.committed, e.tentative) !== e.committed + e.tentative ? " " : "";
  orig.innerHTML = highlight(e.committed) + (e.tentative ? `${sep}<span class="tent">${highlight(e.tentative)}</span>` : "");
  orig.classList.toggle("caret", live);
  const tr = el.querySelector(".trans");
  if (e.translation === PENDING) { tr.textContent = t("pending"); tr.className = "trans pending"; }
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
store.addEventListener("clear", () => {
  for (const el of nodes.values()) el.remove();
  nodes.clear();
  $("empty").style.display = "";
  follow = true;
  $("jump").style.display = "none";
  updateCount();
});
// clear the captions here, in the caption bar and in what a later recording starts with
$("clear").onclick = () => {
  const n = [...store.entries.values()].filter((e) => e.final).length;
  if (!store.entries.size || confirm(t("clear.confirm", { n }))) send({ type: "clear" });
};

list.addEventListener("scroll", () => {
  follow = list.scrollHeight - list.scrollTop - list.clientHeight < 60;
  $("jump").style.display = follow ? "none" : "block";
});
$("jump").onclick = () => { follow = true; scrollToEnd(); };

function updateCount() {
  const n = [...store.entries.values()].filter((e) => e.final).length;
  $("count").textContent = n ? t("count", { n }) : "";
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
  if (s.ui_lang && setLang(s.ui_lang)) onLanguageChange();
  $("dot").classList.toggle("live", !!s.running);
  $("run").textContent = s.running ? t("run.pause") : t("run.start");
  $("run").classList.toggle("primary", !s.running);
  $("run").disabled = !s.connected || !!s.loading;
  $("run").title = s.connected ? (s.loading ?? "") : t("run.offline");
  for (const b of $("target").children) b.classList.toggle("on", b.dataset.v === s.target);
  const on = s.settings?.sources ?? ["other", "me"];  // the sides that are switched on
  for (const k of ["other", "me"]) {
    $(`src-${k}`).classList.toggle("off", !on.includes(k));
    $(`dev-${k}`).textContent = on.includes(k) ? (s.sources?.[k] ?? "") : t("src.off");
  }
  $("who-me").textContent = t(on.includes("me") && !on.includes("other") ? "who.room" : "who.me");
  const lag = s.lag > 1 ? t("stats.lag", { s: s.lag }) : "";
  // recording: armed (records on next start) vs. live (writing now, with a timer)
  const rec = $("rec");
  rec.classList.toggle("armed", !!s.record_on && !s.recording);
  rec.classList.toggle("live", !!s.recording);
  rec.classList.toggle("paused", !!s.recording && !s.running);  // the recording goes on (silence) while paused
  const mm = (n) => `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`;
  rec.textContent = s.recording ? t(s.running ? "rec.live" : "rec.paused", { t: mm(s.record_secs || 0) })
    : s.record_on ? t("rec.armed") : t("rec");
  rec.disabled = !s.connected;
  $("openRec").hidden = !s.last_recording;
  $("stats").innerHTML = s.loading
    ? `<span>${escapeHtml(s.loading)}</span>`
    : s.error
      ? `<span class="error">${escapeHtml(s.error)}</span>`
      : (on.length ? "" : `<span class="notice">${t("src.none")}</span>`) +
        (s.notice ? `<span class="notice">${escapeHtml(s.notice)}</span>` : "") +
        `<span>${t("stats.asr", { engine: escapeHtml(s.engine), ms: s.asr_ms ?? "-" })}</span>` +
        `<span>${escapeHtml(s.model)}${s.target === "off" ? "" : t("stats.mt", { ms: s.mt_ms ?? "-" })}${lag}</span>`;
  if (s.settings) applySettings(s.settings);
  if (s.edition) applyEdition(s);
  if (s.keys) renderKeys(s.keys);
}

// texts filled in by code (not data-i18n) after the language changed
function onLanguageChange() {
  for (const e of store.entries.values()) render(e);
  updateCount();
  fillDevices();
  showHotkey();
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
    // a build that only translates with DeepSeek has no use for a Claude key
    document.querySelector('.keybox[data-provider="anthropic"]').hidden = !ed.models.some((m) => m.startsWith("claude"));
    document.querySelector('.keybox[data-provider="azure"]').hidden = !ed.models.includes("azure-translator");
  }
  $("about").textContent = `随堂字幕 Mitschrift Live ${s.version ?? ""}${ed.name !== "dev" ? ` · ${ed.name}` : ""} · ${t("about.data")} ${s.data_dir ?? ""}`;
}

// reflect the backend's saved settings in the drawer (skip a control while it has focus)
function applySettings(cfg) {
  const setVal = (id, v) => { const el = $(id); if (el && document.activeElement !== el && v !== undefined) el.value = v; };
  setVal("set-engine", cfg.engine);
  setVal("set-model", cfg.model);
  setVal("set-scene", cfg.scene);
  setVal("set-azure_region", cfg.azure_region ?? "");
  setVal("set-speaker", cfg.speaker ?? "");
  setVal("set-mic", cfg.mic ?? "");
  setVal("set-ui_lang", cfg.ui_lang ?? "auto");
  for (const k of ["aec", "zh"]) document.querySelector(`.switch[data-k="${k}"]`)?.classList.toggle("on", !!cfg[k]);
}

function fillDevices() {
  const fill = (id, names, label) => {
    const el = $(id);
    el.innerHTML = `<option value="">${escapeHtml(label)}</option>` +
      names.map((n) => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join("");
  };
  fill("set-speaker", store.devices.speakers ?? [], t("dev.default_out"));
  fill("set-mic", store.devices.mics ?? [], t("dev.default_in"));
  if (store.status.settings) applySettings(store.status.settings);
}
store.addEventListener("devices", fillDevices);
fillDevices();

/* ---------------------------------------------------------------- API keys */

// where each provider's key comes from (the backend never sends the key itself)
function renderKeys(keys) {
  for (const box of document.querySelectorAll(".keybox")) {
    const k = keys[box.dataset.provider] ?? {};
    const status = box.querySelector(".keystatus");
    status.textContent = k.source ? t(`key.${k.source}`, { hint: k.hint }) : t("key.none");
    status.classList.toggle("none", !k.source);
    box.querySelector(".keyclear").hidden = k.source !== "saved";
  }
}
for (const box of document.querySelectorAll(".keybox")) {
  const input = box.querySelector("input");
  const save = (key) => send({ type: "set", key: "api_key", value: { provider: box.dataset.provider, key } });
  box.querySelector(".keysave").onclick = () => { if (input.value.trim()) { save(input.value.trim()); input.value = ""; } };
  input.onkeydown = (ev) => ev.key === "Enter" && box.querySelector(".keysave").click();
  box.querySelector(".keyclear").onclick = () => save("");
}
store.addEventListener("levels", () => {
  $("lv-other").style.width = levelPct(store.levels.other) + "%";
  $("lv-me").style.width = levelPct(store.levels.me) + "%";
});

$("run").onclick = () => send({ type: "control", action: store.status.running ? "stop" : "start" });
$("rec").onclick = () => send({ type: "set", key: "record", value: !store.status.record_on });
$("openRec").onclick = () => api()?.open_recordings?.(store.status.last_recording);
$("target").onclick = (ev) => ev.target.dataset.v && send({ type: "set", key: "target", value: ev.target.dataset.v });
// the two sides in the status bar are switches: system audio / microphone on or off, while running
for (const k of ["other", "me"]) {
  const toggle = () => {
    const on = new Set(store.status.settings?.sources ?? ["other", "me"]);
    if (!on.delete(k)) on.add(k);
    send({ type: "set", key: "sources", value: ["other", "me"].filter((x) => on.has(x)) });
  };
  $(`src-${k}`).onclick = toggle;
  $(`src-${k}`).onkeydown = (ev) => (ev.key === "Enter" || ev.key === " ") && toggle();
}

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
                         ["set-speaker", "speaker"], ["set-mic", "mic"], ["set-ui_lang", "ui_lang"],
                         ["set-azure_region", "azure_region"]]) {
  $(id).onchange = (ev) => send({ type: "set", key, value: ev.target.value.trim() });
}
// caption-bar look: drawn by the desktop shell, live while dragging the sliders
$("set-font").oninput = (ev) => api()?.set_bar_pref?.("font", Number(ev.target.value));
$("set-text-alpha").oninput = (ev) => api()?.set_bar_pref?.("text_alpha", Number(ev.target.value) / 100);
$("set-bg-alpha").oninput = (ev) => api()?.set_bar_pref?.("bg_alpha", Number(ev.target.value) / 100);
$("set-unsure").oninput = (ev) => api()?.set_bar_pref?.("unsure", Number(ev.target.value) / 100);
$("set-bar-show").onchange = (ev) => api()?.set_bar_pref?.("show", ev.target.value);
// desktop shell -> page: the bar's look changed (A−/A+ on the bar itself) or on load
window.__setBarPrefs = (p) => {
  const set = (id, v) => { const el = $(id); if (el && document.activeElement !== el) el.value = v; };
  set("set-font", p.font);
  set("set-text-alpha", Math.round(p.text_alpha * 100));
  set("set-bg-alpha", Math.round(p.bg_alpha * 100));
  set("set-unsure", Math.round((p.unsure ?? 0.55) * 100));
  set("set-bar-show", p.show ?? "both");
};
let hotkey = null;  // the global hotkey the desktop shell managed to register
function showHotkey() {
  $("hk").textContent = hotkey ? ` · ${hotkey}` : "";
  $("hk-hint").textContent = hotkey ? t("lock.hint_hk", { hk: hotkey }) : t("lock.hint");
}
window.__setHotkey = (hk) => { hotkey = hk; showHotkey(); };
showHotkey();
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
    out = "# 随堂字幕 Mitschrift Live\n\n" + done.map((e) => `**${fmtTime(e.start)} ${whoOf(e)}** ${e.committed}${e.translation ? `  \n> ${e.translation}` : ""}\n`).join("\n");
  } else {
    out = done.map((e) => `[${fmtTime(e.start)}] ${whoOf(e)}: ${e.committed}${e.translation ? `\n    → ${e.translation}` : ""}`).join("\n");
  }
  if (api()?.save_text) return api().save_text(out, fmt);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([out], { type: "text/plain;charset=utf-8" }));
  a.download = `mitschrift-live-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")}.${fmt}`;
  a.click();
}
for (const b of document.querySelectorAll("[data-export]")) b.onclick = () => exportAs(b.dataset.export);

window.__lcReady = true;
