"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : (r.statusText || "request failed"));
  return j;
}
let toastTimer;
function toast(msg, err = false) {
  let t = $(".toast");
  if (!t) { t = document.createElement("div"); t.className = "toast"; t.setAttribute("role", "status"); document.body.append(t); }
  t.className = "toast" + (err ? " err" : "");
  t.textContent = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.remove(), err ? 9000 : 3500);
}
const guard = fn => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); } };

/* ---------------------------------------------------------------- state */
const S = { cat: null, tab: "sim", sim: null, hw: null, truth: null, ws: {}, mapKey: "" };

/* ---------------------------------------------------------------- tabs */
function setTab(tab) {
  S.tab = tab;
  $$(".tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  $$("main > section[id^=tab-]").forEach(s => { s.hidden = s.id !== "tab-" + tab; });
  $("#s-video").src = tab === "sim" ? "/api/sim/video" : "";
  $("#h-video").src = tab === "hw" ? "/api/hw/video" : "";
  history.replaceState(null, "", "#" + tab);
  renderPills();
  document.dispatchEvent(new CustomEvent("tabchange", { detail: tab }));
}
$$(".tabs button").forEach(b => b.addEventListener("click", () => setTab(b.dataset.tab)));

function connectWs(kind) {
  const ws = new WebSocket(`ws://${location.host}/ws/${kind}`);
  ws.onmessage = e => { S[kind] = JSON.parse(e.data); kind === "sim" ? renderSim() : renderHw(); renderPills(); };
  ws.onclose = () => setTimeout(() => connectWs(kind), 1500);
  S.ws[kind] = ws;
}

function pill(text, cls = "") { return `<span class="pill ${cls}">${esc(text)}</span>`; }
function renderPills() {
  if (S.tab === "lab" || S.tab === "train") { $("#pills").innerHTML = window.labPills ? window.labPills() : ""; return; }
  if (S.tab === "build") { $("#pills").innerHTML = window.buildPills ? window.buildPills() : ""; return; }
  if (S.tab === "goal") { $("#pills").innerHTML = window.goalPills ? window.goalPills() : ""; return; }
  const s = S[S.tab];
  if (!s) { $("#pills").innerHTML = pill("connecting..."); return; }
  const p = [];
  p.push(pill(s.detector ? "detector: " + s.detector : "no detector", s.detector ? "" : "warn"));
  if (S.tab === "sim") {
    p.push(pill(s.link, s.link.startsWith("connected") ? "ok" : "warn"));
    p.push(pill(s.mission_state, s.mission_state === "running" ? "ok" : s.mission_state === "finished" ? "ok" : s.mission_state.startsWith("blocked") ? "bad" : ""));
  } else {
    p.push(pill(s.rover_connected ? "rover online" : "rover offline", s.rover_connected ? "ok" : "warn"));
    p.push(pill(s.cam_connected ? "camera online" : "camera offline", s.cam_connected ? "ok" : "warn"));
  }
  $("#pills").innerHTML = p.join("");
}

/* ---------------------------------------------------------------- map drawing */
function drawMap(svg, map, st, opts = {}) {
  const nodes = map.nodes, edges = map.edges;
  const xs = Object.values(nodes).map(n => n.pos[0]), ys = Object.values(nodes).map(n => n.pos[1]);
  const pad = 5, x0 = Math.min(...xs) - pad, x1 = Math.max(...xs) + pad, y0 = Math.min(...ys) - pad, y1 = Math.max(...ys) + pad;
  const W = x1 - x0, H = y1 - y0, u = Math.max(W, H) / 60;
  svg.setAttribute("viewBox", `${x0} ${-y1} ${W} ${H}`);
  const route = new Set();
  (st?.route || []).forEach((n, i, a) => { if (i) route.add([a[i - 1], n].sort().join("|")); });
  let h = "";
  for (const [id, e] of Object.entries(edges)) {
    const A = nodes[e.a].pos, B = nodes[e.b].pos, on = route.has([e.a, e.b].sort().join("|"));
    const fit = opts.fit?.[id];
    const col = fit && !fit.fits ? "#5a2a30" : on ? "#4da3ff" : "#3a4658";
    h += `<line x1="${A[0]}" y1="${-A[1]}" x2="${B[0]}" y2="${-B[1]}" stroke="${col}" stroke-width="${u * (1.1 + e.width * 0.8)}" stroke-linecap="round"/>`;
    h += `<text x="${(A[0] + B[0]) / 2}" y="${-(A[1] + B[1]) / 2 - u * 1.8}" fill="#7d8ba0" font-size="${u * 2}" text-anchor="middle">${esc(id)}</text>`;
  }
  for (const [id, n] of Object.entries(nodes)) {
    h += `<circle cx="${n.pos[0]}" cy="${-n.pos[1]}" r="${u * 2.2}" fill="#1c2635" stroke="#516076" stroke-width="${u * 0.25}"/>`;
    h += `<text x="${n.pos[0]}" y="${-n.pos[1] + u * 0.7}" fill="#aab6c8" font-size="${u * 1.9}" text-anchor="middle">${esc(id)}</text>`;
  }
  for (const [id, g] of Object.entries(map.goals)) {
    const n = nodes[g.node].pos;
    h += `<circle cx="${n[0] + u * 3}" cy="${-n[1] - u * 3}" r="${u * 1.3}" fill="${g.color}" stroke="#fff" stroke-width="${u * 0.2}"><title>${esc(id)}</title></circle>`;
  }
  const at = (edge, s) => { const e = edges[edge], A = nodes[e.a].pos, B = nodes[e.b].pos, L = Math.hypot(B[0] - A[0], B[1] - A[1]); return [A[0] + (B[0] - A[0]) / L * s, A[1] + (B[1] - A[1]) / L * s]; };
  if (opts.truth) for (const d of opts.truth) { const p = at(d.edge, d.s); h += `<circle cx="${p[0]}" cy="${-p[1]}" r="${u * 1.5}" fill="none" stroke="#4cd08a" stroke-width="${u * 0.3}"/>`; }
  for (const d of st?.defects || []) {
    if (!edges[d.edge]) continue;
    const p = at(d.edge, d.s);
    h += `<circle cx="${p[0]}" cy="${-p[1]}" r="${u * 0.95}" fill="${d.cls === "crack" ? "#ff6464" : "#3eb0ff"}"><title>${d.cls} #${d.id}</title></circle>`;
  }
  const po = st?.pose;
  if (po) {
    const a = -po.yaw * 180 / Math.PI;
    h += `<g transform="translate(${po.x} ${-po.y}) rotate(${a})"><path d="M ${u * 2.2} 0 L ${-u * 1.5} ${u * 1.4} L ${-u * 1.5} ${-u * 1.4} Z" fill="#ffd54a" stroke="#000" stroke-width="${u * 0.15}"/></g>`;
  }
  svg.innerHTML = h;
}

/* ---------------------------------------------------------------- shared renderers */
function defectRows(list, kind) {
  if (!list.length) return `<tr><td colspan="5" class="hint">nothing found yet</td></tr>`;
  return list.map(d => `<tr><td>${d.id}</td><td><span class="tag ${d.cls}">${d.cls}${d.subtype ? "/" + esc(d.subtype) : ""}</span></td>
    <td>${kind === "sim" ? esc(d.edge) + " @ " : ""}${d.s.toFixed(1)} m, ${d.angle.toFixed(0)}&deg;</td><td>${d.size_cm[0]}&times;${d.size_cm[1]} cm</td>
    <td><img alt="defect ${d.id}" src="/api/${kind}/defect/${d.id}.jpg?h=${d.hits}"></td></tr>`).join("");
}
function logRows(log) { return log.map(l => `<div><b>${l.t}s</b> ${esc(l.msg)}</div>`).join(""); }
function setText(id, v) { const e = $(id); if (e && e.textContent !== String(v)) e.textContent = v; }

/* ---------------------------------------------------------------- SIMULATION */
function currentOverrides() {
  const base = S.cat.rovers[$("#s-rover").value], o = {};
  const len = +$("#s-len").value / 100, wid = +$("#s-wid").value / 100, sp = +$("#s-speed").value, dr = $("#s-drive").value;
  if (Math.abs(len - base.length) > 1e-4) o.length = len;
  if (Math.abs(wid - base.width) > 1e-4) o.width = wid;
  if (Math.abs(sp - base.max_speed) > 1e-4) o.max_speed = sp;
  if (dr !== base.drive) o.drive = dr;
  return o;
}
function fillRoverFields() {
  const r = S.cat.rovers[$("#s-rover").value];
  $("#s-len").value = Math.round(r.length * 100); $("#s-wid").value = Math.round(r.width * 100);
  $("#s-speed").value = r.max_speed; $("#s-drive").value = r.drive; setText("#s-speedv", r.max_speed.toFixed(2));
}
let fitTimer;
function refreshFit() {
  clearTimeout(fitTimer);
  fitTimer = setTimeout(guard(async () => {
    const map = $("#s-map").value, rover = $("#s-rover").value;
    const r = await api("/api/sim/fitcheck", { map, rover, overrides: currentOverrides() });
    S.fit = r.fit;
    $("#s-fit").innerHTML = Object.entries(r.fit).map(([id, f]) =>
      `<span class="chip ${f.fits ? (f.can_turn ? "ok" : "warn") : "no"}" title="${esc(f.reason)}">${esc(id)}: ${f.fits ? (f.can_turn ? "fits" : "fits, can't turn") : "too big"}</span>`).join("");
    drawSimMap();
  }), 150);
}
function drawSimMap() {
  // while a simulation is running show *its* map (with rover + findings); otherwise preview the selected one
  const live = S.sim?.map && (S.sim.webots_running || S.sim.link.startsWith("connected")) ? S.sim.map : null;
  const m = S.cat.maps[live || $("#s-map").value];
  if (!m) return;
  const showLive = live === m.name;
  drawMap($("#s-svg"), m, showLive ? S.sim : null,
    { fit: showLive ? null : S.fit, truth: $("#s-truth").checked && showLive && S.truth ? S.truth : null });
}
function renderSim() {
  const s = S.sim;
  setText("#s-st-link", s.link.replace("connected: ", "ok: ")); setText("#s-st-mis", s.mission_state);
  setText("#s-st-fps", s.fps_in); setText("#s-st-det", s.det_ms); setText("#s-st-def", s.defects.length);
  setText("#s-detname", s.detector ? "(" + s.detector + ")" : "");
  $("#s-defects").innerHTML = defectRows(s.defects, "sim");
  $("#s-log").innerHTML = logRows(s.log);
  if (s.evaluation) {
    const e = s.evaluation;
    $("#s-eval").innerHTML = `Truth check: found <b>${e.true_positive}</b> of <b>${e.true_positive + e.missed}</b> known defects &middot; recall <b>${(e.recall * 100).toFixed(0)}%</b> &middot; precision <b>${(e.precision * 100).toFixed(0)}%</b> &middot; false alarms ${e.false_positive}`;
  } else $("#s-eval").textContent = s.mission_state === "running" ? "Mission running..." : "";
  const rp = $("#s-report");
  if (s.report) { rp.hidden = false; rp.href = `/reports/${s.report}/report.html`; } else rp.hidden = true;
  if (s.map && S.truthMap !== s.map) { S.truthMap = s.map; api("/api/sim/truth").then(t => { S.truth = t.defects; }).catch(() => { }); }
  drawSimMap();
}
async function initSim() {
  const c = S.cat;
  $("#s-map").innerHTML = Object.keys(c.maps).map(m => `<option>${esc(m)}</option>`).join("");
  if (c.maps.straight_t) $("#s-map").value = "straight_t";
  $("#s-rover").innerHTML = Object.keys(c.rovers).map(r => `<option>${esc(r)}</option>`).join("");
  $("#s-rover").value = c.rovers.mid_4wd ? "mid_4wd" : Object.keys(c.rovers)[0];
  $("#s-det").innerHTML = c.detectors.map(d => `<option value="${esc(d.id)}">${esc(d.label)}</option>`).join("");
  $("#s-det").value = c.default_detector;
  const onMap = () => {
    const m = c.maps[$("#s-map").value];
    $("#s-goal").innerHTML = Object.entries(m.goals).map(([id, g]) => `<option value="${esc(id)}">${esc(id)} (${esc(g.color)})</option>`).join("");
    $("#s-mapinfo").textContent = `${Object.keys(m.nodes).length} chambers, ${Object.keys(m.edges).length} pipes, ${m.defects.length} planted defects`;
    refreshFit();
  };
  $("#s-map").onchange = onMap;
  $("#s-rover").onchange = () => { fillRoverFields(); refreshFit(); };
  ["#s-len", "#s-wid", "#s-drive"].forEach(i => $(i).addEventListener("input", refreshFit));
  $("#s-speed").addEventListener("input", () => { setText("#s-speedv", (+$("#s-speed").value).toFixed(2)); });
  $("#s-truth").onchange = drawSimMap;
  $("#s-mission").onchange = guard(async () => { if (S.sim?.webots_running) await api("/api/sim/mission", { mode: $("#s-mission").value, goal: $("#s-goal").value }); });
  $("#s-det").onchange = guard(() => api("/api/sim/detector", { id: $("#s-det").value }));
  $("#s-start").onclick = guard(async () => {
    $("#s-msg").textContent = "Generating world and launching Webots...";
    try {
      const r = await api("/api/sim/start", {
        map: $("#s-map").value, rover: $("#s-rover").value, overrides: currentOverrides(), detector: $("#s-det").value,
        mission: { mode: $("#s-mission").value, goal: $("#s-goal").value }, ambient: +$("#s-amb").value, mode: $("#s-mode").value
      });
      $("#s-msg").textContent = "Webots is starting; the camera appears when the rover connects (about 15-30 s).";
      S.truthMap = null;
    } catch (e) { $("#s-msg").textContent = ""; throw e; }
  });
  $("#s-stop").onclick = guard(async () => { await api("/api/sim/stop", {}); $("#s-msg").textContent = "Stopped."; });
  fillRoverFields(); onMap();
  padSetup($("#s-pad"), (v, w) => api("/api/sim/drive", { v, w }).catch(() => { }), () => S.sim?.rover?.max_speed ?? 0.4, () => S.sim?.rover?.max_yaw_rate ?? 1.0, "sim");
}

/* ---------------------------------------------------------------- manual drive (pad + keyboard) */
const keys = new Set();
const padHandlers = {};
function padSetup(pad, send, vmax, wmax, tab) {
  const press = (k, on) => { on ? keys.add(k) : keys.delete(k); };
  $$("button[data-k]", pad).forEach(b => {
    const k = b.dataset.k;
    ["pointerdown"].forEach(ev => b.addEventListener(ev, e => { e.preventDefault(); b.setPointerCapture(e.pointerId); press(k, true); }));
    ["pointerup", "pointercancel", "lostpointercapture"].forEach(ev => b.addEventListener(ev, () => press(k, false)));
  });
  padHandlers[tab] = { send, vmax, wmax };
}
const KEYMAP = { ArrowUp: "up", w: "up", ArrowDown: "down", s: "down", ArrowLeft: "left", a: "left", ArrowRight: "right", d: "right" };
addEventListener("keydown", e => { if (/input|select|textarea/i.test(e.target.tagName) && e.target.type !== "range") return; const k = KEYMAP[e.key]; if (k) { keys.add(k); e.preventDefault(); } });
addEventListener("keyup", e => { const k = KEYMAP[e.key]; if (k) keys.delete(k); });
addEventListener("blur", () => keys.clear());
setInterval(() => {
  const h = padHandlers[S.tab];
  if (!h) return;
  const fwd = (keys.has("up") ? 1 : 0) - (keys.has("down") ? 1 : 0), turn = (keys.has("left") ? 1 : 0) - (keys.has("right") ? 1 : 0);
  if (fwd || turn) h.send(fwd * h.vmax() * 0.8, turn * h.wmax() * 0.8);
  else if (h.wasMoving) h.send(0, 0);
  h.wasMoving = !!(fwd || turn);
}, 100);

/* ---------------------------------------------------------------- HARDWARE */
const WIRING = `
<details><summary>Power: 4&times;AA NiMH (about 4.8 V)</summary>
<p class="note warn">The ESP32 boards use an AMS1117 regulator that needs about 4.6 V or more. Fresh NiMH cells sag below that under motor load, so powering the boards straight from the pack causes brown-out resets.</p>
<pre class="code">4xAA pack (+) ──┬──────────────► DRV8833 VM  (+ 470uF across VM/GND, 10uF ceramic close to the chip)
                ├──► 5 V boost module (MT3608-class, set to ~5.1 V, 2 A)
                │       OUT+ ──┬─► ESP32 DevKit  5V/VIN  (+ 470uF)
                │              └─► ESP32-CAM     5V     (+ 470uF)
                └──► divider 100k / 100k ──► ESP32 GPIO34 (battery sense)
pack (-) ───────┴──── common GND with every board</pre>
<p class="note">Firmware cuts the motors and latches at about 4.0 V (1.0 V/cell) to protect the cells.</p></details>
<details><summary>ESP32 DevKit &rarr; DRV8833</summary>
<pre class="code">GPIO25 → AIN1     GPIO27 → BIN1     (left motor = A, right motor = B)
GPIO26 → AIN2     GPIO14 → BIN2
GPIO33 → nSLEEP   GPIO32 ← nFAULT (open drain, pull-up)
GPIO34 ← battery divider midpoint (ADC1, safe with Wi-Fi)
GPIO2  → status LED (built in)     3V3 → DRV8833 logic is not needed (VM only)</pre></details>
<details><summary>Flash the ESP32-CAM with your Arduino</summary>
<pre class="code">Arduino RESET ─► Arduino GND      (turns the Arduino into a plain USB-serial bridge)
Arduino 5V   ─► ESP32-CAM 5V      (use the boost/5 V supply if it resets while flashing)
Arduino GND  ─► ESP32-CAM GND
Arduino RX(0)─► ESP32-CAM U0R     Arduino TX(1) ─► ESP32-CAM U0T      (RX to RX, TX to TX)
ESP32-CAM IO0 ─► GND  while flashing; remove it, then press the board's RST button</pre>
<p class="note">Upload with: <code>arduino-cli upload -p COMx --fqbn esp32:esp32:esp32cam firmware/esp32cam_stream</code> after editing <code>config.h</code>.</p></details>`;

function renderHw() {
  const s = S.hw;
  setText("#h-st-rover", s.rover_connected ? "online" : "offline"); setText("#h-st-cam", s.cam_connected ? "online" : "offline");
  setText("#h-st-fps", s.cam_fps); setText("#h-st-det", s.det_ms);
  const t = s.telemetry || {};
  setText("#h-st-bat", t.vbat != null ? t.vbat.toFixed(2) : "-"); setText("#h-st-rssi", t.rssi ?? "-");
  const pct = t.vbat != null ? Math.max(0, Math.min(100, (t.vbat - 4.0) / (5.5 - 4.0) * 100)) : 0;
  const bar = $("#h-bat"); bar.style.width = pct + "%"; bar.style.background = pct < 20 ? "var(--bad)" : pct < 45 ? "var(--warn)" : "var(--ok)";
  setText("#h-s", s.s.toFixed(2) + " m"); setText("#h-fault", t.fault ? "FAULT" : t.fault === 0 ? "ok" : "-"); setText("#h-rtt", s.rover_rtt_ms ? s.rover_rtt_ms + " ms" : "-");
  setText("#h-detname", s.detector ? "(" + s.detector + ")" : "");
  const w = [];
  const usb = s.rover_link === "serial", info = s.rover_info || {};
  $("#h-armcard").hidden = !usb; $("#h-usbbox").hidden = !usb; $("#c-usbnote").hidden = !usb;
  if (usb) {
    const box = $("#h-checks");
    if (box.children.length !== s.pre_drive.length) box.innerHTML = s.pre_drive.map(c => `<label class="check"><input type="checkbox" data-id="${esc(c.id)}"> ${esc(c.text)}</label>`).join("");
    const pill = $("#h-armpill"); pill.textContent = s.armed ? "drive unlocked" : "locked"; pill.className = "pill " + (s.armed ? "ok" : "warn");
    setText("#h-st-rover", s.rover_connected ? "USB online" : info.error ? "USB error" : "USB starting");
    setText("#h-dist", info.dist_cm != null ? info.dist_cm.toFixed(0) + " cm" : info.dist_age_s == null ? "not fitted" : "no reading");
    setText("#h-ir", info.ir == null ? "not fitted" : info.ir ? "OBSTACLE" : "clear");
    setText("#h-line", info.last_line || "-");
    $("#h-blocked").textContent = info.blocked ? "Forward is blocked: " + info.blocked + "." : "";
    if (info.error) w.push(info.error);
    const lv = $("#h-level"); if (document.activeElement !== lv) lv.value = info.level; setText("#h-levelv", info.level);
    if (!info.firmware_telemetry && s.rover_connected) w.push("This firmware sends no sensor data (the older rover_wasd sketch). Driving works; distance and IR readouts need firmware/esp32_rover_serial.");
  }
  if (t.latched) w.push("Battery cut-off latched: swap/charge the cells, then press 'Reset battery latch'.");
  if (t.fault) w.push("DRV8833 reports a fault (over-current or over-temperature).");
  if (s.cam_error && !s.cam_connected) w.push("Camera: " + s.cam_error);
  if (s.fit && !s.fit.fits) w.push(s.fit.reason);
  else if (s.fit && !s.fit.can_turn) w.push(s.fit.reason);
  $("#h-warn").textContent = w.join("  ");
  $("#h-defects").innerHTML = defectRows(s.defects, "hw");
  $("#h-log").innerHTML = logRows(s.log);
  setText("#h-capn", s.captured); setText("#h-rstate", s.replay === "idle" ? "" : "Replay: " + s.replay);
  if (!S.hwInit) {
    S.hwInit = true;
    const p = s.profile;
    $("#h-len").value = (p.length * 100).toFixed(1); $("#h-wid").value = (p.width * 100).toFixed(1);
    $("#h-ch").value = (p.camera.height * 100).toFixed(1); $("#h-ct").value = p.camera.tilt_deg; $("#h-fov").value = p.camera.fov_deg;
    $("#h-pipe").value = Math.round(s.pipe_diameter * 100);
    $("#h-rover").value = s.rover_host || ""; $("#h-cam").value = s.cam_url || "";
  }
  const f = s.fit;
  $("#h-fit").innerHTML = f ? `<span class="chip ${f.fits ? (f.can_turn ? "ok" : "warn") : "no"}">${f.fits ? (f.can_turn ? "fits the test pipe" : "fits, cannot turn in it") : "does not fit the test pipe"}</span>` : "";
}
function initHw() {
  $("#h-wiring").innerHTML = WIRING;
  $("#h-connect").onclick = guard(async () => { await api("/api/hw/connect", { rover_host: $("#h-rover").value, cam_url: $("#h-cam").value }); toast("Connecting..."); });
  $("#h-disconnect").onclick = guard(() => api("/api/hw/disconnect", {}));
  $("#h-mock").onclick = guard(async () => { const r = await api("/api/hw/mock/start", {}); $("#h-rover").value = r.rover_host || "127.0.0.1"; $("#h-cam").value = r.cam_url || "http://127.0.0.1:8081/stream"; toast("Emulator running and connected"); });
  $("#h-mockstop").onclick = guard(() => api("/api/hw/mock/stop", {}));
  $("#h-setup").onclick = guard(async () => {
    const r = await api("/api/hw/setup", { pipe_diameter: +$("#h-pipe").value / 100, profile: {
      length: +$("#h-len").value / 100, width: +$("#h-wid").value / 100,
      camera: { height: +$("#h-ch").value / 100, tilt_deg: +$("#h-ct").value, fov_deg: +$("#h-fov").value } } });
    toast(r.fit.fits ? "Setup saved" : "Saved - " + r.fit.reason, !r.fit.fits);
  });
  $("#h-speed").addEventListener("input", () => setText("#h-speedv", (+$("#h-speed").value).toFixed(2))); setText("#h-speedv", "0.12");
  $("#h-stop").onclick = guard(() => api("/api/hw/stop", {}));
  $("#h-reset").onclick = guard(() => api("/api/hw/reset-latch", {}));
  $("#h-findports").onclick = guard(async () => {
    const r = await api("/api/hw/ports");
    $("#h-ports").innerHTML = r.ports.map(p => `<option value="${esc(p.port)}">${esc(p.description)}</option>`).join("");
    $("#h-portnote").textContent = r.ports.length ? r.ports.map(p => p.port + " (" + p.description + ")").join(" · ") : "no USB serial ports found";
    if (r.ports.length === 1 && !$("#h-rover").value) $("#h-rover").value = r.ports[0].port;
  });
  $("#h-mockusb").onclick = guard(async () => { const r = await api("/api/hw/mock/start", { serial: true }); $("#h-rover").value = r.rover_host; $("#h-cam").value = r.cam_url; toast("USB emulator running and connected"); });
  $("#h-arm").onclick = guard(async () => {
    const checklist = {}; $$("#h-checks input").forEach(i => { checklist[i.dataset.id] = i.checked; });
    await api("/api/hw/arm", { checklist }); toast("Drive controls unlocked");
  });
  $("#h-disarm").onclick = guard(async () => { await api("/api/hw/disarm", {}); $$("#h-checks input").forEach(i => { i.checked = false; }); toast("Locked, rover stopped"); });
  $("#h-level").onchange = guard(() => api("/api/hw/speed-level", { level: +$("#h-level").value }));
  $("#h-det").innerHTML = S.cat.detectors.map(d => `<option value="${esc(d.id)}">${esc(d.label)}</option>`).join("");
  $("#h-det").value = S.cat.default_detector;
  $("#h-det").onchange = guard(() => api("/api/hw/detector", { id: $("#h-det").value }));
  $("#h-cap").onclick = guard(async () => { const r = await api("/api/hw/capture", {}); toast("Saved " + r.saved); });
  $("#h-auto").onchange = guard(() => api("/api/hw/autocapture", { seconds: +$("#h-auto").value }));
  $("#h-rplay").onclick = guard(() => api("/api/hw/replay", { path: $("#h-rpath").value }));
  $("#h-rstop").onclick = guard(() => api("/api/hw/replay/stop", {}));
  $("#c-srun").onclick = guard(() => api("/api/hw/calibrate/run", { kind: "speed", value: +$("#c-sv").value, seconds: +$("#c-st").value }));
  $("#c-sapply").onclick = guard(async () => { const r = await api("/api/hw/calibrate/speed", { v: +$("#c-sv").value, seconds: +$("#c-st").value, measured_m: +$("#c-sm").value / 100 }); toast(`Top speed ${r.vmax_old} → ${r.vmax_new} m/s`); });
  $("#c-wrun").onclick = guard(() => api("/api/hw/calibrate/run", { kind: "yaw", value: +$("#c-wv").value, seconds: +$("#c-wt").value }));
  $("#c-wapply").onclick = guard(async () => { const r = await api("/api/hw/calibrate/yaw", { w: +$("#c-wv").value, seconds: +$("#c-wt").value, measured_deg: +$("#c-wm").value }); toast(`Yaw gain → ${r.yaw_gain_new}`); });
  $$(".fw").forEach(b => b.onclick = guard(async () => {
    const out = $("#h-fwout"); out.hidden = false; out.textContent = "Compiling " + b.dataset.t + " (first build can take a minute)...";
    const r = await api("/api/hw/firmware/compile?target=" + b.dataset.t, {});
    out.textContent = (r.ok ? "BUILD OK\n" : "BUILD FAILED\n") + r.output;
  }));
  let lockedToast = 0;
  padSetup($("#h-pad"), (v, w) => api("/api/hw/drive", { v, w }).catch(e => {
    if (v || w) { const now = Date.now(); if (now - lockedToast > 4000) { lockedToast = now; toast(e.message, true); } }
  }), () => +$("#h-speed").value, () => 1.0, "hw");
}

/* ---------------------------------------------------------------- boot */
(async function boot() {
  try {
    S.cat = await api("/api/catalog");
    await initSim(); initHw();
    connectWs("sim"); connectWs("hw");
    setTab({ "#hw": "hw", "#sim": "sim", "#train": "train", "#build": "build", "#lab": "lab", "#goal": "goal" }[location.hash] || "goal");
  } catch (e) { toast("Could not load catalog: " + e.message, true); }
})();
