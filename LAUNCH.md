# Launching the PhenoCleaning app

Two ways to run it as a **relaunchable** app. Both keep it running until *you* stop it.

---

## Option A — One-click launcher (recommended for now)

No Docker needed, and it has **full access to your data** (including mapped network drives).

1. Double-click **`run_app.bat`** in this folder (or, in PowerShell:
   `powershell -ExecutionPolicy Bypass -File run_app.ps1`).
2. A console window opens and a browser tab appears at **http://localhost:8501**.
3. **Leave the console window open** while you work. To stop the app, close that window
   or press **Ctrl+C** in it. To relaunch, just double-click `run_app.bat` again.

That's it — because *you* launched it, it stays up (unlike a server launched by an assistant,
which the environment can terminate).

> Tip: right-click `run_app.bat` → *Send to* → *Desktop (create shortcut)* for a one-click icon.

---

## Option B — Docker (portable / shareable, for later)

Docker gives a reproducible, auto-restarting container — ideal for sharing the tool or for a
GitHub release. **Docker Desktop must be installed first** (it is not on this machine yet:
https://www.docker.com/products/docker-desktop/).

```powershell
# from this folder
docker compose up -d          # build + start; stays up, auto-restarts on reboot
#  -> open http://localhost:8501
docker compose logs -f        # watch logs
docker compose down           # stop
```

Or without compose:

```powershell
docker build -t phenocleaning .
docker run --rm -p 8501:8501 -v "/path/to/your/data:/data/experiment:ro" -e PLANTEYE_DEFAULT_FOLDER=/data/experiment phenocleaning
```

### Data access inside Docker — read this
A container is isolated: it can only see host folders you **mount** as volumes (see
`docker-compose.yml`). Edit the host paths there to your data, then in the app sidebar type the
**container** path (e.g. `/data/downloads/...`) as the experiment folder.

**Windows network drives (e.g. a mapped `Z:\`) generally do NOT mount into Docker.** For such data, either
copy it to a local folder first and mount that, or just use **Option A** (native launcher), which
reads mapped drives directly. Docker shines for the *shareable* version where users upload their own data.

---

## Requirements
- **Option A:** Python 3.12+ on PATH with the packages in `requirements.txt`
  (`pip install -r requirements.txt` if you ever move to a fresh machine/venv).
- **Option B:** Docker Desktop; everything else is built into the image from `requirements.txt`.

## Stopping a stray server
If a port-in-use error appears, another instance is running. Native: close its console window.
Find strays: `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? { $_.CommandLine -like '*streamlit*' }`
then `Stop-Process -Id <PID>`. Docker: `docker compose down`.
