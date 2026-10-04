"""mosaik_vm.rooms.emit - `emit_rooms_mos`, the four sections in order."""
from .context import derive
from . import emit_dispatch, emit_load, emit_prelude, emit_start


def emit_rooms_mos(info):
    """The `rooms` module text from a world-shape dict (see generate_rooms).

    Emits load_room (paint + doors/triggers/entities + the scene-type handler
    pick, ONLY the used branches) + start(start_room) the fixed shell calls once.
    """
    c = derive(info)
    L = []
    emit_prelude.emit(c, L)     # header, imports, consts, probes
    emit_load.emit(c, L)        # load_room
    emit_dispatch.emit(c, L)    # the scene-type handler pick
    emit_start.emit(c, L)       # start()
    return "\n".join(L)
