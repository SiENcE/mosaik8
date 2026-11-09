#!/usr/bin/env python3
"""The 2026-11-03 engine review's BATCH A: toolchain correctness (plan
`docs/engine-review-open-items-plan.md` in the studio, 2026-11-07).

  #22  out-of-range event operands were MASKED at encode (`Instr.encode`):
       `fade_out frames=300` faded for 44 frames, `actor_move_to x=70000` went
       to 4464. Now every u8 operand is -128..255 and every u16 -32768..65535
       or the compile fails, naming the script; an expression's literal past
       65535, an `actor_x(n)` past the actor pool, a `rand()` bound past 65535
       and a one-argument `atan2` are refused; timer / music-routine slots are
       capped at their array (a `timer = 9` was a silent no-op on console).
  #25  the CLI (`python -m mosaik_vm`) and the scaffold wrote `compile_path`'s
       single program, ignoring `[[script]] platforms`: a Lynx-only script
       shipped on every console. Both go through `emit_scripts_module` now
       (`loader.scripts_module_path`), as the studio's Save always did.
  #21  the `.v8s` assembler truncated a SWITCH count past 255.
  #23  ...and enforced almost none of spec §15's caps: an RPN stream deeper
       than the stack, THREADN n > NARGS, RPN ARG >= NARGS, operand widths
       (masked) and the heap cells of DATA_PEEK / TBL_GET / A_GET_DIR.
  #24  ...and its RPN width list missed ACTOR_MOVING's actor byte, so a
       listing did not re-assemble (`PUSH 1 ACTOR_MOVING 2 ADD` ate the ADD).
  LOW  `_is_expr(None)` read an ABSENT field as an expression, so a defaulted
       literal selected the stack-argument `_E` form.

Measured 2026-11-07: every one of the 57 local event projects compiles to
the same scripts module before and after (blob-neutral). Mutation: run
against the pre-batch toolchain, every check here but the controls fails.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik_vm as m  # noqa: E402

FAILS = []


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


def _compile(events, name="main"):
    return m.Compiler().compile([{"name": name, "events": events + [{"event": "stop"}]},
                                 {"name": "t", "events": [{"event": "stop"}]}])


def _refused(events, needle="", name="main"):
    try:
        _compile(events, name)
        return False, "accepted"
    except m.VmError as e:
        return (needle in str(e)), str(e)
    except Exception as e:                       # noqa: BLE001 - a crash is not a refusal
        return False, "%s: %s" % (type(e).__name__, e)


def operands():
    print("\n[#22: event operands are validated, not masked]")
    for label, ev, needle in (
            ("fade_out frames=300", {"event": "fade_out", "frames": 300}, "FADE"),
            ("shake frames=300", {"event": "shake", "frames": 300, "amp": 2}, "SHAKE"),
            ("actor_move_to x=70000", {"event": "actor_move_to", "actor": 0, "x": 70000,
                                       "y": 0}, "A_MOVE_TO"),
            ("timer_set timer=9", {"event": "timer_set", "timer": 9, "period": 10,
                                   "script": "t"}, "not a slot"),
            ("timer_stop timer=4", {"event": "timer_stop", "timer": 4}, "not a slot"),
            ("music_routine slot=7", {"event": "music_routine", "slot": 7,
                                      "script": "t"}, "not a slot")):
        ok, why = _refused([ev], needle)
        check("%s is refused" % label, ok, why)
    ok, why = _refused([{"event": "fade_out", "frames": 300}], "script 'intro'", "intro")
    check("an encode-time refusal names its SCRIPT (the studio's status line shows it)",
          ok, why)
    try:
        prog = _compile([{"event": "fade_out", "frames": 255},
                         {"event": "timer_set", "timer": 3, "period": 10, "script": "t"},
                         {"event": "music_routine", "script": "t"},
                         {"event": "shake", "frames": 255, "amp": 2}])
        check("the in-range values still compile (control)", len(prog.code) > 0)
    except Exception as e:                       # noqa: BLE001
        check("the in-range values still compile (control)", False, e)


def expressions():
    print("\n[#22: expressions]")
    for label, expr, needle in (
            ("a literal past 65535", "70000 + 1", "16 bits"),
            ("actor_x(300) (it read actor 44)", "actor_x(300)", "actor out of range"),
            ("actor_y(8), one past the default pool", "actor_y(8)", "actor out of range"),
            ("rand(70000)", "rand(70000)", "bound"),
            ("atan2 with one argument", "atan2(1)", "argument")):
        ok, why = _refused([{"event": "set_var", "var": "v", "expr": expr}], needle)
        check("%s is refused" % label, ok, why)
    try:
        _compile([{"event": "set_var", "var": "v",
                   "expr": "actor_x(7) + rand(65535) + atan2(1, 2) + 65535"}])
        check("the limits themselves compile (control)", True)
    except Exception as e:                       # noqa: BLE001
        check("the limits themselves compile (control)", False, e)


def absent_fields():
    print("\n[LOW: an absent field is a literal, not an expression]")
    check("_is_expr(None) is False", m.events._is_expr(None) is False)
    cs = bytes(_compile([{"event": "change_scene", "room": 1}]).code)
    check("change_scene without x / y keeps the compact RAISE (room 1 at 0, 0)",
          cs.startswith(bytes([m.OPS["RAISE"][0], 2, 1, 0, 0, 0, 0])), cs.hex())
    pj = bytes(_compile([{"event": "projectile", "x": 10, "y": 20, "vy": -3}]).code)
    check("a projectile without vx keeps the compact PROJ_LAUNCH (vx 0)",
          pj.startswith(bytes([m.OPS["PROJ_LAUNCH"][0], 10, 0, 20, 0, 0, 0xFD])), pj.hex())


def cli_platforms():
    print("\n[#25: the CLI and the scaffold fork per console]")
    src = os.path.join(ROOT, "projects", "vm-percon")
    tmp = tempfile.mkdtemp(prefix="batch_a_")
    try:
        root = os.path.join(tmp, "p")
        shutil.copytree(src, root, ignore=shutil.ignore_patterns("build"))
        out = os.path.join(tmp, "scripts.mos")
        r = subprocess.run([sys.executable, "-m", "mosaik_vm",
                            os.path.join(root, "scripts"), "-o", out],
                           capture_output=True, text=True, cwd=ROOT)
        text = open(out, encoding="utf-8").read() if os.path.exists(out) else ""
        check("the CLI ran", r.returncode == 0, (r.stdout + r.stderr)[-400:])
        check("the CLI's scripts.mos FORKS per console (vm-percon has a Lynx-only "
              "script)", 'if platform == "lynx" {' in text
              and "Forked per console" in text, text[:300])
        want = m.emit_scripts_module(
            m.load_scripts(os.path.join(root, "scripts")),
            vwf=m.project_uses_vwf(root), font_map=m.project_font_map(root),
            tables=m.load_tables(os.path.join(root, "scripts")))
        check("...and is exactly what the studio's Save writes (emit_scripts_module)",
              text.replace("\r\n", "\n") == want.replace("\r\n", "\n"))
        committed = open(os.path.join(src, "src", "scripts.mos"),
                         encoding="utf-8").read().replace("\r\n", "\n")
        check("vm-percon's committed scripts.mos is that forked module",
              committed == want.replace("\r\n", "\n"))
        new = os.path.join(tmp, "new")
        m.scaffold_project(new, "fresh")
        scaf = open(os.path.join(new, "src", "scripts.mos"), encoding="utf-8").read()
        via = getattr(m, "scripts_module_path", None)
        check("the scaffold writes through the same path",
              via is not None and scaf == via(os.path.join(new, "scripts")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _asm(src):
    try:
        return m.assemble_v8s(src), ""
    except m.VmError as e:
        return None, str(e)


def assembler():
    print("\n[#21 / #23 / #24: the .v8s assembler]")
    for label, src, needle in (
            ("#21 SWITCH with 256 cases", "main:\n  SWITCH 256" + ", 0, main" * 256
             + "\n  STOP\n", "at most 255"),
            ("#23 THREADN passing 5 arguments", "main:\n  THREADN main, 5\n  STOP\n",
             "THREADN"),
            ("#23 RPN ARG 4", "main:\n  RPN ARG 4\n  STOP\n", "ARG"),
            ("#23 an RPN stream 9 cells deep", "main:\n  RPN " + "PUSH 1 " * 9
             + "ADD " * 8 + "\n  STOP\n", "stack cells"),
            ("#23 WAIT 300 (a masked width)", "main:\n  WAIT 300\n  STOP\n", "does not fit"),
            ("#23 RPN PUSH 70000", "main:\n  RPN PUSH 70000\n  STOP\n", "does not fit"),
            ("H4 DATA_PEEK into heap cell 200", "main:\n  DATA_PEEK 0, 200\n  STOP\n",
             "heap cell 200"),
            ("TBL_GET into heap cell 200", "main:\n  RPN PUSH 0\n  TBL_GET 0, 0, 200\n"
             "  STOP\n", "heap cell 200"),
            ("A_GET_DIR into heap cell 200", "main:\n  A_GET_DIR 0, 200\n  STOP\n",
             "heap cell 200"),
            ("H5 INPUT_ATTACH on button 8", "main:\n  INPUT_ATTACH 8, main\n  STOP\n",
             "buttons are 0..7"),
            ("H5 ...and with the override flag (136)",
             "main:\n  INPUT_ATTACH 136, main\n  STOP\n", "buttons are 0..7")):
        prog, why = _asm(src)
        check("%s is refused" % label, prog is None and needle in why, why or "accepted")
    prog, why = _asm("main:\n  RPN PUSH 1 ACTOR_MOVING 2 ADD\n  STOP\n")
    code = bytes(prog.code) if prog else b""
    check("#24 ACTOR_MOVING takes its actor byte (the ADD is not eaten as the id)",
          code[:9] == bytes([m.OPS["RPN"][0], m.RPN["PUSH"], 1, 0,
                             m.RPN["ACTOR_MOVING"], 2, m.RPN["ADD"], m.RPN["END"],
                             m.OPS["STOP"][0]]), code.hex() or why)
    # The listing round trip: an event program's .vms re-assembles to the same
    # bytes (ACTOR_MOVING was the token that broke it).
    ev = m.Compiler().compile([{"name": "main", "events": [
        {"event": "set_var", "var": "v", "expr": "actor_moving(3) + 1"},
        {"event": "stop"}]}])
    back, why = _asm(ev.to_vms())
    check("#24 an event program's listing re-assembles to the same blob",
          back is not None and bytes(back.code) == bytes(ev.code), why)
    prog, why = _asm("main:\n  THREADN main, 4\n  RPN " + "PUSH 1 " * 8 + "ADD " * 7
                     + "\n  WAIT 255\n  INPUT_ATTACH 135, main\n  STOP\n")
    check("the limits themselves assemble (control)", prog is not None, why)


def main():
    print("=== review_batch_a_test ===")
    operands()
    expressions()
    absent_fields()
    cli_platforms()
    assembler()
    print("\n%s" % ("ALL PASS" if not FAILS else "%d FAILED" % len(FAILS)))
    for f in FAILS:
        print("  - " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
