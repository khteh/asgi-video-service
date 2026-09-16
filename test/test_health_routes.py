"""End-to-end HTTP tests for the /health/live and /health/ready probe
endpoints (src/health/routes.py), using FakeProvider so no ffmpeg/edge-tts/
network is required - matching test_api_routes.py's approach.
"""
from __future__ import annotations

import dataclasses

import pytest

from conftest import FakeProvider
from src.domain.models import GenerationProviderName
from src.health import checks as health_checks
from src.main import create_app


@pytest.fixture
def app(settings, monkeypatch):
    async def _fake_edge_tts_ok(**_kwargs):
        return health_checks.CheckResult("edge_tts_reachable", True)

    monkeypatch.setattr(health_checks, "check_edge_tts_reachable", _fake_edge_tts_ok)

    application = create_app(settings)
    application.config["WTF_CSRF_ENABLED"] = False
    providers = application.extensions["providers"]
    providers._providers[GenerationProviderName.SIMULATED] = FakeProvider(available=True)
    providers._providers[GenerationProviderName.AI] = FakeProvider(available=False)
    return application


@pytest.mark.asyncio
async def test_liveness_ok_before_worker_starts_but_reflects_worker_state(app):
    # start()/stop() aren't driven by Quart's before_serving hook in a bare
    # test_client() (that only fires for a real serve() run), so this
    # exercises JobWorker.is_running() directly through the live endpoint
    # rather than relying on app startup having called it.
    worker = app.extensions["worker"]
    client = app.test_client()

    resp = await client.get("/health/live")
    assert resp.status_code == 503  # no worker tasks yet

    worker.start()
    try:
        resp = await client.get("/health/live")
        assert resp.status_code == 200
        body = await resp.get_json()
        assert body["status"] == "ok"
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_readiness_ok_when_simulated_provider_and_dependencies_are_fine(app):
    client = app.test_client()

    resp = await client.get("/health/ready")

    assert resp.status_code == 200
    body = await resp.get_json()
    assert body["status"] == "ok"
    assert body["mode"] == "simulated"
    assert all(c["ok"] for c in body["checks"])


@pytest.mark.asyncio
async def test_readiness_fails_when_configured_for_ai_mode_without_a_key(app, settings):
    # The `app` fixture wires an *unavailable* FakeProvider as "ai" - the
    # same failure shape a real ai-mode deployment sees with no
    # AI_VIDEO_API_KEY configured (see GenericAIVideoProvider.preflight).
    settings_ai = dataclasses.replace(settings, default_generation_provider="ai")
    application = create_app(settings_ai)
    providers = application.extensions["providers"]
    providers._providers[GenerationProviderName.AI] = FakeProvider(available=False)
    client = application.test_client()

    resp = await client.get("/health/ready")

    assert resp.status_code == 503
    body = await resp.get_json()
    assert body["status"] == "unavailable"
    assert body["mode"] == "ai"
    provider_check = next(c for c in body["checks"] if c["name"] == "provider:ai")
    assert provider_check["ok"] is False


@pytest.mark.asyncio
async def test_readiness_fresh_query_param_is_accepted(app):
    client = app.test_client()

    resp = await client.get("/health/ready?fresh=true")

    assert resp.status_code == 200
