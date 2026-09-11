"""Provider factory / registry - maps a provider name to a configured
VideoGenerationProvider instance.
"""
from __future__ import annotations

from src.config import Settings
from src.domain.models import GenerationProviderName

from .ai.provider import GenericAIVideoProvider
from .base import VideoGenerationProvider
from .simulated.provider import SimulatedVideoProvider


class ProviderRegistry:
    def __init__(self, providers: dict[GenerationProviderName, VideoGenerationProvider]) -> None:
        self._providers = providers

    def get(self, name: GenerationProviderName) -> VideoGenerationProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise ValueError(f"Unknown generation provider: {name!r}") from exc

    @classmethod
    def from_settings(cls, settings: Settings) -> "ProviderRegistry":
        return cls(
            {
                GenerationProviderName.SIMULATED: SimulatedVideoProvider(settings),
                GenerationProviderName.AI: GenericAIVideoProvider(settings),
            }
        )
