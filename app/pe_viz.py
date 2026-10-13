"""3D visualisation for PhenoCleaning: colour indices, still views, and animations.

Everything renders headless with matplotlib (Agg) + Pillow and returns PNG/GIF bytes, so it needs
no extra software. One colour scale is shared across all frames of an animation, so a change in
colour is a change in the plant, not in the scaling.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pandas as pd

import pe_data

# ---------------------------------------------------------------------------- indices

def _rgb16(p):
    if all(c in p.dtype.names for c in ("red16", "green16", "blue16")):
        return tuple(p[c].astype(np.float64) for c in ("red16", "green16", "blue16"))
    return tuple(p[c].astype(np.float64) for c in ("red", "green", "blue"))


def _chroma(p):
    R, G, B = _rgb16(p)
    s = R + G + B
    s = np.where(s == 0, 1.0, s)
    return R / s, G / s, B / s


def _ratio(a, b):
    return np.where(b == 0, np.nan, a / np.where(b == 0, 1.0, b))


LIGHTNESS_FULL_SCALE = 2200.0  # 16-bit channel value HortControl treats as 100 % lightness


def _hsl(p):
    R, G, B = (c / 65535.0 for c in _rgb16(p))
    mx, mn = np.maximum(np.maximum(R, G), B), np.minimum(np.minimum(R, G), B)
    light = (mx + mn) / 2
    d = mx - mn
    sat = np.where(d == 0, 0.0, d / np.maximum(1 - np.abs(2 * light - 1), 1e-9))
    return light * 65535.0 / LIGHTNESS_FULL_SCALE, np.clip(sat, 0, 1)


def _f(name):
    return lambda p: p[name].astype(np.float64)


# key: (menu label, group, function, colormap, fixed range or None, one-line description)
INDICES = {
    "rgb": ("True colour", "Colour", None, None, None, "The scan's own RGB colour, brightened for display."),
    "ndvi": ("NDVI", "Sensor", _f("ndvi"), "RdYlGn", (-0.2, 0.9),
             "(NIR − Red) / (NIR + Red) as stored by PlantEye — greenness / vigour."),
    "ndvi940": ("NDVI (940 nm)", "Sensor",
                lambda p: _ratio(p["nir940"].astype(float) - _rgb16(p)[0], p["nir940"].astype(float) + _rgb16(p)[0]),
                "RdYlGn", (-0.2, 0.9), "NDVI using the second NIR band (940 nm)."),
    "nir": ("NIR", "Sensor", _f("nir"), "inferno", None, "Near-infrared reflectance (leaf structure)."),
    "nir940": ("NIR 940 nm", "Sensor", _f("nir940"), "inferno", None, "Second NIR band (water-sensitive)."),
    "psri": ("PSRI", "Sensor", lambda p: _ratio(_rgb16(p)[0] - _rgb16(p)[2], p["nir"].astype(float)),
             "RdYlGn_r", None, "(Red − Blue) / NIR — senescence; higher = more senescent."),
    "npci": ("NPCI", "Sensor", lambda p: _ratio(_rgb16(p)[0] - _rgb16(p)[2], _rgb16(p)[0] + _rgb16(p)[2]),
             "RdYlGn_r", None, "(Red − Blue) / (Red + Blue) — chlorophyll / pigment ratio."),
    "hue": ("Hue", "Colour", _f("hue"), "hsv", (0, 360), "Hue angle in degrees (green ≈ 90–150°)."),
    "gli": ("GLI", "RGB index", lambda p: _ratio(2 * _rgb16(p)[1] - _rgb16(p)[0] - _rgb16(p)[2],
                                                 2 * _rgb16(p)[1] + _rgb16(p)[0] + _rgb16(p)[2]),
            "RdYlGn", (-0.3, 0.4), "Green Leaf Index (2G − R − B)/(2G + R + B)."),
    "exg": ("ExG", "RGB index", lambda p: 2 * _chroma(p)[1] - _chroma(p)[0] - _chroma(p)[2],
            "RdYlGn", None, "Excess Green 2g − r − b (chromatic coordinates)."),
    "exr": ("ExR", "RGB index", lambda p: 1.4 * _chroma(p)[0] - _chroma(p)[1],
            "RdYlGn_r", None, "Excess Red 1.4r − g — stress / senescence."),
    "exgr": ("ExG − ExR", "RGB index",
             lambda p: (2 * _chroma(p)[1] - _chroma(p)[0] - _chroma(p)[2]) - (1.4 * _chroma(p)[0] - _chroma(p)[1]),
             "RdYlGn", None, "Excess Green minus Excess Red — robust vegetation contrast."),
    "ngrdi": ("NGRDI", "RGB index", lambda p: _ratio(_rgb16(p)[1] - _rgb16(p)[0], _rgb16(p)[1] + _rgb16(p)[0]),
              "RdYlGn", (-0.3, 0.3), "(G − R)/(G + R) — green–red difference."),
    "vari": ("VARI", "RGB index",
             lambda p: np.clip(_ratio(_rgb16(p)[1] - _rgb16(p)[0], _rgb16(p)[1] + _rgb16(p)[0] - _rgb16(p)[2]), -1, 1),
             "RdYlGn", (-0.5, 0.5), "(G − R)/(G + R − B) — visible atmospherically resistant index."),
    "mgrvi": ("MGRVI", "RGB index",
              lambda p: _ratio(_rgb16(p)[1] ** 2 - _rgb16(p)[0] ** 2, _rgb16(p)[1] ** 2 + _rgb16(p)[0] ** 2),
              "RdYlGn", (-0.5, 0.5), "(G² − R²)/(G² + R²) — modified green–red index."),
    "rgbvi": ("RGBVI", "RGB index",
              lambda p: _ratio(_rgb16(p)[1] ** 2 - _rgb16(p)[0] * _rgb16(p)[2],
                               _rgb16(p)[1] ** 2 + _rgb16(p)[0] * _rgb16(p)[2]),
              "RdYlGn", (-0.5, 0.7), "(G² − R·B)/(G² + R·B) — RGB vegetation index."),
    "tgi": ("TGI", "RGB index", lambda p: _chroma(p)[1] - 0.39 * _chroma(p)[0] - 0.61 * _chroma(p)[2],
            "RdYlGn", None, "Triangular Greenness Index (chromatic form) — tracks chlorophyll."),
    "cive": ("CIVE", "RGB index",
             lambda p: 0.441 * _chroma(p)[0] - 0.811 * _chroma(p)[1] + 0.385 * _chroma(p)[2] + 18.787,
             "RdYlGn_r", None, "Colour Index of Vegetation Extraction — lower = greener."),
    "lightness": ("Lightness", "Colour", lambda p: 100 * _hsl(p)[0], "gray", None, "HSL lightness (%)."),
    "saturation": ("Saturation", "Colour", lambda p: 100 * _hsl(p)[1], "magma", (0, 100), "HSL saturation (%)."),
    "height": ("Height", "Structure", lambda p: p["z"].astype(np.float64), "viridis", None,
               "Height of each point (mm, scanner reference)."),
}


def index_menu() -> dict:
    """{menu label: key}, grouped and ordered for a single selectbox."""
    return {f"{v[0]}  ·  {v[1]}": k for k, v in INDICES.items()}


def index_values(pts: np.ndarray, key: str) -> np.ndarray:
    return np.asarray(INDICES[key][2](pts), dtype=np.float64)


def shared_norm(clouds: list, key: str):
    """One colour normalisation for every frame: the index's fixed range, else the 2–98th
    percentile of all clouds pooled."""
    from matplotlib import colors as mcolors
    fixed = INDICES[key][4]
    if fixed:
        return mcolors.Normalize(*fixed)
    vals = np.concatenate([index_values(c, key) for c in clouds if len(c)]) if clouds else np.array([0, 1.0])
    vals = vals[np.isfinite(vals)]
    lo, hi = np.percentile(vals, [2, 98]) if len(vals) > 1 else (0.0, 1.0)
    return mcolors.Normalize(lo, hi if hi > lo else lo + 1)


def point_colors(pts: np.ndarray, key: str, norm=None, brighten: float = 1.6) -> np.ndarray:
    from matplotlib import colormaps
    if key == "rgb":
        rgb = np.column_stack([pts["red"], pts["green"], pts["blue"]]).astype(float) / 255.0
        return np.clip(rgb * brighten, 0, 1)
    norm = norm or shared_norm([pts], key)
    v = index_values(pts, key)
    c = colormaps[INDICES[key][3]](norm(np.nan_to_num(v, nan=norm.vmin)))[:, :3]
    return c


# ---------------------------------------------------------------------------- loading

def plant_positions(verts: np.ndarray) -> dict:
    """``unit_id`` → carrier position (1..n), by order along the carrier (y)."""
    uid = verts["unit_id"]
    y = verts["y"].astype(np.float64)
    order = sorted(np.unique(uid), key=lambda u: y[uid == u].mean())
    return {int(u): i + 1 for i, u in enumerate(order)}


def load_plant(row, position: int | None):
    """Points (and mesh faces) for one plant from an inventory row: a per-plant ``unitfile`` is
    the plant; a ``_full`` carrier file is cut to the plant at ``position`` (None = whole carrier)."""
    verts, faces, _, _ = pe_data.read_ply_lenient(row["path"])
    if row.get("kind") == "unitfile" or position is None:
        return verts, faces
    pos = plant_positions(verts)
    want = [u for u, p in pos.items() if p == int(position)]
    if not want:
        return verts[:0], None
    m = verts["unit_id"] == want[0]
    if faces is not None:
        remap = -np.ones(len(verts), np.int64)
        remap[np.where(m)[0]] = np.arange(int(m.sum()))
        faces = remap[faces[m[faces].all(axis=1)]]
    return verts[m], faces


def plant_options(inventory: pd.DataFrame, tabular: pd.DataFrame | None,
                  status: pd.DataFrame | None = None) -> pd.DataFrame:
    """Every plant that has at least one 3D scan: block, position, label (with genotype /
    treatment when the export is loaded), and how many dates it was scanned on. With ``status``
    (from :func:`pe_data.ply_status`) only scans that survive the cleaning count."""
    if status is not None:
        ok = status[status["status"] != "excluded"]
        d = ok[["block", "position", "local"]].copy()
    else:
        recs = []
        for _, r in inventory.iterrows():
            if r["kind"] == "unitfile":
                recs.append((int(r["block"]), int(r["unit_n"]), r["local"]))
            else:
                for p in range(1, 5):
                    recs.append((int(r["block"]), p, r["local"]))
        d = pd.DataFrame(recs, columns=["block", "position", "local"])
    if d.empty:
        return pd.DataFrame(columns=["block", "position", "dates", "label"])
    d = d.groupby(["block", "position"]).agg(dates=("local", lambda s: s.dt.normalize().nunique())).reset_index()
    lab = {}
    if tabular is not None and len(tabular) and {"block", "position"} <= set(tabular.columns):
        t = tabular.drop_duplicates(["block", "position"])
        for _, r in t.iterrows():
            bits = [str(r[c]) for c in ("genotype", "treatment") if c in t and pd.notna(r[c])]
            lab[(int(r["block"]), int(r["position"]))] = (" / ".join(bits), bool(r.get("is_blank", False)))
        d = d[[(b, p) in lab and not lab[(b, p)][1] for b, p in zip(d["block"], d["position"])]]
    d["label"] = [f"{b}:{p}" + (f" — {lab[(b, p)][0]}" if (b, p) in lab and lab[(b, p)][0] else "")
                  + f"  ({n} date{'s' if n != 1 else ''})"
                  for b, p, n in zip(d["block"], d["position"], d["dates"])]
    return d.reset_index(drop=True)


def plant_series(inventory: pd.DataFrame, block: int, position: int, max_points: int = 40000,
                 allowed_paths=None):
    """[(local time, points)] for one plant across every scan date, oldest first. With
    ``allowed_paths`` only those files are used (the scans kept by the cleaning); one scan per date
    (the latest, if more than one remains)."""
    inv = inventory[inventory["block"] == block].copy()
    inv = inv[(inv["kind"] == "full") | (inv["unit_n"] == position)]
    if allowed_paths is not None:
        allowed = {str(a) for a in allowed_paths}
        inv = inv[inv["path"].astype(str).isin(allowed)]
    inv = inv.sort_values("local").groupby(inv["local"].dt.normalize()).tail(1)
    out = []
    rng = np.random.default_rng(0)
    for _, r in inv.iterrows():
        pts, _ = load_plant(r, position)
        if len(pts) < 10:
            continue
        if len(pts) > max_points:
            pts = pts[np.sort(rng.choice(len(pts), max_points, replace=False))]
        out.append((r["local"], pts))
    return out


# ---------------------------------------------------------------------------- rendering

def _figure(w_px, h_px, bg):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig = Figure(figsize=(w_px / 100, h_px / 100), dpi=100, facecolor=bg)
    FigureCanvasAgg(fig)
    return fig


def _to_image(fig):
    from PIL import Image
    fig.canvas.draw()
    return Image.frombytes("RGBA", fig.canvas.get_width_height(),
                           bytes(fig.canvas.buffer_rgba())).convert("RGB")


def _ink(bg):
    return "white" if bg == "black" else "#222222"


_AXES = {"top": ("x", "y"), "side": ("y", "z")}


def _draw_2d(ax, pts, key, norm, view, point_size, brighten):
    a, b = _AXES[view]
    depth = pts["z"] if view == "top" else -pts["x"]
    o = np.argsort(depth)
    p = pts[o]
    ax.scatter(p[a], p[b], c=point_colors(p, key, norm, brighten), s=point_size, linewidths=0,
               rasterized=True)


def _extent(clouds, view, pad=0.06):
    """Half-width/height of a frame that fits every cloud (each centred on its own middle), so a
    plant never jumps or rescales between frames."""
    a, b = _AXES[view]
    ha = max([1.0] + [np.ptp(c[a].astype(float)) / 2 for c in clouds if len(c)])
    hb = max([1.0] + [np.ptp(c[b].astype(float)) / 2 for c in clouds if len(c)])
    return ha * (1 + pad), hb * (1 + pad)


def _colorbar(fig, key, norm, bg, rect=(0.15, 0.045, 0.7, 0.022)):
    if key == "rgb":
        return
    from matplotlib import colorbar, colormaps
    cax = fig.add_axes(rect)
    cb = colorbar.ColorbarBase(cax, cmap=colormaps[INDICES[key][3]], norm=norm, orientation="horizontal")
    cb.outline.set_visible(False)
    cax.tick_params(labelsize=7, colors=_ink(bg), length=2)
    cax.set_title(INDICES[key][0], fontsize=8, color=_ink(bg), pad=2)


def still_views(pts: np.ndarray, key: str = "rgb", size: int = 420, bg: str = "white",
                point_size: float = 1.0, brighten: float = 1.6) -> bytes:
    """Top and side view side by side, one PNG."""
    norm = None if key == "rgb" else shared_norm([pts], key)
    fig = _figure(2 * size, size, bg)
    for k, view in enumerate(("top", "side")):
        ax = fig.add_axes([0.02 + 0.5 * k, 0.12, 0.46, 0.8], facecolor=bg)
        _draw_2d(ax, pts, key, norm, view, point_size, brighten)
        ax.set_aspect("equal"); ax.axis("off")
        ax.set_title(f"{'Top' if view == 'top' else 'Side'} view", fontsize=9, color=_ink(bg))
    _colorbar(fig, key, norm, bg, rect=(0.3, 0.05, 0.4, 0.025))
    buf = io.BytesIO(); _to_image(fig).save(buf, format="PNG")
    return buf.getvalue()


def _gif(frames, fps, hold_last=0):
    frames = frames + [frames[-1]] * int(hold_last)
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], loop=0,
                   duration=int(round(1000 / max(1, fps))), disposal=2, optimize=True)
    return buf.getvalue()


def _date_label(t, t0):
    return f"{pd.Timestamp(t):%d %b %Y}  ·  day {(pd.Timestamp(t).normalize() - pd.Timestamp(t0).normalize()).days}"


def _setup_3d(fig, rect, bg):
    import mpl_toolkits.mplot3d  # noqa: F401
    ax = fig.add_axes(rect, projection="3d")
    ax.set_facecolor(bg)
    ax.set_axis_off()
    return ax


def _box(ax, rh, rz, zoom=1.3):
    ax.set_xlim(-rh, rh); ax.set_ylim(-rh, rh); ax.set_zlim(0, rz)
    ax.set_box_aspect((2 * rh, 2 * rh, rz), zoom=zoom)


def _centred(pts):
    xyz = np.column_stack([pts["x"], pts["y"], pts["z"]]).astype(np.float64)
    mid = (xyz.min(0) + xyz.max(0)) / 2
    mid[2] = xyz[:, 2].min()
    return xyz - mid


def turntable_gif(pts: np.ndarray, faces=None, *, key: str = "rgb", turns: int = 1, n_frames: int = 60,
                  fps: int = 15, size: int = 480, elev: float = 18.0, bg: str = "white",
                  point_size: float = 2.0, brighten: float = 1.6, max_points: int = 60000,
                  progress=None) -> bytes:
    """A looping turntable of one cloud (or its mesh, if ``faces`` given)."""
    is_mesh = faces is not None and len(faces) > 0
    if not is_mesh and len(pts) > max_points:
        pts = pts[np.sort(np.random.default_rng(0).choice(len(pts), max_points, replace=False))]
    xyz = _centred(pts)
    norm = None if key == "rgb" else shared_norm([pts], key)
    col = point_colors(pts, key, norm, brighten)
    rh = float(np.hypot(xyz[:, 0], xyz[:, 1]).max() or 1) * 1.1
    rz = float(xyz[:, 2].max() or 1) * 1.05
    fig = _figure(size, size, bg)
    ax = _setup_3d(fig, [0, 0.06, 1, 0.94], bg)
    if is_mesh:
        tri = ax.plot_trisurf(xyz[:, 0], xyz[:, 1], xyz[:, 2], triangles=np.asarray(faces),
                              linewidth=0, antialiased=False, shade=False)
        tri.set_fc(col[np.asarray(faces)].mean(axis=1))
    else:
        ax.scatter(xyz[:, 0], xyz[:, 1], xyz[:, 2], c=col, s=point_size, linewidths=0, depthshade=False)
    _box(ax, rh, rz)
    _colorbar(fig, key, norm, bg, rect=(0.25, 0.04, 0.5, 0.02))
    frames = []
    n_frames = max(2, int(n_frames))
    for i in range(n_frames):
        ax.view_init(elev=elev, azim=-60 + 360.0 * turns * i / n_frames)
        frames.append(_to_image(fig))
        if progress:
            progress((i + 1) / n_frames)
    return _gif(frames, fps)


def growth_gif(series, *, key: str = "rgb", view: str = "top", size: int = 480, fps: int = 2,
               hold_last: int = 3, bg: str = "white", point_size: float = 1.2, brighten: float = 1.6,
               title: str = "", elev: float = 18.0, progress=None) -> bytes:
    """One plant across its scan dates, one frame per date. ``view`` = 'top', 'side' or '3d'.
    Fixed framing and one colour scale for the whole sequence."""
    clouds = [p for _, p in series]
    norm = None if key == "rgb" else shared_norm(clouds, key)
    t0 = series[0][0]
    frames = []
    if view == "3d":
        cs = [_centred(p) for p in clouds]
        rh = max(float(np.hypot(c[:, 0], c[:, 1]).max()) for c in cs) * 1.1
        rz = max(float(c[:, 2].max()) for c in cs) * 1.05
    else:
        ha, hb = _extent(clouds, view)
        a, b = _AXES[view]
        b_lo = min(float(c[b].min()) for c in clouds) if view == "side" else None
    for i, (t, pts) in enumerate(series):
        fig = _figure(size, int(size * 1.08), bg)
        if view == "3d":
            ax = _setup_3d(fig, [0, 0.1, 1, 0.82], bg)
            c = _centred(pts)
            ax.scatter(c[:, 0], c[:, 1], c[:, 2], c=point_colors(pts, key, norm, brighten), s=point_size,
                       linewidths=0, depthshade=False)
            _box(ax, rh, rz)
            ax.view_init(elev=elev, azim=-60)
        else:
            ax = fig.add_axes([0.03, 0.11, 0.94, 0.8], facecolor=bg)
            _draw_2d(ax, pts, key, norm, view, point_size, brighten)
            ca = (float(pts[a].min()) + float(pts[a].max())) / 2
            ax.set_xlim(ca - ha, ca + ha)
            if view == "top":
                cb = (float(pts[b].min()) + float(pts[b].max())) / 2
                ax.set_ylim(cb - hb, cb + hb)
            else:
                ax.set_ylim(b_lo - 0.02 * hb, b_lo + 2 * hb)
            ax.set_aspect("equal"); ax.axis("off")
        fig.text(0.03, 0.965, title, fontsize=10, color=_ink(bg), va="top", weight="bold")
        fig.text(0.97, 0.965, _date_label(t, t0), fontsize=10, color=_ink(bg), va="top", ha="right")
        _colorbar(fig, key, norm, bg)
        frames.append(_to_image(fig))
        if progress:
            progress((i + 1) / len(series))
    return _gif(frames, fps, hold_last)


def turntable_through_time_gif(series, *, key: str = "rgb", frames_per_date: int = 24,
                               turns_per_date: float = 1.0, size: int = 480, fps: int = 15,
                               elev: float = 18.0, bg: str = "white", point_size: float = 1.5,
                               brighten: float = 1.6, title: str = "", progress=None) -> bytes:
    """The plant spins continuously while stepping through its scan dates."""
    clouds = [p for _, p in series]
    norm = None if key == "rgb" else shared_norm(clouds, key)
    cs = [_centred(p) for p in clouds]
    rh = max(float(np.hypot(c[:, 0], c[:, 1]).max()) for c in cs) * 1.1
    rz = max(float(c[:, 2].max()) for c in cs) * 1.05
    t0 = series[0][0]
    frames, k, total = [], 0, len(series) * frames_per_date
    for (t, pts), c in zip(series, cs):
        fig = _figure(size, int(size * 1.08), bg)
        ax = _setup_3d(fig, [0, 0.1, 1, 0.82], bg)
        ax.scatter(c[:, 0], c[:, 1], c[:, 2], c=point_colors(pts, key, norm, brighten), s=point_size,
                   linewidths=0, depthshade=False)
        _box(ax, rh, rz)
        fig.text(0.03, 0.965, title, fontsize=10, color=_ink(bg), va="top", weight="bold")
        fig.text(0.97, 0.965, _date_label(t, t0), fontsize=10, color=_ink(bg), va="top", ha="right")
        _colorbar(fig, key, norm, bg)
        for _ in range(frames_per_date):
            ax.view_init(elev=elev, azim=-60 + 360.0 * turns_per_date * k / frames_per_date)
            frames.append(_to_image(fig))
            k += 1
            if progress:
                progress(k / total)
    return _gif(frames, fps)


def compare_gif(groups, *, key: str = "rgb", view: str = "top", size: int = 300, fps: int = 2,
                hold_last: int = 3, bg: str = "white", point_size: float = 0.8, brighten: float = 1.6,
                progress=None) -> bytes:
    """Several plants side by side, stepping through the union of their scan dates in sync.
    ``groups`` = [(label, series)]. Every panel uses the same mm scale and colour scale; a plant
    with no scan on a date shows its most recent earlier scan, marked '(last scan)'."""
    groups = [(g, s) for g, s in groups if s]
    clouds = [p for _, s in groups for _, p in s]
    norm = None if key == "rgb" else shared_norm(clouds, key)
    dates = sorted({pd.Timestamp(t).normalize() for _, s in groups for t, _ in s})
    t0 = dates[0]
    n = len(groups)
    ncol = min(n, 4)
    nrow = int(np.ceil(n / ncol))
    a, b = _AXES["top" if view == "top" else "side"]
    ha, hb = _extent(clouds, "top" if view == "top" else "side")
    b_lo = min(float(c[b].min()) for c in clouds)
    top_px, bot_px, title_px = 44, 46, 20
    cell_h = int(size * 1.05)
    W, H = size * ncol, cell_h * nrow + top_px + bot_px
    frames = []
    for i, d in enumerate(dates):
        fig = _figure(W, H, bg)
        for j, (label, s) in enumerate(groups):
            r, c = divmod(j, ncol)
            top = H - top_px - r * cell_h
            ax = fig.add_axes([(c + 0.03) / ncol, (top - cell_h + 4) / H, 0.94 / ncol,
                               (cell_h - title_px - 8) / H], facecolor=bg)
            past = [(t, p) for t, p in s if pd.Timestamp(t).normalize() <= d]
            note = ""
            if past:
                t, pts = past[-1]
                if pd.Timestamp(t).normalize() != d:
                    note = " (last scan)"
                _draw_2d(ax, pts, key, norm, view, point_size, brighten)
                ca = (float(pts[a].min()) + float(pts[a].max())) / 2
                ax.set_xlim(ca - ha, ca + ha)
                if view == "top":
                    cb = (float(pts[b].min()) + float(pts[b].max())) / 2
                    ax.set_ylim(cb - hb, cb + hb)
                else:
                    ax.set_ylim(b_lo - 0.02 * hb, b_lo + 2 * hb)
            else:
                note = " (not yet scanned)"
            ax.set_aspect("equal"); ax.axis("off")
            ax.set_title(label + note, fontsize=8, color=_ink(bg))
        fig.text(0.5, 1 - 10 / H, _date_label(d, t0), fontsize=11, color=_ink(bg), ha="center", va="top",
                 weight="bold")
        _colorbar(fig, key, norm, bg, rect=(0.3, 12 / H, 0.4, 9 / H))
        frames.append(_to_image(fig))
        if progress:
            progress((i + 1) / len(dates))
    return _gif(frames, fps, hold_last)


SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]


def trait_chart_gif(d: pd.DataFrame, trait: str, group: str | None = None, *, size: int = 640,
                    fps: int = 2, hold_last: int = 4, progress=None) -> bytes:
    """Trait trajectories drawing in date by date (mean ± SE per group on a true elapsed-days axis).
    Up to 8 groups; extra groups are folded into 'Other'."""
    from matplotlib.lines import Line2D
    d = d.dropna(subset=[trait]).copy()
    d["elapsed"] = (d["date"] - d["date"].min()).dt.days
    if group:
        keep = d[group].astype(str).value_counts().index[:7 if d[group].nunique() > 8 else 8]
        d["_g"] = np.where(d[group].astype(str).isin(keep), d[group].astype(str), "Other")
    else:
        d["_g"] = "all plants"
    stats = d.groupby(["_g", "elapsed"])[trait].agg(["mean", "sem"]).reset_index()
    groups = list(dict.fromkeys(sorted(stats["_g"].unique(), key=lambda g: (g == "Other", g))))
    days = sorted(stats["elapsed"].unique())
    lo = (stats["mean"] - stats["sem"].fillna(0)).min(); hi = (stats["mean"] + stats["sem"].fillna(0)).max()
    pad = 0.06 * (hi - lo or 1)
    frames = []
    for i, day in enumerate(days):
        fig = _figure(size, int(size * 0.62), "white")
        ax = fig.add_axes([0.1, 0.14, 0.62, 0.74])
        for k, g in enumerate(groups):
            s = stats[(stats["_g"] == g) & (stats["elapsed"] <= day)]
            col = SERIES_COLORS[k % len(SERIES_COLORS)]
            ax.plot(s["elapsed"], s["mean"], "-", color=col, lw=2)
            ax.errorbar(s["elapsed"], s["mean"], yerr=s["sem"], fmt="o", ms=5, color=col, mec="white",
                        mew=1, capsize=2, lw=1)
        ax.set_xlim(min(days) - 1, max(days) + 1); ax.set_ylim(lo - pad, hi + pad)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        ax.grid(color="#e4e3df", lw=0.6); ax.set_axisbelow(True)
        ax.set_xlabel("days elapsed", fontsize=9, color="#52514e"); ax.set_ylabel(trait, fontsize=9, color="#52514e")
        ax.tick_params(labelsize=8, colors="#52514e")
        date = d.loc[d["elapsed"] == day, "date"].iloc[0]
        ax.set_title(f"{trait}  ·  {pd.Timestamp(date):%d %b %Y} (day {day})", fontsize=10, loc="left")
        if len(groups) > 1 or group:
            handles = [Line2D([], [], color=SERIES_COLORS[k % 8], marker="o", lw=2) for k in range(len(groups))]
            ax.legend(handles, groups, title=group, fontsize=8, title_fontsize=8, frameon=False,
                      loc="upper left", bbox_to_anchor=(1.02, 1))
        frames.append(_to_image(fig))
        if progress:
            progress((i + 1) / len(days))
    return _gif(frames, fps, hold_last)
