# Hyperframes Composition Brief: Interior-Gen

## Objective
Create a short, polished launch-style brag video for Interior-Gen — an AI interior-design and house-planning platform.

## Output
- Composition directory: `brag-output/composition/`
- Rendered video: `brag-output/brag.mp4`
- Format: landscape — 1920x1080
- Duration: ~21 seconds (15-25s hard bounds)

## Source Material
- Project root: `c:/Users/User/interior-gen`
- Primary files read: `static/index.html`, inline Tailwind config in `index.html` (colors/fonts), `README.md`, `static/examples/*.webp`
- Product name: Interior-Gen
- Tagline / strongest claim: "Picture it — before you build it." / "100% original structure preserved" / "Know what each tier actually costs to build"
- Key UI or visual moment to recreate: the real upload dropzone ("Drop a room photo here · click to browse · JPG, PNG, WEBP") and the real 4-image results grid (Original / Economical / Mid-Range / Premium)
- Copy that must appear verbatim:
  - "Picture it before you build it."
  - "Drop a room photo here"
  - "Original" / "Economical" / "Mid-Range" / "Premium"
  - "The same walls, windows, and layout carried through all three tiers."
  - "Know what each tier actually costs to build"
  - "+ a real AutoCAD (.dxf) export"

## Creative Direction
- Tone preset: polished
- Creative direction: a quiet, premium product reveal — real-estate editorial photography crossed with a software demo, not a flashy tech-startup sizzle reel. Investor-facing.
- Interpretation: fewer scenes, longer holds, soft crossfades instead of hard cuts. Confidence through restraint — real imagery and real copy carry the video, not motion tricks or loud claims.
- Angle: The product's own tagline already is the hook. Show the same room transformed three real, priced ways, then reveal that the pricing is real (not a guess), then close with the fact that this same engine also plans whole houses with a genuine AutoCAD export.
- Hook: "Picture it" then, a beat later, italic "before you build it." over the real premium hero photo.
- Outro / punchline: the wordmark "Interior‑Gen" settling on warm linen, tagline restated once beneath it.
- Avoid:
  - Generic SaaS language ("streamline your workflow" etc. — none of that appears in this product's real copy anyway)
  - Abstract filler visuals / stock motion graphics
  - Any UI element, stat, or claim not actually present in the real product

## Visual Identity
- Background: `#faf6ee` (warm linen)
- Text: `#33362e` (on-background) / `#44403C` (home-stone-dark, for headings) / `#78716C` (home-stone, for body-on-light)
- Accent: `#7A4712` (primary) / `#6E3F0F` (home-gold) / `#C08A4E` (primary-fixed-dim, lighter gold for highlights)
- Display font: Fraunces (serif; italic used for the emotional word, exactly as the live site does — e.g. "before you build it.")
- Body font: Poppins
- Visual references from the project: `static/examples/original.webp`, `static/examples/economical.webp`, `static/examples/mid.webp`, `static/examples/premium.webp` (real, unedited product output — the strongest asset available), `static/logo.png`

## Storyboard
Use the storyboard in `brag-output/brag-plan.md` as the creative contract.

Scene summary:
1. Hook — 3.3s — real premium hero photo, headline "Picture it *before you build it.*" settles on top
2. Reveal — 3.3s — the real upload dropzone UI; the real original room photo drags/drops onto it
3. Key highlight: the transformation — 6.55s — the 4 real tier images (Original → Economical → Mid-Range → Premium) reveal one by one with their real labels, then hold together with the real caption line
4. Key highlight: real pricing + the second product — 4.36s — the real pricing claim + 6 material categories, crossfading into plot dimensions → a computed floor-plan blueprint + "a real AutoCAD (.dxf) export"
5. Outro / punchline — 3.5s — the real wordmark treatment + tagline restated

## Audio
- Audio role: warm, restrained instrumental bed with minimal accent SFX (polished posture)
- Audio arc: bed fades in under the hook, stays steady through the middle scenes, small swell under the final logo card, short fade-out in the last ~1s
- Music: `happy-beats-business-moves-vol-12-by-ende-dot-app.mp3` (already copied into `composition/assets/music/`)
- Music treatment: start volume ~0.3, steady through the body, gentle swell (not louder than ~0.4) under the outro logo card, fade to 0 by the final frame
- Music cue guidance: bundled preset at `<skill-dir>/assets/music/cues/happy-beats-business-moves-vol-12-by-ende-dot-app.music-cues.md/.json` — tempo 109.96 BPM. Suggested strong-cue locks (use within ±0.15s, pick whichever best matches the final implemented timing, no more than 1-3 total): ~3.27s (hook → reveal transition), ~6.56s (reveal → transformation transition), ~13.11s (transformation → pricing/second-product transition). Sequential 4-tier reveal in Scene 3 may loosely align to the beat grid (~0.55s spacing) but each tier must still hold to its full reading floor — do not rush.
- Audio-reactive treatment: subtle; the hero glow / accent-gold presence on cards may breathe gently with RMS. No waveform, equalizer, or particle visuals.
- Audio-coupled moments:
  - Scene 2 (photo lands in dropzone) — soft drop-style SFX at the moment of landing
  - Scene 3 (first and last tier image only — Economical and Premium) — soft landing SFX; skip SFX on the middle two to stay restrained
  - Scene 4 (pricing stat arrives) — one gentle bell/accent-style SFX
  - Scene 5 (logo lands) — one quiet bell-style hit, then let it ring into the fade
- SFX selection guidance: match the polished tone's SFX energy table — minimal but present, nothing aggressive, prefer `interface/drop_*` and `interface/bong_001`-style low/medium high-frequency-risk sounds.
- SFX analysis guidance: consult `<skill-dir>/assets/sfx/sfx-analysis.md` — prefer low/medium high-frequency-risk files for these repeated, polished moments.
- Exact SFX choice: Hyperframes should choose exact filenames, timestamps, density, and volume based on the implemented animation.
- Audio files: music already copied into `brag-output/composition/assets/music/`. Copy any chosen SFX into `brag-output/composition/assets/sfx/` (directory already created).

## Hyperframes Instructions
Load the composition-building Hyperframes domain skills — `hyperframes-core` (composition contract + `data-*` timing), `hyperframes-animation` (motion), `hyperframes-creative` (design spec, beats, audio-reactive), `hyperframes-keyframes` (seek-safe keyframes), and `hyperframes-cli` (lint/check/render). `/brag` is its own workflow: do not enter the `hyperframes` entry-point intent interview and do not route into its generic promo / launch-video workflow. Prefer native Hyperframes conventions over anything in `/brag`.

Requirements:
- Show at least one real UI, copy, or visual element from the source project (the upload dropzone and the 4-image results grid both qualify — use both).
- Keep all text readable in the final render (respect the reading-time floors from `brag-plan.md`).
- Keep the video within 15-25 seconds.
- Include the planned music/SFX layer (not disabled, not documented as silent).
- Treat the audio notes above as guidance, not a fixed cue sheet — choose exact SFX after the visual animation exists.
- Treat music cue metadata as optional timing hints; ignore cues that hurt readability, scene pacing, or the product story.
- Use only 1-3 strong cue locks total.
- Honor the planned fade-in/fade-out and outro swell using the best Hyperframes-supported implementation.
- Use local project assets (`static/examples/*.webp`, `static/logo.png`) directly for imagery — do not regenerate or restyle the real product photos.
- Run `hyperframes check` before render — it is brag's single gate.
