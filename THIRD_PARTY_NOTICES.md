# Third-party notices

MosaiK8 is MIT-licensed (`LICENSE`). This file lists the third-party work it
contains, derives from, or links into the ROMs it builds, with the licence of
each and, for every MIT work whose code it contains, that licence's full text
as its terms require.

This is our reading of the upstream licence files; it is not legal advice.

## Summary

| Upstream | Licence | What MosaiK8 uses it for |
|---|---|---|
| [gbvm](https://github.com/chrismaltby/gbvm) | MIT | Inspiration for the design of VM8, MosaiK8's own cross-platform virtual machine. No gbvm code is included. |
| [CrossZGB](https://github.com/gbdk-2020/CrossZGB) | MIT | The variable-width text renderer (`mosaik/codegen/gbdk_text.py`) is derived from its `vwf_print_render`, and **that code is compiled into every ROM that draws variable-width text**; the prebuilt Mega Duck hUGEDriver object came via CrossZGB. |
| [hUGEDriver / hUGETracker](https://github.com/SuperDisk/hUGEDriver) | Public domain | `vendor/hugedriver/` (the unmodified driver objects and header), the `.uge` reader in `mosaik_vm/uge.py` (a port of hUGETracker's reader), the note table in `lib/vm/music.mos`. |
| [flozz/gameboy-examples](https://github.com/flozz/gameboy-examples) | WTFPL | The tileset and map of `projects/background` (from its example 06; credited in `background.mos`). |
| [GBDK-2020](https://github.com/gbdk-2020/gbdk-2020) | GPLv2 with a linking exception (SDCC's runtime likewise) | **Fetched, never bundled** (`setup_tools.py`). GBDK's library, including its console font `font_ibm`, is LINKED into GBDK-console ROMs; no GBDK source or data is copied into this repository. |
| [cc65](https://github.com/cc65/cc65) | zlib | **Fetched, never bundled.** Its runtime, including the PC Engine console font `pce_font`, is LINKED into Lynx / PC Engine ROMs. |
| [Pan Docs](https://github.com/gbdev/pandocs) | CC0 | Cited as the Game Boy hardware reference; nothing copied. |

The emulator cores and tools `setup_tools.py` fetches for testing carry their
own licences; the README lists them. None of them is part of this repository
or of a ROM.

## What this means for a ROM you build

The runtimes MosaiK8 links into a ROM (GBDK-2020 / SDCC with their linking
exceptions, cc65's zlib runtime, the public-domain hUGEDriver) generally permit
proprietary ROMs, subject to their licence terms and to those of every other
piece of code, art and music you put into the game.

**A ROM that uses variable-width text** (`text.vwf_*`) contains code derived
from CrossZGB. Its MIT notice below must accompany that ROM: put it in the
credits, the manual, or a text file distributed with it. The generated C of such a program carries
the same notice as a comment, so it travels with the source too.

## CrossZGB

```text
The MIT License (MIT)

Copyright (c) 2016 - 2024 Gonzalo de Santos Garcia, GBDK-2020 organization

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## hUGEDriver and hUGETracker

hUGETracker and hUGEDriver are dedicated to the public domain by their author
(upstream README: "hUGETracker and hUGEDriver are dedicated to the public
domain."). `vendor/hugedriver/README.md` records which release the vendored
objects are and where they came from.

## flozz/gameboy-examples

The tileset and map of `projects/background` come from example 06 of
https://github.com/flozz/gameboy-examples ("Graphics 3 - background"), published under
the WTFPL (Do What The F*ck You Want To Public License, version 2), which
places no conditions on reuse. Credited in `projects/background/src/background.mos`.
