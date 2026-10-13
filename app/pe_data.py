"""Data layer for the PhenoCleaning app.

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
    """Recursively find every `.ply` for an experiment and dedup to one record per scan. Handles
    `<block>_<UTC>_full.ply` (whole carrier) and `<block>_<UTC>_unitfile_N_M.ply` (one plant, N =
    carrier position). Where a `_full` file exists for a (block, UTC) its per-plant files are
    redundant; otherwise every plant's unitfile is kept. Genuine retakes (same block, different
    UTC) are kept. Returns path, block, utc, local time, kind, unit_n (position, unitfiles only),
    size."""
    folder = Path(folder)
    recs = []
    for p in folder.rglob("*.ply"):
        m = _PLY_FULL.match(p.name)
        kind, unit_n = "full", None
        if not m:
            m = _PLY_UNIT.match(p.name)
            kind = "unitfile"
            if m:
                unit_n = int(m.group(3))
        if not m:
            continue
        utc = pd.to_datetime(m.group(2), format="%Y%m%dT%H%M%S")
        try:
            mb = round(p.stat().st_size / 1024**2, 2)
        except OSError:
            mb = float("nan")
        recs.append({"path": p, "block": int(m.group(1)), "utc": utc, "kind": kind, "unit_n": unit_n,
                     "local": utc + pd.Timedelta(hours=offset_hours), "mb": mb})
    cols = ["path", "block", "utc", "kind", "unit_n", "local", "mb"]
    if not recs:
        return pd.DataFrame(columns=cols)
    inv = pd.DataFrame(recs)
    full_keys = set(zip(inv.loc[inv["kind"] == "full", "block"], inv.loc[inv["kind"] == "full", "utc"]))
    redundant = (inv["kind"] == "unitfile") & np.array(
        [k in full_keys for k in zip(inv["block"], inv["utc"])], dtype=bool)
    inv = (inv[~redundant]
           .drop_duplicates(["block", "utc", "kind", "unit_n"])
           .sort_values(["local", "block", "unit_n"], na_position="first")
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
