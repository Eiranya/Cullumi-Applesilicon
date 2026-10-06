from __future__ import annotations

import json
import logging
import logging.handlers
import multiprocessing
import os
import secrets
import sys
import threading
import traceback
import webbrowser
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from cullumi import http_api
from cullumi.analysis_worker import PhotoAnalysisPool
from cullumi.config import ConfigStore, app_data_dir
from cullumi.face_analysis import FaceAnalyzer, configure_accelerator
from cullumi.project_store import ProjectManager
from cullumi.scanner import Scanner
from cullumi.similarity import SimilarityGroupCache

logger = logging.getLogger(__name__)


def configure_logging() -> Path | None:
    """Send our diagnostics to a file under the app data directory.

    The bundle is built with ``console=False``, so stdout and stderr are
    discarded -- and the macOS unified log carries nothing for the process
    either (verified). Without a handler, a warning such as "the accelerator
    fell back to CPU" or "drag-and-drop could not be armed" would be
    impossible to diagnose on a user's machine.

    Warning level only: this is for things the user may need to report, not
    for routine tracing. Rotating caps the file at ~256 KB.
    """
    try:
        log_path = app_data_dir() / "cullumi.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=256 * 1024, backupCount=1, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root = logging.getLogger()
        root.setLevel(logging.WARNING)
        root.addHandler(handler)
        return log_path
    except Exception:
        # Diagnostics must never prevent the app from starting.
        return None


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


CONFIG = ConfigStore()
MANAGER = ProjectManager(CONFIG)
SIMILARITY_GROUPS = SimilarityGroupCache()
FACE_ANALYZER = FaceAnalyzer(resource_path("models/blink"))
# Apply the stored acceleration preference before any inference runs. Sessions
# are created lazily on first use, so this only has to happen once at startup;
# the settings route re-applies it when the user toggles the switch.
configure_accelerator(bool(CONFIG.snapshot().get("blink_gpu_enabled", True)))
ANALYSIS_RUNNER = PhotoAnalysisPool()
SCANNER = Scanner(
    CONFIG,
    MANAGER,
    SIMILARITY_GROUPS,
    FACE_ANALYZER,
    ANALYSIS_RUNNER,
)
TOKEN = secrets.token_urlsafe(24)
WEB_ROOT = resource_path("web")
APP_ICON = WEB_ROOT / "assets" / "icons" / "brand-icon.ico"
APPLICATION = http_api.ApplicationContext(
    CONFIG,
    MANAGER,
    SCANNER,
    SIMILARITY_GROUPS,
    TOKEN,
    WEB_ROOT,
    FACE_ANALYZER,
    analysis_runner=ANALYSIS_RUNNER,
)
http_api.configure(APPLICATION)


def apply_native_window_icon(window: Any) -> None:
    """Set the window icon on Windows.

    macOS takes the icon from the ``.app`` bundle's ``CFBundleIconFile`` at
    load time, and pywebview documents ``icon=`` as "supported only on
    GTK/QT". The pythonnet imports below therefore stay behind the platform
    guard so they never execute on macOS.
    """
    if sys.platform != "win32" or not APP_ICON.is_file():
        return
    if not window.events.shown.wait(15):
        return
    try:
        from System import Action
        from System.Drawing import Icon

        native = window.native
        icon = Icon(str(APP_ICON))
        native.Invoke(Action(lambda: setattr(native, "Icon", icon)))
    except Exception:
        pass


def _run_in_ui(window: Any, script: str) -> None:
    """Evaluate a snippet in the page, logging rather than raising on failure.

    Drop handling runs on a pywebview worker thread; an exception here would be
    swallowed by that thread and leave the user staring at nothing.
    """
    try:
        window.evaluate_js(script)
    except Exception:
        logger.warning("无法向界面发送拖放结果：%s", traceback.format_exc())


def _handle_drop(window: Any, event: Any) -> None:
    """Translate a dropped item into an "open this folder" request for the UI."""
    payload = event or {}
    files = payload.get("dataTransfer", {}).get("files", []) or []
    paths = [
        entry.get("pywebviewFullPath")
        for entry in files
        if isinstance(entry, dict) and entry.get("pywebviewFullPath")
    ]
    if not paths:
        # pywebview only attaches pywebviewFullPath once it has matched the
        # dropped name against the pasteboard, so this is a real failure mode.
        _run_in_ui(window, "window.cullumiRejectDrop('missing')")
        return
    # Directory URLs arrive with a trailing slash. Normalise before the value
    # reaches project_id_for(), which keys projects off the resolved path.
    root = os.path.normpath(paths[0])
    if not os.path.isdir(root):
        _run_in_ui(window, "window.cullumiRejectDrop('not-a-folder')")
        return
    _run_in_ui(window, f"window.cullumiAcceptDrop({json.dumps(root)})")


def enable_folder_drop(window: Any) -> None:
    """Arm drag-and-drop so a folder dropped on the window opens it.

    The path can only be obtained here. A browser exposes nothing but the bare
    file name, so pywebview's macOS backend reads it off the pasteboard
    instead -- and it only does that while at least one ``drop`` listener is
    registered through its DOM API, which is what this call sets up. Without
    it, `_dnd_state['num_listeners']` stays 0 and drops are ignored outright.

    Waiting on ``loaded`` rather than ``shown`` matters: pywebview's
    `get_element` goes straight to `evaluate_js` with no readiness wait of its
    own (unlike `create_element`, which blocks on `loaded`), so registering too
    early would quietly attach the listener to nothing.
    """
    if not window.events.loaded.wait(20):
        logger.warning("页面未在超时内加载完成，拖放功能不可用")
        return
    try:
        target = window.dom.get_element("body")
    except Exception:
        logger.warning("拖放功能不可用（DOM 不可访问）：%s", traceback.format_exc())
        return
    if target is None:
        logger.warning("拖放功能不可用：未找到可用于接收拖放的 body 元素")
        return
    try:
        target.on("drop", lambda event: _handle_drop(window, event))
    except Exception:
        # Never fatal: without the DOM bridge dragging is unavailable, but the
        # folder picker still works, which is the documented fallback.
        logger.warning("注册拖放监听失败：%s", traceback.format_exc())


def prepare_window(window: Any) -> None:
    """One-shot window setup that needs the GUI to be up (off the GUI thread)."""
    apply_native_window_icon(window)
    enable_folder_drop(window)


def start_webview(url: str) -> None:
    """Open the local UI in a native window and block until it closes."""
    import webview

    window = webview.create_window(
        "Cullumi", url, width=1460, height=940, min_size=(980, 680)
    )
    if sys.platform == "win32":
        # Windows requires the Edge WebView2 runtime, which pywebview reaches
        # through pythonnet. Keep the upstream check so a missing WebView2
        # install falls back to the browser instead of showing a blank window.
        from webview.platforms import winforms

        if winforms.renderer != "edgechromium":
            raise RuntimeError("Microsoft Edge WebView2 不可用")

        webview.start(
            prepare_window,
            (window,),
            icon=str(APP_ICON),
            gui="edgechromium",
        )
        return

    # macOS renders through WKWebView (webview/platforms/cocoa.py). 'cocoa' is
    # the only valid value for webview.start(gui=...) on Darwin: 'wkwebview' is
    # the backend's `renderer` attribute, not an accepted GUI name.
    webview.start(prepare_window, (window,), gui="cocoa")


def run() -> None:
    configure_logging()
    server = ThreadingHTTPServer(("127.0.0.1", 0), http_api.Handler)
    server.application = APPLICATION
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/?token={TOKEN}"
    print(url)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        start_webview(url)
    except Exception:
        # Any WebView failure (missing runtime, sandbox denial, unsupported
        # GUI backend) degrades to the system browser so the app stays usable
        # instead of showing a blank window.
        try:
            log_path = app_data_dir() / "webview-error.log"
            log_path.write_text(
                f"{datetime.now().isoformat(timespec='seconds')}\n{traceback.format_exc()}",
                encoding="utf-8",
            )
        except Exception:
            pass
        webbrowser.open(url)
        try:
            while True:
                threading.Event().wait(3600)
        except KeyboardInterrupt:
            pass
    finally:
        ANALYSIS_RUNNER.close()
        server.shutdown()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    run()
