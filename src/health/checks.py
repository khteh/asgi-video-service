"""Downstream-dependency validation backing the Kubernetes health probes.

This app has two distinct "modes of operation" (Settings.
default_generation_provider: "simulated" or "ai"), and each depends on a
different set of downstream services - checking the wrong set (or both,
unconditionally) would make a "simulated"-mode deployment report unready
just because no AI_VIDEO_API_KEY is configured, which it was never going
to use, or waste time/quota hitting a real AI vendor from a deployment
that only ever runs "simulated" jobs. Every check below is mode-aware:

- "simulated": no API key, but it shells out to ffmpeg/ffprobe (see
  src/generation/simulated/video_builder.py) and needs outbound network
  access to edge-tts's free speech endpoint for narration (tts.py).
- "ai": needs AI_VIDEO_API_KEY configured and the configured vendor
  endpoint reachable (see GenericAIVideoProvider.preflight).

Library presence (Pillow, httpx, edge_tts, quart, ...) is deliberately
NOT checked here: every provider module imports the libraries it needs at
module level, and src/generation/registry.py imports both provider
modules unconditionally at app-startup time regardless of which mode is
configured - so if a required library were actually missing, the process
would fail on import before it ever got far enough to serve a probe at
all (visible to Kubernetes as CrashLoopBackOff, not a failed readiness
check). A runtime "is this importable" check here would be unreachable
dead code.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

from src.config import Settings
from src.domain.errors import ServiceError
from src.domain.models import GenerationProviderName
from src.generation.registry import ProviderRegistry

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


def check_output_dir_writable(settings: Settings) -> CheckResult:
    """Every job, in either mode, needs to persist its status JSON (and,
    for "simulated", its video/slide/audio scratch files) under
    output_dir - a missing or read-only output_dir fails every job the
    same way an unreachable provider does, so it belongs in the same
    readiness gate as the mode-specific checks below."""
    marker = Path(settings.output_dir) / ".healthcheck"
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("ok")
        marker.unlink(missing_ok=True)
        return CheckResult("output_dir_writable", True, str(settings.output_dir))
    except OSError as exc:
        return CheckResult("output_dir_writable", False, str(exc))


async def check_provider_preflight(
    providers: ProviderRegistry, provider_name: GenerationProviderName
) -> CheckResult:
    """Delegates to the provider's own preflight() - the exact same
    fail-fast check JobService.submit runs before creating a job - so a
    health probe and a real job submission always agree on what "ready"
    means for the configured mode. For "simulated" this covers the
    ffmpeg/ffprobe binaries it shells out to (see SimulatedVideoProvider.
    preflight); for "ai" it covers the API key and upstream reachability
    (see GenericAIVideoProvider.preflight).
    """
    try:
        await providers.get(provider_name).preflight()
        return CheckResult(f"provider:{provider_name.value}", True)
    except ServiceError as exc:
        return CheckResult(f"provider:{provider_name.value}", False, f"{exc.code}: {exc.message}")
    except Exception as exc:  # noqa: BLE001 - a probe must never itself crash
        return CheckResult(f"provider:{provider_name.value}", False, str(exc))


async def check_edge_tts_reachable(*, timeout: float = 5.0) -> CheckResult:
    """Only meaningful in "simulated" mode: narration synthesis
    (src/generation/simulated/tts.py) needs outbound network access to
    Microsoft's free edge-tts speech endpoint. Deliberately NOT folded
    into SimulatedVideoProvider.preflight() itself - that runs
    synchronously before every single job submission, and adding a
    network round trip there would slow down every "simulated" job
    creation just to re-prove something the readiness probe already
    checks on its own cadence (and caches - see DependencyHealth).
    edge_tts.list_voices() is the library's own lightest call: one small
    HTTPS GET with no audio synthesis - a good proxy for "is the endpoint
    reachable" without the cost of a real narration request.
    """
    try:
        import edge_tts

        await asyncio.wait_for(edge_tts.list_voices(), timeout=timeout)
        return CheckResult("edge_tts_reachable", True)
    except asyncio.TimeoutError:
        return CheckResult("edge_tts_reachable", False, f"timed out after {timeout:.0f}s")
    except Exception as exc:  # noqa: BLE001 - normalize every failure mode
        return CheckResult("edge_tts_reachable", False, str(exc))


class DependencyHealth:
    """Backs the /health/ready endpoint (src/health/routes.py).

    Two of its checks make a real network call: the configured provider's
    preflight(), and - only in "simulated" mode - the edge-tts
    reachability check. Kubernetes polls readiness continuously in steady
    state (typically every ~10-15s); without caching, every tick would
    re-hit an external endpoint, which is noisy at best. Both are cached
    for `cache_seconds`. Pass use_cache=False (wired to the `?fresh=true`
    query param on /health/ready, and to what startupProbe is configured
    to request in k8s/deployment.yaml) to force a fresh check - the right
    behavior while a pod is still starting up, since there's no
    steady-state polling cost to worry about yet and "has initialization
    actually succeeded" should mean a real, uncached check.

    check_output_dir_writable is cheap and purely local, so it always runs
    fresh regardless of use_cache.
    """

    def __init__(
        self,
        settings: Settings,
        providers: ProviderRegistry,
        *,
        cache_seconds: Optional[float] = None,
    ) -> None:
        self.settings = settings
        self.providers = providers
        self.cache_seconds = (
            cache_seconds if cache_seconds is not None else settings.health_check_cache_seconds
        )
        self._cache: dict[str, tuple[float, CheckResult]] = {}
        self._lock = asyncio.Lock()

    async def _cached(
        self, key: str, use_cache: bool, check: Callable[[], Awaitable[CheckResult]]
    ) -> CheckResult:
        if use_cache:
            cached = self._cache.get(key)
            if cached is not None and (time.monotonic() - cached[0]) < self.cache_seconds:
                return cached[1]

        async with self._lock:
            # Re-check after acquiring the lock: a concurrent caller may
            # already have refreshed this key while this one was waiting.
            if use_cache:
                cached = self._cache.get(key)
                if cached is not None and (time.monotonic() - cached[0]) < self.cache_seconds:
                    return cached[1]
            result = await check()
            self._cache[key] = (time.monotonic(), result)
            return result

    def _default_provider_name(self) -> GenerationProviderName:
        return GenerationProviderName.from_str(self.settings.default_generation_provider)

    async def readiness(self, *, use_cache: bool = True) -> tuple[bool, list[CheckResult]]:
        provider_name = self._default_provider_name()
        checks = [check_output_dir_writable(self.settings)]

        checks.append(
            await self._cached(
                f"provider:{provider_name.value}",
                use_cache,
                lambda: check_provider_preflight(self.providers, provider_name),
            )
        )

        if provider_name is GenerationProviderName.SIMULATED:
            checks.append(
                await self._cached("edge_tts_reachable", use_cache, check_edge_tts_reachable)
            )

        return all(c.ok for c in checks), checks
