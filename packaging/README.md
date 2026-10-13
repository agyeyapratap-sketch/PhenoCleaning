# Building the standalone PhenoCleaning app (no Python needed by users)

Produces a **double-click application** for non-coders — no Python install, no terminal.
Because it runs natively, it reads local **and network-drive** data directly (unlike Docker).

Files here:
- `launcher.py` — the app entry point that boots Streamlit and opens the browser.
- `phenocleaning.spec` — the PyInstaller recipe (bundles the app + scientific stack, excludes torch etc.).

---

## Build it on Windows (verified working)

From the `PlantEyePipeline` folder, with the deps installed
(`pip install -r requirements.txt pyinstaller`):

```powershell
pyinstaller packaging\phenocleaning.spec --noconfirm
```

> If you hit `WinError 206: filename too long`, build to a short path:
> `pyinstaller packaging\phenocleaning.spec --noconfirm --distpath C:\Users\<you>\pe_dist --workpath C:\Users\<you>\pe_build`
> (Windows' 260-char path limit + a long project path is the cause. Keep the output near the drive root.)

**Output:** `dist\PhenoCleaning\` — a folder of roughly 300 MB. Users run **`PhenoCleaning.exe`**
inside it. To distribute: zip the whole `PhenoCleaning` folder (they unzip and double-click the exe),
or wrap it in the Windows installer:

```powershell
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss   # -> packaging\Output\PhenoCleaning-Setup.exe
```

Keep the built folder **outside OneDrive** so it doesn't sync.

---

## macOS & Linux — build via GitHub Actions (can't cross-compile from Windows)

PyInstaller builds a native binary **on each OS**, so Mac/Linux binaries can't be made on a Windows
machine. The included workflow does it for you in the cloud:

`.github/workflows/build-app.yml` builds on `windows-latest`, `macos-latest` (one **universal2** app
for Apple Silicon and Intel) and `ubuntu-22.04`, and when you publish a GitHub Release attaches:
`PhenoCleaning-Setup.exe`, `PhenoCleaning-Windows-x64-portable.zip`,
`PhenoCleaning-macOS-universal2-portable.zip` and `PhenoCleaning-Linux-x64-portable.zip`.

1. Push this project to GitHub.
2. Publish a Release (or **Actions → Build standalone apps → Run workflow** for a test build).
3. The files appear on the Release a few minutes later (or under the run's artifacts).

### Platform notes
- **Windows:** users may see a SmartScreen "unknown publisher" prompt (unsigned) → *More info → Run anyway*.
  Code-signing removes it (needs a certificate).
- **macOS:** the binary is unsigned, so Gatekeeper blocks it → right-click → *Open* the first time, or
  notarize it (needs a paid Apple Developer account). A proper `.app` bundle + notarization is a later step.
- **Linux:** ship the folder or wrap it as an **AppImage** for a true double-click (later step).

---

## What's included and excluded
The app needs only streamlit, numpy, pandas, matplotlib, pillow and openpyxl (`requirements.txt`).
The spec **excludes** `torch`, `tensorflow`, `jax`, `scipy`, `IPython`/notebook, and GUI toolkits —
they may be installed on your dev machine but the app never uses them, and torch in particular broke
the Windows build with over-long paths. If you add a feature that needs a new library, add it to `requirements.txt`
and (if PyInstaller misses it) to `hiddenimports` in `phenocleaning.spec`, then rebuild.
