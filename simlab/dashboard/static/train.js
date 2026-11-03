"use strict";
/* Train tab: start RL training runs, watch them learn, compare the result with the classical baseline.
   Relies on app.js helpers ($, $$, esc, api, toast, guard, S, setTab) and on lab.js (LAB.cat, labWatch). */
const TR = { runs: [], sel: null, detail: null, timer: null };

const TRAIN_HTML = `
<div class="col">
  <div class="card">
    <h3>New training run</h3>
    <label for="t-veh">Vehicle</label><select id="t-veh"></select>
    <label for="t-task">Task</label><select id="t-task"></select>
    <div class="hint" id="t-taskinfo"></div>
    <div class="row">
      <div><label for="t-algo">Algorithm</label><select id="t-algo"><option value="ppo">PPO (on-policy)</option><option value="sac">SAC (off-policy)</option></select></div>
      <div><label for="t-steps">Steps</label><input id="t-steps" type="number" value="300000" min="5000" step="10000"></div>
    </div>
    <div class="row">
      <div><label for="t-envs">Parallel simulators</label><input id="t-envs" type="number" value="8" min="1" max="16"></div>
      <div><label for="t-seed">Seed</label><input id="t-seed" type="number" value="0" step="1"></div>
    </div>
    <label class="check" style="margin-top:12px"><input type="checkbox" id="t-curr" checked> curriculum: start easy, reach the full task at 40 % of the steps</label>
    <div class="row" style="margin-top:12px"><button class="btn primary" id="t-start">Start training</button></div>
    <p class="note">Simulation runs on the CPU (one process per simulator); the policy network is small and also trains on the CPU.</p>
    <p class="note" id="t-msg"></p>
  </div>
  <div class="card"><h3>Runs</h3><div id="t-runs" class="runs"><span class="hint">no runs yet</span></div></div>
  <div class="card">
    <h3>Train a camera detector</h3>
    <p class="note" style="margin-top:0">Uses the vehicle and task selected above. The simulator renders labelled images from many viewpoints with randomised lighting and colours, then fine-tunes RF-DETR on the GPU.</p>
    <div class="row">
      <div><label for="d-images">Images</label><input id="d-images" type="number" value="800" min="50" max="20000" step="50"></div>
      <div><label for="d-epochs">Epochs</label><input id="d-epochs" type="number" value="20" min="1" max="200"></div>
      <div><label for="d-size">Model</label><select id="d-size"><option>nano</option><option>small</option><option>medium</option></select></div>
    </div>
    <div class="row" style="margin-top:12px"><button class="btn primary" id="d-start">Capture and train</button></div>
    <p class="note" id="d-msg"></p>
    <h3 style="margin-top:14px">Detector runs</h3><div id="d-runs" class="runs"><span class="hint">no detector runs yet</span></div>
  </div>
</div>
<div class="col">
  <div class="card">
    <h3>Learning curves <span class="hint" id="t-title"></span></h3>
    <canvas id="t-chart" class="chart" style="height:260px"></canvas>
    <div class="legend"><span class="lg" style="--c:#4cd08a">evaluation success</span><span class="lg" style="--c:#4da3ff">training success</span><span class="lg dash" style="--c:#f5b942">classical baseline</span></div>
    <canvas id="t-chart2" class="chart" style="height:150px"></canvas>
    <div class="legend"><span class="lg" style="--c:#c792ea">training return</span><span class="lg" style="--c:#4cd08a">evaluation return</span></div>
  </div>
  <div class="two">
    <div class="card"><h3>Run</h3><div class="stats" id="t-stats"></div><div class="row" style="margin-top:10px"><button class="btn small" id="t-stop" disabled>Stop</button><button class="btn small danger" id="t-del" disabled>Delete</button></div><pre class="code" id="t-log" hidden></pre></div>
    <div class="card"><h3>Trained vs classical baseline <span class="hint">same scenarios</span></h3><div id="t-final" class="hint">finishes when training ends</div><div class="row" style="margin-top:10px"><button class="btn primary" id="t-watch" disabled>Watch trained policy in Lab</button></div></div>
  </div>
  <div class="card" id="d-card" hidden>
    <h3>Detector <span class="hint" id="d-title"></span></h3>
    <div class="stats" id="d-stats"></div>
    <div id="d-eval" style="margin-top:10px"></div>
    <div class="two" id="d-previews" style="margin-top:10px"></div>
    <div class="row" style="margin-top:10px"><button class="btn small" id="d-stop" disabled>Stop</button><button class="btn small danger" id="d-del" disabled>Delete</button><button class="btn primary small" id="d-try" disabled>Try it in the Lab</button></div>
    <pre class="code" id="d-log" hidden></pre>
  </div>
</div>`;

const pct = v => v === null || v === undefined ? "-" : (100 * v).toFixed(0) + " %";
const num = (v, nd = 1) => v === null || v === undefined ? "-" : (+v).toFixed(nd);

/* ---------------------------------------------------------------- charts (no library) */
function drawChart(cv, series, { ymin = 0, ymax = 1, fmt = v => v, steps = 1 }) {
  const dpr = devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;      // CSS size is fixed in the markup: no feedback
  if (!w || !h) return;
  cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
  const c = cv.getContext("2d");
  c.scale(dpr, dpr);
  const L = 44, R = 10, T = 8, B = 20, pw = w - L - R, ph = h - T - B;
  const css = getComputedStyle(document.documentElement);
  c.clearRect(0, 0, w, h);
  c.font = "11px ui-monospace, Consolas, monospace"; c.textBaseline = "middle";
  c.strokeStyle = css.getPropertyValue("--line"); c.fillStyle = css.getPropertyValue("--muted"); c.lineWidth = 1;
  const X = x => L + pw * Math.min(1, x / Math.max(steps, 1)), Y = y => T + ph * (1 - (y - ymin) / (ymax - ymin || 1));
  for (let i = 0; i <= 4; i++) {
    const y = ymin + (ymax - ymin) * i / 4;
    c.beginPath(); c.moveTo(L, Y(y)); c.lineTo(w - R, Y(y)); c.stroke();
    c.textAlign = "right"; c.fillText(fmt(y), L - 6, Y(y));
  }
  c.textAlign = "center"; c.textBaseline = "top";
  for (let i = 0; i <= 4; i++) c.fillText(((steps * i / 4) / 1000).toFixed(0) + "k", L + pw * i / 4, h - B + 5);
  for (const s of series) {
    if (!s.pts.length) continue;
    c.strokeStyle = s.color; c.fillStyle = s.color; c.lineWidth = s.width || 2; c.setLineDash(s.dash || []);
    c.beginPath();
    s.pts.forEach(([x, y], i) => { const px = X(x), py = Y(Math.max(ymin, Math.min(ymax, y))); i ? c.lineTo(px, py) : c.moveTo(px, py); });
    c.stroke(); c.setLineDash([]);
    if (s.dots) s.pts.forEach(([x, y]) => { c.beginPath(); c.arc(X(x), Y(Math.max(ymin, Math.min(ymax, y))), 3, 0, 7); c.fill(); });
  }
}

function smooth(pts, k = 3) {
  return pts.map((p, i) => { const a = pts.slice(Math.max(0, i - k + 1), i + 1); return [p[0], a.reduce((s, q) => s + q[1], 0) / a.length]; });
}

function renderChart(d) {
  const total = d?.total_steps || 1, tr = d?.train || [], ev = d?.eval || [];
  const base = d?.baseline?.success_rate;
  drawChart($("#t-chart"), [
    { pts: smooth(tr.filter(r => r.train_success !== null).map(r => [r.steps, r.train_success])), color: "#4da3ff", width: 1.5 },
    { pts: base === undefined ? [] : [[0, base], [total, base]], color: "#f5b942", dash: [6, 5], width: 1.5 },
    { pts: ev.map(r => [r.steps, r.success]), color: "#4cd08a", dots: true },
  ], { ymin: 0, ymax: 1, fmt: v => (100 * v).toFixed(0) + "%", steps: total });
  const rets = [...tr.map(r => r.train_return), ...ev.map(r => r["return"])].filter(v => v !== null && v !== undefined);
  const lo = Math.min(0, ...rets), hi = Math.max(1, ...rets);
  drawChart($("#t-chart2"), [
    { pts: smooth(tr.filter(r => r.train_return !== null).map(r => [r.steps, r.train_return])), color: "#c792ea", width: 1.5 },
    { pts: ev.map(r => [r.steps, r["return"]]), color: "#4cd08a", dots: true },
  ], { ymin: lo, ymax: hi, fmt: v => v.toFixed(0), steps: total });
}

/* ---------------------------------------------------------------- runs list and details */
const ACTIVE = new Set(["starting", "running", "evaluating"]);

async function refreshRuns() {
  const r = await api("/api/train/runs");
  TR.runs = r.runs;
  $("#t-runs").innerHTML = TR.runs.length ? TR.runs.map(run => {
    const p = run.total_steps ? Math.min(100, 100 * (run.steps || 0) / run.total_steps) : 0;
    const cls = run.status === "done" ? "ok" : ACTIVE.has(run.status) ? "warn" : run.status === "stopped" ? "" : "bad";
    return `<div class="run${run.id === TR.sel ? " sel" : ""}" data-id="${esc(run.id)}">
      <div class="row tight"><b>${esc(run.vehicle)}</b><span class="hint">${esc(run.task)} &middot; ${esc(run.algo)}</span><span class="pill ${cls}">${esc(run.status)}</span></div>
      <div class="bar"><i style="width:${p}%"></i></div>
      <div class="hint">${(run.steps || 0).toLocaleString()} / ${(run.total_steps || 0).toLocaleString()} steps${run.best_eval != null ? " &middot; best eval " + pct(run.best_eval) : ""}</div></div>`;
  }).join("") : `<span class="hint">no runs yet</span>`;
  $$("#t-runs .run").forEach(el => el.onclick = () => selectRun(el.dataset.id));
  if (!TR.sel && TR.runs.length) TR.sel = TR.runs[0].id;
  if (TR.sel) await showDetail();
}

async function selectRun(id) { TR.sel = id; await refreshRuns(); }

async function showDetail() {
  let d;
  try { d = await api("/api/train/run/" + TR.sel); } catch { TR.sel = null; return; }
  TR.detail = d;
  $("#t-title").textContent = `${d.vehicle} / ${d.task} / ${d.algo}`;
  const stat = (v, l) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`;
  const left = d.total_steps - (d.steps || 0), eta = d.fps > 0 && ACTIVE.has(d.status) ? Math.round(left / d.fps) : null;
  $("#t-stats").innerHTML = stat(d.status, "status") + stat((d.steps || 0).toLocaleString(), "steps") + stat(d.fps ? Math.round(d.fps).toLocaleString() + "/s" : "-", "speed") +
    stat(eta !== null ? (eta > 90 ? Math.round(eta / 60) + " min" : eta + " s") : "-", "time left") + stat(pct(d.best_eval), "best eval success") + stat(pct(d.train_success), "training success");
  const log = $("#t-log");
  log.hidden = !(d.status === "crashed" || d.status === "error");
  log.textContent = d.error ? d.error + "\n\n" + d.log : d.log;
  $("#t-stop").disabled = !ACTIVE.has(d.status);
  $("#t-del").disabled = ACTIVE.has(d.status);
  $("#t-watch").disabled = !d.model;
  renderChart(d);
  renderFinal(d);
}

function renderFinal(d) {
  const f = d.final_eval;
  if (!f) { $("#t-final").innerHTML = `<span class="hint">${ACTIVE.has(d.status) ? "the comparison runs when training ends" : "no final evaluation for this run"}</span>`; return; }
  const row = (label, r) => `<tr><td>${label}</td><td><b>${pct(r.success_rate)}</b> <span class="hint">(${pct(r.success_ci95[0])}&ndash;${pct(r.success_ci95[1])})</span></td><td>${num(r.mean_time_to_success)} s</td><td>${num(r.contact_steps_per_episode, 2)}</td><td>${Object.keys(r.crashes).length ? esc(JSON.stringify(r.crashes)) : r.timeouts + " timeouts"}</td></tr>`;
  $("#t-final").innerHTML = `<table><thead><tr><th></th><th>success (95% CI)</th><th>time</th><th>contact/ep</th><th>failures</th></tr></thead><tbody>${row("trained policy", f.trained)}${f.baseline ? row("classical baseline", f.baseline) : ""}</tbody></table>
    <p class="note">${f.trained.episodes} episodes each, identical scenarios. Overlapping intervals mean the difference is not significant.</p>`;
}

/* ---------------------------------------------------------------- actions */
function fillTasks() {
  const kind = LAB.cat.vehicles[$("#t-veh").value].kind;
  const tasks = Object.entries(LAB.cat.tasks).filter(([, t]) => (!t.kinds.length || t.kinds.includes(kind)) && t.backends.includes("mujoco"));
  $("#t-task").innerHTML = tasks.map(([n]) => `<option value="${esc(n)}">${esc(n)}</option>`).join("") || `<option value="">(no task for ${esc(kind)})</option>`;
  $("#t-taskinfo").textContent = tasks.length ? LAB.cat.tasks[$("#t-task").value]?.description || "" : "";
}

async function startTraining() {
  if (!$("#t-task").value) throw new Error("this vehicle has no task yet");
  const steps = +$("#t-steps").value;
  const r = await api("/api/train/start", { vehicle: $("#t-veh").value, task: $("#t-task").value, algo: $("#t-algo").value, total_steps: steps,
    n_envs: +$("#t-envs").value, seed: +$("#t-seed").value || 0, curriculum_fraction: $("#t-curr").checked ? 0.4 : 0.0,
    eval_every: Math.max(5000, Math.round(steps / 12)), eval_episodes: 20 });
  $("#t-msg").textContent = "Started " + r.id;
  TR.sel = r.id;
  await refreshRuns();
}

/* ---------------------------------------------------------------- camera detector runs */
const DT = { runs: [], sel: null, detail: null };

async function refreshDets() {
  DT.runs = (await api("/api/perception/runs")).runs;
  $("#d-runs").innerHTML = DT.runs.length ? DT.runs.map(run => {
    const p = run.total_steps ? Math.min(100, 100 * (run.steps || 0) / run.total_steps) : 0;
    const cls = run.status === "done" ? "ok" : ACTIVE.has(run.status) ? "warn" : run.status === "stopped" ? "" : "bad";
    return `<div class="run${run.id === DT.sel ? " sel" : ""}" data-id="${esc(run.id)}">
      <div class="row tight"><b>${esc(run.vehicle)}</b><span class="hint">${esc(run.task)} &middot; ${esc(run.algo)}</span><span class="pill ${cls}">${esc(run.status === "running" && run.stage ? run.stage : run.status)}</span></div>
      <div class="bar"><i style="width:${p}%"></i></div>
      <div class="hint">${esc(run.stage || "")} ${(run.steps || 0).toLocaleString()} / ${(run.total_steps || 0).toLocaleString()}</div></div>`;
  }).join("") : `<span class="hint">no detector runs yet</span>`;
  $$("#d-runs .run").forEach(el => el.onclick = () => { DT.sel = el.dataset.id; guard(refreshDets)(); });
  if (!DT.sel && DT.runs.length) DT.sel = DT.runs[0].id;
  $("#d-card").hidden = !DT.sel;
  if (DT.sel) await showDet();
}

async function showDet() {
  let d;
  try { d = await api("/api/perception/run/" + DT.sel); } catch { DT.sel = null; $("#d-card").hidden = true; return; }
  DT.detail = d;
  $("#d-title").textContent = `${d.vehicle} / ${d.task} / ${(d.classes || []).join(", ")}`;
  const stat = (v, l) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`;
  const ev = d.detector_eval;
  $("#d-stats").innerHTML = stat(d.status === "running" && d.stage ? d.stage : d.status, "status") + stat(`${(d.steps || 0)} / ${(d.total_steps || 0)}`, d.stage === "capture" ? "images" : "epochs") +
    stat(d.best_eval != null ? d.best_eval.toFixed(2) : "-", "best validation mAP") + stat(ev ? ev.mean_f1.toFixed(2) : "-", "test F1 (mean)") + stat(ev?.ms_per_image ? ev.ms_per_image + " ms" : "-", "inference / image");
  const ds = d.dataset;
  $("#d-eval").innerHTML = ev ? `<table><thead><tr><th>class</th><th>precision</th><th>recall</th><th>F1</th><th>test boxes</th></tr></thead><tbody>${Object.entries(ev.classes).map(([c, r]) =>
    `<tr><td>${esc(c)}</td><td>${pct(r.precision)}</td><td>${pct(r.recall)}</td><td>${pct(r.f1)}</td><td>${r.tp + r.fn}</td></tr>`).join("")}</tbody></table>
    <p class="note">Held-out scenes (${ev.images} images), boxes matched at IoU 0.5. These are simulator images: real cameras need real data or heavier randomisation.</p>`
    : ds ? `<p class="note">Dataset: ${Object.entries(ds).map(([s, v]) => `${s} ${v.images} images`).join(", ")}</p>` : "";
  $("#d-previews").innerHTML = d.previews.map(n => `<div><div class="hint">${n === "dataset_preview.jpg" ? "training labels" : "test detections (green) vs truth (white)"}</div><img class="preview" src="/api/perception/file/${esc(d.id)}/${esc(n)}?t=${Date.now() % 100000}" alt="${esc(n)}"></div>`).join("");
  const log = $("#d-log");
  log.hidden = !(d.status === "crashed" || d.status === "error");
  log.textContent = (d.error ? d.error + "\n\n" : "") + d.log;
  $("#d-stop").disabled = !ACTIVE.has(d.status);
  $("#d-del").disabled = ACTIVE.has(d.status);
  $("#d-try").disabled = !d.model;
}

async function startDetector() {
  if (!$("#t-task").value) throw new Error("this vehicle has no task yet");
  const r = await api("/api/perception/start", { vehicle: $("#t-veh").value, task: $("#t-task").value, images: +$("#d-images").value,
    epochs: +$("#d-epochs").value, size: $("#d-size").value, seed: +$("#t-seed").value || 0 });
  $("#d-msg").textContent = "Started " + r.id;
  DT.sel = r.id;
  await refreshDets();
}

function schedule() {
  clearTimeout(TR.timer);
  if (S.tab !== "train") return;
  const busy = TR.runs.some(r => ACTIVE.has(r.status)) || DT.runs.some(r => ACTIVE.has(r.status));
  TR.timer = setTimeout(guard(async () => { await refreshRuns(); await refreshDets(); schedule(); }), busy ? 1500 : 6000);
}

(function initTrain() {
  const sec = $("#tab-train");
  if (!sec) return;
  sec.innerHTML = TRAIN_HTML;
  const ready = () => {
    const byKind = {};
    for (const [n, v] of Object.entries(LAB.cat.vehicles)) (byKind[v.kind] ||= []).push(n);
    $("#t-veh").innerHTML = Object.entries(byKind).map(([k, ns]) => `<optgroup label="${esc(LAB.cat.kinds[k].title)}">${ns.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join("")}</optgroup>`).join("");
    $("#t-veh").value = LAB.cat.vehicles.mid_4wd ? "mid_4wd" : $("#t-veh").value;
    $("#t-veh").onchange = fillTasks;
    $("#t-task").onchange = () => { $("#t-taskinfo").textContent = LAB.cat.tasks[$("#t-task").value]?.description || ""; };
    fillTasks();
    if (LAB.cat.tasks.goto_open) { $("#t-task").value = "goto_open"; $("#t-taskinfo").textContent = LAB.cat.tasks.goto_open.description; }
  };
  document.addEventListener("labready", ready);
  if (window.LAB && LAB.cat) ready();
  $("#t-start").onclick = guard(startTraining);
  $("#t-stop").onclick = guard(async () => { await api("/api/train/stop", { id: TR.sel }); $("#t-msg").textContent = "Stopping..."; });
  $("#t-del").onclick = guard(async () => { if (!confirm("Delete this run and its saved models?")) return; await api("/api/train/delete", { id: TR.sel }); TR.sel = null; await refreshRuns(); });
  $("#t-watch").onclick = guard(async () => { if (TR.detail?.model) await labWatch(TR.detail); });
  $("#d-start").onclick = guard(startDetector);
  $("#d-stop").onclick = guard(async () => { await api("/api/perception/stop", { id: DT.sel }); $("#d-msg").textContent = "Stopping..."; });
  $("#d-del").onclick = guard(async () => { if (!confirm("Delete this detector run, its dataset and model?")) return; await api("/api/perception/delete", { id: DT.sel }); DT.sel = null; await refreshDets(); });
  $("#d-try").onclick = guard(async () => { if (DT.detail?.model) await labTryDetector(DT.detail); });
  document.addEventListener("tabchange", e => { if (e.detail === "train") { guard(async () => { await refreshRuns(); await refreshDets(); })().then(schedule); } });
  addEventListener("resize", () => TR.detail && renderChart(TR.detail));
})();
