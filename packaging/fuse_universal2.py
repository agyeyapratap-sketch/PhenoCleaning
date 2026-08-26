#!/usr/bin/env python3
"""Fuse arm64 + x86_64 macOS wheels into universal2 wheels, for a universal2 PyInstaller build.

numpy / scipy / pandas / pyarrow (and friends) do **not** publish universal2 wheels — they ship
separate ``*_arm64`` and ``*_x86_64`` wheels. To build one macOS app that runs natively on both
Apple Silicon and Intel, CI downloads both single-arch wheel sets and this script merges each
matched pair with ``delocate-merge`` (from the ``delocate`` package). Pure-python (``py3-none-any``)
and already-``universal2`` wheels are copied through unchanged.

Usage:  python fuse_universal2.py <arm64_dir> <x86_64_dir> <out_dir>

Called from .github/workflows/build-app.yml on the macOS leg. Dev-only; not shipped in the app.
"""
import glob
import os
import shutil
import subprocess
import sys


def _prefix(path):
    """Everything before the ``-macosx`` platform tag — identifies distribution+version+abi.

    e.g. ``numpy-2.5.1-cp312-cp312-macosx_11_0_arm64.whl`` → ``numpy-2.5.1-cp312-cp312``,
    so an arm64 wheel and its x86_64 counterpart share a prefix and can be paired. Only
    ``macosx_*`` wheels reach this function — pure-python (``none-any``) and already-``universal2``
    wheels are filtered out before pairing.
    """
    name = os.path.basename(path)
    return name.split("-macosx")[0] if "-macosx" in name else name


def main():
    if len(sys.argv) != 4:
        sys.exit("usage: fuse_universal2.py <arm64_dir> <x86_64_dir> <out_dir>")
    arm_dir, x86_dir, out_dir = sys.argv[1:4]
    os.makedirs(out_dir, exist_ok=True)

    x86 = {_prefix(w): w for w in glob.glob(os.path.join(x86_dir, "*.whl"))}
    merged = pure = already = single = 0

    for w in sorted(glob.glob(os.path.join(arm_dir, "*.whl"))):
        name = os.path.basename(w)
        if "none-any" in name:                 # pure python — no arch to fuse
            shutil.copy(w, out_dir); pure += 1
        elif "universal2" in name:             # some deps already ship a fat wheel
            shutil.copy(w, out_dir); already += 1
        elif _prefix(w) in x86:
            x86_wheel = x86[_prefix(w)]
            if "universal2" in os.path.basename(x86_wheel):
                # The wheel pip picked for the x86_64 target is itself already universal2 — some
                # packages (e.g. httptools) ship only arm64 + universal2, with no x86_64-only wheel.
                # Merging an arm64-thin wheel with an already-fat one makes `lipo` fail on the
                # duplicate arm64 slice, so just use the fat wheel — it already has both arches.
                shutil.copy(x86_wheel, out_dir); already += 1
                print(f"  INFO: {name}: x86_64 counterpart is already universal2 — used as-is",
                      file=sys.stderr)
            else:                              # the common case: merge the arm64/x86_64 pair
                subprocess.check_call(["delocate-merge", w, x86_wheel, "-w", out_dir])
                merged += 1
        else:                                  # no x86_64 counterpart — copy, best effort
            shutil.copy(w, out_dir); single += 1
            print(f"  WARN: no x86_64 match for {name} — copied arm64-only "
                  "(a universal2 build may reject it)", file=sys.stderr)

    print(f"universal2 wheels ready in {out_dir}: "
          f"merged={merged} pure={pure} already-universal2={already} arm-only={single}")
    if single:
        print("  note: arm-only wheels above have no Intel slice; if PyInstaller then fails on "
              "target_arch, that package is the reason.", file=sys.stderr)


if __name__ == "__main__":
    main()
