// Shared two-pill header + mobile slide-out sidebar, used by every page that
// ISN'T the main app (login.html, signup.html, terms.html, privacy.html) -
// the single source of truth for what used to be several independently
// hand-copied headers (auth-header.js for login/signup, a THIRD hand-copied
// single-pill header duplicated across terms.html AND privacy.html). Real,
// reported bug this replaces: on mobile, every one of those old headers
// rendered as one big h-16 pill with the full-size logo + a CTA link crammed
// into it (nav links just disappeared via hidden md:flex, nothing replaced
// them) - bulky and inconsistent with index.html's real header, which
// already shrinks to two small pills (logo left, hamburger right) below the
// md breakpoint. This renders that EXACT same two-pill shape + the exact
// same slide-in-from-the-right mobile sidebar (markup/motion copied from
// index.html/app.js) on every page that uses it, so the mobile nav looks and
// behaves identically everywhere, not just on the main app.
//
// Deliberately NOT Clerk-aware (no login-state-driven avatar/menu) - none of
// the headers this replaces were either (auth-header.js always showed a
// static LOG IN/SIGN UP CTA; terms.html/privacy.html did too) - this is a
// pure visual/interaction-consistency fix, not a scope expansion into
// syncing auth state across every secondary page.
//
// Usage: <div id="site-header"></div><script src="/static/site-header.js"></script>
// Optional data attributes on the script tag itself:
//   data-cta-label / data-cta-href / data-cta-filled - when data-cta-label is
//     given, the desktop pill (and the sidebar) show ONE contextual CTA
//     instead of the usual LOG IN + SIGN UP pair - used by login.html
//     ("SIGN UP", filled) and signup.html ("LOG IN", plain), matching each
//     page's existing per-page emphasis (showing both would be redundant
//     when you're already on one of the two auth pages). Omitted entirely on
//     terms.html/privacy.html, which show the normal LOG IN + SIGN UP pair.
(function () {
  const scriptEl = document.currentScript;
  const ctaLabel = scriptEl.dataset.ctaLabel || null;
  const ctaHref = scriptEl.dataset.ctaHref || "/static/signup.html";
  const ctaFilled = scriptEl.dataset.ctaFilled === "true";

  const NAV_LINKS = [
    { label: "HOME", href: "/?tab=home" },
    { label: "ROOM REDESIGN", href: "/?tab=room" },
    { label: "BUILD A HOUSE", href: "/?tab=house" },
    { label: "PLANS", href: "/?tab=pricing" },
  ];

  const navLinksHtml = NAV_LINKS.map(
    (link) =>
      `<a href="${link.href}" class="font-label-caps text-label-caps px-4 py-2 rounded-full text-on-night-variant hover:text-on-night transition-colors">${link.label}</a>`
  ).join("");

  // Desktop auth area: either the single contextual CTA (login/signup pages)
  // or the usual LOG IN + SIGN UP pair (every other page) - mirrors
  // index.html's real nav-auth-guest state exactly, since none of these
  // pages ever show a logged-in state in the header.
  const desktopAuthHtml = ctaLabel
    ? `<a href="${ctaHref}" class="font-label-caps text-label-caps ${
        ctaFilled
          ? "bg-white text-night rounded-full px-4 py-2 hover:bg-white/85 hover:shadow-md transition-all duration-200"
          : "text-on-night-variant hover:text-on-night transition-colors"
      } whitespace-nowrap shrink-0">${ctaLabel}</a>`
    : `<a href="/static/login.html" class="font-label-caps text-label-caps text-on-night-variant hover:text-on-night transition-colors">LOG IN</a>
       <a href="/static/signup.html" class="font-label-caps text-label-caps bg-white text-night rounded-full px-4 py-2 hover:bg-white/85 hover:shadow-md transition-all duration-200">SIGN UP</a>`;

  const sidebarAuthHtml = ctaLabel
    ? `<a href="${ctaHref}" class="mobile-sidebar-link block px-5 py-3.5 font-label-caps text-label-caps text-on-night-variant hover:text-on-night hover:bg-white/5 transition-colors">${ctaLabel}</a>`
    : `<a href="/static/login.html" class="mobile-sidebar-link block px-5 py-3.5 font-label-caps text-label-caps text-on-night-variant hover:text-on-night hover:bg-white/5 transition-colors">LOG IN</a>
       <a href="/static/signup.html" class="mobile-sidebar-link block px-5 py-3.5 font-label-caps text-label-caps text-on-night-variant hover:text-on-night hover:bg-white/5 transition-colors">SIGN UP</a>`;

  // Two independently-pilled containers (logo pill + everything-else pill),
  // shrinking below md exactly like index.html's real header (h-16->h-11,
  // w-8 logo->w-6, etc.) - the everything-else pill shows nav+auth on
  // desktop and ONLY the hamburger button on mobile.
  const headerHtml = `
    <header class="fixed top-3 md:top-4 inset-x-0 z-50 px-margin-mobile pointer-events-none">
      <div class="max-w-4xl mx-auto flex items-center justify-between gap-2 md:gap-3">
        <div class="bg-night/50 backdrop-blur-2xl rounded-full shadow-[0_12px_32px_rgba(28,24,21,0.3)] border border-white/10 pointer-events-auto shrink-0">
          <div class="h-11 md:h-16 px-3 md:px-4 lg:px-5 flex items-center">
            <a href="/" class="flex items-center gap-1.5 md:gap-2.5 shrink-0">
              <img src="/static/logo.png" alt="Interior-Gen" class="w-6 h-6 md:w-8 md:h-8 object-contain shrink-0"/>
              <span class="font-headline-sm text-sm md:text-headline-sm tracking-tight text-on-night whitespace-nowrap">Interior&#8209;Gen</span>
            </a>
          </div>
        </div>
        <div class="bg-night/50 backdrop-blur-2xl rounded-full shadow-[0_12px_32px_rgba(28,24,21,0.3)] border border-white/10 pointer-events-auto shrink-0">
          <div class="h-11 md:h-16 px-2 md:px-3 lg:px-6 flex items-center justify-between gap-3 md:gap-4 lg:gap-6">
            <nav class="hidden md:flex items-center gap-1">${navLinksHtml}</nav>
            <span class="hidden md:block w-px h-6 bg-white/15 shrink-0"></span>
            <div class="hidden md:flex items-center gap-4 shrink-0">${desktopAuthHtml}</div>
            <button type="button" class="md:hidden flex items-center justify-center w-8 h-8 rounded-full hover:bg-white/10 transition-colors" id="site-header-mobile-menu-btn" aria-label="Open menu" aria-expanded="false" aria-controls="site-header-mobile-sidebar-panel">
              <span class="material-symbols-outlined text-on-night text-xl" id="site-header-mobile-menu-icon">menu</span>
            </button>
          </div>
        </div>
      </div>
    </header>

    <div class="fixed inset-0 z-[90] hidden" id="site-header-mobile-sidebar-overlay">
      <div class="absolute inset-0 bg-night/70 backdrop-blur-sm" id="site-header-mobile-sidebar-backdrop"></div>
      <div class="absolute top-0 right-0 h-full w-[78%] max-w-xs bg-night border-l border-night-border shadow-[0_0_40px_rgba(0,0,0,0.45)] flex flex-col" id="site-header-mobile-sidebar-panel">
        <div class="flex items-center justify-between h-16 px-5 border-b border-night-border shrink-0">
          <div class="flex items-center gap-2.5">
            <img src="/static/logo.png" alt="Interior-Gen" class="w-7 h-7 object-contain shrink-0"/>
            <span class="font-headline-sm text-headline-sm text-on-night">Interior&#8209;Gen</span>
          </div>
          <button type="button" class="w-9 h-9 flex items-center justify-center rounded-full hover:bg-white/10 transition-colors" id="site-header-mobile-sidebar-close" aria-label="Close menu">
            <span class="material-symbols-outlined text-on-night">close</span>
          </button>
        </div>
        <nav class="flex flex-col py-2 overflow-y-auto">
          ${NAV_LINKS.map(
            (link) =>
              `<a href="${link.href}" class="mobile-sidebar-link block px-5 py-3.5 font-label-caps text-label-caps text-on-night-variant hover:text-on-night hover:bg-white/5 transition-colors">${link.label}</a>`
          ).join("")}
          <div class="mt-2 pt-2 border-t border-night-border">${sidebarAuthHtml}</div>
          <div class="mt-2 pt-2 border-t border-night-border">
            <a href="/static/privacy.html" class="mobile-sidebar-link block px-5 py-3.5 font-label-caps text-label-caps text-on-night-variant hover:text-on-night hover:bg-white/5 transition-colors">PRIVACY POLICY</a>
            <a href="/static/terms.html" class="mobile-sidebar-link block px-5 py-3.5 font-label-caps text-label-caps text-on-night-variant hover:text-on-night hover:bg-white/5 transition-colors">TERMS</a>
          </div>
        </nav>
      </div>
    </div>`;

  document.getElementById("site-header").outerHTML = headerHtml;

  /* ---------- Mobile sidebar open/close ---------- */
  // Same slide-in-from-the-right + backdrop-fade recipe as index.html's real
  // mobile sidebar (app.js's openMobileSidebar/closeMobileSidebar) - copied
  // rather than shared, since these pages don't load app.js at all (it's
  // tightly coupled to the main app's tab/results/generation state). Follows
  // the same house rules as every GSAP interaction in this project: a
  // prefers-reduced-motion + missing-gsap fallback (instant show/hide, never
  // stuck invisible), and gsap.set() + .to() for the staggered link reveal -
  // never .from() + stagger (see the gsap-transitions skill for why).
  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const menuBtn = document.getElementById("site-header-mobile-menu-btn");
  const menuIcon = document.getElementById("site-header-mobile-menu-icon");
  const overlay = document.getElementById("site-header-mobile-sidebar-overlay");
  const backdrop = document.getElementById("site-header-mobile-sidebar-backdrop");
  const panel = document.getElementById("site-header-mobile-sidebar-panel");
  const closeBtn = document.getElementById("site-header-mobile-sidebar-close");

  let tl = null;

  function isOpen() {
    return !overlay.classList.contains("hidden");
  }

  function open() {
    if (isOpen()) return;
    menuBtn.setAttribute("aria-expanded", "true");
    overlay.classList.remove("hidden");
    document.body.style.overflow = "hidden";
    menuIcon.textContent = "close";

    if (typeof gsap === "undefined" || prefersReducedMotion) return;

    if (tl) tl.kill();
    const links = panel.querySelectorAll("nav > *");
    gsap.set(backdrop, { opacity: 0 });
    gsap.set(panel, { x: "100%" });
    gsap.set(links, { opacity: 0, x: 16 });

    tl = gsap.timeline();
    tl.to(backdrop, { opacity: 1, duration: 0.25, ease: "power1.out" }, 0)
      .to(panel, { x: "0%", duration: 0.38, ease: "power3.out" }, 0)
      .to(links, { opacity: 1, x: 0, duration: 0.24, ease: "power2.out", stagger: 0.04 }, "-=0.2");
  }

  function close() {
    if (!isOpen()) return;
    menuBtn.setAttribute("aria-expanded", "false");
    document.body.style.overflow = "";
    menuIcon.textContent = "menu";

    if (typeof gsap === "undefined" || prefersReducedMotion) {
      overlay.classList.add("hidden");
      return;
    }

    if (tl) tl.kill();
    tl = gsap.timeline({
      onComplete: () => {
        overlay.classList.add("hidden");
        gsap.set(panel, { clearProps: "transform" });
        gsap.set(backdrop, { clearProps: "opacity" });
      },
    });
    tl.to(panel, { x: "100%", duration: 0.28, ease: "power2.in" }, 0).to(
      backdrop,
      { opacity: 0, duration: 0.2, ease: "power1.in" },
      0
    );
  }

  menuBtn.addEventListener("click", () => (isOpen() ? close() : open()));
  closeBtn.addEventListener("click", close);
  backdrop.addEventListener("click", close);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && isOpen()) close();
  });
  // Defensive: if the viewport is resized/rotated past md while open (the
  // hamburger itself becomes hidden then, via md:hidden), don't leave the
  // sidebar stuck open with no visible way to close it.
  window.addEventListener("resize", () => {
    if (window.innerWidth >= 768 && isOpen()) close();
  });
})();
