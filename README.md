# PlantEye Pipeline

An auditable, design-agnostic pipeline for turning the raw outputs of a Phenospex **PlantEye /
TraitFinder** phenotyping scanner (tabular exports + 3D `.ply` point clouds) into one clean,
quality-checked, analysis-ready dataset — with the scientist in control of every decision.

> **Naming note:** "PlantEye" is a product name of Phenospex. This is an independent, third-party
> tool for working with PlantEye data and is not affiliated with or endorsed by Phenospex.
> Consider renaming before wide release (see `PUBLISHING.md`).

## Why
High-throughput phenotyping produces data faster than it can be checked, and silent data-handling
errors (imputed missing occasions, mislabelled duplicate exports, irregular timing treated as
regular, pseudoreplication) quietly inflate confidence. This pipeline makes that class of error
**hard to commit undetected** — it surfaces conflicts and lets you resolve them, and it never
imputes or silently merges.

## What it does
- **Ingest & reconcile** several TraitFinder exports (`.csv`/`.zip`) as one experiment.
- **Curate** — you decide which scans to keep / drop / relabel; nothing is auto-removed.
- **Quality & missing-data checks** — the audit, encoded (uneven intervals, blanks-with-signal,
  duplicate vs repeat scans, carrier rescans, …).
- **Irregular-timing time-series** on a true elapsed-days axis (no imputation).
- **3D viewer** — per-plant RGB / NIR / NDVI panels, geometry-based **soil/pot-rim removal**
  (moss-proof), mesh reconstruction, and mesh-vs-portal trait comparison.
- **Turntable animation** — spin any plant (point cloud or reconstructed mesh) into a looping
  GIF for talks, figures, or the supplement — coloured by true RGB or any spectral channel
  (NDVI, greenness, NIR, hue…). Self-contained: no ffmpeg or extra software needed.
- **Export** — tidy CSVs, cleaned mesh `.ply`, and a full audit log.

## Install (no coding required)
Download the latest build for your OS from the **[Releases](../../releases)** page:

| OS | Download | How to run |
|---|---|---|
| **Windows** | `PlantEye-Setup.exe` (installer) | Run it → Start-menu / desktop shortcut |
| **macOS (Apple Silicon *or* Intel)** | `PlantEye-macOS-universal2-portable.zip` | Unzip → right-click `PlantEye` → **Open** (first time) |
| **Linux** | `PlantEye-Linux-x64-portable.zip` | Unzip → `./PlantEye/PlantEye` |

The app opens in your browser at `http://localhost:8501`. It runs on your own machine, so it reads
your local and network-drive data directly. (First launch on Windows/macOS may warn about an
unidentified developer — see `PUBLISHING.md` for code-signing/notarization notes.)

## System requirements

**To run the app (end users):**
- **OS:** Windows 10/11 (64-bit) · macOS 12+ (a single **universal2** build runs natively on both
  Apple Silicon and Intel) · Linux 64-bit with glibc ≥ 2.35 (Ubuntu 22.04+, Debian 12+, RHEL/Rocky 9+).
- **CPU:** Windows/Linux any 64-bit x86-64 processor; macOS runs natively on Apple Silicon and Intel.
- **RAM:** 8 GB minimum; **16 GB recommended** for 3D point-cloud viewing/cleaning and batch processing.
- **Disk:** ~350 MB for the installed app, plus room for your data and cleaned/exported files.
- **Browser:** the app opens in your default browser (Chrome, Edge, Firefox, or Safari); a
  1280×800 or larger window is comfortable.
- **GPU:** not required (CPU only). **Internet:** not required to run — only to download the app.
- **Network:** uses local port `8501` (falls back to the next free port). Network-drive data needs
  the drive mapped/reachable on your machine (VPN/campus as applicable).

**To run from source (developers):** Python 3.12+ with the packages in `requirements.txt`.

**To build the standalone apps (maintainers):** Python 3.12 + PyInstaller (+ Inno Setup on Windows),
or just use the GitHub Actions workflow. See `packaging/README.md`.

**Input data it accepts:** TraitFinder tabular exports (`*_PlantEye.csv` or `.zip`); optionally
binary PlantEye `.ply` point clouds, an `.xlsx` design workbook, and an `.xlsx` manual-measurements
workbook. No data is bundled — you point the app at your own folders.

## Run from source (developers)
```bash
pip install -r requirements.txt
python -m streamlit run app/pe_app.py
```
Or double-click `run_app.bat` (Windows). Set a convenience default folder with the
`PLANTEYE_DEFAULT_FOLDER` environment variable. See **`LAUNCH.md`** for all launch options
(native launcher, Docker) and **`packaging/README.md`** for building the standalone apps.

## Data & privacy
No experiment data is included in this repository, and the app hard-codes no paths — you point it
at your own folders. All curation is non-destructive: your raw files are never modified.

## License & citation
MIT (see `LICENSE`). If you use this in research, please cite it — see `CITATION.cff`.
