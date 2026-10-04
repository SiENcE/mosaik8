#!/usr/bin/env python3
"""MosaiK8 toolchain & emulator installer.

Downloads everything the build tool and the test harnesses need, into the
same folders the repo expects (all of them gitignored):

    gbdk/              GBDK-2020 (lcc/sdcc)        -> GBDK-backend consoles
    cc65/              cc65 (cl65)                 -> Lynx / PC Engine
    emu/libretro/      Handy + Beetle Lynx + Holani (Lynx) + Beetle PCE Fast +
                       Genesis Plus GX (SMS/Game Gear) + FCEUmm (NES) cores ->
                       headless console testing (driven by emu/libretro/
                       run_lynx.py via libretro.py)
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
import hashlib
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

# Where the fetched toolchains GO. Beside this file by default, which is what
# a checkout wants and what every path in this repo assumes. An installed
# studio cannot write into its own install tree (Program Files, a signed .app),
# so it passes --prefix, or sets $MOSAIK8_HOME and lets both sides agree
# without a flag -- the same variable mosaik8_build.py's toolchain finders read.
PREFIX = ROOT
GBDK_DIR = os.path.join(PREFIX, "gbdk")
CC65_DIR = os.path.join(PREFIX, "cc65")
LIBRETRO_DIR = os.path.join(PREFIX, "emu", "libretro")


def set_prefix(prefix):
    """Rebind the install directories under `prefix` (module-level by design:
    every installer below reads the three globals)."""
    global PREFIX, GBDK_DIR, CC65_DIR, LIBRETRO_DIR
    PREFIX = os.path.abspath(os.path.expanduser(prefix))
    GBDK_DIR = os.path.join(PREFIX, "gbdk")
    CC65_DIR = os.path.join(PREFIX, "cc65")
    LIBRETRO_DIR = os.path.join(PREFIX, "emu", "libretro")
    return PREFIX


def default_prefix():
    """$MOSAIK8_HOME when set, else this checkout."""
    env = os.environ.get("MOSAIK8_HOME")
    return os.path.abspath(os.path.expanduser(env)) if env else ROOT


set_prefix(default_prefix())

GBDK_RELEASE_API = "https://api.github.com/repos/gbdk-2020/gbdk-2020/releases/latest"
GBDK_FALLBACK_URL = ("https://github.com/gbdk-2020/gbdk-2020/releases/download/"
                     "4.5.0/gbdk-{asset}")
CC65_SNAPSHOT_URL = ("https://sourceforge.net/projects/cc65/files/"
                     "cc65-snapshot-win32.zip/download")
BUILDBOT = "https://buildbot.libretro.com/nightly/{platform}/latest/{core}.zip"

# SHA-256 pins for the GBDK fallback release (immutable artifacts; taken from
# the GitHub release API's per-asset digests). The dynamic "latest release"
# path instead verifies against the digest the API reports for the chosen
# asset. The cc65 SourceForge snapshot and the libretro nightlies are ROLLING
# artifacts with no stable hash to pin -- those downloads log their SHA-256
# so an install is at least auditable, and warn that they are unverified.
GBDK_FALLBACK_SHA256 = {
    "linux-arm64.tar.gz":
        "31eb2235f0fdb60163d0b1e9574a022098d6069cd56606a1daca4478a46e0439",
    "linux64.tar.gz":
        "d7857a5f6d135ee4c249043ca26aad9f2ec8ab5d4106d97720d404114f42605c",
    "macos-arm64.tar.gz":
        "289ee60e46c5a2785a21e35533f84a5131ed4a063b21b0dbdedc9a10af15bf78",
    "macos.tar.gz":
        "1aa549d12032d8f6509d11923bb28b1a453098f42597feb378e9a42541f8fd89",
    "win32.zip":
        "30279db9e3cc3ebd5bac3c2679aa02b5613214567650a8eb51cfb498ea4c8fcd",
    "win64.zip":
        "266854ce92e3064871c5b28cd3436cc2a6cb136af9e7cf617140108f8c1c5890",
}

DOWNLOAD_TIMEOUT = 60  # seconds without data before a download aborts

PIP_PACKAGES = ["toml", "pillow", "pyboy", "libretro.py"]


def log(msg):
    print(msg, flush=True)


def download(url, dest_path, expected_sha256=None):
    """Download url to dest_path with a progress indicator.

    The archive is executed later, so integrity matters: HTTPS is required,
    the SHA-256 of what arrived is always logged, and when a pin is supplied
    a mismatch deletes the file and raises (a compromised or corrupt mirror
    must never leave an installable archive behind)."""
    if not url.lower().startswith("https://"):
        raise RuntimeError(f"refusing non-HTTPS download: {url}")
    log(f"  downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "mosaik8-setup"})
    show_progress = sys.stdout.isatty()
    digest = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp, \
            open(dest_path, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if total and show_progress:
                sys.stdout.write(f"\r    {done * 100 // total}% of {total // 1024} KiB")
                sys.stdout.flush()
        if total and show_progress:
            sys.stdout.write("\n")
    actual = digest.hexdigest()
    if expected_sha256:
        if actual != expected_sha256.lower():
            os.remove(dest_path)
            raise RuntimeError(
                f"SHA-256 mismatch for {url}\n"
                f"    expected {expected_sha256}\n"
                f"    got      {actual}\n"
                f"  The download was discarded. Re-run; if it persists the "
                f"mirror may be compromised or the pin stale.")
        log(f"  sha256 verified: {actual}")
    else:
        log(f"  sha256 {actual} (UNVERIFIED -- rolling artifact, no pin)")
    return dest_path


def _extractall_safe(tf, dest):
    """tarfile.extractall with the path-traversal filter (a malicious member
    like ../../x must not write outside dest). `filter=` exists on 3.12+ and
    the late 3.9/3.10/3.11 security releases; older Pythons fall back to the
    unfiltered call."""
    try:
        tf.extractall(dest, filter="data")
    except TypeError:
        tf.extractall(dest)


def host_os():
    s = _platform.system()
    return {"Windows": "windows", "Linux": "linux", "Darwin": "mac"}.get(s, s.lower())


# --------------------------------------------------------------------------
# GBDK-2020


def _found_here_or_on_the_system(local, home_var, tool):
    """Is a toolchain usable, by the BUILD's rules rather than ours?

    `mosaik8_build` looks for a compiler in three places: `$<TOOL>_HOME`, a
    tree beside the project, then PATH. A probe that only checked the tree we
    fetch would report "missing" on every Linux box that installed the
    toolchain from its package manager - and it did, which made the studio's
    setup wizard permanently wrong about a machine where builds work fine.
    The probe answers the question the user is actually asking: can I build?"""
    exe = tool + (".exe" if os.name == "nt" else "")
    if os.path.isfile(os.path.join(local, "bin", exe)):
        return True
    home = os.environ.get(home_var)
    if home and os.path.isfile(os.path.join(home, "bin", exe)):
        return True
    return shutil.which(tool) is not None


def gbdk_installed():
    return _found_here_or_on_the_system(GBDK_DIR, "GBDK_HOME", "lcc")


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
    sha256 = GBDK_FALLBACK_SHA256.get(asset)
    try:
        req = urllib.request.Request(GBDK_RELEASE_API,
                                     headers={"User-Agent": "mosaik8-setup"})
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
            release = json.load(resp)
        for a in release.get("assets", []):
            if a["name"] == f"gbdk-{asset}":
                url = a["browser_download_url"]
                # Verify against the digest the release API reports for this
                # asset (present on current GitHub releases).
                dig = a.get("digest") or ""
                sha256 = dig[7:] if dig.startswith("sha256:") else None
                log(f"  latest GBDK-2020 release: {release.get('tag_name')}")
                break
    except OSError as e:
        log(f"  (GitHub API unreachable, using pinned release: {e})")

    with tempfile.TemporaryDirectory() as tmp:
        archive = download(url, os.path.join(tmp, os.path.basename(url)),
                           expected_sha256=sha256)
        unpack_dir = os.path.join(tmp, "unpacked")
        # Both the zip and the tarballs contain a single top-level gbdk/ folder.
        if archive.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(unpack_dir)
        else:
            import tarfile
            with tarfile.open(archive) as tf:
                _extractall_safe(tf, unpack_dir)
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
    return _found_here_or_on_the_system(CC65_DIR, "CC65_HOME", "cl65")


def install_cc65():
    """Fetch the cc65 Windows snapshot and unpack it as ./cc65."""
    if not component_note("cc65")[0]:
        # Asked for explicitly (`--only cc65`) on a platform with no upstream
        # binary. A silent return here used to end in "Done." with nothing
        # installed, which is the one answer that helps nobody.
        raise RuntimeError(component_note("cc65")[1])
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


def buildbot_platform():
    """The buildbot's own {os}/{arch} path segment, which is NOT host_os().

    Two ways this differed from the obvious guess, both MEASURED against the
    server (2026-09-10) rather than assumed: macOS lives under `apple/osx`,
    not `mac`, and 64-bit ARM Linux is `aarch64` - `arm64` and `armv8` are
    both 404. All six cores we fetch exist for every combination below, so an
    arm64 machine is a supported preview host and not a special case.

    Windows publishes x86_64 only, so an ARM Windows host is told that
    plainly instead of downloading a 404 page."""
    machine = _platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    osname = host_os()
    if osname == "windows":
        if arm:
            raise RuntimeError(
                "the libretro buildbot publishes no ARM Windows cores; the "
                "GB/GBC preview (PyBoy) works, the others need a core built "
                "by hand into emu/libretro/")
        return "windows/x86_64"
    if osname == "linux":
        return "linux/aarch64" if arm else "linux/x86_64"
    if osname == "mac":
        return "apple/osx/arm64" if arm else "apple/osx/x86_64"
    raise RuntimeError("no libretro nightlies for this OS (%s)" % osname)


CORES = ("handy_libretro", "mednafen_lynx_libretro",
         # Holani: a cycle-stepped Rust Lynx core (a 3rd Lynx option beside
         # Handy/Beetle in the studio's core picker). Boots homebrew BIOS-less
         # via the bundled Free Lynx Boot ROM; uses lynxboot.img when present.
         "holani_libretro",
         "mednafen_pce_fast_libretro",
         # SMS + Game Gear (Genesis Plus GX) and NES (FCEUmm) -- both boot
         # homebrew BIOS-less, so the same run_lynx.py --core harness can
         # screen-diff those consoles too.
         "genesis_plus_gx_libretro", "fceumm_libretro")


def cores_installed():
    return all(os.path.isfile(os.path.join(LIBRETRO_DIR, core_filename(c)))
               for c in CORES)


def install_cores():
    """Fetch the libretro cores from the buildbot: Handy + Beetle Lynx (Lynx),
    Beetle PCE Fast (PC Engine), Genesis Plus GX (SMS / Game Gear) and FCEUmm
    (NES). All boot the framework's homebrew ROMs without a BIOS (Beetle Lynx
    being the exception -- see lynxboot.img note below)."""
    os.makedirs(LIBRETRO_DIR, exist_ok=True)
    for core in CORES:
        name = core_filename(core)
        url = BUILDBOT.format(platform=buildbot_platform(), core=name)
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
# The emulator libraries, as WHEELS, for an installed (frozen) studio.
#
# `python` above is the developer path: pip, into site-packages. An INSTALLED
# studio has neither - there is no pip in a frozen application and no
# site-packages to write to - so the same two libraries are fetched as wheels
# into <prefix>/pylibs, which the studio appends to sys.path.
#
# They are fetched rather than bundled on purpose: PyBoy is LGPL-3.0, and not
# redistributing it leaves Qt as the only LGPL component an installed studio
# ships. A wheel is a zip and both are published
# as binaries, so there is no build step and no need to carry pip.

PYPI_JSON = "https://pypi.org/pypi/{name}/{version}/json"

#: name -> version. PINNED: an unpinned fetch is not reproducible, and these
#: are the versions the preview backends are tested against. Dependencies are
#: DECLARED here, not resolved: PyBoy also wants numpy (which the application
#: already bundles) and pysdl2 (only for its SDL window - the studio runs
#: `PyBoy(window="null")`), and libretro.py wants typing_extensions below 3.13.
PYLIB_PINS = {
    "pyboy": "2.7.0",
    "libretro.py": "0.7.2",
    "typing_extensions": "4.15.0",
}

#: What each wheel unpacks as, for the installed check.
PYLIB_MODULES = {"pyboy": "pyboy", "libretro.py": "libretro",
                 "typing_extensions": "typing_extensions.py"}


def pylibs_dir():
    return os.path.join(PREFIX, "pylibs")


def wheel_tags():
    """(python tag, abi tag, [acceptable platform tags]) for this interpreter."""
    py = "cp%d%d" % sys.version_info[:2]
    machine = _platform.machine().lower()
    osname = host_os()
    if osname == "windows":
        plats = ["win_amd64"] if machine in ("amd64", "x86_64") else ["win32"]
    elif osname == "linux":
        arch = "aarch64" if machine in ("aarch64", "arm64") else "x86_64"
        plats = ["manylinux", arch]        # substring match, see pick_wheel
    else:
        arch = "arm64" if machine == "arm64" else "x86_64"
        plats = ["macosx", arch, "universal2"]
    return py, py, plats


def _plat_ok(plat, wanted):
    """Does a wheel's platform tag satisfy `wanted`?

    **The answer comes from `wanted`, never from the host.** `pick_wheel`
    takes its tags as an argument precisely so it can be asked about a
    platform other than the one running, and reading `host_os()` here broke
    that silently: on Linux a Windows-shaped `wanted` fell through to the
    manylinux arm, `"manylinux" in "win_amd64"` is false, and every
    win_amd64 wheel read as "wrong platform" - two checks that pass on
    Windows fail on Linux, for a function whose comment says the machine
    cannot matter. `wanted[0]` is the family, set by `wheel_tags()`.
    """
    if plat == "any":
        return True
    if not wanted:
        return False
    if wanted[0] == "manylinux":
        return "manylinux" in plat and wanted[1] in plat
    if wanted[0] == "macosx":
        return "macosx" in plat and (wanted[1] in plat or "universal2" in plat)
    return plat in wanted                      # windows: the exact tags


def pick_wheel(files, py_tag=None, plats=None):
    """Choose the wheel for this interpreter from a PyPI file listing.

    Pure, so it can be tested without the network. `files` is the PyPI JSON
    API's ``urls`` list. Returns the chosen entry, or None - and None must be
    a hard error at the call site rather than a fallback: a wheel for the
    wrong interpreter fails at import time, three screens away from here."""
    if py_tag is None:
        py_tag, _abi, plats = wheel_tags()
    best = None
    for f in files:
        if f.get("packagetype") != "bdist_wheel":
            continue
        name = f.get("filename", "")
        if not name.endswith(".whl"):
            continue
        parts = name[:-4].split("-")
        if len(parts) < 5:
            continue
        wheel_py, _abi, plat = parts[-3], parts[-2], parts[-1]
        if not _plat_ok(plat, plats):
            continue
        pys = wheel_py.split(".")
        if py_tag in pys:
            rank = 0                       # built for this exact interpreter
        elif any(p in ("py3", "py2.py3") for p in pys):
            rank = 1                       # pure python
        else:
            continue
        # A platform-specific wheel beats a universal one for the same rank.
        rank = rank * 2 + (1 if plat == "any" else 0)
        if best is None or rank < best[0]:
            best = (rank, f)
    return best[1] if best else None


def pylibs_installed():
    d = pylibs_dir()
    return all(os.path.exists(os.path.join(d, mod))
               for mod in PYLIB_MODULES.values())


def install_pylibs():
    """Download the pinned wheels and unpack them into <prefix>/pylibs."""
    import json

    target = pylibs_dir()
    os.makedirs(target, exist_ok=True)
    for name, version in PYLIB_PINS.items():
        url = PYPI_JSON.format(name=name, version=version)
        req = urllib.request.Request(url, headers={"User-Agent": "mosaik8-setup"})
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
            data = json.load(resp)
        chosen = pick_wheel(data.get("urls") or [])
        if chosen is None:
            raise RuntimeError(
                "no %s %s wheel for this interpreter (%s, %s). Install it with "
                "pip instead, or pin a version that publishes one."
                % (name, version, "cp%d%d" % sys.version_info[:2], host_os()))
        with tempfile.TemporaryDirectory() as tmp:
            whl = download(chosen["url"], os.path.join(tmp, chosen["filename"]),
                           expected_sha256=(chosen.get("digests") or {}).get("sha256"))
            with zipfile.ZipFile(whl) as zf:
                zf.extractall(target)
        log(f"  installed {name} {version} -> {target}")
    # A zip does not carry POSIX permissions, and a wheel may ship a script or
    # a loadable object that needs the executable bit.
    if os.name != "nt":
        _make_executable(target)


def _make_executable(root):
    """Restore +x on unpacked binaries (zipfile drops permissions)."""
    import stat
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if fn.endswith((".so", ".dylib")) or "." not in fn:
                p = os.path.join(dirpath, fn)
                try:
                    os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR |
                             stat.S_IXGRP | stat.S_IXOTH)
                except OSError:
                    pass


# --------------------------------------------------------------------------

COMPONENTS = {
    "gbdk":   (gbdk_installed, install_gbdk, "GBDK-2020 toolchain (gbdk/)"),
    "cc65":   (cc65_installed, install_cc65, "cc65 toolchain (cc65/)"),
    "cores":  (cores_installed, install_cores,
               "libretro cores: Lynx/PCE/SMS/GG/NES (emu/libretro/)"),
    "python": (python_packages_installed, install_python_packages,
               "Python packages via pip, for a DEV checkout "
               "(pyboy, libretro.py, pillow, toml)"),
    "pylibs": (pylibs_installed, install_pylibs,
               "emulator libraries as wheels (pyboy, libretro.py) -> pylibs/, "
               "for an installed studio with no pip"),
}

def component_note(name):
    """Why a component cannot be FETCHED on this machine. (fetchable, note).

    Only cc65 is affected today: upstream publishes a Windows snapshot only.
    On Linux and macOS the honest answer is the package manager, and a UI has
    to say so rather than offer a button that logs three lines and then
    reports success."""
    if name == "cc65" and host_os() != "windows":
        return False, ("no upstream binary for %s: install cc65 from your "
                       "package manager (apt install cc65 / brew install "
                       "cc65), or set CC65_HOME" % host_os())
    return True, ""


def default_components():
    """What a bare run installs.

    Everything EXCEPT the two overlapping Python paths, which are alternatives
    rather than a pair: `python` is pip into site-packages (a developer
    checkout), `pylibs` is wheels into the data root (an installed app with no
    pip, and what the studio's setup wizard runs). A function, not a constant,
    because `--prefix` is parsed AFTER this module is imported."""
    names = ["gbdk", "cc65", "cores",
             "pylibs" if PREFIX != ROOT else "python"]
    # A component we cannot fetch here is not a default: a bare run should not
    # print "[install] cc65" and then "Done." having installed nothing.
    return [n for n in names if component_note(n)[0]]


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
    ap.add_argument("--json", action="store_true",
                    help="with --check: machine-readable status, for the "
                         "studio's setup wizard (the human table is not a "
                         "parsing surface)")
    ap.add_argument("--prefix", metavar="DIR", default=None,
                    help="install into DIR instead of beside this file "
                         "(default: $MOSAIK8_HOME, else the checkout)")
    args = ap.parse_args()

    if args.prefix:
        set_prefix(args.prefix)
    if PREFIX != ROOT:
        # --json's stdout is PARSED, so nothing but the document goes on it.
        if not args.json:
            log(f"install prefix: {PREFIX}")
        os.makedirs(PREFIX, exist_ok=True)

    names = default_components()
    if args.only:
        names = [n.strip() for n in args.only.split(",") if n.strip()]
        unknown = [n for n in names if n not in COMPONENTS]
        if unknown:
            ap.error(f"unknown component(s): {', '.join(unknown)}")

    if args.check and args.json:
        import json as _json
        status = {}
        for name in names:
            installed, _install, label = COMPONENTS[name]
            try:
                present = bool(installed())
            except Exception as e:            # a probe must not crash the UI
                present = False
                label = f"{label} (check failed: {e})"
            fetchable, note = component_note(name)
            status[name] = {"installed": present, "label": label,
                            "fetchable": fetchable, "note": note}
        print(_json.dumps({"prefix": PREFIX, "components": status}))
        return 0

    failures = 0
    for name in names:
        installed, install, label = COMPONENTS[name]
        present = installed()
        fetchable, note = component_note(name)
        if args.check:
            log(f"[{'ok' if present else 'missing'}] {label}")
            if not present and not fetchable:
                log(f"  {note}")
            continue
        if not present and not fetchable:
            log(f"[skip] {label}")
            log(f"  {note}")
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
