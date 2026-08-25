# "Is this possible?" feasibility checker for Build a House (deferred)

## Status

Deferred - proposed and discussed on 2026-08-25, not started. Recorded here so
the approach doesn't have to be re-derived when picked up.

## What this is

User request: if someone asks for requirements that can't realistically fit
the stated plot (e.g. a front yard + backyard + a basketball court on a small
plot), tell them it's not possible and suggest what to drop/change - instead
of silently generating something that ignores half the request.

## Recommended approach: hybrid (deterministic math + Gemini reasoning), NOT
pure-LLM and NOT pure-math

Three approaches were considered:

1. **Pure Gemini ("just ask the LLM if it fits")** - rejected. LLMs are
   unreliable at real area arithmetic ("does a basketball court fit in
   40x30ft?") - a feasibility checker that confidently gives WRONG verdicts
   is worse than no checker at all, actively destroys trust.
2. **Pure deterministic math (no AI)** - rejected as the sole approach.
   Accurate, but brittle: the "extras" field is free text (anything from
   "basketball court" to "koi pond" to "meditation garden") - a hardcoded
   area-lookup table can't cover arbitrary user phrasing, and can't produce
   natural-language suggestions.
3. **Hybrid (recommended)**: Python computes the authoritative numbers (plot
   area from length x width, buildable area x floor count, and a small known-
   footprint table for common extras like "basketball court" ~ 1000 sqft
   half-court), THEN Gemini is given those real numbers + the user's stated
   requirements and produces the verdict + natural-language suggestions.
   Gemini never invents the numbers, only reasons over them and phrases the
   response - the same "real data -> LLM synthesizes, LLM never guesses the
   hard numbers" pattern this codebase already uses for materials pricing
   (see CLAUDE.md's "Materials & pricing feature" section, specifically the
   SerpApi-search-then-Gemini-synthesizes design) and worth reusing directly
   rather than reinventing.

## Two open design decisions (ask the user before building)

1. **Advisory warning vs. hard gate.** Recommended: advisory - show the
   warning + suggestions but still let the user generate anyway if they
   want. A hard block that's occasionally wrong (edge cases, unusual but
   valid layouts) would be actively obstructive; an advisory suggestion that's
   occasionally wrong is just ignorable. Needs explicit confirmation before
   building either way, since a hard gate is a real behavior change to the
   upload flow.
2. **When it runs.** Recommended: at input time, before generation starts (a
   "Check feasibility" step, or inline on submit) - so it saves a wasted
   generation (and, if `HOUSE_RENDER_ENABLED`/real render is on, wasted
   OpenAI/Kaggle cost) rather than only warning after the fact on the
   results screen.

## Where this would plug in

- Reuses `dimensions` (`length`/`width`/`unit`) and the structured
  `house_inputs` (`floor_count`/`bedrooms`/`bathrooms`/`extras`) already
  collected by the "Plot Parameters" panel (`static/index.html`,
  `app/main.py`'s `create_house_project`) - no new inputs needed to start.
- A new best-effort Gemini call, same shape/cost profile as
  `analyze_plot()`/`generate_room_layout()` (free text quota, not the paid
  image path) - would live in `app/providers/gemini.py` alongside those.
- Frontend: a warning/suggestion panel shown after the structured inputs are
  filled in, before (or instead of, if a hard gate) the Generate button -
  exact placement TBD when this is picked up.

## Why deferred

Proposed in the same session the friend-hosted AutoCAD Kaggle model was
wired in (see CLAUDE.md's "Real DXF/AutoCAD-format export" entry) - parked
to keep that work scoped, not because of any technical blocker.
