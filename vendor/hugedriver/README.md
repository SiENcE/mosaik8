# hUGEDriver - vendored, prebuilt

The Game Boy music driver from [hUGETracker](https://github.com/SuperDisk/hUGEDriver),
prebuilt as SDCC objects so a VM8 project links it with **no extra toolchain and no
network**. This lives in the ENGINE, not the studio, because a project has to build
with bare `mosaik8`.

| file | what | size |
|---|---|---|
| `hUGEDriver_gb.o` | Game Boy / Game Boy Color / Analogue Pocket - **hUGEDriver 6.1.3** | `_CODE` 1,947 B + `_DATA` 100 B |
| `hUGEDriver_duck.o` | Mega Duck (its APU registers are remapped) - CrossZGB's pre-6.1.1 build | `_CODE` 2,082 B + `_DATA` 100 B |
| `hUGEDriver.h` | the **matched** header (see the warning below) | |

## Provenance and licence

hUGEDriver is **dedicated to the public domain** by its author (see the upstream
README). Nothing here is modified:

- `hUGEDriver_gb.o` is the object inside the OFFICIAL release
  [hUGEDriver 6.1.3](https://github.com/SuperDisk/hUGEDriver/releases/tag/v6.1.3)
  (`hUGEDriver-6.1.3.zip`, `gbdk/hUGEDriver.lib`, the `hUGEDriver.o` member; line
  endings as checked out). 6.1.3 is the latest release (2024-07-14); master since
  then only swaps a DAA sequence for a shorter one (no behaviour change).
- `hUGEDriver_duck.o` and `hUGEDriver.h` come from
  [CrossZGB](https://github.com/gbdk-2020/CrossZGB) (`common/lib/duck/`,
  `common/include/`); CrossZGB's own wrapper code is MIT. 6.1.3's `hUGEDriver.h`
  is identical to this one apart from line endings, so the header matches both
  objects.

**History (2026-09-23).** The GB object used to be CrossZGB's too, and that build
predates 6.1.1: it was 1,946 B, one byte short of every 6.1.1+ build, and the
byte is the `xor a` that upstream commit 13a26513 ("Fix hUGE_set_position not
working because of flag thing") added to `_hUGE_set_position` - without it the C
entry point returns on a stale ZF or never sets `row_break`. Nothing called it, so
it was latent. The swap to 6.1.3 costs exactly **+1 B of bank 0** on every hUGE
build (measured by the resident end: the reference-engine sample conversion's GBC build
0x3F40 -> 0x3F41, GB 0x3E7D -> 0x3E7E, the shooter conversion +1 on both;
`vm-musicroutine` is an unbanked 32 KB cart) and passes `hugedriver_test.py`
(115), the studio's subpattern ROM test, `vm-musicroutine/verify.py` (28)
and both conversions' `verify.py`. The Mega Duck object has no upstream release
build and still lacks the fix.

They are **checked in rather than fetched**, which was a deliberate choice:
building them from source needs RGBDS *plus* hUGEDriver's `rgb2sdas.py`, and that
path is broken at upstream HEAD - RGBDS 1.0.1 emits object revision 13 while
`rgb2sdas.py` accepts only 6..9, so it would need a pinned old RGBDS as well as a
network step.

## THE HEADER MUST MATCH THE OBJECT, and upstream's does not

**This cost a full debugging session, so read it before touching these files.**

The song descriptor changed shape upstream. The version in the hUGEDriver repo
today starts:

```c
const unsigned char tempo1, tempo2, tempo3, tempo4;
const unsigned char order_cnt;              /* 5 bytes before order1 */
```

while **every shipped driver** - CrossZGB's objects here *and* the reference engine 4.x's
`hUGEDriver.lib` - is built against:

```c
unsigned char tempo;
const unsigned char * order_cnt;            /* 3 bytes before order1 */
```

Note `order_cnt` is declared as a POINTER but carries a COUNT (`2 * number of
orders`); that is the shipped ABI, odd as it looks, and hUGETracker's own C export
writes it that way.

Pairing the newer header with one of these objects shifts every order pointer by
two bytes. **It does not fail loudly**: `hUGE_init` succeeds, the driver's tick
counters advance, its RAM even holds plausible-looking pointers (each one is the
NEXT channel's order list), and the ROM is simply silent - the APU registers keep
their power-on values. The symptom looks like a dead driver, not a struct mismatch.

If these objects are ever replaced, **replace the header from the same source in
the same commit**, and re-run `mosaik8/tests/hugedriver_test.py`, which pins the
descriptor layout the generator emits against the header that ships here.

## Verifying by hand

The check that catches the mismatch is the APU, not the linker:

```
NR52 (0xFF26) low nibble != 0   ->  a channel is actually sounding
NR12 (0xFF12) == the instrument's envelope byte
```
