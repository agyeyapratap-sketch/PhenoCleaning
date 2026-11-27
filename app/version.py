"""PhenoCleaning version — the single place to bump it.

Packaged builds also carry ``build_info.json`` (written by ``packaging/write_build_info.py`` when
the spec runs) with the build date and git commit, so every screen and every error report says
exactly which build a user has.
"""
import json
from pathlib import Path

__version__ = "1.2.1"


def build_info() -> dict:
    p = Path(__file__).resolve().parent / "build_info.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"version": __version__, "date": "", "commit": "dev"}


def version_string() -> str:
    """``PhenoCleaning 1.2.1 (build 2026-11-27, abc1234)``; ``(dev)`` when run from source."""
    b = build_info()
    if b.get("date"):
        return f"PhenoCleaning {__version__} (build {b['date']}, {b.get('commit', '?')})"
    return f"PhenoCleaning {__version__} (dev)"
