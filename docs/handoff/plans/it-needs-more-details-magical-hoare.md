# Golden-hour Mumbai: the portfolio as a ride on a local train

## Context
Parth no longer wants the silicon-chip concept (PV-3). The new direction, chosen from three
options, is **golden-hour Mumbai**: a painterly, anime-background-style ride on a Mumbai local at
sunset, where each station is a project and the sky turns from gold to night as you scroll. A
GitHub scan with his token (private + collaborator repos) surfaced much stronger work than the
public profile showed. The chip site must be packed up, not deleted.

Decisions from Q&A:
- Archive the chip site to `C:\Users\Parth\Desktop\portfolio-archive\pv3-chip\` (with its own
  `startup.bat`) so it never deploys with the new site.
- Client work: show **RK Enterprises** and **AKMO Chemicals** as case studies, **no code links,
  high-level only** (AKMO README: "owned by AKMO Chemicals LLP. Confidential.").
- Credits: **H.A.L.O. — Parth led the project within his SIH team**; **COSTA — team project he
  worked on** (repo owned by ishwar-prog).
- Security: the PAT is now in the transcript. It is used only for read-only API calls, never
  written to disk; tell Parth to revoke it. Public images can be fetched without it.

## Project inventory (from the scan; 511 commits + 51 private contributions last year)
**Fast stops (featured, full station):** in journey order, ending at night
1. **WMC Virtual Lab** (omtawde09, 28 ★): React + FastAPI + Kotlin Android lab; Parth built
   Practicals 6 and 7 (multipath fading + noise/interference analysers, backend + UI).
2. **WhisperFlow / Susurrus** (225 commits) + its landing page (53).
3. **Enterprise Knowledge Assistant** (38): zero-trust Slack RAG, local Gemma on an RTX 5050.
4. **Client work, one station, two posters:** RK Enterprises (Next.js 16 B2B machinery site,
   17-machine catalogue, Zod + Prisma enquiries to WhatsApp) and AKMO Chemicals (R&D/inventory
   system: immutable formulation versioning, live vs. snapshot cost roll-up, similarity detection;
   with Om Tawde).
5. **Codeframe** (`frigshwar`): design canvas → React + Tailwind.
6. **Football BlueLock**: first-person multiplayer gully football on a painted maidan, EGO super
   moves, WebSocket relay, LAN play. The maidan with a match is visible from the train.
7. **COSTA** (SIH26169, team): finds a beacon by *how it blinks*, gimbal lock in ~1 s, 28/28
   validation, no training data. A blinking satellite crosses the sky.
8. **H.A.L.O.** (SIH26174, led by Parth): offline on-board AI that follows ISS science procedures
   from video (YOLO11 on real ISS footage, mAP50 0.79, 25 fps on a laptop GPU), EN/HI voice alerts,
   hash-chained signed log, 276 tests, 7/7 requirements. The ISS streaks across the night sky.

**Slow stations (the fast train skips them; boards flash past, click for a card):** K.C. College
(2024), Color Vision Assistant (2025), Credence hackathon (Jan 2026, 10 of 23 commits), Imarticus
internship (A+), AI Voice Callbot, Agent Safety Net, 2'nd_Brain, Nexus-AI, StudyOS (live), Shorts
Intelligence OS, Gym Tracker (live). Skip: second-brain, jepa_to_llm, trusty-trade, test repos.

## Experience design
- **POV:** the open door of a moving local, the city streaming past. Vertical scroll drives the
  train's distance; the camera eases into each fast stop and holds.
- **Time of day** tracks progress: golden afternoon → sunset → dusk → night (window lights,
  stars). H.A.L.O. and COSTA arrive at night.
- **Stations:** trilingual yellow board (Devanagari + English) with the project name; a
  dot-matrix **platform indicator** (time, F/S, coaches, destination = the project's stats) and
  **platform posters** (real images from public repos: H.A.L.O., COSTA, Football gameplay).
- **"Pudhil station…"** next-station announcement as a subtitle (Marathi/Hindi/English) on
  approach; optional speech via browser speechSynthesis behind a sound toggle (off by default).
- **About:** a railway season-pass ID card with Parth's photo ("Route: K.C. College ⇄ Anywhere,
  valid till 2028") plus the existing about copy.
- **Skills:** the monthly ticket / route map. **Contact:** terminus enquiry window.
- **Linger details:** crows on catenary wires, kites, a chai stall, a vada pav stall, gulmohar
  trees, Bandra–Worli Sea Link on the skyline, clickable scene easter eggs.
- **Résumé:** `timetable.html`, a printable railway-timetable résumé (replaces the datasheet).

## Technical approach
- Static site, no build step, classic scripts (works from file://), same file layout pattern as
  the chip site (`index.html`, `css/main.css`, `js/content.js`, `js/*.js`, `assets/`).
- **Renderer (`js/scene.js`, WebGL2):** one compositor shader. The sky (time-of-day gradient,
  sun, stepped anime clouds, stars) is procedural in the shader. City layers (far skyline, mid
  buildings + water tanks + catenary, near trees/walls) are generated once with seeded Canvas2D
  drawing into tiling strips (silhouette + window-light masks), uploaded as textures, parallaxed
  per layer, and tinted by time of day. Grain and glow in the same pass; adaptive resolution and
  render-on-change as before.
- **Sprites in world space** for stations, landmarks (Sea Link, maidan match, ISS/beacon sky
  events); DOM overlays for crisp text (boards, indicator, panels).
- **Reuse** from the chip build: the scroll → stop blending (`tourTarget`/`blendStops` pattern
  in the archived `js/app.js`), reveal-on-scroll, toast, loader pattern, photo crops in
  `assets/img/`, the startup.bat server one-liner.
- **Fonts:** Instrument Serif (display), Geist (body), Mukta (Devanagari), a condensed sans for
  signage, dot-matrix drawn in canvas.
- **Learning-mode handoff:** ask Parth to implement the small rule that decides a project's
  station type (fast stop vs. skipped halt) in `js/content.js` (`TODO(human)`).

## Steps
1. Archive: move the current site files to `Desktop\portfolio-archive\pv3-chip\` and verify it
   still runs from its own `startup.bat`.
2. Fetch public repo images (H.A.L.O., COSTA, Football) and optimise them with Pillow.
3. Content model (`js/content.js`) for all stations; then the renderer, stations, UI, timetable.
4. Update `startup.bat` and the share image for the new concept.

## Verification
- `node --check` on every script; zero console errors.
- Browser pane (ask Parth to show it with Ctrl+Shift+B if hidden): screenshots at 1280 × 800
  and 375 × 812 for the hero, every fast stop, the night section, the ID card and the terminus.
- Panel heights fit 1280 × 800; no horizontal overflow at 375 px.
- Render timing < 12 ms per frame at ~1.6 MP across day/night positions.
- `timetable.html` prints cleanly to A4; the archived chip site still opens.
