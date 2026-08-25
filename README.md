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
- **Export** — tidy CSVs, cleaned mesh `.ply`, and a full audit log.

## Install (no coding required)
Download the latest build for your OS from the **[Releases](../../releases)** page:

| OS | Download | How to run |
|---|---|---|
| **Windows** | `PlantEye-Setup.exe` (installer) | Run it → Start-menu / desktop shortcut |
| **macOS** | `PlantEye-macOS-portable.zip` | Unzip → right-click `PlantEye` → **Open** (first time) |
| **Linux** | `PlantEye-Linux-portable.zip` | Unzip → `./PlantEye/PlantEye` |

The app opens in your browser at `http://localhost:8501`. It runs on your own machine, so it reads
your local and network-drive data directly. (First launch on Windows/macOS may warn about an
unidentified developer — see `PUBLISHING.md` for code-signing/notarization notes.)

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
