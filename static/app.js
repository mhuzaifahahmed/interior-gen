const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// All /api/... calls are always relative (same-origin), on both local dev
// (single FastAPI server serves this file + the API together) and
// production (vercel.json proxies /api/:path* to the real Render backend
// server-side).
function downloadFilenameFor(key, url) {
  // Tier images can now be PNG (OpenAI) or JPEG (Kaggle, if IMAGE_PROVIDER=kaggle) -
  // derive the extension from the actual served URL instead of hardcoding
  // ".png", so a JPEG file isn't downloaded with a mismatched extension.
  const match = /\.(png|jpe?g|webp)(?:$|\?)/i.exec(url || "");
  const ext = match ? match[1].toLowerCase() : "png";
  return `${key}.${ext}`;
}

function apiUrl(path) {
  return path;
}

// Render's free tier spins the backend down after ~15 min idle - the first
// request after that takes 30-60s to wake it back up. Left completely
// unhandled, that just looks like a hang with no explanation. This shows a
// small toast if ANY backend call (authFetch below, or the plain fetch()
// calls still used for a couple of unauthenticated bits) takes longer than
// COLD_START_HINT_DELAY_MS - long enough that a normal warm request never
// triggers it, short enough that a real cold start gets explained quickly
// rather than left silent. Reference-counted so overlapping requests (e.g.
// the Promise.all in the history modal) don't hide the toast the instant
// the fastest one finishes while others are still in flight.
const COLD_START_HINT_DELAY_MS = 4000;
const coldStartToast = document.getElementById("cold-start-toast");
let coldStartInFlight = 0;
let coldStartTimer = null;

function beginColdStartWatch() {
  coldStartInFlight += 1;
  if (coldStartTimer === null) {
    coldStartTimer = setTimeout(() => coldStartToast.classList.remove("hidden"), COLD_START_HINT_DELAY_MS);
  }
}

function endColdStartWatch() {
  coldStartInFlight = Math.max(0, coldStartInFlight - 1);
  if (coldStartInFlight === 0) {
    clearTimeout(coldStartTimer);
    coldStartTimer = null;
    coldStartToast.classList.add("hidden");
  }
}

async function fetchWithColdStartHint(path, options) {
  beginColdStartWatch();
  try {
    return await fetch(path, options);
  } finally {
    endColdStartWatch();
  }
}

// Best-effort human-readable label for the current user (from Clerk's own
// profile, already loaded client-side - no extra network call) - sent along
// with project/house-project creation purely so the backend can fold it
// into that user's S3 folder name, since browsing the bucket by opaque
// Clerk ids alone (e.g. "user_2abc...") is hard to match back to a real
// person. Falls back to "" (backend degrades to id-only naming) if Clerk
// hasn't loaded a user yet or has no name/email set.
async function currentUserDisplayName() {
  const Clerk = await clerkReady;
  const user = Clerk.user;
  if (!user) return "";
  return user.fullName || user.username || user.primaryEmailAddress?.emailAddress || "";
}

// Attaches a fresh Clerk session token as `Authorization: Bearer <token>` to
// an authenticated API call - this is the entire auth mechanism now (see
// app/auth.py's require_user), no cookies/credentials involved at all, which
// sidesteps cross-site cookie blocking (Safari/WebKit ITP) entirely rather
// than working around it. Clerk.session.getToken() caches the token in
// memory and only makes a network call once it's actually close to expiry,
// so calling this on every request is cheap. `clerkReady` is defined in
// static/clerk-init.js, loaded before this file.
async function authFetch(path, options = {}) {
  const Clerk = await clerkReady;
  const token = await Clerk.session?.getToken();
  const headers = { ...(options.headers || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return fetchWithColdStartHint(path, { ...options, headers });
}

const uploadView = document.getElementById("upload-view");
const form = document.getElementById("upload-form");
const fileInput = document.getElementById("file-input");
const dropzone = document.getElementById("dropzone");
const dropzoneEmpty = document.getElementById("dropzone-empty");
const dropzonePreview = document.getElementById("dropzone-preview");
const previewImg = document.getElementById("preview-img");
const previewFilename = document.getElementById("preview-filename");
const generateBtn = document.getElementById("generate-btn");
const cancelGenerationBtn = document.getElementById("cancel-generation-btn");
const removePhotoBtn = document.getElementById("remove-photo-btn");
const interiorStyleSelect = document.getElementById("interior-style-select");
const colorPaletteSelect = document.getElementById("color-palette-select");
const additionalInstructionsInput = document.getElementById("additional-instructions");
const cityInput = document.getElementById("city-input");
const roomLengthInput = document.getElementById("room-length");
const roomWidthInput = document.getElementById("room-width");
const roomHeightInput = document.getElementById("room-height");
const roomDimensionUnitInput = document.getElementById("room-dimension-unit");

const progressCard = document.getElementById("progress-card");
const progressMessageEl = document.getElementById("progress-message");
const progressSubtitleEl = document.getElementById("progress-subtitle");
const progressBarFill = document.getElementById("progress-bar-fill");
const progressStepperEl = document.getElementById("stage-stepper");
const conceptPreviewsEl = document.getElementById("concept-previews");

const errorCard = document.getElementById("error-card");
const errorDetail = document.getElementById("error-detail");
const retryBtn = document.getElementById("retry-btn");

const resultsSection = document.getElementById("results");
const roomDescriptionEl = document.getElementById("room-description");
const imageModelNoteEl = document.getElementById("image-model-note");
const resultsGrid = document.getElementById("results-grid");
const startOverBtn = document.getElementById("start-over-btn");

const lightbox = document.getElementById("lightbox");
const lightboxImg = document.getElementById("lightbox-img");
const lightboxCaption = document.getElementById("lightbox-caption");
const lightboxClose = document.getElementById("lightbox-close");

const materialsModalOverlay = document.getElementById("materials-modal-overlay");
const materialsModalTitle = document.getElementById("materials-modal-title");
const materialsModalBody = document.getElementById("materials-modal-body");
const materialsModalClose = document.getElementById("materials-modal-close");

const historyModalOverlay = document.getElementById("history-modal-overlay");
const historyModalBody = document.getElementById("history-modal-body");
const historyModalClose = document.getElementById("history-modal-close");
const historyModalTabs = document.getElementById("history-modal-tabs");

/* ---------- "Build a House" tab DOM refs ---------- */

const tabBtnHome = document.getElementById("tab-btn-home");
const tabBtnRoom = document.getElementById("tab-btn-room");
const tabBtnHouse = document.getElementById("tab-btn-house");
const homeTabPanel = document.getElementById("home-tab-panel");
const roomTabPanel = document.getElementById("room-tab-panel");
const houseTabPanel = document.getElementById("house-tab-panel");
const homeCtaBtns = document.querySelectorAll(".js-cta-room");
const homeHeroHouseBtn = document.getElementById("home-hero-house-btn");
const homeToolsHouseRow = document.getElementById("home-tools-house-row");

/* ---------- Auth ---------- */
/* Clerk (https://clerk.com) owns the entire identity system - signup, login,
   Google sign-in, session issuance. This backend only verifies the token a
   Clerk-authenticated frontend sends (app/auth.py's require_user), so a
   logged-out visitor can still browse the landing page and switch tabs, but
   POSTing to /api/projects or /api/house-projects 401s. applyAuthUI() below
   drives which nav block shows (guest links vs. name/avatar + log out) -
   fed by Clerk.user directly (available client-side after clerkReady
   resolves - see static/clerk-init.js), not a round-trip to our own
   backend, and kept live via Clerk.addListener() so signing out updates the
   UI immediately without a page reload. */

const navAuthGuest = document.getElementById("nav-auth-guest");
const navAuthUser = document.getElementById("nav-auth-user");
const navUsernameEl = document.getElementById("nav-username");
const navUserAvatarEl = document.getElementById("nav-user-avatar");
const navMenuNameEl = document.getElementById("nav-menu-name");
const navMenuEmailEl = document.getElementById("nav-menu-email");
const navLoginBtn = document.getElementById("nav-login-btn");
const navSignupBtn = document.getElementById("nav-signup-btn");
const navLogoutBtn = document.getElementById("nav-logout-btn");

/* ---------- Mobile nav sidebar ---------- */
/* Below the md breakpoint the desktop tab-bar and auth area both disappear
   (see index.html's header - both wrapped in hidden md:flex now) and this
   slide-in sidebar is the only way to reach Home/Room Redesign/Build a
   House/auth/Privacy/Terms on a phone. Mirrors nav-auth-guest/nav-auth-user's
   own guest-vs-logged-in toggle (see checkAuthState() below) as its own
   parallel set of elements, rather than trying to reuse the desktop ones
   directly - the desktop nav-user-menu is a small anchored dropdown,
   fundamentally a different shape than a full-height sidebar list. */
const mobileMenuBtn = document.getElementById("mobile-menu-btn");
const mobileSidebarOverlay = document.getElementById("mobile-sidebar-overlay");
const mobileSidebarBackdrop = document.getElementById("mobile-sidebar-backdrop");
const mobileSidebarPanel = document.getElementById("mobile-sidebar-panel");
const mobileSidebarClose = document.getElementById("mobile-sidebar-close");
const mobileSidebarAuthGuest = document.getElementById("mobile-sidebar-auth-guest");
const mobileSidebarAuthUser = document.getElementById("mobile-sidebar-auth-user");
const mobileSidebarUsernameEl = document.getElementById("mobile-sidebar-username");
const mobileSidebarEmailEl = document.getElementById("mobile-sidebar-email");
const mobileSidebarAvatarEl = document.getElementById("mobile-sidebar-avatar");
const mobileSidebarHistoryBtn = document.getElementById("mobile-sidebar-history-btn");
const mobileSidebarLogoutBtn = document.getElementById("mobile-sidebar-logout-btn");

// Two-letter monogram for the avatar chip - initials of the first two words of
// the display name, or the first two characters if it's a single word.
function initialsFrom(name) {
  const words = (name || "").trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

// /static/login.html and /static/signup.html, not the clean /login and
// /signup routes - those clean routes only exist when this backend serves
// its own frontend (app/main.py's login_page()/signup_page()). On a
// static-only host (e.g. this file being served from Vercel with no backend
// alongside it), those routes 404 - the /static/*.html path works
// identically either way, since the backend also serves it at that same
// path via its own StaticFiles mount.
navLoginBtn.addEventListener("click", () => (window.location.href = "/static/login.html"));
navSignupBtn.addEventListener("click", () => (window.location.href = "/static/signup.html"));
navLogoutBtn.addEventListener("click", async () => {
  const Clerk = await clerkReady;
  await Clerk.signOut();
  window.location.href = "/";
});

function applyAuthUI(user) {
  if (!user) {
    navAuthGuest.classList.remove("hidden");
    navAuthUser.classList.add("hidden");
    mobileSidebarAuthGuest.classList.remove("hidden");
    mobileSidebarAuthUser.classList.add("hidden");
    return;
  }
  const displayName = user.fullName || user.username || user.primaryEmailAddress?.emailAddress || "Account";
  const email = user.primaryEmailAddress?.emailAddress || "";
  navUsernameEl.textContent = displayName;
  navUserAvatarEl.textContent = initialsFrom(displayName);
  navMenuNameEl.textContent = displayName;
  navMenuEmailEl.textContent = email;
  navAuthGuest.classList.add("hidden");
  navAuthUser.classList.remove("hidden");
  mobileSidebarUsernameEl.textContent = displayName;
  mobileSidebarEmailEl.textContent = email;
  mobileSidebarAvatarEl.textContent = initialsFrom(displayName);
  mobileSidebarAuthGuest.classList.add("hidden");
  mobileSidebarAuthUser.classList.remove("hidden");
}

clerkReady
  .then((Clerk) => {
    applyAuthUI(Clerk.user);
    Clerk.addListener(({ user }) => applyAuthUI(user));
  })
  .catch((err) => {
    console.error("Clerk failed to load", err);
    applyAuthUI(null);
  });

/* ---------- Mobile nav sidebar open/close (GSAP) ---------- */
/* Slide-in-from-the-right panel + backdrop fade, not the small anchored
   "morph" dropdown recipe used elsewhere (nav-user-menu, the Style/Palette
   dropdowns) - a full-height sidebar is a different UI shape (edge-anchored
   panel + scrim, not a small corner popover), so it gets its own slide
   animation instead of forcing that recipe onto something it wasn't
   designed for. Still follows the same house rules as every other GSAP
   interaction in this file: prefers-reduced-motion + missing-gsap fallback,
   and gsap.set() (start state) + .to() (end state) for the staggered link
   reveal - never .from() + stagger, which was observed elsewhere in this
   project to silently freeze at its start values (see the gsap-transitions
   skill for the full incident writeup). */
let mobileSidebarTl = null;

function isMobileSidebarOpen() {
  return !mobileSidebarOverlay.classList.contains("hidden");
}

function openMobileSidebar() {
  if (isMobileSidebarOpen()) return;
  mobileMenuBtn.setAttribute("aria-expanded", "true");
  mobileSidebarOverlay.classList.remove("hidden");
  document.body.style.overflow = "hidden"; // lock background scroll while open

  if (typeof gsap === "undefined" || prefersReducedMotion) return;

  if (mobileSidebarTl) mobileSidebarTl.kill();
  const links = mobileSidebarPanel.querySelectorAll("nav > *");
  gsap.set(mobileSidebarBackdrop, { opacity: 0 });
  gsap.set(mobileSidebarPanel, { x: "100%" });
  gsap.set(links, { opacity: 0, x: 16 });

  mobileSidebarTl = gsap.timeline();
  mobileSidebarTl
    .to(mobileSidebarBackdrop, { opacity: 1, duration: 0.25, ease: "power1.out" }, 0)
    .to(mobileSidebarPanel, { x: "0%", duration: 0.38, ease: "power3.out" }, 0)
    .to(links, { opacity: 1, x: 0, duration: 0.24, ease: "power2.out", stagger: 0.04 }, "-=0.2");
}

function closeMobileSidebar() {
  if (!isMobileSidebarOpen()) return;
  mobileMenuBtn.setAttribute("aria-expanded", "false");
  document.body.style.overflow = "";

  if (typeof gsap === "undefined" || prefersReducedMotion) {
    mobileSidebarOverlay.classList.add("hidden");
    return;
  }

  if (mobileSidebarTl) mobileSidebarTl.kill();
  mobileSidebarTl = gsap.timeline({
    onComplete: () => {
      mobileSidebarOverlay.classList.add("hidden");
      gsap.set(mobileSidebarPanel, { clearProps: "transform" });
      gsap.set(mobileSidebarBackdrop, { clearProps: "opacity" });
    },
  });
  mobileSidebarTl
    .to(mobileSidebarPanel, { x: "100%", duration: 0.28, ease: "power2.in" }, 0)
    .to(mobileSidebarBackdrop, { opacity: 0, duration: 0.2, ease: "power1.in" }, 0);
}

mobileMenuBtn.addEventListener("click", () => {
  isMobileSidebarOpen() ? closeMobileSidebar() : openMobileSidebar();
});
mobileSidebarClose.addEventListener("click", closeMobileSidebar);
mobileSidebarBackdrop.addEventListener("click", closeMobileSidebar);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && isMobileSidebarOpen()) closeMobileSidebar();
});
// Defensive: if the viewport is resized/rotated past the md breakpoint while
// open (mobile-menu-btn itself becomes hidden then, via md:hidden), don't
// leave the sidebar stuck open with no visible way to close it.
window.addEventListener("resize", () => {
  if (window.innerWidth >= 768 && isMobileSidebarOpen()) closeMobileSidebar();
});

// Home/Room Redesign/Build a House reuse the EXACT SAME switchTab() the
// desktop tab-bar uses (defined further down this file, but a hoisted
// function declaration so it's safe to reference here) - no duplicated tab
// logic, this just also closes the sidebar afterward.
mobileSidebarPanel.querySelectorAll(".mobile-sidebar-link[data-tab]").forEach((btn) => {
  btn.addEventListener("click", () => {
    switchTab(btn.dataset.tab);
    closeMobileSidebar();
  });
});

// History/Log out reuse the desktop nav's own buttons' existing click
// handlers (fetch+redirect for logout, modal-opening for history) via a
// synthetic click, rather than duplicating that logic here.
mobileSidebarHistoryBtn.addEventListener("click", () => {
  closeMobileSidebar();
  navHistoryBtn.click();
});
mobileSidebarLogoutBtn.addEventListener("click", () => {
  navLogoutBtn.click();
});

/* ---------- User menu dropdown (History) ---------- */

const navUserMenuBtn = document.getElementById("nav-user-menu-btn");
const navUserMenuChevron = document.getElementById("nav-user-menu-chevron");
const navUserMenu = document.getElementById("nav-user-menu");
const navHistoryBtn = document.getElementById("nav-history-btn");

// Smooth morph open/close (per the gsap-transitions skill) instead of an
// instant hidden-class toggle - the menu scales+fades in from its top-right
// anchor (matching its `right-0` positioning) while its 3 children (identity
// header, History, Log out) cascade in with a short stagger. Per the skill's
// documented gotcha, staggered tweens inside a timeline use gsap.set() + .to()
// rather than .from() (which was observed to silently stick at start values
// when combined with stagger + an overlapping timeline position).
let navUserMenuTl = null;

function openNavUserMenu() {
  navUserMenuBtn.setAttribute("aria-expanded", "true");
  navUserMenuChevron.style.transform = "rotate(180deg)";
  navUserMenu.classList.remove("hidden");

  if (typeof gsap === "undefined" || prefersReducedMotion) {
    return;
  }

  if (navUserMenuTl) navUserMenuTl.kill();
  const items = Array.from(navUserMenu.children);
  gsap.set(navUserMenu, { transformOrigin: "top right", scale: 0.85, opacity: 0, y: -8 });
  gsap.set(items, { opacity: 0, y: -6 });

  navUserMenuTl = gsap.timeline();
  navUserMenuTl
    .to(navUserMenu, { scale: 1, opacity: 1, y: 0, duration: 0.32, ease: "back.out(1.7)" })
    .to(items, { opacity: 1, y: 0, duration: 0.22, ease: "power2.out", stagger: 0.05 }, "-=0.18");
}

function closeNavUserMenu() {
  navUserMenuBtn.setAttribute("aria-expanded", "false");
  navUserMenuChevron.style.transform = "rotate(0deg)";

  if (typeof gsap === "undefined" || prefersReducedMotion) {
    navUserMenu.classList.add("hidden");
    return;
  }

  if (navUserMenuTl) navUserMenuTl.kill();
  navUserMenuTl = gsap.timeline({
    onComplete: () => {
      navUserMenu.classList.add("hidden");
      gsap.set(navUserMenu, { clearProps: "transform,opacity" });
    },
  });
  navUserMenuTl.to(navUserMenu, { scale: 0.9, opacity: 0, y: -6, duration: 0.16, ease: "power1.in" });
}

navUserMenuBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  const isOpen = !navUserMenu.classList.contains("hidden");
  if (isOpen) {
    closeNavUserMenu();
  } else {
    openNavUserMenu();
  }
});

document.addEventListener("click", (e) => {
  if (!navUserMenu.classList.contains("hidden") && !navUserMenu.contains(e.target)) {
    closeNavUserMenu();
  }
});

navHistoryBtn.addEventListener("click", () => {
  closeNavUserMenu();
  openHistoryModal();
});

/* ---------- Interior Style / Color Palette dropdowns (Room Redesign form) ---------- */
/* Native <select> elements can't be animated - their popup renders outside the
   DOM, out of CSS/JS reach - so these are custom button+listbox dropdowns
   reusing the exact same "morph" open/close recipe as #nav-user-menu above
   (per CLAUDE.md's dropdown convention: every dropdown on this site uses this
   same animation for visual consistency). A hidden <input> (interior-style-
   select/color-palette-select - same ids the rest of app.js already reads
   .value from and listens for "change" on) carries the actual form value, so
   nothing else in the submit/validation/pending-generation code needs to know
   these aren't real <select> elements. */

function setupMorphDropdown({ btnId, chevronId, menuId, hiddenInputId, placeholder }) {
  const btn = document.getElementById(btnId);
  const chevron = document.getElementById(chevronId);
  const menu = document.getElementById(menuId);
  const labelEl = document.getElementById(`${btnId}-label`);
  const hiddenInput = document.getElementById(hiddenInputId);
  let tl = null;

  function isOpen() {
    return !menu.classList.contains("hidden");
  }

  function open() {
    if (isOpen()) return;
    btn.setAttribute("aria-expanded", "true");
    chevron.style.transform = "rotate(180deg)";
    menu.classList.remove("hidden");

    if (typeof gsap === "undefined" || prefersReducedMotion) return;

    if (tl) tl.kill();
    const items = Array.from(menu.children);
    gsap.set(menu, { transformOrigin: "top", scale: 0.92, opacity: 0, y: -8 });
    gsap.set(items, { opacity: 0, y: -6 });

    tl = gsap.timeline();
    tl.to(menu, { scale: 1, opacity: 1, y: 0, duration: 0.32, ease: "back.out(1.7)" }).to(
      items,
      { opacity: 1, y: 0, duration: 0.22, ease: "power2.out", stagger: 0.05 },
      "-=0.18"
    );
  }

  function close() {
    if (!isOpen()) return;
    btn.setAttribute("aria-expanded", "false");
    chevron.style.transform = "rotate(0deg)";

    if (typeof gsap === "undefined" || prefersReducedMotion) {
      menu.classList.add("hidden");
      return;
    }

    if (tl) tl.kill();
    tl = gsap.timeline({
      onComplete: () => {
        menu.classList.add("hidden");
        gsap.set(menu, { clearProps: "transform,opacity" });
      },
    });
    tl.to(menu, { scale: 0.9, opacity: 0, y: -6, duration: 0.16, ease: "power1.in" });
  }

  function setValue(value) {
    hiddenInput.value = value;
    labelEl.textContent = value || placeholder;
    labelEl.classList.toggle("text-on-surface-variant", !value);
    labelEl.classList.toggle("text-on-surface", !!value);
    // Existing code (updateGenerateButtonState, etc.) listens for "change" on
    // this hidden input, same as it would on a real <select>.
    hiddenInput.dispatchEvent(new Event("change"));
  }

  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    isOpen() ? close() : open();
  });

  menu.querySelectorAll("[data-value]").forEach((opt) => {
    opt.addEventListener("click", () => {
      setValue(opt.dataset.value);
      close();
    });
  });

  document.addEventListener("click", (e) => {
    if (isOpen() && !menu.contains(e.target) && !btn.contains(e.target)) close();
  });

  return { setValue, close };
}

const interiorStyleDropdown = setupMorphDropdown({
  btnId: "interior-style-btn",
  chevronId: "interior-style-chevron",
  menuId: "interior-style-menu",
  hiddenInputId: "interior-style-select",
  placeholder: "Select a style…",
});
const colorPaletteDropdown = setupMorphDropdown({
  btnId: "color-palette-btn",
  chevronId: "color-palette-chevron",
  menuId: "color-palette-menu",
  hiddenInputId: "color-palette-select",
  placeholder: "Select a palette…",
});

const houseUploadView = document.getElementById("house-upload-view");
const houseForm = document.getElementById("house-upload-form");
const houseFileInput = document.getElementById("house-file-input");
const houseDropzone = document.getElementById("house-dropzone");
const houseDropzoneEmpty = document.getElementById("house-dropzone-empty");
const houseDropzonePreview = document.getElementById("house-dropzone-preview");
const housePreviewImg = document.getElementById("house-preview-img");
const housePreviewFilename = document.getElementById("house-preview-filename");
const houseGenerateBtn = document.getElementById("house-generate-btn");
const houseCancelGenerationBtn = document.getElementById("house-cancel-generation-btn");
const houseRemovePhotoBtn = document.getElementById("house-remove-photo-btn");
const houseLengthInput = document.getElementById("house-length");
const houseWidthInput = document.getElementById("house-width");
const houseUnitInput = document.getElementById("house-unit");
const houseFloorCountInput = document.getElementById("house-floor-count");
const houseBedroomsInput = document.getElementById("house-bedrooms");
const houseBathroomsInput = document.getElementById("house-bathrooms");
const houseExtrasInput = document.getElementById("house-extras");

const houseProgressCard = document.getElementById("house-progress-card");
const houseProgressMessageEl = document.getElementById("house-progress-message");
const houseProgressSubtitleEl = document.getElementById("house-progress-subtitle");
const houseProgressBarFill = document.getElementById("house-progress-bar-fill");
const houseProgressStepperEl = document.getElementById("house-stage-stepper");

const houseErrorCard = document.getElementById("house-error-card");
const houseErrorDetail = document.getElementById("house-error-detail");
const houseRetryBtn = document.getElementById("house-retry-btn");

const houseResultsSection = document.getElementById("house-results");
const plotDescriptionEl = document.getElementById("plot-description");
const houseImageModelNoteEl = document.getElementById("house-image-model-note");
const houseFeasibilityBannerEl = document.getElementById("house-feasibility-banner");
const houseResultsGrid = document.getElementById("house-results-grid");
const houseStartOverBtn = document.getElementById("house-start-over-btn");

/* ---------- Tab switching ---------- */
/* Three panels sharing one page (Home / Room Redesign / Build a House) -
   switching tabs only toggles which top-level panel is visible, it doesn't
   touch either flow's own internal state machine (showState()/showHouseState()
   below), so leaving mid-progress on one tab and coming back to it later
   still shows the right thing. */

const TAB_ORDER = ["home", "room", "house"];
const TAB_PANELS = { home: homeTabPanel, room: roomTabPanel, house: houseTabPanel };
const TAB_BTNS = { home: tabBtnHome, room: tabBtnRoom, house: tabBtnHouse };
let currentTab = "home";
let tabSwitchTl = null;

const tabActivePill = document.getElementById("tab-active-pill");

// Slides the pill behind the active tab's text (left/width, CSS-transitioned
// via #tab-active-pill's own transition in components.css) instead of each
// button drawing its own static background - so switching tabs reads as one
// continuous pill moving across the bar, not an instant swap.
function moveTabActivePill(tab) {
  const btn = TAB_BTNS[tab];
  if (!btn || !tabActivePill) return;
  tabActivePill.style.left = `${btn.offsetLeft}px`;
  tabActivePill.style.width = `${btn.offsetWidth}px`;
}

if (tabActivePill) {
  // Position immediately (real bug found live: waiting exclusively on
  // document.fonts.ready left the pill at its default zero width/no-left
  // state - effectively invisible - for however long that promise took to
  // settle, which was unreliably slow). Re-position again once webfonts
  // are ready, in case Fraunces/Poppins swapping in shifted the button's
  // width from its fallback-font size.
  moveTabActivePill(currentTab);
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => moveTabActivePill(currentTab));
  }
}

function switchTab(tab) {
  if (tab === currentTab) return;
  tabBtnHome.classList.toggle("is-active", tab === "home");
  tabBtnRoom.classList.toggle("is-active", tab === "room");
  tabBtnHouse.classList.toggle("is-active", tab === "house");
  moveTabActivePill(tab);

  const oldTab = currentTab;
  const oldPanel = TAB_PANELS[oldTab];
  const newPanel = TAB_PANELS[tab];
  currentTab = tab;

  if (typeof gsap === "undefined" || prefersReducedMotion) {
    oldPanel.hidden = true;
    newPanel.hidden = false;
    return;
  }

  if (tabSwitchTl) tabSwitchTl.kill();

  // If a previous switch got interrupted mid-flight (rapid tab clicking),
  // force the one panel not involved in *this* switch back to a clean
  // hidden state - only 3 tabs exist, so that's always exactly one panel.
  TAB_ORDER.forEach((t) => {
    if (t !== oldTab && t !== tab) {
      TAB_PANELS[t].hidden = true;
      gsap.set(TAB_PANELS[t], { clearProps: "all" });
    }
  });

  // Sequential slide+fade - old panel slides/fades out while still in normal
  // flow, THEN (only once fully invisible) the swap + scroll-reset happens,
  // THEN the new panel slides/fades in from the opposite edge. Never having
  // both panels in flow/visible at once avoids the earlier position:absolute
  // overlap approach, which collapsed the page's scrollable height for the
  // animation's duration and caused a visible scroll-position jump mid-fade.
  // Direction follows the tabs' left-to-right nav order (Home/Room/House).
  const dir = TAB_ORDER.indexOf(tab) > TAB_ORDER.indexOf(oldTab) ? 1 : -1;
  const distance = 28;

  gsap.set(oldPanel, { x: 0, opacity: 1 });

  tabSwitchTl = gsap.timeline({
    onComplete: () => {
      tabSwitchTl = null;
    },
  });
  tabSwitchTl
    .to(oldPanel, { x: -dir * distance, opacity: 0, duration: 0.2, ease: "power1.in" })
    .call(() => {
      oldPanel.hidden = true;
      gsap.set(oldPanel, { clearProps: "all" });
      newPanel.hidden = false;
      gsap.set(newPanel, { x: dir * distance, opacity: 0 });
      window.scrollTo(0, 0);
    })
    .to(newPanel, { x: 0, opacity: 1, duration: 0.32, ease: "power2.out" });
}

tabBtnHome.addEventListener("click", () => switchTab("home"));
tabBtnRoom.addEventListener("click", () => switchTab("room"));
tabBtnHouse.addEventListener("click", () => switchTab("house"));

homeCtaBtns.forEach((btn) => btn.addEventListener("click", () => switchTab("room")));
homeHeroHouseBtn.addEventListener("click", () => switchTab("house"));
homeToolsHouseRow.addEventListener("click", () => switchTab("house"));

/* ---------- Pending generation (survive the login redirect) ---------- */
/* A logged-out visitor can fill out the whole upload form before hitting
   "Generate" - the 401 from /api/projects or /api/house-projects only
   happens at submit time. Redirecting straight to /login used to throw
   away everything they'd just entered (photo included), which after login
   dumped them back on a blank Home page - confusing and worth fixing.
   sessionStorage (not localStorage) since this is a one-shot "finish what
   you were doing" handoff, not something that should linger across
   unrelated future visits. The photo survives as a base64 data URL (already
   computed anyway, for the dropzone preview) - reconstructed into a real
   File via DataTransfer once we're back. */

const PENDING_GENERATION_KEY = "interior-gen:pending-generation";

/* ---------- Active generation (survive navigation/reload) ---------- */
/* Generation itself is a server-side BackgroundTask (app/main.py) that
   always runs to completion regardless of the client - but the poll loop
   that shows live progress only lives in this page's memory, so a reload or
   navigating away (e.g. to sign up) used to orphan the view: the generation
   kept running, but the user landed back on a blank upload screen with no
   indication anything was happening (only recoverable later via History).
   localStorage (not sessionStorage - this SHOULD survive a full reload/new
   tab, unlike the one-shot pending-generation handoff above) holds just
   {tab, id} for whichever generation is currently in flight, cleared the
   moment it reaches any terminal state (done/failed/cancelled) or the user
   explicitly cancels it. */

const ACTIVE_GENERATION_KEY = "interior-gen:active-generation";

function saveActiveGeneration(tab, id) {
  try {
    localStorage.setItem(ACTIVE_GENERATION_KEY, JSON.stringify({ tab, id }));
  } catch {
    // localStorage full/unavailable - not fatal, reload-resume just won't work.
  }
}

function clearActiveGeneration() {
  try {
    localStorage.removeItem(ACTIVE_GENERATION_KEY);
  } catch {
    // ignore
  }
}

// Per-tab poll state - a project id + a cancelled flag, so a stale poll
// scheduled via setTimeout() before a Cancel click can recognize it's no
// longer current and stop rescheduling itself instead of racing the UI
// back into "progress" state.
let activeRoomProjectId = null;
let roomPollCancelled = false;
let activeHouseProjectId = null;
let housePollCancelled = false;

async function resumeActiveGeneration() {
  const raw = localStorage.getItem(ACTIVE_GENERATION_KEY);
  if (!raw) return;

  let active;
  try {
    active = JSON.parse(raw);
  } catch {
    clearActiveGeneration();
    return;
  }
  if (!active || !active.id || (active.tab !== "room" && active.tab !== "house")) {
    clearActiveGeneration();
    return;
  }

  // Instant tab switch (page-load context, no GSAP transition needed - same
  // reasoning as restorePendingGeneration() below).
  homeTabPanel.hidden = true;
  roomTabPanel.hidden = active.tab !== "room";
  houseTabPanel.hidden = active.tab !== "house";
  tabBtnHome.classList.remove("is-active");
  tabBtnRoom.classList.toggle("is-active", active.tab === "room");
  tabBtnHouse.classList.toggle("is-active", active.tab === "house");
  currentTab = active.tab;

  if (active.tab === "room") {
    showState("progress");
    startProgressMessages();
    activeRoomProjectId = active.id;
    roomPollCancelled = false;
    pollProject(active.id);
  } else {
    showHouseState("progress");
    startHouseProgress();
    activeHouseProjectId = active.id;
    housePollCancelled = false;
    pollHouseProject(active.id);
  }
}

function savePendingGeneration(tab, fields) {
  try {
    sessionStorage.setItem(PENDING_GENERATION_KEY, JSON.stringify({ tab, ...fields }));
  } catch {
    // sessionStorage full/unavailable (e.g. private browsing) - not fatal,
    // the user just re-enters their fields after logging in.
  }
}

async function restorePendingGeneration() {
  const raw = sessionStorage.getItem(PENDING_GENERATION_KEY);
  if (!raw) return;
  sessionStorage.removeItem(PENDING_GENERATION_KEY);

  let pending;
  try {
    pending = JSON.parse(raw);
  } catch {
    return;
  }

  if (pending.tab !== "room" && pending.tab !== "house") return;

  // Instant switch (no GSAP transition) - this runs at page load, before the
  // user has seen the Home tab at all, so there's nothing to transition from.
  homeTabPanel.hidden = true;
  roomTabPanel.hidden = pending.tab !== "room";
  houseTabPanel.hidden = pending.tab !== "house";
  tabBtnHome.classList.remove("is-active");
  tabBtnRoom.classList.toggle("is-active", pending.tab === "room");
  tabBtnHouse.classList.toggle("is-active", pending.tab === "house");
  currentTab = pending.tab;

  if (pending.tab === "room") {
    interiorStyleDropdown.setValue(pending.interiorStyle || "");
    colorPaletteDropdown.setValue(pending.colorPalette || "");
    additionalInstructionsInput.value = pending.additionalInstructions || "";
    cityInput.value = pending.city || "";
    roomLengthInput.value = pending.roomLength || "";
    roomWidthInput.value = pending.roomWidth || "";
    roomHeightInput.value = pending.roomHeight || "";
    roomDimensionUnitInput.value = pending.roomDimensionUnit || "ft";
    updateGenerateButtonState();
  } else {
    houseLengthInput.value = pending.length || "";
    houseWidthInput.value = pending.width || "";
    houseUnitInput.value = pending.unit || "ft";
    houseFloorCountInput.value = pending.floorCount || "1";
    houseBedroomsInput.value = pending.bedrooms || "3";
    houseBathroomsInput.value = pending.bathrooms || "2";
    houseExtrasInput.value = pending.extras || "";
  }

  if (!pending.fileDataUrl) return;
  try {
    const blob = await (await fetch(pending.fileDataUrl)).blob();
    const file = new File([blob], pending.fileName || "photo", {
      type: pending.fileType || blob.type,
    });
    const dt = new DataTransfer();
    dt.items.add(file);
    if (pending.tab === "room") {
      fileInput.files = dt.files;
      setSelectedFile(file);
    } else {
      houseFileInput.files = dt.files;
      setHouseSelectedFile(file);
    }
  } catch {
    // Best-effort - if the data URL can't be turned back into a file, the
    // text fields are still restored above, which is most of the way there.
  }
}

const TIERS = [
  { key: "original", label: "Original", desc: "Your uploaded room." },
  { key: "economical", label: "Economical", desc: "Fresh paint and clean practical finishes." },
  { key: "mid", label: "Mid-Range", desc: "Warm woods and upgraded fixtures." },
  { key: "premium", label: "Premium", desc: "Marble, brass, and designer lighting." },
];

// Real-signal-driven progress model. Replaced an earlier fixed-timeline/9-item
// checklist design: showing 9 growing rows felt cluttered for a ~90s wait, and
// (more importantly) grouping into 4 stages lets 3 of them key off state the
// backend ALREADY returns on every poll (app/main.py's get_project already
// includes per-tier image URLs the moment each finishes, plus materials_status
// and room_description) - so stages 2-4 below track REALITY, not a script.
// Only stage 1 ("Analyzing your room") has no finer-grained real signal to key
// off (describe_room/tier_notes run before the image loop, with only
// room_description observable) - it shows immediately at submit time and
// yields the moment anything real shows up.
// 3 real stages, not 4 - "Finalizing" used to sit after this and never
// corresponded to any real distinct backend step, so it just sat active
// (spinning, unchecked) for a while after the real work was already done,
// reading as "one more thing is still happening" when nothing was.
const STAGES = [
  { label: "Analyzing your room…" },
  { label: "Generating concepts…" },
  { label: "Preparing your cost estimates…" },
];

// Order matches TIERS above (Economical, Mid-Range, Premium) - the two were
// briefly inconsistent (results grid led with Premium while this led with
// Economical), which read as a bug once both were visible back to back;
// both now use the same low-to-high order throughout the site.
const CONCEPT_PREVIEW_TIERS = [
  { key: "economical", label: "Economical" },
  { key: "mid", label: "Mid-Range" },
  { key: "premium", label: "Premium" },
];

// If nothing real has happened (no stage advance, no concept reveal, no
// materials settling) for this long, fade in a reassurance line rather than
// leaving a bare spinner with no text.
const STALL_REASSURANCE_MS = 15000;

/* ---------- Real-signal-driven progress-bar fill ---------- */
/* Previously a hardcoded timer (0.6%/150ms up to a 92% cap - reached in ~23s
   regardless of how long the actual generation took, then sat frozen there
   for the remaining 1-5 minutes, completely decoupled from real progress - a
   real, reported bug). Replaced with a fill anchored to the SAME real signals
   the stage stepper already uses (deriveStageIndex/deriveHouseStageIndex) via
   setProgress(confirmed, ceiling), called every poll:
     - `confirmed` is the percent JUSTIFIED by data that has actually arrived
       (e.g. all 3 tier images present) - the bar is allowed to sit at this
       value indefinitely, monotonically non-decreasing.
     - `ceiling` is a soft cap just below the NEXT expected milestone - between
       polls (a 3s gap) the bar gently creeps from `confirmed` toward `ceiling`
       via requestAnimationFrame, decelerating as it approaches, so it's never
       visibly frozen but also never overtakes a milestone it hasn't actually
       confirmed yet.
   No wall-clock/timer assumption anywhere - a 1-minute and a 5-minute
   generation both pace correctly, since the bar only ever moves in response
   to (or in anticipation of, within the soft ceiling) a real poll response.
   finish() is still the only path to 100%, called solely from the real
   completion callback. */
function createSignalFill(barEl) {
  let current = 0;
  let confirmed = 0;
  let ceiling = 0;
  let rafId = null;
  let running = false;

  function render() {
    barEl.style.width = `${current}%`;
  }

  function tick() {
    if (!running) return;
    const cap = Math.max(confirmed, ceiling);
    if (current < cap) {
      // Decelerating step (proportional to remaining gap), with a small
      // minimum so it never crawls to an imperceptible stop.
      const gap = cap - current;
      const step = Math.max(gap * 0.05, 0.08);
      current = Math.min(cap, current + step);
      render();
    }
    rafId = requestAnimationFrame(tick);
  }

  function start() {
    current = 0;
    confirmed = 0;
    ceiling = 6; // small immediate trickle so it's not frozen at 0% pre-first-poll
    running = true;
    render();
    if (rafId) cancelAnimationFrame(rafId);
    rafId = requestAnimationFrame(tick);
  }

  // Both values only ever move forward - a later poll can't un-confirm
  // progress or lower the ceiling, even if (unexpectedly) a computed value
  // came back smaller than before.
  function setProgress(newConfirmed, newCeiling) {
    confirmed = Math.max(confirmed, newConfirmed);
    ceiling = Math.max(ceiling, Math.min(newCeiling, 99)); // trickle alone never reaches 100
  }

  function finish() {
    running = false;
    if (rafId) cancelAnimationFrame(rafId);
    current = 100;
    render();
  }

  return { start, setProgress, finish };
}

const roomProgressFill = createSignalFill(progressBarFill);
const houseProgressFill = createSignalFill(houseProgressBarFill);

// Milestone weights for the room-redesign bar (sum of the non-"done" values
// tops out at 92%, leaving the final jump to 100% for the real "done"
// response via finish() - see createSignalFill's docstring). Representative
// weights, not measured timings: the point is monotonic real-signal
// anchoring, not modeling exactly how long each stage takes.
function computeRoomProgressTarget(data) {
  let confirmed = 5; // polling at all means the pipeline has at least started
  let ceiling = 15;

  if (data.room_description) {
    confirmed = 15;
    ceiling = 15 + 21;
  }

  const tierCount = CONCEPT_PREVIEW_TIERS.filter(
    (tier) => data.images && data.images[tier.key]
  ).length;
  if (tierCount > 0) {
    confirmed = 15 + tierCount * 21;
    ceiling = confirmed + (tierCount < 3 ? 21 : 14);
  }

  const materialsSettled = data.materials_status === "done" || data.materials_status === "skipped";
  if (materialsSettled) {
    confirmed = 92;
    ceiling = 98;
  }

  return { confirmed, ceiling };
}

// Mirrors computeRoomProgressTarget()'s reasoning, scaled to the house
// flow's 3-stage shape (plot analysis -> blueprint -> render).
function computeHouseProgressTarget(data) {
  let confirmed = 6;
  let ceiling = 22;

  if (data.plot_description) {
    confirmed = 22;
    ceiling = 45;
  }

  if (data.blueprint_status && data.blueprint_status !== "idle") {
    if (data.blueprint_status === "done") {
      confirmed = 70;
      ceiling = 92;
    } else {
      confirmed = 45;
      ceiling = 70;
    }
  }

  if (data.images && data.images.render) {
    confirmed = 92;
    ceiling = 98;
  }

  return { confirmed, ceiling };
}

let selectedFile = null;
let currentStageIndex = -1;
let revealedConceptTiers = new Set();
let materialsWereRunning = false;
let stallTimer = null;

/* ---------- File selection ---------- */

// Generate button stays disabled until every REQUIRED field is filled: a photo,
// an Interior Style, and a Color Palette (Additional Instructions and city stay
// optional). Re-checked on every relevant change instead of only on file select,
// since a user can pick the photo first and the dropdowns after, in any order.
function updateGenerateButtonState() {
  generateBtn.disabled = !(selectedFile && interiorStyleSelect.value && colorPaletteSelect.value);
}

function setSelectedFile(file) {
  if (!file) return;
  selectedFile = file;

  const reader = new FileReader();
  reader.onload = () => {
    previewImg.src = reader.result;
    previewFilename.textContent = file.name;
    dropzoneEmpty.hidden = true;
    dropzonePreview.hidden = false;
    updateGenerateButtonState();
  };
  reader.readAsDataURL(file);
}

function clearSelectedFile() {
  selectedFile = null;
  fileInput.value = "";
  updateGenerateButtonState();
  dropzoneEmpty.hidden = false;
  dropzonePreview.hidden = true;
}

fileInput.addEventListener("change", () => setSelectedFile(fileInput.files[0]));
interiorStyleSelect.addEventListener("change", updateGenerateButtonState);
colorPaletteSelect.addEventListener("change", updateGenerateButtonState);

removePhotoBtn.addEventListener("click", (e) => {
  // Stop this from bubbling up to the <label> (which would reopen the file picker)
  e.preventDefault();
  e.stopPropagation();
  clearSelectedFile();
});

["dragenter", "dragover"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.add("is-dragover");
  })
);

["dragleave", "drop"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.remove("is-dragover");
  })
);

dropzone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) setSelectedFile(file);
});

/* ---------- City persistence ---------- */
/* City is asked once and remembered (localStorage) so returning users don't have to
   retype it every time - the field only shows a placeholder ("e.g. Karachi") the very
   first time; after that it stays filled with whatever was last entered/confirmed,
   until the user changes it themselves. */

const CITY_STORAGE_KEY = "interior-gen:city";

const savedCity = localStorage.getItem(CITY_STORAGE_KEY);
if (savedCity) cityInput.value = savedCity;

/* ---------- Submit ---------- */

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  // Belt-and-suspenders: mirrors updateGenerateButtonState()'s gating in case a
  // form submits via Enter-key implicit submission even while the button itself
  // is disabled (browser behavior here is inconsistent).
  if (!selectedFile || !interiorStyleSelect.value || !colorPaletteSelect.value) return;

  const city = cityInput.value.trim();

  if (!city) {
    const proceedWithoutCity = confirm(
      "Without a location, pricing and local material info won't be available - only the " +
        "three redesign images will be generated. Continue without a location?"
    );
    if (!proceedWithoutCity) return;
  } else {
    localStorage.setItem(CITY_STORAGE_KEY, city);
  }

  generateBtn.disabled = true; // belt-and-suspenders against double-submit;
  // showState("progress") below already hides the whole upload view (button
  // included), but this holds even if that transition is ever changed later.

  showState("progress");
  startProgressMessages();

  const formData = new FormData();
  formData.append("file", selectedFile);
  formData.append("interior_style", interiorStyleSelect.value);
  formData.append("color_palette", colorPaletteSelect.value);
  formData.append("additional_instructions", additionalInstructionsInput.value.trim());
  formData.append("city", city);
  formData.append("display_name", await currentUserDisplayName());
  // Optional room measurements - improves material-cost accuracy when given;
  // left blank, the backend falls back to its existing Gemini-vision area
  // estimate (see app/main.py's _compute_room_dimensions()).
  if (roomLengthInput.value) formData.append("room_length", roomLengthInput.value);
  if (roomWidthInput.value) formData.append("room_width", roomWidthInput.value);
  if (roomHeightInput.value) formData.append("room_height", roomHeightInput.value);
  formData.append("dimension_unit", roomDimensionUnitInput.value);

  let projectId;
  try {
    const res = await authFetch(apiUrl("/api/projects"), { method: "POST", body: formData });
    if (res.status === 401) {
      savePendingGeneration("room", {
        fileName: selectedFile.name,
        fileType: selectedFile.type,
        fileDataUrl: previewImg.src,
        interiorStyle: interiorStyleSelect.value,
        colorPalette: colorPaletteSelect.value,
        additionalInstructions: additionalInstructionsInput.value,
        city: cityInput.value,
        roomLength: roomLengthInput.value,
        roomWidth: roomWidthInput.value,
        roomHeight: roomHeightInput.value,
        roomDimensionUnit: roomDimensionUnitInput.value,
      });
      window.location.href = "/static/login.html";
      return;
    }
    if (!res.ok) throw new Error(await res.text());
    ({ project_id: projectId } = await res.json());
  } catch (err) {
    showError(err.message);
    return;
  }

  activeRoomProjectId = projectId;
  roomPollCancelled = false;
  saveActiveGeneration("room", projectId);
  pollProject(projectId);
});

cancelGenerationBtn.addEventListener("click", async () => {
  if (!activeRoomProjectId || roomPollCancelled) return;
  if (!confirm("Cancel this generation? This can't be undone and won't be saved to History.")) return;

  const idToCancel = activeRoomProjectId;
  roomPollCancelled = true;
  clearActiveGeneration();
  showState("upload");

  try {
    await authFetch(apiUrl(`/api/projects/${idToCancel}/cancel`), { method: "POST" });
  } catch {
    // Best-effort - if this request fails, the generation just runs to
    // completion server-side and lands in History normally instead of
    // being discarded. Not worth surfacing an error for.
  }
});

/* ---------- Progress engine ---------- */
/* Drives the stage stepper, main status, sub-label, progress bar, and concept
   preview cards off real data returned by every poll of GET /api/projects/{id}
   (see app/main.py's get_project - per-tier image URLs, materials_status, and
   room_description are all already present there, even while status=running).
   The bar advances in 4 discrete jumps (25/50/75/100%) as each stage completes,
   not continuously - 100% is reserved for the real "done" response. */

function renderStageStepper() {
  progressStepperEl.innerHTML = STAGES.map(
    (stage) => `
      <li class="stage-item">
        <span class="stage-icon"></span>
        <span class="stage-label">${stage.label.replace("…", "")}</span>
      </li>
    `
  ).join("");
}

// lastStageDone lets the final stage ("Preparing your cost estimates") show
// as checked the moment materials_status genuinely settles (done/skipped),
// even on a poll where the overall project isn't marked "done" quite yet -
// otherwise that last checkmark sat unchecked/spinning for a few extra
// seconds after the real work behind it had already finished.
function setStepperState(activeIndex, allDone, lastStageDone = false) {
  const lastIndex = STAGES.length - 1;
  progressStepperEl.querySelectorAll(".stage-item").forEach((el, i) => {
    const done = allDone || i < activeIndex || (i === lastIndex && lastStageDone);
    el.classList.toggle("is-done", done);
    el.classList.toggle("is-active", !done && i === activeIndex);
  });
}

function renderConceptPreviewShell() {
  conceptPreviewsEl.innerHTML = CONCEPT_PREVIEW_TIERS.map(
    (tier) => `
      <div class="concept-card" data-tier="${tier.key}">
        <div class="concept-skeleton"></div>
        <img class="concept-img" alt="${tier.label} concept" />
        <p class="concept-label">${tier.label}</p>
      </div>
    `
  ).join("");
}

function revealConceptTier(tierKey, url) {
  if (revealedConceptTiers.has(tierKey)) return;
  revealedConceptTiers.add(tierKey);

  const card = conceptPreviewsEl.querySelector(`.concept-card[data-tier="${tierKey}"]`);
  if (!card) return;
  card.querySelector(".concept-img").src = url;
  card.classList.add("is-resolved");
}

function setStageLabel(index) {
  progressMessageEl.classList.add("is-changing");
  setTimeout(() => {
    progressMessageEl.textContent = STAGES[index].label;
    progressMessageEl.classList.remove("is-changing");
  }, 220);
}

function updateSubLabel(text) {
  progressSubtitleEl.classList.add("is-changing");
  setTimeout(() => {
    progressSubtitleEl.textContent = text;
    progressSubtitleEl.classList.remove("is-changing");
  }, 180);

  // Reset the stall-reassurance clock on every real update - it only fires
  // when NOTHING has happened (no stage advance, no concept reveal, no
  // materials settling) for STALL_REASSURANCE_MS.
  clearTimeout(stallTimer);
  stallTimer = setTimeout(() => updateSubLabel("Still working — almost there…"), STALL_REASSURANCE_MS);
}

/* Derives which of the 3 real stages we're in from REAL data already present
   on every poll response (app/main.py's get_project) - not a timer. Each
   tier's image key is committed to the DB (and so returned here) the moment
   that tier finishes generating, well before the overall project is "done". */
function deriveStageIndex(data) {
  const tierUrls = CONCEPT_PREVIEW_TIERS.map((t) => data.images && data.images[t.key]);
  const allImagesDone = tierUrls.every(Boolean);
  const anyImageDone = tierUrls.some(Boolean);

  if (allImagesDone) return 2; // Preparing your cost estimates
  if (anyImageDone || data.room_description) return 1; // Generating concepts
  return 0; // Analyzing your room
}

function applyPollUpdate(data) {
  const { confirmed, ceiling } = computeRoomProgressTarget(data);
  roomProgressFill.setProgress(confirmed, ceiling);

  for (const tier of CONCEPT_PREVIEW_TIERS) {
    const url = data.images && data.images[tier.key];
    if (url && !revealedConceptTiers.has(tier.key)) {
      revealConceptTier(tier.key, url);
      updateSubLabel(`${tier.label} concept ready`);
    }
  }

  const materialsSettled = data.materials_status === "done" || data.materials_status === "skipped";
  if (data.materials_status === "running") {
    materialsWereRunning = true;
  } else if (materialsWereRunning && data.materials_status === "done") {
    materialsWereRunning = false;
    updateSubLabel("Cost estimates ready");
  }

  const derived = deriveStageIndex(data);
  if (derived > currentStageIndex) {
    currentStageIndex = derived;
    setStageLabel(currentStageIndex);
  }
  // Runs every poll (not just on a stage advance) so materialsSettled can
  // retroactively check off the last stage as soon as it's real, without
  // waiting for the next stage index bump (there isn't one - it's the last).
  setStepperState(currentStageIndex, false, materialsSettled);
}

function startProgressMessages() {
  renderStageStepper();
  renderConceptPreviewShell();
  revealedConceptTiers = new Set();
  materialsWereRunning = false;
  currentStageIndex = 0;

  roomProgressFill.start();
  setStepperState(0, false);
  progressMessageEl.textContent = STAGES[0].label;
  progressSubtitleEl.textContent = "";

  clearTimeout(stallTimer);
  stallTimer = setTimeout(() => updateSubLabel("Still working — almost there…"), STALL_REASSURANCE_MS);
}

function stopProgressMessages() {
  clearTimeout(stallTimer);
  stallTimer = null;
  progressMessageEl.classList.remove("is-changing");
  progressSubtitleEl.classList.remove("is-changing");
}

/* Snaps everything to "done" using the final response's real data - guarantees
   every concept card resolves and every stage shows complete even if the
   backend finished faster than the last poll caught up on some sub-detail. */
function finishProgress(data, onDone) {
  for (const tier of CONCEPT_PREVIEW_TIERS) {
    const url = data.images && data.images[tier.key];
    if (url) revealConceptTier(tier.key, url);
  }
  setStepperState(STAGES.length, true);
  roomProgressFill.finish();
  stopProgressMessages();

  // Brief pause so the bar's jump to 100% and the final checkmarks are
  // actually visible before the progress card gets hidden in favor of results.
  setTimeout(onDone, 350);
}

async function pollProject(projectId) {
  // A stale poll scheduled before a Cancel click (or before a different
  // generation started) recognizes it's no longer current and stops here,
  // rather than racing the UI back into "progress" state.
  if (roomPollCancelled || projectId !== activeRoomProjectId) return;

  let data;
  try {
    const res = await authFetch(apiUrl(`/api/projects/${projectId}`));
    if (res.status === 401) {
      // A real bug this fixes: without this check, a 401 body
      // ({"detail": "login required"}) has no `status` field, so the old
      // code fell through to the setTimeout below and polled forever,
      // showing "loading" indefinitely even though the generation had
      // already finished server-side. The session token can expire mid-poll
      // (long generations, or a Clerk token-refresh failure) well after the
      // initial authenticated POST that created this project succeeded.
      // The active-generation record is deliberately KEPT here (not a
      // terminal outcome) - reloading after logging back in resumes polling.
      showError(
        "Your session expired while this was generating. Please log in again - " +
          "the generation itself finished and will be in your History once you're back in."
      );
      return;
    }
    if (!res.ok) {
      showError(`Lost connection while checking progress (status ${res.status}).`);
      return;
    }
    data = await res.json();
  } catch (err) {
    showError("Lost connection while checking progress. " + err.message);
    return;
  }

  if (data.status === "failed") {
    clearActiveGeneration();
    showError(data.error || "Unknown error during generation.");
    return;
  }

  if (data.status === "cancelled") {
    // Reached only via a resumed poll after reload finding a generation
    // that was cancelled elsewhere (e.g. another tab/device) - a same-tab
    // Cancel click already transitions the UI directly, before this branch
    // could ever run for it (see cancelGenerationBtn's handler).
    roomPollCancelled = true;
    clearActiveGeneration();
    showState("upload");
    return;
  }

  applyPollUpdate(data);

  if (data.status === "done") {
    clearActiveGeneration();
    finishProgress(data, () => {
      renderResults(data);
      showState("results");
    });
    return;
  }

  setTimeout(() => pollProject(projectId), 3000);
}

/* ---------- Results ---------- */

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

/* Materials/pricing UI, one per tier card (not shown for "original" - see
   renderResults). Built from data.materials[tierKey] + data.materials_status.
   By the time results render, run_pipeline() has already joined the materials
   futures (see app/pipeline/generate.py), so "running" is only a defensive
   state here, not the normal path - materials should already be settled
   (done, or a never-empty fallback) whenever status is "done". Shown as a
   popup modal (openMaterialsModal below) rather than an in-card dropdown -
   the card itself is too narrow for an item/price/source table without text
   wrapping awkwardly or getting visually clipped. */
function renderMaterialsSection(tierKey, data) {
  if (data.materials_status === "skipped") {
    return '<p class="materials-note">Add a location on the upload step to see local material pricing.</p>';
  }

  if (data.materials_status === "running") {
    return '<p class="materials-note materials-note-loading"><span class="materials-spinner"></span>Fetching local prices…</p>';
  }

  const tierMaterials = data.materials && data.materials[tierKey];
  if (!tierMaterials || !Array.isArray(tierMaterials.items) || tierMaterials.items.length === 0) {
    return '<p class="materials-note">Material pricing isn\'t available for this image right now.</p>';
  }

  return '<button type="button" class="materials-open-btn">View materials &amp; cost</button>';
}

function buildMaterialsModalBodyHTML(tierMaterials) {
  const rows = tierMaterials.items
    .map(
      (item) => `
        <tr>
          <td>
            <p class="material-name">${escapeHtml(item.name)}</p>
            <p class="material-spec">${escapeHtml(item.spec)}</p>
          </td>
          <td class="material-price">
            ${escapeHtml(item.price)}
            ${item.is_estimate ? '<span class="estimate-badge">Estimate</span>' : ""}
          </td>
          <td class="material-source">
            ${
              item.source_url
                ? `<a href="${escapeHtml(item.source_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.source_url)}</a>`
                : "—"
            }
          </td>
        </tr>
      `
    )
    .join("");

  return `
    <table class="materials-table">
      <thead><tr><th>Item</th><th>Price</th><th>Source</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <p class="materials-total">Rough total: <strong>${escapeHtml(tierMaterials.total)}</strong></p>
  `;
}

function openMaterialsModal(tierLabel, tierMaterials) {
  materialsModalTitle.textContent = `${tierLabel} — Materials & Cost`;
  materialsModalBody.innerHTML = buildMaterialsModalBodyHTML(tierMaterials);
  materialsModalOverlay.hidden = false;
}

function closeMaterialsModal() {
  materialsModalOverlay.hidden = true;
}

/* ---------- History modal ---------- */
/* Lists every past Room Redesign / Build a House project for the logged-in
   user (GET /api/projects, GET /api/house-projects - see app/main.py's
   list_projects/list_house_projects). Fetched fresh each time the modal
   opens rather than cached, so a generation finished since the last open
   still shows up. Reuses openLightbox/openMaterialsModal - a past project's
   images/materials are the exact same shape a live result's are. */

let historyActiveTab = "room";
let historyRoomProjects = [];
let historyHouseProjects = [];

function formatHistoryDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function renderHistoryThumb(url, label) {
  return `
    <button type="button" class="history-thumb-btn w-16 h-16 rounded-lg overflow-hidden border border-outline-variant shrink-0" data-url="${url}" data-label="${escapeHtml(label)}">
      <img class="w-full h-full object-cover" src="${url}" alt="${escapeHtml(label)}" loading="lazy"/>
    </button>
  `;
}

function renderHistoryRoomCard(project, index) {
  const thumbs = TIERS.filter((t) => project.images[t.key])
    .map((t) => renderHistoryThumb(project.images[t.key], t.label))
    .join("");

  const materialsButtons = TIERS.filter((t) => t.key !== "original")
    .filter(
      (t) =>
        project.materials &&
        project.materials[t.key] &&
        Array.isArray(project.materials[t.key].items) &&
        project.materials[t.key].items.length
    )
    .map(
      (t) => `
      <button type="button" class="history-materials-btn font-label-caps text-[11px] tracking-widest uppercase text-primary hover:underline" data-project-index="${index}" data-tier-key="${t.key}" data-tier-label="${escapeHtml(t.label)}">
        ${escapeHtml(t.label)} cost
      </button>`
    )
    .join("");

  return `
    <div class="flex flex-col gap-3 py-5 border-b border-outline-variant/70">
      <div class="flex items-center justify-between gap-4 flex-wrap">
        <p class="font-label-caps text-[11px] tracking-widest uppercase text-on-surface-variant">${formatHistoryDate(project.created_at)}${project.city ? " &middot; " + escapeHtml(project.city) : ""}</p>
        ${project.status !== "done" ? `<span class="font-label-caps text-[11px] tracking-widest uppercase text-on-surface-variant">${escapeHtml(project.status)}</span>` : ""}
      </div>
      ${project.room_description ? `<p class="font-body-md text-on-surface-variant text-sm italic">&quot;${escapeHtml(project.room_description)}&quot;</p>` : ""}
      <div class="flex gap-2 flex-wrap">${thumbs}</div>
      ${materialsButtons ? `<div class="flex gap-4 flex-wrap mt-1">${materialsButtons}</div>` : ""}
    </div>
  `;
}

function renderHistoryHouseCard(project) {
  const thumbs = [];
  if (project.images.plot) thumbs.push([project.images.plot, "Plot"]);
  if (project.images.render) thumbs.push([project.images.render, "Render"]);
  (project.blueprint_urls || []).forEach((url, i) => thumbs.push([url, `Floor ${i + 1}`]));
  // Real vendor output (app/providers/kaggle_autocad.py) - was missing here
  // entirely (a real gap: a project whose Concept Layout call finished AFTER
  // the main results page stopped polling, see pollFloorPlanCatchUp(), had
  // NO way to ever see it without this), unlike the live results page which
  // already showed it via renderHouseResults().
  if (project.floor_plan_status === "done") {
    (project.floor_plan_urls || []).forEach((url, i) =>
      thumbs.push([url, project.floor_plan_urls.length > 1 ? `Concept Layout ${i + 1}` : "Concept Layout"])
    );
  }

  const thumbsHtml = thumbs.map(([url, label]) => renderHistoryThumb(url, label)).join("");

  return `
    <div class="flex flex-col gap-3 py-5 border-b border-outline-variant/70">
      <div class="flex items-center justify-between gap-4 flex-wrap">
        <p class="font-label-caps text-[11px] tracking-widest uppercase text-on-surface-variant">${formatHistoryDate(project.created_at)}</p>
        ${project.status !== "done" ? `<span class="font-label-caps text-[11px] tracking-widest uppercase text-on-surface-variant">${escapeHtml(project.status)}</span>` : ""}
      </div>
      ${project.plot_description ? `<p class="font-body-md text-on-surface-variant text-sm italic">&quot;${escapeHtml(project.plot_description)}&quot;</p>` : ""}
      ${project.prompt ? `<p class="font-body-md text-on-surface-variant text-sm">${escapeHtml(project.prompt)}</p>` : ""}
      <div class="flex gap-2 flex-wrap">${thumbsHtml}</div>
    </div>
  `;
}

function renderHistoryTabContent() {
  if (historyActiveTab === "room") {
    historyModalBody.innerHTML = historyRoomProjects.length
      ? historyRoomProjects.map((p, i) => renderHistoryRoomCard(p, i)).join("")
      : '<p class="font-body-md text-on-surface-variant text-sm">No room redesigns yet.</p>';
  } else {
    historyModalBody.innerHTML = historyHouseProjects.length
      ? historyHouseProjects.map((p) => renderHistoryHouseCard(p)).join("")
      : '<p class="font-body-md text-on-surface-variant text-sm">No house concepts yet.</p>';
  }

  historyModalBody.querySelectorAll(".history-thumb-btn").forEach((btn) => {
    btn.addEventListener("click", () => openLightbox(btn.dataset.url, btn.dataset.label));
  });
  historyModalBody.querySelectorAll(".history-materials-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const project = historyRoomProjects[Number(btn.dataset.projectIndex)];
      openMaterialsModal(btn.dataset.tierLabel, project.materials[btn.dataset.tierKey]);
    });
  });
}

async function openHistoryModal() {
  historyModalOverlay.hidden = false;
  historyModalBody.innerHTML = '<p class="font-body-md text-on-surface-variant text-sm">Loading…</p>';

  try {
    const [roomRes, houseRes] = await Promise.all([
      authFetch(apiUrl("/api/projects")),
      authFetch(apiUrl("/api/house-projects")),
    ]);
    historyRoomProjects = roomRes.ok ? await roomRes.json() : [];
    historyHouseProjects = houseRes.ok ? await houseRes.json() : [];
  } catch {
    historyModalBody.innerHTML =
      '<p class="font-body-md text-error text-sm">Couldn\'t load your history right now.</p>';
    return;
  }

  renderHistoryTabContent();
}

function closeHistoryModal() {
  historyModalOverlay.hidden = true;
}

historyModalTabs.querySelectorAll(".history-tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    historyActiveTab = btn.dataset.historyTab;
    historyModalTabs
      .querySelectorAll(".history-tab-btn")
      .forEach((b) => b.classList.toggle("is-active", b === btn));
    renderHistoryTabContent();
  });
});

historyModalClose.addEventListener("click", closeHistoryModal);
historyModalOverlay.addEventListener("click", (e) => {
  if (e.target === historyModalOverlay) closeHistoryModal();
});

// Backend's get_image_model_label() can return "our model + OpenAI" when the
// 3 room tiers genuinely used different providers (a per-tier runtime
// fallback - see app/providers/hybrid.py). Collapsed here to a single clean
// statement rather than exposing the split to the user - "our model" wins
// when it produced anything at all, since that's the more distinctive claim.
function displayModelLabel(rawLabel) {
  if (!rawLabel) return null;
  return rawLabel.includes("our model") ? "our model" : rawLabel;
}

function renderResults(data) {
  roomDescriptionEl.textContent = data.room_description ? `"${data.room_description}"` : "";
  const modelLabel = displayModelLabel(data.image_model);
  if (modelLabel) {
    imageModelNoteEl.textContent = `Generated using ${modelLabel}`;
    imageModelNoteEl.hidden = false;
  } else {
    imageModelNoteEl.hidden = true;
  }
  resultsGrid.innerHTML = "";

  for (const tier of TIERS) {
    const url = data.images[tier.key];
    if (!url) continue;

    const isOriginal = tier.key === "original";

    const card = document.createElement("div");
    card.className = "result-card";
    card.innerHTML = `
      <div class="img-wrap">
        <img src="${url}" alt="${tier.label} redesign" loading="lazy" />
        <a class="download-btn" href="${url}" download="${downloadFilenameFor(tier.key, url)}" aria-label="Download ${tier.label} image" title="Download image">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3v12m0 0l-4-4m4 4l4-4" stroke-linecap="round" stroke-linejoin="round"/><path d="M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </a>
      </div>
      <div class="caption">
        <p class="tier-name">${tier.label}</p>
        <p class="tier-desc">${tier.desc}</p>
      </div>
      ${isOriginal ? "" : renderMaterialsSection(tier.key, data)}
    `;
    card.addEventListener("click", () => openLightbox(url, tier.label));
    card.querySelector(".download-btn").addEventListener("click", (e) => e.stopPropagation());

    const materialsEl = card.querySelector(".materials-open-btn, .materials-note");
    if (materialsEl) {
      materialsEl.addEventListener("click", (e) => {
        e.stopPropagation();
        if (materialsEl.classList.contains("materials-open-btn")) {
          openMaterialsModal(tier.label, data.materials[tier.key]);
        }
      });
    }

    resultsGrid.appendChild(card);
  }
}

function openLightbox(url, label) {
  lightboxImg.src = url;
  lightboxImg.alt = label;
  lightboxCaption.textContent = label;
  lightbox.hidden = false;
}

lightboxClose.addEventListener("click", () => (lightbox.hidden = true));
lightbox.addEventListener("click", (e) => {
  if (e.target === lightbox) lightbox.hidden = true;
});

// "A Real Example" figures (Home landing page) - clickable straight to the
// same lightbox the generator results use, since they're real output images.
document.querySelectorAll(".js-example-figure").forEach((fig) => {
  fig.addEventListener("click", () => {
    openLightbox(fig.dataset.lightboxUrl, fig.dataset.lightboxLabel);
  });
});

materialsModalClose.addEventListener("click", closeMaterialsModal);
materialsModalOverlay.addEventListener("click", (e) => {
  if (e.target === materialsModalOverlay) closeMaterialsModal();
});

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  lightbox.hidden = true;
  closeMaterialsModal();
  closeHistoryModal();
});

/* ---------- Error / retry ---------- */

function showError(message) {
  stopProgressMessages();
  errorDetail.textContent = message;
  showState("error");
}

function resetToUpload() {
  stopProgressMessages();
  roomPollCancelled = true;
  clearActiveGeneration();
  clearSelectedFile();
  interiorStyleDropdown.setValue("");
  colorPaletteDropdown.setValue("");
  additionalInstructionsInput.value = "";
  showState("upload");
}

retryBtn.addEventListener("click", resetToUpload);
startOverBtn.addEventListener("click", resetToUpload);

/* ---------- State machine (which section is visible) ---------- */

function showState(state) {
  uploadView.hidden = state !== "upload";
  progressCard.hidden = state !== "progress";
  errorCard.hidden = state !== "error";
  resultsSection.hidden = state !== "results";
}

/* ================================================================
   "Build a House" tab - a second, independent flow alongside the
   room-redesign one above. Mirrors its patterns (file selection,
   real-signal-driven progress stepper, poll loop, results-grid with
   download buttons) at a smaller scale (3 stages, one deliverable
   image, no materials panel) rather than inventing a new architecture.
   ================================================================ */

/* ---------- File selection ---------- */

let houseSelectedFile = null;

// Generate Concept only requires both dimensions - the plot photo is
// optional (2026-09, see CLAUDE.md's "Image-input investigation" entry):
// the floor plan/blueprint/DXF/Concept Layout are computed purely from
// dimensions + room program and never touch the photo, so there's no reason
// to block generation on it. A photo, when given, still improves the
// (currently disabled) exterior render and the best-effort plot analysis.
function updateHouseGenerateBtnState() {
  houseGenerateBtn.disabled = !(houseLengthInput.value.trim() && houseWidthInput.value.trim());
}

function setHouseSelectedFile(file) {
  if (!file) return;
  houseSelectedFile = file;

  const reader = new FileReader();
  reader.onload = () => {
    housePreviewImg.src = reader.result;
    housePreviewFilename.textContent = file.name;
    houseDropzoneEmpty.hidden = true;
    houseDropzonePreview.hidden = false;
    updateHouseGenerateBtnState();
  };
  reader.readAsDataURL(file);
}

function clearHouseSelectedFile() {
  houseSelectedFile = null;
  houseFileInput.value = "";
  updateHouseGenerateBtnState();
  houseDropzoneEmpty.hidden = false;
  houseDropzonePreview.hidden = true;
}

houseFileInput.addEventListener("change", () => setHouseSelectedFile(houseFileInput.files[0]));
houseLengthInput.addEventListener("input", updateHouseGenerateBtnState);
houseWidthInput.addEventListener("input", updateHouseGenerateBtnState);

houseRemovePhotoBtn.addEventListener("click", (e) => {
  e.preventDefault();
  e.stopPropagation();
  clearHouseSelectedFile();
});

["dragenter", "dragover"].forEach((evt) =>
  houseDropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    houseDropzone.classList.add("is-dragover");
  })
);

["dragleave", "drop"].forEach((evt) =>
  houseDropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    houseDropzone.classList.remove("is-dragover");
  })
);

houseDropzone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) setHouseSelectedFile(file);
});

/* ---------- Submit ---------- */

houseForm.addEventListener("submit", async (e) => {
  e.preventDefault();

  houseGenerateBtn.disabled = true; // belt-and-suspenders against double-submit

  showHouseState("progress");
  startHouseProgress();

  const formData = new FormData();
  // Plot photo is optional - only append when the user actually selected one
  // (see updateHouseGenerateBtnState()'s comment for why this is no longer
  // a hard requirement).
  if (houseSelectedFile) formData.append("file", houseSelectedFile);
  if (houseLengthInput.value) formData.append("length", houseLengthInput.value);
  if (houseWidthInput.value) formData.append("width", houseWidthInput.value);
  formData.append("unit", houseUnitInput.value);
  formData.append("floor_count", houseFloorCountInput.value);
  formData.append("bedrooms", houseBedroomsInput.value);
  formData.append("bathrooms", houseBathroomsInput.value);
  formData.append("extras", houseExtrasInput.value.trim());
  formData.append("display_name", await currentUserDisplayName());

  let houseProjectId;
  try {
    const res = await authFetch(apiUrl("/api/house-projects"), {
      method: "POST",
      body: formData,
    });
    if (res.status === 401) {
      savePendingGeneration("house", {
        fileName: houseSelectedFile ? houseSelectedFile.name : undefined,
        fileType: houseSelectedFile ? houseSelectedFile.type : undefined,
        fileDataUrl: houseSelectedFile ? housePreviewImg.src : undefined,
        length: houseLengthInput.value,
        width: houseWidthInput.value,
        unit: houseUnitInput.value,
        floorCount: houseFloorCountInput.value,
        bedrooms: houseBedroomsInput.value,
        bathrooms: houseBathroomsInput.value,
        extras: houseExtrasInput.value,
      });
      window.location.href = "/static/login.html";
      return;
    }
    if (!res.ok) throw new Error(await res.text());
    ({ house_project_id: houseProjectId } = await res.json());
  } catch (err) {
    showHouseError(err.message);
    return;
  }

  activeHouseProjectId = houseProjectId;
  housePollCancelled = false;
  saveActiveGeneration("house", houseProjectId);
  pollHouseProject(houseProjectId);
});

houseCancelGenerationBtn.addEventListener("click", async () => {
  if (!activeHouseProjectId || housePollCancelled) return;
  if (!confirm("Cancel this generation? This can't be undone and won't be saved to History.")) return;

  const idToCancel = activeHouseProjectId;
  housePollCancelled = true;
  clearActiveGeneration();
  showHouseState("upload");

  try {
    await authFetch(apiUrl(`/api/house-projects/${idToCancel}/cancel`), { method: "POST" });
  } catch {
    // Best-effort - see cancelGenerationBtn's identical comment above.
  }
});

/* ---------- Progress engine ---------- */
/* Same real-signal-driven philosophy as the room flow's stepper (see above),
   scaled down to this feature's actual shape: 4 stages, no per-item reveals,
   since there's exactly one deliverable render (no 3-tier concept previews).
   "Drawing your floor plan" tracks the free algorithmic blueprint step
   (app/pipeline/floor_layout.py + blueprint_svg.py) - the still-inert real
   floor-plan VENDOR slot (app/providers/idealhouse.py) has nothing to
   preview mid-flight, so it isn't its own stage. */

// 3 real stages, not 4 - "Finalizing" used to sit after "Generating your
// concept render" and never corresponded to a real distinct backend step
// (same fix as the room flow's stepper above), so it just sat spinning after
// the render already existed.
const HOUSE_STAGES = [
  { label: "Analyzing your plot…" },
  { label: "Drawing your floor plans…" },
  { label: "Generating your concept render…" },
];

let houseCurrentStageIndex = -1;

function renderHouseStageStepper() {
  houseProgressStepperEl.innerHTML = HOUSE_STAGES.map(
    (stage) => `
      <li class="stage-item">
        <span class="stage-icon"></span>
        <span class="stage-label">${stage.label.replace("…", "")}</span>
      </li>
    `
  ).join("");
}

// renderDone lets the final stage ("Generating your concept render") show as
// checked the moment the render image genuinely exists, even on a poll where
// the overall project isn't marked "done" quite yet - see setStepperState's
// identical lastStageDone param in the room flow above for the full reasoning.
function setHouseStepperState(activeIndex, allDone, renderDone = false) {
  const lastIndex = HOUSE_STAGES.length - 1;
  houseProgressStepperEl.querySelectorAll(".stage-item").forEach((el, i) => {
    const done = allDone || i < activeIndex || (i === lastIndex && renderDone);
    el.classList.toggle("is-done", done);
    el.classList.toggle("is-active", !done && i === activeIndex);
  });
}

function setHouseStageLabel(index) {
  houseProgressMessageEl.classList.add("is-changing");
  setTimeout(() => {
    houseProgressMessageEl.textContent = HOUSE_STAGES[index].label;
    houseProgressMessageEl.classList.remove("is-changing");
  }, 220);
}

/* Derives which of the 3 real stages we're in from REAL data on every poll
   response (app/main.py's get_house_project), not a timer - plot_description,
   blueprint_status, and images.render are all committed to the DB (and so
   returned here) as soon as each step actually finishes, well before the
   overall project is "done". blueprint_status is the free algorithmic
   blueprint step (app/pipeline/floor_layout.py + blueprint_svg.py) -
   unrelated to floor_plan_status, which stays reserved for a still-inert
   future paid vendor. */
function deriveHouseStageIndex(data) {
  if (data.blueprint_status !== "idle") return 2; // Generating your concept render
  if (data.plot_description || data.floor_plan_status !== "idle") return 1;
  return 0;
}

function applyHousePollUpdate(data) {
  const { confirmed, ceiling } = computeHouseProgressTarget(data);
  houseProgressFill.setProgress(confirmed, ceiling);

  const derived = deriveHouseStageIndex(data);
  if (derived > houseCurrentStageIndex) {
    houseCurrentStageIndex = derived;
    setHouseStageLabel(houseCurrentStageIndex);
  }
  const renderDone = Boolean(data.images && data.images.render);
  setHouseStepperState(houseCurrentStageIndex, false, renderDone);
}

function startHouseProgress() {
  renderHouseStageStepper();
  houseCurrentStageIndex = 0;

  houseProgressFill.start();
  setHouseStepperState(0, false);
  houseProgressMessageEl.textContent = HOUSE_STAGES[0].label;
  houseProgressSubtitleEl.textContent = "";
}

function stopHouseProgress() {
  houseProgressMessageEl.classList.remove("is-changing");
}

function finishHouseProgress(onDone) {
  setHouseStepperState(HOUSE_STAGES.length, true);
  houseProgressFill.finish();
  stopHouseProgress();
  setTimeout(onDone, 350);
}

async function pollHouseProject(houseProjectId) {
  // See pollProject()'s identical guard above for why this matters.
  if (housePollCancelled || houseProjectId !== activeHouseProjectId) return;

  let data;
  try {
    const res = await authFetch(apiUrl(`/api/house-projects/${houseProjectId}`));
    if (res.status === 401) {
      // Same fix as pollProject() above - see its comment for the full
      // explanation (a stale/expired session token mid-poll used to loop
      // forever instead of surfacing an error). Active-generation record
      // kept, same reasoning as pollProject()'s 401 branch.
      showHouseError(
        "Your session expired while this was generating. Please log in again - " +
          "the generation itself finished and will be in your History once you're back in."
      );
      return;
    }
    if (!res.ok) {
      showHouseError(`Lost connection while checking progress (status ${res.status}).`);
      return;
    }
    data = await res.json();
  } catch (err) {
    showHouseError("Lost connection while checking progress. " + err.message);
    return;
  }

  if (data.status === "failed") {
    clearActiveGeneration();
    showHouseError(data.error || "Unknown error during generation.");
    return;
  }

  if (data.status === "cancelled") {
    // See pollProject()'s identical branch above for when this is reached.
    housePollCancelled = true;
    clearActiveGeneration();
    showHouseState("upload");
    return;
  }

  applyHousePollUpdate(data);

  if (data.status === "done") {
    clearActiveGeneration();
    finishHouseProgress(() => {
      renderHouseResults(data);
      showHouseState("results");
    });
    lastDisplayedHouseProjectId = houseProjectId;
    // v11 (app/pipeline/generate_house.py) decoupled the Concept Layout call
    // from the project's own "done" status - it can still be "running" here
    // (a real, live-observed gap: without this, the card simply never
    // appeared unless the user manually reloaded the page). Keep a light,
    // separate catch-up poll going just for that one field, without
    // re-entering the progress UI.
    if (data.floor_plan_status === "running") {
      setTimeout(() => pollFloorPlanCatchUp(houseProjectId), 3000);
    }
    return;
  }

  setTimeout(() => pollHouseProject(houseProjectId), 3000);
}

// Guards pollFloorPlanCatchUp() from clobbering a NEWER generation's results
// if the user starts another one before the previous project's Concept
// Layout call finishes catching up.
let lastDisplayedHouseProjectId = null;

async function pollFloorPlanCatchUp(houseProjectId) {
  if (houseProjectId !== lastDisplayedHouseProjectId) return;

  let data;
  try {
    const res = await authFetch(apiUrl(`/api/house-projects/${houseProjectId}`));
    if (!res.ok) return; // best-effort catch-up only - not worth surfacing an error for this
    data = await res.json();
  } catch (err) {
    return;
  }

  if (houseProjectId !== lastDisplayedHouseProjectId) return; // re-check after the await

  if (data.floor_plan_status === "running") {
    setTimeout(() => pollFloorPlanCatchUp(houseProjectId), 3000);
    return;
  }

  // Settled (done/unavailable/not_configured) - refresh the results view to
  // pick up the Concept Layout card (or its "unavailable" notice), same
  // idempotent render used for the initial completion.
  renderHouseResults(data);
}

/* ---------- Results ---------- */

const HOUSE_RESULT_TIERS = [
  { key: "plot", label: "Original Plot", desc: "Your uploaded plot photo." },
  { key: "render", label: "Concept Render", desc: "AI-generated exterior/interior concept." },
];

// Feasibility hard-gate result (app/pipeline/feasibility.py) - shown as a
// banner above the results grid. "not_feasible" means the blueprint/Concept
// Layout stages were skipped entirely (see their own gated blocks below);
// "tight" is informational only - everything still generated normally, just
// close to real-world minimum room sizes.
function renderHouseFeasibilityBanner(feasibility) {
  if (!feasibility || feasibility.verdict === "feasible") {
    houseFeasibilityBannerEl.hidden = true;
    houseFeasibilityBannerEl.innerHTML = "";
    return;
  }

  const isBlocking = feasibility.verdict === "not_feasible";
  houseFeasibilityBannerEl.className = isBlocking
    ? "max-w-2xl mx-auto mb-10 px-6 py-4 border bg-error-container/10 border-error/30"
    : "max-w-2xl mx-auto mb-10 px-6 py-4 border bg-tertiary-container/40 border-tertiary/40";
  const titleClass = isBlocking ? "text-error" : "text-on-tertiary-container";
  const title = isBlocking ? "Requested program doesn't fit this plot" : "Tight fit";
  houseFeasibilityBannerEl.innerHTML = `
    <p class="font-headline-sm ${titleClass} font-semibold mb-1 text-center">${title}</p>
    <p class="font-body-md text-on-surface-variant text-sm text-center">${feasibility.explanation || ""}</p>
  `;
  houseFeasibilityBannerEl.hidden = false;
}

function renderHouseResults(data) {
  plotDescriptionEl.textContent = data.plot_description ? `"${data.plot_description}"` : "";
  if (data.render_model) {
    // House rendering has no runtime fallback (unlike room tiers), so
    // render_model is always a single value - no "+" collapsing needed here.
    houseImageModelNoteEl.textContent = `Generated using ${data.render_model}`;
    houseImageModelNoteEl.hidden = false;
  } else {
    houseImageModelNoteEl.hidden = true;
  }
  renderHouseFeasibilityBanner(data.feasibility);
  houseResultsGrid.innerHTML = "";

  for (const tier of HOUSE_RESULT_TIERS) {
    const url = data.images[tier.key];
    if (!url) continue;

    const card = document.createElement("div");
    card.className = "result-card";
    card.innerHTML = `
      <div class="img-wrap">
        <img src="${url}" alt="${tier.label}" loading="lazy" />
        <a class="download-btn" href="${url}" download="${downloadFilenameFor(tier.key, url)}" aria-label="Download ${tier.label}" title="Download image">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3v12m0 0l-4-4m4 4l4-4" stroke-linecap="round" stroke-linejoin="round"/><path d="M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </a>
      </div>
      <div class="caption">
        <p class="tier-name">${tier.label}</p>
        <p class="tier-desc">${tier.desc}</p>
      </div>
    `;
    card.addEventListener("click", () => openLightbox(url, tier.label));
    card.querySelector(".download-btn").addEventListener("click", (e) => e.stopPropagation());
    houseResultsGrid.appendChild(card);
  }

  // Free algorithmic blueprint step (app/pipeline/floor_layout.py +
  // blueprint_svg.py) - unrelated to floor_plan_status below, which stays
  // reserved for a still-inert future paid vendor. This IS the floor-plan
  // image - 100% deterministic (no AI model ever touches geometry/
  // dimensions/text), with furniture and staircase symbols (v5) - hence the
  // non-disclaiming label, unlike a hypothetical AI-generated floor plan. An
  // earlier AI-drawn "CAD plan" enhancement was tried and reverted after a
  // real generation showed hallucinated dimension text - see CLAUDE.md.
  if (data.blueprint_status === "done" && data.blueprint_urls && data.blueprint_urls.length) {
    data.blueprint_urls.forEach((url, i) => {
      const floorNumber = i + 1;
      const label = `Floor ${floorNumber} Layout`;
      // Real AutoCAD-format (.dxf) export of this same floor's geometry - no
      // AI model involved (app/pipeline/blueprint_dxf.py), same room
      // rectangles as the PNG above. May be missing for an individual floor
      // even when the PNG succeeded (DXF export is its own best-effort step
      // server-side) - the link is simply omitted in that case.
      const dxfUrl = (data.blueprint_dxf_urls || [])[i];
      const dxfLinkHtml = dxfUrl
        ? `<a class="materials-open-btn" href="${dxfUrl}" download="blueprint_floor${floorNumber}.dxf">Download AutoCAD File (.dxf)</a>`
        : "";
      const card = document.createElement("div");
      card.className = "result-card";
      card.innerHTML = `
        <div class="img-wrap">
          <img src="${url}" alt="${label}" loading="lazy" />
          <a class="download-btn" href="${url}" download="blueprint_floor${floorNumber}.png" aria-label="Download ${label}" title="Download image">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3v12m0 0l-4-4m4 4l4-4" stroke-linecap="round" stroke-linejoin="round"/><path d="M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </a>
        </div>
        <div class="caption">
          <p class="tier-name">${label}</p>
          <p class="tier-desc">Computed from your stated dimensions - an accurate room layout, though not a full architectural/code-compliant plan.</p>
        </div>
        ${dxfLinkHtml}
      `;
      card.addEventListener("click", () => openLightbox(url, label));
      card.querySelector(".download-btn").addEventListener("click", (e) => e.stopPropagation());
      const dxfLink = card.querySelector(".materials-open-btn");
      if (dxfLink) dxfLink.addEventListener("click", (e) => e.stopPropagation());
      houseResultsGrid.appendChild(card);
    });
  }

  // Real vendor output (app/providers/kaggle_autocad.py, a friend-hosted
  // Kaggle SDXL+ControlNet model - see CLAUDE.md for the honest quality
  // evaluation). One card PER FLOOR (data.floor_plan_urls, floor-ordered) -
  // the vendor generates one image per floor natively, so showing only the
  // first would silently hide floors 2+ for any multi-floor request.
  // Deliberately labeled "Concept Layout - not a precise blueprint" (no
  // image-gen model guarantees dimensional accuracy) - NOT the same as the
  // blueprint cards above, which already are dimensionally accurate.
  if (data.floor_plan_status === "done" && data.floor_plan_urls && data.floor_plan_urls.length) {
    data.floor_plan_urls.forEach((url, i) => {
      const floorNumber = i + 1;
      const label = data.floor_plan_urls.length > 1 ? `Concept Layout - Floor ${floorNumber}` : "Concept Layout";
      const card = document.createElement("div");
      card.className = "result-card";
      card.innerHTML = `
        <div class="img-wrap">
          <img src="${url}" alt="${label}" loading="lazy" />
          <a class="download-btn" href="${url}" download="floor_plan_floor${floorNumber}.png" aria-label="Download ${label}" title="Download image">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3v12m0 0l-4-4m4 4l4-4" stroke-linecap="round" stroke-linejoin="round"/><path d="M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </a>
        </div>
        <div class="caption">
          <p class="tier-name">${label}</p>
          <p class="tier-desc">Not a precise blueprint - no image-gen API guarantees dimensional accuracy.</p>
        </div>
      `;
      card.addEventListener("click", () => openLightbox(url, label));
      card.querySelector(".download-btn").addEventListener("click", (e) => e.stopPropagation());
      houseResultsGrid.appendChild(card);
    });
  }

  // Real, visible placeholder while Model B (the Kaggle Concept Layout call)
  // is still generating - v11 (app/pipeline/generate_house.py) deliberately
  // decoupled it from the project's own "done" status (a live ~2min/floor
  // Kaggle call was making the WHOLE page wait), but that meant the card
  // just silently appeared later with zero on-screen indication anything was
  // still happening. Reuses the same shimmer-skeleton visual language as the
  // Room Redesign concept-preview cards (components.css's .concept-skeleton)
  // for consistency, instead of a bare spinner. Gets replaced automatically
  // the next time renderHouseResults() runs (pollFloorPlanCatchUp() calls it
  // again once floor_plan_status settles) - no separate removal logic needed
  // since the whole grid is rebuilt from scratch each call.
  if (data.floor_plan_status === "running") {
    const card = document.createElement("div");
    card.className = "result-card";
    card.innerHTML = `
      <div class="concept-card">
        <div class="concept-skeleton"></div>
      </div>
      <div class="caption">
        <p class="tier-name">Concept Layout</p>
        <p class="tier-desc">Still generating - this can take a couple of minutes. The rest of your results are ready below.</p>
      </div>
    `;
    houseResultsGrid.appendChild(card);
  }

  // "unavailable" (distinct from "not_configured") means a vendor WAS
  // configured but the live call failed in a way that looks like the Kaggle
  // notebook session being offline (app/providers/session_errors.py) - a
  // real, actionable problem, so it gets a visible note instead of the card
  // just silently never appearing (which is the correct, silent behavior
  // for "not_configured" - no vendor set up at all). Styled like the "tight
  // fit" feasibility banner above (informational, not blocking) - see
  // renderHouseFeasibilityBanner() - spans the full grid width via inline
  // style since houseResultsGrid is a CSS grid of image cards, not a place
  // this text block would otherwise sit correctly among them.
  if (data.floor_plan_status === "unavailable") {
    const notice = document.createElement("div");
    notice.className = "px-6 py-4 border bg-tertiary-container/40 border-tertiary/40";
    notice.style.gridColumn = "1 / -1";
    notice.innerHTML = `
      <p class="font-headline-sm text-on-tertiary-container font-semibold mb-1 text-center">Concept Layout unavailable</p>
      <p class="font-body-md text-on-surface-variant text-sm text-center">${
        data.floor_plan_error || "The AI model's Kaggle session appears to be offline."
      }</p>
    `;
    houseResultsGrid.appendChild(notice);
  }
}

/* ---------- Error / retry ---------- */

function showHouseError(message) {
  stopHouseProgress();
  houseErrorDetail.textContent = message;
  showHouseState("error");
}

function resetToHouseUpload() {
  stopHouseProgress();
  housePollCancelled = true;
  clearActiveGeneration();
  clearHouseSelectedFile();
  houseLengthInput.value = "";
  houseWidthInput.value = "";
  houseExtrasInput.value = "";
  showHouseState("upload");
}

houseRetryBtn.addEventListener("click", resetToHouseUpload);
houseStartOverBtn.addEventListener("click", resetToHouseUpload);

/* ---------- State machine (which section is visible, within this tab) ---------- */

function showHouseState(state) {
  houseUploadView.hidden = state !== "upload";
  houseProgressCard.hidden = state !== "progress";
  houseErrorCard.hidden = state !== "error";
  houseResultsSection.hidden = state !== "results";
}

/* ---------- Scroll reveal ---------- */
/* Fades/rises elements in as they enter the viewport. One-time per element
   (unobserves after revealing) rather than re-triggering on every scroll -
   a subtle motion cue, not a distraction. Falls back to instantly visible
   if IntersectionObserver isn't available. */

const revealTargets = document.querySelectorAll("[data-reveal]");

if (revealTargets.length) {
  if ("IntersectionObserver" in window) {
    const revealObserver = new IntersectionObserver(
      (entries, observer) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const delay = entry.target.dataset.revealDelay || 0;
          entry.target.style.transitionDelay = `${delay}ms`;
          entry.target.classList.add("is-revealed");
          observer.unobserve(entry.target);
        }
      },
      { threshold: 0.15 }
    );
    revealTargets.forEach((el) => revealObserver.observe(el));
  } else {
    revealTargets.forEach((el) => el.classList.add("is-revealed"));
  }
}

/* ---------- "Real Example" authenticity toast (Home landing page only) ---------- */
/* Fires once, the first time #examples (the Original/Economical/Mid/Premium
   grid) scrolls into view, to make sure a first-time visitor actually clicks
   that those images are real generated output, not stock photography. Shown
   at most once ever per browser (localStorage, not sessionStorage - this
   isn't a per-visit thing, it's a "you've already been told this" thing) so
   returning visitors never see it again. */

const REAL_EXAMPLE_TOAST_KEY = "interior-gen:seen-real-example-toast";
const REAL_EXAMPLE_TOAST_AUTOHIDE_MS = 9000;

const realExampleToast = document.getElementById("real-example-toast");
const realExampleToastClose = document.getElementById("real-example-toast-close");
const examplesSection = document.getElementById("examples");

function hideRealExampleToast() {
  if (!realExampleToast || realExampleToast.hidden) return;
  if (typeof gsap !== "undefined" && !prefersReducedMotion) {
    gsap.to(realExampleToast, {
      y: 16,
      opacity: 0,
      duration: 0.25,
      ease: "power1.in",
      onComplete: () => {
        realExampleToast.hidden = true;
        gsap.set(realExampleToast, { clearProps: "all" });
      },
    });
  } else {
    realExampleToast.hidden = true;
  }
}

if (realExampleToast && examplesSection && !localStorage.getItem(REAL_EXAMPLE_TOAST_KEY)) {
  realExampleToastClose?.addEventListener("click", hideRealExampleToast);

  if ("IntersectionObserver" in window) {
    const toastObserver = new IntersectionObserver(
      (entries, observer) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          localStorage.setItem(REAL_EXAMPLE_TOAST_KEY, "1");
          realExampleToast.hidden = false;
          if (typeof gsap !== "undefined" && !prefersReducedMotion) {
            gsap.from(realExampleToast, { y: 24, opacity: 0, duration: 0.5, ease: "power2.out" });
          }
          setTimeout(hideRealExampleToast, REAL_EXAMPLE_TOAST_AUTOHIDE_MS);
          observer.disconnect();
        }
      },
      { threshold: 0.4 }
    );
    toastObserver.observe(examplesSection);
  }
}

/* ---------- GSAP hero + stagger reveals (Home landing page only) ---------- */
/* See .claude/skills/gsap-transitions/SKILL.md. Migrated section-by-section -
   the plain [data-reveal] IntersectionObserver above still drives the
   simpler single-block fades (About, cost-estimate highlight, closing CTA);
   these two spots get real GSAP sequencing/stagger instead, on top of the
   sections that had their [data-reveal] removed when they were migrated. */

if (typeof gsap !== "undefined" && !prefersReducedMotion) {
  gsap.registerPlugin(ScrollTrigger);

  const heroCopy = document.getElementById("hero-copy");
  if (heroCopy) {
    const eyebrow = heroCopy.querySelector(".hero-eyebrow");
    const heading = heroCopy.querySelector("h1");
    const subtext = heroCopy.querySelector("p");
    const ctaItems = gsap.utils.toArray(heroCopy.querySelectorAll(".hero-cta-row > *"));
    // .set() the starting state then .to() the end state, rather than
    // .from() - a raw .from() on a staggered array target inside a
    // timeline with an overlapping "-=" position reliably got stuck at
    // its start values in this GSAP version (verified live: the timeline
    // reported progress() === 1 while the DOM never got the final
    // opacity/transform). .set()+.to() does not have that failure mode.
    gsap.set([eyebrow, subtext], { y: 16, opacity: 0 });
    gsap.set(heading, { y: 24, opacity: 0 });
    gsap.set(ctaItems, { y: 14, opacity: 0 });
    gsap
      .timeline({ defaults: { ease: "power3.out" } })
      .to(eyebrow, { y: 0, opacity: 1, duration: 0.5 })
      .to(heading, { y: 0, opacity: 1, duration: 0.7 }, "-=0.25")
      .to(subtext, { y: 0, opacity: 1, duration: 0.6 }, "-=0.35")
      .to(ctaItems, { y: 0, opacity: 1, duration: 0.5, stagger: 0.1 }, "-=0.3");
  }

  document.querySelectorAll("[data-gsap-stagger]").forEach((container) => {
    const items = gsap.utils.toArray(container.children);
    gsap.set(items, { y: 24, opacity: 0 });
    gsap.to(items, {
      y: 0,
      opacity: 1,
      duration: 0.5,
      ease: "power2.out",
      stagger: 0.06,
      scrollTrigger: {
        trigger: container,
        start: "top 85%",
        toggleActions: "restart reverse restart reverse",
      },
    });
  });
}

// A real in-flight generation takes priority over a merely-drafted form (the
// two shouldn't normally coexist, but if they somehow do, resuming a
// generation that's actually running beats restoring unsubmitted field
// values). Deliberately placed at the very end of this file, after every
// module-level const/let/function it depends on (roomProgressFill,
// houseProgressFill, all the tab/dropdown/input elements, etc.) - this used
// to run near the top of the file (right after resumeActiveGeneration()/
// restorePendingGeneration() were defined) and crashed with a temporal-dead-
// zone ReferenceError the moment resumeActiveGeneration() reached
// startProgressMessages()'s roomProgressFill.start() call, since that const
// wasn't initialized yet at that point in the file. Because that crash
// happened inside an unawaited async function, it silently aborted AFTER
// already switching to the progress tab/card but BEFORE starting the
// progress fill or the first poll - a real, reported bug: refreshing mid-
// generation left the page stuck showing 0% forever, un-recoverable without
// manually clearing localStorage. The try/catch below is an extra safety
// net so a future bug in either resume path degrades to a clean upload
// screen instead of a stuck/broken page.
// Both target functions are async - a plain try/catch around the call
// wouldn't catch a rejection surfacing after their first internal await, so
// .catch() on the returned promise is the correct safety net here.
if (localStorage.getItem(ACTIVE_GENERATION_KEY)) {
  resumeActiveGeneration().catch((err) => {
    console.error("Failed to resume active generation:", err);
    clearActiveGeneration();
  });
} else {
  restorePendingGeneration().catch((err) => {
    console.error("Failed to restore pending generation:", err);
  });
}
