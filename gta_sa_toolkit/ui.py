# GTA SA Toolkit - properties, operators and panels
import os

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       IntProperty, PointerProperty, StringProperty)
from bpy_extras.io_utils import ImportHelper

from . import anim as anim_ops
from . import events, mapimport, preview
from .formats import ifp as ifp_fmt

_IFP_CACHE = {}
_JOB = {"job": None}


def load_ifp(path, reload=False):
    path = bpy.path.abspath(path)
    key = os.path.normcase(os.path.abspath(path))
    if reload or key not in _IFP_CACHE:
        _IFP_CACHE[key] = ifp_fmt.IfpFile.load(path)
    return _IFP_CACHE[key]


def ped_label(arm):
    """The ped's name for the panel: its collection minus ".dff" ("bmyst.dff.001" -> "bmyst"), else the
    armature's name. The armature object stays "Root": the name of the root frame in the DFF."""
    for c in arm.users_collection:
        n = c.name
        if n.lower().endswith(".dff") or ".dff." in n.lower():
            return n[:n.lower().rindex(".dff")]
    return arm.name


def in_game_folder(path, game_root):
    """True when path is inside the game folder (string check only: safe in draw())."""
    if not path or not game_root:
        return False
    f = os.path.normcase(os.path.abspath(bpy.path.abspath(path)))
    g = os.path.normcase(os.path.abspath(bpy.path.abspath(game_root)))
    return f == g or f.startswith(g.rstrip("\\/") + os.sep)


def prop_ph(layout, data, prop, placeholder, **kw):
    """layout.prop with grey hint text in an empty field (Blender versions without it: no hint)."""
    try:
        layout.prop(data, prop, placeholder=placeholder, **kw)
    except TypeError:
        layout.prop(data, prop, **kw)


def set_bone_selected(arm, pb, state):
    # Blender 5 moved bone selection; support old and new API
    for target in (pb, pb.bone):
        if hasattr(target, "select"):
            try:
                target.select = state
                return
            except (AttributeError, TypeError):
                pass
    if hasattr(pb.bone, "select_set"):
        pb.bone.select_set(state)


def bone_is_selected(pb):
    for target in (pb, pb.bone):
        if hasattr(target, "select"):
            return bool(target.select)
    return False


def get_armature(context):
    ob = getattr(context, "active_object", None) or context.view_layer.objects.active
    if ob is None:
        return None
    if ob.type == 'ARMATURE':
        return ob
    if ob.parent and ob.parent.type == 'ARMATURE':
        return ob.parent
    for c in ob.children:
        if c.type == 'ARMATURE':
            return c
    for m in getattr(ob, "modifiers", []):
        if m.type == 'ARMATURE' and m.object:
            return m.object
    return None


def stand_up(context, arm, game=None):
    """Imported SA peds are in their bind pose (lying down). Give them the game's idle pose."""
    try:
        root = bpy.path.abspath(context.scene.gta_tk.game_root)
        f = load_ifp(os.path.join(root, "anim", "ped.ifp"))
        a = f.find("IDLE_stance") or f.find("IDLE_armed")
        if a is None:
            return False
        o = anim_ops.ApplyOptions()
        o.fps, o.start_frame, o.new_action, o.clear_pose = 30.0, 1.0, True, True
        anim_ops.apply_animation(arm, a, o, action_name="%s Idle Pose" % arm.name)
        return True
    except Exception as e:           # noqa
        print("[GTA SA Toolkit] idle pose:", e)
        return False


def ifp_fps(props, scene):
    return {'30': 30.0, '60': 60.0, 'SCENE': scene.render.fps / scene.render.fps_base,
            'CUSTOM': props.ifp_custom_fps}[props.ifp_fps_mode]


# ============================================================================= properties
def _plan_changed(self, context):
    """Any setting that changes what Import Map would place makes the preview out of date."""
    preview.clear()


class GTATK_SectionItem(bpy.types.PropertyGroup):
    use: BoolProperty(name="Import", default=False, update=_plan_changed)
    count: IntProperty()
    lods: IntProperty()
    cx: FloatProperty()
    cy: FloatProperty()


def _rename_import(self, context):
    if self.coll is not None:
        self.coll.name = mapimport.import_label(self.num, self.name)


class GTATK_ImportItem(bpy.types.PropertyGroup):
    """One map import (name = what the list shows; double-click to rename)."""
    name: StringProperty(name="Name", update=_rename_import)
    num: IntProperty()
    objects: IntProperty()
    when: StringProperty()
    cx: FloatProperty()               # area centre / radius (radius 0 = sections or map code, no area)
    cy: FloatProperty()
    radius: FloatProperty()
    coll: PointerProperty(type=bpy.types.Collection)


class GTATK_AnimItem(bpy.types.PropertyGroup):
    bones: IntProperty()
    duration: FloatProperty()


class GTATK_AnimHit(bpy.types.PropertyGroup):
    """An all-animations search result (name = animation)."""
    source: StringProperty()
    duration: FloatProperty()


_HIT_BUSY = {"on": False}


def _search_anims(self, context):
    """Search box: every animation of every game IFP (plus the listed file when it's another one)."""
    from . import ifplist
    if _HIT_BUSY["on"]:
        return
    _HIT_BUSY["on"] = True
    try:
        self.anim_hits.clear()
        self.anim_hits_index = -1
        q = self.anim_query.strip()
        if not q:
            return
        hits = ifplist.search(context, q)
        cur = current_source(self)
        if cur and not (cur == "ped.ifp" or cur.startswith("anim.img/")):       # a file of your own
            low = q.lower()
            hits = [(a.name, cur, a.duration) for a in self.anims if low in a.name.lower()] + hits
        for name, src, dur in hits:
            h = self.anim_hits.add()
            h.name, h.source, h.duration = name, src, dur
    finally:
        _HIT_BUSY["on"] = False


def _pick_hit(self, context):
    """Picking a search result lists its file and highlights the animation."""
    if _HIT_BUSY["on"] or not 0 <= self.anim_hits_index < len(self.anim_hits):
        return
    h = self.anim_hits[self.anim_hits_index]
    name, src = h.name, h.source
    try:
        choose_source(context, src)                 # also empties the search box and the results
    except Exception as e:                          # noqa
        print("[GTA SA Toolkit] IFP search:", e)
        return
    for i, a in enumerate(self.anims):
        if a.name == name:
            self.anims_index = i
            break


class GTATK_Props(bpy.types.PropertyGroup):
    # ---- game
    game_root: StringProperty(name="GTA SA Folder", subtype='DIR_PATH',
                              description="Folder that contains gta_sa.exe, data\\ and models\\")
    sections: CollectionProperty(type=GTATK_SectionItem)
    sections_index: IntProperty()

    # ---- map import
    map_source: EnumProperty(update=_plan_changed, name="Import", items=(
        ('SECTIONS', "Sections", "Every object of the sections ticked in the list"),
        ('AREA', "Area", "Everything inside a circle, across every section"),
    ), default='SECTIONS')
    limit_to_area: BoolProperty(update=_plan_changed, name="Limit to Area", default=False,
                                description="Only keep objects of the checked sections that are inside the area")
    area_center: EnumProperty(update=_plan_changed, name="Center", items=(
        ('CURSOR', "3D Cursor", "Use the 3D cursor X/Y (GTA world coordinates)"),
        ('COORDS', "Coordinates", "Type X/Y world coordinates"),
    ), default='CURSOR')
    area_x: FloatProperty(update=_plan_changed, name="X", default=2495.0)
    area_y: FloatProperty(update=_plan_changed, name="Y", default=-1687.0)
    area_radius: FloatProperty(update=_plan_changed, name="Radius", default=250.0, min=1.0, max=10000.0, unit='LENGTH')
    area_margin: FloatProperty(update=_plan_changed, name="Include Big Objects Within", default=80.0, min=0.0,
                               max=1000.0, unit='LENGTH',
                               description="Big models (ground tiles, buildings) whose centre is up to this far "
                                           "outside the circle are imported too, so the area's edge has no holes")
    include_stream: BoolProperty(update=_plan_changed, name="Streamed IPLs", default=True,
                                 description="Include the binary *_streamN.ipl files from the IMG archives - "
                                             "most of the map lives there")
    skip_lod: BoolProperty(update=_plan_changed, name="Skip LOD models", default=True)
    only_lod: BoolProperty(update=_plan_changed, name="Only LOD models", default=False,
                           description="Import just the low-detail LOD models (whole-map overview)")
    include_interiors: BoolProperty(update=_plan_changed, name="Interiors", default=False,
                                    description="Also import objects placed in interiors")
    load_textures: BoolProperty(name="Textures", default=True)
    pack_images: BoolProperty(name="Pack Images", default=True,
                              description="Embed the imported GTA textures in the .blend when you save "
                                          "(otherwise they are lost on reload)")
    load_collisions: BoolProperty(name="Collisions", default=False)
    use_mat_split: BoolProperty(name="Material Split", default=False)
    share_materials: BoolProperty(name="Share Materials", default=True,
                                  description="Reuse one material for identical texture/colour combinations "
                                              "across all imported models (much faster, lighter files)")
    placement: EnumProperty(name="Placement", items=(
        ('INSTANCE', "Collection Instances", "Fast and light: each placement is an instance of one library model"),
        ('LINKED', "Editable Objects", "Each placement is a real object (mesh data still shared)"),
    ), default='INSTANCE')
    skip_placed: BoolProperty(update=_plan_changed, name="Skip Already Placed", default=True,
                              description="Leave out objects that are already in the scene (same model ID "
                                          "within 1 cm), so importing an overlapping area adds only what's missing")
    hide_while_importing: BoolProperty(name="Hide While Importing", default=True,
                                       description="Keep the new map objects hidden in the viewport until the "
                                                   "import finishes (also shown again if it's stopped or fails)")
    max_instances: IntProperty(update=_plan_changed, name="Max Objects", default=30000, min=0,
                               description="Safety limit (0 = no limit). With an area, nearest objects win")
    progress: StringProperty()
    imports: CollectionProperty(type=GTATK_ImportItem)
    imports_index: IntProperty()
    import_counter: IntProperty(description="Number of the last map import in this file")

    # ---- model from IMG
    model_name: StringProperty(name="Model", description="Model name in gta3.img, e.g. bmyst, cesar, infernus")

    # ---- IFP import
    ifp_path: StringProperty(name="IFP", subtype='FILE_PATH')
    # the IFP the Peds tab and Studio's Animate tab both list: "ped.ifp", "anim.img/<name>.ifp" or a file path
    ifp_source: StringProperty(name="IFP Source")
    anims: CollectionProperty(type=GTATK_AnimItem)
    anims_index: IntProperty()
    anim_hits: CollectionProperty(type=GTATK_AnimHit)
    anim_hits_index: IntProperty(default=-1, update=_pick_hit)
    anim_query: StringProperty(name="Search", options={'TEXTEDIT_UPDATE'}, update=_search_anims,
                               description="Show only animations whose name contains this")
    ifp_fps_mode: EnumProperty(name="Frame Rate", items=(
        ('30', "30 fps", ""), ('60', "60 fps", ""), ('SCENE', "Scene fps", ""), ('CUSTOM', "Custom", "")),
        default='30')
    ifp_custom_fps: FloatProperty(name="fps", default=25.0, min=1.0)
    ifp_snap: BoolProperty(name="Snap to Frames", default=False)
    ifp_start: EnumProperty(name="Start", items=(
        ('FIRST', "Frame 1", ""), ('CURRENT', "Current Frame", ""),
        ('END', "After Current Action", "Append after the end of the armature's current action")),
        default='FIRST')
    ifp_root: EnumProperty(name="Root Motion", items=(
        ('BONE', "Root Bone", "Keep the root translation on the root bone (as in the game)"),
        ('OBJECT', "Armature Object", "Move the horizontal root travel onto the armature object"),
        ('INPLACE', "In Place", "Remove horizontal root travel"),
        ('NONE', "Ignore Root Position", "Root keeps its first position")), default='BONE')
    ifp_new_action: BoolProperty(name="New Action", default=True,
                                 description="Create a new Action per animation (off = insert into current action)")
    ifp_clear_pose: BoolProperty(name="Reset Pose First", default=True)
    ifp_adjust_range: BoolProperty(name="Fit Scene Range", default=True)
    ifp_set_scene_fps: BoolProperty(name="Set Scene fps", default=True)

    # ---- IFP export
    exp_path: StringProperty(name="Output", subtype='FILE_PATH')
    # numbers fixed so saved files keep their choice (REPLACE was first and the default in older versions)
    exp_mode: EnumProperty(name="Mode", items=(
        ('NEW', "New IFP", "Write a new IFP that only has this animation", 2),
        ('REPLACE', "Replace in IFP", "Replace the animation with the same name in a base IFP", 0),
        ('APPEND', "Append to IFP", "Add as a new animation to a base IFP", 1)), default='NEW')
    exp_base: StringProperty(name="Base IFP", subtype='FILE_PATH',
                             description="IFP to modify (defaults to the loaded one)")
    exp_anim_name: StringProperty(name="Anim Name", description="Empty = action's IFP name or action name")
    exp_ifp_name: StringProperty(name="Package Name", default="ped")
    exp_format: EnumProperty(name="Format", items=(('ANP3', "ANP3 (SA)", ""), ('ANPK', "ANPK (III/VC)", "")),
                             default='ANP3')
    exp_step: IntProperty(name="Sample Step", default=1, min=1, max=10)
    exp_use_scene_range: BoolProperty(name="Use Scene Range", default=False)


# ============================================================================= game / map operators
class GTATK_OT_scan_game(bpy.types.Operator):
    bl_idname = "gtatk.scan_game"
    bl_label = "Scan Game"
    bl_description = "Read gta.dat, IDEs, IPLs and IMG directories"

    def execute(self, context):
        p = context.scene.gta_tk
        try:
            game = mapimport.get_game(p.game_root, reload=True)
        except Exception as e:           # noqa
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        old = {s.name: s.use for s in p.sections}
        p.sections.clear()
        for sec in game.sections:
            insts = sec.all_insts(True)
            if not insts:
                continue
            it = p.sections.add()
            it.name = sec.name
            it.use = old.get(sec.name, False)
            it.count = len(insts)
            it.lods = sum(1 for i in insts if i.is_lod)
            if sec.bounds:
                it.cx = (sec.bounds[0] + sec.bounds[2]) * 0.5
                it.cy = (sec.bounds[1] + sec.bounds[3]) * 0.5
        try:
            from . import ui_extra
            ui_extra.fill_after_scan(context, game)
        except Exception as e:           # noqa
            print("[GTA SA Toolkit] vehicle list:", e)
        msg = "%d sections, %d object types, %d vehicles, %d IMG entries" % (
            len(p.sections), len(game.objects), len(game.vehicles), len(game.imgs.index))
        if game.warnings:
            msg += " (%d warnings, see console)" % len(game.warnings)
            for w in game.warnings:
                print("[GTA SA Toolkit]", w)
        try:
            from . import mods
            if mods.folders():
                found = mods.refresh(context, game)
                msg += ", %d custom models" % len(found)
        except Exception as e:           # noqa - a broken mod folder must not stop the scan
            print("[GTA SA Toolkit] mod folders:", e)
        try:
            from . import ifplist
            n = len(ifplist.build_index(context))
            msg += ", %d animations (%.1f s)" % (n, ifplist._IDX["secs"])
        except Exception as e:           # noqa
            print("[GTA SA Toolkit] animation index:", e)
        self.report({'INFO'}, msg)
        events.game_scanned()
        return {'FINISHED'}


class GTATK_OT_sections_select(bpy.types.Operator):
    bl_idname = "gtatk.sections_select"
    bl_label = "Select Sections"
    action: EnumProperty(items=(('ALL', "All", ""), ('NONE', "None", ""), ('INVERT', "Invert", "")))

    def execute(self, context):
        for s in context.scene.gta_tk.sections:
            s.use = {'ALL': True, 'NONE': False, 'INVERT': not s.use}[self.action]
        return {'FINISHED'}


class GTATK_OT_cursor_to_section(bpy.types.Operator):
    bl_idname = "gtatk.cursor_to_section"
    bl_label = "Cursor to Section"
    bl_description = "Move the 3D cursor to the centre of the highlighted section"

    def execute(self, context):
        p = context.scene.gta_tk
        if not p.sections:
            return {'CANCELLED'}
        s = p.sections[p.sections_index]
        context.scene.cursor.location = (s.cx, s.cy, 0.0)
        return {'FINISHED'}


def _area_center(context):
    p = context.scene.gta_tk
    if p.area_center == 'CURSOR':
        c = context.scene.cursor.location
        return (c.x, c.y)
    return (p.area_x, p.area_y)


def _status(context, text):
    """Show text in Blender's status bar (None clears it)."""
    ws = getattr(context, "workspace", None)
    if ws is not None:
        try:
            ws.status_text_set(text)
        except Exception:                 # noqa
            pass


class GTATK_OT_import_map(bpy.types.Operator):
    bl_idname = "gtatk.import_map"
    bl_label = "Import Map"
    bl_description = "Import the chosen sections / area directly from the game files (Esc to stop)"
    bl_options = {'REGISTER', 'UNDO'}

    _timer = None

    def execute(self, context):
        if _JOB["job"] is not None:
            self.report({'WARNING'}, "A map import is already running")
            return {'CANCELLED'}
        preview.clear()
        p = context.scene.gta_tk
        try:
            game = mapimport.get_game(p.game_root)
            plan = mapimport.build_plan(game, p, _area_center(context))
        except Exception as e:           # noqa
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        if not plan.insts:
            self.report({'WARNING'}, "Nothing to import - tick sections or move the area")
            return {'CANCELLED'}
        try:
            _JOB["job"] = mapimport.MapImportJob(context, game, p, plan)
        except Exception as e:           # noqa
            _JOB["job"] = None
            self.report({'ERROR'}, "Could not start the import: %s" % e)
            return {'CANCELLED'}
        wm = context.window_manager
        wm.progress_begin(0, 1000)
        self._timer = wm.event_timer_add(0.02, window=context.window)
        wm.modal_handler_add(self)
        p.progress = "Starting: %d objects, %d models" % (len(plan.insts), len(plan.models))
        _status(context, "GTA map import: starting (%d objects, %d models)  ·  Esc to stop"
                % (len(plan.insts), len(plan.models)))
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        job = _JOB["job"]
        if event.type == 'ESC' and event.value == 'PRESS':
            return self._end(context, cancelled=True)
        # Undo/redo while the importer holds references to new datablocks would invalidate them
        # (and can crash Blender), so swallow those shortcuts until the import is finished.
        if event.type == 'Z' and (event.ctrl or event.oskey):
            return {'RUNNING_MODAL'}
        if event.type != 'TIMER' or job is None:
            return {'PASS_THROUGH'}
        try:
            done = job.step(0.3)
        except Exception as e:           # noqa - never leave a half-finished job behind
            import traceback
            traceback.print_exc()
            job.errors.append("Import stopped by an error: %s" % e)
            return self._end(context, cancelled=True)
        p = context.scene.gta_tk
        p.progress = job.progress_text()
        context.window_manager.progress_update(int(job.progress() * 1000))
        _status(context, job.status_text())
        if context.area:
            context.area.tag_redraw()
        for a in context.screen.areas:
            if a.type == 'VIEW_3D':
                a.tag_redraw()
        if done:
            return self._end(context)
        return {'RUNNING_MODAL'}

    def _end(self, context, cancelled=False):
        job = _JOB["job"]
        wm = context.window_manager
        if self._timer:
            wm.event_timer_remove(self._timer)
        wm.progress_end()
        _status(context, None)
        _JOB["job"] = None
        p = context.scene.gta_tk
        if job:
            try:
                job.finish()
            except Exception as e:       # noqa
                job.errors.append("finish: %s" % e)
            for e in job.errors[:50]:
                print("[GTA SA Toolkit]", e)
            p.progress = "%s: %d objects placed%s, %d models%s" % (
                "Stopped" if cancelled else "Done", job.created,
                (", %d skipped (already placed)" % job.plan.skipped_placed) if job.plan.skipped_placed else "",
                sum(1 for v in job.model_colls.values() if v),
                (", %d problems (see console)" % len(job.errors)) if job.errors else "")
            self.report({'WARNING' if cancelled or job.errors else 'INFO'}, p.progress)
            for a in context.screen.areas:
                if a.type == 'VIEW_3D':
                    for s in a.spaces:
                        if s.type == 'VIEW_3D' and s.clip_end < 20000:
                            s.clip_end = 20000
        return {'CANCELLED' if cancelled else 'FINISHED'}


class GTATK_OT_remove_import(bpy.types.Operator):
    bl_idname = "gtatk.remove_import"
    bl_label = "Remove Imported Map"
    bl_description = ("Delete the highlighted import's objects, plus the library models no other import "
                      "or object uses (one undo step)")
    bl_options = {'REGISTER', 'UNDO'}

    num: IntProperty(default=-1, options={'SKIP_SAVE'},
                     description="Import number (-1 = the one highlighted in the list)")

    @classmethod
    def poll(cls, context):
        return _JOB["job"] is None and len(context.scene.gta_tk.imports) > 0

    def _target(self, context):
        p = context.scene.gta_tk
        if self.num >= 0:
            return mapimport.find_import(p, self.num)[1]
        if 0 <= p.imports_index < len(p.imports):
            return p.imports[p.imports_index]
        return None

    def invoke(self, context, event):
        it = self._target(context)
        if it is None:
            return {'CANCELLED'}
        return context.window_manager.invoke_confirm(
            self, event, title="Remove %s?" % mapimport.import_label(it.num, it.name),
            message="Deletes its %d objects and library models nothing else uses." % it.objects,
            confirm_text="Remove", icon='WARNING') if bpy.app.version >= (4, 1, 0) else             context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        p = context.scene.gta_tk
        it = self._target(context)
        if it is None:
            self.report({'ERROR'}, "No such import")
            return {'CANCELLED'}
        label = mapimport.import_label(it.num, it.name)
        n_objs, n_models = mapimport.remove_import(context.scene, p, it.num)
        events.import_removed()
        p.progress = "Removed %s: %d objects, %d unused library models" % (label, n_objs, n_models)
        self.report({'INFO'}, p.progress)
        return {'FINISHED'}


class GTATK_OT_import_model(bpy.types.Operator):
    bl_idname = "gtatk.import_model"
    bl_label = "Import Model from IMG"
    bl_description = "Import the typed ped or model (with its textures) at the 3D cursor"
    bl_options = {'REGISTER', 'UNDO'}

    @events.batched
    def execute(self, context):
        p = context.scene.gta_tk
        if not p.model_name.strip():
            self.report({'ERROR'}, "Type a model name or ID first (e.g. bmyst, 7, infernus, 18645)")
            return {'CANCELLED'}
        try:
            game = mapimport.get_game(p.game_root)
            key = p.model_name.strip().lower()
            if any(v.model.lower() == key or str(v.id) == key for v in game.vehicles.values()):
                return bpy.ops.gtatk.import_vehicle(model=p.model_name.strip())
            from . import library
            coll = library.place_model(context, game, p.model_name.strip())
        except Exception as e:           # noqa
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, "Imported %s" % coll.name)
        return {'FINISHED'}


# ============================================================================= IFP operators
class GTATK_OT_ifp_load(bpy.types.Operator, ImportHelper):
    bl_idname = "gtatk.ifp_load"
    bl_label = "Open IFP"
    bl_description = "Open an IFP file and list its animations"
    filename_ext = ".ifp"
    filter_glob: StringProperty(default="*.ifp", options={'HIDDEN'})

    def invoke(self, context, event):
        p = context.scene.gta_tk
        if p.ifp_path:
            self.filepath = bpy.path.abspath(p.ifp_path)
        elif p.game_root:
            self.filepath = os.path.join(bpy.path.abspath(p.game_root), "anim", "ped.ifp")
        return ImportHelper.invoke(self, context, event)

    def execute(self, context):
        p = context.scene.gta_tk
        try:
            f = load_ifp(self.filepath, reload=True)
        except Exception as e:           # noqa
            self.report({'ERROR'}, "Could not read IFP: %s" % e)
            return {'CANCELLED'}
        from . import library
        p.ifp_path = self.filepath
        p.ifp_source = library.anim_source(p.game_root, self.filepath)
        fill_anim_list(p, f)
        self.report({'INFO'}, "%s: %s '%s', %d animations" % (os.path.basename(self.filepath), f.version,
                                                           f.name, len(f.animations)))
        return {'FINISHED'}


def current_source(p):
    """The IFP both animation lists show (Library source string), "" when none is chosen yet. Files
    from older versions only have ifp_path."""
    if p.ifp_source:
        return p.ifp_source
    if p.ifp_path:
        from . import library
        return library.anim_source(p.game_root, p.ifp_path)
    return ""


def choose_source(context, src):
    """Load an IFP source into the shared animation list. Returns the IfpFile."""
    from . import library
    p = context.scene.gta_tk
    f = library.load_anim_file(context, src)
    p.ifp_source = src
    if src == "ped.ifp":
        p.ifp_path = os.path.join(bpy.path.abspath(p.game_root).rstrip("\\/"), "anim", "ped.ifp")
    elif not src.startswith("anim.img/"):
        p.ifp_path = src
    p.anim_query = ""
    fill_anim_list(p, f)
    from . import ifplist
    ifplist.push_recent(src)
    return f


def _ifp_choice_items(self, context):
    from . import ifplist
    p = context.scene.gta_tk if context else None
    return ifplist.menu_items(p.game_root if p is not None else "")


class GTATK_OT_ifp_choose(bpy.types.Operator):
    bl_idname = "gtatk.ifp_choose"
    bl_label = "Choose IFP"
    bl_description = "Pick the IFP to list animations from (shared by the Peds and Animate tabs)"
    bl_property = "source"
    source: EnumProperty(items=_ifp_choice_items, name="IFP")
    filepath: StringProperty(subtype='FILE_PATH', options={'HIDDEN', 'SKIP_SAVE'})
    filter_glob: StringProperty(default="*.ifp", options={'HIDDEN'})

    def invoke(self, context, event):
        if self.source == 'OTHER':
            p = context.scene.gta_tk
            self.filepath = bpy.path.abspath(p.ifp_path) if p.ifp_path else \
                os.path.join(bpy.path.abspath(p.game_root), "anim", "")
            context.window_manager.fileselect_add(self)
            return {'RUNNING_MODAL'}
        return self.execute(context)

    def execute(self, context):
        from . import ifplist, library
        src = ifplist.source_of(self.source)
        if src == 'OTHER':
            if not self.filepath:
                return {'CANCELLED'}
            src = library.anim_source(context.scene.gta_tk.game_root, bpy.path.abspath(self.filepath))
        try:
            f = choose_source(context, src)
        except Exception as e:           # noqa
            self.report({'ERROR'}, "Could not read the IFP: %s" % e)
            return {'CANCELLED'}
        self.report({'INFO'}, "%s: %d animations" % (os.path.basename(src), len(f.animations)))
        return {'FINISHED'}


def draw_ifp_browser(layout, context, rows=8):
    """The IFP dropdown, search and animation list: the Peds tab's IFP Animation section, and Studio's
    Add Clip through api.draw_ifp_browser. Stored values only."""
    from . import library
    p = context.scene.gta_tk
    src = current_source(p)
    r = layout.row(align=True)
    r.operator_menu_enum("gtatk.ifp_choose", "source", icon='FILEBROWSER',
                         text=library.source_label(src) if src else "Choose an IFP…")
    if src:
        library.draw_star(r, 'IFP', src, library.source_label(src), src)
    r = layout.row(align=True)
    prop_ph(r, p, "anim_query", "Search all animations", text="", icon='VIEWZOOM')
    if p.anim_query:
        r.operator("gtatk.anim_search_clear", text="", icon='X')
        if p.anim_hits:
            layout.template_list("GTATK_UL_anim_hits", "", p, "anim_hits", p, "anim_hits_index", rows=rows)
        else:
            layout.label(text="No animation matches", icon='INFO')
    elif p.anims:
        layout.template_list("GTATK_UL_anims", "", p, "anims", p, "anims_index", rows=rows)


class GTATK_OT_anim_search_clear(bpy.types.Operator):
    bl_idname = "gtatk.anim_search_clear"
    bl_label = "Clear Search"
    bl_description = "Back to the listed file's animations"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        context.scene.gta_tk.anim_query = ""
        return {'FINISHED'}


def fill_anim_list(p, f):
    p.anims.clear()
    for a in f.animations:
        it = p.anims.add()
        it.name = a.name
        it.bones = len(a.bones)
        it.duration = a.duration
    p.anims_index = 0
    if not p.exp_base:
        p.exp_base = p.ifp_path
    p.exp_ifp_name = f.name or p.exp_ifp_name
    p.exp_format = f.version if f.version in ('ANP3', 'ANPK') else 'ANP3'


def _apply_options(context, arm, p):
    o = anim_ops.ApplyOptions()
    o.fps = ifp_fps(p, context.scene)
    o.snap = p.ifp_snap
    o.root_mode = p.ifp_root
    o.new_action = p.ifp_new_action
    o.clear_pose = p.ifp_clear_pose
    if p.ifp_start == 'FIRST':
        o.start_frame = 1.0
    elif p.ifp_start == 'CURRENT':
        o.start_frame = float(context.scene.frame_current)
    else:
        ad = arm.animation_data
        if ad and ad.action:
            # append after the current action, into that same action (like GTA Tools' "Load at End")
            o.start_frame = float(ad.action.frame_range[1])
            o.new_action = False
        else:
            o.start_frame = float(context.scene.frame_current)
    return o


class GTATK_OT_ifp_apply(bpy.types.Operator):
    bl_idname = "gtatk.ifp_apply"
    bl_label = "Apply to Armature"
    bl_description = "Apply the highlighted animation to the selected ped"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = context.scene.gta_tk
        arm = get_armature(context)
        if arm is None:
            self.report({'ERROR'}, "Select a ped (import one first)")
            return {'CANCELLED'}
        if not p.anims:
            self.report({'ERROR'}, "Open an IFP first")
            return {'CANCELLED'}
        from . import library
        name = p.anims[min(p.anims_index, len(p.anims) - 1)].name
        try:
            _act, missing, a = library.apply_anim(context, arm, current_source(p), name)
        except Exception as e:           # noqa
            self.report({'ERROR'}, "%s - open the IFP again" % e)
            return {'CANCELLED'}
        if missing:
            self.report({'WARNING'}, "Applied '%s'. Bones not found: %s" % (a.name, ", ".join(missing[:12])))
        else:
            self.report({'INFO'}, "Applied '%s' (%d bones, %.2fs)" % (a.name, len(a.bones), a.duration))
        return {'FINISHED'}


class GTATK_OT_ifp_import_all(bpy.types.Operator):
    bl_idname = "gtatk.ifp_import_all"
    bl_label = "Import All as Actions"
    bl_description = "Create one Action per animation in the IFP (switch them in the Action editor)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = context.scene.gta_tk
        arm = get_armature(context)
        if arm is None or not p.anims:
            self.report({'ERROR'}, "Open an IFP and select an armature")
            return {'CANCELLED'}
        from . import library
        src = current_source(p)
        try:
            f = library.load_anim_file(context, src)
        except Exception as e:           # noqa
            self.report({'ERROR'}, "Could not read the IFP: %s" % e)
            return {'CANCELLED'}
        opt = _apply_options(context, arm, p)
        opt.new_action = True
        opt.start_frame = 1.0
        opt.clear_pose = False
        prefix = (f.name or os.path.splitext(library.source_label(src))[0]) + ":"
        first = None
        for a in f.animations:
            act, _ = anim_ops.apply_animation(arm, a, opt, action_name=prefix + a.name)
            first = first or act
        if first:
            arm.animation_data.action = first
        self.report({'INFO'}, "Created %d actions" % len(f.animations))
        return {'FINISHED'}


class GTATK_OT_ifp_export(bpy.types.Operator):
    bl_idname = "gtatk.ifp_export"
    bl_label = "Export IFP"
    bl_description = "Bake the armature's current action into an IFP animation"

    def invoke(self, context, event):
        p = context.scene.gta_tk
        if in_game_folder(p.exp_path, p.game_root):
            msg = "This writes a file inside your GTA San Andreas folder"
            try:
                return context.window_manager.invoke_confirm(self, event, title="Write into the game folder?",
                                                             message=msg, confirm_text="Export", icon='WARNING')
            except TypeError:
                return context.window_manager.invoke_confirm(self, event)
        return self.execute(context)

    def execute(self, context):
        p = context.scene.gta_tk
        arm = get_armature(context)
        if arm is None or not arm.animation_data or not arm.animation_data.action:
            self.report({'ERROR'}, "Active armature has no action to export")
            return {'CANCELLED'}
        act = arm.animation_data.action
        out = bpy.path.abspath(p.exp_path)
        if not out:
            self.report({'ERROR'}, "Set an output file")
            return {'CANCELLED'}
        if not out.lower().endswith(".ifp"):
            out += ".ifp"
        name = p.exp_anim_name or act.get("ifp_anim_name") or act.name.split(":")[-1]
        sc = context.scene
        if p.exp_use_scene_range:
            f0, f1 = sc.frame_start, sc.frame_end
        else:
            f0, f1 = act.frame_range
        fps = ifp_fps(p, sc)
        try:
            anim = anim_ops.bake_action_to_anim(context, arm, act, name, fps, f0, f1, p.exp_step)
        except Exception as e:           # noqa
            self.report({'ERROR'}, "Could not bake the action: %s" % e)
            return {'CANCELLED'}
        if not anim.bones:
            self.report({'ERROR'}, "The action has no bone keys to export")
            return {'CANCELLED'}
        try:
            res = anim_ops.export_ifp([anim], out, fmt=p.exp_format, mode=p.exp_mode,
                                      base=bpy.path.abspath(p.exp_base or p.ifp_path), ifp_name=p.exp_ifp_name)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        for n in res["notes"]:
            self.report({'WARNING'}, n)
        _IFP_CACHE.pop(os.path.normcase(os.path.abspath(out)), None)
        self.report({'INFO'}, "Wrote '%s' (%s, %d bones, %d keys) to %s" % (
            name, res["format"], len(anim.bones), len(anim.bones[0].keyframes) if anim.bones else 0, out))
        return {'FINISHED'}


# ============================================================================= character tools
class GTATK_OT_reset_pose(bpy.types.Operator):
    bl_idname = "gtatk.reset_pose"
    bl_label = "Reset Pose"
    bl_options = {'REGISTER', 'UNDO'}
    selected_only: BoolProperty(default=False)

    def execute(self, context):
        arm = get_armature(context)
        if not arm:
            return {'CANCELLED'}
        for pb in arm.pose.bones:
            if self.selected_only and not bone_is_selected(pb):
                continue
            pb.location = (0, 0, 0)
            pb.rotation_quaternion = (1, 0, 0, 0)
            pb.rotation_euler = (0, 0, 0)
            pb.scale = (1, 1, 1)
        return {'FINISHED'}


class GTATK_OT_clear_anim(bpy.types.Operator):
    bl_idname = "gtatk.clear_anim"
    bl_label = "Unlink Action"
    bl_description = "Unlink the armature's action, reset the pose and put the armature back where it was"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        arm = get_armature(context)
        if not arm:
            return {'CANCELLED'}
        if arm.animation_data:
            arm.animation_data.action = None
        if "gta_rest_loc" in arm:            # root motion was moved onto the object
            arm.location = tuple(arm["gta_rest_loc"])
        for pb in arm.pose.bones:
            pb.location = (0, 0, 0)
            pb.rotation_quaternion = (1, 0, 0, 0)
            pb.scale = (1, 1, 1)
        return {'FINISHED'}


class GTATK_OT_select_keyed(bpy.types.Operator):
    bl_idname = "gtatk.select_keyed"
    bl_label = "Select Keyed Bones"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        arm = get_armature(context)
        if not arm or not arm.animation_data or not arm.animation_data.action:
            return {'CANCELLED'}
        names, _ = anim_ops.action_bone_names(arm.animation_data.action, arm)
        for pb in arm.pose.bones:
            set_bone_selected(arm, pb, pb.name in names)
        return {'FINISHED'}


class GTATK_OT_apply_pose_rest(bpy.types.Operator):
    bl_idname = "gtatk.apply_pose_rest"
    bl_label = "Apply Pose as Rest (keep mesh)"
    bl_description = ("Bake the current pose into the meshes and make it the new rest pose "
                      "(like GTA Tools 'Apply Current Pose')")
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        msg = "Bakes the current pose into the meshes as the new rest pose; any current action is unlinked"
        try:
            return context.window_manager.invoke_confirm(self, event, title="Apply Pose as Rest?", message=msg,
                                                         confirm_text="Apply", icon='WARNING')
        except TypeError:
            return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        arm = get_armature(context)
        if not arm:
            return {'CANCELLED'}
        meshes = [o for o in bpy.data.objects if o.type == 'MESH' and any(
            m.type == 'ARMATURE' and m.object == arm for m in o.modifiers)]
        for mo in meshes:
            if mo.data.shape_keys:
                self.report({'ERROR'}, "%s has shape keys - cannot apply" % mo.name)
                return {'CANCELLED'}
        mode = arm.mode
        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for mo in meshes:
            if mo.data.users > 1:
                mo.data = mo.data.copy()
            for m in [m for m in mo.modifiers if m.type == 'ARMATURE' and m.object == arm]:
                name = m.name
                with context.temp_override(object=mo, active_object=mo, selected_objects=[mo]):
                    bpy.ops.object.modifier_apply(modifier=name)
                nm = mo.modifiers.new(name, 'ARMATURE')
                nm.object = arm
                nm.use_vertex_groups = True
                nm.use_bone_envelopes = False
        with context.temp_override(object=arm, active_object=arm, selected_objects=[arm]):
            bpy.ops.object.mode_set(mode='POSE')
            bpy.ops.pose.armature_apply(selected=False)
            bpy.ops.object.mode_set(mode=mode if mode in ('OBJECT', 'POSE') else 'OBJECT')
        # the current action was keyed against the old rest pose: keeping it would pose the
        # character twice, so unlink it (it stays in the file with a fake user)
        had = arm.animation_data.action.name if arm.animation_data and arm.animation_data.action else None
        if had:
            arm.animation_data.action = None
            for pb in arm.pose.bones:
                pb.location = (0, 0, 0)
                pb.rotation_quaternion = (1, 0, 0, 0)
                pb.scale = (1, 1, 1)
            self.report({'WARNING'}, "New rest pose applied. Action '%s' was unlinked - actions made "
                                     "before this no longer match; re-apply IFP animations." % had)
        return {'FINISHED'}


class GTATK_OT_limit_weights(bpy.types.Operator):
    bl_idname = "gtatk.limit_weights"
    bl_label = "Limit Weights to 4 + Normalize"
    bl_description = "GTA supports 4 bone weights per vertex: limit and normalize the selected meshes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        meshes = [o for o in context.selected_objects if o.type == 'MESH' and o.vertex_groups]
        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for mo in meshes:
            with context.temp_override(object=mo, active_object=mo, selected_objects=[mo]):
                bpy.ops.object.vertex_group_limit_total(group_select_mode='ALL', limit=4)
                bpy.ops.object.vertex_group_normalize_all(group_select_mode='ALL', lock_active=False)
        self.report({'INFO'}, "Processed %d meshes" % len(meshes))
        return {'FINISHED'}


# ============================================================================= UI
class GTATK_UL_sections(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        from .formats.mapdata import section_label
        row = layout.row(align=True)
        row.prop(item, "use", text="")
        label = section_label(item.name)
        row.label(text=label)
        sub = row.row(align=True)
        sub.alignment = 'RIGHT'
        if label != item.name:
            code = sub.row(align=True)
            code.active = False                 # dimmed: the game's own code
            code.label(text=item.name)
        sub.label(text="%d" % item.count)


class GTATK_UL_imports(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        sub = row.row()
        sub.ui_units_x = 1.3
        sub.label(text="#%d" % item.num)
        row.prop(item, "name", text="", emboss=False)       # double-click to rename
        sub = row.row()
        sub.ui_units_x = 2.8
        sub.alignment = 'RIGHT'
        sub.label(text="%d objs" % item.objects)


class GTATK_UL_anims(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        from . import library
        p = context.scene.gta_tk
        row = layout.row()
        row.label(text=item.name, icon='ACTION')
        sub = row.row()
        sub.ui_units_x = 2.2                # narrow duration column: the name gets the room
        sub.alignment = 'RIGHT'
        sub.label(text="%.2fs" % item.duration)
        src = current_source(p)
        library.draw_star(row, 'ANIM', src + "|" + item.name, item.name, library.source_label(src))



class GTATK_UL_anim_hits(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        from . import library
        row = layout.row()
        row.label(text=item.name, icon='ACTION')
        sub = row.row()
        sub.alignment = 'RIGHT'
        sub.label(text="%s · %.2fs" % (library.source_label(item.source), item.duration))
        library.draw_star(row, 'ANIM', item.source + "|" + item.name, item.name, library.source_label(item.source))


class _Panel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "GTA"


class GTATK_PT_game(_Panel, bpy.types.Panel):
    bl_label = "GTA SA Toolkit"

    def draw(self, context):
        p = context.scene.gta_tk
        col = self.layout.column()
        col.prop(p, "game_root", text="")
        col.operator("gtatk.scan_game", text="Scan Game Files", icon='VIEWZOOM')


class GTATK_PT_map(_Panel, bpy.types.Panel):
    bl_label = "Map Import"
    bl_parent_id = "GTATK_PT_game"

    def draw(self, context):
        p = context.scene.gta_tk
        l = self.layout
        l.row(align=True).prop(p, "map_source", expand=True)
        if p.map_source == 'SECTIONS':
            if not p.sections:
                l.label(text="Scan the game files to list sections", icon='INFO')
            l.template_list("GTATK_UL_sections", "", p, "sections", p, "sections_index", rows=6)
            r = l.row(align=True)
            r.operator("gtatk.sections_select", text="All").action = 'ALL'
            r.operator("gtatk.sections_select", text="None").action = 'NONE'
            r.operator("gtatk.sections_select", text="Invert").action = 'INVERT'
            r.operator("gtatk.cursor_to_section", text="", icon='PIVOT_CURSOR')
            l.prop(p, "limit_to_area")
        on = preview.shown()
        area = p.map_source == 'AREA' or p.limit_to_area
        if area:
            b = l.box()
            b.prop(p, "area_center")
            if p.area_center == 'COORDS':
                r = b.row(align=True)
                r.prop(p, "area_x")
                r.prop(p, "area_y")
            b.prop(p, "area_radius")
            b.prop(p, "area_margin")
        r = l.row(align=True)
        if area:
            r.operator("gtatk.pick_area", icon='EYEDROPPER')
        r.operator("gtatk.map_preview", text="Hide Preview" if on else "Preview",
                   icon='HIDE_OFF' if on else 'HIDE_ON', depress=on)
        if on:
            l.label(text=preview.short_text(preview.data()), icon='INFO')
        # options: the everyday ones always shown, one per row so no label is cut off
        b = l.box()
        c = b.column(align=True)
        c.prop(p, "load_textures")
        c.prop(p, "skip_placed")
        b.prop(p, "placement")
        b.prop(p, "max_instances")
        h, body = b.panel("GTATK_map_more", default_closed=True)
        h.label(text="More Options")
        if body:
            c = body.column(align=True)
            for k in ("include_stream", "skip_lod", "only_lod", "include_interiors", "pack_images",
                      "load_collisions", "use_mat_split", "share_materials", "hide_while_importing"):
                c.prop(p, k)
        r = l.row(align=True)
        r.scale_y = 1.6
        r.operator("gtatk.import_map", icon='WORLD')
        if p.progress:
            l.label(text=p.progress)
        if p.imports:
            b = l.box()
            b.label(text="Imported Maps", icon='OUTLINER_COLLECTION')
            b.template_list("GTATK_UL_imports", "", p, "imports", p, "imports_index", rows=3)
            b.operator("gtatk.remove_import", text="Remove", icon='TRASH')


class GTATK_PT_model(_Panel, bpy.types.Panel):
    bl_label = "Model from IMG"
    bl_parent_id = "GTATK_PT_game"

    def draw(self, context):
        p = context.scene.gta_tk
        from . import library
        r = self.layout.row(align=True)
        prop_ph(r, p, "model_name", "Name or ID, e.g. bmyst", text="")
        r.operator("gtatk.import_model", text="Import", icon='IMPORT')
        g = library.loaded_game()        # never loads the game here
        item = library.model_item(g, p.model_name) if g is not None else None
        on = item is not None and library.is_favorite(item[0], item[1])
        r.operator("gtatk.favorite_typed", text="", icon='SOLO_ON' if on else 'SOLO_OFF')


class GTATK_PT_ifp(_Panel, bpy.types.Panel):
    bl_label = "IFP Animation"

    def draw(self, context):
        p = context.scene.gta_tk
        l = self.layout
        arm = get_armature(context)
        l.label(text="Ped: %s" % (ped_label(arm) if arm else "- select a ped -"),
                icon='ARMATURE_DATA' if arm else 'ERROR')
        if arm is not None:
            from . import mods
            for w in mods.warnings_of(arm):         # stored at import (custom models)
                l.label(text=w, icon='ERROR')
        draw_ifp_browser(l, context)
        b = l.box()
        # label | dropdown split, so "Frame Rate" / "Root Motion" are never cut off
        for key, lab in (("ifp_fps_mode", "Frame Rate"), ("ifp_start", "Start"), ("ifp_root", "Root Motion")):
            s = b.split(factor=0.42, align=True)
            s.label(text=lab)
            s.prop(p, key, text="")
            if key == "ifp_fps_mode" and p.ifp_fps_mode == 'CUSTOM':
                b.prop(p, "ifp_custom_fps")
        c = b.column(align=True)
        for key in ("ifp_new_action", "ifp_clear_pose", "ifp_adjust_range", "ifp_set_scene_fps", "ifp_snap"):
            c.prop(p, key)
        r = l.row(align=True)
        r.scale_y = 1.4
        r.operator("gtatk.ifp_apply", icon='PLAY')
        l.operator("gtatk.ifp_import_all", icon='ACTION')


class GTATK_PT_ifp_export(_Panel, bpy.types.Panel):
    bl_label = "IFP Export"
    bl_parent_id = "GTATK_PT_ifp"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        p = context.scene.gta_tk
        l = self.layout
        arm = get_armature(context)
        act = arm.animation_data.action if arm and arm.animation_data else None
        l.label(text="Action: %s" % (act.name if act else "-"))
        l.prop(p, "exp_mode")
        if p.exp_mode != 'NEW':
            prop_ph(l, p, "exp_base", "The loaded IFP")
        else:
            l.prop(p, "exp_ifp_name")
            l.prop(p, "exp_format")
        prop_ph(l, p, "exp_anim_name", "The action's name")
        prop_ph(l, p, "exp_path", "Pick an .ifp file to write")
        if in_game_folder(p.exp_path, p.game_root):
            l.label(text="Writes into the game folder", icon='ERROR')
        r = l.row(align=True)
        r.prop(p, "exp_step")
        r.prop(p, "exp_use_scene_range", text="Scene Range")
        l.operator("gtatk.ifp_export", icon='EXPORT')


class GTATK_PT_char(_Panel, bpy.types.Panel):
    bl_label = "Ped Tools"
    bl_parent_id = "GTATK_PT_ifp"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        c = self.layout.column(align=True)
        c.operator("gtatk.reset_pose", icon='LOOP_BACK')
        c.operator("gtatk.reset_pose", text="Reset Selected Bones").selected_only = True
        c.operator("gtatk.clear_anim", icon='X')
        c.operator("gtatk.select_keyed", icon='RESTRICT_SELECT_OFF')
        c.operator("gtatk.limit_weights", icon='GROUP_VERTEX')
        # changes the meshes for good: kept apart from the everyday buttons, and it asks first
        c = self.layout.column()
        c.separator(factor=1.5)
        c.operator("gtatk.apply_pose_rest", icon='POSE_HLT')


def menu_import_ifp(self, context):
    self.layout.operator(GTATK_OT_ifp_load.bl_idname, text="GTA Animation (.ifp) [GTA SA Toolkit]")


classes = (
    GTATK_SectionItem, GTATK_ImportItem, GTATK_AnimItem, GTATK_AnimHit, GTATK_Props,
    GTATK_OT_scan_game, GTATK_OT_sections_select, GTATK_OT_cursor_to_section,
    GTATK_OT_import_map, GTATK_OT_remove_import, GTATK_OT_import_model,
    GTATK_OT_ifp_load, GTATK_OT_ifp_choose, GTATK_OT_ifp_apply, GTATK_OT_ifp_import_all, GTATK_OT_ifp_export,
    GTATK_OT_reset_pose, GTATK_OT_clear_anim, GTATK_OT_select_keyed,
    GTATK_OT_apply_pose_rest, GTATK_OT_limit_weights,
    GTATK_UL_sections, GTATK_UL_imports, GTATK_UL_anims, GTATK_UL_anim_hits, GTATK_OT_anim_search_clear,
)


@bpy.app.handlers.persistent
def _pack_on_save(*_):
    try:
        if any(getattr(s, "gta_tk", None) and s.gta_tk.pack_images for s in bpy.data.scenes):
            mapimport.pack_pending_images()
    except Exception as e:           # noqa
        print("[GTA SA Toolkit] pack on save failed:", e)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    if _pack_on_save not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_pack_on_save)
    bpy.types.Scene.gta_tk = PointerProperty(type=GTATK_Props)
    bpy.types.TOPBAR_MT_file_import.append(menu_import_ifp)


def unregister():
    if _pack_on_save in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_pack_on_save)
    bpy.types.TOPBAR_MT_file_import.remove(menu_import_ifp)
    del bpy.types.Scene.gta_tk
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
