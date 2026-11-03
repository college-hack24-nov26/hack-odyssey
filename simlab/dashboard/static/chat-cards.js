"use strict";
/* chat-cards.js: the ten card renderers (understanding, design, watch, tests, results, parts, improve, limits, answer, blocked).
   Each is registered with SimChat.registerCard; the core builds the shell (icon, title, status chip, one-line summary, chevron) and calls mount/update for the body.
   Card payloads are the server's (simlab/chat/cards.py): `state` is the card's own state word, everything else is plain JSON. */
(function () {
  const V = SimChat.views, h = V.h;
  const W = SimChat.words = { autopilot: "simple built-in autopilot" };

  /* ---------------------------------------------------------------- shared */
  function reg(type, def) {
    SimChat.registerCard(type, def);
  }
  /* mount helper: builds the keyed sections once, remembers them on the body for update() */
  function sec(body, card, defs) {
    body.classList.add("ch-v-body");
    body._secs = V.sections(body, defs);
    body._secs.update(card, false);
    return body._secs;
  }
  function upd(body, card) { if (body._secs) body._secs.update(card); }
  const mountedAll = (set, fn) => { for (const b of [...set]) { if (!b.isConnected) set.delete(b); else fn(b); } };
  const st = (state, label, extra) => Object.assign({ state, label }, extra || {});
  const cardOf = type => { const S = SimChat.state; return S && S.cards ? Object.values(S.cards).filter(c => c.type === type).pop() : null; };
  const platformIcon = p => "platform-" + (p === "water" || p === "air" ? p : "ground");
  const soc = b => (b === null || b === undefined ? null : typeof b === "number" ? b : b.soc !== undefined ? b.soc : null);
  const socText = b => (soc(b) === null ? "-" : V.num(100 * soc(b), 0) + " %");
  const hintSay = text => h("p", { class: "ch-v-muted ch-v-note ch-v-hint" }, V.icon("send"), h("span", null, "Or just say it: ", h("i", { text: text })));
  const actionsOf = (card, type) => (card.actions || []).find(a => a.type === type);

  /* ---------------------------------------------------------------- 1. understanding */
  const numUnit = n => (n.unit ? " " + n.unit : "");
  reg("understanding", {
    icon: c => platformIcon(c.platform), footer: false, defaultOpen: false,
    title: c => c.title || "What I understood",
    status: c => c.state === "ready" ? st("done", "Ready") : st("running", "Reading"),
    summary: c => c.summary || "",
    mount(body, card) {
      let editing = null;                         // the job number whose editor is open
      const rerender = () => body._secs.force("numbers", body._card);
      body._card = card;
      const clampNote = (n, raw) => raw < n.min ? `Limited to ${V.num(n.min)}${numUnit(n)}, the simulator's minimum.` : raw > n.max ? `Limited to ${V.num(n.max)}${numUnit(n)}, the simulator's maximum.` : "";
      const editor = n => {
        const isFlag = n.max - n.min === 1 && Number.isInteger(n.min);
        const box = h("div", { class: "ch-v-pop", role: "group", "aria-label": "Change " + n.label });
        if (isFlag) {
          const pick = v => { editing = null; V.send({ type: "set_job_number", args: { name: n.key, value: v } }, null, n.label + ": " + (v ? "wet" : "dry")); rerender(); };
          box.append(h("p", { class: "ch-v-pop-t", text: n.label }), h("div", { class: "ch-v-actions" },
            V.btn("Dry", { kind: n.value ? "" : "primary", onClick: () => pick(0) }), V.btn("Wet", { kind: n.value ? "primary" : "", onClick: () => pick(1) }),
            V.btn("Cancel", { kind: "ghost", onClick: () => { editing = null; rerender(); } })));
          return box;
        }
        const span = n.max - n.min, step = span >= 100 ? 1 : span >= 10 ? 0.5 : span >= 1 ? 0.05 : 0.01;
        const num = h("input", { type: "number", class: "ch-v-in ch-v-mono", id: "ch-num-" + n.key, value: n.value, min: n.min, max: n.max, step, inputmode: "decimal" });
        const rng = h("input", { type: "range", class: "ch-v-range-in", min: n.min, max: n.max, step, value: n.value, "aria-label": n.label + " slider" });
        const note = h("p", { class: "ch-v-muted ch-v-note", "aria-live": "polite" });
        const sync = from => { const v = parseFloat(from.value); if (Number.isFinite(v)) { if (from === num) rng.value = v; else num.value = v; note.textContent = clampNote(n, v); } };
        num.addEventListener("input", () => sync(num)); rng.addEventListener("input", () => sync(rng));
        const apply = () => {
          const raw = parseFloat(num.value);
          if (!Number.isFinite(raw)) { note.textContent = "Type a number first."; return; }
          const v = Math.min(n.max, Math.max(n.min, raw));
          editing = null;
          V.send({ type: "set_job_number", args: { name: n.key, value: v } }, null, "Set " + n.label.toLowerCase() + " to " + V.num(v) + numUnit(n));
          rerender();
        };
        num.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); apply(); } if (e.key === "Escape") { editing = null; rerender(); } });
        box.append(h("label", { class: "ch-v-pop-t", for: num.id, text: n.label + (n.unit ? " (" + n.unit + ")" : "") }), h("div", { class: "ch-v-pop-r" }, rng, num),
          h("p", { class: "ch-v-muted ch-v-note", text: `Allowed: ${V.num(n.min)} to ${V.num(n.max)}${numUnit(n)}` }), note,
          h("div", { class: "ch-v-actions" }, V.btn("Apply", { kind: "primary", onClick: apply }), V.btn("Cancel", { kind: "ghost", onClick: () => { editing = null; rerender(); } })));
        setTimeout(() => num.focus(), 0);
        return box;
      };
      sec(body, card, [
        { key: "job", deps: c => [c.job, c.badge, c.platform_label, c.observation_note], render: c => [
          h("p", { class: "ch-v-job" }, h("span", { class: "ch-v-plat" }, V.icon(platformIcon(c.platform)), h("span", { text: c.platform_label || "" })), h("q", { text: c.job || "" }),
            c.badge ? V.chip(c.badge.label, c.badge.source === "ai" ? "ch-v-chip--ai" : "") : null),
          c.observation_note ? h("p", { class: "ch-v-band ch-v-band--info" }, V.icon("warn"), h("span", { text: c.observation_note })) : null] },
        { key: "numbers", hold: true, deps: c => [c.numbers, editing], render: c => {
          const nums = c.numbers || [];
          if (!nums.length) return null;
          const cur = nums.find(n => n.key === editing);
          return [h("h4", { class: "ch-v-h4", text: "Numbers I used (tap to change)" }),
            h("div", { class: "ch-v-chips" }, nums.map(n => {
              const b = h("button", { type: "button", class: "ch-v-numchip" + (n.touched ? " is-yours" : "") + (n.stated ? " is-stated" : ""), "aria-expanded": String(editing === n.key), "aria-label": `${n.label}: ${V.num(n.value)}${numUnit(n)}. Change` },
                h("span", { class: "ch-v-numchip-l", text: n.label }), h("b", { class: "ch-v-mono", text: n.max - n.min === 1 && n.min === 0 ? (n.value ? "yes" : "no") : V.num(n.value) + numUnit(n) }),
                n.touched ? h("span", { class: "ch-v-tag", text: "yours" }) : null);
              b.addEventListener("click", () => { editing = editing === n.key ? null : n.key; rerender(); });
              return b;
            })), cur ? editor(cur) : null];
        } },
        { key: "req", deps: c => c.requirements, render: c => (c.requirements || []).length ? [h("h4", { class: "ch-v-h4", text: "What it has to do" }), h("ul", { class: "ch-v-lines" }, c.requirements.map(r => h("li", { text: r })))] : null },
        { key: "assume", deps: c => [c.assumptions, editing], render: c => (c.assumptions || []).length ? [h("h4", { class: "ch-v-h4", text: "What I assumed" }), h("div", { class: "ch-v-chips" }, c.assumptions.map(a => {
          const n = a.param && (c.numbers || []).find(x => x.key === a.param);
          if (!n) return h("span", { class: "ch-v-chip", text: a.text });
          const b = h("button", { type: "button", class: "ch-v-chip ch-v-chip--btn", "aria-label": a.text + ". Change" }, h("span", { text: a.text }), h("u", { text: "Change" }));
          b.addEventListener("click", () => { editing = n.key; rerender(); });
          return b;
        }))] : null },
        { key: "nt", deps: c => c.not_tested, render: c => V.hatched("NOT TESTED", c.not_tested) },
        { key: "foot", flash: false, deps: () => 0, render: () => V.row(V.btn("Open details", { kind: "ghost", onClick: () => V.openSheet("design") })) },
      ]);
    },
    update(body, card) { body._card = card; upd(body, card); },
  });

  /* ---------------------------------------------------------------- 2. design */
  const pickOf = c => (c.proposals || [])[c.pick || 0] || {};
  reg("design", {
    icon: () => { const u = cardOf("understanding"); return platformIcon(u && u.platform); }, footer: false, defaultOpen: true,
    title: c => c.title || "Design",
    status: c => {
      if (c.state === "unavailable") return st("not-tested", "No design");
      const l = (c.verdict || {}).light;
      return l === "green" ? st("met", "Fits on paper") : l === "red" ? st("below", "Does not fit yet") : st("warn", "Check these");
    },
    summary: c => c.state === "unavailable" ? (c.summary || "") : ((pickOf(c).label || "Design") + ": " + (c.headline || "")),
    mount(body, card) {
      let more = false;
      const rerender = () => body._secs.force("more", body._card);
      body._card = card;
      sec(body, card, [
        { key: "head", deps: c => [pickOf(c).label, c.pill, c.verdict, c.observation_note, c.state, c.summary], render: c => c.state === "unavailable" ? h("p", { class: "ch-v-band ch-v-band--warn" }, V.icon("circle-slash"), h("span", { text: c.summary || "No design found." })) : [
          h("div", { class: "ch-v-dhead" }, h("b", { class: "ch-v-dname", text: pickOf(c).label || "Design" }), c.pill ? V.chip(c.pill, "ch-v-pill") : null),
          V.verdict(c.verdict),
          c.observation_note ? h("p", { class: "ch-v-band ch-v-band--info" }, V.icon("warn"), h("span", { text: c.observation_note })) : null] },
        { key: "main", deps: c => [c.key_facts, c.preview && c.preview.state, c.preview && c.preview.reason, c.pick, pickOf(c).machine_sig, c.state], render: c => c.state === "unavailable" ? null : h("div", { class: "ch-v-dmain" }, V.keyFacts(c.key_facts), V.snapshot(c)) },
        { key: "means", deps: c => [c.what_this_means, c.note, c.meets_goal], render: c => c.state === "unavailable" ? null : [h("p", { class: "ch-v-muted ch-v-note" }, c.what_this_means || ""), c.note && c.meets_goal === false ? h("p", { class: "ch-v-note", text: c.note }) : null] },
        { key: "more", deps: c => [more, c.proposals, c.pick, c.scanned], render: c => c.state === "unavailable" ? null : [
          (() => {
            const b = h("button", { type: "button", class: "ch-v-disc", "aria-expanded": String(more) }, V.icon("chevron", "ch-v-disc-ic"), h("span", { text: more ? "Hide the checks and options" : "Show the checks and options" }));
            b.addEventListener("click", () => { more = !more; rerender(); });
            return b;
          })(), more ? V.designDetail(c) : null] },
        { key: "foot", flash: false, deps: c => c.state, render: c => c.state === "unavailable" ? null : V.row(V.btn("Open details", { kind: "ghost", onClick: () => V.openSheet("design") })) },
      ]);
    },
    update(body, card) { body._card = card; upd(body, card); },
  });

  /* ---------------------------------------------------------------- 3. watch it work */
  const TERMINAL = new Set(["finished", "stopped", "replaced", "error", "interrupted"]);
  const isRunning = r => !!r && (r.state === "running" || r.state === "starting");
  function resultWord(run) {
    const f = run && run.final;
    if (!f) return run && isRunning(run) ? "Running" : "-";
    return f.success ? "Reached the goal" : f.crash ? "Failed: " + f.crash : f.timeout ? "Time ran out" : "Ended";
  }
  reg("watch", {
    icon: "play", footer: false, defaultOpen: true,
    title: c => c.title || "Watch it work",
    status: c => {
      const r = c.current;
      if (c.state === "unavailable") return st("not-tested", "Needs MuJoCo");
      if (!r) return c.state === "idle" ? st("queued", "Waiting") : st("queued", "Ready");
      if (r.state === "starting") return st("running", "Starting");
      if (r.state === "running") return st("running", "Running");
      if (r.state === "finished") return r.final && r.final.success ? st("done", "Reached the goal") : st("stopped", resultWord(r));
      if (r.state === "replaced") return st("stopped", "Replaced");
      if (r.state === "stopped") return st("stopped", "Stopped");
      if (r.state === "interrupted") return st("interrupted", "Interrupted");
      return st("error", "Error");
    },
    summary: c => c.current ? `${W.autopilot[0].toUpperCase() + W.autopilot.slice(1)}, ${c.current.conditions_text || "calm conditions"}. A demonstration, not a test.` : (c.summary || ""),
    mount(body, card) {
      body._card = card;
      const live = SimChat.live;
      const cond = live && live.cond ? live.cond : {};   // knob -> value for the sliders that are switched on (shared with the Focus rail)
      let condRun = null;
      const slot = h("div", { class: "ch-lv-slot", "aria-label": "Simulation view", role: "img" });
      const poster = h("img", { class: "ch-lv-poster", alt: "The last picture of this run", hidden: true });
      const over = h("div", { class: "ch-lv-over", hidden: true, role: "status" });
      const awayText = h("span", { text: "Showing live in the bar below" });
      const away = h("div", { class: "ch-lv-away", hidden: true }, V.icon("play"), awayText);
      const pip = h("div", { class: "ch-lv-pipwrap" });
      const hint = h("span", { class: "ch-lv-hint", hidden: true, text: "Click to orbit" });
      const view = h("div", { class: "ch-lv-view" }, slot, poster, over, away, pip, hint);
      const numEls = {};
      const nums = h("div", { class: "ch-lv-nums", role: "group", "aria-label": "Live numbers" }, [["time", "Time"], ["dist", "Distance to goal"], ["bat", "Battery"], ["res", "Result"]].map(([k, l]) => {
        const b = h("b", { class: "ch-v-mono", text: "-" }); numEls[k] = b;
        return h("div", { class: "ch-lv-num" }, b, h("span", { text: l }));
      }));
      const setNums = o => { for (const k of ["time", "dist", "bat", "res"]) numEls[k].textContent = o[k] === undefined || o[k] === null ? "-" : o[k]; };
      const fromRun = r => {
        const f = r && r.final || {};
        return { time: f.t !== undefined && f.t !== null ? V.num(f.t, 1) + " s" : "-", dist: "-", bat: socText(f.battery), res: resultWord(r) };
      };
      body.append(view, nums);
      body.classList.add("ch-v-body");
      let freezeNote = "";
      const showPoster = (url, why) => { poster.hidden = !url; if (url) poster.src = url; freezeNote = why || freezeNote; };
      const refreshOverlay = card2 => {
        const r = card2.current;
        let msg = null, icon = "play";
        if (card2.state === "unavailable" || card2.mujoco === false) { msg = card2.summary || "Watching needs MuJoCo, which is not installed here."; icon = "circle-slash"; }
        else if (!r) { msg = card2.state === "idle" ? "Waiting for the live view to be free." : "Not started yet."; icon = "clock"; }
        else if (r.state === "starting") { msg = "Starting the simulation"; icon = "clock"; }
        else if (r.state === "replaced") { msg = "Replaced by a newer run"; icon = "return"; }
        else if (r.state === "interrupted") { msg = "Interrupted by a restart"; icon = "return"; }
        else if (r.state === "error") { msg = r.note || "The run could not start."; icon = "warn"; }
        else if (r.state === "stopped") { msg = "Stopped" + (r.final && r.final.t ? " at " + V.num(r.final.t, 1) + " s" : ""); icon = "stop"; }
        else if (r.state === "finished") { msg = resultWord(r); icon = r.final && r.final.success ? "check" : "cross"; }
        const showMsg = msg && (!r || !isRunning(r)) && (poster.hidden || r.state === "replaced" || r.state === "interrupted");
        over.hidden = !showMsg;
        over.replaceChildren();
        if (showMsg) over.append(V.icon(icon), h("span", { text: msg }));
        view.classList.toggle("is-frozen", !!r && TERMINAL.has(r.state));
        view.classList.toggle("is-empty", !r || card2.state === "unavailable");
      };
      const sync = card2 => {
        const r = card2.current, S = SimChat.state || {};
        const newest = (S.order || []).filter(id => S.cards[id] && S.cards[id].type === "watch").pop();
        const mine = !newest || newest === card2.id;
        if (!live) return;
        if (r && isRunning(r) && mine && card2.mujoco !== false && !r.mock) {
          poster.hidden = true; over.hidden = true;
          live.attach(card2.id, slot, { runId: r.id, sceneId: r.scene_id || null, conditions: r.conditions || {} });
        } else {
          if (live.owner && live.owner() === card2.id) live.freeze(card2.id, { runId: r && r.id, why: r && r.state });
          const url = (r && r.poster) || (r && live.poster && live.poster(r.id)) || (!mine && card2.current && live.poster(card2.current.id)) || null;
          showPoster(url && /^(data:image\/|\/api\/chat\/)/.test(url) ? url : null);
          setNums(fromRun(r));
        }
        refreshOverlay(card2);
      };
      /* live numbers, 4 times a second, written straight into the four spans */
      const onStats = s => {
        if (!body.isConnected || !live || live.owner() !== body._card.id) return;
        const hasRes = s.result;
        setNums({ time: V.num(s.t, 1) + " s", dist: s.dist !== undefined && s.dist !== null ? V.num(s.dist, 1) + " m" : "-", bat: socText(s.battery),
          res: hasRes ? (s.result.success ? "Reached the goal" : s.result.crash ? "Failed: " + s.result.crash : s.result.timeout ? "Time ran out" : "Ended") : "Running" });
      };
      if (live) {
        live.on("stats", onStats);
        live.on("poster", e => { if (body.isConnected && body._card.current && e.runId === body._card.current.id) { showPoster(e.url); refreshOverlay(body._card); } });
        live.on("thumb", e => { if (e.cardId === body._card.id) { away.hidden = !e.away; slot.classList.toggle("is-away", !!e.away); awayText.textContent = e.where === "pane" ? "Showing live in the Simulation pane" : e.where === "focus" ? "Showing full screen" : "Showing live in the bar below"; } });
        live.on("interactive", e => { if (e.cardId === body._card.id) hint.hidden = !(e.available && e.where !== "pane"); });
        live.on("replaced", e => { if (e.cardId === body._card.id) refreshOverlay(body._card); });
      }
      view.addEventListener("click", () => { if (live && live.owner() === body._card.id) live.setOrbit(true); });
      const rerenderCond = () => body._secs.force("cond", body._card);
      const runWith = (b) => {
        const conditions = {};
        for (const [k, v] of Object.entries(cond)) if (v !== null && v !== undefined) conditions[k] = v;
        V.send({ type: "watch", args: Object.keys(conditions).length ? { conditions } : {} }, b);
      };
      body._runWith = runWith;
      sec(body, card, [
        { key: "caption", deps: c => [c.current && c.current.label, c.current && c.current.similar, c.demo_note, c.similar_note], render: c => {
          const r = c.current;
          return h("p", { class: "ch-lv-cap" },
            h("b", { text: (r ? W.autopilot[0].toUpperCase() + W.autopilot.slice(1) + ", " + (r.conditions_text || "calm conditions") + ", try " + (r.n ? 1 : 1) + ". " : "") }),
            c.demo_note || "This is a demonstration, not a test.", r && r.similar ? h("span", { class: "ch-v-muted", text: " " + (c.similar_note || "") }) : null);
        } },
        { key: "controls", flash: false, deps: c => [c.current && c.current.state, c.mujoco, c.actions], render: c => {
          const r = c.current, running = isRunning(r), off = c.mujoco === false;
          const cam = h("button", { type: "button", class: "ch-v-btn", "aria-pressed": "false", disabled: !running || undefined }, V.icon("play"), h("span", { class: "ch-v-btn-t", text: "Camera" }));
          cam.addEventListener("click", () => { const on = cam.getAttribute("aria-pressed") !== "true"; cam.setAttribute("aria-pressed", String(on)); live && live.setCamera(on, pip); });
          return V.row(
            V.btn(r ? "Run again" : "Run", { kind: "primary", icon: "refresh", disabled: off || running && false, reason: off ? "Needs MuJoCo" : null, onClick: (e, b) => runWith(b) }),
            running ? V.btn("Stop", { icon: "stop", onClick: (e, b) => V.send({ type: "stop", args: { job: "live" } }, b) }) : null,
            V.btn("Focus", { icon: "expand", disabled: !running, reason: running ? null : "Start a run to focus on it", onClick: () => live && live.focus() }),
            cam);
        } },
        { key: "cond", hold: true, deps: c => [c.conditions, c.current && c.current.id], render: c => {
          const conds = c.conditions || [];
          if (!conds.length) return null;
          if (condRun !== (c.current && c.current.id)) {
            condRun = c.current && c.current.id;
            for (const k of Object.keys(cond)) delete cond[k];
            if (c.current && c.current.conditions) for (const k of Object.keys(c.current.conditions)) cond[k] = c.current.conditions[k];
          }
          const det = h("details", { class: "ch-v-det", open: Object.keys(cond).length ? true : null }, h("summary", null, h("span", { text: "Tough conditions" }), h("span", { class: "ch-v-muted", text: " (change, then Run again)" })));
          det.append(V.condForm(conds, cond, { idp: "card" }));
          det.append(h("div", { class: "ch-v-actions" }, V.btn("Back to calm", { kind: "ghost", onClick: () => { for (const k of Object.keys(cond)) delete cond[k]; rerenderCond(); } })));
          return det;
        } },
        { key: "runs", deps: c => c.runs, render: c => (c.runs || []).length > 1 || (c.runs || []).some(r => TERMINAL.has(r.state)) ? [
          h("h4", { class: "ch-v-h4", text: "Runs" }),
          h("ul", { class: "ch-v-runs" }, (c.runs || []).slice().reverse().map(r => h("li", null,
            h("span", { class: "ch-v-mono", text: "Run " + r.n }), h("span", { class: "ch-v-runc", text: r.conditions_text || "calm" }),
            V.badge(r.state === "finished" ? (r.final && r.final.success ? "met" : "below") : r.state === "running" || r.state === "starting" ? "running" : r.state === "replaced" ? "replaced" : r.state === "error" ? "error" : r.state === "interrupted" ? "interrupted" : "stopped",
              r.state === "replaced" ? "Replaced by a newer run" : r.state === "finished" ? resultWord(r) : null),
            r.final && r.final.t ? h("span", { class: "ch-v-muted ch-v-mono", text: V.num(r.final.t, 1) + " s" }) : null)))] : null },
      ]);
      sync(card);
      body._sync = sync;
    },
    update(body, card) {
      body._card = card;
      upd(body, card);
      if (body._sync) body._sync(card);
    },
  });

  /* ---------------------------------------------------------------- 4. tests */
  const verdictState = (c) => {
    const lr = c.last_run;
    if (c.status === "untestable") return "not_tested";
    if (c.status === "running") return "running";
    if (!lr && c._verdict) return c.stale ? "stale" : c._verdict === "met" ? "met" : c._verdict === "below" ? "below" : "stopped";
    if (!lr) return "not_run";
    if (c.stale) return "stale";
    return lr.verdict === "met" ? "met" : lr.verdict === "below" ? "below" : lr.verdict === "partial" ? "stopped" : "not_run";
  };
  SimChat.testState = verdictState;
  reg("tests", {
    icon: "check", footer: false, defaultOpen: false,
    title: c => c.title || "Tests",
    status: c => c.state === "running" ? st("running", "Running") : c.state === "stale" ? st("stale", "Stale") : c.state === "empty" ? st("queued", "No tests") : st("done", "Ready"),
    summary: c => c.summary || "",
    mount(body, card) {
      body._card = card;
      SimChat.on("card:patch", c => { if (c.type === "results" && body.isConnected && body._card) upd(body, body._card); });
      SimChat.on("card:create", c => { if (c.type === "results" && body.isConnected && body._card) upd(body, body._card); });
      sec(body, card, [
        { key: "counts", deps: c => [c.counts, c.rows && c.rows.map(r => [r.status, r.stale]), V.resSig()], render: c => {
          const k = V.counts(V.withResults(c.rows)), nt = k.nt;
          return [h("div", { class: "ch-v-chips" }, [
            k.ran ? V.badge("done", k.ran + " ran") : null, k.ready ? V.badge("not_run", k.ready + " not run yet") : null, k.stale ? V.badge("stale", k.stale + " stale") : null,
            k.running ? V.badge("running", k.running + " running") : null, nt ? V.badge("not_tested", nt + " NOT TESTED") : null]),
            h("p", { class: "ch-v-muted ch-v-note", text: c.cost_line || "" })];
        } },
        { key: "top", deps: c => [c.top, c.rows && c.rows.map(r => [r.id, r.status, r.stale, r.last_run && r.last_run.verdict]), V.resSig()], render: c => (c.top || []).length ? h("ul", { class: "ch-v-toplist" }, c.top.map(t => {
          const row = V.withResults(c.rows).find(r => r.id === t.id);
          return h("li", null, h("span", { class: "ch-v-toplist-t" }, h("b", { text: t.title }), h("span", { class: "ch-v-muted", text: t.line })), row ? V.badge(verdictState(row)) : null);
        })) : null },
        { key: "bar", deps: c => [c.bar_text, c.threshold_owner], render: c => c.bar_text ? h("p", { class: "ch-v-note" }, h("b", { text: "Bar: " }), c.bar_text.replace(/^.*?(\d+% of tries succeed).*$/, "$1") !== c.bar_text ? c.bar_text : c.bar_text, c.threshold_owner === "user" ? V.chip("you set this") : null) : null },
        { key: "nt", deps: c => c.untestable, render: c => V.hatched("Can't be tested here (" + (c.untestable || []).length + ")", c.untestable) },
        { key: "foot", flash: false, deps: c => [c.actions, c.can_undo, c.counts && c.counts.tests], render: c => [
          V.row(
            (() => { const a = actionsOf(c, "run_tests") || { type: "run_tests", label: "Run all", args: { cases: "all" }, primary: true }; return V.actionBtn(a, { label: "Run all", icon: "play" }); })(),
            V.btn("Open the test table", { kind: "ghost", icon: "sheet", onClick: () => V.openSheet("tests") }),
            V.btn("Undo", { kind: "ghost", icon: "undo", disabled: !c.can_undo, reason: c.can_undo ? null : "Nothing to undo", onClick: (e, b) => V.send({ type: "undo", args: {} }, b) })),
          hintSay("add wind of 12 m/s as a test")] },
      ]);
    },
    update(body, card) { body._card = card; upd(body, card); },
  });

  /* ---------------------------------------------------------------- 5. results */
  reg("results", {
    icon: "history", footer: false, defaultOpen: true,
    title: c => c.title || "Results",
    status: c => ({ queued: st("queued", "Queued"), running: st("running", c.progress && c.progress.stopping ? "Stopping" : "Running"), done: st("done", "Finished"), stopped: st("stopped", "Stopped"),
      error: st("error", "Error"), interrupted: st("interrupted", "Interrupted"), cancelled: st("stopped", "Withdrawn") })[c.state] || st("done", c.state || ""),
    summary: c => {
      if (c.state === "running") { const p = c.progress || {}; return `Condition ${Math.min((p.done || 0) + 1, p.total || 1)} of ${p.total || "?"}, ${p.tries_done || 0} of ${p.tries_total || "?"} tries`; }
      if (c.state === "queued") return "Waiting for the tests already running";
      return c.headline || c.summary || "";
    },
    mount(body, card) {
      body._card = card;
      sec(body, card, [
        { key: "prog", flash: false, deps: c => [c.state, c.progress, c.matrix && c.matrix.rows.map(r => r.cells.map(x => x.state === "running"))], render: c => c.state === "running" || c.state === "queued" ? [
          V.resultsProgress(c),
          c.state === "running" ? V.row(V.btn(c.progress && c.progress.stopping ? "Stopping after the current condition" : "Stop", { icon: "stop", disabled: !!(c.progress && c.progress.stopping), onClick: (e, b) => V.send({ type: "stop", args: { job: "tests" } }, b) })) : V.row(V.btn("Stop waiting", { icon: "stop", onClick: (e, b) => V.send({ type: "stop", args: { job: "tests" } }, b) }))] : null },
        { key: "head", deps: c => [c.state, c.headline, c.weakest, c.bar_text, c.stale, c.what_this_means], render: c => c.state === "running" || c.state === "queued" ? null : [
          c.state === "error" ? h("p", { class: "ch-v-band ch-v-band--bad" }, V.icon("warn"), h("span", { text: c.headline || c.summary || "The tests could not run." })) : h("p", { class: "ch-v-headline", text: c.headline || c.summary }),
          c.state === "interrupted" ? h("p", { class: "ch-v-band ch-v-band--warn" }, V.icon("return"), h("span", { text: "The server restarted while this was running. Finished conditions were kept." })) : null,
          V.weakest(c.weakest),
          c.bar_text ? h("p", { class: "ch-v-note" }, h("b", { text: "Bar: " }), c.bar_text) : null,
          c.stale ? h("p", { class: "ch-v-band ch-v-band--stale" }, V.badge("stale"), h("span", { text: "The tests changed after this run, so these numbers may no longer apply." })) : null,
          c.what_this_means ? h("p", { class: "ch-v-muted ch-v-note", text: c.what_this_means }) : null] },
        { key: "matrix", deps: c => c.matrix, render: c => V.matrix(c.matrix, { compact: true }) },
        { key: "legend", flash: false, deps: () => 0, render: () => V.legend() },
        { key: "nt", flash: false, deps: c => [c.not_tested, c.limit_line, c.statement], render: c => V.notTestedBlock(c) },
        { key: "foot", flash: false, deps: c => [c.state, c.actions, c.chips, c.report, c.weakest, c.case_ids], render: c => {
          if (c.state === "running" || c.state === "queued") return null;
          const out = [], seen = new Set();
          const push = (a, o) => { if (!a || seen.has(a.type + (a.label || ""))) return; seen.add(a.type + (a.label || "")); out.push(V.actionBtn(a, o)); };
          if (c.state === "error" || c.state === "interrupted") out.push(V.btn("Retry", { kind: "primary", icon: "refresh", onClick: (e, b) => V.send({ type: "run_tests", args: { cases: c.case_ids || "all" } }, b) }));
          const w = (c.actions || []).find(a => a.type === "watch");
          if (w && c.weakest) push(Object.assign({}, w, { label: "Watch the weakest test", primary: true }), { icon: "play" });
          for (const ch of c.chips || []) push(Object.assign({ enabled: ch.enabled, reason: ch.reason }, ch.action, { label: ch.label }));
          push((c.actions || []).find(a => a.type === "sheet"), {});
          return [V.row(out, V.btn("Open the full matrix", { kind: "ghost", icon: "sheet", onClick: () => V.openSheet("results") }),
            c.report && c.report.url ? h("a", { class: "ch-v-btn ch-v-btn--ghost", href: c.report.url, target: "_blank", rel: "noopener" }, V.icon("link"), h("span", { class: "ch-v-btn-t", text: "Open full report" })) : null)];
        } },
      ]);
    },
    update(body, card) { body._card = card; upd(body, card); },
  });

  /* ---------------------------------------------------------------- 6. parts and build sheet */
  const partsBlock = (c, o = {}) => {
    const rows = c.parts || [];
    return [
      h("p", { class: "ch-v-note" }, h("b", { text: V.plural(rows.length, "part") }), ` - ${(c.to_buy || []).length} to buy, ${rows.length - (c.to_buy || []).length} you have`),
      V.partsTable(o.full ? rows : rows.slice(0, 6)),
      !o.full && rows.length > 6 ? h("p", { class: "ch-v-muted ch-v-note", text: `and ${rows.length - 6} more in the full build sheet` }) : null,
      c.power_note ? h("p", { class: "ch-v-note" }, h("b", { text: "Power: " }), c.power_note) : null,
      c.firmware ? h("p", { class: "ch-v-note" }, h("b", { text: "Firmware: " }), h("code", { class: "ch-v-mono", text: c.firmware })) : null];
  };
  V.partsFull = function (c) {
    const rover = c.rover;
    const link = (rover && rover.enabled) || c.rover_link === true
      ? h("a", { class: "ch-v-btn ch-v-btn--ghost ch-v-roverlink", href: (rover && rover.href) || "/#hw" }, V.icon("link"), h("span", { class: "ch-v-btn-t", text: "Drive the real rover (leaves this chat)" }))
      : rover ? h("p", { class: "ch-v-muted ch-v-note ch-v-roverlink is-off" }, V.icon("lock"), h("span", null, h("b", { text: "Drive the real rover (leaves this chat): " }), rover.reason || "only for the ESP32 ground rover")) : null;
    return h("div", { class: "ch-v-sheetdoc" },
      partsBlock(c, { full: true }),
      h("h4", { class: "ch-v-h4", text: "Wiring" }), V.wiring(c.wiring),
      (c.steps || []).length ? [h("h4", { class: "ch-v-h4", text: "Steps" }), h("ol", { class: "ch-v-lines ch-v-steps" }, c.steps.map(s => h("li", { text: s })))] : null,
      (c.safety || []).length ? [h("h4", { class: "ch-v-h4", text: "Before you build or power it up" }), h("ul", { class: "ch-v-lines" }, c.safety.map(s => h("li", { text: s })))] : null,
      c.caveat ? h("p", { class: "ch-v-band ch-v-band--info" }, V.icon("warn"), h("span", { text: c.caveat })) : null,
      h("p", { class: "ch-v-muted ch-v-note", text: c.hardware_line || "Driving a real machine happens on the Hardware screen, with its checklist and STOP." }),
      link);
  };
  function copyParts(btn, c) {
    V.copy(V.partsText(c.parts)).then(() => { const t = btn.querySelector(".ch-v-btn-t"); if (t) { const o = t.textContent; t.textContent = "Copied"; setTimeout(() => { t.textContent = o; }, 1400); } }, () => { });
  }
  V.partsActions = function (c) {
    return V.row(
      V.btn("Export HTML", { icon: "sheet", onClick: (e, b) => V.send({ type: "sheet", args: { format: "html" } }, b) }),
      V.btn("Copy parts list", { icon: "link", kind: "ghost", onClick: (e, b) => copyParts(b, c) }),
      c.export && c.export.url ? h("a", { class: "ch-v-btn ch-v-btn--ghost", href: c.export.url, target: "_blank", rel: "noopener" }, V.icon("link"), h("span", { class: "ch-v-btn-t", text: "Open the exported sheet" })) : null);
  };
  reg("parts", {
    icon: "link", footer: false, defaultOpen: false,
    title: c => c.title || "Parts & build sheet",
    status: c => st("done", "Ready"),
    summary: c => c.summary || "",
    mount(body, card) {
      body._card = card;
      sec(body, card, [
        { key: "main", deps: c => [c.parts, c.to_buy, c.power_note, c.firmware], render: c => partsBlock(c) },
        { key: "wiring", deps: c => c.wiring, render: c => c.wiring && c.wiring.generated ? h("details", { class: "ch-v-det" }, h("summary", { text: "Wiring table" }), V.wiring(c.wiring)) : V.wiring(c.wiring) },
        { key: "steps", deps: c => [c.steps, c.safety, c.caveat], render: c => [
          (c.steps || []).length ? h("details", { class: "ch-v-det" }, h("summary", { text: "Steps (" + c.steps.length + ")" }), h("ol", { class: "ch-v-lines ch-v-steps" }, c.steps.map(s => h("li", { text: s })))) : null,
          (c.safety || []).length ? h("details", { class: "ch-v-det" }, h("summary", { text: "Before you build or power it up" }), h("ul", { class: "ch-v-lines" }, c.safety.map(s => h("li", { text: s })))) : null,
          c.caveat ? h("p", { class: "ch-v-muted ch-v-note", text: c.caveat }) : null] },
        { key: "hw", deps: c => [c.rover, c.rover_link, c.hardware_line], render: c => {
          const on = (c.rover && c.rover.enabled) || c.rover_link === true;
          return [h("p", { class: "ch-v-muted ch-v-note", text: c.hardware_line || "Driving a real machine happens on the Hardware screen, with its checklist and STOP." }),
            on ? h("a", { class: "ch-v-btn ch-v-btn--ghost ch-v-roverlink", href: (c.rover && c.rover.href) || "/#hw" }, V.icon("link"), h("span", { class: "ch-v-btn-t", text: "Drive the real rover (leaves this chat)" }))
              : c.rover ? h("p", { class: "ch-v-muted ch-v-note ch-v-roverlink is-off" }, V.icon("lock"), h("span", { text: "Drive the real rover is off: " + (c.rover.reason || "only for the ESP32 ground rover") })) : null];
        } },
        { key: "foot", flash: false, deps: c => [c.parts, c.export], render: c => [V.partsActions(c), V.row(V.btn("Open the build sheet", { kind: "ghost", icon: "sheet", onClick: () => V.openSheet("parts") }))] },
      ]);
    },
    update(body, card) { body._card = card; upd(body, card); },
  });

  /* ---------------------------------------------------------------- 7. improve with AI */
  const NOTICE_KEY = "ch-ai-notice";
  const noticeSeen = () => { try { return localStorage.getItem(NOTICE_KEY) === "1"; } catch (e) { return false; } };
  const noticeSet = () => { try { localStorage.setItem(NOTICE_KEY, "1"); } catch (e) { /* fine: asked again next time */ } };
  const improveBodies = new Set();
  const AIWORD = { idle: ["queued", "Ready"], queued: ["queued", "Waiting"], running: ["running", "Planning"], done: ["done", "Ready to review"], applied: ["done", "In use"], kept: ["done", "Kept yours"],
    failed: ["error", "Failed"], unavailable: ["not-tested", "Unavailable"], stopped: ["stopped", "Stopped"], unsupported: ["not-tested", "Not supported"], interrupted: ["interrupted", "Interrupted"] };
  function diffTable(d) {
    const by = {};
    for (const c of d.changes || []) (by[c.area] = by[c.area] || []).push(c);
    const AREA = { understanding: "How the job is described", job: "Job numbers", tests: "Tests", design: "The machine", assumptions: "Assumptions" };
    return h("div", { class: "ch-v-diff" },
      h("p", { class: "ch-v-note" }, h("b", { text: V.plural(d.count, "difference") }), d.yours ? ` (${d.yours} where you changed it yourself, marked "yours")` : ""),
      Object.keys(AREA).filter(a => by[a]).map(a => h("div", { class: "ch-v-table-wrap" }, h("table", { class: "ch-v-table ch-v-difft" },
        h("caption", { text: AREA[a] }),
        h("thead", null, h("tr", null, ["What", "Your draft", "AI version"].map(t => h("th", { scope: "col", text: t })))),
        h("tbody", null, by[a].map(c => h("tr", { class: "ch-v-diff--" + c.kind },
          h("th", { scope: "row" }, h("span", { text: c.label }), c.yours ? h("span", { class: "ch-v-tag", text: "yours" }) : null),
          h("td", { text: c.draft }), h("td", { text: c.ai }))))))));
  }
  function costText(b) {
    const lim = b && b.limit ? b.limit : 50;
    return `about 2-3 min, uses 1-3 of about ${lim} daily requests` + (b && b.left !== null && b.left !== undefined ? `, about ${b.left} left` : "");
  }
  reg("improve", {
    icon: "ai", footer: false, defaultOpen: true,
    title: c => c.title || "Improve with AI",
    status: c => { const w = AIWORD[c.state || "idle"] || ["done", c.state || ""]; return st(w[0], w[1]); },
    summary: c => c.summary || (c.state === "idle" ? "Ask a free AI model for a second plan" : ""),
    mount(body, card) {
      body._card = card;
      improveBodies.add(body);
      sec(body, card, [
        { key: "idle", flash: false, deps: c => [c.state, SimChat.state && SimChat.state.budget], render: c => {
          if ((c.state || "idle") !== "idle") return null;
          const b = (SimChat.state && SimChat.state.budget) || {};
          return [noticeSeen() ? null : h("p", { class: "ch-v-band ch-v-band--info" }, V.icon("warn"), h("span", { text: "Free models may keep what you send." })),
            h("p", { class: "ch-v-note", text: "When you describe a new job and an AI key is saved, a free AI model makes a second plan (" + costText(b) + "). Your quick draft stays usable; the AI's plan comes back as a comparison and you choose what to keep." }),
            b.configured === false ? h("p", { class: "ch-v-muted ch-v-note", text: "AI is off: no key saved. Everything else still works." }) : b.exhausted ? h("p", { class: "ch-v-muted ch-v-note", text: b.reset || "Resets at 00:00 UTC" }) : null];
        } },
        { key: "run", flash: false, deps: c => [c.state, c.phase, c.seconds, c.chars, c.tail, c.draft_note], render: c => c.state === "running" || c.state === "queued" ? [
          (c.state === "running" ? noticeSet() : null),
          h("div", { class: "ch-v-bar is-indet", role: "progressbar", "aria-label": "The AI is planning (no percentage: its time is unknown)" }, h("i")),
          h("p", { class: "ch-v-prog-l" }, h("b", { text: c.state === "queued" ? "Waiting for another AI plan to finish" : (c.phase || "Planning") }), h("span", { class: "ch-v-muted ch-v-mono", text: " " + V.secs(c.seconds) + (c.chars ? ", " + c.chars + " characters written" : "") })),
          c.tail ? h("pre", { class: "ch-v-tail ch-v-mono", "aria-hidden": "true", text: c.tail }) : null,
          h("p", { class: "ch-v-muted ch-v-note", text: c.draft_note || "Your draft stays usable meanwhile." }),
          V.row(V.btn("Stop", { icon: "stop", onClick: (e, b) => V.send({ type: "stop", args: { job: "plan" } }, b) }))] : null },
        { key: "done", deps: c => [c.state, c.diff, c.actions], render: c => c.state === "done" && c.diff ? [diffTable(c.diff), V.row((c.actions || []).map(a => V.actionBtn(a)))] : (c.state === "applied" || c.state === "kept") ? [
          h("p", { class: "ch-v-note", text: c.state === "applied" ? "You are using the AI's plan." : "You kept your draft." }), c.diff ? h("details", { class: "ch-v-det" }, h("summary", { text: "What was different" }), diffTable(c.diff)) : null] : null },
        { key: "fail", deps: c => [c.state, c.reason, c.closest, c.requests_note], render: c => ["failed", "unavailable", "unsupported", "stopped", "interrupted"].includes(c.state) ? [
          h("p", { class: "ch-v-band ch-v-band--" + (c.state === "failed" ? "bad" : "warn") }, V.icon(c.state === "stopped" ? "stop" : c.state === "interrupted" ? "return" : "warn"),
            h("span", { text: ({ failed: "The AI step failed. ", unavailable: "AI unavailable. ", unsupported: "The AI says this cannot be built here. ", stopped: "Stopped. ", interrupted: "Interrupted by a restart. " })[c.state] + (c.reason && c.state !== "stopped" ? c.reason : "") })),
          c.closest ? h("p", { class: "ch-v-note", text: "Closest ready-made job: " + c.closest }) : null,
          c.requests_note ? h("p", { class: "ch-v-muted ch-v-note", text: c.requests_note }) : null,
          c.state === "stopped" ? h("p", { class: "ch-v-muted ch-v-note", text: "Requests already sent still count today. Your draft is unchanged." }) : null] : null },
      ]);
    },
    update(body, card) { body._card = card; upd(body, card); },
  });
  SimChat.on("budget", () => mountedAll(improveBodies, b => upd(b, b._card)));

  /* ---------------------------------------------------------------- 8. limits */
  reg("limits", {
    icon: "circle-slash", footer: false, defaultOpen: true,
    title: c => c.title || "What I can't test",
    status: c => st("not-tested", (c.count || 0) + " not tested"),
    summary: c => c.line || c.summary || "",
    mount(body, card) {
      body._card = card;
      sec(body, card, [
        { key: "bul", deps: c => [c.bullets, c.observation_only], render: c => h("ul", { class: "ch-v-lines" }, (c.bullets || []).map(b => h("li", { text: b })), c.observation_only ? h("li", { text: "Drones: observation only." }) : null) },
        { key: "items", deps: c => c.items, render: c => V.hatched("NOT TESTED (" + (c.items || []).length + ")", (c.items || []).map(i => i.text), { always: true }) },
        { key: "bars", deps: c => c.user_bars, render: c => (c.user_bars || []).length ? [h("h4", { class: "ch-v-h4", text: "Bars you set" }), h("ul", { class: "ch-v-lines" }, c.user_bars.map(b => h("li", { text: b })))] : null },
        { key: "st", deps: c => [c.statement, c.line], render: c => [c.statement ? h("p", { class: "ch-v-muted ch-v-note ch-v-statement", text: c.statement }) : null, V.limitLine(c.line)] },
        { key: "foot", flash: false, deps: () => 0, render: () => V.row(V.btn("Back to my job", { kind: "ghost", icon: "return", onClick: () => V.openSheet("tests") })) },
      ]);
    },
    update(body, card) { body._card = card; upd(body, card); },
  });

  /* ---------------------------------------------------------------- 9. answer */
  reg("answer", {
    icon: "send", footer: false, defaultOpen: true,
    title: c => c.title || "Answer",
    status: c => st("done", "Answered"),
    summary: c => c.text || c.summary || "",
    mount(body, card) {
      body._card = card;
      sec(body, card, [
        { key: "q", deps: c => c.question, render: c => c.question ? h("p", { class: "ch-v-muted ch-v-note" }, h("i", { text: "You asked: " + c.question })) : null },
        { key: "t", deps: c => c.text, render: c => h("p", { class: "ch-v-answer", text: c.text || "" }) },
        { key: "d", deps: c => c.details, render: c => (c.details || []).length ? h("details", { class: "ch-v-det" }, h("summary", { text: "Show the " + c.details.length + " details" }), h("ul", { class: "ch-v-lines" }, c.details.map(d => h("li", { text: d })))) : null },
        { key: "l", flash: false, deps: c => c.limit_line, render: c => V.limitLine(c.limit_line) },
      ]);
    },
    update(body, card) { body._card = card; upd(body, card); },
  });

  /* ---------------------------------------------------------------- 10. blocked / waiting */
  reg("blocked", {
    icon: "stop", footer: false, defaultOpen: true,
    title: c => c.state === "waiting" ? "Waiting" : (c.title || "Can't do that here"),
    status: c => c.state === "waiting" ? st("queued", "Waiting") : st("blocked", "Can't do that"),
    summary: c => c.reason || c.summary || "",
    mount(body, card) {
      body._card = card;
      sec(body, card, [
        { key: "r", deps: c => [c.reason, c.state], render: c => h("p", { class: "ch-v-band ch-v-band--" + (c.state === "waiting" ? "info" : "warn") }, V.icon(c.state === "waiting" ? "clock" : "circle-slash"), h("span", { text: c.reason || "" })) },
        { key: "ticket", deps: c => c.ticket, render: c => c.ticket ? h("p", { class: "ch-v-note" }, h("b", { text: "Up next" }), " - position " + c.ticket.position + " ",
          V.btn("Cancel", { kind: "ghost", icon: "cross", aria: "Cancel the queued job", onClick: (e, b) => V.send(c.ticket.cancel || { type: "cancel_queued", args: { ticket: c.ticket.id } }, b) })) : null },
        { key: "chips", flash: false, deps: c => [c.nearest, c.chips, c.link], render: c => {
          const out = [];
          if (c.nearest && c.nearest.action) out.push(V.btn(c.nearest.label, { kind: "primary", onClick: (e, b) => V.send(c.nearest.action, b) }));
          for (const ch of c.chips || []) if (!(c.nearest && c.nearest.label === ch.label)) out.push(V.btn(ch.label, { kind: ch.kind === "primary" ? "" : "ghost", disabled: ch.enabled === false, reason: ch.reason, onClick: (e, b) => V.send(ch.action, b) }));
          if (c.link && c.link.href) out.push(h("a", { class: "ch-v-btn ch-v-btn--ghost", href: c.link.href }, V.icon("link"), h("span", { class: "ch-v-btn-t", text: c.link.label || "Open" })));
          return out.length ? V.row(out) : null;
        } },
      ]);
    },
    update(body, card) { body._card = card; upd(body, card); },
  });
})();
