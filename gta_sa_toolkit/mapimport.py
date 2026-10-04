# GTA SA Toolkit - map import (whole sections / areas) straight from the game's IMG archives.
import math
import os
import time

import bpy
from mathutils import Matrix, Quaternion, Vector

from . import cache
from . import events
from .formats import mapdata

_GAME = {"root": None, "data": None}
LIB_NAME = "GTA Model Library"
COL_LIB_NAME = "GTA Collision Library"


# ----------------------------------------------------------------------------- game data cache
def get_game(root, reload=False):
    root = bpy.path.abspath(root or "").rstrip("\\/")
    if not root or not os.path.isdir(root):
        raise RuntimeError("Set the GTA San Andreas folder first.")
    if reload or _GAME["data"] is None or _GAME["root"] != root:
        if _GAME["data"]:
            _GAME["data"].close()
        gd = mapdata.GameData(root)
        gd.load_sections()
        _GAME.update(root=root, data=gd)
    return _GAME["data"]


def exterior(inst):
    return (inst.interior & 0xFF) in (0, 13)


# ----------------------------------------------------------------------------- textures
class CIDict(dict):
    """Case-insensitive dict for texture-name lookups."""

    def __setitem__(self, k, v):
        super().__setitem__(k.lower(), v)

    def __getitem__(self, k):
        return super().__getitem__(k.lower())

    def __contains__(self, k):
        return super().__contains__(k.lower())

    def get(self, k, d=None):
        return super().get(k.lower(), d)


def _make_image(name, rgba, w, h, pack):
    """rgba: bytes (top row first) or a (h, w, 4) uint8 numpy array."""
    img = bpy.data.images.get(name)
    if img and img.size[0] == w and img.size[1] == h:
        return img
    img = bpy.data.images.new(name, w, h, alpha=True)
    try:
        import numpy as np
        if isinstance(rgba, np.ndarray):
            arr = rgba[::-1]
        else:
            arr = np.frombuffer(bytes(rgba), dtype=np.uint8)[: w * h * 4].reshape(h, w, 4)[::-1]
        img.pixels.foreach_set(arr.ravel().astype(np.float32) * (1.0 / 255.0))
    except Exception:                       # noqa - no numpy: slow path
        px = [0.0] * (w * h * 4)
        rb = bytes(rgba)
        for y in range(h):
            src = (h - 1 - y) * w * 4
            dst = y * w * 4
            for i in range(w * 4):
                px[dst + i] = rb[src + i] / 255.0
        img.pixels.foreach_set(px)
    img["gta_tk_tex"] = True
    if pack == 'NOW':
        img.pack()
    return img


def pack_pending_images():
    """Pack GTA textures that were created in memory (called right before the .blend is saved)."""
    n = 0
    for img in bpy.data.images:
        # has_data: made in memory. FILE: loaded from the asset cache (pixels maybe not read yet);
        # packing it keeps the .blend working even after the cache is cleared
        if img.get("gta_tk_tex") and img.packed_file is None and (img.has_data or img.source == 'FILE'):
            try:
                img.pack()
                n += 1
            except RuntimeError:
                pass
    return n


def _as_array(rgba, w, h):
    import numpy as np
    if isinstance(rgba, np.ndarray):
        return rgba
    return np.frombuffer(bytes(rgba), dtype=np.uint8)[: w * h * 4].reshape(h, w, 4)


class TxdCache:
    def __init__(self, game, pack=True):
        self.game = game
        self.pack = pack
        self.single = {}

    def _load_one(self, txd_name):
        key = txd_name.lower()
        if key in self.single:
            return self.single[key]
        out = cache.load_txd(self.game, key, self.pack)        # None when the cache is off / has no copy
        if out is None:
            out = self._decode(key)
        self.single[key] = out
        return out

    def _decode(self, key):
        out = {}
        keep = [] if cache.enabled() else None                   # (name, rgba array) for the cache
        data = self.game.read_txd_bytes(key)
        if data:
            from .formats import txd
            try:
                t = txd.load(data)
            except txd.RWError as e:
                keep = None
                print("[GTA SA Toolkit] TXD %s: %s" % (key, e))
                t = None
            for tex in (t.textures if t is not None else []):
                try:
                    w, h = tex.size(0)
                    name = "%s.txd/%s" % (key, tex.name)
                    img = bpy.data.images.get(name)          # already loaded earlier: reuse
                    fresh = not (img and img.size[0] == w and img.size[1] == h)
                    if fresh or keep is not None:
                        rgba = tex.decode(0)
                        if keep is not None:
                            keep.append((tex.name, rgba))
                    if fresh:
                        img = _make_image(name, rgba, w, h, self.pack)
                    out[tex.name.lower()] = [img]
                except txd.RWError as e:
                    keep = None
                    print("[GTA SA Toolkit] texture %s/%s: %s" % (key, tex.name, e))
        if keep:
            cache.save_txd(self.game, key, keep)
        return out

    def get(self, txd_name, extra_parents=()):
        result = CIDict()
        chain = list(extra_parents)[::-1] + list(reversed(self.game.txd_chain(txd_name)))
        for t in chain:   # parents first, child overrides
            for k, v in self._load_one(t).items():
                result[k] = v
        return result


# ----------------------------------------------------------------------------- DFF models
def _ensure_child_collection(parent, name, hidden=False, exclude=False):
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    if coll.name not in parent.children:
        try:
            parent.children.link(coll)
        except RuntimeError:
            pass
    if hidden or exclude:
        lc = _find_layer_collection(bpy.context.view_layer.layer_collection, coll.name)
        if lc:
            if exclude:
                lc.exclude = True
            if hidden:
                lc.hide_viewport = True
    return coll


def _find_layer_collection(lc, name):
    if lc.collection.name == name:
        return lc
    for c in lc.children:
        r = _find_layer_collection(c, name)
        if r:
            return r
    return None


def cache_key(game, model, txd, opts, textures, extra_parents=()):
    chain = list(game.txd_chain(txd)) + [p.lower() for p in extra_parents] if textures else []
    return cache.model_key(model, chain, dict(opts, textures=textures)), chain


def build_model(game, model, txd, opts, txd_cache=None, extra_parents=(), loaded=None, share=None):
    """Import of <model>.dff (our reader + builder: formats/dff.py, rwbuild.py) into a new collection
    linked to the scene collection. opts: connect_bones / use_mat_split / remove_doubles. txd_cache None
    = no textures. share: {material key: material} shared across a map import.
    Returns (collection or None when the DFF is missing, True when it came from the asset cache).
    Raises RuntimeError with the reader's message for a file that can't be read.
    With the cache on, a cached copy is used (loaded: already fetched by cache.fetch) and a fresh
    build is remembered for the cache."""
    use_cache = cache.enabled()
    if use_cache and not cache.in_session():
        cache.validate()                    # map imports validate once, in cache.begin()
    tex = txd_cache.get(txd, extra_parents) if txd_cache is not None else {}
    if use_cache:
        key, chain = cache_key(game, model, txd, opts, txd_cache is not None, extra_parents)
        ld = loaded if loaded is not None else cache.fetch([key]).get(key)
        if ld is not None:
            cache.STATS["hits"] += 1
            return cache.finish_model(ld, tex, bpy.context.scene.collection, share), True
    data = game.imgs.read(model + ".dff")
    if not data:
        return None, False
    coll, rec = build_own(model, data, opts, tex, share)
    if use_cache:
        cache.STATS["builds"] += 1
        cache.remember(game, key, model, coll, rec, chain)
    return coll, False


def build_own(model, data, opts, tex, share=None):
    """Build a model's DFF bytes as collection "<model>.dff": (collection, cache.OwnRecipes). Raises
    RuntimeError with the reader's plain message when the file can't be read."""
    from . import rwbuild
    from .formats import dff as rwdff
    try:
        d = rwdff.load(data)
    except rwdff.RWError as e:
        raise RuntimeError("%s.dff: %s" % (model, e))
    coll, recipes = rwbuild.build(d, model, tex, opts, share)
    for w in d.warnings:
        print("[GTA SA Toolkit] %s.dff: %s" % (model, w))
    uv = any(desc["uv_anim"] for descs in recipes.values() for desc in descs)
    return coll, cache.OwnRecipes({p: ("rw", descs) for p, descs in recipes.items()}, uv)


# ----------------------------------------------------------------------------- planning
class Plan:
    def __init__(self):
        self.insts = []           # list of mapdata.Inst
        self.models = []          # unique model ids, import order
        self.skipped_lod = 0
        self.skipped_interior = 0
        self.skipped_area = 0
        self.truncated = 0
        self.center = None        # area centre (x, y) when an area was used
        self.skipped_placed = 0   # already in the scene (skip_already_placed)


def build_plan(game, props, center=None):
    plan = Plan()
    if props.map_source == 'SECTIONS':
        chosen = {s.name for s in props.sections if s.use}
        sections = [s for s in game.sections if s.name in chosen]
    else:
        sections = list(game.sections)

    use_area = props.map_source == 'AREA' or props.limit_to_area
    cx = cy = r2 = r2big = None
    if use_area:
        cx, cy = center
        plan.center = (cx, cy)
        r2 = props.area_radius ** 2
        r2big = (props.area_radius + props.area_margin) ** 2

    for sec in sections:
        for inst in sec.all_insts(props.include_stream):
            if props.skip_lod and inst.is_lod and not props.only_lod:
                plan.skipped_lod += 1
                continue
            if props.only_lod and not inst.is_lod:
                continue
            if not props.include_interiors and not exterior(inst):
                plan.skipped_interior += 1
                continue
            if use_area:
                dx, dy = inst.pos[0] - cx, inst.pos[1] - cy
                d2 = dx * dx + dy * dy
                od = game.objects.get(inst.id)
                big = od is not None and od.draw_dist >= 150.0
                if d2 > (r2big if big else r2):
                    plan.skipped_area += 1
                    continue
            if inst.id not in game.objects:
                continue
            plan.insts.append(inst)

    if props.max_instances and len(plan.insts) > props.max_instances:
        if use_area:
            plan.insts.sort(key=lambda i: (i.pos[0] - cx) ** 2 + (i.pos[1] - cy) ** 2)
        plan.truncated = len(plan.insts) - props.max_instances
        plan.insts = plan.insts[:props.max_instances]

    seen = set()
    for i in plan.insts:
        if i.id not in seen:
            seen.add(i.id)
            plan.models.append(i.id)
    return plan


def import_name(props, center=None, game=None):
    """Default list name for an import: the place at its area's centre ("Ganton r250"), else its
    sections ("Los Santos – East" for one, codes for several)."""
    from .formats.mapdata import section_label
    area = ""
    place = ""
    if props.map_source == 'AREA' or props.limit_to_area:
        area = "r%d" % round(props.area_radius)
        if center and game is not None:
            place = game.place_at(center[0], center[1])
    if props.map_source == 'AREA':
        cx, cy = center if center else (0.0, 0.0)
        return "%s %s" % (place or "Area %d, %d" % (round(cx), round(cy)), area)
    names = [s.name for s in props.sections if s.use]
    text = section_label(names[0]) if len(names) == 1 else \
        ", ".join(names[:3]) + (" + %d more" % (len(names) - 3) if len(names) > 3 else "")
    if place:
        return "%s (%s, %s)" % (place, text, area)
    return "%s (area %s)" % (text, area) if area else text


# ----------------------------------------------------------------------------- import tracking
# Every map import gets a number (props.import_counter) and an entry in props.imports. Its placed
# objects carry ob["gta_import"] = number and live under their own collection "GTA Map / #N name".
def import_label(num, name):
    return "#%d %s" % (num, name)


def imported_areas(scene, props):
    """(x, y, radius) per map import in the list: its stored area, else (sections, map code, files from
    older imports) a circle around its placed objects, worked out here in one pass. Not for draw()."""
    out, need = [], {}
    for it in props.imports:
        if it.radius > 0:
            out.append((it.cx, it.cy, it.radius))
        else:
            need[it.num] = []
    if need:
        for ob in scene.objects:
            n = ob.get("gta_import")
            if n in need:
                p = ob.get("gta_pos")
                need[n].append((p[0], p[1]) if p is not None and len(p) >= 2 else ob.matrix_world.translation.xy[:])
        for pts in need.values():
            if pts:
                cx = sum(p[0] for p in pts) / len(pts)
                cy = sum(p[1] for p in pts) / len(pts)
                r = max(math.hypot(p[0] - cx, p[1] - cy) for p in pts)
                out.append((cx, cy, max(r, 10.0)))
    return out


def find_import(props, num):
    for i, it in enumerate(props.imports):
        if it.num == num:
            return i, it
    return -1, None


SAME_SPOT = 0.01          # metres: an instance within 1 cm of a placed one with the same ID is a duplicate


def _placed_index(scene):
    """(id, x cm, y cm, z cm) -> list of positions of map objects already in the scene."""
    index = {}
    for ob in scene.objects:
        gid = ob.get("gta_id")
        if gid is None or "gta_lod" not in ob:          # only placed map objects (not vehicles etc.)
            continue
        pos = ob.get("gta_pos")
        if pos is None:                                 # placed by 1.5 or older
            t = ob.matrix_world.translation
            pos = (t.x, t.y, t.z)
        else:
            pos = tuple(pos)
        key = (int(gid), round(pos[0] * 100), round(pos[1] * 100), round(pos[2] * 100))
        index.setdefault(key, []).append(pos)
    return index


def _take_placed(index, oid, pos):
    """Find (and use up) a placed object with this ID within SAME_SPOT of pos."""
    kx, ky, kz = round(pos[0] * 100), round(pos[1] * 100), round(pos[2] * 100)
    lim = SAME_SPOT * SAME_SPOT + 1e-9
    for dx in (0, -1, 1):
        for dy in (0, -1, 1):
            for dz in (0, -1, 1):
                lst = index.get((oid, kx + dx, ky + dy, kz + dz))
                if not lst:
                    continue
                for j, q in enumerate(lst):
                    if (q[0] - pos[0]) ** 2 + (q[1] - pos[1]) ** 2 + (q[2] - pos[2]) ** 2 <= lim:
                        lst.pop(j)
                        return True
    return False


def skip_already_placed(scene, plan):
    """Drop planned instances that are already in the scene (same model ID, within 1 cm) and the
    models only they needed. Each placed object stands for one planned instance. Returns the count."""
    index = _placed_index(scene)
    if not index:
        return 0
    keep = [i for i in plan.insts if not _take_placed(index, i.id, i.pos)]
    skipped = len(plan.insts) - len(keep)
    if skipped:
        plan.insts = keep
        needed = {i.id for i in keep}
        plan.models = [m for m in plan.models if m in needed]
        plan.skipped_placed += skipped
    return skipped


def remove_import(scene, props, num):
    """Delete one import: its objects, its collections, and the library models (with their meshes,
    materials and textures) that it used and nothing else in the file still uses.
    Returns (objects removed, library models removed)."""
    i, it = find_import(props, num)
    objs = [o for o in bpy.data.objects if o.get("gta_import") == num]
    colls = []
    if it is not None and it.coll is not None:
        colls = [it.coll] + list(it.coll.children_recursive)
        seen = set(objs)
        objs += [o for c in colls for o in c.objects if o not in seen]

    # library model collections this import used (instances, or editable copies sharing mesh data)
    lib_colls = []
    for name in (LIB_NAME, COL_LIB_NAME):
        root = bpy.data.collections.get(name)
        if root is not None:
            lib_colls += [c for c in root.children if c.get("gta_model") or name == COL_LIB_NAME]
    by_data = {}
    for c in lib_colls:
        for o in c.all_objects:
            if o.data is not None:
                by_data.setdefault(o.data, c)
    candidates = set()
    for o in objs:
        if o.instance_collection is not None:
            candidates.add(o.instance_collection)
        elif o.data is not None and o.data in by_data:
            candidates.add(by_data[o.data])
    candidates &= set(lib_colls)

    n_objs = len(objs)
    if objs or colls:
        bpy.data.batch_remove(objs + colls)
    if i >= 0:
        props.imports.remove(i)
        props.imports_index = max(0, min(props.imports_index, len(props.imports) - 1))
    if not candidates:
        return n_objs, 0

    # which candidates does something outside the library still use?
    lib_objs = set()
    for c in lib_colls:
        lib_objs.update(c.all_objects)
    used_colls, used_data = set(), set()
    for o in bpy.data.objects:
        if o.instance_collection is not None:
            used_colls.add(o.instance_collection)
        if o.data is not None and o not in lib_objs:
            used_data.add(o.data)
    dead = [c for c in candidates
            if c not in used_colls and not any(o.data in used_data for o in c.all_objects)]
    if not dead:
        return n_objs, 0

    dead_objs = {o for c in dead for o in c.all_objects}
    datas = {o.data for o in dead_objs if o.data is not None}
    mats = {m for d in datas for m in getattr(d, "materials", ()) if m is not None}
    imgs = set()
    for m in mats:
        if m.node_tree:
            imgs.update(n.image for n in m.node_tree.nodes if getattr(n, "image", None) is not None)
    bpy.data.batch_remove(list(dead_objs) + dead)
    # then whatever those models alone used (users == 0 now); lower levels only after upper ones
    for group in (datas, mats, imgs):
        orphans = [d for d in group if d.users == 0]
        if orphans:
            bpy.data.batch_remove(orphans)
    # TXDs hold more textures than their models use: those were never used by anything
    spare = [im for im in bpy.data.images if im.get("gta_tk_tex") and im.users == 0]
    if spare:
        bpy.data.batch_remove(spare)
    return n_objs, len(dead)


# ----------------------------------------------------------------------------- time estimate
# Seconds per model built and per placed object, measured on the developer's PC (test timings:
# Grove Street r120 = 116 models + 296 objects in 1.22 s; LAe+LAe2 re-import = 3206 objects, no new
# models, in 0.71 s). A scene already full of objects slows everything (~4x at 14,000 objects).
SEC_PER_MODEL = 0.0099
SEC_PER_OBJECT = 0.00022
SEC_PER_CACHED_MODEL = 0.002    # appended from the asset cache (Grove Street ~0.0016, LAe+LAe2 ~0.0037)
_CAL = {"factor": 1.0}          # learnt from finished imports in this session (actual / estimate)


def library_models(scene=None):
    """Lower-case names of the models already in the model library (nothing to build)."""
    lib = bpy.data.collections.get(LIB_NAME)
    if lib is None:
        return set()
    return {c.get("gta_model", "").lower() for c in lib.children if len(c.all_objects)} - {""}


def new_models(game, plan, lib_names):
    return [m for m in plan.models if game.objects[m].model.lower() not in lib_names]


def cached_count(game, models, opts, textures):
    """How many of these model ids the asset cache holds (0 when it's off)."""
    if not cache.enabled():
        return 0
    idx = cache.index()
    return sum(1 for m in models
               if cache_key(game, game.objects[m].model, game.objects[m].txd, opts, textures)[0] in idx["models"])


def map_opts(props):
    return {'use_mat_split': props.use_mat_split, 'remove_doubles': True, 'connect_bones': False}


def estimate_seconds(n_new_models, n_objects, scene_objects, calibrate=True, n_cached=0):
    """n_cached: how many of the new models come from the asset cache (about 4x cheaper to load)."""
    avg_scene = scene_objects + n_objects / 2.0          # the scene grows while importing
    base = ((n_new_models - n_cached) * SEC_PER_MODEL + n_cached * SEC_PER_CACHED_MODEL
            + n_objects * SEC_PER_OBJECT)
    return base * (1.0 + 3.0 * avg_scene / 14000.0) * (_CAL["factor"] if calibrate else 1.0)


def learn(actual, estimate):
    """Nudge later estimates towards this machine's real speed (big enough imports only)."""
    if estimate > 0.5 and actual > 0.5:
        ratio = min(max(actual / (estimate / _CAL["factor"]), 0.25), 4.0)
        _CAL["factor"] = 0.5 * _CAL["factor"] + 0.5 * ratio


def inst_matrix(inst):
    x, y, z, w = inst.rot
    q = Quaternion((-w, x, y, z))
    return Matrix.Translation(Vector(inst.pos)) @ q.to_matrix().to_4x4()


# ----------------------------------------------------------------------------- importer state machine
class MapImportJob:
    def __init__(self, context, game, props, plan, name=None):
        self.game = game
        if getattr(props, "skip_placed", False):
            skip_already_placed(context.scene, plan)
        self.props = props
        self.plan = plan
        self.settings = map_opts(props)
        self.cache_on = cache.enabled()
        self.cache_open = False
        self.loaded = None             # id -> cache.Loaded, fetched at the first step
        self.from_cache = 0
        self.txd = TxdCache(game, False) if props.load_textures else None
        self.share = {} if getattr(props, "share_materials", True) else None   # materials shared by the import
        scene_root = context.scene.collection
        self.lib = _ensure_child_collection(scene_root, LIB_NAME, exclude=True)
        self.col_lib = None
        self.col_parent = None
        if props.load_collisions:
            self.col_lib = _ensure_child_collection(scene_root, COL_LIB_NAME, exclude=True)
        self.map_root = _ensure_child_collection(scene_root, "GTA Map")
        if props.load_collisions:
            self.col_parent = _ensure_child_collection(scene_root, "GTA Map Collisions", hidden=True)
        self.num = props.import_counter + 1
        props.import_counter = self.num
        self.name = name or import_name(props, plan.center, game)
        self.import_coll = bpy.data.collections.new(import_label(self.num, self.name))
        self.map_root.children.link(self.import_coll)
        self.hidden = bool(getattr(props, "hide_while_importing", False))
        if self.hidden:                # shown again in finish(), which runs on done, Esc and errors
            self.import_coll.hide_viewport = True
        it = props.imports.add()
        it.num, it.name, it.coll = self.num, self.name, self.import_coll
        it.when = time.strftime("%Y-%m-%d %H:%M")
        if plan.center and (props.map_source == 'AREA' or props.limit_to_area):
            it.cx, it.cy = plan.center[0], plan.center[1]
            it.radius = props.area_radius
        props.imports_index = len(props.imports) - 1
        self.model_colls = {}          # id -> collection or None
        self.col_colls = {}            # lower model name -> collection
        self.col_loaded_ides = set()
        self.section_colls = {}
        self.stage = 'MODELS'
        self.mi = 0
        self.ii = 0
        self.errors = []
        self.created = 0
        self.new_objects = []          # everything this import created (for events.import_done)
        new = new_models(game, plan, library_models())
        self.n_new_models = len(new)
        n_cached = cached_count(game, new, self.settings, props.load_textures)
        self.estimate = estimate_seconds(self.n_new_models, len(plan.insts), len(context.scene.objects),
                                         n_cached=n_cached)
        self.t_start = time.perf_counter()
        self._eta = None               # (seconds left, when it was estimated) for status_text()
        cache.begin()                  # ended in finish(): models built here go into one cache pack
        self.cache_open = True

    # ---------------------------------------------------------------- models
    def _existing_model(self, name):
        low = name.lower()
        for coll in self.lib.children:
            if coll.get("gta_model", "").lower() == low and len(coll.all_objects):
                return coll
        coll = bpy.data.collections.get("%s.dff" % name)          # libraries made by older versions
        if coll and coll.name in self.lib.children and len(coll.all_objects):
            return coll
        return None

    def import_model(self, oid):
        od = self.game.objects[oid]
        coll = self._existing_model(od.model)
        if coll is None:
            ld = self.loaded.pop(oid, None) if self.loaded else None
            coll, hit = build_model(self.game, od.model, od.txd, self.settings, self.txd, loaded=ld, share=self.share)
            if coll is None:
                self.errors.append("DFF not found: %s" % od.model)
                return None
            self.from_cache += hit
            self.new_objects.extend(coll.all_objects)
            try:
                from . import lighttags
                lighttags.tag_collection_lights(coll, od.model)
            except Exception:              # noqa
                pass
            try:
                bpy.context.scene.collection.children.unlink(coll)
            except RuntimeError:
                pass
            self.lib.children.link(coll)
            coll["gta_model"] = od.model
            # embedded collisions are child collections: keep them out of the instance
            for child in list(coll.children):
                coll.children.unlink(child)
                if self.col_lib is not None and child.name not in self.col_lib.children:
                    self.col_lib.children.link(child)
                    self.col_colls.setdefault(od.model.lower(), child)
        if self.props.load_collisions:
            self._load_collision(od)
        return coll

    def _load_collision(self, od):
        name = od.model.lower()
        if name in self.col_colls or od.ide in self.col_loaded_ides:
            return
        self.col_loaded_ides.add(od.ide)
        from . import rwbuild
        from .formats import col as rwcol
        needed = {self.game.objects[i].model.lower() for i in self.plan.models
                  if self.game.objects[i].ide == od.ide}
        prefix = od.ide + "_"
        for fname in self.game.imgs.names_with_ext(".col"):
            if not fname.lower().startswith(prefix) and fname.lower() != od.ide + ".col":
                continue
            data = self.game.imgs.read(fname)
            try:
                models = [m for m in rwcol.load(data) if m.name.lower() in needed]
            except rwcol.RWError as e:
                self.errors.append("COL %s: %s" % (fname, e))
                continue
            if not models:
                continue
            for c in rwbuild.build_collision(models, "COL", link=False):
                self.col_lib.children.link(c)
                mname = c.name.split(".", 1)[1].lower() if "." in c.name else c.name.lower()
                self.col_colls[mname] = c

    # ---------------------------------------------------------------- instances
    def _section_coll(self, sec):
        c = self.section_colls.get(sec.name)
        if c is None:
            c = bpy.data.collections.new("%s (#%d)" % (sec.name, self.num))
            self.import_coll.children.link(c)
            self.section_colls[sec.name] = c
        return c

    def place(self, inst):
        coll = self.model_colls.get(inst.id)
        if coll is None:
            return
        od = self.game.objects[inst.id]
        target = self._section_coll(inst.section)
        m = inst_matrix(inst)
        if self.props.placement == 'INSTANCE':
            ob = bpy.data.objects.new(od.model, None)
            ob.instance_type = 'COLLECTION'
            ob.instance_collection = coll
            ob.empty_display_size = 0.5
            ob.matrix_world = m
            ob["gta_import"] = self.num
            target.objects.link(ob)
            self.new_objects.append(ob)
        else:
            ob = None
            mapping = {}
            for src in list(coll.all_objects):
                dup = src.copy()          # shares mesh data with the library model
                dup["gta_import"] = self.num
                mapping[src] = dup
                target.objects.link(dup)
                self.new_objects.append(dup)
            for src, dup in mapping.items():
                if src.parent in mapping:
                    dup.parent = mapping[src.parent]
                    dup.matrix_parent_inverse = src.matrix_parent_inverse.copy()
                else:
                    dup.parent = None
                    dup.matrix_world = m @ src.matrix_world
                    ob = dup
        if ob is not None:
            ob["gta_id"] = inst.id
            ob["gta_interior"] = inst.interior
            ob["gta_lod"] = inst.lod
            ob["gta_pos"] = inst.pos
        if self.col_parent is not None:
            cc = self.col_colls.get(od.model.lower())
            if cc is not None:
                co = bpy.data.objects.new(od.model + ".col", None)
                co.instance_type = 'COLLECTION'
                co.instance_collection = cc
                co.matrix_world = m
                co["gta_import"] = self.num
                self.col_parent.objects.link(co)
                self.new_objects.append(co)
        self.created += 1

    def _fetch_cached(self):
        """Append every cached model this import needs up front: one libraries.load per pack."""
        if not self.cache_on:
            return {}
        lib = library_models()
        keys = {}
        for oid in self.plan.models:
            od = self.game.objects[oid]
            if od.model.lower() not in lib:
                keys[cache_key(self.game, od.model, od.txd, self.settings, self.txd is not None)[0]] = oid
        got = cache.fetch(list(keys))
        return {keys[k]: v for k, v in got.items()}

    # ---------------------------------------------------------------- stepping
    def step(self, budget=0.25):
        t0 = time.perf_counter()
        if self.stage == 'MODELS':
            if self.loaded is None:
                self.loaded = self._fetch_cached()
            while self.mi < len(self.plan.models):
                oid = self.plan.models[self.mi]
                self.mi += 1
                try:
                    self.model_colls[oid] = self.import_model(oid)
                except Exception as e:           # noqa
                    self.model_colls[oid] = None
                    self.errors.append("%s: %s" % (self.game.objects[oid].model, e))
                if time.perf_counter() - t0 > budget:
                    return False
            self.stage = 'PLACE'
        if self.stage == 'PLACE':
            while self.ii < len(self.plan.insts):
                inst = self.plan.insts[self.ii]
                self.ii += 1
                try:
                    self.place(inst)
                except Exception as e:           # noqa
                    self.errors.append("place %s: %s" % (inst.model, e))
                if self.ii % 200 == 0 and time.perf_counter() - t0 > budget:
                    return False
            self.stage = 'DONE'
        return True

    def progress_text(self):
        if self.stage == 'MODELS':
            return "Models %d / %d" % (self.mi, len(self.plan.models))
        if self.stage == 'PLACE':
            return "Placing %d / %d" % (self.ii, len(self.plan.insts))
        return "Done"

    def progress(self):
        total = len(self.plan.models) + len(self.plan.insts) * 0.05 + 1e-9
        return (self.mi + self.ii * 0.05) / total

    def time_left(self, now=None):
        """Seconds left, estimated from the speed so far and smoothed so it doesn't jump around.
        None for the first 2 seconds (too early to tell)."""
        now = time.perf_counter() if now is None else now
        elapsed = now - self.t_start
        frac = self.progress()
        if elapsed < 2.0 or frac < 0.01:
            return None
        raw = elapsed * (1.0 - frac) / frac
        if self._eta is not None:
            prev, at = self._eta
            raw = 0.7 * max(prev - (now - at), 0.0) + 0.3 * raw
        self._eta = (raw, now)
        return raw

    def status_text(self, now=None):
        """One line for Blender's status bar: percentage, current step, time left."""
        parts = ["GTA map import %d%%" % int(self.progress() * 100), self.progress_text()]
        left = self.time_left(now)
        if left is not None:
            parts.append("about %s left" % format_duration(left))
        parts.append("Esc to stop")
        return "  ·  ".join(parts)

    def _close_entry(self):
        """Store the final object count in the import list; an import that placed nothing
        (everything already there, or stopped at once) leaves no entry and no empty collections."""
        i, it = find_import(self.props, self.num)
        if self.created:
            if it is not None:
                it.objects = self.created
            return
        if it is not None:
            self.props.imports.remove(i)
            self.props.imports_index = min(self.props.imports_index, len(self.props.imports) - 1)
        colls = [self.import_coll] + list(self.import_coll.children_recursive)
        if not any(len(c.objects) for c in colls):
            bpy.data.batch_remove(colls)

    def show(self):
        if self.hidden:
            self.hidden = False
            try:
                self.import_coll.hide_viewport = False
            except ReferenceError:
                pass

    def finish(self):
        self.show()
        if self.stage == 'DONE':
            learn(time.perf_counter() - self.t_start, self.estimate)
        try:
            self._close_entry()
        except Exception as e:             # noqa
            self.errors.append("import list: %s" % e)
        if self.loaded:
            cache.discard(self.loaded.values())       # fetched but not used (stopped early)
            self.loaded = {}
        if self.cache_open:
            self.cache_open = False
            try:
                cache.end()            # writes this import's new models as one pack
            except Exception as e:     # noqa
                self.errors.append("asset cache: %s" % e)
        self.game.imgs.close()        # release gta3.img etc. so IMG tools can save them
        if self.new_objects:
            objs, self.new_objects = self.new_objects, []
            events.import_done(objs)


def format_duration(secs):
    secs = int(round(secs))
    if secs < 60:
        return "%d s" % secs
    if secs < 3600:
        return "%d m %02d s" % divmod(secs, 60)
    return "%d h %02d m" % (secs // 3600, secs % 3600 // 60)


# ----------------------------------------------------------------------------- single model from IMG
def import_model_from_img(context, game, name, load_textures=True, pack=True, connect_bones=False):
    name = name.strip()
    if name.lower().endswith(".dff"):
        name = name[:-4]
    if game.imgs.find(name + ".dff") is None:
        raise RuntimeError("'%s.dff' not found in the game's IMG archives" % name)
    od = game.by_name.get(name.lower())
    txd_name = od.txd if od else name
    opts = {'connect_bones': connect_bones, 'use_mat_split': False, 'remove_doubles': False}
    coll, _hit = build_model(game, name, txd_name, opts, TxdCache(game, False) if load_textures else None)
    game.imgs.close()
    try:
        from . import lighttags
        lighttags.tag_collection_lights(coll, name)
    except Exception:                      # noqa
        pass
    events.import_done(list(coll.all_objects))
    return coll
