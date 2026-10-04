# GTA SA Studio - the Animate tab: clip list, clip settings, IFP browser, preview (engine: animate.py).
import os

import bpy
from bpy.props import EnumProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

from . import animate, corelink


def _ped(context):
    return animate.ped_of(context.active_object)


class _NeedsPed:
    @classmethod
    def poll(cls, context):
        return corelink.api() is not None and _ped(context) is not None


# ----------------------------------------------------------------------------- lists
class GTASTUDIO_UL_anim_clips(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        r = layout.row(align=True)
        ic = {'AFTER': 'NEXT_KEYFRAME', 'AT': 'KEYFRAME', 'REPLACE': 'SEQ_STRIP_DUPLICATE'}[item.place]
        r.label(text="%d  %s" % (index + 1, item.anim_name), icon=ic if item.source else 'ERROR')
        extra = " x%d" % item.repeats if item.repeats > 1 else ""
        r.label(text="%d-%d%s" % (round(item.start), round(item.end), extra))


# ----------------------------------------------------------------------------- operators
class GTASTUDIO_OT_anim_add(_NeedsPed, bpy.types.Operator):
    bl_idname = "gtastudio.anim_add"
    bl_label = "Add Clip"
    bl_description = "Add the animation picked below at the end of the selected ped's clip list"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        picked = corelink.api().selected_anim(context.scene)
        if picked is None:
            self.report({'ERROR'}, "Pick an IFP and an animation in the list first")
            return {'CANCELLED'}
        arm = _ped(context)
        had = arm.animation_data.action if arm.animation_data else None
        try:
            _row, missing = animate.add_clip(arm, picked[0], picked[1], animate.fps_of(context.scene))
        except Exception as e:        # noqa
            self.report({'ERROR'}, "Could not add it: %s" % e)
            return {'CANCELLED'}
        if missing:
            self.report({'WARNING'}, "%d bone%s not on this ped: %s" % (
                len(missing), "" if len(missing) == 1 else "s", ", ".join(missing[:5])))
        if had is not None and had.get("gta_combo_row") is None and had != arm.gta_anim_studio.baked:
            self.report({'INFO'}, "'%s' no longer plays on top (kept in the file)" % had.name)
        return {'FINISHED'}


class GTASTUDIO_OT_anim_remove(_NeedsPed, bpy.types.Operator):
    bl_idname = "gtastudio.anim_remove"
    bl_label = "Remove Clip"
    bl_description = "Remove the selected clip (its NLA track goes too)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        arm = _ped(context)
        animate.remove_clip(arm, arm.gta_anim_studio.active)
        return {'FINISHED'}


class GTASTUDIO_OT_anim_move(_NeedsPed, bpy.types.Operator):
    bl_idname = "gtastudio.anim_move"
    bl_label = "Move Clip"
    bl_description = "Move the selected clip earlier or later in the playback order"
    bl_options = {'REGISTER', 'UNDO'}
    direction: IntProperty(default=-1)

    def execute(self, context):
        arm = _ped(context)
        animate.move_clip(arm, arm.gta_anim_studio.active, self.direction)
        return {'FINISHED'}


class GTASTUDIO_OT_anim_jump(_NeedsPed, bpy.types.Operator):
    bl_idname = "gtastudio.anim_jump"
    bl_label = "Show Pose"
    bl_description = "Show this clip alone at its first or last frame (Show All brings the others back)"
    which: EnumProperty(items=(('START', "Start Pose", ""), ('END', "End Pose", "")))

    def execute(self, context):
        st = _ped(context).gta_anim_studio
        if not (0 <= st.active < len(st.clips)):
            return {'CANCELLED'}
        r = st.clips[st.active]
        f = r.start if self.which == 'START' else r.end
        animate.set_solo(_ped(context), r)
        context.scene.frame_set(int(f), subframe=f - int(f))
        return {'FINISHED'}


class GTASTUDIO_OT_anim_show_all(_NeedsPed, bpy.types.Operator):
    bl_idname = "gtastudio.anim_show_all"
    bl_label = "Show All"
    bl_description = "Play every clip again (after Start/End Pose showed one alone)"

    def execute(self, context):
        animate.set_solo(_ped(context), None)
        return {'FINISHED'}


class GTASTUDIO_OT_anim_fit(_NeedsPed, bpy.types.Operator):
    bl_idname = "gtastudio.anim_fit"
    bl_label = "Fit Timeline"
    bl_description = "Set the scene's frame range to the whole combination"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        rng = animate.combo_range(_ped(context))
        if rng is None:
            self.report({'ERROR'}, "Add a clip first")
            return {'CANCELLED'}
        sc = context.scene
        sc.frame_start, sc.frame_end = rng
        if not rng[0] <= sc.frame_current <= rng[1]:
            sc.frame_set(rng[0])
        return {'FINISHED'}


class GTASTUDIO_OT_anim_bake(_NeedsPed, bpy.types.Operator):
    bl_idname = "gtastudio.anim_bake"
    bl_label = "Bake"
    bl_description = ("Play the clips into one action named after the ped (a key every frame). The clips' NLA "
                      "tracks stay, muted: Edit Clips (or changing any clip) brings them back")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            act, f0, f1 = animate.bake(_ped(context), context.scene)
        except Exception as e:        # noqa
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, "Baked '%s': frames %d-%d" % (act.name, f0, f1))
        return {'FINISHED'}


class GTASTUDIO_OT_anim_unbake(_NeedsPed, bpy.types.Operator):
    bl_idname = "gtastudio.anim_unbake"
    bl_label = "Edit Clips"
    bl_description = "Play the clips again instead of the baked action (the baked action is kept)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        animate.unbake(_ped(context))
        return {'FINISHED'}


class GTASTUDIO_OT_anim_export(_NeedsPed, bpy.types.Operator, ExportHelper):
    bl_idname = "gtastudio.anim_export"
    bl_label = "Export IFP"
    bl_description = ("Bake (if needed) and save as an IFP animation: a new file, or into an existing one. "
                      "ANPK is used automatically when the ped travels more than 32 m")
    filename_ext = ".ifp"
    filter_glob: StringProperty(default="*.ifp", options={'HIDDEN'})

    def invoke(self, context, event):
        st = _ped(context).gta_anim_studio
        if st.export_path:
            self.filepath = bpy.path.abspath(st.export_path)
        else:
            self.filepath = (st.export_name or animate.ped_name(_ped(context))) + ".ifp"
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        arm = _ped(context)
        st = arm.gta_anim_studio
        if st.export_mode == 'REPLACE' and not os.path.isfile(self.filepath):
            self.report({'ERROR'}, "Pick an existing IFP to put the animation into")
            return {'CANCELLED'}
        try:
            path, fmt, notes = animate.export(arm, context.scene, self.filepath, st.export_mode)
        except Exception as e:        # noqa
            self.report({'ERROR'}, "Export failed: %s" % e)
            return {'CANCELLED'}
        st.export_path = path
        st.last_export = "%s -> %s (%s)" % (st.export_name or animate.ped_name(arm), os.path.basename(path), fmt)
        for n in notes:
            self.report({'WARNING'}, n)
        self.report({'INFO'}, "Saved " + st.last_export)
        return {'FINISHED'}


# ----------------------------------------------------------------------------- tab
def _section(layout, idname, title, icon='NONE', closed=False):
    header, body = layout.panel("GTASTUDIO_anim_" + idname, default_closed=closed)
    header.label(text=title, icon=icon)
    return body


def draw_animate(layout, context):
    arm = _ped(context)
    if arm is None:
        col = layout.column(align=True)
        col.label(text="Select a ped", icon='INFO')
        col.label(text="(Peds tab > import one)", icon='BLANK1')
        return
    st = arm.gta_anim_studio
    layout.label(text=animate.ped_name(arm), icon='ARMATURE_DATA')

    b = _section(layout, "clips", "Clips", 'NLA')
    if b:
        r = b.row()
        r.template_list("GTASTUDIO_UL_anim_clips", "", st, "clips", st, "active", rows=4)
        c = r.column(align=True)
        c.operator("gtastudio.anim_remove", text="", icon='REMOVE')
        c.separator()
        c.operator("gtastudio.anim_move", text="", icon='TRIA_UP').direction = -1
        c.operator("gtastudio.anim_move", text="", icon='TRIA_DOWN').direction = 1
        if not st.clips:
            b.label(text="Add clips below, in playback order", icon='INFO')
        elif 0 <= st.active < len(st.clips):
            draw_clip(b, st.clips[st.active])

    b = _section(layout, "add", "Add Clip", 'ADD')
    if b:
        a = corelink.api()
        a.draw_ifp_browser(b, context, rows=5)          # the same list as the Peds tab
        if a.selected_anim(context.scene) is not None:
            r = b.row()
            r.scale_y = 1.2
            r.operator("gtastudio.anim_add", icon='ADD')

    b = _section(layout, "preview", "Preview", 'PLAY')
    if b:
        r = b.row(align=True)
        r.prop(st, "start_frame")
        r.operator("gtastudio.anim_fit", icon='TIME')
        b.label(text="Play it with the timeline (Space)", icon='INFO')

    b = _section(layout, "bake", "Bake & Export", 'EXPORT')
    if b:
        if st.is_baked and st.baked is not None:
            r = b.row(align=True)
            r.label(text="Playing the baked '%s'" % st.baked.name, icon='CHECKMARK')
            r.operator("gtastudio.anim_unbake", icon='NLA')
        else:
            r = b.row()
            r.scale_y = 1.2
            r.operator("gtastudio.anim_bake", icon='REC')
        c = b.column()
        c.prop(st, "export_name")
        c.prop(st, "export_mode")
        if st.export_mode == 'NEW':
            c.prop(st, "export_format")
        r = b.row()
        r.scale_y = 1.2
        r.operator("gtastudio.anim_export", icon='EXPORT')
        if st.last_export:
            b.label(text="Last: " + st.last_export, icon='FILE_TICK')


def draw_clip(layout, row):
    if row.source is None:
        layout.label(text="Its animation is missing from the file", icon='ERROR')
        return
    box = layout.box()
    box.label(text="%s  (%.1f frames)" % (row.anim_name, row.length), icon='ACTION')
    c = box.column(align=True)
    r = c.row(align=True)
    r.prop(row, "trim_start")
    r.prop(row, "trim_end")
    r = c.row(align=True)
    r.operator("gtastudio.anim_jump", text="Start Pose", icon='REW').which = 'START'
    r.operator("gtastudio.anim_jump", text="End Pose", icon='FF').which = 'END'
    if row.id_data.gta_anim_studio.solo:
        c.operator("gtastudio.anim_show_all", icon='HIDE_OFF')
    box.prop(row, "place")
    if row.place != 'AFTER':
        r = box.row(align=True)
        r.prop(row, "at_frame")
        if row.place == 'REPLACE':
            r.prop(row, "range_end")
    r = box.row(align=True)
    r.prop(row, "speed")
    r.prop(row, "repeats")
    r = box.row(align=True)
    r.prop(row, "blend")
    r.prop(row, "blend_curve", text="")
    box.prop(row, "match_root")


classes = (GTASTUDIO_UL_anim_clips, GTASTUDIO_OT_anim_add,
           GTASTUDIO_OT_anim_remove, GTASTUDIO_OT_anim_move, GTASTUDIO_OT_anim_jump, GTASTUDIO_OT_anim_show_all,
           GTASTUDIO_OT_anim_fit, GTASTUDIO_OT_anim_bake, GTASTUDIO_OT_anim_unbake, GTASTUDIO_OT_anim_export)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
