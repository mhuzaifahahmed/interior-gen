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
const progressBarFill = document.getElementById("progress-bar-fill");

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

// Deliberate choice (revised from an earlier, stricter version of this list): these
// ARE staged/specific per-tier messages, not literal real-time backend status - the
// backend doesn't report progress at this granularity, and an earlier version of this
// project treated that as a reason to keep messages vague/generic on principle. Product
// decision since then: the ~1 minute wait feels more engaging with specific, varied
// messages than with honest-but-vague ones, even knowing they're not strictly telemetry.
// Shown in a shuffled order each run (see startProgressMessages) rather than this fixed
// sequence, so it doesn't read as an obviously scripted, always-identical script.
const PROGRESS_MESSAGES = [
  "Analyzing your room's layout…",
  "Generating your Premium image…",
  "Fetching designer material options…",
  "Generating your Mid-Range image…",
  "Fetching real-time price data…",
  "Generating your Economical image…",
  "Balancing lighting and textures…",
  "Finalizing your three redesigns…",
  "Almost ready — just a little longer…",
];

let selectedFile = null;
let progressMessageTimer = null;
let messageQueue = [];

let progressBarTimer = null;
let progressBarPercent = 0;

// Never quite reaches 100% on its own - only a real "done" response snaps it there.
// Classic loading-bar trick so it doesn't look "stuck" if generation runs long.
const PROGRESS_BAR_CAP = 92;

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

/* ---------- Progress messaging ---------- */
/* Cycles through PROGRESS_MESSAGES with a fade transition between each, so a ~1 minute
   wait feels animated/engaged rather than a single frozen sentence. Each message holds
   for ~9-10 seconds (randomized, not a fixed beat) and the order is reshuffled every run
   instead of always playing in the same sequence - both are there so the cycle doesn't
   read as an obviously premade script. If generation runs long enough to exhaust one
   shuffle, it reshuffles and keeps going rather than freezing on the last line. */

function shuffle(array) {
  const arr = array.slice();
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

function nextMessage() {
  if (messageQueue.length === 0) {
    const last = progressMessageEl.textContent;
    messageQueue = shuffle(PROGRESS_MESSAGES);
    if (messageQueue[0] === last && messageQueue.length > 1) {
      [messageQueue[0], messageQueue[1]] = [messageQueue[1], messageQueue[0]];
    }
  }
  return messageQueue.shift();
}

function scheduleNextMessage() {
  const holdMs = 9000 + Math.random() * 1000; // ~9-10s, not a metronome
  progressMessageTimer = setTimeout(() => {
    progressMessageEl.classList.add("is-changing");
    setTimeout(() => {
      progressMessageEl.textContent = nextMessage();
      progressMessageEl.classList.remove("is-changing");
      scheduleNextMessage();
    }, 250);
  }, holdMs);
}

function startProgressMessages() {
  messageQueue = shuffle(PROGRESS_MESSAGES);
  progressMessageEl.textContent = messageQueue.shift();
  scheduleNextMessage();
  startProgressBar();
}

function stopProgressMessages() {
  clearTimeout(progressMessageTimer);
  progressMessageTimer = null;
  progressMessageEl.classList.remove("is-changing");
  stopProgressBar();
}

/* ---------- Progress bar ---------- */
/* Fills toward PROGRESS_BAR_CAP over roughly a minute, in small irregular steps/ticks
   (both the step size and the delay between ticks are randomized) rather than a smooth
   linear animation, so it reads as real progress rather than an obviously fake timer.
   Steps shrink as the bar approaches the cap (an easing curve), which is what makes ~65s
   land near the cap without a hard-coded frame-by-frame schedule. */

function tickProgressBar() {
  const remaining = PROGRESS_BAR_CAP - progressBarPercent;
  const step = Math.max(0.15, remaining * (0.012 + Math.random() * 0.02));
  progressBarPercent = Math.min(PROGRESS_BAR_CAP, progressBarPercent + step);
  progressBarFill.style.width = `${progressBarPercent}%`;

  const tickMs = 500 + Math.random() * 700; // ~0.5-1.2s, irregular
  progressBarTimer = setTimeout(tickProgressBar, tickMs);
}

function startProgressBar() {
  progressBarPercent = 0;
  progressBarFill.style.width = "0%";
  tickProgressBar();
}

function stopProgressBar() {
  clearTimeout(progressBarTimer);
  progressBarTimer = null;
}

function finishProgressBar() {
  stopProgressBar();
  progressBarFill.style.width = "100%";
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
    finishProgressBar();
    stopProgressMessages();
    // Brief pause so the bar's jump to 100% is actually visible before the
    // progress card gets hidden in favor of the results view.
    setTimeout(() => {
      renderResults(data);
      showState("results");
    }, 300);
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
      <div class="img-wrap"><img src="${url}" alt="${tier.label} redesign" loading="lazy" /></div>
      <div class="caption">
        <p class="tier-name">${tier.label}</p>
        <p class="tier-desc">${tier.desc}</p>
      </div>
    `;
    card.addEventListener("click", () => openLightbox(url, tier.label));
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
