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

/* ---------- "Build a House" tab DOM refs ---------- */

const tabBtnHome = document.getElementById("tab-btn-home");
const tabBtnRoom = document.getElementById("tab-btn-room");
const tabBtnHouse = document.getElementById("tab-btn-house");
const homeTabPanel = document.getElementById("home-tab-panel");
const roomTabPanel = document.getElementById("room-tab-panel");
const houseTabPanel = document.getElementById("house-tab-panel");
const homeCtaBtn = document.getElementById("home-cta-btn");

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

function switchTab(tab) {
  const isHome = tab === "home";
  const isHouse = tab === "house";
  const isRoom = !isHome && !isHouse;
  homeTabPanel.hidden = !isHome;
  roomTabPanel.hidden = !isRoom;
  houseTabPanel.hidden = !isHouse;
  tabBtnHome.classList.toggle("is-active", isHome);
  tabBtnRoom.classList.toggle("is-active", isRoom);
  tabBtnHouse.classList.toggle("is-active", isHouse);
}

tabBtnHome.addEventListener("click", () => switchTab("home"));
tabBtnRoom.addEventListener("click", () => switchTab("room"));
tabBtnHouse.addEventListener("click", () => switchTab("house"));

homeCtaBtn.addEventListener("click", () => switchTab("room"));

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
const STAGES = [
  { label: "Analyzing your room…" },
  { label: "Generating concepts…" },
  { label: "Preparing your cost estimates…" },
  { label: "Finalizing your three concepts…" },
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

function setStepperState(activeIndex, allDone) {
  progressStepperEl.querySelectorAll(".stage-item").forEach((el, i) => {
    el.classList.toggle("is-done", allDone || i < activeIndex);
    el.classList.toggle("is-active", !allDone && i === activeIndex);
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

/* Derives which of the 4 stages we're in from REAL data already present on
   every poll response (app/main.py's get_project) - not a timer. Each tier's
   image key is committed to the DB (and so returned here) the moment that
   tier finishes generating, well before the overall project is "done". */
function deriveStageIndex(data) {
  const tierUrls = CONCEPT_PREVIEW_TIERS.map((t) => data.images && data.images[t.key]);
  const allImagesDone = tierUrls.every(Boolean);
  const anyImageDone = tierUrls.some(Boolean);
  const materialsSettled = data.materials_status === "done" || data.materials_status === "skipped";

  if (data.status === "done") return STAGES.length - 1;
  if (allImagesDone && materialsSettled) return 3; // Finalizing
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

  if (data.materials_status === "running") {
    materialsWereRunning = true;
  } else if (materialsWereRunning && data.materials_status === "done") {
    materialsWereRunning = false;
    updateSubLabel("Cost estimates ready");
  }

  const derived = deriveStageIndex(data);
  if (derived > currentStageIndex) {
    currentStageIndex = derived;
    setStageLabel(Math.min(currentStageIndex, STAGES.length - 1));
    setStepperState(currentStageIndex, false);
    progressBarFill.style.width = `${currentStageIndex * 25}%`;
  }
}

function startProgressMessages() {
  renderStageStepper();
  renderConceptPreviewShell();
  revealedConceptTiers = new Set();
  materialsWereRunning = false;
  currentStageIndex = 0;

  progressBarFill.style.width = "0%";
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
  progressBarFill.style.width = "100%";
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

materialsModalClose.addEventListener("click", closeMaterialsModal);
materialsModalOverlay.addEventListener("click", (e) => {
  if (e.target === materialsModalOverlay) closeMaterialsModal();
});

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  lightbox.hidden = true;
  closeMaterialsModal();
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
   scaled down to this feature's actual shape: 3 stages, no per-item reveals,
   since there's exactly one deliverable render (no 3-tier concept previews)
   and floor-plan generation is currently a stub (see app/providers/
   idealhouse.py) so there's nothing to preview mid-flight there either. */

const HOUSE_STAGES = [
  { label: "Analyzing your plot…" },
  { label: "Generating your concept render…" },
  { label: "Finalizing…" },
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

function setHouseStepperState(activeIndex, allDone) {
  houseProgressStepperEl.querySelectorAll(".stage-item").forEach((el, i) => {
    el.classList.toggle("is-done", allDone || i < activeIndex);
    el.classList.toggle("is-active", !allDone && i === activeIndex);
  });
}

function setHouseStageLabel(index) {
  houseProgressMessageEl.classList.add("is-changing");
  setTimeout(() => {
    houseProgressMessageEl.textContent = HOUSE_STAGES[index].label;
    houseProgressMessageEl.classList.remove("is-changing");
  }, 220);
}

/* Derives which of the 3 stages we're in from REAL data on every poll
   response (app/main.py's get_house_project), not a timer - plot_description
   and images.render are both committed to the DB (and so returned here) as
   soon as each step actually finishes, well before the overall project is
   "done". */
function deriveHouseStageIndex(data) {
  if (data.status === "done" || (data.images && data.images.render)) return HOUSE_STAGES.length - 1;
  if (data.plot_description || data.floor_plan_status !== "idle") return 1;
  return 0;
}

function applyHousePollUpdate(data) {
  const derived = deriveHouseStageIndex(data);
  if (derived > houseCurrentStageIndex) {
    houseCurrentStageIndex = derived;
    setHouseStageLabel(Math.min(houseCurrentStageIndex, HOUSE_STAGES.length - 1));
    setHouseStepperState(houseCurrentStageIndex, false);
    houseProgressBarFill.style.width = `${(houseCurrentStageIndex / HOUSE_STAGES.length) * 100}%`;
  }
}

function startHouseProgress() {
  renderHouseStageStepper();
  houseCurrentStageIndex = 0;

  houseProgressBarFill.style.width = "0%";
  setHouseStepperState(0, false);
  houseProgressMessageEl.textContent = HOUSE_STAGES[0].label;
  houseProgressSubtitleEl.textContent = "";
}

function stopHouseProgress() {
  houseProgressMessageEl.classList.remove("is-changing");
}

function finishHouseProgress(onDone) {
  setHouseStepperState(HOUSE_STAGES.length, true);
  houseProgressBarFill.style.width = "100%";
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

  // Floor-plan generation is deferred (no vendor wired in yet - see
  // app/providers/idealhouse.py) - deliberately show NO floor-plan card at all
  // rather than a broken/empty placeholder. Once a vendor is wired in, this is
  // where a floor-plan card should be added, labeled "Concept Layout - not a
  // precise blueprint" (no image-gen API guarantees dimensional accuracy).
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
