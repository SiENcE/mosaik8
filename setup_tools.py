#!/usr/bin/env python3
"""MosaiK8 toolchain & emulator installer.

Downloads everything the build tool and the test harnesses need, into the
same folders the repo expects (all of them gitignored):

    gbdk/              GBDK-2020 (lcc/sdcc)        -> GBDK-backend consoles
    cc65/              cc65 (cl65)                 -> Lynx / PC Engine
    emu/libretro/      Handy + Beetle Lynx cores   -> headless Lynx testing
                       (driven by emu/libretro/run_lynx.py via libretro.py)
    pip packages       pyboy (headless Game Boy), libretro.py, pillow, toml

Usage:
    python setup_tools.py                # install everything that is missing
    python setup_tools.py --only gbdk,cores
    python setup_tools.py --force        # reinstall even if already present
    python setup_tools.py --check        # report what is installed, change nothing

Notes:
- The Beetle Lynx core needs the real Lynx boot ROM (lynxboot.img, 512 bytes,
  copyrighted -- not downloadable here). Drop it into emu/libretro/ yourself;
  without it the harness falls back to the Handy core, which boots homebrew
  BIOS-less.
- cc65 binary snapshots are published for Windows only; on other systems
  install cc65 from your package manager and set CC65_HOME.
"""

import argparse
import io
import os
import platform as _platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
GBDK_DIR = os.path.join(ROOT, "gbdk")
CC65_DIR = os.path.join(ROOT, "cc65")
LIBRETRO_DIR = os.path.join(ROOT, "emu", "libretro")

GBDK_RELEASE_API = "https://api.github.com/repos/gbdk-2020/gbdk-2020/releases/latest"
GBDK_FALLBACK_URL = ("https://github.com/gbdk-2020/gbdk-2020/releases/download/"
                     "4.5.0/gbdk-{asset}")
CC65_SNAPSHOT_URL = ("https://sourceforge.net/projects/cc65/files/"
                     "cc65-snapshot-win32.zip/download")
BUILDBOT = "https://buildbot.libretro.com/nightly/{os}/x86_64/latest/{core}.zip"

PIP_PACKAGES = ["toml", "pillow", "pyboy", "libretro.py"]


def log(msg):
    print(msg, flush=True)


def download(url, dest_path):
    """Download url to dest_path with a small progress indicator."""
    log(f"  downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "mosaik8-setup"})
    show_progress = sys.stdout.isatty()
    with urllib.request.urlopen(req) as resp, open(dest_path, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if total and show_progress:
                sys.stdout.write(f"\r    {done * 100 // total}% of {total // 1024} KiB")
                sys.stdout.flush()
        if total and show_progress:
            sys.stdout.write("\n")
    return dest_path


def host_os():
    s = _platform.system()
    return {"Windows": "windows", "Linux": "linux", "Darwin": "mac"}.get(s, s.lower())


# --------------------------------------------------------------------------
# GBDK-2020


def gbdk_installed():
    lcc = "lcc.exe" if os.name == "nt" else "lcc"
    return os.path.isfile(os.path.join(GBDK_DIR, "bin", lcc))


def gbdk_asset_name():
    osname = host_os()
    if osname == "windows":
        return "win64.zip" if _platform.machine().endswith("64") else "win32.zip"
    if osname == "linux":
        return ("linux-arm64.tar.gz" if _platform.machine().startswith("a")
                else "linux64.tar.gz")
    if osname == "mac":
        return ("macos-arm64.tar.gz" if _platform.machine() == "arm64"
                else "macos.tar.gz")
    raise RuntimeError(f"no GBDK-2020 binary release for this OS ({osname})")


def install_gbdk():
    """Fetch the latest GBDK-2020 release and unpack it as ./gbdk."""
    import json
    asset = gbdk_asset_name()
    url = GBDK_FALLBACK_URL.format(asset=asset)
    try:
        req = urllib.request.Request(GBDK_RELEASE_API,
                                     headers={"User-Agent": "mosaik8-setup"})
        with urllib.request.urlopen(req) as resp:
            release = json.load(resp)
        for a in release.get("assets", []):
            if a["name"] == f"gbdk-{asset}":
                url = a["browser_download_url"]
                log(f"  latest GBDK-2020 release: {release.get('tag_name')}")
                break
    except OSError as e:
        log(f"  (GitHub API unreachable, using pinned release: {e})")

    with tempfile.TemporaryDirectory() as tmp:
        archive = download(url, os.path.join(tmp, os.path.basename(url)))
        unpack_dir = os.path.join(tmp, "unpacked")
        # Both the zip and the tarballs contain a single top-level gbdk/ folder.
        if archive.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(unpack_dir)
        else:
            import tarfile
            with tarfile.open(archive) as tf:
                tf.extractall(unpack_dir)
        inner = os.path.join(unpack_dir, "gbdk")
        if not os.path.isdir(inner):  # archive without wrapper dir
            inner = unpack_dir
        if os.path.isdir(GBDK_DIR):
            shutil.rmtree(GBDK_DIR)
        shutil.move(inner, GBDK_DIR)
    log(f"  installed GBDK-2020 -> {GBDK_DIR}")


# --------------------------------------------------------------------------
# cc65


def cc65_installed():
    cl65 = "cl65.exe" if os.name == "nt" else "cl65"
    return os.path.isfile(os.path.join(CC65_DIR, "bin", cl65))


def install_cc65():
    """Fetch the cc65 Windows snapshot and unpack it as ./cc65."""
    if host_os() != "windows":
        log("  cc65 binary snapshots exist for Windows only.")
        log("  Install cc65 from your package manager (apt/brew install cc65)")
        log("  and set CC65_HOME to the install prefix.")
        return
    with tempfile.TemporaryDirectory() as tmp:
        archive = download(CC65_SNAPSHOT_URL, os.path.join(tmp, "cc65-snapshot.zip"))
        if not zipfile.is_zipfile(archive):
            raise RuntimeError(
                "cc65 download did not return a zip (SourceForge mirror issue?). "
                "Re-run, or fetch cc65-snapshot-win32.zip manually from "
                "https://cc65.github.io/ and unpack it as ./cc65")
        unpack_dir = os.path.join(tmp, "unpacked")
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(unpack_dir)
        # The snapshot zip has bin/, lib/, cfg/, ... at the top level.
        inner = unpack_dir
        if not os.path.isdir(os.path.join(inner, "bin")):
            entries = os.listdir(inner)
            if len(entries) == 1:
                inner = os.path.join(inner, entries[0])
        if os.path.isdir(CC65_DIR):
            shutil.rmtree(CC65_DIR)
        shutil.move(inner, CC65_DIR)
    log(f"  installed cc65 -> {CC65_DIR}")


# --------------------------------------------------------------------------
# libretro cores (headless Lynx testing)


def core_filename(core):
    ext = {"windows": "dll", "linux": "so", "mac": "dylib"}[host_os()]
    return f"{core}.{ext}"


def cores_installed():
    return all(os.path.isfile(os.path.join(LIBRETRO_DIR, core_filename(c)))
               for c in ("handy_libretro", "mednafen_lynx_libretro"))


def install_cores():
    """Fetch the Handy and Beetle Lynx cores from the libretro buildbot."""
    os.makedirs(LIBRETRO_DIR, exist_ok=True)
    for core in ("handy_libretro", "mednafen_lynx_libretro"):
        name = core_filename(core)
        url = BUILDBOT.format(os=host_os(), core=name)
        with tempfile.TemporaryDirectory() as tmp:
            archive = download(url, os.path.join(tmp, name + ".zip"))
            with zipfile.ZipFile(archive) as zf:
                zf.extract(name, tmp)
            dest = os.path.join(LIBRETRO_DIR, name)
            if os.path.isfile(dest):
                os.remove(dest)
            shutil.move(os.path.join(tmp, name), dest)
        log(f"  installed {name} -> {LIBRETRO_DIR}")
    if not os.path.isfile(os.path.join(LIBRETRO_DIR, "lynxboot.img")):
        log("  note: no lynxboot.img (Lynx boot ROM, copyrighted). The harness")
        log("  will use Handy (BIOS-less). For the Beetle Lynx core, copy the")
        log("  512-byte lynxboot.img into emu/libretro/ yourself.")


# --------------------------------------------------------------------------
# Python packages (emulator harnesses)


def python_packages_installed():
    import importlib.util
    mods = ("toml", "PIL", "pyboy", "libretro")
    return all(importlib.util.find_spec(m) is not None for m in mods)


def install_python_packages():
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade"] + PIP_PACKAGES
    log("  " + " ".join(cmd))
    subprocess.run(cmd, check=True)


# --------------------------------------------------------------------------

COMPONENTS = {
    "gbdk":   (gbdk_installed, install_gbdk, "GBDK-2020 toolchain (gbdk/)"),
    "cc65":   (cc65_installed, install_cc65, "cc65 toolchain (cc65/)"),
    "cores":  (cores_installed, install_cores, "libretro Lynx cores (emu/libretro/)"),
    "python": (python_packages_installed, install_python_packages,
               "Python packages (pyboy, libretro.py, pillow, toml)"),
}


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="MosaiK8 toolchain installer")
    ap.add_argument("--only", metavar="LIST",
                    help="comma-separated subset: " + ",".join(COMPONENTS))
    ap.add_argument("--force", action="store_true",
                    help="reinstall components that are already present")
    ap.add_argument("--check", action="store_true",
                    help="only report what is installed")
    args = ap.parse_args()

    names = list(COMPONENTS)
    if args.only:
        names = [n.strip() for n in args.only.split(",") if n.strip()]
        unknown = [n for n in names if n not in COMPONENTS]
        if unknown:
            ap.error(f"unknown component(s): {', '.join(unknown)}")

    failures = 0
    for name in names:
        installed, install, label = COMPONENTS[name]
        present = installed()
        if args.check:
            log(f"[{'ok' if present else 'missing'}] {label}")
            continue
        if present and not args.force:
            log(f"[skip] {label} — already installed")
            continue
        log(f"[install] {label}")
        try:
            install()
        except Exception as e:
            log(f"  ❌ {name} failed: {e}")
            failures += 1

    if not args.check:
        log("")
        log("Done." if not failures else f"{failures} component(s) FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
