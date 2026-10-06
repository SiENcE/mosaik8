"""A minimal ctypes libretro frontend: video frames, audio capture, joypad, RAM.

    sys.path.insert(0, os.path.join(<engine>, "emu", "libretro"))
    from retro import Core
    core = Core(".../genesis_plus_gx_libretro.dll", "game.gg", record_audio=True)
    core.press("start"); core.run(10); core.press()
    core.image(), core.ram(), core.poke(offset, value), core.audio

The one copy the projects' own checks and tools import (their `verify.py`,
tours, profilers), for the SMS / Game Gear / PC Engine cores beside
this file. Unlike `run_lynx.py` (a one-shot command line on libretro.py) it is
stepped frame by frame from Python, so a test can react to what the game
does. No dependencies beyond the core library and Pillow.

A core is a shared library loaded ONCE per process: `close()` unloads the
game but not the library, so run one ROM per process.
"""
import ctypes as C
import os

ENV_SET_PIXEL_FORMAT = 10
ENV_GET_SYSTEM_DIRECTORY = 9
ENV_GET_SAVE_DIRECTORY = 31
ENV_GET_VARIABLE = 15
ENV_GET_VARIABLE_UPDATE = 17
ENV_GET_LOG_INTERFACE = 27
ENV_GET_CAN_DUPE = 3

BUTTONS = {"B": 0, "Y": 1, "SELECT": 2, "START": 3, "UP": 4, "DOWN": 5,
           "LEFT": 6, "RIGHT": 7, "A": 8, "X": 9}


class GameInfo(C.Structure):
    _fields_ = [("path", C.c_char_p), ("data", C.c_void_p),
                ("size", C.c_size_t), ("meta", C.c_char_p)]


class Geometry(C.Structure):
    _fields_ = [("base_width", C.c_uint), ("base_height", C.c_uint),
                ("max_width", C.c_uint), ("max_height", C.c_uint),
                ("aspect", C.c_float)]


class Timing(C.Structure):
    _fields_ = [("fps", C.c_double), ("sample_rate", C.c_double)]


class AVInfo(C.Structure):
    _fields_ = [("geometry", Geometry), ("timing", Timing)]


class Variable(C.Structure):
    _fields_ = [("key", C.c_char_p), ("value", C.c_char_p)]


ENV_CB = C.CFUNCTYPE(C.c_bool, C.c_uint, C.c_void_p)
VIDEO_CB = C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint, C.c_size_t)
AUDIO_CB = C.CFUNCTYPE(None, C.c_int16, C.c_int16)
AUDIO_BATCH_CB = C.CFUNCTYPE(C.c_size_t, C.c_void_p, C.c_size_t)
POLL_CB = C.CFUNCTYPE(None)
STATE_CB = C.CFUNCTYPE(C.c_int16, C.c_uint, C.c_uint, C.c_uint, C.c_uint)


class Core:
    def __init__(self, core_path, rom_path, variables=None, record_audio=False):
        self.lib = C.CDLL(core_path)
        self.fmt = 0            # 0 = 0RGB1555, 1 = XRGB8888, 2 = RGB565
        self.frame = None       # (bytes, w, h, pitch)
        self.pad = set()
        self.audio = bytearray() if record_audio else None
        self.vars = {k.encode(): v.encode() for k, v in (variables or {}).items()}
        self._sysdir = C.c_char_p(os.path.dirname(core_path).encode())
        self._keep = []

        self._env = ENV_CB(self._environment)
        self._vid = VIDEO_CB(self._video)
        self._aud = AUDIO_CB(self._audio_sample)
        self._audb = AUDIO_BATCH_CB(self._audio_batch)
        self._poll = POLL_CB(lambda: None)
        self._state = STATE_CB(self._input_state)

        L = self.lib
        L.retro_set_environment(self._env)
        L.retro_set_video_refresh(self._vid)
        L.retro_set_audio_sample(self._aud)
        L.retro_set_audio_sample_batch(self._audb)
        L.retro_set_input_poll(self._poll)
        L.retro_set_input_state(self._state)
        L.retro_init()
        with open(rom_path, "rb") as f:
            self._rom = f.read()
        buf = C.create_string_buffer(self._rom, len(self._rom))
        self._keep.append(buf)
        info = GameInfo(rom_path.encode(), C.cast(buf, C.c_void_p), len(self._rom), None)
        L.retro_load_game.argtypes = [C.POINTER(GameInfo)]
        L.retro_load_game.restype = C.c_bool
        if not L.retro_load_game(C.byref(info)):
            raise RuntimeError("core refused " + rom_path)
        av = AVInfo()
        L.retro_get_system_av_info.argtypes = [C.POINTER(AVInfo)]
        L.retro_get_system_av_info(C.byref(av))
        self.fps = av.timing.fps
        self.sample_rate = av.timing.sample_rate

    def _environment(self, cmd, data):
        cmd &= 0xFFFF
        if cmd == ENV_SET_PIXEL_FORMAT:
            self.fmt = C.cast(data, C.POINTER(C.c_int))[0]
            return True
        if cmd in (ENV_GET_SYSTEM_DIRECTORY, ENV_GET_SAVE_DIRECTORY):
            C.cast(data, C.POINTER(C.c_char_p))[0] = self._sysdir
            return True
        if cmd == ENV_GET_VARIABLE:
            v = C.cast(data, C.POINTER(Variable))
            val = self.vars.get(v[0].key)
            if val is None:
                return False
            v[0].value = val
            return True
        if cmd == ENV_GET_VARIABLE_UPDATE:
            C.cast(data, C.POINTER(C.c_bool))[0] = False
            return True
        if cmd == ENV_GET_CAN_DUPE:
            C.cast(data, C.POINTER(C.c_bool))[0] = True
            return True
        return False

    def _video(self, data, w, h, pitch):
        if data:
            self.frame = (C.string_at(data, pitch * h), w, h, pitch)

    def _audio_sample(self, l, r):
        if self.audio is not None:
            self.audio += int(l).to_bytes(2, "little", signed=True)
            self.audio += int(r).to_bytes(2, "little", signed=True)

    def _audio_batch(self, data, frames):
        if self.audio is not None:
            self.audio += C.string_at(data, frames * 4)
        return frames

    def _input_state(self, port, device, index, ident):
        if port == 0 and device == 1:
            return 1 if ident in self.pad else 0
        return 0

    def press(self, *names):
        self.pad = {BUTTONS[n.upper()] for n in names}

    def run(self, n=1):
        for _ in range(n):
            self.lib.retro_run()

    def image(self):
        from PIL import Image
        data, w, h, pitch = self.frame
        if self.fmt == 1:
            img = Image.frombuffer("RGB", (w, h), data, "raw", "BGRX", pitch, 1)
        elif self.fmt == 2:
            img = Image.frombuffer("RGB", (w, h), data, "raw", "BGR;16", pitch, 1)
        else:
            img = Image.frombuffer("RGB", (w, h), data, "raw", "BGR;15", pitch, 1)
        return img.copy()

    def ram(self):
        L = self.lib
        L.retro_get_memory_data.restype = C.c_void_p
        L.retro_get_memory_size.restype = C.c_size_t
        n = L.retro_get_memory_size(2)
        p = L.retro_get_memory_data(2)
        return C.string_at(p, n) if p and n else b""

    def poke(self, offset, value):
        """Write one byte of system RAM."""
        L = self.lib
        L.retro_get_memory_data.restype = C.c_void_p
        p = L.retro_get_memory_data(2)
        C.memset(p + offset, value, 1)

    def close(self):
        self.lib.retro_unload_game()
        self.lib.retro_deinit()
