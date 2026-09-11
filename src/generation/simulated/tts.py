"""Narration synthesis for the simulated provider, via edge-tts.

edge-tts drives Microsoft Edge's neural text-to-speech voices for free and
without an API key, which is what makes natural-sounding (non-monotonic)
narration possible in the "simulated" provider without any paid credentials.
It does require outbound network access to Microsoft's speech endpoint; if
that's unavailable this surfaces a clear GenerationFailedError rather than
silently shipping a video with broken or missing narration.
"""
from __future__ import annotations

from pathlib import Path

import edge_tts

from src.domain.errors import GenerationFailedError


async def synthesize(text: str, voice: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(output_path))
    except Exception as exc:  # noqa: BLE001 - normalize every TTS failure mode
        raise GenerationFailedError(
            f"Text-to-speech synthesis failed (voice={voice!r}): {exc}"
        ) from exc

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise GenerationFailedError(
            "Text-to-speech produced an empty audio file - check network "
            "access to the edge-tts speech endpoint."
        )
