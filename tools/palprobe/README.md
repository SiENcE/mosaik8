# palprobe - does a sprite change palette as its animation does?

Two probes for per-FRAME sprite palettes (the worked sample is
`projects/vm-palanim`). Both read the OAM ATTRIBUTE byte, because that is where the
claim lives - a screenshot would only say the picture changed, not which of the
two palette mechanisms changed it.

- **`frame_palette_probe.py ROM.gbc SYM.noi [scene] [x] [y]`** - the COLOUR half.
  Jumps to a room by poking vm.core's pending exception (RAISE 2, the
  `ladderprobe` technique - it runs the real room load, sprite residency and the
  per-cell palette rows included) and reports every OAM slot drawn under more
  than one palette over 240 frames. On the reference-engine sample conversion's scene
  0 (its first interior room) the
  savepoint's two objects come back as palettes 4 and 5, its authored
  `pal = [5, 4]`; the same probe on a build without the feature reports **none**,
  which is the control that makes the reading mean something.
  Needs a `.noi`: relink with `-Wl-j` added to the build's own `lcc` line and a
  different `-o`, so the shipped ROM is untouched.

- **`dmg_flash_probe.py ROM.gb [label]`** - the DMG half, which needs no symbols
  at all. Runs the ROM in forced DMG mode with canned taps and prints how many
  sprites are on OBP1 whenever the screen changes. Run it on three ROMs (the
  worked case is the platformer conversion, local-only and not part of this
  repository):

  | ROM | title screen | intro cutscene |
  |---|---|---|
  | the platformer's reference ROM | 0 | **4 / 0 / 4** |
  | its conversion before per-frame palettes | **2** (wrong - the old odd-slot rule) | 1, never alternating |
  | its conversion after | 0 | **4 / 0 / 4** |

  The frame numbers do not line up between ROMs that pace differently, which is
  why the scene fingerprint is printed beside the count: compare the SHAPE of
  the sequence, not the frames it lands on.

## The trap this directory earned on day one

Both probes ask "did the coloured sprite CHANGE" - neither asks "did everything
ELSE keep its colour", and that second arm is where the first regression hid:
`F_PAL` defaulted uncoloured frames to 0 (a real palette), so recolouring the
savepoint flattened a big animated actor's per-cell palette 6 and the player's
0/1/2 row to OBJ palette 0 in every room - with both probes green. A SCREENSHOT
found it in seconds. The `frame_palette_probe`'s
histogram output is the cheap version of that arm: in the parallax room a
healthy build reads `{0: 1, 1: 1, 2: 2, 6: 24}` (the player's per-cell row +
the big actor on 6), and the flattened one read palette 0 across the board.

PyBoy trap while screenshotting: `pb.tick(1, False)` DISABLES rendering, so
`pb.screen.image` after it is solid white. Tick the final frame with
`render=True` before saving.
