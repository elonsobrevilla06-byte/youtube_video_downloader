const form = document.getElementById("download-form");
const submitBtn = document.getElementById("submit-btn");
const statusPanel = document.getElementById("status-panel");
const statusTitle = document.getElementById("status-title");
const progressFill = document.getElementById("progress-fill");
const statusDetail = document.getElementById("status-detail");
const downloadLink = document.getElementById("download-link");
const errorPanel = document.getElementById("error-panel");
const downloadTypeRadios = document.querySelectorAll('input[name="download_type"]');
const qualityField = document.getElementById("quality-field");
const urlField = document.getElementById("url");
const platformBadge = document.getElementById("platform-badge");

function toggleFormatFields() {
  const isMp3 = document.querySelector('input[name="download_type"]:checked').value === "mp3";
  qualityField.classList.toggle("hidden", isMp3);
}

downloadTypeRadios.forEach((radio) => radio.addEventListener("change", toggleFormatFields));
toggleFormatFields(); // set correct initial state on page load

// Client-side platform detection is just for a nice badge next to the
// input; the server independently validates the URL, so this never
// needs to be exhaustive or airtight.
const PLATFORM_PATTERNS = [
  { name: "YouTube", re: /(^|\.)youtube\.com$|(^|\.)youtu\.be$/i },
  { name: "Instagram", re: /(^|\.)instagram\.com$/i },
  { name: "Facebook", re: /(^|\.)facebook\.com$|(^|\.)fb\.watch$/i },
];

function updatePlatformBadge() {
  const value = urlField.value.trim();
  if (!value) {
    platformBadge.classList.add("hidden");
    return;
  }
  let host;
  try {
    host = new URL(value).hostname;
  } catch {
    platformBadge.classList.add("hidden");
    return;
  }
  const match = PLATFORM_PATTERNS.find((p) => p.re.test(host));
  if (match) {
    platformBadge.textContent = `Detected: ${match.name}`;
    platformBadge.classList.remove("hidden");
  } else {
    platformBadge.textContent = "Unsupported link — use YouTube, Instagram, or Facebook";
    platformBadge.classList.remove("hidden");
  }
}

urlField.addEventListener("input", updatePlatformBadge);

let pollTimer = null;

function resetUI() {
  errorPanel.classList.add("hidden");
  errorPanel.textContent = "";
  downloadLink.classList.add("hidden");
  statusPanel.classList.remove("hidden");
  progressFill.style.width = "0%";
  statusDetail.textContent = "";
}

function showError(message) {
  clearInterval(pollTimer);
  errorPanel.textContent = message;
  errorPanel.classList.remove("hidden");
  statusPanel.classList.add("hidden");
  submitBtn.disabled = false;
  submitBtn.textContent = "Download";
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  resetUI();

  submitBtn.disabled = true;
  submitBtn.textContent = "Starting...";

  const payload = {
    url: document.getElementById("url").value.trim(),
    quality: document.getElementById("quality").value,
    audio_only: document.querySelector('input[name="download_type"]:checked').value === "mp3",
    playlist: document.getElementById("playlist").checked,
  };

  try {
    const res = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();

    if (!res.ok) {
      showError(data.error || "Failed to start download.");
      return;
    }

    statusTitle.textContent = "Preparing download...";
    pollStatus(data.job_id);
  } catch (err) {
    showError("Network error: " + err.message);
  }
});

function pollStatus(jobId) {
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/status/${jobId}`);
      const job = await res.json();

      if (!res.ok) {
        showError(job.error || "Lost track of the job.");
        return;
      }

      if (job.title) {
        statusTitle.textContent = job.title;
      }

      if (job.status === "downloading") {
        progressFill.style.width = `${job.percent || 0}%`;
        statusDetail.textContent = `Downloading... ${job.percent?.toFixed(1) || 0}% | speed: ${job.speed || "N/A"} | ETA: ${job.eta || "N/A"}`;
      } else if (job.status === "processing") {
        progressFill.style.width = "100%";
        statusDetail.textContent = "Processing (merging/converting)...";
      } else if (job.status === "done") {
        clearInterval(pollTimer);
        progressFill.style.width = "100%";
        statusDetail.textContent = "Done! Click below to save the file (available once).";

        downloadLink.href = `/api/file/${jobId}`;
        downloadLink.classList.remove("hidden");
        downloadLink.addEventListener("click", () => {
          setTimeout(() => {
            downloadLink.classList.add("hidden");
            statusDetail.textContent = "Saved.";
          }, 500);
        }, { once: true });

        submitBtn.disabled = false;
        submitBtn.textContent = "Download";
      } else if (job.status === "error") {
        showError(job.error || "Download failed.");
      } else {
        statusDetail.textContent = "Starting...";
      }
    } catch (err) {
      showError("Network error while polling status: " + err.message);
    }
  }, 1000);
}