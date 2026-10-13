# PhenoCleaning

An auditable, design-agnostic pipeline for turning the raw outputs of a Phenospex **PlantEye /
TraitFinder** phenotyping scanner (tabular exports + 3D `.ply` point clouds) into one clean,
quality-checked, analysis-ready dataset — with the scientist in control of every decision.

## Why
High-throughput phenotyping produces data faster than it can be checked, and silent data-handling
errors (imputed missing occasions, mislabelled duplicate exports, irregular timing treated as
regular, pseudoreplication) quietly inflate confidence. This pipeline makes that class of error
**hard to commit undetected** — it surfaces conflicts and lets you resolve them, and it never
imputes or silently merges.

## What it does
Five tabs, kept deliberately simple:
- **Data** — combine several TraitFinder exports (`.csv`/`.zip`) into one experiment, resolve
  **duplicate scans** (you pick the keeper; nothing is auto-removed) and **remove blank pots**.
- **Time-series** — trait trajectories on a true elapsed-days axis (no imputation), grouped by any
  label, with a one-click **animated chart**.
- **3D viewer** — view any PlantEye `.ply` scan from above and the side in **true colour or 20
  colour indices** (NDVI, NDVI-940, NIR, PSRI, NPCI, GLI, ExG, ExR, ExG−ExR, NGRDI, VARI, MGRVI,
  RGBVI, TGI, CIVE, hue, lightness, saturation, height). Animate it:
  a **turntable**, a plant **growing over time** (top, side or 3D), **spinning through time**, or
  **several plants side by side** in sync. One colour scale per animation; GIFs need no extra software.
- **Export** — the cleaned dataset, a copy of just the `.ply` files that match it (duplicates and
  blank pots left out, with the reason for each), every animation, and a log of every decision.
- **Advanced** — optional factors, quality and missing-data checks, outlier curation, design
  workbook and manual-measurement validation.

## Install (no coding required)
Download the latest build for your OS from the **[Releases](../../releases)** page:

| OS | Download | How to run |
|---|---|---|
| **Windows** | `PhenoCleaning-Setup.exe` (installer) | Run it → Start-menu / desktop shortcut |
| **Windows (no install)** | `PhenoCleaning-Windows-x64-portable.zip` | Unzip → run `PhenoCleaning\PhenoCleaning.exe` |
| **macOS (Apple Silicon *or* Intel)** | `PhenoCleaning-macOS-universal2-portable.zip` | Unzip → right-click `PhenoCleaning` → **Open** (first time) |
| **Linux** | `PhenoCleaning-Linux-x64-portable.zip` | Unzip → `./PhenoCleaning/PhenoCleaning` |

The app opens in your browser at `http://localhost:8501`. It runs on your own machine, so it reads
your local and network-drive data directly. (First launch on Windows/macOS may warn about an
unidentified developer — see `PUBLISHING.md` for code-signing/notarization notes.)

## System requirements

**To run the app (end users):**

| | Minimum | Recommended |
|---|---|---|
| **OS** | Windows 10 (64-bit) · macOS 12 · Linux with glibc ≥ 2.35 (Ubuntu 22.04+, Debian 12+, RHEL/Rocky 9+) | Windows 11 · current macOS |
| **CPU** | any 64-bit processor (x86-64; Apple Silicon or Intel Mac — one universal2 build) | a recent 4-core+ laptop or desktop |
| **RAM** | 4 GB | 8 GB |
| **Disk** | ~300 MB for the app | plus room for your data and exported animations (GIFs are ~0.1–1 MB each) |
| **Display** | 1280 × 800 | 1920 × 1080 |
| **GPU** | not used | — |

- **No other software needed.** The installer includes everything; you do **not** need Python,
  ffmpeg, Open3D or a graphics card. Animations are saved as GIFs, which open in any browser and
  drop straight into PowerPoint, Keynote or Google Slides.
- **Browser:** the app opens in your default browser — Chrome, Edge, Firefox or Safari (current versions).
- **Speed:** cleaning and charting the tabular data is instant. In the 3D viewer, a still view takes
  about a second; animations take a few seconds up to ~30 s on a typical laptop (spinning through
  time is the slowest).
- **Internet:** not needed to run — only to download the app. Everything stays on your computer.
- **Network drives:** data on a mapped/network drive works (VPN/campus connection as applicable).
  The 3D viewer reads `.ply` files on demand, so for large experiments it is faster to copy the
  `.ply` folder to your own disk first.
- **Port:** the app uses local port `8501` (or the next free one).

**To run from source (developers):** Python 3.12 with the pinned packages in `requirements.txt`
(streamlit, numpy, pandas, matplotlib, pillow, openpyxl).

**To build the standalone apps (maintainers):** Python 3.12 + PyInstaller (+ Inno Setup on Windows),
or just use the GitHub Actions workflow. See `packaging/README.md`.

**Input data it accepts:** TraitFinder tabular exports (`*_PlantEye.csv` or `.zip`); optionally the
PlantEye 3D files (`<carrier>_<time>_full.ply` whole-carrier scans or `…_unitfile_N_1.ply` per-plant
scans, as downloaded with the portal's 3D downloader), an `.xlsx` design workbook, and an `.xlsx`
manual-measurements workbook. No data is bundled — you point the app at your own folders.

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
