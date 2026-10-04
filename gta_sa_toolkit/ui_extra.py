# GTA SA Toolkit - UI for vehicles, weapons, SA-MP toys/objects and map code
import math
import os
import random

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       FloatVectorProperty, IntProperty, PointerProperty, StringProperty)
from bpy_extras.io_utils import ImportHelper

from . import events, library, mapimport, peds, samp, tabs, thumbs, ui, vehicles, weapons


def _game(context):
    return mapimport.get_game(context.scene.gta_tk.game_root)


def _arm(context):
    from .ui import get_armature
    return get_armature(context)


def fill_after_scan(context, game):
    x = context.scene.gta_tk_x
    x.weapons.clear()
    for oid, model, disp, cat, wid in weapons.list_weapons(game):
        it = x.weapons.add()
        it.name = disp
        it.oid, it.model, it.cat, it.wid = oid, model, cat, wid
    x.peds.clear()
    for model, pid, cat, label in peds.list_peds(game):
        it = x.peds.add()
        it.name, it.pid, it.cat, it.label = model, pid, cat, label
    x.vehicles.clear()
    for v in sorted(game.vehicles.values(), key=lambda v: (v.name.lower(), v.model.lower())):
        it = x.vehicles.add()
        it.name = v.model                   # what Import uses
        it.label = v.name                   # what the list shows ("Ambulance")
        it.vid = v.id
        it.vtype = v.type
        it.game_name = v.game_name


def fill_mods(context):
    """Put the scanned mod models (mods.models()) into the Peds and Vehicles lists, after the game's."""
    from . import mods
    x = context.scene.gta_tk_x
    for coll in (x.peds, x.vehicles):
        for i in range(len(coll) - 1, -1, -1):
            if coll[i].mod_key:
                coll.remove(i)
    for m in mods.models('PED'):
        it = x.peds.add()
        it.name, it.pid, it.cat, it.label, it.mod_key = m.name, -1, 'MOD', "Mod", m.key
    for m in mods.models('VEHICLE'):
        it = x.vehicles.add()
        it.name, it.label, it.vid, it.vtype, it.mod_key = m.name, m.name, -1, "mod", m.key
        it.game_name = ""


def mod_not_yet(op, item):
    """Custom vehicles are listed; they're imported with import_mod."""
    op.report({'ERROR'}, "%s is a custom vehicle: importing custom vehicles comes in the next update" % item.name)
    return {'CANCELLED'}


def import_mod_row(op, context, key):
    """Import a custom ped / object from a list row, reporting its warnings."""
    from . import mods
    try:
        coll, warnings = mods.import_mod(context, _game(context), key)
    except Exception as e:           # noqa
        op.report({'ERROR'}, str(e))
        return {'CANCELLED'}
    for w in warnings:
        op.report({'WARNING'}, w)
    notes = mods.LAST["notes"]
    op.report({'INFO'}, "Imported %s (custom model)%s" % (coll.name, "; " + ", ".join(notes) if notes else ""))
    return {'FINISHED'}


# ============================================================================= props
def veh_matches(item, q):
    """Vehicle-list search: q (lower case) matches the model name, the in-game name, its GXT key or the ID."""
    if not q:
        return True
    if q.isdigit():
        return str(item.vid).startswith(q)
    return q in item.name.lower() or q in item.label.lower() or q in item.game_name.lower()


def _upd_veh_query(self, context):
    """Highlight the best match so Import acts on a vehicle the list shows."""
    q = self.veh_query.strip().lower()
    if not q or not self.vehicles:
        return
    best, best_score = -1, 9
    for i, it in enumerate(self.vehicles):
        if not veh_matches(it, q):
            continue
        exact = str(it.vid) == q or it.name.lower() == q or it.label.lower() == q or it.game_name.lower() == q
        score = 0 if exact else (1 if it.name.lower().startswith(q) or it.label.lower().startswith(q) else 2)
        if score < best_score:
            best, best_score = i, score
    if best >= 0:
        self.vehicles_index = best


def _upd_weapon_cat(self, context):
    """Keep the highlighted weapon inside the visible category (Import/Give act on it)."""
    if self.weapon_cat == 'ALL' or not self.weapons:
        return
    cur = self.weapons[min(self.weapons_index, len(self.weapons) - 1)]
    if cur.cat == self.weapon_cat:
        return
    for i, it in enumerate(self.weapons):
        if it.cat == self.weapon_cat:
            self.weapons_index = i
            return


def _get_base(self):
    """A mod row's 'based on' game model (chosen, else the guess) - a dict lookup and a short scan."""
    from . import mods
    m = mods.get(self.mod_key) if self.mod_key else None
    if m is None:
        return ""
    return mods.base_of(m, mapimport._GAME.get("data"))[0] or ""


def _set_base(self, value):
    from . import mods
    if self.mod_key:
        mods.set_base(self.mod_key, value.strip() or None)


class GTATK_VehItem(bpy.types.PropertyGroup):
    base: StringProperty(name="Based On", get=_get_base, set=_set_base,
                         description="The game vehicle this custom vehicle borrows from: colour sets, wheel "
                                     "size, door/boot handling, upgrades, paintjobs and missing textures. "
                                     "Empty = the guess (same name, else a typical one of its kind)")
    vid: IntProperty()
    mod_key: StringProperty()           # custom / modded model (mods.py), else empty
    vtype: StringProperty()
    game_name: StringProperty()
    label: StringProperty()


class GTATK_PedItem(bpy.types.PropertyGroup):
    base: StringProperty(name="Based On", get=_get_base, set=_set_base,
                         description="The game ped this custom ped borrows from (its textures when its own "
                                     ".txd lacks some). Empty = the guess (the game ped with the same name)")
    pid: IntProperty()                  # -1 for story characters (no IDE entry) and mods
    mod_key: StringProperty()           # custom / modded model (mods.py), else empty
    cat: StringProperty()               # peds.CATEGORIES id
    label: StringProperty()             # type shown beside the name ("Grove Street", "Cop", "Story")


def ped_matches(item, q, cat):
    if cat != 'ALL' and item.cat != cat:
        return False
    if not q:
        return True
    if q.isdigit():
        return item.pid >= 0 and str(item.pid).startswith(q)
    return q in item.name.lower() or q in item.label.lower()


def _upd_ped_filter(self, context):
    """Keep the highlighted ped among the shown ones (Import acts on it); the best name match wins."""
    q, cat = self.ped_query.strip().lower(), self.ped_cat
    best, best_score = -1, 9
    for i, it in enumerate(self.peds):
        if not ped_matches(it, q, cat):
            continue
        score = 0 if (q and (it.name.lower() == q or str(it.pid) == q)) else (1 if q and it.name.lower().startswith(q) else 2)
        if score < best_score:
            best, best_score = i, score
    if best >= 0 and (best_score < 2 or not ped_matches(self.peds[min(self.peds_index, len(self.peds) - 1)], q, cat)):
        self.peds_index = best


class GTATK_WeapItem(bpy.types.PropertyGroup):
    oid: IntProperty()
    wid: IntProperty()
    model: StringProperty()
    cat: StringProperty()


class GTATK_ObjItem(bpy.types.PropertyGroup):
    oid: IntProperty()
    mod_key: StringProperty()           # custom / modded model (mods.py), else empty
    txd: StringProperty()
    source: StringProperty()
    kind: StringProperty()


def _bone_items(self, context):
    return [(str(i), "%d  %s" % (i, n), "") for i, n, _ in samp.SAMP_BONES]


class GTATK_ExtraProps(bpy.types.PropertyGroup):
    # vehicles
    vehicles: CollectionProperty(type=GTATK_VehItem)
    vehicles_index: IntProperty()
    veh_query: StringProperty(name="Search", update=_upd_veh_query, options={'TEXTEDIT_UPDATE'},
                              description="Vehicle model name, in-game name or ID (e.g. infernus, 411, taxi)")
    veh_colour_mode: EnumProperty(name="Paint", items=(
        ('RANDOM', "Random (game sets)", "A random colour pair the game uses for this vehicle"),
        ('SET', "Game Colour Set", "Pick one of the vehicle's carcols.dat colour sets"),
        ('CUSTOM', "Custom IDs", "Any GTA colour IDs 0-126 (like SA-MP colour ids)")), default='RANDOM')
    veh_colour_set: IntProperty(name="Set", default=0, min=0)
    veh_col1: IntProperty(name="Colour 1", default=1, min=0, max=255)
    veh_col2: IntProperty(name="Colour 2", default=1, min=0, max=255)
    veh_col3: IntProperty(name="Colour 3", default=0, min=0, max=255)
    veh_col4: IntProperty(name="Colour 4", default=0, min=0, max=255)
    veh_hide_damage: BoolProperty(name="Hide damaged parts", default=True)
    veh_hide_lod: BoolProperty(name="Hide low-detail parts", default=True)

    peds: CollectionProperty(type=GTATK_PedItem)
    peds_index: IntProperty()
    ped_query: StringProperty(name="Search", update=_upd_ped_filter, options={'TEXTEDIT_UPDATE'},
                              description="Model name, ID or type (e.g. bmyst, 105, grove)")
    ped_cat: EnumProperty(name="Type", update=_upd_ped_filter,
                          items=[(c[0], c[1], c[2], i) for i, c in enumerate(peds.CATEGORIES)], default='ALL')
    ped_auto_pose: BoolProperty(name="Stand peds up (idle pose)", default=True,
                                description="Imported peds get the game's IDLE_stance pose instead of the "
                                            "lying bind pose")

    # weapons
    weapons: CollectionProperty(type=GTATK_WeapItem)
    weapons_index: IntProperty()
    weapon_cat: EnumProperty(name="Type", items=weapons.CATEGORIES, default='ALL', update=_upd_weapon_cat)
    weapon_hand: EnumProperty(name="Hand", items=(('RIGHT', "Right Hand", ""), ('LEFT', "Left Hand", "")),
                              default='RIGHT')

    # UI layout
    ui_tab: EnumProperty(name="Section", items=tabs.enum_items, default=0)     # see tabs.py

    # objects / toys
    obj_query: StringProperty(name="Search", description="Model name or ID (e.g. hat, 18645, glasses)")
    obj_only_samp: BoolProperty(name="SA-MP only", default=False,
                                description="Only objects from SAMP.img (the toys/attachable objects)")
    objects: CollectionProperty(type=GTATK_ObjItem)
    objects_index: IntProperty()
    toy_bone: EnumProperty(name="Bone", items=_bone_items)
    toy_offset: FloatVectorProperty(name="Offset", size=3, default=(0.0, 0.0, 0.0), precision=4)
    toy_rot: FloatVectorProperty(name="Rotation", size=3, default=(0.0, 0.0, 0.0),
                                 description="Degrees, SA-MP convention")
    toy_scale: FloatVectorProperty(name="Scale", size=3, default=(1.0, 1.0, 1.0))
    toy_line: StringProperty(name="Code", description="Paste a SetPlayerAttachedObject(...) line")
    toy_index: IntProperty(name="Slot", min=0, max=9, default=0,
                           description="SetPlayerAttachedObject slot (index) read from a pasted line")

    # pawn map code
    pawn_path: StringProperty(name="Map Code", subtype='FILE_PATH',
                              description=".pwn / .txt with CreateObject / CreateDynamicObject lines")
    pawn_vehicles: BoolProperty(name="Vehicles", default=True,
                                description="Also place the vehicles (AddStaticVehicle, CreateVehicle...)")
    pawn_removes: BoolProperty(name="Remove Buildings", default=True,
                               description="Apply RemoveBuildingForPlayer lines: hide those game objects "
                                           "if they're in the scene")


# ============================================================================= vehicle ops
def _veh_colours(x, game, model):
    if x.veh_colour_mode == 'CUSTOM':
        return [x.veh_col1, x.veh_col2, x.veh_col3, x.veh_col4], -1
    if x.veh_colour_mode == 'SET':
        return None, x.veh_colour_set
    return None, -1


class GTATK_OT_import_vehicle(bpy.types.Operator):
    bl_idname = "gtatk.import_vehicle"
    bl_label = "Import Vehicle"
    bl_description = "Import the highlighted vehicle at the 3D cursor (wheels, paint and textures included)"
    bl_options = {'REGISTER', 'UNDO'}

    model: StringProperty(default="")

    @events.batched
    def execute(self, context):
        x = context.scene.gta_tk_x
        if not self.model and x.vehicles and x.vehicles[min(x.vehicles_index, len(x.vehicles) - 1)].mod_key:
            return import_mod_row(self, context, x.vehicles[min(x.vehicles_index, len(x.vehicles) - 1)].mod_key)
        try:
            game = _game(context)
            name = self.model or (x.vehicles[x.vehicles_index].name if x.vehicles else "")
            cols, cset = _veh_colours(x, game, name)
            coll, root = library.place_vehicle(context, game, name, cols, cset, x.veh_hide_damage, x.veh_hide_lod)
        except Exception as e:        # noqa
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, "Imported %s (colours %s)" % (coll.name, list(root.get("gta_colours", [])) if root else ""))
        return {'FINISHED'}


class GTATK_OT_clear_vehicle_search(bpy.types.Operator):
    bl_idname = "gtatk.clear_vehicle_search"
    bl_label = "Clear Search"
    bl_description = "Show every vehicle again"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        context.scene.gta_tk_x.veh_query = ""
        return {'FINISHED'}


class GTATK_OT_repaint_vehicle(bpy.types.Operator):
    bl_idname = "gtatk.repaint_vehicle"
    bl_label = "Repaint Selected"
    bl_description = "Apply the colour settings to the selected vehicle(s)"
    bl_options = {'REGISTER', 'UNDO'}

    randomize: BoolProperty(default=False)

    def execute(self, context):
        x = context.scene.gta_tk_x
        try:
            game = _game(context)
        except Exception as e:        # noqa
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        roots = {o for o in context.selected_objects if o.get("gta_vehicle")} or \
            {o.parent for o in context.selected_objects if o.parent and o.parent.get("gta_vehicle")}
        if not roots:
            self.report({'WARNING'}, "Select an imported vehicle")
            return {'CANCELLED'}
        for r in roots:
            model = r["gta_vehicle"]
            sets = game.car_colour_sets.get(model.lower()) or [(1, 1)]
            if self.randomize:
                cols = [random.randint(0, len(game.car_colours) - 1) for _ in range(4)]
            elif x.veh_colour_mode == 'CUSTOM':
                cols = [x.veh_col1, x.veh_col2, x.veh_col3, x.veh_col4]
            elif x.veh_colour_mode == 'SET':
                cols = list(sets[min(x.veh_colour_set, len(sets) - 1)])
            else:
                cols = list(random.choice(sets))
            ctx_sel = [r] + list(r.children_recursive)
            rgb = [tuple(c / 255.0 for c in game.colour_rgb(int(i))) for i in cols]
            vehicles.paint_vehicle(ctx_sel, rgb)
            r["gta_colours"] = [int(c) for c in cols]
        return {'FINISHED'}


# ============================================================================= objects / toys
class GTATK_OT_search_objects(bpy.types.Operator):
    bl_idname = "gtatk.search_objects"
    bl_label = "Search"
    bl_description = "Search every object (GTA + SA-MP) by model name or ID"

    def execute(self, context):
        x = context.scene.gta_tk_x
        try:
            game = _game(context)
        except Exception as e:        # noqa
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        res = samp.search_objects(game, x.obj_query, limit=2000)
        if x.obj_only_samp:
            res = [od for od in res if od.id in game.samp_ids]
        x.objects.clear()
        for od in res[:400]:
            it = x.objects.add()
            it.name = od.model
            it.oid = od.id
            it.txd = od.txd
            it.kind = od.kind
            it.source = "SA-MP" if od.id in game.samp_ids else "GTA"
        from . import mods
        q = x.obj_query.strip().lower()
        if not x.obj_only_samp:
            for m in mods.models('OBJECT'):          # custom / modded objects, by name
                if not q or q in m.name.lower():
                    it = x.objects.add()
                    it.name, it.oid, it.kind, it.source, it.mod_key = m.name, -1, 'objs', "Mod", m.key
        x.objects_index = 0
        self.report({'INFO'}, "%d results" % len(x.objects))
        return {'FINISHED'}


class GTATK_OT_import_object(bpy.types.Operator):
    bl_idname = "gtatk.import_object"
    bl_label = "Import Object"
    bl_description = "Import the highlighted object at the 3D cursor (vehicles get wheels and paint)"
    bl_options = {'REGISTER', 'UNDO'}

    @events.batched
    def execute(self, context):
        x = context.scene.gta_tk_x
        if not x.objects:
            self.report({'ERROR'}, "Search for an object first")
            return {'CANCELLED'}
        it = x.objects[min(x.objects_index, len(x.objects) - 1)]
        if it.mod_key:
            return import_mod_row(self, context, it.mod_key)
        try:
            game = _game(context)
        except Exception as e:        # noqa
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        if it.oid in game.vehicles:
            return bpy.ops.gtatk.import_vehicle(model=it.name)
        try:
            library.place_model(context, game, str(it.oid), tag_id=True)
        except Exception as e:        # noqa
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, "Imported %s (%d)" % (it.name, it.oid))
        return {'FINISHED'}


class GTATK_OT_parse_toy_line(bpy.types.Operator):
    bl_idname = "gtatk.parse_toy_line"
    bl_label = "Read Code"
    bl_description = "Fill model, bone, offset, rotation and scale from a SetPlayerAttachedObject line"

    def execute(self, context):
        x = context.scene.gta_tk_x
        line = x.toy_line.strip()
        if not line.endswith(";"):
            line += ";"
        pm = samp.parse_pawn(line)
        if not pm.attachments:
            self.report({'ERROR'}, "No SetPlayerAttachedObject(...) found")
            return {'CANCELLED'}
        idx, model, bone, ox, oy, oz, rx, ry, rz, sx, sy, sz = pm.attachments[0]
        try:
            game = _game(context)
        except Exception as e:        # noqa
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        od = game.objects.get(model)
        if od is None:
            self.report({'ERROR'}, "Model %d is not in the game or SA-MP IDE files" % model)
            return {'CANCELLED'}
        x.obj_query = str(model)
        x.objects.clear()                      # exact match only (no SA-MP filter, no prefix hits)
        it = x.objects.add()
        it.name, it.oid, it.txd, it.kind = od.model, od.id, od.txd, od.kind
        it.source = "SA-MP" if od.id in game.samp_ids else "GTA"
        x.objects_index = 0
        if not 1 <= bone <= 18:
            self.report({'WARNING'}, "Bone %d is not a SA-MP attachment bone (1-18) - kept %s" % (bone, x.toy_bone))
        if 1 <= bone <= 18:
            x.toy_bone = str(bone)
        x.toy_offset, x.toy_rot, x.toy_scale = (ox, oy, oz), (rx, ry, rz), (sx, sy, sz)
        x.toy_index = min(max(idx, 0), 9)
        return {'FINISHED'}


class GTATK_OT_attach_toy(bpy.types.Operator):
    bl_idname = "gtatk.attach_toy"
    bl_label = "Attach to Ped"
    bl_description = "Import the highlighted object and attach it to the selected ped's bone (SA-MP style)"
    bl_options = {'REGISTER', 'UNDO'}

    @events.batched
    def execute(self, context):
        x = context.scene.gta_tk_x
        arm = _arm(context)
        if arm is None:
            self.report({'ERROR'}, "Select a ped (import one first)")
            return {'CANCELLED'}
        if not x.objects:
            self.report({'ERROR'}, "Search and highlight an object first")
            return {'CANCELLED'}
        it = x.objects[min(x.objects_index, len(x.objects) - 1)]
        try:
            _coll, bone = library.attach_toy(context, _game(context), arm, it.oid, int(x.toy_bone),
                                            tuple(x.toy_offset), tuple(x.toy_rot), tuple(x.toy_scale),
                                            x.toy_index)
        except Exception as e:        # noqa
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, "Attached %s to %s" % (it.name, bone.name.strip()))
        return {'FINISHED'}


class GTATK_OT_import_pawn(bpy.types.Operator, ImportHelper):
    bl_idname = "gtatk.import_pawn"
    bl_label = "Import Map Code (.pwn)"
    bl_description = ("Place objects/vehicles from Pawn map code (CreateObject, CreateDynamicObject, "
                      "AddStaticVehicle, RemoveBuildingForPlayer...)")
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ".pwn"
    filter_glob: StringProperty(default="*.pwn;*.inc;*.txt;*.p", options={'HIDDEN'})

    _timer = None

    def invoke(self, context, event):
        x = context.scene.gta_tk_x
        if x.pawn_path:
            self.filepath = bpy.path.abspath(x.pawn_path)
        return ImportHelper.invoke(self, context, event)

    @events.batched
    def execute(self, context):
        context.scene.gta_tk_x.pawn_path = self.filepath
        try:
            text = open(self.filepath, "r", encoding="utf-8", errors="replace").read()
        except Exception as e:        # noqa
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        return import_pawn_text(self, context, text, os.path.splitext(os.path.basename(self.filepath))[0])


class GTATK_OT_paste_pawn(bpy.types.Operator):
    bl_idname = "gtatk.paste_pawn"
    bl_label = "Paste Map Code"
    bl_description = ("Place the objects/vehicles of Pawn map code copied to the clipboard (CreateObject, "
                      "CreateDynamicObject, AddStaticVehicle, RemoveBuildingForPlayer...)")
    bl_options = {'REGISTER', 'UNDO'}
    text: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})       # tests (the clipboard is empty in background)

    @events.batched
    def execute(self, context):
        text = self.text or context.window_manager.clipboard
        if not samp.parse_pawn(text or "").has_content():
            self.report({'ERROR'}, "The clipboard has no SA-MP map code (CreateObject... lines)")
            return {'CANCELLED'}
        return import_pawn_text(self, context, text, "pasted")


def import_pawn_text(op, context, text, name):
    """Place the objects / vehicles of Pawn map code (file or clipboard). name: import label after 'SA-MP '."""
    x = context.scene.gta_tk_x
    p = context.scene.gta_tk
    try:
        game = _game(context)
    except Exception as e:        # noqa
        op.report({'ERROR'}, str(e))
        return {'CANCELLED'}
    pm = samp.parse_pawn(text)
    removed = samp.apply_removes(pm) if x.pawn_removes else 0
    vcount = 0
    if x.pawn_vehicles:
        for model, vx, vy, vz, ang, c1, c2 in pm.vehicles[:300]:
            if model not in game.vehicles:
                continue
            sets = game.car_colour_sets.get(game.vehicles[model].model.lower()) or [(1, 1)]
            pick = list(random.choice(sets))
            cols = [c1 if c1 >= 0 else pick[0], c2 if c2 >= 0 else pick[1]]
            try:
                vehicles.import_vehicle(context, game, str(model), colours=cols,
                                        location=(vx, vy, vz), rotation_z=math.radians(ang))
                vcount += 1
            except Exception as e:  # noqa
                print("[GTA SA Toolkit] vehicle %s: %s" % (model, e))
    label = "SA-MP " + name
    plan = samp.pawn_plan(game, pm, label)
    extra = ", %d vehicles, %d buildings removed" % (vcount, removed)
    if pm.skipped:
        extra += ", %d lines skipped (variables/expressions)" % pm.skipped
    if not plan.insts:
        op.report({'INFO'}, "0 objects" + extra)
        return {'FINISHED'}
    from . import ui as main_ui
    if main_ui._JOB["job"] is not None:
        op.report({'WARNING'}, "A map import is already running")
        return {'CANCELLED'}
    from . import preview
    preview.clear()
    job = mapimport.MapImportJob(context, game, p, plan, name=label)
    try:
        while not job.step(1e9):
            pass
    finally:
        job.finish()
    msg = "%d objects" % job.created
    if plan.skipped_placed:
        msg += " (%d skipped, already placed)" % plan.skipped_placed
    msg += extra
    if job.errors:
        msg += ", %d problems (see console)" % len(job.errors)
        for e in job.errors[:50]:
            print("[GTA SA Toolkit]", e)
    op.report({'INFO'}, msg)
    return {'FINISHED'}


# ============================================================================= weapons
def _weapon(context):
    x = context.scene.gta_tk_x
    if not x.weapons:
        return None
    return x.weapons[min(x.weapons_index, len(x.weapons) - 1)]


class GTATK_OT_import_weapon(bpy.types.Operator):
    bl_idname = "gtatk.import_weapon"
    bl_label = "Import Weapon"
    bl_description = "Import the highlighted weapon model at the 3D cursor"
    bl_options = {'REGISTER', 'UNDO'}

    @events.batched
    def execute(self, context):
        w = _weapon(context)
        if w is None:
            self.report({'ERROR'}, "Press 'Scan Game Files' first")
            return {'CANCELLED'}
        try:
            library.place_weapon(context, _game(context), w.oid)
        except Exception as e:        # noqa
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, "Imported %s (%s)" % (w.name, w.model))
        return {'FINISHED'}


class GTATK_OT_give_weapon(bpy.types.Operator):
    bl_idname = "gtatk.give_weapon"
    bl_label = "Give to Ped"
    bl_description = "Put the highlighted weapon in the selected ped's hand"
    bl_options = {'REGISTER', 'UNDO'}

    @events.batched
    def execute(self, context):
        x = context.scene.gta_tk_x
        w = _weapon(context)
        arm = _arm(context)
        if w is None or arm is None:
            self.report({'ERROR'}, "Select a ped (armature) and a weapon")
            return {'CANCELLED'}
        try:
            library.place_weapon(context, _game(context), w.oid, arm, x.weapon_hand)
        except Exception as e:        # noqa
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, "%s -> %s" % (w.name, arm.name))
        return {'FINISHED'}


def _label(layout, text, icon_value, icon):
    """Item name with the model's thumbnail when the asset cache has one, else the type icon."""
    if icon_value:
        layout.label(text=text, icon_value=icon_value)
    else:
        layout.label(text=text, icon=icon)


class GTATK_UL_weapons(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        r = layout.row()
        _label(r, item.name, thumbs.icon(item.model), 'MOD_PHYSICS')
        r.label(text=("ID %d" % item.wid) if item.wid >= 0 else item.model)
        library.draw_star(r, 'WEAPON', item.oid, item.name, str(item.oid))

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        cat = context.scene.gta_tk_x.weapon_cat
        flt = []
        needle = self.filter_name.lower()
        for it in items:
            ok = (cat == 'ALL' or it.cat == cat) and (not needle or needle in it.name.lower() or needle in it.model.lower())
            flt.append(self.bitflag_filter_item if ok else 0)
        return flt, []


# ============================================================================= UI
class GTATK_UL_vehicles(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        r = layout.row()
        if item.mod_key:
            _label(r, item.name, thumbs.item_icon('VEHICLE', item.mod_key), 'AUTO')
            r.label(text="Mod")
            library.draw_star(r, 'VEHICLE', item.mod_key, item.name, "Mod")
            return
        _label(r, item.label or item.name, thumbs.item_icon('VEHICLE', item.vid), 'AUTO')
        r.label(text="%d · %s" % (item.vid, item.name))
        library.draw_star(r, 'VEHICLE', item.vid, item.label or item.name, str(item.vid))

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        q = context.scene.gta_tk_x.veh_query.strip().lower()
        if not q:
            return [], []
        return [self.bitflag_filter_item if veh_matches(it, q) else 0 for it in items], []


class GTATK_UL_peds(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        r = layout.row()
        key = item.mod_key or (str(item.pid) if item.pid >= 0 else item.name.lower())
        _label(r, item.name.lower(), thumbs.item_icon('PED', key), 'OUTLINER_OB_ARMATURE')
        sub = r.row()
        sub.alignment = 'RIGHT'
        sub.label(text="%d · %s" % (item.pid, item.label) if item.pid >= 0 else item.label)
        library.draw_star(r, 'PED', key, item.name.lower(), str(item.pid) if item.pid >= 0 else "")

    def filter_items(self, context, data, propname):
        x = context.scene.gta_tk_x
        q, cat = x.ped_query.strip().lower(), x.ped_cat
        if not q and cat == 'ALL':
            return [], []
        return [self.bitflag_filter_item if ped_matches(it, q, cat) else 0 for it in getattr(data, propname)], []


class GTATK_OT_import_ped(bpy.types.Operator):
    bl_idname = "gtatk.import_ped"
    bl_label = "Import Ped"
    bl_description = "Import the highlighted ped at the 3D cursor (standing up when 'Stand peds up' is on)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return len(context.scene.gta_tk_x.peds) > 0

    @events.batched
    def execute(self, context):
        x = context.scene.gta_tk_x
        it = x.peds[min(x.peds_index, len(x.peds) - 1)]
        if it.mod_key:
            return import_mod_row(self, context, it.mod_key)
        try:
            coll = library.place_model(context, _game(context), str(it.pid) if it.pid >= 0 else it.name)
        except Exception as e:           # noqa
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, "Imported %s" % coll.name)
        return {'FINISHED'}


class GTATK_UL_objects(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        r = layout.row()
        if item.mod_key:
            _label(r, item.name, thumbs.item_icon('OBJECT', item.mod_key), 'OBJECT_DATA')
            r.label(text="Mod")
            library.draw_star(r, 'OBJECT', item.mod_key, item.name, "Mod")
            return
        _label(r, item.name, thumbs.item_icon('OBJECT', item.oid), 'OBJECT_DATA' if item.source == "GTA" else 'MOD_CLOTH')
        r.label(text="%d  %s" % (item.oid, item.source))
        library.draw_star(r, _obj_kind(item), item.oid, item.name, str(item.oid))


def _obj_kind(item):
    """Library kind of an object-list row, from what the scan stored (no game lookups in draw)."""
    if item.kind == 'cars':
        return 'VEHICLE'
    if item.kind == 'weap' or item.oid in weapons.WEAPONS:
        return 'WEAPON'
    if item.kind == 'peds':
        return 'PED'
    return 'TOY' if item.source == "SA-MP" else 'OBJECT'


class _Panel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "GTA"


class GTATK_PT_vehicles(_Panel, bpy.types.Panel):
    bl_label = "Vehicles"
    bl_parent_id = "GTATK_PT_game"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        x = context.scene.gta_tk_x
        l = self.layout
        if not x.vehicles:
            l.label(text="Scan the game files to list vehicles", icon='INFO')
        r = l.row(align=True)
        ui.prop_ph(r, x, "veh_query", "Name or ID, e.g. infernus, 411", text="", icon='VIEWZOOM')
        if x.veh_query:
            r.operator("gtatk.clear_vehicle_search", text="", icon='X')
        l.template_list("GTATK_UL_vehicles", "", x, "vehicles", x, "vehicles_index", rows=6)
        if x.vehicles and x.vehicles[min(x.vehicles_index, len(x.vehicles) - 1)].mod_key:
            l.prop_search(x.vehicles[min(x.vehicles_index, len(x.vehicles) - 1)], "base", x, "vehicles",
                          icon='LINKED')
        l.prop(x, "veh_colour_mode")
        if x.veh_colour_mode == 'SET':
            l.prop(x, "veh_colour_set")
        elif x.veh_colour_mode == 'CUSTOM':
            r = l.row(align=True)
            r.prop(x, "veh_col1", text="")
            r.prop(x, "veh_col2", text="")
            r.prop(x, "veh_col3", text="")
            r.prop(x, "veh_col4", text="")
        r = l.row()
        r.prop(x, "veh_hide_damage")
        r.prop(x, "veh_hide_lod")
        r = l.row(align=True)
        r.scale_y = 1.3
        r.operator("gtatk.import_vehicle", text="Import", icon='IMPORT')
        if not tabs.has_section('VEHICLE', "st_paint"):      # with Studio its Colours section does this
            r = l.row(align=True)
            r.operator("gtatk.repaint_vehicle", icon='BRUSH_DATA').randomize = False
            r.operator("gtatk.repaint_vehicle", text="Random Paint", icon='FILE_REFRESH').randomize = True


class GTATK_PT_objects(_Panel, bpy.types.Panel):
    bl_label = "Objects & SA-MP Toys"
    bl_parent_id = "GTATK_PT_game"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        x = context.scene.gta_tk_x
        l = self.layout
        r = l.row(align=True)
        r.prop(x, "obj_query", text="", icon='VIEWZOOM')
        r.operator("gtatk.search_objects", text="", icon='VIEWZOOM')
        l.prop(x, "obj_only_samp")
        l.template_list("GTATK_UL_objects", "", x, "objects", x, "objects_index", rows=6)
        l.operator("gtatk.import_object", icon='IMPORT')
        b = l.box()
        b.label(text="Attach as Toy (SetPlayerAttachedObject)", icon='MOD_CLOTH')
        r = b.row(align=True)
        r.prop(x, "toy_line", text="")
        r.operator("gtatk.parse_toy_line", text="", icon='PASTEDOWN')
        b.prop(x, "toy_bone")
        b.prop(x, "toy_offset")
        b.prop(x, "toy_rot")
        b.prop(x, "toy_scale")
        b.operator("gtatk.attach_toy", icon='BONE_DATA')
        l.prop(x, "ped_auto_pose")


class GTATK_PT_pawn(_Panel, bpy.types.Panel):
    bl_label = "SA-MP Map Code"
    bl_parent_id = "GTATK_PT_game"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        x = context.scene.gta_tk_x
        l = self.layout
        c = l.column(align=True)
        c.prop(x, "pawn_vehicles")
        c.prop(x, "pawn_removes")
        r = l.row(align=True)
        r.operator("gtatk.import_pawn", text="Import .pwn", icon='FILE_SCRIPT')
        r.operator("gtatk.paste_pawn", text="Paste", icon='PASTEDOWN')


classes = (
    GTATK_VehItem, GTATK_PedItem, GTATK_WeapItem, GTATK_ObjItem, GTATK_ExtraProps,
    GTATK_UL_peds, GTATK_OT_import_ped,
    GTATK_OT_import_weapon, GTATK_OT_give_weapon, GTATK_UL_weapons,
    GTATK_OT_import_vehicle, GTATK_OT_clear_vehicle_search, GTATK_OT_repaint_vehicle,
    GTATK_OT_search_objects, GTATK_OT_import_object, GTATK_OT_parse_toy_line, GTATK_OT_attach_toy,
    GTATK_OT_import_pawn, GTATK_OT_paste_pawn,
    GTATK_UL_vehicles, GTATK_UL_objects,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.gta_tk_x = PointerProperty(type=GTATK_ExtraProps)


def unregister():
    del bpy.types.Scene.gta_tk_x
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
