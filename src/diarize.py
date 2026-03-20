"""
Speaker diarization module using pyannote.audio.
Requires HF_TOKEN env var or explicit token param.
"""
from __future__ import annotations
from dataclasses import dataclass
import os
import logging

logger = logging.getLogger(__name__)


@dataclass
class SpeakerSegment:
    speaker: str   # e.g. "SPEAKER_00"
    start: float   # seconds
    end: float     # seconds


class DiarizationEngine:
    """Wraps pyannote/speaker-diarization-3.1."""

    MODEL = "pyannote/speaker-diarization-3.1"

    def __init__(
        self,
        hf_token: str | None = None,
        num_speakers: int | None = None,
        hf_endpoint: str | None = None,
    ):
        self.hf_token = hf_token or os.environ.get("HF_TOKEN")
        self.num_speakers = num_speakers
        if hf_endpoint:
            os.environ.setdefault("HF_ENDPOINT", hf_endpoint)
        # Prevent pyannote from checking HF on every load (fails in China)
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        self._pipeline = None

    def load(self):
        """Lazy-load pyannote pipeline."""
        try:
            from pyannote.audio import Pipeline
            import torch
        except ImportError:
            raise ImportError(
                "pyannote.audio not installed. Run: pip install pyannote.audio"
            )

        if not self.hf_token:
            raise ValueError(
                "HF_TOKEN required for pyannote. "
                "Set env var HF_TOKEN or pass hf_token= to DiarizationEngine."
            )

        logger.info(f"Loading diarization model: {self.MODEL}")
        self._pipeline = Pipeline.from_pretrained(
            self.MODEL, token=self.hf_token
        )

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
