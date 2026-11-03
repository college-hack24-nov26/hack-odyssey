# Plan: an AI planner that decides, and a realistic-looking simulator

(Replaces the finished Goal-tab plan. Latest commit `0756b60`; 406 tests pass. Nothing is changed until you approve.)

## 1. Context

**What you asked for (2 things).**
1. **Stop the rigid planning page.** Today the Goal tab matches a sentence against three fixed jobs, asks three fixed questions, and checks fixed formulas. You want a language model (OpenRouter free API) to *decide*: understand any job, think through the environment and edge cases in depth (storms, rough water, debris, failures), choose requirements, pick parts, and choose what to stress-test, and to ask only the questions that matter.
2. **Rework the visuals from scratch.** The boat is "two blocks on flat blue water". You want machines that look like the real thing, in a believable environment.

**Your choices.** API key goes in a **settings box in the dashboard**; the planner uses **Thinking Machines Inkling (975B, `thinkingmachines/inkling:free`)**; visuals start from **real designs I know**, refined later with your photos.

**Facts that shape the design (researched).**
- OpenRouter `:free` models: **20 requests/min and 50 requests/day** (1,000/day after buying $10 of credit). The 50 is shared across all free models and failed attempts count. So **a plan must cost at most 3 calls**.
- **Inkling is slow: about 18 tokens/s** (p50 latency ~4 s to first output). A 3,000-token answer takes ~3 minutes. So calls must be **streamed with live progress**, compact, and cancellable.
- Free-model availability changes; some free endpoints **log prompts**. We send only your job text, your answers and our public parts catalogue. The settings box says so.
- Structured JSON output is not guaranteed on every free model: validate everything, repair once, never trust.
- Today's viewer is three.js r128 (one file, no addons, offline). Core has Extrude/Lathe/Tube/ShaderMaterial/PMREM, so realistic models and water need **no downloads**.
- The Lab never sets hazards, so waves never appear in live runs; the viewer draws every geom as a flat-shaded primitive.

## 2. Principle: the model proposes, the simulator checks

The model never gets the last word. Every claim it makes is **validated by code**: parts must exist in the library, builds go through the Builder's evaluator, numbers are clamped to what the plants support, requirements are computed by the existing physics, and anything the simulator cannot model is written into the report's **"not tested"** list instead of being pretended. The report shows two clearly separated things: *what the AI decided and why* and *what the simulator verified*.

## 3. Part A: the AI planner

### 3.1 Flow (at most 3 model calls per plan)
```
 your sentence ─► [call 1: understand + plan] ─► validate ─┬─ needs answers ─► you answer ─► (call 1 again, with answers)
                                                           └─ ok ─► evaluate designs ─┬─ all fine ─► show
                                                                                      └─ problems ─► [call 2: revise with the evaluator's findings] ─► evaluate ─► show
                                                           (any failure/limit ─► the existing offline planner completes the job)
 after the stress test ─► [call 3, optional: plain-language commentary on the measured results, labelled "AI commentary"]
```

### 3.2 What the model returns (JSON, validated)
- `understanding`: summary, platform (`ground` | `air` | `water` | `unsupported`) and why, assumptions.
- `questions` (at most 4, only ones that would change the design): text, why it matters, choices or a number with unit and default.
- `scenarios`: the in-depth brainstorm, each with *why it matters*, severity levels, and which simulator hazard it maps to, or `null`. Unmappable ones go to "not tested".
- `requirements`: each `{metric, op, value, unit, source: user|inferred, rationale}` where `metric` comes from a **closed vocabulary** (runtime_min, top_speed_mps, mass_kg, freeboard_m, wind_thrust_margin, twr, climb_deg, fit_pipe_d_m, flight_time_min, payload_kg, range_m, redundancy_count ...). A metric the code cannot compute is recorded as "stated, not checked".
- `task`: a preset name plus task parameters (validated against each `Param` range).
- `stress_plan`: hazards (knob, levels, which asked value to compare), and fault cases, from a **whitelist of knobs** with clamped ranges.
- `designs`: one to three complete builds with the reason for each part.

### 3.3 Safety net
- Schema check, then **one repair call** if the JSON is broken or a field is invalid.
- Unknown parts or invalid builds are dropped and logged; if none survive, the deterministic grid search in `design.py`, **seeded by the model's part choices**, finds a verified machine.
- User text is untrusted: the model's output is data, validated like any input, so "ignore your instructions" cannot add a tool, a knob or a part.
- The key is read only inside the server thread; never logged, never sent to the browser, never in an error message.
- **Offline fallback:** no key, no internet, rate limit, or a model error → the current keyword + fixed-question planner runs, and the screen says "offline planner".

### 3.4 Model handling
Default `thinkingmachines/inkling:free`, checked against OpenRouter's live `/api/v1/models` (free pricing, supported parameters). Fallback chain if it is busy or removed: `inkling-small:free`, `nvidia/nemotron-3-super-120b-a12b:free`, `google/gemma-4-31b-it:free`. Strict `json_schema` response format only when the model lists `structured_outputs`; otherwise JSON-in-prompt plus extraction. Streaming on, reasoning effort moderate, output budget capped. 429 → wait for `Retry-After` / exponential backoff. The settings box shows requests left today (`GET /api/v1/key`) and has a **Test** button and a model override.

### 3.5 New files
| File | Purpose |
|---|---|
| `simlab/llm/settings.py` | key + model in a private `config/llm.json` (git-ignored; `SIMLAB_LLM` overrides the path; `OPENROUTER_API_KEY` also read); never returns the key |
| `simlab/llm/client.py` | OpenRouter client: model list, chat, streaming, retry/backoff, usage, fallback chain, cancellation |
| `simlab/llm/prompts.py` | builds the system prompt from the **live** library, knobs, tasks, build schema (≈4–5k tokens) |
| `simlab/llm/schema.py` | JSON Schemas, validators, clamps, one-shot repair |
| `simlab/llm/metrics.py` | the metric registry (computes each requirement from evaluator facts) |
| `simlab/llm/planner.py` | the flow in 3.1, verification loop, fallback to `design.py` |
| `simlab/hazards/synth.py` | turns a validated `stress_plan` into a catalogue dict for `run_sweep` (adds `not_tested` and the statement in code) |

Changed: `goal_router.py` (`/api/goal/llm/{status,settings,test}`, `/api/goal/plan/{start,status,answer,stop}` as a streamed background job, like the sweep job), `goal.js` (reworked page, below), `design.py` (accept a plan's requirements and seed parts), `.gitignore`, `pyproject.toml`, docs.

### 3.6 The reworked Goal page
One free-text box ("describe the job, as much as you like"). Below it a **live planning panel**: phases (Understanding → Scenarios → Requirements → Candidates → Verifying → Revising) with the streamed text; follow-up questions appear inline only if the model needs them; a **Scenarios** card lists every situation the AI thought of with ✓ *simulated* or ✗ *not testable here*; the result card keeps the traffic light, checks, parts and buttons; a badge says *AI plan, verified by the simulator*. "Watch it work" and the stress test use the plan's task and stress plan.

## 4. Part B: realistic visuals

### 4.1 Architecture (from the viewer audit)
- **Composite visual models** in new `simlab/dashboard/static/models/` (`kit.js` helpers, `boat.js`, `rover.js`, `drone.js`, `person.js`), built from core three.js geometry and attached to the vehicle body; wheels and propellers spin from the real MuJoCo child bodies, so no new streaming.
- `Plant.visual()` (new hook) returns the model name and real dimensions (hull length/width/draft, wheel radii, rotor positions, camera mount, lamp); `Plant.env()` returns wave and wind parameters; `scene()` adds `visual`, `vehicle_bodies` and a geom `group`, so the old DECOR boxes are hidden and the collision box stays for physics.
- **`env.js`**: gradient sky dome with sun glow, matching fog, an environment map for reflections, tone mapping, one soft shadow light, and an **animated water surface** whose vertex shader reproduces the plant's five wave cosines exactly (same height, direction, phases, clock), so the picture and the physics agree and the boat rides the visible waves.
- Boat gets a spinning **propeller body and a rudder body** (hinge joints, as wheels already do).
- Lab gets a **hazards control** (wave height, wind, current) wired through `runner.start(hazards)`, so waves finally appear in live runs.
- Viewer pauses when hidden; one merged mesh per material; ≤150 draw calls per vehicle.

### 4.2 The models (generic designs, no brand copies)
- **Rescue boat:** shaped plan-view hull (pointed bow, V-bottom, flared sheer line), orange hull with white deck, rub-rail, grab handles, small console with windscreen, mast with a navigation light, GPS dome and antenna, stern outboard thruster with spinning propeller and a rudder blade, a lifebuoy ring on the bow, and a wake foam strip.
- **Pipe-crawler rover:** low body shell, two rubber track units (or four tyres with tread and rim spokes, spinning), a camera dome on a stalk with a lens, a ring of LEDs/lamp with a light cone, an internal PCB with a visible ESP32 board and a battery pack (visible in the Builder's ghost view), a tether eyelet.
- **Drone:** carbon X-frame (plates and tube arms), motor bells, two- or three-blade propellers that spin with thrust, battery strap, flight-controller stack, camera on a mount, landing legs, small LEDs; hex variant for the F550 frame.
- **Targets:** a person in a life jacket and a debris box for the rescue task; textured pipe walls with the cracks and leaks visible (the viewer ignores textures today).
- **Builder preview:** same models in a see-through "ghost" mode with the chosen parts inside (water builds finally get a thruster).
- **Onboard camera (MuJoCo):** fog, a richer sky and a noisy water texture, so the camera view matches.

## 5. Milestones (one commit each; full suite green at every gate)

| # | What | Needs your key? |
|---|---|---|
| L0 | `simlab/llm`: settings (private key file, never exposed), client (models, chat, stream, retry, fallback), status/test endpoints, tests with a fake server | no |
| L1 | prompts, JSON schemas, validators/clamps/repair, metric registry, `hazards/synth.py` | no |
| L2 | planner flow + verification loop + fallback to `design.py` + offline fallback | no (mocked) |
| V0 | `env.js` sky, fog, tone mapping, shadows, **animated water** matching the physics; hazards control in the Lab | no |
| V1 | boat model + propeller/rudder bodies + `Plant.visual/env` + scene JSON | no |
| V2 | rover model, drone model, person/debris, textured pipes | no |
| L3 | reworked Goal page (live planning panel, adaptive questions, scenarios card, AI/verified labels) | mocked; **live check with your key** |
| V3 | Builder ghost preview with parts inside; thruster for water builds | no |
| V4 | onboard-camera fog/sky/water; performance pass | no |
| D | docs (`GOAL.md`, a new `PLANNER.md`), `PROJECT_BRIEF` update, full regression, scenario sweep | live run |

## 6. Verification
1. `pytest -q` at every gate (406 now). New tests use a **fake OpenRouter**: valid plan; broken JSON repaired once; invented parts dropped; out-of-range hazards clamped; unsupported platform answered honestly; a prompt-injection sentence changes nothing; 429 handled with backoff and the offline planner takes over; the key never appears in any response, log line or error; at most 3 calls per plan.
2. Live with **your key** (entered in the settings box; I never see or store it in the repo): three ordinary jobs plus odd ones ("map an underwater cave" must come back *unsupported*, "a rover for a farm field" must still produce an honest answer); check requests used against the 50/day counter.
3. Visuals in the browser pane: screenshots of boat, rover and drone; waves in the picture visibly match the wave height set in the Lab; frame rate stays smooth; the Builder ghost view shows the parts; the old Lab flows still work.
4. Webots smoke and the existing tabs unaffected.

## 7. Honest limits and risks
- **Quality and speed of a free model.** Inkling can be slow (minutes) and busy; 50 calls/day means about 16–25 plans per day. Mitigations: streaming, compact outputs, caching of identical requests, the offline planner, optional $10 credit for 1,000/day.
- **The AI can only choose among what the simulator can test.** It will think of hazards we cannot simulate (breaking waves, corrosion, rain on lenses); those land in "not tested", never in a pass.
- **Free endpoints may log prompts.** Only your job text and public part data are sent; the settings box states this.
- **Visuals are procedural approximations of typical designs, not CAD of a product.** They will look far better than blocks and move correctly, but not like a photograph. Photos from you refine them.
- Water waves match physics for the boat only; the drone's and rover's physics are unchanged. The planner still cannot design machines the parts library lacks.
