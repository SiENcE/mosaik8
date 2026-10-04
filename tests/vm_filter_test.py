"""Per-console SCRIPT filtering (review 2.1 / P1 #5, Stage 3): a `[[script]]` with a
`platforms` allow-list forks the scripts module into `if platform` guards, dropping
the excluded scripts' bytes per target (a STOP/RET stub keeps ENTRY_* + exports
uniform). Untagged programs are byte-identical.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mosaik_vm as m  # noqa: E402
from mosaik import MosaikCompiler  # noqa: E402

_FAILS = []


def check(label, cond):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        _FAILS.append(label)
    return cond


_SCRIPTS = [
    {"name": "main", "events": [
        {"event": "start_thread", "script": "greet"},
        {"event": "start_thread", "script": "gb_only"}]},
    {"name": "greet", "events": [{"event": "text", "string": "hi"}]},
    {"name": "gb_only", "events": [{"event": "text", "string": "GBEXCL"}],
     "platforms": ["gameboy"]},
]


def main():
    print("VM8 per-console script filtering (Stage 3)")
    print("=" * 50)

    # Untagged -> byte-identical to the plain single-bucket emission.
    plain = [{k: v for k, v in s.items() if k != "platforms"} for s in _SCRIPTS]
    check("an untagged program is byte-identical (guard-free)",
          m.emit_scripts_module(plain)
          == m.Compiler().compile(plain).to_scripts_mos())

    src = m.emit_scripts_module(_SCRIPTS)
    check("a `platforms` tag forks scripts.mos into an if-platform guard",
          'if platform == "gameboy" {' in src and "\n    } else {\n" in src)
    gb, els = src.split("\n    } else {\n", 1)   # split on the GUARD else (4-space)
    check("the gb-only script's text is in the gameboy branch, dropped from the else",
          "GBEXCL" in gb and "GBEXCL" not in els)
    check("every ENTRY_* stays defined in BOTH branches (uniform export)",
          src.count("const ENTRY_gb_only") == 2
          and "ENTRY_gb_only" in src.split("export", 1)[1])

    import re
    sizes = [int(x) for x in re.findall(r"const CODE: array\[u8, (\d+)\]", src)]
    check("the else branch's CODE blob is smaller (dropped the gb-only body)",
          len(sizes) == 2 and min(sizes) < max(sizes))

    # render_text reads the open box's text origin from vm.core (core sizes the
    # box from the string's line count), so a standalone compile needs the seam
    # stubbed -- a real build links lib/vm/core.mos.
    core_stub = ('module "vm.core" {\n'
                 '    function box_top() -> u8 { return SCREEN_ROWS - 3 }\n'
                 '    function box_left() -> u8 { return 2 }\n'
                 '    function var_get(idx: u8) -> i16 { return 0 }\n'
                 '    function set_code_window(base: addr, enter: function() -> u8, leave: function(u8)) { }\n'
                 '    function set_code_window_res(base: addr) { }\n'
                 '    export box_top, box_left, var_get, set_code_window, set_code_window_res\n}\n')
    for platform in ("gameboy", "sms", "lynx", "nes", "pce", "gamegear"):
        out = MosaikCompiler().compile_program(
            [("scripts.mos", src), ("core.mos", core_stub)], platform=platform)
        check("[%s] the forked scripts module compiles" % platform,
              not out.startswith("Compilation error:"))

    # Runtime: the else BUCKET runs cleanly -- `main` spawns the excluded script
    # (a STOP stub there) then stops; every thread terminates, nothing hangs.
    rt = [
        {"name": "main", "events": [{"event": "start_thread", "script": "opt"},
                                    {"event": "stop"}]},
        {"name": "opt", "events": [{"event": "set_var", "var": "x", "value": 1},
                                   {"event": "stop"}], "platforms": ["gameboy"]},
    ]
    from mosaik_scenes import _platform_buckets
    buckets = _platform_buckets([[m._script_platforms(s) for s in rt]])
    else_keep = buckets[-1][1][0]                       # the default (else) bucket
    bucket = [s if i in else_keep else m._stub_script(s)
              for i, s in enumerate(rt)]
    prog = m.Compiler().compile(bucket)
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(8)
    check("the stubbed script runs as a clean no-op (no hang; threads settle)",
          vm.any_active_threads() == 0)

    # A `sub` script stubs to RET (returns) not STOP, so a caller keeps running.
    stub_sub = m._stub_script({"name": "helper", "events": [{"event": "wait",
                              "frames": 99}], "sub": True})
    check("a `sub` script stubs to an empty RET body (caller-safe)",
          stub_sub.get("sub") is True and stub_sub["events"] == [])

    print("=" * 50)
    ok = not _FAILS
    print("All VM8 script-filter checks passed" if ok else
          "SOME CHECKS FAILED: " + ", ".join(_FAILS))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
