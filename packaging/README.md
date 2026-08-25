# Building the standalone PlantEye app (no Python needed by users)

Produces a **double-click application** for non-coders — no Python install, no terminal.
Because it runs natively, it reads local **and network-drive** data directly (unlike Docker).

Files here:
- `launcher.py` — the app entry point that boots Streamlit and opens the browser.
- `planteye.spec` — the PyInstaller recipe (bundles the app + scientific stack, excludes torch etc.).

---

## Build it on Windows (verified working)

From the `PlantEyePipeline` folder, with the deps installed
(`pip install -r requirements.txt pyinstaller`):

```powershell
pyinstaller packaging\planteye.spec --noconfirm
```

> If you hit `WinError 206: filename too long`, build to a short path:
> `pyinstaller packaging\planteye.spec --noconfirm --distpath C:\Users\<you>\pe_dist --workpath C:\Users\<you>\pe_build`
> (Windows' 260-char path limit + a long project path is the cause. Keep the output near the drive root.)

**Output:** `dist\PlantEye\` — a ~300 MB folder. Users run **`PlantEye.exe`** inside it.
To distribute: zip the whole `PlantEye` folder and send it (they unzip and double-click the exe).

Keep the built folder **outside OneDrive** (don't let 300 MB sync). A great next step for a polished
installer with a Start-menu shortcut is **Inno Setup** wrapping this folder — ask and I'll add it.

---

## macOS & Linux — build via GitHub Actions (can't cross-compile from Windows)

PyInstaller builds a native binary **on each OS**, so Mac/Linux binaries can't be made on a Windows
machine. The included workflow does it for you in the cloud:

`.github/workflows/build-app.yml` builds on `windows-latest`, `macos-latest`, and `ubuntu-latest`,
zips each, and (when you publish a GitHub Release) attaches all three to it.

1. Push this project to GitHub.
2. **Actions → Build standalone apps → Run workflow** (or publish a Release).
3. Download `PlantEye-Windows.zip`, `PlantEye-macOS.zip`, `PlantEye-Linux.zip` from the run's artifacts
   (or the Release).

### Platform notes
- **Windows:** users may see a SmartScreen "unknown publisher" prompt (unsigned) → *More info → Run anyway*.
  Code-signing removes it (needs a certificate).
- **macOS:** the binary is unsigned, so Gatekeeper blocks it → right-click → *Open* the first time, or
  notarize it (needs a paid Apple Developer account). A proper `.app` bundle + notarization is a later step.
- **Linux:** ship the folder or wrap it as an **AppImage** for a true double-click (later step).

---

## What's excluded (and why the build is ~300 MB not multi-GB)
The spec **excludes** `torch`, `tensorflow`, `jax`, `IPython`/notebook, and GUI toolkits — they may be
installed on your dev machine but the app never uses them, and torch in particular broke the Windows
build with over-long paths. If you add a feature that needs a new library, add it to `requirements.txt`
and (if PyInstaller misses it) to `hiddenimports` in `planteye.spec`, then rebuild.
