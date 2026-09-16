"""JSON API routes.

Thin HTTP adapters over JobService / ArtifactStore - all real logic
(validation, provider preflight, generation, persistence) lives in the
layers those objects wrap. Every ServiceError subclass carries its own
`http_status` and `code`, so error handling here is uniform: one handler
turns any ServiceError raised anywhere in this blueprint into the right
JSON error response.
"""
from __future__ import annotations

from quart import Blueprint, current_app, jsonify, request, send_file

from src.domain.errors import ServiceError

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.errorhandler(ServiceError)
async def handle_service_error(exc: ServiceError):
    return jsonify({"error": {"code": exc.code, "message": exc.message}}), exc.http_status


@api_bp.get("/health")
async def health():
    return jsonify({"status": "ok"})


@api_bp.post("/jobs")
async def submit_job():
    """Submit a new video generation request.

    Body: {"query": str, "difficulty"?: "beginner"|"intermediate"|"advanced",
    "provider"?: "simulated"|"ai"}.

    On invalid/off-topic queries, or an unready "ai" provider, this returns
    an error status immediately and creates NO job (see JobService.submit).
    """
    payload = await request.get_json(force=True, silent=True) or {}
    query = payload.get("query", "")
    difficulty = payload.get("difficulty")
    provider = payload.get("provider")

    job_service = current_app.extensions["job_service"]
    job = await job_service.submit(query, difficulty=difficulty, provider=provider)
    return jsonify(job.to_dict()), 201


@api_bp.get("/jobs")
async def list_jobs():
    job_service = current_app.extensions["job_service"]
    jobs = await job_service.list_jobs()
    return jsonify({"jobs": [j.to_dict() for j in jobs]})


@api_bp.get("/jobs/<job_id>")
async def get_job(job_id: str):
    job_service = current_app.extensions["job_service"]
    job = await job_service.get(job_id)
    return jsonify(job.to_dict())


@api_bp.get("/jobs/<job_id>/artifact")
async def get_artifact(job_id: str):
    job_service = current_app.extensions["job_service"]
    artifact_store = current_app.extensions["artifact_store"]
    job = await job_service.get(job_id)
    video_path = artifact_store.resolve_video(job)
    return await send_file(video_path, mimetype="video/mp4")


@api_bp.get("/jobs/<job_id>/thumbnail")
async def get_thumbnail(job_id: str):
    job_service = current_app.extensions["job_service"]
    artifact_store = current_app.extensions["artifact_store"]
    job = await job_service.get(job_id)
    thumbnail_path = artifact_store.resolve_thumbnail(job)
    return await send_file(thumbnail_path, mimetype="image/png")


@api_bp.delete("/jobs/<job_id>")
async def delete_job(job_id: str):
    """Deletes a job's status file and any video/thumbnail/working files it
    produced. 404 if the job doesn't exist, 409 if it's still
    PENDING/GENERATING (see JobService.delete_job)."""
    job_service = current_app.extensions["job_service"]
    await job_service.delete_job(job_id)
    return "", 204
