# Brag Plan: Interior-Gen

## What is this app?
Interior-Gen turns one room photo into three structurally-faithful, fully-priced interior redesigns — or a bare plot's dimensions into a computed, feasibility-checked house floor plan with a real AutoCAD export.

## The angle
The product's own tagline already IS the hook: "Picture it before you build it." The angle is a quiet, confident reveal — the same photo, the same walls and windows, transformed three real ways, followed by the fact that isn't obvious from a screenshot: every one of those looks comes back with real, itemized pricing, not a guess. The video should feel like watching a real architectural/interior consultation compress into seconds, not like a generic "AI app" ad.

## Hook (first 2-3 seconds)
The display headline itself, exactly as it reads on the live product: "Picture it — before you build it." set in the site's own serif italic treatment over the real premium hero photo. No logo yet. The claim comes first.

## Key moments (the middle)
- The real 4-image sequence — Original → Economical → Mid-Range → Premium — revealing one by one, using the actual site imagery, each tier landing with its own label exactly as styled on the live results grid.
- The pricing beat: "Know what each tier actually costs to build" with the real per-tier material categories (paint, flooring, lighting, ceiling, feature wall, decor) — the thing that separates this from a plain image generator.
- The second product, in one confident beat: plot dimensions in → a computed floor plan out, with a genuine AutoCAD (.dxf) export — proof this isn't just a photo filter, it's a real planning tool.

## Outro / punchline
The wordmark "Interior‑Gen" in the site's own oversized ghost-type treatment, tagline restated once, clean: "Picture it before you build it."

## User flow worth showing
1. **Entry** — a room photo dropped onto the real upload dropzone ("Drop a room photo here").
2. **Key action** — style parameters selected (Interior style + Color palette), then generation.
3. **Result** — the three priced tiers land side by side with the original, structure visibly unchanged (same walls, windows, camera angle).

Secondary flow (Build a House, compressed into one beat given runtime): plot dimensions entered → computed floor plan + AutoCAD export appear.

## Tone
- Preset: polished
- Creative direction: a quiet, premium product reveal — real-estate editorial photography crossed with a software demo, not a flashy tech-startup sizzle reel.
- Interpretation: fewer scenes, longer holds, soft crossfades instead of hard cuts. Confidence through restraint — let the real imagery and real copy carry the video rather than motion tricks or loud claims.

## Format: landscape — 1920x1080
## Duration: ~21s

## Visual identity (from the project)
- Background: `#faf6ee` (warm linen)
- Accent: `#7A4712` (primary, warm brown-gold) / `#6E3F0F` (home-gold)
- Text: `#33362e` (on-background/on-surface, warm charcoal) and `#44403C` (home-stone-dark, headings)
- Display font: Fraunces (serif, used italic for the emotional word in headlines — e.g. "before you build it.")
- Body font: Poppins
- Strongest visual element: the real Original → Economical → Mid-Range → Premium image set at `static/examples/*.webp` — an unedited, real product output, not a mockup.

## Share copy (draft)
One photo in, three real redesigns out — structure preserved, real material pricing included. Interior-Gen: picture it before you build it.

## Audio direction
- Role: warm, restrained instrumental bed with minimal accent SFX
- Music: `happy-beats-business-moves-vol-12-by-ende-dot-app.mp3` — "Steady and clean," recommended for polished/cinematic tone
- Music treatment: starts under the hook at low volume (~0.3), stays steady through the middle, small swell under the final logo card, short fade-out in the last ~1s
- Music cue guidance: bundled preset read (tempo 109.96 BPM). Scene boundaries targeted near strong cues: ~3.27s (end hook), ~6.56s (end reveal), ~13.11s (end transformation), ~17.47s (end pricing/second-product beat), ~19.66s (logo card lands), outro settles by ~21s. Sequential 4-tier reveal in Scene 3 uses the beat grid (~0.55s spacing) as a loose alignment guide only, not a hard snap — each tier still holds to its full reading floor.
- Audio-reactive treatment: subtle; hero glow / accent-color presence may breathe gently with RMS. No waveform or equalizer visuals.
- SFX posture: minimal but present (polished) — 2-3 cues total, nothing aggressive.
- Audio-coupled moments: a soft `interface/drop_001`-style cue as each of the 4 tier images lands (accent the first and last only, not all four); one gentle `interface/bong_001`-style accent under the pricing stat; one quiet bell-style hit under the final logo card.
- Restraint rule: no dense/rhythmic scoring, no per-item SFX on all four tier reveals, nothing louder than a soft accent — this is an investor-facing, premium product reveal, not a hype reel.

## Storyboard

### Scene 1 — Hook — 3.3s
Real premium hero photo (`static/examples/premium.webp`) fills the frame, dimmed slightly. Headline text settles on top, matching the live site's treatment: "Picture it" then, a beat later, italic accent "before you build it." in the site's gold-soft tone.
Sequential/interaction: none
Audio intent: warm, confident open — let the music bed establish before anything else happens
Audio-coupled idea: none (no SFX on the hook itself — let it breathe)
Music: bed fades in under the hero image
Transition mood: soft crossfade → Scene 2

### Scene 2 — Reveal — 3.3s
Recreate the real upload dropzone from the Room Redesign tab: "Drop a room photo here · click to browse · JPG, PNG, WEBP" with the site's actual dashed-corner camera-icon treatment. A cursor drags the real `original.webp` room photo onto it; the dropzone swaps to the photo preview state.
Sequential/interaction: yes — simulated drag-and-drop of the original room photo onto the real dropzone UI
Audio intent: a small, satisfying "it's working" moment
Audio-coupled idea: soft drop-style SFX exactly as the photo lands in the dropzone
Music: steady bed continues
Transition mood: soft crossfade → Scene 3

### Scene 3 — Key highlight: the transformation — 6.55s
The four real tier images reveal one by one, left to right, each with its real label exactly as styled on the live results grid: Original → Economical → Mid‑Range → Premium. All four settle together at the end with the real supporting line from the site: "The same walls, windows, and layout carried through all three tiers."
Sequential/interaction: yes — 4 images arrive one by one (roughly evenly spaced across the scene, each held to a full readable settle, not rushed), then hold as a set
Audio intent: build quiet confidence as each real result lands
Audio-coupled idea: soft landing SFX accented on the FIRST (Economical) and LAST (Premium) image only — not all four, to stay restrained
Music: steady bed, loose alignment to the beat grid for the 4 arrivals
Transition mood: soft crossfade → Scene 4

### Scene 4 — Key highlight: real pricing + the second product — 4.36s
Left half (first ~2.5s): the real stat card copy — "Know what each tier actually costs to build" — with the six real material categories (paint, flooring, lighting, ceiling, feature wall, decor) appearing as a tight label row, not six separate beats.
Then a clean crossfade (remaining ~1.9s) into the second product in one confident beat: plot dimensions ("40 × 60 ft") transforming into a real computed floor-plan blueprint image, with the line "+ a real AutoCAD (.dxf) export" settling underneath.
Sequential/interaction: label row for the 6 material categories arrives as one grouped reveal, not sequential per item (too much text for the time budget otherwise)
Audio intent: the "this is a real tool, not a toy" moment — slightly more weight than Scene 3
Audio-coupled idea: one gentle bell-style accent under the pricing stat's arrival
Music: steady bed, no cue on the internal crossfade (avoid rushing it)
Transition mood: soft crossfade → Scene 5

### Scene 5 — Outro / punchline — 3.5s
The site's own oversized ghost-type wordmark "Interior‑Gen" settles centered on the warm linen background, small and confident (not shouting). Tagline restates once, smaller, underneath: "Picture it before you build it."
Sequential/interaction: none
Audio intent: a settled, confident close — not a hype sting
Audio-coupled idea: one quiet bell-style hit as the wordmark lands, then let it hold in silence-adjacent quiet as the bed fades
Music: gentle swell then fade-out in the final second
Transition mood: hold to black/end

**Music mood for this video:** polished, steady, warm — corporate-adjacent but not sterile
**Audio summary:** One steady instrumental bed under the whole video with three restrained accent cues (drop-in, pricing beat, final logo) — never louder than the visuals, reinforcing a premium, investor-facing reveal rather than a hype reel.
