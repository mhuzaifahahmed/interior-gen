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

const progressCard = document.getElementById("progress-card");
const progressMessageEl = document.getElementById("progress-message");
const progressSubtitleEl = document.getElementById("progress-subtitle");
const progressBarFill = document.getElementById("progress-bar-fill");
const progressChecklistEl = document.getElementById("progress-checklist");

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

const TIERS = [
  { key: "original", label: "Original", desc: "Your uploaded room." },
  { key: "premium", label: "Premium", desc: "Marble, brass, and designer lighting." },
  { key: "mid", label: "Mid-Range", desc: "Warm woods and upgraded fixtures." },
  { key: "economical", label: "Economical", desc: "Fresh paint and clean practical finishes." },
];

// Deliberate choice (same reasoning as the rest of this staged timeline): these ARE
// scripted per-stage messages/subtitles, not literal real-time backend status - the
// backend doesn't report progress at this granularity. A ~1 minute wait feels far more
// reassuring with a specific, narrated story (room analysis -> architectural detection
// -> design brief -> per-tier generation -> cost estimation -> finalizing) than with
// vague filler, even knowing it's not strictly telemetry. Unlike an earlier version of
// this list, these are NOT shuffled - they read as one coherent narrative in a fixed
// order, so shuffling would break the story rather than making it feel more real.
const CHECKLIST_ITEMS = [
  "Image uploaded",
  "Room analysis complete",
  "Architectural elements identified",
  "Design brief understood",
  "Economical concept generated",
  "Budget estimation complete",
  "Mid-Range concept generated",
  "Premium concept generated",
  "Material estimates prepared",
  "Rendering final presentation",
];

// `start` is the second (within the ~60s target timeline) this stage begins.
// `checklistDone` is how many CHECKLIST_ITEMS should be checked once this stage is
// showing. `activeIndex` (only set on the final stage) marks the item that should
// show an in-progress spinner rather than a checkmark, until the backend actually
// reports completion.
const STAGES = [
  {
    start: 0,
    status: "Uploading your image securely…",
    subtitles: ["Preparing your workspace", "Validating uploaded image", "Initializing project"],
    checklistDone: 0,
  },
  {
    start: 4,
    status: "Analyzing room layout and architectural features…",
    subtitles: ["Detecting walls", "Understanding room geometry", "Mapping architectural structure"],
    checklistDone: 1,
  },
  {
    start: 8,
    status: "Identifying walls, flooring, windows, lighting, and fixed elements…",
    subtitles: ["Detecting permanent fixtures", "Preserving architectural details", "Mapping existing materials"],
    checklistDone: 2,
  },
  {
    start: 13,
    status: "Understanding your design request and preferred style…",
    subtitles: ["Interpreting renovation goals", "Matching interior style", "Planning design direction"],
    checklistDone: 3,
  },
  {
    start: 18,
    status: "Creating the Economical concept using cost-effective materials…",
    subtitles: ["Selecting affordable finishes", "Optimizing value", "Building practical renovation concept"],
    checklistDone: 4,
  },
  {
    start: 25,
    status: "Estimating finishes and approximate material costs…",
    subtitles: ["Comparing material options", "Calculating renovation estimates", "Preparing budget analysis"],
    checklistDone: 5,
  },
  {
    start: 32,
    status: "Designing the Mid-Range concept with upgraded materials…",
    subtitles: ["Selecting premium finishes", "Improving comfort and aesthetics", "Balancing design and cost"],
    checklistDone: 6,
  },
  {
    start: 39,
    status: "Balancing aesthetics, durability, and budget…",
    subtitles: ["Refining material combinations", "Validating design consistency", "Optimizing renovation plan"],
    checklistDone: 6,
  },
  {
    start: 46,
    status: "Crafting the Premium concept with luxury finishes and custom details…",
    subtitles: ["Applying premium materials", "Enhancing lighting and textures", "Creating luxury interior concept"],
    checklistDone: 7,
  },
  {
    start: 53,
    status: "Preparing material recommendations and rough cost estimates…",
    subtitles: ["Comparing renovation options", "Finalizing recommendations", "Organizing project summary"],
    checklistDone: 8,
  },
  {
    start: 57,
    status: "Finalizing your three interior concepts…",
    subtitles: ["Rendering high-resolution visuals", "Performing final quality checks", "Preparing presentation"],
    checklistDone: 8,
    activeIndex: 9,
  },
];

const TOTAL_DURATION_MS = 60000;
// The bar never reaches 100% on its own - only a real "done" response snaps it there.
// Classic loading-bar trick so it doesn't look "stuck" if generation runs longer than
// the ~60s reference timeline (real generation time varies with provider latency).
const PROGRESS_BAR_CAP = 97;

let selectedFile = null;
let progressStartTime = null;
let progressRafId = null;
let currentStageIndex = -1;
let currentSubtitleIndex = -1;
let subtitleTimer = null;

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

/* ---------- Submit ---------- */

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!selectedFile) return;

  showState("progress");
  startProgressMessages();

  const formData = new FormData();
  formData.append("file", selectedFile);
  formData.append("style_notes", stylePromptInput.value.trim());

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
/* Drives the main status, rotating subtitle, progress bar, and checklist off a single
   real-time clock (progressStartTime) mapped onto the STAGES timeline above, rather
   than independent timers - this is what keeps all four pieces moving in sync and
   avoids drift between them. The bar fill is time-based and linear against the ~60s
   target (not randomized), per the requirement that it track real elapsed time rather
   than an arbitrary animation; PROGRESS_BAR_CAP still guards against generation
   running long. If the backend finishes before the reference timeline catches up,
   fastForwardToCompletion() visibly steps through the remaining stages instead of
   snapping straight to the end. */

function renderChecklistShell() {
  progressChecklistEl.innerHTML = CHECKLIST_ITEMS.map(
    (label, i) => `
      <li class="checklist-item" data-index="${i}">
        <span class="check-icon"></span>
        <span class="check-label">${label}</span>
      </li>
    `
  ).join("");
}

function setChecklistState(doneCount, activeIndex) {
  const items = progressChecklistEl.querySelectorAll(".checklist-item");
  items.forEach((el, i) => {
    el.classList.toggle("is-done", i < doneCount);
    el.classList.toggle("is-active", i === activeIndex && i >= doneCount);
  });
}

function markChecklistComplete() {
  progressChecklistEl.querySelectorAll(".checklist-item").forEach((el) => {
    el.classList.remove("is-active");
    el.classList.add("is-done");
  });
}

function scheduleSubtitleRotation(subtitles) {
  clearTimeout(subtitleTimer);
  currentSubtitleIndex = 0;
  progressSubtitleEl.textContent = subtitles[0];

  const advance = () => {
    subtitleTimer = setTimeout(() => {
      currentSubtitleIndex = (currentSubtitleIndex + 1) % subtitles.length;
      progressSubtitleEl.classList.add("is-changing");
      setTimeout(() => {
        progressSubtitleEl.textContent = subtitles[currentSubtitleIndex];
        progressSubtitleEl.classList.remove("is-changing");
        advance();
      }, 180);
    }, 2200);
  };
  advance();
}

function applyStage(index) {
  if (index === currentStageIndex) return;
  currentStageIndex = index;
  const stage = STAGES[index];

  progressMessageEl.classList.add("is-changing");
  setTimeout(() => {
    progressMessageEl.textContent = stage.status;
    progressMessageEl.classList.remove("is-changing");
  }, 220);

  setChecklistState(stage.checklistDone, stage.activeIndex ?? -1);
  scheduleSubtitleRotation(stage.subtitles);
}

function stageIndexForElapsedSeconds(elapsedSeconds) {
  let index = 0;
  for (let i = 0; i < STAGES.length; i++) {
    if (elapsedSeconds >= STAGES[i].start) index = i;
  }
  return index;
}

function tickProgress() {
  const elapsedMs = Date.now() - progressStartTime;
  applyStage(stageIndexForElapsedSeconds(elapsedMs / 1000));

  const pct = Math.min((elapsedMs / TOTAL_DURATION_MS) * 100, PROGRESS_BAR_CAP);
  progressBarFill.style.width = `${pct}%`;

  progressRafId = requestAnimationFrame(tickProgress);
}

function startProgressMessages() {
  renderChecklistShell();
  currentStageIndex = -1;
  progressBarFill.style.width = "0%";
  progressStartTime = Date.now();
  tickProgress();
}

function stopProgressMessages() {
  cancelAnimationFrame(progressRafId);
  progressRafId = null;
  clearTimeout(subtitleTimer);
  subtitleTimer = null;
  progressMessageEl.classList.remove("is-changing");
  progressSubtitleEl.classList.remove("is-changing");
}

/* If the backend reports "done" before the reference timeline reaches the final
   stage, step through the remaining stages quickly (rather than teleporting the
   checklist/status straight to the end) so the completion still reads as real
   progress, per "intelligently advance... without making the UI appear fake". */
function fastForwardToCompletion(onDone) {
  cancelAnimationFrame(progressRafId);
  progressRafId = null;

  function step() {
    const nextIndex = currentStageIndex + 1;
    if (nextIndex >= STAGES.length) {
      onDone();
      return;
    }
    applyStage(nextIndex);
    const pct = Math.min((STAGES[nextIndex].start / 60) * 100 + 3, PROGRESS_BAR_CAP);
    progressBarFill.style.width = `${pct}%`;
    setTimeout(step, 280);
  }
  step();
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

  if (data.status === "done") {
    const finish = () => {
      markChecklistComplete();
      progressBarFill.style.width = "100%";
      stopProgressMessages();
      // Brief pause so the bar's jump to 100% and the final checkmark are
      // actually visible before the progress card gets hidden in favor of
      // the results view.
      setTimeout(() => {
        renderResults(data);
        showState("results");
      }, 350);
    };

    if (currentStageIndex < STAGES.length - 1) {
      fastForwardToCompletion(finish);
    } else {
      finish();
    }
    return;
  }

  setTimeout(() => pollProject(projectId), 3000);
}

/* ---------- Results ---------- */

function renderResults(data) {
  roomDescriptionEl.textContent = data.room_description ? `"${data.room_description}"` : "";
  resultsGrid.innerHTML = "";

  for (const tier of TIERS) {
    const url = data.images[tier.key];
    if (!url) continue;

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
    `;
    card.addEventListener("click", () => openLightbox(url, tier.label));
    card.querySelector(".download-btn").addEventListener("click", (e) => e.stopPropagation());
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
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") lightbox.hidden = true;
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
