"""Write app/build_info.json (version, build date, git commit). Run automatically by the spec."""
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from version import __version__  # noqa: E402

try:
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    sha += "+local" if dirty else ""
except Exception:
    sha = "unknown"

info = {"version": __version__, "date": dt.date.today().isoformat(), "commit": sha}
(ROOT / "app" / "build_info.json").write_text(json.dumps(info), encoding="utf-8")
print("build_info:", info)
