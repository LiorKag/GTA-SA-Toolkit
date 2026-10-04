# GTA SA Toolkit - published interface for other add-ons (GTA SA Studio).
#
# Everything another add-on may use lives here; the rest of the package is private and can change
# in any release. API_LEVEL goes up only when something here changes in a way callers would notice
# (a function removed, arguments or return values changed). Adding functions does not bump it.
#
#   api = the "api" module of the installed gta_sa_toolkit package (GTA SA Studio, built in:
#         "from .. import api" in studio/corelink.py)
#   if api.API_LEVEL >= 2: ...
#
# API_LEVEL history
#   1  game data, import_model/vehicle, ifp_*, tabs, events
#   2  map lighting moved to Studio (imports no longer set light strength or vehicle-light
#      glow); light tags documented below; tag_scene_lights, set_vehicle_light, on_import_removed,
#      game_file, import_bounds.
#   3  vehicles for Studio's Vehicle tab: vehicle_data, paintjob_image, import_vehicle_part,
#      paint_vehicle_material, paint_vehicle_objects, colour_rgb, UPGRADE_KINDS, model flag constants.
#   4  SA-MP reverse maths for Studio's Edit tab: SAMP_BONES, samp_euler_to_quat,
#      quat_to_samp_euler, toy_root, toy_values, set_toy_values, attached_line, map_object_root,
#      map_object_values, object_line. Toys remember their slot (gta_samp_index).
#   5  set_toy_values(bone=...) moves a toy to another SA-MP bone.
#   6  the game's road and sidewalk graph for Studio's paths: path_graph, area_of, areas_in,
#      area_bounds, LANE_WIDTH.
#   7  one IFP browser shared with the Peds tab: IFP sources ("ped.ifp", "anim.img/<name>.ifp"
#      or a file path) with ifp_sources, ifp_file, current_ifp, set_current_ifp, selected_anim,
#      draw_ifp_browser.
#   8  tabs by subject: register_section / unregister_section add sections into any tab
#      (Studio puts Pawn Export + paths into World, the Toy Editor into Peds, colours/lights/doors into
#      the new core Vehicles tab 'VEHICLE'); Studio's own Vehicle and Edit tabs are gone.
#   9  mod_timecyc_files (timecyc files found in the Mod Folders, for Studio's timecyc list).
#
# An add-on that plugs into core can define, in its top-level module:
#   gtatk_core_ready(api)   called when core finishes registering (e.g. re-enabled after the add-on)
#   gtatk_core_gone()       called when core starts unregistering
import os
import sys

import bpy
from mathutils import Vector

from . import anim as _anim
from . import events as _events
from . import lighttags as _lighttags
from . import mapimport as _map
from . import paths as _paths
from . import samp as _samp
from . import tabs as _tabs
from .formats import nodes as _nodes

API_LEVEL = 9

_VERSION = {"v": None}


def core_version():
    """The core add-on's version as a tuple, e.g. (1, 5, 0)."""
    if _VERSION["v"] is None:
        path = os.path.join(os.path.dirname(__file__), "blender_manifest.toml")
        v = (0, 0, 0)
        try:
            for line in open(path, encoding="utf-8"):
                if line.strip().startswith("version"):
                    v = tuple(int(p) for p in line.split("=", 1)[1].strip().strip('"').split("."))
                    break
        except (OSError, ValueError):
            pass
        _VERSION["v"] = v
    return _VERSION["v"]


# ----------------------------------------------------------------------------- SA-MP
# Each reverse function is the exact inverse of core's import maths (same bone table, same YXZ
# euler order), so exporting what core imported gives back the same numbers.
SAMP_BONES = _samp.SAMP_BONES              # (samp bone id, label, GTA ped bone id)


def samp_euler_to_quat(rx, ry, rz):
    """SA-MP euler degrees -> Blender quaternion."""
    return _samp.samp_euler_to_quat(rx, ry, rz)


def quat_to_samp_euler(q):
    """Blender quaternion -> SA-MP euler degrees (rx in [-90, 90], ry/rz in (-180, 180])."""
    return _samp.quat_to_samp_euler(q)


def toy_root(ob):
    """The attached toy (object on a ped bone with gta_toy) ob belongs to, or None."""
    return _samp.toy_root(ob)


def toy_values(toy):
    """SetPlayerAttachedObject values of a toy root, read from where it is now:
    {"model", "index", "bone", "offset", "rot", "scale"}."""
    return _samp.toy_values(toy)


def set_toy_values(toy, offset=None, rot=None, scale=None, index=None, bone=None):
    """Move a toy root to these values (None = keep). bone: a SA-MP bone id (SAMP_BONES); the toy
    moves onto it keeping offset/rotation/scale. ValueError if the ped has no such bone."""
    _samp.set_toy_values(toy, offset, rot, scale, index, bone)


def attached_line(values, playerid="playerid"):
    """A finished SetPlayerAttachedObject(...); line for toy_values() output."""
    return _samp.attached_line(values, playerid)


def map_object_root(ob):
    """The placed map object (gta_id) ob belongs to, or None."""
    return _samp.map_object_root(ob)


def map_object_values(objects):
    """[(object, (model, (x, y, z), (rx, ry, rz), (sx, sy, sz)))] for placed map objects
    (map_object_root results), as SA-MP CreateObject values."""
    roots = _samp.library_roots()
    return [(ob, _samp.placed_values(ob, roots)) for ob in objects]


def object_line(model, pos, rot, dynamic=False):
    """CreateObject(...); or CreateDynamicObject(...); line."""
    return _samp.object_line(model, pos, rot, dynamic)


# ----------------------------------------------------------------------------- game data
def get_game(root=None, reload=False):
    """The scanned game (read it, don't change it). root defaults to the folder set in the panel.
    Stable fields: objects {id: obj with .id .model .txd .kind .ide .draw_dist},
    vehicles {id: v with .id .model .txd .type .game_name}, by_name {lower model name: obj},
    sections (map sections with .name .bounds), car_colours, car_colour_sets {model: [(c1, c2..)]},
    colour_rgb(index) -> (r, g, b) 0-255, samp_ids (set of SA-MP object ids)."""
    if root is None:
        root = bpy.context.scene.gta_tk.game_root
    return _map.get_game(root, reload=reload)


def game_file(relpath, root=None):
    """Absolute path of a file in the game folder, e.g. game_file("data/timecyc.dat"), or None when
    no folder is set or the file isn't there. root defaults to the folder set in the panel."""
    if root is None:
        root = bpy.context.scene.gta_tk.game_root
    if not root:
        return None
    path = os.path.join(bpy.path.abspath(root), *relpath.replace("\\", "/").split("/"))
    return path if os.path.isfile(path) else None


def import_bounds(num=None):
    """Where a map import sits: {"num", "name", "count", "min": (x, y, z), "max": (x, y, z)} from the
    positions of its placed objects, or None. num=None: the import selected in the World tab's list.
    Walks the scene's objects once: call it from a button, never from draw()."""
    sc = bpy.context.scene
    p = sc.gta_tk
    if not p.imports:
        return None
    if num is None:
        it = p.imports[min(max(p.imports_index, 0), len(p.imports) - 1)]
    else:
        it = next((i for i in p.imports if i.num == num), None)
        if it is None:
            return None
    mn, mx, n = [float("inf")] * 3, [float("-inf")] * 3, 0
    for o in sc.objects:
        if o.get("gta_import") == it.num:
            t = o.matrix_world.translation
            for k in range(3):
                mn[k] = min(mn[k], t[k])
                mx[k] = max(mx[k], t[k])
            n += 1
    if not n:
        return None
    return {"num": it.num, "name": it.name, "count": n, "min": tuple(mn), "max": tuple(mx)}


# ----------------------------------------------------------------------------- traffic and ped paths
# The game's path nodes (nodes0-63.dat in gta3.img). The map is 8 x 8 areas of 750 m, area 0 at the
# south-west corner (-3000, -3000), numbered west -> east then south -> north.
LANE_WIDTH = _nodes.LANE_WIDTH              # metres between lane centres


def path_graph(reload=False):
    """The whole map's road and sidewalk graph (read once, kept until an IMG changes; ~0.3 s).
    Stable fields: .nodes {key: node}, .navis {key: navi}, .lanes(a, b) -> (lanes going a->b, lanes
    coming b->a, median width m), .navi_between(a, b). key = (area, index).
    node: .key .pos (x, y, z) .ped (sidewalk) .width (m) .links [keys] .lengths [m] .traffic (0-3)
    .boats .emergency .highway .flags. navi: .pos (x, y) .dir (dx, dy) towards .target (a node key)
    .left .right (lane counts against / along dir) .width (median, m)."""
    return _paths.graph(get_game(), reload=reload)


def area_of(x, y):
    """Area number (0-63) of a map position."""
    return _nodes.area_of(x, y)


def areas_in(mn, mx):
    """Area numbers touching the rectangle mn..mx ((x, y) each)."""
    return _nodes.areas_in(mn, mx)


def area_bounds(area):
    """((min x, min y), (max x, max y)) of an area."""
    return _nodes.area_bounds(area)


# ----------------------------------------------------------------------------- importing
def _resolve_model(game, name_or_id):
    key = str(name_or_id).strip()
    if not key:
        raise ValueError("model name or ID is empty")
    if key.isdigit() and int(key) in game.objects:
        return game.objects[int(key)].model
    return key


def _is_vehicle(game, key):
    key = str(key).strip().lower()
    return any(v.model.lower() == key or str(v.id) == key for v in game.vehicles.values())


def import_model(name_or_id, location=None, textures=True):
    """Import any model (ped, object, weapon; vehicles get wheels and paint) at location
    (default: the 3D cursor). Returns its collection. Fires on_import_done once."""
    game = get_game()
    if _is_vehicle(game, name_or_id):
        return import_vehicle(name_or_id, location=location)[0]
    name = _resolve_model(game, name_or_id)
    ctx = bpy.context
    loc = ctx.scene.cursor.location.copy() if location is None else location
    pack = getattr(ctx.scene.gta_tk, "pack_images", True)
    with _events.import_batch():
        coll = _map.import_model_from_img(ctx, game, name, textures, pack)
        for ob in list(coll.objects):
            if ob.parent is None:
                ob.location = ob.location + Vector(loc)
    return coll


def import_vehicle(name_or_id, colours=None, colour_set=-1, location=None, rotation_z=0.0,
                   hide_damage=True, hide_lod=True):
    """Import a vehicle by model name or ID. colours: carcols indices [c1, c2, (c3, c4)];
    colour_set: index into the model's colour sets (-1 = random). location defaults to the 3D cursor;
    rotation_z in radians. Returns (collection, root_object). Fires on_import_done once."""
    from . import vehicles
    ctx = bpy.context
    game = get_game()
    loc = ctx.scene.cursor.location.copy() if location is None else location
    with _events.import_batch():
        coll, root, _vdef = vehicles.import_vehicle(
            ctx, game, str(name_or_id), colours=colours, colour_set=colour_set, hide_damage=hide_damage,
            hide_lod=hide_lod, location=loc, rotation_z=rotation_z)
    return coll, root


# ----------------------------------------------------------------------------- vehicles
# Imported vehicles: the root object has gta_vehicle (model name), gta_id and gta_colours (carcols
# indices). Paint materials carry gta_paint_slot (0-3), light materials gta_vehicle_light.
from .formats.carmods import (FLAG_HANGING_BOOT, FLAG_IS_BIKE, FLAG_IS_VAN, FLAG_NO_DOORS,  # noqa: E402,F401
                              FLAG_NOSWING_BOOT, FLAG_REVERSE_BONNET, FLAG_TAILGATE_BOOT, UPGRADE_KINDS)
# UPGRADE_KINDS: (kind, label, name prefix). FLAG_*: bits of vehicle_data()["model_flags"].


def vehicle_data(model):
    """What the game has for a vehicle model: {"model", "id", "type", "colour_sets" [(c1, c2..)],
    "paintjobs" (how many), "upgrades" {kind: [part names]} (carmods.dat + its wheel group; only
    parts whose model exists), "links" {part: part that goes on with it}, "model_flags" (handling.cfg)}."""
    from . import vehicles
    return vehicles.vehicle_data(get_game(), model)


def colour_rgb(index):
    """carcols.dat palette colour (r, g, b) 0-255."""
    return get_game().colour_rgb(int(index))


def paintjob_image(model, n):
    """Paintjob n (1-based) of a model as a bpy image, loaded once and reused; None if missing."""
    from . import vehicles
    pack = getattr(bpy.context.scene.gta_tk, "pack_images", True)
    return vehicles.paintjob_image(get_game(), model, int(n), pack)


def import_vehicle_part(name):
    """Build an upgrade part (e.g. "spl_b_mar_m", "wheel_sr6") at the origin in a new collection.
    Returns (collection, objects). Fires no event: the caller puts it on a vehicle."""
    from . import vehicles
    pack = getattr(bpy.context.scene.gta_tk, "pack_images", True)
    return vehicles.import_part(get_game(), name, pack)


def paint_vehicle_material(material, rgb):
    """Tint one paint material like the game does (texture x colour); rgb 0-1."""
    from . import vehicles
    vehicles.tint_material(material, tuple(rgb))


def paint_vehicle_objects(objects, colours):
    """Paint every paint-slot material on these objects; colours: up to 4 (r, g, b) 0-1. Also tags
    new paint / light materials (gta_paint_slot, gta_vehicle_light)."""
    from . import vehicles
    vehicles.paint_vehicle(objects, [tuple(c) for c in colours])


# ----------------------------------------------------------------------------- IFP animation
def ifp_load(path, reload=False):
    """Read an IFP (cached until it changes on disk through this add-on). Returns an IfpFile:
    .version ('ANP3'/'ANPK'), .name, .animations (each .name, .bones, .duration), .find(name)."""
    from . import ui
    return ui.load_ifp(path, reload=reload)


def ifp_sources(scene=None):
    """The game's IFP files as [(source, label)]: ("ped.ifp", "ped.ifp"), ("anim.img/gangs.ifp",
    "gangs.ifp")... Only the anim.img listing is read (cached)."""
    from . import library
    sc = scene or bpy.context.scene
    return library.ifp_sources(sc.gta_tk.game_root) if sc.gta_tk.game_root else []


def ifp_file(source):
    """The IfpFile of a source: "ped.ifp", "anim.img/<name>.ifp" or a file path (// relative too)."""
    from . import library
    return library.load_anim_file(bpy.context, source)


def current_ifp(scene=None):
    """The source the shared IFP browser (Peds tab, draw_ifp_browser) lists, "" when none yet."""
    from . import ui
    return ui.current_source((scene or bpy.context.scene).gta_tk)


def set_current_ifp(source):
    """Make the shared browser list this source (in the current scene). Returns its IfpFile."""
    from . import ui
    return ui.choose_source(bpy.context, source)


def selected_anim(scene=None):
    """(source, animation name) highlighted in the shared browser, or None."""
    from . import ui
    p = (scene or bpy.context.scene).gta_tk
    src = ui.current_source(p)
    if not src or not p.anims:
        return None
    return src, p.anims[min(p.anims_index, len(p.anims) - 1)].name


def draw_ifp_browser(layout, context, rows=5):
    """Draw the shared IFP dropdown ("Other file…" opens a file browser), search and animation list
    (durations, stars). Picking a file or an animation here shows in the Peds tab too."""
    from . import ui
    ui.draw_ifp_browser(layout, context, rows=rows)


def ifp_apply(armature, anim, start_frame=1.0, fps=30.0, root_mode='BONE', new_action=True,
              action_name=None, clear_pose=True):
    """Put an IFP animation (from ifp_load(...).find(name)) onto a ped armature as an action.
    root_mode: 'BONE' (travel on the root bone), 'OBJECT' (travel on the armature object),
    'INPLACE' or 'NONE'. Returns (action, [names of bones not found])."""
    o = _anim.ApplyOptions()
    o.fps, o.start_frame, o.root_mode = float(fps), float(start_frame), root_mode
    o.new_action, o.clear_pose = new_action, clear_pose
    return _anim.apply_animation(armature, anim, o, action_name=action_name)


def ifp_bake(armature, action=None, name=None, fps=30.0, frame_start=None, frame_end=None, step=1):
    """Sample an action on the armature into an IFP animation (root motion on the object is folded
    back into the root bone). Defaults: the armature's current action and its frame range."""
    act = action or (armature.animation_data.action if armature.animation_data else None)
    if act is None:
        raise ValueError("the armature has no action to bake")
    f0, f1 = act.frame_range
    name = name or act.get("ifp_anim_name") or act.name.split(":")[-1]
    return _anim.bake_action_to_anim(bpy.context, armature, act, name, float(fps),
                                     f0 if frame_start is None else frame_start,
                                     f1 if frame_end is None else frame_end, step)


def ifp_export(anims, path, format='AUTO', mode='NEW', base=None, ifp_name="custom"):
    """Write one animation or a list. mode 'NEW', 'REPLACE' or 'APPEND' (into base, which is
    backed up to base + '.bak' when overwritten). format 'AUTO' | 'ANP3' | 'ANPK'; ANP3 turns into
    ANPK automatically when a move exceeds +/-32 m. Returns (path, format_used)."""
    from . import ui
    if not isinstance(anims, (list, tuple)):
        anims = [anims]
    path = bpy.path.abspath(path)
    res = _anim.export_ifp(list(anims), path, fmt=format, mode=mode,
                           base=bpy.path.abspath(base) if base else None, ifp_name=ifp_name)
    for n in res["notes"]:
        print("[GTA SA Toolkit]", n)
    ui._IFP_CACHE.pop(os.path.normcase(os.path.abspath(path)), None)
    return res["path"], res["format"]


# ----------------------------------------------------------------------------- map lights
# Every importer tags the GTA lights it creates. Tags are custom properties on the light *data*:
#   gta_cat          one of LIGHT_CATEGORIES' keys ('VEHICLE' is used for materials only)
#   gta_base_color   [r, g, b] 0-1, the game's colour
#   gta_base_energy  watts that look right at full strength
#   gta_day, gta_night   whether the game shows the light by day / at night
#   gta_model        the model it came from
# Vehicle head/tail light materials carry gta_vehicle_light = "head" or "tail" (glow off at import).
LIGHT_CATEGORIES = _lighttags.CATEGORIES      # (key, label, description)


def tag_scene_lights():
    """Tag GTA lights already in the file that have no tags yet (imported by an old version).
    Walks every object: call it from a button, never from draw(). Returns how many were tagged."""
    return _lighttags.scan_scene_lights()


def set_vehicle_light(material, rgb, strength):
    """Make a vehicle-light material glow in rgb (0-1) at strength (0 = off)."""
    from . import vehicles
    vehicles.set_emission(material, rgb, strength)


# ----------------------------------------------------------------------------- tabs
def register_tab(id, label, short_label, icon, draw_fn, order=100, icon_active=None, description=""):
    """Add a tab to the GTA SA Toolkit panel. icon / icon_active: PNG paths (big icon, and its
    highlighted copy for the selected tab). draw_fn(layout, context) must only draw stored values.
    Core tabs use order 10-40 and 90 (Library, last); Studio uses 50 (Animate) and 60 (Scene).
    Call unregister_tab(id) from your add-on's unregister()."""
    _tabs.register_tab(id, label, short_label, icon, draw_fn, order, icon_active, description)


def unregister_tab(id):
    _tabs.unregister_tab(id)


def register_section(tab, id, title, draw_fn, order=100, icon='NONE', closed=False, poll=None,
                     draw_header=None, panel_id=None):
    """Add a section (a collapsible part) to a tab: core's 'WORLD', 'CHARS' (Peds), 'VEHICLE',
    'ITEMS' (Objects), 'LIBRARY', or your own tab. draw_fn(layout, context) draws its body (stored
    values only); poll(context) -> False hides it; title may be a function(context) -> text.
    Core's orders: World map 10, SA-MP Map Code 30; Peds import 10, IFP 20, IFP Export 30, Toys 40,
    Ped Tools 60; Vehicles import 10; Objects weapons 10, objects 20. Unregister it when your add-on
    goes (core forgets every section when it is turned off itself).
    Two ids take over core controls while registered: 'VEHICLE' / "st_paint" hides core's Repaint
    buttons, 'CHARS' / "st_toy" hides the Toys section's offset/rotation/scale rows (GTA SA Studio's
    Colours and Toy Editor sections)."""
    _tabs.register_section(tab, id, title, draw_fn, order=order, icon=icon, closed=closed, poll=poll,
                           draw_header=draw_header, panel_id=panel_id)


def unregister_section(tab, id):
    _tabs.unregister_section(tab, id)


def has_section(tab, id):
    return _tabs.has_section(tab, id)


def mod_timecyc_files():
    """timecyc*.dat / *.cfg files in the user's Mod Folders (found by the last game / mods scan; cheap)."""
    from . import mods
    return mods.timecyc_files()


# ----------------------------------------------------------------------------- events
def on_import_done(fn):
    """fn(objects) is called once after each finished import with every object it created
    (including hidden library models). Returns fn, so it works as a decorator."""
    return _events.add("import_done", fn)


def on_import_removed(fn):
    """fn() is called after Remove Imported Map deleted an import. Returns fn."""
    return _events.add("import_removed", fn)


def on_game_scanned(fn):
    """fn() is called after the game files are (re)scanned. Returns fn."""
    return _events.add("game_scanned", fn)


def remove_listener(fn):
    """Stop calling fn (for any event). Call it from your add-on's unregister()."""
    _events.remove(fn)


# ----------------------------------------------------------------------------- add-ons plugged into core
def _plugged_modules():
    for key in bpy.context.preferences.addons.keys():
        mod = sys.modules.get(key)
        if mod is not None and mod.__name__ != __package__:
            yield mod


def _notify(hook, *args):
    """Call hook(*args) on every enabled add-on that defines it (see the top of this file)."""
    try:
        mods = list(_plugged_modules())
    except Exception:                  # noqa - restricted context
        return
    for mod in mods:
        fn = getattr(mod, hook, None)
        if callable(fn):
            try:
                fn(*args)
            except Exception:          # noqa - another add-on must never break core
                import traceback
                print("[GTA SA Toolkit] %s.%s failed:" % (mod.__name__, hook))
                traceback.print_exc()
