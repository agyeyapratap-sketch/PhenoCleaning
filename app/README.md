# PlantEye pipeline — app (v3, design-agnostic + curation)

Turns the raw TraitFinder exports of **any** PlantEye experiment — even when one experiment
was exported as several overlapping files — into one clean, checked, analysis-ready dataset.
Nothing about a particular design is assumed. Built on the verified reader in
`../tools/planteye_io.py`.

## Run it

Easiest: double-click **`../run_app.bat`** (stays up until you close it). Or manually:

```powershell
# from the PlantEyePipeline folder
python -m streamlit run app\pe_app.py
```

For a relaunchable / shareable container, see **`../LAUNCH.md`** (native launcher + Docker).

A browser tab opens. In the sidebar:

1. **Experiment folder** — a folder holding your TraitFinder exports, as `*_PlantEye.csv` **or
   `.zip`**. Sub-folders are searched; junk zips (image/downloader/library) are ignored.
2. Optional: a **design workbook**, **manual measurements** (e.g. height / SPAD), a **PLY folder**.

Press **Discover exports**, then **Combine into one dataset**, then walk the tabs.

## The tabs

| Tab | What it does |
|---|---|
| **① Explore & combine** | Lists every export found (rows, dates, columns); preview any one. Drops an export that is an older subset of another; flags small/overnight batches. Shows records in more than one export **compared by value**. Then combines across time. |
| **② Factors** | Your two portal labels are `genotype` and `treatment`. If you encoded several factors into one (e.g. `61.BC.HT`), split it here into named factors. **No design is assumed.** |
| **③ Design** | Optional. Browse the design workbook, confirm which columns are treatment factors (the app suggests, you edit), see levels / factorial cells / replication, and an optional split-plot hierarchy. |
| **④ Quality** | Four **main** checks (uneven intervals, blanks-with-signal, value-based repeat/duplicate scans, **carrier scanned twice in a day**) plus an **Advanced** section (timing drift, long sessions, factor-tied-to-a-date, label agreement, completeness) — each with *what it means / why it matters / what to do*. |
| **⑤ Missing data** | Completeness of each **scan trait** (per scan) and each **manual measure** (per measured pot). A measure that produced no values shows as EMPTY. No imputation — structural (MNAR) gaps are named, not filled. |
| **⑥ Curate / resolve** | **You decide, nothing is silent.** Pick which scan to keep when a carrier was scanned twice in a day; drill into the *specific* zero/missing and outlier scans and **keep / drop / relabel** them. The app pre-selects a default; you override. Every decision is logged; raw files are never touched. |
| **⑦ Time-series** | Trajectories on a true **elapsed-days** axis, grouped/filtered by any factor. Tick *show individual scans* to see outliers; a table names each outlier scan to act on in Curate. |
| **⑧ 3D viewer** | Recursively finds `.ply` across all sub-folders (deduped on block+time). Panels: **RGB, Enhanced RGB, NIR, NDVI**, top-down and side, with padding. **Geometry-first soil/pot removal** (flat soil plane + pot rim ring via point normals, greenness only confirming) with a **soil/rim/plant mask preview** and one strictness slider; **before/after reflectance distributions**; a **mesh-reconstruction render** of the cleaned triangulated surface; an **optional** "Calculate mesh traits" step comparing mesh area/volume to PlantEye's portal traits; a cleaned **mesh-`.ply`** download; and a **"Clean all scans"** batch. The app never overwrites the portal's traits. |
| **⑨ Ground truth** | Scan height vs manual height as **agreement** (bias + Bland–Altman, not R²); flags any manual measure that produced no numeric results. |
| **⑩ Export** | Tidy combined CSV + per-pot manual-measurement CSV + **curation-decisions CSV** + a provenance/audit log. |

## What changed in v3.1 (per feedback)

- **Geometry-first soil/pot removal.** Soil = flat basal plane (near-vertical point normals via a scipy
  KD-tree), pot = rim ring at the auto-detected pot radius; greenness only *confirms* a removal so green
  leaf tissue is protected. A soil/rim/plant **mask preview** + one strictness slider — you see it and tune it.
- **Triangulation kept.** The reader now loads the mesh faces; the cleaned `.ply` preserves the plant-only
  triangulation (reindexed) so it's re-processable.
- **Mesh reconstruction view** before export, and an **optional** mesh-trait calculation shown side-by-side
  with PlantEye's portal traits (mesh area ≈ portal *Projected* Leaf Area; portal *3D* Leaf Area ≈ 1.8× is a
  proprietary model). Nothing is computed unless you ask; portal traits are never overwritten. The old
  voxel-count "app traits" are gone.
- **Outlier detection is now a suggest-then-select flow** with a **sensitivity** slider (like the duplication
  tool): candidates are highlighted on the trajectory, and you choose which to keep/drop/relabel in Curate.
- **Cleaning is independent of imaging cadence** (pure per-cloud geometry); time-series uses real elapsed
  time, so any imaging frequency — regular or irregular — works without configuration.

## What changed in v3 (per feedback)

- **Curation, not auto-decisions.** A new **Curate / resolve** tab: you choose which scan to keep, and can
  drop/relabel specific scans. `resolve_union` honours your decisions; the old "fuller day" rule is now
  only the *pre-selected default*. Everything is logged; originals untouched.
- **Carrier-level duplicate detection.** Detects a whole barcode scanned twice on one day (keyed on
  `(block, date)` by distinct timestamps) — the case the old timestamp logic missed.
- **Drill into zero/missing & outlier scans.** Row-level lists name the exact `block:position @ timestamp`
  that read 0 or sit far from a plant's neighbours, so you can check the raw data or curate them.
- **PLY merge like tabular.** Recursive discovery across folders, deduped on block+time (both `_full` and
  `_unitfile` names), so the viewer sees every scan of the experiment.
- **3D overhaul.** RGB / Enhanced-RGB / NIR / NDVI, padding, soil/pot-rim removal, before/after reflectance
  distributions, app-side cleaned traits, cleaned-`.ply` export.
- **Clearer checks.** The weaker checks were rewritten (what/why/what-to-do) and moved to an Advanced section.

## What changed in v2 (per feedback)

- **Design-agnostic.** No hard-coded factors, sheet names, or experiment-specific messages.
  You define factors from your own labels; the design workbook is browsed and confirmed, not guessed.
- **Duplicate detection is value-based, not timestamp-based.** Two records are a "duplicate" only
  if their measured values match; two scans of a pot on one day with different values are a *repeat*
  (a repeatability pair), reported as such — never mislabelled a duplicate.
- **Zip exploration.** Tabular `.zip` exports are read and explored directly; you preview each file
  before combining.
- **Missing-data analysis.** New tab; correctly scoped (scan traits per scan, manual measures per
  pot), so a measure like SPAD that produced no numeric results is shown plainly.
- **Simpler.** Plain-language captions, a clear discover → combine → analyse flow.

## Principles (enforced in code)

- Never regularise a date or impute an absent occasion/group (MNAR ≠ a gap to fill).
- Union across time, keep the fuller scan on overlap, never average.
- Surface conflicts; resolve nothing silently.
- Elapsed days is the time axis, always.

## Files & verification

```
app/
  pe_data.py     readers, blank detection, zip discovery, value-based overlap, union, factor split, ground-truth
  pe_design.py   design-workbook browser (suggest + user-confirm factors, optional hierarchy)
  pe_qc.py       quality + missing-data checks
  pe_app.py      Streamlit UI (9 tabs)
  verify_app.py  headless end-to-end check on real data
```

```powershell
python app\verify_app.py   # runs the whole engine and prints what each step produced
```
