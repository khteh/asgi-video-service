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


def _patch_quart_duplicate_h3_body_end() -> None:
    """Work around an unresolved anycorn HTTP/3 bug: davidbrochart/anycorn#89
    (https://github.com/davidbrochart/anycorn/issues/89), filed against this
    exact project, no fix released yet.

    anycorn's H3 protocol handler can dispatch a stream's "end of body"
    event twice for one request - once from a HeadersReceived(stream_ended=
    True) event, again from a following DataReceived(stream_ended=True)
    event - with nothing to check whether the stream already ended. Quart's
    ASGI receive-loop (handle_messages, in quart/asgi.py) takes that second,
    spurious chunk and calls Body.put() on it, but the *first*, legitimate
    end-of-body message already shut the body's asyncio.Queue down
    (Body.set_complete() -> self._queue.shutdown()), so the second put()
    raises asyncio.QueueShutDown. That's unhandled inside Quart's own
    asyncio.TaskGroup in ASGIHTTPConnection.__call__, which crashes the
    entire worker process, not just the one request - there is no src/
    frame anywhere in the traceback, this is not an application bug.

    A chunk that arrives after the body is already complete cannot be
    delivered to the view anyway (nothing is still reading the queue), so
    the only sane options are "crash the process" (today) or "drop it and
    log it" (this patch). This does not touch anycorn - its h3.py is under
    active upstream development (see the closed PRs around H3 framing on
    that repo) and patching it locally would be fighting a moving target;
    Body.put() is the one, stable choke point every transport (HTTP/1, H2,
    H3) funnels through, so guarding it there covers all of them.

    Remove this once anycorn#89 is fixed upstream and the fix is released.
    """
    from quart.wrappers.request import Body

    original_put = Body.put

    async def _put_tolerating_late_chunks(self: Body, data: bytes) -> None:
        try:
            await original_put(self, data)
        except asyncio.queues.QueueShutDown:
            logging.getLogger(__name__).warning(
                "Dropped a request-body chunk (%d bytes) delivered after "
                "the body was already complete - this is anycorn issuing a "
                "duplicate end-of-stream event over HTTP/3 "
                "(see https://github.com/davidbrochart/anycorn/issues/89), "
                "not application data loss for the request that already "
                "completed.",
                len(data),
            )

    Body.put = _put_tolerating_late_chunks


#_patch_quart_duplicate_h3_body_end()


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
    # https://quart-wtf.readthedocs.io/en/stable/how_to_guides/configuration.html
    CSRFProtect(app)
    # Disable CSRFProtect's automatic before_request check. That hook reads
    # the request body itself (await request.form) to pull out the token,
    # and src/web/routes.py's submit() view *also* reads request.form to get
    # the query - two reads of the same ASGI body stream on every submit.
    # Quart's Request.body is backed by an asyncio.Queue fed by the ASGI
    # receive() loop; on Python 3.14 the queue gets shut down once the body
    # is drained, and the second read then hits asyncio.QueueShutDown deep
    # inside Quart's own asgi.py/wrappers/request.py (no application frames
    # in the traceback - this isn't app-code misuse, it's the double read
    # itself). Turning off the default check and validating the token
    # manually, exactly once, inside the one view that needs it removes the
    # second read entirely. It also means the JSON API (src/api/routes.py)
    # is no longer subject to CSRF checks at all, which is correct - CSRF is
    # a browser/cookie-session attack, and the API doesn't authenticate via
    # cookies, so a separate csrf.exempt(api_bp) is unnecessary now too.
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False

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
