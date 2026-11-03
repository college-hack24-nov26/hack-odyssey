"use strict";
/* chat-sheet.js: the Plan, Tests and Parts panes of the right-hand dock (registered with SimChat.panes, see chat-shell.js), built from the cards the chat already holds.
   SimChat.sheet keeps its old API (open(tab), close(), isOpen()): "design" opens Plan, "tests" and "results" open Tests, "parts" opens Parts. The Tests pane holds the editable
   table and, below it, the results matrix; every edit is a structured action. */
(function () {
  const V = SimChat.views, h = V.h;
  const S = (SimChat.sheet = {});
  const secs = {};
  let raf = 0;

  /* ---------------------------------------------------------------- context from the cards the chat holds */
  const lastCard = type => { const st = SimChat.state || {}; return (st.order || []).map(id => st.cards[id]).filter(c => c && c.type === type).pop() || null; };
  const allCards = type => { const st = SimChat.state || {}; return (st.order || []).map(id => st.cards[id]).filter(c => c && c.type === type); };
  function context() {
    const st = SimChat.state || {};
    return { design: lastCard("design"), understanding: lastCard("understanding"), tests: lastCard("tests"), results: allCards("results"), parts: lastCard("parts"), watch: lastCard("watch"), limits: st.limits || {} };
  }
  const empty = (text) => h("div", { class: "ch-sh-empty" }, V.icon("circle-slash"), h("p", { text }));

  /* ---------------------------------------------------------------- tab: Design */
  const pick = c => (c.proposals || [])[c.pick || 0] || {};
  function designDefs() {
    return [
      { key: "none", deps: x => !x.design, render: x => x.design ? null : empty("No design yet. Describe a job in the chat and I will propose a machine.") },
      { key: "head", deps: x => x.design && [pick(x.design).label, x.design.pill, x.design.verdict, x.design.observation_note], render: x => !x.design ? null : [
        h("div", { class: "ch-v-dhead" }, h("b", { class: "ch-v-dname", text: pick(x.design).label || "Design" }), x.design.pill ? V.chip(x.design.pill, "ch-v-pill") : null),
        V.verdict(x.design.verdict), x.design.observation_note ? h("p", { class: "ch-v-band ch-v-band--info" }, V.icon("warn"), h("span", { text: x.design.observation_note })) : null] },
      { key: "snap", deps: x => x.design && [x.design.preview && x.design.preview.state, x.design.pick, pick(x.design).machine_sig], render: x => x.design ? V.snapshot(x.design, { large: true }) : null },
      { key: "detail", deps: x => x.design && [x.design.proposals, x.design.pick, x.design.scanned], render: x => x.design ? V.designDetail(x.design) : null },
      { key: "means", flash: false, deps: x => x.design && x.design.what_this_means, render: x => x.design ? h("p", { class: "ch-v-muted ch-v-note", text: x.design.what_this_means || "" }) : null },
    ];
  }

  /* ---------------------------------------------------------------- tab: Tests (the editable table) */
  let editing = null, adding = false, caps = null, capsTried = false;
  const toNums = text => String(text).split(/[\s,;]+/).map(parseFloat).filter(Number.isFinite);
  const barPct = c => Math.round(100 * c.pass.value);
  function rerenderTests() { if (secs.tests) { secs.tests.force("table", context()); secs.tests.force("add", context()); } }

  function editor(c) {
    const isSweep = c.kind === "sweep", s = c.sweep || {};
    const idp = "ch-ed-" + c.id;
    const levels = isSweep ? h("input", { type: "text", class: "ch-v-in ch-v-mono", id: idp + "-l", value: s.levels.map(v => V.num(v)).join(", "), inputmode: "decimal", autocomplete: "off" }) : null;
    const ask = isSweep ? h("input", { type: "number", class: "ch-v-in ch-v-mono", id: idp + "-a", value: s.ask === null || s.ask === undefined ? "" : s.ask, step: "any" }) : null;
    const tries = h("input", { type: "number", class: "ch-v-in ch-v-mono", id: idp + "-t", value: c.tries.n, min: 1, max: 20, step: 1 });
    const bar = h("input", { type: "number", class: "ch-v-in ch-v-mono", id: idp + "-b", value: barPct(c), min: 5, max: 100, step: 5 });
    const note = h("p", { class: "ch-v-muted ch-v-note", "aria-live": "polite" });
    const field = (label, input, hint) => h("div", { class: "ch-sh-field" }, h("label", { for: input.id, text: label }), input, hint ? h("span", { class: "ch-v-muted ch-v-note", text: hint }) : null);
    const save = btn => {
      const args = { case: c.id };
      if (isSweep) {
        const lv = toNums(levels.value);
        if (levels.value.trim() && !lv.length) { note.textContent = "Type the levels as numbers, like 0.2, 0.5, 0.9."; return; }
        if (lv.length && JSON.stringify(lv) !== JSON.stringify(s.levels)) args.levels = lv;
        const a = ask.value.trim() === "" ? null : parseFloat(ask.value);
        if (a !== null && Number.isFinite(a) && a !== s.ask) args.ask = a;
      }
      const t = parseInt(tries.value, 10);
      if (Number.isFinite(t) && t !== c.tries.n) args.tries = Math.max(1, Math.min(20, t));
      const b = parseFloat(bar.value);
      if (Number.isFinite(b) && Math.round(b) !== barPct(c)) args.bar = Math.max(5, Math.min(100, b)) / 100;
      if (Object.keys(args).length === 1) { note.textContent = "Nothing changed."; return; }
      editing = null;
      V.send({ type: "edit_test", args }, btn);
      rerenderTests();
    };
    return h("form", { class: "ch-sh-edit", "aria-label": "Edit " + c.title, onsubmit: e => { e.preventDefault(); save(e.submitter); } },
      isSweep ? field("Levels, mild to severe", levels, `Allowed ${V.num(s.range[0])} to ${V.num(s.range[1])}${s.unit && s.unit !== "grip" ? " " + s.unit : ""}. I keep them in order and clamp the ends.`) : null,
      isSweep ? field("Level it must handle (optional)", ask) : null,
      field("Tries per level", tries, "1 to 20"), field("Bar: share of tries that must succeed (%)", bar, c.pass.owner === "user" ? "You set this" : "Built-in bar; changing it is marked 'you set'"),
      note,
      V.row(V.btn("Save", { kind: "primary", onClick: (e, b) => save(b) }), V.btn("Cancel", { kind: "ghost", onClick: () => { editing = null; rerenderTests(); } })));
  }

  function testRow(c, tests) {
    const state = SimChat.testState ? SimChat.testState(c) : "not_run";
    const edited = (c.touched || []).filter(t => t !== "created");
    const watchBtn = V.btn("Watch", { kind: "ghost", icon: "play", disabled: !c.watchable, reason: c.watchable ? "Watch a similar run at the top level (a demonstration, not the exact try)" : (c.watch_reason || "Not watchable yet"),
      onClick: (e, b) => V.send({ type: "watch", args: { case: c.id, level: null } }, b) });
    return h("li", { class: "ch-sh-trow" + (c.stale ? " is-stale" : "") + (editing === c.id ? " is-editing" : ""), dataset: { case: c.id } },
      h("div", { class: "ch-sh-tmain" },
        h("div", { class: "ch-sh-ttitle" }, V.badge(state), h("b", { text: c.title }), V.srcBadge(c.source),
          c.pass.owner === "user" ? V.chip("You set " + barPct(c) + "%", "ch-v-chip--warn") : null,
          c.stale && c.last_run ? V.chip("edited after the last run") : edited.length ? V.chip("edited") : null),
        h("p", { class: "ch-v-muted ch-v-note", text: (c.why ? c.why + " " : "") + V.caseLine(c) }),
        h("div", { class: "ch-v-mcells" }, (c.cells || []).map(x => V.cell(x, { compact: false }))),
        h("p", { class: "ch-v-muted ch-v-note ch-v-mono" }, c.pass.owner === "user" ? "You set " + barPct(c) + "% of tries succeed" : barPct(c) + "% of tries succeed", " | ", V.plural(c.tries.n, "try", "tries"), " per level")),
      h("div", { class: "ch-sh-tbtns" }, V.btn("Run", { icon: "play", onClick: (e, b) => V.send({ type: "run_tests", args: { cases: [c.id] } }, b), reason: "Run this test only", title: "Run this test" }), watchBtn,
        V.btn(editing === c.id ? "Close editor" : "Edit", { kind: "ghost", icon: "sheet", onClick: () => { editing = editing === c.id ? null : c.id; rerenderTests(); } }),
        V.btn("Remove", { kind: "ghost", icon: "cross", aria: "Remove " + c.title, onClick: (e, b) => V.send({ type: "remove_test", args: { case: c.id } }, b) })),
      editing === c.id ? editor(c) : null);
  }

  function addForm(x) {
    const t = x.tests, u = x.understanding, platform = u && u.platform;
    if (!capsTried && platform) {
      capsTried = true;
      fetch("/api/chat/capabilities").then(r => r.ok ? r.json() : null).then(j => { caps = j && j.platforms && j.platforms[platform] || null; if (adding) rerenderTests(); }).catch(() => { });
    }
    const have = new Set((t.rows || []).filter(r => r.kind === "sweep").map(r => r.sweep.hazard)), haveF = new Set((t.rows || []).filter(r => r.kind === "fixed").map(r => r.fixed.fault).filter(Boolean));
    const hz = ((caps && caps.hazards) || []).filter(z => !have.has(z.id)), fl = ((caps && caps.faults) || []).filter(f => !haveF.has(f.id));
    const sel = h("select", { class: "ch-v-in", "aria-label": "Add a tough condition", id: "ch-add-sel" }, h("option", { value: "", text: "Choose a condition or a failure..." }),
      hz.length ? h("optgroup", { label: "Tough conditions" }, hz.map(z => h("option", { value: "h:" + z.id, text: z.title + (z.unit && z.unit !== "grip" ? " (" + z.unit + ")" : "") }))) : null,
      fl.length ? h("optgroup", { label: "Failures" }, fl.map(f => h("option", { value: "f:" + f.id, text: f.title }))) : null);
    const lv = h("input", { type: "text", class: "ch-v-in ch-v-mono", id: "ch-add-lv", placeholder: "Levels (optional), like 0.3, 0.6, 0.9", "aria-label": "Levels for the new test", autocomplete: "off" });
    const say = h("input", { type: "text", class: "ch-v-in", id: "ch-add-say", placeholder: "Describe it, e.g. add wind of 12 m/s", "aria-label": "Describe the test you want", autocomplete: "off" });
    const wmenu = (x.watch && x.watch.conditions) || [];
    const combo = {};
    const addBtn = V.btn("Add test", { kind: "primary", icon: "plus", onClick: (e, b) => {
      const v = sel.value;
      if (!v) return;
      const [k, id] = [v[0], v.slice(2)];
      const levels = toNums(lv.value);
      adding = false;
      V.send({ type: "add_test", args: k === "h" ? { hazard: id, ...(levels.length ? { levels } : {}) } : { fault: id } }, b);
      rerenderTests();
    } });
    const sayBtn = V.btn("Send", { kind: "ghost", icon: "send", onClick: () => { const t2 = say.value.trim(); if (t2) { adding = false; say.value = ""; SimChat.sendText(t2); rerenderTests(); } } });
    say.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); sayBtn.click(); } });
    const comboBox = wmenu.length > 1 ? h("details", { class: "ch-v-det" }, h("summary", { text: "Custom combination (several at once)" }), V.condForm(wmenu, combo, { idp: "combo" }),
      V.row(V.btn("Add the combination", { kind: "ghost", onClick: (e, b) => { if (Object.keys(combo).length < 2) return; adding = false; V.send({ type: "add_test", args: { conditions: { ...combo } } }, b); rerenderTests(); } }))) : null;
    return h("div", { class: "ch-sh-add", role: "group", "aria-label": "Add a test" },
      h("div", { class: "ch-sh-addrow" }, sel, lv, addBtn),
      h("div", { class: "ch-sh-addrow" }, say, sayBtn),
      comboBox, !caps ? h("p", { class: "ch-v-muted ch-v-note", text: "The full list of conditions is not loaded here; describe the test in words and I will read it." }) : null);
  }

  function testsDefs() {
    return [
      { key: "none", deps: x => !x.tests, render: x => x.tests ? null : empty("No tests yet. They appear once the draft is ready.") },
      { key: "bar", deps: x => x.tests && [x.tests.counts, x.tests.can_undo, x.tests.can_redo, x.tests.state], render: x => !x.tests ? null : h("div", { class: "ch-sh-tbar" },
        h("p", { class: "ch-sh-cost" }, h("b", { text: V.plural(x.tests.counts.tests, "test") }), `, ${V.plural(x.tests.counts.conditions, "condition")}, ${V.plural(x.tests.counts.tries, "try", "tries")} in all`),
        V.row(V.btn("Run all", { kind: "primary", icon: "play", disabled: !x.tests.counts.tests, onClick: (e, b) => V.send({ type: "run_tests", args: { cases: "all" } }, b) }),
          V.btn("Undo", { kind: "ghost", icon: "undo", disabled: !x.tests.can_undo, onClick: (e, b) => V.send({ type: "undo", args: {} }, b) }),
          V.btn("Redo", { kind: "ghost", icon: "redo", disabled: !x.tests.can_redo, onClick: (e, b) => V.send({ type: "redo", args: {} }, b) }))) },
      { key: "table", hold: true, flash: false, deps: x => x.tests && [x.tests.rows, editing, V.resSig()], render: x => !x.tests ? null : h("ul", { class: "ch-sh-tlist" }, V.withResults(x.tests.rows).filter(r => r.status !== "untestable").map(r => testRow(r, x.tests))) },
      { key: "addbar", flash: false, deps: x => [!!x.tests, adding], render: x => x.tests ? V.row(V.btn(adding ? "Close" : "Add test", { kind: "ghost", icon: adding ? "cross" : "plus", onClick: () => { adding = !adding; rerenderTests(); } })) : null },
      { key: "add", hold: true, flash: false, deps: x => [adding, !!caps, x.tests && x.tests.rows && x.tests.rows.length], render: x => adding && x.tests ? addForm(x) : null },
      { key: "nt", deps: x => x.tests && x.tests.untestable, render: x => x.tests ? V.hatched("Can't be tested here (" + (x.tests.untestable || []).length + ")", x.tests.untestable) : null },
    ];
  }

  /* ---------------------------------------------------------------- tab: Results */
  let runPick = null, sel = null;
  const rerenderResults = () => { if (secs.results) secs.results.force("matrix", context()); secs.results.force("detail", context()); };
  const curRun = x => x.results.find(r => r.id === runPick) || x.results[x.results.length - 1] || null;
  function cellDetail(x, r) {
    if (!sel || !r) return h("p", { class: "ch-v-muted ch-v-note", text: "Pick a cell in the matrix to see what happened, try by try." });
    const row = ((x.tests && x.tests.rows) || []).find(q => q.id === sel.case_id), mrow = (r.matrix.rows || []).find(q => q.case_id === sel.case_id);
    const cell = mrow && mrow.cells.find(q => q.key === sel.key);
    if (!cell) return null;
    const lr = row && row.last_run, lv = lr && (lr.per_level || []).find(l => (l.value === null && cell.value === null) || (l.value !== null && cell.value !== null && Math.abs(l.value - cell.value) < 1e-6));
    return h("div", { class: "ch-sh-cdet", "aria-live": "polite" }, h("h4", { class: "ch-v-h4", text: cell.text }),
      cell.state === "not_run" || cell.state === "queued" || cell.state === "running" ? h("p", { class: "ch-v-note", text: "This condition has not finished yet." }) : null,
      lv ? [h("p", { class: "ch-v-note ch-v-mono" }, `${cell.wins} of ${cell.n} tries succeeded`, lv.mean_time ? `, mean time ${V.num(lv.mean_time, 1)} s` : "", lv.timeouts ? `, ${lv.timeouts} ran out of time` : "",
          lv.crashes && Object.keys(lv.crashes).length ? ", ended: " + Object.entries(lv.crashes).map(([k, n]) => k + " x" + n).join(", ") : ""),
        (lv.per_seed || []).length ? h("ul", { class: "ch-v-lines" }, lv.per_seed.map(p => h("li", { class: "ch-v-mono", text: `try ${p.seed + 1}: ` + (p.success ? `reached the goal${p.t ? " in " + V.num(p.t, 1) + " s" : ""}` : p.crash ? "did not reach it (" + p.crash + ")" : p.timeout ? "did not reach it (ran out of time)" : "did not reach it") }))) : null] : null,
      h("p", { class: "ch-v-muted ch-v-note", text: "A simulated try with the simple built-in autopilot, not a real test." }));
  }
  function resultsDefs() {
    return [
      { key: "none", deps: x => !x.results.length, render: x => x.results.length ? null : [empty("No test results yet. Press Run all, or say \"run all\"."), V.hatched("What is NOT tested", ((x.tests && x.tests.untestable) || []).map(u => u.title + ": " + u.reason), { always: true })] },
      { key: "runsel", flash: false, deps: x => [x.results.map(r => [r.id, r.state]), runPick], render: x => x.results.length > 1 ? h("div", { class: "ch-v-seg ch-sh-runs", role: "radiogroup", "aria-label": "Which run" }, x.results.map(r => {
        const on = r === curRun(x), b = h("button", { type: "button", role: "radio", "aria-checked": String(on), class: "ch-v-seg-b" + (on ? " is-on" : "") }, h("span", { class: "ch-v-seg-t" }, h("b", { text: "Run " + r.id.replace(/\D/g, "") }), h("span", { class: "ch-v-muted", text: r.state })));
        b.addEventListener("click", () => { runPick = r.id; sel = null; refreshNow(); });
        return b;
      })) : null },
      { key: "head", deps: x => { const r = curRun(x); return r && [r.state, r.headline, r.weakest, r.bar_text, r.stale, r.progress]; }, render: x => {
        const r = curRun(x);
        if (!r) return null;
        return [r.state === "running" || r.state === "queued" ? V.resultsProgress(r) : h("p", { class: "ch-v-headline", text: r.headline || r.summary }), V.weakest(r.weakest),
          r.bar_text ? h("p", { class: "ch-v-note" }, h("b", { text: "Bar: " }), r.bar_text) : null,
          r.stale ? h("p", { class: "ch-v-band ch-v-band--stale" }, V.badge("stale"), h("span", { text: "The tests changed after this run." })) : null];
      } },
      { key: "matrix", flash: false, deps: x => { const r = curRun(x); return r && [r.matrix, sel]; }, render: x => { const r = curRun(x); return r ? [V.matrix(r.matrix, { selected: sel, onCell: (row, cell) => { sel = { case_id: row.case_id, key: cell.key }; rerenderResults(); } }), V.legend()] : null; } },
      { key: "detail", flash: false, deps: x => { const r = curRun(x); return r && [sel, r.matrix, x.tests && x.tests.rows && x.tests.rows.map(q => q.last_run && q.last_run.at)]; }, render: x => { const r = curRun(x); return r ? cellDetail(x, r) : null; } },
      { key: "nt", flash: false, deps: x => { const r = curRun(x); return r && [r.not_tested, r.limit_line, r.statement]; }, render: x => { const r = curRun(x); return r ? V.notTestedBlock(r) : null; } },
      { key: "links", flash: false, deps: x => { const r = curRun(x); return r && r.report; }, render: x => { const r = curRun(x); return r && r.report && r.report.url ? V.row(h("a", { class: "ch-v-btn ch-v-btn--ghost", href: r.report.url, target: "_blank", rel: "noopener" }, V.icon("link"), h("span", { class: "ch-v-btn-t", text: "Open full report" }))) : null; } },
    ];
  }

  /* ---------------------------------------------------------------- tab: Parts */
  function partsDefs() {
    return [
      { key: "none", deps: x => !x.parts, render: x => x.parts ? null : empty("No build sheet yet. It appears with the first design.") },
      { key: "doc", deps: x => x.parts && [x.parts.parts, x.parts.wiring, x.parts.steps, x.parts.safety, x.parts.caveat, x.parts.power_note, x.parts.firmware, x.parts.rover, x.parts.rover_link, x.parts.hardware_line, x.parts.to_buy], render: x => x.parts ? V.partsFull(x.parts) : null },
      { key: "foot", flash: false, deps: x => x.parts && [x.parts.parts, x.parts.export], render: x => x.parts ? V.partsActions(x.parts) : null },
    ];
  }

  /* ---------------------------------------------------------------- the panes: Plan (design), Tests (table + results), Parts */
  const foot = () => h("div", { class: "ch-sh-foot" });
  const mountSecs = (body, key, defs) => { secs[key] = V.sections(body, defs()); };
  function paintFoot(node) {
    const x = context();
    node.replaceChildren(V.limitLine((x.limits && x.limits.line) || "Simulation only, with a simple built-in autopilot."));
    node.classList.toggle("is-stale", !!(x.limits && x.limits.stale));
  }
  /* each pane keeps its own sections; refresh() redraws only what changed, and only while the pane is visible (the design picture can start a 3D render) */
  function pane(id, label, icon, order, build) {
    return { id, label, icon, order, mount(body) {
      body.classList.add("ch-v-body");
      const f = foot();
      const secsFor = build(body);
      body.appendChild(f);
      return { refresh() { const x = context(); secsFor.forEach(k => secs[k].update(x, true)); paintFoot(f); } };
    } };
  }
  const paneDefs = [
    pane("plan", "Plan", "list", 10, body => { mountSecs(body, "design", designDefs); return ["design"]; }),
    pane("tests", "Tests", "check", 30, body => {
      body.appendChild(h("h3", { class: "ch-rp-sub", text: "Tests" }));
      const t = h("div", { class: "ch-v-body" }); body.appendChild(t); secs.tests = V.sections(t, testsDefs());
      body.appendChild(h("h3", { class: "ch-rp-sub", id: "ch-rp-results", text: "Results" }));
      const r = h("div", { class: "ch-v-body" }); body.appendChild(r); secs.results = V.sections(r, resultsDefs());
      return ["tests", "results"];
    }),
    pane("parts", "Parts", "box", 40, body => { mountSecs(body, "parts", partsDefs); return ["parts"]; }),
  ];
  const TAB_PANE = { design: "plan", plan: "plan", tests: "tests", results: "tests", parts: "parts" };
  paneDefs.forEach(d => SimChat.panes.register(d));

  function refreshNow() { const id = SimChat.panes.shown(); if (id === "plan" || id === "tests" || id === "parts") SimChat.panes.refresh(id); }
  function schedule() {
    if (raf || !isOpen()) return;
    raf = requestAnimationFrame(() => { raf = 0; refreshNow(); });
  }
  function isOpen() { return SimChat.panes.isOpen() && ["plan", "tests", "parts"].includes(SimChat.panes.shown()); }

  /* ---------------------------------------------------------------- public API (kept from the slide-over sheet, so every "Open the test table" button still works) */
  S.isOpen = isOpen;
  S.open = function (tab) {
    const id = TAB_PANE[tab] || (tab ? "plan" : "plan");
    SimChat.panes.open(id);
    if (tab === "results") setTimeout(() => { const n = document.getElementById("ch-rp-results"); if (n && n.scrollIntoView) n.scrollIntoView({ block: "start", behavior: SimChat.util.reducedMotion() ? "auto" : "smooth" }); }, 60);
  };
  S.close = () => { if (isOpen()) SimChat.panes.close(); };
  SimChat.on("card:create", schedule);
  SimChat.on("card:patch", schedule);
  SimChat.on("limits", schedule);
  SimChat.on("reset", () => { editing = null; adding = false; runPick = null; sel = null; caps = null; capsTried = false; refreshNow(); });
})();
