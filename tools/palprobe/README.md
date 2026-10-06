# palprobe - does a sprite change palette as its animation does?

A probe for per-FRAME sprite palettes (the worked sample is
`projects/vm-palanim`). It reads the OAM ATTRIBUTE byte, because that is where
the claim lives - a screenshot would only say the picture changed, not which of
the two palette mechanisms changed it.

- **`frame_palette_probe.py ROM.gbc SYM.noi [scene] [x] [y]`** - jumps to a room
  by poking vm.core's pending exception (RAISE 2 - it runs the real room load,
  sprite residency and the per-cell palette rows included) and reports every
  OAM slot drawn under more than one palette over 240 frames. On
  `vm-palanim` (GBC, scene 0) it reports slots 2..7, each drawn under two
  attribute values with a different tile per value, which is the frame palette
  following the animation. Run it on a build without the feature too: it must
  report **none** there, the control that makes the reading mean something.
  Needs a `.noi`: `tools/framebudget/build_noi.py <project> --platform
  gameboy_color` relinks with `-Wl-j`.

  The attribute value is masked with `0x17`, so it carries BOTH mechanisms: the
  CGB palette number (bits 0..2) and the DMG OBP1 select (bit 4, read as 16).
  A sprite that FLASHES on the DMG alternates OBP0 / OBP1 (0 / 16); compare the
  SHAPE of the sequence between two ROMs, not the frames it lands on, since two
  builds that pace differently reach a scene at different frames.

## The trap this directory earned on day one

The probe asks "did the coloured sprite CHANGE" - it does not ask "did
everything ELSE keep its colour", and that second arm is where the first
regression hid: `F_PAL` defaulted uncoloured frames to 0 (a real palette), so
recolouring one animated object flattened a big actor's per-cell palette 6 and
the player's 0/1/2 row to OBJ palette 0 in every room - with the probe green. A
SCREENSHOT found it in seconds. Look at a frame of every room the change can
reach, not only the one the probe names.

PyBoy trap while screenshotting: `pb.tick(1, False)` DISABLES rendering, so
`pb.screen.image` after it is solid white. Tick the final frame with
`render=True` before saving.
