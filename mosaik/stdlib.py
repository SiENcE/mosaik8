"""Standard-library module registry.

The standard library is built into the compiler: each call's per-backend
lowering lives in the codegen STDLIB_CALLS_* maps (mosaik/codegen/), and its
public signature is registered with the TypeChecker. This set is just the
module *names* `import` recognises as stdlib (so `import "platform.video"`
resolves, while an unknown import is an error). Keep it in sync with the
STDLIB_CALLS_* maps and the spec's §6 reference.
"""

STDLIB_MODULE_NAMES = frozenset({
    'platform.video', 'platform.input', 'platform.hardware', 'platform.system',
    'platform.sound',
    # Battery-backed persistent storage (save.enable/disable/write_u8/read_u8).
    # Codegen-lowered to gbs_save_* helpers, honest-off via has_save (GB family
    # only in Stage 0). See docs/mosaik_lang_spec.md + sram-save-plan.md.
    'platform.save',
    # Asset residency seam (asset-streaming / large-game groundwork): an
    # `assets.use(id)` / `assets.ptr(id)` indirection so the *source* of an
    # asset's bytes becomes a per-console detail. On the directly-mapped consoles
    # use() is a no-op and ptr() is the bare const pointer -- byte-identical to
    # passing the array straight to a setter; the Lynx streaming lowering (Stage
    # B) loads from the cart and returns a RAM-cache pointer, same call site.
    # Codegen-lowered (no lib module): see the STDLIB_CALLS_* maps + _gen_call.
    'platform.assets',
    'graphics.sprite', 'graphics.bkg', 'graphics.window', 'graphics.text',
    'graphics.draw', 'graphics.palette',
    # Native-extension namespaces (the escape hatch): console-specific features
    # reached as `<console>.*`. They lower to real hardware on their console and
    # to a no-op everywhere else, so one source still builds on all nine.
    'native.lynx',
    # native.huge -- hUGEDriver, the hUGETracker playback driver, linked as a
    # prebuilt object (mosaik8/vendor/hugedriver). GB FAMILY ONLY and honest
    # about it: it is Game Boy APU assembly, so it is a compile error anywhere
    # else rather than a silent no-op (unlike native.lynx, whose verbs have a
    # meaningful "do nothing" -- a music driver that silently plays nothing is
    # the failure this feature exists to remove).
    'native.huge',
})


def stdlib_module_names() -> frozenset:
    """The set of module names `import` resolves to the standard library."""
    return STDLIB_MODULE_NAMES
