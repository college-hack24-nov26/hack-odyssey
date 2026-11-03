# Overhaul: "Gulmohar Ground" portfolio (3D, football_glm art direction)

## Context
User feedback on the train version: "the visuals are ass", the story and animation are "repetitive" (every stop is the same slide → brake → board), and the gradients and assets look cheap. They pointed at `Desktop\football_glm`, their Blue Lock gully-football game.

That game already has a polished art pipeline in three.js, and the plan reuses it:
- painted golden-hour sky dome
- toon shading + outlines
- hand-painted canvas textures
- gulmohar trees + petal fallout
- skyline
- a "painting" grade pass (S-curve, split-tone, paper grain, dither: no banding)
- manga impact frames

We keep the content (`js/content.js`) and replace the presentation. The train build stays in `Desktop\portfolio-archive\train-v1`.

## Concept: one evening on the maidan, told as a match
- **Hero:** we sit low on the grass at golden hour. Parth's photo is a painted poster on the pavilion wall, and petals drift. The ball rests at our feet.
- **Scroll is the match.** It drives a camera spline through the ground. Each project is a **"play"** with its own camera verb and its own super-move effect from the game, so no two transitions repeat:

| Project | Camera verb | Effect (from football_glm) |
|---|---|---|
| H.A.L.O. (led) | crane up into the sky | Crimson Dragon ribbon carries the ball to the title |
| COSTA | orbit the centre circle | Eagle Talon swoop + radar rings |
| Clients (RK, AKMO) | push in on the pavilion noticeboard | paper notices pinned |
| Football BlueLock | first-person dribble + shot | goal net ripple, impact frame |
| Susurrus / Eka / WMC / CodeFrame | quick cuts down the touchline | Mountain Bastion rocks rising with the project cards |
| Earlier work (slow stations) | a "scrapbook" fan of trading-card stickers on the bench | — |
| Terminus / contact | **Zero Hour**: time stops, sepia ink, the ball hangs mid-air, the contact links orbit it | — |

- **Manga impact frames** (two-tone ink + speed lines + kanji-style label, e.g. 開発 "build") punctuate each project title for about 250 ms.
- **Sky time:** the sky goes from golden hour to dusk to floodlights-on night across the scroll. This keeps the `skyTime` idea, now driving the sun direction and the floodlights.

## Tech approach
- Still a static site with no build step. Load three.js r160+ as an ES module from `cdn.jsdelivr.net/npm/three` using an importmap. The site needs a server, which `startup.bat` already provides.
- **Port these modules, not copy:** football_glm is TS/Next, so we hand-port the relevant pieces to plain JS modules in `js/gl/`:
  - `sky.js` ← `src/game/render/sky.ts` (dome shader, cumulus, cirrus, dither)
  - `post.js` ← `post.ts` (grade, grain, vignette, ink/mono/wash modes for impact frames + Zero Hour)
  - `toon.js` + `outline.js` ← `toon.ts`, `outline.ts`
  - `textures.js` ← the subset of `textures.ts` we need (grass, plaster, brick, building facade, leaf). Reuse `mulberry32`.
  - `trees.js`, `petals.js`, `skyline.js` ← the same-named files, reduced counts
  - `ground.js`: a simplified pitch, pavilion, benches and goal (lifted from `world.ts`, no gameplay)
  - `fx.js`: 3 effects lifted from `superFx.ts` (dragon ribbon, rock ridge, eagle swoop)
- `js/director.js` (new) maps scroll to `{shotIndex, localT}`. Each shot is `{camPath, lookAt, fx(t), skyT}`, which replaces `target()`/dwell zones. Blending between shots uses smootherstep on the camera *and* the look target to avoid whip pans.
- **DOM layer:** project panels are reworked as "match cards" (paper texture, ink outline, offset shadow), echoing the game's HUD style. The current notice markup is reused (`notice()` in `app.js`).
- **Quality tiers** (low/med/high) as in football_glm `QUALITY`. The tier is picked from `devicePixelRatio` + first-frame timing. Mobile gets low: no outlines, 1/3 petals.
- **Fallbacks:** reduced-motion cuts instead of flying the camera. No-WebGL shows a static painted poster image.

## Files
- **New:**
  - `js/gl/*.js`
  - `js/director.js`
  - `css/main.css` (rewritten)
- **Rewritten:** `index.html` (importmap, module entry)
- **Kept:**
  - `js/content.js`, plus a per-project `shot` key
  - `assets/img`
  - `startup.bat`
- **Removed:** `js/world.js` and `js/scene.js` (archived with the train version)

## Build order (stop for review after each major part)
1. Archive the train version; scaffold the three.js app. Port sky + post grade + ground and grass, and get the hero shot looking right.
2. Port trees, petals, skyline, toon/outline, and the pavilion with the photo poster.
3. Director + shot list + match cards (the HALO, COSTA and Zero Hour terminus shots first).
4. Remaining shots, impact frames, fx ports, then the mobile/low tier.

## Verification
- `preview_start` (launch.json python server). Check `read_console_messages` for errors.
- Script-scroll through each shot via `javascript_tool`; screenshot the hero, HALO, COSTA and terminus.
- Check banding (dither on), FPS on the low tier with mobile `resize_window`, and the reduced-motion path.
