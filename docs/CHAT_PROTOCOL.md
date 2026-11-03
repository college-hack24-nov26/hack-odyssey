# Chat protocol (milestone C1): the contract the chat page is built from

The chat page (`/chat`) is a plain-JS page. It sends sentences and button presses to the server, and the server answers with a stream of small **events** that add up to messages, cards, chips and a dock. Everything the page draws comes from these events; the only other things it calls are the live 3D view's existing endpoints (`/api/lab/scene`, `/api/lab/state`, `/api/lab/video`, `/ws/lab`).

This file is exact. If the code and this file disagree, the code in `simlab/chat/` and `simlab/dashboard/chat_router.py` is right and this file has a bug.

Contents: 1 Transport and endpoints, 2 Events, 3 Messages, 4 Cards, 5 Actions and chips, 6 Test cases, 7 Results matrix, 8 Draft vs AI, 9 Dock, limits, budget, jobs, 10 Errors, 11 Resume rules, 12 Honesty rules the page must keep, 13 A worked log (flood boat, offline draft).

All JSON. All times are Unix seconds (floats). All ids are strings.

---

## 1. Transport and endpoints

Transport is **HTTP long-poll**, not SSE and not WebSocket. A turn is two calls: `POST .../send` (returns at once, 202) and an open `GET .../events` that returns the moment something new exists.

| Method and path | Purpose |
|---|---|
| `GET /chat` | The page (`simlab/dashboard/static/chat.html`). |
| `GET /api/chat/capabilities` | What the page may offer (section 1.1). |
| `POST /api/chat/new` | Make an empty chat: `{ "id": "<12 hex>", "seq": 4, "title": "New chat" }`. |
| `GET /api/chat/latest` | The chat to open on page load: `{ "id": null }`, or `{ "id", "title", "seq", "events", "last", "more" }` (the same body as `events?after=0`). |
| `GET /api/chat/list` | The sidebar's list, read-only: `{ "chats": [{ "id", "title", "updated", "seq" }] }`, newest first (`?limit=` 1 to 500, default 100). No events are loaded. |
| `GET /api/chat/{id}/events?after=<seq>&wait=<seconds>` | Long-poll (section 1.2). |
| `POST /api/chat/{id}/send` | Send a sentence or an action. Returns **202** (section 1.3). |
| `GET /api/chat/{id}/state` | A snapshot for the Details sheet and for a fast resume (section 1.4). |

Chat ids are exactly 12 lowercase hex characters. Any other id, or an unknown one, is `404 {"detail": "no such chat"}`.

`/chat` and `/api/chat/*` are registered before the catch-all `/api/{kind}/...` and `/ws/{kind}` routes, so nothing shadows them.

### 1.1 `GET /api/chat/capabilities`

```json
{
  "ai": { "configured": false, "enabled": false, "setting": "auto", "model": null, "used_today": 0, "left": null, "limit": 50,
          "approx": true, "low": false, "exhausted": false, "reset": "Resets at 00:00 UTC", "reason": "no key saved", "error": null },
  "mujoco": true,
  "live":  { "busy": false, "holder": null, "queue": [], "external": false },
  "plan":  { "busy": false, "holder": null, "queue": [] },
  "sweep": { "busy": false, "holder": null, "queue": [] },
  "hardware": { "link": "/#hw" },
  "platforms": {
    "water": {
      "label": "Boat", "observation_only": false, "threshold": 0.75, "presets": ["water_rescue"],
      "not_tested": ["Rain, fog, glare and darkness (...)"],
      "hazards": [ { "id": "waves", "title": "Waves", "unit": "m", "range": [0.0, 1.5], "levels": [0, 0.15, 0.3, 0.45, 0.6, 0.9],
                     "knob": "wave_hs", "live": true, "plain": "How tall the waves are ...", "descending": false } ],
      "faults": [ { "id": "thruster_out", "title": "A thruster fails", "plain": "..." } ],
      "params": [ { "key": "range_m", "label": "How far out", "unit": "m", "min": 5.0, "max": 5000.0 } ]
    },
    "ground": { "...": "same shape" }, "air": { "observation_only": true, "...": "same shape" }
  },
  "kits": [ { "id": "my_rover", "label": "My ESP32 rover kit (...)" } ],
  "examples": [ { "id": "flood_boat", "label": "Boat that finds a person in a flooded street", "text": "A boat that finds a person in a flooded street" },
                { "id": "own_parts", "label": "I have my own parts", "kit": "my_rover" } ],
  "copy": { "headline": "What do you want to build and test?", "line": "...", "placeholder": "e.g. a boat that reaches a person 150 m away in 1 m waves", "limits": "..." },
  "action_types": ["new_job", "edit", "..."],
  "card_types": ["understanding", "design", "watch", "tests", "results", "parts", "improve", "limits", "answer", "blocked"]
}
```

`ai.enabled` is true only when a key is saved, the setting is not `"never"` and today's requests are not used up. `ai.left` is approximate (the quota is shared by every app using the key) and is `null` when no key is saved. `live.external` is true when the Lab tab (not the chat) is running the simulation.

### 1.2 `GET /api/chat/{id}/events?after=<seq>&wait=<s>`

- `after`: integer >= 0, the `seq` of the last event the page has (0 for none).
- `wait`: seconds to hold the request open when there is nothing newer, 0 to 25 (values above 25 are rejected with 422).

Response:

```json
{ "events": [ { "seq": 5, "ts": 1793696171.74, "type": "message", "...": "..." } ], "last": 5, "more": false }
```

- Returns **as soon as** any event with `seq > after` exists. Otherwise returns `{ "events": [], "last": <after>, "more": false }` after `wait` seconds. Ask again at once.
- `events` are in `seq` order, with no gaps and no repeats between consecutive calls that pass the previous `last` as `after`.
- A page holds at most 500 events. `"more": true` means the page was cut: call again at once with `after=last` and `wait=0`.
- `"reset": true` (with an empty `events`) means `after` is ahead of this chat, which happens only if the server lost the log. Drop all local state and start again from `after=0`.
- The browser keeps one of these requests open per page, plus short fetches. The long-poll never carries telemetry; the live view stays on `/ws/lab`.

### 1.3 `POST /api/chat/{id}/send`

Body (one of `text` or `action` is required; `text` wins if both are given):

```json
{ "text": "make the waves bigger", "client_msg_id": "c-81f2", "label": null }
{ "action": { "type": "run_tests", "args": { "cases": "all" } }, "client_msg_id": "c-81f3", "label": "Run all tests" }
```

- `client_msg_id`: any string up to 64 characters, chosen by the page. Sending the same id again does **not** run the turn twice; the answer has `"duplicate": true`.
- `label`: the text to show as the user's own message for an action (a chip's label). Without it the transcript shows the action type.
- `text` is at most 2000 characters (422 above that).

Answer, **HTTP 202**:

```json
{ "turn_id": "t2", "accepted": true, "message_id": "m4", "seq": 29,
  "intent": { "kind": "edit", "source": "grammar", "confidence": 0.9, "read_as": "" } }
```

`intent.kind` is what the parser decided (`edit`, `watch`, `run_tests`, `sheet`, `undo`, `redo`, `stop`, `question`, `hardware`, `train`, `help`, `new_job`, `confirm_new`, `new_chat`, `ambiguous`, `unclear`, `blocked`, plus any action type); `source` is `action`, `command`, `grammar`, `question`, `verb`, `new_job` or `fallback`. For `new_chat` the answer also has `"new_chat": "<id of the new chat>"`: switch to it. The result of the turn is **only** in the events.

Turns of one chat are processed in order. `send` never waits for the work.

### 1.4 `GET /api/chat/{id}/state`

The whole current state for the Details sheet:

```json
{
  "id": "…", "title": "…", "seq": 135, "created": 0, "updated": 0, "phase": "ready",
  "ai": { "state": "done", "seconds": 142, "...": "the AI step's fields, without the raw plan" },
  "budget": { "…": "same as capabilities.ai" },
  "resources": { "plan": { "busy": false, "holder": null, "queue": [] }, "sweep": { "…": "…" }, "live": { "…": "…", "external": false } },
  "limits": { "count": 8, "stale": false, "line": "Simulation only, with a simple built-in autopilot. 8 things not tested here." },
  "view": { "messages": [ "…" ], "cards": [ "…every card, current…" ], "chips": [ "…" ], "dock": { "…": "…" } },
  "platform": "water",
  "job": { "text": "…", "summary": "…", "goal": "water_rescue", "source": "draft", "kit": null },
  "plan": { "understanding": { "summary": "…", "platform": "water", "why": "…", "assumptions": [] }, "params": { "range_m": 150, "wave_hs": 1 },
            "touched_params": [], "task": { "preset": "water_rescue", "params": { "range_m": 150 } },
            "stress": { "hazards": [ { "id": "waves", "levels": [0, 0.3], "ask": null } ], "faults": ["thruster_out"] }, "source": "catalogue", "issues": [] },
  "cases": [ "…test cases, section 6…" ],
  "summary": { "tests": 7, "conditions": 24, "tries": 48, "not_tested": 8, "untestable": 0, "stale": 0, "with_results": 7 },
  "matrix": { "…": "results matrix of all tests, section 7" },
  "not_tested": [ "every sentence of what was not tested" ],
  "statement": "Simulator results with a simple built-in controller. …",
  "limit_line": "Simulation only, with a simple built-in autopilot. 8 things not tested here.",
  "proposals": [ "…designs, section 4.2…" ], "pick": 0, "design": { "scanned": 12, "meets_goal": false, "note": "…", "seconds": 0.27 },
  "results": [ { "id": "r1", "card": "results-1", "state": "done", "case_ids": ["tc_waves"], "headline": "…", "weakest": { "case_id": "tc_f_rudder_stuck", "level": null, "title": "…", "why": "…", "fix": "…" },
                "met": 18, "total": 24, "below": 6, "not_run": 0, "started": 0, "seconds": 54.4, "report": "/reports/…/report.html" } ],
  "runs": [ "…watched runs, section 4.3…" ],
  "sheet": { "…": "the build sheet as JSON (goals.assembly.make_sheet), or null before a design exists" },
  "history": [ { "label": "Waves top level 1 to 1.5 m", "ops": [ { "op": "set_levels", "case": "tc_waves", "levels": [0, 1.5] } ] } ],
  "cursor": 1, "can_undo": true, "can_redo": false, "replay_failures": []
}
```

Before a job exists, `phase` is `"empty"` and `cases`, `proposals`, `results` are empty, `plan` is null, `sheet` is null.

`view` plus `seq` are taken under one lock: rendering `view` and then polling `events?after=<seq>` has no gap and no repeat. This is the fast way to resume a long chat (section 11).

---

## 2. Events

Every event has `seq` (1, 2, 3 ... with no gaps), `ts` and `type`. These are the only types (`CARD_TYPES` and `EVENT_TYPES` in `simlab/chat/chat.py`):

| `type` | Extra fields | Meaning |
|---|---|---|
| `message` | the message fields, section 3 | A line in the transcript. |
| `card.create` | `card`: the whole card | A card appears. If a card with that `id` exists it is replaced whole. |
| `card.patch` | `id`, `patch` | Change an existing card (JSON merge patch, below). |
| `chips` | `chips`: array of chips | **Replaces** the suggestion chips shown above the composer. An empty array clears them. |
| `dock` | the dock fields, section 9 | Replaces the dock (step tracker, AI timer, queue). |
| `job` | `job`: `{id, kind, state, label, done, total, position?}` | Progress of one background job (a test run). Replaces any earlier `job` with the same `id`. |
| `budget` | `ai`: same as `capabilities.ai` | The AI requests counter. |
| `limits` | `count`, `stale`, `line` | The Limits strip. `count` is null before a job exists. |
| `error` | `code`, `message`, `card?` | Something failed (section 10). |

### 2.1 JSON merge patch (`card.patch`)

RFC 7386. For each key in `patch`:

- value is an object and the card's value is an object: merge recursively;
- value is `null`: delete the key;
- anything else, **arrays included**: replace the whole value.

So `{ "progress": { "done": 8 } }` changes one number, and `{ "rows": [ … ] }` replaces the whole rows array. A client applies a patch with:

```js
function mergePatch(target, patch) {
  if (patch === null || typeof patch !== "object" || Array.isArray(patch)) return patch;
  const out = (target && typeof target === "object" && !Array.isArray(target)) ? { ...target } : {};
  for (const [k, v] of Object.entries(patch)) { if (v === null) delete out[k]; else out[k] = mergePatch(out[k], v); }
  return out;
}
```

Replaying all events from `seq` 1 in order rebuilds exactly the page; the server's own reload does the same (`simlab/chat/chat.py: View`).

---

## 3. Messages

`{"type":"message", "id":"m5", "role":"user"|"assistant", "kind":…, "text":"…", …}`

| `kind` | When |
|---|---|
| `text` | What the person typed (role `user`). |
| `action` | A button or chip press (role `user`; `text` is the `label` the page sent, or the action type). |
| `say` | A plain assistant sentence. |
| `note` | A secondary assistant sentence (a clamp note, an undo confirmation). Render it smaller. |
| `read_as` | "I read this as: …" after an edit. It carries `undo` (`{type:"undo", args:{}}`, the action the Undo chip sends) and `ops` (the operations that were applied, section 5.3). |

Optional fields: `turn_id`, `client_msg_id`, `intent` (user messages: the full parse, `{kind, source, confidence, read_as, action, ops, topic, issues}`), `link: { "chat": "<id>" }` (an assistant line that points at another chat).

Every assistant sentence comes from `simlab/chat/words.py`. The page writes none of its own assistant sentences.

---

## 4. Cards

A card is an object with at least:

```json
{ "id": "design", "type": "design", "title": "Design", "state": "ready", "summary": "One line the collapsed row shows." }
```

Everything else is per type. Rule: **a card carries everything the page needs to draw it**; the page calls no other API to fill a card, except the live 3D view of the `watch` card.

Card ids: `understanding`, `design`, `watch`, `tests`, `limits`, `parts`, `improve` are single (one per chat, patched in place). `results-<n>` (one per test run, n = 1, 2, ...), `answer-<n>` and `blocked-<n>` are new cards each time. `type` is one of the 10 card types; ids of the multiple kinds start with their type.

`actions` on a card are buttons: `{ "type", "label", "args", "enabled", "reason", "primary" }`. A disabled one has a `reason` to show. Pressing one sends `{type, args}` as an action (section 5).

### 4.1 `understanding` ("What I understood")

Created within a second of a new-job message. Patched when a job number changes (the whole card is rebuilt, so expect `numbers`, `assumptions` and `requirements` patches), and when the design check finishes (`requirements`).

```json
{ "id": "understanding", "type": "understanding", "title": "What I understood", "state": "ready",
  "summary": "Boat: A boat that finds a person in a flooded street 150 m away in 1 m waves",
  "platform": "water", "platform_label": "Boat", "job": "<the person's words>",
  "badge": { "source": "draft", "label": "Quick draft" },
  "numbers": [ { "key": "range_m", "label": "How far out", "value": 150.0, "unit": "m", "min": 5.0, "max": 5000.0, "touched": false, "stated": true,
                 "action": { "type": "set_job_number", "args": { "name": "range_m" } } } ],
  "requirements": [ "Stays out about 60 minutes", "Reaches 150 m and comes back" ],
  "assumptions": [ { "id": "a_minutes", "text": "Assumed: time out 60 min", "param": "minutes" },
                   { "id": "a_autopilot", "text": "Assumed: the simple built-in autopilot drives in every test", "param": null } ],
  "observation_only": false, "observation_note": null,
  "not_tested": [ { "id": "tc_n_rain", "title": "Rain", "reason": "Rain is not simulated here." } ],
  "actions": [] }
```

- `badge.source` is `"draft"` (offline quick draft) or `"ai"`; `badge.label` is `"Quick draft"` or `"AI version"`.
- A number chip sends `{type:"set_job_number", args:{name:<key>, value:<number>}}`; the page adds `value`. `touched` is true after the person edited it; `stated` is true when it came from their first message.
- Drone jobs have `observation_only: true` and `observation_note` is the line to show.

### 4.2 `design`

Created when the parts search finishes (about 0.3 s), patched when the Builder checks finish (`proposals`, `key_facts`), patched again when the see-through preview is ready (`preview`), and rebuilt when the person picks another design or a job number changes.

```json
{ "id": "design", "type": "design", "title": "Design", "state": "ready"|"unavailable", "summary": "Does not meet the goal yet.",
  "proposals": [ {
      "index": 0, "label": "Closest we found", "why": "", "from_ai": false,
      "verdict": { "light": "green"|"amber"|"red", "headline": "Does not meet the goal yet.", "reasons": ["..."] },
      "checks": [ { "id": "time", "text": "Stays out about 60 minutes", "ok": true, "detail": "about 125 min at full power" } ],
      "unmet": 1, "warns": 0, "task_ok": true,
      "parts": [ { "name": "hull_box_1m2", "have": false } ], "owned": [], "to_buy": ["hull_box_1m2"],
      "mass": 17.84, "runtime": 125.0, "build": { "...": "the Builder's build dict" }, "machine_sig": "c8ed860c526f", "pill": "Estimate"|"Calibrated",
      "facts": [ { "key": "draft", "label": "Draft (depth under water)", "value": 0.0297, "unit": "m", "lo": null, "hi": null, "note": "" } ],
      "key_facts": [ { "key": "runtime", "label": "Runtime at about 60 % speed", "value": 125.0, "unit": "min" } ],
      "findings": [ { "severity": "error"|"warn"|"info", "title": "…", "detail": "…", "scope": "task" } ],
      "assumptions": [ "…" ], "budget": { "mass": [], "current": {}, "voltage": {}, "chain": "…" }, "evaluated": true } ],
  "pick": 0, "verdict": { "…": "of the picked one" }, "headline": "…", "pill": "Estimate", "key_facts": [ "…of the picked one…" ],
  "what_this_means": "These numbers are predictions from datasheets; none were measured on a real build.",
  "scanned": 12, "seconds": 0.27, "meets_goal": false, "note": "Nothing in the parts library meets all of this. …",
  "observation_only": false, "observation_note": null,
  "preview": { "state": "pending" }
          | { "state": "ready", "scene": { "…": "same JSON as POST /api/build/preview.scene" }, "poses": [ [0,0,0,1,0,0,0] ] }
          | { "state": "unavailable", "reason": "Preview needs MuJoCo, …" },
  "actions": [ { "type": "pick_design", "label": "Closest we found", "args": { "index": 0 }, "enabled": true, "reason": null, "primary": true } ] }
```

- With no design found: `state:"unavailable"`, `proposals: []`, `preview.state:"unavailable"`.
- `actions` has one `pick_design` per proposal when there is more than one design, else is empty.
- The preview (`scene`, `poses`) is exactly what the Builder's ghost viewer takes: `viewer.load(scene)` then `viewer.setPoses(poses)` (same as `builder.js` does with `/api/build/preview`).
- `verdict.light` is the traffic light of the headline. `pill` is kept verbatim ("Estimate" or "Calibrated").

### 4.3 `watch` ("Watch it work")

Created when the design is ready (state `unavailable` without MuJoCo, `idle` or `running` otherwise). The live view itself is the existing Lab view: use `scene_url`, `state_url`, `ws`, `camera_url`.

```json
{ "id": "watch", "type": "watch", "title": "Watch it work", "state": "idle"|"unavailable"|"starting"|"running"|"finished"|"stopped"|"replaced"|"error"|"interrupted",
  "summary": "Simple built-in autopilot, calm conditions, try 1. This is a demonstration, not a test.",
  "runs": [ { "id": "w1", "n": 1, "label": "…", "source": "auto"|"user", "conditions": { "wave_hs": 1.5 }, "conditions_text": "waves 1.5 m",
              "state": "running"|"finished"|"stopped"|"replaced"|"error"|"interrupted", "started": 0, "finished": 0, "scene_id": "ab12cd34", "similar": true,
              "result": { "success": true, "t": 41.2, "crash": null, "timeout": false },
              "final": { "success": true, "t": 41.2, "crash": null, "timeout": false, "collisions": null, "battery": 0.8 }, "note": "…" } ],
  "current": { "…": "the latest run" },
  "conditions": [ { "hazard": "waves", "knob": "wave_hs", "title": "Waves", "unit": "m", "min": 0.0, "max": 1.5, "mild": 0.0,
                   "levels": [0, 0.15, 0.3], "descending": false, "plain": "…" } ],
  "mujoco": true, "demo_note": "This is a demonstration, not a test.",
  "similar_note": "A similar run at this condition (same settings and try number), not the exact failed try.",
  "scene_url": "/api/lab/scene", "state_url": "/api/lab/state", "ws": "/ws/lab", "camera_url": "/api/lab/video",
  "actions": [ { "type": "watch", "label": "Run again", "args": {}, "enabled": true, "reason": null, "primary": false },
               { "type": "stop", "label": "Stop", "args": { "job": "live" }, "enabled": true, "reason": null, "primary": false } ] }
```

- The calm run starts by itself once, and only if MuJoCo exists and the live view is free; it never replaces a run that is going. Later runs start only on the person's request and **replace** a running one (the old run becomes `replaced`, with `note` "Replaced by a newer run.").
- The page freezes its viewer when the current run's `state` is not `running`, or when the live `scene_id` (from `/api/lab/state`) differs from `current.scene_id`.
- `conditions` are the tough-conditions sliders for this kind of machine; the page sends the chosen values in a `watch` action (`conditions: { "wave_hs": 0.9 }`). The server resolves the machine, the task and the units; **the page never sends a vehicle**. Faults and fixed obstacles are not watchable (a watch for one answers with a `blocked` card).
- Runs with `similar: true` are labelled with `similar_note`: they are a similar run, not a replay of a test's exact try.

### 4.4 `tests`

```json
{ "id": "tests", "type": "tests", "title": "Tests", "state": "ready"|"stale"|"running"|"empty", "summary": "7 tests, 24 conditions, 48 tries in all",
  "counts": { "tests": 7, "conditions": 24, "tries": 48, "not_tested": 8, "untestable": 0, "stale": 0, "with_results": 0 },
  "rows": [ "…test cases with cells, section 6…" ],
  "top": [ { "id": "tc_waves", "title": "Waves up to 1.5 m", "line": "0 to 1.5 m, 2 tries each" } ],
  "untestable": [ { "id": "tc_n_rain", "title": "Rain", "reason": "Rain is not simulated here." } ],
  "bar_text": "75% of tries succeed", "cost_line": "7 tests, 24 conditions, 48 tries in all", "threshold_owner": "code"|"user",
  "can_undo": true, "can_redo": false,
  "actions": [ { "type": "run_tests", "label": "Run all", "args": { "cases": "all" }, "enabled": true, "primary": true, "reason": null },
               { "type": "undo", "…": "…" }, { "type": "redo", "…": "…" }, { "type": "open_details", "label": "Open details", "args": { "tab": "tests" }, "…": "…" } ] }
```

`top` is the three rows the collapsed card lists. `rows` is the full table (untestable ones included, `status: "untestable"`). Show untestable rows in a hatched "Can't be tested here" section; they have no Run or Watch button and are never green.

### 4.5 `results-<n>`

One per test run. Created `queued` or `running`, patched once per finished condition, finished `done`, `stopped` or `error`.

```json
{ "id": "results-1", "type": "results", "title": "Results", "state": "queued"|"running"|"done"|"stopped"|"error"|"interrupted"|"cancelled",
  "summary": "Condition 9 of 24 | Met the bar in 18 of 24 simulated conditions with the simple built-in autopilot, 6 below the bar.",
  "run_id": "r1", "started": 0, "finished": null, "seconds": null,
  "progress": { "done": 8, "total": 24, "tries_done": 16, "tries_total": 48, "label": "wind 8", "stopping": false },
  "matrix": { "…": "section 7" },
  "headline": "", "weakest": { "case_id": "tc_f_rudder_stuck", "level": null, "title": "The rudder jams straight", "why": "…", "fix": "…" } | null,
  "bar_text": "75% of tries succeed", "statement": "Simulator results with a simple built-in controller. …",
  "not_tested": [ "Rain, fog, glare and darkness (…)" ], "limit_line": "Simulation only, with a simple built-in autopilot. 8 things not tested here.",
  "report": null | { "url": "/reports/<folder>/report.html", "urls": [ "…" ] },
  "stale": false, "case_ids": ["tc_waves"], "what_this_means": "A try is one run …", "next_note": "Next: watch the weakest test, make it tougher, or get the build sheet.",
  "chips": [], "actions": [ { "type": "stop", "label": "Stop", "args": { "job": "tests" }, "enabled": true }, { "type": "watch", "label": "Watch the weakest test", "args": { "weakest": true } },
                            { "type": "sheet", "label": "Get the build sheet", "args": { "format": "json" } } ] }
```

- `statement`, `not_tested` and `limit_line` are always present. They must be shown whenever the results are shown, and `not_tested` must not be collapsible.
- Pressing Stop sets `progress.stopping: true`: show "stopping after the current condition". The run then ends `stopped`; finished cells keep their results and the rest read `not_run`.
- `stale` becomes true when a test or the machine changed after the run. Dim the results; do not hide them. Nothing is re-run by itself.
- `weakest` is null until the run ends, and when nothing fell below the bar.
- `cancelled`: the run was waiting in the line ("Up next") and the person withdrew it before it started; `summary` says so and there are no actions.

### 4.6 `parts` ("Parts & build sheet")

```json
{ "id": "parts", "type": "parts", "title": "Parts & build sheet", "state": "ready", "summary": "The parts list has 6 parts; 6 to buy. …",
  "parts": [ { "name": "hull_box_1m2", "title": "1.2 m displacement hull", "kind": "hull", "qty": 1, "have": 0, "source": "…", "confidence": "assumed", "mass_g": 4000.0,
               "to_buy": 1, "have_all": false } ],
  "to_buy": ["hull_box_1m2"], "power_note": "12 V battery pack, about 125.033 min of running (an estimate)", "firmware": null,
  "wiring": { "generated": false, "tables": [], "note": "No wiring table for this machine; wire it manually from the parts' datasheets.", "reason": "…" },
  "steps": ["…"], "safety": ["…"], "caveat": "Numbers on this sheet are estimates from datasheets, not measurements …",
  "hardware_line": "Driving a real machine happens on the Hardware screen, with its checklist and STOP.",
  "rover": { "label": "Drive the real rover (leaves this chat)", "href": "/#hw", "enabled": false, "reason": "…", "leaves_chat": true },
  "export": null | { "url": "/reports/<folder>/sheet.html", "name": "<folder>" }, "observation_only": false,
  "actions": [ { "type": "open_details", "label": "Open build sheet", "args": { "tab": "parts" } }, { "type": "sheet", "label": "Export HTML", "args": { "format": "html" } } ] }
```

`wiring.tables` is non-empty only for the ESP32 + DRV8833 two-motor rover. The `rover` link is plain navigation (an `<a href>`), enabled only for that machine; the chat has no drive, arm or checklist action of any kind, and refuses those if sent (section 5).

### 4.7 `improve` ("Improve with AI")

Appears only when the AI planner runs for a new job. No sentence of the chat ever calls the model except a new job (when a key is saved and requests are left).

```json
{ "id": "improve", "type": "improve",
  "state": "queued"|"running"|"done"|"applied"|"kept"|"failed"|"unavailable"|"stopped"|"unsupported"|"interrupted",
  "summary": "Checking the designs against the physics",
  "phase": "Planning", "seconds": 37, "chars": 1450, "tail": "…the last 700 characters of what the model has written…", "model": "nvidia/…",
  "diff": null | { "…": "section 8" }, "reason": null, "closest": null, "draft_note": "Your draft stays usable meanwhile.", "requests_note": null, "calls": 1,
  "actions": [ { "type": "stop", "label": "Stop", "args": { "job": "plan" }, "primary": true, "enabled": true, "reason": null } ] }
```

- `running`: show an indeterminate bar, `seconds`, `chars`, the mono `tail`, `draft_note` and Stop. `seconds` and `tail` are patched about once a second.
- `done`: there is a `diff`; the three actions are `use_ai`, `keep_mine`, `use_ai_keep_edits`.
- `applied`: the AI plan is now the plan (`use_ai`, `use_ai_keep_edits`, or the first plan when no ready-made job matched). `kept`: the draft was kept (`keep_mine`, or the plans were identical).
- `unavailable` (no key, quota, network, unusable answer): `reason` has a scrubbed sentence, `closest` the closest ready-made job's title or null. `failed`: an error. `stopped`: the person stopped it; the note "Requests already sent still count today." is `requests_note`. `unsupported`: the AI says no machine here can do the job; `reason` says why.
- The AI never asks questions in the chat: `final=True` semantics, questions are dropped.

### 4.8 `limits` ("What I can't test")

```json
{ "id": "limits", "type": "limits", "title": "What I can't test", "state": "ready", "summary": "Here is what I cannot test. Everything I show is simulation only, with the simple built-in autopilot.",
  "items": [ { "text": "Rain is not simulated here.", "kind": "untestable"|"catalogue" } ], "count": 8, "statement": "Simulator results with …",
  "line": "Simulation only, with a simple built-in autopilot. 8 things not tested here.",
  "bullets": [ "Simulation only, with a simple built-in autopilot.", "Ground rovers, boats and observation-only drones.", "Simulates … does not show a machine works in the real world." ],
  "user_bars": [ "Waves up to 1.5 m: 60% of tries succeed (you set this)" ], "observation_only": false,
  "actions": [ { "type": "open_details", "label": "Back to my job", "args": { "tab": "tests" } } ] }
```

Opened by "what can't you test", by the Limits strip, or by an unsupported request.

### 4.9 `answer-<n>`

A template answer from stored results. No model.

```json
{ "id": "answer-1", "type": "answer", "title": "Answer", "state": "ready", "topic": "why_fail", "question": "why did it fail",
  "summary": "<text>", "text": "The rudder jams straight at this test: 1 of 2 tries succeeded; 1 ran out of time. …",
  "details": [ "try 1: did not reach the goal (timeout)", "try 2: reached the goal" ],
  "limit_line": "Simulation only, with a simple built-in autopilot. 8 things not tested here.", "actions": [] }
```

`topic` is one of `why_fail`, `what_means`, `cost`, `can_it`, `overview`. (`limits`, `hardware`, `train` and `help` answer with the `limits` card, the `parts` card, a `blocked` card with a link, or a message.)

### 4.10 `blocked` ("Can't do that here" / "Waiting")

```json
{ "id": "blocked-1", "type": "blocked", "title": "Can't do that here"|"Waiting", "state": "blocked"|"waiting", "summary": "<reason>", "reason": "<reason>",
  "nearest": null | { "label": "Try stronger waves instead", "action": { "type": "edit", "args": { "ops": [ … ] } } },
  "chips": [ { "id": "n", "label": "…", "action": { … }, "kind": "secondary", "enabled": true, "reason": null, "tag": null } ],
  "link": null | { "label": "Open Training (leaves this chat)", "href": "/#train", "leaves_chat": true },
  "ticket": null | { "id": "9f3a1c20", "position": 1, "cancel": { "type": "cancel_queued", "args": { "ticket": "9f3a1c20" } } },
  "actions": [] }
```

Used for: an unsupported ask (rain, fog, GPS ...: also recorded as an untestable test), watching something not watchable, no design yet, the live view busy, training (a link out), a refused action (hardware), a queued job (`state:"waiting"`, `ticket` set: draw "Up next, position N" with an x that sends `ticket.cancel`).

---

## 5. Actions and chips

### 5.1 Action

`{ "type": <string>, "args": { … } }`, sent as `POST .../send { action }`. Everything a chip or button does is an action; typing a sentence parses to the same actions. Unknown types and these hardware types are refused with a `blocked` card and never do anything: `arm`, `disarm`, `drive`, `checklist`, `hw`, `connect`, `disconnect`, `flash`, `estop`, `setup`, `calibrate`.

| `type` | `args` | Effect |
|---|---|---|
| `new_job` | `{ text, kit? }` | Start the job (only when the chat has no job; otherwise asks "Start a new chat for this?"). |
| `use_kit` | `{ kit }` | Design from the parts in a kit (`capabilities.kits[].id`). |
| `edit` | `{ ops: [operation] }` | Apply case operations (5.3) as one undoable step. |
| `add_test` | `{ hazard, levels?, ask? }` or `{ fault }` or `{ conditions: {knob: value}, title? }` or `{ untestable, reason? }` | Add a test. |
| `edit_test` | `{ case, levels?, ask?, tries?, bar? }` | Edit one test. |
| `remove_test` | `{ case }` | Remove a test. |
| `set_job_number` | `{ name, value }` | Change a job number (`understanding.numbers[].key`). Re-checks the design. |
| `set_task_param` | `{ name, value }` | Change the test course (`range_m`, `debris` for the boat). |
| `set_tries` | `{ case?: null, n }` | Tries per level, 1 to 20; `case: null` = every test. |
| `set_bar` | `{ case?: null, value }` | The success bar, 0.05 to 1 (shown as "you set this"). |
| `run_tests` | `{ cases: "all" \| [case ids] }` | Run tests. |
| `watch` | `{ case?, level?, conditions?, worst?, weakest? }` | Start the live view. |
| `sheet` | `{ format: "json" \| "html" }` | Refresh the Parts card; `html` also writes a printable page and sets `parts.export`. |
| `pick_design` | `{ index }` | Use another design from `design.proposals`. |
| `undo`, `redo` | `{}` | Undo or redo the last edit (20 deep). |
| `stop` | `{ job: "plan" \| "tests" \| "live" \| "all" }` | Stop. Tests stop after the current condition. |
| `use_ai`, `keep_mine`, `use_ai_keep_edits` | `{}` | Choose after the Draft vs AI comparison. |
| `answer` | `{ topic }` | Template answer: `limits`, `why_fail`, `what_means`, `cost`, `overview`, `can_it`, `help`, `hardware`, `train`. |
| `new_chat` | `{ text? }` | Make a new chat (the 202 answer carries `new_chat`). |
| `cancel_queued` | `{ ticket }` | Withdraw an "Up next" job. |
| `keep_here` | `{}` | Dismiss the "start a new chat?" question. |
| `open_details` | `{ tab }` | Page-only (open the Details sheet). It is accepted but does nothing on the server; do not send it. |

### 5.2 Sentences

A typed sentence goes through, in order: `/commands` (`/watch /test /run /sheet /undo /redo /new /stop /help`), the edit grammar, questions, verbs, new-job detection, then "Did you mean" chips. No model is involved, ever. The page can send any sentence as `text`.

### 5.3 Case operations

Used by `edit` actions and recorded on `read_as` messages and in `history`. The server clamps every value to the simulator's range and says so in a `note` message ("Waves limited to 1.5 m, the simulator's maximum (you asked for 2.25 m).").

| `op` | Fields |
|---|---|
| `add_sweep` | `hazard` (id from `capabilities.platforms[p].hazards`), `levels?`, `ask?` |
| `add_fixed` | `fault` (id) or `conditions` (`{wave_hs: 0.8, wind_ms: 12}`) with `title?` |
| `add_untestable` | `title`, `reason?`, `key?` |
| `set_levels` | `case`, `levels` (kept mild to severe, 3 to 8 of them) |
| `set_ask` | `case`, `ask`, `insert?` (default true: also add it as a level) |
| `set_tries` | `case` (or null for all), `n` (1 to 20) |
| `set_bar` | `case` (or null), `value` (0.05 to 1) |
| `set_task_param` | `name`, `value` (only `menu.TASK_PARAMS`) |
| `set_job_number` | `name`, `value` (only the platform's numbers) |
| `remove` | `case` |

### 5.4 Chip

Chips come from the server in `chips` events (suggestions, "Did you mean", value choices, the Draft vs AI choice, the start-a-new-chat question, Retry) and inside some cards.

```json
{ "id": "run_all", "label": "Run all tests (7)", "action": { "type": "run_tests", "args": { "cases": "all" } },
  "kind": "primary"|"secondary"|"value"|"suggest", "enabled": true, "reason": null, "tag": null|"instant" }
```

A disabled chip is drawn disabled with `reason`. Pressing a chip sends its `action` with `label` as the label. At most four suggestion chips at a time. The empty-state chips (before any job) are three jobs tagged `instant` plus "I have my own parts".

---

## 6. Test cases

`cards.tests.rows[]` and `state.cases[]`. A test is derived from the plan's stress section, so the stress-test engine runs exactly these.

```json
{
  "id": "tc_waves", "title": "Waves up to 1.5 m", "why": "How tall the waves are (significant wave height).",
  "source": "catalogue"|"ai"|"user", "status": "ready"|"done"|"running"|"untestable", "kind": "sweep"|"fixed"|"untestable",
  "sweep": { "hazard": "waves", "knob": "wave_hs", "task_param": null, "unit": "m", "levels": [0, 0.15, 0.3, 0.45, 0.6, 0.9, 1.5], "ask": 1.0,
             "ask_src": "param"|"plan"|"user"|null, "ask_param": "wave_hs", "descending": false, "mild": 0, "range": [0, 1.5], "title": "Waves" },
  "fixed": null | { "conditions": { "wave_hs": 0.8, "wind_ms": 12 }, "fault": null | "thruster_out", "label": "…", "plain": "…", "fix": "…" },
  "task": { "preset": "water_rescue", "params": { "range_m": 150 } },
  "tries": { "start": 0, "n": 4 },
  "pass": { "metric": "success_rate", "op": ">=", "value": 0.75, "owner": "code"|"user" },
  "untestable_reason": null | "Rain is not simulated here.", "not_tested_text": null | "…",
  "touched": ["levels"], "input_sig": "e2bc44322ccc",
  "last_run": null | { "run_id": "r1", "at": 0, "machine_sig": "…", "input_sig": "…", "bar": 0.75, "partial": false, "verdict": "met"|"below"|"partial",
      "per_level": [ { "value": 0.3, "success": 1.0, "ci": [0.34, 1.0], "n": 2, "crashes": { "capsized": 1 }, "timeouts": 0, "mean_time": 17.55,
                       "per_seed": [ { "seed": 0, "success": true, "t": 17.55, "crash": null, "timeout": false } ] } ] },
  "stale": false, "watchable": true, "watch_reason": null,
  "cells": [ { "key": "tc_waves:0.3", "value": 0.3, "label": "0.3 m", "state": "met"|"below"|"running"|"queued"|"not_run"|"error"|"not_tested",
               "success": 1.0, "wins": 2, "n": 2, "ci": [0.34, 1.0], "text": "Waves 0.3 m: met bar, 2 of 2 tries" } ],
  "conditions": 7
}
```

- `kind: "sweep"` has `sweep` and `fixed: null`; `kind: "fixed"` (a failure such as "a thruster fails", or your own combination) has `fixed` and `sweep: null`, and one cell; `kind: "untestable"` has neither, `status: "untestable"` and a reason.
- The mild level is first and the severe level last. Levels are in the knob's own unit (for grip and mud, see `sweep.unit`).
- `source: "user"` plus `touched` (`"created"`, `"levels"`, `"ask"`, `"tries"`, `"bar"`) mark what the person edited; the page shows those as "yours".
- A test is **stale** when its machine, course, levels, bar or tries changed since its last run. A stale result stays visible (dashed, dimmed, a "Stale" chip) and nothing re-runs by itself.
- Each cell is a button labelled with `text` (a screen reader reads that sentence). Cells in `not_tested` are hatched, never green.
- `watchable: false` (faults, fixed obstacles, debris) shows `watch_reason`.

---

## 7. Results matrix

`results-<n>.matrix`, `state.matrix`:

```json
{ "rows": [ { "case_id": "tc_waves", "title": "Waves up to 1.5 m", "kind": "sweep", "unit": "m", "stale": false, "untestable": false,
              "bar": { "value": 0.75, "owner": "code", "text": "75% of tries succeed" }, "tries": 2,
              "cells": [ { "key": "tc_waves:0", "value": 0.0, "label": "0 m", "state": "met", "success": 1.0, "wins": 2, "n": 2, "ci": [0.34, 1.0], "text": "Waves 0 m: met bar, 2 of 2 tries" } ] } ],
  "met": 18, "below": 6, "not_run": 0, "total": 24, "bar_text": "75% of tries succeed" }
```

- `total` counts the conditions of runnable tests; untestable tests have a row (`untestable: true`, one `not_tested` cell) but add nothing to the counts.
- While a run is going the cell being worked on is `running`, those after it `queued`, finished ones `met` or `below`. Cells filled one at a time arrive as `card.patch` with a whole new `matrix.rows`.
- `bar_text` is the shared bar, or `"set per test (see each row)"` when tests differ. A user-set bar reads "(you set this)".
- The headline is always of the form "Met the bar in N of M simulated conditions with the simple built-in autopilot."

---

## 8. Draft vs AI (`improve.diff`)

When the AI plan arrives and a draft exists, the person's plan (the draft plus their edits) is compared with the AI's. Edits are an ordered list of operations; "Use AI, keep my edits" replays them on the AI plan, and the ones that no longer apply are listed in a message.

```json
{ "count": 4, "yours": 1,
  "changes": [ { "id": "c1", "area": "understanding"|"job"|"tests"|"design"|"assumptions", "kind": "added"|"removed"|"changed",
                 "label": "Worst waves", "draft": "1 m", "ai": "1.2 m", "yours": true } ],
  "edits": [ "Waves top level 1 to 1.5 m" ],
  "ai": { "source": "ai", "model": "nvidia/…", "platform": "water", "summary": "…" },
  "base": { "source": "draft", "platform": "water" } }
```

`yours: true` marks a difference where the person had edited that field themselves. With `count: 0` no diff is shown (state `kept`). Choosing `use_ai` replaces the plan and drops the edits; `use_ai_keep_edits` keeps them on top; `keep_mine` leaves everything as it is. When no ready-made job matched, there is no draft: the AI plan becomes the plan directly (`improve.state: "applied"`) and the understanding, design, tests, watch and parts cards are created from it.

---

## 9. Dock, limits, budget, jobs

### 9.1 `dock` event

```json
{ "type": "dock", "steps": [ { "id": "understand", "label": "Understand", "state": "pending"|"running"|"done"|"skipped", "detail": "0.3 s" } ],
  "elapsed": 0.8, "started": 1793696356.68, "done": true,
  "ai": { "running": false, "state": null, "phase": null, "seconds": 0 },
  "queue": [ { "id": "9f3a1c20", "label": "Run 7 tests", "position": 1, "cancel": { "type": "cancel_queued", "args": { "ticket": "9f3a1c20" } } } ],
  "busy": { "plan": false, "sweep": false, "live": true } }
```

- Steps of the draft: `understand`, `design`, `check`, `tests`, `watch`, `parts` (in that order; `watch` is `skipped` when no run started). While a stress test runs a `stress` step is appended with `detail` "condition 9 of 24".
- `elapsed` is the draft's real seconds. While `done` is false the server does not send an event just because `elapsed` moved: tick it on the page from `started` (a Unix time).
- `ai` is the AI timer: `running`, `state`, `phase`, `seconds` (about once a second).

### 9.2 `limits` event

`{ "count": 8, "stale": false, "line": "Simulation only, with a simple built-in autopilot. 8 things not tested here." }`. `count` is null before a job exists (then `line` is the empty-state line). Draw the Limits strip from `line`. Amber when `stale` is true. It opens the `limits` card (`answer` action with `topic: "limits"`).

### 9.3 `budget` event

`{ "ai": { configured, enabled, setting, model, used_today, left, limit, approx, low, exhausted, reset, reason, error } }`. Show "about N of ~50 AI requests left today (approximate)" when `left` is not null; `low` is true under 10; `exhausted` disables AI with `reset`.

### 9.4 `job` event

`{ "job": { "id": "r1", "kind": "tests", "state": "queued"|"running"|"done"|"stopped"|"error"|"cancelled", "label": "Run 7 tests", "done": 8, "total": 24, "position"?: 1 } }`.

---

## 10. Errors

- HTTP: validation problems are `422 {"detail": "…"}`; an unknown chat is `404 {"detail": "no such chat"}`; an unexpected failure in `send` is `500 {"detail": "<one scrubbed sentence>"}`. `detail` is always a string and never contains the API key.
- Turn failures arrive as an event, not an HTTP error: `{ "type": "error", "code": "turn_failed"|"draft_failed"|"redesign_failed"|"ai_failed", "message": "Something went wrong: …", "card": "improve"? }`. Show `message` inline (in the card named by `card`, or in the transcript) with the card's own Retry where it has one. `message` is scrubbed.
- A job that was running when the server restarted comes back with the card in state `interrupted`, an assistant line saying so, and a Retry chip in `chips`.

---

## 11. Resume rules

- The page keeps `last` (the `seq` of the last event it applied) and the chat id (remember the id in `localStorage`; the server also answers `GET /api/chat/latest`).
- Loop: `GET events?after=<last>&wait=25`; apply every event in order; set `last` to the response's `last`; if `more` is true repeat at once; on a network error wait 1 s and repeat with the same `after`. Never apply an event with `seq <= last`.
- Reload or a second tab: either replay everything (`events?after=0` until `more` is false) or take the fast path: `GET /api/chat/{id}/state`, render `view` (messages, cards, chips, dock), set `last = state.seq`, then continue the loop. Both give the same page.
- The server restores a chat from disk (`runs/chats/<id>/events.jsonl` and `session.json`), re-seeds the AI plan so the Goal tab's stress test can use it, and marks any job that died with the old process `interrupted`.
- Send with a `client_msg_id`; if the answer to a `send` is lost, send again with the same id.

---

## 12. Honesty rules the page must keep

1. The Limits strip is shown on every screen and always reads from the `limits` event.
2. Wherever results are shown (a `results` card, the Details sheet, an export) show `limit_line`, `statement` and `not_tested` with them.
3. Not tested is its own state: hatched, outlined, with the words "not tested", never green or red.
4. Do not write the words safe, safely, guarantee, proven, certified in any text of the page. "Passes" or "met the bar" only next to "in simulation".
5. Drone cards say "observation only" (`observation_note`).
6. No drive, arm or checklist control anywhere. The only way to the real rover is `parts.rover.href` (an ordinary link), enabled only when `parts.rover.enabled`.
7. `watch` runs are demonstrations or similar runs, never presented as the exact failed try.

---

## 13. A worked log: the flood-boat offline draft (no key saved)

The page opens, calls `POST /api/chat/new`, then loops on `GET /api/chat/{id}/events`. The person taps the first example chip, which sends `{ "action": { "type": "new_job", "args": { "text": "A boat that finds a person in a flooded street" } } }`; here they typed their own sentence instead. `ts` is seconds from the first event; long payloads are cut. This is real output of the engine.

```
seq 1  budget   ai: {configured:false, enabled:false, reason:"no key saved"}
seq 2  limits   count:null, line:"Simulates ground rovers, boats and observing drones. A good result here does not show a machine works in the real world."
seq 3  chips    ["Boat that finds a person in a flooded street", "Rover that inspects a 30 cm sewer pipe", "Drone that looks over a field for 10 minutes", "I have my own parts"]
seq 4  dock     steps:[], done:true
--- POST /send {"text":"A boat that finds a person in a flooded street 150 m away in 1 m waves"}  -> 202 {turn_id:"t1", intent:{kind:"new_job", source:"new_job"}}
seq 5  message  m1 user text  "A boat that finds a person in a flooded street 150 m away in 1 m waves"
seq 6  message  m2 assistant say  "Making a quick draft from the ready-made job. I will fill the cards in as each part finishes."
seq 7  dock     understand:running design:pending check:pending tests:pending watch:pending parts:pending
seq 8  card.create understanding  state ready, summary "Boat: A boat that finds a person in a flooded street 150 m away ..."   (t=0.04 s)
seq 9  limits   count:8, line:"Simulation only, with a simple built-in autopilot. 8 things not tested here."
seq 10 dock     understand:done (0.0 s)
seq 11 dock     design:running
seq 12 card.create design  state ready, summary "Does not meet the goal yet."   (t=0.31 s, the parts search took 0.3 s)
seq 13 dock     design:done (0.3 s)
seq 14 dock     check:running
seq 15 card.patch design         {proposals, key_facts}          (the Builder's checks: facts, findings, budget)
seq 16 card.patch understanding  {requirements}
seq 17 dock     check:done (0.0 s)
seq 18 dock     tests:running
seq 19 card.create tests  state ready, summary "7 tests, 24 conditions, 96 tries in all"
seq 20 dock     tests:done
seq 21 dock     watch:running
seq 22 card.create watch  state running, summary "Simple built-in autopilot, calm conditions, try 1. This is a demonstration, not a test."   (t=0.83 s)
seq 23 dock     watch:done (started), busy.live:true
seq 24 dock     parts:running
seq 25 card.create parts  state ready, summary "The parts list has 6 parts; 6 to buy. Numbers on it are estimates, not measurements."
seq 26 dock     parts:done, done:true, elapsed 0.8
seq 27 card.patch design  {preview}                              (the see-through picture, when MuJoCo can draw it)
seq 28 message  m3 assistant say  "Here is a first design and 7 tests (24 conditions). Watching a calm run now."
seq 29 chips    ["Watch it in rough conditions", "Run all tests (7)", "Make the waves tougher", "Parts & build sheet"]
--- the person says "make the waves bigger"
seq 30 message  m4 user text "make the waves bigger"
seq 31 message  m5 assistant read_as "I read this as: Waves top level 1 to 1.5 m."  undo:{type:"undo"}, ops:[{op:"set_levels", case:"tc_waves", levels:[0,0.15,0.3,0.45,0.6,0.9,1.5]}]
seq 32 card.patch tests  {rows, top, can_undo:true, actions}
seq 33 chips    [...]
--- "run all"  (6 tests + 1 fixed ... 24 conditions)
seq 37 message  user text "run all"                       seq 38 message assistant say "Running 24 conditions, 48 tries in all. Stop works after the current condition."
seq 40 card.create results-1  state running, summary "Condition 1 of 24", progress {done:0,total:24,...}, matrix (all cells queued)
seq 41 job      {id:"r1", kind:"tests", state:"running", done:0, total:24}
seq 44 card.patch results-1  {progress:{done:1,...}, summary:"Condition 2 of 24", matrix:{rows:[... first cell "met" ...]}}      (one patch per condition)
   ...
seq 118 card.patch results-1 {report:{url:"/reports/…/report.html"}, progress:{label:"finished"}, summary:"Met the bar in 18 of 24 simulated conditions with the simple built-in autopilot, 6 below the bar.", finished, actions}
seq 119 job     {id:"r1", state:"done", done:24, total:24}
seq 120 message assistant say  "Met the bar in 18 of 24 simulated conditions ... Weakest: ... Bar: 75% of tries succeed. Simulation only, with a simple built-in autopilot. 8 things not tested here."
seq 121 chips   ["Watch the weakest test", "Make it tougher", "Get the build sheet"]
```

A page that follows only this log can draw the whole conversation.
