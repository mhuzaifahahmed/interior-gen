const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const uploadView = document.getElementById("upload-view");
const form = document.getElementById("upload-form");
const fileInput = document.getElementById("file-input");
const dropzone = document.getElementById("dropzone");
const dropzoneEmpty = document.getElementById("dropzone-empty");
const dropzonePreview = document.getElementById("dropzone-preview");
const previewImg = document.getElementById("preview-img");
const previewFilename = document.getElementById("preview-filename");
const generateBtn = document.getElementById("generate-btn");
const removePhotoBtn = document.getElementById("remove-photo-btn");
const stylePromptInput = document.getElementById("style-prompt");
const cityInput = document.getElementById("city-input");

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
/* Real accounts (app/auth.py) - the room/house generators are gated behind
   login (require_user in app/main.py), so a logged-out visitor can still
   browse the landing page and switch tabs, but POSTing to /api/projects or
   /api/house-projects 401s. checkAuthState() drives which nav block shows
   (guest links vs. username + log out) and doubles as the redirect trigger
   for a 401 hit mid-submit. */

const navAuthGuest = document.getElementById("nav-auth-guest");
const navAuthUser = document.getElementById("nav-auth-user");
const navUsernameEl = document.getElementById("nav-username");
const navUserAvatarEl = document.getElementById("nav-user-avatar");
const navMenuNameEl = document.getElementById("nav-menu-name");
const navMenuEmailEl = document.getElementById("nav-menu-email");
const navLoginBtn = document.getElementById("nav-login-btn");
const navSignupBtn = document.getElementById("nav-signup-btn");
const navLogoutBtn = document.getElementById("nav-logout-btn");

// Two-letter monogram for the avatar chip - initials of the first two words of
// the display name, or the first two characters if it's a single word.
function initialsFrom(name) {
  const words = (name || "").trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

navLoginBtn.addEventListener("click", () => (window.location.href = "/login"));
navSignupBtn.addEventListener("click", () => (window.location.href = "/signup"));
navLogoutBtn.addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST" });
  window.location.href = "/";
});

async function checkAuthState() {
  try {
    const res = await fetch("/api/auth/me");
    if (!res.ok) throw new Error("not logged in");
    const user = await res.json();
    const displayName = user.full_name || user.username;
    navUsernameEl.textContent = displayName;
    navUserAvatarEl.textContent = initialsFrom(displayName);
    navMenuNameEl.textContent = displayName;
    navMenuEmailEl.textContent = user.email || "";
    navAuthGuest.classList.add("hidden");
    navAuthUser.classList.remove("hidden");
  } catch {
    navAuthGuest.classList.remove("hidden");
    navAuthUser.classList.add("hidden");
  }
}

checkAuthState();

/* ---------- User menu dropdown (History) ---------- */

const navUserMenuBtn = document.getElementById("nav-user-menu-btn");
const navUserMenuChevron = document.getElementById("nav-user-menu-chevron");
const navUserMenu = document.getElementById("nav-user-menu");
const navHistoryBtn = document.getElementById("nav-history-btn");

function closeNavUserMenu() {
  navUserMenu.classList.add("hidden");
  navUserMenuChevron.style.transform = "rotate(0deg)";
  navUserMenuBtn.setAttribute("aria-expanded", "false");
}

navUserMenuBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  const isOpen = !navUserMenu.classList.contains("hidden");
  if (isOpen) {
    closeNavUserMenu();
  } else {
    navUserMenu.classList.remove("hidden");
    navUserMenuChevron.style.transform = "rotate(180deg)";
    navUserMenuBtn.setAttribute("aria-expanded", "true");
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

const houseUploadView = document.getElementById("house-upload-view");
const houseForm = document.getElementById("house-upload-form");
const houseFileInput = document.getElementById("house-file-input");
const houseDropzone = document.getElementById("house-dropzone");
const houseDropzoneEmpty = document.getElementById("house-dropzone-empty");
const houseDropzonePreview = document.getElementById("house-dropzone-preview");
const housePreviewImg = document.getElementById("house-preview-img");
const housePreviewFilename = document.getElementById("house-preview-filename");
const houseGenerateBtn = document.getElementById("house-generate-btn");
const houseRemovePhotoBtn = document.getElementById("house-remove-photo-btn");
const houseLengthInput = document.getElementById("house-length");
const houseWidthInput = document.getElementById("house-width");
const houseUnitInput = document.getElementById("house-unit");
const housePromptInput = document.getElementById("house-prompt");

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
    stylePromptInput.value = pending.styleNotes || "";
    cityInput.value = pending.city || "";
  } else {
    houseLengthInput.value = pending.length || "";
    houseWidthInput.value = pending.width || "";
    houseUnitInput.value = pending.unit || "ft";
    housePromptInput.value = pending.prompt || "";
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

restorePendingGeneration();

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

/* ---------- Simulated progress-bar fill ---------- */
/* The bar itself fills continuously rather than sitting still between the
   stage-stepper's real 25/50/75% checkpoints and jumping ahead each time one
   lands (which read as "loading point by point"). It's still gated on the
   real "done" signal - finish() is only ever called from the real completion
   callback, so the bar can never show 100% before the result actually is.
   The motion in between is simulated: a smooth decelerating creep up to a
   soft cap, with one randomized brief pause partway through so it reads as a
   real process working on something rather than a perfectly linear bar. The
   stage stepper/labels are untouched by this - they still only ever reflect
   real signals (deriveStageIndex/deriveHouseStageIndex), only the bar's own
   fill motion is simulated. */
function createSimulatedFill(barEl) {
  const CAP = 92; // never auto-reach 100% - only finish() does that
  let timer = null;
  let percent = 0;
  let stallUntil = null;
  let stallScheduled = false;

  function start() {
    percent = 0;
    stallScheduled = false;
    stallUntil = null;
    barEl.style.width = "0%";

    const stallAtPercent = 35 + Math.random() * 30; // somewhere between 35-65%
    const stallDurationMs = 900 + Math.random() * 1400; // 0.9-2.3s

    clearInterval(timer);
    timer = setInterval(() => {
      const now = Date.now();
      if (stallUntil !== null) {
        if (now < stallUntil) return;
        stallUntil = null; // stall over, resume filling
      }
      if (!stallScheduled && percent >= stallAtPercent) {
        stallScheduled = true;
        stallUntil = now + stallDurationMs;
        return;
      }
      // Constant step rate - previously eased out (step shrank toward 0 as
      // percent approached CAP), which visually read as the bar grinding to
      // a halt near the end instead of finishing. A flat step keeps the fill
      // moving at the same visible speed the whole way to CAP.
      const STEP = 0.6;
      percent = Math.min(CAP, percent + STEP);
      barEl.style.width = `${percent}%`;
    }, 150);
  }

  function finish() {
    clearInterval(timer);
    timer = null;
    barEl.style.width = "100%";
  }

  return { start, finish };
}

const roomProgressFill = createSimulatedFill(progressBarFill);
const houseProgressFill = createSimulatedFill(houseProgressBarFill);

let selectedFile = null;
let currentStageIndex = -1;
let revealedConceptTiers = new Set();
let materialsWereRunning = false;
let stallTimer = null;

/* ---------- File selection ---------- */

function setSelectedFile(file) {
  if (!file) return;
  selectedFile = file;

  const reader = new FileReader();
  reader.onload = () => {
    previewImg.src = reader.result;
    previewFilename.textContent = file.name;
    dropzoneEmpty.hidden = true;
    dropzonePreview.hidden = false;
    generateBtn.disabled = false;
  };
  reader.readAsDataURL(file);
}

function clearSelectedFile() {
  selectedFile = null;
  fileInput.value = "";
  generateBtn.disabled = true;
  dropzoneEmpty.hidden = false;
  dropzonePreview.hidden = true;
}

fileInput.addEventListener("change", () => setSelectedFile(fileInput.files[0]));

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
  if (!selectedFile) return;

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
  formData.append("style_notes", stylePromptInput.value.trim());
  formData.append("city", city);

  let projectId;
  try {
    const res = await fetch("/api/projects", { method: "POST", body: formData });
    if (res.status === 401) {
      savePendingGeneration("room", {
        fileName: selectedFile.name,
        fileType: selectedFile.type,
        fileDataUrl: previewImg.src,
        styleNotes: stylePromptInput.value,
        city: cityInput.value,
      });
      window.location.href = "/login";
      return;
    }
    if (!res.ok) throw new Error(await res.text());
    ({ project_id: projectId } = await res.json());
  } catch (err) {
    showError(err.message);
    return;
  }

  pollProject(projectId);
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
  let data;
  try {
    const res = await fetch(`/api/projects/${projectId}`);
    data = await res.json();
  } catch (err) {
    showError("Lost connection while checking progress. " + err.message);
    return;
  }

  if (data.status === "failed") {
    showError(data.error || "Unknown error during generation.");
    return;
  }

  applyPollUpdate(data);

  if (data.status === "done") {
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
    const [roomRes, houseRes] = await Promise.all([fetch("/api/projects"), fetch("/api/house-projects")]);
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

function renderResults(data) {
  roomDescriptionEl.textContent = data.room_description ? `"${data.room_description}"` : "";
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
        <a class="download-btn" href="${url}" download="${tier.key}.png" aria-label="Download ${tier.label} image" title="Download image">
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
  clearSelectedFile();
  stylePromptInput.value = "";
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

// Generate Concept must stay disabled until a plot photo AND both dimensions
// are present - a photo alone isn't enough for the render prompt to carry
// real length/width context.
function updateHouseGenerateBtnState() {
  houseGenerateBtn.disabled = !(
    houseSelectedFile &&
    houseLengthInput.value.trim() &&
    houseWidthInput.value.trim()
  );
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
  if (!houseSelectedFile) return;

  houseGenerateBtn.disabled = true; // belt-and-suspenders against double-submit

  showHouseState("progress");
  startHouseProgress();

  const formData = new FormData();
  formData.append("file", houseSelectedFile);
  if (houseLengthInput.value) formData.append("length", houseLengthInput.value);
  if (houseWidthInput.value) formData.append("width", houseWidthInput.value);
  formData.append("unit", houseUnitInput.value);
  formData.append("prompt", housePromptInput.value.trim());

  let houseProjectId;
  try {
    const res = await fetch("/api/house-projects", { method: "POST", body: formData });
    if (res.status === 401) {
      savePendingGeneration("house", {
        fileName: houseSelectedFile.name,
        fileType: houseSelectedFile.type,
        fileDataUrl: housePreviewImg.src,
        length: houseLengthInput.value,
        width: houseWidthInput.value,
        unit: houseUnitInput.value,
        prompt: housePromptInput.value,
      });
      window.location.href = "/login";
      return;
    }
    if (!res.ok) throw new Error(await res.text());
    ({ house_project_id: houseProjectId } = await res.json());
  } catch (err) {
    showHouseError(err.message);
    return;
  }

  pollHouseProject(houseProjectId);
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
  { label: "Drawing your floor plan…" },
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
  let data;
  try {
    const res = await fetch(`/api/house-projects/${houseProjectId}`);
    data = await res.json();
  } catch (err) {
    showHouseError("Lost connection while checking progress. " + err.message);
    return;
  }

  if (data.status === "failed") {
    showHouseError(data.error || "Unknown error during generation.");
    return;
  }

  applyHousePollUpdate(data);

  if (data.status === "done") {
    finishHouseProgress(() => {
      renderHouseResults(data);
      showHouseState("results");
    });
    return;
  }

  setTimeout(() => pollHouseProject(houseProjectId), 3000);
}

/* ---------- Results ---------- */

const HOUSE_RESULT_TIERS = [
  { key: "plot", label: "Original Plot", desc: "Your uploaded plot photo." },
  { key: "render", label: "Concept Render", desc: "AI-generated exterior/interior concept." },
];

function renderHouseResults(data) {
  plotDescriptionEl.textContent = data.plot_description ? `"${data.plot_description}"` : "";
  houseResultsGrid.innerHTML = "";

  for (const tier of HOUSE_RESULT_TIERS) {
    const url = data.images[tier.key];
    if (!url) continue;

    const card = document.createElement("div");
    card.className = "result-card";
    card.innerHTML = `
      <div class="img-wrap">
        <img src="${url}" alt="${tier.label}" loading="lazy" />
        <a class="download-btn" href="${url}" download="${tier.key}.png" aria-label="Download ${tier.label}" title="Download image">
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
  // reserved for a still-inert future paid vendor. Unlike that hypothetical
  // AI-generated floor plan, these ARE dimensionally accurate: the room
  // rectangles are computed directly from the stated plot dimensions, not
  // guessed by an image model - hence the different, non-disclaiming label.
  if (data.blueprint_status === "done" && data.blueprint_urls && data.blueprint_urls.length) {
    data.blueprint_urls.forEach((url, i) => {
      const floorNumber = i + 1;
      const label = `Floor ${floorNumber} Layout`;
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
      `;
      card.addEventListener("click", () => openLightbox(url, label));
      card.querySelector(".download-btn").addEventListener("click", (e) => e.stopPropagation());
      houseResultsGrid.appendChild(card);
    });
  }

  // Floor-plan generation via a real, PAID vendor is deferred (no vendor
  // wired in yet - see app/providers/idealhouse.py) - deliberately show NO
  // floor-plan card at all rather than a broken/empty placeholder. Once a
  // vendor is wired in, this is where a floor-plan card should be added,
  // labeled "Concept Layout - not a precise blueprint" (no image-gen API
  // guarantees dimensional accuracy) - NOT the same as the blueprint cards
  // above, which already are dimensionally accurate.
  if (data.floor_plan_status === "done" && data.images.floor_plan) {
    const card = document.createElement("div");
    card.className = "result-card";
    card.innerHTML = `
      <div class="img-wrap">
        <img src="${data.images.floor_plan}" alt="Concept Layout" loading="lazy" />
        <a class="download-btn" href="${data.images.floor_plan}" download="floor_plan.png" aria-label="Download floor plan" title="Download image">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3v12m0 0l-4-4m4 4l4-4" stroke-linecap="round" stroke-linejoin="round"/><path d="M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </a>
      </div>
      <div class="caption">
        <p class="tier-name">Concept Layout</p>
        <p class="tier-desc">Not a precise blueprint - no image-gen API guarantees dimensional accuracy.</p>
      </div>
    `;
    card.addEventListener("click", () => openLightbox(data.images.floor_plan, "Concept Layout"));
    card.querySelector(".download-btn").addEventListener("click", (e) => e.stopPropagation());
    houseResultsGrid.appendChild(card);
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
  clearHouseSelectedFile();
  houseLengthInput.value = "";
  houseWidthInput.value = "";
  housePromptInput.value = "";
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
