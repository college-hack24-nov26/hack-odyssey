# PARTH.OS — Autonomous Personal Operating Agent: Implementation Plan

## Context

Parth wants v2 of his personal assistant, rebuilt to the PARTH.OS spec: an ambient daemon that perceives his life feeds, pre-computes academic work (a lab manual posted in a WhatsApp group becomes solved, executed, typeset journal PDF), defends his biological anchors by rebalancing on its own, writes its own tools, and reaches out proactively over Discord/Telegram without being prompted.

`Desktop\bot` (v1: 40 commits, deployed on Render) proved the pieces but hit structural limits. The rebuild goes into the empty `bot_2`:

| v1 limitation (seen in code) | v2 answer |
|---|---|
| WhatsApp reads only captions/filenames and never opens the PDF (`whatsappService.ts`) | Media download → blob store → extraction → solver |
| Alert dedup lives in memory, and one shared `lastMacroNotifiedKey` serves 3 different checks (`autonomousHeartbeat.ts`), so alerts repeat after restarts and some get lost | Durable outbox with idempotency keys |
| Clock-time math breaks after midnight; replans key off the calendar date | "Operating day" time model (10:30 → 10:30 IST) |
| Global singletons and god files (`dashboardServer.ts` 90 KB, `telegram/bot.ts` 50 KB); the agent imports the dashboard | Composition root + dependency injection + event bus |
| `unhandledRejection` is swallowed globally | Typed lint bans floating promises; failures surface as health events |
| Current time in the system prompt; one model with a 10 s timeout | Request-budgeted LLM gateway with fallbacks and a heuristic engine |

**Decisions locked in:** Linux VPS + Docker (development on this Windows PC; the code is cross-platform) · OpenRouter `thinkingmachines/inkling:free` as primary model · Typst for journals · internal schedule only (no Google Calendar).

## Verified constraints that shape the design (checked 2026-10-08)

1. **OpenRouter free tier:** 20 req/min. **50 req/day** if fewer than 10 credits were ever purchased, **1,000/day** at 10 or more. The counter is per UTC day, so it **resets at 05:30 IST**. `GET /api/v1/key` returns `free_model_daily_requests {used, limit, remaining}`, and 429s carry `X-RateLimit-*` / `Retry-After`. ⇒ **Requests are the scarce resource, not tokens** (tokens are free; context is 1M).
2. **`inkling:free`** (MoE, 41B active, text + image input, 1M context) supports `tools` and `reasoning` but has **no `tool_choice` and no `response_format`**. ⇒ Structured output = prompt contract + zod + `jsonrepair`. Fallbacks go through OpenRouter's `models: [...]` array, which triggers on errors, rate limits and moderation: `thinkingmachines/inkling-small:free`, `google/gemma-4-31b-it:free` (both take images and tools).
3. **PDFs:** Inkling has no native file input, so OpenRouter would silently fall back to the **paid** `mistral-ocr` engine. ⇒ Parse PDFs locally and send scanned pages as images.
4. **Free-model privacy:** OpenRouter has a separate "may train on prompts" setting for free models. ⇒ Heuristics prefilter, and classmates' names and phone numbers are redacted before any request.
5. **Toolchain:** `typescript-eslint` supports TypeScript `<6.1`. ⇒ Pin **TypeScript 6.0.3** (not v1's TS 7) to keep `no-floating-promises`. Node 24.15's built-in `node:sqlite` ships SQLite 3.51.3 with FTS5 and JSON1 (probed locally), so the database needs no native dependency. Baileys `latest` is 7.0.0-rc14; the v1-proven `legacy` 6.7.24 is the fallback.

## Architecture

```
 Perception adapters ─► Event Bus (+ persisted event log) ─► Agent Kernel ◄─► LLM Gateway
 WhatsApp (read-only)        │                                   │            (budget · breakers ·
 Email · Classroom           ▼                                   │             fallbacks · heuristics)
 GitHub · Hackathons    Durable Job Queue ─► Academic Pipeline · Governance · Tool Forge ─► Docker Sandbox
 Fitness · Discord/TG        ▲                                   │
 inbound               Chronos engine                 Memory (episodic · FTS5 · vectors · facts · habits)
                                                                 │
                              Dispatcher (outbox + anchor-aware policy) ─► Discord rooms · Telegram
              Telemetry: health registry · watchdog · /health · backups  (cross-cutting)
```

Principles:
- **Composition root + DI.** `createContainer()` wires every service; there are no module-level singletons. An injectable `Clock` makes every time-dependent path testable.
- **Durable by default.** Events, jobs (leases + idempotency keys), notifications (outbox), Chronos runs, the action ledger and the tool registry all live in SQLite (WAL, `synchronous=NORMAL`, foreign keys, busy timeout). A restart loses nothing.
- **Operating day.** A day runs from 10:30 IST to 10:30 IST, and time is minutes 0–1439 from wake. Dinner = [660, 750), deep work = [750, 1080), sleep = [1080, 1440). There are no midnight special cases anywhere.
- **Request-frugal LLM use.** Batch (up to 20 messages per triage call), plan-then-execute (one call returns an action plan, then deterministic execution), heuristics first and the LLM only for judgment.
- **Degradation ladder.** L0 Full → L1 Frugal (LLM only for interactive and academic work) → L2 Heuristic (no LLM; generative jobs are deferred to the 05:30 IST reset). The level is chosen automatically from remaining budget, breaker states and time-to-reset, and every change is announced in `#operating-room`. The system never crashes on LLM failure and never goes silent.
- **Ingested content is data, never instructions.** Every goal carries a trust tier (`owner` | `system` | `untrusted_content`), and the kernel enforces tool policy in code, not in the prompt.
- **LLM-written code runs only in the Docker sandbox.** If Docker is unavailable, results are marked *unverified* and never executed on the host.

## Repository layout (`bot_2/`)

```
src/
  main.ts            composition root, signals, crash-recovery notice
  app/               container.ts (DI), lifecycle.ts (ordered start/stop)
  config/            env.ts (zod), profile.ts → loads config/profile.json (anchors, timetable, subjects, rooms, allowlists, macro targets)
  core/              clock.ts, operatingDay.ts, eventBus.ts, errors.ts
  db/                database.ts, migrate.ts, migrations/NNNN_*.sql, repos/*
  chronos/           engine.ts, jobs/* (one file per cadence or anchor job)
  queue/             jobQueue.ts, workerPool.ts
  llm/               gateway.ts, budget.ts, breaker.ts, structured.ts, redact.ts, providers/{openrouter,heuristic}.ts
  agent/             kernel.ts, planExecute.ts, chatLoop.ts, policy.ts, ledger.ts, dialogueState.ts, tools/*, forge/*
  perception/        whatsapp/, email/, classroom/, github/, hackathons/, fitness/, triage/
  documents/         blobStore.ts, extract.ts, rasterize.ts, ocr.worker.ts
  academic/          pipeline.ts (stage machine), understand.ts, deadline.ts, solver.ts, render.ts
  sandbox/           runner.ts, dockerRunner.ts, rpc.ts
  governance/        anchors.ts, solver.ts, rebalancer.ts, overload.ts, slips.ts
  memory/            episodic.ts, embed.worker.ts, retrieval.ts, facts.ts, habits.ts, reflection.ts
  dispatch/          outbox.ts, policy.ts, discord.ts, telegram.ts, render.ts
  interfaces/        discordCommands.ts, telegramCommands.ts, http.ts
  telemetry/         logger.ts, health.ts, watchdog.ts, backup.ts
sandbox-images/      python/, cpp/, octave/, tool-runner/ (Dockerfiles + stdio harness)
templates/typst/     journal.typ, cover.typ
scripts/             whatsapp-link.ts, doctor.ts, sandbox-build.ts
tests/               unit/, property/, integration/, e2e/, fixtures/ (PDFs, Baileys message JSON, HTML pages)
deploy/              parthos.service (systemd), compose.yaml (alternative), RUNBOOK.md
config/              profile.json, llm.json (route → model chain + reasoning effort)
```

**Stack.** Runtime: `@openrouter/sdk` · `zod` 4 · `jsonrepair` · `baileys` 7 rc · `discord.js` 14 · `grammy` · `pino` · `luxon` · `chrono-node` · `unpdf` + `@napi-rs/canvas` · `mammoth` · `tesseract.js` · `@myriaddreamin/typst-ts-node-compiler` · `@huggingface/transformers` · `imapflow` · `qrcode-terminal`. Dev: `typescript@6.0.3` · `typescript-eslint` · `eslint` 10 · `vitest` 5 · `fast-check` · `tsx` · `@types/node`. TypeScript runs with `strict`, `noUncheckedIndexedAccess`, `noImplicitOverride` and `verbatimModuleSyntax`, compiled to `dist/` for production.

## Deliverable 1 — Perception & ambient ingestion (`perception/`, `chronos/`)

**Chronos engine.** Jobs are `every(ms)` or `dailyAt("HH:MM", IST, weekdays?)` (luxon), each with a misfire policy (`skip` | `fire_once`), a per-job mutex (no overlapping runs) and a `chronos_runs` ledger. On boot it evaluates missed runs, so a dinner alert missed during downtime is skipped while a missed reflection runs once.

| Cadence / time (IST) | Job | Room |
|---|---|---|
| every 10 s | outbox flush, upcoming-block and slip detection, health probes (no LLM) | — |
| every 5 min | pipeline advancement, email + Classroom polls, triage batch flush | — |
| every 30 min | hackathon radar, GitHub telemetry, scheduled Forge tools | — |
| every 6 h | memory consolidation and habit updates (no LLM) | — |
| 10:30 | wake briefing: held notifications, today's plan, reflection summary | `#operating-room` + TG |
| 20:30 | macro check: protein < 80 g → deficit alert with suggestions | `#macro-fitness` + TG |
| 21:25 | dinner protection: wrap up, what is paused | `#schedule-planner` + TG |
| 22:55 | deep-work sprint briefing: top-2 engineering tasks, commit status | `#schedule-planner` + TG |
| 02:00 | daily-commit check for the current operating day | `#schedule-planner` + TG |
| 05:35 | nightly reflection + tomorrow's plan (silent; reported at 10:30) | — |

**WhatsApp watcher.**
- Baileys sits behind a **read-only allowlist `Proxy`**: only `ev`, `groupMetadata`, `user` and `end` are reachable, and any other property throws `WhatsAppReadOnlyViolation`. This is stronger than v1's denylist, which cannot anticipate write methods added in new Baileys versions.
- `markOnlineOnConnect: false` keeps phone notifications working; `syncFullHistory: false`.
- Documents and images are downloaded with `downloadMediaMessage` (no re-upload requests) into `data/blobs/sha256/…`, so the same PDF forwarded to three groups is processed once.
- Link it as a **new linked device** (`npm run whatsapp:link`) so v1 on Render and v2 don't keep replacing each other's connection. It reconnects with backoff; `loggedOut` raises a critical alert. Cloud auth-restore from v1 is kept for disaster recovery.

**Triage.**
- Stage 1, heuristics (ported from v1): subject regexes, commercial and banter filters, monitored-group list, academic filename patterns, near-duplicate detection.
- Stage 2, one batched LLM call (≤ 20 redacted messages) classifies into `coursework_task`, `academic_notice`, `study_resource`, `timetable_change` or `irrelevant`, with subject and deadline hints.
- Attachments in monitored groups go straight to the document pipeline. Raw messages are kept minimally, with a 30-day TTL.

**Other feeds.**
- Email: an `EmailSource` interface; the first adapter is IMAP (`imapflow`, app password), polled every 5 min. A Gmail-API adapter is added only if the college Workspace blocks IMAP. Attachments feed the document pipeline.
- Classroom: v1's `parseIcsFeed` is ported as-is, with tests.
- GitHub: GraphQL `contributionsCollection` → `github_daily(op_day, commits, …)`.
- Hackathon radar: built-in Devfolio and Unstop sources (their public JSON endpoints, cached and polite), filtered to Mumbai, Thane, Navi Mumbai and online. It dedupes, alerts on new events and on early-bird or registration deadlines (T-3d, T-1d), and attaches **registration drafts** (pitch, team, profile fields) that are never submitted. New platforms come in through the Tool Forge.
- Fitness: quick-log presets (ported) via Discord slash commands and Telegram buttons, plus `POST /ingest/fitness` (bearer token) for phone automations such as Health Connect bridge apps.
- Discord and Telegram inbound: owner-only chat routed to the kernel.

## Deliverable 2 — Autonomous tool-calling & self-execution loop (`llm/`, `agent/`)

**LLM gateway.**
- Provider-neutral requests: messages, image parts, `ToolSpec` built from zod, an optional output schema, a route and a priority. Providers are `OpenRouterProvider` (`@openrouter/sdk`) and `HeuristicProvider`. A paid spillover to `thinkingmachines/inkling` exists behind a daily spend cap and is **off** by default.
- `config/llm.json` maps each route to a model chain and reasoning effort: `triage` low, `extract`/`vision` medium, `solve` high, `agent` medium, `reflect` high, `forge` high.
- **RequestBudget governor:** an RPM token bucket (18/min), and RPD from `/api/v1/key` refreshed every 15 min plus a local counter. Priority classes are P0 interactive · P1 academic · P2 briefings/reflection · P3 background. A strict priority queue serializes calls, because 20 RPM is shared by every concurrent job.
- **Structured output:** JSON Schema from `z.toJSONSchema()` goes into the prompt; the reply is extracted, run through `jsonrepair`, then parsed with zod. On failure it makes at most one repair request (budget permitting), then falls back to heuristics.
- A circuit breaker per provider opens on a platform 429 until `X-RateLimit-Reset`, and on 5xx or timeouts with exponential cooldown; half-open state sends a single probe.
- **Redactor:** classmates' names, phone numbers and emails become stable placeholders, reversible locally.
- **Cost guard:** `file` parts are refused unless the PDF engine is explicitly the free `cloudflare-ai`.
- `llm_calls` logs route, the model that actually served (fallback-aware), latency, tokens and outcome.

**Agent kernel.**
- Goals (`agent.goal` jobs) come from Parth's messages, Chronos, perception events, and the kernel itself (follow-ups are capped at 20/day and depth ≤ 3 to prevent runaway loops).
- **Plan-then-execute** for autonomous goals: one request returns an `ActionPlan` (tool steps + summary). The kernel validates every step against the registry and policy, executes deterministically, and allows at most one replan on failure. Interactive chat uses a standard tool loop with ≤ 4 model turns.
- Tool registry entries carry a zod input/output, `sideEffect: none | internal | external`, `idempotent` and `allowedTrust`. Static catalog: tasks, schedule, academic pipeline, hackathons, fitness, memory, GitHub, `notify_parth` (owner channels only), forge.
- Policy engine: `untrusted_content` goals cannot synthesize tools for new domains, cannot widen allowlists, and can reach only Parth's own channels.
- **Action ledger** `(run_id, step, idempotency_key UNIQUE, status, result)`: replays and restarts never duplicate side effects.
- **Working dialogue state:** a per-channel turn buffer plus an *entity focus stack* filled from tool results' `entityRefs`, so "it" or "that hackathon" resolves to the top entity of that type. This is deterministic, unlike v1's regex guessing.
- At L2, a heuristic intent router keeps common commands working (`/plan`, `/next`, "running late", "log 2 eggs").

**Tool Forge (self-synthesizing tools).**
- Meta-tool `create_and_register_tool({name, purpose, inputSchema, outputSchema, allowedDomains, code, tests})`. The generated code targets a small Tool SDK: `export default async (input, io) => …`, with `io.fetch` (GET only), `io.parseHtml` and `io.log`.
- **Verify:**
  1. Check the manifest with zod.
  2. Static gate: transpile via the TypeScript API; the AST check rejects `require`, dynamic `import`, `process`, `eval` and `Function`.
  3. Run in the `parthos/tool-runner` sandbox with `--network none`. `io.fetch` is a JSON-RPC call over stdio that the host executes only for allowlisted domains (GET, ≤ 2 MB, ≤ 10 calls per run).
  4. Tests and the output schema must pass.
  5. Write a versioned registry row, with source in `data/tools/<name>/<version>/`, and run the tool immediately for the goal that needed it.
- **Autonomy envelope:** a tool auto-activates when every domain it uses is on the profile allowlist (devfolio.co, unstop.com, kccemsr.edu.in, mu.ac.in, github.com…). Otherwise it waits for one tap of approval in `#operating-room`.
- **Self-healing:** 3 consecutive schema failures or empty results quarantine the tool, then it is re-synthesized with the failure context, re-verified and promoted as the next version.

## Deliverable 3 — Academic pre-computation pipeline (`academic/`, `documents/`, `sandbox/`)

Stages: **Discovered → Parsed → Solved → Formatted → Ready for Print → Printed → Turned In**, plus `needs_attention` (with a reason) and `superseded`.

1. **Intake:** blob + source context (group, sender role, caption).
2. **Extract locally:** `unpdf` text layer; `mammoth` for .docx. Pages with no text are rasterized (`unpdf` + `@napi-rs/canvas`) for Inkling vision. Offline fallback: `tesseract.js` in a worker thread.
3. **Understand (1 request):** zod `AcademicDoc` = subject, type (lab manual / assignment / question bank / notice / timetable / notes), experiment number, `problems[]`, deliverables (printed or handwritten), deadline hint, confidence. Heuristic fallback: "Experiment No.", "Aim:", subject regexes.
4. **Deadline:** `chrono-node` with an IST reference on the hints and message. If that fails, use the next practical slot from the timetable, then +5 days (v1's default). The source and confidence are stored, and alerts say "assumed Mon 13 Oct — reply to change".
5. **Solve (1 request):** code, tests, Aim/Theory/Algorithm/Conclusion and viva Q&A. It runs in the sandbox, with ≤ 2 repair requests fed with stderr; stdout and plots are captured. Languages: Python by default (scipy covers DSP), C/C++, SQL (sqlite), Octave for MATLAB-style labs. 8086 assembly cannot be executed, so it is included and marked unverified.
6. **Render:** `templates/typst/journal.typ`, with the KCCEMSR header and cover fields ported from v1's HTML cover, compiled in-process by `@myriaddreamin/typst-ts-node-compiler` → `data/bundles/<op-day>/<subject>-exp<n>.pdf`.
7. **Deliver:** an embed and the PDF in `#academic-pipeline`, plus a Telegram ping ("Your DSP Lab 4 report is solved, compiled, and queued for Monday's turn-in."). Governance books a review + print slot before the deadline. Buttons [Printed] [Turned in]; the wake briefing reminds about items due within a day.

Budget: about 3–5 requests per lab.

**Sandbox.** A `SandboxRunner` interface. `DockerRunner` spawns `docker run --rm --network none --read-only --tmpfs /work --memory 512m --cpus 1 --pids-limit 128 --cap-drop ALL --security-opt no-new-privileges --user 65534` with a hard wall-clock kill. Inputs are mounted read-only and outputs go to a per-run `/out`. Images (python, cpp, octave, tool-runner) are built by `npm run sandbox:build`.

## Deliverable 4 — Schedule rebalancing & anchor defense (`governance/`)

- **Inputs:** anchors and college timetable (`config/profile.json`); tasks (estimate, deadline, priority, energy deep/shallow, splittable, minimum chunk); learned optimism multipliers; the current plan; now.
- **Solver (pure function):** a 7-operating-day horizon. Free windows = day − hard anchors − college − 10-minute buffers. Tasks are ordered by `scoreTask()` (deadline slack × priority × energy fit); deep tasks prefer 23:00–04:30, and splittable tasks are chunked. It returns `Plan` + `unplaced` + `atRisk`.
- **Invariants (fast-check property tests):** no block touches dinner or sleep, no overlaps, buffers kept, nothing scheduled past its deadline, and identical input gives identical output. v1's invariant tests are ported.
- **Rebalancer:** re-solves from "now" when a task is added or changed, a block slips or is missed, a deadline appears, and at the 10:30 and 22:55 checkpoints. It diffs old vs new and notifies only when the next 3 hours or a deadline changes.
- **Overload policy:** it decides first, then informs, and never asks "what should I reschedule?". It defers low-priority and undated work first, flags at-risk deadlines, and never borrows from dinner or sleep, e.g. "Overcommitted 2h15m before Mon 10:00 — deferred X, Y; DSP Lab 4 at risk, starts 23:00 tonight."
- **Slips:** if a block passes its end + 5 min with no completion, Telegram shows [Done] [+15m] [Skip]. With no answer within 10 minutes, it assumes +15 min and rebalances.

## Deliverable 5 — Multi-channel dispatcher & self-healing telemetry (`dispatch/`, `telemetry/`)

- **Outbox:** `notifications(idempotency_key UNIQUE, room, severity, title, body, attachments, actions, deliver_after, status, attempts, …)`. Keys such as `macro:2026-10-08` make every alert exactly-once across restarts.
- **Delivery policy `decideDelivery()`:** during sleep, hold for the 10:30 wake digest (critical excepted); during dinner, hold non-critical items for the 22:55 briefing; during deep work, batch low-severity items into digests.
- **Rooms (auto-provisioned):** `#operating-room` (health, wake briefing, approvals, degradation level), `#schedule-planner`, `#academic-pipeline`, `#hackathon-radar`, `#macro-fitness`. Telegram mirrors high and critical items with buttons. Inbound on both is owner-only (allowed user IDs).
- **Adapters:** discord.js (embed colours ported from v1, attachments, buttons) and grammy, with per-channel rate limits and retry with backoff. If Discord is down, high and critical items go to Telegram.
- **Health registry** for whatsapp, discord, telegram, openrouter, sandbox, db, queue and each Chronos job, with debounced state-change alerts.
- **Watchdog:** requeues expired job leases, reconnects sockets, warns when event-loop delay p99 > 200 ms, and tracks disk usage. Daily `PRAGMA quick_check` and `VACUUM INTO data/backups/` (keep 7).
- **HTTP** (`node:http`): `GET /health`, `GET /status` (bearer token), `POST /ingest/fitness`.
- **Failure semantics:** an `unhandledRejection` is logged and raises a health alert while the process stays up. An `uncaughtException` flushes, exits(1), and systemd restarts within seconds. On boot, an unclean-shutdown flag posts "recovered from crash: …" to `#operating-room`.

## Memory & reflection (spec pillar C — `memory/`)

- **Episodic log:** `episodes` + an FTS5 index + local embeddings (`@huggingface/transformers`, all-MiniLM-L6-v2, 384-d, in a worker thread, costing zero API requests).
- **Retrieval:** BM25 ∪ cosine → reciprocal-rank fusion + recency decay + importance.
- **Facts:** `(subject, predicate, object, confidence, valid_from, valid_to)`; a contradiction closes the old interval.
- **Habits:** a per-category optimism multiplier with small-sample shrinkage ("underestimates DSP labs by 35%" is reported only after ≥ 5 samples), velocity by operating-day hour, meal timing, slip rate.
- **6-hourly consolidation** (no LLM): dedupe, habit updates, decay.
- **Nightly reflection at 05:35 IST:** deterministic metrics plus 1 request yield a friction narrative, new facts and tuning proposals. Tuning is limited to whitelisted knobs with bounded deltas (buffer 5–20 min, multiplier 0.7–2.0, alert times ±30 min); the changes are applied and summarized in the wake briefing.

## Data model (`db/migrations/NNNN_*.sql` + `schema_migrations`)

core: `events`, `jobs`, `chronos_runs`, `kv` · governance: `tasks`, `schedule_blocks`, `slips` · academic: `blobs`, `academic_documents`, `academic_problems`, `submissions`, `artifacts` · perception: `messages_raw` (30-day TTL), `hackathons`, `github_daily`, `nutrition_logs`, `workout_logs`, `email_seen` · agent: `llm_calls`, `agent_runs`, `actions`, `tool_registry`, `tool_runs` · memory: `episodes`, `episodes_fts`, `embeddings`, `facts`, `habit_stats`, `reflections` · ops: `notifications`, `component_health`.

## Reused from v1 (`C:\Users\Parth\Desktop\bot\src\…`)

| v1 source | v2 destination | Change |
|---|---|---|
| `services/whatsappService.ts` (read-only guard, `extractWhatsAppMessageText`, group-subject cache, `restoreCloudWhatsAppAuth`) | `perception/whatsapp/*` | denylist → allowlist proxy; add media download; emit events instead of direct calls |
| `services/academicSemanticClassifier.ts` (subject regexes, commercial/banter filters, prompt) | `perception/triage/heuristics.ts`, `llm/providers/heuristic.ts` | becomes the heuristic triage provider |
| `services/contentDeduplicationService.ts` | `perception/triage/dedup.ts` | + blob sha256 |
| `services/googleClassroomService.ts` `parseIcsFeed` | `perception/classroom/ics.ts` | as-is + tests |
| `services/printBundlerService.ts` cover fields | `templates/typst/cover.typ` | HTML → Typst |
| `services/discordService.ts` categories, colours, channel hints | `dispatch/discord.ts` | renamed to the spec's rooms |
| `planner/intervalScheduler.ts`, `replanEngine.ts`, `tests/schedulerMathInvariants.test.ts` | `governance/*`, `tests/property/` | rewritten on operating-day minutes; invariants become property tests |
| `services/fitnessService.ts` presets · `hackathonRepository.ts` seeds · `integrations/whatsappAuth.ts` | `perception/fitness`, `perception/hackathons`, `scripts/whatsapp-link.ts` | ported |

## Deviations from the spec (and why)

| Spec | Plan | Reason |
|---|---|---|
| Reflection at 04:00 | 05:35 IST | 04:00 falls inside the deep-work sprint (it would miss the last 30 min, when the daily commit often lands) and at the tail of the free-quota day. 05:35 is after both the operating-day close and the 05:30 quota reset. |
| 6 h "deep reflection" | 6 h consolidation without the LLM + nightly LLM reflection | spends zero requests four times a day |
| Fully unattended tool synthesis | automatic inside an allowlisted-domain envelope; one tap for new domains | a WhatsApp message must never be able to make the agent fetch from an attacker's domain |
| Discovered → Solved → Formatted → Ready → Turned In | adds Parsed and Printed | v1 lesson: a finished PDF is not a submission |
| MATLAB solutions | Octave / Python | no MATLAB licence inside the sandbox |
| "auto-populates registration drafts" | drafts only, never submitted | forms carry personal data and commitments |
| 10 s ambient cycle | local checks only, no LLM | 20 req/min ceiling |

## Milestones (each one ends with typecheck + lint + tests green)

| # | Milestone | Scope | Exit criteria |
|---|---|---|---|
| M0 | Foundation | `git init`, scaffold (ESM, TS 6.0.3, typed ESLint, Vitest 5 + fast-check), env + `profile.json`, Clock/OperatingDay, SQLite + migrations, event bus, job queue, Chronos, logger, health, `/health`, lifecycle | property tests for operating-day math; queue lease/retry/idempotency; Chronos misfire; daemon boots locally |
| M1 | Governance | anchors, solver, rebalancer, overload, slips | invariant property tests (including the ported v1 suite) |
| M2 | Dispatch + telemetry | outbox + policy, Discord/Telegram adapters, room provisioning, `/plan` `/next` `/status`, anchor alert jobs (heuristic content), watchdog, backups, crash notice | fake-clock day simulation: each alert fires once at the right time and holds are respected; live smoke on a test Discord server |
| M3 | LLM gateway + kernel | OpenRouter provider, budget governor, structured output, redactor, breakers, heuristic provider, degradation ladder, kernel (plan-execute + chat), registry/policy/ledger, dialogue state, static tools | chaos tests (platform and upstream 429, 402, timeouts, malformed JSON) never throw to callers and land on the right ladder level; live smoke of 1–2 requests |
| M4 | Perception | WhatsApp (allowlist proxy, media, link script), triage + dedup, IMAP, Classroom, GitHub, hackathons, fitness ingest | fixture tests from recorded Baileys messages (text, document, image); read-only proxy test; live: link WhatsApp and see a notice land in `#academic-pipeline` |
| M5 | Academic pipeline | blob store, extraction, understanding, deadlines, sandbox + images, solver loop, Typst render, delivery, stage machine | fixture lab-manual PDF → journal PDF end-to-end (scripted LLM; real sandbox when Docker is up); live demo |
| M6 | Memory + reflection | episodic/FTS/embeddings, hybrid retrieval, facts, habits, consolidation, nightly reflection, wake briefing | retrieval tests on fixtures; estimator tests; reflection changes only bounded knobs |
| M7 | Tool Forge | Tool SDK + stdio RPC harness, tool-runner image, static gate, verification, registry, envelope + approvals, self-heal | a scripted forge builds a scraper against a local fixture site; hostile tools (network access, fs escape, foreign domain, infinite loop) are all blocked |
| M8 | Deploy | `deploy/` (systemd unit + rootless Docker for sandboxes; compose as an alternative), `npm run doctor`, RUNBOOK, VPS install | 48 h soak on the VPS with `/status` green |

The VPS part of M8 can move up to right after M4 if you want WhatsApp perception running 24/7 sooner. I'll commit only when you ask (suggestion: one commit per milestone).

**Learn-by-Doing checkpoints** (I scaffold everything around them; each is 5–10 lines of yours):
- M1 `scoreTask()` — how deadline slack, priority and energy fit trade off.
- M2 `decideDelivery()` — the anchor-aware quiet-hours policy.
- M3 `allowRequest()` — how the free daily budget is split across priorities.
- M6 `estimateOptimism()` — the small-sample shrinkage estimator.

## Verification

```
npm run typecheck      # tsc --noEmit (strict)
npm run lint           # typed ESLint: no-floating-promises, no-misused-promises, switch-exhaustiveness-check
npm test               # vitest: unit + property + integration (fake clock, fake adapters, scripted LLM)
npm run test:sandbox   # Docker-gated sandbox + Forge tests
npm run doctor         # env, Docker + images, Discord rooms, Telegram, OpenRouter key + remaining quota, DB integrity
```
- **End-to-end operating-day simulation** (fake clock, 10:30 → 10:30):
  - A PDF arrives at 14:00 and the bundle is delivered.
  - The 20:30, 21:25, 22:55 and 02:00 jobs each fire exactly once.
  - Non-critical alerts are held through dinner and sleep and released at 22:55 and 10:30.
  - Reflection runs at 05:35.
  - A restart in the middle of the day duplicates nothing.
- Vitest fails the run on any unhandled rejection; coverage thresholds apply to the core modules.
- **Live:** a test Discord server + Telegram bot; a sample lab manual sent to a WhatsApp self-chat, watched through every stage.

## What you'll provide, per milestone

- **M1/M5:** timetable, subjects, roll number and semester for `profile.json`.
- **M2:** a Discord bot token and server; a **new** Telegram bot token + your numeric user ID (two pollers on v1's token would hit 409 conflicts).
- **M3:** an OpenRouter key. Decide whether to buy 10 credits once: it lifts free requests from 50 to 1,000/day and is strongly recommended for reliability. Set the free-model training preference.
- **M4:** scan the WhatsApp QR (new linked device); a GitHub token; college IMAP app password; Classroom iCal URL.
- **M5:** Docker Desktop running locally for the sandbox tests.
- **M8:** VPS SSH access (Oracle Always-Free ARM works; `doctor` verifies the arm64 native builds).

## Risks

- Free models can leave the free tier. Routes are config-only, and the heuristic engine is the floor.
- Baileys 7 is a release candidate and WhatsApp's API is unofficial. Read-only mode, no presence updates, `legacy` 6.7.24 as fallback; ban risk can be reduced but not eliminated.
- Devfolio/Unstop endpoints can change. Forge self-healing plus heuristic parsers.
- Docker access is root-equivalent. Rootless Docker on the VPS.
- Free-model solutions can be wrong. Every solution is verified by execution in the sandbox, unexecutable parts are labelled unverified, and the viva sheet pushes for understanding before turn-in.
