"""Covers DependencyHealth (src/health/checks.py) - the mode-aware
downstream-dependency validation backing the /health/ready probe - and
JobWorker.is_running(), which backs /health/live.

FakeProvider stands in for both providers so these tests exercise the
mode-awareness and caching logic itself, not real ffmpeg/edge-tts/network
access (that's exactly what test_simulated_provider.py's preflight test
and test_tts.py already cover for the pieces that do touch the real
world).
"""
from __future__ import annotations

import asyncio
import dataclasses

import pytest

from conftest import FakeProvider
from src.domain.models import GenerationProviderName
from src.generation.registry import ProviderRegistry
from src.health.checks import CheckResult, DependencyHealth
from src.jobs.worker import JobWorker


def _make_health(settings, *, simulated=None, ai=None, cache_seconds=30.0):
    providers = ProviderRegistry(
        {
            GenerationProviderName.SIMULATED: simulated or FakeProvider(available=True),
            GenerationProviderName.AI: ai or FakeProvider(available=True),
        }
    )
    return DependencyHealth(settings, providers, cache_seconds=cache_seconds), providers


@pytest.mark.asyncio
async def test_readiness_ok_when_simulated_provider_and_output_dir_are_fine(
    settings, monkeypatch
):
    async def _fake_edge_tts_ok(**_kwargs):
        return CheckResult("edge_tts_reachable", True)

    monkeypatch.setattr("src.health.checks.check_edge_tts_reachable", _fake_edge_tts_ok)
    health, _ = _make_health(settings)

    ok, checks = await health.readiness()

    assert ok is True
    names = {c.name for c in checks}
    assert names == {"output_dir_writable", "provider:simulated", "edge_tts_reachable"}


@pytest.mark.asyncio
async def test_readiness_skips_edge_tts_check_in_ai_mode(settings, monkeypatch):
    settings = dataclasses.replace(settings, default_generation_provider="ai")
    calls = 0

    async def _fake_edge_tts(**_kwargs):
        nonlocal calls
        calls += 1
        return CheckResult("edge_tts_reachable", True)

    monkeypatch.setattr("src.health.checks.check_edge_tts_reachable", _fake_edge_tts)
    health, _ = _make_health(settings)

    ok, checks = await health.readiness()

    assert ok is True
    names = {c.name for c in checks}
    assert names == {"output_dir_writable", "provider:ai"}
    assert calls == 0


@pytest.mark.asyncio
async def test_readiness_fails_when_the_configured_providers_preflight_fails(
    settings, monkeypatch
):
    async def _fake_edge_tts_ok(**_kwargs):
        return CheckResult("edge_tts_reachable", True)

    monkeypatch.setattr("src.health.checks.check_edge_tts_reachable", _fake_edge_tts_ok)
    health, _ = _make_health(settings, simulated=FakeProvider(available=False))

    ok, checks = await health.readiness()

    assert ok is False
    provider_check = next(c for c in checks if c.name == "provider:simulated")
    assert provider_check.ok is False
    assert "fake_unavailable" in provider_check.detail


@pytest.mark.asyncio
async def test_readiness_fails_when_output_dir_is_not_writable(settings):
    # Make output_dir a *file* instead of a directory - mkdir(parents=True,
    # exist_ok=True) against it then reliably raises OSError, cross-platform,
    # without needing filesystem-permission tricks.
    settings.output_dir.parent.mkdir(parents=True, exist_ok=True)
    settings.output_dir.write_text("not a directory")

    health, _ = _make_health(settings)
    ok, checks = await health.readiness()

    assert ok is False
    assert next(c for c in checks if c.name == "output_dir_writable").ok is False


@pytest.mark.asyncio
async def test_readiness_caches_the_provider_check_within_the_ttl(settings, monkeypatch):
    async def _fake_edge_tts_ok(**_kwargs):
        return CheckResult("edge_tts_reachable", True)

    monkeypatch.setattr("src.health.checks.check_edge_tts_reachable", _fake_edge_tts_ok)
    simulated = FakeProvider(available=True)
    health, _ = _make_health(settings, simulated=simulated, cache_seconds=60.0)

    await health.readiness()
    await health.readiness()

    assert simulated.preflight_calls == 1


@pytest.mark.asyncio
async def test_readiness_fresh_bypasses_the_cache(settings, monkeypatch):
    async def _fake_edge_tts_ok(**_kwargs):
        return CheckResult("edge_tts_reachable", True)

    monkeypatch.setattr("src.health.checks.check_edge_tts_reachable", _fake_edge_tts_ok)
    simulated = FakeProvider(available=True)
    health, _ = _make_health(settings, simulated=simulated, cache_seconds=60.0)

    await health.readiness()
    await health.readiness(use_cache=False)

    assert simulated.preflight_calls == 2


@pytest.mark.asyncio
async def test_worker_is_running_reflects_start_and_stop(settings, tmp_path):
    from src.persistence.artifact_store import ArtifactStore
    from src.persistence.job_store import FileSystemJobStore

    job_store = FileSystemJobStore(tmp_path / "output")
    artifact_store = ArtifactStore(tmp_path / "output")
    providers = ProviderRegistry(
        {
            GenerationProviderName.SIMULATED: FakeProvider(available=True),
            GenerationProviderName.AI: FakeProvider(available=True),
        }
    )
    queue: "asyncio.Queue[str]" = asyncio.Queue()
    worker = JobWorker(
        queue=queue, job_store=job_store, artifact_store=artifact_store,
        providers=providers, concurrency=1,
    )

    assert worker.is_running() is False

    worker.start()
    try:
        assert worker.is_running() is True
    finally:
        await worker.stop()

    assert worker.is_running() is False
