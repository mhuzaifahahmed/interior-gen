const form = document.getElementById("upload-form");
const fileInput = document.getElementById("file-input");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");

const LABELS = {
  original: "Original",
  economical: "Economical",
  mid: "Mid-Range",
  premium: "Premium",
};

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = fileInput.files[0];
  if (!file) return;

  resultsEl.innerHTML = "";
  statusEl.textContent = "Uploading...";

  const formData = new FormData();
  formData.append("file", file);

  let projectId;
  try {
    const res = await fetch("/api/projects", { method: "POST", body: formData });
    if (!res.ok) throw new Error(await res.text());
    ({ project_id: projectId } = await res.json());
  } catch (err) {
    statusEl.textContent = "Upload failed: " + err.message;
    return;
  }

  statusEl.textContent = "Generating tiers... this can take up to a couple of minutes.";
  pollProject(projectId);
});

async function pollProject(projectId) {
  const res = await fetch(`/api/projects/${projectId}`);
  const data = await res.json();

  if (data.status === "failed") {
    statusEl.textContent = "Generation failed: " + (data.error || "unknown error");
    return;
  }

  if (data.status === "done") {
    statusEl.textContent = "Done.";
    renderResults(data.images);
    return;
  }

  statusEl.textContent = `Status: ${data.status}...`;
  setTimeout(() => pollProject(projectId), 3000);
}

function renderResults(images) {
  resultsEl.innerHTML = "";
  for (const key of ["original", "economical", "mid", "premium"]) {
    const url = images[key];
    if (!url) continue;
    const figure = document.createElement("figure");
    figure.innerHTML = `<img src="${url}" alt="${LABELS[key]}" /><figcaption>${LABELS[key]}</figcaption>`;
    resultsEl.appendChild(figure);
  }
}
