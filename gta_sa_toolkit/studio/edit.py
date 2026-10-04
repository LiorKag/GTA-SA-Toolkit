# GTA SA Studio - the Edit tab: toy editor (SA-MP attached objects), Pawn export of map objects and
# traffic / ped paths (paths.py).
# All SA-MP maths are core's (api.toy_values / set_toy_values / map_object_values ...), the exact
# reverse of its toy attach and SA-MP map import; this module is the panel, clipboard and file side.
import os

import bpy
from bpy.props import EnumProperty, FloatVectorProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

from . import corelink


def _api():
    return corelink.api()


def toy_of(ob):
    a = _api()
    return a.toy_root(ob) if a is not None and ob is not None else None


def bone_label(bone):
    a = _api()
    for sid, label, _gid in (a.SAMP_BONES if a is not None else ()):
        if sid == bone:
            return label
    return "not a SA-MP bone"


# ----------------------------------------------------------------------------- toy fields
# Live values: read from the toy's transform each time the panel draws (one object, no scanning),
# so they follow the gizmo; typing a value moves the toy.
def _values(self):
    a = _api()
    ob = self.id_data
    if a is None or a.toy_root(ob) is not ob:
        return None
    return a.toy_values(ob)


def _getter(key, default):
    def get(self):
        v = _values(self)
        return tuple(v[key]) if v is not None else default
    return get


def _setter(key):
    def set_(self, value):
        a = _api()
        if a is not None and a.toy_root(self.id_data) is self.id_data:
            a.set_toy_values(self.id_data, **{key: tuple(value)})
    return set_


def _get_index(self):
    v = _values(self)
    return v["index"] if v is not None else 0


def _set_index(self, value):
    a = _api()
    if a is not None and a.toy_root(self.id_data) is self.id_data:
        a.set_toy_values(self.id_data, index=value)


_BONE_ITEMS = []          # kept alive for Blender (dynamic enum items must stay referenced)


def _bone_items(self, context):
    a = _api()
    if not _BONE_ITEMS and a is not None:
        _BONE_ITEMS.extend((str(sid), "%d  %s" % (sid, label), "SA-MP bone %d: %s" % (sid, label), sid)
                           for sid, label, _gid in a.SAMP_BONES)
    return _BONE_ITEMS or [("0", "-", "", 0)]


def _get_bone(self):
    v = _values(self)
    return v["bone"] if v is not None else 0


def _set_bone(self, value):
    a = _api()
    if a is not None and a.toy_root(self.id_data) is self.id_data and value:
        try:
            a.set_toy_values(self.id_data, bone=value)
        except ValueError as e:
            print("[GTA SA Toolkit]", e)


class GTASTUDIO_ToyProps(bpy.types.PropertyGroup):
    bone: EnumProperty(name="Bone", items=_bone_items, get=_get_bone, set=_set_bone,
                       description="SA-MP bone the toy is attached to. Changing it moves the toy onto the new "
                                   "bone with the same offset, rotation and scale (like the game)")
    index: IntProperty(name="Slot", min=0, max=9, get=_get_index, set=_set_index,
                       description="SetPlayerAttachedObject slot (0-9): a player can wear 10 objects at once")
    # shown rounded (74.9999 reads 75.00); the copied SA-MP line keeps the full values
    offset: FloatVectorProperty(name="Offset", size=3, precision=3, step=1, subtype='TRANSLATION',
                                get=_getter("offset", (0.0, 0.0, 0.0)), set=_setter("offset"),
                                description="fOffsetX/Y/Z: metres from the bone")
    rot: FloatVectorProperty(name="Rotation", size=3, precision=2, step=100, subtype='XYZ',
                             get=_getter("rot", (0.0, 0.0, 0.0)), set=_setter("rot"),
                             description="fRotX/Y/Z in degrees (SA-MP order)")
    scale: FloatVectorProperty(name="Scale", size=3, precision=3, step=1, subtype='XYZ',
                               get=_getter("scale", (1.0, 1.0, 1.0)), set=_setter("scale"),
                               description="fScaleX/Y/Z")


def toy_line(toy):
    a = _api()
    return a.attached_line(a.toy_values(toy))


# ----------------------------------------------------------------------------- pawn export
def export_lines(objects, dynamic):
    """Lines for the map objects among these (parts count once). Returns (lines, skipped, scaled)."""
    a = _api()
    roots, skipped = [], 0
    seen = set()
    for ob in objects:
        r = a.map_object_root(ob)
        if r is None:
            skipped += 1
        elif r.name not in seen:
            seen.add(r.name)
            roots.append(r)
    roots.sort(key=lambda o: o.name)
    lines, scaled = [], 0
    for _ob, (model, pos, rot, sc) in a.map_object_values(roots):
        lines.append(a.object_line(model, pos, rot, dynamic))
        if any(abs(s - 1.0) > 0.001 for s in sc):
            scaled += 1
    return lines, skipped, scaled


def _report_export(op, n, skipped, scaled, where):
    msg = "%d object%s %s" % (n, "" if n == 1 else "s", where)
    if skipped:
        msg += " (%d skipped: not GTA map objects)" % skipped
    op.report({'INFO'}, msg)
    if scaled:
        op.report({'WARNING'}, "%d scaled object%s exported at normal size: SA-MP can't scale map objects"
                  % (scaled, "" if scaled == 1 else "s"))


class _NeedsCore:
    @classmethod
    def poll(cls, context):
        return corelink.api() is not None


class GTASTUDIO_OT_copy_toy_line(_NeedsCore, bpy.types.Operator):
    bl_idname = "gtastudio.copy_toy_line"
    bl_label = "Copy SetPlayerAttachedObject"
    bl_description = "Copy the finished SetPlayerAttachedObject(...) line for the selected toy to the clipboard"

    def execute(self, context):
        toy = toy_of(context.active_object)
        if toy is None:
            self.report({'ERROR'}, "Select an attached toy first")
            return {'CANCELLED'}
        line = toy_line(toy)
        context.window_manager.clipboard = line
        self.report({'INFO'}, "Copied: " + line)
        return {'FINISHED'}


class GTASTUDIO_OT_pawn_copy(_NeedsCore, bpy.types.Operator):
    bl_idname = "gtastudio.pawn_copy"
    bl_label = "Copy to Clipboard"
    bl_description = "Copy the selected map objects to the clipboard as Pawn code"

    def execute(self, context):
        lines, skipped, scaled = export_lines(context.selected_objects, _dynamic(context))
        if not lines:
            self.report({'ERROR'}, "Select map objects imported with GTA SA Toolkit first")
            return {'CANCELLED'}
        context.window_manager.clipboard = "\n".join(lines) + "\n"
        _report_export(self, len(lines), skipped, scaled, "copied")
        return {'FINISHED'}


class GTASTUDIO_OT_pawn_save(_NeedsCore, bpy.types.Operator, ExportHelper):
    bl_idname = "gtastudio.pawn_save"
    bl_label = "Save .pwn"
    bl_description = "Save the selected map objects as Pawn code in a .pwn file"
    filename_ext = ".pwn"
    filter_glob: StringProperty(default="*.pwn;*.inc;*.txt", options={'HIDDEN'})

    def invoke(self, context, event):
        last = context.scene.gta_studio.pawn_path
        if last:
            self.filepath = bpy.path.abspath(last)
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        dynamic = _dynamic(context)
        lines, skipped, scaled = export_lines(context.selected_objects, dynamic)
        if not lines:
            self.report({'ERROR'}, "Select map objects imported with GTA SA Toolkit first")
            return {'CANCELLED'}
        head = "// %d objects exported by GTA SA Studio%s\n" % (
            len(lines), " (needs the streamer plugin)" if dynamic else "")
        try:
            with open(self.filepath, "w", encoding="utf-8", newline="\n") as f:
                f.write(head + "\n".join(lines) + "\n")
        except OSError as e:
            self.report({'ERROR'}, "Could not write %s: %s" % (self.filepath, e))
            return {'CANCELLED'}
        context.scene.gta_studio.pawn_path = self.filepath
        _report_export(self, len(lines), skipped, scaled, "saved to " + os.path.basename(self.filepath))
        return {'FINISHED'}


def _dynamic(context):
    return context.scene.gta_studio.pawn_function == 'DYNAMIC'


# ----------------------------------------------------------------------------- tab
def sec_pawn_export(b, context):
    st = context.scene.gta_studio
    b.row(align=True).prop(st, "pawn_function", expand=True)
    r = b.row(align=True)
    r.scale_y = 1.2
    r.operator("gtastudio.pawn_copy", text="Copy", icon='COPYDOWN')
    r.operator("gtastudio.pawn_save", icon='FILE_TICK')
    b.label(text="Uses the selected map objects", icon='INFO')


def sec_paths(b, context):
    from . import paths
    paths.draw_section(b, context)


# tab, id, title, draw, order, icon, closed - Studio 1.x's Edit tab, now inside core's tabs (panel ids
# as in 1.x so open/closed states carry over): the Toy Editor right after core's Toys (40) in Peds,
# Pawn Export after SA-MP Map Code (30) and the paths after it in World.
SECTIONS = (
    ('CHARS', "toy", "Toy Editor (SA-MP)", lambda b, c: draw_toy(b, c), 45, 'MOD_CLOTH', False),
    ('WORLD', "pawn", "Pawn Export", sec_pawn_export, 35, 'FILE_SCRIPT', True),
    ('WORLD', "paths", "Traffic & Ped Paths", sec_paths, 40, 'CURVE_PATH', True),
)


AREA_OF = {"toy": "toys", "pawn": "pawn", "paths": "paths"}       # section -> preference switch


def add_sections(api, areas=("toys", "pawn", "paths")):
    for tab, sid, title, fn, order, icon, closed in SECTIONS:
        if AREA_OF[sid] not in areas:
            continue
        api.register_section(tab, "st_" + sid, title, fn, order=order, icon=icon, closed=closed,
                             panel_id="GTASTUDIO_edit_" + sid)


def remove_sections(api):
    for tab, sid, *_rest in SECTIONS:
        api.unregister_section(tab, "st_" + sid)


def draw_edit(layout, context):
    """All of these sections in one layout (tests; the panel draws them through core's tabs)."""
    for _tab, _sid, _title, fn, _order, _icon, _closed in SECTIONS:
        fn(layout, context)


def draw_toy(layout, context):
    toy = toy_of(context.active_object)
    if toy is None:
        col = layout.column(align=True)
        col.label(text="Select an attached toy", icon='INFO')
        col.label(text="(attach one in SA-MP Toys above)", icon='BLANK1')
        return
    t = toy.gta_toy_studio
    v = _api().toy_values(toy)
    layout.label(text="%s  ·  model %d" % (toy.name, v["model"]), icon='OBJECT_DATA')
    r = layout.row()
    if any(sid == v["bone"] for sid, _l, _g in _api().SAMP_BONES):
        r.prop(t, "bone", text="", icon='BONE_DATA')
    else:
        r.label(text="Bone: %s" % bone_label(v["bone"]), icon='BONE_DATA')
    r.prop(t, "index")
    col = layout.column()
    col.prop(t, "offset")
    col.prop(t, "rot")
    col.prop(t, "scale")
    r = layout.row()
    r.scale_y = 1.3
    r.operator("gtastudio.copy_toy_line", icon='COPYDOWN')
    layout.label(text="Move, rotate or scale it with the gizmo: the numbers follow", icon='OBJECT_ORIGIN')


classes = (GTASTUDIO_ToyProps, GTASTUDIO_OT_copy_toy_line, GTASTUDIO_OT_pawn_copy, GTASTUDIO_OT_pawn_save)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Object.gta_toy_studio = bpy.props.PointerProperty(type=GTASTUDIO_ToyProps)


def unregister():
    del bpy.types.Object.gta_toy_studio
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
