# PhenoCleaning — app

Turns the TraitFinder exports of **any** PlantEye experiment — even when one experiment was exported
as several overlapping files — into one clean dataset, and lets you see it: trait trajectories over
time and the 3D scans themselves, in true colour or any of 20 colour indices, as stills or
animations. Nothing about a particular design is assumed. Built on the verified reader in
`../tools/planteye_io.py`.

## Run it

Easiest: double-click **`../run_app.bat`** (stays up until you close it). Or manually:

```powershell
# from the PlantEyePipeline folder
python -m streamlit run app\pe_app.py
```

In the sidebar, enter the **experiment folder** (TraitFinder exports as `*_PlantEye.csv` or `.zip`;
sub-folders are searched) and press **Load experiment**. The 3D `.ply` files are found in the same
folder, or set a separate folder under *More inputs*.

## The tabs

| Tab | What it does |
|---|---|
| **Data** | Combines the exports across time. **Duplicate scans**: records in more than one export are compared by value (identical copies merge); a carrier scanned twice in a day shows a one-click choice of which scan to keep. **Blank pots**: one switch removes them from plots and exports. Choose which exports to include. |
| **Time-series** | Trajectories on a true **elapsed-days** axis (mean ± SE), grouped or filtered by any label; **🎞 Animate this chart** draws it in date by date. |
| **3D viewer** | **One scan** — top and side view in the chosen colour, then spin it into a turntable. **Growth over time** — one plant at every scan date (top, side or 3D view), or spinning while it grows. **Compare plants** — up to 8 plants side by side, in sync by date. Every animation uses one colour scale and fixed framing, so changes are the plant, not the display. The viewer follows your cleaning: scans removed as duplicates, blank pots or by you are left out. |
| **Export** | The cleaned dataset (CSV); **Copy kept 3D files** — copies only the `.ply` files that hold plants in the cleaned dataset to a new folder, with `ply_file_list.csv` saying why each other file was left out (originals untouched); every animation made this session; the decisions and audit log. |
| **Advanced** | Optional: split a compound label into factors, quality checks, missing-data analysis, zero/outlier scan curation (keep / drop / relabel), design-workbook browser, and manual-measurement validation (Bland–Altman, not R²). |

### Colour indices (3D viewer)

| Group | Indices |
|---|---|
| Sensor | NDVI, NDVI (940 nm), NIR, NIR 940 nm, PSRI, NPCI |
| RGB | GLI, ExG, ExR, ExG − ExR, NGRDI, VARI, MGRVI, RGBVI, TGI, CIVE |
| Colour | True colour, hue, lightness, saturation |
| Structure | Height |

RGB indices use the 16-bit colour channels; chromatic forms (ExG, ExR, TGI, CIVE) use r, g, b
normalised by R + G + B. Each index's one-line definition is shown under the colour menu.

## Principles (enforced in code)

- Never regularise a date or impute an absent occasion/group (MNAR ≠ a gap to fill).
- Duplicates are judged by value, never by timestamp; you choose the keeper.
- Surface conflicts; resolve nothing silently. Every decision is logged; raw files are never touched.
- Elapsed days is the time axis, always.

## Files & verification

```
app/
  pe_data.py     readers, blank detection, export discovery, value-based overlap, union, factors, ground truth
  pe_viz.py      colour indices, still views, turntable and time-series animations
  pe_design.py   design-workbook browser
  pe_qc.py       quality + missing-data checks
  pe_app.py      Streamlit UI (Data · Time-series · 3D viewer · Export · Advanced)
  verify_app.py  headless end-to-end check on real data
```

```powershell
python app\verify_app.py   # runs the whole engine and prints what each step produced
```
