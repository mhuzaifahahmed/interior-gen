# Commit quality log

A running log of commit hashes on `dev`, each with an honest one-line note on the actual state/quality of
the feature at that point - not a full changelog, just enough to know what a given commit is actually
like without re-checking by hand. Add a new entry each time a commit is checked and confirmed (or found
lacking) - newest first.

- **`79a6873`** (2026-09-02) — **Best Build a House result so far.** Room labels/text stay clean (unchanged
  from `de216a8`) and doors no longer collide visually with them (a real bug hit and fixed while building
  this). New this commit: the deterministic layout gained real, if modest, architectural intelligence - a
  genuine hallway corridor for the private zone (`floor_layout.py`'s `_layout_private_zone()`/
  `_pack_row()`, ≥3 private rooms + enough space) that rooms actually open onto instead of doors going
  straight between unrelated bedrooms (`blueprint_svg.py`'s `_should_suppress_direct_door()`) - visually
  confirmed, not just unit-tested. Also: the Kaggle Concept Layout call is now fully decoupled from the
  project's own completion (so a slow/offline notebook session never blocks the deterministic results),
  a real Kaggle session-offline detector (`app/providers/session_errors.py`) replaces silent
  degradation/raw tracebacks with an actionable message, and a real production robustness bug (SQLite
  writer lock contention with no busy-timeout, silently killing a background thread) was found and fixed
  along the way. Still boxy at the geometry level - no L-shapes, no organic room shaping, no
  validate-and-regenerate loop, no candidate scoring; see
  [house-layout-spec-checklist.md](house-layout-spec-checklist.md) items #17/#20/#21 for what's still
  open.
- **`de216a8`** (2026-08-27) — Build a House: room labels/text render
  clean (legible, not garbled) thanks to programmatic label compositing onto the AI "Concept Layout" card
  (`kaggle_autocad.py`'s `_composite_room_labels()`) instead of letting SDXL draw text. But the
  deterministic floor-plan geometry itself still looks **too boxy** (pure rectangular slice-and-dice, no
  L-shapes, no organic room shaping) and shows **no real architectural intelligence yet** - no real
  staircase footprint/shape, no adjacency solver beyond public/private zone clustering, no
  validate-and-regenerate loop, no candidate scoring. See
  [house-layout-spec-checklist.md](house-layout-spec-checklist.md) items #16/#17/#20/#21 for the exact
  gaps this maps to.
