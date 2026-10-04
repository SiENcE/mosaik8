"""mosaik_vm.glue - src/glue.mos generation (sound always, music driver when songs exist)."""
import os

try:
    import toml
except ImportError:  # pragma: no cover
    toml = None

from .instruments import load_instrument_defs, has_subpatterns
from .songs import load_song_defs


# --------------------------------------------------------------------------
# The AUTO-WIRE glue module: a studio-GENERATED `glue.setup()` the fixed shell calls
# once, wiring exactly the subsystems the project uses (SOUND always; the MUSIC driver
# + per-song voicing + custom waves when the project has songs). So adding a song in the
# tracker auto-wires music -- no hand-editing of main.mos. Regenerated from project
# content, like songs.mos / instruments.mos.
# --------------------------------------------------------------------------
# The audio-console GROUPS the glue wires PER-CONSOLE (mirrors the studio's audio_caps
# groups). Each maps to the platform strings conditional-compilation branches on; a group
# whose chosen driver is NOT ready (hUGEDriver today) wires NOTHING on those platforms
# (NOT a vm.music fallback -- the user's rule). PCE/NES have no music driver at all.
_GLUE_GROUP_PLATFORMS = {
    "gb": ["gameboy", "gameboy_color", "analogue_pocket", "megaduck"],
    "lynx": ["lynx"],
    "smsgg": ["sms", "gamegear"],
}
# Which drivers a group can pick, and which are READY (actually link + play). hUGEDriver is
# selectable but its integrated game-build is a staged engine feature, so it is NOT ready:
# a group set to it wires no music (the studio surfaces the note + the tools/hugeref rig).
# "off" is the explicit no-music value for a group -- same effect, but it says so: the
# Atari Lynx packs CODE+DATA+BSS into ONE ~46.6 KB MAIN, so a big game (every scene type
# + the VM + the bkg engine) can be forced to trade the driver away there and still ship
# music on the roomier consoles (`projects/vm-showcase`).
_GLUE_READY_DRIVERS = {"vm", "huge"}
# Which mosaik pack implements each driver, and what it needs wiring. hUGEDriver
# (`vm.music_huge` over `native.huge`) registers the SAME core seam as vm.music,
# so the only difference here is the module name and that its song data is
# generated C (mosaik_vm.huge) rather than a `songs` module - so it wires no
# song/instrument tables at all.
_DRIVER_PACK = {"vm": "vm.music", "huge": "vm.music_huge"}
# Consoles with NO music driver at all. They are not a configurable GROUP (there is
# nothing to choose), but they still have to be excluded from the wiring: with every
# group on vm.music the setup() used to be emitted UNCONDITIONALLY, which linked the
# whole driver into a PCE / NES build as dead code (~430 B of the PC Engine's 32 KB
# cart). Keeping them here means the "all groups ready" case still forks.
_NO_DRIVER_PLATFORMS = ["pce", "nes"]


def _glue_audio_config(music="vm", audio=None):
    """Normalize the audio config into ``{"enabled": bool, "<group>": "<driver>"}``. Accepts
    the new ``audio`` dict OR the legacy scalar ``music`` ("vm"/"huge"/"off"/"auto")."""
    if audio is not None:
        cfg = {"enabled": bool(audio.get("enabled", True))}
        for g in _GLUE_GROUP_PLATFORMS:
            cfg[g] = str(audio.get(g, "vm")).lower()
        cfg["huge_hz"] = audio.get("huge_hz") or 0
        return cfg
    m = str(music or "vm").lower()
    if m == "off":
        return {"enabled": False, "gb": "vm", "lynx": "vm", "smsgg": "vm"}
    gb = "huge" if m == "huge" else "vm"                 # legacy scalar sets only the GB group
    return {"enabled": True, "gb": gb, "lynx": "vm", "smsgg": "vm"}


def emit_glue_mos(has_songs=False, has_instruments=False, music="vm",
                  audio=None, music_routine=False, has_subpatterns=False):
    """The `glue` module text. SOUND (SFX + the 2nd music voice) is ALWAYS wired, matching the
    previous inline shell. The MUSIC driver is wired PER CONSOLE GROUP, gated by ``audio`` (a
    dict ``{"enabled": bool, "gb"/"lynx"/"smsgg": "<driver>"}``; the legacy scalar ``music`` =
    "vm"/"huge"/"off"/"auto" is also accepted and maps onto the GB group).

    A group set to a driver that is READY (`vm` = our portable vm.music driver today) wires
    vm.music for that group's platforms; a group set to a NOT-ready driver (hUGEDriver, whose
    integrated game-build is a staged engine feature) wires NOTHING on those platforms -- NOT a
    vm.music fallback. So a GB-family set to hUGEDriver builds a SILENT GB ROM (until the bind
    ships) while Lynx / SMS-GG still play via vm.music. When ALL groups use vm.music (the
    default) the wiring is UNCONDITIONAL (byte-identical to the previous shell); a mixed config
    emits a `if platform == ...` conditional so only the ready platforms wire the driver.

    A no-songs project (or `enabled = False`) wires only sound."""
    cfg = _glue_audio_config(music, audio)
    wire_music = has_songs and cfg["enabled"]
    # The platforms whose group uses a READY driver, split by WHICH one: the two
    # packs are mutually exclusive on a target (hUGEDriver is GB-family assembly
    # and `native.huge` is a compile error elsewhere), so they fork per platform
    # exactly like the ready/not-ready split already did.
    vm_platforms, huge_platforms, any_not_ready = [], [], False
    if wire_music:
        for g, plats in _GLUE_GROUP_PLATFORMS.items():
            drv = cfg.get(g, "vm")
            if drv == "huge":
                huge_platforms += plats
            elif drv in _GLUE_READY_DRIVERS:
                vm_platforms += plats
            else:
                any_not_ready = True
    # Even with every GROUP on vm.music there are consoles with no driver at all, so the
    # wiring still has to fork -- otherwise they link the driver as dead code.
    all_vm = wire_music and not any_not_ready and not _NO_DRIVER_PLATFORMS

    imports = ['import "vm.core"', 'import "vm.snd"']
    sound = ["core.set_sound(snd.sfx, snd.tone, snd.stop)",
             "core.set_music_voice(snd.mtone, snd.mstop)"]
    huge_wiring = []
    if huge_platforms:
        # `vm.music_huge` imports `native.huge`, which is a COMPILE ERROR off the
        # GB family - and a mosaik `import` may not sit inside a conditional
        # block, so this pack cannot be imported by a glue.mos that also has to
        # compile for the Lynx or SMS. The caller therefore only offers hUGEDriver
        # to a GB-family-only project (see _glue_huge_ok); anything else keeps
        # vm.music everywhere and is told why.
        imports.append('import "vm.music_huge"')
        huge_wiring = [
            "core.set_music_driver(music_huge.play, music_huge.update, "
            "music_huge.stop)",
            "core.set_music_ctl(music_huge.pause, music_huge.resume, "
            "music_huge.mute)"]
        # The driver TICK RATE. 64 Hz is the reference engine's own (its 16 kHz timer at
        # TMA 0xC0 gives a 256 Hz ISR ticking every 4th) and the default, since
        # modules from a reference-engine project are composed against it; hUGETracker
        # standalone ticks at VBlank, i.e. 60. Emitted only when the project asks
        # for something other than the default, so the common case is unchanged.
        hz = int(cfg.get("huge_hz") or 0)
        if hz and hz != 64:
            huge_wiring.append("music_huge.set_rate(%d)" % max(17, min(255, hz)))
        # W7h - THE `6xy` CALL-ROUTINE QUEUE READER, wired from the same scan
        # that decides the feature is used (the `core.set_save` rule). Emitted
        # only for a project that ATTACHES a routine, because the reader's own
        # C body (`gbs_huge_routine_next`, the thunk and the driver's routines
        # table) is emitted on the same condition - an unconditional
        # registration would be an undefined symbol everywhere else, and the
        # by-use scan is the one thing that cannot go out of step with it.
        if music_routine:
            huge_wiring.append(
                "core.set_music_routines(music_huge.routine_next)")
    music_wiring = []
    if vm_platforms:
        imports += ['import "vm.music"', 'import "songs"']
        music_wiring = ["core.set_music_driver(music.play, music.update, music.stop)",
                        "music.set_song(songs.channels, songs.chkind, songs.cell, "
                        "songs.frames, songs.rows)",
                        "music.set_voicing(songs.flags)"]
        if has_instruments:
            imports.append('import "instruments"')
            music_wiring += ["music.set_instruments(instruments.kind, instruments.p0, "
                             "instruments.p1, instruments.vol)",
                             "music.set_waves(instruments.wavebyte)"]
            # Instrument SUBPATTERNS (hUGE v6 tables), wired only when an
            # instrument plays one: `instruments.sublen/subrow` exist on the
            # same condition, and this line is what the build scans to state
            # `VM_MUSIC_SUBPAT` (`_wants_music_subpat`), which is what compiles
            # the driver's table machinery at all.
            if has_subpatterns:
                music_wiring.append("music.set_subpatterns(instruments.sublen, "
                                    "instruments.subrow)")
        music_wiring.append("core.set_music_ctl(music.pause, music.resume, music.mute)")
        # D9 - OUR driver's `6xy` (effect 15, CALL ROUTINE) feeds the same
        # core drain hUGEDriver's thunk does, through vm.music's own queue.
        # Wired on the same by-use scan as the hUGE arm, for the same reason.
        if music_routine:
            music_wiring.append("core.set_music_routines(music.routine_next)")

    def _setup(body, base):
        ind = " " * base
        return ([ind + "function setup() {"]
                + [ind + "    " + b for b in body]
                + [ind + "}"])

    # One ARM per driver, plus the sound-only default. Emitted as MODULE-LEVEL
    # conditional compilation (the parser keeps one branch per target), NOT a
    # runtime `if` -- `platform` is a compile-time selector.
    arms = []
    if vm_platforms:
        arms.append((vm_platforms, sound + music_wiring))
    if huge_platforms:
        arms.append((huge_platforms, sound + huge_wiring))

    if not arms or (all_vm and not huge_platforms):
        # ONE setup() for every console: sound only (no/off music) or sound+music
        # (all vm). Byte-identical to the previous single-setup shell.
        setup_block = _setup(sound + music_wiring, 4)
    else:
        # Nested if/else, one level per arm (mosaik has no `else if`; branches
        # nest). The innermost else is sound only -- a console with no driver, or
        # a group turned off.
        setup_block, indent = [], 4
        for plats, body in arms:
            cond = " or ".join('platform == "%s"' % p for p in plats)
            setup_block.append(" " * indent + "if " + cond + " {")
            setup_block += _setup(body, indent + 4)
            setup_block.append(" " * indent + "} else {")
            indent += 4
        setup_block += _setup(sound, indent)
        for _ in arms:
            indent -= 4
            setup_block.append(" " * indent + "}")

    L = ["-- GENERATED by MosaiK8 Studio (mosaik_vm) -- the VM8 subsystem WIRING.",
         "-- The fixed shell calls glue.setup() once; this wires SOUND (always) + the MUSIC",
         "-- driver when the project has songs. Regenerated from project content, so adding a",
         "-- song auto-wires music. Edit the project (tracker / Build > Options), not this file."]
    if wire_music and huge_platforms:
        groups = [g for g in _GLUE_GROUP_PLATFORMS if cfg.get(g, "vm") == "huge"]
        L += ["-- NOTE: %s play through the REAL hUGEDriver (vm.music_huge over"
              % "/".join(groups),
              "-- native.huge, a prebuilt object in mosaik8/vendor/hugedriver), so playback is",
              "-- bit-true hUGETracker. Its song DATA is generated C rendered from songs.toml",
              "-- at BUILD time (mosaik_vm.huge), not the songs module -- which is why no song",
              "-- tables are wired on those consoles. It registers the SAME core seam, so",
              "-- vm.core and every music_play script are unchanged."]
    if wire_music and any_not_ready:                           # name the silent groups honestly
        off = [g for g in _GLUE_GROUP_PLATFORMS if cfg.get(g, "vm") == "off"]
        if off:
            L += ["-- NOTE: music is turned OFF on %s (Build > Options > Music) -- no driver +"
                  % "/".join(off),
                  "-- (with [build] shake_exports) no song DATA is wired there, freeing code/",
                  "-- data on those consoles (the tight Lynx MAIN). Other consoles still play."]
    if wire_music and vm_platforms:
        L += ["-- NOTE: %s have no music driver, so setup() forks and wires only sound there"
              % "/".join(_NO_DRIVER_PLATFORMS),
              "-- (with [build] shake_exports the driver + song data cost them nothing)."]
    L += ["",
          'module "glue" {']
    L += ["    " + imp for imp in imports]
    L += [""] + setup_block + ["", "    export setup", "}", ""]
    return "\n".join(L)


def _glue_audio_setting(root):
    """The project's per-console audio config from `studio.toml [audio]`
    (``music`` = "on"/"off" master + ``gb``/``lynx``/``smsgg`` = the group driver; the legacy
    scalar ``music`` = "vm"/"huge"/"off"/"auto" is migrated). Returns the normalized dict."""
    if toml is None:
        return _glue_audio_config("vm")
    a = {}
    sp = os.path.join(root, "studio.toml")
    if os.path.isfile(sp):
        try:
            a = dict(toml.load(sp).get("audio", {}) or {})
        except Exception:
            a = {}
    m = str(a.get("music", "on")).lower()
    if m in ("vm", "huge", "auto", "off"):                     # legacy scalar
        return _glue_audio_config(m)
    enabled = m not in ("off", "false", "0")
    cfg = {"enabled": enabled}
    for g in _GLUE_GROUP_PLATFORMS:
        cfg[g] = str(a.get(g, "vm")).lower()
    cfg["huge_hz"] = a.get("huge_hz") or 0
    return cfg


#: hUGEDriver is Game Boy APU assembly, so `vm.music_huge` only compiles for these.
_HUGE_PLATFORMS = {"gameboy", "gameboy_color", "analogue_pocket", "megaduck"}


def _glue_huge_ok(root):
    """May this project wire hUGEDriver? Only when EVERY target is GB-family.

    `vm.music_huge` imports `native.huge`, a compile error elsewhere, and a
    mosaik `import` cannot be put inside a conditional block - so one glue.mos
    cannot both wire hUGEDriver and compile for the Lynx or SMS. Returns
    (ok, [warnings])."""
    if toml is None:
        return True, []
    path = os.path.join(root, "mosaik.toml")
    if not os.path.isfile(path):
        return True, []
    try:
        targets = [str(t).lower() for t in
                   (toml.load(path).get("project", {}) or {}).get("target_platforms", [])]
    except Exception:
        return True, []
    return huge_conflict(targets)


def huge_conflict(targets):
    """`(ok, [warnings])` for a target list -- the same verdict
    `_glue_huge_ok` reaches from a project's mosaik.toml.

    PUBLIC because a UI has to answer this BEFORE the choice is made: the
    generator's job is to downgrade safely, but a Build dock that lets you
    pick hUGEDriver and then silently wires vm.music is how someone ends up
    believing they are hearing the real driver. One implementation, so the
    dock's note cannot disagree with what the generator then does."""
    other = [t for t in [str(t).lower() for t in targets]
             if t not in _HUGE_PLATFORMS]
    if not other:
        return True, []
    return False, ["hUGEDriver was selected but this project also targets %s, and "
                   "the driver is Game Boy APU assembly -- one generated glue.mos "
                   "cannot both wire it and compile for those consoles (a mosaik "
                   "import cannot be conditional). Keeping vm.music everywhere; "
                   "split the GB family into its own project to use hUGEDriver."
                   % ", ".join(sorted(set(other)))]


def _uses_music_routine(root):
    """Does this project ATTACH a script to a hUGE `6xy` call-routine effect?

    A text scan of the authored event lists, like `_wants_music_isr`'s scan of
    the generated sources: the glue is generated BEFORE the blob exists, so the
    bytecode cannot be asked. It errs WIDE by design - a false positive costs a
    seam registration and the thunk; a false negative is a music routine that
    never fires. Assembly (`.v8s`) front ends are covered too, since the
    mnemonic carries the same name."""
    for sub, exts in (("scripts", (".evt.toml", ".v8s")),):
        d = os.path.join(root, sub)
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if not name.endswith(exts):
                continue
            try:
                with open(os.path.join(d, name), encoding="utf-8") as f:
                    if "music_routine" in f.read():
                        return True
            except OSError:
                continue
    return False


def generate_glue(root, out_path=None, music=None, audio=None, warn=None):
    """Generate `src/glue.mos` from the project's content (has-songs / has-instruments) + the
    audio config (default: read `studio.toml [audio]`). Written by the studio scaffold +
    `VmScripts.generate` (only when a glue.mos already exists, so hand-wired projects are
    untouched)."""
    scripts_dir = os.path.join(root, "scripts")
    has_songs = bool(load_song_defs(scripts_dir))
    inst_defs = load_instrument_defs(scripts_dir)
    has_instruments = bool(inst_defs)
    if audio is None and music is None:
        audio = _glue_audio_setting(root)
    # Downgrade hUGEDriver to vm.music for a project this generator cannot wire
    # it into, and SAY so -- silently ignoring the setting is how a user ends up
    # believing they are hearing the real driver.
    if audio and any(v == "huge" for k, v in audio.items() if k != "enabled"):
        ok, warns = _glue_huge_ok(root)
        if not ok:
            audio = dict(audio)
            for g in _GLUE_GROUP_PLATFORMS:
                if audio.get(g) == "huge":
                    audio[g] = "vm"
            for w in warns:
                # A bare print reaches a terminal build and NOTHING else: run
                # from the studio it is swallowed, so the Build dock's combo
                # read "hUGEDriver" while glue.mos wired vm.music, with the
                # explanation lost. Callers that have somewhere to put it pass
                # a sink.
                if warn is not None:
                    warn(w)
                else:
                    print("  ⚠️  %s" % w)
    out_path = out_path or os.path.join(root, "src", "glue.mos")
    # Generate BEFORE opening the file. `open(..., "w")` TRUNCATES, so an
    # exception raised while emitting (a song over CELL_CHUNK, say) used to
    # leave a ZERO-BYTE generated module behind - which does not fail safe:
    # the build then dies layers later with `imports unknown module ...`,
    # naming neither the real cause nor the limit that was hit.
    text = emit_glue_mos(has_songs, has_instruments, music=music or "vm",
                         audio=audio,
                         music_routine=_uses_music_routine(root),
                         has_subpatterns=has_subpatterns(inst_defs))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    return out_path
