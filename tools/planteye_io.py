"""Readers for Phenospex PlantEye / TraitFinder outputs.

Everything here is derived from inspection of the real exports in this project; see
docs/PlantEye_pipeline_design.pdf for the evidence behind each constant.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# Vertex record layout of a PlantEye "processed" .ply. Field order is taken from the
# header's property declarations; numpy's default packing (unaligned) reproduces the
# file's 50-byte stride exactly.
PLY_VERTEX_DTYPE = np.dtype(
    [
        ("x", "<f4"),
        ("y", "<f4"),
        ("z", "<f4"),
        ("intensity", "u1"),
        ("profile", "<i4"),
        ("x_pos", "<i4"),
        ("segment_id", "<i4"),
        ("unit_id", "<i4"),
        ("red", "u1"),
        ("green", "u1"),
        ("blue", "u1"),
        ("red16", "<u2"),
        ("green16", "<u2"),
        ("blue16", "<u2"),
        ("nir", "<u2"),
        ("nir940", "<u2"),
        ("ndvi", "<f4"),
        ("hue", "<f4"),
    ]
)
assert PLY_VERTEX_DTYPE.itemsize == 50, PLY_VERTEX_DTYPE.itemsize

# The scanner writes local wall-clock in the tabular export but UTC in .ply filenames.
# Site-specific: do not hard-code this at call sites.
DEFAULT_UTC_OFFSET_HOURS = 8  # AWST

_PLY_NAME_RE = re.compile(r"^(\d+)_(\d{8}T\d{6})_full\.ply$")
_FETCH_RE = re.compile(
    r'fetch\s+"/downloadPly\?block=(\d+)&timestamp=([0-9T]+)&output=(\w+)"\s+"([^"]+)"'
)


@dataclass
class PlyScan:
    """One carrier scan: the point cloud plus what the file says about itself."""

    path: Path
    block: int
    utc: pd.Timestamp
    vertices: np.ndarray
    scanner_data: dict
    transform_config: dict

    @property
    def n_points(self) -> int:
        return len(self.vertices)

    @property
    def unit_ids(self) -> np.ndarray:
        return np.unique(self.vertices["unit_id"])

    def local_time(self, offset_hours: int = DEFAULT_UTC_OFFSET_HOURS) -> pd.Timestamp:
        return self.utc + pd.Timedelta(hours=offset_hours)

    def plant(self, unit_id: int) -> np.ndarray:
        return self.vertices[self.vertices["unit_id"] == unit_id]


def _header_json(header: str, key: str) -> dict:
    """Pull one `obj_info <key>{...}` blob out of a PLY header."""
    marker = f"obj_info {key}{{"
    start = header.find(marker)
    if start < 0:
        return {}
    brace = start + len(marker) - 1
    depth = 0
    for i in range(brace, len(header)):
        if header[i] == "{":
            depth += 1
        elif header[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(header[brace : i + 1])
                except json.JSONDecodeError:
                    return {}
    return {}


def read_ply(path: str | Path) -> PlyScan:
    """Read a PlantEye .ply. Faces are present in the file but unused for 2D work."""
    path = Path(path)
    raw = path.read_bytes()

    # The header is whitespace-padded to a block boundary, so find the terminator
    # rather than assuming an offset.
    idx = raw.find(b"end_header\n")
    if idx < 0:
        raise ValueError(f"{path.name}: no end_header found; not a PLY?")
    hdr_end = idx + len(b"end_header\n")
    header = raw[:idx].decode("ascii", errors="replace")

    if b"binary_little_endian" not in raw[:64]:
        raise ValueError(f"{path.name}: expected binary_little_endian PLY")

    m = re.search(r"element vertex (\d+)", header)
    if not m:
        raise ValueError(f"{path.name}: no 'element vertex' count in header")
    n_vertex = int(m.group(1))

    need = hdr_end + n_vertex * PLY_VERTEX_DTYPE.itemsize
    if len(raw) < need:
        raise ValueError(
            f"{path.name}: truncated — need {need} bytes for {n_vertex} vertices, have {len(raw)}"
        )

    vertices = np.frombuffer(raw, dtype=PLY_VERTEX_DTYPE, count=n_vertex, offset=hdr_end)

    fm = _PLY_NAME_RE.match(path.name)
    if not fm:
        raise ValueError(f"{path.name}: filename does not match <block>_<UTC>_full.ply")
    block = int(fm.group(1))
    utc = pd.to_datetime(fm.group(2), format="%Y%m%dT%H%M%S")

    return PlyScan(
        path=path,
        block=block,
        utc=utc,
        vertices=vertices,
        scanner_data=_header_json(header, "scanner_data"),
        transform_config=_header_json(header, "transform_config"),
    )


def ply_inventory(folders: list[str | Path], offset_hours: int = DEFAULT_UTC_OFFSET_HOURS) -> pd.DataFrame:
    """List .ply files without reading their point data."""
    rows = []
    for folder in folders:
        for p in sorted(Path(folder).glob("*.ply")):
            m = _PLY_NAME_RE.match(p.name)
            if not m:
                continue
            utc = pd.to_datetime(m.group(2), format="%Y%m%dT%H%M%S")
            rows.append(
                {
                    "path": p,
                    "source": Path(folder).name,
                    "block": int(m.group(1)),
                    "utc": utc,
                    "local": utc + pd.Timedelta(hours=offset_hours),
                    "mb": round(p.stat().st_size / 1024**2, 2),
                }
            )
    return pd.DataFrame(rows).sort_values(["block", "utc"]).reset_index(drop=True)


def parse_manifest_ps1(zip_path: str | Path) -> pd.DataFrame:
    """Extract the expected-scan manifest from a 3D-files-downloader zip.

    The zip holds no point clouds (it is ~248 KB: a PDF plus the script). Its value is
    the fetch list, which enumerates every scan the device believes it has.
    """
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".ps1")]
        if not names:
            raise ValueError(f"{Path(zip_path).name}: no .ps1 in zip")
        script = z.read(names[0]).decode("utf-8", errors="replace")

    rows = []
    for block, ts, output, filename in _FETCH_RE.findall(script):
        rows.append(
            {
                "block": int(block),
                "utc": pd.to_datetime(ts, format="%Y%m%dT%H%M%S"),
                "output": output,
                "filename": filename,
            }
        )
    ip = re.search(r'\{\s*"([\d.]+)"\s*\}', script)
    df = pd.DataFrame(rows)
    df.attrs["device_ip"] = ip.group(1) if ip else None
    df.attrs["source"] = Path(zip_path).name
    return df


def _parse_timestamps(s: pd.Series) -> pd.Series:
    """Portal exports use ISO; hand-edited copies here use d/m/Y and drop seconds."""
    out = pd.to_datetime(s, format="%Y-%m-%d %H:%M:%S", errors="coerce")
    if out.isna().any():
        alt = pd.to_datetime(s[out.isna()], dayfirst=True, errors="coerce")
        out.loc[out.isna()] = alt
    return out


def read_tabular(path_or_zip: str | Path) -> pd.DataFrame:
    """Read a tabular export, from a .zip or a bare .csv.

    Portal CSVs are UTF-8 but are frequently opened as Latin-1 somewhere upstream,
    which is where `mmÂ²` comes from; decode explicitly to keep the trait names clean.
    """
    p = Path(path_or_zip)
    if p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as z:
            csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not csvs:
                raise ValueError(f"{p.name}: no CSV in zip")
            data = z.read(csvs[0])
            source = f"{p.name}::{csvs[0]}"
    else:
        data = p.read_bytes()
        source = p.name

    df = pd.read_csv(io.BytesIO(data), encoding="utf-8")
    df.columns = [c.strip() for c in df.columns]
    df["timestamp"] = _parse_timestamps(df["timestamp"])

    # unit is "<block>:<position>:<subsample>", zero-padded in some copies.
    parts = df["unit"].astype(str).str.split(":", expand=True)
    df["block"] = parts[0].astype(int)
    df["position"] = parts[1].astype(int)
    df["plant"] = df["block"].astype(str) + ":" + df["position"].astype(str).str.zfill(2)
    df["date"] = df["timestamp"].dt.normalize()
    df.attrs["source"] = source
    return df


def trait_columns(df: pd.DataFrame) -> list[str]:
    """Numeric PlantEye traits, excluding the identifier/derived columns we add."""
    meta = {
        "unit", "timestamp", "Week", "sensor", "genotype", "g_alias", "treatment",
        "block", "position", "plant", "date",
    }
    return [c for c in df.columns if c not in meta and pd.api.types.is_numeric_dtype(df[c])]


def rasterise(pts: np.ndarray, field: str, axes: tuple[str, str], res_mm: float = 2.0):
    """Bin points onto a grid and average `field` per cell.

    Returns (grid, extent) with NaN where no point fell. Mean-per-cell rather than
    nearest-point so the image reports what was measured, not an interpolation.
    """
    a = pts[axes[0]].astype(np.float64)
    b = pts[axes[1]].astype(np.float64)
    v = pts[field].astype(np.float64)

    if len(a) == 0:
        return np.full((1, 1), np.nan), (0, 1, 0, 1)

    a_edges = np.arange(a.min(), a.max() + res_mm, res_mm)
    b_edges = np.arange(b.min(), b.max() + res_mm, res_mm)
    if len(a_edges) < 2 or len(b_edges) < 2:
        return np.full((1, 1), np.nan), (a.min(), a.max(), b.min(), b.max())

    total, _, _ = np.histogram2d(a, b, bins=[a_edges, b_edges], weights=v)
    count, _, _ = np.histogram2d(a, b, bins=[a_edges, b_edges])
    with np.errstate(invalid="ignore", divide="ignore"):
        grid = np.where(count > 0, total / count, np.nan)

    extent = (a_edges[0], a_edges[-1], b_edges[0], b_edges[-1])
    return grid.T, extent
