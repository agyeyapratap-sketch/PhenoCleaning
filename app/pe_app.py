"""PlantEye pipeline — interactive app (v3, design-agnostic).

Run:  streamlit run app/pe_app.py

Ingests the TraitFinder exports (CSV or ZIP) that make up one experiment, lets you explore each
file, combine them across time, and **curate** the result — the app suggests, you decide which
scans to keep/drop/relabel; nothing is resolved silently and your raw files are never touched.
Runs quality + missing-data checks, plots trajectories on a true elapsed-days axis, and renders
per-plant 3D panels with optional soil/pot-rim removal, cleaned app-side traits, and before/after
reflectance distributions. Factors are whatever you define from your own labels.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401 — registers the 3d projection
import numpy as np
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pe_data
import pe_design
import pe_qc

st.set_page_config(page_title="PlantEye pipeline", layout="wide", page_icon="🌱")

SS = st.session_state
for k, v in dict(insts=None, union=None, base_union=None, candidates=None, design=None,
                 gt=None, gt_join=None, drop=set(), decisions={}, audit=[], splits=[],
                 hidden_factors=[]).items():
    SS.setdefault(k, v)

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


# =========================================================================== sidebar
st.sidebar.title("🌱 PlantEye pipeline")
st.sidebar.caption("ingest · explore · combine · curate · check · analyse — for any design")

# A convenience default folder can be set locally via the PLANTEYE_DEFAULT_FOLDER environment
# variable (e.g. in run_app.bat); it is blank otherwise. Never hard-code a path — this ships publicly.
EXAMPLE = os.environ.get("PLANTEYE_DEFAULT_FOLDER", "")
folder = st.sidebar.text_input("① Experiment folder", EXAMPLE,
                               help="A folder holding your TraitFinder exports (.csv or .zip). "
                                    "Sub-folders are searched too.")
with st.sidebar.expander("Optional inputs", expanded=False):
    design_path = st.text_input("Design workbook (.xlsx)", "")
    gt_path = st.text_input("Manual measurements (.xlsx) — e.g. height / SPAD", "")
    ply_folder = st.text_input("PLY folder (for the 3D viewer; blank = the experiment folder)", "")

if st.sidebar.button("Discover exports", type="primary", width="stretch"):
    try:
        insts = pe_data.discover_instances(folder)
        if not insts:
            st.sidebar.error("No PlantEye exports (.csv/.zip) found under that folder.")
        else:
            SS.insts, SS.drop, SS.decisions = insts, set(), {}
            SS.union = SS.base_union = SS.candidates = None
            SS.splits, SS.hidden_factors, SS.gt_join = [], [], None
            log(f"Discovered {len(insts)} export(s) in {folder}")
            SS.design = (pe_design.load_workbook(design_path)
                         if design_path and Path(design_path).exists() else None)
            if SS.design:
                log(f"Loaded design workbook {Path(design_path).name}")
            SS.gt = (pe_data.load_groundtruth(gt_path)
                     if gt_path and Path(gt_path).exists() else None)
            if SS.gt is not None:
                log(f"Loaded manual measurements {Path(gt_path).name} ({len(SS.gt)} rows)")
            st.sidebar.success(f"Found {len(insts)} export(s).")
    except Exception as e:
        st.sidebar.error(f"{type(e).__name__}: {e}")

if SS.insts:
    if st.sidebar.button("Combine into one dataset", width="stretch"):
        rebuild()
        log(f"Combined {len(SS.base_union)} rows (excluded {sorted(SS.drop) or 'nothing extra'}; "
            f"{len(SS.decisions)} manual decision(s))")
        st.sidebar.success(f"Combined: {len(SS.union)} rows.")
    if SS.decisions:
        st.sidebar.caption(f"✎ {len(SS.decisions)} curation decision(s) applied")

# =========================================================================== main
st.title("PlantEye pipeline")

if not SS.insts:
    st.info("Enter your **experiment folder** in the sidebar and press **Discover exports**. "
            "Point it at a folder holding one or more TraitFinder `*_PlantEye.csv` or `.zip` exports.")
    st.markdown(
        "This app turns the raw exports of one experiment — even split across several files — into one "
        "clean, checked dataset that **you** curate:\n\n"
        "1. **Explore** each export before combining.\n"
        "2. **Combine** across time; overlaps are compared by value, never guessed from timestamps.\n"
        "3. **Curate** — the app suggests, you choose which scans to keep/drop/relabel. Nothing silent.\n"
        "4. **Check** quality and **missing data** (including measures that produced no values).\n"
        "5. **Analyse** trajectories, view/clean per-plant 3D clouds, validate against manual measures.")
    st.stop()

tabs = st.tabs(["① Explore & combine", "② Factors", "③ Design", "④ Quality", "⑤ Missing data",
                "⑥ Curate / resolve", "⑦ Time-series", "⑧ 3D viewer", "⑨ Ground truth", "⑩ Export"])

# ---- ① Explore & combine --------------------------------------------------
with tabs[0]:
    st.subheader("Exports found in this experiment")
    summ = pe_data.instance_summary(SS.insts)
    st.dataframe(summ, width="stretch", hide_index=True)
    st.caption("An export that is an older, smaller copy of another (a subset) is dropped by default. "
               "Small or overnight batches are flagged so you can decide — nothing is discarded silently.")

    with st.expander("Preview a single export"):
        pick = st.selectbox("Export", [i.name for i in SS.insts])
        ins = next(i for i in SS.insts if i.name == pick)
        st.write(f"**{ins.name}** · {ins.kind.upper()} · {len(ins.df)} rows · "
                 f"{ins.df['date'].nunique()} date(s) · {len(pe_data.trait_columns(ins.df))} trait columns")
        st.write("Dates: " + ", ".join(pd.Timestamp(d).strftime("%b-%d") for d in sorted(ins.dates)))
        st.dataframe(ins.df.head(30), width="stretch", hide_index=True)

    st.markdown("---")
    st.subheader("Which exports to combine")
    keepable = [i for i in SS.insts if not i.is_stale]
    drop = set()
    cols = st.columns(min(len(keepable), 3) or 1)
    for k, ins in enumerate(keepable):
        note = summ.loc[summ["export"] == ins.name, "note"].iloc[0]
        with cols[k % len(cols)]:
            if not st.checkbox(ins.name, value=(ins.name not in SS.drop),
                               key=f"inc_{ins.name}", help=note or "included"):
                drop.add(ins.name)
    SS.drop = drop

    st.markdown("**Records that appear in more than one export** (compared by value):")
    over = pe_data.overlapping_records([i for i in SS.insts if i.name not in SS.drop])
    if len(over):
        st.dataframe(over, width="stretch", hide_index=True)
        st.caption("Identical copies merge safely. Where they differ they are different scans — choose the "
                   "keeper per carrier in the **Curate / resolve** tab (the default keeps the fuller day).")
    else:
        st.success("No record appears in more than one included export.")

    if SS.union is not None:
        st.markdown("---")
        u = SS.union
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("rows", len(u)); c2.metric("dates", u["date"].nunique())
        c3.metric("carriers", u["block"].nunique()); c4.metric("real scans", int((~u["is_blank"]).sum()))
        st.caption("Combined timepoints: " +
                   ", ".join(pd.Timestamp(d).strftime("%b-%d") for d in sorted(u["date"].unique())))
    else:
        st.info("Press **Combine into one dataset** in the sidebar when the selection looks right.")

# ---- ② Factors ------------------------------------------------------------
with tabs[1]:
    st.subheader("Define grouping factors (optional)")
    st.caption("Every export carries two labels — **genotype** and **treatment** — that you set in the "
               "portal. If you encoded several factors into one label (e.g. `61.BC.HT`), split it here "
               "into named factors. Nothing about your design is assumed.")
    if SS.base_union is None:
        st.info("Combine the exports first (sidebar), then define factors here.")
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
                    st.success(f"Added factors: {', '.join(nm)}")
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

# ---- ③ Design -------------------------------------------------------------
with tabs[2]:
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

# ---- ④ Quality ------------------------------------------------------------
def render_finding(f):
    with st.expander(f"{SEV.get(f.severity, '⬜')} **{f.check}** — {f.message}",
                     expanded=f.severity in ("blocker", "flag")):
        if f.explain:
            st.markdown(f.explain)
        if f.detail is not None and len(f.detail):
            st.dataframe(f.detail, width="stretch", hide_index=True)


with tabs[3]:
    st.subheader("Quality checks")
    if SS.union is None:
        st.info("Combine the exports first (sidebar).")
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

# ---- ⑤ Missing data -------------------------------------------------------
with tabs[4]:
    st.subheader("Missing-data analysis")
    if SS.union is None:
        st.info("Combine the exports first (sidebar).")
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
            st.caption("To see or act on the *specific* zero/missing scans, use the **Curate / resolve** tab.")

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

# ---- ⑥ Curate / resolve ---------------------------------------------------
with tabs[5]:
    st.subheader("Curate / resolve — you decide, nothing is silent")
    if SS.union is None:
        st.info("Combine the exports first (sidebar), then curate here.")
    else:
        st.caption("The app suggests a default; you override. Every decision is logged and only affects "
                   "the combined dataset and exports — your raw files are never touched.")
        if SS.decisions:
            cc1, cc2 = st.columns([3, 1])
            cc1.write(f"**{len(SS.decisions)}** decision(s) currently applied.")
            if cc2.button("Clear all decisions"):
                SS.decisions = {}
                rebuild()
                log("Cleared all curation decisions")
                st.rerun()

        cand = SS.candidates if SS.candidates is not None else candidates_frame()

        # -- Panel A: carrier rescanned twice in a day ------------------------
        st.markdown("#### Carriers scanned more than once in a day")
        cr = pe_data.carrier_rescans(cand)
        if not len(cr):
            st.success("No carrier was scanned twice on the same day.")
        else:
            st.caption("Pick which scan to keep for each carrier/day; the others drop out of the combined set.")
            for _, row in cr.iterrows():
                block, date = int(row["block"]), row["date"]
                sub = cand[(cand["block"] == block) &
                           (cand["date"] == pd.Timestamp(date))]
                opts = {}
                for ts, s in sub.groupby("timestamp"):
                    src = s["source_instance"].iloc[0] if "source_instance" in s else ""
                    opts[f"{pd.Timestamp(ts).strftime('%H:%M:%S')} — {s['unit'].nunique()} units [{src}]"] = ts
                labels = list(opts)
                choice = st.radio(f"Carrier **{block}** on **{date}**", labels, key=f"cr_{block}_{date}",
                                  horizontal=True)
                if st.button("Keep this scan", key=f"crbtn_{block}_{date}"):
                    keep_ts = opts[choice]
                    for _, r in sub[sub["timestamp"] == keep_ts].iterrows():
                        set_decision(row_scan_key(r), "keep", note=f"carrier {block} {date}: keep {choice}")
                    rebuild()
                    log(f"Carrier {block} {date}: kept {choice}")
                    st.success(f"Kept {choice} for carrier {block} on {date}.")
                    st.rerun()

        # -- Panel B: specific zero / missing / outlier scans -----------------
        st.markdown("---")
        st.markdown("#### Zero, missing, and outlier scans")
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
                pool["label"] = (pool["block"].astype(str) + ":" + pool["position"].astype(str)
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

# ---- ⑦ Time-series --------------------------------------------------------
with tabs[6]:
    st.subheader("Trait trajectories (true elapsed-days axis)")
    if SS.union is None:
        st.info("Combine the exports first (sidebar).")
    else:
        u = SS.union[~SS.union["is_blank"]].copy()
        traits = pe_data.measure_columns(u)
        if not traits:
            st.warning("No numeric traits found.")
        else:
            pref = next((t for t in traits if "digital biomass" in t.lower()), traits[0])
            c1, c2, c3 = st.columns(3)
            trait = c1.selectbox("Trait", traits, index=traits.index(pref))
            gopts = ["(none)"] + factor_options(u)
            groupby = c2.selectbox("Group by", gopts, index=min(1, len(gopts) - 1))
            filt = c3.selectbox("Filter by (optional)", ["(all)"] + factor_options(u))
            f1, f2 = st.columns([1, 2])
            show_pts = f1.checkbox("Show individual scans", value=False)
            sens = f2.slider("outlier sensitivity", 1, 10, 5,
                             help="Higher highlights more candidate outliers. They are suggestions — "
                                  "review and act on them in the Curate / resolve tab.")
            k = 6.0 - 0.45 * sens
            d = u
            if filt != "(all)":
                lvl = st.selectbox(f"{filt} =", sorted(d[filt].dropna().unique()))
                d = d[d[filt] == lvl]
            d = d.dropna(subset=[trait]).copy()
            d["elapsed"] = (d["date"] - d["date"].min()).dt.days
            if d.empty:
                st.warning("No data for that selection.")
            else:
                outl = pe_qc.timeseries_outliers(d, trait, k=k)
                fig, ax = plt.subplots(figsize=(9, 4.5))
                if show_pts:
                    ax.scatter(d["elapsed"], d[trait], s=8, alpha=0.25, color="0.5", zorder=1)
                if groupby != "(none)":
                    for g, sub in d.groupby(groupby):
                        m = sub.groupby("elapsed")[trait].agg(["mean", "sem"])
                        ax.errorbar(m.index, m["mean"], yerr=m["sem"], marker="o", capsize=2,
                                    label=str(g), zorder=3)
                    ax.legend(title=groupby, fontsize=8, ncol=2)
                else:
                    m = d.groupby("elapsed")[trait].agg(["mean", "sem"])
                    ax.errorbar(m.index, m["mean"], yerr=m["sem"], marker="o", capsize=2,
                                color="#2c7", zorder=3)
                if len(outl):  # highlight the candidate outliers on the plot
                    keys = set(zip(outl["unit"], pd.to_datetime(outl["date"])))
                    ov = d[[(uu, dd) in keys for uu, dd in zip(d["unit"], d["date"])]]
                    ax.scatter(ov["elapsed"], ov[trait], s=60, facecolors="none",
                               edgecolors="#d53", linewidths=1.5, zorder=5, label="candidate outlier")
                for e in sorted(d["elapsed"].unique()):
                    ax.axvline(e, color="0.92", lw=0.6, zorder=0)
                ax.set_xlabel("days elapsed (real spacing — not a week index)")
                ax.set_ylabel(trait); ax.set_title(trait)
                fig.tight_layout(); st.pyplot(fig); plt.close(fig)
                st.caption("Red circles are candidate outliers at the current sensitivity — points far from "
                           "a plant's own neighbours. They're suggestions; pick which to keep/drop/relabel in "
                           "the **Curate / resolve** tab.")
                if len(outl):
                    st.dataframe(outl, width="stretch", hide_index=True)

# ---- ⑧ 3D viewer ----------------------------------------------------------
LABEL_COLOR = {"plant": "#2ca25f", "soil": "#d53e2a", "rim": "#e8820e"}


def plot_cloud_panels(pts, res, pad, brighten):
    for axes, label in [(("x", "y"), "top-down (x,y)"), (("y", "z"), "side (y,z)")]:
        st.markdown(f"**{label}** — {len(pts):,} points")
        fig, axs = plt.subplots(1, 4, figsize=(13, 3.3))
        img, ext = pe_data.rasterise_rgb(pts, axes, res)
        axs[0].imshow(img, origin="lower", extent=ext, aspect="equal"); axs[0].set_title("RGB", fontsize=9)
        img2, _ = pe_data.rasterise_rgb(pts, axes, res, brighten=brighten, gamma=1.4)
        axs[1].imshow(img2, origin="lower", extent=ext, aspect="equal")
        axs[1].set_title("Enhanced RGB", fontsize=9)
        for ax, (ch, cmap) in zip(axs[2:], [("nir", "inferno"), ("ndvi", "RdYlGn")]):
            grid, _ = pe_data.rasterise(pts, ch, axes, res_mm=res)
            im = ax.imshow(grid, origin="lower", extent=ext, cmap=cmap, aspect="equal")
            ax.set_title(ch.upper(), fontsize=9)
            fig.colorbar(im, ax=ax, fraction=0.046, pad=0.02)
        for ax in axs:
            ax.set_xlim(ext[0] - pad, ext[1] + pad); ax.set_ylim(ext[2] - pad, ext[3] + pad)
            ax.set_xticks([]); ax.set_yticks([])
        fig.tight_layout(); st.pyplot(fig); plt.close(fig)


def plot_mask(pts, labels):
    """Show the soil/rim/plant classification so the user confirms all soil is caught."""
    fig, axs = plt.subplots(1, 2, figsize=(11, 3.6))
    for ax, axes, ttl in [(axs[0], ("x", "y"), "top-down (x,y)"), (axs[1], ("y", "z"), "side (y,z)")]:
        for lab, col in LABEL_COLOR.items():
            m = labels == lab
            if m.any():
                ax.scatter(pts[axes[0]][m], pts[axes[1]][m], s=2, c=col,
                           label=f"{lab} ({int(m.sum())})", linewidths=0)
        ax.set_title(ttl, fontsize=9); ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
        ax.legend(fontsize=7, markerscale=3, loc="upper right")
    fig.tight_layout(); st.pyplot(fig); plt.close(fig)


def plot_mesh3d(verts, faces, color_by="z"):
    """Render the cleaned triangulated surface — the reconstruction, not just points."""
    if faces is None or len(faces) == 0:
        st.caption("No mesh faces to render.")
        return
    xyz = np.column_stack([verts["x"], verts["y"], verts["z"]]).astype(float)
    fig = plt.figure(figsize=(8, 5.5))
    ax = fig.add_subplot(111, projection="3d")
    ax.plot_trisurf(xyz[:, 0], xyz[:, 1], xyz[:, 2], triangles=faces,
                    cmap="YlGn", linewidth=0, antialiased=False)
    ax.set_xticks([]); ax.set_yticks([]); ax.set_zticks([])
    try:
        ax.set_box_aspect((np.ptp(xyz[:, 0]), np.ptp(xyz[:, 1]), np.ptp(xyz[:, 2]) * 3))
    except Exception:
        pass
    ax.view_init(elev=35, azim=-60)
    fig.tight_layout(); st.pyplot(fig); plt.close(fig)


with tabs[7]:
    st.subheader("3D viewer — clean, reconstruct, and (optionally) measure")
    pf = ply_folder or folder
    inv = pe_data.discover_ply(pf) if pf and Path(pf).exists() else pd.DataFrame()
    if not len(inv):
        st.info("No `.ply` files found under that folder. Set a PLY folder in the sidebar (blank = the "
                "experiment folder).")
    else:
        st.caption(f"{len(inv)} point cloud(s) found across sub-folders (deduped on block+time).")
        inv = inv.copy()
        inv["label"] = ("block " + inv["block"].astype(str) + " · "
                        + inv["local"].dt.strftime("%Y-%m-%d %H:%M") + " · " + inv["kind"])
        pick = st.selectbox(f"{len(inv)} scan(s)", inv["label"].tolist())
        path = inv.loc[inv["label"] == pick, "path"].iloc[0]
        try:
            verts, faces, block, utc = pe_data.read_ply_lenient(path)
            uids = np.unique(verts["unit_id"])
            st.caption(f"{len(verts):,} vertices · {0 if faces is None else len(faces):,} faces · "
                       f"block {block} · {utc} · unit_ids {uids.tolist()}")
            c1, c2, c3 = st.columns(3)
            uid = c1.selectbox("Plant (unit_id)", ["(whole carrier)"] + uids.tolist())
            res = c2.slider("resolution (mm/pixel)", 1.0, 5.0, 2.0, 0.5)
            pad = c3.slider("padding around plant (mm)", 0, 60, 15, 5)
            brighten = c3.slider("Enhanced-RGB brightness", 1.0, 3.0, 1.7, 0.1)
            vmask = np.ones(len(verts), bool) if uid == "(whole carrier)" else (verts["unit_id"] == uid)

            # ---- geometry-first cleaning ----
            # defaults; overridden by the sliders when cleaning is on, and reused by the batch below
            soil_ceiling, rim_margin, strictness, pot_radius = 5.0, 15.0, 1.0, None
            clean_on = st.checkbox("Remove soil / pot rim (geometry-based)", value=False)
            if clean_on:
                g1, g2, g3 = st.columns(3)
                soil_ceiling = g1.slider("remove soil up to (mm above surface)", 0.0, 30.0, 5.0, 1.0,
                                         help="A negative cylinder removes everything from the pot base up "
                                              "to this height — including GREEN moss/algae, which a colour "
                                              "test can't. Raise it if moss is taller.")
                rim_margin = g2.slider("rim margin (mm) — negative extends past the rim", -20.0, 40.0,
                                       15.0, 1.0,
                                       help="Positive stops the soil cylinder this far *inside* the rim, so "
                                            "leaves overhanging the edge aren't clipped. Negative pushes it "
                                            "*past* the rim to remove the last thin ring of soil.")
                strictness = g3.slider("rim-ring strictness", 0.5, 1.5, 1.0, 0.1,
                                       help="Widens the pot-rim ring band. Watch the mask preview.")
                pot_dia = st.number_input("pot diameter (mm) — 0 = auto-estimate", min_value=0,
                                          max_value=600, value=0, step=10,
                                          help="Pots are a fixed size, so entering the real diameter is far "
                                               "more reliable than auto-estimation. The soil cylinder radius "
                                               "= diameter/2 − the rim margin above.")
                pot_radius = (pot_dia / 2.0) if pot_dia > 0 else None
                plant_mask, labels, geoms = pe_data.clean_cloud(
                    verts, strictness=strictness, rim_margin=rim_margin, soil_ceiling=soil_ceiling,
                    pot_radius=pot_radius)
                st.markdown("**Classification preview** — is all soil (red) and rim (orange) caught? "
                            "Green moss on the soil should now be red, not green.")
                plot_mask(verts[vmask], labels[vmask])
                kept = int((plant_mask & vmask).sum()); tot = int(vmask.sum())
                st.caption(f"plant kept: {kept:,} / {tot:,} ({100*kept/max(tot,1):.0f}%). "
                           "Adjust strictness until no soil/rim remains on the plant.")
                with st.expander("Auto-detected pot geometry (override if a scan looks wrong)"):
                    st.json({str(k): {kk: round(vv, 1) for kk, vv in v.items()}
                             for k, v in geoms.items()})
            else:
                plant_mask = np.ones(len(verts), bool)
                labels = np.full(len(verts), "plant", dtype=object)
            scope = plant_mask & vmask
            view_pts = verts[scope]

            st.markdown("#### Spectral panels")
            plot_cloud_panels(view_pts, res, pad, brighten)

            if clean_on and scope.any():
                st.markdown("#### Reflectance distributions — before vs after cleaning")
                raw = verts[vmask]
                dist, pct = pe_data.reflectance_distributions(raw, view_pts)
                st.caption(f"{pct}% of points removed. The soil (low-NDVI, grey) peak shrinks, vegetation sharpens.")
                chans = ["ndvi", "greenness", "nir", "hue"]
                fig, axs = plt.subplots(1, len(chans), figsize=(3.2 * len(chans), 3))
                for ax, ch in zip(axs, chans):
                    ax.hist(pe_data.channel_values(raw, ch), bins=50, density=True, alpha=0.5,
                            color="0.55", label="raw")
                    ax.hist(pe_data.channel_values(view_pts, ch), bins=50, density=True, alpha=0.6,
                            color="#2c7", label="clean")
                    ax.set_title(ch, fontsize=9); ax.set_yticks([])
                axs[0].legend(fontsize=7)
                fig.tight_layout(); st.pyplot(fig); plt.close(fig)
                st.dataframe(dist, width="stretch", hide_index=True)

            # ---- mesh reconstruction (see before export) ----
            st.markdown("#### Mesh reconstruction")
            st.caption("The actual triangulated surface (soil/rim removed) — inspect it before exporting.")
            kv, kf = pe_data.filter_mesh(verts, faces, scope)
            plot_mesh3d(kv, kf)

            # ---- turntable animation (spin the plant for slides/figures) ----
            st.markdown("#### Turntable animation")
            st.caption("Spin this plant into a looping animation for talks, figures, or the "
                       "supplement — a self-contained port of the *3d-turntable-animator*, "
                       "rendered here with no extra software (saves a GIF, no ffmpeg needed).")
            tt_pts = view_pts if len(view_pts) else verts[vmask]
            has_mesh = kf is not None and len(kf) > 0
            t1, t2, t3, t4 = st.columns(4)
            what = t1.radio("Spin", (["point cloud", "mesh surface"] if has_mesh else ["point cloud"]),
                            horizontal=False,
                            help="Point cloud keeps the true per-point colour; the mesh spins the "
                                 "reconstructed surface.")
            colour = t2.selectbox("Colour by",
                                   ["true colour", "NDVI", "greenness (G−R)", "NIR",
                                    "NIR940", "hue", "intensity", "height"],
                                   help="True colour uses the scan's RGB; every other option maps "
                                        "one of the spectral/structural channels the app renders "
                                        "elsewhere onto the same colour scales.")
            spin_axis = t3.selectbox("Spin axis (up)", ["z (upright)", "y", "x"], index=0,
                                     help="Which axis stands up and is spun about. Plants stand on z.")
            bg = t4.selectbox("Background", ["white", "black"], index=0)
            s1, s2, s3, s4 = st.columns(4)
            duration = s1.slider("duration (s)", 2, 20, 5, 1)
            fps = s2.slider("frames / second", 8, 30, 15, 1)
            turns = s3.slider("turns", 1, 4, 1, 1)
            px = s4.selectbox("size (px)", [360, 480, 720], index=1)
            s5, s6 = st.columns(2)
            elev = s5.slider("camera tilt (elevation °)", -10, 60, 18, 2)
            psize = s6.slider("point size", 0.5, 6.0, 2.0, 0.5,
                              help="Bigger dots close gaps in a sparse cloud (ignored for the mesh).")
            n_frames = int(round(duration * fps))
            if n_frames > 300:
                n_frames = 300
                st.caption(f"Capped at 300 frames (~{300/fps:.0f}s at {fps} fps) to keep rendering quick.")
            st.caption(f"{n_frames} frames · {'mesh' if what == 'mesh surface' else f'{min(len(tt_pts),60000):,} points'} "
                       f"· loops seamlessly.")
            if st.button("🎞 Render turntable"):
                cb = {"true colour": "rgb", "NDVI": "ndvi", "greenness (G−R)": "greenness",
                      "NIR": "nir", "NIR940": "nir940", "hue": "hue", "intensity": "intensity",
                      "height": "height"}[colour]
                bar = st.progress(0.0)
                with st.spinner("Rendering frames…"):
                    gif = pe_data.turntable_gif(
                        tt_pts, faces=(kf if what == "mesh surface" else None),
                        color_by=cb, up_axis=spin_axis[0], turns=turns, n_frames=n_frames,
                        fps=fps, size=int(px), elev=elev, bg=bg, point_size=psize,
                        brighten=brighten, progress=lambda f: bar.progress(min(f, 1.0)))
                bar.empty()
                SS["turntable_gif"] = gif
                SS["turntable_name"] = f"{Path(path).stem}_turntable.gif"
            if SS.get("turntable_gif"):
                st.image(SS["turntable_gif"], caption="Turntable preview (animated GIF)")
                st.download_button("⬇ turntable (.gif)", SS["turntable_gif"],
                                   SS.get("turntable_name", "turntable.gif"), "image/gif",
                                   width="stretch")
                st.caption("Drop the GIF straight into slides, or convert to MP4 elsewhere if a "
                           "journal needs it. For a browser-based MP4/WebM export, the standalone "
                           "*3d-turntable-animator* web app renders one client-side.")

            # ---- optional mesh traits + portal comparison ----
            st.markdown("#### Mesh traits (optional)")
            calc = st.checkbox("Calculate mesh traits and compare to PlantEye's portal traits", value=False)
            cmp = pd.DataFrame()
            if calc:
                cmp = pe_data.portal_trait_comparison(
                    pe_data.mesh_traits(verts, faces, scope), verts,
                    SS.union if SS.union is not None else pd.DataFrame(),
                    block, (utc + pd.Timedelta(hours=8)) if utc is not None else None)
                if len(cmp):
                    st.dataframe(cmp, width="stretch", hide_index=True)
                    st.caption("Mesh surface area ≈ PlantEye's **Projected** Leaf Area; its **3D** Leaf Area "
                               "is a proprietary model ~1.8× larger (hidden/underside leaf the top-down scan "
                               "can't see). App-derived — the portal traits are never overwritten. You decide "
                               "whether to use these.")
                else:
                    st.caption("No mesh faces (or no portal match) for this scan.")

            # ---- exports ----
            st.markdown("#### Export (after you've inspected the reconstruction)")
            e1, e2 = st.columns(2)
            if kf is not None:
                e1.download_button("⬇ cleaned mesh (.ply, triangulation kept)",
                                   pe_data.write_ply_bytes(kv, kf),
                                   f"{Path(path).stem}_cleaned.ply", "application/octet-stream",
                                   width="stretch")
            if len(cmp):
                e2.download_button("⬇ mesh vs portal traits (CSV)", cmp.to_csv(index=False).encode("utf-8"),
                                   f"mesh_traits_block{block}.csv", "text/csv", width="stretch")

            # ---- batch (applies the SAME settings tuned above to every scan) ----
            with st.expander(f"Clean ALL {len(inv)} scans (batch) → cleaned mesh .ply set", expanded=False):
                pd_txt = f"{pot_radius*2:.0f} mm" if pot_radius else "auto"
                st.caption(f"Applies the **same settings you tuned above** to every scan in the experiment: "
                           f"pot diameter **{pd_txt}**, remove soil to **{soil_ceiling:.0f} mm** above the "
                           f"surface, rim margin **{rim_margin:.0f} mm**, rim strictness **{strictness:.1f}**. "
                           f"Tick *Remove soil / pot rim* above and tune it on one scan first, then run this.")
                if not clean_on:
                    st.warning("Tick *Remove soil / pot rim* above and tune it first — the batch uses those "
                               "exact settings (currently the defaults).")
                out_dir = st.text_input("Output folder", str(Path(pf) / "cleaned_ply"))
                if st.button("Clean all scans"):
                    bar = st.progress(0.0)
                    log_df = pe_data.clean_all_scans(
                        inv, strictness=strictness, out_dir=out_dir,
                        rim_margin=rim_margin, soil_ceiling=soil_ceiling, pot_radius=pot_radius,
                        progress=lambda f: bar.progress(min(f, 1.0)))
                    log(f"Batch-cleaned {len(log_df)} scans → {out_dir} "
                        f"(diameter={pd_txt}, ceiling={soil_ceiling}, rim_margin={rim_margin})")
                    st.success(f"Cleaned {len(log_df)} scans → {out_dir}")
                    st.dataframe(log_df, width="stretch", hide_index=True)
        except Exception as e:
            st.error(f"{type(e).__name__}: {e}")

# ---- ⑨ Ground truth -------------------------------------------------------
with tabs[8]:
    st.subheader("Validate against manual measurements (optional)")
    if SS.gt is None:
        st.info("No manual-measurement workbook loaded. Add one in the sidebar (e.g. Plant Height / "
                "SPAD) to validate the scans. Everything else runs without it.")
    elif SS.gt_join is None:
        st.info("Combine the exports first (sidebar) so the manual data can be joined to scans.")
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

# ---- ⑩ Export -------------------------------------------------------------
with tabs[9]:
    st.subheader("Export & audit log")
    if SS.union is None:
        st.info("Combine the exports first (sidebar).")
    else:
        u = SS.union.copy()
        u["days_elapsed"] = (u["date"] - u["date"].min()).dt.days
        st.download_button("⬇ combined scans (tidy CSV)", u.to_csv(index=False).encode("utf-8"),
                           "planteye_combined.csv", "text/csv", width="stretch")
        if SS.gt_join is not None:
            st.download_button("⬇ manual-measurement join (per pot)",
                               SS.gt_join.to_csv(index=False).encode("utf-8"),
                               "planteye_groundtruth.csv", "text/csv", width="stretch")
        if SS.decisions:
            dec = pd.DataFrame([{"source_instance": k[0], "unit": k[1], "timestamp": k[2], **v}
                                for k, v in SS.decisions.items()])
            st.download_button("⬇ curation decisions (CSV)", dec.to_csv(index=False).encode("utf-8"),
                               "planteye_decisions.csv", "text/csv", width="stretch")
        st.markdown("**Provenance / audit log** — every combine, factor, and curation decision:")
        st.code("\n".join(SS.audit) or "(empty)", language="text")
        st.download_button("⬇ audit log", ("\n".join(SS.audit)).encode("utf-8"),
                           "planteye_audit.txt", "text/plain")
