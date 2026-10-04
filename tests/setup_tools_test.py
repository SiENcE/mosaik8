#!/usr/bin/env python3
"""setup_tools: the install prefix and the wheel picker.

Two things here are worth a test and both are offline.

`set_prefix` exists because an INSTALLED studio cannot fetch toolchains into
its own tree (Program Files, /usr/lib, a signed .app). Every installer in the
module reads the three directory globals, so rebinding them is the whole
mechanism, and `mosaik8_build.tool_prefix()` reads the same `$MOSAIK8_HOME` on
the build side.

`pick_wheel` chooses the PyBoy / libretro.py wheel for this interpreter when
there is no pip to do it (a frozen application). It is deliberately a PURE
function over the PyPI file listing so it can be tested without the network:
picking wrong means an ImportError three screens away from the cause.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import setup_tools as st  # noqa: E402

failures = []


def check(cond, label):
    print(("  [PASS] " if cond else "  [FAIL] ") + label)
    if not cond:
        failures.append(label)


def test_prefix():
    print("\nprefix: where fetched toolchains go")
    original = st.PREFIX
    try:
        with tempfile.TemporaryDirectory() as tmp:
            st.set_prefix(tmp)
            tmp_abs = os.path.abspath(tmp)
            check(st.PREFIX == tmp_abs, "set_prefix rebinds PREFIX")
            check(st.GBDK_DIR == os.path.join(tmp_abs, "gbdk"),
                  "...and GBDK_DIR follows it")
            check(st.CC65_DIR == os.path.join(tmp_abs, "cc65"),
                  "...and CC65_DIR follows it")
            check(st.LIBRETRO_DIR == os.path.join(tmp_abs, "emu", "libretro"),
                  "...and the core directory follows it")
            check(st.pylibs_dir() == os.path.join(tmp_abs, "pylibs"),
                  "...and so does pylibs/")
            # The two Python paths are ALTERNATIVES: pip into site-packages for
            # a checkout, wheels into the prefix for an installed app. A bare
            # run must not do both.
            check("pylibs" in st.default_components()
                  and "python" not in st.default_components(),
                  "a prefixed run defaults to the WHEEL path, not pip")
        st.set_prefix(ROOT)
        check("python" in st.default_components()
              and "pylibs" not in st.default_components(),
              "a checkout defaults to pip, not wheels")
    finally:
        st.set_prefix(original)


def _whl(name, packagetype="bdist_wheel"):
    return {"packagetype": packagetype, "filename": name}


def test_pick_wheel():
    print("\npick_wheel: the right wheel for THIS interpreter")
    py = "cp%d%d" % sys.version_info[:2]

    # Windows-shaped listing, whatever host runs the test: the tags are passed
    # in, so this does not depend on the machine.
    files = [
        _whl("pyboy-2.7.0.tar.gz", "sdist"),
        _whl("pyboy-2.7.0-cp39-cp39-win_amd64.whl"),
        _whl("pyboy-2.7.0-%s-%s-win_amd64.whl" % (py, py)),
        _whl("pyboy-2.7.0-py3-none-any.whl"),
    ]
    got = st.pick_wheel(files, py_tag=py, plats=["win_amd64"])
    check(got is not None and got["filename"].endswith("%s-win_amd64.whl" % py),
          "an exact interpreter+platform wheel wins")

    check(st.pick_wheel([_whl("x-1.0-py3-none-any.whl")], py_tag=py,
                        plats=["win_amd64"]) is not None,
          "a pure-python wheel is accepted")

    check(st.pick_wheel([_whl("x-1.0-cp39-cp39-win_amd64.whl")], py_tag=py,
                        plats=["win_amd64"]) is None,
          "a wheel for another Python is REFUSED, not fallen back to")

    check(st.pick_wheel([_whl("x-1.0-%s-%s-macosx_11_0_arm64.whl" % (py, py))],
                        py_tag=py, plats=["win_amd64"]) is None,
          "a wheel for another platform is refused")

    check(st.pick_wheel([_whl("x-1.0.tar.gz", "sdist")], py_tag=py,
                        plats=["win_amd64"]) is None,
          "an sdist is not a wheel (there is no compiler to build it)")

    # Ordering must not decide the answer.
    a = st.pick_wheel([_whl("x-1.0-py3-none-any.whl"),
                       _whl("x-1.0-%s-%s-win_amd64.whl" % (py, py))],
                      py_tag=py, plats=["win_amd64"])
    b = st.pick_wheel([_whl("x-1.0-%s-%s-win_amd64.whl" % (py, py)),
                       _whl("x-1.0-py3-none-any.whl")],
                      py_tag=py, plats=["win_amd64"])
    check(a is not None and b is not None
          and a["filename"] == b["filename"] == "x-1.0-%s-%s-win_amd64.whl"
          % (py, py),
          "the choice does not depend on the listing order")

    # ...nor on the HOST. The two checks above are Windows-shaped and used to
    # fail on Linux, because the platform predicate read host_os() instead of
    # the tags it was handed - a function whose whole point is to be pure.
    # So ask it about all three families from whatever machine runs this.
    for family, plats, want in (
            ("windows", ["win_amd64"], "win_amd64"),
            ("linux", ["manylinux", "x86_64"], "manylinux_2_17_x86_64"),
            ("macos", ["macosx", "arm64", "universal2"], "macosx_11_0_arm64")):
        listing = [_whl("x-1.0-py3-none-any.whl"),
                   _whl("x-1.0-%s-%s-%s.whl" % (py, py, want))]
        got = st.pick_wheel(listing, py_tag=py, plats=plats)
        check(got is not None and got["filename"].endswith("%s.whl" % want),
              "a %s-shaped listing resolves on any host" % family)
        # ...and the other families' tags are still refused under it.
        other = "win_amd64" if family != "windows" else "manylinux_2_17_x86_64"
        check(st.pick_wheel([_whl("x-1.0-%s-%s-%s.whl" % (py, py, other))],
                            py_tag=py, plats=plats) is None,
              "a %s-shaped listing refuses %s" % (family, other))


def test_pins():
    print("\npins: what an installed studio downloads")
    check(set(st.PYLIB_PINS) == {"pyboy", "libretro.py", "typing_extensions"},
          "the pinned set is the preview libraries plus libretro.py's own "
          "dependency")
    check(all(v and v[0].isdigit() for v in st.PYLIB_PINS.values()),
          "every entry is a concrete version, never a range")
    check(st.PYPI_JSON.startswith("https://"),
          "the metadata query is HTTPS (download() refuses anything else)")


def test_buildbot_platform():
    """The libretro buildbot's path segment is not host_os().

    MEASURED against the server 2026-09-10, because both differences are
    invisible until a download 404s: macOS lives under `apple/osx`, and 64-bit
    ARM Linux is `aarch64` (`arm64` and `armv8` are both 404). All six cores
    exist for every combination below."""
    print("\nbuildbot: the {platform} segment")
    real_system, real_machine = st._platform.system, st._platform.machine

    def pretend(system, machine):
        st._platform.system = lambda: system
        st._platform.machine = lambda: machine

    try:
        pretend("Linux", "x86_64")
        check(st.buildbot_platform() == "linux/x86_64", "linux x86_64")
        pretend("Linux", "aarch64")
        check(st.buildbot_platform() == "linux/aarch64",
              "linux arm64 is spelled aarch64")
        pretend("Darwin", "x86_64")
        check(st.buildbot_platform() == "apple/osx/x86_64",
              "macOS is apple/osx, not mac")
        pretend("Darwin", "arm64")
        check(st.buildbot_platform() == "apple/osx/arm64", "apple silicon")
        pretend("Windows", "AMD64")
        check(st.buildbot_platform() == "windows/x86_64", "windows x86_64")
        pretend("Windows", "ARM64")
        try:
            st.buildbot_platform()
            check(False, "ARM Windows is refused rather than 404-downloaded")
        except RuntimeError:
            check(True, "ARM Windows is refused rather than 404-downloaded")
    finally:
        st._platform.system, st._platform.machine = real_system, real_machine

    check("{platform}" in st.BUILDBOT and "x86_64" not in st.BUILDBOT,
          "the URL takes the whole os/arch segment, not a hardcoded arch")


def test_toolchain_probe_follows_the_build():
    """"Installed" has to mean what mosaik8_build means by it.

    The build looks in $<TOOL>_HOME, then a local tree, then PATH. A probe that
    only knew about the tree we fetch reported "missing" on every machine that
    installed the toolchain from a package manager - so the studio's setup
    wizard was permanently wrong about a box where builds work."""
    print("\nprobe: a system toolchain counts as installed")
    exe = "lcc.exe" if os.name == "nt" else "lcc"
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "bin"))
        open(os.path.join(tmp, "bin", exe), "w").close()
        before = os.environ.get("GBDK_HOME")
        os.environ["GBDK_HOME"] = tmp
        try:
            check(st.gbdk_installed(), "$GBDK_HOME counts")
        finally:
            if before is None:
                del os.environ["GBDK_HOME"]
            else:
                os.environ["GBDK_HOME"] = before


def test_component_notes():
    """A component we cannot FETCH here must say so, not offer a button."""
    print("\nnotes: what this machine cannot download")
    fetchable, note = st.component_note("cc65")
    if st.host_os() == "windows":
        check(fetchable and not note, "cc65 is fetchable on Windows")
    else:
        check(not fetchable and "package manager" in note,
              "cc65 points at the package manager off Windows")
        check("cc65" not in st.default_components(),
              "a bare run does not pretend to install it")
    check(st.component_note("gbdk") == (True, ""),
          "GBDK has a binary release everywhere")



def main():
    print("=" * 60)
    print("setup_tools: prefix + wheel selection")
    print("=" * 60)
    test_prefix()
    test_pick_wheel()
    test_pins()
    test_buildbot_platform()
    test_toolchain_probe_follows_the_build()
    test_component_notes()
    print("\n" + "=" * 60)
    if failures:
        print("FAILED: %d" % len(failures))
        for f in failures:
            print("  - " + f)
        return 1
    print("All setup_tools checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
