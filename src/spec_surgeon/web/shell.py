"""The native window around the page: pywebview (Edge WebView2 on Windows) when it is installed and has a backend
on this computer, else the default browser. ``run_window`` starts the server on a free port, opens the page with
the launch token, blocks until the window closes (or, in the browser, until it is told to stop), and stops the
server."""
from __future__ import annotations

import signal
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
# The window's background before the page paints, per colour scheme: each scheme's --color-bg in
# frontend/src/index.css (tests/test_settings.py checks them), so a light scheme never opens on a dark frame.
BACKGROUNDS = {"clay": "#262624", "blueprint": "#121a26", "graphite": "#1b1d1f", "carbon": "#0b0b0c",
               "linen": "#f5f1ea"}
MIN_SIZE = (1100, 700)
FILE_TYPES = {"documents": ("Word documents (*.docx)",), "template": ("Word documents (*.docx)",),
              "references": ("Pictures and tables (*.png;*.jpg;*.jpeg;*.gif;*.bmp;*.xlsx;*.csv;*.json)",)}


class WebviewShell(Shell):
    """pywebview's window: native file and folder dialogs, a native yes/no question, and closing."""

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

    def pick_folder(self, start: str = "") -> Optional[str]:
        if self.window is None:
            return None
        import webview
        kwargs = {"directory": start} if start else {}
        result = self.window.create_file_dialog(webview.FOLDER_DIALOG, **kwargs)
        if not result:
            return ""
        chosen = result[0] if isinstance(result, (list, tuple)) else result
        return str(chosen)

    def confirm(self, title: str, message: str) -> bool:
        """A native OK / Cancel dialog; OK is True. A dialog the window cannot show answers no: a question that could
        not be asked never lets a copy be deleted."""
        if self.window is None:
            return False
        try:
            return bool(self.window.create_confirmation_dialog(title, message))
        except Exception as exc:  # noqa: BLE001 — an older pywebview, a backend without the dialog
            diag.app_log().warning(f"The close question could not be shown ({exc}); the copies are kept")
            return False

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
    # straight to the loopback: a system or environment proxy (the Windows registry's, ``http_proxy``) would be
    # handed this URL — and the launch token — and cannot reach this computer's own server
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=1) as resp:  # noqa: S310 — our own loopback server
                if resp.status == 200:
                    return server
        except Exception:  # noqa: BLE001 — not up yet
            time.sleep(0.05)
    raise RuntimeError("the window's server did not start")


def unsaved_copies_question(count: int) -> str:
    """What the close asks when edited copies or clean PDFs were never saved: closing deletes them with the scratch
    folder."""
    files = ("1 file made in this window (an edited copy or a clean PDF) has" if count == 1
             else f"{count} files made in this window (edited copies or clean PDFs) have")
    them = "it" if count == 1 else "them"
    return (f"{files} not been saved. Closing deletes {them} with the window's scratch folder.\n\n"
            f"OK closes anyway. Cancel goes back, so you can save {them} with Export → Save the copies…")


def on_closing(state: WindowState) -> bool:
    """The window's X. With edited copies never saved by Export, one native question first: OK closes and lets the
    scratch folder (the copies in it) be deleted (``close_confirmed``), Cancel keeps the window open — the only time
    a close is refused. With nothing unsaved, nothing is asked and nothing is recorded, so copies a run still
    finishing delivers after this are kept for the next start (``gui.main``).
    While work runs the running AI step is cut off, as Stop now does (its progress is saved), and a download is
    cancelled. True: pywebview closes the window."""
    try:
        unsaved = len(state.task.unsaved_copies())
    except Exception:  # noqa: BLE001 — a count that cannot be taken never deletes a copy
        unsaved = -1
    if unsaved:
        if unsaved < 0 or not state.shell.confirm("Copies not saved", unsaved_copies_question(unsaved)):
            diag.event("window.close_refused", unsaved=unsaved)
            diag.app_log().info(f"Window close cancelled: {unsaved} unsaved cop{'y' if unsaved == 1 else 'ies'}")
            return False
        # Consent is recorded only when the question was asked and answered OK. With nothing unsaved now it stays
        # unrecorded: a run still finishing can deliver copies after this, and ``gui.main`` then keeps the scratch
        # folder for the next start instead of deleting copies nobody was asked about.
        state.close_confirmed = True
    try:
        job = state.job_status()
        diag.event("window.closing", job=job["action"] if job else None, unsaved=unsaved)
        diag.app_log().info("Window closing" + (f" while job {job['action']} runs: stopping it" if job else
                                                " (no job running)")
                            + (f"; {unsaved} unsaved cop{'y' if unsaved == 1 else 'ies'} given up" if unsaved else ""))
    except Exception:  # noqa: BLE001 — closing is never refused over bookkeeping
        pass
    try:
        if state.busy:
            state.stop()
        if state.updates is not None and state.updates.state().get("state") == "downloading":
            state.updates.cancel_download()
    except Exception:  # noqa: BLE001 — closing is never refused over bookkeeping
        pass
    return True


def shutdown(state: WindowState, server, *, wait: float = 60.0) -> None:
    """Nothing outlives the window: a job still running is told to stop and waited for, so its last writes (the
    settings, the journal) land before the process ends and never after a later launch's; then the server stops.
    A job that does not stop in ``wait`` seconds leaves every thread's stack in the app log before the process waits
    on it (``gui.main``), so a window that closed but a process that stayed shows why."""
    state.watch.stop()
    thread = state.thread
    with diag.span("window.shutdown", job_running=bool(thread is not None and thread.is_alive())) as outcome:
        if thread is not None and thread.is_alive():
            job = state.job_status()
            state.stop()
            t0 = time.monotonic()
            thread.join(wait)
            outcome["waited_s"] = round(time.monotonic() - t0, 1)
            outcome["job_stopped"] = not thread.is_alive()
            if thread.is_alive():
                diag.app_log().warning(f"Job {job['action'] if job else '?'} did not stop within {wait:.0f} s of the "
                                       "window closing; the process stays until it does. Where every thread of the "
                                       "app is:\n" + diag.thread_stacks())
        try:
            state.save_settings()
        except Exception:  # noqa: BLE001
            pass
        server.should_exit = True


def _stop_on_signals() -> None:
    """The browser fallback waits in the main thread for Ctrl+C. A process started in the background by a script or
    a service inherits SIGINT ignored, and Python then never installs its KeyboardInterrupt handler, so make a
    termination request — SIGTERM, or an inherited-ignored SIGINT — stop the wait the same way: the server is shut
    down and the settings saved, and the exit code is 0."""
    def stop(signum, frame):
        raise KeyboardInterrupt
    for name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        if name == "SIGINT" and signal.getsignal(sig) is not signal.SIG_IGN:
            continue                                  # Python's own Ctrl+C handling is in place; keep it
        try:
            signal.signal(sig, stop)
        except (ValueError, OSError):                 # not the main thread, or a signal this platform refuses
            pass


def native_backend_problem() -> Optional[str]:
    """None when pywebview can open a native window on this computer; else one line why not — it is not installed,
    or it has no backend here (pythonnet and WebView2 on Windows, GTK bindings on Linux, PyObjC on macOS).
    ``webview.start`` would raise the same failure after the server is up; asking first keeps the browser fallback
    reachable with pywebview installed, which it is wherever the ``gui`` extra is."""
    try:
        from webview.guilib import initialize
    except ImportError as e:
        return f"pywebview is not installed ({e})"
    try:
        initialize()
    except Exception as e:  # noqa: BLE001 — WebViewException, a backend's own ImportError or load failure
        return f"pywebview has no native window backend on this computer ({e})"
    return None


def _renderer() -> str:
    """pywebview's renderer once its backend is chosen (``edgechromium`` is WebView2 on Windows); the page's own
    ``page.loaded`` report names the WebView2 version through its user agent."""
    try:
        from webview.guilib import initialize     # what ``native_backend_problem`` already ran; ``start`` runs it again
        return str(getattr(initialize(), "renderer", None) or "unknown")
    except Exception:  # noqa: BLE001 — a name for the log, never a reason to fail
        return "unknown"


def run_window(state: WindowState, *, dev: bool = False, page_url: Optional[str] = None) -> int:
    """Serve, open the page, block until the window closes. ``page_url`` (dev): the Vite dev server instead of the
    built page."""
    port = free_port()
    server = serve(state, port, dev=dev)
    diag.event("window.server.started", port=port)
    state.watch.start()
    base = f"http://127.0.0.1:{port}"
    theme = state.settings.theme
    url = (page_url or base + "/") + f"?token={state.token}&api={base}&theme={theme}"
    try:
        problem = native_backend_problem()
        if problem is None:
            import webview
            shell = WebviewShell()
            state.shell = shell
            try:
                webview.settings["ALLOW_DOWNLOADS"] = True
            except Exception:  # noqa: BLE001 — older pywebview without the settings dict
                pass
            shell.window = webview.create_window(about.APP_NAME, url, width=WINDOW_SIZE[0], height=WINDOW_SIZE[1],
                                                 min_size=MIN_SIZE, text_select=True,
                                                 background_color=BACKGROUNDS.get(theme, BACKGROUNDS["clay"]))
            shell.window.events.closing += lambda: on_closing(state)
            renderer = _renderer()
            diag.event("window.opened", shell="pywebview", renderer=str(renderer), port=port, theme=theme)
            diag.app_log().info(f"Window opened (pywebview, renderer {renderer}) on 127.0.0.1:{port}")
            webview.start(debug=dev)
            diag.event("window.closed")
        else:
            diag.app_log().warning(f"{problem}; opening the page in the default browser")
            diag.event("window.opened", shell="browser", reason=problem, port=port, theme=theme)
            _stop_on_signals()
            webbrowser.open(url)
            print(f"Spec Surgeon is open in your browser at {base}/ — press Ctrl+C here to close it.", file=sys.stderr)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
    finally:
        shutdown(state, server)
    return 0
