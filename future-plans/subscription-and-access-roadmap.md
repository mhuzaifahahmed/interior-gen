# Subscription & access roadmap

**Status: NOT IMPLEMENTED.** This file records decisions and research the user has already approved
in conversation, kept here so a future session can pick up the subscription build without re-deriving
context. Nothing in this file should be built, wired, or allowed to affect current generation logic
until the user explicitly says to start that phase. Today, generation is unmetered for every logged-in
user — no quotas, no per-request model choice, no payment anywhere in the codebase.

## Why this exists

The product is moving toward a paid subscription model. Kaggle/"our model" generation is effectively
free to run (bounded only by a 30 GPU-hr/week/account quota); OpenAI (`gpt-image-1`) generation has a
real per-call cost. The plan is to gate the more expensive/higher-fidelity OpenAI path behind paid tiers
while keeping the free/Kaggle path as the always-available option, plus a short unauthenticated trial
window before login is required at all.

## Approved pricing (2026-09-03, user-confirmed "Approve as proposed")

Cost basis: Kaggle ≈ PKR 0 marginal cost. OpenAI room generation (3 tiers, low/low fidelity) ≈
$0.10/generation (~PKR 28 at ≈280 PKR/USD). OpenAI house render (high/high fidelity) ≈
$0.19/generation (~PKR 53) — House costs roughly 2× Room, hence separate/lower House quotas throughout.

| | **Free** | **Pro — PKR 2,499/mo** | **Studio — PKR 6,999/mo** |
|---|---|---|---|
| Model access | Kaggle/our model only | Kaggle + OpenAI toggle | Kaggle + OpenAI toggle |
| Room generations | 5/mo (Kaggle) | Kaggle 150/mo fair-use; OpenAI 30/mo | Kaggle unlimited fair-use; OpenAI 100/mo |
| House generations | 3/mo (Kaggle) | Kaggle 60/mo fair-use; OpenAI 10/mo | Kaggle unlimited fair-use; OpenAI 40/mo |
| Free retries/generation | 0 (every retry counts) | 2 free, then counts as a generation | 5 free, then counts as a generation |
| Premium features | — | Model toggle, priority queue, full History | + commercial-use license, HD house renders, bulk/export, priority support |
| Est. worst-case OpenAI cost | $0 | ~$4.9/mo | ~$17.6/mo |
| Margin at max usage | n/a | positive (~$4) | positive (~$7) |

**Free-trial strategy (approved: "Add a 7-day Pro trial too"):** the Free tier is always-on and doubles
as a baseline trial (5 room + 3 house/mo, Kaggle-only, no time limit). ADDITIONALLY, offer a **7-day
full Pro trial** (includes the OpenAI toggle and Pro's quotas) to accelerate conversion for users who
want to see OpenAI-quality output before paying — exact trigger (on signup? on first upgrade-prompt
click?) and abuse-prevention (one trial per user/payment method) are undecided, to be designed during
the backend/payment phase.

**Retry policy:** a retry (regenerate with the same inputs) counts as a generation, except a small
per-generation free-retry buffer on paid plans (2 on Pro, 5 on Studio) so one bad output doesn't feel
like a wasted purchase. Free plan: every retry counts fully.

**Room vs. House:** deliberately separate quotas — House's OpenAI render is the more expensive call.

## Pre-login / post-login access rules (user's literal spec, 2026-09-03)

Kept exactly as given — do NOT implement until the backend/payment phase, and keep this fully separate
from the current (unmetered) generation logic so nothing here can accidentally take effect early.

**Before login:**
- 1 generation for Room Redesign.
- 1 generation for Build a House.
- User may choose OpenAI or Kaggle for each.

**After those are used:**
- Login required.
- After login, allow 1 additional generation, user again choosing OpenAI or Kaggle.

**After that:**
- A minimum paid subscription is required to generate further.
- Kaggle generations are bounded by the plan's allowed quota (see pricing table above).
- OpenAI generations are bounded by the plan's OpenAI allowance (see pricing table above).
- Retry/regeneration limits may differ per plan (see "Retry policy" above — subject to refinement
  when this phase is actually built).

## What's already built vs. what this roadmap covers

**Already built (this session, approved short tasks — see CLAUDE.md/commit history for detail):**
- Model-attribution message ("Generated using our model" / "Generated using OpenAI") — already existed,
  wording tightened.
- Build-a-House plot photo made optional (Room Redesign photo stays mandatory — it's an image-edit
  input there, not decorative).

**NOT built — this entire file:**
- Any quota tracking/enforcement (DB fields, decrement-on-generate, reset-on-billing-cycle logic).
- Any payment integration (Stripe or a Pakistan-specific processor — not yet researched).
- Per-request OpenAI-vs-Kaggle model routing on the backend (`HybridProvider` already has both
  providers wired for both Room and House — see CLAUDE.md's "Provider split" — so this is a routing
  change, not new provider code, when it happens).
- The Packages/Pricing page and the OpenAI/Kaggle toggle UI (Part C of the working plan this file's
  research came from — deferred until the user explicitly asks for the subscription frontend pass).
- The pre-login/post-login free-generation counter described above.

## Decisions made (2026-09-12, build phase started on feat/subscription-and-access)

- **Payment processor: researched, not yet chosen.** Stripe ruled out entirely — Pakistan has no
  Stripe support (SBP requires local PSO/PSP licensing, which Stripe hasn't obtained; a foreign-LLC
  workaround gives no PKR settlement/local rails). Compared Safepay (SBP-regulated PSO, native
  tokenized recurring billing, transparent fees ~2.9%+Rs30/domestic txn, developer-friendly REST API —
  **recommended**), PayFast Pakistan (has a subscriptions API but multiple sources report subscription
  businesses needing workarounds or migrating off it), and JazzCash (token-based recurring exists but
  it's fundamentally a mobile-wallet API with heavier merchant onboarding; some third-party libraries
  report subscription features "not fully enabled"). User chose to hold off committing to one — the
  quota/plan backend below is being built processor-agnostic (a plan is just a string on a DB row) so
  whichever processor is picked later only needs a webhook handler that flips that field, not a
  redesign.
- **Free-tier / access-rules reconciliation**: the pricing table's ongoing "5 room + 3 house/month"
  Free tier IS the real, permanent post-login state — the access-rules section's literal "1 additional
  generation after login" was early/rough wording, superseded by the pricing table. So: pre-login
  trial (1 room + 1 house, anonymous, user's choice of Kaggle/OpenAI) -> login -> the account is simply
  on the Free plan and gets its full 5/3 monthly quota (Kaggle-only, no OpenAI access) -> upgrading to
  Pro/Studio is required only once THAT quota (or wanting OpenAI at all) is exceeded.
- **Retry-buffer feature (2/5 free retries per generation on Pro/Studio before a retry counts as a new
  generation) is explicitly DEFERRED**, not dropped. It requires a "regenerate this same generation"
  concept (a parent/retry link between projects, a regenerate endpoint/button) that doesn't exist in
  the app at all today — building the core plan/quota gating first, without inventing retry semantics
  under time pressure, was judged higher value. When this IS built: needs a `parent_project_id`/
  `parent_house_project_id` self-referential column on `Project`/`HouseProject`, a regenerate endpoint
  that copies the parent's inputs into a new row with that link set, and quota logic that checks the
  count of existing retries against `PLAN_QUOTAS[plan]["free_retries"]` before deciding whether the
  new retry consumes quota or not. Free tier's own "every retry counts" behavior needs no special
  handling — it's just the existing quota check with no retry-buffer carve-out, since a retry with no
  buffer is indistinguishable from any other generation.
- **Build sequence (approved, each chunk reviewed before the next)**: (1) `UserPlan` DB model + quota
  logic + gate `create_project`/`create_house_project` on the logged-in Free-tier 5/3 quota, (2)
  anonymous pre-login 1-trial cookie gate, (3) per-request Kaggle-vs-OpenAI routing for Pro/Studio, (4)
  frontend pricing page + toggle + quota display + upgrade prompts, (5) a dev-only admin endpoint to
  manually set a user's plan until real payment exists (there is currently NO way to move a user off
  Free without one, since payment isn't wired — this is a deliberate, temporary stopgap, not meant to
  ship to real users once a real processor is integrated).

## Chunk 2 done (2026-09-12): anonymous pre-login trial

`create_project`/`create_house_project` no longer require login at all - their `user` dependency
changed from `require_user` to `get_current_user` (optional). When there's no logged-in user:
- A plain, `httponly`, 1-year `ig_anon_trial_room`/`ig_anon_trial_house` cookie (one per kind,
  `app/main.py`'s `_consume_anonymous_trial()`) tracks whether this browser's single free trial for
  that generation type was already used. First request: allowed, cookie set. Second request: 401
  ("Free trial already used - please log in to continue") - the SAME status code the frontend already
  handles (`static/app.js`'s `savePendingGeneration()` + redirect-to-`login.html`), so **zero frontend
  changes were needed** for the "please log in now" case.
- `Project.user_id`/`HouseProject.user_id` stay `None` for a trial generation (already a nullable
  column). Storage keys use a single fixed `ANONYMOUS_STORAGE_NAMESPACE = "anonymous"` prefix instead
  of a per-user one (no per-visitor id exists to key by, and there's no History/ownership feature for
  anonymous generations anyway).
- `get_project`/`cancel_project`/`get_house_project`/`cancel_house_project` also relaxed to optional
  auth, with a new shared `_owns_project(project_user_id, user)` check: an anonymous caller (also
  `user=None`, since `authFetch()` sends no `Authorization` header at all when logged out) can view/
  cancel an anonymous project. Deliberately coarse - any anonymous caller can see any anonymous
  project, since there's no per-visitor id to scope by - same low-stakes, low-security posture already
  accepted for the cookie itself. A real (logged-in-owned) project is completely unaffected - still an
  exact Clerk-id match, 404 for everyone else.
- Per-request Kaggle/OpenAI choice for the anonymous trial (the roadmap's literal "user may choose
  OpenAI or Kaggle for each") is **not built yet** - deferred to Chunk 3 alongside the same choice for
  logged-in Pro/Studio users, so it's built once, not twice.
- **Two pre-existing tests were genuinely superseded, not broken**: `test_create_project_requires_login`/
  `test_create_house_project_requires_login` asserted the OLD "no login at all -> 401 immediately"
  contract - renamed to `..._no_longer_requires_login_for_the_first_anonymous_trial` and updated to
  assert 200. Real gap caught while fixing this: neither test had EVER mocked `get_provider`/
  `get_storage` (harmless before, since the request 401'd before reaching any of that code) - now that
  the request actually runs the full pipeline, they needed the same `FakeProvider`/`FakeStorage`
  mocking every other `TestClient(app)` test already uses (see CLAUDE.md's "Storage seam" testing note)
  to avoid silently hitting real S3/providers.
- Tests: 7 new (`test_api.py`: anonymous trial success, blocked-on-second-attempt, per-browser
  isolation, logged-in-user unaffected, anonymous-cannot-see-a-real-user's-project;
  `test_house_api.py`: the house-side trial equivalent). 514/514 passing (one pre-existing, already-
  documented SQLite-lock-contention flake under full-suite load - passes in isolation and on a clean
  rerun, unrelated to this change).

## Chunk 3 done (2026-09-12): per-request Kaggle-vs-OpenAI routing

`POST /api/projects`/`POST /api/house-projects` both gained an optional `preferred_model` Form
field (`"kaggle"`/`"openai"`, anything else silently ignored - same "correct nonsense rather than
error" convention this endpoint already uses). Whether it's honored depends on who's asking:

- **Anonymous (pre-login trial)**: always allowed to choose, per the roadmap's literal spec ("user
  may choose OpenAI or Kaggle for each").
- **Free plan**: never honored - `app/plans.py`'s `resolve_preferred_backend()` always returns
  `None` for Free, so the request silently falls back to the app's configured default, exactly as
  if `preferred_model` had never been sent. Verified end-to-end
  (`test_free_plan_room_preferred_model_is_ignored_not_honored`): a Free user explicitly requesting
  `"openai"` still succeeds (200), which only happens if their choice was ignored and the request
  ran against the Kaggle quota bucket - if the choice had wrongly been honored, it would have 403'd
  immediately (Free's OpenAI allowance is 0).
- **Pro/Studio**: honored, and the request is quota-checked against whichever bucket
  ("room_openai"/"room_kaggle"/etc.) the CHOSEN backend maps to, not the app's default.

**Provider-side plumbing** (`app/providers/hybrid.py`): `HybridProvider` gained
`_resolve_room_provider(preferred_backend)`/`_resolve_house_provider(preferred_backend)` -
`"openai"` always resolves to `self._openai` (always real); `"kaggle"` or `None` resolves to
whatever the site's own configured self-hosted backend already is (`self._room_image_provider`/
`self._house_image_provider`) - so a `None`/no-preference call is 100% identical to before this
feature existed. `generate_image()`/`generate_images_batch()`/`generate_house_render()` all gained
an optional `preferred_backend` param that does nothing but pick which already-constructed provider
instance to delegate to for that one call - the runtime OpenAI-fallback-on-failure logic is
unaffected (still compares object identity against `self._openai`).

**Threaded through the pipelines conditionally, not unconditionally** - `run_pipeline()`/
`run_house_pipeline()` gained a `preferred_backend` param, but only include it in the
`provider.generate_image(...)`/`generate_images_batch(...)`/`generate_house_render(...)` calls
when it's not `None` (`backend_kwargs = {"preferred_backend": ...} if preferred_backend else {}`).
This was a deliberate scope-control decision: threading a required new kwarg through every call
site would have meant updating ~25+ duck-typed `FakeProvider` test doubles across 5 test files
(none of which inherit from the `Provider` ABC) just to accept an unused parameter. The
conditional-kwargs approach means a call with no explicit backend preference (the overwhelming
majority of requests, and every pre-existing test) is byte-for-byte identical to the call made
before this feature existed - zero of those ~25+ fakes needed touching. Only new tests that
specifically exercise backend routing needed fakes that accept the parameter.

**Known, still-open gap, unchanged by this chunk**: Build a House's ongoing MONTHLY quota is still
not enforced for logged-in users (see the "House quota gap" note below) - that requires either a
real Kaggle house model or a pricing-table rework, neither of which per-request routing code alone
can fix. What Chunk 3 DOES add for house: the model-*choice* restriction (Free can never explicitly
force `"openai"`) is enforced independent of quota tracking - it doesn't fully close the cost-
exposure gap (a Free user's house request still runs on OpenAI today regardless of their non-choice,
since that's the only configured backend), but it stops a Free user from actively making it worse.

Tests: `tests/test_plans.py` (4 new, `resolve_preferred_backend()` unit tests),
`tests/test_hybrid_provider.py` (10 new, backend-resolution + label-recording tests),
`tests/test_api.py` (4 new), `tests/test_house_api.py` (2 new). 534/534 passing.

## Chunk 1 finding: Free tier's House quota can't be fulfilled yet (2026-09-12)

`app/main.py::create_house_project` is **deliberately NOT gated** on the quota system yet, unlike
`create_project` (which IS gated, since Room Redesign's default backend is Kaggle and matches Free
tier's real allowance). Real reason: `settings.house_image_provider` defaults to `"openai"` — **no
trained Kaggle house-render model exists in production** (see CLAUDE.md's "Build a House feature"
section) — so gating House creation against the REAL configured backend would 403 every Free-tier
user's very first Build a House generation (their real OpenAI allowance is 0, not the pricing table's
assumed 3 Kaggle generations). Hardcoding the "kaggle" bucket regardless of the real backend was
considered and rejected — it would silently let Free users consume real, paid OpenAI calls
(~$0.19/generation) at zero counted cost, a real cost-exposure risk, not just a quota nuance.

**Resolve this in Chunk 3** (per-request Kaggle/OpenAI routing) — by then, either a real Kaggle house
model exists (making the pricing table's assumption true) or the pricing table itself needs revisiting
for an OpenAI-only house-generation reality (e.g. a smaller free OpenAI house allowance instead of a
Kaggle one). Until then, Build a House stays fully unmetered for every plan, exactly as it behaves in
production today - this is a known, deliberate gap, not a bug.

## Chunk 4 done (2026-09-12): frontend - pricing tab, model toggle, quota display

- **New standalone "PRICING" tab** (`static/index.html`'s nav + mobile sidebar, a 4th entry in
  `TAB_ORDER`/`TAB_PANELS`/`TAB_BTNS` in `app.js`, reusing the exact same `switchTab()` machinery
  Home/Room/House already use - no new tab-switching logic). Shows the same Free/Pro/Studio
  comparison the 403 quota-card already had inline, but reachable any time, not just after hitting
  a limit. Pro/Studio cards dynamically show "Current plan" (disabled) instead of "Upgrade to X"
  when `GET /api/plan` confirms that's the user's real plan (`applyPricingUI()`).
- **No real checkout exists** (payment processor still undecided - see "Decisions made" above), so
  clicking an enabled Upgrade button doesn't pretend to start one. It scrolls to and flashes the
  existing honest disclaimer text ("Self-serve upgrades aren't live yet — reach out and we'll get
  you set up on a paid plan directly.") instead - deliberately not a fake success state or a fake
  contact form, since neither reflects a real capability. Chunk 5's dev-only admin endpoint is the
  only way to actually change a plan for now.
- **Room Redesign gained a Kaggle/OpenAI model-choice toggle** (`#room-model-toggle-wrap`), shown
  only when the choice is real: anonymous (pre-login trial - the roadmap's literal spec) and
  Pro/Studio. Hidden entirely for a logged-in Free-plan user, since `resolve_preferred_backend()`
  already silently ignores their choice server-side - showing a control that does nothing would be
  its own bug. `applyRoomPlanUI()` (`app.js`) decides this live off `GET /api/plan`, and also drives
  the quota-remaining note under it: Free sees real remaining-this-month numbers + an "Upgrade" link
  that jumps to the Pricing tab; Pro/Studio see remaining counts for BOTH backends since they have a
  real choice to make. Wired into the submit handler: `preferred_model` is only appended to the
  upload `FormData` when the toggle is actually visible - a hidden/Free-plan submission is
  byte-for-byte the same request as before this feature existed.
- **Build a House deliberately did NOT get the same toggle.** `settings.house_image_provider`
  defaults to `"openai"` and no trained self-hosted house-render model exists yet (see the "House
  quota gap" note above) - `HybridProvider._resolve_house_provider("kaggle")` resolves to
  whatever the site's configured house backend already is, which today is OpenAI regardless of
  which option a user picked. Showing a toggle where both choices silently do the same (paid) thing
  would misrepresent a capability the app doesn't have yet (see CLAUDE.md's "Stitch reconciliation"
  standing rule: no UI may imply data/capability the backend doesn't produce). Replaced with a
  plain, honest static note instead: "Build a House renders currently use OpenAI — our self-hosted
  house model isn't live yet." Revisit once a real Kaggle house model exists (tracked in
  `HouseProject`'s ongoing "House quota gap" note above) - the toggle markup pattern in Room
  Redesign is directly reusable then.
- **Quota display is refreshed at the moments it can actually change**: on Clerk sign-in/out, on
  switching to the Room or Pricing tab, and immediately after a Room generation is successfully
  submitted (`consume_quota()` runs synchronously inside `POST /api/projects`, before the background
  pipeline even starts - so the remaining count is already stale the instant the request succeeds,
  not when the generation finishes rendering).
- **403 quota-exceeded card gained a "View full pricing" button** (`#quota-view-pricing-btn`)
  alongside the existing "Back" button, jumping straight to the new Pricing tab - the card's own
  inline comparison stays as-is (already useful in the moment), this just adds a path to the
  fuller, dynamically-updating version.
- **Manually verified end-to-end with a headless Chromium smoke test** (Playwright, not part of the
  committed test suite - this is frontend JS with no existing test harness in this repo, see
  CLAUDE.md's "Frontend" section) against a real running `uvicorn` instance: Pricing tab renders
  with zero console/page errors, clicking Upgrade flashes the disclaimer note without error,
  the Room model toggle is visible+clickable for an anonymous visitor and correctly updates the
  hidden `preferred_model` value, the House tab shows the static OpenAI note (no toggle), and the
  403 quota-card's new "View full pricing" button correctly switches to the Pricing tab. Backend
  test suite unaffected (534/534 passing) since this chunk touched no Python.

**Same-day follow-up fixes (2026-09-12), after the user reviewed the live Pricing tab:**
- **Chrome's default I-beam text cursor over plain static copy** (headings, pricing card text, nav
  labels...) made the whole page look "writable" even though none of it was ever editable - a
  site-wide default, not something new to this chunk, just first noticed while reviewing the new
  Pricing page. Fixed with a global `user-select: none` on `body` (`components.css`) - Chrome falls
  back to a normal pointer/arrow cursor over non-selectable text - with an explicit
  `user-select: text` override on `input`/`textarea`/`[contenteditable]` so real form fields (style
  notes, city, room dimensions, etc.) are completely unaffected. Verified with a Playwright check
  that typing into `#additional-instructions` still works normally after this change.
- **"Self-serve upgrades aren't live yet" disclaimer removed from both the Pricing tab and the 403
  quota-card, per explicit request ("remove the self serve line for now")** - along with the
  click-to-flash-it behavior on the Pricing tab's Upgrade buttons (`pricingNote` and its handler
  removed from `app.js`, the now-unreachable `.pricing-note-flash` CSS removed too). Upgrade buttons
  were enabled but inert (no click handler at all) at that point - superseded by the manual-payment
  modal immediately below.

## Manual JazzCash payment modal (2026-09-12)

Per explicit request: clicking "Upgrade to Pro" or "Upgrade to Studio" on the Pricing tab now opens
a modal with manual JazzCash transfer instructions, instead of doing nothing. This is a **manual,
honest stopgap** - not a real payment integration (that's still the undecided
Safepay/PayFast/JazzCash-API question in "Decisions made" above, unaffected by this). The flow is:
the user sends the shown amount to a real JazzCash mobile-wallet account, then reaches out to get
manually upgraded via Chunk 5's dev-only admin endpoint (not yet built).

- **`#jazzcash-modal-overlay`** (`static/index.html`) shows the plan name + price (dynamic per
  button - `openJazzCashModal("pro"|"studio")` in `app.js`), a fixed account title ("Muddassir
  Ahmed") and JazzCash number ("0321-8249255"), and a Copy button
  (`navigator.clipboard.writeText()`, best-effort - degrades silently if the Clipboard API is
  unavailable, the number is already shown in plain text either way).
- **No real JazzCash logo asset exists anywhere in this repo, and none was fetched from an external
  source for it** - fetching/hotlinking a third-party trademarked logo image wasn't done without a
  real, vetted asset to use. `.jazzcash-badge` (`components.css`) is a stylized text wordmark in
  JazzCash's brand red instead - a facsimile, not the literal mark. If a real logo file is ever
  added under `static/` (e.g. the user supplies one), swap the `<span class="jazzcash-badge">` for
  an `<img>` - everything else (sizing, placement in the modal) stays the same.
- **GSAP "morph" open/close, per explicit request and matching this project's own established
  convention** (see CLAUDE.md's Motion/GSAP section - the nav account menu's exact recipe: `gsap.set`
  to a scaled-down/faded/offset start state, `.to()` a springy `back.out(1.7)` settle, with the
  card's children staggered in right after) - adapted here for a centered modal by also fading the
  backdrop in/out alongside the card's scale, rather than positioning from a corner like the nav
  menu does. Guarded by the same `typeof gsap === "undefined" || prefersReducedMotion` fallback to
  an instant show/hide used everywhere else GSAP is used in this codebase.
- **Verified with a headless Chromium smoke test** (Playwright, same approach as Chunk 4's
  verification above): opening via the Pro button shows "Pro" / "PKR 2,499/mo", opening via Studio
  shows "Studio" / "PKR 6,999/mo", Escape closes it, zero console/page errors. Backend suite
  unaffected (534/534 passing) - this is frontend-only.

## Open questions for whoever picks this up next

- 7-day Pro trial: explicitly skipped for this build phase (2026-09-12 decision) — build Free/Pro/
  Studio plan gating first, add the trial as its own follow-up once core paid-plan mechanics are proven.
- Payment processor: Safepay recommended (see above) but not yet confirmed by the user — needs a final
  decision before chunk 3's "upgrade to Pro/Studio" path can be anything but the dev-only admin stopgap.
- Pre-login trial tracking: **decided — browser cookie** (simple, no new infra, accepted as
  bypassable/low-abuse-resistance in exchange for zero friction — matches this app's existing
  localStorage-based low-friction conventions elsewhere).
- Free-tier (and all plans') quota reset: **decided — rolling 30-day window per user**, anchored to
  `UserPlan.quota_window_start`, not a shared calendar-month cutover.
