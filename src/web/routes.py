"""HTML view routes: the learner-facing web interface.

Deliberately separate from src/api - these routes render Jinja templates
and handle browser form submission, while src/api serves the JSON contract
consumed by app.js's status polling (and by any external client). Both
blueprints call the same JobService, so there's exactly one code path for
"submit a job" regardless of which interface triggered it.
"""
from __future__ import annotations

from quart import Blueprint, current_app, redirect, render_template, request, url_for

from src.domain.errors import ServiceError

web_bp = Blueprint(
    "web",
    __name__,
    template_folder="../view/templates",
    static_folder="../view/static",
    static_url_path="/static",
)


@web_bp.get("/")
async def index():
    job_service = current_app.extensions["job_service"]
    recent_jobs = (await job_service.list_jobs())[:5]
    return await render_template("index.html", recent_jobs=recent_jobs)


@web_bp.post("/submit")
async def submit():
    form = await request.form
    query = form.get("query", "")
    difficulty = form.get("difficulty") or None
    provider = form.get("provider") or None

    job_service = current_app.extensions["job_service"]
    try:
        job = await job_service.submit(query, difficulty=difficulty, provider=provider)
    except ServiceError as exc:
        recent_jobs = (await job_service.list_jobs())[:5]
        rendered = await render_template(
            "index.html",
            error_message=exc.message,
            error_code=exc.code,
            submitted_query=query,
            recent_jobs=recent_jobs,
        )
        return rendered, exc.http_status
    return redirect(url_for("web.job_detail", job_id=job.id))


@web_bp.get("/jobs")
async def jobs_list():
    job_service = current_app.extensions["job_service"]
    jobs = await job_service.list_jobs()
    return await render_template("jobs.html", jobs=jobs)


@web_bp.get("/jobs/<job_id>")
async def job_detail(job_id: str):
    job_service = current_app.extensions["job_service"]
    try:
        job = await job_service.get(job_id)
    except ServiceError as exc:
        rendered = await render_template(
            "job_not_found.html", job_id=job_id, error_message=exc.message
        )
        return rendered, exc.http_status
    return await render_template("job_detail.html", job=job)


@web_bp.post("/jobs/<job_id>/delete")
async def delete_job(job_id: str):
    job_service = current_app.extensions["job_service"]
    try:
        await job_service.delete_job(job_id)
    except ServiceError as exc:
        jobs = await job_service.list_jobs()
        rendered = await render_template(
            "jobs.html", jobs=jobs, error_message=exc.message
        )
        return rendered, exc.http_status
    return redirect(url_for("web.jobs_list"))
