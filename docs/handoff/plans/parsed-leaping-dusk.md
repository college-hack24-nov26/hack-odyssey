# SOCIALxBRAND PILOT — motion-led website: build plan

## Context

SOCIALxBRAND PILOT (SxBP), an Indian digital marketing agency, needs its first website. The client wants professional quality, excellent typography, strong animation, and photo/video that feels alive. The brief ranks the priorities as motion > visual > type > interaction > brand > UX > content > architecture, and says "the motion IS the website".

- The project folder `C:\Users\Parth\Desktop\sujal_2` is empty and is not a git repo.
- Sources of truth are the two PDFs (Company Profile and Services & Divisions), plus the 5 logo images and the public Drive folder "SxBP x Vishay Creations Portfolio".
- **Nothing may be invented**: no clients, metrics, testimonials, awards, years in business or city. The location is "India · IST" only.

**Decisions confirmed with the user**

- The enquiry form stays *broad*: it builds a structured brief and hands it to pluggable delivery adapters (WhatsApp, mailto and copy-brief now; an email API can be added later).
- Work carries a **"Production partner: Vishay Creations"** credit.
- **No portfolio categories are excluded.**
- A **curated download** from Drive is approved: about 30 videos and 60 photos (~1–1.5 GB) into a gitignored `media-source/`.

**Established facts**

- **Portfolio**: 500 files, 173 videos and 327 photos, mostly **vertical 9:16 reels**. Every video is named "Vishay Creations.mov", so the category is the only metadata.
- **Toolchain**: Node 24.15, ffmpeg, Python 3.14.
- **Packages**: next 16.3.8, react 19.3, tailwind 4.3.3, gsap 3.15 + @gsap/react 2.1.2 (all plugins free), lenis 1.3.26, sharp 0.35, playwright 1.63.
- **OpenCode 1.18**: Context7 and Playwright MCPs are already configured in the global config.

## Direction — "The Pilot Route"

**Core idea.** Every business gets a *route*, not a package. One recurring motif, the **red signal line**, carries the story.

- It is born from the logomark: the ribbon draws, then the play-triangle launches.
- In the hero it threads through the headline.
- It becomes the scroll-progress line in the nav.
- It draws the 7-station method.
- It draws each division's own approach chain.
- It connects the stations a visitor picks in the Route builder.
- It finally underlines "YOU WILL GROW".

A second motif comes from the logo's **multi-line inline stencil** ("BRAND PILOT"). The parallel strokes become "lanes": outlined numerals and dividers, plus the SHOW/GROW finale, where the outline fills.

**Visual language**

- **Palette**: near-black navy `#0B0A12` as the base, off-white paper `#F3F0E8` for "light acts", signal red `#FF3B36` (from the logo), purple `#8B5CFF` (from the wordmark).
- **Colour rule**: red is used for action and the route; purple marks the "creative" register and selected states. Neither colour is ever used as a background wash.
- **Look**: editorial and cinematic, with hairline grids, `[ 01 ]` bracketed mono indices and coordinate-style meta. There are no aircraft-dashboard clichés.

**Typography** (free licences, self-hosted via `next/font`)

- **Display**: Clash Display (Fontshare FFL), 500–700, tracking −0.04 to −0.06em, line-height 0.85–0.95, `clamp()` up to ~16vw. It gets an **A/B prototype** against Bricolage Grotesque (OFL), with the choice made in the browser.
- **Accent**: Instrument Serif *italic* (OFL), one or two words per headline at the same size, e.g. "EVERY BUSINESS HAS SOMETHING *worth showing*".
- **Body/UI**: Geist (OFL), 16–20px, line-height 1.5.
- **Labels and indices**: Geist Mono, 11–13px uppercase, +0.08em tracking.

**What we take from the references**

| Pattern | Source |
|---|---|
| Mixed sans-caps + serif-italic hero | Ovlaya |
| Rotate-in fan of real reels | Whenevr |
| Word blur-in | Hanzo, Pixello, PromptIQ |
| Scrubbed character fill on the manifesto | Ovlaya, Pixello |
| Index table for many services, with a cursor-following preview | Framo |
| Clickable bidirectional division tickers | Sol, Whenevr |
| Sticky scroll-spy navigator for division detail | Elevix |
| Reel band that settles with parallax | Sol |
| Giant edge-to-edge footer wordmark | PromptIQ |
| One enforced motion grammar | Scale |

**What we avoid**: pricing tables, FAQ filler, logo walls, fake stats, testimonial marquees, starfields, and identical fade-ups on every block.

## Information architecture

| Route | Purpose |
|---|---|
| `/` | The film. A cinematic narrative in 9 acts (below). |
| `/work` | Reel-wall index of real work. Category filters reflow the grid with GSAP Flip, and each tile plays on hover/in view. Carries the partner credit. |
| `/work/[slug]` | One "screening" page per portfolio category (~15). Sequence: full-screen shared-element takeover → title + mono meta (format, pieces shown, *capabilities shown*, which is descriptive of the asset type, never a claimed engagement) → reel carousel and photo gallery (pan/zoom) → a "next screening" that pulls in on scroll. |
| `/capabilities` | The 18-division explorer in 4 clusters from the profile: Strategy & Creative, Performance & Search, Direct, and Growth, PR & AI. It shows the "Not every business needs every service" thesis, an index table and clickable tickers. |
| `/capabilities/[slug]` | 18 product-like division pages, with all PDF content verbatim (sequence below). |
| `/approach` | Method in depth, the philosophy ladder, principles, vision/mission, AI philosophy, who we work with, and what makes us different. |
| `/route` | **Signature: Pilot Route builder + contact** (sequence below). Also offers a "Just talk" mode with direct email, phone and WhatsApp. `/contact` redirects here. |
| `/lab` | Dev-only prototype bench. Returns `notFound()` in production and is excluded from the sitemap. |

**Division page sequence** (`/capabilities/[slug]`):

1. The number and name in display type, with a shared-element transition from the index.
2. The what-it-is statement as a scrubbed reveal.
3. Capability groups that assemble with a staggered stagger-grid.
4. Key deliverables.
5. **The division's own approach chain, drawn as its own mini route.**
6. Relevant work, mapped by category.
7. Related divisions.
8. An "Add to my route" CTA.

**Route builder sequence** (`/route`):

1. **Business** — the 4 audiences, or Other.
2. **Goal** — be seen / remembered / trusted / chosen / grow.
3. **Suggested divisions**, which the visitor can edit. It is clearly labelled: "a starting route; the real plan comes after we understand your business".
4. **Details**.
5. **Summary** — the route is drawn as a line through the chosen stations, then sent via an adapter.

Divisions selected anywhere on the site persist (localStorage) and show as a count on the nav CTA.

**Navigation**

- **Bar contents**: the logomark, then Work · Capabilities · Approach, then a "Start your route" pill with the route count.
- **States**: transparent over the hero, then a solid blurred bar after 80px. It hides on scroll-down and shows on scroll-up.
- **Progress**: a 1px red route-progress line runs across the top.
- **Menu**: a button opens a full-screen menu with reel previews per item; this is the primary nav on mobile.

### Homepage acts (story beats → primary motion)

| # | Act | Story beat | Primary motion |
|---|---|---|---|
| 0 | Intro (first visit only, ≤0.9s) | Opening | The logomark ribbon draws (DrawSVG) and the triangle launches a red line. Skipped for reduced motion and repeat visits. |
| 1 | Hero | Interest | "EVERY BUSINESS HAS SOMETHING *worth showing*" assembles line by line (masked rise). A fan of 5–7 real 9:16 loops rotates in (−75°→0). Sub-line: "We build the strategy, creative and growth systems that help the right people see it." CTAs: Start your Pilot Route / See the work. Live "India · IST" clock. **On scroll**, the headline lines split apart in counter-motion and the fan collapses into one reel that scales to full screen, handing off to Act 2. |
| 2 | Manifesto (pinned 150vh) | Tension | Scrubbed character fill: "We don't simply aim to create digital *activity*." The word activity greys out and is struck through, then "We aim to create digital **impact**." follows in red, then the "make them matter" quote. |
| 3 | Seen → Remembered → Trusted → Chosen → Forward (pinned, 5 states) | Reveal | One word swaps in place (split-flap/mask morph) while the background reel crossfades per state. |
| 4 | Screenings (pinned horizontal) | Discovery | A row of 9:16 phone frames runs horizontally via `containerAnimation`. The active reel plays and the rest show posters; category labels are in `[ ]` mono. It ends in "Enter the work", with the partner credit. |
| 5 | Capabilities | Discovery | "Not every business needs every service." The 18-row index in 4 clusters, a cursor-following reel preview on hover, two clickable tickers, and add-to-route chips. |
| 6 | The Route — method (pinned, 7 stations) | Progression | The red line draws through UNDERSTAND → … → GROW. The environment changes at each station (tone, type state, one media piece). It opens on "We start by understanding the business." |
| 7 | Marketing is for every business (light act) | Trust | 4 audience tabs, each re-routing the line to that audience's needs, taken from the profile text. |
| 8 | AI amplifies | Trust | Strike-and-replace typography for the 3 philosophy pairs ("replace" is struck by the red line, then swapped). Framed as division 18, not the brand. |
| 9 | Finale + footer | Desire → Action | "WE WILL SHOW." in an outlined lane stencil that fills; "YOU WILL GROW." scales up as the route underlines it. Then a giant clipped wordmark, the contact details and the IST clock. |

## Motion system (codified in `docs/motion-system.md` and `src/lib/motion/tokens.ts`)

**Durations**

| Token | Value |
|---|---|
| micro | 0.2 / 0.35s |
| base | 0.6s |
| slow | 0.9s |
| cinematic | 1.2–1.6s |

**Eases** (CustomEase)

| Name | Value | Used for |
|---|---|---|
| `pilot` | (0.22, 1, 0.36, 1) | Entrances |
| `glide` | (0.65, 0, 0.35, 1) | Curtains and state swaps |
| `none` | — | All scrub |
| `back.out(1.4)` | — | Micro only |

**Staggers**: chars 0.02s (hero only), words 0.04s, lines 0.08s, cards 0.07s. Total stagger is capped at 0.5s.

**Presets**

| Preset | Motion | Used for |
|---|---|---|
| `rise` | Line mask, yPercent 100→0, slow, pilot | Display headings |
| `blur-in` | opacity 0, blur 6px, y 8 → base | Secondary headings |
| `lift` | y 40, autoAlpha → base | Blocks |
| `settle` | y 300 / scale 1.15 → 0 / 1, scrubbed | Media bands |

Triggers fire at `top 80%`, once.

**Hierarchy**

- **Primary**: intro, hero assembly, the hero→Act 2 takeover, the method route, SHOW/GROW, page transitions.
- **Secondary**: headline reveals, manifesto scrub, reel parallax, tickers, Flip filters.
- **Micro**: magnetic CTAs, label roll, cursor preview, nav hide/show, chip toggles.

**Scroll**

- Lenis runs with lerp 0.1, `autoRaf:false`, driven by the GSAP ticker (ms conversion, `lagSmoothing(0)`).
- Pins are sized in steps: manifesto 150vh, philosophy 5 × 70vh, method 7 × 80vh, screenings by track width.
- Horizontal tweens use `invalidateOnRefresh` and function values.

**Pages**: React `<ViewTransition>` (no flag needed in Next 16.3) for the index→detail shared titles and reels, with a red route-wipe curtain as the default transition. Navigation is inside transitions only.

**Mobile** (gsap.matchMedia `<768px`), re-choreographed rather than shrunk:

- The screenings become a native scroll-snap swipe rail.
- The method becomes a vertical scrubbed line.
- The hero fan becomes 3 stacked reels.
- The cursor preview becomes tap-to-preview.
- At most 2 videos play at once, using 720p sources.

**Reduced motion**: one matchMedia branch.

- No pins and no scrub; content is stacked.
- Crossfades are ≤200ms.
- Videos show a poster with a play button.
- Lenis is off and ViewTransitions are disabled via CSS.
- No information is carried by motion alone.

**Hygiene**

- Every animation lives in `useGSAP({scope})` and handlers go through `contextSafe`.
- SplitText (`autoSplit` + `onSplit`, `mask:'lines'`, aria-preserving) is used only on display headings.
- Plugins are registered once in `src/lib/gsap.ts` (`'use client'`).
- A **VideoBudget** manager caps concurrent playback (desktop 4, mobile 2) and pauses off-screen video via IntersectionObserver.

## Media pipeline (`scripts/media/`, outputs to `public/media/`)

1. `inventory.mjs` — crawls `embeddedfolderview` recursively into `media-source/inventory.json` (ids, mime, category path).
2. `contact-sheet.mjs` — builds a local HTML sheet from `drive.google.com/thumbnail?id=…&sz=w800` for curation. I curate ~30 videos (2–3 per category) and ~60 photos into `selection.json`.
3. `download.mjs` — `curl.exe -r 0-0` size probe first (abort if the total is >2 GB), then `curl.exe -L --retry 5 -C -` with 2–4 in parallel. Files are named by Drive id, never by the Content-Disposition name (every video is "Vishay Creations.mov"). HTML responses are treated as an interstitial and trigger a back-off.
4. `transcode.mjs` — ffmpeg, never upscaling. Sources are normalised to fps 30 and yuv420p, and audio is stripped.

   | Output | Spec |
   |---|---|
   | Primary loop | 6–10s, 720×1280 AV1 WebM (libsvtav1 crf 34; VP9 if unavailable) |
   | Fallback loop | H.264 MP4 crf 24, `+faststart` |
   | Hero loop | 1080×1920 only when the source is ≥1080 |
   | Preview | 360p, ~200 KB |
   | Poster | Frame at 1s |
   | Scrub version | `-g 10 -bf 0`, only the 1–2 scrubbed clips |

   Budget: ≤1.5 MB per 720p loop.

5. `images.mjs` — sharp `.rotate()`, 480/960/1600/2400 widths, AVIF q50 + WebP q75, plus a 16px LQIP (`blurDataURL`).
6. `src/content/media.ts` — a typed manifest: id, category, kind (reel/film/photo/logo/design), orientation, sources, poster, lqip, **behaviour** (loop | hover | scrub | pan | crossfade | stack | mask). Behaviour is varied per asset so the media has rhythm.

Media components: `Reel`, `ScrubVideo`, `KenBurns`, `CrossfadeStack`, `MaskReveal`, `CursorPreview`.

## Code structure (Next.js 16.3 App Router, TS, Tailwind 4 `@theme`, src/)

- `src/app/(site)/…` — routes listed above. The layout holds `SmoothScroll`, `Nav`, the `ViewTransition` shell and the `Footer`.
- `src/content/` — `site.ts`, `divisions.ts` (all 18, verbatim from the PDF: number, slug, full + short name, cluster, whatItIs, groups, deliverables, approach[]), `method.ts`, `principles.ts`, `audiences.ts`, `ai.ts`, `work.ts`, `media.ts`.
- `src/lib/motion/` — tokens, presets, `SplitHeading`, `useReveal`, `Magnetic`, `RouteLine` (one SVG path component reused by hero, nav, method, divisions and the builder), `VideoBudget`.
- `src/lib/route/` — `store.ts` (selected divisions), `suggest.ts` (audience + goal → suggested divisions), `enquiry/` (a `buildBrief()` plus adapters `whatsapp`, `mailto`, `copy`, behind an `EnquiryAdapter` interface ready for a later Resend/Formspree adapter).
- **SEO**:
  - `metadataBase` https://www.sxbp.com, title template, per-page canonical.
  - `sitemap.ts` (all routes, 18 divisions and the work slugs) and `robots.ts`.
  - An `opengraph-image.tsx` per section.
  - JSON-LD: `ProfessionalService` (name, url, email, telephone, areaServed India + worldwide), `Service` per division and `BreadcrumbList`, with `<` escaped.
  - Honest positioning: "SOCIALxBRAND PILOT — Digital Marketing Agency — India & Global Markets".

## Agent tooling

- **Rules files**: `AGENTS.md` is the canonical project rules file (no-fake-content rules, motion grammar, commands, the media pipeline). `CLAUDE.md` contains `@AGENTS.md`.
- **Skills**: `.opencode/skills/{brand-storytelling,agency-copy,conversion-review,motion-design,visual-qa,accessibility-performance}/SKILL.md`. Each name equals its directory name and uses lowercase-hyphen. They live in this single location (a duplicate in `.claude/skills` would collide). `motion-design` carries the full token, choreography, easing, media, typography, performance and reduced-motion guidance.
- **Reviewer agents**: `creative-director`, `motion-director` (mandatory, harsh critique remit), `conversion-critic`, `frontend-reviewer`, `visual-qa`. Each one has the same prompt body in two files:
  - `.opencode/agents/<name>.md`, with `mode: subagent`, `permission: {edit: deny}`.
  - `.claude/agents/<name>.md`, with `name`, `description`, `tools: Read, Grep, Glob, Bash`, `model: inherit`.
- **MCP**: no new MCPs. The global OpenCode config already supplies Context7 and Playwright. The project `opencode.json` only sets `instructions: ["AGENTS.md","docs/motion-system.md"]`. A minimal `.mcp.json` (context7 http + playwright) lets Claude Code use the same tools.
- **Session note**: the global settings point subagents at an invalid model. Every reviewer and workflow agent in this session must pin `model: 'claude-opus-5-5'`.
- **Docs**: `docs/design-research.md`, `design-brief.md`, `site-architecture.md`, `content-map.md`, `motion-system.md`. They are written from this research and explain the *why* behind each decision.

## Build sequence

1. **Foundations**
   - `git init`.
   - `create-next-app@16.3.8 --ts --tailwind --eslint --app --src-dir --import-alias "@/*"`. Pin TS ~5.9 if TS 7 breaks the type-check.
   - Install gsap, @gsap/react, lenis, sharp, @playwright/test, @axe-core/playwright.
   - Fonts, tokens, content files transcribed from the PDFs.
   - AGENTS.md, skills, agents, docs skeleton, `.claude/launch.json` (dev server, for the browser-pane preview).
2. **Media**: inventory → contact sheet → curate → size probe → download → transcode → manifest.
3. **Prototype lab** (`/lab`)
   - Three heroes: A = reel fan + mixed type; B = giant cropped wordmark over a two-row reel ticker texture; C = reel chips inline inside the headline.
   - The display-font A/B.
   - The manifesto scrub, the screenings rail and the method route line.
   - Run each, then screenshot at 1440 and 390, run the creative-director + motion-director critique, and choose. **Build → run → see → change.**
4. **System**: `src/lib/motion/*`, SmoothScroll, Nav (all states), page transitions, Footer.
5. **Home**: Acts 0–9 in order, inspected in the browser after each act.
6. **Work**: `/work` index + Flip filters, then the `/work/[slug]` screenings.
7. **Capabilities**: index, then the 18 division pages with their own route lines.
8. **Approach, Route builder and enquiry adapters.**
   - *Learning-mode human contribution:* the `suggest.ts` mapping (audience + goal → divisions) is left as the single `TODO(human)` for the user to author.
9. **Responsive and accessibility pass**
   - Responsive choreography at 1440×900, 1280×720, 1024×768, 768×1024, 390×844 and 360×800.
   - Reduced motion.
   - Semantic HTML, keyboard navigation, visible focus, labels, alt text, contrast.
   - SEO.
10. **QA loop**
    - Dev server, then the Playwright screenshot suite (6 viewports × key routes × motion keyframes, plus a reduced-motion project).
    - Run the **5 reviewers in parallel** (workflow, model pinned) and fix every substantive finding.
    - Second review round, then `next build` + `next start` and fix the remaining issues.

## Verification

- `npm run build` succeeds (type-check included), and `npx eslint .` is clean.
- **Playwright** (`tests/`):
  - Screenshots for every route at the 6 viewports and the motion-critical scroll positions.
  - Nav and menu behaviour.
  - The Route builder end-to-end: selection persists and the WhatsApp/mailto URLs are well-formed.
  - No horizontal overflow at 360px.
  - Reduced-motion rendering shows all content with no pins.
  - axe a11y with zero serious violations.
- **Content audit test**: all 18 divisions are present with their PDF names; the contact details match the PDF; a grep guard fails on invented-proof patterns (testimonial / award / "% growth" / client counts / years).
- **Performance budget** (production build): CLS < 0.05; hero poster ≤ 200 KB; video downloaded on first view ≤ 4 MB; ≤ 4 concurrent videos; no long tasks > 200 ms while scrolling the pinned acts (Playwright trace). Mid-range mobile is emulated at 4× CPU throttle.
- **Visual sign-off**: Playwright screenshots are reviewed for each act, together with motion-director and creative-director verdicts.
- **Browser-pane check**: a live check of the running site via `preview_start` on the `.claude/launch.json` dev config.
