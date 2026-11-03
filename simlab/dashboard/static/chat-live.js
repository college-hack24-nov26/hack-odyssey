"use strict";
/* chat-live.js: the ONE shared live 3D view (SimChat.live).
   A single Viewer3D lives inside a host element. The host is moved (not recreated) between the newest "Watch it work" card, a small thumbnail in the dock when that card
   scrolls out of view, and the full-screen Focus overlay. The viewer is fed exactly like the Lab tab does: GET /api/lab/scene when the scene changes, and the /ws/lab
   stream for poses. It freezes into a poster (a still picture) when the run ends, is replaced, or the page stays hidden for 30 s, and the GL context is released.
   A second, short-lived "photo booth" viewer draws the see-through picture for the Design card. At most two GL contexts exist at any time. */
(function () {
  const V = SimChat.views, h = V.h;
  const L = (SimChat.live = {});
  const handlers = {};
  const reduce = () => { try { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) { return false; } };
  L.on = (name, fn) => { (handlers[name] = handlers[name] || []).push(fn); };
  const emit = (name, p) => { for (const fn of handlers[name] || []) { try { fn(p); } catch (e) { console.warn("live handler failed", name, e); } } };
  L.cond = {};                                   // tough-condition values shared by the card and the Focus rail (knob -> number; absent = calm)

  const posters = new Map();                     // run id -> data URL of its last picture
  let own = null;                                // {cardId, runId, slot, sceneId, since}
  let host = null, viewer = null, io = null;
  let ws = null, wsTimer = 0, closing = false;
  let thumb = false, orbit = false, focusEl = null, focusOpener = null;
  let lastSeen = null, sceneLoaded = null, sceneTries = 0, lastT = null, lastStats = 0, hiddenTimer = 0, wanted = null;
  let camImg = null, camOn = false;

  /* ---------------------------------------------------------------- the host and the viewer */
  function ensureViewer() {
    if (viewer) return true;
    if (!window.THREE || !window.Viewer3D) return false;
    host = h("div", { class: "ch-lv-host", role: "img", "aria-label": "Simulation view" });
    try {
      viewer = new Viewer3D(host, { interactive: false, shadows: true, maxDpr: 1.5 });
    } catch (e) {
      console.warn("live view could not start", e);
      host = null; viewer = null;
      return false;
    }
    return true;
  }
  function place() {
    if (!host || !own) return;
    const target = focusEl ? focusEl.querySelector(".ch-lv-stage") : thumb && SimChat.dock && SimChat.dock.slot ? SimChat.dock.slot : own.slot;
    if (target && host.parentNode !== target) target.appendChild(host);
    host.classList.toggle("ch-lv-thumb", !!thumb && !focusEl);
  }
  function availability() { emit("interactive", { cardId: own && own.cardId, available: !!own && !orbit && !thumb && !focusEl }); }
  function setThumb(on) {
    if (thumb === on || !own) return;
    if (on && !(SimChat.dock && SimChat.dock.slot)) return;
    thumb = on;
    place();
    emit("thumb", { cardId: own.cardId, away: on });
    availability();
  }

  /* ---------------------------------------------------------------- attach / freeze / detach */
  L.owner = () => (own ? own.cardId : null);
  L.poster = runId => posters.get(runId) || null;
  L.isFocused = () => !!focusEl;

  L.attach = function (cardId, slot, o = {}) {
    if (own && own.cardId !== cardId) freezeNow(own.cardId, { why: "replaced" });
    if (!ensureViewer()) { emit("unavailable", { cardId }); slot.dataset.unavailable = "1"; return false; }
    const fresh = !own;
    if (own && own.runId !== o.runId) {                       // "Run again": the same card follows a new run
      own.runId = o.runId; own.sceneId = o.sceneId || null; own.since = Date.now(); sceneLoaded = null; sceneTries = 0; lastT = null;
      viewer.resetTrail();
    } else if (fresh) {
      own = { cardId, runId: o.runId, slot, sceneId: o.sceneId || null, since: Date.now() };
      sceneLoaded = null; sceneTries = 0; lastT = null;
    } else if (o.sceneId && !own.sceneId) own.sceneId = o.sceneId;
    if (own.slot !== slot) { own.slot = slot; watchSlot(); }
    if (fresh) watchSlot();
    place();
    openWs();
    availability();
    return true;
  };

  function watchSlot() {
    if (io) io.disconnect();
    io = null;
    if (!own || !window.IntersectionObserver) return;
    io = new IntersectionObserver(es => {
      if (!own || focusEl) return;
      const r = es[es.length - 1].intersectionRatio;
      if (!thumb && r < 0.04) setThumb(true);
      else if (thumb && r > 0.3) setThumb(false);
    }, { threshold: [0, 0.04, 0.15, 0.3, 0.6, 1] });
    io.observe(own.slot);
  }

  function capture() {
    if (!viewer) return null;
    try {
      viewer.renderer.render(viewer.scene, viewer.camera);                     // render, then read the pixels in the same tick (the buffer is not preserved)
      return viewer.renderer.domElement.toDataURL("image/jpeg", 0.8);
    } catch (e) { return null; }
  }
  function teardown() {
    closing = true;
    clearTimeout(wsTimer);
    if (ws) { try { ws.close(); } catch (e) { /* closing anyway */ } ws = null; }
    if (io) { io.disconnect(); io = null; }
    if (focusEl) closeFocus(true);
    setCamera(false);
    if (viewer) { try { viewer.dispose(); } catch (e) { console.warn("viewer dispose", e); } }
    viewer = null;
    if (host) host.remove();
    host = null;
    thumb = false; orbit = false; sceneLoaded = null;
    closing = false;
  }
  function freezeNow(cardId, info = {}) {
    if (!own || own.cardId !== cardId) return;
    const o = own;
    const url = capture();
    if (url) posters.set(o.runId, url);
    if (info.why === "hidden") wanted = { cardId: o.cardId, slot: o.slot, runId: o.runId, sceneId: o.sceneId };
    own = null;
    teardown();
    emit("thumb", { cardId: o.cardId, away: false });
    emit("poster", { runId: o.runId, cardId: o.cardId, url, why: info.why || "ended" });
  }
  L.freeze = freezeNow;
  L.detach = function () { if (!own && !viewer) return; own = null; wanted = null; teardown(); emit("interactive", { available: false }); };
  L.thumbToDock = () => setThumb(true);

  /* ---------------------------------------------------------------- the data feed */
  function openWs() {
    if (ws || !own) return;
    try {
      ws = new WebSocket((location.protocol === "https:" ? "wss" : "ws") + "://" + location.host + "/ws/lab");
    } catch (e) { ws = null; return; }
    const mine = ws;
    ws.onmessage = e => { try { onState(JSON.parse(e.data)); } catch (err) { /* a bad frame is skipped */ } };
    ws.onclose = () => { if (ws === mine) ws = null; if (own && !closing) wsTimer = setTimeout(openWs, 1500); };
    ws.onerror = () => { try { mine.close(); } catch (e) { /* already closing */ } };
  }
  function loadScene(sid) {
    if (!viewer || sceneLoaded === sid) return;
    sceneLoaded = sid;
    fetch("/api/lab/scene").then(r => { if (!r.ok) throw new Error("no scene"); return r.json(); }).then(sc => {
      if (!viewer || !own || sceneLoaded !== sid) return;
      viewer.loadScene(sc);
      lastT = null;
    }).catch(() => { if (sceneLoaded === sid && sceneTries++ < 6) { sceneLoaded = null; setTimeout(() => { if (own && viewer) loadScene(sid); }, 1000); } });
  }
  function onState(s) {
    if (!own || !viewer) return;
    const sid = s.scene_id || null;
    if (sid) lastSeen = sid;
    if (!own.sceneId && sid && s.running && Date.now() - own.since > 3500) own.sceneId = sid;        // the server did not name it in time: take what is running now
    if (own.sceneId && sid && sid !== own.sceneId) {                                                      // another tab or the Workbench started a different run
      const cid = own.cardId;
      freezeNow(cid, { why: "replaced" });
      emit("replaced", { cardId: cid });
      return;
    }
    if (!own.sceneId || sid !== own.sceneId) return;
    if (sceneLoaded !== sid) loadScene(sid);
    if (s.t !== undefined && lastT !== null && s.t < lastT - 0.5) viewer.resetTrail();
    lastT = s.t;
    if (s.poses && sceneLoaded === sid) { viewer.update(s.poses, s.env); viewer.setMarkers(s.markers); }
    const now = performance.now();
    if (now - lastStats > 250) {
      lastStats = now;
      emit("stats", { t: s.t, dist: s.info && s.info.dist, battery: s.battery, result: s.result, running: s.running, status: s.status, speed: s.speed });
    }
  }

  /* ---------------------------------------------------------------- orbit on demand, camera picture */
  L.setOrbit = function (on) {
    if (!viewer || (on && thumb)) return;
    orbit = !!on || !!focusEl;
    viewer.setInteractive(orbit);
    if (on && !focusEl) viewer.mode = "orbit";
    availability();
  };
  document.addEventListener("pointerdown", e => { if (orbit && !focusEl && host && !host.contains(e.target)) L.setOrbit(false); }, { passive: true });

  function setCamera(on, container) {
    camOn = !!on;
    if (!on) { if (camImg) { camImg.removeAttribute("src"); camImg.remove(); } camImg = null; return; }
    if (!own) return;
    if (!camImg) { camImg = h("img", { class: "ch-lv-cam", alt: "The machine's own camera, with detections drawn on it" }); camImg.src = "/api/lab/video?t=" + Date.now(); }
    if (container && camImg.parentNode !== container) container.appendChild(camImg);
  }
  L.setCamera = setCamera;

  /* ---------------------------------------------------------------- Focus mode */
  function focusables(root) { return [...root.querySelectorAll("button,[href],input,select,textarea,[tabindex]:not([tabindex='-1'])")].filter(x => !x.disabled && x.offsetParent !== null); }
  L.focus = function () {
    if (!own || !viewer || focusEl) return;
    focusOpener = document.activeElement;
    const card = SimChat.state && SimChat.state.cards && SimChat.state.cards[own.cardId] || {};
    const stage = h("div", { class: "ch-lv-stage" });
    const numEls = {};
    const nums = h("div", { class: "ch-lv-nums is-col", role: "group", "aria-label": "Live numbers" }, [["time", "Time"], ["dist", "Distance to goal"], ["bat", "Battery"], ["res", "Result"]].map(([k, l]) => {
      const b = h("b", { class: "ch-v-mono", text: "-" }); numEls[k] = b;
      return h("div", { class: "ch-lv-num" }, b, h("span", { text: l }));
    }));
    const onStats = s => {
      numEls.time.textContent = V.num(s.t, 1) + " s";
      numEls.dist.textContent = s.dist !== undefined && s.dist !== null ? V.num(s.dist, 1) + " m" : "-";
      const sc = s.battery === null || s.battery === undefined ? null : typeof s.battery === "number" ? s.battery : s.battery.soc;
      numEls.bat.textContent = sc === null || sc === undefined ? "-" : V.num(100 * sc, 0) + " %";
      numEls.res.textContent = s.result ? (s.result.success ? "Reached the goal" : s.result.crash ? "Failed: " + s.result.crash : s.result.timeout ? "Time ran out" : "Ended") : "Running";
    };
    const camBox = h("div", { class: "ch-lv-camwrap" });
    const camBtn = h("button", { type: "button", class: "ch-v-btn", "aria-pressed": String(camOn) }, V.icon("play"), h("span", { class: "ch-v-btn-t", text: "Camera" }));
    camBtn.addEventListener("click", () => { const on = camBtn.getAttribute("aria-pressed") !== "true"; camBtn.setAttribute("aria-pressed", String(on)); setCamera(on, camBox); });
    const rail = h("div", { class: "ch-lv-rail" },
      h("h3", { class: "ch-lv-rail-h", text: "Watch it work" }), nums,
      h("p", { class: "ch-lv-cap" }, h("b", { text: "Demonstration, not a test. " }), card.current && card.current.conditions_text ? card.current.conditions_text + "." : ""),
      (card.conditions || []).length ? [h("h4", { class: "ch-v-h4", text: "Tough conditions" }), V.condForm(card.conditions, L.cond, { idp: "focus" }),
        V.row(V.btn("Run again with these", { kind: "primary", icon: "refresh", onClick: (e, b) => runAgain(b) }))] : null,
      V.row(camBtn), camBox);
    const speeds = [0.5, 1, 2, 4].map(sp => {
      const b = h("button", { type: "button", class: "ch-v-btn", "aria-pressed": String(sp === 1), "aria-label": "Speed " + sp + "x" }, h("span", { class: "ch-v-btn-t", text: sp + "x" }));
      b.addEventListener("click", () => {
        speeds.forEach(x => x.setAttribute("aria-pressed", "false"));
        b.setAttribute("aria-pressed", "true");
        fetch("/api/lab/speed", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ speed: sp }) }).catch(() => { });
      });
      return b;
    });
    const views = [["orbit", "Orbit"], ["follow", "Follow"], ["top", "Top"]].map(([m, label]) => {
      const b = h("button", { type: "button", class: "ch-v-btn", "aria-pressed": String(viewer.mode === m) }, h("span", { class: "ch-v-btn-t", text: label }));
      b.addEventListener("click", () => { if (m === "orbit") viewer.mode = "orbit"; else viewer.setMode(m); views.forEach(x => x.setAttribute("aria-pressed", String(x === b))); });
      return b;
    });
    const closeBtn = V.btn("Close", { icon: "cross", kind: "primary", onClick: () => closeFocus() });
    const bar = h("div", { class: "ch-lv-bar", role: "toolbar", "aria-label": "Simulation controls" },
      h("div", { class: "ch-lv-bargrp", role: "group", "aria-label": "Speed" }, speeds),
      V.btn("Restart", { icon: "refresh", onClick: (e, b) => runAgain(b) }),
      V.btn("Stop", { icon: "stop", onClick: (e, b) => V.send({ type: "stop", args: { job: "live" } }, b) }),
      h("div", { class: "ch-lv-bargrp", role: "group", "aria-label": "Camera view" }, views), closeBtn);
    focusEl = h("div", { class: "ch-lv-focus", role: "dialog", "aria-modal": "true", "aria-label": "Live simulation, full screen", tabindex: "-1" }, h("div", { class: "ch-lv-fmain" }, stage, rail), bar);
    focusEl._stats = onStats;
    focusEl.addEventListener("keydown", e => {
      if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); closeFocus(); return; }
      if (e.key !== "Tab") return;
      const f = focusables(focusEl);
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); } else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
    L.on("stats", onStats);
    document.body.appendChild(focusEl);
    document.body.classList.add("ch-lv-lock");
    place();
    viewer.setInteractive(true);
    availability();
    emit("thumb", { cardId: own.cardId, away: true });
    focusEl.focus();
    if (camOn) setCamera(true, camBox);
  };
  function runAgain(btn) {
    const conditions = {};
    for (const [k, v] of Object.entries(L.cond)) if (v !== null && v !== undefined) conditions[k] = v;
    V.send({ type: "watch", args: Object.keys(conditions).length ? { conditions } : {} }, btn);
  }
  function closeFocus(silent) {
    if (!focusEl) return;
    const el = focusEl;
    focusEl = null;
    if (handlers.stats) handlers.stats = handlers.stats.filter(fn => fn !== el._stats);
    if (camImg && el.contains(camImg)) setCamera(false);
    document.body.classList.remove("ch-lv-lock");
    el.remove();
    if (viewer) { orbit = false; viewer.setInteractive(false); }
    if (own && !silent) { place(); emit("thumb", { cardId: own.cardId, away: thumb }); availability(); }
    if (!silent && focusOpener && focusOpener.isConnected) focusOpener.focus();
    focusOpener = null;
  }
  L.closeFocus = () => closeFocus();

  /* ---------------------------------------------------------------- when the page is hidden for a while the view freezes and lets go of the GPU */
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      clearTimeout(hiddenTimer);
      hiddenTimer = setTimeout(() => { if (own && document.hidden) freezeNow(own.cardId, { why: "hidden" }); }, 30000);
    } else {
      clearTimeout(hiddenTimer);
      if (wanted && wanted.slot && wanted.slot.isConnected) { const w = wanted; wanted = null; L.attach(w.cardId, w.slot, { runId: w.runId, sceneId: w.sceneId }); } else wanted = null;
    }
  });
  window.addEventListener("pagehide", () => { if (viewer) { own = null; teardown(); } });
  SimChat.on("reset", () => L.detach());

  /* ---------------------------------------------------------------- the photo booth: a see-through picture of the proposed machine */
  const photoCache = new Map();
  let booth = Promise.resolve();
  const frame = () => new Promise(r => requestAnimationFrame(() => r()));
  L.photo = function (preview, key, size = {}) {
    if (photoCache.has(key)) return photoCache.get(key);
    const job = booth.then(async () => {
      if (!window.THREE || !window.Viewer3D) throw new Error("no 3D viewer");
      const w = size.w || 480, hh = size.h || 300;
      const box = h("div", { class: "ch-lv-booth", style: { width: w + "px", height: hh + "px" }, "aria-hidden": "true" });
      document.body.appendChild(box);
      let v = null;
      try {
        v = new Viewer3D(box, { interactive: false, shadows: false, maxDpr: 1 });
        v.mode = "orbit";
        v.loadScene(preview.scene);
        v.update(preview.poses);
        v.az = -2.2; v.el_ = 0.5;
        v.target.set(0, 0, 0.04);
        v.dist = Math.max(0.45, ((preview.scene || {}).vehicle_size || 0.2) * 3.4);
        v.renderer.setSize(w, hh, false);
        v.camera.aspect = w / hh; v.camera.updateProjectionMatrix();
        await frame();
        const ce = Math.cos(v.el_), d = v.dist;                                       // the same camera placement the viewer's own loop uses, done by hand so it works even in a background tab
        v.camera.position.set(v.target.x + d * ce * Math.cos(v.az), v.target.y + d * ce * Math.sin(v.az), v.target.z + d * Math.sin(v.el_));
        v.camera.lookAt(v.target);
        v.env.follow(v.target, performance.now() / 1000);
        v.renderer.render(v.scene, v.camera);
        return v.renderer.domElement.toDataURL("image/jpeg", 0.86);
      } finally {
        try { if (v) v.dispose(); } catch (e) { console.warn("booth dispose", e); }
        box.remove();
      }
    });
    booth = job.catch(() => { });
    photoCache.set(key, job);
    job.catch(() => photoCache.delete(key));
    if (photoCache.size > 12) photoCache.delete(photoCache.keys().next().value);
    return job;
  };
})();
