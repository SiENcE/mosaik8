#!/usr/bin/env python3
"""VM8 toolchain tests (Stage 1): the event compiler + reference interpreter.

Covers, headless (no ROM):
  * the compiler lowers events -> the exact expected bytecode + offsets (golden);
  * the generated `scripts.mos` shape (fetch/entries/strings);
  * the debug map + .vms artifact;
  * RefVM semantics mirror lib/vm/core.mos: concurrent threads, waitable MOVE_TO,
    WAIT frame-counting, LOCK freezing other threads, the textbox open/dismiss,
    SET_CONST -> heap, start_thread, variable index assignment.

RefVM is the reference for vm.core: the Stage-0 spike ROM (PyBoy-verified) and
RefVM produce the same behaviour, so these fast headless checks stand in for the
slow ROM checks on every commit.

Split by topic (2026-08-26) into the tests/vmtest/ package - common.py holds
FAILS/check + the spike golden, one module per concern - and this file stays
the entry point run_all.py discovers; the call order in main() is unchanged.
"""

import os
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS)          # so `import vmtest` works run as a script

from vmtest.common import FAILS  # noqa: E402
from vmtest.compiler_tests import (  # noqa: F401
    test_golden_blob,
    test_artifacts,
    test_refvm_behaviour,
    test_wait_timing,
    test_negative_var,
    test_self_actor_operand,
    test_self_in_expression,
    test_project_file_roundtrip,)
from vmtest.expr_tests import (  # noqa: F401
    test_expressions,
    test_expr_variables_and_state,
    test_conditionals,
    test_menu,
    test_call_ret,
    test_player_state,
    test_rand,
    test_switch,
    test_engine_state_bridge,
    test_expr_params,)
from vmtest.actor_tests import (  # noqa: F401
    test_player_move_to_computed,
    test_fade,
    test_timers,
    test_actor_move_nonblocking,
    test_camera_pan,
    test_shake,
    test_shmup_scroll,
    test_scroll_bg,
    test_input_attach,
    test_projectiles,
    test_death_hook,
    test_collision_groups,
    test_actor_hp,
    test_actor_reactivate,
    test_script_driven_animation_and_bounce,
    test_stop_update_push_seed_clock,
    test_actor_emote,
    test_say_choose,
    test_change_scene,
    test_thread_lock_lifecycle,)
from vmtest.hardening_tests import (  # noqa: F401
    test_p2_hardening,
    test_p1_hardening,
    test_p0_hardening,
    test_save_load,
    test_review_fixes_2026_09,)
from vmtest.audio_tests import (  # noqa: F401
    test_sound,
    test_sound_library,
    test_music,
    test_music_play_from_song_thread,
    test_music_song,
    test_music_transport,
    test_glue,
    test_song_library,
    test_instrument_library,
    test_hw_psg,)
from vmtest.opcode_semantics_tests import ALL as OPCODE_SEMANTICS  # noqa: E402
from vmtest.isa_tests import (  # noqa: F401
    test_scene_type_emission,
    test_new_samples_compile,
    test_scaffold,
    test_vm8_spec_vectors,
    test_vm8_bitwise,
    test_vm8_threads,
    test_dispatch_lockstep_and_pruning,
    test_every_opcode_executes,)


def main():
    test_golden_blob()
    test_artifacts()
    test_refvm_behaviour()
    test_wait_timing()
    test_negative_var()
    test_self_actor_operand()
    test_self_in_expression()
    test_project_file_roundtrip()
    test_expressions()
    test_expr_variables_and_state()
    test_conditionals()
    test_menu()
    test_call_ret()
    test_player_state()
    test_rand()
    test_player_move_to_computed()
    test_fade()
    test_timers()
    test_actor_move_nonblocking()
    test_switch()
    test_engine_state_bridge()
    test_camera_pan()
    test_shake()
    test_shmup_scroll()
    test_scroll_bg()
    test_input_attach()
    test_projectiles()
    test_expr_params()
    test_death_hook()
    test_collision_groups()
    test_actor_hp()
    test_actor_reactivate()
    test_script_driven_animation_and_bounce()
    test_stop_update_push_seed_clock()
    test_actor_emote()
    test_say_choose()
    test_change_scene()
    test_thread_lock_lifecycle()
    test_p0_hardening()
    test_p1_hardening()
    test_p2_hardening()
    test_save_load()
    test_review_fixes_2026_09()
    test_sound()
    test_sound_library()
    test_music()
    test_music_play_from_song_thread()
    test_music_song()
    test_music_transport()
    test_glue()
    test_song_library()
    test_instrument_library()
    test_hw_psg()
    test_scene_type_emission()
    test_new_samples_compile()
    test_scaffold()
    test_vm8_spec_vectors()
    test_vm8_bitwise()
    test_vm8_threads()
    test_dispatch_lockstep_and_pruning()
    test_every_opcode_executes()
    for t in OPCODE_SEMANTICS:      # review V-11: one semantic check per opcode
        t()
    print()
    if FAILS:
        print("VM8 tests FAILED (%d):" % len(FAILS))
        for f in FAILS:
            print("  -", f)
        return 1
    print("All VM8 tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
