"""Frozen entry point for the PhenoCleaning app (built with PyInstaller).

Non-coders double-click the resulting PhenoCleaning.exe; this boots the Streamlit server in-process
and opens the browser automatically. Because it runs natively, it reads local and network-drive
data directly.
"""
import os
import sys
import time
import threading
import webbrowser
from pathlib import Path

PORT = 8501
URL = f"http://localhost:{PORT}"


def _base() -> Path:
    # PyInstaller unpacks bundled data to sys._MEIPASS (onedir: the _internal folder).
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def _open_browser_when_ready(timeout: int = 90) -> None:
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{URL}/_stcore/health", timeout=1)
            webbrowser.open(URL)
            return
        except Exception:
            time.sleep(0.5)


def main() -> None:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
    os.environ["STREAMLIT_GLOBAL_DEVELOPMENT_MODE"] = "false"

    app_path = str(_base() / "app" / "pe_app.py")

    threading.Thread(target=_open_browser_when_ready, daemon=True).start()

    print("\n  PhenoCleaning is starting — a browser tab will open at", URL)
    print("  Keep this window open while you work; close it to stop the app.\n")

    sys.argv = [
        "streamlit", "run", app_path,
        f"--server.port={PORT}",
        "--server.headless=true",          # we open the browser ourselves
        "--global.developmentMode=false",
        "--browser.gatherUsageStats=false",
    ]
    from streamlit.web import cli as stcli
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
