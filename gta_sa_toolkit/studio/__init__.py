# GTA SA Studio - scene, lighting, vehicle, toy, path and animation tools for GTA San Andreas scenes:
# the part of the add-on that works with what's already imported. An ordinary subpackage: it imports
# the add-on's other modules directly and is registered with the rest of the add-on (__init__.py).
# The preference switches (switches.py) only show or hide its tabs and sections.
# Property names (Scene.gta_studio, Object.gta_vehicle_studio / gta_toy_studio / gta_anim_studio) and
# operator names (gtastudio.*) are the same as in earlier versions, so saved files keep their data.

if "bpy" in locals():
    import importlib
    from . import (compat, switches, timecyc, timecycs, props, vehicle, vehicle_props, lighting, render, ops, edit,
                   paths, animate, ui_vehicle, ui_animate, ui_scene)
    for m in (compat, switches, timecyc, timecycs, props, vehicle, vehicle_props, lighting, render, ops, edit,
              paths, animate, ui_vehicle, ui_animate, ui_scene):
        importlib.reload(m)

import bpy  # noqa: E402,F401
from . import (animate, edit, lighting, ops, paths, props, switches, ui_animate, ui_scene, vehicle,  # noqa: E402
               vehicle_props)


def show_tabs():
    """Show the tabs and sections of the areas switched on (called again when a switch changes)."""
    ui_scene.show_tabs(switches.areas_on())


def on_import_done(objects):
    """After every import (events.py): set up the new lights and vehicles."""
    if switches.studio_on():
        lighting.on_import(objects)


def on_import_removed():
    if switches.studio_on():
        lighting.on_removed()


def register():
    props.register()
    vehicle_props.register()
    ops.register()
    edit.register()
    paths.register()
    animate.register()
    ui_animate.register()
    ui_scene.register()
    show_tabs()


def unregister():
    ui_scene.hide_tabs()
    ui_scene.unregister()
    ui_animate.unregister()
    animate.unregister()
    paths.unregister()
    edit.unregister()
    ops.unregister()
    vehicle_props.unregister()
    props.unregister()
    vehicle.free()
