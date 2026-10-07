"""Cc65Backend <concern> methods, mixed into Cc65Backend.

Split out of the former monolithic cc65.py; a plain mixin class - methods run
against the full CodeGenerator instance (self.emit, self.caps, ...)."""


class Cc65SoundMixin:
    def _emit_cc65_sound(self, prof):
        """platform.sound for cc65 consoles: one square-wave beep channel.

        Same contract as the GBDK backend: sound.beep(freq_hz, frames) starts
        a tone and the duration counts down in gbs_present (wait_vblank, 60
        ticks/s; 0 = play until sound.stop()). The generator is the Mikey
        audio chip on the Lynx and the PSG on the PC Engine.
        """
        if not self.caps['has_sound']:
            return
        mc = self.sound_beep2_used   # the OPT-IN second (music) voice is in play
        if mc:
            self.emit("/* platform.sound: a beep channel + a 2nd (music) voice (counted down")
        else:
            self.emit("/* platform.sound: one square-wave beep channel (counted down")
        self.emit("   in gbs_present, 60 ticks/s). */")
        self.emit("static uint16_t gbs_snd_frames = 0;")
        if mc:
            self.emit("static uint16_t gbs_snd_frames2 = 0;  /* 2nd-voice duration */")
        if prof.get('sound') == 'mikey':
            self.emit("/* Mikey audio channel A. Feedback tap 0 through the inverting XOR")
            self.emit("   makes the shift register alternate on every timer underflow ->")
            self.emit("   a square wave at clock / (2 * (backup + 1)). */")
            self.emit("void gbs_sound_stop(void) {")
            self.emit("    MIKEY.channel_a.control = 0;")
            self.emit("    MIKEY.channel_a.volume = 0;")
            self.emit("    gbs_snd_frames = 0;")
            self.emit("}")
            self.emit("void gbs_sound_beep(uint16_t freq, uint16_t frames) {")
            self.emit("    uint32_t half;       /* half-period in 1 MHz base-clock ticks */")
            self.emit("    uint8_t sel = AUD_1;")
            self.emit("    if (freq == 0) freq = 1;")
            self.emit("    half = 500000UL / freq;")
            self.emit("    while (half > 256 && sel < AUD_64) { half >>= 1; ++sel; }")
            self.emit("    if (half) --half;    /* timer counts backup+1 ticks */")
            self.emit("    MIKEY.channel_a.volume = 0x7F;")
            self.emit("    MIKEY.channel_a.feedback = 0x01;  /* tap 0 only */")
            self.emit("    MIKEY.channel_a.dac = 0;")
            self.emit("    MIKEY.channel_a.shiftlo = 0x01;   /* seed the shift register */")
            self.emit("    MIKEY.channel_a.other = 0;")
            self.emit("    MIKEY.channel_a.reload = (uint8_t)half;")
            self.emit("    MIKEY.channel_a.count = (uint8_t)half;")
            self.emit("    MIKEY.channel_a.control = (uint8_t)(ENABLE_RELOAD | ENABLE_COUNT | sel);")
            self.emit("    MIKEY.mstereo = 0;   /* Lynx II: all channels to both ears */")
            self.emit("    gbs_snd_frames = frames;")
            self.emit("}")
            if mc:
                self.emit("/* Mikey audio channel B (music / 2nd voice). Independent of")
                self.emit("   channel A, so the stop is per-channel. NOTE: native.lynx.jingle")
                self.emit("   also uses channel B -- don't combine VM music with jingle. */")
                self.emit("void gbs_sound_stop2(void) {")
                self.emit("    MIKEY.channel_b.control = 0;")
                self.emit("    MIKEY.channel_b.volume = 0;")
                self.emit("    gbs_snd_frames2 = 0;")
                self.emit("}")
                self.emit("void gbs_sound_beep2(uint16_t freq, uint16_t frames) {")
                self.emit("    uint32_t half;")
                self.emit("    uint8_t sel = AUD_1;")
                self.emit("    if (freq == 0) freq = 1;")
                self.emit("    half = 500000UL / freq;")
                self.emit("    while (half > 256 && sel < AUD_64) { half >>= 1; ++sel; }")
                self.emit("    if (half) --half;")
                self.emit("    MIKEY.channel_b.volume = 0x7F;")
                self.emit("    MIKEY.channel_b.feedback = 0x01;")
                self.emit("    MIKEY.channel_b.dac = 0;")
                self.emit("    MIKEY.channel_b.shiftlo = 0x01;")
                self.emit("    MIKEY.channel_b.other = 0;")
                self.emit("    MIKEY.channel_b.reload = (uint8_t)half;")
                self.emit("    MIKEY.channel_b.count = (uint8_t)half;")
                self.emit("    MIKEY.channel_b.control = (uint8_t)(ENABLE_RELOAD | ENABLE_COUNT | sel);")
                self.emit("    MIKEY.mstereo = 0;")
                self.emit("    gbs_snd_frames2 = frames;")
                self.emit("}")
        else:  # 'pce_psg'
            self.emit("/* PC Engine PSG channel 0: load a square waveform once, then key on")
            self.emit("   with a 12-bit frequency divider (3.579545 MHz / 32 / f). The cc65")
            self.emit("   startup silences the PSG master balance; beep restores it. */")
            self.emit("#define GBS_PSG(reg) (*(volatile uint8_t *)(0x0800u + (reg)))")
            # A program that asks sound.busy() shares PSG 0 with a music driver
            # (vm.music's borrow), which loads its OWN waveform there: the
            # load-once cache would then play the next beep in the song's
            # timbre (measured: a 12.5 % instrument's square, 35 % quieter), so
            # the beep loads its square every time. Unchanged otherwise.
            reload = getattr(self, "sound_busy_used", False)
            if not reload:
                self.emit("static uint8_t gbs_psg_wave_loaded = 0;")
            self.emit("void gbs_sound_stop(void) {")
            self.emit("    GBS_PSG(0) = 0;  /* select channel 0 */")
            self.emit("    GBS_PSG(4) = 0;  /* key off, volume 0 */")
            self.emit("    gbs_snd_frames = 0;")
            self.emit("}")
            self.emit("void gbs_sound_beep(uint16_t freq, uint16_t frames) {")
            self.emit("    uint16_t divider;")
            self.emit("    uint8_t i;")
            self.emit("    if (freq < 28) freq = 28;  /* divider must fit 12 bits */")
            self.emit("    divider = (uint16_t)(111861UL / freq);")
            self.emit("    GBS_PSG(1) = 0xFF;  /* main volume left + right */")
            self.emit("    GBS_PSG(0) = 0;     /* select channel 0 */")
            if reload:
                self.emit("    GBS_PSG(4) = 0x40;  /* DDA on... */")
                self.emit("    GBS_PSG(4) = 0x00;  /* ...and off: reset the waveform index */")
                self.emit("    for (i = 0; i < 32; ++i) GBS_PSG(6) = (i < 16) ? 0x00 : 0x1F;")
            else:
                self.emit("    if (!gbs_psg_wave_loaded) {")
                self.emit("        GBS_PSG(4) = 0x40;  /* DDA on... */")
                self.emit("        GBS_PSG(4) = 0x00;  /* ...and off: reset the waveform index */")
                self.emit("        for (i = 0; i < 32; ++i) GBS_PSG(6) = (i < 16) ? 0x00 : 0x1F;")
                self.emit("        gbs_psg_wave_loaded = 1;")
                self.emit("    }")
            self.emit("    GBS_PSG(5) = 0xFF;  /* channel balance left + right */")
            self.emit("    GBS_PSG(2) = (uint8_t)divider;")
            self.emit("    GBS_PSG(3) = (uint8_t)((divider >> 8) & 0x0F);")
            self.emit("    GBS_PSG(4) = 0x9F;  /* key on, volume max */")
            self.emit("    gbs_snd_frames = frames;")
            self.emit("}")
            if mc:
                self.emit("/* PC Engine PSG channel 1 (music / 2nd voice). */")
                self.emit("static uint8_t gbs_psg_wave_loaded2 = 0;")
                self.emit("void gbs_sound_stop2(void) {")
                self.emit("    GBS_PSG(0) = 1;  /* select channel 1 */")
                self.emit("    GBS_PSG(4) = 0;  /* key off, volume 0 */")
                self.emit("    gbs_snd_frames2 = 0;")
                self.emit("}")
                self.emit("void gbs_sound_beep2(uint16_t freq, uint16_t frames) {")
                self.emit("    uint16_t divider;")
                self.emit("    uint8_t i;")
                self.emit("    if (freq < 28) freq = 28;")
                self.emit("    divider = (uint16_t)(111861UL / freq);")
                self.emit("    GBS_PSG(1) = 0xFF;")
                self.emit("    GBS_PSG(0) = 1;     /* select channel 1 */")
                self.emit("    if (!gbs_psg_wave_loaded2) {")
                self.emit("        GBS_PSG(4) = 0x40;")
                self.emit("        GBS_PSG(4) = 0x00;")
                self.emit("        for (i = 0; i < 32; ++i) GBS_PSG(6) = (i < 16) ? 0x00 : 0x1F;")
                self.emit("        gbs_psg_wave_loaded2 = 1;")
                self.emit("    }")
                self.emit("    GBS_PSG(5) = 0xFF;")
                self.emit("    GBS_PSG(2) = (uint8_t)divider;")
                self.emit("    GBS_PSG(3) = (uint8_t)((divider >> 8) & 0x0F);")
                self.emit("    GBS_PSG(4) = 0x9F;")
                self.emit("    gbs_snd_frames2 = frames;")
                self.emit("}")
