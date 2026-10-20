"""The native window around the page: pywebview (Edge WebView2 on Windows) when it is there, else the default
browser. ``run_window`` starts the server on a free port, opens the page with the launch token, blocks until the
window closes, and stops the server."""
from __future__ import annotations

import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from typing import List, Optional

from .. import about
from .. import diagnostics as diag
from .server import Shell, WindowState, create_app

WINDOW_SIZE = (1440, 900)
MIN_SIZE = (1100, 700)
FILE_TYPES = {"documents": ("Word documents (*.docx)",), "template": ("Word documents (*.docx)",),
              "references": ("Pictures and tables (*.png;*.jpg;*.jpeg;*.gif;*.xlsx;*.csv;*.json)",)}


class WebviewShell(Shell):
    """pywebview's window: native file dialogs, and closing."""

    def __init__(self):
        self.window = None

    def pick_files(self, kind: str) -> Optional[List[str]]:
        if self.window is None:
            return None
        import webview
        result = self.window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=kind != "template",
                                                file_types=FILE_TYPES.get(kind, FILE_TYPES["documents"]))
        if not result:
            return []
        return [str(p) for p in (result if isinstance(result, (list, tuple)) else [result])]

    def close(self) -> None:
        if self.window is not None:
            self.window.destroy()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(state: WindowState, port: int, *, dev: bool = False):
    """Start uvicorn on a daemon thread; returns the server (``should_exit`` stops it)."""
    import uvicorn
    app = create_app(state, dev=dev)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="spec-surgeon-server")
    thread.start()
    deadline = time.monotonic() + 15
    url = f"http://127.0.0.1:{port}/api/health?token={state.token}"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:  # noqa: S310 — our own loopback server
                if resp.status == 200:
                    return server
        except Exception:  # noqa: BLE001 — not up yet
            time.sleep(0.05)
    raise RuntimeError("the window's server did not start")


def run_window(state: WindowState, *, dev: bool = False, page_url: Optional[str] = None) -> int:
    """Serve, open the page, block until the window closes. ``page_url`` (dev): the Vite dev server instead of the
    built page."""
    port = free_port()
    server = serve(state, port, dev=dev)
    base = f"http://127.0.0.1:{port}"
    url = (page_url or base + "/") + f"?token={state.token}&api={base}"
    try:
        try:
            import webview
        except ImportError:
            webview = None
        if webview is not None:
            shell = WebviewShell()
            state.shell = shell
            try:
                webview.settings["ALLOW_DOWNLOADS"] = True
            except Exception:  # noqa: BLE001 — older pywebview without the settings dict
                pass
            shell.window = webview.create_window(about.APP_NAME, url, width=WINDOW_SIZE[0], height=WINDOW_SIZE[1],
                                                 min_size=MIN_SIZE, text_select=True)
            webview.start(debug=dev)
        else:
            diag.app_log().warning("pywebview is not installed; opening the page in the default browser")
            webbrowser.open(url)
            print(f"Spec Surgeon is open in your browser at {base}/ — press Ctrl+C here to close it.", file=sys.stderr)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
    finally:
        try:
            state.save_settings()
        except Exception:  # noqa: BLE001
            pass
        server.should_exit = True
    return 0
