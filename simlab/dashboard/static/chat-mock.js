"use strict";
/* chat-mock.js: an offline, deterministic replay of a chat, switched on only by /chat?mock=1 (nothing happens otherwise).
   It replaces the network methods of SimChat.net, so the real core code runs unchanged: long-poll, optimistic echo with client_msg_id, card folding, dock, chips.
   Extras: ?speed=4 makes every delay 4x shorter; in the console SimChat.mock.fail(5000) makes polling fail for 5 s (to see the Reconnecting bar).
   Content: chat-fixtures.js (Builder B) provides SimChat.fixtures.floodBoat / sewerRover / droneField, each a whole conversation as an event list with times (`t`, ms
   since the log starts). The mock cuts such a log into turns at its user messages, replays the first turn (the draft) when a job is started, and replays a later turn
   when the person asks for the same thing (make the waves bigger, what about rain, run all, build sheet, improve with AI). Without fixtures a small built-in list of
   generic cards is used so this file works on its own. Nothing here is real: every number comes from the fixture or the built-in list. */
(function () {
  const q = new URLSearchParams(location.search);
  if (q.get("mock") !== "1" || typeof SimChat === "undefined") return;
  const speed = Math.max(0.25, Number(q.get("speed")) || 1);
  try { localStorage.removeItem("simlab.chat.id"); } catch (e) { /* ignore */ }
  try { history.replaceState(null, "", location.pathname + location.search); } catch (e) { /* ignore */ }

  const log = [];
  let seq = 0, mid = 0, tid = 0, failUntil = 0;
  const waiters = new Set();
  const timers = new Set();
  const M = { plan: false, results: 0, running: null, ai: null, watchOn: false, t0: 0, steps: [], stopRequested: false, cond: 0, fx: null, used: {}, segTimers: {}, lastResults: null, job: null };

  function later(ms, fn) {
    const t = setTimeout(() => { timers.delete(t); fn(); }, ms / speed);
    timers.add(t);
    return t;
  }
  function emit(type, payload) {
    const ev = Object.assign({ seq: ++seq, ts: Date.now() / 1000, type }, payload || {});
    log.push(ev);
    Array.from(waiters).forEach(w => w());
    return ev;
  }
  const say = (text, kind, extra) => emit("message", Object.assign({ id: "m" + (++mid), role: "assistant", kind: kind || "say", text }, extra || {}));
  const clone = o => JSON.parse(JSON.stringify(o));
  const act = (type, label, args, o) => Object.assign({ type, label, args: args || {}, enabled: true, reason: null, primary: false }, o || {});
  const chipOf = (id, label, type, args, kind, tag) => ({ id, label, action: { type, args: args || {} }, kind: kind || "secondary", enabled: true, reason: null, tag: tag || null });

  /* ---------------------------------------------------------------- dock (used by the built-in lists and by stop) */
  const STEP_NAMES = [["understand", "Understand"], ["design", "Design"], ["check", "Check"], ["tests", "Tests"], ["watch", "Watch"], ["parts", "Parts"]];
  function publishDock(extra) {
    const running = !!(M.ai && M.ai.state === "running");
    emit("dock", Object.assign({
      steps: clone(M.steps), elapsed: M.t0 ? Math.round((Date.now() - M.t0) / 100) / 10 : 0,
      ai: { running, state: M.ai ? M.ai.state : null, phase: M.ai ? M.ai.phase : null, seconds: M.ai ? M.ai.seconds : 0 },
      queue: [], busy: { plan: running, sweep: !!M.running, live: M.watchOn },
    }, extra || {}));
  }
  function step(id, state, detail) {
    const s = M.steps.find(x => x.id === id);
    if (s) { s.state = state; s.detail = detail || ""; }
    publishDock();
  }
  const limitsEv = (n, stale) => ({ count: n, stale: !!stale, line: "Simulation only, with a simple built-in autopilot. " + n + (n === 1 ? " thing" : " things") + " not tested here." });
  const budgetEv = left => ({ ai: { configured: true, enabled: true, setting: "auto", left, limit: 50, approx: true, low: false, exhausted: false } });

  /* ---------------------------------------------------------------- fixtures: cut a log into turns */
  function segments(list) {
    if (!Array.isArray(list) || !list.length) return null;
    const when = e => (e.t != null ? Number(e.t) : (Number(e.ts) || 0) * 1000);
    const iu = [];
    list.forEach((e, i) => { if (e.type === "message" && e.role === "user") iu.push(i); });
    if (!iu.length) return null;
    const segs = iu.map((start, k) => ({ text: String(list[start].text || ""), t0: when(list[start]), events: list.slice(start + 1, k + 1 < iu.length ? iu[k + 1] : list.length), when, key: "s" + k }));
    return { pre: list.slice(0, iu[0]), segs, when };
  }
  function logFor(text) {
    const F = SimChat.fixtures || {};
    const t = (text || "").toLowerCase();
    const name = /sewer|pipe|rover/.test(t) ? "sewerRover" : /drone|field|air/.test(t) ? "droneField" : "floodBoat";
    return segments(F[name]) || segments(F.floodBoat);
  }
  /* replay one turn: every event at its own pace; returns the timers so a Stop can cancel the rest */
  function playSeg(seg, done) {
    const ts = [];
    let last = 0;
    seg.events.forEach(e => {
      const d = Math.max(0, seg.when(e) - seg.t0);
      last = Math.max(last, d);
      ts.push(later(d, () => emitFixture(e)));
    });
    if (done) ts.push(later(last + 40, done));
    M.segTimers[seg.key] = ts;
    return ts;
  }
  function emitFixture(e) {
    const copy = clone(e);
    const type = copy.type;
    ["type", "seq", "ts", "t"].forEach(k => delete copy[k]);
    if (type === "card.create" && copy.card && copy.card.type === "results") M.lastResults = copy.card.id;
    if (type === "dock" && copy.busy) { M.watchOn = !!copy.busy.live; M.running = copy.busy.sweep ? (M.running || { fixture: true }) : null; }
    if (type === "job" && copy.job && copy.job.kind === "tests") M.job = copy.job;
    emit(type, copy);
  }
  function findSeg(re) { return M.fx && M.fx.segs.find(s => re.test(s.text) && !M.used[s.key]); }

  /* ---------------------------------------------------------------- the built-in generic cards (when there are no fixtures) */
  const BUILTIN = {
    understanding: () => ({ id: "understanding", type: "understanding", title: "What I understood", state: "ready", platform: "water", summary: "Boat: find a person 100 m away in 0.5 m waves",
      lines: ["Job: reach a person 100 m out", "Waves up to 0.5 m", "Assumed: calm current", "Assumed: the simple built-in autopilot drives in every test"], actions: [] }),
    design: () => ({ id: "design", type: "design", title: "Design", state: "ready", summary: "Fits 4 of 5 in the estimate: freeboard 0.18 m, needs 0.25 m", seconds: 0.9,
      lines: ["Checked 412 designs in 0.9 s", "These numbers are predictions from datasheets; none were measured on a real build."], actions: [act("open_details", "Open details", { tab: "design" })] }),
    watch: () => ({ id: "watch", type: "watch", title: "Watch it work", state: "running", summary: "Simple built-in autopilot, calm conditions, try 1. This is a demonstration, not a test.",
      lines: ["Time, distance, battery and result appear here when the live view is attached."], actions: [act("stop", "Stop", { job: "live" })] }),
    tests: () => ({ id: "tests", type: "tests", title: "Tests", state: "ready", summary: "6 tests, 23 conditions, 92 tries in all; 2 cannot be tested here",
      lines: ["Waves 0.15 to 0.9 m, 4 tries each", "Wind 3 to 15 m/s, 4 tries each", "Current 0.2 to 1 m/s, 4 tries each", "NOT TESTED: rain, fog"],
      actions: [act("run_tests", "Run all", { cases: "all" }, { primary: true }), act("undo", "Undo", {}, { enabled: false }), act("open_details", "Open details", { tab: "tests" })] }),
  };
  const CHIPS_AFTER_DRAFT = () => [
    chipOf("watch_rough", "Watch it in rough conditions", "watch", { worst: true }),
    chipOf("run_all", "Run all tests (6)", "run_tests", { cases: "all" }, "primary"),
    chipOf("add_more", "Make the waves tougher", "edit", { ops: [] }),
    chipOf("improve", "Improve with AI (about 2-3 min, uses 1-3 requests)", "improve", {}, "secondary", "ai"),
  ];

  /* ---------------------------------------------------------------- scripts */
  function newJob(text) {
    M.plan = true; M.results = 0; M.t0 = Date.now(); M.watchOn = false; M.running = null; M.ai = null; M.used = {}; M.fx = logFor(text);
    M.steps = STEP_NAMES.map(s => ({ id: s[0], label: s[1], state: "pending", detail: "" }));
    emit("budget", budgetEv(38));
    emit("limits", limitsEv(0, false));
    if (M.fx) {
      const draft = M.fx.segs[0];
      M.used[draft.key] = true;
      M.fx.pre.forEach(e => { if (e.type === "budget" || e.type === "limits") emitFixture(e); });
      playSeg(draft);
      return;
    }
    step("understand", "running", "reading your job");
    later(350, () => { emit("card.create", { card: BUILTIN.understanding() }); step("understand", "done"); step("design", "running", "checking designs"); });
    later(450, () => say("Here is a first design and 6 tests (23 conditions). Watching a calm run now."));
    later(1100, () => { emit("card.create", { card: BUILTIN.design() }); step("design", "done"); step("check", "done"); step("tests", "running", "6 tests"); });
    later(1250, () => { emit("card.create", { card: BUILTIN.watch() }); M.watchOn = true; step("tests", "done"); step("watch", "running", "calm, try 1"); });
    later(1450, () => { emit("card.create", { card: BUILTIN.tests() }); emit("limits", limitsEv(2, false)); step("parts", "running", "build sheet"); });
    later(1800, () => { step("parts", "done"); emit("chips", { chips: CHIPS_AFTER_DRAFT() }); });
    later(9000, () => { if (M.watchOn) { M.watchOn = false; emit("card.patch", { id: "watch", patch: { state: "done", summary: "Reached the person in 41.2 s (a demonstration, not a test)", actions: [act("watch", "Run again", {})] } }); step("watch", "done"); } });
  }

  function runAll() {
    if (!M.plan) { say("Describe a job first, or tap one of the examples."); return; }
    if (M.running) { say("Tests are already running."); return; }
    const seg = findSeg(/run all|run the tests|test it/i);
    if (seg) { M.used[seg.key] = true; M.running = { fixture: true, seg }; M.stopRequested = false; playSeg(seg, () => { M.running = null; }); return; }
    M.results += 1;
    const id = "results" + M.results, total = 23, runId = "run" + M.results;
    M.running = { id, runId }; M.stopRequested = false; M.cond = 0; M.lastResults = id;
    const names = ["calm", "waves 0.15 m", "waves 0.3 m", "waves 0.45 m", "waves 0.6 m", "waves 0.9 m", "wind 3 m/s", "wind 6 m/s", "wind 9 m/s", "wind 12 m/s", "wind 15 m/s", "current 0.2 m/s", "current 0.4 m/s", "current 0.6 m/s", "current 0.8 m/s", "current 1 m/s",
      "thruster 1 out", "thruster 2 out", "rudder stuck", "lag 0.1 s", "lag 0.2 s", "lag 0.4 s", "dropout 10%"];
    if (!M.steps.length) M.steps = STEP_NAMES.map(s => ({ id: s[0], label: s[1], state: "done", detail: "" }));
    emit("card.create", { card: { id, type: "results", title: "Results", state: "running", summary: "Condition 1 of " + total + " (" + names[0] + ")", progress: { done: 0, total, label: "conditions" }, lines: [],
      actions: [act("stop", "Stop", { job: "tests" })] } });
    emit("job", { job: { id: runId, kind: "tests", state: "running", label: "Run 6 tests", done: 0, total } });
    publishDock();
    emit("chips", { chips: [chipOf("stop", "Stop", "stop", { job: "tests" }, "primary")] });
    const tickMs = 420;
    const tick = () => {
      if (!M.running || M.running.id !== id) return;
      M.cond += 1;
      const done = M.cond, met = Math.max(0, done - Math.floor(done / 5) - (done > 14 ? 2 : 0));
      const last = names.slice(Math.max(0, done - 3), done);
      if (M.stopRequested || done >= total) {
        const stopped = M.stopRequested && done < total;
        const text = stopped
          ? "Met the bar in " + met + " of " + done + " simulated conditions run so far with the simple built-in autopilot (stopped early, " + (total - done) + " not run). Weakest: waves above 0.6 m."
          : "Met the bar in " + met + " of " + total + " simulated conditions with the simple built-in autopilot. Weakest: waves above 0.6 m.";
        emit("job", { job: { id: runId, kind: "tests", state: stopped ? "stopped" : "done", label: "Run 6 tests", done, total } });
        emit("card.patch", { id, patch: { state: stopped ? "stopped" : "done", summary: text, progress: { done, total, label: "conditions" }, lines: last.concat(["NOT TESTED: rain, fog" + (stopped ? ", and " + (total - done) + " conditions not run" : "")]),
          seconds: Math.round(done * tickMs / 100) / 10, actions: [act("watch", "Watch the weakest test", { weakest: true }), act("sheet", "Get the build sheet", { format: "json" })] } });
        M.running = null; publishDock();
        say(stopped ? "Stopped. " + done + " of " + total + " conditions were run; the rest are marked not run." : "Done. " + text);
        emit("chips", { chips: [chipOf("watch_weakest", "Watch the weakest test", "watch", { weakest: true }, "primary"), chipOf("make_tougher", "Make it tougher", "edit", { ops: [] }), chipOf("get_sheet", "Get the build sheet", "sheet", { format: "json" })] });
        return;
      }
      emit("job", { job: { id: runId, kind: "tests", state: "running", label: "Run 6 tests", done, total } });
      emit("card.patch", { id, patch: { summary: (M.stopRequested ? "Stopping after the current condition. " : "") + "Condition " + (done + 1) + " of " + total + " (" + names[done] + ")", progress: { done, total, label: "conditions" }, lines: last } });
      later(tickMs, tick);
    };
    later(tickMs, tick);
  }

  function improve() {
    if (M.ai && M.ai.state === "running") { say("An AI plan is already running."); return; }
    if (!M.plan) { say("Describe a job first, or tap one of the examples."); return; }
    const seg = findSeg(/improve/i);
    if (seg) { M.used[seg.key] = true; M.ai = { state: "running", phase: "planning", seconds: 0 }; playSeg(seg, () => { M.ai = null; }); return; }
    M.ai = { state: "running", phase: "planning", seconds: 0 };
    emit("card.create", { card: { id: "improve", type: "improve", title: "Improve with AI", state: "running", summary: "Planning", phase: "planning", seconds: 0, chars: 0, tail: "", draft_note: "Your draft stays usable meanwhile.",
      actions: [act("stop", "Stop", { job: "plan" }, { primary: true })] } });
    emit("budget", budgetEv(37));
    emit("chips", { chips: [chipOf("stop", "Stop", "stop", { job: "plan" }, "primary")] });
    publishDock();
    const t0 = Date.now();
    const secs = () => {
      if (!M.ai || M.ai.state !== "running") return;
      M.ai.seconds = Math.round((Date.now() - t0) / 1000 * speed);
      emit("card.patch", { id: "improve", patch: { seconds: M.ai.seconds, chars: 400 * M.ai.seconds, tail: '..."levels": [0.15, 0.3, 0.45, 0.6, 0.9]'.slice(0, 12 + 4 * M.ai.seconds) } });
      publishDock(); later(1000, secs);
    };
    later(1000, secs);
    later(3000, () => {
      if (!M.ai || M.ai.state !== "running") return;
      M.ai.state = "done"; M.ai.phase = null;
      emit("card.patch", { id: "improve", patch: { state: "done", summary: "The AI's plan is ready: 3 changes to your draft", seconds: 3, draft_note: null,
        diff: { count: 3, yours: 1, changes: [
          { id: "c1", area: "Tests", kind: "changed", label: "Waves test: tries per level", draft: "4 tries", ai: "6 tries", yours: false },
          { id: "c2", area: "Tests", kind: "added", label: "Stuck rudder test", draft: "not in the draft", ai: "added", yours: false },
          { id: "c3", area: "Job", kind: "changed", label: "Worst waves", draft: "0.9 m (you set this)", ai: "0.6 m", yours: true }] },
        actions: [act("use_ai", "Use AI version", {}, { primary: true }), act("keep_mine", "Keep mine", {}), act("use_ai_keep_edits", "Use AI, keep my edits", {})] } });
      publishDock();
      say("The AI's plan differs from your draft in 3 places. Pick what to keep.");
      emit("chips", { chips: [chipOf("use_ai", "Use AI version", "use_ai", {}, "primary"), chipOf("keep_mine", "Keep mine", "keep_mine"), chipOf("use_ai_keep_edits", "Use AI, keep my edits", "use_ai_keep_edits")] });
    });
  }

  function finishAI(kind) {
    M.ai = null;
    emit("card.patch", { id: "improve", patch: { state: kind === "keep" ? "kept" : "applied", summary: kind === "keep" ? "Kept your draft" : "Using the AI's plan", actions: [] } });
    if (kind !== "keep" && M.lastResults) emit("card.patch", { id: M.lastResults, patch: { state: "stale" } });
    if (kind !== "keep") emit("limits", limitsEv(2, !!M.lastResults));
    say(kind === "keep" ? "Kept your draft." : "Switched to the AI's plan. Earlier results are marked out of date; nothing was re-run.");
    publishDock();
    emit("chips", { chips: CHIPS_AFTER_DRAFT() });
  }

  function cancelRunning(kind) {
    Object.keys(M.segTimers).forEach(k => {
      const seg = M.fx && M.fx.segs.find(s => s.key === k);
      if (seg && ((kind === "tests" && /run all|test/i.test(seg.text)) || (kind === "plan" && /improve/i.test(seg.text)))) M.segTimers[k].forEach(t => { clearTimeout(t); timers.delete(t); });
    });
  }
  function stop(job) {
    if (job === "tests" && M.running) {
      if (M.running.fixture) {
        cancelRunning("tests");
        const j = M.job || { id: "r1", kind: "tests", label: "Run tests", done: 0, total: 0 };
        emit("card.patch", { id: M.lastResults || "results-1", patch: { progress: { stopping: true } } });
        later(450, () => {
          emit("job", { job: Object.assign({}, j, { state: "stopped" }) });
          emit("card.patch", { id: M.lastResults || "results-1", patch: { state: "stopped", summary: "Stopped after " + j.done + " of " + j.total + " conditions; the rest are marked not run.", progress: { stopping: false, done: j.done, total: j.total }, actions: [] } });
          M.running = null; publishDock({ busy: { plan: false, sweep: false, live: M.watchOn } });
          say("Stopped. " + j.done + " of " + j.total + " conditions were run; the rest are marked not run.");
          emit("chips", { chips: [chipOf("run_all", "Run all tests", "run_tests", { cases: "all" }, "primary")] });
        });
        return;
      }
      M.stopRequested = true;
      emit("card.patch", { id: M.running.id, patch: { summary: "Stopping after the current condition" } });
    } else if (job === "plan" && M.ai && M.ai.state === "running") {
      cancelRunning("plan");
      M.ai = null;
      emit("card.patch", { id: "improve", patch: { state: "stopped", summary: "Stopped", actions: [] } });
      say("Stopped. Requests already sent still count today; your draft is untouched.");
      publishDock(); emit("chips", { chips: CHIPS_AFTER_DRAFT() });
    } else if (job === "live" && M.watchOn) {
      M.watchOn = false;
      emit("card.patch", { id: "watch", patch: { state: "stopped", summary: "Stopped" } });
      publishDock();
    }
  }

  function handle(body) {
    const action = body.action, text = body.text ? String(body.text) : "";
    const shown = text || body.label || (action && action.type) || "";
    emit("message", { id: "m" + (++mid), role: "user", kind: text ? "text" : "action", text: shown, client_msg_id: body.client_msg_id, turn_id: "t" + (++tid) });
    const t = (text || body.label || "").toLowerCase();
    const type = action ? action.type : "";
    if (type === "new_job" || type === "use_kit" || (!M.plan && text && !/^\//.test(text))) {
      if (M.plan) { say("(mock) This replay holds one job. Reload the page to start another."); return; }
      if (type === "use_kit") say("Using your parts. Describe the job and I will design with what you have first.");
      return newJob((action && action.args && action.args.text) || text || body.label || "");
    }
    if (!M.plan) { say("Describe a job first, or tap one of the examples."); return; }
    if (type === "run_tests" || /\b(run all|run the tests|test it|stress)\b/.test(t)) return runAll();
    if (type === "improve" || /\bimprove\b|ask the ai/.test(t)) return improve();
    if (type === "use_ai" || type === "use_ai_keep_edits") return finishAI("use");
    if (type === "keep_mine") return finishAI("keep");
    if (type === "stop") return stop(action.args && action.args.job);
    if (type === "sheet" || /\b(build sheet|parts list)\b/.test(t)) { const s = findSeg(/sheet/i); if (s) { M.used[s.key] = true; return playSeg(s); } }
    if (/\b(rain|fog|snow|gps|underwater)\b/.test(t)) {
      const s = findSeg(/rain|fog|snow/i);
      if (s) { M.used[s.key] = true; return playSeg(s); }
      emit("card.create", { card: { id: "blocked" + (++tid), type: "blocked", title: "Can't do that here", state: "blocked", summary: "The simulator can't test " + (/fog/.test(t) ? "fog" : "rain") + "; I recorded it as not tested.", reason: "The simulator can't test this; I recorded it as not tested.", actions: [] } });
      emit("limits", limitsEv(3, !!M.lastResults));
      return;
    }
    if (type === "answer" && action.args && action.args.topic === "limits") {
      emit("card.create", { card: { id: "limits" + (++tid), type: "limits", title: "What I can't test", state: "ready", summary: "Simulation only, with a simple built-in autopilot. 2 things not tested here.",
        lines: ["Rain is not simulated here.", "Fog and poor visibility are not simulated here.", "A good result here does not show a machine works in the real world."], count: 2, items: [], bullets: ["Simulation only, with a simple built-in autopilot."], actions: [] } });
      return;
    }
    if (type === "undo" || /\bundo\b/.test(t)) { say("Undid the last change.", "note"); return; }
    if (type === "edit" || (/(wave|wind|grip|current)/.test(t) && /(bigger|more|higher|larger|worse|tougher|\d)/.test(t))) {
      const s = findSeg(/(wave|wind|grip|bigger|tougher)/i);
      if (s) { M.used[s.key] = true; return playSeg(s); }
      say("I read this as: waves top level 0.6 to 0.9 m.", "read_as", { undo: { type: "undo", args: {} } });
      say("Waves limited to 0.9 m, the simulator's maximum (you asked for more).", "note");
      emit("card.patch", { id: "tests", patch: { summary: "6 tests, 24 conditions, 96 tries in all; 2 cannot be tested here", lines: ["Waves 0.15 to 0.9 m, 4 tries each (just changed)", "Wind 3 to 15 m/s, 4 tries each", "Current 0.2 to 1 m/s, 4 tries each", "NOT TESTED: rain, fog"] } });
      if (M.lastResults) { emit("card.patch", { id: M.lastResults, patch: { state: "stale" } }); emit("limits", limitsEv(2, true)); }
      return;
    }
    if (/^\/?boom\b/.test(t)) { emit("error", { code: "mock", message: "This is a test error from the mock, shown as a toast." }); return; }
    say("(mock) I read that, but this offline replay only knows a few phrases: \"make the waves bigger\", \"what if there is heavy rain\", \"run all\", \"build sheet\", \"improve with AI\". Or use the buttons.");
  }

  /* ---------------------------------------------------------------- the network replacement */
  const abortErr = () => { const e = new Error("aborted"); e.name = "AbortError"; return e; };
  Object.assign(SimChat.net, {
    newChat() { return Promise.resolve({ id: "mock-" + Math.random().toString(36).slice(2, 8), seq: 0, title: "New chat" }); },
    latest() { return Promise.resolve({ id: null }); },
    capabilities() {
      return Promise.resolve({ ai: { configured: true, enabled: true, setting: "auto", left: 38, limit: 50, approx: true, low: false, exhausted: false }, mujoco: true,
        copy: { headline: "What do you want to build and test?", line: "Describe a job. I will design a machine, test it in simulation, and tell you what I could not test.", placeholder: "e.g. a boat that reaches a person 150 m away in 1 m waves",
          limits: "Simulates ground rovers, boats and observing drones. A good result here does not show a machine works in the real world." } });
    },
    poll(id, after, wait, signal) {
      return new Promise((resolve, reject) => {
        if (signal && signal.aborted) { reject(abortErr()); return; }
        if (Date.now() < failUntil) { setTimeout(() => reject(new Error("mock network down")), 300); return; }
        const answer = () => {
          const evs = log.filter(e => e.seq > after).slice(0, 500);
          return { events: clone(evs), last: evs.length ? evs[evs.length - 1].seq : after, more: false };
        };
        if (log.some(e => e.seq > after) || wait <= 0) { setTimeout(() => resolve(answer()), 15); return; }
        let timer;
        const finish = () => { clearTimeout(timer); waiters.delete(wake); if (signal) signal.removeEventListener("abort", onAbort); };
        const wake = () => { finish(); setTimeout(() => (Date.now() < failUntil ? reject(new Error("mock network down")) : resolve(answer())), 10); };
        const onAbort = () => { finish(); reject(abortErr()); };
        waiters.add(wake);
        if (signal) signal.addEventListener("abort", onAbort);
        timer = setTimeout(() => { finish(); resolve({ events: [], last: after, more: false }); }, Math.min(wait, 25) * 1000);
      });
    },
    send(id, body) {
      if (Date.now() < failUntil) return Promise.reject(new Error("mock network down"));
      later(30, () => handle(body));
      return Promise.resolve({ turn_id: "t" + (tid + 1), accepted: true, intent: { kind: body.text ? "text" : "action", source: "mock", confidence: 1 }, seq });
    },
    rename() { return Promise.resolve({ ok: true }); },
    state() { return Promise.resolve({ id: "mock", title: SimChat.state.title, seq }); },
  });

  SimChat.mock = { fail(ms) { failUntil = Date.now() + (ms || 3000); Array.from(waiters).forEach(w => w()); }, log, speed, reset() { timers.forEach(clearTimeout); timers.clear(); } };
})();
