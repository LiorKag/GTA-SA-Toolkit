# GTA SA Toolkit - custom / modded models (finding and listing them).
# Part B: importing peds and objects (import_mod), warnings. Sources = the folders in the preferences' Mod Folders: loose .dff files (with their .txd) and .img
# archives inside them. Each DFF is looked into (formats.dffprobe) to tell ped / vehicle / object;
# a per-model override in the preferences wins. Models inside a .img are listed only when they are new
# or differ in size from the game's (name + size from the IMG index; "Show all" lists every one).
# Results live in memory (_MODS) and the probes in a small JSON cache keyed by file size + date, so a
# rescan only reads what changed. Keys: "mod|<dff path>" (loose) or "mod|<img path>|<entry>" (.img).
import hashlib
import json
import os
import time

import bpy

from .formats import dffprobe
from .formats import img as img_fmt

KINDS = ('PED', 'VEHICLE', 'OBJECT')
PROBE_VERSION = 3       # part of every cache signature: bump when dffprobe's rules change (2: peds by bone ids,
                        # 3: vehicle kind)
# a custom vehicle with no game namesake is "based on" a typical game model of its kind (colour sets,
# wheel size, handling flags, upgrades); the user can pick another
DEFAULT_VEHICLE_BASE = {'CAR': "admiral", 'BIKE': "pcj600", 'BOAT': "speeder", 'HELI': "maverick", 'PLANE': "dodo"}
KIND_LABELS = {'PED': "Ped", 'VEHICLE': "Vehicle", 'OBJECT': "Object"}


class ModModel:
    __slots__ = ("key", "name", "kind", "detected", "path", "entry", "txd", "folder", "textures",
                 "frames", "new", "error", "vkind")

    def __init__(self, key, name, path, entry, folder):
        self.key, self.name, self.path, self.entry, self.folder = key, name, path, entry, folder
        self.kind = self.detected = 'OBJECT'
        self.txd = ""                   # loose .txd path, "img:<entry>" in the same .img, or ""
        self.textures, self.frames, self.new, self.error = [], 0, True, ""
        self.vkind = 'CAR'              # for vehicles: CAR / BIKE / BOAT / HELI / PLANE

    @property
    def source_label(self):
        """Where it came from, short: "mymod.dff" or "gta3.img"."""
        return os.path.basename(self.path)


_MODS = {"models": [], "by_key": {}, "secs": 0.0, "folders": 0, "notes": [], "timecycs": []}


def timecyc_files():
    """timecyc*.dat / *.cfg files found in the Mod Folders by the last scan (for GTA SA Studio's timecyc
    list; .aaa backups left out). Stored values: safe in draw()."""
    return list(_MODS["timecycs"])


def models(kind=None):
    return [m for m in _MODS["models"] if kind is None or m.kind == kind]


def get(key):
    return _MODS["by_key"].get(key)


def is_mod_key(key):
    return str(key).startswith("mod|")


def summary():
    """(folders, {kind: count}) of the last scan - stored values, safe in draw()."""
    c = {k: 0 for k in KINDS}
    for m in _MODS["models"]:
        c[m.kind] += 1
    return _MODS["folders"], c


# ----------------------------------------------------------------------------- preferences
def _prefs():
    from . import library
    return library.prefs()


def folders():
    """[(path, include subfolders)] of the enabled Mod Folders that exist."""
    pr = _prefs()
    if pr is None:
        return []
    out = []
    for f in pr.mod_folders:
        p = bpy.path.abspath(f.path).rstrip("\\/")
        if f.enabled and p and os.path.isdir(p):
            out.append((p, f.subfolders))
    return out


def overrides():
    pr = _prefs()
    try:
        return json.loads(pr.mod_overrides) if pr is not None and pr.mod_overrides else {}
    except ValueError:
        return {}


def set_override(key, kind):
    """kind = 'PED' / 'VEHICLE' / 'OBJECT', or None to go back to the detected type."""
    pr = _prefs()
    ov = overrides()
    if kind:
        ov[key] = kind
    else:
        ov.pop(key, None)
    pr.mod_overrides = json.dumps(ov, sort_keys=True)
    m = get(key)
    if m is not None:
        m.kind = kind or m.detected


# ----------------------------------------------------------------------------- based on
def _bases():
    pr = _prefs()
    try:
        return json.loads(pr.mod_bases) if pr is not None and pr.mod_bases else {}
    except ValueError:
        return {}


def guess_base(m, game):
    """The game model a mod borrows from when none was chosen: the game model with the same name, else
    (vehicles) a typical one of its kind (DEFAULT_VEHICLE_BASE), (peds) the game ped one of its textures
    is named after, else None."""
    if game is None:
        return None
    low = m.name.lower()
    if m.kind == 'VEHICLE':
        if any(v.model.lower() == low for v in game.vehicles.values()):
            return low
        return DEFAULT_VEHICLE_BASE.get(m.vkind, "admiral")
    od = game.by_name.get(low)
    if od is not None and (m.kind != 'PED' or od.kind == 'peds'):
        return low
    if m.kind == 'PED' and game.imgs.find(low + ".dff") is not None:
        return low                                  # a story character (loose DFF in the game's IMG)
    if m.kind == 'PED':                             # made from a game ped: one of its textures is named
        return _ped_from_textures(m, game)          # after it (e.g. a ped mod using "swmori")
    return None


_GUESS = {}                                         # key + probe signature -> guessed ped (read once)


def _ped_from_textures(m, game):
    """The game ped one of the mod's textures is named after, preferring a texture its own .txd lacks
    (e.g. a mod with hmori in its .txd that needs swmori from the game's swmori.txd)."""
    cands = [t.lower() for t in m.textures
             if game.by_name.get(t.lower()) is not None and game.by_name[t.lower()].kind == 'peds']
    if len(cands) < 2:
        return cands[0] if cands else None
    k = m.key + "|" + "|".join(cands)
    if k not in _GUESS:
        own = set()
        try:
            own = set(dffprobe.txd_names(read_txd(m) or b""))
        except OSError:
            pass
        _GUESS[k] = next((c for c in cands if c not in own), cands[0])
    return _GUESS[k]


def base_of(m, game):
    """(game model name or None, chosen by hand)."""
    b = _bases().get(m.key)
    if b:
        return b, True
    return guess_base(m, game), False


def set_base(key, model):
    """Choose the game model a mod is based on (None = back to the guess)."""
    pr = _prefs()
    b = _bases()
    if model:
        b[key] = str(model).lower()
    else:
        b.pop(key, None)
    pr.mod_bases = json.dumps(b, sort_keys=True)


# ----------------------------------------------------------------------------- probe cache
def _cache_path():
    d = os.environ.get("GTATK_LIBRARY_DIR")          # tests: next to their own library.json
    if not d:
        d = bpy.utils.extension_path_user(__package__, path="mods", create=True)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "mod_probes.json")


def _load_cache():
    try:
        with open(_cache_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_cache(cache):
    try:
        with open(_cache_path(), "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except OSError as e:
        print("[GTA SA Toolkit] mod cache:", e)


def _probe(cache, sig, read):
    """Probe result for a signature (cached), read() gives the DFF bytes when needed."""
    hit = cache.get(sig)
    if hit is None:
        info = dffprobe.probe(read())
        hit = {"kind": info.kind(), "tex": info.textures[:64], "frames": len(info.frames), "err": info.error,
               "vk": info.vehicle_kind()}
        cache[sig] = hit
    return hit


# ----------------------------------------------------------------------------- scanning
def scan(game=None, show_all=None):
    """Find every mod model in the Mod Folders. game: the scanned game (its IMG index tells which models
    in a .img are new / changed). Returns the models; also stored for the lists (models())."""
    t = time.perf_counter()
    pr = _prefs()
    if show_all is None:
        show_all = bool(pr and pr.mods_show_all)
    # name -> every size it has in the game's archives (gta3.img, gta_int.img, SAMP.img...): a model is
    # unchanged when any of them has the same size (the combined index keeps only the last archive's copy)
    game_index = {}
    for arc in (game.imgs.archives if game is not None else ()):
        for e in arc.order:
            game_index.setdefault(e.name.lower(), set()).add(e.size)
    ov = overrides()
    cache, used = _load_cache(), {}
    out, notes, tcs = [], [], []
    roots = folders()
    for root, sub in roots:
        for dirpath, dirs, files in os.walk(root):
            if not sub:
                dirs[:] = []
            dirs.sort()
            low = {f.lower(): f for f in files}
            txds = [f for f in files if f.lower().endswith(".txd")]
            for f in sorted(files):
                path = os.path.join(dirpath, f)
                lf = f.lower()
                if lf.startswith("timecyc") and (lf.endswith(".dat") or lf.endswith(".cfg")):
                    tcs.append(path)
                if lf.endswith(".dff"):
                    out.append(_loose(path, low, txds, dirpath, root, cache, used))
                elif lf.endswith(".img"):
                    try:
                        out.extend(_img_models(path, root, game_index, show_all, cache, used))
                    except Exception as e:           # noqa - one broken archive must not stop the scan
                        notes.append("%s: %s" % (f, e))
    for m in out:
        m.kind = ov.get(m.key, m.detected)
        if m.kind not in KINDS:
            m.kind = m.detected
    _save_cache(used)                        # only what still exists: the cache doesn't grow forever
    _MODS.update(models=out, by_key={m.key: m for m in out}, secs=time.perf_counter() - t,
                 folders=len(roots), notes=notes, timecycs=tcs)
    return out


def _sig(path, entry=None, size=None):
    st = os.stat(path)
    return "v%d|%s|%s|%d|%d" % (PROBE_VERSION, os.path.normcase(path), entry or "",
                                size if size is not None else st.st_size, st.st_mtime_ns)


def _fill(m, hit):
    m.detected = hit["kind"]
    m.textures, m.frames, m.error = hit.get("tex", []), hit.get("frames", 0), hit.get("err", "")
    m.vkind = hit.get("vk", 'CAR')


def _loose(path, low, txds, dirpath, root, cache, used):
    name = os.path.splitext(os.path.basename(path))[0]
    m = ModModel("mod|" + os.path.normcase(os.path.abspath(path)), name, path, None, root)
    sig = _sig(path)

    def read():
        with open(path, "rb") as fh:
            return fh.read()
    hit = _probe(cache, sig, read)
    used[sig] = hit
    _fill(m, hit)
    same = low.get(name.lower() + ".txd")
    if same:
        m.txd = os.path.join(dirpath, same)
    elif len(txds) == 1:                     # e.g. kb_chair02.dff + kbcouch1.txd: the folder's only .txd
        m.txd = os.path.join(dirpath, txds[0])
    return m


def _img_models(path, root, game_index, show_all, cache, used):
    arc = img_fmt.ImgArchive(path)
    try:
        names = {e.name.lower(): e for e in arc.order}
        out = []
        for e in arc.order:
            if not e.name.lower().endswith(".dff"):
                continue
            new = e.size not in game_index.get(e.name.lower(), ())
            if not new and not show_all:
                continue
            name = os.path.splitext(e.name)[0]
            m = ModModel("mod|%s|%s" % (os.path.normcase(os.path.abspath(path)), e.name.lower()), name, path,
                         e.name, root)
            m.new = new
            sig = _sig(path, e.name.lower(), e.size)
            hit = _probe(cache, sig, lambda e=e: arc.read(e))
            used[sig] = hit
            _fill(m, hit)
            txd = names.get(name.lower() + ".txd")
            m.txd = "img:" + txd.name if txd is not None else ""
            out.append(m)
        return out
    finally:
        arc.close()


# ----------------------------------------------------------------------------- importing (part B)
# A mod model is built like a game model (our reader + builder, at the 3D cursor, peds stood up,
# light tags, the import event, Recent) and gets the same tags plus gta_mod = its key. Its DFF is read
# straight from its bytes (non-English folder names are fine). Textures: the
# mod's own .txd first, then the game's .txd of the same name for what it lacks; image names carry a
# short hash of the mod so a modded "sweet" never reuses (or overwrites) the game Sweet's images.
# Warnings are worked out here, reported, and stored on the model (gta_mod_warnings) for the panels.


def read_dff(m):
    if m.entry is None:
        with open(m.path, "rb") as f:
            return f.read()
    arc = img_fmt.ImgArchive(m.path)
    try:
        return arc.read(m.entry)
    finally:
        arc.close()


def read_txd(m):
    """The mod's own .txd bytes, or None."""
    if not m.txd:
        return None
    if m.txd.startswith("img:"):
        arc = img_fmt.ImgArchive(m.path)
        try:
            return arc.read(m.txd[4:])
        finally:
            arc.close()
    with open(m.txd, "rb") as f:
        return f.read()


def cache_id(m):
    """The mod's name in the asset cache and for its thumbnail: unique (a game model with the same name
    never clashes) and plain ASCII (non-English folder names never reach a file name)."""
    import re
    return "mod-%s-%s" % (hashlib.md5(m.key.encode("utf-8")).hexdigest()[:6],
                          re.sub(r"[^a-z0-9_.-]", "_", m.name.lower())[:40])


def by_cache_id(cid):
    return next((m for m in _MODS["models"] if cache_id(m) == cid), None)


def _sources(m):
    """The files a mod is built from (the cache rebuilds it when one changes)."""
    out = [m.path]
    if m.txd and not m.txd.startswith("img:"):
        out.append(m.txd)
    return out


def image_prefix(m):
    return "mod-%s/" % hashlib.md5(m.key.encode("utf-8")).hexdigest()[:6]


def _decode_txd(data, prefix, pack):
    """{lower texture name: [image]} of TXD bytes; images named prefix + texture name."""
    from . import mapimport
    from .formats import txd
    out = {}
    try:
        t = txd.load(data)
    except txd.RWError as e:
        print("[GTA SA Toolkit] mod textures: %s" % e)
        return out
    for tex in t.textures:
        try:
            w, h = tex.size(0)
            out[tex.name.lower()] = [mapimport._make_image(prefix + tex.name, tex.decode(0), w, h, pack)]
        except txd.RWError as e:
            print("[GTA SA Toolkit] mod texture %s: %s" % (tex.name, e))
    return out


def _game_txd(game, model):
    """The game's .txd name for a model (vehicles.ide / IDE entry, else the model name) if it exists."""
    if game is None or not model:
        return None
    low = model.lower()
    v = next((v for v in game.vehicles.values() if v.model.lower() == low), None)
    od = game.by_name.get(low)
    txd = (v.txd if v else (od.txd if od else low)).lower()
    return txd if game.imgs.find(txd + ".txd") is not None or game.find_loose_txd(txd) else None


def textures_for(m, game, pack=True):
    """Textures for a mod, searched in this order: the mod's own .txd, the game's .txd with the
    model's name, the "based on" model's .txd (vehicles: then the game's shared vehicle.txd).
    Returns (CIDict, [texture names none of them has], {"the game's elegy.txd": count} for the textures
    that came from somewhere other than the mod's own .txd)."""
    from . import mapimport
    layers = []                                     # (label, {name: [image]}), first wins
    data = read_txd(m)
    if data:
        try:
            layers.append((None, _decode_txd(data, image_prefix(m), pack)))
        except Exception as e:     # noqa
            print("[GTA SA Toolkit] mod TXD %s: %s" % (m.txd, e))
    if game is not None:
        cache = mapimport.TxdCache(game, pack)
        seen = set()
        base, _manual = base_of(m, game)
        for model in (m.name, base):
            txd = _game_txd(game, model)
            if txd and txd not in seen:
                seen.add(txd)
                layers.append(("the game's %s.txd" % txd, cache.get(txd)))
        if m.kind == 'VEHICLE' and "vehicle" not in seen:
            layers.append(("the game's vehicle.txd", cache.get("vehicle")))
    tex, origin = mapimport.CIDict(), {}
    for label, got in reversed(layers):            # lowest priority first, the mod's own last (wins)
        for k, v in got.items():
            tex[k] = v
            origin[k.lower()] = label
    missing = [t for t in m.textures if t.lower() not in tex]
    borrowed = {}
    for t in m.textures:
        label = origin.get(t.lower())
        if label:
            borrowed[label] = borrowed.get(label, 0) + 1
    return tex, missing, borrowed


def check_model(m, game, coll, missing_tex):
    """What won't work on this model: [(short text for the panel, full sentence for the status bar)]."""
    out = []
    arm = next((o for o in coll.all_objects if o.type == 'ARMATURE'), None)
    if m.kind == 'PED':
        if arm is None:
            out.append(("No skeleton", "No skeleton: it can't take animations or toys (is it really a ped?)"))
        else:
            gta = dffprobe.GTA_BONE_IDS            # matched by bone id, like the animations (names vary)
            have = {b.get("bone_id") for b in arm.data.bones}
            lost = sorted(gta - have)
            if lost:
                out.append(("%d GTA bones missing" % len(lost),
                            "%d of %d GTA bones missing (ids %s%s): animations may look wrong" % (
                                len(lost), len(gta), ", ".join(str(i) for i in lost[:4]),
                                "..." if len(lost) > 4 else "")))
    if missing_tex:
        n = len(missing_tex)
        out.append(("%d texture%s missing" % (n, "" if n == 1 else "s"),
                    "%d texture%s not found (%s%s): not in its .txd or the game's" % (
                        n, "" if n == 1 else "s", ", ".join(missing_tex[:3]), "..." if n > 3 else "")))
    return out


LAST = {"notes": [], "cached": False}        # the last import: notes ("3 textures from ..."), from the cache


def _build(context, m, tex):
    """Build of a mod with our reader + builder, or its asset-cache copy (keyed by the mod's cache_id;
    rebuilt when its files change). Returns the collection; RuntimeError for a file that can't be read."""
    from . import cache, mapimport
    use_cache = cache.enabled()
    key = None
    LAST["cached"] = False
    if use_cache:
        if not cache.in_session():
            cache.validate()
        key = cache.model_key(cache_id(m), [], {'textures': True, 'mod': True})
        ld = cache.fetch([key]).get(key)
        if ld is not None:
            cache.STATS["hits"] += 1
            LAST["cached"] = True
            return cache.finish_model(ld, tex, context.scene.collection)
    coll, own = mapimport.build_own(m.name, read_dff(m), {}, tex)
    if key is not None:
        cache.STATS["builds"] += 1
        cache.remember(None, key, cache_id(m), coll, own, [], srcs=_sources(m))
    return coll


def import_mod(context, game, key, pose=None, record=True):
    """Build a mod ped / vehicle / object at the 3D cursor (vehicles: finished like game vehicles, from
    their "based on" model's data). Returns (collection, warnings as full sentences); LAST["notes"]
    says where borrowed textures came from."""
    from . import events, library, lighttags, mapimport, ui
    m = get(key)
    if m is None:
        raise RuntimeError("That custom model is no longer in your Mod Folders (Rescan Mods?)")
    tex, missing, borrowed = textures_for(m, game, context.scene.gta_tk.pack_images)
    coll = _build(context, m, tex)
    base, _manual = base_of(m, game)
    cur = context.scene.cursor.location
    checks = []
    if m.kind == 'VEHICLE':
        from . import vehicles
        vdef = next((v for v in game.vehicles.values() if v.model.lower() == (base or "").lower()), None) \
            if game is not None else None
        root = vehicles.finish_vehicle(coll, game, vdef, vdef.model if vdef else m.name, location=cur.copy(),
                                       name=m.name)
        if root is not None:
            root["gta_mod_base"] = vdef.model if vdef else ""
            root["gta_mod_base_name"] = vdef.name if vdef else ""       # "Elegy", for Studio's notes
        checks += vehicles.vehicle_checks(coll, m.vkind)
    else:
        for ob in list(coll.objects):
            if ob.parent is None:
                ob.location = ob.location + cur
    checks += check_model(m, game, coll, missing)
    warnings = [full for _short, full in checks]
    notes = ["%d texture%s from %s" % (n, "" if n == 1 else "s", label) for label, n in sorted(borrowed.items())]
    for ob in list(coll.all_objects):
        if ob.parent is None or ob.type == 'ARMATURE' or ob.get("gta_vehicle"):
            ob["gta_mod"] = m.key
            ob["gta_mod_name"] = m.name
            if checks:
                ob["gta_mod_warnings"] = [short for short, _full in checks]
    try:
        lighttags.tag_collection_lights(coll, m.name)
    except Exception:                      # noqa
        pass
    arm = next((o for o in coll.all_objects if o.type == 'ARMATURE'), None)
    if pose is None:
        pose = bool(context.scene.gta_tk_x.ped_auto_pose)
    if arm is not None:
        if pose:
            ui.stand_up(context, arm, game)
        library._select_only(context, arm)
    if m.kind == 'VEHICLE':
        root = next((o for o in coll.all_objects if o.get("gta_vehicle")), None)
        if root is not None:
            library._select_only(context, root)
    if record:
        library.record(m.kind, m.key, m.name, "Mod", {"pose": bool(pose)} if m.kind == 'PED' else {})
    events.import_done(list(coll.all_objects))
    if game is not None:
        game.imgs.close()
    LAST["notes"] = notes
    return coll, warnings


def warnings_of(ob):
    """Short stored warnings of an imported mod (the object or any parent) - safe in draw()."""
    while ob is not None:
        w = ob.get("gta_mod_warnings")
        if w:
            return list(w)
        if ob.get("gta_mod"):
            return []
        ob = ob.parent
    return []


# ----------------------------------------------------------------------------- after a scan
def refresh(context, game=None):
    """Scan the Mod Folders and put the models into the Peds / Vehicles lists and the search index.
    Returns the models. Not for draw()."""
    from . import library, mapimport, ui_extra
    game = game if game is not None else mapimport._GAME.get("data")
    found = scan(game)
    ui_extra.fill_mods(context)
    library._INDEX["key"] = None                # search everything picks the mods up
    return found


# ----------------------------------------------------------------------------- operators
class GTATK_OT_mod_folder_add(bpy.types.Operator):
    bl_idname = "gtatk.mod_folder_add"
    bl_label = "Add Mod Folder"
    bl_description = "Add a folder with custom models (.dff + .txd files, .img archives)"
    bl_options = {'INTERNAL'}
    directory: bpy.props.StringProperty(subtype='DIR_PATH')

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        pr = _prefs()
        if pr is None or not self.directory:
            return {'CANCELLED'}
        f = pr.mod_folders.add()
        f.path = self.directory
        context.preferences.is_dirty = True
        n = len(refresh(context))
        self.report({'INFO'}, "%d custom models found" % n)
        return {'FINISHED'}


class GTATK_OT_mod_folder_remove(bpy.types.Operator):
    bl_idname = "gtatk.mod_folder_remove"
    bl_label = "Remove Mod Folder"
    bl_description = "Stop using this folder (the files stay where they are)"
    bl_options = {'INTERNAL'}
    index: bpy.props.IntProperty()

    def execute(self, context):
        pr = _prefs()
        if pr is None or not 0 <= self.index < len(pr.mod_folders):
            return {'CANCELLED'}
        pr.mod_folders.remove(self.index)
        context.preferences.is_dirty = True
        refresh(context)
        return {'FINISHED'}


class GTATK_OT_mods_rescan(bpy.types.Operator):
    bl_idname = "gtatk.mods_rescan"
    bl_label = "Rescan Mods"
    bl_description = "Look through the Mod Folders again (only new or changed files are read)"

    def execute(self, context):
        try:
            found = refresh(context)
        except Exception as e:           # noqa
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        _f, c = summary()
        msg = "%d custom models: %d peds, %d vehicles, %d objects (%.1f s)" % (
            len(found), c['PED'], c['VEHICLE'], c['OBJECT'], _MODS["secs"])
        for n in _MODS["notes"]:
            self.report({'WARNING'}, n)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


classes = (GTATK_OT_mod_folder_add, GTATK_OT_mod_folder_remove, GTATK_OT_mods_rescan)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
