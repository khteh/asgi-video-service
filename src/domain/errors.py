"""Service-level exceptions.

Every exception here carries an HTTP status and a stable machine-readable
`code` so the API layer can translate it into a JSON error response without
any generation- or persistence-specific knowledge leaking into src/api.
"""
from __future__ import annotations


class ServiceError(Exception):
    """Base class for errors the API layer knows how to translate."""

    http_status: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class InvalidQueryError(ServiceError):
    """Query is structurally invalid: empty, whitespace-only, punctuation
    noise, digits-only, too short/long, etc. Raised before any job exists.
    """

    http_status = 400
    code = "invalid_query"


class NotStemRelevantError(ServiceError):
    """Query is well-formed text but doesn't make semantic sense or isn't
    a STEM topic. Raised before any job exists.
    """

    http_status = 422
    code = "not_stem_relevant"


class ProviderUnavailableError(ServiceError):
    """The requested generation provider can't service the request right
    now (missing/invalid API key, no network, upstream unreachable). Raised
    during preflight, before any job exists - so no job is ever created for
    a doomed request.
    """

    http_status = 503
    code = "provider_unavailable"


class JobNotFoundError(ServiceError):
    http_status = 404
    code = "job_not_found"


class ArtifactNotFoundError(ServiceError):
    http_status = 404
    code = "artifact_not_found"


class GenerationFailedError(ServiceError):
    """Raised internally by a provider when generation fails mid-flight,
    after the job already exists. The job service catches this and marks
    the job FAILED rather than propagating it as an HTTP error.
    """

    http_status = 500
    code = "generation_failed"
