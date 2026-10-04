# GTA SA Studio - operators
import os

import bpy
from bpy.props import StringProperty

from . import corelink, lighting, render, vehicle
from .props import PRESET_HOURS


class _NeedsCore:
    @classmethod
    def poll(cls, context):
        return corelink.api() is not None


class GTASTUDIO_OT_time_preset(_NeedsCore, bpy.types.Operator):
    bl_idname = "gtastudio.time_preset"
    bl_label = "Time of Day"
    bl_description = "Quick time: Day = 12:00, Dusk = 19:00, Night = 0:00"
    when: StringProperty(default='NIGHT')

    def execute(self, context):
        context.scene.gta_studio.hour = PRESET_HOURS.get(self.when, 0.0)
        return {'FINISHED'}


# ----------------------------------------------------------------------------- Preview Lighting
# Blender's default Material Preview lights the scene with its own studio HDRI, so the GTA sky, sun
# and map lights (and so Day / Dusk / Night) don't show. Preview Lighting switches one 3D view to
# Material Preview with the scene's world and lights; pressing it again gives the old shading back.
_PREV_SHADING = {}          # 3D view space pointer -> (type, use_scene_lights, use_scene_world)


def shows_scene_lighting(shading):
    """True when this 3D view shading shows the scene's own world and lights. Draw-safe."""
    if shading.type == 'MATERIAL':
        return shading.use_scene_lights and shading.use_scene_world
    if shading.type == 'RENDERED':
        return shading.use_scene_lights_render and shading.use_scene_world_render
    return False


def toggle_scene_lighting(key, shading):
    """Switch a view to Material Preview with scene world + lights, or back to what it had (Solid when
    unknown, e.g. after reopening the file). Returns the new on/off state."""
    if shows_scene_lighting(shading):
        typ, lights, world = _PREV_SHADING.pop(key, ('SOLID', shading.use_scene_lights, shading.use_scene_world))
        shading.use_scene_lights, shading.use_scene_world = lights, world
        shading.type = typ              # saved only while not lit, so this never shows the scene lighting
        return False
    _PREV_SHADING[key] = (shading.type, shading.use_scene_lights, shading.use_scene_world)
    shading.type = 'MATERIAL'
    shading.use_scene_lights = shading.use_scene_world = True
    return True


class GTASTUDIO_OT_preview_lighting(bpy.types.Operator):
    bl_idname = "gtastudio.preview_lighting"
    bl_label = "Preview Lighting"
    bl_description = ("Show this 3D view in Material Preview with the scene's own sky, sun and map lights, "
                      "so time of day and weather are visible. Click again to go back")

    @classmethod
    def poll(cls, context):
        return context.space_data is not None and context.space_data.type == 'VIEW_3D'

    def execute(self, context):
        sp = context.space_data
        on = toggle_scene_lighting(sp.as_pointer(), sp.shading)
        self.report({'INFO'}, "Scene lighting shown" if on else "Viewport shading back")
        return {'FINISHED'}


# ----------------------------------------------------------------------------- timecyc
_TC_ITEMS = []          # keeps the dynamic enum items alive


def _timecyc_items(self, context):
    from . import timecycs
    _TC_ITEMS[:] = [(c, lab, "Use %s for the sky, sun and fog" % lab, 'WORLD', i)
                    for i, (c, lab) in enumerate(timecycs.entries())]
    return _TC_ITEMS


class GTASTUDIO_OT_timecyc_choose(_NeedsCore, bpy.types.Operator):
    bl_idname = "gtastudio.timecyc_choose"
    bl_label = "Timecyc"
    bl_description = ("Which timecyc drives the sky, sun and fog of this scene. A file for another game or "
                      "version is refused with a message and the current one stays")
    bl_property = "choice"
    choice: bpy.props.EnumProperty(items=_timecyc_items, name="Timecyc")

    def execute(self, context):
        from . import timecycs
        try:
            timecycs.choose(context.scene, self.choice)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, "Sky from %s" % context.scene.gta_studio.timecyc_label)
        return {'FINISHED'}


class GTASTUDIO_OT_timecyc_add(_NeedsCore, bpy.types.Operator):
    bl_idname = "gtastudio.timecyc_add"
    bl_label = "Add Timecyc File"
    bl_description = "Add a timecyc.dat to the list (e.g. a weather mod or an unmodified copy) and use it"
    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.dat;*.cfg", options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from . import timecyc, timecycs
        path = bpy.path.abspath(self.filepath)
        try:
            timecyc.load(path)                    # checked before it goes on the list
        except (OSError, timecyc.TimecycError) as e:
            self.report({'ERROR'}, "%s: %s" % (os.path.basename(path) or "No file", e))
            return {'CANCELLED'}
        pr = corelink.prefs()
        if all(os.path.normcase(bpy.path.abspath(f.path)) != os.path.normcase(path) for f in pr.timecyc_files):
            pr.timecyc_files.add().path = path
            context.preferences.is_dirty = True
        timecycs.choose(context.scene, "F:" + path)
        self.report({'INFO'}, "Sky from %s" % context.scene.gta_studio.timecyc_label)
        return {'FINISHED'}


class GTASTUDIO_OT_timecyc_remove(_NeedsCore, bpy.types.Operator):
    bl_idname = "gtastudio.timecyc_remove"
    bl_label = "Remove from List"
    bl_description = ("Take the chosen timecyc off the list (a Mod Folder file is only hidden; nothing is "
                      "deleted) and go back to the game's")

    @classmethod
    def poll(cls, context):
        return super().poll(context) and context.scene.gta_studio.timecyc_choice[:2] in ("F:", "M:")

    def execute(self, context):
        from . import timecycs
        p = context.scene.gta_studio
        pr = corelink.prefs()
        path = p.timecyc_choice[2:]
        if p.timecyc_choice.startswith("F:"):
            for i in range(len(pr.timecyc_files) - 1, -1, -1):
                if os.path.normcase(bpy.path.abspath(pr.timecyc_files[i].path)) == os.path.normcase(path):
                    pr.timecyc_files.remove(i)
        else:
            hid = [h for h in pr.timecyc_hidden.split("\n") if h] + [os.path.normcase(path)]
            pr.timecyc_hidden = "\n".join(hid)
        context.preferences.is_dirty = True
        timecycs.choose(context.scene, timecycs.GAME)
        return {'FINISHED'}


class GTASTUDIO_OT_find_lights(_NeedsCore, bpy.types.Operator):
    bl_idname = "gtastudio.find_lights"
    bl_label = "Find Lights"
    bl_description = "Find and sort GTA lights (2DFX) already in the scene, e.g. from older imports"

    def execute(self, context):
        n = corelink.api().tag_scene_lights()
        lighting.apply(context.scene)
        c = lighting.refresh_counts(context.scene)
        found = ", ".join("%s %d" % (k.title(), v) for k, v in c.items() if v)
        self.report({'INFO'}, "%d new lights tagged. %s" % (n, found))
        return {'FINISHED'}


class GTASTUDIO_OT_add_camera(_NeedsCore, bpy.types.Operator):
    bl_idname = "gtastudio.add_camera"
    bl_label = "Add Camera"
    bl_description = ("A camera aimed at the selected model (or the 3D cursor), with the chosen lens. "
                      "Move the target empty to re-aim it")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        cam = render.add_camera(context)
        self.report({'INFO'}, "%s is the scene camera" % cam.name)
        return {'FINISHED'}


class GTASTUDIO_OT_turntable(_NeedsCore, bpy.types.Operator):
    bl_idname = "gtastudio.turntable"
    bl_label = "Turntable"
    bl_description = "A camera that circles the selected vehicle or model once, looping, over the chosen number of frames"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return corelink.api() is not None and context.active_object is not None

    def execute(self, context):
        ob = context.active_object
        while ob.parent is not None and ob.parent.name != "GTA Turntable":
            ob = ob.parent                              # the vehicle's root, not one wheel
        try:
            render.turntable(context, ob, context.scene.gta_studio.turntable_frames)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, "Turntable around %s: frames %d-%d" % (ob.name, context.scene.frame_start,
                                                                      context.scene.frame_end))
        return {'FINISHED'}


class GTASTUDIO_OT_frame_map_area(_NeedsCore, bpy.types.Operator):
    bl_idname = "gtastudio.frame_map_area"
    bl_label = "Frame Map Area"
    bl_description = ("Camera and render settings (1920x1080, EEVEE) for the map import selected in the World tab. "
                      "Then render with F12")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            cam, info = render.frame_map_area(context)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, "%s frames #%d %s (%d objects): 1920x1080, %s"
                    % (cam.name, info["num"], info["name"], info["count"], context.scene.render.engine))
        return {'FINISHED'}


# ----------------------------------------------------------------------------- Vehicle tab
class _OnVehicle(_NeedsCore):
    @classmethod
    def poll(cls, context):
        return corelink.api() is not None and vehicle.find_root(context.active_object) is not None


def _vehicle_root(context):
    return vehicle.find_root(context.active_object)


class GTASTUDIO_OT_vehicle_setup(_OnVehicle, bpy.types.Operator):
    bl_idname = "gtastudio.vehicle_setup"
    bl_label = "Set Up Vehicle"
    bl_description = "Find this vehicle's paint, lights, doors, extras, paintjobs and upgrades (once per vehicle)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        root = _vehicle_root(context)
        try:
            vs = vehicle.setup(root)
        except Exception as e:        # noqa
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, "%s: %d doors/parts, %d upgrade slots, %d paintjobs"
                    % (root.name, len(vs.parts), len(vs.upgrades), vs.paintjob_count))
        return {'FINISHED'}


class GTASTUDIO_OT_vehicle_random_set(_OnVehicle, bpy.types.Operator):
    bl_idname = "gtastudio.vehicle_random_set"
    bl_label = "Random Colour Set"
    bl_description = "One of this model's colour combinations from the game, picked at random"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        import random
        vs = _vehicle_root(context).gta_vehicle_studio
        n = len(vehicle.vehicle_data(vs.model)["colour_sets"])
        if not n:
            self.report({'WARNING'}, "The game has no colour sets for %s" % vs.model)
            return {'CANCELLED'}
        cur = int(vs.colour_set) if vs.colour_set.isdigit() else -1
        pick = random.choice([i for i in range(n) if i != cur] or [0])
        vs.colour_set = str(pick)
        return {'FINISHED'}


class GTASTUDIO_OT_vehicle_plain_paint(_OnVehicle, bpy.types.Operator):
    bl_idname = "gtastudio.vehicle_plain_paint"
    bl_label = "Back to Plain Paint"
    bl_description = "Take the paintjob off and show the body colours again"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        _vehicle_root(context).gta_vehicle_studio.paintjob = '0'
        return {'FINISHED'}


class GTASTUDIO_OT_vehicle_close_all(_OnVehicle, bpy.types.Operator):
    bl_idname = "gtastudio.vehicle_close_all"
    bl_label = "Close All"
    bl_description = "Close every door, the bonnet and the boot"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        for p in _vehicle_root(context).gta_vehicle_studio.parts:
            if p.kind != 'EXTRA':
                p.open = 0.0
        return {'FINISHED'}


classes = (GTASTUDIO_OT_time_preset, GTASTUDIO_OT_preview_lighting, GTASTUDIO_OT_timecyc_choose,
           GTASTUDIO_OT_timecyc_add, GTASTUDIO_OT_timecyc_remove, GTASTUDIO_OT_find_lights, GTASTUDIO_OT_add_camera, GTASTUDIO_OT_turntable,
           GTASTUDIO_OT_frame_map_area,
           GTASTUDIO_OT_vehicle_setup, GTASTUDIO_OT_vehicle_random_set, GTASTUDIO_OT_vehicle_plain_paint,
           GTASTUDIO_OT_vehicle_close_all)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
