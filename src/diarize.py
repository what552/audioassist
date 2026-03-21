"""
Speaker diarization module using pyannote.audio.
Models are loaded from local paths managed by ModelManager.
HF_TOKEN is only required for pyannote-diarization-3.1 (gated model).
pyannote-diarization-community-1 (default) requires no token.
"""
from __future__ import annotations
from dataclasses import dataclass
import os
import logging

from .model_manager import ModelManager

logger = logging.getLogger(__name__)

DEFAULT_DIARIZER_MODEL = "pyannote-diarization-community-1"


@dataclass
class SpeakerSegment:
    speaker: str   # e.g. "SPEAKER_00"
    start: float   # seconds
    end: float     # seconds


class DiarizationEngine:
    """Loads a pyannote diarization pipeline from a local ModelManager path."""

    def __init__(
        self,
        model_id: str | None = None,
        hf_token: str | None = None,
        num_speakers: int | None = None,
    ):
        self.model_id = model_id or DEFAULT_DIARIZER_MODEL
        # hf_token kept for backward compat (3.1 gated model); not passed to
        # from_pretrained since we load from a local path.
        self.hf_token = hf_token or os.environ.get("HF_TOKEN")
        self.num_speakers = num_speakers
        self._pipeline = None

    def load(self):
        """Lazy-load pyannote pipeline from local ModelManager path."""
        if self._pipeline is not None:
            return

        try:
            from pyannote.audio import Pipeline
        except ImportError:
            raise ImportError(
                "pyannote.audio not installed. Run: pip install pyannote.audio"
            )

        mm = ModelManager()
        info = mm.get_model(self.model_id)

        if info is None:
            raise ValueError(
                f"Unknown diarizer model: {self.model_id!r}. "
                "Check ModelManager.CATALOG for valid IDs."
            )

        if info.requires_token and not self.hf_token:
            raise ValueError(
                f"HF_TOKEN required for {self.model_id}. "
                "Set env var HF_TOKEN or pass hf_token= to DiarizationEngine."
            )

        local_path = mm.local_path(self.model_id)
        logger.info(f"Loading diarization model from: {local_path}")
        self._pipeline = Pipeline.from_pretrained(local_path)

        import torch
        if torch.backends.mps.is_available():
            device = torch.device("mps")
        elif torch.cuda.is_available():
            device = torch.device("cuda")
        else:
            device = torch.device("cpu")

        logger.info(f"Diarization running on: {device}")
        self._pipeline.to(device)

    def diarize(self, audio_path: str) -> list[SpeakerSegment]:
        """Return speaker segments for the audio file."""
        if self._pipeline is None:
            self.load()

        kwargs = {}
        if self.num_speakers:
            kwargs["num_speakers"] = self.num_speakers

        logger.info(f"Diarizing: {audio_path}")
        result = self._pipeline(audio_path, **kwargs)

        # pyannote 4.x returns DiarizeOutput; 3.x returns Annotation directly
        annotation = (
            result.speaker_diarization
            if hasattr(result, "speaker_diarization")
            else result
        )

        segments = []
        for turn, _, speaker in annotation.itertracks(yield_label=True):
            segments.append(SpeakerSegment(
                speaker=speaker,
                start=turn.start,
                end=turn.end,
            ))
        return segments
