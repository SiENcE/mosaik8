"""mosaik_vm.scaffold - New-project scaffolding + src/clips.mos generation."""
import os

try:
    import toml
except ImportError:  # pragma: no cover
    toml = None

from .loader import compile_path
from .projio import _project_sprite_pngs, _project_toml, _read_world_kinds


# --------------------------------------------------------------------------
# New Project scaffold. A VM8 project
# is a STATIC shell (never woven) + event lists + the generated scripts module.
# The studio's New Project will call scaffold_project(); the CLI `new` exposes it.
#
# SHELL_MAIN is PUBLIC because the shell is front-end agnostic: it imports the
# `scripts` module and boots it, and knows nothing about where that module came
# from. A project authored as TEXT ASSEMBLY (scripts/*.v8s, the second front
# end) needs exactly the same shell, so the studio lays this one down rather
# than keeping a copy that drifts.
# --------------------------------------------------------------------------
SHELL_MAIN = '''\
-- main.mos -- the VM8 static shell (generated once by New Project).
--
-- All game logic is DATA: the bytecode in the `scripts` module (compiled from
-- scripts/*.evt.toml by mosaik_vm.py), run by the fixed engine (lib/vm/*). This
-- shell only uploads art + hands the frame to the interpreter; it never changes.
--
-- DATA-DRIVEN ANIMATION (opt-in): author clips in the Animation dock
-- (studio.toml [animations.<kind>]); mosaik_vm.generate_clips writes src/clips.mos.
-- Then add the vm.canim + clips module imports to the header above (NO quotes in
-- THIS comment -- the build's import scanner is a regex over raw text, so a quoted
-- import written in a comment is read as a real import + fails the build), then:
--   ...  canim.set_clips(clips.frame, clips.count, clips.period, clips.flip, W, H)
--   ...  core.set_anim(canim.tick_all)   -- W,H = the actor metasprite size
--   ...  clips.upload_flip()   -- AFTER the sheet sprite.set_data; uploads the SMS/GG
--                              -- pre-mirrored LEFT tiles when a clip uses flip_left
--                              -- (a no-op on the flip-capable consoles, safe always).
-- and animate a placed actor from a script with the `actor_set_clip` event.
-- To ALSO animate the PLAYER data-driven (after player.set_base):
--   ...  canim.set_player(scenes.KIND_<player>)  -- state+facing derived each frame
-- Left out by default so a non-animating game never links engine.anim (zero cost).

module "main" {
    import "platform.video"
    import "graphics.sprite"
    import "vm.core"
    import "vm.snd"
    import "scripts"

    -- Project art: one 8x8 placeholder sprite tile (swap for your own).
    const TILES: array[u8, 16] = [
        0x3C, 0x3C, 0x42, 0x7E, 0x81, 0xFF, 0x81, 0xFF,
        0x81, 0xFF, 0x42, 0x7E, 0x3C, 0x3C, 0x00, 0x00
    ]

    function main() {
        sprite.set_data(0, 1, TILES)
        video.enable_lcd()
        video.show_background()
        video.show_sprites()

        core.boot(scripts.fetch, scripts.render_text, scripts.ENTRY_main)
        -- SOUND: route the sound events through platform.sound.
        -- set_sound = SFX on the beep channel; set_music_voice = MUSIC on its OWN
        -- channel (so music + SFX coexist). Drop these + the vm.snd import for silence.
        core.set_sound(snd.sfx, snd.tone, snd.stop)
        core.set_music_voice(snd.mtone, snd.mstop)
        core.run()
    }

    export main
}
'''

_STARTER_EVENTS = '''\
# The authored event lists for this VM8 project (the reference engine's project-file analogue). Edit
# these + re-run: python mosaik_vm.py <this folder> -o src/scripts.mos
# The starter: greet the player, then wander one actor around a rectangle.

[[script]]
name = "main"
events = [
  { event = "actor_activate", actor = 0, tile = 0, x = 40, y = 40 },
  { event = "lock" },
  { event = "text", string = "VM8 PROJECT\\nPRESS A" },
  { event = "unlock" },
  { event = "start_thread", script = "wander" },
  { event = "stop" },
]

[[script]]
name = "wander"
loop = true
events = [
  { event = "actor_move_to", actor = 0, x = 120, y = 40  },
  { event = "actor_move_to", actor = 0, x = 120, y = 110 },
  { event = "actor_move_to", actor = 0, x = 40,  y = 110 },
  { event = "actor_move_to", actor = 0, x = 40,  y = 40  },
]
'''
def generate_clips(root, out_path=None):
    """DATA-DRIVEN animation (Option X): generate `src/clips.mos` from the project's
    studio.toml `[animations.<kind>]` + the world `[kinds]` + the sprite manifests, so
    a fixed VM shell + the runtime (vm.canim) animate placed kinds. Returns the path,
    or None when the project has no `[animations]` (then no clips module + no cost).

    Sprite-cell tile offsets are cumulative ACROSS sheets (each sheet uploaded above
    the previous), matching a shell that uploads them in `[assets] sprites` order."""
    import mosaik_anim
    from mosaik_assets import (sheet_sprite_defs, sprite_frame_masks,
                               sprite_frame_descs)
    studio = os.path.join(root, "studio.toml")
    if not os.path.isfile(studio):
        return None
    anims = toml.load(studio).get("animations", {})
    if not anims:
        return None
    kind_ids = _read_world_kinds(root)
    # PER-ROOM SPRITE RESIDENCY (studio.toml [sprites] residency = "room"):
    # each kind has its OWN sheet uploaded at a per-room VRAM base, so cell
    # offsets stay RELATIVE to their png (vm.canim adds the actor's base at
    # draw). The default keeps the boot-upload-everything layout: offsets
    # accumulate across sheets in [assets] order.
    residency = (toml.load(studio).get("sprites", {}) or {}
                 ).get("residency") == "room"
    sheet_pngs = [p for p in _project_sprite_pngs(root) if os.path.isfile(p)]
    cell_tile = {}
    cell_size = {}
    cell_mask = {}
    cell_desc = {}
    base = 0
    for png in sheet_pngs:
        defs = sheet_sprite_defs(png)
        # SPARSE frames (a manifest entry with a `mask`): the rect holds only
        # the DRAWN columns, so the tile accounting above is unchanged, but the
        # frame is `cols` wide on screen and its blank columns are skipped at
        # draw time (sprite.set_meta_mask).
        sparse = sprite_frame_masks(png)
        for name, off, w, h in defs:
            cell_tile[name] = off if residency else base + off
            cols, mask = sparse.get(name, (0, 0))
            cell_size[name] = (cols or w, h)
            if mask:
                cell_mask[name] = mask
        # DESCRIPTOR frames ([[frame]] entries): named compositions over the
        # sheet's pool cells at authored offsets - rows may overlap, cells may
        # repeat (the tile dedupe). They own no rect, so the tile accounting
        # above never sees them.
        for fname, (pw, ph, objs) in sprite_frame_descs(png).items():
            cell_desc[fname] = (pw, ph, objs)
            cell_size[fname] = ((pw + 7) // 8, (ph + 7) // 8)
        if defs:
            n, off, w, h = defs[-1]
            base += off + w * h        # this sheet's total tiles
    # Per-kind metasprite size, taken from the kind's OWN first clip cell. A
    # world that mixes sprite shapes (a reference-engine import: 2x2 NPCs beside a 7x6
    # boss) cannot draw every actor at one size -- set_meta fans w*h OAM
    # objects each, so the largest would blow the GB's 40-object limit and
    # scramble every sprite on screen. transpile_kinds emits the per-kind
    # tables only when the sizes actually differ, so a uniform world is
    # byte-identical.
    kind_size = {}
    for kname, clips in anims.items():
        for clip in clips.values():
            if not isinstance(clip, dict):
                continue        # scalar keys beside the clips (default_state)
            for _facing, (seq, _flip, _pal) in mosaik_anim._resolve(clip).items():
                hit = [c for c in seq if c in cell_size]
                if hit:
                    kind_size[kname] = cell_size[hit[0]]
                    break
            if kname in kind_size:
                break
    # SMS/GG soft-flip bake: pre-mirror the RIGHT cells a flip_left LEFT derives from,
    # so the no-sprite-flip consoles render the correct facing (review 2026-07-12 2.5).
    # None when nothing uses flip_left -> the clips module stays byte-identical.
    if residency:
        # Per-room residency: each kind's frames are RELATIVE to its own sheet,
        # so the boot bake (pixels at ONE fixed base above every sheet) cannot
        # serve it. The BUILD appends each kind's mirrors to the end of its
        # own sheet on SMS/GG (mosaik8_build._bake_soft_flip), and the clips
        # only need the kind-relative indices - from the same planner the
        # build and generate_rooms use. This used to be skipped outright, and
        # every left-facing actor of a reference-engine conversion drew unmirrored on
        # SMS/GG. (A [[frame]] DESCRIPTOR composition is not a sheet cell, so
        # the planner leaves it on the FLIP_X frames.)
        ksheets = (toml.load(studio).get("kind_sprites", {}) or {})
        kind_pngs = {k: os.path.join(root, "assets", str(s) + ".png")
                     for k, s in ksheets.items()}
        bake = mosaik_anim.residency_flip_bake(
            anims, {k: p for k, p in kind_pngs.items() if os.path.isfile(p)})
    else:
        cells = mosaik_anim._mirror_cells(
            clip for clips in anims.values() for clip in clips.values()
            if isinstance(clip, dict))  # skip scalars beside the clips (default_state)
        bake = mosaik_anim.build_flip_bake(cells, sheet_pngs)
    # 8x16 OBJ mode forks a descriptor world's DESC blob per platform (one
    # 8x16 object per pool cell on the GB family, two stacked 8x8 objects
    # everywhere else); a build without the flag emits the 8x8 form only.
    mt = os.path.join(root, "mosaik.toml")
    obj16 = False
    if cell_desc and os.path.isfile(mt):
        obj16 = bool((toml.load(mt).get("build", {}) or {}).get("obj_8x16"))
    src = mosaik_anim.transpile_kinds(anims, cell_tile, kind_ids, bake=bake,
                                      kind_size=kind_size,
                                      cell_mask=cell_mask or None,
                                      cell_desc=cell_desc or None,
                                      obj16=obj16)
    out_path = out_path or os.path.join(root, "src", "clips.mos")
    # A Lynx overlay build keeps the per-frame clip lookups resident (`hot`,
    # mosaik.hotmark); every other project's text is unchanged.
    from mosaik.hotmark import mark_hot
    src = mark_hot(src, "clips", root)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(src)
    return out_path


def scaffold_project(root, name=None):
    """Create a runnable VM8 project skeleton at `root`: mosaik.toml, the
    static shell (src/main.mos), a starter event list (scripts/main.evt.toml),
    and the compiled src/scripts.mos. Returns the list of written paths."""
    name = name or os.path.basename(os.path.normpath(root))
    src = os.path.join(root, "src")
    scripts = os.path.join(root, "scripts")
    os.makedirs(src, exist_ok=True)
    os.makedirs(scripts, exist_ok=True)
    written = []

    def write(path, text):
        with open(path, "w") as f:
            f.write(text)
        written.append(path)

    write(os.path.join(root, "mosaik.toml"), _project_toml(name))
    write(os.path.join(src, "main.mos"), SHELL_MAIN)
    write(os.path.join(scripts, "main.evt.toml"), _STARTER_EVENTS)
    # Compile the starter events so `New -> build` works immediately.
    prog = compile_path(scripts)
    write(os.path.join(src, "scripts.mos"), prog.to_scripts_mos())
    return written
