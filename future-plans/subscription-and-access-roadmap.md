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
