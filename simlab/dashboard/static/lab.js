"use strict";
/* Lab tab: pick a vehicle + task + policy, edit the vehicle with a form generated from its schema, watch it run in 3D.
   Relies on helpers from app.js ($, $$, esc, api, toast, guard, S, padSetup). */
const LAB = { cat: null, spec: null, valid: false, state: null, viewer: null, sceneId: null, vmax: 1, wmax: 1, space: "unicycle", timer: null, lastHud: 0 };

const LAB_HTML = `
<div class="col">
  <div class="card">
    <h3>Run</h3>
    <label for="l-veh">Vehicle</label><select id="l-veh"></select>
    <div class="hint" id="l-vehinfo"></div>
    <label for="l-task">Task</label><select id="l-task"></select>
    <div class="hint" id="l-taskinfo"></div>
    <div class="row">
      <div><label for="l-pol">Policy</label><select id="l-pol"></select></div>
      <div><label for="l-spd">Speed</label><select id="l-spd">
        <option value="0.25">0.25x</option><option value="0.5">0.5x</option><option value="1" selected>1x real time</option>
        <option value="2">2x</option><option value="4">4x</option><option value="0">max</option></select></div>
    </div>
    <label for="l-det">Detector <span class="hint">draws boxes on the camera view</span></label><select id="l-det"></select>
    <div class="row">
      <div><label for="l-seed">Seed</label><input id="l-seed" type="number" value="1" step="1"></div>
      <label class="check"><input type="checkbox" id="l-loop" checked> keep running episodes</label>
    </div>
    <details id="l-cond"><summary>Conditions <span class="hint">waves, wind, current, mud: fixed for the run</span></summary><div id="l-condbody" class="sliders"></div></details>
    <div class="row" style="margin-top:12px">
      <button class="btn primary" id="l-start">Start</button>
      <button class="btn" id="l-reset" disabled>New episode</button>
      <button class="btn danger" id="l-stop" disabled>Stop</button>
    </div>
    <p class="note" id="l-msg"></p>
  </div>
  <div class="card">
    <h3>Vehicle designer <button class="btn small" id="l-save" title="Save this vehicle to config/vehicles">Save as...</button></h3>
    <div class="row import"><input id="l-imp" type="text" placeholder="Import a robot: path to an MJCF .xml or URDF file" aria-label="Model file to import"><button class="btn small" id="l-impgo" style="flex:0 0 auto">Import</button></div>
    <div id="l-form"></div>
    <div class="kv facts" id="l-derived"></div>
    <div id="l-issues"></div>
  </div>
  <div class="card" id="l-drivecard">
    <h3>Manual drive <span class="hint">arrows / WASD</span></h3>
    <div class="pad" id="l-pad">
      <span></span><button class="btn" data-k="up" aria-label="forward">&#9650;</button><span></span>
      <button class="btn" data-k="left" aria-label="turn left">&#9664;</button><button class="btn" data-k="down" aria-label="reverse">&#9660;</button><button class="btn" data-k="right" aria-label="turn right">&#9654;</button>
    </div>
    <div id="l-sliders" class="sliders" hidden></div>
    <p class="note" id="l-drivenote"></p>
  </div>
</div>
<div class="col">
  <div class="card">
    <h3>3D view <span class="seg" id="l-seg"><button data-v="follow" class="on">Follow</button><button data-v="orbit">Orbit</button><button data-v="top">Top</button></span></h3>
    <div class="view3d" id="l-view"><div class="hud" id="l-hud">press Start</div><div class="banner" id="l-banner" hidden></div></div>
    <p class="note">Drag to rotate, wheel to zoom, right-drag or Shift-drag to pan.</p>
  </div>
  <div class="two">
    <div class="card"><h3>Onboard camera <span class="hint" id="l-camname"></span></h3><div class="video"><img id="l-video" alt="Onboard camera"></div></div>
    <div class="card"><h3>Telemetry</h3><div class="stats" id="l-stats"></div>
      <table style="margin-top:10px"><thead><tr><th>reward term</th><th>this step</th></tr></thead><tbody id="l-terms"></tbody></table></div>
  </div>
</div>`;

const fmtv = (v, nd = 2) => (v === null || v === undefined || Number.isNaN(v)) ? "-" : (+v).toFixed(nd);
const clone = o => JSON.parse(JSON.stringify(o));

/* ---------------------------------------------------------------- form generated from the kind's parameter schema */
function fieldHtml(path, p, value) {
  const id = "f:" + path, tip = esc(p.doc || ""), unit = p.unit ? ` <i>${esc(p.unit)}</i>` : "";
  const label = `<label for="${esc(id)}" title="${tip}">${esc(p.name.replace(/_/g, " "))}${unit}</label>`;
  if (p.type === "enum") return `<div>${label}<select id="${esc(id)}" data-path="${esc(path)}" data-type="enum">${p.choices.map(c => `<option${c === value ? " selected" : ""}>${esc(c)}</option>`).join("")}</select></div>`;
  if (p.type === "bool") return `<div class="bool"><label title="${tip}"><input type="checkbox" id="${esc(id)}" data-path="${esc(path)}" data-type="bool"${value ? " checked" : ""}> ${esc(p.name.replace(/_/g, " "))}</label></div>`;
  if (p.type === "str") return `<div class="wide">${label}<input type="text" id="${esc(id)}" data-path="${esc(path)}" data-type="str" value="${esc(value)}"></div>`;
  if (p.type === "strlist") return `<div class="wide">${label}<input type="text" id="${esc(id)}" data-path="${esc(path)}" data-type="strlist" value="${esc((value || []).join(", "))}"></div>`;
  const step = p.type === "int" ? 1 : "any";
  return `<div>${label}<input type="number" id="${esc(id)}" data-path="${esc(path)}" data-type="${p.type}" value="${value}" step="${step}"${p.min != null ? ` min="${p.min}"` : ""}${p.max != null ? ` max="${p.max}"` : ""}></div>`;
}

/* Conditions: fixed stress for the run. Which sliders exist depends on the machine; values apply from the next Start. */
LAB.cond = {};
const LAB_CONDITIONS = {
  boat: [["wave_hs", "Wave height", "m", 0, 1.5, 0.05], ["wind_ms", "Wind", "m/s", 0, 20, 1], ["current_ms", "Current", "m/s", 0, 3, 0.1]],
  multirotor: [["wind_ms", "Wind", "m/s", 0, 20, 1]],
  ground_wheeled: [["grip_mu", "Grip (0.8 = dry)", "", 0.05, 0.8, 0.05], ["mud", "Mud and debris (share of pulling force)", "", 0, 0.9, 0.05]],
};
function labConditions() {
  const kind = LAB.spec && LAB.spec.kind, list = LAB_CONDITIONS[kind] || [];
  if (LAB.condKind === kind) return;                              // only rebuild when the machine type changes, not on every edit
  LAB.condKind = kind;
  $("#l-cond").hidden = !list.length;
  LAB.cond = {};
  $("#l-condbody").innerHTML = list.map(([k, name, unit, lo, hi, st]) => {
    const v = k === "grip_mu" ? 0.8 : 0;
    LAB.cond[k] = v;
    return `<label>${esc(name)} <b id="cv-${k}">${v}</b> ${esc(unit)}<input type="range" data-k="${k}" min="${lo}" max="${hi}" step="${st}" value="${v}"></label>`;
  }).join("");
  $$("#l-condbody input").forEach(el => { el.oninput = () => { LAB.cond[el.dataset.k] = +el.value; $("#cv-" + el.dataset.k).textContent = (+el.value).toFixed(2); }; });
}
function labHazards() {
  const h = { ...LAB.cond };
  if ("mud" in h) { h.drag_ms2 = h.mud * ((LAB.spec && LAB.spec.params && LAB.spec.params.max_accel) || 0.5); delete h.mud; }
  return h;
}

function buildForm() {
  const spec = LAB.spec, kind = LAB.cat.kinds[spec.kind];
  const body = kind.body_schema.map(p => fieldHtml("body." + p.name, p, spec.body[p.name])).join("");
  const params = kind.schema.map(p => fieldHtml("params." + p.name, p, spec.params[p.name])).join("");
  const sensors = spec.sensors.map((s, i) => `<details><summary>${esc(s.type)}: ${esc(s.name)}</summary><div class="fields">` +
    LAB.cat.sensors[s.type].map(p => fieldHtml(`sensors.${i}.${p.name}`, p, s[p.name])).join("") + `</div></details>`).join("");
  $("#l-form").innerHTML = `<div class="hint">${esc(kind.title)}</div>
    <details open><summary>Body</summary><div class="fields">${body}</div></details>
    <details open><summary>${esc(kind.title)}</summary><div class="fields">${params}</div></details>${sensors}`;
  $$("#l-form [data-path]").forEach(el => el.addEventListener("input", onEdit));
  $$("#l-form select[data-path]").forEach(el => el.addEventListener("change", onEdit));
}

function setPath(obj, path, v) {
  const parts = path.split(".");
  let o = obj;
  for (const k of parts.slice(0, -1)) o = o[k];
  o[parts.at(-1)] = v;
}

function onEdit(e) {
  const el = e.target, t = el.dataset.type;
  let v = t === "bool" ? el.checked : t === "enum" || t === "str" ? el.value : t === "strlist" ? el.value.split(",").map(s => s.trim()).filter(Boolean)
    : el.value === "" ? NaN : +el.value;
  setPath(LAB.spec, el.dataset.path, v);
  clearTimeout(LAB.timer);
  LAB.timer = setTimeout(guard(revalidate), 200);
}

async function revalidate() {
  labConditions();
  const r = await api("/api/lab/validate", { vehicle: LAB.spec });
  LAB.valid = r.ok;
  const lvl = i => i.level === "error" ? "bad" : "warn";
  $("#l-issues").innerHTML = r.issues.map(i => `<p class="note ${lvl(i) === "bad" ? "err" : "warn"}"><b>${esc(i.field)}</b> ${esc(i.msg)}</p>`).join("")
    || (r.ok ? `<p class="note ok">Vehicle checks passed.</p>` : "");
  $("#l-derived").innerHTML = r.derived.map(f => `<span>${esc(f.label)}</span><b>${fmtv(f.value, Math.abs(f.value) >= 100 ? 0 : 2)} ${esc(f.unit)}</b>`).join("");
  $("#l-start").disabled = !r.ok;
  if (r.ok) {
    const sp = r.spaces[LAB.spec.default_space], ch = Object.fromEntries(sp.channels.map(c => [c.name, c]));
    LAB.space = LAB.spec.default_space;
    LAB.vmax = ch.v ? ch.v.hi : ch.vx ? ch.vx.hi : 1; LAB.wmax = ch.w ? ch.w.hi : ch.yaw_rate ? ch.yaw_rate.hi : 1;
    LAB.ch = ch;
    const keys = LAB.space === "unicycle" || LAB.space === "velocity";
    $("#l-pad").hidden = !keys;
    $("#l-drivenote").textContent = LAB.space === "velocity"
      ? "Up/Down: forward/back. Left/Right: turn. Q/E: slide left/right. R/F: climb/descend. The autopilot keeps it level."
      : keys ? "" : "Move the sliders to command each channel; they keep their value until you change them.";
    buildSliders(keys ? [] : sp.channels);
  }
}

/* one slider per command channel for spaces that have no key mapping (imported models: 'raw', boats: 'helm', drone motors) */
LAB.sliders = {};
function buildSliders(channels) {
  const box = $("#l-sliders");
  box.hidden = !channels.length;
  LAB.sliders = {};
  box.innerHTML = channels.map(c => `<label>${esc(c.name)} <b id="sv-${esc(c.name)}">0</b>${c.unit ? " " + esc(c.unit) : ""}
    <input type="range" data-ch="${esc(c.name)}" min="${c.lo}" max="${c.hi}" step="${(c.hi - c.lo) / 200 || 0.01}" value="${Math.max(c.lo, Math.min(c.hi, 0))}"></label>`).join("") +
    (channels.length ? `<button class="btn small" id="l-zero">Zero all</button>` : "");
  $$("#l-sliders input").forEach(el => { LAB.sliders[el.dataset.ch] = 0; el.oninput = () => { LAB.sliders[el.dataset.ch] = +el.value; $("#sv-" + el.dataset.ch).textContent = (+el.value).toFixed(2); }; });
  if (channels.length) $("#l-zero").onclick = () => $$("#l-sliders input").forEach(el => { el.value = 0; el.oninput(); });
}
let slidersWereActive = false;
setInterval(() => {
  if (S.tab !== "lab" || !Object.keys(LAB.sliders).length) return;
  const active = Object.values(LAB.sliders).some(v => v !== 0);
  if (active || slidersWereActive) api("/api/lab/command", { u: LAB.sliders }).catch(() => { });
  slidersWereActive = active;
}, 100);

/* keys beyond the arrows/WASD handled by app.js: strafe and climb for flying machines */
const FLY_KEYS = { q: "slideL", e: "slideR", r: "up", f: "down", PageUp: "up", PageDown: "down" };
const flyHeld = new Set();
addEventListener("keydown", e => { if (S.tab !== "lab" || /input|select|textarea/i.test(e.target.tagName)) return; const k = FLY_KEYS[e.key]; if (k) { flyHeld.add(k); e.preventDefault(); } });
addEventListener("keyup", e => { const k = FLY_KEYS[e.key]; if (k) flyHeld.delete(k); });
addEventListener("blur", () => flyHeld.clear());
let flyMoving = false;
setInterval(() => {
  if (S.tab !== "lab" || LAB.space !== "velocity" || !LAB.ch) return;
  const held = k => (keys.has(k) ? 1 : 0), ax = held("up") - held("down"), yaw = held("left") - held("right");
  const sl = (flyHeld.has("slideL") ? 1 : 0) - (flyHeld.has("slideR") ? 1 : 0), cl = (flyHeld.has("up") ? 1 : 0) - (flyHeld.has("down") ? 1 : 0);
  const any = ax || yaw || sl || cl;
  if (!any && !flyMoving) return;
  flyMoving = !!any;
  const c = LAB.ch;
  api("/api/lab/command", { u: { vx: ax * 0.6 * c.vx.hi, vy: sl * 0.6 * c.vy.hi, vz: cl > 0 ? cl * 0.7 * c.vz.hi : cl * 0.7 * -c.vz.lo, yaw_rate: yaw * 0.7 * c.yaw_rate.hi } }).catch(() => { });
}, 100);

/* ---------------------------------------------------------------- selectors */
function pickVehicle(name) {
  const v = LAB.cat.vehicles[name];
  LAB.spec = clone(v.spec);
  $("#l-vehinfo").textContent = v.description;
  buildForm();
  const tasks = Object.entries(LAB.cat.tasks).filter(([, t]) => (!t.kinds.length || t.kinds.includes(v.kind)) && t.backends.includes("mujoco"));
  $("#l-task").innerHTML = tasks.length ? tasks.map(([n, t]) => `<option value="${esc(n)}">${esc(n)}</option>`).join("") : `<option value="">(no task for ${esc(v.kind)} yet)</option>`;
  const preferred = [...(v.kind === "boat" ? ["sandbox_water"] : []), "goto_open", "waypoints_easy"].find(p => tasks.some(([n]) => n === p));
  if (preferred) $("#l-task").value = preferred;
  pickTask();
  guard(revalidate)();
}
function pickTask() {
  const t = LAB.cat.tasks[$("#l-task").value];
  $("#l-taskinfo").textContent = t ? t.description : "";
  refreshPolicies($("#l-pol").value);
  refreshDetectors($("#l-det").value);
}

/* detectors trained for exactly the classes this task labels (the same objects a camera should find) */
function refreshDetectors(keep) {
  const want = JSON.stringify([...(LAB.cat.tasks[$("#l-task").value]?.spec.labels || [])].sort());
  const dets = (LAB.cat.detectors || []).filter(d => JSON.stringify([...(d.classes || [])].sort()) === want);
  $("#l-det").innerHTML = `<option value="">none</option>` + dets.map(d => `<option value="${esc(d.path)}">${esc(d.vehicle)} / ${esc(d.task)}${d.mean_f1 != null ? " &middot; F1 " + d.mean_f1.toFixed(2) : ""} (${esc(d.id.slice(0, 15))})</option>`).join("");
  if (keep && [...$("#l-det").options].some(o => o.value === keep)) $("#l-det").value = keep;
}

/* baseline / manual / idle, plus every trained model made for this kind of vehicle and this task */
function refreshPolicies(keep) {
  const kind = LAB.spec?.kind, task = $("#l-task").value;
  const models = (LAB.cat.models || []).filter(m => m.kind === kind && m.task === task);
  const hasBaseline = LAB.cat.tasks[task]?.baseline !== false;          // e.g. the sandbox has nothing to automate
  if (!hasBaseline && (!keep || keep === "baseline")) keep = "manual";
  $("#l-pol").innerHTML = LAB.cat.policies.filter(p => p !== "baseline" || hasBaseline).map(p => `<option>${esc(p)}</option>`).join("") +
    models.map(m => `<option value="model:${esc(m.path)}">trained: ${esc(m.vehicle)}${m.best_eval != null ? " " + Math.round(100 * m.best_eval) + "%" : ""} (${esc(m.id.slice(0, 15))})</option>`).join("");
  if (keep && [...$("#l-pol").options].some(o => o.value === keep)) $("#l-pol").value = keep;
}

/* open a finished training run in the Lab: same vehicle, same task, the trained policy driving */
async function labOpen(spec, task) {
  setTab("lab");
  LAB.cat = await api("/api/lab/catalog");
  if (LAB.cat.vehicles[spec.name]) { $("#l-veh").value = spec.name; pickVehicle(spec.name); }
  LAB.spec = clone(spec);
  buildForm();
  await revalidate();
  if (LAB.cat.tasks[task.name]) $("#l-task").value = task.name;
  refreshPolicies();
  refreshDetectors();
}

async function labWatch(run) {
  const spec = run.vehicle_spec, task = run.task_spec, pol = "model:" + run.model;
  await labOpen(spec, task);
  if (![...$("#l-pol").options].some(o => o.value === pol)) $("#l-pol").insertAdjacentHTML("beforeend", `<option value="${esc(pol)}">trained: ${esc(run.id.slice(0, 15))}</option>`);
  $("#l-pol").value = pol;
  await api("/api/lab/start", { vehicle: spec, task, policy: pol, speed: 1, seed: 1, loop: true });
  LAB.sceneId = null;
}

/* a finished detector run: the baseline drives, the trained detector draws its boxes on the camera */
async function labTryDetector(run) {
  await labOpen(run.vehicle_spec, run.task_spec);
  $("#l-pol").value = "baseline";
  if ([...$("#l-det").options].some(o => o.value === run.model)) $("#l-det").value = run.model;
  await api("/api/lab/start", { vehicle: run.vehicle_spec, task: run.task_spec, policy: "baseline", speed: 1, seed: 1, loop: true, detector: run.model });
  LAB.sceneId = null;
}

/* ---------------------------------------------------------------- run control */
const labRunning = () => !!(LAB.state && LAB.state.running && LAB.state.status !== "idle");

async function labStart() {
  if (!$("#l-task").value) throw new Error("this vehicle has no task yet");
  await api("/api/lab/start", { vehicle: LAB.spec, task: $("#l-task").value, policy: $("#l-pol").value, speed: +$("#l-spd").value,
    seed: +$("#l-seed").value || 0, loop: $("#l-loop").checked, detector: $("#l-det").value || null, hazards: labHazards() });
  LAB.sceneId = null;
  $("#l-msg").textContent = "Starting...";
}

function setButtons() {
  const run = labRunning();
  $("#l-start").disabled = !LAB.valid || (run && LAB.state.status === "starting");
  $("#l-stop").disabled = !run; $("#l-reset").disabled = !run;
  const img = $("#l-video"), want = run && S.tab === "lab" && LAB.state.camera;
  if (want && !img.dataset.on) { img.src = "/api/lab/video?t=" + Date.now(); img.dataset.on = "1"; }
  if (!want && img.dataset.on) { img.removeAttribute("src"); delete img.dataset.on; }
}

window.labPills = () => {
  const s = LAB.state;
  if (!s) return `<span class="pill">connecting...</span>`;
  const cls = s.status === "error" ? "bad" : s.running && s.status === "running" ? "ok" : s.running ? "warn" : "";
  return [`<span class="pill ${cls}">${esc(s.status || "idle")}</span>`, s.vehicle ? `<span class="pill">${esc(s.vehicle)}</span>` : "",
    s.policy ? `<span class="pill">policy: ${esc(s.policy)}</span>` : "", s.sim_fps ? `<span class="pill">${s.sim_fps} steps/s</span>` : ""].join("");
};

function onState(s) {
  LAB.state = s;
  if (s.status === "error") $("#l-msg").textContent = s.error;
  else if (s.running && s.status !== "starting") $("#l-msg").textContent = "";
  if (s.scene_id && s.scene_id !== LAB.sceneId) {
    LAB.sceneId = s.scene_id;
    api("/api/lab/scene").then(sc => { ensureViewer().loadScene(sc); $("#l-camname").textContent = (sc.cameras || [])[0] || "no camera"; }).catch(() => { });
  }
  if (LAB.viewer && s.t !== undefined && LAB.lastT !== undefined && s.t < LAB.lastT - 0.5) LAB.viewer.resetTrail();   // a new episode began
  LAB.lastT = s.t;
  if (s.poses && LAB.viewer) { LAB.viewer.update(s.poses, s.env); LAB.viewer.setMarkers(s.markers); }
  if (s.policy && $("#l-pol").value !== s.policy && document.activeElement !== $("#l-pol")) $("#l-pol").value = s.policy;
  const now = performance.now();
  if (now - LAB.lastHud > 100) { LAB.lastHud = now; renderTelemetry(s); }
  setButtons();
  if (S.tab === "lab") renderPills();
}

function renderTelemetry(s) {
  if (!s.running || s.pos === undefined) { $("#l-hud").textContent = s.status === "error" ? "error" : "press Start"; return; }
  const i = s.info || {};
  const flying = s.pos[2] > 0.3 || LAB.spec?.kind === "multirotor";
  $("#l-hud").innerHTML = `t ${fmtv(s.t, 1)} s &nbsp; ${fmtv(s.speed)} m/s` + (flying ? ` &nbsp; alt ${fmtv(s.pos[2], 1)} m` : "") +
    (i.dist !== undefined ? ` &nbsp; target ${fmtv(i.dist, 1)} m` : "") + (s.collided ? ` &nbsp; <b class="bad">contact</b>` : "");
  const st = s.stats, rate = st.episodes ? Math.round(100 * st.successes / st.episodes) + " %" : "-";
  const stat = (v, l) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`;
  const bat = s.battery;
  $("#l-stats").innerHTML = stat(fmtv(s.t, 1) + " s", "sim time") + stat(fmtv(s.speed) + " m/s", "speed") +
    stat(flying ? fmtv(s.pos[2], 2) + " m" : fmtv(s.yaw_deg, 0) + "°", flying ? "altitude" : "heading") +
    stat(i.dist !== undefined ? fmtv(i.dist, 2) + " m" : "-", "distance to target") + stat(fmtv(s.return, 1), "episode return") +
    stat(`${st.successes}/${st.episodes} (${rate})`, "episodes succeeded") + stat(st.mean_time ? fmtv(st.mean_time, 1) + " s" : "-", "mean time to succeed") +
    (bat ? stat(`${fmtv(100 * bat.soc, 0)} % / ${fmtv(bat.voltage, 1)} V`, "battery") : stat(st.collisions, "contact steps")) +
    (s.detector ? stat(s.detections.length ? s.detections.map(d => d.cls).join(", ") : "nothing", `detector${s.detector_ms ? " " + s.detector_ms + " ms" : ""}`) : "") +
    stat(Object.entries(s.cmd).map(([k, v]) => `${k} ${fmtv(v)}`).join(" "), "command");
  $("#l-terms").innerHTML = Object.entries(s.terms).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${fmtv(v, 3)}</td></tr>`).join("");
  const b = $("#l-banner"), r = s.result;
  if (r) { b.hidden = false; b.className = "banner " + (r.success ? "okb" : "badb"); b.textContent = r.success ? `Succeeded in ${r.t} s` : r.crash ? `Failed: ${r.crash}` : r.timeout ? "Time ran out" : "Episode ended"; }
  else b.hidden = true;
}

function ensureViewer() {
  if (!LAB.viewer) LAB.viewer = new Viewer3D($("#l-view"));
  return LAB.viewer;
}

function connectLab() {
  const ws = new WebSocket(`ws://${location.host}/ws/lab`);
  ws.onmessage = e => onState(JSON.parse(e.data));
  ws.onclose = () => setTimeout(connectLab, 1500);
}

/* ---------------------------------------------------------------- init */
(async function initLab() {
  const sec = $("#tab-lab");
  if (!sec) return;
  sec.innerHTML = LAB_HTML;
  try { LAB.cat = await api("/api/lab/catalog"); } catch (e) { toast("Lab catalog failed: " + e.message, true); return; }
  const byKind = {};
  for (const [n, v] of Object.entries(LAB.cat.vehicles)) (byKind[v.kind] ||= []).push(n);
  // vehicles that have a task to run come first, so the default selection is something that can start
  const runnable = k => Object.values(LAB.cat.tasks).some(t => !t.kinds.length || t.kinds.includes(k));
  const kindOrder = Object.keys(byKind).sort((a, b) => runnable(b) - runnable(a) || a.localeCompare(b));
  $("#l-veh").innerHTML = kindOrder.map(k => `<optgroup label="${esc(LAB.cat.kinds[k].title)}">${byKind[k].map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join("")}</optgroup>`).join("");
  if (LAB.cat.vehicles.mid_4wd) $("#l-veh").value = "mid_4wd";
  $("#l-veh").onchange = () => pickVehicle($("#l-veh").value);
  $("#l-task").onchange = pickTask;
  $("#l-start").onclick = guard(labStart);
  $("#l-stop").onclick = guard(() => api("/api/lab/stop", {}));
  $("#l-reset").onclick = guard(() => { LAB.viewer?.resetTrail(); return api("/api/lab/reset", {}); });
  $("#l-pol").onchange = guard(() => labRunning() ? api("/api/lab/policy", { policy: $("#l-pol").value }) : 0);
  $("#l-spd").onchange = guard(() => labRunning() ? api("/api/lab/speed", { speed: +$("#l-spd").value }) : 0);
  $("#l-save").onclick = guard(async () => {
    const name = prompt("Save this vehicle as (letters, digits, _ . -):", LAB.spec.name + "_custom");
    if (!name) return;
    const r = await api("/api/lab/save", { vehicle: { ...clone(LAB.spec), name } });
    toast("Saved " + r.path);
    LAB.cat = await api("/api/lab/catalog");
  });
  $("#l-impgo").onclick = guard(async () => {
    const path = $("#l-imp").value.trim();
    if (!path) throw new Error("type the path of an .xml or .urdf file");
    const r = await api("/api/lab/import", { path });
    LAB.spec = clone(r.spec);
    if (!$$("#l-veh option").some(o => o.value === r.spec.name)) $("#l-veh").insertAdjacentHTML("afterbegin", `<option value="${esc(r.spec.name)}">${esc(r.spec.name)} (imported)</option>`);
    $("#l-veh").value = r.spec.name;
    const tasks = Object.entries(LAB.cat.tasks).filter(([, t]) => (!t.kinds.length || t.kinds.includes("custom")) && t.backends.includes("mujoco"));
    $("#l-task").innerHTML = tasks.map(([n]) => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
    $("#l-vehinfo").textContent = r.spec.description;
    buildForm(); pickTask(); await revalidate();
    toast(`Imported ${r.spec.name}: ${r.spec.params.actuators.length} actuators`);
  });
  $$("#l-seg button").forEach(b => b.onclick = () => { $$("#l-seg button").forEach(x => x.classList.toggle("on", x === b)); ensureViewer().setMode(b.dataset.v); });
  padSetup($("#l-pad"), (v, w) => LAB.space === "unicycle" ? api("/api/lab/command", { u: { v, w } }).catch(() => { }) : 0, () => LAB.vmax, () => LAB.wmax, "lab");
  pickVehicle($("#l-veh").value || Object.keys(LAB.cat.vehicles)[0]);
  connectLab();
  document.dispatchEvent(new Event("labready"));
  document.addEventListener("tabchange", e => {
    if (e.detail === "lab") {
      ensureViewer().resize();
      api("/api/lab/catalog").then(c => { LAB.cat.models = c.models; LAB.cat.detectors = c.detectors; refreshPolicies($("#l-pol").value); refreshDetectors($("#l-det").value); }).catch(() => { });     // new models since last visit
    }
    setButtons();
  });
  if (S.tab === "lab") ensureViewer();
})();
