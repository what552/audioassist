"""Shared resolution of AudioAssist data, configuration, and output paths."""
from __future__ import annotations

import json
import os

from platformdirs import user_data_dir


def resolve_runtime_paths(
    output_override: str | None = None,
    config_override: str | None = None,
) -> tuple[str, str, str]:
    app_data_dir = user_data_dir("TranscribeApp", appauthor=False)
    config_path = os.path.abspath(
        os.path.expanduser(config_override or os.path.join(app_data_dir, "config.json"))
    )
    output_dir = output_override
    if not output_dir:
        try:
            with open(config_path, encoding="utf-8") as file:
                configured = json.load(file).get("storage", {}).get("output_dir", "")
            if configured and os.path.isdir(os.path.expanduser(configured)):
                output_dir = configured
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    default_output = os.path.join(app_data_dir, "output")
    return (
        os.path.abspath(os.path.expanduser(output_dir or default_output)),
        config_path,
        os.path.dirname(config_path),
    )
