"use strict";
/* Builder tab: describe the hardware you have, see what it can do, fix what is wrong, send it to the Lab.
   Relies on helpers from app.js ($, $$, esc, api, toast, guard, S, setTab), lab.js (fieldHtml, setPath, clone, fmtv, LAB, pickVehicle)
   and viewer3d.js (Viewer3D). */
const BLD = { lib: null, build: null, res: null, viewer: null, timer: null, previewSig: "", seq: 0, framed: false, saved: "", draft: null, ownTimer: null };

const BUILD_HTML = `
<div class="col">
  <div class="card">
    <h3>Machine <span class="hint" id="b-state"></span></h3>
    <div id="b-f-machine"></div>
    <div class="row" style="margin-top:12px">
      <button class="btn primary" id="b-save">Save</button>
      <button class="btn" id="b-lab" title="Save, then open this machine in the Lab">Save and open in Lab</button>
      <button class="btn small danger" id="b-del" hidden>Delete</button>
    </div>
    <p class="note" id="b-msg"></p>
  </div>
  <div class="card"><h3 id="b-t-chassis">Chassis and drive</h3><div id="b-f-chassis"></div></div>
  <div class="card"><h3>Power</h3><div id="b-f-power"></div></div>
  <div class="card"><h3>Controller, lamp and extras</h3><div id="b-f-elec"></div></div>
  <div class="card"><h3>Sensors <button class="btn small" id="b-addsensor">Add sensor</button></h3><div id="b-f-sensors"></div></div>
  <div class="card">
    <h3>Parts library <select id="b-lkind" style="width:auto"></select></h3>
    <div class="hint">Tick how many of each you own: your parts are listed first in the pickers. Badges say how far to trust a number.</div>
    <div id="b-lib" class="parts"></div>
    <details><summary>Add your own part</summary><div id="b-pform"></div></details>
  </div>
</div>
<div class="col">
  <div class="card">
    <h3>3D preview</h3>
    <div class="view3d" id="b-view"></div>
    <p class="note" id="b-vnote">The chassis is drawn see-through; blue cones are camera fields of view. Drag to rotate, wheel to zoom.</p>
  </div>
  <div class="card">
    <h3>Predicted performance <span class="pill" id="b-est" title="Estimates from datasheet numbers; calibrate with measurements to tie them to your real machine">estimate</span></h3>
    <div class="kv facts" id="b-facts" style="margin-top:0;padding-top:0;border-top:0"></div>
  </div>
  <div class="card">
    <h3>Will it do the job? <select id="b-task" style="width:auto;max-width:60%"></select></h3>
    <div id="b-fit" class="hint">Pick a task to check this machine against what it needs.</div>
  </div>
  <div class="card"><h3>Findings</h3><div id="b-find"></div></div>
  <div class="card"><h3>Budgets</h3><div id="b-budget"></div></div>
  <div class="card"><h3>Assumptions</h3><div id="b-assume" class="hint"></div></div>
  <div class="card" id="c-card">
    <h3>Calibrate with your real machine</h3>
    <p class="hint" style="margin-top:0">Predictions come from datasheets, which are optimistic. Measure what you can (any subset) and the build will use your numbers instead of its assumptions.</p>
    <div class="fields">
      <div><label for="c-mass">mass <i>g</i></label><input id="c-mass" type="number" min="0" step="any"></div><div></div>
      <div><label for="c-dist">straight run: distance <i>cm</i></label><input id="c-dist" type="number" min="0" step="any"></div>
      <div><label for="c-secs">... covered in <i>s</i> (after the first second)</label><input id="c-secs" type="number" min="0" step="any" value="3"></div>
      <div><label for="c-spin">spin on the spot: angle <i>deg</i></label><input id="c-spin" type="number" min="0" step="any"></div>
      <div><label for="c-spins">... in <i>s</i></label><input id="c-spins" type="number" min="0" step="any" value="3"></div>
      <div><label for="c-t90">time to reach 90 % of top speed <i>s</i></label><input id="c-t90" type="number" min="0" step="any"></div>
      <div><label for="c-run">runtime on a full battery <i>min</i></label><input id="c-run" type="number" min="0" step="any"></div>
    </div>
    <div class="row" style="margin-top:10px"><button class="btn primary small" id="c-apply">Apply measurements</button><button class="btn small" id="c-clear">Clear calibration</button></div>
    <div id="c-out"></div>
  </div>
</div>`;

const bget = path => path.split(".").reduce((o, k) => o?.[k], BLD.build);
const numStr = x => (x === null || x === undefined || Number.isNaN(+x)) ? "" : String(Number((+x).toPrecision(6)));
const fmtn = (v, nd = 3) => fmtv(v, Math.abs(v) >= 100 ? 0 : nd);

/* ---------------------------------------------------------------- form pieces */
function bnum(path, label, { unit = "", scale = 1, step = "any", min = null } = {}) {
  return `<div><label for="b:${esc(path)}">${esc(label)}${unit ? ` <i>${esc(unit)}</i>` : ""}</label><input id="b:${esc(path)}" type="number" data-b="${esc(path)}" data-t="num" data-scale="${scale}" step="${step}"${min !== null ? ` min="${min}"` : ""} value="${numStr(bget(path) / scale)}"></div>`;
}
function bint(path, label, { min = 1, max = 99 } = {}) {
  return `<div><label for="b:${esc(path)}">${esc(label)}</label><input id="b:${esc(path)}" type="number" data-b="${esc(path)}" data-t="int" step="1" min="${min}" max="${max}" value="${bget(path) ?? ""}"></div>`;
}
function bsel(path, label, options, { wide = false, act = "" } = {}) {
  const cur = bget(path) ?? "";
  return `<div${wide ? ' class="wide"' : ""}><label for="b:${esc(path)}">${esc(label)}</label><select id="b:${esc(path)}" data-b="${esc(path)}" data-t="str"${act ? ` data-act="${act}"` : ""}>` +
    options.map(([v, t]) => `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(t)}</option>`).join("") + `</select></div>`;
}
function btext(path, label, wide = true) {
  return `<div${wide ? ' class="wide"' : ""}><label for="b:${esc(path)}">${esc(label)}</label><input id="b:${esc(path)}" type="text" data-b="${esc(path)}" data-t="str" value="${esc(bget(path) ?? "")}"></div>`;
}
/* parts of one kind as picker options: yours first, then the rest, each with its trust badge */
function partOptions(kinds, { none = "" } = {}) {
  const list = [].concat(kinds).flatMap(k => BLD.lib.parts[k] || []);
  list.sort((a, b) => (b.owned > 0) - (a.owned > 0) || a.name.localeCompare(b.name));
  return (none ? [["", none]] : []).concat(list.map(p => [p.name, `${p.owned > 0 ? "★ " : ""}${p.name} (${p.confidence})`]));
}

function renderForms() {
  const b = BLD.build;
  const ex = BLD.lib.examples, saved = BLD.lib.builds;
  const platforms = { ground: "Ground vehicle (wheels or tracks)", air: "Drone (multirotor)", water: "Boat" };
  $("#b-f-machine").innerHTML = btext("name", "Name") + `<label for="b-platform">Kind of machine</label><select id="b-platform">` +
    BLD.lib.platforms.map(p => `<option value="${esc(p)}"${p === b.platform ? " selected" : ""}>${esc(platforms[p] || p)}</option>`).join("") + `</select>` +
    `<label for="b-load">Open a saved build or an example</label><select id="b-load"><option value="">(choose)</option>` +
    saved.map(n => `<option value="saved:${esc(n)}">saved: ${esc(n)}</option>`).join("") +
    Object.keys(ex).map(n => `<option value="example:${esc(n)}">example: ${esc(n)}</option>`).join("") + `</select>` +
    btext("description", "Description");
  $("#b-load").onchange = guard(loadChoice);
  $("#b-platform").onchange = () => {                                      // a different kind of machine starts from an example of that kind
    const ex = Object.values(BLD.lib.examples).find(e => e.platform === $("#b-platform").value);
    if (ex) bldSetBuild(ex);
  };
  const form = bldChassisForm(b);
  $("#b-t-chassis").textContent = form.title;
  $("#b-f-chassis").innerHTML = form.html;
  $("#c-card").hidden = b.platform !== "ground";
  bldFillTasks();
  $("#b-f-power").innerHTML = `<div class="fields">` + bsel("power.battery", "battery cell", partOptions("battery"), { wide: true }) +
    bint("power.series", "in series", { max: 14 }) + bint("power.parallel", "in parallel", { max: 20 }) +
    bsel("power.regulator", "voltage regulator", partOptions("regulator", { none: "none (electronics run straight from the battery)" }), { wide: true }) + `</div>`;
  bldRenderElectronics(b);
}

/* the part of the form that depends on the kind of machine */
function bldChassisForm(b) {
  if (b.platform === "air") {
    return { title: "Frame and propulsion", html: `<div class="fields">` + bsel("chassis.frame", "frame", partOptions("frame"), { wide: true }) +
      bnum("chassis.extra_mass_kg", "payload / extra mass", { unit: "g", scale: 0.001 }) + `<div></div>` +
      bsel("drive.propulsor", "motor + propeller (one per rotor)", partOptions("propulsor_air"), { wide: true }) +
      bsel("drive.esc", "speed controller (one per rotor)", partOptions("esc"), { wide: true }) + `</div><p class="hint">The number of rotors follows the frame's layout. Air models are first guesses: flight limits come from rules of thumb.</p>` };
  }
  if (b.platform === "water") {
    return { title: "Hull and thruster", html: `<div class="fields">` + bsel("chassis.hull", "hull", partOptions("hull"), { wide: true }) +
      bnum("chassis.ballast_kg", "ballast / payload", { unit: "g", scale: 0.001 }) + `<div></div>` +
      bsel("drive.thruster", "thruster", partOptions("thruster_water"), { wide: true }) + bint("drive.count", "thrusters", { min: 1, max: 4 }) + `</div>` +
      `<p class="hint">The boat model is a first guess: top speed is the plug-in's own estimate.</p>` };
  }
  return { title: "Chassis and drive", html: `<div class="fields">` +
    bnum("chassis.length", "length", { unit: "cm", scale: 0.01 }) + bnum("chassis.width", "width", { unit: "cm", scale: 0.01 }) + bnum("chassis.height", "height", { unit: "cm", scale: 0.01 }) +
    bnum("chassis.frame_mass_kg", "frame mass", { unit: "g", scale: 0.001 }) +
    bsel("chassis.drive", "drive layout", BLD.lib.drives.map(d => [d, d])) + (b.chassis.drive === "diff2wd" ? bint("chassis.caster_count", "casters", { min: 0, max: 8 }) : "") +
    bsel("drive.motor", "motor", partOptions("motor_dc"), { wide: true }) + bint("drive.motor_count", "motors (even)", { min: 2, max: 12 }) +
    bnum("drive.speed_limit_mps", "speed limit", { unit: "m/s (0 = none)" }) +
    bsel("drive.wheel", "wheel or track", partOptions("wheel"), { wide: true }) + bsel("drive.driver", "motor driver", partOptions("driver_dc"), { wide: true }) + `</div>` };
}

/* the tasks this kind of machine can do, keeping the current choice when it still makes sense */
function bldFillTasks() {
  const keep = $("#b-task").value, tasks = BLD.lib.tasks[BLD.build.platform] || [];
  $("#b-task").innerHTML = `<option value="">(no task)</option>` + tasks.map(t => `<option value="${esc(t.name)}" title="${esc(t.description)}">${esc(t.name)}${t.backends.includes("webots") ? " (Webots)" : ""}</option>`).join("");
  if (tasks.some(t => t.name === keep)) $("#b-task").value = keep;
}

function bldRenderElectronics(b) {
  const lampOk = b.platform !== "water";                                    // the boat model has no lamp
  $("#b-f-elec").innerHTML = `<div class="fields">` + bsel("controller", "controller board", partOptions("mcu"), { wide: true }) +
    (lampOk ? `<div class="wide"><label for="b-lamp">lamp</label><select id="b-lamp">${partOptions("lamp", { none: "none" }).map(([v, t]) => `<option value="${esc(v)}"${v === (b.lamp?.part || "") ? " selected" : ""}>${esc(t)}</option>`).join("")}</select></div>` +
      (b.lamp ? bint("lamp.count", "lamps", { max: 20 }) : "") : "") + `</div><label>Extras (anything else on board)</label>` +
    b.extras.map((e, i) => `<div class="fields">` + btext(`extras.${i}.name`, "name", false) + bnum(`extras.${i}.mass_kg`, "mass", { unit: "g", scale: 0.001 }) +
      bnum(`extras.${i}.current_a`, "current", { unit: "mA", scale: 0.001 }) + `<div><button class="btn small danger" data-rm-extra="${i}">remove</button></div></div>`).join("") +
    `<button class="btn small" id="b-addextra" style="margin-top:6px">Add extra</button>`;
  if ($("#b-lamp")) $("#b-lamp").onchange = () => { b.lamp = $("#b-lamp").value ? { part: $("#b-lamp").value, count: b.lamp?.count || 1 } : null; renderForms(); bldSchedule(); };
  $("#b-addextra").onclick = () => { b.extras.push({ name: "extra", mass_kg: 0.01, current_a: 0 }); renderForms(); bldSchedule(); };
  $$("[data-rm-extra]").forEach(x => x.onclick = () => { b.extras.splice(+x.dataset.rmExtra, 1); renderForms(); bldSchedule(); });
  const sensorOptions = partOptions(["sensor_camera", "sensor_range", "sensor_imu", "sensor_gps"]);
  $("#b-f-sensors").innerHTML = b.sensors.length ? b.sensors.map((s, i) => `<details open><summary>${esc(s.name)} <span class="hint">${esc(s.part)}</span></summary><div class="fields">` +
    bsel(`sensors.${i}.part`, "part", sensorOptions, { wide: true }) + btext(`sensors.${i}.name`, "name", false) +
    bnum(`sensors.${i}.mount.forward`, "forward", { unit: "cm", scale: 0.01 }) + bnum(`sensors.${i}.mount.height`, "height above floor", { unit: "cm", scale: 0.01 }) +
    bnum(`sensors.${i}.mount.tilt_deg`, "tilt", { unit: "deg, + looks down" }) + `<div><button class="btn small danger" data-rm-sensor="${i}">remove</button></div></div></details>`).join("")
    : `<p class="hint">No sensors yet. The simulator adds the ideal ones a task needs, but a real camera is what lets a detector see.</p>`;
  $$("[data-rm-sensor]").forEach(x => x.onclick = () => { b.sensors.splice(+x.dataset.rmSensor, 1); renderForms(); bldSchedule(); });
}

/* typing: one delegated handler for every bound field */
function onBuilderInput(e) {
  const el = e.target.closest("[data-b]");
  if (!el) return;
  const t = el.dataset.t;
  const v = t === "num" ? (el.value === "" ? NaN : +el.value * (+el.dataset.scale || 1)) : t === "int" ? (el.value === "" ? NaN : parseInt(el.value, 10)) : el.value;
  setPath(BLD.build, el.dataset.b, v);
  if (el.dataset.b === "chassis.drive" || el.dataset.b.endsWith(".part")) { renderForms(); }
  bldSchedule();
}

/* ---------------------------------------------------------------- evaluate and show */
function bldSchedule() { clearTimeout(BLD.timer); BLD.timer = setTimeout(guard(bldRefresh), 250); }

async function bldRefresh() {
  const seq = ++BLD.seq;
  const res = await api("/api/build/evaluate", { build: BLD.build, task: $("#b-task").value || null });
  if (seq !== BLD.seq) return;                                             // a newer edit is already on its way
  BLD.res = res;
  bldRenderResult(res);
  bldUpdatePreview(res);
  if (S.tab === "build") renderPills();
}

function bldRenderResult(res) {
  $("#b-facts").innerHTML = res.facts.length ? res.facts.map(f => `<span>${esc(f.label)}</span><b title="${esc(f.note || "")}">${fmtn(f.value)} ${esc(f.unit)}` +
    `${f.lo != null && f.hi != null ? ` <small class="hint">(${fmtn(f.lo)} to ${fmtn(f.hi)})</small>` : ""}</b>`).join("") : `<span>Fix the problems below to see predictions.</span><b></b>`;
  $("#b-est").className = "pill" + (BLD.build.calibration && Object.keys(BLD.build.calibration).length ? " ok" : "");
  $("#b-est").textContent = BLD.build.calibration && Object.keys(BLD.build.calibration).length ? "calibrated" : "estimate";
  const label = { error: "Problem", warn: "Warning", info: "Note" };
  const issues = res.issues.filter(i => i.level === "error").map(i => `<div class="find error"><b>${esc(i.field)}</b> ${esc(i.msg)}</div>`).join("");
  BLD.cands = [];
  const fixHtml = f => {
    if (!f.fix) return "";
    const cs = f.fix.candidates || [];
    const buttons = cs.map(c => { BLD.cands.push(c.patch); return `<button class="btn small" data-cand="${BLD.cands.length - 1}" title="${esc(c.title)} (${esc(c.confidence)}); leaves ${c.errors} problem(s) and ${c.warnings} warning(s)">${c.owned ? "★ " : ""}use ${esc(c.part)}</button>`; }).join(" ");
    return `<div class="fix">${esc(f.fix.why || f.fix.action)}${buttons ? ": " + buttons : (f.fix.action === "add_part" || f.fix.action === "change_part" ? " (no part in the library fixes this on its own: add yours)" : "")}</div>`;
  };
  $("#b-find").innerHTML = issues + (res.findings.length ? res.findings.map(f => `<div class="find ${f.severity}"><b>${label[f.severity]}: ${esc(f.title)}</b> <span class="hint">${esc(f.id)}${f.scope === "task" ? ", for this task" : ""}</span>` +
    `${f.detail ? `<div class="hint">${esc(f.detail)}</div>` : ""}${fixHtml(f)}</div>`).join("")
    : (res.vehicle ? `<p class="note ok">No problems found.</p>` : ""));
  $$("[data-cand]").forEach(x => x.onclick = () => bldApplyPatch(BLD.cands[+x.dataset.cand]));
  const icon = { ok: "✓", warn: "!", fail: "✗", info: "i" };
  $("#b-fit").innerHTML = res.fit ? `<p class="note ${res.task_ok ? "ok" : "err"}" style="margin:0 0 6px">${res.task_ok ? "This machine can do the task." : "This machine cannot do the task as it is."}</p>` +
    res.fit.items.map(i => `<div class="fititem ${i.status}"><b>${icon[i.status]}</b> ${esc(i.title)}<div class="hint">${esc(i.detail)}</div></div>`).join("") : "Pick a task to check this machine against what it needs.";
  const bud = res.budget || {};
  if (bud.mass) {
    const cur = bud.current, vol = bud.voltage;
    $("#b-budget").innerHTML = `<div class="hint">${esc(bud.chain)}</div>` +
      `<table style="margin-top:8px"><thead><tr><th>part</th><th>qty</th><th>mass</th></tr></thead><tbody>` +
      bud.mass.map(m => `<tr><td>${esc(m.part)}</td><td>${m.qty}</td><td>${fmtv(m.kg * 1000, 0)} g</td></tr>`).join("") +
      `<tr><td><b>total</b></td><td></td><td><b>${fmtv(bud.mass_total * 1000, 0)} g</b></td></tr></tbody></table>` +
      `<div class="kv facts"><span>Electronics</span><b>${fmtv(cur.battery_elec_a * 1000, 0)} mA from the battery</b><span>At cruise</span><b>${fmtv(cur.cruise_a, 2)} A</b>` +
      `<span>Every motor stalled</span><b>${fmtv(cur.stall_a, 2)} A of ${fmtv(cur.pack_max_a, 1)} A rated</b><span>Rail for electronics</span><b>${fmtv(vol.rail_min, 1)} to ${fmtv(vol.rail_max, 1)} V</b></div>`;
  } else $("#b-budget").innerHTML = "";
  $("#b-assume").innerHTML = res.assumptions.length ? `<ul style="margin:0;padding-left:18px">${res.assumptions.map(a => `<li>${esc(a)}</li>`).join("")}</ul>` : "";
  $("#b-save").disabled = !res.ok;
  $("#b-lab").disabled = !res.ok;
}

/* a suggestion is a patch the server already tried: {"set": path, "value": v} or {"append": list, "value": item} */
function bldApplyPatch(p) {
  if (p.set) setPath(BLD.build, p.set, clone(p.value));
  else (BLD.build[p.append] ||= []).push(clone(p.value));
  renderForms();
  bldSchedule();
}

window.buildPills = () => {
  const r = BLD.res;
  if (!r) return `<span class="pill">loading...</span>`;
  const own = r.findings.filter(f => f.scope !== "task"), errs = own.filter(f => f.severity === "error").length + r.issues.filter(i => i.level === "error").length;
  const warns = own.filter(f => f.severity === "warn").length;
  return [`<span class="pill ${errs ? "bad" : "ok"}">${errs ? errs + " problem" + (errs > 1 ? "s" : "") : "no problems"}</span>`, warns ? `<span class="pill warn">${warns} warning${warns > 1 ? "s" : ""}</span>` : "",
    r.fit ? `<span class="pill ${r.task_ok ? "ok" : "bad"}">${esc(r.fit.task)}: ${r.task_ok ? "suitable" : "not suitable"}</span>` : "", r.vehicle ? `<span class="pill">${esc(r.vehicle.name)}</span>` : ""].join("");
};

/* ---------------------------------------------------------------- calibration */
async function bldCalibrate(clear) {
  const v = id => { const x = parseFloat($(id).value); return Number.isFinite(x) && x > 0 ? x : null; };
  const r = await api("/api/build/calibrate", { build: BLD.build, clear, measurements: {
    mass_g: v("#c-mass"), straight_cm: v("#c-dist"), straight_s: v("#c-secs"), spin_deg: v("#c-spin"), spin_s: v("#c-spins"), t90_s: v("#c-t90"), runtime_min: v("#c-run") } });
  if (r.table.length || clear) { BLD.build = r.build; BLD.build.extras ||= []; BLD.build.sensors ||= []; }
  $("#c-out").innerHTML = (r.table.length ? `<table style="margin-top:10px"><thead><tr><th></th><th>predicted</th><th>measured</th><th>off by</th><th>now</th></tr></thead><tbody>` +
    r.table.map(t => `<tr><td>${esc(t.what)} <span class="hint">${esc(t.unit)}</span></td><td>${fmtn(t.predicted)}</td><td>${fmtn(t.measured)}</td><td>${t.error_pct > 0 ? "+" : ""}${t.error_pct} %</td><td>${fmtn(t.after)}</td></tr>`).join("") + `</tbody></table>` : "") +
    r.notes.map(n => `<p class="note ${r.table.length ? "warn" : ""}">${esc(n)}</p>`).join("");
  bldSchedule();
}

/* ---------------------------------------------------------------- 3D preview */
function ensureBuildViewer() {
  if (!BLD.viewer) { BLD.viewer = new Viewer3D($("#b-view")); BLD.viewer.mode = "orbit"; }
  return BLD.viewer;
}
async function bldUpdatePreview(res) {
  if (!res.vehicle || S.tab !== "build") { if (!res.vehicle) BLD.previewSig = ""; return; }
  const b = BLD.build;
  const sig = JSON.stringify([b.platform, b.chassis, b.drive, b.power.battery, b.power.series, b.power.parallel, b.sensors, b.lamp, b.controller]);
  if (sig === BLD.previewSig) return;
  BLD.previewSig = sig;
  try {
    const p = await api("/api/build/preview", { build: BLD.build });
    const v = ensureBuildViewer();
    v.mode = "orbit";
    v.loadScene(p.scene);
    v.update(p.poses);
    if (!BLD.framed) { v.az = -2.2; v.el_ = 0.5; BLD.framed = true; }
    v.target.set(0, 0, 0.04);
    v.dist = Math.max(0.45, (p.scene.vehicle_size || 0.2) * 3.4);
    $("#b-vnote").textContent = "The machine is drawn see-through so the parts inside show (battery, controller, motors or thrusters, sensors); blue cones are camera fields of view. Drag to rotate, wheel to zoom.";
  } catch (e) { $("#b-vnote").textContent = "No preview: " + e.message; }
}

/* ---------------------------------------------------------------- load, save, send to the Lab */
async function loadChoice() {
  const [kind, name] = ($("#b-load").value || ":").split(":");
  if (!name) return;
  const b = kind === "example" ? clone(BLD.lib.examples[name]) : await api("/api/build/get?name=" + encodeURIComponent(name));
  bldSetBuild(b, kind === "saved" ? name : "");
}
function bldSetBuild(b, savedName = "") {
  BLD.build = clone(b);
  BLD.build.extras ||= []; BLD.build.sensors ||= []; BLD.build.calibration ||= {};
  BLD.saved = savedName;
  BLD.previewSig = "";
  $("#b-del").hidden = !savedName;
  $("#b-state").textContent = savedName ? "saved" : "not saved";
  renderForms();
  bldSchedule();
}
async function bldSaveBuild() {
  const r = await api("/api/build/save", { build: BLD.build });
  BLD.lib.builds = r.builds;
  BLD.saved = BLD.build.name;
  $("#b-del").hidden = false;
  $("#b-state").textContent = "saved";
  $("#b-msg").textContent = `Saved. "${BLD.build.name}" is now a vehicle in the Lab (${r.vehicle_path}).`;
  renderForms();
  return r.vehicle;
}
async function bldOpenInLab(spec) {
  setTab("lab");
  LAB.cat = await api("/api/lab/catalog");
  if (!$$("#l-veh option").some(o => o.value === spec.name)) $("#l-veh").insertAdjacentHTML("afterbegin", `<option value="${esc(spec.name)}">${esc(spec.name)} (built)</option>`);
  $("#l-veh").value = spec.name;
  pickVehicle(spec.name);
}

/* ---------------------------------------------------------------- parts library and your own parts */
function renderLib() {
  const kinds = Object.keys(BLD.lib.kinds);
  if (!$("#b-lkind").options.length) $("#b-lkind").innerHTML = kinds.map(k => `<option value="${esc(k)}">${esc(BLD.lib.kinds[k].title)}</option>`).join("");
  const kind = $("#b-lkind").value || kinds[0];
  const brief = p => Object.entries(p.params).slice(0, 4).map(([k, v]) => `${k.replace(/_/g, " ")} ${Array.isArray(v) ? v.join("/") : numStr(v)}`).join(", ");
  $("#b-lib").innerHTML = `<table><thead><tr><th>part</th><th>trust</th><th>own</th></tr></thead><tbody>` + (BLD.lib.parts[kind] || []).map(p =>
    `<tr title="${esc((p.notes || "") + " Source: " + p.source)}"><td><b>${esc(p.name)}</b><div class="hint">${esc(brief(p))}${p.mass_kg ? `, ${fmtv(p.mass_kg * 1000, 0)} g` : ""}</div></td>` +
    `<td><span class="badge ${esc(p.confidence)}">${esc(p.confidence)}</span>${p.origin === "user" ? ' <span class="badge">yours</span>' : ""}</td>` +
    `<td><input type="number" min="0" max="99" step="1" class="own" data-own="${esc(p.name)}" value="${p.owned || 0}" aria-label="how many ${esc(p.name)} you own"></td></tr>`).join("") + `</tbody></table>`;
  $$("[data-own]").forEach(x => x.onchange = () => {
    BLD.lib.owned[x.dataset.own] = Math.max(0, parseInt(x.value, 10) || 0);
    clearTimeout(BLD.ownTimer);
    BLD.ownTimer = setTimeout(guard(async () => { await api("/api/build/inventory", { owned: BLD.lib.owned }); await reloadLibrary(); }), 300);
  });
}
async function reloadLibrary() {
  const keepKind = $("#b-lkind").value;
  BLD.lib = await api("/api/build/library");
  renderLib();
  $("#b-lkind").value = keepKind || $("#b-lkind").value;
  renderForms();
}
function newDraft(kind, from) {
  const k = BLD.lib.kinds[kind];
  const base = from ? BLD.lib.parts[kind].find(p => p.name === from) : null;
  BLD.draft = base ? { ...clone(base), name: base.name + "_mine", confidence: "measured", source: "" }
    : { name: "my_" + kind, kind, title: "", source: "", confidence: "assumed", mass_kg: 0, notes: "", params: Object.fromEntries(k.params.map(p => [p.name, p.default])) };
  renderPartForm();
}
function renderPartForm() {
  const d = BLD.draft, k = BLD.lib.kinds[d.kind];
  const txt = (key, label) => `<div class="wide"><label for="np-${key}">${label}</label><input id="np-${key}" type="text" data-np="${key}" value="${esc(d[key])}"></div>`;
  $("#b-pform").innerHTML = `<div class="fields"><div><label for="np-kind">kind</label><select id="np-kind">${Object.keys(BLD.lib.kinds).map(x => `<option value="${esc(x)}"${x === d.kind ? " selected" : ""}>${esc(BLD.lib.kinds[x].title)}</option>`).join("")}</select></div>` +
    `<div><label for="np-from">start from</label><select id="np-from"><option value="">defaults</option>${(BLD.lib.parts[d.kind] || []).map(p => `<option>${esc(p.name)}</option>`).join("")}</select></div>` +
    txt("name", "name (letters, digits, _ . -)") + txt("title", "description") + txt("source", "where the numbers come from (datasheet link, label, your measurement)") +
    `<div><label for="np-confidence">confidence</label><select id="np-confidence" data-np="confidence">${["datasheet", "typical", "assumed", "measured"].map(c => `<option${c === d.confidence ? " selected" : ""}>${c}</option>`).join("")}</select></div>` +
    `<div><label for="np-mass">mass <i>g</i></label><input id="np-mass" type="number" step="any" min="0" data-np="mass_g" value="${numStr(d.mass_kg * 1000)}"></div>` +
    k.params.map(p => fieldHtml("np.params." + p.name, p, d.params[p.name])).join("") + `</div>` +
    `<div class="row" style="margin-top:10px"><button class="btn primary small" id="np-save">Save part</button></div><p class="note" id="np-msg"></p>`;
  $("#np-kind").onchange = () => newDraft($("#np-kind").value, "");
  $("#np-from").onchange = () => newDraft(d.kind, $("#np-from").value);
  $$("#b-pform [data-np]").forEach(el => el.addEventListener("input", () => {
    const key = el.dataset.np;
    if (key === "mass_g") d.mass_kg = (+el.value || 0) / 1000; else d[key] = el.value;
  }));
  $$("#b-pform [data-path]").forEach(el => el.addEventListener("input", () => {
    const t = el.dataset.type;
    setPath(d, el.dataset.path.slice(3), t === "bool" ? el.checked : t === "enum" || t === "str" ? el.value : t === "strlist" ? el.value.split(",").map(s => s.trim()).filter(Boolean) : (el.value === "" ? NaN : +el.value));
  }));
  $("#np-save").onclick = guard(async () => {
    const r = await api("/api/build/part", { part: BLD.draft });
    $("#np-msg").textContent = "Saved to " + r.path + (r.issues.length ? ". Note: " + r.issues.map(i => i.msg).join("; ") : "");
    await reloadLibrary();
  });
}

/* ---------------------------------------------------------------- init */
(async function initBuilder() {
  const sec = $("#tab-build");
  if (!sec) return;
  sec.innerHTML = BUILD_HTML;
  try { BLD.lib = await api("/api/build/library"); } catch (e) { toast("Builder library failed: " + e.message, true); return; }
  sec.addEventListener("input", onBuilderInput);
  $("#b-task").onchange = bldSchedule;
  $("#b-lkind").onchange = renderLib;
  renderLib();
  newDraft("motor_dc", "");
  $("#c-apply").onclick = guard(() => bldCalibrate(false));
  $("#c-clear").onclick = guard(() => bldCalibrate(true));
  $("#b-save").onclick = guard(bldSaveBuild);
  $("#b-lab").onclick = guard(async () => bldOpenInLab(await bldSaveBuild()));
  $("#b-del").onclick = guard(async () => {
    if (!BLD.saved || !confirm(`Delete the saved build "${BLD.saved}" and its vehicle?`)) return;
    const r = await api("/api/build/delete", { name: BLD.saved });
    BLD.lib.builds = r.builds;
    bldSetBuild(BLD.build, "");
    $("#b-msg").textContent = "Deleted.";
  });
  $("#b-addsensor").onclick = () => { BLD.build.sensors.push({ part: "hc_sr04", name: "sensor" + (BLD.build.sensors.length + 1), mount: { forward: 0.05, height: 0.05, tilt_deg: 0 } }); renderForms(); bldSchedule(); };
  bldSetBuild(BLD.lib.examples.esp32_pipe_rover || Object.values(BLD.lib.examples)[0]);
  document.addEventListener("tabchange", e => {
    if (e.detail !== "build") return;
    ensureBuildViewer().resize();
    BLD.previewSig = "";
    bldSchedule();
    api("/api/build/library").then(l => { BLD.lib = l; renderLib(); }).catch(() => { });
  });
  if (S.tab === "build") { ensureBuildViewer(); bldSchedule(); }
})();
