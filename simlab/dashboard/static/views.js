"use strict";
/* views.js: pure builders for the chat page's card bodies and the Details sheet.
   Everything returns DOM nodes (text goes in through textContent, never through innerHTML; the only innerHTML is the page's own static icon SVG).
   Attached to SimChat.views; this file declares nothing at column 0 so it cannot collide with the other dashboard scripts. */
(function () {
  const U = SimChat.util || {};
  const V = (SimChat.views = {});

  /* ---------------------------------------------------------------- tiny DOM helper */
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (k === "dataset") Object.assign(el.dataset, v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    add(el, kids);
    return el;
  }
  function add(el, kids) {
    for (const k of kids) {
      if (k === null || k === undefined || k === false) continue;
      if (Array.isArray(k)) add(el, k);
      else el.appendChild(k instanceof Node ? k : document.createTextNode(String(k)));
    }
    return el;
  }
  V.h = h;
  V.add = add;

  /* ---------------------------------------------------------------- numbers, icons, words */
  V.num = function (v, d) {
    if (v === null || v === undefined || v === "" || Number.isNaN(+v)) return "-";
    v = +v;
    const a = Math.abs(v);
    const digits = d !== undefined ? d : a >= 100 ? 0 : a >= 10 ? 1 : 2;
    return String(Number(v.toFixed(digits)));
  };
  V.secs = function (n) {
    n = Math.max(0, Math.round(+n || 0));
    return n < 60 ? n + " s" : Math.floor(n / 60) + " min " + String(n % 60).padStart(2, "0") + " s";
  };
  V.plural = (n, one, many) => n + " " + (n === 1 ? one : many || one + "s");
  V.icon = function (name, cls) {
    const s = h("span", { class: "ch-v-ic" + (cls ? " " + cls : ""), "aria-hidden": "true" });
    try { s.innerHTML = (U.icon && U.icon(name)) || ""; } catch (e) { /* no icon: the word still carries the meaning */ }
    return s;
  };
  V.copy = function (text) {
    if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(text);
    return new Promise((ok, no) => {
      const t = h("textarea", { style: { position: "fixed", opacity: "0" } });
      t.value = text; document.body.appendChild(t); t.select();
      try { document.execCommand("copy") ? ok() : no(); } catch (e) { no(e); } finally { t.remove(); }
    });
  };

  /* state vocabulary: every state has an icon AND a word. 'dot' is the pulsing accent dot, 'ring' a hollow ring. */
  const STATES = {
    met: { icon: "check", word: "Met bar" }, below: { icon: "cross", word: "Below bar" }, running: { dot: true, word: "Running" },
    queued: { icon: "clock", word: "Queued" }, not_run: { ring: true, word: "Not run" }, not_tested: { icon: "circle-slash", word: "NOT TESTED" },
    stale: { icon: "refresh", word: "Stale" }, error: { icon: "warn", word: "Could not run" }, stopped: { icon: "stop", word: "Stopped" },
    interrupted: { icon: "return", word: "Interrupted" }, done: { icon: "check", word: "Done" }, ready: { ring: true, word: "Ready" },
    replaced: { icon: "return", word: "Replaced" },
  };
  V.STATES = STATES;
  V.glyph = function (state) {
    const s = STATES[state] || STATES.ready;
    if (s.dot) return h("span", { class: "ch-v-dot", "aria-hidden": "true" });
    if (s.ring) return h("span", { class: "ch-v-ring", "aria-hidden": "true" });
    return V.icon(s.icon);
  };
  V.badge = function (state, word) {
    const s = STATES[state] || STATES.ready;
    return h("span", { class: "ch-v-st ch-v-st--" + state }, V.glyph(state), h("span", { text: word || s.word }));
  };
  V.chip = (text, cls) => h("span", { class: "ch-v-chip" + (cls ? " " + cls : "") }, text);

  /* ---------------------------------------------------------------- buttons and actions */
  V.btn = function (label, o = {}) {
    const b = h("button", {
      type: "button", class: "ch-v-btn" + (o.kind ? " ch-v-btn--" + o.kind : "") + (o.cls ? " " + o.cls : ""),
      title: o.reason || o.title || null, "aria-label": o.aria || null, "aria-disabled": o.disabled ? "true" : null,
    }, o.icon ? V.icon(o.icon) : null, h("span", { class: "ch-v-btn-t", text: label }), o.tag ? h("span", { class: "ch-v-tag", text: o.tag }) : null);
    if (o.disabled) { b.disabled = true; b.classList.add("is-off"); }
    if (o.onClick) b.addEventListener("click", ev => { if (!b.disabled) o.onClick(ev, b); });
    return b;
  };
  /* send a structured action; the button shows it is busy until the server accepted it */
  const SAY = { stop: "Stop", undo: "Undo", redo: "Redo", watch: "Watch it", run_tests: "Run tests", improve: "Improve with AI", sheet: "Get the build sheet", pick_design: "Use this design", edit_test: "Change a test", add_test: "Add a test", remove_test: "Remove a test", cancel_queued: "Cancel", set_job_number: "Change a job number" };
  V.send = function (action, btn, label) {
    label = label || SAY[action.type];
    if (btn) { btn.disabled = true; btn.setAttribute("aria-busy", "true"); }
    let p;
    try { p = Promise.resolve(label ? SimChat.send(action, { label }) : SimChat.send(action)); } catch (e) { p = Promise.reject(e); }
    return p.catch(() => { }).finally(() => { if (btn && btn.isConnected) { btn.disabled = false; btn.removeAttribute("aria-busy"); } });
  };
  /* a button for a backend action {type, label, args, enabled, reason, primary} */
  V.actionBtn = function (a, o = {}) {
    const kind = o.kind || (a.primary ? "primary" : "");
    return V.btn(o.label || a.label, {
      kind, icon: o.icon, reason: a.enabled === false ? a.reason : null, disabled: a.enabled === false, tag: o.tag,
      onClick: (ev, b) => (o.onClick ? o.onClick(ev, b) : V.send({ type: a.type, args: a.args || {} }, b, o.label || a.label)),
    });
  };
  V.openSheet = tab => { if (SimChat.sheet && SimChat.sheet.open) SimChat.sheet.open(tab); };
  V.row = (...kids) => h("div", { class: "ch-v-actions" }, kids);

  /* ---------------------------------------------------------------- partial updates and the 1 s clock */
  /* sections(root, defs): each def {key, deps(card) -> value, render(card) -> Node|Node[]}; update(card) re-renders only the ones whose deps changed. */
  V.sections = function (root, defs) {
    const slots = defs.map(d => { const el = h("div", { class: "ch-v-sec" + (d.cls ? " " + d.cls : ""), dataset: { sec: d.key } }); root.appendChild(el); return { d, el, sig: null }; });
    return {
      update(card, flash = true) {
        for (const s of slots) {
          let sig;
          try { sig = JSON.stringify(s.d.deps ? s.d.deps(card) : card); } catch (e) { sig = String(Math.random()); }
          if (sig === s.sig) continue;
          const first = s.sig === null;
          const ae = document.activeElement;
          if (!first && !s.forced && s.d.hold && ae && s.el.contains(ae) && /^(INPUT|SELECT|TEXTAREA)$/.test(ae.tagName)) continue;      // never pull an input out from under the person typing
          s.sig = sig; s.forced = false;
          s.el.replaceChildren();
          let out = null;
          try { out = s.d.render(card); } catch (e) { out = h("p", { class: "ch-v-err", text: "This part could not be drawn." }); console.warn("card section failed", s.d.key, e); }
          add(s.el, [out]);
          s.el.hidden = !s.el.childNodes.length;
          if (!first && flash && s.d.flash !== false) V.flash(s.el);
        }
      },
      slot: key => (slots.find(s => s.d.key === key) || {}).el,
      force(key, card) { const s = slots.find(x => x.d.key === key); if (s) { s.sig = "\u0000"; s.forced = true; this.update(card, false); } },
    };
  };
  V.flash = function (el) {
    el.classList.remove("ch-v-flash");
    void el.offsetWidth;
    el.classList.add("ch-v-flash");
    setTimeout(() => el.classList.remove("ch-v-flash"), 260);
  };
  /* one shared ticker keeps every "elapsed" span honest (real seconds, never a made-up percentage) */
  const clocks = new Set();
  let clockTimer = 0;
  function tickClocks() {
    for (const c of [...clocks]) {
      if (!c.el.isConnected) { clocks.delete(c); continue; }
      c.el.textContent = V.secs(Date.now() / 1000 - c.since);
    }
    if (!clocks.size) { clearInterval(clockTimer); clockTimer = 0; }
  }
  V.elapsed = function (sinceEpochSeconds, cls) {
    const now = Date.now() / 1000;
    const since = sinceEpochSeconds && sinceEpochSeconds <= now + 2 ? sinceEpochSeconds : now;       // no start time (or one in the future): count from when it is first shown
    const el = h("span", { class: "ch-v-mono " + (cls || ""), text: V.secs(now - since) });
    clocks.add({ el, since });
    if (!clockTimer) clockTimer = setInterval(tickClocks, 1000);
    return el;
  };

  /* ---------------------------------------------------------------- verdict, facts, checks, findings */
  const LIGHT = { green: { icon: "check", word: "Meets it on paper", cls: "ok" }, amber: { icon: "warn", word: "Check these first", cls: "warn" }, red: { icon: "cross", word: "Does not meet it yet", cls: "bad" } };
  V.LIGHT = LIGHT;
  V.verdict = function (v) {
    if (!v) return null;
    const l = LIGHT[v.light] || LIGHT.amber;
    return h("div", { class: "ch-v-verdict ch-v-verdict--" + l.cls }, V.icon(l.icon), h("span", { class: "ch-v-verdict-w", text: l.word }), h("span", { class: "ch-v-verdict-h", text: v.headline || "" }));
  };
  V.keyFacts = facts => h("dl", { class: "ch-v-keyfacts" }, (facts || []).slice(0, 4).map(f =>
    h("div", { class: "ch-v-kf" }, h("dt", { text: f.label }), h("dd", { class: "ch-v-mono" }, V.num(f.value), f.unit ? h("span", { class: "ch-v-unit", text: " " + f.unit }) : null))));
  V.facts = facts => h("dl", { class: "ch-v-facts" }, (facts || []).map(f =>
    h("div", { class: "ch-v-fact", title: f.note || null },
      h("dt", { text: f.label }),
      h("dd", { class: "ch-v-mono" }, V.num(f.value), f.unit ? h("span", { class: "ch-v-unit", text: " " + f.unit }) : null,
        f.lo !== null && f.lo !== undefined && f.hi !== null && f.hi !== undefined ? h("span", { class: "ch-v-range", text: "typical " + V.num(f.lo) + " to " + V.num(f.hi) }) : null))));
  V.checks = checks => h("ul", { class: "ch-v-checks" }, (checks || []).map(c =>
    h("li", { class: "ch-v-check ch-v-check--" + (c.ok ? "met" : "below") },
      V.icon(c.ok ? "check" : "cross"), h("span", { class: "ch-v-check-w", text: c.ok ? "Met" : "Not met" }),
      h("span", { class: "ch-v-check-t" }, h("b", { text: c.text }), c.detail ? h("span", { class: "ch-v-muted", text: " " + c.detail }) : null))));
  const SEV = { error: { icon: "cross", word: "Problem" }, warn: { icon: "warn", word: "Watch" }, info: { icon: "check", word: "Note" } };
  V.findings = fs => (fs && fs.length) ? h("ul", { class: "ch-v-findings" }, fs.map(f => {
    const s = SEV[f.severity] || SEV.info;
    return h("li", { class: "ch-v-finding ch-v-finding--" + (f.severity || "info") }, V.icon(s.icon), h("span", { class: "ch-v-check-w", text: s.word }),
      h("span", { class: "ch-v-check-t" }, h("b", { text: f.title }), f.detail ? h("span", { class: "ch-v-muted", text: " " + f.detail }) : null,
        f.fix ? h("span", { class: "ch-v-fix", text: " Suggested fix: " + f.fix }) : null));
  })) : null;

  /* ---------------------------------------------------------------- parts */
  V.partChips = parts => h("ul", { class: "ch-v-partchips" }, (parts || []).map(p =>
    h("li", { class: "ch-v-partchip" + (p.have ? " is-have" : "") }, V.icon(p.have ? "check" : "plus"), h("span", { text: (p.title || p.name || "").replace(/_/g, " ") }), h("span", { class: "ch-v-muted", text: p.have ? "have" : "to buy" }))));
  V.partsTable = rows => h("div", { class: "ch-v-table-wrap" }, h("table", { class: "ch-v-table ch-v-parts" },
    h("thead", null, h("tr", null, ["Qty", "Part", "Have it?", "Confidence", "Mass"].map(t => h("th", { scope: "col", text: t })))),
    h("tbody", null, (rows || []).map(r => h("tr", null,
      h("td", { class: "ch-v-mono", text: r.qty + "x" }),
      h("td", null, h("b", { text: r.title || r.name }), h("div", { class: "ch-v-muted ch-v-mono", text: r.name })),
      h("td", null, h("span", { class: "ch-v-st ch-v-st--" + (r.have_all ? "met" : "queued") }, V.glyph(r.have_all ? "met" : "queued"), r.have_all ? "Have" : "Buy " + (r.to_buy || r.qty - r.have))),
      h("td", { text: r.confidence || "" }),
      h("td", { class: "ch-v-mono", text: r.mass_g !== undefined ? V.num(r.mass_g) + " g" : "" }))))));
  V.wiring = w => {
    if (!w) return null;
    if (!w.generated) return h("p", { class: "ch-v-muted ch-v-note" }, V.icon("circle-slash"), " " + (w.note || "No wiring table for this machine; wire it manually."));
    return h("div", { class: "ch-v-wiring" }, (w.tables || []).map(t => h("div", null,
      h("h4", { class: "ch-v-h4", text: t.title }),
      h("div", { class: "ch-v-table-wrap" }, h("table", { class: "ch-v-table" },
        h("thead", null, h("tr", null, ["From", "To", "Note"].map(x => h("th", { scope: "col", text: x })))),
        h("tbody", null, (t.rows || []).map(r => h("tr", null, h("td", { class: "ch-v-mono", text: r.from }), h("td", { class: "ch-v-mono", text: r.to }), h("td", { text: r.note || "" })))))))));
  };
  V.partsText = rows => (rows || []).map(r => `${r.qty}x ${r.title || r.name} (${r.have_all ? "have" : "buy " + (r.to_buy || r.qty - r.have)})`).join("\n");

  /* ---------------------------------------------------------------- NOT TESTED */
  V.hatched = (title, items, o = {}) => (items && items.length) || o.always ? h("section", { class: "ch-v-nt", "aria-label": title },
    h("h4", { class: "ch-v-nt-h" }, V.icon("circle-slash"), h("span", { text: title })),
    items && items.length ? h("ul", { class: "ch-v-nt-l" }, items.map(i => h("li", null, typeof i === "string" ? i : h("span", null, h("b", { text: i.title }), i.reason ? h("span", { class: "ch-v-muted", text: ": " + i.reason }) : null)))) : null) : null;
  V.limitLine = text => text ? h("p", { class: "ch-v-limit" }, V.icon("lock"), h("span", { text })) : null;

  /* ---------------------------------------------------------------- the matrix */
  const SHORT = { met: "met", below: "below", running: "running", queued: "queued", not_run: "not run", error: "error", not_tested: "NOT TESTED" };
  V.cell = function (c, o = {}) {
    const st = c.state in SHORT ? c.state : "not_run";
    const pct = c.n ? Math.round(100 * (c.success !== null && c.success !== undefined ? c.success : c.wins / c.n)) : null;
    const el = h(o.onClick ? "button" : "div", {
      class: "ch-v-cell ch-v-cell--" + st + (o.compact ? " is-compact" : "") + (o.selected ? " is-sel" : ""), type: o.onClick ? "button" : null,
      "aria-label": c.text, title: c.text, "aria-pressed": o.onClick && o.selected !== undefined ? String(!!o.selected) : null,
    },
      o.compact ? null : h("span", { class: "ch-v-cell-l ch-v-mono", text: c.label }),
      h("span", { class: "ch-v-cell-v" }, V.glyph(st), o.compact ? null : h("span", { class: "ch-v-mono", text: pct !== null ? pct + "%" : SHORT[st] })),
      !o.compact && c.n ? h("span", { class: "ch-v-cell-n ch-v-mono", text: c.wins + "/" + c.n }) : null);
    if (o.onClick) el.addEventListener("click", () => o.onClick(c));
    return el;
  };
  /* matrix = results_matrix JSON {rows:[{case_id,title,unit,stale,untestable,bar:{text},tries,cells[]}], bar_text, ...}.
     Rows have different level counts, so each row lists its own levels. */
  V.matrix = function (m, o = {}) {
    if (!m || !m.rows || !m.rows.length) return h("p", { class: "ch-v-muted", text: "No tests in this run." });
    return h("div", { class: "ch-v-matrix" + (o.compact ? " is-compact" : ""), role: "group", "aria-label": "Results by condition" }, m.rows.map(r =>
      h("div", { class: "ch-v-mrow" + (r.stale ? " is-stale" : "") + (r.untestable ? " is-nt" : "") },
        h("div", { class: "ch-v-mname" },
          h("span", { class: "ch-v-mtitle", text: r.title }),
          r.stale ? V.badge("stale") : null,
          o.compact ? null : h("span", { class: "ch-v-muted" }, r.untestable ? "NOT TESTED" : (r.bar ? r.bar.text : "") + (r.tries ? ", " + V.plural(r.tries, "try", "tries") + " each" : ""))),
        h("div", { class: "ch-v-mcells" }, (r.cells || []).map(c => V.cell(c, {
          compact: o.compact, onClick: o.onCell ? cell => o.onCell(r, cell) : null,
          selected: o.selected ? o.selected.key === c.key : undefined,
        }))))));
  };
  V.legend = () => h("ul", { class: "ch-v-legend", "aria-label": "Legend" }, ["met", "below", "running", "queued", "not_run", "not_tested"].map(s => h("li", null, V.badge(s))));

  /* a sparkline of success per level (SVG, no libraries) */
  V.spark = function (values, o = {}) {
    const w = o.w || 96, hh = o.h || 28, pad = 3, ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${w} ${hh}`); svg.setAttribute("width", w); svg.setAttribute("height", hh); svg.setAttribute("class", "ch-v-spark");
    svg.setAttribute("role", "img"); svg.setAttribute("aria-label", o.label || "trend");
    const vs = (values || []).filter(v => v !== null && v !== undefined);
    if (vs.length < 2) return svg;
    const lo = o.min !== undefined ? o.min : Math.min(...vs), hi = o.max !== undefined ? o.max : Math.max(...vs), span = hi - lo || 1;
    const pts = values.map((v, i) => v === null || v === undefined ? null : [pad + i * (w - 2 * pad) / (values.length - 1), hh - pad - (v - lo) / span * (hh - 2 * pad)]).filter(Boolean);
    const line = document.createElementNS(ns, "polyline");
    line.setAttribute("points", pts.map(p => p.map(x => x.toFixed(1)).join(",")).join(" "));
    line.setAttribute("fill", "none"); line.setAttribute("stroke", "currentColor"); line.setAttribute("stroke-width", "1.6"); line.setAttribute("stroke-linejoin", "round");
    svg.appendChild(line);
    const end = pts[pts.length - 1], dot = document.createElementNS(ns, "circle");
    dot.setAttribute("cx", end[0]); dot.setAttribute("cy", end[1]); dot.setAttribute("r", 2.2); dot.setAttribute("fill", "currentColor");
    svg.appendChild(dot);
    return svg;
  };

  /* the Tests card's rows show what each test found only when the server re-sends them after a run; until then the newest Results matrix fills in (the server's own data always wins) */
  V.results = () => { const st = SimChat.state || {}; return (st.order || []).map(id => st.cards[id]).filter(c => c && c.type === "results" && c.matrix); };
  V.resSig = () => V.results().map(r => r.id + ":" + r.matrix.rows.map(q => q.cells.map(c => c.state[0]).join("")).join("|")).join(";");
  V.withResults = function (rows) {
    const latest = V.results().reverse();
    return (rows || []).map(r => {
      if (r.status === "untestable" || r.last_run) return r;
      for (const rc of latest) {
        const mr = rc.matrix.rows.find(q => q.case_id === r.id);
        if (!mr) continue;
        const any = mr.cells.some(c => c.state === "met" || c.state === "below" || c.state === "running" || c.state === "queued");
        if (!any) continue;
        const fin = mr.cells.every(c => c.state === "met" || c.state === "below");
        const part = mr.cells.some(c => c.state === "met" || c.state === "below");
        return Object.assign({}, r, { cells: mr.cells, stale: !!mr.stale, status: mr.cells.some(c => c.state === "running") ? "running" : fin ? "done" : "ready",
          _verdict: fin ? (mr.cells.every(c => c.state === "met") ? "met" : "below") : part ? "partial" : null });
      }
      return r;
    });
  };

  /* ---------------------------------------------------------------- tests */
  V.srcBadge = src => h("span", { class: "ch-v-chip ch-v-src ch-v-src--" + src, text: ({ catalogue: "built-in", ai: "AI", user: "you" })[src] || src });
  /* one condition line for a case: "Waves 0.15 to 0.9 m, 4 tries each" */
  V.caseLine = function (c) {
    if (c.kind === "sweep") {
      const s = c.sweep, u = s.unit && s.unit !== "grip" ? " " + s.unit : "";
      return V.num(s.levels[0]) + " to " + V.num(s.levels[s.levels.length - 1]) + u + ", " + V.plural(c.tries.n, "try", "tries") + " each";
    }
    if (c.kind === "fixed") return "one combination, " + V.plural(c.tries.n, "try", "tries");
    return c.untestable_reason || "cannot be tested here";
  };
  V.counts = function (rows) {
    const t = { ran: 0, ready: 0, stale: 0, running: 0, nt: 0 };
    for (const c of rows || []) {
      if (c.status === "untestable") t.nt++;
      else if (c.status === "running") t.running++;
      else if (c.stale) t.stale++;
      else if (c.status === "done") t.ran++;
      else t.ready++;
    }
    return t;
  };

  /* ---------------------------------------------------------------- see-through snapshot */
  V.snapshot = function (card, o = {}) {
    const fig = h("figure", { class: "ch-v-snap" + (o.large ? " is-large" : ""), "aria-label": "See-through picture of the machine" });
    const pv = card.preview || { state: "pending" };
    const note = h("figcaption", { class: "ch-v-snap-c" });
    fig.append(note);
    const place = (icon, text) => { fig.classList.add("is-empty"); fig.prepend(h("span", { class: "ch-v-snap-ph" }, V.icon(icon), h("span", { text }))); };
    if (pv.state === "unavailable") { place("circle-slash", pv.reason || "No preview here."); return fig; }
    if (pv.state !== "ready" || !pv.scene) { fig.classList.add("is-wait"); fig.prepend(h("span", { class: "ch-v-snap-ph ch-v-shimmer" }, h("span", { text: "Drawing the see-through picture" }))); return fig; }
    const key = (card.pick || 0) + ":" + ((card.proposals && card.proposals[card.pick || 0] || {}).machine_sig || "") + (o.large ? ":L" : "");
    const done = url => { fig.classList.remove("is-wait"); fig.querySelector(".ch-v-snap-ph") && fig.querySelector(".ch-v-snap-ph").remove(); const img = h("img", { alt: "The machine, drawn see-through so the parts inside show", src: url, class: "ch-v-snap-img" }); fig.prepend(img); requestAnimationFrame(() => img.classList.add("is-in")); note.textContent = "Drawn see-through; the parts inside are shown."; };
    fig.classList.add("is-wait"); fig.prepend(h("span", { class: "ch-v-snap-ph ch-v-shimmer" }, h("span", { text: "Drawing the see-through picture" })));
    const live = SimChat.live;
    if (!live || !live.photo) { fig.querySelector(".ch-v-snap-ph").remove(); place("circle-slash", "The preview needs the 3D viewer."); return fig; }
    live.photo(pv, key, o.large ? { w: 960, h: 600 } : { w: 480, h: 300 }).then(done, () => { fig.querySelector(".ch-v-snap-ph") && fig.querySelector(".ch-v-snap-ph").remove(); fig.classList.remove("is-wait"); place("circle-slash", "Could not draw the preview here."); });
    return fig;
  };

  /* ---------------------------------------------------------------- the full design block (card "more" and the sheet's Design tab) */
  V.alternatives = function (card) {
    const props = card.proposals || [];
    if (props.length < 2) return null;
    return h("div", { class: "ch-v-seg", role: "radiogroup", "aria-label": "Design options" }, props.map((p, i) => {
      const l = LIGHT[(p.verdict || {}).light] || LIGHT.amber;
      const on = i === (card.pick || 0);
      const b = h("button", { type: "button", role: "radio", "aria-checked": String(on), class: "ch-v-seg-b" + (on ? " is-on" : ""), title: (p.verdict || {}).headline || "" },
        V.icon(l.icon, "ch-v-tone-" + l.cls), h("span", { class: "ch-v-seg-t" }, h("b", { text: p.label }), h("span", { class: "ch-v-muted ch-v-mono", text: V.num(p.runtime, 0) + " min, " + V.num(p.mass) + " kg" })));
      b.addEventListener("click", () => { if (!on) V.send({ type: "pick_design", args: { index: i } }, b); });
      return b;
    }));
  };
  V.designDetail = function (card) {
    const p = (card.proposals || [])[card.pick || 0] || {};
    return h("div", { class: "ch-v-detail" },
      V.alternatives(card),
      h("h4", { class: "ch-v-h4", text: "Requirements" }), V.checks(p.checks),
      p.findings && p.findings.length ? [h("h4", { class: "ch-v-h4", text: "Findings" }), V.findings(p.findings)] : null,
      p.facts && p.facts.length ? [h("h4", { class: "ch-v-h4", text: "All the numbers" }), V.facts(p.facts)] : null,
      p.parts && p.parts.length ? [h("h4", { class: "ch-v-h4", text: "Parts" }), V.partChips(p.parts)] : null,
      card.scanned ? h("p", { class: "ch-v-muted ch-v-note", text: "Looked at " + card.scanned + " possible builds" + (card.seconds ? " in " + V.num(card.seconds, 1) + " s" : "") + "." }) : null);
  };

  /* ---------------------------------------------------------------- the tough-conditions sliders (the watch card and the Focus rail share one `values` object: knob -> number, absent = calm) */
  V.condForm = function (menu, values, o = {}) {
    const fmt = (c, v) => V.num(v, v >= 10 ? 1 : 2) + (c.unit && c.unit !== "grip" ? " " + c.unit : "");
    return h("div", { class: "ch-lv-sliders" }, (menu || []).map(cd => {
      const on = values[cd.knob] !== undefined && values[cd.knob] !== null;
      const span = cd.max - cd.min, step = span >= 20 ? 1 : span >= 2 ? 0.1 : span >= 0.5 ? 0.05 : 0.01;
      const val = on ? values[cd.knob] : cd.levels[Math.min(1, cd.levels.length - 1)];
      const id = "ch-cs-" + (o.idp || "c") + "-" + cd.knob;
      const out = h("output", { class: "ch-v-mono", for: id, text: fmt(cd, val) });
      const rng = h("input", { type: "range", class: "ch-v-range-in", id, min: cd.min, max: cd.max, step, value: val, "aria-label": cd.title, title: cd.plain || null });
      rng.disabled = !on;
      const chk = h("input", { type: "checkbox", "aria-label": "Use " + cd.title });
      chk.checked = on;
      chk.addEventListener("change", () => { if (chk.checked) { values[cd.knob] = +rng.value; rng.disabled = false; } else { delete values[cd.knob]; rng.disabled = true; } if (o.onChange) o.onChange(); });
      rng.addEventListener("input", () => { out.textContent = fmt(cd, +rng.value); if (chk.checked) values[cd.knob] = +rng.value; if (o.onChange) o.onChange(); });
      return h("div", { class: "ch-lv-slider" }, h("label", { class: "ch-lv-sl-l" }, chk, h("span", { text: cd.title })), rng, out);
    }));
  };

  /* ---------------------------------------------------------------- results pieces shared by the card and the sheet */
  V.resultsProgress = function (card) {
    const p = card.progress || {};
    const run = (card.matrix && card.matrix.rows || []).flatMap(r => r.cells.map(c => ({ r, c }))).find(x => x.c.state === "running");
    const where = run ? run.c.text.replace(/: running.*$/, "") : (p.label || "");
    const line = card.state === "queued" ? "Waiting for the tests that are already running." :
      `Condition ${Math.min((p.done || 0) + 1, p.total || 1)} of ${p.total || "?"}${where && p.label !== "starting" ? " (" + where.charAt(0).toLowerCase() + where.slice(1) + ")" : ""}, ${p.tries_done || 0} of ${p.tries_total || "?"} tries`;
    const pct = p.total ? Math.round(100 * (p.done || 0) / p.total) : 0;
    return h("div", { class: "ch-v-prog" },
      h("p", { class: "ch-v-prog-l", "aria-live": "off" }, h("b", { text: line })),
      card.state === "running" ? h("div", { class: "ch-v-bar", role: "progressbar", "aria-label": "Conditions finished", "aria-valuemin": "0", "aria-valuemax": String(p.total || 0), "aria-valuenow": String(p.done || 0) }, h("i", { style: { width: pct + "%" } })) :
        h("div", { class: "ch-v-bar is-indet", "aria-label": "Waiting" }, h("i")),
      h("p", { class: "ch-v-muted ch-v-note" }, card.state === "queued" ? "Waiting for " : "Running for ", V.elapsed(card.started), p.stopping ? " - stopping after the current condition" : ""));
  };
  V.weakest = w => w ? h("p", { class: "ch-v-weak" }, V.icon("cross"), h("span", null, h("b", { text: "Weakest: " }), w.title + (w.why ? ". " + w.why : ""))) : null;
  V.notTestedBlock = function (card) {
    const items = card.not_tested || [];
    return h("div", { class: "ch-v-ntwrap" },
      V.hatched("What was NOT tested", items, { always: true }),
      V.limitLine(card.limit_line),
      card.statement ? h("p", { class: "ch-v-muted ch-v-note ch-v-statement", text: card.statement }) : null);
  };
})();
