from __future__ import annotations

import os
import sys
import threading
import time
import urllib.request

import uvicorn

from backend.app.main import app

# PyInstaller windowed builds (console=False) leave these as None; uvicorn's
# log formatters call .isatty() on them and crash, so point them at os.devnull.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")


URL = "http://127.0.0.1:8765"


def run_server() -> None:
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")


def wait_for_server() -> None:
    for _ in range(80):
        try:
            urllib.request.urlopen(f"{URL}/api/health", timeout=1)
            return
        except Exception:
            time.sleep(0.25)


def main() -> None:
    import webview

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    wait_for_server()

    webview.create_window(
        "英语刷题机",
        URL,
        width=1280,
        height=860,
        min_size=(960, 640),
    )
    webview.start()


if __name__ == "__main__":
    main()
