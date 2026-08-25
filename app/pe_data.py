"""Data layer for the PlantEye pipeline app.

Wraps the verified reader in ``tools/planteye_io.py`` and adds the things the app needs on
top of a single tabular export, kept **design-agnostic**: blank detection, discovery of the
several TraitFinder exports (``.csv`` or ``.zip``) that make up one experiment, a union-
across-time merge, value-based overlap detection (never timestamp-based), an optional helper
to split a compound label into named factors, and the optional ground-truth join.

Nothing here assumes a particular experiment. It works from the two labels every PlantEye
export carries — ``genotype`` and ``treatment`` — plus whatever factors the user chooses to
derive. Nothing is imputed or silently merged.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

_TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
from planteye_io import (  # noqa: E402
    PLY_VERTEX_DTYPE,
    read_ply,
    read_tabular,
    ply_inventory,
    parse_manifest_ps1,
    rasterise,
    trait_columns,
)

__all__ = [
    "read_tabular", "ply_inventory", "parse_manifest_ps1", "rasterise", "trait_columns",
    "read_ply", "read_ply_lenient",
    "decode_factors", "is_blank_series", "categorical_factors", "measure_columns",
    "suggest_split", "split_compound",
    "Instance", "discover_instances", "instance_summary",
    "detect_supersession", "union_instances", "overlapping_records",
    "scan_key", "carrier_rescans", "resolve_union", "discover_ply",
    "REFLECTANCE_CHANNELS", "channel_values", "reflectance_distributions", "rasterise_rgb",
    "point_normals", "estimate_pot_geometry", "classify_cloud", "clean_cloud", "filter_mesh",
    "mesh_traits", "portal_trait_comparison", "clean_all_scans", "write_ply_bytes",
    "load_groundtruth", "join_groundtruth", "manual_measures",
]

# Columns that identify a scan rather than describe a treatment or a measurement.
ID_LIKE = {
    "unit", "timestamp", "Week", "sensor", "g_alias", "block", "position", "plant",
    "date", "is_blank", "source_instance",
}
_BLANK_TOKENS = {"", "blank", "empty", "nan", "none", "-", "na", "n/a"}


def is_blank_series(s: pd.Series) -> pd.Series:
    """True where a label marks an empty/blank carrier slot (design-agnostic)."""
    v = s.astype(str).str.strip().str.lower()
    return v.isin(_BLANK_TOKENS)


def decode_factors(df: pd.DataFrame) -> pd.DataFrame:
    """Add only what is universal: an ``is_blank`` flag. Everything else (splitting a
    compound ``treatment``/``genotype`` code into named factors) is opt-in via
    :func:`split_compound`, so the app never guesses another experiment's encoding."""
    out = df.copy()
    g = out["genotype"] if "genotype" in out else pd.Series("", index=out.index)
    t = out["treatment"] if "treatment" in out else pd.Series("", index=out.index)
    out["is_blank"] = is_blank_series(g) | is_blank_series(t)
    return out


def categorical_factors(df: pd.DataFrame, max_levels: int = 60) -> list[str]:
    """Columns that look like grouping factors: text/low-cardinality, not ids or traits."""
    out = []
    for c in df.columns:
        if c in ID_LIKE:
            continue
        s = df[c]
        if pd.api.types.is_numeric_dtype(s):
            continue  # numeric columns are treated as measurements, not factors
        n = s.nunique(dropna=True)
        if 1 <= n <= max_levels:
            out.append(c)
    return out


def measure_columns(df: pd.DataFrame) -> list[str]:
    """Numeric measurement/trait columns (what missing-data and time-series analyse)."""
    return trait_columns(df)


def suggest_split(series: pd.Series,
                  delims=(".", "_", "-", "/", ":", "|", " ")) -> dict | None:
    """If every non-blank value of a label splits into the *same* number of parts by one
    delimiter, suggest that split. Lets the app offer 'treatment 61.BC.HT -> 3 factors'
    without hard-coding any experiment's scheme."""
    s = series.dropna().astype(str).str.strip()
    s = s[~s.str.lower().isin(_BLANK_TOKENS)]
    if s.empty:
        return None
    for d in delims:
        counts = s.str.split(re.escape(d)).map(len)
        if counts.nunique() == 1 and counts.iloc[0] > 1:
            return {"delim": d, "n_parts": int(counts.iloc[0])}
    return None


def split_compound(df: pd.DataFrame, col: str, delim: str,
                   names: list[str] | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Split a compound label column into new factor columns (blank rows -> NaN).
    Returns the augmented frame and the new column names."""
    out = df.copy()
    parts = out[col].astype(str).str.strip().str.split(re.escape(delim), expand=True)
    n = parts.shape[1]
    cols = list(names) if (names and len(names) == n) else [f"{col}_{i+1}" for i in range(n)]
    blank = out["is_blank"] if "is_blank" in out else pd.Series(False, index=out.index)
    for i, c in enumerate(cols):
        out[c] = parts[i].where(~blank, np.nan)
    return out, cols


# Face record: 1 uchar vertex-count + 3 int32 indices (PlantEye meshes are all triangles).
_PLY_FACE_DTYPE = np.dtype([("n", "u1"), ("i", "<i4", 3)])


def read_ply_lenient(path: str | Path):
    """Read a PLY's vertex block AND the triangulated face block, without requiring the
    `<block>_<UTC>_full.ply` filename. Returns ``(vertices, faces, block, utc)`` where ``faces``
    is an (M, 3) int array of vertex indices, or None if the file has no faces. PlantEye meshes
    are all clean triangles (verified); a non-triangle face block is treated as absent."""
    path = Path(path)
    raw = path.read_bytes()
    idx = raw.find(b"end_header\n")
    if idx < 0:
        raise ValueError(f"{path.name}: no end_header found; not a PLY?")
    hdr_end = idx + len(b"end_header\n")
    header = raw[:idx].decode("ascii", errors="replace")
    if b"binary_little_endian" not in raw[:64]:
        raise ValueError(f"{path.name}: expected binary_little_endian PLY")
    m = re.search(r"element vertex (\d+)", header)
    if not m:
        raise ValueError(f"{path.name}: no vertex count in header")
    n = int(m.group(1))
    verts = np.frombuffer(raw, dtype=PLY_VERTEX_DTYPE, count=n, offset=hdr_end)

    faces = None
    fm = re.search(r"element face (\d+)", header)
    if fm and int(fm.group(1)) > 0:
        nf = int(fm.group(1))
        face_off = hdr_end + n * PLY_VERTEX_DTYPE.itemsize
        if len(raw) - face_off >= nf * _PLY_FACE_DTYPE.itemsize:  # all-triangle assumption holds
            frec = np.frombuffer(raw, dtype=_PLY_FACE_DTYPE, count=nf, offset=face_off)
            if (frec["n"] == 3).all():
                faces = frec["i"].astype(np.int64)

    bm = re.match(r"^(\d+)_", path.name)
    tm = re.search(r"(\d{8}T\d{6})", path.name)
    block = int(bm.group(1)) if bm else None
    utc = pd.to_datetime(tm.group(1), format="%Y%m%dT%H%M%S") if tm else None
    return verts, faces, block, utc


# ---------------------------------------------------------------- point-cloud cleaning

# The reflectance channels the viewer/distributions expose. 'greenness' = green - red, the
# cleanest soil-vs-tissue separator found on the real data.
REFLECTANCE_CHANNELS = ("ndvi", "greenness", "nir", "red", "green", "blue", "hue")


def channel_values(pts: np.ndarray, ch: str) -> np.ndarray:
    """Per-point values for a named reflectance channel (handles the derived 'greenness')."""
    if ch == "greenness":
        return pts["green"].astype(np.float64) - pts["red"].astype(np.float64)
    return pts[ch].astype(np.float64)


def _xyz(pts: np.ndarray) -> np.ndarray:
    return np.column_stack([pts["x"], pts["y"], pts["z"]]).astype(np.float64)


def point_normals(pts: np.ndarray, k: int = 20):
    """Per-point surface normal + planarity via local PCA over k nearest neighbours (scipy
    cKDTree, batched eigh). Soil-plane points get near-vertical normals (|nz|≈1); pot rim/wall
    points get near-horizontal/radial normals; plant tissue gets varied normals."""
    from scipy.spatial import cKDTree
    xyz = _xyz(pts)
    if len(xyz) < 4:
        return np.tile([0, 0, 1.0], (len(xyz), 1)), np.zeros(len(xyz))
    k = min(k, len(xyz))
    _, idx = cKDTree(xyz).query(xyz, k=k)
    nb = xyz[idx]                                   # (N, k, 3)
    c = nb - nb.mean(axis=1, keepdims=True)
    cov = np.einsum("nki,nkj->nij", c, c) / k       # (N, 3, 3)
    w, v = np.linalg.eigh(cov)                       # ascending eigenvalues
    normals = v[:, :, 0]                             # smallest-eigenvalue eigenvector
    planarity = (w[:, 1] - w[:, 0]) / (w[:, 2] + 1e-9)
    return normals, planarity


def soil_plane_z(pts: np.ndarray) -> float:
    """The soil-surface height as the **peak of the low-z distribution** over the whole cloud.
    Pooling every pot's exposed soil into one histogram is robust to per-pot occlusion (a pot whose
    soil is hidden by foliage would give a falsely-high local estimate). All pots sit on one level
    bench, so this shared plane applies to each."""
    z = pts["z"].astype(np.float64)
    lo = z[z <= np.percentile(z, 40)]
    if len(lo) < 10:
        return float(np.percentile(z, 5))
    hist, edges = np.histogram(lo, bins=40)
    i = int(np.argmax(hist))
    return float((edges[i] + edges[i + 1]) / 2.0)


def estimate_pot_geometry(pts: np.ndarray, pot_radius: float | None = None,
                          soil_z: float | None = None) -> dict:
    """Pot geometry for a single pot's points: a **robust centre** (median x,y — stable even when
    the plant occludes the soil), the soil-surface height, and a radius. ``soil_z`` should be the
    carrier-wide :func:`soil_plane_z` (robust to occlusion); if None it falls back to this pot's low
    percentile. ``pot_radius`` (recommended — pots are a fixed, known size) is used directly if
    given, else auto-estimated from the point spread."""
    xyz = _xyz(pts)
    x, y, z = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    cx, cy = float(np.median(x)), float(np.median(y))       # robust centre
    zmin = float(np.percentile(z, 1))
    sz = float(soil_z) if soil_z is not None else float(np.percentile(z, 5))
    if pot_radius:
        radius = float(pot_radius)
    else:
        r = np.hypot(x - cx, y - cy)
        radius = float(np.percentile(r, 92))                # plant spreads ~ to the pot edge
    return {"cx": cx, "cy": cy, "radius": radius, "soil_z": sz,
            "zmin": zmin, "rim_z": zmin + 20.0}


def classify_cloud(pts: np.ndarray, geom: dict | None = None, strictness: float = 1.0,
                   normals=None, planarity=None, rim_margin: float = 15.0,
                   soil_ceiling: float = 5.0, pot_radius: float | None = None,
                   soil_z: float | None = None):
    """Label every point **plant / soil / rim** by geometry. Returns (labels, geom).

    **Soil = a negative cylinder** centred on the pot: everything within
    ``radius − rim_margin`` of the pot centre and at or below ``soil surface + soil_ceiling`` mm.
    This is **pure geometry — no greenness gate** — so **green moss/algae on the soil is removed**
    too (a reflectance test cannot, because moss is green). The ``rim_margin`` keeps the cylinder
    *inside* the pot rim, so plant parts overhanging the edge are never clipped; ``soil_ceiling``
    (a few mm) sets how far above the soil surface to cut, so the canopy is kept.

    **Rim** = the pot's rim ring — near the pot radius, low, with a radial (non-vertical) normal.
    """
    if geom is None:
        geom = estimate_pot_geometry(pts, pot_radius=pot_radius, soil_z=soil_z)
    xyz = _xyz(pts)
    z = xyz[:, 2]
    r = np.hypot(xyz[:, 0] - geom["cx"], xyz[:, 1] - geom["cy"])
    labels = np.full(len(pts), "plant", dtype=object)

    cyl_r = max(geom["radius"] - rim_margin, 1.0)
    ceiling = geom["soil_z"] + soil_ceiling
    soil = (r <= cyl_r) & (z <= ceiling)             # the negative cylinder (moss-proof)
    labels[soil] = "soil"

    if normals is None:
        normals, planarity = point_normals(pts)
    nz = np.abs(normals[:, 2])
    rim_band = (0.05 + 0.05 * strictness) * max(geom["radius"], 1.0)
    rim = ((np.abs(r - geom["radius"]) <= rim_band) & (nz < 0.55)
           & (z <= geom["soil_z"] + 40.0) & (labels == "plant"))
    labels[rim] = "rim"
    return labels, geom


def _drop_tiny_components(pts: np.ndarray, keep: np.ndarray, res_mm: float = 4.0,
                          min_voxels: int = 4) -> np.ndarray:
    """Sweep only *tiny* isolated blobs of kept points (stray granules) via voxel connected
    components, per unit_id. Deliberately small threshold so leaf tips survive."""
    from scipy import ndimage
    out = keep.copy()
    xyz = _xyz(pts)
    for u in np.unique(pts["unit_id"]):
        m = (pts["unit_id"] == u) & keep
        if m.sum() < min_voxels:
            continue
        p = xyz[m]
        gi = np.floor((p - p.min(0)) / res_mm).astype(int)
        dims = gi.max(0) + 1
        grid = np.zeros(dims, dtype=bool)
        grid[gi[:, 0], gi[:, 1], gi[:, 2]] = True
        lab, n = ndimage.label(grid, structure=np.ones((3, 3, 3)))
        sizes = np.bincount(lab.ravel())
        small = {c for c in range(1, n + 1) if sizes[c] < min_voxels}
        if small:
            bad = np.array([lab[tuple(g)] in small for g in gi])
            out[np.where(m)[0][bad]] = False
    return out


def clean_cloud(pts: np.ndarray, strictness: float = 1.0, geom: dict | None = None,
                use_geometry: bool = True, small_components: bool = True,
                ndvi_min: float = 0.4, gr_min: float = 8.0,
                rim_margin: float = 15.0, soil_ceiling: float = 5.0,
                pot_radius: float | None = None):
    """Classify soil/pot vs plant and return ``(plant_mask, labels, geoms)`` — geometry, computed
    **per pot** (``unit_id``) so each pot's centre/radius/rim is its own. Soil is a **negative
    cylinder** (moss-proof, no greenness — see :func:`classify_cloud`): ``rim_margin`` keeps it
    inside the rim (no plant loss at the edge), ``soil_ceiling`` sets how far above the surface to
    cut. ``plant_mask`` is a boolean over the input order (so faces filter with it). ``geoms`` maps
    unit_id → geometry dict. Falls back to the colour gate when ``use_geometry`` is False."""
    if use_geometry and len(pts) >= 4:
        normals, planarity = point_normals(pts)
        gsoil = soil_plane_z(pts)                    # one robust soil plane for the whole carrier
        labels = np.full(len(pts), "plant", dtype=object)
        geoms = {}
        for u in np.unique(pts["unit_id"]):
            m = np.where(pts["unit_id"] == u)[0]
            g = None if geom is None else geom.get(int(u)) if isinstance(geom, dict) else geom
            lab, g = classify_cloud(pts[m], g, strictness, normals[m], planarity[m],
                                    rim_margin=rim_margin, soil_ceiling=soil_ceiling,
                                    pot_radius=pot_radius, soil_z=gsoil)
            labels[m] = lab
            geoms[int(u)] = g
        geom = geoms
    else:
        ndvi = pts["ndvi"].astype(np.float64)
        gr = pts["green"].astype(np.float64) - pts["red"].astype(np.float64)
        labels = np.where((ndvi > ndvi_min) & (gr > gr_min), "plant", "soil").astype(object)
        geom = geom if isinstance(geom, dict) else {}
    plant_mask = labels == "plant"
    if small_components and plant_mask.any():
        plant_mask = _drop_tiny_components(pts, plant_mask)
        labels = np.where(plant_mask, labels, np.where(labels == "plant", "soil", labels))
    return plant_mask, labels, geom


def filter_mesh(verts: np.ndarray, faces, plant_mask: np.ndarray):
    """Keep only triangles whose 3 vertices are all plant, and reindex to the kept vertices.
    Returns (kept_verts, kept_faces|None)."""
    kept_verts = verts[plant_mask]
    if faces is None:
        return kept_verts, None
    remap = -np.ones(len(verts), dtype=np.int64)
    remap[np.where(plant_mask)[0]] = np.arange(int(plant_mask.sum()))
    keep_face = plant_mask[faces].all(axis=1)
    new_faces = remap[faces[keep_face]]
    return kept_verts, new_faces


def mesh_traits(verts: np.ndarray, faces, plant_mask: np.ndarray | None = None) -> pd.DataFrame:
    """Per-plant (``unit_id``) traits from the **triangulated mesh**: surface/projected leaf area
    (Σ triangle areas), convex-hull volume & area (scipy), and height. Optionally restricted to
    plant triangles via ``plant_mask``. Clearly app-derived; never overwrites portal values."""
    if faces is None or len(faces) == 0:
        return pd.DataFrame()
    xyz = _xyz(verts)
    uid = verts["unit_id"]
    f = faces
    if plant_mask is not None:
        f = faces[plant_mask[faces].all(axis=1)]
    rows = []
    for u in np.unique(uid):
        fu = f[(uid[f] == u).all(axis=1)]
        if len(fu) == 0:
            continue
        v0, v1, v2 = xyz[fu[:, 0]], xyz[fu[:, 1]], xyz[fu[:, 2]]
        area = 0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0), axis=1).sum()
        pu = xyz[np.unique(fu)]
        hv = ha = np.nan
        if len(pu) >= 4:
            try:
                from scipy.spatial import ConvexHull
                h = ConvexHull(pu)
                hv, ha = h.volume, h.area
            except Exception:
                pass
        z = pu[:, 2]
        rows.append({
            "unit_id": int(u), "n_triangles": int(len(fu)),
            "mesh_surface_area_mm2": round(float(area), 1),
            "convex_hull_volume_mm3": round(float(hv), 1),
            "convex_hull_area_mm2": round(float(ha), 1),
            "mesh_height_mm": round(float(np.percentile(z, 99) - np.percentile(z, 1)), 1),
        })
    return pd.DataFrame(rows)


def _find_col(df, *keys):
    for c in df.columns:
        cl = c.lower()
        if all(k in cl for k in keys):
            return c
    return None


def portal_trait_comparison(mtraits: pd.DataFrame, verts: np.ndarray,
                            tabular: pd.DataFrame, block: int, local_dt) -> pd.DataFrame:
    """Join the mesh traits to PlantEye's portal traits for the same plant so the user can
    compare and decide. Maps ``unit_id`` → carrier position by y-centroid order, then to the
    tabular row (block:position on the matching date)."""
    if mtraits.empty:
        return pd.DataFrame()
    uid = verts["unit_id"]
    y = verts["y"].astype(np.float64)
    order = sorted(np.unique(uid), key=lambda u: y[uid == u].mean())
    uid2pos = {int(u): i + 1 for i, u in enumerate(order)}

    tb = tabular[tabular["block"] == block] if "block" in tabular else tabular.iloc[0:0]
    if local_dt is not None and "date" in tb:
        d = pd.Timestamp(local_dt).normalize()
        same = tb[tb["date"] == d]
        if len(same):
            tb = same
    c_proj = _find_col(tb, "projected", "leaf", "area")
    c_3d = _find_col(tb, "3d", "leaf", "area")
    c_hull = _find_col(tb, "convex", "hull", "area")
    c_h = _find_col(tb, "plant", "height", "max") or _find_col(tb, "plant", "height")

    def portal(pos, col):
        if not col:
            return np.nan
        row = tb[tb["position"] == pos]
        return float(row[col].iloc[0]) if len(row) else np.nan

    rows = []
    for _, mt in mtraits.iterrows():
        pos = uid2pos.get(int(mt["unit_id"]))
        rows.append({
            "unit_id": mt["unit_id"], "position": pos,
            "mesh_surface_area": mt["mesh_surface_area_mm2"],
            "portal_projected_leaf_area": portal(pos, c_proj),
            "portal_3d_leaf_area": portal(pos, c_3d),
            "mesh_hull_volume": mt["convex_hull_volume_mm3"],
            "portal_hull_area": portal(pos, c_hull),
            "mesh_height": mt["mesh_height_mm"],
            "portal_height": portal(pos, c_h),
        })
    return pd.DataFrame(rows)


def clean_all_scans(inventory: pd.DataFrame, strictness: float = 1.0,
                    out_dir: str | Path | None = None, progress=None,
                    rim_margin: float = 15.0, soil_ceiling: float = 5.0,
                    pot_radius: float | None = None) -> pd.DataFrame:
    """Batch: run the geometry cleaner over a whole-experiment PLY inventory (from
    :func:`discover_ply`), optionally writing a cleaned **mesh** `.ply` per scan to ``out_dir``
    (non-destructive). Returns a per-scan log of points/faces removed."""
    logs = []
    n = len(inventory)
    for i, (_, row) in enumerate(inventory.iterrows()):
        try:
            verts, faces, blk, utc = read_ply_lenient(row["path"])
            plant_mask, labels, geom = clean_cloud(verts, strictness=strictness,
                                                   rim_margin=rim_margin, soil_ceiling=soil_ceiling,
                                                   pot_radius=pot_radius)
            kv, kf = filter_mesh(verts, faces, plant_mask)
            if out_dir:
                Path(out_dir).mkdir(parents=True, exist_ok=True)
                (Path(out_dir) / f"{Path(row['path']).stem}_cleaned.ply").write_bytes(
                    write_ply_bytes(kv, kf))
            logs.append({"scan": Path(row["path"]).name, "block": blk,
                         "points_in": len(verts), "points_kept": int(plant_mask.sum()),
                         "removed": int((~plant_mask).sum())})
        except Exception as e:
            logs.append({"scan": Path(row["path"]).name, "block": row.get("block"),
                         "points_in": np.nan, "points_kept": np.nan, "removed": f"ERROR: {e}"})
        if progress:
            progress((i + 1) / max(n, 1))
    return pd.DataFrame(logs)


def reflectance_distributions(raw_pts: np.ndarray, clean_pts: np.ndarray,
                              channels=REFLECTANCE_CHANNELS):
    """Summarise how cleaning shifts each reflectance trait's density: raw vs clean median and
    the shift, plus the % of points removed. The UI plots the full histograms from
    :func:`channel_values`; this returns the summary table."""
    rows = []
    for ch in channels:
        r = channel_values(raw_pts, ch)
        c = channel_values(clean_pts, ch) if len(clean_pts) else np.array([np.nan])
        rows.append({
            "channel": ch,
            "raw_median": round(float(np.median(r)), 3),
            "clean_median": round(float(np.nanmedian(c)), 3),
            "shift": round(float(np.nanmedian(c) - np.median(r)), 3),
        })
    pct_removed = round(100 * (1 - len(clean_pts) / len(raw_pts)), 1) if len(raw_pts) else 0.0
    return pd.DataFrame(rows), pct_removed


def rasterise_rgb(pts: np.ndarray, axes=("x", "y"), res_mm: float = 2.0,
                  brighten: float = 1.0, gamma: float = 1.0):
    """Rasterise the red/green/blue channels into one true-colour image (H×W×3 in [0,1]).
    ``brighten`` (>1) and ``gamma`` (>1) lighten it for the 'Enhanced RGB' panel. Empty cells
    are black. Returns (image, extent) with the cloud's true extent (pad at plot time)."""
    gr, ext = rasterise(pts, "red", axes, res_mm)
    gg, _ = rasterise(pts, "green", axes, res_mm)
    gb, _ = rasterise(pts, "blue", axes, res_mm)
    img = np.zeros((*gr.shape, 3), dtype=np.float64)
    for k, g in enumerate((gr, gg, gb)):
        img[:, :, k] = np.nan_to_num(g, nan=0.0)
    img = np.clip(img / 255.0, 0, 1)
    if brighten != 1.0:
        img = np.clip(img * brighten, 0, 1)
    if gamma and gamma != 1.0:
        img = np.clip(img, 0, 1) ** (1.0 / gamma)
    return img, ext


def turntable_gif(pts: np.ndarray, faces=None, *, color_by="rgb", up_axis="z",
                  turns: int = 1, n_frames: int = 60, fps: int = 20, size: int = 480,
                  elev: float = 18.0, margin: float = 1.15, bg: str = "white",
                  point_size: float = 2.0, brighten: float = 1.6,
                  max_points: int = 60000, progress=None) -> bytes:
    """Render a looping **turntable animation** of a point cloud (or mesh) → animated-GIF bytes.

    A companion to the standalone *3d-turntable-animator* (github.com/agyeyapratap-sketch/
    3d-turntable-animator): the framing follows its reference ``render_turntable.py`` — centre
    the geometry, fit the camera from the bounding sphere and a margin, and orbit about the
    chosen up-axis — but rendering here uses **matplotlib (Agg) + Pillow** instead of
    Open3D/ffmpeg, so it runs headless, needs no extra dependency, and reuses the app's
    existing 3D machinery. The camera (not the data) orbits: elevation is held and azimuth
    sweeps ``360×turns`` degrees, so the loop is seamless.

    ``color_by`` — ``'rgb'`` (true colour, brightened) or any spectral channel the app
    already renders: ``'ndvi'``, ``'greenness'``, ``'nir'``, ``'nir940'``, ``'hue'``,
    ``'intensity'``, plus ``'height'``.
    ``up_axis`` — which data axis stands up / is spun about (``'z'`` for an upright plant).
    ``faces`` — if an (M,3) index array is given, the triangulated surface is spun instead of
    points. Returns GIF bytes for ``st.image`` and a download button.
    """
    import io
    import mpl_toolkits.mplot3d  # noqa: F401  (registers the '3d' projection)
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib import colormaps, colors as mcolors
    from PIL import Image

    is_mesh = faces is not None and len(faces) > 0
    xyz = _xyz(pts)
    if len(xyz) == 0:
        raise ValueError("no points to animate")

    # decimate a dense *point* cloud for responsiveness (the reference defers this too);
    # a mesh keeps every vertex so its face indices stay valid.
    src = pts
    if not is_mesh and max_points and len(xyz) > max_points:
        sel = np.random.default_rng(0).choice(len(xyz), int(max_points), replace=False)
        xyz, src = xyz[sel], pts[sel]

    # stand the requested axis up (matplotlib's vertical is z), then centre on the bounding box
    order = {"x": (1, 2, 0), "y": (2, 0, 1), "z": (0, 1, 2)}[up_axis]
    xyz = xyz[:, order]
    xyz = xyz - (xyz.min(axis=0) + xyz.max(axis=0)) / 2.0
    # Frame so the plant fills the frame vertically without ever clipping as it spins: the
    # horizontal half-extent is the in-plane radius (the widest the silhouette gets at any
    # azimuth), the vertical half-extent is the height. A matching box_aspect keeps it true-scale.
    rh = (float(np.hypot(xyz[:, 0], xyz[:, 1]).max()) or 1.0) * float(margin)
    rz = (float(np.abs(xyz[:, 2]).max()) or 1.0) * float(margin)

    # colours, matched to the rest of the app — true colour or any spectral channel
    _CH_CMAP = {"ndvi": "RdYlGn", "greenness": "RdYlGn", "nir": "inferno",
                "nir940": "inferno", "hue": "hsv", "intensity": "gray",
                "height": "YlGn"}
    facecolors = None
    if color_by == "rgb" and all(c in src.dtype.names for c in ("red", "green", "blue")):
        rgb = np.column_stack([src["red"], src["green"], src["blue"]]).astype(float) / 255.0
        col = np.clip(rgb * float(brighten), 0, 1)
    else:
        vals = xyz[:, 2] if color_by == "height" else channel_values(src, color_by)
        vlo, vhi = np.percentile(vals, [2, 98]) if len(vals) > 1 else (0.0, 1.0)
        norm = mcolors.Normalize(vlo, vhi if vhi > vlo else vlo + 1)
        col = colormaps[_CH_CMAP.get(color_by, "viridis")](norm(vals))
    if is_mesh:
        facecolors = col  # per-vertex; trisurf averages to faces

    dark = bg == "black"
    fig = Figure(figsize=(size / 100.0, size / 100.0), dpi=100,
                 facecolor="black" if dark else "white")
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111, projection="3d")
    ax.set_facecolor("black" if dark else "white")

    if is_mesh:
        ax.plot_trisurf(xyz[:, 0], xyz[:, 1], xyz[:, 2], triangles=np.asarray(faces),
                        color="#7cbf6b" if facecolors is None else None,
                        linewidth=0, antialiased=False, shade=facecolors is None)
        # per-vertex colour → average onto triangles (plot_trisurf can't take vertex colours)
        if facecolors is not None:
            tri = ax.collections[-1]
            tri.set_fc(facecolors[np.asarray(faces)].mean(axis=1))
    else:
        ax.scatter(xyz[:, 0], xyz[:, 1], xyz[:, 2], c=col, s=float(point_size),
                   linewidths=0, depthshade=False)

    ax.set_box_aspect((rh, rh, rz))
    ax.set_xlim(-rh, rh); ax.set_ylim(-rh, rh); ax.set_zlim(-rz, rz)
    ax.set_axis_off()

    frames = []
    n_frames = max(2, int(n_frames))
    for i in range(n_frames):
        ax.view_init(elev=float(elev), azim=-60.0 + 360.0 * int(turns) * i / n_frames)
        fig.canvas.draw()
        frames.append(Image.frombytes(
            "RGBA", fig.canvas.get_width_height(),
            bytes(fig.canvas.buffer_rgba())).convert("RGB"))
        if progress:
            progress((i + 1) / n_frames)

    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:],
                   loop=0, duration=int(round(1000.0 / max(1, int(fps)))),
                   disposal=2, optimize=True)
    return buf.getvalue()


_PLY_HDR_TYPE = {"float32": "float", "uint8": "uchar", "uint16": "ushort", "int32": "int"}


def write_ply_bytes(verts: np.ndarray, faces=None) -> bytes:
    """Serialise a (cleaned) vertex array back to a binary_little_endian PLY. If ``faces`` (an
    (M,3) index array, already reindexed to ``verts``) is given, the triangulated **mesh** is
    preserved so the cleaned cloud is re-processable. Non-destructive to the original."""
    props = [f"property {_PLY_HDR_TYPE[str(verts.dtype[n])]} {n}" for n in verts.dtype.names]
    header = "ply\nformat binary_little_endian 1.0\n" + f"element vertex {len(verts)}\n" + \
             "\n".join(props) + "\n"
    if faces is not None and len(faces):
        header += f"element face {len(faces)}\nproperty list uchar int vertex_index\n"
    header += "end_header\n"
    out = header.encode("ascii") + np.ascontiguousarray(verts).tobytes()
    if faces is not None and len(faces):
        frec = np.empty(len(faces), dtype=_PLY_FACE_DTYPE)
        frec["n"] = 3
        frec["i"] = np.asarray(faces, dtype=np.int32)
        out += frec.tobytes()
    return out


# --------------------------------------------------------------------------- instances

@dataclass
class Instance:
    """One TraitFinder export (a `*_PlantEye.csv` or its `.zip`) with its decoded scans."""
    name: str
    path: Path
    df: pd.DataFrame
    is_stale: bool = False
    superset_of: list = field(default_factory=list)

    @property
    def kind(self) -> str:
        return self.path.suffix.lower().lstrip(".")

    @property
    def dates(self) -> set:
        return set(self.df["date"].dropna().unique())

    @property
    def keyset(self) -> set:
        return set(zip(self.df["block"], self.df["date"]))


def _instance_name(p: Path, root: Path) -> str:
    """A short, stable name for an export: its export folder, else the file stem."""
    stem = p.stem.replace("_PlantEye", "").replace("Tabular-data_", "")
    for parent in (p.parent, p.parent.parent):
        if parent != root and parent.name and "tabular" not in parent.name.lower():
            return parent.name
    return stem


def _signature(df: pd.DataFrame):
    """Content fingerprint so a `.zip` and its extracted `.csv` collapse to one instance."""
    return (len(df), int(df["block"].min()), int(df["block"].max()),
            str(df["date"].min()), str(df["date"].max()), int(df["date"].nunique()))


def discover_instances(folder: str | Path) -> list[Instance]:
    """Find every tabular PlantEye export (``.csv`` or ``.zip``) under a folder and decode it.

    Recurses (so `old/` re-exports are found). Deduplicates by content so a zip and its
    extracted csv are one instance (csv preferred). Ordered by first scan date.
    """
    folder = Path(folder)
    candidates: list[Path] = []
    for p in sorted(folder.rglob("*")):
        low = p.name.lower()
        if p.suffix.lower() == ".csv" and "planteye" in low and not low.endswith("_raw_data.csv"):
            candidates.append(p)
        elif p.suffix.lower() == ".zip":
            candidates.append(p)

    by_sig: dict = {}
    order: list = []
    for p in candidates:
        try:
            df = decode_factors(read_tabular(p))
        except Exception:
            continue
        sig = _signature(df)
        if sig in by_sig:  # same content already seen — prefer the csv copy
            ins = by_sig[sig]
            if p.suffix.lower() == ".csv" and ins.path.suffix.lower() == ".zip":
                ins.path = p
                ins.name = _instance_name(p, folder)
            continue
        ins = Instance(name=_instance_name(p, folder), path=p, df=df)
        by_sig[sig] = ins
        order.append(ins)

    order.sort(key=lambda i: min(i.dates) if i.dates else pd.Timestamp.max)
    _mark_supersession(order)
    return order


def _mark_supersession(insts: list[Instance]) -> None:
    """Flag any instance whose (block, date) records are a strict subset of another's."""
    for a in insts:
        for b in insts:
            if a is b or not a.keyset:
                continue
            if a.keyset < b.keyset:
                a.is_stale = True
                b.superset_of.append(a.name)
                break


def detect_supersession(insts: list[Instance]) -> list[tuple]:
    """(stale, superset) name pairs where one export's records are a strict subset of another."""
    return [(a.name, b.name) for a in insts for b in insts
            if a is not b and a.keyset and a.keyset < b.keyset]


def instance_summary(insts: list[Instance]) -> pd.DataFrame:
    """One row per export: coverage, scale, and an advisory note."""
    max_real = max((int((~i.df["is_blank"]).sum()) for i in insts), default=0)
    rows = []
    for ins in insts:
        d = sorted(ins.dates)
        real = int((~ins.df["is_blank"]).sum())
        overnight = False
        if ins.df["timestamp"].notna().any():
            overnight = any(g["timestamp"].dt.hour.max() - g["timestamp"].dt.hour.min() > 6
                            for _, g in ins.df.groupby("date"))
        note = ""
        if ins.is_stale:
            note = "older re-export of another export (subset) — dropped by default"
        elif max_real and (real < 0.25 * max_real or overnight):
            note = "small or overnight batch — possibly a setup/test run; review before including"
        rows.append({
            "export": ins.name,
            "type": ins.kind,
            "rows": len(ins.df),
            "real_scans": real,
            "blanks": len(ins.df) - real,
            "dates": len(d),
            "first": d[0].strftime("%Y-%m-%d") if d else "",
            "last": d[-1].strftime("%Y-%m-%d") if d else "",
            "carriers": ins.df["block"].nunique(),
            "default": "drop" if ins.is_stale else "include",
            "note": note,
        })
    return pd.DataFrame(rows)


def overlapping_records(insts: list[Instance]) -> pd.DataFrame:
    """Find (unit, date) records that appear in more than one *kept* export, and compare the
    records **by value** (not by timestamp). Reports whether the copies are identical (safe to
    merge) or differ (genuinely different scans — the union keeps the fuller scan of the day).
    """
    kept = [i for i in insts if not i.is_stale]
    if len(kept) < 2:
        return pd.DataFrame(columns=["unit", "date", "exports", "records"])

    shared = None
    frames = []
    for ins in kept:
        d = ins.df.copy()
        d["__export"] = ins.name
        frames.append(d)
        tc = set(trait_columns(ins.df))
        shared = tc if shared is None else (shared & tc)
    alld = pd.concat(frames, ignore_index=True)
    tc = sorted(shared or [])

    counts = alld.groupby(["unit", "date"])["__export"].transform("nunique")
    over = alld[counts > 1]
    if over.empty:
        return pd.DataFrame(columns=["unit", "date", "exports", "records"])

    rows = []
    for (unit, date), sub in over.groupby(["unit", "date"]):
        if sub["__export"].nunique() < 2:
            continue
        vals = sub[tc].round(3) if tc else sub[[]]
        identical = bool(len(tc)) and (vals.nunique(dropna=False) <= 1).all()
        rows.append({
            "unit": unit,
            "date": pd.Timestamp(date).strftime("%Y-%m-%d"),
            "exports": ", ".join(sorted(sub["__export"].unique())),
            "records": "identical — safe to merge" if identical
                       else "differ — different scans (union keeps the fuller day)",
        })
    return pd.DataFrame(rows)


def union_instances(insts: list[Instance], drop: tuple = ()) -> pd.DataFrame:
    """Union kept exports across time; where the same (unit, date) appears twice, keep the row
    from the export that scanned the most units that day (the fuller scan). Stale re-exports
    and anything in ``drop`` are excluded. Adds ``source_instance`` for provenance."""
    frames = []
    for ins in insts:
        if ins.is_stale or ins.name in drop:
            continue
        d = ins.df.copy()
        d["source_instance"] = ins.name
        frames.append(d)
    if not frames:
        return pd.DataFrame()
    alld = pd.concat(frames, ignore_index=True)
    day_scale = alld.groupby(["source_instance", "date"])["unit"].transform("size")
    alld = alld.assign(_scale=day_scale)
    alld = (alld.sort_values("_scale", ascending=False)
                .drop_duplicates(["unit", "date"], keep="first")
                .drop(columns="_scale")
                .sort_values(["date", "block", "position"])
                .reset_index(drop=True))
    return alld


# ----------------------------------------------------------------- curation / resolve

def scan_key(source_instance, unit, timestamp) -> tuple:
    """The identity of one physical scan row: (export, unit, timestamp). Used as the key of
    the user's per-record decisions so nothing is resolved silently."""
    ts = pd.Timestamp(timestamp)
    return (str(source_instance), str(unit), ts.isoformat())


def carrier_rescans(df: pd.DataFrame) -> pd.DataFrame:
    """A whole *carrier* (barcode / block) scanned two or more times on the same day — against
    the normal once-per-day trend. Keyed on (block, date) counting **distinct timestamps**, so
    it catches carrier-level rescans that the (unit, date) checks miss. Returns each carrier/day
    with the competing scan times, per-time unit counts, and which export each came from."""
    if "date" not in df or "timestamp" not in df:
        return pd.DataFrame(columns=["block", "date", "n_scans", "scans"])
    nts = df.groupby(["block", "date"])["timestamp"].nunique()
    multi = nts[nts > 1].index
    rows = []
    for block, date in multi:
        sub = df[(df["block"] == block) & (df["date"] == date)]
        parts = []
        for ts, s in sub.groupby("timestamp"):
            src = s["source_instance"].iloc[0] if "source_instance" in s else ""
            parts.append(f"{pd.Timestamp(ts).strftime('%H:%M:%S')} — {s['unit'].nunique()} units"
                         + (f" [{src}]" if src else ""))
        rows.append({
            "block": int(block),
            "date": pd.Timestamp(date).strftime("%Y-%m-%d"),
            "n_scans": int(sub["timestamp"].nunique()),
            "scans": " ; ".join(sorted(parts)),
        })
    return pd.DataFrame(rows)


def resolve_union(insts: list[Instance], decisions: dict | None = None,
                  drop: tuple = ()) -> pd.DataFrame:
    """Like :func:`union_instances`, but honours the user's per-record ``decisions`` instead of
    deciding silently. ``decisions`` maps ``scan_key(...) -> {action, new_label?, field?, note?}``:

    - ``drop``    — remove that scan entirely.
    - ``relabel`` — overwrite ``field`` (default ``treatment``) with ``new_label`` for that scan.
    - ``keep``    — on a (unit, date) collision, prefer that scan over the automatic default.

    With no decision for a collision, the automatic default is the fuller-scan-of-the-day rule
    (identical to ``union_instances``), so the app *suggests* and the user *overrides*.
    """
    decisions = decisions or {}
    frames = []
    for ins in insts:
        if ins.is_stale or ins.name in drop:
            continue
        d = ins.df.copy()
        d["source_instance"] = ins.name
        frames.append(d)
    if not frames:
        return pd.DataFrame()
    alld = pd.concat(frames, ignore_index=True)
    alld["scan_key"] = [scan_key(si, u, t) for si, u, t
                        in zip(alld["source_instance"], alld["unit"], alld["timestamp"])]

    # apply drops
    dropped = {k for k, v in decisions.items() if v.get("action") == "drop"}
    if dropped:
        alld = alld[~alld["scan_key"].isin(dropped)].copy()
    # apply relabels
    for k, v in decisions.items():
        if v.get("action") == "relabel" and v.get("new_label") is not None:
            field = v.get("field", "treatment")
            if field in alld.columns:
                alld.loc[alld["scan_key"] == k, field] = v["new_label"]
    # a relabel may change blank status
    if {"genotype", "treatment"} & set(alld.columns):
        g = alld["genotype"] if "genotype" in alld else pd.Series("", index=alld.index)
        t = alld["treatment"] if "treatment" in alld else pd.Series("", index=alld.index)
        alld["is_blank"] = is_blank_series(g) | is_blank_series(t)

    # resolve (unit, date) collisions: user 'keep' preference wins, else fuller-day default
    prefer = {k for k, v in decisions.items() if v.get("action") == "keep"}
    alld["_pref"] = alld["scan_key"].isin(prefer).astype(int)
    alld["_scale"] = alld.groupby(["source_instance", "date"])["unit"].transform("size")
    alld = (alld.sort_values(["_pref", "_scale"], ascending=False)
                .drop_duplicates(["unit", "date"], keep="first")
                .drop(columns=["_pref", "_scale", "scan_key"])
                .sort_values(["date", "block", "position"])
                .reset_index(drop=True))
    return alld


# --------------------------------------------------------------------- PLY discovery

_PLY_FULL = re.compile(r"^(\d+)_(\d{8}T\d{6})_full\.ply$", re.I)
_PLY_UNIT = re.compile(r"^(\d+)_(\d{8}T\d{6})_unitfile_(\d+)_(\d+)\.ply$", re.I)


def discover_ply(folder: str | Path, offset_hours: int = 8) -> pd.DataFrame:
    """Recursively find every `.ply` for an experiment (spread across download folders like the
    tabular exports) and dedup to one record per scan. Handles both `<block>_<UTC>_full.ply`
    (whole carrier, richer) and `<block>_<UTC>_unitfile_N_M.ply` (per-plant split); on the same
    `(block, UTC)` the `_full` file wins (the split is redundant). Genuine retakes (same block,
    different UTC) are kept. Returns path, block, utc, local time, kind, size."""
    folder = Path(folder)
    recs = []
    for p in folder.rglob("*.ply"):
        m = _PLY_FULL.match(p.name)
        kind = "full"
        if not m:
            m = _PLY_UNIT.match(p.name)
            kind = "unitfile"
        if not m:
            continue
        utc = pd.to_datetime(m.group(2), format="%Y%m%dT%H%M%S")
        try:
            mb = round(p.stat().st_size / 1024**2, 2)
        except OSError:
            mb = float("nan")
        recs.append({"path": p, "block": int(m.group(1)), "utc": utc, "kind": kind,
                     "local": utc + pd.Timedelta(hours=offset_hours), "mb": mb})
    cols = ["path", "block", "utc", "kind", "local", "mb"]
    if not recs:
        return pd.DataFrame(columns=cols)
    inv = pd.DataFrame(recs)
    inv["_rank"] = (inv["kind"] == "full").astype(int)  # prefer full
    inv = (inv.sort_values(["block", "utc", "_rank"], ascending=[True, True, False])
              .drop_duplicates(["block", "utc"], keep="first")
              .drop(columns="_rank")
              .sort_values(["local", "block"])
              .reset_index(drop=True))
    return inv[cols]


# ------------------------------------------------------------------------ ground truth

_SHEET_DATE = re.compile(r"(\d{1,2})[._-](\d{1,2})(?:[._-](\d{2,4}))?")


def load_groundtruth(path: str | Path, year: int | None = None) -> pd.DataFrame:
    """Load an optional manual-measurement workbook (e.g. Plant Height / SPAD), one sheet per
    date. Design-agnostic: keeps every numeric measurement column it finds, joined later on
    barcode+position. Non-numeric entries are coerced to NaN and flagged, never dropped.
    """
    path = Path(path)
    xl = pd.ExcelFile(path)
    frames = []
    for sheet in xl.sheet_names:
        df = pd.read_excel(xl, sheet)
        df.columns = [str(c).strip() for c in df.columns]
        if "barcode" not in df.columns:
            continue
        pos_col = next((c for c in df.columns
                        if c.lower() in ("unit column", "unit_column", "position")), None)
        if pos_col is None:
            continue
        m = _SHEET_DATE.search(sheet)
        hdate = pd.NaT
        if m:
            dd, mm = int(m.group(1)), int(m.group(2))
            yy = int(m.group(3)) if m.group(3) else (year or 2026)
            yy += 2000 if yy < 100 else 0
            try:
                hdate = pd.Timestamp(year=yy, month=mm, day=dd)
            except ValueError:
                hdate = pd.NaT
        out = pd.DataFrame({
            "block": pd.to_numeric(df["barcode"], errors="coerce"),
            "position": pd.to_numeric(df[pos_col], errors="coerce"),
            "sheet": sheet,
            "date": hdate,
        })
        # every measurement column: numeric coercion + a per-column non-numeric flag
        skip = {"barcode", pos_col, "table", "col", "row"}
        for c in df.columns:
            if c in skip or c.startswith("TFmeta") or c == pos_col:
                continue
            num = pd.to_numeric(df[c], errors="coerce")
            raw_present = df[c].notna()
            if num.notna().any() or (raw_present.any() and not pd.api.types.is_numeric_dtype(df[c])):
                key = _measure_key(c)
                out[key] = num
                out[key + "__nonnumeric"] = num.isna() & raw_present
        for meta in ("TFmeta.treatment", "TFmeta.genotype"):
            out[meta] = df[meta] if meta in df.columns else np.nan
        frames.append(out)
    if not frames:
        raise ValueError(f"{path.name}: no sheet with a 'barcode' column")
    gt = pd.concat(frames, ignore_index=True).dropna(subset=["block", "position"])
    gt["block"] = gt["block"].astype(int)
    gt["position"] = gt["position"].astype(int)
    meas = [c for c in gt.columns if c.startswith("manual_") and not c.endswith("__nonnumeric")]
    keep = pd.Series(False, index=gt.index)
    for c in meas:
        keep |= gt[c].notna() | gt.get(c + "__nonnumeric", False)
    return gt[keep].reset_index(drop=True) if meas else gt.reset_index(drop=True)


def _measure_key(colname: str) -> str:
    return "manual_" + re.sub(r"\s+", "_", colname.strip().lower())


def manual_measures(gt: pd.DataFrame) -> list[str]:
    return [c for c in gt.columns if c.startswith("manual_") and not c.endswith("__nonnumeric")]


def join_groundtruth(scans: pd.DataFrame, gt: pd.DataFrame,
                     height_key: str | None = None,
                     scan_height_col: str | None = None) -> pd.DataFrame:
    """Join every manual measurement to the pot's scan on the same date (fallback: nearest
    scan). Self-validates treatment/genotype labels. Returns one row per measured pot with all
    manual measures + the scan-derived height for agreement analysis."""
    measures = manual_measures(gt)
    if height_key is None:
        height_key = next((c for c in measures if "height" in c), None)
    if scan_height_col is None:
        cands = [c for c in scans.columns
                 if "height" in c.lower() and pd.api.types.is_numeric_dtype(scans[c])]
        scan_height_col = next((c for c in cands if "max" in c.lower()),
                               cands[0] if cands else None)

    rows = []
    for _, r in gt.iterrows():
        pot = scans[(scans["block"] == r["block"]) & (scans["position"] == r["position"])]
        base = {"block": r["block"], "position": r["position"], "date": r["date"]}
        for c in measures:
            base[c] = r[c]
            base[c + "__nonnumeric"] = bool(r.get(c + "__nonnumeric", False))
        if pot.empty:
            rows.append({**base, "scan_date": pd.NaT, "scan_height": np.nan,
                         "matched": "no scan for pot", "label_mismatch": np.nan})
            continue
        same = pot[pot["date"] == r["date"]] if pd.notna(r["date"]) else pot.iloc[0:0]
        if not same.empty:
            pick, how = same.iloc[-1], "same date"
        elif pd.notna(r["date"]):
            pick, how = pot.loc[(pot["date"] - r["date"]).abs().idxmin()], "nearest scan"
        else:
            pick, how = pot.iloc[-1], "last scan"
        rows.append({
            **base,
            "scan_date": pick["date"],
            "scan_height": pick[scan_height_col] if scan_height_col in pick else np.nan,
            "matched": how,
            "label_mismatch": _label_mismatch(r, pick),
        })
    res = pd.DataFrame(rows)
    res.attrs["height_key"] = height_key
    res.attrs["scan_height_col"] = scan_height_col
    return res


def _label_mismatch(gt_row, scan_row):
    checks = []
    if pd.notna(gt_row.get("TFmeta.treatment")) and "treatment" in scan_row:
        checks.append(str(gt_row["TFmeta.treatment"]).strip() != str(scan_row["treatment"]).strip())
    if pd.notna(gt_row.get("TFmeta.genotype")) and "genotype" in scan_row:
        checks.append(str(gt_row["TFmeta.genotype"]).strip() != str(scan_row["genotype"]).strip())
    return bool(any(checks)) if checks else np.nan
