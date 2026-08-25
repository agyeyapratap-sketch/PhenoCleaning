"""Headless end-to-end check of the (design-agnostic) engine on a real experiment.

Runs the same functions the UI calls — discovery, overlap-by-value, union, optional factor
split, design auto-detect, QC, missing-data, ground-truth join.

Point it at your own experiment folder via an environment variable, then run it:
    PLANTEYE_TEST_FOLDER="/path/to/your/experiment"  python app/verify_app.py
Optionally set PLANTEYE_TEST_DESIGN, PLANTEYE_TEST_PHSPAD, and PLANTEYE_TEST_PLY for the
design workbook, the manual-measurements workbook, and a folder holding a `.ply` cloud.
"""
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pe_data, pe_design, pe_qc  # noqa: E402

_folder = os.environ.get("PLANTEYE_TEST_FOLDER")
if not _folder:
    print("Set PLANTEYE_TEST_FOLDER to your experiment folder to run this check, e.g.:\n"
          '  PLANTEYE_TEST_FOLDER="/path/to/experiment" python app/verify_app.py')
    sys.exit(0)
ROOT = Path(_folder)
DESIGN = Path(os.environ.get("PLANTEYE_TEST_DESIGN", "")) if os.environ.get("PLANTEYE_TEST_DESIGN") else None
PHSPAD = Path(os.environ.get("PLANTEYE_TEST_PHSPAD", "")) if os.environ.get("PLANTEYE_TEST_PHSPAD") else None
pd.set_option("display.width", 140, "display.max_columns", 20)


def rule(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


rule("1. DISCOVER EXPORTS (.csv or .zip, deduped by content)")
insts = pe_data.discover_instances(ROOT)
print(pe_data.instance_summary(insts).to_string(index=False))

rule("2. OVERLAPPING RECORDS (compared by value, not timestamp)")
over = pe_data.overlapping_records([i for i in insts if not i.is_stale])
print(f"{len(over)} overlapping (unit,date) record(s)")
if len(over):
    print(over.head(6).to_string(index=False))

rule("3. COMBINE ACROSS TIME")
base = pe_data.union_instances(insts)
print(f"combined rows: {len(base)}  dates: {base['date'].nunique()}  carriers: {base['block'].nunique()}")
print("dates:", [pd.Timestamp(d).strftime('%m-%d') for d in sorted(base['date'].unique())])

rule("4. DEFINE FACTORS (optional split of the compound treatment code)")
sug = pe_data.suggest_split(base.loc[~base['is_blank'], 'treatment'])
print("suggest_split(treatment):", sug)
uni = base
if sug:
    uni, newcols = pe_data.split_compound(base, "treatment", sug["delim"],
                                          ["growthstage", "harvesttime", "temperature"][:sug["n_parts"]])
    print("added factor columns:", newcols)
print("categorical factors available:", pe_data.categorical_factors(uni))

rule("5. DESIGN WORKBOOK (user-confirmed factors)")
if DESIGN and DESIGN.exists():
    wb = pe_design.load_workbook(DESIGN)
    sheet = pe_design.suggest_layout_sheet(wb)
    df = wb.frames[sheet]
    facs = pe_design.suggest_factors(df)
    print(f"  layout sheet: {sheet} ({len(df)} rows)")
    print(f"  suggested factors: {facs}")
    summ = pe_design.summarize(df, facs)
    for c, lv in summ["levels"].items():
        print(f"    {c}: {lv}")
    print(f"  cells={summ['cells']} units={summ['units']} reps~{summ['reps']:.2f}")
else:
    print("!! design workbook not found")

rule("6. QUALITY CHECKS")
gt = pe_data.load_groundtruth(PHSPAD) if (PHSPAD and PHSPAD.exists()) else None
gt_join = pe_data.join_groundtruth(uni, gt) if gt is not None else None
for f in pe_qc.run_all(uni, gt_join, factors=pe_data.categorical_factors(uni)):
    print(f"  [{f.severity:7}] {f.check}: {f.message[:96]}")

rule("7. MISSING DATA (scan traits per scan; manual per measured pot)")
scan = uni[~uni["is_blank"]]
traits = pe_data.measure_columns(scan)
ssum = pe_qc.missingness_summary(scan, traits)
print("scan traits — top missing:")
print(ssum.head(4).to_string(index=False))
print("scan EMPTY traits:", ssum[ssum['status'].str.startswith('EMPTY')]['measure'].tolist())
if gt is not None:
    mm = pe_data.manual_measures(gt)
    gsum = pe_qc.missingness_summary(gt, mm)
    print("\nmanual measures (over measured pots):")
    print(gsum.to_string(index=False))

rule("8. GROUND TRUTH")
if gt is not None:
    print(f"manual measures: {pe_data.manual_measures(gt)}")
    hk, hc = gt_join.attrs['height_key'], gt_join.attrs['scan_height_col']
    m = gt_join[gt_join['scan_height'].notna() & gt_join[hk].notna()]
    if len(m):
        diff = m['scan_height'] - m[hk]
        print(f"height agreement ({hk} vs {hc}): bias {diff.mean():+.1f}, SD {diff.std():.1f}, n={len(m)}")
    for c in pe_data.manual_measures(gt):
        if gt_join[c].notna().sum() == 0:
            print(f"  measure with NO numeric results: {c}")

rule("9. v3 — CURATION, CARRIER RESCANS, ANOMALIES, OUTLIERS")
cand = pd.concat([i.df.assign(source_instance=i.name) for i in insts if not i.is_stale],
                 ignore_index=True)
cr = pe_data.carrier_rescans(cand)
print(f"carrier rescans (pre-dedup): {len(cr)}")
if len(cr):
    print(cr.head(3).to_string(index=False))
size = next((t for t in pe_data.measure_columns(uni) if "digital biomass" in t.lower()),
            pe_data.measure_columns(uni)[0])
an = pe_qc.anomalous_scans(uni[~uni["is_blank"]], size)
outl = pe_qc.timeseries_outliers(uni[~uni["is_blank"]], size)
print(f"anomalous (0/NaN) scans for '{size}': {len(an)}  |  time-series outliers: {len(outl)}")
# resolve_union honours a keep + a drop decision (pick the earliest scan dynamically)
if len(cand):
    r0 = cand.sort_values("date").iloc[0]
    k = pe_data.scan_key(r0["source_instance"], r0["unit"], r0["timestamp"])
    base_n = len(pe_data.resolve_union(insts))
    dropped = pe_data.resolve_union(insts, {k: {"action": "drop"}})
    print(f"resolve_union with 1 drop: {base_n} -> {len(dropped)} rows")

rule("10. v3.1 — PLY MERGE + GEOMETRY CLEANING + MESH TRAITS")
inv = pe_data.discover_ply(ROOT)
print(f"discover_ply under experiment folder: {len(inv)} unique scans")
_ply_dir = os.environ.get("PLANTEYE_TEST_PLY")
plys = list(Path(_ply_dir).rglob('*_full.ply'))[:1] if (_ply_dir and Path(_ply_dir).exists()) else []
if plys:
    v, faces, b, u = pe_data.read_ply_lenient(plys[0])
    print(f"read_ply_lenient: {len(v)} verts, {0 if faces is None else len(faces)} faces")
    mask, labels, geoms = pe_data.clean_cloud(v)  # geometry-first
    ul, cl = np.unique(labels, return_counts=True)
    print(f"clean_cloud labels: {dict(zip(ul, cl.tolist()))}  plant kept {100*mask.mean():.0f}%")
    dist, pct = pe_data.reflectance_distributions(v, v[mask])
    print(f"NDVI median shift: {dist.loc[dist.channel=='ndvi','shift'].iloc[0]:+.3f}  "
          f"greenness shift: {dist.loc[dist.channel=='greenness','shift'].iloc[0]:+.1f}  ({pct}% removed)")
    mt = pe_data.mesh_traits(v, faces, plant_mask=mask)
    print(f"mesh_traits: {len(mt)} units, total plant mesh area "
          f"{mt['mesh_surface_area_mm2'].sum():.0f} mm² (portal Projected ~7624)")
    kv, kf = pe_data.filter_mesh(v, faces, mask)
    tmp = Path(tempfile.gettempdir()) / "_verify_cleaned.ply"
    tmp.write_bytes(pe_data.write_ply_bytes(kv, kf))
    v2, f2, _, _ = pe_data.read_ply_lenient(tmp)
    print(f"cleaned mesh .ply round-trip: {len(v2)} verts, {0 if f2 is None else len(f2)} faces "
          f"({'OK' if len(v2)==len(kv) and f2 is not None and len(f2)==len(kf) else 'MISMATCH'})")
    tmp.unlink(missing_ok=True)

rule("DONE — engine ran end to end")
