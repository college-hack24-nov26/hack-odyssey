"use strict";
/* Goal tab: describe the job in your own words, the AI planner thinks it through (what can go wrong, what the machine needs, which parts, what to stress-test),
   the simulator checks every claim, then you watch it, stress-test it, and get the build sheet. The AI proposes; the simulator and the Builder check.
   Without an AI key (or when it fails) the three ready-made jobs still work offline.
   Relies on helpers from app.js ($, $$, esc, api, toast, guard, S, setTab), lab.js (labOpen, LAB, clone) and builder.js (bldSetBuild).
   Every top-level name here starts with goal/GOAL: all tab scripts share one global scope. */
const GOAL = { goals: [], kits: [], goal: null, answers: {}, res: null, pick: 0, job: null, timer: null, busy: false, llm: null, planJob: null, planTimer: null, qAnswers: {} };

const GOAL_HTML = `
<div class="col">
  <div class="card">
    <h3>Describe the job</h3>
    <textarea id="g-text" rows="5" placeholder="Say what the machine must do and where, in your own words. For example: a boat that goes out to people trapped by a flood, even in strong wind and rough water." aria-label="the job"></textarea>
    <label for="g-kit">Parts you already have</label>
    <select id="g-kit"></select>
    <div class="row" style="margin-top:10px"><button class="btn primary" id="g-plan">Plan it with AI</button><button class="btn danger" id="g-planstop" hidden>Stop</button></div>
    <p class="note" id="g-llmline"></p>
    <details id="g-llmbox"><summary>AI settings (OpenRouter)</summary>
      <label for="g-key">API key <span class="hint">stored only on this computer, never shown again</span></label>
      <input id="g-key" type="password" autocomplete="off" placeholder="sk-or-v1-...">
      <label for="g-model">Model <span class="hint">leave empty for the default</span></label>
      <input id="g-model" type="text" placeholder="nvidia/nemotron-3-ultra-550b-a55b:free">
      <div class="row" style="margin-top:8px"><button class="btn small primary" id="g-keysave">Save</button><button class="btn small" id="g-keytest">Test (uses 1 request)</button><button class="btn small danger" id="g-keyclear">Remove key</button></div>
      <p class="note">Free models may keep what you send. Only your job text, your answers and the public parts list are sent. The free tier allows about 50 requests a day; one plan uses 1 to 3.</p>
      <p class="note">Some free models (the Inkling ones) are limited by OpenRouter to approved apps and refuse this tool; they are skipped automatically and the next model is used.</p>
    </details>
  </div>
  <details class="card" id="g-ready"><summary><b>Ready-made jobs</b> <span class="hint">no AI needed</span></summary>
    <div class="goal-cards" id="g-cards"></div>
    <div id="g-qcard" hidden>
      <h3 id="g-qtitle" style="margin-top:12px">A few questions</h3>
      <div id="g-questions"></div>
      <div class="row" style="margin-top:12px"><button class="btn primary" id="g-design">Design my machine</button></div>
      <p class="note" id="g-wait"></p>
    </div>
  </details>
</div>
<div class="col">
  <div class="card" id="g-welcome"><h3>How this works</h3>
    <ol class="goal-steps"><li>Describe the job in your own words.</li>
    <li>The AI thinks it through: what can go wrong, what the machine needs, which real parts could do it.</li>
    <li><b>The simulator checks every claim.</b> Parts must exist, the physics must work, the numbers are measured, not believed.</li>
    <li>Watch it do the job, then stress-test it: waves, wind, mud, failures. You see where it starts to fail and what was not tested.</li>
    <li>Get a parts list, wiring and steps to build it.</li></ol>
    <p class="note">Everything here is an estimate from datasheets and simulation. It has not been checked against your real hardware until you calibrate it.</p>
  </div>
  <div class="card" id="g-planpanel" hidden></div>
  <div class="card" id="g-understood" hidden></div>
  <div class="card" id="g-scen" hidden></div>
  <div class="card" id="g-result" hidden></div>
  <div class="card" id="g-stress" hidden></div>
</div>`;

const goalCur = () => GOAL.res && GOAL.res.proposals[GOAL.pick];
const goalNum = (v, d = 1) => (v == null ? "-" : (+v).toFixed(d));
const GOAL_UNITS = { pipe_d: ["must fit through", "m"], distance_m: ["distance", "m"], wet: ["wet floor", ""], minutes: ["time", "min"], wind_ms: ["wind", "m/s"], wave_hs: ["waves", "m"], range_m: ["range", "m"], payload_kg: ["load", "kg"] };

/* ---------------------------------------------------------------- AI settings and status */
async function goalLlmStatus() {
  try { GOAL.llm = await api("/api/goal/llm/status"); } catch (e) { GOAL.llm = null; }
  const s = GOAL.llm, line = $("#g-llmline");
  $("#g-llmbox").hidden = !!(s && s.configured);                     // once a key is saved the box is gone: nobody needs to see it again
  if (!s) { line.textContent = "Could not read the AI settings."; return; }
  $("#g-model").placeholder = s.default_model || "nvidia/nemotron-3-ultra-550b-a55b:free";
  if (!s.configured) { line.innerHTML = `AI planner: <b>not set up</b>. Add your OpenRouter key below (once; it is remembered). Until then the ready-made jobs work without it.`; return; }
  const u = s.usage;
  const skipped = (s.skipped_models || []).length ? ` <span class="hint">Skipped, limited to approved apps by OpenRouter: ${s.skipped_models.map(esc).join(", ")}.</span>` : "";
  line.innerHTML = `AI planner: <b>ready</b> (${esc(s.model)})` + (u && u.free_requests_left != null ? `, ${u.free_requests_left} of ${u.free_requests_limit} free requests left today` : "") + (s.usage_error ? ` <span class="hint">(${esc(s.usage_error)})</span>` : "") + skipped;
}

async function goalSaveKey() {
  const body = {}, key = $("#g-key").value.trim(), model = $("#g-model").value.trim();
  if (key) body.api_key = key;
  body.model = model;
  await api("/api/goal/llm/settings", body);
  $("#g-key").value = "";
  toast(key ? "Key saved on this computer" : "Model saved");
  await goalLlmStatus();
}

async function goalTestKey() {
  $("#g-keytest").disabled = true;
  try { const r = await api("/api/goal/llm/test", {}); toast(`The AI answered in ${r.seconds} s (${r.model})`); }
  finally { $("#g-keytest").disabled = false; goalLlmStatus(); }
}

/* ---------------------------------------------------------------- planning with the AI */
async function goalPlan(answers, final) {
  const text = $("#g-text").value.trim();
  if (!text) { toast("Describe the job first", true); return; }
  $("#g-welcome").hidden = true;
  GOAL.res = null; GOAL.pick = 0;
  ["#g-understood", "#g-scen", "#g-result", "#g-stress"].forEach(s => { $(s).hidden = true; });
  const panel = $("#g-planpanel");
  panel.hidden = false;
  panel.innerHTML = goalPlanHtml("Starting", 0, "");
  $("#g-plan").disabled = true; $("#g-planstop").hidden = false;
  const r = await api("/api/goal/plan/start", { text, answers: answers || {}, kit: $("#g-kit").value || null, final: !!final });
  GOAL.planJob = r.id;
  clearInterval(GOAL.planTimer);
  GOAL.planTimer = setInterval(goalPollPlan, 1000);
}

function goalPlanHtml(phase, seconds, tail, open) {
  return `<h3>Planning <span class="hint">${seconds ? seconds + " s" : ""}</span></h3>
    <div class="goal-phase"><span class="goal-dots"><i></i><i></i><i></i></span> ${esc(phase)}...</div>
    <div class="bar"><i class="goal-indeterminate"></i></div>
    <p class="hint">A free model writes slowly (about 18 words a second), so a plan can take one to three minutes. Your job is checked by the simulator afterwards; nothing the AI says is taken on trust.</p>
    ${tail ? `<details${open ? " open" : ""}><summary>What the AI is writing</summary><pre class="code goal-tail">${esc(tail)}</pre></details>` : ""}`;
}

function goalPlanEnded() {
  clearInterval(GOAL.planTimer); GOAL.planTimer = null;
  $("#g-plan").disabled = false; $("#g-planstop").hidden = true;
}

async function goalPollPlan() {
  let j;
  try { j = await api("/api/goal/plan/status?id=" + GOAL.planJob); } catch (e) { goalPlanEnded(); return; }
  if (j.state === "running") {
    const panel = $("#g-planpanel"), keep = panel.querySelector("details"), open = !!(keep && keep.open);          // the panel is redrawn every second: keep what the reader opened
    panel.innerHTML = goalPlanHtml(j.phase, Math.round(j.seconds || 0), j.tail, open);
    const pre = panel.querySelector(".goal-tail");
    if (pre) pre.scrollTop = pre.scrollHeight;
    return;
  }
  goalPlanEnded();
  goalLlmStatus();
  if (j.state === "error") { $("#g-planpanel").innerHTML = `<h3>Planning</h3><p class="note err">${esc(j.error)}</p><p class="hint">The ready-made jobs still work without the AI.</p>`; return; }
  if (j.state === "stopped") { $("#g-planpanel").innerHTML = `<h3>Planning</h3><p class="note">Stopped.</p>`; return; }
  const r = j.result;
  if (j.state === "questions") { goalAsk(r); return; }
  if (j.state === "unsupported") { goalUnsupported(r); return; }
  goalShow(r);
}

function goalAsk(r) {
  GOAL.qAnswers = {};
  const qs = r.plan.questions;
  $("#g-planpanel").innerHTML = `<h3>The AI needs to know</h3><p class="hint">${esc(r.plan.understanding.summary)}</p>` + qs.map(q => `<div class="goal-q"><label>${esc(q.text)}</label>
    ${q.why ? `<div class="hint">${esc(q.why)}</div>` : ""}
    ${q.choices.length ? `<div class="goal-opts" data-q="${esc(q.id)}">${q.choices.map((c, i) => `<button class="btn small" data-v="${esc(c)}">${esc(c)}</button>`).join("")}</div>`
      : `<input type="number" step="any" data-q="${esc(q.id)}" value="${q.number && q.number.default != null ? q.number.default : ""}"${q.number && q.number.max != null ? ` min="${q.number.min}" max="${q.number.max}"` : ""} style="max-width:160px"> ${esc(q.number ? q.number.unit : "")}${q.number && q.number.max != null ? ` <span class="hint">(the simulator supports ${+q.number.min}–${+q.number.max})</span>` : ""}`}</div>`).join("") +
    `<div class="row" style="margin-top:12px"><button class="btn primary" id="g-qgo">Continue</button></div>`;
  $$("#g-planpanel .goal-opts").forEach(box => $$("button", box).forEach(b => b.onclick = () => {
    GOAL.qAnswers[box.dataset.q] = b.dataset.v;
    $$("button", box).forEach(x => x.classList.toggle("primary", x === b));
  }));
  $("#g-qgo").onclick = guard(async () => {
    $$("#g-planpanel input[data-q]").forEach(i => { if (i.value !== "") GOAL.qAnswers[i.dataset.q] = +i.value; });
    qs.forEach(q => { if (!(q.id in GOAL.qAnswers)) GOAL.qAnswers[q.id] = q.choices.length ? q.choices[0] : (q.number && q.number.default != null ? q.number.default : "no preference"); });
    await goalPlan(GOAL.qAnswers, true);
  });
}

function goalUnsupported(r) {
  $("#g-planpanel").innerHTML = `<h3>Not something this simulator can do</h3><p class="note warn">${esc(r.note)}</p>
    <p class="hint">It covers ground rovers, drones and boats. Try describing the job differently, or use a ready-made job.</p>`;
  goalUnderstood(r);
}

/* ---------------------------------------------------------------- showing a plan */
function goalShow(r) {
  GOAL.res = r; GOAL.pick = 0; GOAL.goal = r.goal || null;
  $("#g-planpanel").hidden = true;
  goalUnderstood(r);
  goalScenarios(r);
  if (r.unmatched || !r.goal) { $("#g-result").hidden = false; $("#g-result").innerHTML = `<h3>No machine yet</h3><p class="note warn">${esc(r.note || "")}</p>`; return; }
  goalResult();
}

function goalUnderstood(r) {
  const el = $("#g-understood"), p = r.plan;
  el.hidden = false;
  if (!p) { el.innerHTML = `<h3>How this was made <span class="pill warn">offline planner</span></h3><p class="note">${esc(r.note || "")}</p>`; return; }
  const u = p.understanding, pr = Object.entries(p.params || {});
  const badge = u.platform === "unsupported" ? `<span class="pill warn">AI answer: not possible here</span>` : `<span class="pill ok">AI plan, checked by the simulator</span>`;
  el.innerHTML = `<h3>What the AI understood ${badge}</h3>
    <p class="goal-head">${esc(u.summary)}</p>
    <p class="hint">Machine type: <b>${esc(u.platform)}</b>. ${esc(u.why)} <span class="hint">(${esc(r.model || "")}, ${r.calls} request${r.calls === 1 ? "" : "s"})</span></p>
    ${u.assumptions.length ? `<div class="hint">Assumed: ${u.assumptions.map(esc).join(" · ")}</div>` : ""}
    ${pr.length ? `<div class="chips" style="margin-top:8px">${pr.map(([k, v]) => `<span class="chip">${esc((GOAL_UNITS[k] || [k, ""])[0])}: ${+v.toFixed(2)} ${esc((GOAL_UNITS[k] || ["", ""])[1])}</span>`).join("")}</div>` : ""}
    ${(p.requirements || []).length ? `<div class="hint" style="margin-top:8px">Extra requirements it set: ${p.requirements.map(q => esc(q.text) + " (" + esc(q.source === "user" ? "from you" : "inferred") + ")").join(" · ")}</div>` : ""}
    ${(p.issues || []).length ? `<details style="margin-top:8px"><summary>What the checker corrected or dropped (${p.issues.length})</summary><ul>${p.issues.map(i => `<li>${esc(i)}</li>`).join("")}</ul></details>` : ""}`;
}

function goalScenarios(r) {
  const el = $("#g-scen"), sc = r.plan && r.plan.scenarios;
  if (!sc || !sc.length) { el.hidden = true; return; }
  el.hidden = false;
  const tested = sc.filter(s => s.simulated).length;
  el.innerHTML = `<h3>What could go wrong <span class="hint">${tested} of ${sc.length} can be simulated</span></h3>` +
    sc.map(s => `<div class="fititem ${s.simulated ? "ok" : "warn"}"><b>${s.simulated ? "✓" : "✗"}</b>${esc(s.name)} <span class="hint">${esc(s.why)} ${s.simulated ? "· will be stress-tested" : "· <u>not testable here</u>, listed as not tested"}</span></div>`).join("");
}

/* ---------------------------------------------------------------- the result (shared with the ready-made jobs) */
const GOAL_LIGHT = { green: ["ok", "Looks right"], amber: ["warn", "Watch these"], red: ["bad", "Not there yet"] };

function goalResult() {
  const r = GOAL.res, el = $("#g-result"), p = goalCur();
  el.hidden = false; $("#g-welcome").hidden = true;
  if (!p) { el.innerHTML = `<h3>Your machine</h3><p class="note warn">${esc(r.note || "No design found.")}</p>`; return; }
  const f = p.facts, plat = GOAL.goal.platform;
  const nums = plat === "air" ? [[goalNum(f.runtime, 0), "min in the air"], [goalNum(f.twr), "thrust / weight"], [goalNum(p.mass * 1000, 0), "grams"]]
    : plat === "water" ? [[goalNum(f.top_speed), "m/s top speed"], [goalNum(f.runtime, 0), "min at full power"], [goalNum(p.mass, 0), "kg"]]
      : [[goalNum(f.top_speed, 2), "m/s top speed"], [goalNum(f.runtime, 0), "min of driving"], [goalNum(p.mass * 1000, 0), "grams"]];
  const [cls, word] = GOAL_LIGHT[p.verdict.light];
  const worstOn = !!(r.watch && Object.keys(r.watch.conditions).length);
  const tabs = r.proposals.length > 1 ? `<div class="seg" id="g-tabs">${r.proposals.map((x, i) => `<button class="${i === GOAL.pick ? "on" : ""}" data-i="${i}">${esc(x.label)}</button>`).join("")}</div>` : "";
  el.innerHTML = `<h3>${esc(r.meets_goal ? "Your machine" : p.label)} <span class="pill ${cls}">${word}</span></h3>${tabs}
    <p class="goal-head">${esc(p.verdict.headline)}</p>
    ${p.verdict.reasons.map(t => `<div class="find ${p.verdict.light === "red" ? "error" : "warn"}">${esc(t)}</div>`).join("")}
    ${p.why ? `<p class="hint">Why the AI chose it: ${esc(p.why)}</p>` : ""}
    ${r.note ? `<p class="note warn">${esc(r.note)}</p>` : ""}
    <div class="stats">${nums.map(([v, l]) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`).join("")}</div>
    <h3 style="margin-top:14px">Does it meet what was asked? <span class="hint">measured by the simulator's physics</span></h3>
    ${p.checks.map(c => `<div class="fititem ${c.ok ? "ok" : "fail"}"><b>${c.ok ? "✓" : "✗"}</b>${esc(c.text)} <span class="hint">${esc(c.detail)}</span></div>`).join("")}
    <h3 style="margin-top:14px">Parts</h3>
    <div class="chips">${p.parts.map(n => `<span class="chip ${p.owned.includes(n) ? "ok" : "warn"}" title="${p.owned.includes(n) ? "you have this" : "to buy"}">${esc(n)}</span>`).join("")}</div>
    <p class="hint">Green: you have it. Amber: to buy. The sheet lists where each number came from.</p>
    <div class="row" style="margin-top:12px;flex-wrap:wrap">
      <button class="btn primary" id="g-watch">Watch it work</button>
      ${worstOn ? `<button class="btn" id="g-worst" title="${esc(Object.entries(GOAL.res.watch.conditions).map(([k, v]) => k + " " + v).join(", "))}">Watch in the worst conditions</button>` : ""}
      <button class="btn primary" id="g-tough">How tough is it?</button>
      <button class="btn" id="g-sheet">Build sheet</button>
      <button class="btn small" id="g-adv">Advanced: open in the Builder</button>
      ${plat === "ground" && (GOAL.res.params || {}).pipe_d ? `<button class="btn small" id="g-rover">Use on my real rover</button>` : ""}
    </div><p class="note" id="g-msg"></p>`;
  $$("#g-tabs button").forEach(b => b.onclick = () => { GOAL.pick = +b.dataset.i; $("#g-stress").hidden = true; goalResult(); });
  $("#g-watch").onclick = guard(() => goalWatch(false)); $("#g-tough").onclick = guard(goalTough); $("#g-sheet").onclick = guard(goalSheet);
  if ($("#g-worst")) $("#g-worst").onclick = guard(() => goalWatch(true));
  $("#g-adv").onclick = guard(goalAdvanced);
  if ($("#g-rover")) $("#g-rover").onclick = guard(goalRover);
}

async function goalWatch(worst) {
  const p = goalCur(), w = GOAL.res.watch;
  const task = w ? w.task : GOAL.goal.task;
  await labOpen(p.vehicle, { name: w ? w.task.name : GOAL.goal.task });
  $("#l-pol").value = "baseline";
  const conditions = worst && w ? w.conditions : {};                              // calm by default: the worst case can be a machine that cannot move at all (grip 0.05), which the stress test reports
  await api("/api/lab/start", { vehicle: p.vehicle, task, policy: "baseline", speed: 1, seed: 1, loop: false, hazards: conditions });
  Object.entries(conditions).forEach(([k, v]) => { const el = document.querySelector(`#l-condbody input[data-k="${k}"]`); if (el) { el.value = v; el.oninput(); } });
  LAB.sceneId = null;
  toast(Object.keys(conditions).length ? "Running in the worst conditions you asked for: " + Object.entries(conditions).map(([k, v]) => k + " " + v).join(", ") : "Running");
}

function goalAdvanced() {
  const b = clone(goalCur().build); b.name = (GOAL.goal.id === "plan" ? "ai" : GOAL.goal.id) + "_machine";
  setTab("build"); bldSetBuild(b);
}

async function goalSheet() {
  const r = await api("/api/goal/sheet", { goal: GOAL.goal.id, build: goalCur().build, owned: GOAL.res.owned_used || null, plan_id: GOAL.res.plan_id || null });
  $("#g-msg").innerHTML = `Build sheet ready: <a href="${esc(r.url)}" target="_blank" rel="noopener">open it (printable)</a>. ` +
    (r.sheet.wiring.generated ? "It includes the pin table for the ESP32 rover." : "Pin-level wiring is only generated for the ESP32 + DRV8833 rover; this sheet has the parts list, power and safety steps.");
}

async function goalRover() {
  const v = goalCur().vehicle, cam = (v.sensors || []).find(s => s.type === "camera");
  const profile = { length: v.body.length, width: v.body.width, height: v.body.height, mass: v.body.mass, wheel_radius: v.params.wheel_radius };
  if (cam) profile.camera = { height: cam.height, tilt_deg: cam.tilt_deg, fov_deg: cam.fov_deg, width_px: cam.width_px, height_px: cam.height_px, fps: cam.fps };
  await api("/api/hw/setup", { profile, pipe_diameter: GOAL.res.params.pipe_d || null });
  S.hwInit = false;
  setTab("hw");
  toast("Size and camera copied to the Hardware tab. Connect the rover (COM port), tick the checklist, then drive. Speed and turn rate stay as you calibrated them.");
}

/* ---------------------------------------------------------------- stress test */
async function goalTough() {
  const st = $("#g-stress"); st.hidden = false;
  st.innerHTML = `<h3>How tough is it?</h3><p class="note">Pushing the machine through the conditions the plan lists, and the failures that can happen. This takes a few minutes. The same scenes are used at every level, so only the hazard changes.</p>
    <div class="bar"><i id="g-bar" style="width:0"></i></div><p class="hint" id="g-prog">starting...</p><button class="btn small danger" id="g-stop">Stop</button>`;
  const r = await api("/api/goal/sweep/start", { goal: GOAL.goal.id, build: goalCur().build, answers: GOAL.answers, episodes: 4, plan_id: GOAL.res.plan_id || null });
  GOAL.job = r.id;
  $("#g-stop").onclick = guard(() => api("/api/goal/sweep/stop?id=" + GOAL.job, {}));
  clearInterval(GOAL.timer); GOAL.timer = setInterval(goalPoll, 1200);
}

async function goalPoll() {
  let j;
  try { j = await api("/api/goal/sweep/status?id=" + GOAL.job); } catch (e) { clearInterval(GOAL.timer); return; }
  if (j.state === "running") {
    $("#g-bar").style.width = (j.total ? 100 * j.done / j.total : 0) + "%"; $("#g-prog").textContent = `${j.done} of ${j.total} conditions (${j.label})`;
    return;
  }
  clearInterval(GOAL.timer);
  if (j.state === "error") { $("#g-stress").innerHTML = `<h3>How tough is it?</h3><p class="note err">${esc(j.error)}</p>`; return; }
  goalShowSweep(j);
}

function goalCells(levels, thr) {
  return levels.map(l => `<span class="goal-cell"><i>${+l.value.toFixed(2)}</i><u class="${l.success >= thr ? "ok" : l.success >= 0.5 ? "mid" : "bad"}" title="${l.n} runs">${Math.round(100 * l.success)}%</u></span>`).join("");
}

function goalShowSweep(j) {
  const r = j.result, w = r.weakest;
  $("#g-stress").innerHTML = `<h3>How tough is it? ${j.state === "stopped" ? `<span class="pill warn">stopped early</span>` : ""}</h3>
    ${w ? `<div class="find warn"><b>Weakest point: ${esc(w.title)}.</b> ${esc(w.why)}<div class="fix">What may help: ${esc(w.fix)}</div></div>` : `<p class="note ok">Nothing in the tested list broke the design.</p>`}
    <h3 style="margin-top:12px">Conditions that get worse step by step</h3>
    ${r.hazards.map(h => `<div class="goal-h"><b>${esc(h.title)}</b> <span class="hint">${esc(h.plain)}</span><div class="goal-cells">${goalCells(h.levels, r.threshold)}</div><div>${esc(h.summary)}</div></div>`).join("")}
    <h3 style="margin-top:12px">Things that go wrong</h3>
    ${r.faults.map(f => `<div class="fititem ${f.ok ? "ok" : "fail"}"><b>${f.ok ? "✓" : "✗"}</b>${esc(f.title)} <span class="hint">${f.ok ? "it carries on" : "the job succeeds in " + Math.round(100 * f.success) + "% of runs"}${Object.keys(f.crashes).length ? " (" + Object.entries(f.crashes).map(([k, v]) => k + " x" + v).join(", ") + ")" : ""}</span></div>`).join("")}
    <details style="margin-top:12px" open><summary>What was NOT tested</summary><ul>${r.not_tested.map(t => `<li>${esc(t)}</li>`).join("")}</ul></details>
    <p class="note warn">${esc(r.statement)}</p>
    <p><a href="${esc(j.report)}" target="_blank" rel="noopener">Open the full report</a> (${r.episodes} runs per condition, ${r.seconds} s)</p>`;
}

/* ---------------------------------------------------------------- the ready-made jobs (no AI) */
function goalCards() {
  $("#g-cards").innerHTML = GOAL.goals.map(g => `<button class="goal-card${GOAL.goal && GOAL.goal.id === g.id ? " on" : ""}" data-id="${esc(g.id)}">
    <b>${esc(g.card.title || g.title)}</b><span>${esc(g.card.blurb || "")}</span></button>`).join("");
  $$("#g-cards .goal-card").forEach(b => b.onclick = () => goalSelect(b.dataset.id));
}

function goalSelect(id) {
  GOAL.goal = GOAL.goals.find(g => g.id === id) || null;
  GOAL.answers = {}; GOAL.res = null; GOAL.pick = 0;
  GOAL.goal && GOAL.goal.questions.forEach(q => { GOAL.answers[q.id] = q.default; });
  ["#g-result", "#g-stress", "#g-understood", "#g-scen", "#g-planpanel"].forEach(s => { $(s).hidden = true; });
  $("#g-welcome").hidden = !!GOAL.goal;
  goalCards();
  if (!GOAL.goal) { $("#g-qcard").hidden = true; return; }
  $("#g-qcard").hidden = false; $("#g-qtitle").textContent = GOAL.goal.title;
  $("#g-questions").innerHTML = GOAL.goal.questions.map(q => `<div class="goal-q"><label>${esc(q.text)}</label><div class="goal-opts" data-q="${esc(q.id)}">` +
    q.choices.map((c, i) => `<button class="btn small${GOAL.answers[q.id] === i ? " primary" : ""}" data-i="${i}">${esc(c.label)}</button>`).join("") + `</div></div>`).join("");
  $$("#g-questions .goal-opts").forEach(box => $$("button", box).forEach(b => b.onclick = () => {
    GOAL.answers[box.dataset.q] = +b.dataset.i;
    $$("button", box).forEach(x => x.classList.toggle("primary", x === b));
  }));
  $("#g-wait").textContent = "";
}

async function goalDesign() {
  if (!GOAL.goal || GOAL.busy) return;
  GOAL.busy = true; $("#g-design").disabled = true; $("#g-wait").textContent = "Searching the parts library and checking each design...";
  try {
    const res = await api("/api/goal/propose", { goal: GOAL.goal.id, answers: GOAL.answers, kit: $("#g-kit").value || null });
    res.source = "offline";
    GOAL.res = res; GOAL.pick = 0; $("#g-stress").hidden = true;
    $("#g-understood").hidden = true; $("#g-scen").hidden = true;
    goalResult();
    $("#g-wait").textContent = `Checked ${res.scanned} designs.`;
  } finally { GOAL.busy = false; $("#g-design").disabled = false; }
}

window.goalPills = () => `<span class="pill">${GOAL.goal ? esc(GOAL.goal.title) : "describe a job"}</span>`;

(async function goalBoot() {
  const root = document.getElementById("tab-goal");
  if (!root) return;
  root.innerHTML = GOAL_HTML;
  try {
    const r = await api("/api/goal/goals");
    GOAL.goals = r.goals; GOAL.kits = r.kits;
    $("#g-kit").innerHTML = `<option value="">Only what I mark as owned in the Builder</option>` + r.kits.map(k => `<option value="${esc(k.id)}">${esc(k.label)}</option>`).join("");
    goalCards();
    $("#g-plan").onclick = guard(() => goalPlan(null, false));
    $("#g-planstop").onclick = guard(() => api("/api/goal/plan/stop?id=" + GOAL.planJob, {}));
    $("#g-keysave").onclick = guard(goalSaveKey);
    $("#g-keytest").onclick = guard(goalTestKey);
    $("#g-keyclear").onclick = guard(async () => { await api("/api/goal/llm/settings", { api_key: "" }); toast("Key removed"); goalLlmStatus(); });
    $("#g-design").onclick = guard(goalDesign);
    await goalLlmStatus();
    if (!GOAL.llm || !GOAL.llm.configured) $("#g-llmbox").open = true;
  } catch (e) { root.innerHTML = `<div class="card"><p class="note err">Could not load the goals: ${esc(e.message)}</p></div>`; }
})();
