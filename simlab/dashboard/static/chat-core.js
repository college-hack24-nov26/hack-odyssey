"use strict";
/* SimChat: the core of the chat page (/chat). It owns the store, the long-poll loop, the card shell, the dock, the chips, the Limits strip and the composer.
   The ONLY column-0 declaration of the whole chat front end is `const SimChat` below: every other chat file is an IIFE that attaches to it (the dashboard scripts
   share one global scope and tests/test_parts_api.py fails on duplicate top-level names). Everything inside is indented on purpose.
   Extension points for the other chat files: SimChat.registerCard, SimChat.on, SimChat.util, SimChat.layers, SimChat.shell and SimChat.panes (the sidebar and the right-hand
   pane registry, set by chat-shell.js), SimChat.sheet (the Plan/Tests/Parts panes, chat-sheet.js), SimChat.live (chat-live.js), SimChat.dock.slot,
   SimChat.fixtures (chat-fixtures.js), SimChat.net (replaced by chat-mock.js). Events emitted: chat (id changed), reset, message, card:create, card:patch, limits, theme, pane. */
const SimChat = (function () {
  const $ = id => document.getElementById(id);
  const reduced = () => !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);
  const LS = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* blocked storage: the page works without it */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
  };

  /* ================================================================ util */
  const ICONS = {
    "platform-water": '<path d="M2 9.5h12l-1.8 3.2H3.8z"/><path d="M8 9.5V3l3.6 4.6H8"/>',
    "platform-ground": '<path d="M3 9h10v3H3z"/><path d="M5.5 9V6.5h5V9"/><circle cx="5" cy="13" r="1.3"/><circle cx="11" cy="13" r="1.3"/>',
    "platform-air": '<circle cx="8" cy="8" r="1.5"/><path d="M5 5l2 2M11 5L9 7M5 11l2-2M11 11L9 9"/><circle cx="3.8" cy="3.8" r="1.4"/><circle cx="12.2" cy="3.8" r="1.4"/><circle cx="3.8" cy="12.2" r="1.4"/><circle cx="12.2" cy="12.2" r="1.4"/>',
    "check": '<path d="M3 8.5l3.2 3.2L13 4.8"/>',
    "cross": '<path d="M4 4l8 8M12 4l-8 8"/>',
    "clock": '<circle cx="8" cy="8" r="5.6"/><path d="M8 4.8V8l2.2 1.4"/>',
    "stop": '<rect x="4" y="4" width="8" height="8" rx="1.6" fill="currentColor" stroke="none"/>',
    "refresh": '<path d="M13.2 8A5.2 5.2 0 1 1 11.6 4.3"/><path d="M13.4 2.6v3.2h-3.2"/>',
    "circle-slash": '<circle cx="8" cy="8" r="5.6"/><path d="M4 12L12 4"/>',
    "return": '<path d="M12.5 3.5v5a1.5 1.5 0 0 1-1.5 1.5H4"/><path d="M6.5 7.5L3.5 10l3 2.5"/>',
    "chevron": '<path d="M4.5 6.5L8 10l3.5-3.5"/>',
    "play": '<path d="M5 3.5v9l7.5-4.5z" fill="currentColor" stroke="none"/>',
    "pause": '<rect x="4.5" y="3.5" width="2.6" height="9" rx=".8" fill="currentColor" stroke="none"/><rect x="8.9" y="3.5" width="2.6" height="9" rx=".8" fill="currentColor" stroke="none"/>',
    "expand": '<path d="M9.5 2.5h4v4M6.5 13.5h-4v-4M13.5 2.5L9 7M2.5 13.5L7 9"/>',
    "sheet": '<path d="M2.5 4A1.5 1.5 0 0 1 4 2.5h8A1.5 1.5 0 0 1 13.5 4v8a1.5 1.5 0 0 1-1.5 1.5H4A1.5 1.5 0 0 1 2.5 12z"/><path d="M9 2.5v11"/>',
    "send": '<path d="M8 13V3M3.8 7.2L8 3l4.2 4.2"/>',
    "plus": '<path d="M8 3v10M3 8h10"/>',
    "history": '<path d="M2.8 8a5.2 5.2 0 1 0 1.6-3.8"/><path d="M2.6 2.6v3.2h3.2"/><path d="M8 5v3.2l2 1.2"/>',
    "moon": '<path d="M12.8 9.6A5.4 5.4 0 0 1 6.4 3.2a5.4 5.4 0 1 0 6.4 6.4z"/>',
    "sun": '<circle cx="8" cy="8" r="2.6"/><path d="M8 1.8v1.5M8 12.7v1.5M1.8 8h1.5M12.7 8h1.5M3.6 3.6l1 1M11.4 11.4l1 1M12.4 3.6l-1 1M4.6 11.4l-1 1"/>',
    "ai": '<path d="M8 2.2l1.4 3.7 3.7 1.4-3.7 1.4L8 12.4 6.6 8.7 2.9 7.3l3.7-1.4z"/>',
    "lock": '<rect x="3.5" y="7" width="9" height="6.5" rx="1.5"/><path d="M5.5 7V5.5a2.5 2.5 0 0 1 5 0V7"/>',
    "link": '<path d="M9 3h4v4M13 3L7.5 8.5M12 9.5V12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h2.5"/>',
    "undo": '<path d="M6 3.8L3 6.8l3 3"/><path d="M3 6.8h6.2a3.6 3.6 0 0 1 0 7.2H7"/>',
    "redo": '<path d="M10 3.8l3 3-3 3"/><path d="M13 6.8H6.8a3.6 3.6 0 0 0 0 7.2H9"/>',
    "warn": '<path d="M8 2.5L14 13H2z"/><path d="M8 6.5v3M8 11.3v.1"/>',
    "info": '<circle cx="8" cy="8" r="5.6"/><path d="M8 7.4v3.6M8 5v.1"/>',
    "menu": '<circle cx="3.5" cy="8" r="1.1" fill="currentColor" stroke="none"/><circle cx="8" cy="8" r="1.1" fill="currentColor" stroke="none"/><circle cx="12.5" cy="8" r="1.1" fill="currentColor" stroke="none"/>',
    "close": '<path d="M4 4l8 8M12 4l-8 8"/>',
    "settings": '<path d="M2.5 5h11M2.5 11h11"/><circle cx="6" cy="5" r="1.7" fill="var(--ch-surface)"/><circle cx="10" cy="11" r="1.7" fill="var(--ch-surface)"/>',
    "down": '<path d="M8 3v10M3.8 8.8L8 13l4.2-4.2"/>',
    "sidebar": '<rect x="2.5" y="3" width="11" height="10" rx="2"/><path d="M6.2 3v10"/>',
    "panes": '<rect x="2.5" y="3" width="11" height="10" rx="2"/><path d="M9.8 3v10"/>',
    "folder": '<path d="M2.5 5.2A1.7 1.7 0 0 1 4.2 3.5h2.3l1.5 1.7h3.8a1.7 1.7 0 0 1 1.7 1.7v4.4a1.7 1.7 0 0 1-1.7 1.7H4.2a1.7 1.7 0 0 1-1.7-1.7z"/>',
    "folder-plus": '<path d="M2.5 5.2A1.7 1.7 0 0 1 4.2 3.5h2.3l1.5 1.7h3.8a1.7 1.7 0 0 1 1.7 1.7v4.4a1.7 1.7 0 0 1-1.7 1.7H4.2a1.7 1.7 0 0 1-1.7-1.7z"/><path d="M8 7.3v3.4M6.3 9h3.4"/>',
    "chat": '<path d="M3 4.2A1.7 1.7 0 0 1 4.7 2.5h6.6A1.7 1.7 0 0 1 13 4.2v4.6a1.7 1.7 0 0 1-1.7 1.7H7.2L4.4 13v-2.5h0A1.7 1.7 0 0 1 3 8.8z"/>',
    "search": '<circle cx="7.2" cy="7.2" r="4.2"/><path d="M10.4 10.4L13.5 13.5"/>',
    "list": '<path d="M5.5 4.5h8M5.5 8h8M5.5 11.5h8"/><circle cx="2.8" cy="4.5" r=".7" fill="currentColor" stroke="none"/><circle cx="2.8" cy="8" r=".7" fill="currentColor" stroke="none"/><circle cx="2.8" cy="11.5" r=".7" fill="currentColor" stroke="none"/>',
    "cube": '<path d="M8 2.2l5 2.6v6.4L8 13.8 3 11.2V4.8z"/><path d="M3 4.8l5 2.6 5-2.6M8 7.4v6.4"/>',
    "box": '<path d="M2.6 5.2L8 2.6l5.4 2.6v5.6L8 13.4l-5.4-2.6z"/><path d="M2.6 5.2L8 7.8l5.4-2.6M8 7.8v5.6"/>',
    "tasks": '<rect x="2.5" y="2.8" width="11" height="10.4" rx="2"/><path d="M5.2 6.2l1 1 1.8-1.9M9 6.4h2.4M5.2 10l1 1 1.8-1.9M9 10.2h2.4"/>',
  };
  function icon(name) {
    const p = ICONS[name];
    if (!p) return "";
    return '<svg class="ch-i" viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' + p + "</svg>";
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function add(node, kids) {
    for (const c of kids) {
      if (c == null || c === false) continue;
      if (Array.isArray(c)) add(node, c);
      else if (c instanceof Node) node.appendChild(c);
      else node.appendChild(document.createTextNode(String(c)));
    }
  }
  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    if (attrs && typeof attrs === "object" && !(attrs instanceof Node) && !Array.isArray(attrs)) {
      for (const k of Object.keys(attrs)) {
        const v = attrs[k];
        if (v == null || v === false) continue;
        if (k === "class") n.className = v;
        else if (k === "text") n.textContent = v;
        else if (k === "dataset") Object.assign(n.dataset, v);
        else if (k === "style" && typeof v === "string") n.style.cssText = v;
        else if (k.length > 2 && k.slice(0, 2) === "on" && typeof v === "function") n.addEventListener(k.slice(2).toLowerCase(), v);
        else if (v === true) n.setAttribute(k, "");
        else n.setAttribute(k, String(v));
      }
    } else if (attrs != null) kids.unshift(attrs);
    add(n, kids);
    return n;
  }
  function iconEl(name) {
    const s = document.createElement("span");
    s.className = "ch-ico";
    s.style.display = "inline-flex";
    s.innerHTML = icon(name);                                                      // static, trusted markup from the table above
    return s;
  }
  function fmtNum(v, digits) {
    if (v == null || v === "" || (typeof v === "number" && !isFinite(v))) return "-";
    const n = Number(v);
    if (!isFinite(n)) return String(v);
    const d = digits == null ? 2 : digits;
    return String(Math.round(n * Math.pow(10, d)) / Math.pow(10, d));
  }
  function fmtSecs(n) {
    n = Number(n);
    if (!isFinite(n) || n < 0) return "";
    if (n < 60) return (n < 10 ? n.toFixed(1) : String(Math.round(n))) + " s";
    return Math.floor(n / 60) + ":" + String(Math.floor(n % 60)).padStart(2, "0") + " min";
  }
  function clock(n) {
    n = Math.max(0, Math.floor(Number(n) || 0));
    return String(Math.floor(n / 60)).padStart(2, "0") + ":" + String(n % 60).padStart(2, "0");
  }
  function uid() { return Date.now().toString(36) + Math.random().toString(36).slice(2, 8); }
  function isEditable(t) {
    if (!t || !t.tagName) return false;
    const tag = t.tagName.toUpperCase();
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t.isContentEditable === true;
  }
  function mergePatch(target, patch) {
    if (patch === null || typeof patch !== "object" || Array.isArray(patch)) return patch;
    const out = target && typeof target === "object" && !Array.isArray(target) ? Object.assign({}, target) : {};
    for (const k of Object.keys(patch)) {
      if (patch[k] === null) delete out[k];
      else out[k] = mergePatch(out[k], patch[k]);
    }
    return out;
  }
  const clone = o => (o == null ? o : JSON.parse(JSON.stringify(o)));
  const sleepers = new Set();
  function sleep(ms, signal) {
    return new Promise(res => {
      const t = setTimeout(done, ms);
      function done() { clearTimeout(t); sleepers.delete(done); if (signal) signal.removeEventListener("abort", done); res(); }
      sleepers.add(done);
      if (signal) signal.addEventListener("abort", done);
    });
  }
  function wakeSleepers() { Array.from(sleepers).forEach(f => f()); }
  function flash(node) {
    if (!node || reduced()) return;
    node.classList.remove("ch-val-flash");
    void node.offsetWidth;
    node.classList.add("ch-val-flash");
    setTimeout(() => node.classList.remove("ch-val-flash"), 600);
  }
  let announceT = 0;
  function announce(text) {
    const a = $("ch-announcer");
    if (!a || !text) return;
    clearTimeout(announceT);
    a.textContent = "";
    announceT = setTimeout(() => { a.textContent = String(text); }, 60);
  }
  function toast(message, kind) {
    const box = $("ch-toasts");
    if (!box || !message) return;
    while (box.children.length >= 3) box.firstChild.remove();
    const close = el("button", { type: "button", "aria-label": "Dismiss" });
    close.innerHTML = icon("close");
    const t = el("div", { class: "ch-toast", dataset: { kind: kind || "info" } }, iconEl(kind === "error" ? "warn" : "info"), el("div", { text: String(message) }), close);
    close.addEventListener("click", () => t.remove());
    box.appendChild(t);
    setTimeout(() => t.remove(), kind === "error" ? 9000 : 5000);
  }
  /* keep Tab inside a container while it is open; returns a function that stops doing so */
  function trapFocus(container) {
    function onKey(e) {
      if (e.key !== "Tab") return;
      const items = Array.from(container.querySelectorAll('a[href], button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex="-1"])')).filter(n => n.offsetParent !== null);
      if (!items.length) { e.preventDefault(); return; }
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && (document.activeElement === first || !container.contains(document.activeElement))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (document.activeElement === last || !container.contains(document.activeElement))) { e.preventDefault(); first.focus(); }
    }
    container.addEventListener("keydown", onKey);
    return () => container.removeEventListener("keydown", onKey);
  }
  const util = { esc, el, iconEl, icon, fmtSecs, fmtNum, clock, uid, mergePatch, clone, flash, announce, toast, trapFocus, isEditable, reducedMotion: reduced,
    skeleton(h) { return el("span", { class: "ch-skel", style: "--ch-h:" + (h || 14) + "px" }); } };

  /* ================================================================ layers (Esc closes the topmost) */
  const stack = [];
  const layers = {
    push(id, close) { layers.remove(id); stack.push({ id, close }); },
    remove(id) { const i = stack.findIndex(l => l.id === id); if (i >= 0) stack.splice(i, 1); },
    top() { return stack.length ? stack[stack.length - 1].id : null; },
    closeTop() { const l = stack[stack.length - 1]; if (!l) return false; stack.pop(); try { l.close(); } catch (e) { console.error(e); } return true; },
    has() { return stack.length > 0; },
  };

  /* ================================================================ events (on / emit) */
  const handlers = {};
  function on(name, fn) { (handlers[name] = handlers[name] || []).push(fn); return () => { handlers[name] = (handlers[name] || []).filter(f => f !== fn); }; }
  function emit(name, a, b) { (handlers[name] || []).slice().forEach(f => { try { f(a, b); } catch (e) { console.error("SimChat handler for " + name + " failed", e); } }); }

  /* ================================================================ state */
  const state = { chatId: null, title: "New chat", seq: 0, events: [], cards: {}, order: [], chips: [], dock: {}, limits: { count: null, stale: false, line: null }, budget: {}, cases: [], jobs: {},
    busy: false, sending: 0, replaying: false, connected: true, messages: 0, lastTouched: null, caps: null };
  const shells = {};                                    // card id -> {root, head, body, foot, clip, sumEl, ...}
  const openPref = {};                                  // card id -> the user's own open/closed choice
  let pending = [];                                     // optimistic user bubbles waiting for the server's echo
  const settings = { ai: LS.get("simlab.chat.ai") || "ask", theme: LS.get("simlab.chat.theme") || "auto" };

  /* ================================================================ network (chat-mock.js replaces these methods) */
  async function jfetch(url, opts) {
    const r = await fetch(url, opts);
    let data = null;
    try { data = await r.json(); } catch (e) { /* no body */ }
    if (!r.ok) {
      let msg = data && typeof data.detail === "string" ? data.detail : "";
      if (!msg && data && Array.isArray(data.detail) && data.detail[0] && data.detail[0].msg) msg = data.detail[0].msg;
      const err = new Error(msg || ("The server answered " + r.status));
      err.status = r.status;
      throw err;
    }
    return data;
  }
  const post = (url, body, signal) => jfetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}), signal });
  const net = {
    async newChat() {
      try { return await post("/api/chat/new", {}); }
      catch (e) { if (e.status === 404 || e.status === 405) return post("/api/chat", {}); throw e; }
    },
    latest() { return jfetch("/api/chat/latest"); },
    list() { return jfetch("/api/chat/list"); },
    capabilities() { return jfetch("/api/chat/capabilities"); },
    poll(id, after, wait, signal) { return jfetch("/api/chat/" + encodeURIComponent(id) + "/events?after=" + after + "&wait=" + wait, { signal }); },
    send(id, body) { return post("/api/chat/" + encodeURIComponent(id) + "/send", body); },
    rename(id, title) { return post("/api/chat/" + encodeURIComponent(id) + "/rename", { title }); },
    state(id) { return jfetch("/api/chat/" + encodeURIComponent(id) + "/state"); },
  };

  /* ================================================================ card registry and the generic renderer */
  const defs = {};
  const DEFAULT_ICON = { understanding: "ai", design: "sheet", watch: "play", tests: "check", results: "check", parts: "sheet", improve: "ai", limits: "circle-slash", answer: "info", blocked: "circle-slash" };
  const generic = {
    icon: c => (c.type === "understanding" && c.platform ? "platform-" + c.platform : DEFAULT_ICON[c.type] || "sheet"),
    title: c => c.title || c.type,
    summary: c => c.summary || "",
    defaultOpen: false,
    mount(body, card) { generic.update(body, card); },
    update(body, card) {
      body.replaceChildren();
      if (card.summary) body.appendChild(el("p", { text: card.summary, style: "margin:4px 0 8px" }));
      if (Array.isArray(card.lines) && card.lines.length) body.appendChild(el("ul", { style: "margin:0 0 8px;padding-left:18px" }, card.lines.map(t => el("li", { text: String(t) }))));
      const pr = card.progress;
      if (pr && typeof pr.done === "number" && typeof pr.total === "number" && pr.total > 0) {
        const bar = el("div", { class: "ch-prog", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": String(pr.total), "aria-valuenow": String(pr.done), "aria-label": "Progress" }, el("i"));
        bar.style.setProperty("--ch-p", Math.min(100, 100 * pr.done / pr.total) + "%");
        body.appendChild(bar);
        body.appendChild(el("p", { class: "ch-mono", style: "margin:6px 0 0;font-size:12.5px;color:var(--ch-muted)", text: pr.done + " of " + pr.total + (pr.label ? " " + pr.label : "") }));
      }
    },
  };
  function registerCard(type, def) { defs[type] = Object.assign({}, def); }
  function defFor(type) { return defs[type] || generic; }

  /* card.state (server) / registry status() -> the chip. States: queued running done met below stopped interrupted not-tested stale error (+ warn, blocked) */
  const ST_ICON = { done: "check", met: "check", below: "cross", queued: "clock", stopped: "stop", interrupted: "return", "not-tested": "circle-slash", blocked: "circle-slash", stale: "refresh", error: "warn", warn: "warn" };
  const ST_WORD = { queued: "Waiting", running: "Running", done: "Ready", met: "Met bar", below: "Below bar", stopped: "Stopped", interrupted: "Interrupted", "not-tested": "NOT TESTED", blocked: "Can't do here", stale: "Stale", error: "Problem", warn: "Partly" };
  const SERVER_STATE = { ready: ["done", "Ready"], done: ["done", "Done"], applied: ["done", "In use"], kept: ["done", "Kept"], idle: ["queued", "Not run yet"], empty: ["queued", "Empty"], pending: ["queued", "Waiting"], loading: ["queued", "Loading"],
    waiting: ["queued", "Waiting"], queued: ["queued", "Waiting"], running: ["running", "Running"], stopping: ["running", "Stopping"], stale: ["stale", "Stale"], failed: ["error", "Problem"], error: ["error", "Problem"],
    stopped: ["stopped", "Stopped"], interrupted: ["interrupted", "Interrupted"], unavailable: ["not-tested", "Not available"], unsupported: ["not-tested", "NOT TESTED"], blocked: ["blocked", "Can't do here"],
    met: ["met", "Met bar"], below: ["below", "Below bar"], partial: ["warn", "Partly met"] };
  function statusOf(card, def) {
    let st = null;
    if (def.status) { try { st = def.status(card); } catch (e) { console.error(e); } }
    if (!st) {
      const m = SERVER_STATE[String(card.state || card.status || "done").toLowerCase()] || ["done", String(card.state || "Ready")];
      st = { state: m[0], label: m[1] };
    }
    const s = ST_ICON[st.state] || st.state === "running" ? st.state : "done";
    return { state: s, label: st.label || ST_WORD[s] || s };
  }

  /* ================================================================ actions */
  function openSheet(tab, opts) {
    if (SimChat.sheet && typeof SimChat.sheet.open === "function") { SimChat.sheet.open(tab || tabForActivity(), opts); return true; }
    toast("The details view is not available in this build.");
    return false;
  }
  function tabForActivity() {
    const t = state.lastTouched;
    return t === "results" ? "results" : t === "tests" ? "tests" : t === "parts" ? "parts" : "design";
  }
  function runAction(a, btn) {
    if (!a || a.enabled === false) return Promise.resolve();
    if (a.type === "open_details") { openSheet(a.args && a.args.tab); return Promise.resolve(); }
    if (btn) { btn.disabled = true; setTimeout(() => { btn.disabled = false; }, 700); }
    return send({ type: a.type, args: a.args || {} }, { label: a.label });
  }
  function actionButtons(actions, opts) {
    opts = opts || {};
    const frag = document.createDocumentFragment();
    const list = (actions || []).filter(a => a && a.type && !(a.enabled === false && (a.type === "stop" || a.type === "undo" || a.type === "redo")));
    const details = list.find(a => a.type === "open_details");
    const others = list.filter(a => a.type !== "open_details").slice(0, opts.max == null ? 3 : opts.max);
    others.concat(details ? [details] : []).forEach(a => {
      const dis = a.enabled === false;
      const b = el("button", { type: "button", class: "ch-btn" + (a.primary ? " ch-btn--primary" : "") + (a.type === "stop" ? " ch-btn--stop" : ""), disabled: dis, title: dis && a.reason ? a.reason : null,
        "aria-label": dis && a.reason ? a.label + " (not available: " + a.reason + ")" : null }, a.label || a.type);
      if (a.type === "open_details") b.insertBefore(iconEl("sheet"), b.firstChild);
      b.addEventListener("click", () => runAction(a, b));
      frag.appendChild(b);
    });
    return frag;
  }
  function chipEl(c) {
    const dis = c.enabled === false;
    const tag = c.tag ? String(c.tag).toLowerCase() : "";
    const b = el("button", { type: "button", class: "ch-chip", disabled: dis, title: dis && c.reason ? c.reason : null, dataset: { primary: String(c.primary === true || c.kind === "primary"), tag } },
      el("span", { text: c.label || c.id }),
      tag ? el("span", { class: "ch-tag", dataset: { tag } }, tag === "ai" ? iconEl("ai") : null, tag === "ai" ? "AI" : tag) : null,
      dis && c.reason ? el("span", { class: "ch-sr", text: " (not available: " + c.reason + ")" }) : null);
    b.addEventListener("click", () => {
      if (c.local) { c.local(); return; }
      if (c.action) { b.disabled = true; setTimeout(() => { b.disabled = false; }, 600); runAction(Object.assign({ label: c.label }, c.action, { enabled: true })); }
    });
    return b;
  }

  /* ================================================================ cards */
  function setOpen(id, open, user) {
    const sh = shells[id];
    if (!sh) return;
    open = !!open;
    sh.open = open;
    sh.root.dataset.open = String(open);
    sh.head.setAttribute("aria-expanded", String(open));
    sh.clip.inert = !open;
    if (user) openPref[id] = open;
    emit("card:toggle", { id, open, card: state.cards[id] });
  }
  function refreshShell(sh, card, animateSummary) {
    const def = defFor(card.type);
    const st = statusOf(card, def);
    sh.root.dataset.state = st.state;
    sh.chip.dataset.st = st.state;
    sh.chip.replaceChildren();
    if (st.state !== "running") sh.chip.appendChild(iconEl(ST_ICON[st.state] || "check"));
    sh.chip.appendChild(document.createTextNode(st.label));
    let title = card.title || card.type;
    if (def.title) { try { title = def.title(card) || title; } catch (e) { console.error(e); } }
    sh.titleEl.textContent = title;
    let sum = card.summary || "";
    if (def.summary) { try { sum = def.summary(card); } catch (e) { console.error(e); } }
    if (sh.sumEl.textContent !== (sum || "")) { sh.sumEl.textContent = sum || ""; if (animateSummary) flash(sh.sumEl); }
    sh.head.setAttribute("aria-label", title + ", " + st.label + (sum ? ". " + sum : ""));
    const secs = card.seconds != null ? card.seconds : card.duration;
    sh.dur.textContent = typeof secs === "number" ? fmtSecs(secs) : "";
    let ic = typeof def.icon === "function" ? def.icon(card) : def.icon;
    if (!ic) ic = card.type === "understanding" && card.platform ? "platform-" + card.platform : DEFAULT_ICON[card.type] || "sheet";
    if (sh.icName !== ic) { sh.icName = ic; sh.ico.innerHTML = icon(ic); }
    sh.root.classList.toggle("ch-hatch-card", st.state === "not-tested");
    if (def.footer !== false) {
      sh.foot.replaceChildren(actionButtons(card.actions, { max: 3 }));
    }
    return st;
  }
  function buildCard(card, o) {
    const def = defFor(card.type);
    const id = card.id;
    const uidc = "ch-card-" + id.replace(/[^\w-]/g, "_");
    const ico = el("span", { class: "ch-card-ico" });
    const titleEl = el("span", { class: "ch-card-title", id: uidc + "-t" });
    const sumEl = el("span", { class: "ch-card-sum" });
    const chip = el("span", { class: "ch-st" });
    const dur = el("span", { class: "ch-card-dur ch-mono" });
    const chev = el("span", { class: "ch-chev" });
    chev.innerHTML = icon("chevron");
    const head = el("button", { type: "button", class: "ch-card-head", id: uidc + "-h", "aria-expanded": "false", "aria-controls": uidc + "-b" }, ico, titleEl, sumEl, chip, dur, chev);
    const err = el("div", { class: "ch-card-err", role: "alert", hidden: true });
    const body = el("div", { class: "ch-card-body", "aria-live": "off" });
    const foot = el("div", { class: "ch-card-foot" });
    const clip = el("div", { class: "ch-card-clip", id: uidc + "-b", role: "region", "aria-labelledby": uidc + "-t" }, el("div", { class: "ch-card-inner" }, body, foot));
    const root = el("article", { class: "ch-card" + (o.replay ? " ch-nostagger" : ""), id: uidc, dataset: { type: card.type, cardId: id, open: "false", state: "done" }, role: "group", "aria-labelledby": uidc + "-t" },
      el("h3", { class: "ch-card-h" }, head), err, clip);
    if (o.delay) root.style.setProperty("--ch-d", o.delay + "ms");
    const sh = { root, head, body, foot, clip, sumEl, titleEl, chip, dur, ico, err, open: false, icName: null };
    shells[id] = sh;
    head.addEventListener("click", () => { setOpen(id, !sh.open, true); stick = false; });
    clip.inert = true;
    refreshShell(sh, card, false);
    try { if (def.mount) def.mount(body, card); } catch (e) { console.error("card mount failed", card.type, e); showCardError(id, "This card could not be drawn. The rest of the chat still works."); }
    const open = id in openPref ? openPref[id] : !!def.defaultOpen && (!o.replay || o.isLast !== false);
    setOpen(id, open, false);
    return root;
  }
  function showCardError(id, msg) {
    const sh = shells[id];
    if (!sh) return;
    sh.err.textContent = msg;
    sh.err.hidden = !msg;
  }
  function cardCreate(card, o) {
    if (!card || !card.id) return;
    const existed = shells[card.id];
    state.cards[card.id] = clone(card);
    state.lastTouched = card.type;
    if (existed) { cardUpdate(card.id, card, o, true); return; }
    state.order.push(card.id);
    hideTyping();
    const node = buildCard(card, o);
    $("ch-stream").appendChild(node);
    syncCases();
    emit("card:create", state.cards[card.id]);
    if (!o.replay) announce((card.title || card.type) + (card.summary ? ": " + card.summary : ""));
  }
  function cardUpdate(id, patch, o, whole) {
    const sh = shells[id];
    if (!sh) return;
    const card = whole ? state.cards[id] : (state.cards[id] = mergePatch(state.cards[id], patch));
    state.lastTouched = card.type;
    const def = defFor(card.type);
    if (sh.err && !sh.err.hidden) showCardError(id, "");
    refreshShell(sh, card, !o.replay);
    try { if (def.update) def.update(sh.body, card, patch); } catch (e) { console.error("card update failed", card.type, e); showCardError(id, "This card could not be updated. Reload the page to redraw it."); }
    syncCases();
    emit("card:patch", card, patch);
  }
  function syncCases() {
    const t = state.cards.tests;
    state.cases = t && Array.isArray(t.rows) ? t.rows : state.cases;
  }

  /* ================================================================ messages */
  function paragraphs(text) {
    const parts = String(text == null ? "" : text).split(/\n{2,}/);
    return parts.map(p => el("p", { text: p.trim() }));
  }
  function hideTyping() { const t = $("ch-typing"); if (t) t.remove(); }
  function showTyping() {
    if ($("ch-typing")) { $("ch-stream").appendChild($("ch-typing")); return; }
    $("ch-stream").appendChild(el("div", { class: "ch-typing", id: "ch-typing", role: "presentation" }, el("i"), el("i"), el("i"), el("span", { class: "ch-sr", text: "Working on it" })));
    follow(true);
  }
  function addUserBubble(text, cmid, o) {
    const node = el("div", { class: "ch-msg ch-msg--user" + (o && o.replay ? " ch-nostagger" : ""), dataset: { cmid: cmid || "" } }, text);
    $("ch-stream").appendChild(node);
    return node;
  }
  function onMessage(ev, o) {
    if (ev.role === "user") {
      let idx = -1;
      if (ev.client_msg_id) idx = pending.findIndex(p => p.cmid === ev.client_msg_id);
      if (idx < 0) idx = pending.findIndex(p => p.text === ev.text && !ev.client_msg_id);
      if (idx >= 0) {
        const p = pending.splice(idx, 1)[0];
        p.node.removeAttribute("data-pending");
        p.node.dataset.mid = ev.id || "";
        state.messages++;
        if (state.title === "New chat" && ev.text) setTitle(ev.text.replace(/\s+/g, " ").slice(0, 48), false);
        showTyping();
        emit("message", ev);
        return;
      }
      addUserBubble(ev.text, ev.client_msg_id, o).dataset.mid = ev.id || "";
      state.messages++;
      if (state.title === "New chat" && ev.text) setTitle(ev.text.replace(/\s+/g, " ").slice(0, 48), false);
      if (!o.replay) showTyping();
      emit("message", ev);
      return;
    }
    hideTyping();
    state.messages++;
    const kind = ev.kind || "say";
    let node;
    if (kind === "read_as") {
      node = el("div", { class: "ch-msg ch-msg--read" }, iconEl("info"), el("span", { text: ev.text }));
      if (ev.undo) {
        const b = el("button", { type: "button", class: "ch-linkbtn" }, iconEl("undo"), "Undo");
        b.addEventListener("click", () => { b.disabled = true; runAction(Object.assign({ label: "Undo" }, ev.undo, { enabled: true })); });
        node.appendChild(b);
      }
    } else {
      node = el("div", { class: "ch-msg ch-msg--bot" + (kind === "note" ? " ch-msg--note" : "") }, paragraphs(ev.text));
      if (ev.link && ev.link.chat) {
        const b = el("button", { type: "button", class: "ch-linkbtn" }, iconEl("link"), "Open the new chat");
        b.addEventListener("click", () => switchChat(ev.link.chat));
        node.appendChild(b);
      }
      if (Array.isArray(ev.chips) && ev.chips.length) node.appendChild(el("div", { class: "ch-msg-chips" }, ev.chips.map(chipEl)));
    }
    if (o.replay) node.classList.add("ch-nostagger");
    $("ch-stream").appendChild(node);
    if (!o.replay) announce(ev.text);
    emit("message", ev);
  }

  /* ================================================================ event folding */
  let batchN = 0;
  function fold(ev, o) {
    switch (ev.type) {
      case "message": onMessage(ev, o); break;
      case "card.create": cardCreate(ev.card, Object.assign({}, o, { delay: o.replay ? 0 : Math.min(batchN++, 5) * 40 })); break;
      case "card.patch": cardUpdate(ev.id, ev.patch || {}, o, false); break;
      case "chips": hideTyping(); state.chips = Array.isArray(ev.chips) ? ev.chips : []; renderChips(); emit("chips", state.chips); break;
      case "dock": {
        const d = Object.assign({}, ev); delete d.type; delete d.seq; delete d.ts;
        state.dock = d; dockAt = performance.now(); renderDock(); recomputeBusy(); emit("dock", state.dock); break;
      }
      case "job": {
        const j = ev.job || {};
        if (j.id) {
          state.jobs[j.id] = j;
          if (j.kind === "tests" && j.state === "running" && !jobStart[j.id]) jobStart[j.id] = performance.now();
          if (j.kind === "tests" && j.state === "running" && !o.replay && j.done !== lastAnnounced) { lastAnnounced = j.done; announceTests(j); }
        }
        renderDock(); recomputeBusy(); emit("job", j); break;
      }
      case "budget": state.budget = ev.ai || ev.budget || {}; renderAI(); emit("budget", state.budget); break;
      case "limits": {
        const l = Object.assign({}, ev); delete l.type; delete l.seq; delete l.ts;
        state.limits = l; renderLimits(); emit("limits", state.limits); break;
      }
      case "error": {
        hideTyping();
        if (!o.replay) {
          if (ev.card && shells[ev.card]) { showCardError(ev.card, ev.message || "Something went wrong."); announce(ev.message); }
          else toast(ev.message || "Something went wrong.", "error");
        }
        emit("error", ev); break;
      }
      default: break;
    }
  }
  let lastAnnounced = -1, lastAnnounceAt = 0;
  function announceTests(j) {
    const now = Date.now();
    if (now - lastAnnounceAt < 4000) return;
    lastAnnounceAt = now;
    announce("Tests running, " + Math.min(j.done + 1, j.total) + " of " + j.total);
  }
  function applyEvents(list, o) {
    o = Object.assign({ replay: state.replaying }, o || {});
    batchN = 0;
    const stickBefore = stick;
    const lastCardIdx = (() => { let li = -1; (list || []).forEach((e, i) => { if (e.type === "card.create") li = i; }); return li; })();
    (list || []).forEach((ev, i) => {
      if (!ev || !ev.type) return;
      if (ev.seq != null) { if (ev.seq <= state.seq) return; state.seq = ev.seq; }
      state.events.push(ev);
      try { fold(ev, Object.assign({}, o, { isLast: o.replay ? i === lastCardIdx : true })); } catch (e) { console.error("event failed", ev, e); }
    });
    updateMode();
    renderComposerState();
    if (o.replay) { stick = true; scrollBottom(true); } else if (stickBefore) follow();
  }
  function recomputeBusy() {
    const d = state.dock || {}, b = d.busy || {};
    const runningJob = Object.keys(state.jobs).some(k => state.jobs[k].state === "running");
    state.busy = !!(b.plan || b.sweep || b.live || runningJob || (d.ai && d.ai.running) || (d.steps || []).some(s => s.state === "running") || state.sending > 0);
    document.body.dataset.busy = String(state.busy);
    renderComposerState();
  }
  function stopTargets() {
    const d = state.dock || {}, b = d.busy || {}, t = [];
    if (b.plan || (d.ai && d.ai.running)) t.push({ job: "plan", label: "Stop AI plan" });
    if (b.sweep || runningTests()) t.push({ job: "tests", label: "Stop tests" });
    if (b.live) t.push({ job: "live", label: "Stop simulation" });
    return t;
  }
  function runningTests() { const k = Object.keys(state.jobs).find(x => state.jobs[x].kind === "tests" && state.jobs[x].state === "running"); return k ? state.jobs[k] : null; }

  /* ================================================================ scrolling */
  const scroller = () => $("ch-scroll");
  let stick = true, autoUntil = 0;
  const atBottom = (px) => { const s = scroller(); return s.scrollHeight - s.scrollTop - s.clientHeight <= (px == null ? 48 : px); };
  function scrollBottom(instant) {
    const s = scroller();
    autoUntil = performance.now() + (instant || reduced() ? 120 : 520);
    s.scrollTo({ top: s.scrollHeight, behavior: instant || reduced() ? "auto" : "smooth" });
    $("ch-jump").hidden = true;
  }
  function follow(smooth) {
    if (stick) { if (smooth) scrollBottom(false); else { const s = scroller(); autoUntil = performance.now() + 120; s.scrollTop = s.scrollHeight; } }
    else if (!atBottom()) $("ch-jump").hidden = false;
  }
  function wireScroll() {
    const s = scroller();
    s.addEventListener("scroll", () => {
      if (performance.now() < autoUntil) return;
      stick = atBottom();
      $("ch-jump").hidden = stick;
    }, { passive: true });
    const jump = $("ch-jump");
    jump.innerHTML = icon("down") + "<span>Jump to latest</span>";
    jump.addEventListener("click", () => { stick = true; scrollBottom(false); });
    if (window.ResizeObserver) {
      new ResizeObserver(() => follow(false)).observe($("ch-stream"));
      new ResizeObserver(es => document.documentElement.style.setProperty("--ch-foot-h", Math.round(es[0].target.getBoundingClientRect().height) + "px")).observe($("ch-foot"));
    }
  }

  /* ================================================================ mode (empty state <-> chat) */
  function hasContent() { return state.order.length > 0 || state.messages > 0 || pending.length > 0; }
  function updateMode() {
    const want = hasContent() ? "chat" : "empty";
    if (document.body.dataset.state !== want) setMode(want);
    renderLimits();
  }
  function setMode(want) {
    const foot = $("ch-foot");
    const before = foot.getBoundingClientRect().top;
    document.body.dataset.state = want;
    const after = foot.getBoundingClientRect().top;
    if (want === "chat" && !state.replaying && !reduced() && before !== after && Math.abs(before - after) > 4) {
      foot.style.setProperty("--ch-dy", (before - after) + "px");
      foot.classList.remove("ch-slide");
      void foot.offsetWidth;
      foot.classList.add("ch-slide");
      foot.addEventListener("animationend", () => foot.classList.remove("ch-slide"), { once: true });
    }
    if (want === "chat") stick = true;
  }

  /* ================================================================ dock */
  let dockAt = 0, dockOpen = false, liveStart = 0, tick = 0;
  const jobStart = {};
  function dockModel() {
    const d = state.dock || {}, steps = d.steps || [], ai = d.ai || {}, q = d.queue || [], b = d.busy || {};
    const tj = runningTests();
    const stepRun = steps.find(s => s.state === "running");
    const stepIdx = stepRun ? steps.indexOf(stepRun) + 1 : 0;
    const pipelineActive = steps.length > 0 && !steps.every(s => s.state === "done" || s.state === "skipped");
    const visible = !!(pipelineActive || ai.running || b.live || tj || q.length);
    return { d, steps, ai, q, b, tj, stepRun, stepIdx, pipelineActive, visible };
  }
  function renderDock() {
    const m = dockModel();
    const dock = $("ch-dock");
    dock.hidden = !m.visible || document.body.dataset.state === "empty";
    dock.dataset.live = String(!!(m.stepRun || m.tj || m.ai.running || m.b.live));
    if (m.b.live && !liveStart) liveStart = performance.now();
    if (!m.b.live) liveStart = 0;
    let text = "Working";
    if (m.tj) text = "Tests: condition " + Math.min((m.tj.done || 0) + 1, m.tj.total || 1) + " of " + (m.tj.total || "?");
    else if (m.stepRun) text = m.stepRun.label + (m.stepRun.detail ? ": " + m.stepRun.detail : "");
    else if (m.pipelineActive) { const p = m.steps.find(s => s.state === "pending" || s.state === "waiting"); text = p ? p.label + ": waiting" : "Working"; }
    else if (m.ai.running) text = "AI planning" + (m.ai.phase ? ": " + m.ai.phase : "");
    else if (m.b.live) text = "Watching a run";
    else if (m.q.length) text = "Waiting for another job to finish";
    $("ch-dock-text").textContent = text;
    // the AI lane line
    const aiLine = $("ch-dock-ai");
    aiLine.hidden = !m.ai.running || !!(!m.tj && !m.stepRun && !m.pipelineActive);
    // progress: real only for counted work (tests), indeterminate for anything else that runs
    const prog = $("ch-dock-prog");
    if (m.tj && m.tj.total) { prog.hidden = false; prog.classList.remove("ch-prog--indet"); prog.style.setProperty("--ch-p", Math.min(100, 100 * (m.tj.done || 0) / m.tj.total) + "%"); prog.setAttribute("role", "progressbar"); prog.setAttribute("aria-valuemin", "0"); prog.setAttribute("aria-valuemax", String(m.tj.total)); prog.setAttribute("aria-valuenow", String(m.tj.done || 0)); prog.setAttribute("aria-label", "Tests progress"); }
    else if (m.stepRun || m.ai.running) { prog.hidden = false; prog.classList.add("ch-prog--indet"); prog.removeAttribute("aria-valuenow"); prog.setAttribute("role", "progressbar"); prog.setAttribute("aria-label", "Working"); prog.style.removeProperty("--ch-p"); }
    else prog.hidden = true;
    // steps list
    const list = $("ch-dock-steps");
    list.replaceChildren();
    m.steps.forEach(s => {
      const mark = el("span", { class: "ch-step", dataset: { s: s.state } });
      if (s.state === "done") mark.innerHTML = icon("check");
      list.appendChild(el("li", {}, mark, el("span", { text: s.label }), s.detail ? el("small", { text: s.detail }) : null, el("span", { class: "ch-sr", text: " " + (s.state === "done" ? "done" : s.state === "running" ? "in progress" : s.state === "skipped" ? "skipped" : "waiting") })));
    });
    list.hidden = !dockOpen || !m.steps.length;
    $("ch-dock-main").setAttribute("aria-expanded", String(dockOpen && m.steps.length > 0));
    $("ch-dock-chev").style.visibility = m.steps.length ? "visible" : "hidden";
    // stop button
    const targets = stopTargets();
    const sb = $("ch-dock-stop");
    sb.hidden = !targets.length;
    sb.replaceChildren(iconEl("stop"), document.createTextNode(targets.length > 1 ? "Stop..." : "Stop"));
    // queue
    const up = $("ch-upnext");
    up.replaceChildren();
    up.hidden = !m.q.length || document.body.dataset.state === "empty";
    if (m.q.length) {
      up.appendChild(el("span", { text: "Up next" }));
      m.q.forEach(it => {
        const x = el("button", { type: "button", "aria-label": "Remove from the queue: " + it.label });
        x.innerHTML = icon("close");
        x.addEventListener("click", () => { if (it.cancel) runAction(Object.assign({ label: "Remove from queue" }, it.cancel, { enabled: true })); });
        up.appendChild(el("span", { class: "ch-queued" }, el("span", { text: it.label + (it.position ? " (" + it.position + ")" : "") }), x));
      });
    }
    clearInterval(tick);
    if (m.visible) tick = setInterval(updateDockTimes, 1000);
    updateDockTimes();
    renderComposerState();
  }
  function updateDockTimes() {
    const m = dockModel();
    const now = performance.now();
    const since = (now - dockAt) / 1000;
    let t = "";
    if (m.tj) t = clock((now - (jobStart[m.tj.id] || now)) / 1000);
    else if (m.stepRun) t = "Step " + m.stepIdx + "/" + m.steps.length + " · " + clock((m.d.elapsed || 0) + since);
    else if (m.pipelineActive && m.d.elapsed != null) t = clock((m.d.elapsed || 0) + since);
    else if (m.ai.running) t = clock((m.ai.seconds || 0) + since);
    else if (m.b.live && liveStart) t = clock((now - liveStart) / 1000);
    $("ch-dock-time").textContent = t;
    if (m.ai.running) $("ch-dock-ai").textContent = "AI planning " + clock((m.ai.seconds || 0) + since) + ", your draft stays usable";
  }

  /* ================================================================ chips, limits, AI chip */
  function renderChips() {
    const box = $("ch-chips");
    box.replaceChildren();
    (state.chips || []).forEach(c => box.appendChild(chipEl(c)));
  }
  const EMPTY_LIMITS = "Simulates ground rovers, boats and observing drones. A good result here does not show a machine works in the real world.";
  function limitsText() {
    if (document.body.dataset.state === "empty") return (state.caps && state.caps.copy && state.caps.copy.limits) || EMPTY_LIMITS;
    const l = state.limits || {};
    let text = l.line;
    if (!text) text = l.count == null ? "Simulation only. Simple built-in autopilot. Nothing tested yet." : "Simulation only. Simple built-in autopilot. " + l.count + (l.count === 1 ? " thing" : " things") + " not tested.";
    if (l.stale) text += " Some results are out of date.";
    return text;
  }
  function renderLimits() {
    const b = $("ch-limits");
    const stale = !!(state.limits && state.limits.stale) && document.body.dataset.state !== "empty";
    b.dataset.stale = String(stale);
    b.replaceChildren(iconEl(stale ? "warn" : "info"), el("span", { text: limitsText() }));
    b.setAttribute("aria-label", limitsText() + " Open what I can't test.");
    b.tabIndex = document.body.dataset.state === "empty" ? -1 : 0;
  }
  function renderAI() {
    const b = state.budget || {}, chip = $("ch-aichip");
    if (!b.configured) { chip.hidden = true; return; }
    const left = b.left != null ? b.left : b.free_left;
    let text = "";
    if (b.setting === "never") text = "AI is off";
    else if (b.exhausted || left === 0) text = "AI used up today, resets 00:00 UTC";
    else if (left != null) text = "AI about " + left + " left today, approx.";
    else text = "AI ready";
    chip.hidden = false;
    chip.dataset.low = String(!!(b.low || b.exhausted));
    chip.replaceChildren(iconEl("ai"), el("span", { text }));
    chip.title = "Free AI models allow about 50 requests a day, shared by everything using the key. The count is approximate.";
  }
  function emptyChipList() {
    const ex = state.caps && Array.isArray(state.caps.examples) && state.caps.examples.length ? state.caps.examples : [
      { id: "flood_boat", label: "Boat that finds a person in a flooded street", text: "A boat that finds a person in a flooded street" },
      { id: "sewer_rover", label: "Rover that inspects a 30 cm sewer pipe", text: "A rover that inspects a 30 cm sewer pipe" },
      { id: "field_drone", label: "Drone that looks over a field for 10 minutes", text: "A drone that looks over a field for 10 minutes" },
      { id: "own_parts", label: "I have my own parts", kit: "my_rover" }];
    const out = ex.map(e => ({ id: "ex:" + e.id, label: e.label, tag: "instant", action: e.kit ? { type: "use_kit", args: { kit: e.kit } } : { type: "new_job", args: { text: e.text } } }));
    out.push({ id: "recent", label: "Recent chats", local: () => openDrawer() });
    return out;
  }
  function renderEmptyChips() {
    const box = $("ch-empty-chips");
    box.replaceChildren();
    emptyChipList().forEach(c => box.appendChild(chipEl(c)));
    const copy = state.caps && state.caps.copy;
    if (copy) {
      if (copy.headline) $("ch-hero-h").textContent = copy.headline;
      if (copy.line) $("ch-hero-p").textContent = copy.line;
      if (copy.placeholder) $("ch-input").placeholder = copy.placeholder;
    }
  }

  /* ================================================================ composer */
  const input = () => $("ch-input");
  function autosize() {
    const t = input();
    t.style.height = "auto";
    t.style.height = Math.min(t.scrollHeight, 168) + "px";
  }
  function renderComposerState() {
    const hasText = input().value.trim().length > 0;
    const targets = stopTargets();
    const showStop = targets.length > 0 && !hasText;
    $("ch-stop").hidden = !showStop;
    $("ch-send").hidden = showStop;
    $("ch-send").disabled = !hasText;
  }
  function focusComposer() { const t = input(); t.focus(); t.setSelectionRange(t.value.length, t.value.length); }
  function submitComposer() {
    const text = input().value.trim();
    if (!text) return;
    input().value = "";
    autosize();
    renderComposerState();
    sendText(text);
  }
  function stopFlow(anchor) {
    const t = stopTargets();
    if (!t.length) return;
    if (t.length === 1) { send({ type: "stop", args: { job: t[0].job } }, { label: t[0].label }); return; }
    const menu = $("ch-stopmenu");
    menu.replaceChildren();
    t.concat([{ job: "all", label: "Stop all" }]).forEach(x => {
      const b = el("button", { type: "button", role: "menuitem", class: "ch-menu-item" }, iconEl("stop"), x.label);
      b.addEventListener("click", () => {
        closeStopMenu();
        if (x.job === "all") t.forEach(y => send({ type: "stop", args: { job: y.job } }, { label: y.label }));
        else send({ type: "stop", args: { job: x.job } }, { label: x.label });
      });
      menu.appendChild(b);
    });
    menu.hidden = false;
    layers.push("stopmenu", closeStopMenu);
    menu.querySelector("button").focus();
  }
  function closeStopMenu() { $("ch-stopmenu").hidden = true; layers.remove("stopmenu"); }

  /* ================================================================ sending */
  let creating = null;
  async function ensureChat() {
    if (state.chatId) return state.chatId;
    if (!creating) {
      creating = SimChat.net.newChat().then(r => { setChat(r.id); if (r.title) setTitle(r.title, false); startLoop(r.id); return r.id; }).finally(() => { creating = null; });
    }
    return creating;
  }
  function setChat(id) {
    const changed = state.chatId !== id;
    state.chatId = id;
    if (id) {
      LS.set("simlab.chat.id", id);
      try { history.replaceState(null, "", location.pathname + location.search + "#p/" + id); } catch (e) { /* ignore */ }
    } else {
      LS.del("simlab.chat.id");
      try { history.replaceState(null, "", location.pathname + location.search); } catch (e) { /* ignore */ }
    }
    if (changed) emit("chat", { id });
  }
  function send(action, opts) {
    opts = opts || {};
    const shown = opts.label || (action && (action.label || action.type)) || "";
    return doSend({ action: { type: action.type, args: action.args || {} }, label: opts.label || action.label || null }, shown);
  }
  function sendText(text) { return doSend({ text }, text); }
  async function doSend(body, shown, retryNode) {
    const cmid = body.client_msg_id || "c" + uid();                                 // a retry reuses the id, so the server never runs the turn twice
    body.client_msg_id = cmid;
    if (body.label == null) delete body.label;
    let node = retryNode;
    if (node) { node.dataset.pending = "true"; node.removeAttribute("data-failed"); const f = node.querySelector(".ch-msg-fail"); if (f) f.remove(); node.dataset.cmid = cmid; }
    else {
      node = addUserBubble(shown, cmid, {});
      node.dataset.pending = "true";
      pending.push({ cmid, text: shown, node });
    }
    const pend = pending.find(p => p.node === node);
    if (pend) pend.cmid = cmid; else pending.push({ cmid, text: shown, node });
    state.sending++;
    stick = true;
    updateMode();
    recomputeBusy();
    follow(true);
    try {
      await ensureChat();
      const r = await SimChat.net.send(state.chatId, body);
      if (r && r.new_chat) setTimeout(() => switchChat(r.new_chat), 400);
      setTimeout(() => { if (pending.some(p => p.node === node)) showTyping(); }, 250);
      return r;
    } catch (e) {
      const i = pending.findIndex(p => p.node === node);
      if (i >= 0) pending.splice(i, 1);
      node.dataset.failed = "true";
      node.removeAttribute("data-pending");
      const retry = el("button", { type: "button", class: "ch-linkbtn" }, iconEl("refresh"), "Try again");
      retry.addEventListener("click", () => doSend(body, shown, node));
      node.appendChild(el("div", { class: "ch-msg-fail" }, el("span", { text: "Not sent. " + (e && e.message ? e.message : "") }), retry));
      toast("That did not go through. " + (e && e.message ? e.message : ""), "error");
      hideTyping();
      return null;
    } finally {
      state.sending = Math.max(0, state.sending - 1);
      recomputeBusy();
    }
  }

  /* ================================================================ poll loop */
  let loop = null;
  function startLoop(id) {
    stopLoop();
    const my = { id, ctl: new AbortController() };
    loop = my;
    state.seq = 0;
    state.replaying = true;
    runLoop(my);
  }
  function stopLoop() { if (loop) { loop.ctl.abort(); loop = null; } }
  function setConn(ok) {
    state.connected = ok;
    const bar = $("ch-reconnect");
    bar.hidden = ok;
    if (!ok) bar.replaceChildren(iconEl("refresh"), document.createTextNode("Reconnecting..."));
  }
  async function runLoop(my) {
    let backoff = 600, quick = true, fails = 0;
    while (loop === my) {
      try {
        const r = await SimChat.net.poll(my.id, state.seq, quick ? 0 : 25, my.ctl.signal);
        if (loop !== my) return;
        fails = 0; backoff = 600;
        if (!state.connected) setConn(true);
        if (r && r.reset) { clearView(true); state.seq = 0; state.replaying = true; quick = true; continue; }
        const evs = (r && r.events) || [];
        if (evs.length) applyEvents(evs, { replay: state.replaying });
        if (r && r.last != null) state.seq = Math.max(state.seq, r.last);
        if (!(r && r.more)) { if (state.replaying) finishReplay(); quick = false; }
      } catch (e) {
        if (loop !== my || (e && e.name === "AbortError")) return;
        if (e && e.status === 404) { chatGone(); return; }
        fails++;
        quick = true;                                                              // after a failure the next request is a quick probe, so recovery shows at once
        setConn(false);
        await sleep(backoff, my.ctl.signal);
        backoff = Math.min(backoff * 2, 8000);
      }
    }
  }
  function finishReplay() {
    state.replaying = false;
    document.body.removeAttribute("data-restoring");
    stick = true;
    scrollBottom(true);
    if (!hasContent()) { /* an empty chat: stay on the welcome screen */ }
    emit("replayed", { events: state.events.length });
  }
  function chatGone() {
    const restoring = state.events.length === 0;
    document.body.removeAttribute("data-restoring");
    setChat(null);
    clearView(true);
    if (!restoring) toast("That chat is no longer on this machine. Start a new one below.", "error");
  }

  /* ================================================================ chats */
  function clearView(keepId) {
    $("ch-stream").replaceChildren();
    Object.keys(shells).forEach(k => delete shells[k]);
    Object.keys(openPref).forEach(k => delete openPref[k]);
    Object.keys(jobStart).forEach(k => delete jobStart[k]);
    Object.assign(state, { events: [], cards: {}, order: [], chips: [], dock: {}, limits: { count: null, stale: false, line: null }, cases: [], jobs: {}, messages: 0, busy: false, sending: 0, lastTouched: null });
    pending = [];
    state.seq = 0;
    setTitle("New chat", false);
    renderChips(); renderDock(); renderLimits(); renderComposerState(); updateMode();
    setConn(true);
    emit("reset", { keepId: !!keepId });
  }
  function switchChat(id) {
    if (!id) return;
    stopLoop();
    clearView(true);
    setChat(id);
    startLoop(id);
  }
  async function newChat() {
    stopLoop();
    clearView(false);
    setChat(null);
    try {
      const r = await SimChat.net.newChat();
      setChat(r.id);
      startLoop(r.id);
    } catch (e) { toast("Could not start a new chat. " + (e && e.message ? e.message : ""), "error"); }
    focusComposer();
  }
  function setTitle(t, remote) {
    state.title = t || "New chat";
    const i = $("ch-title");
    if (i && i.value !== state.title) i.value = state.title;
    document.title = (state.title === "New chat" ? "" : state.title + " - ") + "simlab";
    if (remote && state.chatId) { Promise.resolve().then(() => SimChat.net.rename(state.chatId, state.title)).catch(() => { /* the server may not support renaming yet */ }); }
  }

  /* ================================================================ menus, settings, theme (the sidebar and the panes live in chat-shell.js) */
  function openDrawer() { if (SimChat.shell) SimChat.shell.openSidebar({ search: true }); }
  function closePops(except) {
    ["ch-menu", "ch-settings"].forEach(id => {
      if (id === except) return;
      const p = $(id);
      if (!p.hidden) { p.hidden = true; layers.remove(id); if (id === "ch-menu") $("ch-menu-btn").setAttribute("aria-expanded", "false"); }
    });
    if (!$("ch-stopmenu").hidden && except !== "ch-stopmenu") closeStopMenu();
  }
  function togglePop(id, btn) {
    const p = $(id);
    if (!p.hidden) { closePops(); if (btn) btn.focus(); return; }
    closePops(id);
    p.hidden = false;
    if (id === "ch-menu") $("ch-menu-btn").setAttribute("aria-expanded", "true");
    layers.push(id, () => { p.hidden = true; if (id === "ch-menu") $("ch-menu-btn").setAttribute("aria-expanded", "false"); if (btn) btn.focus(); });
    const first = p.querySelector("button, a, input");
    if (first) first.focus();
  }
  function buildMenu() {
    const m = $("ch-menu");
    m.replaceChildren();
    const a = el("a", { class: "ch-menu-item", role: "menuitem", href: "/" }, iconEl("link"), "Advanced tools");
    const s = el("button", { type: "button", class: "ch-menu-item", role: "menuitem" }, iconEl("settings"), "Settings");
    const n = el("button", { type: "button", class: "ch-menu-item", role: "menuitem" }, iconEl("plus"), "New chat");
    s.addEventListener("click", () => { closePops(); togglePop("ch-settings", $("ch-menu-btn")); });
    n.addEventListener("click", () => { closePops(); newChat(); });
    m.append(a, s, n);
    m.addEventListener("keydown", e => {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      const items = Array.from(m.querySelectorAll("[role=menuitem]"));
      const i = items.indexOf(document.activeElement);
      e.preventDefault();
      items[(i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length].focus();
    });
  }
  function radio(name, value, label, note, checked, onchange) {
    const i = el("input", { type: "radio", name, value, checked: checked ? true : null });
    i.addEventListener("change", () => onchange(value));
    return el("label", { class: "ch-radio" }, i, el("span", {}, label, note ? el("small", { text: note }) : null));
  }
  function buildSettings() {
    const p = $("ch-settings");
    p.replaceChildren();
    const ai = el("fieldset", {}, el("legend", { text: "AI use" }),
      radio("ch-ai", "ask", "Ask me first", "Shows the cost before it uses a request", settings.ai === "ask", v => { settings.ai = v; LS.set("simlab.chat.ai", v); emit("settings", Object.assign({}, settings)); }),
      radio("ch-ai", "auto", "Automatic", "Plans new jobs with the AI after a short countdown", settings.ai === "auto", v => { settings.ai = v; LS.set("simlab.chat.ai", v); emit("settings", Object.assign({}, settings)); }),
      radio("ch-ai", "never", "Never", "Everything stays on this machine", settings.ai === "never", v => { settings.ai = v; LS.set("simlab.chat.ai", v); emit("settings", Object.assign({}, settings)); }));
    const th = el("fieldset", {}, el("legend", { text: "Theme" }),
      radio("ch-th", "auto", "Match my system", null, settings.theme === "auto", setTheme),
      radio("ch-th", "light", "Light", null, settings.theme === "light", setTheme),
      radio("ch-th", "dark", "Dark", null, settings.theme === "dark", setTheme));
    p.append(el("h2", { text: "Settings" }), ai, th, el("p", { class: "ch-note", text: "These choices are saved on this device. Nothing is sent to an AI unless you start it." }));
  }
  function effectiveTheme() {
    const t = document.documentElement.getAttribute("data-theme");
    if (t === "light" || t === "dark") return t;
    return window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }
  function setTheme(mode) {
    settings.theme = mode;
    if (mode === "light" || mode === "dark") { document.documentElement.setAttribute("data-theme", mode); LS.set("simlab.chat.theme", mode); }
    else { document.documentElement.removeAttribute("data-theme"); LS.del("simlab.chat.theme"); }
    paintTheme();
    emit("theme", effectiveTheme());
  }
  function paintTheme() {
    const eff = effectiveTheme();
    const b = $("ch-theme-btn");
    b.innerHTML = icon(eff === "dark" ? "sun" : "moon");
    b.setAttribute("aria-label", eff === "dark" ? "Switch to the light theme" : "Switch to the dark theme");
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", eff === "dark" ? "#0d1218" : "#f6f7f9");
  }

  /* ================================================================ keyboard (one handler, guarded: nothing here fires while typing except Esc and Ctrl/Cmd+K) */
  function onKeydown(e) {
    const typing = isEditable(e.target);
    if (e.key === "Escape") { if (layers.closeTop()) e.preventDefault(); return; }
    if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === "k" || e.key === "K")) { e.preventDefault(); focusComposer(); return; }
    if (typing || e.altKey) return;
    if (e.key === "/" && !e.ctrlKey && !e.metaKey) { e.preventDefault(); focusComposer(); return; }
    if ((e.ctrlKey || e.metaKey) && (e.key === "z" || e.key === "Z") && state.cards.tests) {
      e.preventDefault();
      send({ type: e.shiftKey ? "redo" : "undo", args: {} }, { label: e.shiftKey ? "Redo" : "Undo" });
    } else if ((e.ctrlKey || e.metaKey) && (e.key === "y" || e.key === "Y") && state.cards.tests) {
      e.preventDefault();
      send({ type: "redo", args: {} }, { label: "Redo" });
    }
  }

  /* ================================================================ boot */
  function wire() {
    $("ch-menu-btn").innerHTML = icon("menu");
    $("ch-send").innerHTML = icon("send");
    $("ch-stop").innerHTML = icon("stop");
    $("ch-stop").setAttribute("aria-label", "Stop");
    $("ch-dock-chev").innerHTML = icon("chevron");
    $("ch-theme-btn").addEventListener("click", () => setTheme(effectiveTheme() === "dark" ? "light" : "dark"));
    $("ch-menu-btn").addEventListener("click", e => { e.stopPropagation(); togglePop("ch-menu", $("ch-menu-btn")); });
    document.addEventListener("click", e => { if (!e.target.closest(".ch-menuwrap") && !e.target.closest(".ch-stopmenu") && !e.target.closest("#ch-dock-stop") && !e.target.closest("#ch-stop")) closePops(); });
    $("ch-settings").addEventListener("click", e => e.stopPropagation());
    $("ch-limits").addEventListener("click", () => { if (document.body.dataset.state !== "empty") send({ type: "answer", args: { topic: "limits" } }, { label: "What can't you test?" }); });
    $("ch-dock-main").addEventListener("click", () => { if (!dockModel().steps.length) return; dockOpen = !dockOpen; renderDock(); });
    $("ch-dock-stop").addEventListener("click", () => stopFlow($("ch-dock-stop")));
    $("ch-stop").addEventListener("click", () => stopFlow($("ch-stop")));
    $("ch-dock-slot").addEventListener("click", () => { if (SimChat.live && typeof SimChat.live.focus === "function") SimChat.live.focus(); });
    const t = input();
    t.addEventListener("input", () => { autosize(); renderComposerState(); });
    t.addEventListener("keydown", e => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); submitComposer(); }
    });
    $("ch-composer").addEventListener("submit", e => { e.preventDefault(); submitComposer(); });
    const title = $("ch-title");
    title.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); title.blur(); } if (e.key === "Escape") { title.value = state.title; title.blur(); } });
    title.addEventListener("blur", () => { const v = title.value.trim() || "New chat"; if (v !== state.title) setTitle(v, true); else title.value = v; });
    document.addEventListener("keydown", onKeydown);
    document.addEventListener("visibilitychange", () => { if (!document.hidden && !state.connected) wakeSleepers(); });
    window.addEventListener("online", wakeSleepers);
    window.addEventListener("offline", () => setConn(false));
    if (window.matchMedia) { const mq = matchMedia("(prefers-color-scheme: light)"); const h = () => { if (settings.theme === "auto") paintTheme(); }; if (mq.addEventListener) mq.addEventListener("change", h); }
    wireScroll();
    if (window.ResizeObserver) { let w = 0; new ResizeObserver(es => { const nw = Math.round(es[0].contentRect.width); if (nw !== w) { w = nw; autosize(); } }).observe($("ch-composer")); }       // the box was sized before the columns settled: measure again when its width changes
    buildMenu();
    buildSettings();
    paintTheme();
    renderEmptyChips();
    renderLimits();
    renderAI();
    renderDock();
    renderComposerState();
    autosize();
  }
  function boot() {
    wire();
    SimChat.net.capabilities().then(c => { state.caps = c; renderEmptyChips(); renderLimits(); if (c && c.ai && !Object.keys(state.budget).length) { state.budget = c.ai; renderAI(); } }).catch(() => { /* optional */ });
    let id = null;
    const m = /^#p\/([\w-]+)/.exec(location.hash || "");
    if (m) id = m[1];
    if (!id) id = LS.get("simlab.chat.id");
    if (id) {
      document.body.dataset.restoring = "true";                                    // no flash of the welcome screen while the old chat loads
      setTimeout(() => document.body.removeAttribute("data-restoring"), 2500);
      setChat(id); startLoop(id);
    } else { updateMode(); focusComposer(); }
    emit("boot", { chatId: state.chatId });
  }

  /* ================================================================ the public object */
  const SimChat = {
    util, state, net, settings, layers, fixtures: {}, shell: null, panes: null, sheet: null, live: null, mock: null,
    dock: { slot: $("ch-dock-slot") },
    stream: $("ch-stream"),
    on, emit, registerCard, send, sendText, applyEvents, runAction, actionButtons, chipEl, openSheet,
    cardEl: id => (shells[id] ? shells[id].root : null),
    cardBody: id => (shells[id] ? shells[id].body : null),
    isOpen: id => !!(shells[id] && shells[id].open),
    setOpen: (id, open) => setOpen(id, open, false),
    showCardError, scrollToLatest: () => { stick = true; scrollBottom(false); },
    toast, announce, newChat, switchChat, setTheme, focusComposer,
    boot,
  };
  document.addEventListener("DOMContentLoaded", () => SimChat.boot());
  if (document.readyState !== "loading") setTimeout(() => SimChat.boot(), 0);
  return SimChat;
})();
