# PyInstaller spec — PlantEye pipeline standalone app (one-folder build).
# Build from the PlantEyePipeline folder:  pyinstaller packaging/planteye.spec --noconfirm
# Output: dist/PlantEye/PlantEye.exe  (ship the whole dist/PlantEye folder, or an installer)
import os
from PyInstaller.utils.hooks import collect_all, copy_metadata

ROOT = os.getcwd()  # PlantEyePipeline

# macOS multi-arch: CI sets PYI_TARGET_ARCH=universal2 (after fusing universal2 wheels) so one
# build runs natively on both Apple Silicon and Intel. Unset on Windows/Linux → native build.
TARGET_ARCH = os.environ.get("PYI_TARGET_ARCH") or None

datas, binaries, hiddenimports = [], [], []

# Streamlit (and altair) must be collected wholesale — code, data files, and metadata.
for pkg in ("streamlit", "altair"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

# Some packages are located at runtime via importlib.metadata — copy their dist-info.
for meta in ("streamlit", "numpy", "pandas", "altair", "pyarrow", "packaging"):
    try:
        datas += copy_metadata(meta)
    except Exception:
        pass

# The app's own source, bundled as data so Streamlit can run it (mirrors the source layout).
for f in ("pe_app.py", "pe_data.py", "pe_qc.py", "pe_design.py"):
    datas.append((os.path.join(ROOT, "app", f), "app"))
datas.append((os.path.join(ROOT, "tools", "planteye_io.py"), "tools"))

hiddenimports += [
    "scipy", "scipy.spatial", "scipy.spatial.ckdtree", "scipy.ndimage",
    "scipy.spatial._ckdtree", "scipy.spatial.transform",
    "matplotlib", "matplotlib.backends.backend_agg", "mpl_toolkits.mplot3d",
    "openpyxl", "pandas", "numpy", "pyarrow",
]

a = Analysis(
    [os.path.join(ROOT, "packaging", "launcher.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # heavy ML/data libs present on this machine but unused by the app
        "torch", "torchvision", "torchaudio", "tensorflow", "tensorboard",
        "keras", "jax", "jaxlib", "sympy", "numba", "llvmlite",
        # notebook / GUI stacks not needed for a headless Streamlit build
        "IPython", "ipykernel", "notebook", "nbconvert", "nbformat", "jupyter",
        "tkinter", "PyQt5", "PySide2", "PySide6", "PyQt6",
        "pytest", "sphinx",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="PlantEye",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=True,  # keep a console so users/we can see logs & errors (flip to False later)
    target_arch=TARGET_ARCH,  # 'universal2' on macOS CI; None (native) elsewhere
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PlantEye")
