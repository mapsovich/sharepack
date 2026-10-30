// Launcher UI. The actual work happens in launcher.py, exposed as window.pywebview.api.
const $ = (id) => document.getElementById(id);
let api;
let state = { packs: [], settings: {}, defaults: {} };
let target = null;       // code of the selected pack once it passed the check
let busy = false;
let checkTimer = null;

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const icon = (name) => `<svg viewBox="0 0 12 12"><use href="#i-${name}"/></svg>`;
const row = (label, value) => `${label}${value !== undefined && value !== "" ? ` <span class="val">${esc(value)}</span>` : ""}`;

// Window buttons are shared by all screens and come from a <template>.
for (const slot of document.querySelectorAll("[data-window-buttons]")) {
  slot.replaceWith($("window-buttons").content.cloneNode(true));
}

// Console collapse; launcher.py resizes the window.
for (const slot of document.querySelectorAll("[data-console-toggle]")) {
  const frag = $("console-toggle").content.cloneNode(true);
  frag.querySelector(".console-toggle").addEventListener("click", () => setConsoleHidden(true));
  frag.querySelector(".console-bar").addEventListener("click", () => setConsoleHidden(false));
  slot.replaceWith(frag);
}

// ---------- Console scrollbar ----------

const TRACK = 59;      // track height, same as the line in the mockup
const MIN_THUMB = 12;

function updateScrollbars() {
  for (const con of document.querySelectorAll(".console")) {
    const text = con.querySelector(".console-text");
    const thumb = con.querySelector(".console-thumb");
    const max = text.scrollHeight - text.clientHeight;
    const h = max > 0 ? Math.max(MIN_THUMB, TRACK * text.clientHeight / text.scrollHeight) : TRACK;
    thumb.style.height = `${h}px`;
    thumb.style.top = `${max > 0 ? (TRACK - h) * text.scrollTop / max : 0}px`;
  }
}

for (const con of document.querySelectorAll(".console")) {
  const text = con.querySelector(".console-text");
  const track = con.querySelector(".console-scroll");
  text.addEventListener("scroll", updateScrollbars);
  // drag the thumb or click the track
  const scrollTo = (clientY) => {
    const r = track.getBoundingClientRect();
    const k = (clientY - r.top) / r.height;
    text.scrollTop = Math.min(Math.max(k, 0), 1) * (text.scrollHeight - text.clientHeight);
  };
  track.addEventListener("mousedown", (e) => {
    e.preventDefault();
    track.classList.add("is-dragging");
    scrollTo(e.clientY);
    const move = (ev) => scrollTo(ev.clientY);
    const up = () => {
      track.classList.remove("is-dragging");
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  });
  new MutationObserver(updateScrollbars).observe(text, { childList: true, subtree: true, characterData: true });
}

function setConsoleHidden(hidden) {
  if (document.body.classList.contains("console-hidden") === hidden) return;
  document.body.classList.toggle("console-hidden", hidden);
  updateScrollbars();
  if (api) api.set_console(hidden);
}

function show(screen) {
  if (screen === "settings") fillSettings();
  Dropdown.closeAll();
  for (const s of document.querySelectorAll(".screen")) s.classList.toggle("is-active", s.id === screen);
}

// ---------- Dropdown ----------
// items: [{ value, label, hint?, action?: async () => {value,label}|null, muted?,
//           buttons?: [{ icon, title, danger?, onClick: (button) => void }] }]  (icon buttons on the right)

class Dropdown {
  static all = [];

  constructor(el, onChange) {
    this.el = el;
    this.onChange = onChange || (() => {});
    this.items = [];
    this.value = "";
    el.innerHTML = `<button type="button" class="input"><span class="dropdown-label"></span></button>
      <img class="chevron" src="assets/chevron.svg" width="14" height="8" alt=""><div class="dropdown-list"></div>`;
    this.button = el.querySelector(".input");
    this.label = el.querySelector(".dropdown-label");
    this.list = el.querySelector(".dropdown-list");
    this.button.addEventListener("click", (e) => { e.stopPropagation(); this.toggle(); });
    this.list.addEventListener("click", (e) => e.stopPropagation());
    Dropdown.all.push(this);
    this.render();
  }

  static closeAll(except) {
    for (const d of Dropdown.all) if (d !== except) d.el.classList.remove("is-open");
  }

  toggle() {
    const open = !this.el.classList.contains("is-open");
    Dropdown.closeAll(this);
    if (open) this.render();  // resets a half-confirmed delete
    this.el.classList.toggle("is-open", open);
  }

  setItems(items) {
    this.items = items;
    this.render();
  }

  // Adds the value to the list if it is not there (e.g. a path from the settings).
  setValue(value, label) {
    if (value && !this.items.some((i) => i.value === value && !i.action)) {
      const firstAction = this.items.findIndex((i) => i.action);
      const item = { value, label: label || value };
      if (firstAction < 0) this.items.push(item); else this.items.splice(firstAction, 0, item);
    }
    this.value = value || "";
    this.render();
  }

  render() {
    const current = this.items.find((i) => i.value === this.value && !i.action);
    this.label.textContent = this.value && current ? current.label : tr(this.el.dataset.placeholder);
    this.button.title = current && current.hint ? current.hint : "";
    this.el.classList.toggle("is-empty", !this.value);
    this.list.innerHTML = "";
    for (const item of this.items) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "dropdown-item" + (item.action ? " dropdown-item--action" : "") + (item.muted ? " dropdown-item--muted" : "")
        + (!item.action && item.value === this.value ? " is-selected" : "");
      b.innerHTML = esc(item.label) + (item.hint ? `<small>${esc(item.hint)}</small>` : "");
      b.title = item.hint || "";
      b.addEventListener("click", () => this.pick(item));
      if (!item.buttons) {
        this.list.appendChild(b);
        continue;
      }
      const rowEl = document.createElement("div");
      rowEl.className = "dropdown-row";
      rowEl.appendChild(b);
      for (const btn of item.buttons) {
        const x = document.createElement("button");
        x.type = "button";
        x.className = "dropdown-mini" + (btn.danger ? " dropdown-mini--danger" : "");
        x.innerHTML = icon(btn.icon);
        x.title = btn.title;
        x.addEventListener("click", () => btn.onClick(x));
        rowEl.appendChild(x);
      }
      this.list.appendChild(rowEl);
    }
  }

  async pick(item) {
    if (item.muted) return;
    this.el.classList.remove("is-open");
    if (item.action) {
      const res = await item.action();
      if (!res) return;
      this.setValue(res.value, res.label);
    } else {
      this.value = item.value;
      this.render();
    }
    this.onChange(this.value);
  }
}

document.addEventListener("click", () => Dropdown.closeAll());

// ---------- Main screen ----------
// The start button shows the state: grey (waiting or busy), green (play), red (error).
// Error details go to the console.

let ddPack;
let lastCheck = null;    // check_code result for the selected code
const NICK_RE = /^[A-Za-z0-9_]{3,16}$/;

// progress 0..1 fills the button with green from left to right
function setStart(text, kind, progress) {
  if (kind === "error") setConsoleHidden(false);  // show the details
  const b = $("start");
  b.textContent = text;
  b.disabled = kind !== "ok";
  b.classList.toggle("start--ok", kind === "ok");
  b.classList.toggle("start--error", kind === "error");
  const filling = progress !== undefined;
  // a new download starts from zero without animating backwards
  if (filling && !b.classList.contains("start--progress")) b.style.setProperty("--p", 0);
  b.classList.toggle("start--progress", filling);
  if (filling) requestAnimationFrame(() => b.style.setProperty("--p", progress));
}

function renderConsole(info, errors) {
  const v = (value, cls = "val") => value !== undefined && value !== "" ? ` <span class="${cls}">${esc(value)}</span>` : "";
  const status = (ok) => ok === undefined ? "" : v(tr(ok ? "status_ok" : "status_error"), ok ? "val--ok" : "val--err");
  const i = info || {};
  const left = [
    `<p>${tr("pack_name")}${v(i.name)}</p>`, `<p>${tr("pack_version")}${v(i.version)}</p>`,
    `<p>${tr("pack_files")}${v(i.files)}</p>`, `<p>${tr("pack_mods")}${v(i.mods)}</p>`];
  const right = [
    `<p title="${esc(i.core || "")}">${tr("core_name")}${v(i.core)}</p>`, `<p>${tr("core_version")}${v(i.core_version)}</p>`,
    `<p>${tr("pack_status")}${status(i.pack_ok)}</p>`, `<p>${tr("launcher_status")}${status(i.launcher_ok)}</p>`];
  const errs = (errors || []).map((e) => `<div class="err">${esc(e)}</div>`).join("");
  const c = $("main-console");
  c.innerHTML = (errs ? `<div class="console-errors">${errs}</div>` : "")
    + `<div class="console-cols"><div>${left.join("")}</div><div>${right.join("")}</div></div>`;
  c.scrollTop = 0;
}

function consoleLog(text, isError, cls) {
  const c = $("main-console");
  cls = cls || (isError ? "err" : "");
  c.insertAdjacentHTML("beforeend", `<div${cls ? ` class="${cls}"` : ""}>${esc(text)}</div>`);
  c.scrollTop = c.scrollHeight;
}

// Button state from the code check and the nickname.
function refreshStart() {
  if (busy) return;
  const res = lastCheck;
  if (!res) return setStart(tr("idle"), "idle");
  const nick = $("nick").value.trim();
  const errors = [...res.details];
  let text = res.button, kind = res.state;
  if (kind === "ok" && !nick) {
    text = tr("enter_nick"); kind = "idle";
  } else if (kind === "ok" && !NICK_RE.test(nick)) {
    text = tr("bad_nick"); kind = "error";
    errors.push(tr("nick_rule"));
  }
  target = kind === "ok" ? currentCode() : null;
  setStart(text, kind);
  renderConsole(res.info, errors);
}

function idle() {
  if (busy) return;
  target = null;
  lastCheck = null;
  renderConsole(null);
  setStart(tr("idle"), "idle");
}

function currentCode() {
  return $("code").value.trim() || ddPack.value;
}

async function checkCode(code) {
  setStart(tr("checking"), "busy");
  const res = await api.check_code(code);
  // The code may have changed while waiting.
  if (currentCode() !== code || busy) return;
  lastCheck = res;
  refreshStart();
}

function scheduleCheck(delay) {
  clearTimeout(checkTimer);
  const code = currentCode();
  if (!code) return idle();
  checkTimer = setTimeout(() => checkCode(code), delay);
}

function onCodeInput() {
  if ($("code").value.trim() && ddPack.value) ddPack.setValue("");
  scheduleCheck(300);
}

function onPackChange(value) {
  if (value) $("code").value = "";
  scheduleCheck(0);
}

function fillPacks() {
  const items = state.packs.map((p) => ({
    value: p.code, label: p.name || p.code, hint: p.code,
    buttons: [
      { icon: "folder", title: tr("open_profile"), onClick: () => api.open_profile(p.code) },
      { icon: "redownload", title: tr("redownload"), onClick: () => redownloadPack(p.code) },
      { icon: "delete", title: tr("delete_pack"), danger: true, onClick: (b) => deletePack(p, b) },
    ],
  }));
  if (!items.length) items.push({ value: "", label: tr("no_packs"), muted: true });
  const v = ddPack.value;
  ddPack.setItems(items);
  ddPack.setValue(state.packs.some((p) => p.code === v) ? v : "");
}

function selectPack(code) {
  $("code").value = "";
  ddPack.setValue(code);
}

function redownloadPack(code) {
  if (busy) return;
  Dropdown.closeAll();
  selectPack(code);
  clearTimeout(checkTimer);
  busy = true;
  target = null;
  $("main-console").innerHTML = "";
  setStart(tr("redownloading"), "busy");
  api.redownload_pack($("nick").value.trim(), code);
}

// Delete takes two clicks; the first one turns the icon red.
async function deletePack(pack, button) {
  if (busy) return;
  if (!button.classList.contains("is-armed")) {
    button.classList.add("is-armed");
    button.title = tr("delete_confirm");
    return;
  }
  Dropdown.closeAll();
  const res = await api.delete_pack(pack.code);
  const wasSelected = ddPack.value === pack.code;
  state.packs = res.packs;
  fillPacks();
  if (!res.ok) {
    idle();
    $("main-console").innerHTML = `<div class="err">${esc(res.error)}</div>`;
    return setStart(tr("delete_failed"), "error");
  }
  if (wasSelected) idle();
  consoleLog(tr("deleted", { name: pack.name || pack.code }));
}

function play() {
  if (!target || busy) return;
  busy = true;
  $("main-console").innerHTML = "";
  setStart(tr("loading"), "busy");
  const nick = $("nick").value.trim();
  rememberNick(nick);
  api.play(nick, target);
}

// ---------- Saved nicknames ----------
// A nickname is saved when "play" is pressed; the newest goes first.

const nickBox = { el: $("dd-nick") };
Dropdown.all.push(nickBox);  // so it closes together with the other lists

function rememberNick(nick) {
  if (!nick) return;
  state.nicks = [nick, ...(state.nicks || []).filter((n) => n.toLowerCase() !== nick.toLowerCase())].slice(0, 10);
  renderNicks();
}

function renderNicks() {
  const nicks = state.nicks || [];
  const list = nickBox.el.querySelector(".dropdown-list");
  nickBox.el.classList.toggle("is-empty", !nicks.length);
  if (!nicks.length) nickBox.el.classList.remove("is-open");
  list.innerHTML = "";
  for (const nick of nicks) {
    const rowEl = document.createElement("div");
    rowEl.className = "dropdown-row";
    const b = document.createElement("button");
    b.type = "button";
    b.className = "dropdown-item" + (nick === $("nick").value.trim() ? " is-selected" : "");
    b.textContent = nick;
    b.addEventListener("click", () => {
      $("nick").value = nick;
      nickBox.el.classList.remove("is-open");
      refreshStart();
    });
    const del = document.createElement("button");
    del.type = "button";
    del.className = "dropdown-mini dropdown-mini--danger";
    del.title = tr("forget_nick");
    del.innerHTML = icon("delete");
    del.addEventListener("click", async () => {
      state.nicks = await api.forget_nick(nick);
      renderNicks();
    });
    rowEl.append(b, del);
    list.appendChild(rowEl);
  }
}

function setupNicks() {
  nickBox.el.querySelector(".combo-toggle").addEventListener("click", (e) => {
    e.stopPropagation();
    const open = !nickBox.el.classList.contains("is-open");
    Dropdown.closeAll(nickBox);
    if (open) renderNicks();
    nickBox.el.classList.toggle("is-open", open);
  });
  nickBox.el.querySelector(".dropdown-list").addEventListener("click", (e) => e.stopPropagation());
  renderNicks();
}

// Called by launcher.py during installation and while the game runs.
window.onStatus = (text) => consoleLog(text);
window.onWarning = (text) => { setConsoleHidden(false); consoleLog(text, false, "warn"); };
window.onProgress = (p) => { if (busy) setStart(tr("downloading", { p: Math.floor(p * 100) }), "busy", p); };
window.onRunning = () => setStart(tr("running"), "busy", 1);
window.onFinished = (error, packs) => {
  busy = false;
  if (packs) { state.packs = packs; fillPacks(); }
  if (error) {
    // short text in the button, details in the console
    consoleLog(error.detail, true);
    target = null;
    setStart(error.short, "error");
  } else if (currentCode()) {
    // re-check after a redownload
    scheduleCheck(0);
  } else {
    idle();
  }
};

// ---------- Settings ----------

let ddDir, ddRam, ddJava;

function ramItems() {
  const total = state.total_ram_mb || 8192;
  const items = [{ value: "", label: tr("default"), hint: tr("as_in_pack") }];
  // Leave at least 2 GB to the system.
  for (const gb of [2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 32]) {
    if (gb * 1024 <= total - 2048) items.push({ value: String(gb * 1024), label: tr("gb", { n: gb }) });
  }
  return items;
}

function dirItems() {
  return [
    { value: "", label: tr("default"), hint: state.defaults.profiles_dir },
    { value: "", label: tr("browse"), action: async () => {
      const path = await api.browse_folder();
      return path ? { value: path, label: path } : null;
    } },
  ];
}

function ramLabel(value) {
  const mb = parseInt(value, 10);
  return mb ? (mb % 1024 ? tr("mb", { n: mb }) : tr("gb", { n: mb / 1024 })) : value;
}

async function loadJavas() {
  ddJava.setItems([{ value: "", label: tr("default"), hint: tr("java_auto") },
    { value: "", label: tr("java_searching"), muted: true }]);
  ddJava.setValue(state.settings.java_path || "");
  const javas = await api.find_java();
  const items = [{ value: "", label: tr("default"), hint: tr("java_auto") }];
  for (const j of javas) items.push({ value: j.path, label: j.label, hint: j.path });
  if (!javas.length) items.push({ value: "", label: tr("java_none"), muted: true });
  items.push({ value: "", label: tr("browse"), action: async () => {
    const path = await api.browse_java();
    return path ? { value: path, label: (await api.describe_java(path)) || path } : null;
  } });
  // something may have been picked during the search
  const chosen = ddJava.value;
  ddJava.setItems(items);
  ddJava.setValue(chosen, chosen && ((javas.find((j) => j.path === chosen) || {}).label || await api.describe_java(chosen)));
  describeSettings();
}

function setupSettings() {
  ddDir = new Dropdown($("dd-dir"), describeSettings);
  ddRam = new Dropdown($("dd-ram"), describeSettings);
  ddJava = new Dropdown($("dd-java"), describeSettings);
  ddDir.setItems(dirItems());
  ddRam.setItems(ramItems());
  loadJavas();
  $("s-args").addEventListener("input", describeSettings);
}

function fillSettings() {
  const s = state.settings;
  ddDir.setValue(s.profiles_dir || "");
  ddRam.setValue(s.memory || "", ramLabel(s.memory || ""));
  ddJava.setValue(s.java_path || "");
  $("s-args").value = s.java_args || "";
  describeSettings();
}

function describeSettings() {
  const d = state.defaults;
  const labelOf = (dd) => dd.value ? dd.label.textContent : "";
  $("settings-console").innerHTML = [
    row(tr("d_profiles_dir"), ddDir.value || d.profiles_dir),
    row(tr("d_ram"), labelOf(ddRam) || tr("d_memory_default")),
    row(tr("d_java"), labelOf(ddJava) || tr("d_java_default")),
    row(tr("d_java_args"), $("s-args").value.trim() || tr("d_args_default")),
  ].join("<br>");
}

async function saveSettings() {
  const values = {
    profiles_dir: ddDir.value,
    memory: ddRam.value,
    java_path: ddJava.value,
    java_args: $("s-args").value.trim(),
  };
  const res = await api.save_settings(values);
  if (!res.ok) {
    $("settings-console").innerHTML = `<span class="err">${esc(res.error)}</span>`;
    return;
  }
  state.settings = res.settings;
  show("main");
  // settings affect the launcher status, so re-check
  if (currentCode() && !busy) scheduleCheck(0);
}

// ---------- Language ----------
// Switches ru/en in place; unsaved settings are kept.

// Theme: "auto" follows Windows, the button cycles auto -> light -> dark.
const THEMES = ["auto", "light", "dark"];
const lightQuery = window.matchMedia("(prefers-color-scheme: light)");
let THEME = "auto";
let systemTheme = "dark";

function applyTheme() {
  document.documentElement.dataset.theme = THEME === "auto" ? systemTheme : THEME;
  const btn = $("theme");
  btn.title = tr("theme_" + THEME);
  btn.querySelector("use").setAttribute("href", "#i-theme-" + THEME);
}

async function switchTheme() {
  THEME = await api.set_theme(THEMES[(THEMES.indexOf(THEME) + 1) % THEMES.length]);
  applyTheme();
}

lightQuery.addEventListener("change", (e) => {
  systemTheme = e.matches ? "light" : "dark";
  applyTheme();
});

function showLangButton() {
  $("lang").textContent = LANG.toUpperCase();
  applyTheme();  // the theme button title depends on the language
}

async function switchLanguage() {
  const next = LANGS[(LANGS.indexOf(LANG) + 1) % LANGS.length];
  LANG = await api.set_language(next);
  applyStaticTexts();
  showLangButton();
  const dir = ddDir.value, ram = ddRam.value;
  ddDir.setItems(dirItems());
  ddDir.setValue(dir);
  ddRam.setItems(ramItems());
  ddRam.setValue(ram, ramLabel(ram));
  ddPack.render();
  fillPacks();
  renderNicks();
  describeSettings();
  loadJavas();  // Java labels come from launcher.py
  // so do errors and pack info, so re-check the code
  if (!busy) {
    if (currentCode()) scheduleCheck(0); else idle();
  }
}

// ---------- Startup ----------

window.addEventListener("pywebviewready", async () => {
  api = window.pywebview.api;
  state = await api.get_state();
  LANG = LANGS.includes(state.lang) ? state.lang : "ru";
  THEME = THEMES.includes(state.theme) ? state.theme : "auto";
  systemTheme = state.system_theme === "light" ? "light" : "dark";
  applyStaticTexts();
  showLangButton();
  // scale to the screen; launcher.py resizes the window
  document.documentElement.style.zoom = state.zoom || 1;
  document.body.classList.toggle("console-hidden", !!state.console_hidden);
  await api.show_window();

  ddPack = new Dropdown($("dd-pack"), onPackChange);
  fillPacks();
  setupSettings();
  $("nick").value = state.nick || "";
  $("nick").addEventListener("input", refreshStart);
  setupNicks();
  idle();
  if (state.code) {
    if (state.packs.some((p) => p.code === state.code)) ddPack.setValue(state.code);
    else $("code").value = state.code;
    scheduleCheck(0);
  }

  $("code").addEventListener("input", onCodeInput);
  $("code").addEventListener("change", onCodeInput);
  $("start").addEventListener("click", play);
  $("save").addEventListener("click", saveSettings);
  for (const el of document.querySelectorAll("[data-go]")) el.addEventListener("click", () => show(el.dataset.go));
  for (const el of document.querySelectorAll("[data-action]")) el.addEventListener("click", () => api[el.dataset.action]());
  for (const el of document.querySelectorAll("[data-link]")) el.addEventListener("click", () => api.open_link(el.dataset.link));
  $("open-profiles").addEventListener("click", () => api.open_profiles_dir(ddDir.value));
  $("lang").addEventListener("click", switchLanguage);
  $("theme").addEventListener("click", switchTheme);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") Dropdown.closeAll();
    if (e.key === "Enter" && $("main").classList.contains("is-active")) play();
  });

  // the splash stays until launcher.py is ready
  const { update } = await api.ready();
  if (update) return showUpdate(update);
  show("main");
});

function showUpdate(update) {
  $("update-version").textContent = update.version;
  show("update");
  const toMain = async () => { await api.enter_main(); show("main"); };
  $("update-download").addEventListener("click", async (e) => {
    const b = e.currentTarget;
    b.disabled = true;
    b.textContent = tr("update_downloading");
    const res = await api.download_update();
    // on success the launcher restarts itself
    if (!res.restarting) toMain();
  });
  $("update-later").addEventListener("click", toMain);
}
