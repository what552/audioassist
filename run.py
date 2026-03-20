#!/usr/bin/env python3
"""
AudioAssist startup entry point.
"""
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

import webview
import app as _app_module
from app import API


def main():
    api = API()
    ui_path = os.path.join(os.path.dirname(__file__), "ui", "index.html")

    window = webview.create_window(
        "AudioAssist",
        url=f"file://{ui_path}",
        js_api=api,
        width=1280,
        height=800,
        min_size=(900, 600),
    )
    _app_module._window = window
    webview.start(debug="--debug" in sys.argv)


if __name__ == "__main__":
    main()
