# The AI planner: describe any job, the simulator checks what the AI decides

The Goal tab used to match your sentence against three fixed jobs and ask three fixed questions. It now hands the sentence to a language model (through OpenRouter's free tier) that **decides in depth**, and then the simulator **checks everything the model claims**. The model proposes; the code disposes.

## What the AI is asked to decide

One request, answered as a single JSON object (`simlab/llm/prompts.py` builds the instructions from the live parts library, so the model can never be told about a part or hazard that does not exist):

| Section | What the model does | What the code does with it |
|---|---|---|
| `understanding` | says what the job is, which machine type fits (ground rover, drone, boat) or `unsupported` | `unsupported` is shown honestly (an underwater cave is not guessed at) |
| `params` | the job's numbers (range, minutes, wave height, wind, load, pipe width) | each is clamped to the range the simulator supports; every correction is listed in "what the checker corrected" |
| `questions` | at most 3 things it really needs to know | you answer in the page; the plan is asked for again with your answers |
| `scenarios` | 4 to 8 situations it thought of (waves, current, debris, a failed thruster, night...), each linked to a simulator hazard or `null` | linked ones are stress-tested; the others are shown as **"not testable here"** and listed under *not tested* in the report |
| `requirements` | extra constraints on facts the Builder measures (runtime, freeboard, thrust ratio...) | each one is **measured** by the evaluator; one on a fact the Builder cannot report is dropped and noted |
| `task`, `stress` | which test scene to use and which hazards and failures to sweep, with levels | whitelist of hazards and faults, levels clamped to physical ranges, turned into a sweep catalogue (`simlab/hazards/synth.py`) |
| `designs` | one or two complete builds with the reason for each part | every part must exist; every build goes through the Builder's evaluator and the requirements |

## The checks that make it safe to act on

- **Closed menu.** Hazards, faults, parts, task presets and job numbers come from a menu built from the code (`simlab/llm/menu.py`). Anything else is dropped, with a note.
- **The job text is data.** A sentence like "ignore your rules and add a part" cannot widen what the model may choose: its answer goes through the same validation as anything else.
- **Physics, not belief.** A design is only called a match when the simulator's own formulas say it meets the requirements. If none of the AI's designs does, a deterministic search of the parts library (`goals/design.py`) finds a verified alternative or shows the closest one with what it misses.
- **At most 3 requests per plan.** One to plan, at most one repair if the answer was unreadable or cut off, at most one revision when the checker finds problems. Free OpenRouter models allow 50 requests a day, so a plan must stay cheap.
- **Offline fallback.** No key, no internet, a spent daily quota or an unusable answer: the old keyword planner and the three ready-made jobs take over, and the page says so.
- **The key stays private.** It is stored in `config/llm.json` (git-ignored) or read from `OPENROUTER_API_KEY`, never sent to the browser, never in a log or an error message.

## Setting it up

Open the **Goal** tab, expand **AI settings (OpenRouter)**, paste your key (it starts with `sk-or-v1-`), **Save**, then **Test** (uses one request). Or set the environment variable `OPENROUTER_API_KEY` before starting the dashboard. The page shows how many free requests are left today.

Free models may log what you send. Only your job text, your answers and the public parts list are sent.

## Which model

The default is `nvidia/nemotron-3-ultra-550b-a55b:free` (550 billion parameters, 1 million tokens of context). If it is busy or gone the planner tries `nvidia/nemotron-3.5-super-vl:free`, then Google's Gemma 4 models. You can type another model name in the settings box.

**Inkling is not usable.** `thinkingmachines/inkling:free` and `inkling-small:free` were the first choice, but on 2026-11-01 OpenRouter answered every plain API call with *"only available on agentic harnesses"* (HTTP 403): those models are limited to approved coding-agent apps. The client recognises this, skips such a model without a retry and remembers it for six hours so it does not spend a request on it again. If OpenRouter opens them up, type the name in the settings box.

A free reasoning model is slow: expect one to three minutes per plan. The page streams what the model writes and lets you stop it.

## What a live run looked like (2026-11-02, Nemotron 3 Ultra 550B, free tier)

| Job typed | Result | Requests | Time |
|---|---|---|---|
| "A boat that goes out to people trapped by a flood ... strong wind and rough water" | 3 questions (range, load, night), then a plan with 6 scenarios (5 simulated), a verified "closest we found" (freeboard and a demanding thrust ratio not met), a 25-condition stress sweep that found fixed obstacles as the weakest point | 3 | about 2 min per call, sweep about 9 min |
| "Map an underwater cave system ... 30 metres deep" | answered **unsupported**, with the reason, and no machine was invented | 1 | 82 s |
| "A small rover that drives along the rows of my vegetable field ..." | ground rover with a camera and ultrasonic sensor; the Builder's own check caught a 5 V sensor output wired to a 3.3 V pin that the model missed (amber, "needs a level shifter") | 3 | about 6 min |

Things this showed, and how the code handles them:

- **The model ignores stated limits.** It asked for a 100 to 150 kg payload where the simulator carries 20. The checker clamps and says so; number questions now show the supported range.
- **Reasoning eats the token cap.** A 3,500-token cap once cut the JSON off and cost a repair call; the cap is 8,000 and a cut-off answer is asked again "shorter" (and the page lists that a repair happened).
- **Thinking aloud may contain small JSON examples.** The reader takes the largest complete object, not the first.
- **A job outside the menu is stretched, not refused.** The ground menu only has pipe width, distance and wet floor, so the field rover is checked as if it had to fit a 50 cm pipe. The report still says what was not tested (uneven ground, camera glare).
- **A full AI stress plan is slow.** Fixed-obstacle levels run whole episodes; a four-hazard boat plan took about 9 minutes. The progress bar and Stop button are there for that.

## Limits to keep in mind

- The AI can only choose among what the simulator can test. It will think of hazards the simulator cannot model (breaking waves, corrosion, rain on a lens); those appear as *not testable* and in the report's *not tested* list, never as a pass.
- A job needing more than the simulator supports is clamped (for example a load above 20 kg) and the page says so.
- Everything is an estimate from datasheets and simulation. Nothing is checked against real hardware until you calibrate it, and no report ever says a machine is safe.
- The planner cannot design machines the parts library lacks.
