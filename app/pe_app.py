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

st.set_page_config(page_title="PhenoCleaning", layout="wide", page_icon="🌱")

SS = st.session_state
for k, v in dict(insts=None, union=None, base_union=None, candidates=None, design=None,
                 gt=None, gt_join=None, drop=set(), decisions={}, audit=[], splits=[],
                 hidden_factors=[], remove_blanks=True, anim={}).items():
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
def ply_inventory(folder: str) -> pd.DataFrame:
    return pe_data.discover_ply(folder)


@st.cache_data(show_spinner=False, max_entries=64)
def plant_series(folder: str, block: int, position: int, allowed: tuple):
    return pe_viz.plant_series(ply_inventory(folder), block, position, allowed_paths=allowed)


@st.cache_data(show_spinner=False, max_entries=8)
def scan_status(folder: str, kept: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    """Which plant in which .ply survives the cleaning (kept / excluded / not in exports)."""
    return pe_data.ply_status(ply_inventory(folder), kept, candidates)


def current_status(folder: str) -> pd.DataFrame:
    cand = SS.candidates if SS.candidates is not None else candidates_frame()
    return scan_status(folder, clean_rows(), cand)


def allowed_files(status: pd.DataFrame, block: int, position: int) -> tuple:
    ok = status[(status["block"] == block) & (status["position"] == position)
                & (status["status"] != "excluded")]
    return tuple(sorted(ok["path"].astype(str).unique()))


# =========================================================================== sidebar
st.sidebar.title("🌱 PhenoCleaning")
st.sidebar.caption("clean · explore · animate")

# A convenience default folder can be set locally via the PLANTEYE_DEFAULT_FOLDER environment
# variable (e.g. in run_app.bat); it is blank otherwise. Never hard-code a path — this ships publicly.
EXAMPLE = os.environ.get("PLANTEYE_DEFAULT_FOLDER", "")
folder = st.sidebar.text_input("Experiment folder", EXAMPLE,
                               help="A folder holding your TraitFinder exports (.csv or .zip) and, "
                                    "optionally, the 3D .ply files. Sub-folders are searched too.")
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
        "4. **Export** — the cleaned dataset, your animations, and a log of every decision.")
    st.stop()

tabs = st.tabs(["Data", "Time-series", "3D viewer", "Export", "Advanced"])

# ---- Data -----------------------------------------------------------------
with tabs[0]:
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
                 "automatically; where values differ they are different scans — pick the keeper below.")
        with st.expander("Show these records"):
            st.dataframe(over, width="stretch", hide_index=True)
    cand = SS.candidates if SS.candidates is not None else candidates_frame()
    cr = pe_data.carrier_rescans(cand)
    if not len(cr) and not len(over):
        st.success("No duplicate scans found.")
    for _, row in cr.iterrows():
        block, date = int(row["block"]), row["date"]
        sub = cand[(cand["block"] == block) & (cand["date"] == pd.Timestamp(date))]
        opts = {}
        for ts, s in sub.groupby("timestamp"):
            src = s["source_instance"].iloc[0] if "source_instance" in s else ""
            opts[f"{pd.Timestamp(ts):%H:%M:%S} — {s['unit'].nunique()} pots  [{src}]"] = ts
        in_use = set(pd.to_datetime(SS.union.loc[(SS.union["block"] == block)
                                                 & (SS.union["date"] == pd.Timestamp(date)), "timestamp"]))
        current = next((i for i, ts in enumerate(opts.values()) if pd.Timestamp(ts) in in_use), 0)
        cc = st.columns([3, 1])
        choice = cc[0].radio(f"Carrier **{block}** scanned {len(opts)}× on **{date}** — keep:", list(opts),
                             index=current, key=f"cr_{block}_{date}", horizontal=True)
        if cc[1].button("Keep", key=f"crbtn_{block}_{date}"):
            for _, r in sub[sub["timestamp"] == opts[choice]].iterrows():
                set_decision(row_scan_key(r), "keep", note=f"carrier {block} {date}: keep {choice}")
            for _, r in sub[sub["timestamp"] != opts[choice]].iterrows():
                set_decision(row_scan_key(r), "drop", note=f"carrier {block} {date}: duplicate of {choice}")
            rebuild()
            log(f"Carrier {block} {date}: kept {choice}, dropped the other scan(s)")
            st.rerun()

    summ = pe_data.instance_summary(SS.insts)
    with st.expander(f"Exports in this experiment ({len(SS.insts)}) — choose which to combine"):
        st.dataframe(summ, width="stretch", hide_index=True)
        st.caption("An older, smaller copy of another export is excluded automatically.")
        drop = set()
        for ins in [i for i in SS.insts if not i.is_stale]:
            note = summ.loc[summ["export"] == ins.name, "note"].iloc[0]
            if not st.checkbox(ins.name, value=(ins.name not in SS.drop), key=f"inc_{ins.name}",
                               help=note or "included"):
                drop.add(ins.name)
        if drop != SS.drop:
            SS.drop = drop
            rebuild()
            log(f"Exports excluded: {sorted(drop) or 'none'}")
            st.rerun()

# ---- Time-series ----------------------------------------------------------
with tabs[1]:
    u = clean_rows().copy()
    traits = pe_data.measure_columns(u)
    if not traits:
        st.warning("No numeric traits found.")
    else:
        pref = next((t for t in traits if "digital biomass" in t.lower()), traits[0])
        c1, c2, c3 = st.columns(3)
        trait = c1.selectbox("Trait", traits, index=traits.index(pref))
        gopts = ["(none)"] + factor_options(u)
        groupby = c2.selectbox("Group by", gopts, index=min(1, len(gopts) - 1))
        filt = c3.selectbox("Show only", ["(all)"] + factor_options(u))
        d = u
        if filt != "(all)":
            lvl = st.selectbox(f"{filt} =", sorted(d[filt].dropna().astype(str).unique()))
            d = d[d[filt].astype(str) == lvl]
        d = d.dropna(subset=[trait]).copy()
        d["elapsed"] = (d["date"] - d["date"].min()).dt.days
        if d.empty:
            st.warning("No data for that selection.")
        else:
            show_pts = st.checkbox("Show individual plants", value=False)
            fig, ax = plt.subplots(figsize=(9, 4.2))
            if show_pts:
                ax.scatter(d["elapsed"], d[trait], s=8, alpha=0.25, color="0.55", zorder=1)
            grp = groupby if groupby != "(none)" else None
            for k, (g, sub) in enumerate(d.groupby(grp) if grp else [("all", d)]):
                m = sub.groupby("elapsed")[trait].agg(["mean", "sem"])
                ax.errorbar(m.index, m["mean"], yerr=m["sem"], marker="o", capsize=2, lw=2,
                            color=pe_viz.SERIES_COLORS[k % 8], label=str(g), zorder=3)
            if grp:
                ax.legend(title=grp, fontsize=8, ncol=2, frameon=False)
            for e in sorted(d["elapsed"].unique()):
                ax.axvline(e, color="0.93", lw=0.6, zorder=0)
            ax.spines[["top", "right"]].set_visible(False)
            ax.set_xlabel("days elapsed (real spacing between scans)"); ax.set_ylabel(trait)
            fig.tight_layout(); st.pyplot(fig); plt.close(fig)
            st.caption("Mean ± standard error per date. Odd points? Check them in **Advanced → Outliers**.")
            if st.button("🎞 Animate this chart"):
                name = re.sub(r"[^A-Za-z0-9]+", "_", f"{trait} by {grp or 'all'}").strip("_") + ".gif"
                with st.spinner("Rendering…"):
                    keep_anim(name, pe_viz.trait_chart_gif(d, trait, grp))
                SS["last_chart"] = name
            if SS.get("last_chart"):
                show_anim(SS["last_chart"])

# ---- 3D viewer ------------------------------------------------------------
with tabs[2]:
    inv = ply_inventory(PLY_DIR) if PLY_DIR and Path(PLY_DIR).exists() else pd.DataFrame()
    if not len(inv):
        st.info("No 3D `.ply` files found. They are searched for in the experiment folder, or set a "
                "**3D .ply folder** under *More inputs* in the sidebar.")
    else:
        status = current_status(PLY_DIR)
        plants = pe_viz.plant_options(inv, SS.union, status)
        n_ex = int((status["status"] == "excluded").sum())
        if n_ex:
            st.caption(f"Showing the scans in your cleaned data — {n_ex} plant scan(s) left out "
                       "(duplicates, blank pots or scans you dropped).")
        series_of = lambda p: plant_series(PLY_DIR, int(p["block"]), int(p["position"]),
                                           allowed_files(status, int(p["block"]), int(p["position"])))
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
with tabs[3]:
    u = clean_rows().copy()
    u["days_elapsed"] = (u["date"] - u["date"].min()).dt.days
    st.subheader("Cleaned dataset")
    st.caption(f"{len(u)} rows · duplicates resolved · blank pots "
               f"{'removed' if SS.remove_blanks else 'kept'}.")
    st.download_button("⬇ Cleaned scans (CSV)", u.to_csv(index=False).encode("utf-8"),
                       "phenocleaning_combined.csv", "text/csv", type="primary")
    st.subheader("3D files")
    inv_x = ply_inventory(PLY_DIR) if PLY_DIR and Path(PLY_DIR).exists() else pd.DataFrame()
    if not len(inv_x):
        st.caption("No 3D `.ply` files found for this experiment.")
    else:
        stx = current_status(PLY_DIR)
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


with tabs[4]:
    st.caption("Optional tools for deeper checks. Nothing here is needed for a basic clean-up.")
    sub = st.tabs(["Factors", "Quality checks", "Missing data", "Outliers", "Design", "Ground truth"])
    with sub[0]:
        st.subheader("Define grouping factors (optional)")
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

    with sub[1]:
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

    with sub[2]:
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

    with sub[3]:
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

    with sub[4]:
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

    with sub[5]:
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

