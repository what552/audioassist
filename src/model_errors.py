"""Shared model-readiness errors for local-only runtime paths."""
from __future__ import annotations


class ModelNotReadyError(RuntimeError):
    """Raised when a required app-managed model cannot be used locally."""

    def __init__(self, model_id: str, reason: str):
        self.model_id = model_id
        self.reason = reason  # "model_not_downloaded" | "model_incomplete"
        super().__init__(
            f"Model '{model_id}' is not ready: {reason.replace('_', ' ')}. "
            "Please download it from the Model Library."
        )
