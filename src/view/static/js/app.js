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

  // Poll job status on the job detail page until it's done.
  const panel = document.querySelector(".job-detail");
  if (!panel) return;

  const jobId = panel.dataset.jobId;
  const initialStatus = panel.dataset.status;
  if (initialStatus === "completed" || initialStatus === "failed") return;

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
});
