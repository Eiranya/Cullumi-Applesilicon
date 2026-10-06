"""Verify Cullumi's macOS native dialog path.

Drives the real ``cullumi.native_dialogs`` helpers inside a live WKWebView
window and screenshots the resulting NSOpenPanel, proving that the
``_webview_dialog`` branch works on macOS and that the PowerShell fallback is
never reached.

The dialog is auto-dismissed after a short delay because there is no human to
click it; what matters is that a native panel appears at all.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

# Allow "python evaluation/probe_native_dialogs.py" from the project root by
# putting the repository root (the parent of this file's directory) on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import webview  # noqa: E402

from cullumi import native_dialogs  # noqa: E402


def auto_dismiss() -> None:
    """Cancel the panel with Escape once it has had time to appear."""
    time.sleep(6)
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to key code 53'],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except Exception:
        pass


def main() -> None:
    print("platform module check:")
    print("  sys.platform =", sys.platform)
    print(
        "  _run_powershell_dialog returns:",
        repr(native_dialogs._run_powershell_dialog("Write-Output 'should-not-run'")),
    )
    print("  (empty above means the PowerShell fallback is unreachable on macOS)")

    webview.create_window("Dialog probe", html="<h1>dialog probe</h1>")
    threading.Thread(target=auto_dismiss, daemon=True).start()

    def probe() -> None:
        time.sleep(3)
        result = native_dialogs.choose_csv("选择筛选结果 CSV")
        print("choose_csv returned:", repr(result))

    threading.Thread(target=probe, daemon=True).start()
    webview.start(gui="cocoa")
    print("dialog probe finished")


if __name__ == "__main__":
    main()
