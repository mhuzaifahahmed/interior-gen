# Build a House: theme/style parity with Room Redesign (deferred)

## Status

Deferred. Parked on 2026-08-12 while focusing on making the "Build a House"
tab's core output (the AI render + the CAD-style blueprint) good first, one
parameter at a time, before touching visual polish. See the backend/floor-
logic work this was split off from - two renders (photo + blueprint-based), a
dedicated higher render quality, a full CAD-look blueprint renderer, and
floor-count/common-sense room placement.

## What this covers when picked back up

The house tab already shares Room Redesign's exact "Sage & Linen" theme
tokens and component CSS (`.result-card`, the progress stepper, etc.) - this
is **polish-porting, not a retheme**. Specifically:

1. **Image hero + gradient scrim** above the house upload header, matching
   the room tab's pattern (`static/index.html`, room panel ~lines 393-403:
   a `bg-cover` background image at `brightness-50` with a
   `from-background/70 via-background/85 to-background` gradient scrim).
   The house tab currently has just a plain centered text header on
   `bg-background`.

2. **"READY TO ANALYZE" status chip** on the house dropzone, matching the
   room tab's pattern (`static/index.html` ~lines 429-434: a
   `bg-surface-container-lowest` strip with a pulsing `bg-primary` dot).

3. **Live concept-preview shimmer tiles during generation**, reusing the
   room tab's `.concept-card`/`.concept-skeleton` CSS
   (`static/components.css` ~lines 258-310) - slots for the primary render,
   the layout render, and the floor blueprint(s), each filling in as its
   image URL appears mid-poll (mirror `applyHousePollUpdate()`'s room-tab
   equivalent).

4. **Results hierarchy** - make the photoreal `render` the hero card
   (spanning the grid width), with a secondary row underneath for
   `render_from_layout` ("3D Layout Render"), the original plot photo, and
   the per-floor blueprint cards. Today (as of the two-render backend work)
   all of these render as uniform same-size `.result-card`s in
   `renderHouseResults()` (`static/app.js`) with no visual hierarchy telling
   the hero apart from the supporting images.

## Why deferred

Explicit user call: "I wana first focus on a single output to be great then
i will touch different parameters one by one." Styling work adds surface
area to review before the core render/blueprint quality is validated.

## Reference files

- `static/index.html` - room tab panel (hero/chip patterns to copy),
  house tab panel (`#house-tab-panel`)
- `static/app.js` - `renderResults()`/`showState()` (room) vs.
  `renderHouseResults()`/`showHouseState()` (house)
- `static/components.css` - `.result-card`, `.concept-card`/
  `.concept-skeleton`, the stage-stepper classes
