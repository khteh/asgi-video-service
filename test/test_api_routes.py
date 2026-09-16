"""End-to-end HTTP tests against the JSON API, using FakeProvider so no
ffmpeg/edge-tts/network is required to run this suite.
"""
from __future__ import annotations

import pytest

from conftest import FakeProvider
from src.main import create_app
from src.domain.models import GenerationProviderName

@pytest.fixture
def app(settings):
    # `settings` is the shared fixture from conftest.py: a fresh
    # Settings(output_dir=tmp_path / "output") per test function, so each
    # test's job store is isolated in pytest's own tmp_path rather than the
    # real output/ directory - no cleanup needed since nothing is shared.
    application = create_app(settings)
    application.config["WTF_CSRF_ENABLED"] = False
    providers = application.extensions["providers"]
    providers._providers[GenerationProviderName.SIMULATED] = FakeProvider(available=True)
    providers._providers[GenerationProviderName.AI] = FakeProvider(available=False)
    return application

@pytest.mark.asyncio
async def test_health(app):
    client = app.test_client()
    resp = await client.get("/api/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_submit_list_and_status_roundtrip(app):
    client = app.test_client()

    resp = await client.post("/api/jobs", json={"query": "How does the pH scale work?"})
    assert resp.status_code == 201
    body = await resp.get_json()
    job_id = body["id"]
    assert body["status"] == "pending"
    assert body["difficulty"] == "intermediate"

    list_resp = await client.get("/api/jobs")
    assert list_resp.status_code == 200
    listed = await list_resp.get_json()
    assert any(j["id"] == job_id for j in listed["jobs"])

    status_resp = await client.get(f"/api/jobs/{job_id}")
    assert status_resp.status_code == 200
    status_body = await status_resp.get_json()
    assert status_body["id"] == job_id


@pytest.mark.asyncio
async def test_unknown_job_returns_404(app):
    client = app.test_client()
    resp = await client.get("/api/jobs/does-not-exist")
    assert resp.status_code == 404
    body = await resp.get_json()
    assert body["error"]["code"] == "job_not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_query", ["", "   ", "~!@#$", "12345"])
async def test_invalid_query_returns_400_and_creates_no_job(app, bad_query):
    client = app.test_client()
    resp = await client.post("/api/jobs", json={"query": bad_query})
    assert resp.status_code == 400
    body = await resp.get_json()
    assert body["error"]["code"] == "invalid_query"

    list_resp = await client.get("/api/jobs")
    listed = await list_resp.get_json()
    assert listed["jobs"] == []


@pytest.mark.asyncio
async def test_off_topic_query_returns_422_and_creates_no_job(app):
    client = app.test_client()
    resp = await client.post("/api/jobs", json={"query": "What is your favorite pizza topping?"})
    assert resp.status_code == 422
    body = await resp.get_json()
    assert body["error"]["code"] == "not_stem_relevant"

    list_resp = await client.get("/api/jobs")
    listed = await list_resp.get_json()
    assert listed["jobs"] == []


@pytest.mark.asyncio
async def test_ai_provider_unavailable_returns_503_and_creates_no_job(app):
    client = app.test_client()
    resp = await client.post(
        "/api/jobs",
        json={"query": "How does the pH scale work?", "provider": "ai"},
    )
    assert resp.status_code == 503
    body = await resp.get_json()
    assert body["error"]["code"] == "fake_unavailable"

    list_resp = await client.get("/api/jobs")
    listed = await list_resp.get_json()
    assert listed["jobs"] == []


@pytest.mark.asyncio
async def test_artifact_404_before_job_completes(app):
    client = app.test_client()
    resp = await client.post("/api/jobs", json={"query": "How does the pH scale work?"})
    body = await resp.get_json()
    job_id = body["id"]

    artifact_resp = await client.get(f"/api/jobs/{job_id}/artifact")
    assert artifact_resp.status_code == 404
    error_body = await artifact_resp.get_json()
    assert error_body["error"]["code"] == "artifact_not_found"


@pytest.mark.asyncio
async def test_delete_unknown_job_returns_404(app):
    client = app.test_client()
    resp = await client.delete("/api/jobs/does-not-exist")
    assert resp.status_code == 404
    body = await resp.get_json()
    assert body["error"]["code"] == "job_not_found"


@pytest.mark.asyncio
async def test_delete_pending_job_returns_409(app):
    client = app.test_client()
    resp = await client.post("/api/jobs", json={"query": "How does the pH scale work?"})
    body = await resp.get_json()
    job_id = body["id"]

    delete_resp = await client.delete(f"/api/jobs/{job_id}")
    assert delete_resp.status_code == 409
    error_body = await delete_resp.get_json()
    assert error_body["error"]["code"] == "job_not_deletable"
