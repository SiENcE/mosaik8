"""Audio emitters for the GBDK backend: the portable beep/SFX prelude and
the native.huge (hUGEDriver) binding.

A GbdkBackend concern mixin - methods run against the full
CodeGenerator instance (self.emit, self.caps, ...)."""


class GbdkSoundMixin:
    def _gbdk_timer_installer(self):
        # Both clients must select the same vector: GBDK forbids mixing its
        # ordinary and low-priority timer chains in one ROM.
        return "add_low_priority_TIM" if self.parallax_used else "add_TIM"

    def _emit_gbdk_huge(self):
        """`native.huge` -- the hUGEDriver wrappers (emitted only when imported).

        The driver itself is a PREBUILT SDCC object added to the link by
        `mosaik8_build` (`mosaik8/vendor/hugedriver`); this only wraps its entry
        points so mosaik can reach them, since mosaik has no pointers and cannot
        name a song descriptor.

        The split with the generated song module is: HARDWARE here, DATA there.
        `gbs_huge_play` powers up the APU and then calls `gbs_huge_song_init`,
        which the generated module defines (it owns the song table and switches
        to each song's own ROM bank -- a library is tens of KB and cannot be
        resident). A program that calls `huge.play` with no song module fails as
        an undefined symbol at link, which is the loud failure this codebase
        prefers to a silently silent ROM.

        **The APU power-up belongs here.** hUGEDriver does not do it - its README
        tells the game to write NR52/NR51/NR50 itself - and a driver that plays
        into a powered-down APU looks exactly like a broken driver. Every other
        piece of hardware setup in this backend is prelude-owned, so this is too.
        """
        self.emit("/* native.huge: hUGEDriver (prebuilt object, GB family only). The")
        self.emit("   header ships beside the object and MUST match it -- pairing a")
        self.emit("   newer hUGEDriver.h with these builds shifts every order pointer")
        self.emit("   and yields a SILENT rom, not an error. See vendor/hugedriver. */")
        self.emit('#include "hUGEDriver.h"')
        self.emit("/* Defined by the generated song module (it owns the song table and")
        self.emit("   the per-song ROM bank). */")
        self.emit("void gbs_huge_song_init(uint8_t song);")
        self.emit("/* Power up the APU first: hUGEDriver leaves that to the game, and a")
        self.emit("   driver ticking into a powered-down APU looks exactly like a broken")
        self.emit("   driver (NR52 reads 0x70 and every channel bit stays 0). */")
        self.emit("/* SFX <-> MUSIC ARBITRATION, the reference engine's model (music_manager.c).")
        self.emit("   hUGEDriver owns all four channels, and vm.snd writes two of them")
        self.emit("   directly (sound.beep is pulse 2, the 2nd music voice is pulse 1), so")
        self.emit("   without this the first effect of a room takes a channel away")
        self.emit("   mid-note and leaves its envelope at zero -- the song loses that")
        self.emit("   voice for good, and while the effect sounds BOTH drivers write the")
        self.emit("   same registers.")
        self.emit("   The fix is to BORROW, not to reserve: an effect mutes the channel in")
        self.emit("   the driver while it plays and hands it straight back, so a song that")
        self.emit("   uses all four keeps all four whenever nothing else is sounding.")
        self.emit("   TWO masks, OR-ed and never assigned: the game's own music_mute is")
        self.emit("   persistent, the borrow is temporary, and an effect ending must not")
        self.emit("   clear a mute the game asked for. Bit 0 = CH1 .. bit 3 = CH4. */")
        self.emit("static uint8_t gbs_huge_gmute = 0;   /* the game's own music_mute */")
        self.emit("static uint8_t gbs_huge_sfxm = 0;    /* channels an effect has borrowed */")
        self.emit("static uint8_t gbs_huge_effm = 0;    /* what the driver was last given */")
        self.emit("static void gbs_huge_apply_mute(void) {")
        self.emit("    uint8_t m = gbs_huge_gmute | gbs_huge_sfxm;")
        self.emit("    if (m != gbs_huge_effm) hUGE_mute_mask = gbs_huge_effm = m;")
        self.emit("}")
        self.emit("void gbs_huge_sfx_take(uint8_t ch) {")
        self.emit("    gbs_huge_sfxm |= ch;")
        self.emit("    gbs_huge_apply_mute();")
        self.emit("}")
        self.emit("void gbs_huge_sfx_give(uint8_t ch) {")
        self.emit("    gbs_huge_sfxm &= (uint8_t)~ch;")
        self.emit("    gbs_huge_apply_mute();")
        self.emit("    /* The driver CACHES which waveform is in wave RAM to avoid")
        self.emit("       re-uploading it, so an effect that used CH3 has invalidated that")
        self.emit("       cache without the driver knowing -- CH3 would then play the")
        self.emit("       overwritten RAM forever. The reference engine resets it on every effect end;")
        self.emit("       so do we, and the reload costs nothing until CH3 next triggers. */")
        self.emit("    hUGE_reset_wave();")
        self.emit("}")
        self.emit("void gbs_huge_mute(uint8_t mask) {")
        self.emit("    gbs_huge_gmute = mask;")
        self.emit("    gbs_huge_apply_mute();")
        self.emit("}")
        self.emit("/* PLAYING THE SONG THAT IS ALREADY PLAYING IS A NO-OP, which is GB")
        self.emit("   Studio's own rule (`music_load` early-outs when the bank and track")
        self.emit("   both match). Without it every scene whose On Init plays the area's")
        self.emit("   theme RESTARTS it from the top, so walking between rooms never lets")
        self.emit("   the song past its opening bars - which reads as 'the music does not")
        self.emit("   keep going', not as a bug in the driver. Cleared by stop(), so")
        self.emit("   replaying after a stop starts over as asked. */")
        self.emit("static uint8_t gbs_huge_cur = 0xFF;")
        self.emit("static uint8_t gbs_huge_on = 0;")
        self.emit("void gbs_huge_play(uint8_t song) {")
        self.emit("    if (gbs_huge_on && song == gbs_huge_cur) return;")
        self.emit("    NR52_REG = 0x80; NR51_REG = 0xFF; NR50_REG = 0x77;")
        self.emit("    gbs_huge_gmute = 0x00;")
        self.emit("    gbs_huge_apply_mute();")
        self.emit("    gbs_huge_cur = song;")
        self.emit("    gbs_huge_on = 1;")
        self.emit("    gbs_huge_song_init(song);")
        self.emit("    gbs_huge_wire();")
        self.emit("}")
        self.emit("/* The driver reads its patterns IN PLACE on every tick, so the song's")
        self.emit("   bank has to be mapped around each tick as well as around init. The")
        self.emit("   song module owns the variable; 0 means the data is resident. */")
        self.emit("extern uint8_t gbs_huge_bank;")
        self.emit("/* THE TICK IS A TIMER INTERRUPT, NOT A GAME-LOOP CALL, and that is the")
        self.emit("   whole point. hUGE's tempo is in TICKS, so the driver has to be called")
        self.emit("   at a FIXED rate; driving it from the VM loop ties the music to the")
        self.emit("   frame rate, and a VM frame is not a display frame. Measured on")
        self.emit("   the reference-engine sample conversion against the reference engine's own ROM: theirs holds 64.0 Hz whether")
        self.emit("   the player stands still or walks, ours ran 59.7 Hz standing and")
        self.emit("   collapsed to 24.3 Hz walking -- 38% of tempo, swinging with load,")
        self.emit("   which is heard as both wrong speed and unstable envelopes/vibrato.")
        self.emit("   4096 Hz / (256 - 192) = 64 Hz, exactly the reference engine's own timer rate. */")
        self.emit("void gbs_huge_isr(void) NONBANKED {")
        if self.parallax_used or self.win_cut_used:
            # THE DRIVER TICK MUST NOT HOLD OFF A SCANLINE INTERRUPT. This ISR
            # runs ~1-2k cycles with IME off, and when the timer fires just
            # before a band boundary (or the box's sprite-cut line) the LYC
            # interrupt is DELAYED past its hblank: the band's SCX lands 1-6
            # scanlines late and its top rows render at the PREVIOUS band's
            # scroll - a one-frame horizontal displacement of the hills that
            # reads as flicker while walking. Measured on the reference-engine sample conversion's
            # parallax room: band 1's scroll took effect at line 80 on 880
            # of 900 frames and at 81-86 on 20; of 29 late frames, 28 had
            # this tick running at the boundary. The standard remedy is
            # INTERRUPT NESTING (hUGEDriver's own docs suggest it for exactly
            # this): re-enable interrupts for the duration of the tick so LYC
            # preempts, and disable again before returning so the dispatcher's
            # invariants hold. The nested LCD/VBL handlers are all NONBANKED
            # and touch only registers + their own WRAM, so running them with
            # the SONG bank mapped is safe. The re-entrancy latch drops a
            # nested tick outright - the period is 65,536 cycles against a
            # ~2k tick, so it can only trip under pathological load, and one
            # dropped tick is inaudible where a recursing one is a crash.
            # Emitted only when the program HAS a scanline consumer, so every
            # other hUGE project's ISR is byte-identical.
            self.emit("    static volatile uint8_t in_tick = 0;")
            self.emit("    uint8_t saved;")
            if self.parallax_used:
                self.emit("    /* The low-priority TIMER chain enables nesting at the vector,")
                self.emit("       before dispatcher overhead can delay an LYC band boundary.")
                self.emit("       Unlike a raw ISR_NESTED_VECTOR it retains GBDK's register")
                self.emit("       saves and handler chain; BOTH timer clients select it.")
                self.emit("       Re-enable here too: a preceding callback may return with")
                self.emit("       IME off. Keep the latch, bank restore and safe return. */")
            else:
                # Preserve no-parallax output exactly, including its historical
                # comment. The supported-chain explanation above supersedes it
                # only for the parallax programs changed by this fix.
                self.emit("    /* NEST FIRST, BEFORE THIS ISR'S OWN PROLOGUE. The LYC band chain")
                self.emit("       must be able to preempt this ~2k-cycle tick, or a band's scroll")
                self.emit("       lands scanlines late whenever the timer fires at a boundary")
                self.emit("       (the parallax hill flicker). Nesting AFTER the latch and the")
                self.emit("       bank switch still left 176 cycles with interrupts off -")
                self.emit("       MEASURED, and 0.39 of a scanline is a whole boundary's worth of")
                self.emit("       exposure. Moving the nest to the top took the reference-engine sample conversion's")
                self.emit("       band-boundary slip from 1.00%% of frames to 0.66%%. This is")
                self.emit("       `gbs_mdrv_isr`'s own order, which already nests before it does")
                self.emit("       anything.")
                self.emit("")
                self.emit("       The REST of that exposure is GBDK's chaining ISR dispatcher,")
                self.emit("       which runs before this function is entered at all. The reference VM has")
                self.emit("       none of it: it does not use a dispatcher, its timer VECTOR is")
                self.emit("       literally `ei` then `jp` (the reference VM's src/core/gb/interrupt_timer.s).")
                self.emit("       GBDK offers the same shape as ISR_NESTED_VECTOR, and it was")
                self.emit("       tried and REVERTED: the raw vector cannot chain (the portable")
                self.emit("       driver's watchdog wants the timer too) and a handler installed")
                self.emit("       there must save its own registers - with `__interrupt` added")
                self.emit("       the ROM still hung inside its FIRST tick (one entry, never")
                self.emit("       another, the title stopped answering the pad). Left as a")
                self.emit("       measured dead end rather than a half-working path. */")
            self.emit("    enable_interrupts();")
            self.emit("    if (in_tick) {          /* pathological nested tick: drop it */")
            self.emit("        disable_interrupts();   /* the dispatcher's invariant */")
            self.emit("        return;")
            self.emit("    }")
            self.emit("    in_tick = 1;")
            self.emit("    saved = CURRENT_BANK;")
            self.emit("    if (gbs_huge_bank) SWITCH_ROM(gbs_huge_bank);")
            self.emit("    hUGE_dosound();")
            self.emit("    disable_interrupts();")
            self.emit("    SWITCH_ROM(saved);")
            self.emit("    in_tick = 0;")
        else:
            self.emit("    uint8_t saved = CURRENT_BANK;")
            self.emit("    if (gbs_huge_bank) SWITCH_ROM(gbs_huge_bank);")
            self.emit("    hUGE_dosound();")
            self.emit("    SWITCH_ROM(saved);")
        self.emit("}")
        self.emit("static uint8_t gbs_huge_wired = 0;")
        self.emit("/* The driver tick rate in Hz. 64 is the reference engine's own (its 16 kHz timer")
        self.emit("   at TMA 0xC0 gives a 256 Hz ISR and it ticks every 4th), and hUGE")
        self.emit("   modules from a reference-engine project are composed against it. hUGETracker")
        self.emit("   standalone ticks at VBlank instead, so 60 is the other sensible")
        self.emit("   value; huge.set_rate(hz) picks. 4096/hz must fit a byte, so hz >= 17. */")
        self.emit("uint8_t gbs_huge_hz = 64;")
        self.emit("void gbs_huge_set_rate(uint8_t hz) {")
        self.emit("    if (hz < 17) hz = 17;")
        self.emit("    gbs_huge_hz = hz;")
        self.emit("    TMA_REG = (uint8_t)(256 - (4096u / hz));")
        self.emit("    TAC_REG = TACF_START | TACF_4KHZ;")
        self.emit("}")
        self.emit("static void gbs_huge_wire(void) {")
        self.emit("    if (gbs_huge_wired) return;")
        self.emit("    gbs_huge_wired = 1;")
        self.emit("    CRITICAL { %s(gbs_huge_isr); }" % self._gbdk_timer_installer())
        self.emit("    gbs_huge_set_rate(gbs_huge_hz);")
        self.emit("    /* OR into the live mask: another feature may already own VBL/LCD")
        self.emit("       (the dialogue box's sprite cut does), and ASSIGNING would turn")
        self.emit("       its interrupt off -- or its later assign would turn this one off,")
        self.emit("       silencing the music the moment a text box opened. */")
        self.emit("    set_interrupts(IE_REG | VBL_IFLAG | TIM_IFLAG);")
        self.emit("}")
        self.emit("/* The music seam's per-frame call. The TIMER owns the tick, so this is")
        self.emit("   deliberately EMPTY -- ticking here too would double the tempo. */")
        self.emit("void gbs_huge_update(void) { }")
        if self.huge_routines_used:
            self._emit_gbdk_huge_routines()
        self.emit("void gbs_huge_setpos(uint8_t pattern) { hUGE_set_position(pattern); }")
        self.emit("/* Stop = mute every channel and silence the envelopes. The driver has")
        self.emit("   no stop entry point of its own; muting is how hUGE quiets a channel,")
        self.emit("   and zeroing the envelopes kills anything already ringing. */")
        self.emit("void gbs_huge_stop(void) {")
        self.emit("    gbs_huge_on = 0;")
        self.emit("    gbs_huge_gmute = 0x0F;")
        self.emit("    gbs_huge_apply_mute();")
        self.emit("    NR12_REG = 0x00; NR22_REG = 0x00; NR32_REG = 0x00; NR42_REG = 0x00;")
        self.emit("    NR14_REG = 0x80; NR24_REG = 0x80; NR34_REG = 0x80; NR44_REG = 0x80;")
        self.emit("}")

    def _emit_gbdk_huge_routines(self):
        """W7h -- the `6xy` CALL-ROUTINE thunk, its queue and the driver table.

        A `6xy` cell makes hUGEDriver jump through the song descriptor's
        `routines` table. The reference engine registers ONE thunk in all sixteen slots
        (`hUGEDriverRoutines.h`) and does the real dispatch itself; so do we,
        because the driver's routine id is the effect parameter's low nibble
        while the VM's slot is its low two BITS - two different decodings of
        one byte, and only the second is ours to choose.

        THE QUEUE IS WHY THIS IS C. The thunk runs inside the driver's TIMER
        INTERRUPT (`gbs_huge_isr`), and a VM thread may not be spawned from an
        interrupt - so the thunk only enqueues, and `vm.core`'s main loop
        drains through `gbs_huge_routine_next`. That is `music_manager.c`'s own
        split (`hUGETrackerRoutine` enqueues, `music_events_update` drains from
        `core.c`'s loop), and the ring needs a CRITICAL section around it,
        which mosaik has no way to express.

        Emitted only when the program's blob carries `MUSIC_ROUTINE`, so every
        other hUGE project's prelude is byte-identical - and so is its song
        data, whose `routines` field stays NULL (mosaik_vm/huge.py).
        """
        self.emit("/* W7h: the hUGE `6xy` call-routine queue. The driver calls the thunk")
        self.emit("   from its TIMER INTERRUPT, so it only ENQUEUES; vm.core's main loop")
        self.emit("   drains it and spawns. The reference engine's music_manager.c, same split. */")
        self.emit("#define GBS_HUGE_RTQ 4        /* must be a power of 2 */")
        self.emit("static volatile uint8_t gbs_huge_rtq[GBS_HUGE_RTQ];")
        self.emit("static volatile uint8_t gbs_huge_rth = 0, gbs_huge_rtt = 0;")
        self.emit("/* ON OVERFLOW THE OLDEST GOES, not the newest: the tail advances with")
        self.emit("   the head, which is the reference's rule (a full queue means the game")
        self.emit("   loop is behind, and the LATEST musical event is the interesting one). */")
        self.emit("void hUGETrackerRoutine(unsigned char tick, unsigned int param) NONBANKED {")
        self.emit("    if (tick) return;     /* once per ROW, not once per tick */")
        self.emit("    gbs_huge_rth = (gbs_huge_rth + 1) & (GBS_HUGE_RTQ - 1);")
        self.emit("    if (gbs_huge_rth == gbs_huge_rtt)")
        self.emit("        gbs_huge_rtt = (gbs_huge_rtt + 1) & (GBS_HUGE_RTQ - 1);")
        self.emit("    gbs_huge_rtq[gbs_huge_rth] = (uint8_t)param;")
        self.emit("}")
        self.emit("/* The next queued parameter byte, or 0xFFFF when the queue is empty.")
        self.emit("   A u16 because every byte value is a legal parameter - there is no")
        self.emit("   spare sentinel in 0..255. */")
        self.emit("uint16_t gbs_huge_routine_next(void) {")
        self.emit("    uint8_t data;")
        self.emit("    if (gbs_huge_rth == gbs_huge_rtt) return 0xFFFF;")
        self.emit("    CRITICAL {")
        self.emit("        gbs_huge_rtt = (gbs_huge_rtt + 1) & (GBS_HUGE_RTQ - 1);")
        self.emit("        data = gbs_huge_rtq[gbs_huge_rtt];")
        self.emit("    }")
        self.emit("    return data;")
        self.emit("}")
        self.emit("/* ALL SIXTEEN driver slots point at the one thunk, as the reference engine's")
        self.emit("   hUGEDriverRoutines.h does: hUGEDriver indexes this by the effect")
        self.emit("   parameter's LOW NIBBLE, which is not the VM's routine slot. */")
        self.emit("const hUGERoutine_t gbs_huge_routines[16] = {")
        self.emit("    hUGETrackerRoutine, hUGETrackerRoutine, hUGETrackerRoutine, hUGETrackerRoutine,")
        self.emit("    hUGETrackerRoutine, hUGETrackerRoutine, hUGETrackerRoutine, hUGETrackerRoutine,")
        self.emit("    hUGETrackerRoutine, hUGETrackerRoutine, hUGETrackerRoutine, hUGETrackerRoutine,")
        self.emit("    hUGETrackerRoutine, hUGETrackerRoutine, hUGETrackerRoutine, hUGETrackerRoutine")
        self.emit("};")

    def _emit_gbdk_music_isr(self):
        """vm.music's WATCHDOG interrupt tick.

        The driver used to be advanced ONLY from the main loop (run()'s
        display-frame catch-up plus the stage-1 music_pump() calls), so a room
        load, a streamer refill or a box repaint - each of which blocks for
        many display frames inside one VM frame - stopped the song where it
        stood (longest silence 18 display frames after stage 1, and the
        residue was one unsplittable call no further pump can reach).

        THE INTERRUPT IS A SAFETY NET, NOT THE OWNER, and that is the whole
        design. The first cut gave the tick to the interrupt outright and
        folded the main-loop arm away - which put a ~2k-cycle handler at an
        arbitrary point in every frame, and an interrupt that runs long
        displaces every scanline-locked effect in the machine: measured in the
        converted parallax room as 40 of 300 frames whose band boundaries
        moved (several by 6 lines, i.e. a strip of the room drawn at another
        band's scroll - the reported "parallax scenes flicker and show tiles
        that are not part of the scene"), and with a box open in the RPG check conversion's
        shop the same delay landed on the window sprite-cut line. Moving it
        from the VBL chain to the timer helped and did not fix it (5 of 300),
        because the problem is not WHICH interrupt - it is that the tick runs
        at all while the raster is live.

        So the main loop keeps the tick, exactly as it always did and at
        exactly the same point in the frame (system.music_tick() from run()'s
        arm and from every stage-1 music_pump() site), and the interrupt only
        fires the driver when the loop has NOT serviced it for GBS_MDRV_LATE
        display frames - which during normal play is never, and during a
        blocking load is exactly what was missing.

        Everything is paced from ONE owner (gbs_mdrv_last), so the two callers
        cannot double-tick, and gbs_mdrv_busy (test-and-set inside a CRITICAL
        of a few instructions) means an interrupt landing inside a main-loop
        tick is dropped rather than re-entering the driver.

        The rest of the safety model:
        * BANK NEUTRALITY: the tick lands on either the seam stub's BANKED
          trampoline (code_banks) or the bank-neutrality wrapper a
          streamed-seam reader gets - both restore the interrupted code's
          mapped bank. The explicit save/restore here is belt and braces on
          top (the hUGE ISR's own discipline).
        * THE HOLD LATCH (gbs_mdrv_hold): the driver's main-loop mutators
          (music.play; the SMS/GG beep's PSG latch/data pair) raise it so the
          ISR cannot tick half-initialized state or split a two-byte PSG
          frequency write. A held tick is DROPPED, not deferred.
        * The watchdog still NESTS on the GB family (enable_interrupts()
          before it does anything), so even a rare late tick cannot hold off
          the LYC band chain - the gbs_huge_isr rule.
        """
        gb = bool(self.caps.get('has_gb_regs'))
        self.emit("/* vm.music's tick (6.6 stage 2). The MAIN LOOP owns it, at the same")
        self.emit("   point in the frame it always did; the interrupt below is a WATCHDOG")
        self.emit("   that fires the driver only when the loop has not serviced it for")
        self.emit("   GBS_MDRV_LATE display frames - a blocking room load / box repaint,")
        self.emit("   never normal play. An interrupt that ticks while the raster is live")
        self.emit("   displaces every scanline-locked effect in the machine (the parallax")
        self.emit("   bands, the box's sprite cut), which is what the first cut did. */")
        self.emit("#define GBS_MDRV_LATE 4      /* display frames of slack; 4 = ~66 ms */")
        self.emit("static void (*gbs_mdrv_tick)(void);")
        self.emit("static uint8_t gbs_mdrv_last;    /* the ONE pacing owner */")
        self.emit("static volatile uint8_t gbs_mdrv_busy;")
        self.emit("volatile uint8_t gbs_mdrv_hold;  /* main-loop mutator latch: stand down */")
        self.emit("static void gbs_mdrv_pump(uint8_t least) NONBANKED {")
        self.emit("    uint8_t fd = 0, saved, go = 0;")
        self.emit("    if (gbs_mdrv_hold || !gbs_mdrv_tick) return;")
        self.emit("    /* Test-and-set together, or the interrupt and the main loop can both")
        self.emit("       read the same elapsed count and tick it twice. A few instructions")
        self.emit("       with IME off cannot displace a scanline. */")
        self.emit("    CRITICAL {")
        self.emit("        fd = (uint8_t)sys_time - gbs_mdrv_last;")
        self.emit("        if (fd >= least && !gbs_mdrv_busy) {")
        self.emit("            if (fd > 8) fd = 8;  /* the MUSIC_CATCHUP_MAX rule: a long")
        self.emit("                                    block loses time, never fast-forwards */")
        self.emit("            gbs_mdrv_last = (uint8_t)sys_time;")
        self.emit("            gbs_mdrv_busy = 1;")
        self.emit("            go = 1;")
        self.emit("        }")
        self.emit("    }")
        self.emit("    if (!go) return;")
        self.emit("    saved = CURRENT_BANK;")
        self.emit("    while (fd--) gbs_mdrv_tick();")
        self.emit("    SWITCH_ROM(saved);")
        self.emit("    gbs_mdrv_busy = 0;")
        self.emit("}")
        self.emit("/* The MAIN LOOP's tick: run()'s per-frame arm and every music_pump()")
        self.emit("   site. `least` 1 = whatever has elapsed, which is what the mosaik-side")
        self.emit("   catch-up did before this - same place in the frame, same pacing. */")
        self.emit("void gbs_music_tick(void) NONBANKED { gbs_mdrv_pump(1); }")
        self.emit("void gbs_mdrv_isr(void) NONBANKED {")
        if gb:
            self.emit("    /* Nest before doing anything: even this rare tick must not hold")
            self.emit("       off the LYC band chain (the gbs_huge_isr rule). */")
            self.emit("    enable_interrupts();")
            self.emit("    gbs_mdrv_pump(GBS_MDRV_LATE);")
            self.emit("    disable_interrupts();        /* the dispatcher's invariant */")
        else:
            self.emit("    gbs_mdrv_pump(GBS_MDRV_LATE);")
        self.emit("}")
        self.emit("void gbs_music_isr_wire(void (*fn)(void)) {")
        self.emit("    static uint8_t wired = 0;")
        self.emit("    gbs_mdrv_tick = fn;          /* set BEFORE arming: it may fire at once */")
        self.emit("    if (wired) return;           /* arming twice would double the tempo */")
        self.emit("    wired = 1;")
        if gb:
            # The TIMER, not the VBL chain: the wire runs at boot, so a
            # VBL-chained handler runs FIRST in the chain - ahead of the
            # parallax VBL commit and the box sprite-cut's restore, which is
            # 10 scanlines of budget it does not own. hUGE's own home.
            self.emit("    CRITICAL { %s(gbs_mdrv_isr); }" % self._gbdk_timer_installer())
            self.emit("    /* 16 Hz, NOT hUGE's 64: this interrupt POLLS (it acts only when")
            self.emit("       the loop is GBS_MDRV_LATE frames behind), so its rate is how")
            self.emit("       fast a stall is noticed, not the tempo. Measured on the")
            self.emit("       converted parallax room, band-boundary jitter per 300 frames:")
            self.emit("       64 Hz = 3, 16 Hz = 0 (= the pre-feature baseline), while the")
            self.emit("       worst music gap only moves 4 -> 6 display frames. */")
            self.emit("    TMA_REG = 0x00u;             /* 4096/256 = 16 Hz */")
            self.emit("    TAC_REG = TACF_START | TACF_4KHZ;")
            # OR the mask, never assign (the hUGE rule): the box sprite cut
            # owns VBL/LCD in many programs.
            self.emit("    set_interrupts(IE_REG | VBL_IFLAG | TIM_IFLAG);")
        else:
            # SMS/GG have no timer interrupt; the VBL chain is the one fixed
            # clock, and nothing scanline-critical rides it there (no scanline
            # parallax, no window sprite cut). set_interrupts talks to the VDP
            # and the frame interrupt is already on (sys_time counts it), so it
            # is not touched.
            self.emit("    CRITICAL { add_VBL(gbs_mdrv_isr); }")
        self.emit("}")
        self.emit("void gbs_music_hold(uint8_t on) { gbs_mdrv_hold = on; }")

    def _emit_gbdk_sound(self):
        """platform.sound for GBDK consoles: one square-wave beep channel.

        sound.beep(freq_hz, frames) starts a tone; the duration counts down in
        gbs_wait_vblank (60 ticks/s, so `frames` matches the wait_vblank pacing
        on every console; 0 = play until sound.stop()). The tone generator
        differs per family: Game Boy APU pulse channel 2 (the NR*_REG symbols
        resolve per console in GBDK's library -- the Mega Duck remap included),
        the SN76489 PSG on SMS/Game Gear, and APU pulse 1 on the NES.
        """
        mc = self.sound_beep2_used   # the OPT-IN second (music) voice is in play
        # hUGEDriver owns every channel, so an effect has to BORROW the one it
        # writes and give it back (see the arbitration in _emit_gbdk_huge). GB
        # family only, which is the only place native.huge compiles at all.
        huge = bool(getattr(self, "native_huge_imported", False)
                    and self.caps.get("has_gb_regs"))
        if mc:
            self.emit("/* platform.sound: a beep channel + a 2nd (music) voice. */")
        else:
            self.emit("/* platform.sound: one square-wave beep channel. */")
        self.emit("static uint16_t gbs_snd_frames = 0;")
        if mc:
            self.emit("static uint16_t gbs_snd_frames2 = 0;  /* 2nd-voice duration */")
        if self.caps['has_gb_regs']:
            # Mega Duck quirk: the volume-envelope registers (NR12/NR22/NR42)
            # have their nibbles swapped relative to the Game Boy.
            envelope = '0x0F' if self.platform == 'megaduck' else '0xF0'
            if mc:
                self.emit("/* Game Boy APU, pulse channel 2 (SFX / primary). 2nd voice in")
                self.emit("   use: silence THIS channel only (NRx2 upper 5 bits = 0 turns its")
                self.emit("   DAC off), NOT the whole APU -- else an ending SFX kills the music. */")
                self.emit("void gbs_sound_stop(void) { NR22_REG = 0x00; gbs_snd_frames = 0;%s }"
                      % (" gbs_huge_sfx_give(0x02);" if huge else ""))
            elif huge:
                # Same reasoning as the 2nd-voice arm above, for the same reason:
                # hUGEDriver owns the APU whether or not this program also uses
                # the 2nd voice, so an ending effect must silence its CHANNEL and
                # not power the APU down under the music.
                self.emit("/* Game Boy APU, pulse channel 2. Silence the CHANNEL (its DAC),")
                self.emit("   NOT the whole APU -- hUGEDriver is playing through it. */")
                self.emit("void gbs_sound_stop(void) { NR22_REG = 0x00; gbs_snd_frames = 0;"
                          " gbs_huge_sfx_give(0x02); }")
            else:
                self.emit("/* Game Boy APU, pulse channel 2 (no sweep). Powering the APU off")
                self.emit("   clears every register, so stop() is a single write. */")
                self.emit("void gbs_sound_stop(void) { NR52_REG = 0x00; gbs_snd_frames = 0; }")
            self.emit("void gbs_sound_beep(uint16_t freq, uint16_t frames) {")
            self.emit("    uint16_t period;")
            self.emit("    if (freq < 64) freq = 64;  /* APU floor: period must be >= 0 */")
            self.emit("    period = (uint16_t)(2048 - (uint16_t)(131072UL / freq));")
            self.emit("    NR52_REG = 0x80;  /* APU on */")
            self.emit("    NR51_REG = 0xFF;  /* route every channel left + right */")
            self.emit("    NR50_REG = 0x77;  /* master volume max */")
            self.emit("    NR21_REG = 0x80;  /* 50% duty */")
            self.emit("    NR22_REG = %s;  /* full volume, no envelope */" % envelope)
            self.emit("    NR23_REG = (uint8_t)period;")
            self.emit("    NR24_REG = 0x80 | (uint8_t)(period >> 8);  /* trigger */")
            self.emit("    gbs_snd_frames = frames;")
            if huge:
                self.emit("    gbs_huge_sfx_take(0x02);  /* pulse 2 = hUGE CH2 */")
            self.emit("}")
            if mc:
                self.emit("/* Game Boy APU, pulse channel 1 (music / 2nd voice, sweep off). */")
                self.emit("void gbs_sound_stop2(void) { NR12_REG = 0x00; gbs_snd_frames2 = 0;%s }"
                          % (" gbs_huge_sfx_give(0x01);" if huge else ""))
                self.emit("void gbs_sound_beep2(uint16_t freq, uint16_t frames) {")
                self.emit("    uint16_t period;")
                self.emit("    if (freq < 64) freq = 64;")
                self.emit("    period = (uint16_t)(2048 - (uint16_t)(131072UL / freq));")
                self.emit("    NR52_REG = 0x80; NR51_REG = 0xFF; NR50_REG = 0x77;")
                self.emit("    NR10_REG = 0x00;  /* sweep off */")
                self.emit("    NR11_REG = 0x80;  /* 50% duty */")
                self.emit("    NR12_REG = %s;" % envelope)
                self.emit("    NR13_REG = (uint8_t)period;")
                self.emit("    NR14_REG = 0x80 | (uint8_t)(period >> 8);  /* trigger */")
                self.emit("    gbs_snd_frames2 = frames;")
                if huge:
                    self.emit("    gbs_huge_sfx_take(0x01);  /* pulse 1 = hUGE CH1 */")
                self.emit("}")
        elif self.platform in ('sms', 'gamegear'):
            # SN76489 tone channels are independent + stop is per-channel attenuation,
            # so the primary stop is the same whether or not the 2nd voice is used.
            self.emit("/* SN76489 PSG, tone channel 0 (latch/data bytes on the PSG port). */")
            self.emit("void gbs_sound_stop(void) {")
            self.emit("    PSG = PSG_LATCH | PSG_CH0 | PSG_VOLUME | 0x0F;  /* attenuation max */")
            self.emit("    gbs_snd_frames = 0;")
            self.emit("}")
            if self.music_isr_used:
                # A PSG FREQUENCY IS A LATCH/DATA PAIR, and the vm.music VBL
                # tick (6.6 stage 2) can fire between the two bytes - the data
                # byte would then pair with whatever register the ISR last
                # latched (a one-frame wrong-pitch blip on the music). Raise
                # the ISR's stand-down latch around the pair; a tick that
                # lands inside is dropped, which is inaudible.
                self.emit("extern volatile uint8_t gbs_mdrv_hold;")
            self.emit("void gbs_sound_beep(uint16_t freq, uint16_t frames) {")
            self.emit("    uint16_t divider;")
            self.emit("    if (freq < 110) freq = 110;  /* divider must fit 10 bits */")
            self.emit("    divider = (uint16_t)(111861UL / freq);  /* 3.579545 MHz / 32 / f */")
            if self.platform == 'gamegear':
                self.emit("    GG_SOUND_PAN = 0xFF;  /* all channels to both ears */")
            if self.music_isr_used:
                self.emit("    gbs_mdrv_hold = 1;  /* the latch/data pair must not be split */")
            self.emit("    PSG = (uint8_t)(PSG_LATCH | PSG_CH0 | (divider & 0x0F));")
            self.emit("    PSG = (uint8_t)((divider >> 4) & 0x3F);")
            if self.music_isr_used:
                self.emit("    gbs_mdrv_hold = 0;")
            self.emit("    PSG = PSG_LATCH | PSG_CH0 | PSG_VOLUME | 0x00;  /* attenuation 0 = loudest */")
            self.emit("    gbs_snd_frames = frames;")
            self.emit("}")
            if mc:
                self.emit("/* SN76489 PSG, tone channel 1 (music / 2nd voice). */")
                self.emit("void gbs_sound_stop2(void) {")
                self.emit("    PSG = PSG_LATCH | PSG_CH1 | PSG_VOLUME | 0x0F;")
                self.emit("    gbs_snd_frames2 = 0;")
                self.emit("}")
                self.emit("void gbs_sound_beep2(uint16_t freq, uint16_t frames) {")
                self.emit("    uint16_t divider;")
                self.emit("    if (freq < 110) freq = 110;")
                self.emit("    divider = (uint16_t)(111861UL / freq);")
                if self.music_isr_used:
                    self.emit("    gbs_mdrv_hold = 1;  /* see gbs_sound_beep */")
                self.emit("    PSG = (uint8_t)(PSG_LATCH | PSG_CH1 | (divider & 0x0F));")
                self.emit("    PSG = (uint8_t)((divider >> 4) & 0x3F);")
                if self.music_isr_used:
                    self.emit("    gbs_mdrv_hold = 0;")
                self.emit("    PSG = PSG_LATCH | PSG_CH1 | PSG_VOLUME | 0x00;")
                self.emit("    gbs_snd_frames2 = frames;")
                self.emit("}")
        else:
            # NES: the beep enables pulse 1 via 0x4015; with a 2nd voice, enable BOTH
            # pulses (else an SFX 0x4015 write would disable the music pulse) and stop
            # per-channel by zeroing that pulse's volume, not clearing 0x4015.
            if mc:
                self.emit("/* NES APU, pulse channel 1 (SFX / primary). */")
            else:
                self.emit("/* NES APU, pulse channel 1. */")
            self.emit("void gbs_sound_stop(void) {")
            if mc:
                self.emit("    (*(volatile uint8_t *)0x4000) = 0x30;  /* pulse 1 volume 0 */")
            else:
                self.emit("    (*(volatile uint8_t *)0x4015) = 0x00;  /* silence all channels */")
            self.emit("    gbs_snd_frames = 0;")
            self.emit("}")
            self.emit("void gbs_sound_beep(uint16_t freq, uint16_t frames) {")
            self.emit("    uint16_t timer;")
            self.emit("    if (freq < 55) freq = 55;  /* timer must fit 11 bits */")
            self.emit("    timer = (uint16_t)(111861UL / freq) - 1;  /* 1.789773 MHz / 16 / f */")
            if mc:
                self.emit("    (*(volatile uint8_t *)0x4015) = 0x03;  /* enable both pulses */")
            else:
                self.emit("    (*(volatile uint8_t *)0x4015) = 0x01;  /* enable pulse 1 */")
            self.emit("    (*(volatile uint8_t *)0x4000) = 0xBF;  /* 50% duty, no length, vol 15 */")
            self.emit("    (*(volatile uint8_t *)0x4001) = 0x08;  /* sweep off (negate: no mute) */")
            self.emit("    (*(volatile uint8_t *)0x4002) = (uint8_t)timer;")
            self.emit("    (*(volatile uint8_t *)0x4003) = (uint8_t)((timer >> 8) & 0x07);")
            self.emit("    gbs_snd_frames = frames;")
            self.emit("}")
            if mc:
                self.emit("/* NES APU, pulse channel 2 (music / 2nd voice). */")
                self.emit("void gbs_sound_stop2(void) {")
                self.emit("    (*(volatile uint8_t *)0x4004) = 0x30;  /* pulse 2 volume 0 */")
                self.emit("    gbs_snd_frames2 = 0;")
                self.emit("}")
                self.emit("void gbs_sound_beep2(uint16_t freq, uint16_t frames) {")
                self.emit("    uint16_t timer;")
                self.emit("    if (freq < 55) freq = 55;")
                self.emit("    timer = (uint16_t)(111861UL / freq) - 1;")
                self.emit("    (*(volatile uint8_t *)0x4015) = 0x03;  /* both pulses enabled */")
                self.emit("    (*(volatile uint8_t *)0x4004) = 0xBF;")
                self.emit("    (*(volatile uint8_t *)0x4005) = 0x08;")
                self.emit("    (*(volatile uint8_t *)0x4006) = (uint8_t)timer;")
                self.emit("    (*(volatile uint8_t *)0x4007) = (uint8_t)((timer >> 8) & 0x07);")
                self.emit("    gbs_snd_frames2 = frames;")
                self.emit("}")
        self.emit("/* wait_vblank with the beep-duration countdown (60 ticks/s). */")
        self.emit("void gbs_wait_vblank(void) {")
        self.emit("    vsync();")
        if self.bkg_move_used and self.platform in ('sms', 'gamegear'):
            # SMS/GG: commit the scroll shadow at the START of v-blank, the
            # same moment GBDK's ISR copies the shadow OAM - so the background
            # and the sprites drawn against it change on the ONE clock (see
            # _emit_gbdk_scroll_move).
            self.emit("    /* Commit the scroll shadow in v-blank, so the background")
            self.emit("       and the sprite table change on the same frame (the")
            self.emit("       actors-slide-while-walking report). */")
            if self.raster_used:
                self.emit("    if (!gbs_rs_on)")
            if self._view_real():
                # video.set_view: the shadow is in ROOM-VIEW space; the
                # letterbox offset is applied here, at the one commit.
                self.emit("    move_bkg(GBS_VIEW_SCX(gbs_scr_shx), GBS_VIEW_SCY(gbs_scr_shy));")
            else:
                self.emit("    move_bkg(gbs_scr_shx, gbs_scr_shy);")
        if self.bkg_move_used and self.caps.get('has_gb_regs'):
            # THE SCROLL COMMIT: vsync() returns at the START of v-blank, so
            # the register write here can never land mid-frame - the whole
            # point of the shadow (see _emit_gbdk_scroll_move). While parallax
            # bands are armed the LYC chain owns SCX/SCY and this stands down,
            # like every other writer (the gbs_px_move arbitration this
            # replaces).
            self.emit("    /* Commit the scroll shadow at the start of v-blank - a")
            self.emit("       mid-frame register write shears the picture at whatever")
            self.emit("       scanline the game loop had reached (see gbs_scroll_move). */")
            if self.parallax_used:
                self.emit("    if (!gbs_px_n) {")
                self.emit("        SCX_REG = gbs_scr_shx;")
                self.emit("        SCY_REG = gbs_scr_shy;")
                self.emit("    }")
            elif self.raster_used:
                # The scanline table owns SCX/SCY while it is armed.
                self.emit("    if (!gbs_rs_on) {")
                self.emit("        SCX_REG = gbs_scr_shx;")
                self.emit("        SCY_REG = gbs_scr_shy;")
                self.emit("    }")
            else:
                self.emit("    SCX_REG = gbs_scr_shx;")
                self.emit("    SCY_REG = gbs_scr_shy;")
        self.emit("    if (gbs_snd_frames && --gbs_snd_frames == 0) gbs_sound_stop();")
        if mc:
            self.emit("    if (gbs_snd_frames2 && --gbs_snd_frames2 == 0) gbs_sound_stop2();")
        self.emit("}")
        if self.frames_used:
            self.emit("/* system.frames(): the DISPLAY-frame counter. `sys_time` is")
            self.emit("   advanced by GBDK's VBL interrupt, so it keeps counting at the")
            self.emit("   LCD's 59.7 Hz however long a game frame takes - which is the")
            self.emit("   whole point. A caller compares two reads to learn how many")
            self.emit("   display frames its last frame really spanned. u8 is enough:")
            self.emit("   the deltas it is read for are single digits, and the wrap is")
            self.emit("   exact under unsigned subtraction. */")
            self.emit("uint8_t gbs_frames(void) { return (uint8_t)sys_time; }")
