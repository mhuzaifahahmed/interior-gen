// Shared header for login.html/signup.html - the SINGLE source of truth for
// this nav, so it can never drift out of sync between the two pages again
// (real bug this replaced: each page hand-copied its own <header> markup,
// and one of them was missing the PRICING link entirely while the other had
// stale hrefs - a class of bug that's now structurally impossible since
// there's only one copy of this markup left to edit).
//
// Renders a SINGLE merged pill (not the two separate logo/nav pills
// index.html's real, Clerk-aware header uses - this is a deliberately
// simpler, static version for the pre-auth pages) with vertical-line
// dividers between Logo | Nav links | primary CTA.
//
// Usage: <div id="auth-page-header"></div><script src="/static/auth-header.js" data-cta-label="SIGN UP" data-cta-href="/static/signup.html"></script>
(function () {
  const scriptEl = document.currentScript;
  const ctaLabel = scriptEl.dataset.ctaLabel || "LOG IN";
  const ctaHref = scriptEl.dataset.ctaHref || "/static/login.html";
  // The login page emphasizes "SIGN UP" as a filled pill button (the
  // primary conversion action); the signup page's "LOG IN" stays a plain
  // text link (secondary, you're already signing up) - matches each page's
  // original per-page styling, now driven by one shared template instead of
  // two hand-copied ones.
  const ctaFilled = scriptEl.dataset.ctaFilled === "true";
  const ctaClass = ctaFilled
    ? "font-label-caps text-label-caps bg-white text-night rounded-full px-3 py-1.5 md:px-4 md:py-2 whitespace-nowrap shrink-0 hover:bg-white/85 hover:shadow-md transition-all duration-200"
    : "font-label-caps text-label-caps text-on-night-variant whitespace-nowrap shrink-0 hover:text-on-night transition-colors";

  const NAV_LINKS = [
    { label: "HOME", tab: "home" },
    { label: "ROOM REDESIGN", tab: "room" },
    { label: "BUILD A HOUSE", tab: "house" },
    { label: "PRICING", tab: "pricing" },
  ];

  const navLinksHtml = NAV_LINKS.map(
    (link) =>
      `<a href="/?tab=${link.tab}" class="font-label-caps text-label-caps px-4 py-2 rounded-full text-on-night-variant hover:text-on-night transition-colors">${link.label}</a>`
  ).join("");

  const divider = `<span class="hidden md:block w-px h-6 bg-white/15 shrink-0"></span>`;

  const headerHtml = `
    <header class="fixed top-4 inset-x-0 z-50 px-margin-mobile pointer-events-none">
      <div class="max-w-4xl mx-auto flex items-center justify-center">
        <div class="bg-night/50 backdrop-blur-2xl rounded-full shadow-[0_12px_32px_rgba(28,24,21,0.3)] border border-white/10 pointer-events-auto" id="auth-header-pill">
          <div class="h-16 px-3 md:px-4 lg:px-6 flex items-center gap-2 md:gap-4 lg:gap-6">
            <a href="/?tab=home" class="flex items-center gap-2 md:gap-2.5 shrink-0 min-w-0">
              <img src="/static/logo.png" alt="Interior-Gen" class="w-8 h-8 object-contain shrink-0"/>
              <span class="hidden sm:inline font-headline-sm text-headline-sm tracking-tight text-on-night whitespace-nowrap">Interior&#8209;Gen</span>
            </a>
            ${divider}
            <nav class="hidden md:flex items-center gap-1">${navLinksHtml}</nav>
            ${divider}
            <div class="flex items-center gap-4 shrink-0 ml-auto">
              <a href="${ctaHref}" class="${ctaClass}">${ctaLabel}</a>
            </div>
          </div>
        </div>
      </div>
    </header>`;

  document.getElementById("auth-page-header").outerHTML = headerHtml;

  // "Cut to full" entrance: the pill starts visually clipped down to a
  // narrow center sliver (clip-path inset) and expands out to its full
  // width - literal "cut" -> "full" motion, not just a fade. clip-path is
  // used instead of scaleX so the inner content (logo/nav/button) never
  // gets horizontally stretched/distorted mid-animation. Same house rules
  // as every other GSAP interaction in this codebase (see the
  // gsap-transitions skill): guard for prefers-reduced-motion/missing GSAP,
  // and gsap.set() start state + .to() end state - never .from(), which was
  // observed elsewhere in this project to silently freeze at its start
  // values.
  const pillEl = document.getElementById("auth-header-pill");
  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (typeof gsap === "undefined" || prefersReducedMotion || !pillEl) return;

  gsap.set(pillEl, { clipPath: "inset(0% 38% 0% 38% round 999px)", opacity: 0 });
  gsap.to(pillEl, {
    clipPath: "inset(0% 0% 0% 0% round 999px)",
    opacity: 1,
    duration: 0.65,
    ease: "power3.out",
    delay: 0.05,
  });
})();
