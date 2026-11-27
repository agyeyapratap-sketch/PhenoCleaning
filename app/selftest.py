"""Self-test that runs INSIDE the packaged app:  PhenoCleaning.exe --selftest   (exit 0 = all good).

PyInstaller can silently leave out modules that are only imported by name at runtime (matplotlib's
PDF/SVG backends were missed in v1.2.0, so PDF export failed only in the packaged build). This test
exercises every such path in the frozen app itself, and CI refuses to publish a build that fails it.
It uses small synthetic data generated here — no user data is bundled.
"""
from __future__ import annotations

import io
import sys
import tempfile
import traceback
from pathlib import Path


def _fixture(folder: Path) -> None:
    """Two exports in one folder (the v1.1 name-collision case); export B has a 2-row carrier
    (4 columns x 2 rows) and one carrier scanned twice on a day."""
    import pandas as pd
    head = ["unit", "timestamp", "sensor", "genotype", "g_alias", "treatment",
            "Digital Biomass mm³", "Plant Height Max mm"]
    rows_a, rows_b = [], []
    for day, d in enumerate(["2026-07-07", "2026-08-06", "2026-09-03"]):
        for c in range(1, 5):
            rows_a.append([f"1:{c}:1", f"{d} 10:00:00", "A", "G1", "G1", f"2_R_K_{c}",
                           1000.0 * (day + 1) * c, 50.0 + 10 * day])
            for r in (1, 2):
                rows_b.append([f"2:{c}:{r}", f"{d} 11:00:00", "A", "G2", "G2", f"3_N_M_{c}{r}",
                               900.0 * (day + 1) * c, 40.0 + 10 * day])
    for c in range(1, 5):   # carrier 2 rescanned on the first day
        for r in (1, 2):
            rows_b.append([f"2:{c}:{r}", "2026-07-07 11:05:00", "A", "G2", "G2", f"3_N_M_{c}{r}",
                           950.0 * c, 41.0])
    pd.DataFrame(rows_a, columns=head).to_csv(folder / "ExpA_PlantEye.csv", index=False)
    pd.DataFrame(rows_b, columns=head).to_csv(folder / "ExpB_PlantEye.csv", index=False)


def run() -> int:
    here = Path(__file__).resolve().parent
    for p in (here, here.parent / "tools"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    import matplotlib
    matplotlib.use("Agg")

    failures = 0

    def check(name, fn):
        nonlocal failures
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failures += 1
            print(f"FAIL  {name}\n{traceback.format_exc()}")

    import numpy as np
    import pandas as pd

    def figures():
        import pe_viz
        d = pd.DataFrame({"unit": ["1:1:1", "1:1:1", "1:2:1", "1:2:1"],
                          "date": pd.to_datetime(["2026-07-07", "2026-08-06"] * 2),
                          "g": ["a", "a", "b", "b"], "y": [1.0, 2.0, 1.5, 2.5]})
        fig, _ = pe_viz.trait_figure(d, "y", "g", show_points=True)
        for fmt, magic in (("png", b"\x89PNG"), ("pdf", b"%PDF"), ("svg", b"<?xml")):
            b = pe_viz.figure_bytes(fig, fmt, dpi=72)
            assert b.startswith(magic) and len(b) > 500, f"{fmt}: {b[:20]!r}"
        gif = pe_viz.trait_chart_gif(d, "y", "g", size=200)
        assert gif.startswith(b"GIF8"), "animated chart is not a GIF"

    def three_d():
        import pe_viz
        from planteye_io import PLY_VERTEX_DTYPE
        rng = np.random.default_rng(0)
        pts = np.zeros(500, dtype=PLY_VERTEX_DTYPE)
        for k in ("x", "y", "z"):
            pts[k] = rng.uniform(0, 100, 500)
        pts["red"], pts["green"], pts["blue"] = 60, 140, 60
        pts["red16"], pts["green16"], pts["blue16"] = 600, 1400, 600
        pts["nir"], pts["nir940"], pts["ndvi"], pts["hue"] = 3000, 2800, 0.6, 110
        assert pe_viz.still_views(pts, "ndvi", size=160).startswith(b"\x89PNG")
        assert pe_viz.turntable_gif(pts, key="rgb", n_frames=2, size=160).startswith(b"GIF8")

    def excel():
        import openpyxl  # noqa: F401  (manual-measurement / design workbooks)
        buf = io.BytesIO()
        pd.DataFrame({"barcode": [1], "position": [1], "height": [10.0]}).to_excel(buf, index=False)
        buf.seek(0)
        assert pd.read_excel(buf)["height"].iloc[0] == 10.0

    def data():
        import pe_data
        with tempfile.TemporaryDirectory() as tmp:
            _fixture(Path(tmp))
            insts = pe_data.discover_instances(tmp)
            names = [i.name for i in insts]
            assert len(insts) == 2 and len(set(names)) == 2, f"export names not unique: {names}"
            cand = pd.concat([i.df.assign(source_instance=i.name) for i in insts], ignore_index=True)
            assert len(cand) == 12 + 24 + 8, len(cand)
            u = pe_data.resolve_union(insts)
            assert len(u) == 12 + 24, f"combined rows {len(u)}"
            assert u.loc[u["block"] == 2, "unit"].nunique() == 8, "2-row carrier lost plants"
            assert len(pe_data.unresolved_rescans(cand, {})) == 1, "rescan not detected"
            assert "scan_of_day" not in u, "scan_of_day added without a keep-both choice"
            # keep BOTH scans of the rescanned carrier-day: 8 extra rows, numbered 1 and 2
            day1 = cand[(cand["block"] == 2) & (cand["date"] == pd.Timestamp("2026-07-07"))]
            both = {pe_data.scan_key(s, n, t): {"action": "keep"}
                    for s, n, t in zip(day1["source_instance"], day1["unit"], day1["timestamp"])}
            assert len(pe_data.unresolved_rescans(cand, both)) == 0, "keep-both not counted as resolved"
            ub = pe_data.resolve_union(insts, both)
            assert len(ub) == 12 + 24 + 8, f"keep-both rows {len(ub)}"
            assert sorted(ub["scan_of_day"].unique()) == [1, 2], "scan_of_day not numbered"
            assert "scan_of_day" not in pe_data.trait_columns(ub), "scan_of_day treated as a trait"
            import pe_viz
            _, summ = pe_viz.trait_figure(ub[ub["block"] == 2], "Plant Height Max mm")
            assert summ["n"].max() == 8, "a double-scanned plant was counted twice in the mean"

    def ui():
        import streamlit  # noqa: F401

    def image_serving():
        """The web server must send headers before the body for images. Starlette 1.10 + Streamlit
        1.59.2 sent image responses without headers, so every graph/3D view/GIF failed to load."""
        import asyncio
        from starlette.responses import Response
        from streamlit.web.server.starlette.starlette_gzip_middleware import SelectiveGZipMiddleware

        async def one(ctype, accept):
            sent = []

            async def send(m):
                sent.append(m["type"])

            async def receive():
                return {"type": "http.request"}

            app = SelectiveGZipMiddleware(Response(b"x" * 5000, media_type=ctype), minimum_size=500)
            await app({"type": "http", "path": "/media/x",
                       "headers": [(b"accept-encoding", accept)]}, receive, send)
            return sent

        for ctype in ("image/png", "image/gif", "application/pdf", "text/csv"):
            for accept in (b"gzip", b""):
                sent = asyncio.run(one(ctype, accept))
                assert sent[:1] == ["http.response.start"], f"{ctype} (gzip={bool(accept)}): {sent}"

    check("figure export: PNG, PDF, SVG and animated GIF", figures)
    check("web server sends images, PDFs and CSVs correctly", image_serving)
    check("3D rendering: still view and turntable", three_d)
    check("Excel workbooks (openpyxl)", excel)
    check("combine exports: unique names, 2-row carrier, duplicate scans", data)
    check("Streamlit", ui)
    print("SELFTEST " + ("OK" if not failures else f"FAILED ({failures})"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
