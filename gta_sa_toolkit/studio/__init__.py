# GTA SA Studio - scene, lighting, vehicle, toy, path and animation tools for GTA San Andreas scenes.
# Part of GTA SA Toolkit (in earlier, private versions it was a separate add-on). It uses the rest of the
# add-on only through api.py (corelink.py). register() / unregister() are called by core's studiolink.py
# when the "GTA SA Studio" switch in the add-on preferences is on; with it off nothing here is registered.
# Property names (Scene.gta_studio, Object.gta_vehicle_studio / gta_toy_studio / gta_anim_studio) and
# operator names (gtastudio.*) are the same as in the separate Studio, so saved files keep their data.

if "bpy" in locals():
    import importlib
    from . import (compat, timecyc, timecycs, corelink, props, vehicle, vehicle_props, lighting, render, ops, edit, paths,
                   animate, ui_vehicle, ui_animate, ui_scene)
    for m in (compat, timecyc, timecycs, corelink, props, vehicle, vehicle_props, lighting, render, ops, edit, paths,
              animate, ui_vehicle, ui_animate, ui_scene):
        importlib.reload(m)

import bpy  # noqa: E402,F401
from . import (animate, corelink, edit, lighting, ops, paths, props, ui_animate, ui_scene, vehicle,  # noqa: E402
               vehicle_props)


def register():
    props.register()
    vehicle_props.register()
    ops.register()
    edit.register()
    paths.register()
    animate.register()
    ui_animate.register()
    ui_scene.register()
    lighting.register()
    corelink.connect()


def unregister():
    corelink.disconnect()
    lighting.unregister()
    ui_scene.unregister()
    ui_animate.unregister()
    animate.unregister()
    paths.unregister()
    edit.unregister()
    ops.unregister()
    vehicle_props.unregister()
    props.unregister()
    vehicle.free()
