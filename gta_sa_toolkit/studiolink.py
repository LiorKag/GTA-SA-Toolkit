# GTA SA Toolkit - turns the built-in GTA SA Studio (studio/) on and off, and handles the separate
# "GTA SA Studio" add-on of earlier, private versions.
#
# Switches (add-on preferences): studio_on = everything of Studio (off: none of its classes, properties
# or tabs are registered); studio_<area> = one tab / group of sections (studio/corelink.AREAS).
#
# Old separate Studio still installed and turned on: the built-in one is NOT registered (both would
# register the same classes and properties), the old one keeps working with this core, and the panel
# and the preferences say to remove it, with a button that does the switch: its settings (timecyc
# list, own timecyc.dat, hidden finds, the stock timecyc numbers) are copied here, it's turned off and
# the built-in Studio is turned on. Its settings are also copied when a file loads while it's on, so
# they survive if it's removed by hand. Found by its package name ending in "gta_sa_studio", whatever
# repository it came from.
import os

import bpy
from bpy.app.handlers import persistent

OLD_NAME = "gta_sa_studio"
_S = {"on": False, "old": None}


def prefs():
    from . import library
    return library.prefs()


def old_studio():
    """Module name of the separate old Studio when it's turned on, else None."""
    for key in bpy.context.preferences.addons.keys():
        if key.split(".")[-1] == OLD_NAME:
            return key
    return None


def is_on():
    return _S["on"]


def old():
    return _S["old"]


# ----------------------------------------------------------------------------- on / off
def start():
    """Core's register(): the built-in Studio unless the switch is off or the old one is on."""
    _S["old"] = old_studio()
    if _S["old"]:
        copy_old_settings()
        print("[GTA SA Toolkit] the separate GTA SA Studio add-on is still on: the built-in Studio stays off "
              "until it's removed")
        return
    p = prefs()
    if p is None or p.studio_on:
        _register()


def stop():
    _unregister()


def _register():
    if _S["on"]:
        return
    from . import studio
    studio.register()
    _S["on"] = True


def _unregister():
    if not _S["on"]:
        return
    from . import studio
    try:
        studio.unregister()
    finally:
        _S["on"] = False


def switch_changed(_self=None, _context=None):
    """studio_on changed in the preferences."""
    if _S["old"]:
        return
    p = prefs()
    if p is not None and p.studio_on:
        _register()
    else:
        _unregister()


def areas_changed(_self=None, _context=None):
    """A studio_<area> switch changed: re-plug Studio's tabs and sections."""
    if _S["on"]:
        from .studio import corelink
        corelink.connect()


# ----------------------------------------------------------------------------- the old separate Studio
def copy_old_settings(force=False):
    """Copy the old Studio's preferences (timecyc list, own timecyc.dat, hidden finds) and its stock timecyc
    numbers into this add-on's, once (or again with force). True when something was copied."""
    p = prefs()
    key = _S["old"] or old_studio()
    ent = bpy.context.preferences.addons.get(key) if key else None
    op = getattr(ent, "preferences", None) if ent else None
    if p is None or op is None or (p.studio_prefs_copied and not force):
        return False
    have = {os.path.normcase(f.path) for f in p.timecyc_files}
    for f in getattr(op, "timecyc_files", []):
        if os.path.normcase(f.path) not in have:
            p.timecyc_files.add().path = f.path
    if getattr(op, "timecyc_path", "") and not p.timecyc_path:
        p.timecyc_path = op.timecyc_path
    hidden = [h for h in (p.timecyc_hidden + "\n" + getattr(op, "timecyc_hidden", "")).splitlines() if h.strip()]
    p.timecyc_hidden = "\n".join(dict.fromkeys(hidden))
    _copy_stock_numbers()
    p.studio_prefs_copied = True
    bpy.context.preferences.is_dirty = True
    print("[GTA SA Toolkit] copied the separate GTA SA Studio's settings (%d timecyc files)" % len(p.timecyc_files))
    return True


def _copy_stock_numbers():
    """The stock timecyc numbers the old Studio kept in its own user folder -> ours (same repository)."""
    if os.environ.get("GTATK_LIBRARY_DIR"):
        return                                         # tests keep them in their own folder
    try:
        mine = bpy.utils.extension_path_user(__package__, path="timecyc", create=True)
    except (ValueError, OSError):
        return
    dst = os.path.join(mine, "stock_timecyc.json")
    src = os.path.join(os.path.dirname(os.path.dirname(mine)), OLD_NAME, "timecyc", "stock_timecyc.json")
    if os.path.isfile(src) and not os.path.isfile(dst):
        import shutil
        shutil.copy2(src, dst)


def use_builtin():
    """The button: copy the old Studio's settings, turn it off, turn the built-in one on.
    Returns an error message or None."""
    import addon_utils
    key = _S["old"] or old_studio()
    if key:
        copy_old_settings()
        errors = []
        addon_utils.disable(key, default_set=True, handle_error=lambda e: errors.append(str(e)))
        if key in bpy.context.preferences.addons:
            return "The old GTA SA Studio couldn't be turned off: %s" % (errors[0] if errors else "unknown")
    _S["old"] = None
    p = prefs()
    if p is not None:
        p.studio_on = True                      # its update registers the built-in Studio
    _register()
    return None


@persistent
def _on_load(_dummy=None):
    if _S["old"]:
        try:
            copy_old_settings()
        except Exception as e:                 # noqa - never break opening a file
            print("[GTA SA Toolkit] copying the old Studio's settings failed: %s" % e)


class GTATK_OT_studio_use_builtin(bpy.types.Operator):
    bl_idname = "gtatk.studio_use_builtin"
    bl_label = "Use the Built-in Studio"
    bl_description = ("GTA SA Studio is now part of GTA SA Toolkit. Copy the old add-on's settings, turn it off "
                      "and turn on the built-in Studio (then remove the old one in Preferences > Get Extensions)")

    def execute(self, context):
        err = use_builtin()
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        self.report({'INFO'}, "Built-in GTA SA Studio is on. Remove the old one in Preferences > Get Extensions")
        return {'FINISHED'}


def draw_old_message(layout):
    """The message about the old separate Studio (panel + preferences); nothing when it's not there."""
    if not _S["old"]:
        return
    box = layout.box()
    col = box.column(align=True)
    col.label(text="GTA SA Studio is now part of", icon='ERROR')
    col.label(text="GTA SA Toolkit: remove the old", icon='BLANK1')
    col.label(text="separate GTA SA Studio add-on.", icon='BLANK1')
    box.operator("gtatk.studio_use_builtin", icon='CHECKMARK')


def register():
    bpy.utils.register_class(GTATK_OT_studio_use_builtin)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    bpy.utils.unregister_class(GTATK_OT_studio_use_builtin)
