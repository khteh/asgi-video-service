"""Quart application factory.

This is the composition root: the only place that wires the validation,
generation, persistence, job-service, and worker layers together and hands
the result to the API/web blueprints via `app.extensions`. Every other
module only depends on the interfaces defined in its own layer (see the
Protocols in src/validation/base.py, src/generation/base.py,
src/persistence/job_store.py), so this file is also the only place that
would need to change to, say, swap FileSystemJobStore for a database.
"""
from __future__ import annotations

import asyncio, json, logging

from quart import Quart, Response, request
from quart_wtf.csrf import CSRFProtect, CSRFError
from quart_cors import cors
from quart_uploads import UploadSet, configure_uploads, FE
from quart import flash, request, json, Blueprint, session, render_template, session, redirect, url_for
from anycorn.config import Config
from anycorn.middleware import HTTPToHTTPSRedirectMiddleware
from datetime import date, datetime, timedelta, timezone
from src.api.routes import api_bp
from src.config import Settings
from src.domain.models import GenerationProviderName
from src.generation.registry import ProviderRegistry
from src.jobs.service import JobService
from src.jobs.worker import JobWorker
from src.persistence.artifact_store import ArtifactStore
from src.persistence.job_store import FileSystemJobStore
from src.validation import build_validator
from src.web.routes import web_bp
from src.config import settings
config = Config()
config.from_toml("/etc/hypercorn.toml")

def _add_secure_headers(response: Response) -> Response:
    response.headers["Strict-Transport-Security"] = (
        "max-age=63072000; includeSubDomains; preload"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response

def create_app() -> Quart:
    # static_folder=None: the app package itself has no top-level "static"
    # dir (view/static belongs to web_bp, see src/web/routes.py) - without
    # this, Quart still registers a default "/static/<path>" route for the
    # app, which would shadow the blueprint's own static route at the same
    # URL prefix and silently 404 every CSS/JS/image request.
    app = Quart(__name__, template_folder='view/templates', static_url_path='', static_folder='view/static')
    app.config.from_file("/etc/stem-video-service_config.json", json.load)
    app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # form/JSON bodies are tiny
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = timedelta(days=90)
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["WTF_CSRF_TIME_LIMIT"] = None # Max age in seconds for CSRF tokens. If set to None, the CSRF token is valid for the life of the session.
    app.config["SECRET_KEY"] = settings.SECRET_KEY
    app.config["JWT_SECRET_KEY"] = settings.JWT_SECRET_KEY
    app.after_request(_add_secure_headers)
    app = cors(app, allow_credentials=True, allow_origin="https://localhost")
    app = cors(app, allow_credentials=True, allow_origin="https://localhost")
    # https://quart-wtf.readthedocs.io/en/stable/how_to_guides/configuration.html
    CSRFProtect(app)

    @app.errorhandler(CSRFError)
    async def handle_csrf_error(e):
        logging.exception(f"handle_csrf_error {e.description}")
        await flash("Session expired!", "danger")
        if "url" in session and session["url"]:
            return redirect(session["url"]), 440
        else:
            return redirect(url_for("web.index")), 440

    job_store = FileSystemJobStore(settings.output_dir)
    artifact_store = ArtifactStore(settings.output_dir)
    providers = ProviderRegistry.from_settings(settings)
    validator = build_validator(settings)

    queue: "asyncio.Queue[str]" = asyncio.Queue()
    job_service = JobService(
        validator=validator,
        providers=providers,
        job_store=job_store,
        queue=queue,
        default_provider=GenerationProviderName.from_str(settings.default_generation_provider),
    )
    worker = JobWorker(
        queue=queue,
        job_store=job_store,
        artifact_store=artifact_store,
        providers=providers,
        concurrency=settings.worker_concurrency,
    )

    app.extensions["settings"] = settings
    app.extensions["job_store"] = job_store
    app.extensions["artifact_store"] = artifact_store
    app.extensions["providers"] = providers
    app.extensions["job_service"] = job_service
    app.extensions["worker"] = worker

    app.register_blueprint(api_bp)
    app.register_blueprint(web_bp)

    @app.before_serving
    async def _start_worker() -> None:
        worker.start()

    @app.after_serving
    async def _stop_worker() -> None:
        await worker.stop()

    return app


# Module-level instance for ASGI servers that import a dotted path
# (`anycorn src.app:app`).
logging.info(f"Running app...")
app = create_app()
