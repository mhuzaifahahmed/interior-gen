---
name: gsap-transitions
description: Add smooth GSAP-powered animations and transitions to Interior-Gen's vanilla HTML/JS frontend (no build step, CDN-loaded) — hero/section reveals, tab switches, staggered grids, hover micro-interactions, and progress-stepper motion. Use when the user asks for smoother animations, page/tab transitions, scroll effects, or "make it feel less static."
license: MIT
metadata:
  author: interior-gen
  version: "1.0.0"
---

# GSAP Transitions Skill

Adds professional, physics-based motion to `static/index.html` / `static/app.js` using GSAP loaded from CDN — no npm, no build step, consistent with this project's "vanilla frontend" convention (see `CLAUDE.md`).

## Setup (one-time)

Add to the `<head>` of every HTML page that needs animation (currently only `index.html` needs this):

```html
<script src="https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/ScrollTrigger.min.js"></script>
```

Register the plugin once, near the top of `app.js`:

```js
gsap.registerPlugin(ScrollTrigger);
```

**Respect `prefers-reduced-motion`** — wrap all entrance/scroll animations in a check so motion-sensitive users get an instant, static layout instead:

```js
const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
```

## Where this replaces existing CSS

`static/components.css` currently has a plain CSS `[data-reveal]` + `IntersectionObserver` scroll-reveal (`app.js`, "Scroll reveal" section). GSAP + ScrollTrigger can do the same job with more control (stagger, easing curves, scrub-tied-to-scroll effects) — but **don't duplicate both systems on the same element**. Pick one per section: keep the cheap CSS version for simple fades, reach for GSAP when you need staggering, sequencing, or scroll-scrubbed motion.

## Core Patterns

### 1. Section entrance (replaces `[data-reveal]` with more control)

```js
gsap.utils.toArray("[data-gsap-reveal]").forEach((el) => {
  gsap.from(el, {
    y: 28,
    opacity: 0,
    duration: 0.9,
    ease: "power3.out",
    scrollTrigger: {
      trigger: el,
      start: "top 85%",
      once: true, // don't re-trigger on scroll back up
    },
  });
});
```

### 2. Staggered grid (tier cards, example thumbnails, process steps)

```js
gsap.from("#examples figure", {
  y: 24,
  opacity: 0,
  duration: 0.7,
  ease: "power2.out",
  stagger: 0.08, // 80ms between each card — matches the 30-50ms/item guidance for larger grids, slightly slower reads better for 4-8 items
  scrollTrigger: { trigger: "#examples", start: "top 80%", once: true },
});
```

### 3. Tab switch crossfade (Home / Room Redesign / Build a House)

Replace the instant `el.hidden = true/false` toggle in `switchTab()` with a crossfade:

```js
function animateTabSwitch(hideEl, showEl) {
  const tl = gsap.timeline();
  tl.to(hideEl, {
    opacity: 0,
    duration: 0.18,
    ease: "power1.in",
    onComplete: () => { hideEl.hidden = true; },
  }).set(showEl, { opacity: 0, hidden: false })
    .to(showEl, { opacity: 1, duration: 0.28, ease: "power2.out" });
}
```

Exit is intentionally shorter than enter (~65%) per standard motion guidance — feels responsive, not sluggish.

### 4. Button press/hover feedback

```js
document.querySelectorAll(".js-cta-room, #home-hero-house-btn").forEach((btn) => {
  btn.addEventListener("mouseenter", () => gsap.to(btn, { y: -2, duration: 0.2, ease: "power2.out" }));
  btn.addEventListener("mouseleave", () => gsap.to(btn, { y: 0, duration: 0.2, ease: "power2.out" }));
});
```
(Tailwind's `hover:-translate-y-0.5` already does this via CSS — only switch a given button to GSAP if you need something CSS can't do, like a spring overshoot.)

### 5. Progress stepper / stage transitions (Room Redesign & Build a House progress cards)

When `deriveStageIndex()` advances a stage, animate the fill instead of snapping:

```js
gsap.to("#progress-bar-fill", {
  width: `${pct}%`,
  duration: 0.6,
  ease: "power2.out",
});
```

### Known gotcha, verified live in this project (2026-08)

`gsap.from(target, { ...stagger... })` chained inside a `gsap.timeline()` at an overlapping
`"-=N"` position, targeting an array/NodeList of 2+ elements, got **silently stuck at its start
values** — the timeline itself reported `progress() === 1` (fully complete) but the DOM elements
never received their final `opacity`/`transform`. Non-staggered `.from()` calls in the same
timeline worked fine; only the staggered one froze. Root cause not fully isolated (suspected
interaction between `.from()`'s implicit end-value capture, `stagger`, and the negative overlap
position within this GSAP CDN build), and it reproduced with `gsap.utils.toArray()`-wrapped
targets too, so it isn't a plain NodeList issue.

**Fix that reliably works**: explicit `gsap.set(targets, { ...startValues })` immediately before
the timeline, then `.to(targets, { ...endValues })` instead of `.from()`. Use this pattern for
*any* staggered tween inside a timeline in this project — don't reach for `.from()` + `stagger` +
overlap position again without testing it live first (verify with a real screenshot/inline-style
check after the wait, not just "no console error").

### 6. Concept-preview image reveal (when a tier's image lands mid-generation)

```js
function revealConceptTier(tierKey, url) {
  // ...existing DOM insertion...
  gsap.from(imgEl, { opacity: 0, scale: 1.04, duration: 0.5, ease: "power2.out" });
}
```

## Rules for This Project

- **Never block input during an animation** — GSAP timelines must not `preventDefault()` clicks or add invisible overlays that eat clicks mid-transition.
- **Transform/opacity only** for anything scroll-tied or frequent — never animate `width`/`height`/`top`/`left` in a ScrollTrigger callback (causes layout thrash). Exception: the progress-bar-fill width tween above is low-frequency (few times per generation), acceptable.
- **150–400ms for micro-interactions**, up to 600ms for hero/section entrances. Nothing above ~800ms.
- **`once: true` on ScrollTrigger** for entrance reveals — this is a one-way marketing page, not a scrubbing interactive doc; re-triggering on scroll-up feels glitchy.
- **Keep `data-reveal` working** where GSAP isn't explicitly wired in — don't rip out the existing IntersectionObserver system project-wide in one pass; migrate section by section so nothing goes permanently invisible if a script errors.
- Kill/revert any in-flight timeline on `switchTab()` re-entry (rapid tab clicking) via `gsap.killTweensOf(el)` before starting a new one, to avoid stacking conflicting tweens.

## Debugging

If an element never appears (stuck at `opacity: 0`): check that `ScrollTrigger` script tag loaded (network tab), that `once: true` didn't already fire and get killed by a duplicate script re-run, and that the trigger element isn't `hidden` at animation-registration time (GSAP can't measure a `display: none` element's position correctly — register triggers only for currently-visible tab panels, or re-run `ScrollTrigger.refresh()` after `switchTab()`).
