"""PhenoCleaning — interactive app.

Run:  streamlit run app/pe_app.py

Combine the TraitFinder exports of one experiment, remove duplicate scans and blank pots, look at
trait trajectories over time, and view and animate the PlantEye 3D scans in any colour index.
Everything you decide is logged; your raw files are never modified.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pe_data
import pe_design
import pe_qc
import pe_viz
from version import __version__, version_string

VERSION = version_string()


def _log_dir() -> Path:
    """logs/ next to PhenoCleaning.exe (packaged) or next to the source (dev); home if not writable."""
    base = (Path(sys.executable).parent if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[1])
    for d in (base / "logs", Path.home() / "PhenoCleaning-logs"):
        try:
            d.mkdir(parents=True, exist_ok=True)
            return d
        except OSError:
            continue
    return Path.cwd()


def log_error(where: str) -> Path | None:
    """Append the current exception's full traceback to logs/phenocleaning-<date>.log."""
    import datetime as _dt
    import traceback as _tb
    try:
        f = _log_dir() / f"phenocleaning-{_dt.date.today().isoformat()}.log"
        with f.open("a", encoding="utf-8") as fh:
            fh.write(f"\n=== {_dt.datetime.now():%Y-%m-%d %H:%M:%S} · {VERSION} · {where}\n")
            fh.write(_tb.format_exc())
        return f
    except Exception:
        return None


def report_error(where: str):
    """Plain-English error for the user; the traceback goes to the log file, never the screen."""
    log_error(where)
    st.error(f"Sorry — {where} hit a problem in **{VERSION}**. Everything else still works. "
             "Please use **⬇ Download logs for debugging** (sidebar → *About / report a problem*) and "
             "email the file to the developer so it can be fixed.")


def _attach_server_logging() -> None:
    """Also send the web server's own warnings and errors (Streamlit, uvicorn, Starlette) to today's
    log file — those happen outside the app's code (e.g. the v1.2.0 'Expected ASGI message' error that
    stopped graphs showing) and otherwise only reach the console window. Once per process."""
    import datetime as _dt
    import logging
    if getattr(sys, "_phenocleaning_server_log", False):
        return
    try:
        f = _log_dir() / f"phenocleaning-{_dt.date.today().isoformat()}.log"
        h = logging.FileHandler(f, encoding="utf-8")
        h.setLevel(logging.WARNING)
        h.setFormatter(logging.Formatter(f"\n=== %(asctime)s · {VERSION} · server · %(name)s · "
                                         "%(levelname)s\n%(message)s"))
        logging.getLogger().addHandler(h)
        for name in ("streamlit", "uvicorn", "uvicorn.error", "uvicorn.asgi", "starlette"):
            lg = logging.getLogger(name)
            if not lg.propagate:      # loggers that don't pass records up to the root logger
                lg.addHandler(h)
        sys._phenocleaning_server_log = True
    except Exception:
        pass


def debug_bundle() -> bytes:
    """A zip for the developer: every log file, plus diagnostics (version, OS, library versions,
    what is loaded, and this session's decision log). Contains folder paths and file names but no
    measurement data."""
    import datetime as _dt
    import io as _io
    import platform
    import zipfile
    mods = {}
    for m in ("streamlit", "starlette", "uvicorn", "matplotlib", "pandas", "numpy", "PIL", "openpyxl"):
        try:
            mods[m] = getattr(__import__(m), "__version__", "?")
        except Exception as e:
            mods[m] = f"not importable ({type(e).__name__})"
    lines = [VERSION, f"created {_dt.datetime.now():%Y-%m-%d %H:%M:%S}",
             f"OS: {platform.platform()}", f"Python: {sys.version.split()[0]}",
             f"packaged: {bool(getattr(sys, 'frozen', False))}", f"log folder: {_log_dir()}", "",
             "Libraries:"] + [f"  {k} {v}" for k, v in mods.items()]
    if SS.get("insts"):
        lines += ["", f"Exports loaded ({len(SS.insts)}):"]
        lines += [f"  {i.name}  ({len(i.df)} rows, {i.path.suffix}){'  [stale]' if i.is_stale else ''}"
                  for i in SS.insts]
    if SS.get("union") is not None:
        u = SS.union
        lines += [f"Combined: {len(u)} rows, {u['block'].nunique()} carriers, {u['date'].nunique()} dates; "
                  f"{len(SS.decisions)} decision(s)"]
    lines += ["", "Session log:"] + [f"  {a}" for a in SS.get("audit", [])]
    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("diagnostics.txt", "\n".join(lines))
        for f in sorted(_log_dir().glob("phenocleaning-*.log"))[-30:]:
            try:
                z.write(f, f"logs/{f.name}")
            except OSError:
                pass
    return buf.getvalue()


class guard:
    """`with tabs[1], guard("the Time-series tab"):` — an error in one tab shows a plain message and is
    logged, and the rest of the app keeps working. Streamlit's own rerun/stop signals derive from
    BaseException, so they pass straight through."""

    def __init__(self, where: str):
        self.where = where

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if et is not None and issubclass(et, Exception):
            report_error(self.where)
            return True
        return False

st.set_page_config(page_title=f"PhenoCleaning {__version__}", layout="wide", page_icon="🌱")

SS = st.session_state
for k, v in dict(insts=None, union=None, base_union=None, candidates=None, design=None,
                 gt=None, gt_join=None, drop=set(), decisions={}, audit=[], splits=[],
                 hidden_factors=[], remove_blanks=True, anim={}, flash=None,
                 ply_offset=None).items():
    SS.setdefault(k, v)

FOLDER_LAYOUT = """\
MyExperiment/                         ← enter THIS folder in the sidebar
├── Group2/                           ← one sub-folder per export, named after it
│   ├── PHENO-0023_Group2_PlantEye.csv    (or the .zip from the portal)
│   └── 3D/                           ← optional: that export's .ply files
├── Group3/
│   └── PHENO-0023_Group3_PlantEye.csv
└── Group4/ …"""

FOLDER_HELP = (
    "**Several exports of one experiment** (e.g. one per pot height) are combined into one dataset. "
    "Put each export in its **own sub-folder named after it**, inside one experiment folder, and enter "
    "the experiment folder above. The sub-folder names label each export everywhere in the app and in "
    "the `source_instance` column of the combined CSV. Exports placed loose in one folder also work — "
    "they are then labelled by file name.")

SEV = {"blocker": "🟥", "flag": "🟧", "warn": "🟨", "info": "🟦", "ok": "🟩"}


def log(m):
    SS.audit.append(m)


def apply_splits(base: pd.DataFrame) -> pd.DataFrame:
    df = base
    for sp in SS.splits:
        if sp["col"] in df.columns:
            df, _ = pe_data.split_compound(df, sp["col"], sp["delim"], sp["names"])
    drop = [c for c in SS.get("hidden_factors", []) if c in df.columns]
    if drop:
        df = df.drop(columns=drop)
    return df


def factor_options(df) -> list[str]:
    return pe_data.categorical_factors(df)


def candidates_frame() -> pd.DataFrame:
    """The included exports concatenated *before* dedup — every physical scan is addressable here."""
    frames = []
    for ins in SS.insts or []:
        if ins.is_stale or ins.name in SS.drop:
            continue
        d = ins.df.copy()
        d["source_instance"] = ins.name
        frames.append(d)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def rebuild():
    """Recompute the combined dataset honouring the user's per-record decisions."""
    SS.base_union = pe_data.resolve_union(SS.insts, decisions=SS.decisions, drop=tuple(SS.drop))
    SS.union = apply_splits(SS.base_union)
    SS.candidates = candidates_frame()
    if SS.gt is not None and not SS.union.empty:
        SS.gt_join = pe_data.join_groundtruth(SS.union, SS.gt)


def set_decision(key, action, **extra):
    SS.decisions[key] = {"action": action, **extra}


def row_scan_key(r):
    return pe_data.scan_key(r.get("source_instance", ""), r["unit"], r["timestamp"])


def clean_rows() -> pd.DataFrame:
    """The combined dataset as used everywhere downstream: blanks removed if the user chose so."""
    u = SS.union
    return u[~u["is_blank"]] if SS.remove_blanks else u


def keep_anim(name: str, data: bytes):
    SS.anim[name] = data


def show_anim(name: str):
    if name in SS.anim:
        st.image(SS.anim[name])
        st.download_button("⬇ Download GIF", SS.anim[name], name, "image/gif", key=f"dl_{name}")


@st.cache_data(show_spinner=False)
def ply_inventory(folder: str, offset: float = 0.0) -> pd.DataFrame:
    """3D files with ``local`` = UTC + ``offset`` hours (see :func:`ply_offset`)."""
    return pe_data.with_offset(pe_data.discover_ply(folder), offset)


@st.cache_data(show_spinner=False)
def ply_skipped(folder: str) -> list:
    return pe_data.skipped_ply(folder)


@st.cache_data(show_spinner=False, max_entries=8)
def detected_offset(folder: str, candidates: pd.DataFrame) -> tuple:
    return pe_data.detect_utc_offset(ply_inventory(folder), candidates)


def ply_offset(folder: str) -> tuple:
    """(hours, matched fraction, auto?) — the scanner's offset from UTC, auto-detected by lining the
    3D files up with the scans unless the user has set it under 3D settings."""
    cand = SS.candidates if SS.candidates is not None else candidates_frame()
    auto, frac = detected_offset(folder, cand)
    if SS.ply_offset is not None:
        return float(SS.ply_offset), frac, False
    return auto, frac, True


@st.cache_data(show_spinner=False, max_entries=64)
def plant_series(folder: str, offset: float, block: int, position: int, row: int, allowed: tuple,
                 n_rows: int):
    return pe_viz.plant_series(ply_inventory(folder, offset), block, position, row,
                               allowed_paths=allowed, n_rows=n_rows)


@st.cache_data(show_spinner=False, max_entries=8)
def scan_status(folder: str, offset: float, kept: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    """Which plant in which .ply survives the cleaning (kept / excluded / not in exports)."""
    return pe_data.ply_status(ply_inventory(folder, offset), kept, candidates)


def current_status(folder: str, offset: float) -> pd.DataFrame:
    cand = SS.candidates if SS.candidates is not None else candidates_frame()
    return scan_status(folder, offset, clean_rows(), cand)


def allowed_files(status: pd.DataFrame, block: int, position: int, row: int) -> tuple:
    ok = status[(status["block"] == block) & (status["position"] == position)
                & (status["row"] == row) & (status["status"] != "excluded")]
    return tuple(sorted(ok["path"].astype(str).unique()))


def rows_on_carrier(block: int) -> int:
    """How many rows of pots this carrier has (1 for the usual single row; 2 for e.g. 4×2 = 8)."""
    u = SS.union
    if u is None or "row" not in u:
        return 1
    r = u.loc[u["block"] == block, "row"]
    return int(r.max()) if len(r) else 1


def rescan_resolver(prefix: str, only_unresolved: bool = False) -> int:
    """One choice per carrier scanned more than once on a day: keep one scan, several, or none.
    The suggested scan (fewest pots reading zero, then the later scan) is pre-selected, but nothing
    counts as chosen until the user confirms it. Returns the number still unconfirmed."""
    cand = SS.candidates if SS.candidates is not None else candidates_frame()
    cr = pe_data.carrier_rescans(cand)
    open_ = pe_data.unresolved_rescans(cand, SS.decisions)
    open_keys = set(zip(open_["block"], open_["date"])) if len(open_) else set()
    rows = open_ if only_unresolved else cr
    multi_src = cand["source_instance"].nunique() > 1 if "source_instance" in cand else False
    for _, row in rows.iterrows():
        block, date = int(row["block"]), row["date"]
        sub = cand[(cand["block"] == block) & (cand["date"] == pd.Timestamp(date))]
        ch = pe_data.rescan_choices(cand, block, date)
        opts, suggested = {}, []
        for _, c in ch.iterrows():
            lab = (f"{pd.Timestamp(c['timestamp']):%H:%M:%S} — {c['pots']} pots"
                   + (f", {c['failed']} read 0" if c["failed"] else "")
                   + (f" [{c['source_instance']}]" if multi_src else "")
                   + ("  ✓ suggested" if c["suggested"] else ""))
            opts[lab] = (pd.Timestamp(c["timestamp"]), str(c["source_instance"]))
            if c["suggested"]:
                suggested = [lab]
        resolved = (block, date) not in open_keys
        default = suggested
        if resolved:
            sub_keys = {row_scan_key(r) for _, r in sub.iterrows()}
            kept = {(k[0], k[2]) for k, v in SS.decisions.items()
                    if v.get("action") == "keep" and k in sub_keys}
            default = [lab for lab, (ts, src) in opts.items() if (src, ts.isoformat()) in kept]
        cc = st.columns([5, 1])
        chosen = cc[0].pills(f"{'✅' if resolved else '⚠️'} Carrier **{block}** scanned {len(ch)}× on "
                             f"**{date}** — keep (pick one or more):", list(opts),
                             selection_mode="multi", default=default,
                             key=f"{prefix}cr_{block}_{date}",
                             help="Pick the good scan. Pick more than one only if the scans really "
                                  "differ and you want them all (each is kept as its own row, numbered "
                                  "in the `scan_of_day` column; graphs average them per plant). Pick "
                                  "none to drop all of them.")
        if not chosen:
            cc[0].caption("No scan picked: **all** of this carrier's scans that day will be dropped.")
        elif len(chosen) > 1:
            cc[0].caption(f"Keeping **{len(chosen)} scans**: each plant on this carrier gets "
                          f"{len(chosen)} rows for {date} (`scan_of_day` 1, 2 …).")
        if cc[1].button("Update" if resolved else "Confirm", key=f"{prefix}crbtn_{block}_{date}",
                        type="secondary" if resolved else "primary"):
            targets = {opts[lab] for lab in chosen}
            what = "; ".join(lab.replace("  ✓ suggested", "") for lab in chosen) or "drop all"
            for _, r in sub.iterrows():
                keep = (pd.Timestamp(r["timestamp"]), str(r["source_instance"])) in targets
                set_decision(row_scan_key(r), "keep" if keep else "drop",
                             note=f"carrier {block} {date}: {'keep' if keep else 'drop'} ({what})")
            rebuild()
            log(f"Carrier {block} {date}: keep {what}")
            st.rerun()
    return len(open_keys)


# =========================================================================== sidebar
st.sidebar.title("🌱 PhenoCleaning")
st.sidebar.caption("clean · explore · animate")

# A convenience default folder can be set locally via the PLANTEYE_DEFAULT_FOLDER environment
# variable (e.g. in run_app.bat); it is blank otherwise. Never hard-code a path — this ships publicly.
EXAMPLE = os.environ.get("PLANTEYE_DEFAULT_FOLDER", "")
folder = st.sidebar.text_input("Experiment folder", EXAMPLE,
                               help="A folder holding your TraitFinder exports (.csv or .zip) and, "
                                    "optionally, the 3D .ply files. Sub-folders are searched too. "
                                    "Several exports of one experiment? Put each in its own sub-folder "
                                    "named after it (e.g. Group2, Group3) inside this folder.")
with st.sidebar.expander("More inputs (optional)", expanded=False):
    ply_folder = st.text_input("3D .ply folder (blank = experiment folder)", "")
    design_path = st.text_input("Design workbook (.xlsx)", "")
    gt_path = st.text_input("Manual measurements (.xlsx)", "")

if st.sidebar.button("Load experiment", type="primary", width="stretch"):
    try:
        insts = pe_data.discover_instances(folder)
        if not insts:
            st.sidebar.error("No PlantEye exports (.csv/.zip) found under that folder.")
        else:
            SS.insts, SS.drop, SS.decisions = insts, set(), {}
            SS.splits, SS.hidden_factors, SS.gt_join, SS.anim = [], [], None, {}
            SS.design = (pe_design.load_workbook(design_path)
                         if design_path and Path(design_path).exists() else None)
            SS.gt = (pe_data.load_groundtruth(gt_path)
                     if gt_path and Path(gt_path).exists() else None)
            rebuild()
            log(f"Loaded {len(insts)} export(s) from {folder}; combined {len(SS.union)} rows")
    except Exception as e:
        st.sidebar.error(f"{type(e).__name__}: {e}")

if SS.union is not None:
    u0 = SS.union
    st.sidebar.success(f"{len(SS.insts)} export(s) · {u0['date'].nunique()} dates · "
                       f"{int((~u0['is_blank']).sum())} plant scans")
    if SS.decisions:
        st.sidebar.caption(f"✎ {len(SS.decisions)} decision(s) applied")

PLY_DIR = ply_folder or folder

_attach_server_logging()
with st.sidebar.expander("About / report a problem"):
    st.markdown(f"**{VERSION}**")
    st.download_button("⬇ Download logs for debugging", debug_bundle(),
                       f"phenocleaning-logs-{pd.Timestamp.now():%Y%m%d-%H%M}.zip", "application/zip",
                       key="dl_debug_logs", width="stretch",
                       help="One zip with the error logs and a diagnostics file (version, system, "
                            "library versions, what's loaded, your decisions). It includes folder and "
                            "file names but none of your measurement data. Email it to the developer.")
    st.caption(f"Logs are also kept in:  \n`{_log_dir()}`")
st.sidebar.caption(VERSION)

# =========================================================================== main
if SS.union is None:
    st.title("PhenoCleaning")
    st.info("Enter your **experiment folder** in the sidebar and press **Load experiment**.")
    st.markdown(
        "PhenoCleaning turns the exports of one PlantEye experiment — even split across several "
        "files — into one clean dataset, and lets you see it:\n\n"
        "1. **Data** — combine the exports, remove duplicate scans and blank pots.\n"
        "2. **Time-series** — trait trajectories on a true elapsed-days axis, and animate them.\n"
        "3. **3D viewer** — view any scan in true colour or 20 reflectance indices, spin it, and "
        "animate a plant growing over time or several plants side by side.\n"
        "4. **Export** — the cleaned dataset, your figures and animations, and a log of every decision.")
    st.subheader("How to organise your experiment folder")
    st.markdown(FOLDER_HELP)
    st.code(FOLDER_LAYOUT, language="text")
    st.markdown(
        "**Then:** check **Data → Duplicate scans** and confirm which scan to keep wherever a carrier was "
        "scanned twice on a day (the combined CSV can't be downloaded until you have), remove any rogue "
        "scans in **Advanced → Remove scans**, and optionally split your treatment label into factors in "
        "**Advanced → Factors**.\n\n"
        "**3D files:** both the *processed* whole-carrier files (`…_full.ply`) and the per-plant *unit* "
        "files (`…_unitfile_<column>_<row>.ply`) work.")
    st.stop()

tabs = st.tabs(["Data", "Time-series", "3D viewer", "Export", "Advanced"])

# ---- Data -----------------------------------------------------------------
with tabs[0], guard("the Data tab"):
    u = SS.union
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Plant scans", int((~u["is_blank"]).sum()))
    c2.metric("Dates", u["date"].nunique())
    c3.metric("Carriers", u["block"].nunique())
    c4.metric("Blank-pot rows", int(u["is_blank"].sum()))
    st.caption("Dates: " + ", ".join(pd.Timestamp(d).strftime("%d %b") for d in sorted(u["date"].unique())))

    st.subheader("Blank pots")
    rb = st.toggle("Remove blank pots from plots and exports", value=SS.remove_blanks,
                   help="Rows whose genotype or treatment is 'Blank' (empty carrier slots).")
    if rb != SS.remove_blanks:
        SS.remove_blanks = rb
        log(f"Blank pots {'removed' if rb else 'kept'}")

    st.subheader("Duplicate scans")
    over = pe_data.overlapping_records([i for i in SS.insts if i.name not in SS.drop])
    if len(over):
        st.write(f"**{len(over)}** record(s) appear in more than one export. Identical copies are merged "
                 "automatically; where values differ they are different scans — choose below.")
        with st.expander("Show these records"):
            st.dataframe(over, width="stretch", hide_index=True)
    cand = SS.candidates if SS.candidates is not None else candidates_frame()
    cr = pe_data.carrier_rescans(cand)
    if not len(cr) and not len(over):
        st.success("No duplicate scans found.")
    if len(cr):
        n_open = len(pe_data.unresolved_rescans(cand, SS.decisions))
        msg = (f"**{len(cr)}** carrier-day(s) were scanned more than once. For each, choose the scan to keep "
               "(or drop them all if none is good) and press **Confirm**. The suggested scan — fewest pots "
               "reading zero, then the later scan — is pre-selected.")
        if n_open:
            st.warning(msg + f"  \n**{len(cr) - n_open} of {len(cr)} confirmed** — the combined CSV can be "
                       "downloaded once all are confirmed.")
        else:
            st.success(f"All {len(cr)} duplicate carrier-day(s) confirmed. You can change any choice below.")
        rescan_resolver("d_")

    summ = pe_data.instance_summary(SS.insts)
    with st.expander(f"Exports in this experiment ({len(SS.insts)}) — choose which to combine"):
        st.dataframe(summ, width="stretch", hide_index=True)
        st.caption("An older, smaller copy of another export is excluded automatically.")
        drop = set()
        for i, ins in enumerate(SS.insts):
            if ins.is_stale:
                continue
            note = summ.loc[summ["export"] == ins.name, "note"].iloc[0]
            if not st.checkbox(ins.name, value=(ins.name not in SS.drop), key=f"inc_{i}",
                               help=note or "included"):
                drop.add(ins.name)
        if drop != SS.drop:
            SS.drop = drop
            rebuild()
            log(f"Exports excluded: {sorted(drop) or 'none'}")
            st.rerun()
    with st.expander("How should my experiment folder look?"):
        st.markdown(FOLDER_HELP)
        st.code(FOLDER_LAYOUT, language="text")

# ---- Time-series ----------------------------------------------------------
with tabs[1], guard("the Time-series tab"):
    u = clean_rows().copy()
    traits = pe_data.measure_columns(u)
    if not traits:
        st.warning("No numeric traits found.")
    else:
        pref = next((t for t in traits if "digital biomass" in t.lower()), traits[0])
        facs = factor_options(u)
        c1, c2, c3 = st.columns(3)
        trait = c1.selectbox("Trait", traits, index=traits.index(pref))
        gopts = ["(none)"] + facs
        groupby = c2.selectbox("Group by", gopts, index=min(1, len(gopts) - 1),
                               help="Any label column, including factors you defined in Advanced → Factors.")
        filt = c3.selectbox("Filter by", ["(all)"] + facs)
        d = u
        if filt != "(all)":
            lv_all = sorted(d[filt].dropna().astype(str).unique())
            lv = st.multiselect(f"Show only these {filt} levels", lv_all, default=lv_all[:1], key=f"ts_filt_lv_{filt}")
            d = d[d[filt].astype(str).isin(lv)]
        grp = groupby if groupby != "(none)" else None
        if grp:
            g_all = sorted(d[grp].dropna().astype(str).unique())
            g_keep = st.multiselect(f"{grp} groups to show", g_all, default=g_all, key=f"ts_groups_{grp}")
            d = d[d[grp].astype(str).isin(g_keep)]
        d = d.dropna(subset=[trait]).copy()

        o1, o2, o3, o4 = st.columns([1.1, 1.6, 1.1, 1.2])
        show_pts = o1.checkbox("Show individual scans", value=False,
                               help="Each scan of the plotted plants, in its group's colour.")
        omode = o2.radio("Suggested outliers", ["Off", "Highlight", "Exclude"], horizontal=True,
                         help="Off = plot every scan as it is. Highlight = ring the scans that sit far "
                              "from the same plant's neighbouring dates. Exclude = leave them out of the "
                              "points and the means (nothing is removed from your data).")
        sens = o3.slider("Sensitivity", 1, 10, 5, disabled=omode == "Off",
                         help="Higher flags more scans.")
        no_zero = o4.checkbox("Leave out zero readings", value=False,
                              help="Scans where this trait reads 0 (usually a failed scan or an empty pot).")
        if no_zero:
            d = d[pd.to_numeric(d[trait], errors="coerce") != 0]
        flagged = None
        if omode != "Off":
            flagged = pe_qc.timeseries_outliers(clean_rows()[lambda x: ~x["is_blank"]], trait,
                                                k=6.0 - 0.45 * sens)
            in_plot = (flagged.merge(d[["unit", "date"]].assign(date=d["date"].dt.strftime("%Y-%m-%d"))
                                     .drop_duplicates(),
                                     on=["unit", "date"]) if len(flagged) else flagged)
            st.caption(f"{len(in_plot)} suggested outlier scan(s) among the plotted plants "
                       f"({'ringed in red' if omode == 'Highlight' else 'left out'}). Review or drop them "
                       "for good in **Advanced → Outliers**.")

        with st.expander("Figure size, text & export (publication quality)"):
            f1, f2, f3, f4 = st.columns(4)
            width_cm = f1.number_input("Width (cm)", 5.0, 40.0, 17.0, 0.5,
                                       help="Single journal column ≈ 8.5 cm, full page width ≈ 17.5 cm.")
            shape = f2.selectbox("Aspect ratio", ["16:9 (wide)", "4:3", "3:2", "1:1 (square)", "3:4 (tall)",
                                                  "Custom height"], index=2)
            ratios = {"16:9 (wide)": 16 / 9, "4:3": 4 / 3, "3:2": 3 / 2, "1:1 (square)": 1.0, "3:4 (tall)": 3 / 4}
            if shape == "Custom height":
                height_cm = f3.number_input("Height (cm)", 3.0, 40.0, 10.0, 0.5)
            else:
                height_cm = round(width_cm / ratios[shape], 2)
                f3.metric("Height (cm)", f"{height_cm:g}")
            font_pt = f4.number_input("Text size (pt)", 5.0, 24.0, 10.0, 0.5,
                                      help="Axis labels; tick labels and legend scale with it. Journals "
                                           "usually ask for 7–10 pt at final size.")
            g1, g2, g3, g4 = st.columns(4)
            line_w = g1.number_input("Line width", 0.3, 5.0, 1.5, 0.1)
            marker = g2.number_input("Marker size", 0.0, 15.0, 5.0, 0.5)
            point_size = g3.number_input("Scan point size", 1.0, 60.0, 10.0, 1.0)
            dpi = g4.select_slider("PNG resolution (dpi)", [150, 300, 600, 1200], 300)
            h1, h2, h3, h4 = st.columns(4)
            err_lab = h1.selectbox("Error bars", list(pe_viz.ERROR_BARS))
            x_mode = h2.selectbox("x axis", ["Days elapsed", "Scan date"])
            legend = h3.selectbox("Legend", ["right", "below", "best", "none"])
            grid = h4.checkbox("Grid lines", value=False)
            title = st.text_input("Title (optional)", "")
            j1, j2 = st.columns(2)
            xlabel = j1.text_input("x-axis label", "days elapsed" if x_mode == "Days elapsed" else "scan date")
            ylabel = j2.text_input("y-axis label", trait)
        if d.empty:
            st.warning("No data for that selection.")
        else:
            fig, summary = pe_viz.trait_figure(
                d, trait, grp, show_points=show_pts, flagged=flagged, outlier_mode=omode.lower()
                if omode != "Off" else "show", error=pe_viz.ERROR_BARS[err_lab],
                x_mode="date" if x_mode == "Scan date" else "days", width_cm=width_cm, height_cm=height_cm,
                font_pt=font_pt, line_w=line_w, marker=marker, point_size=point_size, title=title,
                xlabel=xlabel, ylabel=ylabel, legend=legend, grid=grid)
            st.image(pe_viz.figure_bytes(fig, "png", dpi=110))
            err_note = {"se": "± standard error", "sd": "± standard deviation",
                        "ci95": "± 95% confidence interval", "none": ""}[pe_viz.ERROR_BARS[err_lab]]
            st.caption(f"Mean {err_note} per date; true spacing between scan dates. The preview is drawn at "
                       "the size you set, so text appears exactly as in the exported file.")
            stem = re.sub(r"[^A-Za-z0-9]+", "_", f"{trait} by {grp or 'all'}").strip("_")
            e1, e2, e3, e4, e5 = st.columns(5)
            for col, fmt, mime in ((e1, "png", "image/png"), (e2, "pdf", "application/pdf"),
                                   (e3, "svg", "image/svg+xml")):
                try:   # each format on its own, so one failing never takes the others down
                    data = pe_viz.figure_bytes(fig, fmt, dpi=dpi)
                except Exception:
                    f = log_error(f"{fmt.upper()} figure export")
                    col.caption(f"⚠ {fmt.upper()} export unavailable in this build ({__version__}). "
                                "Please report it" + (f" — log: `{f.name}`" if f else "") + ".")
                    continue
                col.download_button(f"⬇ {fmt.upper()}", data, f"{stem}.{fmt}", mime)
            e4.download_button("⬇ Plotted values (CSV)", summary.to_csv(index=False).encode("utf-8"),
                               f"{stem}_summary.csv", "text/csv")
            if e5.button("🎞 Animate"):
                name = stem + ".gif"
                with st.spinner("Rendering…"):
                    keep_anim(name, pe_viz.trait_chart_gif(d, trait, grp))
                SS["last_chart"] = name
            if SS.get("last_chart"):
                show_anim(SS["last_chart"])

# ---- 3D viewer ------------------------------------------------------------
with tabs[2], guard("the 3D viewer"):
    has_ply = bool(PLY_DIR) and Path(PLY_DIR).exists()
    inv = ply_inventory(PLY_DIR) if has_ply else pd.DataFrame()
    skipped = ply_skipped(PLY_DIR) if has_ply else []
    with st.expander("Which 3D files can I use?"):
        st.markdown(pe_data.PLY_NAME_HELP)
        if skipped:
            st.warning(f"{len(skipped)} `.ply` file(s) skipped because their names aren't recognised:")
            st.dataframe(pd.DataFrame({"skipped file": skipped}), width="stretch", hide_index=True)
    if not len(inv):
        st.info("No 3D `.ply` files found. They are searched for in the experiment folder, or set a "
                "**3D .ply folder** under *More inputs* in the sidebar."
                + (f" ({len(skipped)} `.ply` file(s) were found but have unrecognised names — see above.)"
                   if skipped else ""))
    else:
        off, frac, auto = ply_offset(PLY_DIR)
        inv = ply_inventory(PLY_DIR, off)
        with st.expander(f"3D time matching — scanner clock UTC{off:+g} h "
                         f"({'auto-detected' if auto else 'set by you'}; {frac:.0%} of 3D files matched a scan)",
                         expanded=frac < 0.5):
            st.caption("3D file names carry UTC time, the exports carry the scanner's local time. The offset "
                       "that lines up the most 3D files with a scan is detected automatically (it handles any "
                       "time zone and daylight saving). Override it only if the match rate is low.")
            m1, m2 = st.columns([1, 1])
            man = m1.number_input("Offset from UTC (hours)", -12.0, 14.0, float(off), 0.5, key="ply_off_in")
            if m2.button("Use this offset"):
                SS.ply_offset = man
                log(f"3D time offset set to UTC{man:+g} h")
                st.rerun()
            if not auto and m2.button("Back to auto-detect"):
                SS.ply_offset = None
                st.rerun()
        if frac < 0.5:
            st.warning("Most 3D files don't line up with a scan in the exports, so the viewer can't tell which "
                       "of them survived the cleaning. Check the time offset above, or that these 3D files "
                       "belong to the loaded exports.")
        status = current_status(PLY_DIR, off)
        plants = pe_viz.plant_options(inv, SS.union, status)
        n_ex = int((status["status"] == "excluded").sum())
        if n_ex:
            st.caption(f"Showing the scans in your cleaned data — {n_ex} plant scan(s) left out "
                       "(duplicates, blank pots or scans you dropped).")
        series_of = lambda p: plant_series(
            PLY_DIR, off, int(p["block"]), int(p["position"]), int(p["row"]),
            allowed_files(status, int(p["block"]), int(p["position"]), int(p["row"])),
            rows_on_carrier(int(p["block"])))
        mode = st.radio("Show", ["One scan", "Growth over time", "Compare plants"], horizontal=True,
                        label_visibility="collapsed")
        menu = pe_viz.index_menu()
        c1, c2 = st.columns([2, 3])
        key = menu[c1.selectbox("Colour by", list(menu))]
        c2.caption(pe_viz.INDICES[key][5])
        with st.expander("Display options"):
            o1, o2, o3, o4 = st.columns(4)
            bg = o1.selectbox("Background", ["white", "black"])
            psize = o2.slider("Point size", 0.3, 4.0, 1.2, 0.1)
            size = o3.select_slider("Image size (px)", [360, 480, 600, 720], 480)
            speed = o4.slider("Animation speed (frames/s)", 1, 20, 2)
            elev = st.slider("Camera tilt for 3D views (°)", -10, 60, 18)
        names = plants["label"].tolist()
        pick = lambda lab: plants.loc[plants["label"] == lab].iloc[0]
        short = lambda lab: lab.split("  (")[0]

        if mode == "One scan":
            a1, a2 = st.columns(2)
            plab = a1.selectbox("Plant", names)
            p = pick(plab)
            series = series_of(p)
            if not series:
                st.warning("No points for this plant.")
            else:
                dates = [f"{pd.Timestamp(t):%d %b %Y %H:%M}" for t, _ in series]
                di = a2.selectbox("Scan", range(len(dates)), format_func=lambda i: dates[i],
                                  index=len(dates) - 1)
                pts = series[di][1]
                st.image(pe_viz.still_views(pts, key, size=size, bg=bg, point_size=psize))
                if st.button("🎞 Spin it (turntable)"):
                    bar = st.progress(0.0)
                    name = f"{short(plab).split(' ')[0].replace(':', '-')}_{series[di][0]:%Y%m%d}_{key}_turntable.gif"
                    keep_anim(name, pe_viz.turntable_gif(pts, key=key, size=size, bg=bg, elev=elev,
                                                         point_size=psize * 1.5, fps=15,
                                                         progress=lambda f: bar.progress(min(f, 1.0))))
                    bar.empty()
                    SS["last_spin"] = name
                if SS.get("last_spin"):
                    show_anim(SS["last_spin"])

        elif mode == "Growth over time":
            g1, g2 = st.columns(2)
            plab = g1.selectbox("Plant", names, key="grow_plant")
            style = g2.radio("Animation", ["Top view", "Side view", "3D view", "Spinning through time"],
                             horizontal=True)
            if st.button("🎞 Make animation", type="primary"):
                p = pick(plab)
                series = series_of(p)
                if len(series) < 2:
                    st.warning("This plant has fewer than two scan dates.")
                else:
                    bar = st.progress(0.0)
                    cb = lambda f: bar.progress(min(f, 1.0))
                    stem = f"{short(plab).split(' ')[0].replace(':', '-')}_{key}"
                    if style == "Spinning through time":
                        name = f"{stem}_spin_through_time.gif"
                        gif = pe_viz.turntable_through_time_gif(series, key=key, size=size, bg=bg, elev=elev,
                                                                point_size=psize * 1.3, title=short(plab),
                                                                progress=cb)
                    else:
                        view = {"Top view": "top", "Side view": "side", "3D view": "3d"}[style]
                        name = f"{stem}_growth_{view}.gif"
                        gif = pe_viz.growth_gif(series, key=key, view=view, size=size, fps=speed, bg=bg,
                                                point_size=psize, elev=elev, title=short(plab), progress=cb)
                    bar.empty()
                    keep_anim(name, gif)
                    SS["last_growth"] = name
            if SS.get("last_growth"):
                show_anim(SS["last_growth"])

        else:
            chosen = st.multiselect("Plants to compare (up to 8)", names, max_selections=8,
                                    help="Tip: pick one plant per treatment to compare treatments.")
            view = st.radio("View", ["Top view", "Side view"], horizontal=True, key="cmp_view")
            if st.button("🎞 Make animation", type="primary", disabled=len(chosen) < 2):
                bar = st.progress(0.0)
                groups = []
                for lab in chosen:
                    p = pick(lab)
                    groups.append((short(lab), series_of(p)))
                name = f"compare_{len(chosen)}_plants_{key}.gif"
                keep_anim(name, pe_viz.compare_gif(groups, key=key, view="top" if view == "Top view" else "side",
                                                   size=max(240, size // 2), fps=speed, bg=bg,
                                                   point_size=psize * 0.7,
                                                   progress=lambda f: bar.progress(min(f, 1.0))))
                bar.empty()
                SS["last_cmp"] = name
            if len(chosen) < 2:
                st.caption("Pick at least two plants.")
            if SS.get("last_cmp"):
                show_anim(SS["last_cmp"])

# ---- Export ---------------------------------------------------------------
with tabs[3], guard("the Export tab"):
    u = clean_rows().copy()
    u["days_elapsed"] = (u["date"] - u["date"].min()).dt.days
    st.subheader("Cleaned dataset")
    cand_x = SS.candidates if SS.candidates is not None else candidates_frame()
    open_x = pe_data.unresolved_rescans(cand_x, SS.decisions)
    if len(open_x):
        st.warning(f"**Choose the scans to combine first.** {len(open_x)} carrier-day(s) were scanned more "
                   "than once and you haven't confirmed which scan to keep. Confirm each below (or under "
                   "**Data → Duplicate scans**); the download unlocks when all are confirmed.")
        rescan_resolver("x_", only_unresolved=True)
    derived = [n for s in SS.splits for n in s["names"] if n in u.columns]
    if derived:
        inc_f = st.checkbox(f"Include the grouping factors you defined ({', '.join(derived)})", value=True,
                            help="Added as extra columns, next to the label they were split from.")
        if inc_f:
            lead = [c for c in u.columns if c not in derived]
            at = lead.index("treatment") + 1 if "treatment" in lead else len(lead)
            u = u[lead[:at] + derived + lead[at:]]
        else:
            u = u.drop(columns=derived)
    else:
        st.caption("Tip: split a compound treatment label (e.g. `HT_BS_R1`) into separate factor columns in "
                   "**Advanced → Factors** — they are then added to this CSV and can be used to group plots.")
    n_drop = sum(v.get("action") == "drop" for v in SS.decisions.values())
    st.caption(f"{len(u)} rows · {u['block'].nunique()} carriers · {u['date'].nunique()} dates · blank pots "
               f"{'removed' if SS.remove_blanks else 'kept'} · {n_drop} scan(s) removed by your decisions.")
    st.download_button("⬇ Cleaned scans (CSV)", u.to_csv(index=False).encode("utf-8"),
                       "phenocleaning_combined.csv", "text/csv", type="primary", disabled=bool(len(open_x)))
    st.subheader("3D files")
    inv_x = ply_inventory(PLY_DIR) if PLY_DIR and Path(PLY_DIR).exists() else pd.DataFrame()
    if not len(inv_x):
        st.caption("No 3D `.ply` files found for this experiment.")
    else:
        stx = current_status(PLY_DIR, ply_offset(PLY_DIR)[0])
        n_files = stx.groupby("path")["status"].apply(lambda s: (s == "kept").any())
        st.caption(f"{int(n_files.sum())} of {len(n_files)} `.ply` file(s) hold plants in the cleaned "
                   "dataset. Copy just those to a new folder — your original files are not touched.")
        default_out = str(Path(PLY_DIR).parent / f"{Path(PLY_DIR).name}_cleaned_3d")
        out_dir = st.text_input("Copy to folder", default_out)
        if st.button("Copy kept 3D files"):
            if Path(out_dir).resolve() == Path(PLY_DIR).resolve():
                st.error("Choose a different folder from the original 3D folder.")
            else:
                bar = st.progress(0.0)
                lst = pe_data.copy_kept_ply(stx, out_dir, progress=lambda f: bar.progress(min(f, 1.0)))
                bar.empty()
                SS["ply_copy"] = (out_dir, lst)
                log(f"Copied {int(lst['copied'].sum())} kept .ply file(s) to {out_dir}; "
                    f"{int((~lst['copied']).sum())} left out")
        if SS.get("ply_copy"):
            od, lst = SS["ply_copy"]
            st.success(f"Copied {int(lst['copied'].sum())} file(s) to `{od}` — "
                       f"{int((~lst['copied']).sum())} left out. The list is saved there as "
                       "`ply_file_list.csv`.")
            with st.expander("Which files were left out, and why"):
                st.dataframe(lst[~lst["copied"]] if (~lst["copied"]).any() else lst,
                             width="stretch", hide_index=True)
    if SS.anim:
        st.subheader("Animations made this session")
        for name, data in SS.anim.items():
            st.download_button(f"⬇ {name}", data, name, "image/gif", key=f"exp_{name}")
    with st.expander("Decisions and audit log"):
        if SS.decisions:
            dec = pd.DataFrame([{"source_instance": k[0], "unit": k[1], "timestamp": k[2], **v}
                                for k, v in SS.decisions.items()])
            st.download_button("⬇ Decisions (CSV)", dec.to_csv(index=False).encode("utf-8"),
                               "phenocleaning_decisions.csv", "text/csv")
        if SS.gt_join is not None:
            st.download_button("⬇ Manual-measurement join (CSV)",
                               SS.gt_join.to_csv(index=False).encode("utf-8"),
                               "phenocleaning_groundtruth.csv", "text/csv")
        st.code("\n".join(SS.audit) or "(empty)", language="text")
        st.download_button("⬇ Audit log", ("\n".join(SS.audit)).encode("utf-8"),
                           "phenocleaning_audit.txt", "text/plain")

# ---- Advanced -------------------------------------------------------------
def render_finding(f):
    with st.expander(f"{SEV.get(f.severity, '⬜')} **{f.check}** — {f.message}",
                     expanded=f.severity in ("blocker", "flag")):
        if f.explain:
            st.markdown(f.explain)
        if f.detail is not None and len(f.detail):
            st.dataframe(f.detail, width="stretch", hide_index=True)


with tabs[4], guard("the Advanced tab"):
    st.caption("Optional tools for deeper checks. Nothing here is needed for a basic clean-up.")
    sub = st.tabs(["Factors", "Remove scans", "Quality checks", "Missing data", "Outliers", "Design",
                   "Ground truth"])
    sub = [sub[0], *sub[2:], sub[1]]   # keep the existing section order below; Remove scans is last
    with sub[0], guard("Advanced → Factors"):
        st.subheader("Define grouping factors (optional)")
        if SS.flash:
            st.success(SS.flash)
            SS.flash = None
        st.caption("Every export carries two labels — **genotype** and **treatment** — that you set in the "
                   "portal. If you encoded several factors into one label (e.g. `61.BC.HT`), split it here "
                   "into named factors. Nothing about your design is assumed.")
        if SS.base_union is None:
            st.info("Combine the exports first (press Load experiment), then define factors here.")
        else:
            base = SS.base_union
            splittable = [c for c in ("treatment", "genotype") if c in base.columns]
            colc = st.selectbox("Label to split", splittable)
            if colc:
                sug = pe_data.suggest_split(base[colc])
                samples = base.loc[~base["is_blank"], colc].dropna().unique()[:6]
                st.write("Examples: " + ", ".join(map(str, samples)))
                if sug:
                    st.write(f"Looks like **{sug['n_parts']} parts** split by `{sug['delim']}`.")
                    delim = st.text_input("Delimiter", sug["delim"])
                    names = st.text_input("Names for the parts (comma-separated)",
                                          ", ".join(f"{colc}_{i+1}" for i in range(sug["n_parts"])))
                    if st.button("Add these factors"):
                        nm = [x.strip() for x in names.split(",") if x.strip()]
                        SS.splits = [s for s in SS.splits if s["col"] != colc] + \
                                    [{"col": colc, "delim": delim, "names": nm}]
                        rebuild()
                        log(f"Split {colc} by '{delim}' into {nm}")
                        # rerun so the Time-series and Export tabs (drawn earlier in this run) pick up
                        # the new columns straight away — without it the export still served the old CSV
                        SS.flash = (f"Added factors: {', '.join(nm)} — now available in Time-series → Group by "
                                    "/ Filter by, and added to the exported CSV.")
                        st.rerun()
                else:
                    st.caption("No consistent delimiter found — this label is already a single factor.")
            if SS.splits:
                st.markdown("**Defined factor sets** — delete any specific set you don't need:")
                for i, sp in enumerate(list(SS.splits)):
                    dc = st.columns([5, 1])
                    dc[0].write(f"• from `{sp['col']}` → {', '.join(sp['names'])}")
                    if dc[1].button("Delete set", key=f"delset_{i}"):
                        removed = SS.splits[i]["names"]
                        SS.splits = [s for j, s in enumerate(SS.splits) if j != i]
                        SS.hidden_factors = [h for h in SS.hidden_factors if h not in removed]
                        rebuild()
                        log(f"Deleted factor set from {sp['col']}: {removed}")
                        st.rerun()

                derived = [n for s in SS.splits for n in s["names"]]
                if derived:
                    st.caption("Or remove individual factors (keep the rest of the set):")
                    hide = st.multiselect("Factors to remove", derived,
                                          default=[h for h in SS.hidden_factors if h in derived],
                                          key="hide_factors")
                    if st.button("Apply factor removals"):
                        SS.hidden_factors = hide
                        rebuild()
                        log(f"Removed individual factors: {hide or '(none)'}")
                        st.rerun()

                if st.button("Clear ALL defined factors"):
                    SS.splits, SS.hidden_factors = [], []
                    rebuild()
                    st.rerun()
            st.markdown("**Factors available for grouping:** " + ", ".join(factor_options(SS.union)))

    with sub[1], guard("Advanced → Quality checks"):
        st.subheader("Quality checks")
        if SS.union is None:
            st.info("Combine the exports first (press Load experiment).")
        else:
            measures = pe_data.measure_columns(SS.union[~SS.union["is_blank"]])
            findings = pe_qc.run_all(SS.union, SS.gt_join, factors=factor_options(SS.union),
                                     measures=measures, candidates=SS.candidates)
            core = [f for f in findings if not f.advanced]
            adv = [f for f in findings if f.advanced]
            nflag = sum(f.severity in ("blocker", "flag") for f in core)
            st.write(f"**{len(core)} main checks** · {nflag} need attention")
            for f in core:
                render_finding(f)
            with st.expander(f"Advanced checks ({len(adv)}) — timing, confounds, label & completeness detail"):
                st.caption("These are context checks. Each explains what it means, why it matters, and what to do.")
                for f in adv:
                    render_finding(f)

    with sub[2], guard("Advanced → Missing data"):
        st.subheader("Missing-data analysis")
        if SS.union is None:
            st.info("Combine the exports first (press Load experiment).")
        else:
            st.caption("How complete is each measurement? A measure that produced **no values at all** shows "
                       "as EMPTY. No value is ever imputed — a whole group or occasion missing is structural "
                       "(MNAR), not a gap to fill.")
            st.markdown("#### Scan traits (per scan)")
            u = SS.union[~SS.union["is_blank"]].copy()
            traits = pe_data.measure_columns(u)
            summ = pe_qc.missingness_summary(u, traits)
            empty = summ[summ["status"].str.startswith("EMPTY")]
            if len(empty):
                st.error(f"{len(empty)} scan trait(s) produced no values: {', '.join(empty['measure'])}")
            st.dataframe(summ, width="stretch", hide_index=True)
            c1, c2 = st.columns(2)
            incomplete = summ[summ["missing"] > 0]["measure"].tolist() or traits
            meas = c1.selectbox("Trait", incomplete, key="miss_trait")
            grp = c2.selectbox("Group by", ["date"] + factor_options(u), key="miss_grp")
            if meas and grp:
                bg = pe_qc.missingness_by_group(u, meas, grp)
                st.dataframe(bg, width="stretch", hide_index=True)
                if bg["pct_missing"].eq(100).any():
                    gone = bg[bg["pct_missing"] == 100][grp].astype(str).tolist()
                    st.warning(f"'{meas}' is entirely absent for {grp} = {', '.join(gone)} — if that group was "
                               f"harvested or not scanned, this is structural, not imputable.")
                st.caption("To see or act on the *specific* zero/missing scans, use the **Outliers** tab.")

            if SS.gt is not None:
                st.markdown("#### Manual measurements (per measured pot)")
                st.caption("Scoped to the pots actually measured (not every scan row). Free-text entries (e.g. "
                           "SPAD noted on another instrument) count as missing-numeric here and are flagged in "
                           "the Ground truth tab — directly answering 'did this measure produce results?'.")
                mm = pe_data.manual_measures(SS.gt)
                gsumm = pe_qc.missingness_summary(SS.gt, mm)
                gsumm["measure"] = gsumm["measure"].str.replace("manual_", "", regex=False)
                st.dataframe(gsumm, width="stretch", hide_index=True)
                for c in mm:
                    if SS.gt[c].notna().sum() == 0:
                        st.error(f"**{c.replace('manual_','')}** produced no numeric values at all.")

    with sub[3], guard("Advanced → Outliers"):
        st.subheader("Zero, missing, and outlier scans")
        st.caption("Suggestions only — choose which scans to drop, keep or relabel. Every decision is "
                   "logged and can be cleared.")
        if SS.decisions:
            cc1, cc2 = st.columns([3, 1])
            cc1.write(f"**{len(SS.decisions)}** decision(s) currently applied.")
            if cc2.button("Clear all decisions"):
                SS.decisions = {}
                rebuild()
                log("Cleared all curation decisions")
                st.rerun()
        if SS.union is not None:
            u = SS.union.copy()
            traits = pe_data.measure_columns(u)
            if traits:
                pref = next((t for t in traits if "digital biomass" in t.lower()), traits[0])
                cc1, cc2 = st.columns([2, 1])
                trait = cc1.selectbox("Trait to inspect", traits, index=traits.index(pref), key="cur_trait")
                sens = cc2.slider("outlier sensitivity", 1, 10, 5, key="cur_sens",
                                  help="Higher flags more points as candidate outliers. These are only "
                                       "*suggestions* — you still choose which to act on below.")
                k = 6.0 - 0.45 * sens  # sens 1 → k5.55 (few), sens 10 → k1.5 (many)
                an = pe_qc.anomalous_scans(u[~u["is_blank"]], trait)
                outl = pe_qc.timeseries_outliers(u[~u["is_blank"]], trait, k=k)
                st.write(f"**{len(an)}** zero/missing scan(s) · **{len(outl)}** candidate outlier(s) for `{trait}` "
                         f"(sensitivity {sens})")
                if len(an):
                    with st.expander(f"Zero / missing scans ({len(an)})", expanded=False):
                        st.dataframe(an, width="stretch", hide_index=True)
                if len(outl):
                    with st.expander(f"Time-series outliers ({len(outl)}) — far from a plant's neighbours",
                                     expanded=True):
                        st.dataframe(outl, width="stretch", hide_index=True)

                # act on selected scans
                pool = pd.concat([an, outl], ignore_index=True) if len(an) or len(outl) else pd.DataFrame()
                if len(pool):
                    pool = pool.drop_duplicates(subset=[c for c in ["unit", "timestamp"] if c in pool])
                    pool["label"] = (pool["unit"].astype(str)
                                     + " @ " + pool["date"].astype(str) + " " + pool["timestamp"].str[-8:]
                                     + " [" + pool.get("source_instance", "").astype(str) + "]")
                    chosen = st.multiselect("Select scans to act on", pool["label"].tolist(), key="cur_sel")
                    action = st.radio("Action", ["drop", "keep", "relabel"], horizontal=True, key="cur_act")
                    new_label = ""
                    if action == "relabel":
                        field = st.selectbox("Field to relabel", ["treatment", "genotype"], key="cur_field")
                        new_label = st.text_input("New value", key="cur_newlabel")
                    if st.button("Apply to selected scans", key="cur_apply") and chosen:
                        picks = pool[pool["label"].isin(chosen)]
                        for _, r in picks.iterrows():
                            k = pe_data.scan_key(r.get("source_instance", ""), r["unit"],
                                                 pd.Timestamp(r["timestamp"]))
                            if action == "relabel":
                                set_decision(k, "relabel", new_label=new_label, field=field)
                            else:
                                set_decision(k, action)
                        rebuild()
                        log(f"{action} applied to {len(picks)} scan(s) via curate ({trait})")
                        st.success(f"{action.title()} applied to {len(picks)} scan(s).")
                        st.rerun()

    with sub[4], guard("Advanced → Design"):
        st.subheader("Design workbook (optional)")
        if SS.design is None:
            st.info("No design workbook loaded — the app just uses the labels in your scans, which is fine. "
                    "Add one in the sidebar to browse the layout, confirm the treatment factors, and see the "
                    "replication and any split-plot hierarchy.")
        else:
            wb = SS.design
            st.caption(f"Sheets: {', '.join(wb.sheets)}")
            default_sheet = pe_design.suggest_layout_sheet(wb)
            sheet = st.selectbox("Layout sheet", wb.sheets,
                                 index=wb.sheets.index(default_sheet) if default_sheet in wb.sheets else 0)
            df = wb.frames[sheet]
            st.write(f"{len(df)} rows · {len(df.columns)} columns")
            sugg = pe_design.suggest_factors(df)
            facs = st.multiselect("Treatment factor columns (edit if the guess is off)",
                                  list(df.columns), default=sugg,
                                  help="These are the columns the app treats as your treatment factors. "
                                       "Coordinate/id columns are excluded from the guess.")
            if facs:
                summ = pe_design.summarize(df, facs)
                for c, lv in summ["levels"].items():
                    st.markdown(f"- **{c}**: {', '.join(lv)}  ({len(lv)})")
                reps = summ["reps"]
                st.write(f"Full-factorial cells: **{summ['cells']}** · units: **{summ['units']}**"
                         + (f" · ≈ **{reps:.1f}** rep(s) per cell" if reps else ""))
            with st.expander("Split-plot / nesting hierarchy (optional)"):
                st.caption("Pick the layout columns that nest, coarsest first (e.g. table → mainunit → pot). "
                           "The app reports which factors each level holds constant — i.e. its experimental unit.")
                nest = st.multiselect("Nesting columns (coarsest → finest)", list(df.columns))
                if nest and facs:
                    st.json(pe_design.detect_hierarchy(df, nest, facs))
            st.info("If a factor was applied to whole groups — a tray, a bench, a growth room — rather than "
                    "randomised per plant, its **main effect is confounded** with that group, and no software "
                    "can separate them from the data alone. Declare it, and lean on the interactions that "
                    "difference the group effect out.")

    with sub[5], guard("Advanced → Ground truth"):
        st.subheader("Validate against manual measurements (optional)")
        if SS.gt is None:
            st.info("No manual-measurement workbook loaded. Add one in the sidebar (e.g. Plant Height / "
                    "SPAD) to validate the scans. Everything else runs without it.")
        elif SS.gt_join is None:
            st.info("Combine the exports first (press Load experiment) so the manual data can be joined to scans.")
        else:
            gj = SS.gt_join
            hk, hc = gj.attrs.get("height_key"), gj.attrs.get("scan_height_col")
            if hk and hc:
                m = gj[gj["scan_height"].notna() & gj[hk].notna()].copy()
                st.write(f"**{len(gj)}** measured pot(s) joined by carrier:position · **{len(m)}** with both "
                         f"manual `{hk.replace('manual_','')}` and scan `{hc}`")
                if len(m):
                    diff = m["scan_height"] - m[hk]
                    mean_ = (m["scan_height"] + m[hk]) / 2
                    bias, sd = diff.mean(), diff.std()
                    c1, c2, c3 = st.columns(3)
                    c1.metric("mean bias (scan − manual)", f"{bias:+.1f}")
                    c2.metric("SD of difference", f"{sd:.1f}")
                    c3.metric("95% limits of agreement", f"{bias-1.96*sd:.0f} … {bias+1.96*sd:.0f}")
                    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.2))
                    a1.scatter(m[hk], m["scan_height"], s=14, alpha=0.6)
                    lims = [0, max(m["scan_height"].max(), m[hk].max()) * 1.05]
                    a1.plot(lims, lims, "0.6", ls="--", lw=1, label="1:1")
                    a1.set_xlabel(f"manual {hk.replace('manual_','')}"); a1.set_ylabel(f"scan {hc}")
                    a1.set_title("scan vs manual"); a1.legend(fontsize=8)
                    a2.scatter(mean_, diff, s=14, alpha=0.6, color="#c60")
                    for y, ls in [(bias, "-"), (bias + 1.96 * sd, "--"), (bias - 1.96 * sd, "--")]:
                        a2.axhline(y, color="0.4", ls=ls, lw=1)
                    a2.set_xlabel("mean of scan & manual"); a2.set_ylabel("scan − manual")
                    a2.set_title("Bland–Altman")
                    fig.tight_layout(); st.pyplot(fig); plt.close(fig)
                    st.caption("Agreement (bias + limits), not R². A large, roughly constant offset means the "
                               "scan measures from a different reference plane than the ruler — a calibration "
                               "you can apply, not a sensor failure.")
            else:
                st.caption("No height column detected to validate; see the measures below.")
            for c in (pe_data.manual_measures(SS.gt) if SS.gt is not None else []):
                n_num = gj[c].notna().sum()
                n_nn = int(gj.get(c + "__nonnumeric", pd.Series(dtype=bool)).sum())
                if n_num == 0:
                    st.warning(f"**{c.replace('manual_','')}** produced no numeric values"
                               + (f" ({n_nn} free-text entr(y/ies), coerced and flagged)." if n_nn else "."))

    with sub[6], guard("Advanced → Remove scans"):
        st.subheader("Remove bad scans")
        st.caption("Take scans out of the analysis: a rogue or test scan, a carrier scanned by mistake, a dead "
                   "or mislabelled plant, or a whole bad day. Choose any combination below — an empty box "
                   "means *all*. Your files are never changed; every removal is logged and can be restored.")
        cand_r = SS.candidates if SS.candidates is not None else candidates_frame()
        if cand_r.empty:
            st.info("Load an experiment first.")
        else:
            cr_dates = sorted(cand_r["date"].dropna().unique())
            per_date = cand_r.groupby("date")["block"].nunique()
            date_lab = {f"{pd.Timestamp(d):%Y-%m-%d}  ({per_date[d]} carrier{'s' if per_date[d] != 1 else ''})": d
                        for d in cr_dates}
            r1, r2, r3 = st.columns(3)
            pick_d = r1.multiselect("Date(s)", list(date_lab), key="rm_dates")
            pick_b = r2.multiselect("Carrier(s) / barcode(s)", sorted(cand_r["block"].unique().tolist()),
                                    key="rm_blocks")
            pool_p = cand_r if not pick_b else cand_r[cand_r["block"].isin(pick_b)]
            pick_p = r3.multiselect("Plant(s)  barcode:column:row", sorted(pool_p["unit"].astype(str).unique()),
                                    key="rm_plants", help="Leave empty for every plant on the chosen carriers.")
            sel = cand_r
            if pick_d:
                sel = sel[sel["date"].isin([date_lab[x] for x in pick_d])]
            if pick_b:
                sel = sel[sel["block"].isin(pick_b)]
            if pick_p:
                sel = sel[sel["unit"].astype(str).isin(pick_p)]
            if not (pick_d or pick_b or pick_p):
                st.info("Pick at least one date, carrier or plant. Tip: **Advanced → Quality checks** flags "
                        "dates on which only a few carriers were scanned.")
            elif sel.empty:
                st.warning("No scans match that selection.")
            else:
                already = {k for k, v in SS.decisions.items() if v.get("action") == "drop"}
                keys = [row_scan_key(r) for _, r in sel.iterrows()]
                new = [k for k in keys if k not in already]
                size = pe_data.size_column(sel)
                show = [c for c in ["unit", "timestamp", "genotype", "treatment", size, "source_instance"]
                        if c and c in sel.columns]
                st.write(f"**{len(sel)}** scan row(s) match ({len(sel) - len(new)} already removed):")
                st.dataframe(sel[show].sort_values(["timestamp", "unit"]), width="stretch", hide_index=True,
                             height=min(320, 38 + 35 * len(sel)))
                reason = st.text_input("Reason (saved in the decision log)", "rogue scan", key="rm_reason")
                if st.button(f"Remove {len(new)} scan(s)", type="primary", disabled=not new, key="rm_go"):
                    for k in new:
                        set_decision(k, "drop", note=f"removed: {reason}")
                    rebuild()
                    log(f"Removed {len(new)} scan(s) ({reason}): dates={pick_d or 'all'} "
                        f"carriers={pick_b or 'all'} plants={pick_p or 'all'}")
                    st.rerun()

            drops = {k: v for k, v in SS.decisions.items() if v.get("action") == "drop"}
            if drops:
                st.markdown(f"#### Currently removed ({len(drops)} scan rows)")
                tab = pd.DataFrame([{"plant": k[1], "scan time": k[2].replace("T", " "), "export": k[0],
                                     "why": v.get("note", "")} for k, v in drops.items()])
                st.dataframe(tab.sort_values(["scan time", "plant"]), width="stretch", hide_index=True,
                             height=min(320, 38 + 35 * len(tab)))
                labs = {f"{k[1]} @ {k[2].replace('T', ' ')} [{k[0]}]": k for k in drops}
                back = st.multiselect("Restore these scans", list(labs), key="rm_restore")
                if st.button("Restore selected", disabled=not back, key="rm_restore_go"):
                    for lab in back:
                        SS.decisions.pop(labs[lab], None)
                    rebuild()
                    log(f"Restored {len(back)} removed scan(s)")
                    st.rerun()
