from __future__ import annotations

import threading
import time
import urllib.request
import webbrowser

import uvicorn


URL = "http://127.0.0.1:8765"


def open_when_ready() -> None:
    for _ in range(80):
        try:
            urllib.request.urlopen(f"{URL}/api/health", timeout=1)
            webbrowser.open(URL)
            return
        except Exception:
            time.sleep(0.25)


if __name__ == "__main__":
    threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8765)

