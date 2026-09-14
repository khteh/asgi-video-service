// Browser-local tracking of "the job the user is currently waiting on".
//
// The backend has no auth/session concept, so there's no server-side way
// to know "this browser already has a job in flight" - the API/jobs list
// is global. Instead we track it client-side: the job detail page records
// its own job id here whenever that job is still pending/generating, and
// clears it once the job reaches a terminal state (completed, or actually
// failed after the backend has exhausted all its retries - an interim
// retry never surfaces as anything but "generating" externally, so it
// never gets treated as terminal here either). The index page reads this
// on load to decide whether to show the submission form or an in-progress
// panel, which is what keeps a second submission from being made while
// one is already running.
const CURRENT_JOB_KEY = "stemVideoCurrentJobId";

function getCurrentJobId() {
  try {
    return localStorage.getItem(CURRENT_JOB_KEY);
  } catch (err) {
    return null;
  }
}

function setCurrentJobId(jobId) {
  try {
    localStorage.setItem(CURRENT_JOB_KEY, jobId);
  } catch (err) {
    // Storage unavailable (private browsing, disabled, etc.) - the
    // in-progress panel just won't persist across page loads; not fatal.
  }
}

function clearCurrentJobId(jobId) {
  try {
    // Only clear if it's still the same job - avoids a stale detail page
    // clobbering a different job that was started after it.
    if (!jobId || localStorage.getItem(CURRENT_JOB_KEY) === jobId) {
      localStorage.removeItem(CURRENT_JOB_KEY);
    }
  } catch (err) {
    // Ignore - see setCurrentJobId.
  }
}

document.addEventListener("DOMContentLoaded", () => {
  // Fill the question textarea from an example button.
  document.querySelectorAll(".example-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const field = document.getElementById("query");
      if (field) {
        field.value = btn.dataset.query;
        field.focus();
      }
    });
  });

  initIndexPage();
  initJobDetailPage();
});

// --- Index page: submission form vs. "current job in progress" panel ---

function initIndexPage() {
  const submitForm = document.getElementById("submit-form");
  const currentJobPanel = document.getElementById("current-job-panel");
  if (!submitForm || !currentJobPanel) return;

  const examplesSection = document.getElementById("examples-section");
  const badge = document.getElementById("current-job-badge");
  const fill = document.getElementById("current-job-progress-fill");
  const stageLabel = document.getElementById("current-job-stage");
  const link = document.getElementById("current-job-link");

  const showCurrentJob = (job, jobId) => {
    submitForm.hidden = true;
    if (examplesSection) examplesSection.hidden = true;
    currentJobPanel.hidden = false;
    if (badge) {
      badge.textContent = job.status;
      badge.className = `status-badge status-${job.status}`;
    }
    if (fill) fill.style.width = `${Math.round(job.progress * 100)}%`;
    if (stageLabel) stageLabel.textContent = job.stage;
    if (link) link.href = `/jobs/${jobId}`;
  };

  const showForm = () => {
    submitForm.hidden = false;
    if (examplesSection) examplesSection.hidden = false;
    currentJobPanel.hidden = true;
  };

  const checkCurrentJob = async (jobId) => {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      if (!res.ok) {
        // Job no longer exists (or another error) - nothing to block on.
        clearCurrentJobId(jobId);
        showForm();
        return;
      }
      const job = await res.json();
      if (job.status === "completed" || job.status === "failed") {
        clearCurrentJobId(jobId);
        showForm();
        return;
      }
      showCurrentJob(job, jobId);
      setTimeout(() => checkCurrentJob(jobId), 3000);
    } catch (err) {
      console.error("Failed to check current job status", err);
      // Leave whatever's currently shown as-is; try again shortly.
      setTimeout(() => checkCurrentJob(jobId), 3000);
    }
  };

  const currentJobId = getCurrentJobId();
  if (currentJobId) {
    checkCurrentJob(currentJobId);
  }
}

// --- Job detail page: poll status, and track/clear the current-job id ---

function initJobDetailPage() {
  const panel = document.querySelector(".job-detail");
  if (!panel) return;

  const jobId = panel.dataset.jobId;
  const initialStatus = panel.dataset.status;

  if (initialStatus === "completed" || initialStatus === "failed") {
    clearCurrentJobId(jobId);
    return;
  }

  // Still in progress (including an interim retry, which is only ever
  // visible externally as "generating") - remember it so the index page
  // knows to block new submissions until this one is done.
  setCurrentJobId(jobId);

  const badge = document.getElementById("status-badge");
  const fill = document.getElementById("progress-fill");
  const stageLabel = document.getElementById("stage-label");

  const poll = async () => {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      if (res.ok) {
        const job = await res.json();
        if (badge) {
          badge.textContent = job.status;
          badge.className = `status-badge status-${job.status}`;
        }
        if (fill) fill.style.width = `${Math.round(job.progress * 100)}%`;
        if (stageLabel) stageLabel.textContent = job.stage;
        if (job.status === "completed" || job.status === "failed") {
          clearCurrentJobId(jobId);
          window.location.reload();
          return;
        }
      }
    } catch (err) {
      console.error("Failed to poll job status", err);
    }
    setTimeout(poll, 2000);
  };

  setTimeout(poll, 2000);
}
