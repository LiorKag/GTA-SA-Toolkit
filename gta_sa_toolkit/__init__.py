# GTA SA Toolkit for Blender 4.4+ / 5.x
# IFP animation import/export and whole-map import for GTA San Andreas.
# Reads DFF / TXD / COL itself (formats/, rwbuild.py); conventions from DragonFF (credited in rwbuild.py).
# License: GPL-3.0-or-later

if "bpy" in locals():
    import importlib
    from . import formats, events, tabs, cache, thumbs, anim, mapimport, lighttags, vehicles, weapons, samp, paths, radar, overlay, areapick, preview, library, ifplist, peds, mods, migrate, rwbuild, texfix, ui, ui_extra, ui_layout, studio
    from .formats import ifp, img, mapdata, dxt, nodes, gxt, dffprobe, rw, dff, txd, col, colmats
    for m in (ifp, img, gxt, dffprobe, mapdata, dxt, rw, dff, txd, col, colmats, nodes, events, tabs, cache, thumbs, anim, mapimport, lighttags, vehicles, weapons, samp, paths, radar, overlay, areapick, preview, library, ifplist, peds, mods, migrate, rwbuild, texfix, ui, ui_extra, ui_layout, studio):
        importlib.reload(m)

import bpy  # noqa: E402
from . import areapick, library, migrate, mods, preview, studio, texfix, thumbs, ui, ui_extra, ui_layout  # noqa: E402


def register():
    ui.register()
    ui_extra.register()
    library.register()
    mods.register()
    thumbs.register()
    areapick.register()
    preview.register()
    ui_layout.register()
    migrate.register()
    texfix.register()
    studio.register()


def unregister():
    studio.unregister()
    texfix.unregister()
    migrate.unregister()
    ui_layout.unregister()
    preview.unregister()
    areapick.unregister()
    thumbs.unregister()
    mods.unregister()
    library.unregister()
    ui_extra.unregister()
    ui.unregister()
