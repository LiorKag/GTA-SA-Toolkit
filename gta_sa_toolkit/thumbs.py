# GTA SA Toolkit - thumbnails of cached models (only with the asset cache on).
#
# A thumbnail is rendered once per cached model, the first time the model shows up in a list
# (Library, search results, item lists), and stored as thumbs/<model>.png in the cache folder.
# draw() only looks things up: a model without a thumbnail is queued, and a short one-shot timer
# renders the queue (one model per tick, so Blender stays responsive) and stops when it's empty.
#
# Rendering: the cached model is appended into the file, drawn by an instance empty in a temporary
# scene with a camera, rendered with Workbench (studio light, texture colours, transparent film,
# 128 px), and everything the render created is removed again.
import math
import os
import re
import time

import bpy
from mathutils import Vector

from . import cache

SIZE = 128
_P = {"coll": None, "root": None, "files": set(), "icons": {}, "queue": [], "queued": set(),
      "failed": set(), "gen": 0}


def _dir(r):
    return os.path.join(r, "thumbs")


def path_of(r, model):
    return os.path.join(_dir(r), model.lower() + ".png")


def _sync(r):
    """Remember which thumbnails exist (one folder listing per cache folder)."""
    if _P["root"] != r:
        _P["root"] = r
        _P["icons"].clear()
        _P["failed"].clear()
        try:
            _P["files"] = {f[:-4].lower() for f in os.listdir(_dir(r)) if f.lower().endswith(".png")}
        except OSError:
            _P["files"] = set()


# ============================================================================= lookup (draw-safe)
def icon(model):
    """icon_value of the model's thumbnail, or 0. Queues a render when a cached model has none."""
    if not model or _P["coll"] is None:
        return 0
    r = cache.root()
    if r is None:
        return 0
    _sync(r)
    low = model.lower()
    iid = _P["icons"].get(low)
    if iid is not None:
        return iid
    if low in _P["files"]:
        key = "%s#%d" % (low, _P["gen"])
        try:
            pv = _P["coll"].get(key) or _P["coll"].load(key, path_of(r, low), 'IMAGE')
            iid = pv.icon_id
        except (KeyError, RuntimeError):
            iid = 0
        _P["icons"][low] = iid
        return iid
    if low not in _P["queued"] and low not in _P["failed"] and cache.has_model(low):
        _P["queue"].append(low)
        _P["queued"].add(low)
        if not bpy.app.timers.is_registered(_work):
            bpy.app.timers.register(_work, first_interval=0.2)
    return 0


def item_model(kind, key):
    """Model name of a Library item (kind, key), or None (animations, unknown ids)."""
    if kind in ('ANIM', 'IFP'):
        return None
    if str(key).startswith("mod|"):         # custom model: its cache id (mods.cache_id)
        from . import mods
        m = mods.get(key)
        return mods.cache_id(m) if m is not None else None
    from . import mapimport
    game = mapimport._GAME["data"]
    if game is None:
        return None
    k = str(key).strip()
    if not k.isdigit():
        return k or None
    v = game.vehicles.get(int(k)) if kind == 'VEHICLE' else None
    if v is not None:
        return v.model
    od = game.objects.get(int(k))
    return od.model if od else None


def item_icon(kind, key):
    return icon(item_model(kind, key))


# ============================================================================= rendering
def _work():
    """Timer: render one queued thumbnail per tick; stops (returns None) when the queue is empty."""
    t0 = time.perf_counter()
    while _P["queue"] and time.perf_counter() - t0 < 0.1:
        low = _P["queue"].pop(0)
        try:
            ok = render(low) is not None
        except Exception as e:           # noqa
            print("[GTA SA Toolkit] thumbnail %s failed: %s" % (low, e))
            ok = False
        if not ok:
            _P["failed"].add(low)
        _P["queued"].discard(low)
    _redraw()
    return 0.05 if _P["queue"] else None


def _redraw():
    wm = bpy.context.window_manager
    for win in getattr(wm, "windows", ()):
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


_KINDS = ("objects", "meshes", "materials", "images", "collections", "armatures", "cameras", "lights",
          "node_groups", "actions", "curves")


def _snapshot():
    return {k: set(getattr(bpy.data, k)) for k in _KINDS}


def _hidden(ob):
    n = re.sub(r"\.\d+$", "", ob.name).lower()
    if n.endswith(("_dam", "_vlo", "_lod")) or n.startswith("2dfx") or ob.hide_render:
        return True
    return ob.type == 'MESH' and getattr(getattr(ob, "dff", None), "type", "OBJ") == "COL"


def render(model):
    """Render the thumbnail of a cached model into the cache folder. Returns its path, or None when
    the model isn't cached (or the game isn't loaded, for its textures)."""
    from . import mapimport, vehicles
    r = cache.root()
    game = mapimport._GAME["data"]
    if r is None or game is None:
        return None
    key, entry = cache.model_entry(model)
    if key is None:
        return None
    before = _snapshot()
    scene = cam = None
    try:
        ld = cache.fetch([key]).get(key)
        if ld is None:
            return None
        tex = mapimport.CIDict()
        mod = None
        if model.startswith("mod-"):                    # custom model: textures found like at import
            from . import mods
            mod = mods.by_cache_id(model)
            if mod is None:
                return None
            tex = mods.textures_for(mod, game, False)[0]
        txds = key.split("|")[1].split(">") if key.split("|")[1] else []
        for t in reversed(txds):                        # parents first, the model's own TXD wins
            imgs = cache.load_txd(game, t, False) or {}
            for k, v in imgs.items():
                tex[k] = v
        coll = cache.finish_model(ld, tex)
        for k in ld.kids:                               # embedded collisions: not in the picture
            if k.name in coll.children:
                coll.children.unlink(k)
        objs = list(coll.all_objects)
        low = model.lower()
        if mod is not None:                             # custom vehicle: its base car's wheels / colours
            low = (mods.base_of(mod, game)[0] or "") if mod.kind == 'VEHICLE' else ""
        vdef = next((v for v in game.vehicles.values() if v.model.lower() == low), None)
        if vdef is not None:                            # wheels on every corner, first default colour
            vehicles._duplicate_wheels(objs, vdef)
            objs = list(coll.all_objects)
            sets = game.car_colour_sets.get(vdef.model.lower()) or [(1, 1)]
            vehicles.paint_vehicle(objs, [tuple(c / 255.0 for c in game.colour_rgb(int(i))) for i in sets[0]])
        for ob in objs:
            if _hidden(ob):
                ob.hide_render = True
        arm = next((o for o in objs if o.type == 'ARMATURE'), None)
        if arm is not None:                             # peds: the game's idle pose, not lying down
            from . import ui
            ui.stand_up(bpy.context, arm, game)

        scene = bpy.data.scenes.new("gtatk_thumb")
        inst = bpy.data.objects.new("gtatk_thumb_model", None)
        inst.instance_type = 'COLLECTION'
        inst.instance_collection = coll
        scene.collection.objects.link(inst)
        vl = scene.view_layers[0]
        vl.update()                                     # makes the scene's depsgraph
        pts = []                                        # posed, deformed bounds of what's rendered
        if vl.depsgraph is not None:
            for di in vl.depsgraph.object_instances:
                ob = di.object
                if ob.type == 'MESH' and not ob.original.hide_render:
                    mw = di.matrix_world.copy()
                    pts += [mw @ Vector(c) for c in ob.bound_box]
        else:
            pts = [ob.matrix_world @ Vector(c) for ob in objs
                   if ob.type == 'MESH' and not ob.hide_render for c in ob.bound_box]
        if not pts:
            return None
        lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
        hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
        centre, radius = (lo + hi) / 2, max((hi - lo).length / 2, 0.05)

        cam = bpy.data.cameras.new("gtatk_thumb_cam")
        cam.lens = 50.0
        cam.clip_start, cam.clip_end = radius * 0.01, radius * 10.0
        cob = bpy.data.objects.new("gtatk_thumb_cam", cam)
        scene.collection.objects.link(cob)
        scene.camera = cob
        view = Vector((0.9, 1.4, 0.6)).normalized()     # GTA models face +Y: front-right, a bit from above
        fov = 2 * math.atan(18.0 / cam.lens)
        cob.location = centre + view * (radius / math.sin(fov / 2) * 0.92)
        cob.rotation_euler = (-view).to_track_quat('-Z', 'Y').to_euler()

        rd = scene.render
        rd.engine = 'BLENDER_WORKBENCH'
        rd.resolution_x = rd.resolution_y = SIZE
        rd.resolution_percentage = 100
        rd.film_transparent = True
        rd.image_settings.file_format = 'PNG'
        rd.image_settings.color_mode = 'RGBA'
        scene.view_settings.view_transform = 'Standard'
        sh = scene.display.shading
        sh.light = 'STUDIO'
        sh.color_type = 'TEXTURE'
        sh.show_shadows = False
        sh.show_cavity = False
        os.makedirs(_dir(r), exist_ok=True)
        out = path_of(r, model)
        rd.filepath = out
        bpy.ops.render.render(write_still=True, scene=scene.name)
        if not os.path.isfile(out):
            return None
        low = model.lower()
        _P["files"].add(low)
        _P["icons"].pop(low, None)
        return out
    finally:
        if scene is not None:
            bpy.data.scenes.remove(scene)
        after = _snapshot()                   # everything the render made (incl. "Render Result")
        new = [i for k in _KINDS for i in after[k] - before[k]]
        if new:
            bpy.data.batch_remove(new)


# ============================================================================= invalidation
def forget(r, models):
    """Delete the thumbnails of these (lower-case) models (their cache entries were dropped)."""
    for m in models:
        try:
            os.remove(path_of(r, m))
        except OSError:
            pass
        _P["files"].discard(m)
        _P["icons"].pop(m, None)
    if models:
        _P["gen"] += 1                       # re-rendered files load under a new preview name


def reset():
    """After Clear Cache (or a folder change): forget everything."""
    _P.update(root=None, files=set(), queue=[], queued=set(), failed=set())
    _P["icons"].clear()
    _P["gen"] += 1
    if _P["coll"] is not None:
        _P["coll"].clear()


def register():
    import bpy.utils.previews
    _P["coll"] = bpy.utils.previews.new()


def unregister():
    if bpy.app.timers.is_registered(_work):
        bpy.app.timers.unregister(_work)
    if _P["coll"] is not None:
        bpy.utils.previews.remove(_P["coll"])
        _P["coll"] = None
    _P.update(root=None, files=set(), queue=[], queued=set(), failed=set())
    _P["icons"].clear()
