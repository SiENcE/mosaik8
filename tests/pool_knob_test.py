#!/usr/bin/env python3
"""The VM8 runtime slot pools as a build knob (`[build] actor_pool`/`trigger_pool`).

`vm.actor`/`vm.entity`/`vm.canim`/`vm.clip` are indexed by ACTOR slot and
`vm.trigger` by TRIGGER slot, all fixed at 8. The reference engine's own limit is 20
actors, so a real import loses entities to the pool alone - two rooms of the
reference-engine sample already fit the hardware background and still drop actors
(`town/Music House`, 9) and triggers (`Player's House`, 11).

Growing the pools unconditionally would cost BSS in EVERY VM8 game including
the tight Lynx samples. Measured on the GB with both pools at 20 (a controlled
build diff of the reference-engine sample conversion, WRAM `_DATA` 1,395 -> 1,851 B): **+29 B
per actor slot** (vm.actor 14 + vm.entity 8 + vm.canim 7; +15 more when a
project links vm.clip, whose 4 function-pointer arrays cost 2 B each) and
**+9 B per trigger slot**, with resident `_CODE` unchanged at 12,773 B. So it
is a knob, defaulting to the historical 8.

The mechanism is an INTEGER compile-time define. `defines` already carried
build-derived BOOLEAN flags (the VM8 dispatch pruning, `vm_quant`), but an
array length needs a value, and `parser.parse_type` accepted only a NUMBER
literal - so a pool size could not be expressed at all without forking every
declaration behind a conditional. An integer define now resolves both as an
array LENGTH and as a bare expression atom (so `const ACTORS = VM_ACTOR_POOL`
tracks the same knob), folding at parse time so `ArrayType.size` stays an int
and the typechecker/codegen are untouched.

Contract pinned here:
  * the default is 8 and byte-identical (goldens + every existing project).
  * an integer define sizes an array and folds as a const initializer.
  * a BOOLEAN define must NOT size an array (bool is an int subclass in
    Python - a dispatch flag silently sizing a pool would be a memory bug).
  * an unknown array-length identifier is a clear error, never a default.
  * `[build] actor_pool`/`trigger_pool` validate their range.
  * `generate_rooms` refuses a room past EITHER configured pool, and both
    runtimes drop extras silently, so the refusal is the only guard.
  * `isa.ACTOR_POOL` follows the project, so a literal actor id past the
    pool is rejected at lowering.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik import MosaikCompiler
from mosaik.compiler import POOL_DEFINE_DEFAULTS
from mosaik.lexer import Lexer
from mosaik.parser import Parser

_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def _c(src, defines=None):
    return MosaikCompiler().compile_program([("t.mos", src)], platform="gameboy",
                                            defines=defines)


def _sizes(c, name):
    """Every declared length of array `name` in the generated C.

    A SINGLE-module program is not name-mangled, so the C symbol is the bare
    mosaik name; a multi-module one mangles to `<module>_<name>`. Matches a
    DECLARATION (`uint8_t slots[8];`) only - a bare `name[` would also match
    every indexing site (`slots[0] = ...`) and report the subscript as a size.
    """
    return re.findall(r"\b%s\[(\d+)\];" % re.escape(name), c)


def _const(c, name):
    """A mosaik `const` is emitted as a C #define: `#define NAME (value)`."""
    m = re.search(r"#define %s \((\d+)\)" % re.escape(name), c)
    return m.group(1) if m else None


def _err(src, defines=None):
    """compile_program REPORTS rather than raises: it returns the diagnostic
    as a string starting with 'Compilation error:'. Return it, or None when the
    program compiled."""
    out = _c(src, defines)
    return out if out.startswith("Compilation error:") else None


_SRC = """
module "main" {
    const POOL = VM_ACTOR_POOL
    var slots: array[u8, VM_ACTOR_POOL]
    var trig: array[u16, VM_TRIGGER_POOL]
    function main() {
        slots[0] = POOL
        trig[0] = 1
    }
}
"""


def test_defaults_are_the_shipped_sizes():
    print("\n[defaults]")
    check(POOL_DEFINE_DEFAULTS == {"VM_ACTOR_POOL": 8, "VM_TRIGGER_POOL": 8,
                                   "VM_ANIM_SLOTS": 8},
          "POOL_DEFINE_DEFAULTS is the historical 8s (+ the animator pool)")
    # No defines at all: the compiler must still supply them, or lib/vm would
    # not parse for the goldens / a bare compile_program.
    c = _c(_SRC)
    check(_sizes(c, "slots") == ["8"], "actor pool defaults to 8")
    check(_sizes(c, "trig") == ["8"], "trigger pool defaults to 8")
    check(_const(c, "POOL") == "8", "const folds to the default value")


def test_knob_sizes_arrays_and_consts():
    print("\n[knob]")
    c = _c(_SRC, defines={"VM_ACTOR_POOL": 20, "VM_TRIGGER_POOL": 12})
    check(_sizes(c, "slots") == ["20"], "actor pool sizes the array to 20")
    check(_sizes(c, "trig") == ["12"], "trigger pool sizes the array to 12")
    # `const POOL = VM_ACTOR_POOL` must track the knob, or a loop bound would
    # disagree with the array it walks.
    check(_const(c, "POOL") == "20",
          "a const initialised from the define tracks it (got %s)"
          % _const(c, "POOL"))
    # Only the caller's keys change; the other keeps its default.
    c2 = _c(_SRC, defines={"VM_ACTOR_POOL": 20})
    check(_sizes(c2, "trig") == ["8"], "an unset pool keeps its default")


def test_boolean_define_must_not_size_an_array():
    print("\n[bool is not a size]")
    src = 'module "main" {\n var a: array[u8, FLAG]\n function main() { a[0] = 1 }\n}\n'
    # bool is an int subclass in Python: a dispatch flag reaching the array-size
    # path would silently produce array[u8, 1] / array[u8, 0].
    for val in (True, False):
        err = _err(src, defines={"FLAG": val})
        check(err is not None and "FLAG" in err,
              "a %r define is refused as an array length" % val)
    # ... and it still resolves as a CONDITION, which is what it is for.
    cond = ('module "main" {\n if FLAG {\n  var a: array[u8, 4]\n'
            ' } else {\n  var a: array[u8, 9]\n }\n function main() { a[0] = 1 }\n}\n')
    c = _c(cond, defines={"FLAG": True})
    check(_sizes(c, "a") == ["4"], "a boolean define still folds a condition")


def test_unknown_length_is_a_clear_error():
    print("\n[unknown identifier]")
    src = 'module "main" {\n var a: array[u8, NOPE]\n function main() { a[0] = 1 }\n}\n'
    err = _err(src)
    check(err is not None and "NOPE" in err,
          "an unknown array length names the identifier (%s)"
          % ((err or "compiled").splitlines()[0][:70]))


def test_parser_folds_only_integer_defines():
    print("\n[parser fold]")
    p = Parser(Lexer("module \"m\" { }").tokenize(), defines={"N": 5, "B": True})
    check(p.int_define("N") == 5, "int_define returns an integer define")
    check(p.int_define("B") is None, "int_define rejects a boolean")
    check(p.int_define("MISSING") is None, "int_define rejects an unknown name")


def test_lib_vm_parses_and_is_unchanged_by_default():
    print("\n[lib/vm]")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "lib", "vm", "actor.mos"), encoding="utf-8").read()
    check("VM_ACTOR_POOL" in src, "lib/vm/actor.mos names the define")

    # `vm.actor` imports engine.camera etc, so compiling it alone would need the
    # whole closure. Parse it instead: the AST carries the resolved array sizes,
    # which is exactly the contract (a size resolves at PARSE time to an int).
    def pool_widths(path, defines):
        p = Parser(Lexer(open(path, encoding="utf-8").read()).tokenize(),
                   defines=defines)
        out = {}
        for mod in p.parse().modules:
            for d in mod.declarations:
                t = getattr(d, "type", None)
                if t is not None and hasattr(t, "size") and hasattr(t, "element_type"):
                    out[d.name] = t.size
        return out

    lib = os.path.join(root, "lib", "vm")
    # Arrays in these modules that are NOT indexed by a pool slot, by name and
    # fixed width. An entry here is a conscious decision: anything else with a
    # width other than the pool's is a slot array that missed the knob.
    #   p_mskc: the PLAYER's per-frame-index sparse-mask cache (2026-09-05),
    #           sixteen clip frames, one player - not a pool.
    #   own:    `[build] oam_on_wake`'s owner byte per OAM OBJECT (2026-10-09),
    #           the largest GBDK sprite table (64) - indexed by object, not slot.
    NON_POOL = {"vm.canim": {"p_mskc": 16}, "vm.actor": {"own": 64}}
    for fname, define, pack in (("actor.mos", "VM_ACTOR_POOL", "vm.actor"),
                                ("entity.mos", "VM_ACTOR_POOL", "vm.entity"),
                                ("canim.mos", "VM_ACTOR_POOL", "vm.canim"),
                                # vm.clip is sized by the ANIMATOR pool, not
                                # the actor pool: a game binds a clip slot per
                                # pool actor AND one for the player, so the
                                # actor pool is one slot short (vm-clipdemo at
                                # actor_pool = 1 wrote past every c_* array).
                                ("clip.mos", "VM_ANIM_SLOTS", "vm.clip"),
                                ("trigger.mos", "VM_TRIGGER_POOL", "vm.trigger")):
        path = os.path.join(lib, fname)
        fixed = NON_POOL.get(pack, {})
        base = pool_widths(path, dict(POOL_DEFINE_DEFAULTS))
        wide = pool_widths(path, dict(POOL_DEFINE_DEFAULTS, **{define: 20}))
        check(all(base.get(k) == w and wide.get(k) == w for k, w in fixed.items()),
              "%s non-pool arrays keep their fixed width (%s)"
              % (pack, ", ".join("%s=%d" % kv for kv in sorted(fixed.items())) or "none"))
        base = {k: v for k, v in base.items() if k not in fixed}
        wide = {k: v for k, v in wide.items() if k not in fixed}
        check(base and set(base.values()) == {8},
              "%s pool arrays default to 8 (%d arrays)" % (pack, len(base)))
        # EVERY slot-indexed array must move together - a split would let a
        # loop bounded on one walk off the end of another.
        check(wide and set(wide.values()) == {20},
              "%s pool arrays ALL follow the knob to 20" % pack)
        check(set(base) == set(wide),
              "%s declares the same arrays either way" % pack)


def test_build_config_validates_the_knob():
    print("\n[mosaik.toml]")
    from mosaik8_build import BuildConfig
    cfg = BuildConfig.__new__(BuildConfig)
    cfg.config = {"build": {}}
    check(cfg.get_pool_defines() == {}, "unset passes nothing (byte-identical)")
    cfg.config = {"build": {"actor_pool": 20, "trigger_pool": 12}}
    # engine.anim is indexed BY ACTOR SLOT, so raising the actor pool must
    # raise the animator pool with it -- actors 8.. wrote past every array in
    # engine.anim before this was derived (MAXA was a flat 8).
    check(cfg.get_pool_defines() == {"VM_ACTOR_POOL": 20, "VM_TRIGGER_POOL": 12,
                                     "VM_ANIM_SLOTS": 20},
          "set values map to the defines, and the animator pool follows")
    cfg.config = {"build": {"actor_pool": 4}}
    check(cfg.get_pool_defines().get("VM_ANIM_SLOTS") == 8,
          "a SMALL actor pool never shrinks engine.anim below its shipped 8")
    # ...and vm.clip rides the SAME define, so a small pool cannot shrink the
    # clip slots either. It used to read VM_ACTOR_POOL: at actor_pool = 1 the
    # c_* arrays were one entry long and vm-clipdemo's `bind(1, ...)` (slot 0 =
    # player, slot 1 = its one actor) wrote past all eleven of them - the
    # player silently stopped animating on the GB, the Lynx booted black.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    clip = open(os.path.join(root, "lib", "vm", "clip.mos"), encoding="utf-8").read()
    check("VM_ACTOR_POOL" not in clip.split("const MAXC")[1],
          "vm.clip never sizes a slot array by the ACTOR pool")
    for bad in (0, 256, "x"):
        cfg.config = {"build": {"actor_pool": bad}}
        try:
            cfg.get_pool_defines()
            ok = False
        except ValueError:
            ok = True
        check(ok, "actor_pool %r is refused" % (bad,))
    check("actor_pool" in BuildConfig.APPLIED_KEYS["build"]
          and "trigger_pool" in BuildConfig.APPLIED_KEYS["build"],
          "both keys are APPLIED (not warned as unknown)")


def test_actor_id_validation_follows_the_pool():
    print("\n[actor id lowering]")
    from mosaik_vm import isa
    from mosaik_vm.events import lower_event
    prev = isa.ACTOR_POOL
    try:
        isa.set_actor_pool(8)
        try:
            lower_event({"event": "actor_deactivate", "actor": 9}, None)
            ok = False
        except Exception as e:
            ok = "out of range" in str(e)
        check(ok, "actor 9 is refused at the default pool of 8")
        isa.set_actor_pool(20)
        try:
            lower_event({"event": "actor_deactivate", "actor": 9}, None)
            ok = True
        except Exception as e:
            ok = "out of range" not in str(e)
        check(ok, "actor 9 is accepted once the pool is 20")
        check(isa.set_actor_pool(999) == isa.SELF_ACTOR - 1,
              "the pool is capped below the SELF sentinel")
    finally:
        isa.set_actor_pool(prev)


def test_generate_rooms_refuses_past_either_pool():
    print("\n[generate_rooms refusal]")
    from mosaik_vm.rooms import DEFAULT_ACTOR_POOL, DEFAULT_TRIGGER_POOL
    check(DEFAULT_ACTOR_POOL == 8 and DEFAULT_TRIGGER_POOL == 8,
          "rooms defaults match the runtime")
    import inspect
    from mosaik_vm import rooms
    src = inspect.getsource(rooms.generate_rooms)
    check("load_pool_config" in src, "generate_rooms reads the configured pools")
    check("trigger_pool" in src, "the refusal names the trigger knob too")


if __name__ == "__main__":
    print("=" * 60)
    print("VM8 slot pools as a build knob")
    print("=" * 60)
    test_defaults_are_the_shipped_sizes()
    test_knob_sizes_arrays_and_consts()
    test_boolean_define_must_not_size_an_array()
    test_unknown_length_is_a_clear_error()
    test_parser_folds_only_integer_defines()
    test_lib_vm_parses_and_is_unchanged_by_default()
    test_build_config_validates_the_knob()
    test_actor_id_validation_follows_the_pool()
    test_generate_rooms_refuses_past_either_pool()
    print("\n" + "=" * 60)
    if _FAILED:
        print("FAILED (%d):" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        sys.exit(1)
    print("All pool-knob checks passed")
