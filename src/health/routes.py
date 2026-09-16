"""Kubernetes probe endpoints: GET /health/live and GET /health/ready.

Deliberately NOT under api_bp's `/api` prefix and not part of the JSON API
contract app.js/external clients use - these exist purely for the
kubelet's startupProbe/readinessProbe/livenessProbe to hit, at the fixed
paths this app's k8s/deployment.yaml expects. See src/health/checks.py for
what each one actually validates and why liveness deliberately validates
less than readiness.
"""
from __future__ import annotations

from quart import Blueprint, current_app, jsonify, request

health_bp = Blueprint("health", __name__)


@health_bp.get("/health/live")
async def liveness():
    """Liveness: process/event-loop health only - deliberately NOT a
    downstream-dependency check. Kubernetes responds to a failed liveness
    probe by killing and restarting the pod, and restarting this pod
    cannot fix an outage in edge-tts or a real AI video vendor - wiring
    dependency checks into liveness would turn a transient external
    outage into a self-inflicted restart storm across every replica (a
    well-known Kubernetes anti-pattern). What a restart *can* fix is this
    process's own background worker tasks silently dying (JobWorker.
    _run's loop only ever exits via stop()'s explicit cancellation, so if
    every task has died some other way, this process can no longer
    process any job, and a fresh set of tasks from a restart is the right
    remedy) - so that's the one thing this checks.
    """
    worker = current_app.extensions["worker"]
    if not worker.is_running():
        return jsonify({"status": "error", "detail": "worker tasks are not running"}), 503
    return jsonify({"status": "ok"}), 200


@health_bp.get("/health/ready")
async def readiness():
    """Readiness: can this pod currently serve traffic, including the
    downstream dependencies its configured generation mode (simulated/ai
    - see Settings.default_generation_provider) actually needs right now.
    Kubernetes responds to a failed readiness probe by removing the pod
    from Service endpoints WITHOUT restarting it - the right response to
    "an external dependency is temporarily down", unlike liveness above.

    Pass ?fresh=true to bypass the cache described in DependencyHealth and
    force an uncached check - used once by startupProbe in k8s/
    deployment.yaml (which polls this same endpoint while the pod is
    coming up, before Kubernetes ever starts the steady-state readiness/
    liveness probes), and available for manual/ad-hoc troubleshooting.
    """
    health = current_app.extensions["health"]
    skip_cache = request.args.get("fresh", "").strip().lower() in ("1", "true", "yes")
    ok, checks = await health.readiness(use_cache=not skip_cache)
    body = {
        "status": "ok" if ok else "unavailable",
        "mode": current_app.extensions["settings"].default_generation_provider,
        "checks": [c.to_dict() for c in checks],
    }
    return jsonify(body), 200 if ok else 503
