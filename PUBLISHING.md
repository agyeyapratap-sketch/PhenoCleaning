# Publishing to GitHub and shipping Windows / macOS / Linux apps

A complete, non-expert walkthrough: put this project on GitHub, and let GitHub build a
**double-click Windows installer** plus **macOS and Linux** apps for you — no Mac or Linux
machine required (GitHub builds each in the cloud, because installers/binaries can't be
cross-compiled).

---

## 0. Before you publish (read once)

- **The app is named PhenoCleaning.** "PlantEye" is Phenospex's product name, so this tool ships
  under the distinct name **PhenoCleaning**. That name appears in `README.md`,
  `CITATION.cff`, `packaging/installer.iss`, `packaging/phenocleaning.spec` (the `name=` and exe), and
  the app title in `app/pe_app.py`.
- **Data is already excluded.** `.gitignore` keeps out all data (`*.ply/*.zip/*.xlsx/*.csv`), the
  unpublished `docs/` PDF, dev-only scripts, and build outputs. Institutional paths were removed
  from the code. Nothing private should be committed — verify in step 3.

---

## 1. One-time setup

- **Git** — already installed (`git --version`).
- **A GitHub account** — https://github.com/signup (free).
- *(Optional, only to build the Windows installer on your own PC)* **Inno Setup** —
  https://jrsoftware.org/isdl.php

---

## 2. The local repository is ready

This folder has been initialised as a git repository with a first commit. To confirm:
```powershell
cd "<...>\PlantEyePipeline"
git log --oneline -1
git status
```

---

## 3. Sanity-check what will be published
```powershell
git ls-files          # lists exactly the files that will go public — skim it
```
There should be **no** `.ply/.csv/.xlsx`, no `docs/`, no `dist/` or `build/`, and no personal
paths. If you see anything sensitive, tell me and I'll adjust `.gitignore`.

---

## 4. Create the GitHub repository (web browser)

1. Go to **https://github.com/new**.
2. **Repository name**: e.g. `planteye-pipeline` (or your chosen name).
3. Choose **Public** (needed for free CI + a citable release) or Private.
4. **Do NOT** tick "Add a README / .gitignore / license" — this project already has them.
5. Click **Create repository**. GitHub shows a URL like `https://github.com/USERNAME/planteye-pipeline.git`.

---

## 5. Push your code

> **Commit author:** the first commit is authored as
> `Agyeya Pratap <agyeyapratap@users.noreply.github.com>` — a GitHub no-reply address, so your
> personal email is never exposed. If your GitHub username is different, re-point it so GitHub
> attributes the commit to you:
> ```powershell
> git config user.email "YOUR_GITHUB_ID@users.noreply.github.com"
> git commit --amend --reset-author --no-edit
> ```

Copy the URL from step 4, then:
```powershell
git remote add origin https://github.com/USERNAME/planteye-pipeline.git
git branch -M main
git push -u origin main
```
(You'll be prompted to sign in to GitHub the first time.)

---

## 6. Build the apps (three OSes, automatically)

**Option A — build now to test:**
1. On your repo page: **Actions** tab → **Build apps** → **Run workflow** → **Run**.
2. Wait ~5–10 min. Open the finished run → **Artifacts** → download:
   - `PhenoCleaning-Windows-Installer` (the `PhenoCleaning-Setup.exe` installer)
   - `PhenoCleaning-Windows-portable`, `PhenoCleaning-macOS-portable`, `PhenoCleaning-Linux-portable` (zips)

**Option B — publish a Release (recommended for real distribution):**
1. Repo page → **Releases** → **Draft a new release**.
2. **Choose a tag** → type `v1.0.0` → **Create new tag**. Add a title/notes → **Publish release**.
3. The workflow runs automatically and **attaches all installers/zips to that Release**.
4. Share the Release page — users download the file for their OS (see the table in `README.md`).

---

## 7. Building the Windows installer on your own PC (optional)
If you'd rather not use CI for Windows:
```powershell
# 1) build the app (short output path avoids Windows' 260-char limit)
pyinstaller packaging\phenocleaning.spec --noconfirm
# 2) compile the installer
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss
# -> packaging\Output\PhenoCleaning-Setup.exe
```

---

## 8. Platform caveats (tell your users)

- **Windows:** the installer/app is unsigned, so SmartScreen may say *"Windows protected your PC"*
  → **More info → Run anyway**. A code-signing certificate removes this (paid).
- **macOS:** the app is unsigned/unnotarized → Gatekeeper blocks it → **right-click → Open** the
  first time (then **Open** in the dialog). Proper notarization needs a paid Apple Developer
  account; a signed/notarized `.app` and a `.dmg` are a later step.
- **Linux:** ship the zip, or wrap the folder as an **AppImage** for a true double-click (later step).

---

## 9. Make it citable (optional, good for the paper)
Enable the **Zenodo–GitHub integration** (https://zenodo.org/account/settings/github/), then each
GitHub Release mints a **DOI** automatically. Add that DOI to `CITATION.cff` and your paper.

---

## Updating later
Make changes → `git add -A && git commit -m "..." && git push`. For a new build, publish a new
Release (e.g. `v1.0.1`) and CI rebuilds everything.
