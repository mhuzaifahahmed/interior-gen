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

## Open questions for whoever picks this up next

- 7-day Pro trial: exact entry point and abuse-prevention mechanism (see above).
- Payment processor for PKR billing (Stripe's Pakistan support is limited — needs its own research
  pass before the backend phase starts).
- Whether "1 generation before login" is tracked by IP, browser fingerprint, or cookie — each has
  different abuse/UX trade-offs, undecided.
- Whether Free-tier monthly quotas reset on a rolling 30-day window or a calendar month.
