"use strict";
/* chat-shell.js: the Claude-desktop-style frame of the chat page: the left sidebar (SimChat.shell) and the right-hand dock of tabbed panes (SimChat.panes).
   Loaded right after chat-core.js, before the files that register panes. Everything is inside one IIFE (the dashboard scripts share one global scope).

   Pane registry: SimChat.panes.register({ id, label, short, icon, order, mount(body, ctx) -> {refresh?}, show(), hide(), refresh() }). A pane is built the first time it is shown;
   show/hide fire when it becomes visible or hidden (dock closed, another tab picked); refresh() is called on show and whenever SimChat.panes.refresh(id) is called while it
   is visible. Later cards register themselves the same way (the Rover and Background tasks panes below are placeholders any later file can replace by registering the same id).
   Wide screens: sidebar (>= 900 px) and panes (>= 1200 px) are columns. Narrower: both are drawers over the chat with a shared scrim, a focus trap and Esc to close.

   Sidebar data: GET /api/chat/list (the per-chat store) plus projects, which for now are kept in this browser only (localStorage "simlab.chat.projects"). */
(function () {
  const U = SimChat.util, el = U.el, $ = id => document.getElementById(id);
  const root = document.documentElement;
  const mql = q => (window.matchMedia ? window.matchMedia(q) : { matches: false, addEventListener() { } });
  const inlineSb = () => mql("(min-width: 900px)").matches;
  const inlineRp = () => mql("(min-width: 1200px)").matches;
  const LS = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* blocked storage: nothing is remembered */ } },
  };
  const layers = SimChat.layers;
  const scrim = () => $("ch-scrim");
  function setDialog(node, on, label) {
    if (on) { node.setAttribute("role", "dialog"); node.setAttribute("aria-modal", "true"); if (label) node.setAttribute("aria-label", label); }
    else { node.removeAttribute("role"); node.removeAttribute("aria-modal"); }
  }
  function syncScrim() {
    const sbOpen = !inlineSb() && root.dataset.sb === "open", rpOpen = !inlineRp() && root.dataset.rp === "open";
    scrim().hidden = !(sbOpen || rpOpen);
  }

  /* ================================================================ panes */
  const P = (SimChat.panes = {});
  const defs = [];
  const built = {};                                    // id -> {panel, body, api, failed}
  const badges = {};
  let current = null, isOpen = false, shown = null, userClosed = false, rpOpener = null, rpRelease = null;

  function register(def) {
    if (!def || !def.id) return;
    const i = defs.findIndex(d => d.id === def.id);
    if (i >= 0) defs.splice(i, 1);
    defs.push(Object.assign({ order: 100, icon: "sheet", label: def.id }, def));
    defs.sort((a, b) => a.order - b.order);
    if (built[def.id]) { built[def.id].panel.remove(); delete built[def.id]; if (shown === def.id) shown = null; }      // re-registered: build it afresh
    renderTabs();
    if (isOpen) syncVisible();
  }
  const defOf = id => defs.find(d => d.id === id) || null;
  function renderTabs() {
    const tabs = $("ch-rp-tabs");
    if (!tabs) return;
    tabs.replaceChildren();
    defs.forEach(d => {
      const on = d.id === current;
      const b = el("button", { type: "button", role: "tab", id: "ch-rp-t-" + d.id, class: "ch-rp-tab", "aria-controls": "ch-rp-p-" + d.id, "aria-selected": String(on), tabindex: on ? "0" : "-1",
        "aria-label": d.label, title: d.label, dataset: { pane: d.id } }, U.iconEl(d.icon), el("span", { class: "ch-rp-tab-t", text: d.short || d.label }));
      if (badges[d.id]) b.appendChild(el("span", { class: "ch-rp-badge", text: badges[d.id] }));
      b.addEventListener("click", () => select(d.id));
      tabs.appendChild(b);
    });
  }
  function ensureBuilt(id) {
    if (built[id]) return built[id];
    const d = defOf(id);
    const body = el("div", { class: "ch-rp-body" });
    const panel = el("div", { class: "ch-rp-panel", role: "tabpanel", id: "ch-rp-p-" + id, "aria-labelledby": "ch-rp-t-" + id, tabindex: "0", hidden: true }, body);
    $("ch-rp-panels").appendChild(panel);
    const b = (built[id] = { panel, body, api: {}, failed: false });
    try { b.api = (d.mount && d.mount(body, { panel })) || {}; }
    catch (e) { console.error("pane mount failed", id, e); b.failed = true; body.replaceChildren(el("p", { class: "ch-rp-err", text: "This pane could not be drawn. The chat still works." })); }
    return b;
  }
  function callHook(d, name) { if (d && typeof d[name] === "function") { try { d[name](); } catch (e) { console.error("pane " + name + " failed", d.id, e); } } }
  /* show/hide hooks follow what the person can actually see: the open dock's selected tab */
  function syncVisible() {
    const want = isOpen && current ? current : null;
    Array.from($("ch-rp-panels").children).forEach(p => { p.hidden = !(want && p.id === "ch-rp-p-" + want); });
    if (shown !== want) {
      if (shown) callHook(defOf(shown), "hide");
      shown = want;
      if (want) {
        const b = ensureBuilt(want);
        b.panel.hidden = false;
        callHook(defOf(want), "show");
        refreshPane(want);
      }
    }
    $("ch-overview-btn").setAttribute("aria-expanded", String(isOpen));
    $("ch-overview-btn").dataset.on = String(isOpen);
  }
  function refreshPane(id) {
    if (shown !== id) return;
    const b = built[id];
    if (b && !b.failed && b.api && typeof b.api.refresh === "function") { try { b.api.refresh(); } catch (e) { console.error("pane refresh failed", id, e); } }
    const d = defOf(id);
    if (d && typeof d.refresh === "function") callHook(d, "refresh");
  }
  function select(id, focus) {
    if (!defOf(id)) return;
    current = id;
    renderTabs();
    syncVisible();
    if (isOpen) LS.set("simlab.chat.rp", id);
    SimChat.emit("pane", { id, open: isOpen });
    if (focus) { const t = document.querySelector('#ch-rp-tabs [aria-selected="true"]'); if (t) t.focus(); }
  }
  function open(id, o) {
    o = o || {};
    if (id && !defOf(id)) id = null;
    if (id) current = id; else if (!current && defs.length) current = defs[0].id;
    const was = isOpen;
    isOpen = true;
    root.dataset.rp = "open";
    const rp = $("ch-rp");
    rp.inert = false;
    if (!was) {
      const overlay = !inlineRp();
      setDialog(rp, overlay, "Panes");
      if (overlay) {
        if (root.dataset.sb === "open") S.closeSidebar({ silent: true });
        rpOpener = o.auto ? null : (document.activeElement && document.activeElement !== document.body ? document.activeElement : null);
        rpRelease = U.trapFocus(rp);
        layers.push("panes", () => P.close());
      }
      syncScrim();
    }
    renderTabs();
    syncVisible();
    LS.set("simlab.chat.rp", current || "");
    SimChat.emit("pane", { id: current, open: true });
    if (!o.auto && !was) setTimeout(() => { const t = document.querySelector('#ch-rp-tabs [aria-selected="true"]'); if (t) t.focus(); }, 30);
  }
  function close(o) {
    if (!isOpen) return;
    o = o || {};
    isOpen = false;
    root.dataset.rp = "closed";
    const rp = $("ch-rp");
    rp.inert = true;
    setDialog(rp, false);
    if (rpRelease) { rpRelease(); rpRelease = null; }
    layers.remove("panes");
    syncScrim();
    syncVisible();
    if (!o.silent) { userClosed = true; LS.set("simlab.chat.rp", ""); }
    SimChat.emit("pane", { id: current, open: false });
    if (rpOpener && rpOpener.isConnected && !o.silent) rpOpener.focus();
    rpOpener = null;
  }
  Object.assign(P, {
    register, open, close, select,
    toggle() { if (isOpen) close(); else open(current); },
    isOpen: () => isOpen, current: () => current, shown: () => shown,
    list: () => defs.map(d => ({ id: d.id, label: d.label })),
    refresh: id => refreshPane(id || shown),
    setBadge(id, text) { if (text) badges[id] = String(text); else delete badges[id]; renderTabs(); },
    inline: inlineRp,
    /* the dock opens by itself (e.g. when a simulation starts) only on a wide screen and only until the person closes it */
    canAutoOpen: () => inlineRp() && !userClosed && !isOpen,
    placeholder(o) {
      return el("div", { class: "ch-rp-empty" }, U.iconEl(o.icon || "info"), el("h3", { text: o.title }), el("p", { text: o.text }), o.hint ? el("p", { class: "ch-rp-hint", text: o.hint }) : null);
    },
  });

  /* two placeholders so the dock is complete; the next step's cards register the same ids to replace them */
  register({ id: "rover", label: "Rover", icon: "platform-ground", order: 50,
    mount(body) { body.appendChild(P.placeholder({ icon: "platform-ground", title: "No rover connected", text: "Connect a rover: coming in the next step.",
      hint: "That step adds the connection, a health check, the pre-drive checklist and a Stop button here. Until then this page cannot move a real machine." })); } });
  register({ id: "tasks", label: "Background tasks", short: "Tasks", icon: "tasks", order: 60,
    mount(body) { body.appendChild(P.placeholder({ icon: "tasks", title: "Nothing listed yet", text: "Long jobs will be listed here: coming in the next step.",
      hint: "For now the progress bar above the message box shows what is running, with its Stop button." })); } });

  /* ================================================================ sidebar */
  const S = (SimChat.shell = {});
  const KEY = "simlab.chat.projects";
  let store = { projects: [], map: {} };
  let chats = [], loaded = false, failed = false, query = "", naming = false, pendingProject = null, activeProject = null, sbOpener = null, sbRelease = null, fetchT = 0, menuFor = null;

  try { const j = JSON.parse(LS.get(KEY) || "null"); if (j && Array.isArray(j.projects)) store = { projects: j.projects.filter(p => p && p.id && p.name), map: j.map && typeof j.map === "object" ? j.map : {} }; } catch (e) { /* start empty */ }
  const save = () => LS.set(KEY, JSON.stringify(store));
  const projectOf = cid => { const pid = store.map[cid]; return store.projects.find(p => p.id === pid) ? pid : null; };
  function assign(cid, pid) {
    if (!cid) return;
    if (pid && store.projects.some(p => p.id === pid)) store.map[cid] = pid; else delete store.map[cid];
    save(); renderList();
  }
  function createProject(name) {
    name = String(name || "").replace(/\s+/g, " ").trim().slice(0, 40);
    if (!name) return null;
    const p = { id: "p" + U.uid(), name, collapsed: false };
    store.projects.push(p); save();
    return p;
  }

  function rel(updated) {
    let u = Number(updated);
    if (!isFinite(u) || u <= 0) return "";
    if (u > 1e12) u /= 1000;
    const d = Date.now() / 1000 - u;
    if (d < 60) return "now";
    if (d < 3600) return Math.floor(d / 60) + "m";
    if (d < 86400) return Math.floor(d / 3600) + "h";
    if (d < 7 * 86400) return Math.floor(d / 86400) + "d";
    try { return new Date(u * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" }); } catch (e) { return ""; }
  }
  function chatRows() {
    const st = SimChat.state, rows = chats.map(c => Object.assign({}, c));
    const me = st.chatId && rows.find(c => c.id === st.chatId);
    if (me) me.title = st.title || me.title;
    else if (st.chatId) rows.unshift({ id: st.chatId, title: st.title || "New chat", updated: Date.now() / 1000, seq: 0 });
    return rows;
  }

  function chatButton(c, inProject) {
    const active = c.id === SimChat.state.chatId;
    const title = c.title || "New chat";
    const b = el("button", { type: "button", class: "ch-sb-chat", "aria-current": active ? "true" : null, title }, el("span", { class: "ch-sb-chat-t", text: title }), el("span", { class: "ch-sb-chat-when ch-mono", text: rel(c.updated) }));
    b.addEventListener("click", () => {
      pendingProject = null;
      activeProject = projectOf(c.id);
      if (!active) SimChat.switchChat(c.id);
      renderList();
      S.afterNavigate();
    });
    const more = el("button", { type: "button", class: "ch-sb-more", "aria-label": "Move “" + title + "” to a project", "aria-haspopup": "menu", "aria-expanded": "false" });
    more.innerHTML = U.icon("menu");
    more.addEventListener("click", e => { e.stopPropagation(); openMoveMenu(c, more); });
    return el("div", { class: "ch-sb-row" + (inProject ? " is-in" : "") }, b, more);
  }
  function renderList() {
    const box = $("ch-sb-list");
    if (!box) return;
    const q = query.trim().toLowerCase();
    const rows = chatRows().filter(c => !q || String(c.title || "").toLowerCase().includes(q));
    box.replaceChildren();
    if (naming) box.appendChild(nameForm());
    const projects = store.projects.filter(p => !q || rows.some(c => store.map[c.id] === p.id));
    if (projects.length) {
      box.appendChild(el("h2", { class: "ch-sb-h", text: "Projects" }));
      projects.forEach(p => {
        const mine = rows.filter(c => store.map[c.id] === p.id);
        const open = q ? true : !p.collapsed;
        const gid = "ch-sb-pg-" + p.id;
        const head = el("button", { type: "button", class: "ch-sb-proj", "aria-expanded": String(open), "aria-controls": gid },
          el("span", { class: "ch-sb-chev" }), U.iconEl("folder"), el("span", { class: "ch-sb-proj-t", text: p.name }), el("span", { class: "ch-sb-count ch-mono", text: String(mine.length) }));
        head.querySelector(".ch-sb-chev").innerHTML = U.icon("chevron");
        head.addEventListener("click", () => { if (q) return; p.collapsed = !p.collapsed; save(); renderList(); });
        const add = el("button", { type: "button", class: "ch-sb-more ch-sb-add", "aria-label": "New chat in " + p.name });
        add.innerHTML = U.icon("plus");
        add.addEventListener("click", () => newChat(p.id));
        const group = el("div", { class: "ch-sb-group", id: gid, role: "group", "aria-label": p.name, hidden: !open });
        if (!mine.length && !q) group.appendChild(el("p", { class: "ch-sb-none", text: "No chats yet." }));
        mine.forEach(c => group.appendChild(chatButton(c, true)));
        box.appendChild(el("div", { class: "ch-sb-proj-wrap" + (activeProject === p.id ? " is-active" : "") }, el("div", { class: "ch-sb-row ch-sb-prow" }, head, add), group));
      });
    }
    const loose = rows.filter(c => !projectOf(c.id));
    if (loose.length || !projects.length) box.appendChild(el("h2", { class: "ch-sb-h", text: projects.length ? "Chats" : "Recent chats" }));
    loose.forEach(c => box.appendChild(chatButton(c, false)));
    if (!rows.length) box.appendChild(el("p", { class: "ch-sb-none", text: q ? "No chats match “" + query.trim() + "”." : failed ? "The chat list could not be loaded." : loaded ? "No chats yet. Describe a job to start one." : "Loading..." }));
    if (q) U.announce(rows.length === 1 ? "1 chat matches" : rows.length + " chats match");
  }

  function nameForm() {
    const input = el("input", { type: "text", class: "ch-sb-nameinput", "aria-label": "Project name", placeholder: "Project name", maxlength: "40", autocomplete: "off", spellcheck: "false" });
    const done = () => { naming = false; renderList(); $("ch-sb-newproj").focus(); };
    const form = el("form", { class: "ch-sb-nameform" }, input,
      el("button", { type: "submit", class: "ch-btn ch-btn--primary", text: "Add" }),
      el("button", { type: "button", class: "ch-btn ch-btn--ghost", text: "Cancel", onclick: done }));
    form.addEventListener("submit", e => {
      e.preventDefault();
      const p = createProject(input.value);
      if (!p) { input.focus(); return; }
      naming = false; activeProject = p.id; renderList();
      U.announce("Project " + p.name + " created");
      $("ch-sb-newproj").focus();
    });
    input.addEventListener("keydown", e => { if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); done(); } });
    setTimeout(() => input.focus(), 0);
    return form;
  }

  /* the small "move to project" menu on a chat row */
  function closeMoveMenu() {
    const m = $("ch-sb-menu");
    if (m) m.remove();
    layers.remove("sbmenu");
    document.querySelectorAll(".ch-sb-more[aria-expanded='true']").forEach(b => b.setAttribute("aria-expanded", "false"));
    menuFor = null;
  }
  function openMoveMenu(c, btn) {
    if (menuFor === c.id) { closeMoveMenu(); return; }
    closeMoveMenu();
    menuFor = c.id;
    btn.setAttribute("aria-expanded", "true");
    const cur = projectOf(c.id);
    const item = (pid, label, icon) => {
      const b = el("button", { type: "button", role: "menuitemradio", "aria-checked": String(cur === pid), class: "ch-menu-item" }, U.iconEl(icon), el("span", { text: label }));
      if (cur === pid) b.appendChild(U.iconEl("check"));
      b.addEventListener("click", () => { closeMoveMenu(); assign(c.id, pid); U.announce("Moved to " + label); });
      return b;
    };
    const menu = el("div", { class: "ch-pop ch-sb-menu", id: "ch-sb-menu", role: "menu", "aria-label": "Move to a project" }, item(null, "No project", "chat"), store.projects.map(p => item(p.id, p.name, "folder")));
    if (!store.projects.length) menu.appendChild(el("p", { class: "ch-sb-none", text: "Make a project first with New project." }));
    $("ch-sb").querySelector(".ch-sb-in").appendChild(menu);
    const r = btn.getBoundingClientRect(), sr = $("ch-sb").getBoundingClientRect();
    menu.style.left = Math.max(8, Math.min(r.right - sr.left - 220, sr.width - 232)) + "px";
    menu.style.top = Math.min(r.bottom - sr.top + 4, Math.max(8, window.innerHeight - 40 - 38 * (store.projects.length + 1))) + "px";
    layers.push("sbmenu", () => { closeMoveMenu(); if (btn.isConnected) btn.focus(); });
    menu.addEventListener("keydown", e => {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      const items = Array.from(menu.querySelectorAll("[role=menuitemradio]")), i = items.indexOf(document.activeElement);
      e.preventDefault();
      items[(i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length].focus();
    });
    const first = menu.querySelector("button");
    if (first) first.focus();
  }
  document.addEventListener("click", e => { if (menuFor && !e.target.closest("#ch-sb-menu") && !e.target.closest(".ch-sb-more")) closeMoveMenu(); });

  function fetchChats() {
    clearTimeout(fetchT);
    return Promise.resolve().then(() => SimChat.net.list()).then(r => { chats = r && Array.isArray(r.chats) ? r.chats : []; failed = false; }).catch(() => { failed = true; })
      .then(() => { loaded = true; renderList(); });
  }
  const scheduleFetch = ms => { clearTimeout(fetchT); fetchT = setTimeout(fetchChats, ms == null ? 1200 : ms); };

  function newChat(pid) {
    closeMoveMenu();
    pendingProject = pid || null;
    activeProject = pid || null;
    const st = SimChat.state, empty = !st.order.length && !st.messages;
    if (empty) {                                                                       // an empty chat is already a new one: reuse it
      if (st.chatId && pid) { assign(st.chatId, pid); pendingProject = null; }
      renderList(); S.afterNavigate(); SimChat.focusComposer();
      return;
    }
    S.afterNavigate();
    SimChat.newChat();
  }

  function openSidebar(o) {
    o = o || {};
    const was = root.dataset.sb === "open";
    root.dataset.sb = "open";
    const sb = $("ch-sb");
    sb.inert = false;
    $("ch-history-btn").setAttribute("aria-expanded", "true");
    if (!was) {
      const overlay = !inlineSb();
      setDialog(sb, overlay, "Projects and chats");
      if (overlay) {
        if (root.dataset.rp === "open") P.close({ silent: true });
        sbOpener = document.activeElement && document.activeElement !== document.body ? document.activeElement : null;
        sbRelease = U.trapFocus(sb);
        layers.push("sidebar", () => S.closeSidebar());
      } else LS.set("simlab.chat.sb", "1");
      syncScrim();
      fetchChats();
    }
    if (!o.noFocus) setTimeout(() => (o.search ? $("ch-sb-q") : $("ch-sb-newchat")).focus(), 30);
  }
  function closeSidebar(o) {
    if (root.dataset.sb !== "open") return;
    o = o || {};
    root.dataset.sb = "closed";
    const sb = $("ch-sb");
    sb.inert = true;
    setDialog(sb, false);
    $("ch-history-btn").setAttribute("aria-expanded", "false");
    if (sbRelease) { sbRelease(); sbRelease = null; }
    layers.remove("sidebar");
    closeMoveMenu();
    syncScrim();
    if (inlineSb() && !o.silent) LS.set("simlab.chat.sb", "0");
    if (!o.silent && sbOpener && sbOpener.isConnected) sbOpener.focus();
    sbOpener = null;
  }
  Object.assign(S, {
    openSidebar, closeSidebar,
    toggleSidebar() { if (root.dataset.sb === "open") closeSidebar(); else openSidebar(); },
    isSidebarOpen: () => root.dataset.sb === "open",
    refreshChats: fetchChats,
    afterNavigate() { if (!inlineSb() && root.dataset.sb === "open") closeSidebar({ silent: true }); },
    projects: { list: () => store.projects.map(p => Object.assign({}, p)), assign, create: createProject, of: projectOf },
  });

  /* ================================================================ wiring */
  function wire() {
    const ic = (id, name) => { $(id).innerHTML = U.icon(name); };
    ic("ch-history-btn", "sidebar"); ic("ch-sb-close", "sidebar"); ic("ch-rp-close", "close");
    $("ch-sb-newchat").append(U.iconEl("plus"), document.createTextNode("New chat"));
    $("ch-sb-newproj").append(U.iconEl("folder-plus"), document.createTextNode("New project"));
    $("ch-overview-btn").append(U.iconEl("panes"), el("span", { class: "ch-btn-t", text: "Panes" }));
    $("ch-overview-btn").setAttribute("aria-label", "Panes: plan, simulation, tests, parts, rover and background tasks");
    $("ch-sb").querySelector(".ch-sb-search").insertBefore(U.iconEl("search"), $("ch-sb-q"));
    $("ch-history-btn").addEventListener("click", () => S.toggleSidebar());
    $("ch-sb-close").addEventListener("click", () => closeSidebar());
    $("ch-sb-newchat").addEventListener("click", () => newChat(null));
    $("ch-sb-newproj").addEventListener("click", () => { naming = true; renderList(); });
    $("ch-sb-q").addEventListener("input", e => { query = e.target.value; renderList(); });
    $("ch-sb-q").addEventListener("keydown", e => { if (e.key === "Escape" && query) { e.stopPropagation(); e.preventDefault(); e.target.value = ""; query = ""; renderList(); } });
    $("ch-overview-btn").addEventListener("click", () => P.toggle());
    $("ch-rp-close").addEventListener("click", () => P.close());
    scrim().addEventListener("click", () => { if (!inlineRp() && root.dataset.rp === "open") P.close(); if (!inlineSb() && root.dataset.sb === "open") closeSidebar(); });
    $("ch-rp-tabs").addEventListener("keydown", e => {
      const i = defs.findIndex(d => d.id === current);
      const to = e.key === "ArrowRight" ? i + 1 : e.key === "ArrowLeft" ? i - 1 : e.key === "Home" ? 0 : e.key === "End" ? defs.length - 1 : null;
      if (to === null) return;
      e.preventDefault();
      select(defs[(to + defs.length) % defs.length].id, true);
    });
    /* crossing a breakpoint turns columns into drawers or back: settle the state instead of leaving a half-open drawer */
    mql("(min-width: 900px)").addEventListener("change", () => { const was = root.dataset.sb === "open"; if (was) closeSidebar({ silent: true }); if (inlineSb() && LS.get("simlab.chat.sb") !== "0") openSidebar({ noFocus: true }); syncScrim(); });
    mql("(min-width: 1200px)").addEventListener("change", () => { if (isOpen) close({ silent: true }); const want = LS.get("simlab.chat.rp"); if (inlineRp() && want) open(want, { auto: true }); syncScrim(); });
    document.addEventListener("visibilitychange", () => { if (!document.hidden && root.dataset.sb === "open") scheduleFetch(0); });
    SimChat.on("chat", () => { const id = SimChat.state.chatId; if (id && pendingProject && !store.map[id]) { store.map[id] = pendingProject; pendingProject = null; save(); } renderList(); scheduleFetch(800); });
    SimChat.on("message", () => { renderList(); scheduleFetch(1500); });
    SimChat.on("reset", () => renderList());
  }

  function init() {
    wire();
    $("ch-sb").inert = root.dataset.sb !== "open";
    $("ch-rp").inert = true;
    $("ch-history-btn").setAttribute("aria-expanded", String(root.dataset.sb === "open"));
    renderTabs();
    renderList();
    if (root.dataset.sb === "open") fetchChats();
    const want = LS.get("simlab.chat.rp");
    if (inlineRp() && want && defOf(want)) open(want, { auto: true }); else root.dataset.rp = "closed";
  }
  /* the panes that register after this file (Plan, Tests, Parts, Simulation) are in the registry before DOMContentLoaded fires; start once they are */
  document.addEventListener("DOMContentLoaded", init);
  if (document.readyState !== "loading") setTimeout(init, 0);
})();
