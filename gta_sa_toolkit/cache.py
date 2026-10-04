# GTA SA Toolkit - asset cache (off by default).
#
# When it's on, built models are saved to .blend "packs" and appended on later imports,
# and decoded textures are saved as PNG and loaded from there. Layout, inside the folder the user
# picked (Clear Cache only ever deletes this subfolder):
#
#   GTA SA Toolkit Cache/
#     index.json              entries + the size and modified time of every source file they came from
#     models/p0001.blend      the models one import built: meshes + objects, material slots left empty
#     models/p0001.mats       the material descriptions of those meshes (rwbuild.describe; pickle)
#     textures/<txd>/*.png    decoded textures, shared by all models
#     thumbs/<model>.png      thumbnails (thumbs.py)
#
# Why materials aren't in the .blend: appending a material makes Blender check every other material
# in the file, so appending LAe+LAe2 (1,700 materials) took 30 s against 9 s for a fresh build.
# Meshes and objects alone append in ~1 s, and rwbuild.material() rebuilds the materials from the
# saved descriptions, identical to a fresh import.
#
# Invalidation: before each import, the recorded size + modified time of every source IMG / loose TXD
# is compared with the file on disk. Anything built from a changed file is deleted (whole packs, the
# TXD folders, thumbnails) and rebuilt the next time it's needed. A different builder version
# (rwbuild.BUILD_VERSION) or cache FORMAT starts the cache over.
import json
import os
import pickle
import re
import shutil
import struct
import time
import zlib

import bpy


FORMAT = 3                # cache layout version (3: every model built by rwbuild)
DIRNAME = "GTA SA Toolkit Cache"

_IDX = {"root": None, "data": None, "models": None}
_SIZE = {"root": None, "bytes": 0}
_PENDING = {"depth": 0, "items": []}      # models built inside a session(), written as one pack at its end
_WARNED = set()
STATS = {"hits": 0, "builds": 0}          # models appended from the cache / built fresh with the cache on


# ============================================================================= settings
def _prefs():
    try:
        return bpy.context.preferences.addons[__package__].preferences
    except (KeyError, AttributeError):
        return None


def folder_of(pr):
    d = (pr.cache_dir or "").strip()
    return os.path.join(bpy.path.abspath(d), DIRNAME) if d else None


def root():
    """The cache folder when the cache is on (and has a folder), else None."""
    pr = _prefs()
    if pr is None or not pr.cache_enabled:
        return None
    return folder_of(pr)


def enabled():
    return root() is not None


def _log(msg):
    print("[GTA SA Toolkit] cache: %s" % msg)


# ============================================================================= index
def _builder_version():
    from . import rwbuild
    return "rw%d" % rwbuild.BUILD_VERSION


def _empty(builder):
    return {"format": FORMAT, "builder": builder, "files": {}, "models": {}, "packs": {}, "txds": {},
            "next": 1}


def _index_path(r):
    return os.path.join(r, "index.json")


def index(r=None):
    """The index of cache folder r (loaded once, kept in memory). Starts over when the builder changes."""
    r = r or root()
    if r is None:
        return None
    if _IDX["root"] == r and _IDX["data"] is not None:
        return _IDX["data"]
    ver = _builder_version()
    data = None
    try:
        with open(_index_path(r), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        pass
    if data is not None and (data.get("format") != FORMAT or data.get("builder") != ver):
        _log("builder or cache format changed (%s -> %s): starting over" % (data.get("builder"), ver))
        _wipe(r)
        data = None
    if data is None:
        data = _empty(ver)
    _IDX.update(root=r, data=data, models=None)
    return data


def _save_index(r, data):
    _IDX["models"] = None
    os.makedirs(r, exist_ok=True)
    tmp = _index_path(r) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, _index_path(r))


def _wipe(r):
    for sub in ("models", "textures", "thumbs"):
        shutil.rmtree(os.path.join(r, sub), ignore_errors=True)
    try:
        os.remove(_index_path(r))
    except OSError:
        pass


def _norm(path):
    return os.path.normcase(os.path.abspath(path))


def signature(path):
    """[size, modified time in ns] of a source file, or None when it's gone."""
    try:
        st = os.stat(path)
        return [st.st_size, st.st_mtime_ns]
    except OSError:
        return None


def _note_file(data, path):
    p = _norm(path)
    if p not in data["files"]:
        data["files"][p] = signature(path)
    return p


def validate():
    """Drop everything built from source files that changed since. Cheap (a few os.stat calls);
    called at the start of every import. Returns the number of models dropped."""
    r = root()
    if r is None:
        return 0
    data = index(r)
    stale = {p for p, sig in data["files"].items() if signature(p) != sig}
    if not stale:
        return 0
    dead_keys = {k for k, e in data["models"].items() if stale.intersection(e["src"])}
    dead_packs = {data["models"][k]["pack"] for k in dead_keys}
    for pk in dead_packs:                              # a pack goes as a whole
        dead_keys.update(data["packs"].get(pk, []))
        _remove_pack_files(r, pk)
        data["packs"].pop(pk, None)
    models = {data["models"][k]["model"] for k in dead_keys if k in data["models"]}
    for k in dead_keys:
        data["models"].pop(k, None)
    for t in [t for t, e in data["txds"].items() if e["src"] in stale]:
        shutil.rmtree(os.path.join(r, "textures", data["txds"][t]["dir"]), ignore_errors=True)
        data["txds"].pop(t)
    for p in stale:
        data["files"].pop(p, None)
    from . import thumbs
    thumbs.forget(r, models)
    _save_index(r, data)
    _log("%d source file(s) changed: %d cached models will be rebuilt" % (len(stale), len(dead_keys)))
    refresh_size()
    return len(dead_keys)


def _remove_pack_files(r, pk):
    for ext in (".blend", ".mats"):
        try:
            os.remove(os.path.join(r, "models", pk + ext))
        except OSError:
            pass


def has_model(model):
    """True when some cached entry holds this model (thumbnails can be made from it). Cheap enough
    for draw(): a set lookup, rebuilt only after the index changes."""
    r = root()
    if r is None:
        return False
    data = index(r)
    if _IDX.get("models") is None:
        _IDX["models"] = {e["model"] for e in data["models"].values()}
    return model.lower() in _IDX["models"]


def model_entry(model):
    """(key, entry) of a cached copy of this model (any import options), or (None, None)."""
    data = index()
    if not data:
        return None, None
    low = model.lower()
    for k, e in data["models"].items():
        if e["model"] == low:
            return k, e
    return None, None


# ============================================================================= size / clear
def refresh_size():
    """Walk the cache folder once and store its size (panels show the stored value)."""
    pr = _prefs()
    r = folder_of(pr) if pr else None
    total = 0
    if r and os.path.isdir(r):
        for base, _dirs, files in os.walk(r):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(base, f))
                except OSError:
                    pass
    _SIZE.update(root=r, bytes=total)
    return total


def size_text():
    b = _SIZE["bytes"]
    if b < 1024 * 1024:
        return "%d KB" % (b // 1024)
    if b < 1024 ** 3:
        return "%.0f MB" % (b / 1024 ** 2)
    return "%.1f GB" % (b / 1024 ** 3)


def clear():
    """Delete this cache's folder (only our own subfolder of the chosen folder)."""
    pr = _prefs()
    r = folder_of(pr) if pr else None
    if r and os.path.isdir(r) and os.path.basename(r) == DIRNAME:
        shutil.rmtree(r, ignore_errors=True)
    _IDX.update(root=None, data=None, models=None)
    from . import thumbs
    thumbs.reset()
    refresh_size()


# ============================================================================= textures (PNG)
def _png(path, arr):
    """Write a (h, w, 4) uint8 array (top row first) as PNG. zlib level 1: ~2x faster than Blender's
    image saver and only slightly bigger."""
    import numpy as np
    h, w = arr.shape[:2]
    raw = np.empty((h, w * 4 + 1), np.uint8)
    raw[:, 0] = 0
    raw[:, 1:] = arr.reshape(h, w * 4)

    def chunk(tag, d):
        return struct.pack(">I", len(d)) + tag + d + struct.pack(">I", zlib.crc32(tag + d) & 0xffffffff)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw.tobytes(), 1)) + chunk(b"IEND", b""))


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:60] or "_"


def txd_source(game, txd):
    e = game.imgs.find(txd + ".txd")
    if e is not None:
        return e.archive.path
    return game.find_loose_txd(txd)


def load_txd(game, txd, pack):
    """{lower texture name: [image]} from the cache (images load their pixels lazily), or None."""
    r = root()
    if r is None:
        return None
    data = index(r)
    e = data["txds"].get(txd)
    if e is None:
        return None
    src = txd_source(game, txd)
    if src is None or _norm(src) != e["src"]:
        return None
    folder = os.path.join(r, "textures", e["dir"])
    out = {}
    for low, (name, fn, _w, _h) in e["tex"].items():
        iname = "%s.txd/%s" % (txd, name)
        img = bpy.data.images.get(iname)
        if img is None:
            path = os.path.join(folder, fn)
            if not os.path.isfile(path):
                return None
            img = bpy.data.images.load(path, check_existing=False)
            img.name = iname
            img["gta_tk_tex"] = True
            if pack == 'NOW':
                img.pack()
        out[low] = [img]
    return out


def save_txd(game, txd, decoded):
    """decoded: list of (texture name, (h, w, 4) uint8 array). Writes the PNGs and the index entry."""
    r = root()
    if r is None or not decoded:
        return
    src = txd_source(game, txd)
    if src is None:
        return
    data = index(r)
    d = _safe(txd)
    folder = os.path.join(r, "textures", d)
    try:
        os.makedirs(folder, exist_ok=True)
        tex = {}
        for i, (name, arr) in enumerate(decoded):
            fn = "%03d_%s.png" % (i, _safe(name))
            _png(os.path.join(folder, fn), arr)
            tex[name.lower()] = [name, fn, int(arr.shape[1]), int(arr.shape[0])]
    except (OSError, ValueError) as e:
        _warn("textures", "could not write textures (%s)" % e)
        return
    data["txds"][txd] = {"src": _note_file(data, src), "dir": d, "tex": tex}
    if _PENDING["depth"] == 0:
        _save_index(r, data)


def _warn(key, msg):
    if key not in _WARNED:
        _WARNED.add(key)
        _log(msg)


# ============================================================================= models
def _dumps(obj):
    return pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)


def model_key(model, chain, opts):
    """Everything that changes what gets built: model, TXD chain (parents last), import options."""
    o = ",".join("%s=%d" % (k, int(bool(v))) for k, v in sorted(opts.items()))
    return "%s|%s|%s" % (model.lower(), ">".join(chain), o)


class OwnRecipes:
    """What rwbuild.build gives the cache: recipes = {mesh pointer: ('rw', [material description])};
    uv_anim = it has UV-animated materials (such models aren't cached)."""

    def __init__(self, recipes, uv_anim):
        self.recipes, self.uv_anim = recipes, uv_anim


def remember(game, key, model, coll, rec, txd_chain, srcs=None):
    """A model was just built with the cache on: queue it for the current session's pack (or write
    it at once outside a session). Models with UV-animated materials are left out. srcs: the files it
    was built from when they aren't game IMG entries (custom models: their .dff / .img and .txd)."""
    if root() is None or rec.uv_anim or not rec.recipes:
        return
    if srcs is None:
        srcs = []
        e = game.imgs.find(model + ".dff")
        if e is None:
            return
        srcs.append(e.archive.path)
        for t in txd_chain:
            s = txd_source(game, t)
            if s:
                srcs.append(s)
    item = {"key": key, "model": model.lower(), "coll": coll, "kids": list(coll.children),
            "recipes": rec.recipes, "src": srcs}
    _PENDING["items"].append(item)
    if _PENDING["depth"] == 0:
        flush()


class session:
    """Models built inside are written as one pack when the outermost session ends."""

    def __enter__(self):
        begin()
        return self

    def __exit__(self, *exc):
        end()
        return False


def in_session():
    return _PENDING["depth"] > 0


def begin():
    if _PENDING["depth"] == 0 and root() is not None:
        validate()
    _PENDING["depth"] += 1


def end():
    _PENDING["depth"] = max(0, _PENDING["depth"] - 1)
    if _PENDING["depth"] == 0:
        flush()


def flush():
    """Write the queued models as one pack."""
    items, _PENDING["items"] = _PENDING["items"], []
    r = root()
    if r is None:
        return
    data = index(r)
    if not items:
        _save_index(r, data)            # textures written during the session
        return
    t0 = time.perf_counter()
    ids, recipes, stripped, entries = set(), [], [], []
    for it in items:
        try:
            coll = it["coll"]
            coll.name
            kids = []
            for k in it["kids"]:
                try:
                    k.name
                    kids.append(k)
                except ReferenceError:
                    pass
        except ReferenceError:            # removed or undone before the session ended
            continue
        ok = True
        meshes = {o.data for c in [coll] + kids for o in c.all_objects if o.type == 'MESH' and o.data}
        todo = []
        for me in meshes:
            rcp = it["recipes"].get(me.as_pointer())
            if rcp is None:
                continue                  # e.g. collision meshes: keep their materials in the pack
            if len(rcp[1]) != len(me.materials):
                ok = False
                break
            todo.append((me, rcp))
        if not ok:
            continue
        for me, rcp in todo:
            me["gtatk_rk"] = len(recipes)
            recipes.append(rcp)
            stripped.append((me, list(me.materials)))
        ids.add(coll)
        ids.update(kids)
        entries.append((it, coll.name, [k.name for k in kids]))
    if not entries:
        return
    pk = "p%04d" % data["next"]
    data["next"] += 1
    folder = os.path.join(r, "models")
    try:
        os.makedirs(folder, exist_ok=True)
        for me, mats in stripped:
            for i in range(len(mats)):
                me.materials[i] = None
        bpy.data.libraries.write(os.path.join(folder, pk + ".blend"), ids, path_remap='NONE', compress=False)
        blob = _dumps(recipes)
        with open(os.path.join(folder, pk + ".mats"), "wb") as f:
            f.write(blob)
    except Exception as e:                # noqa - disk full, no permission, ...
        _log("could not write %s (%s)" % (pk, e))
        _remove_pack_files(r, pk)
        return
    finally:
        for me, mats in stripped:
            for i, m in enumerate(mats):
                me.materials[i] = m
            del me["gtatk_rk"]
    keys = []
    for it, cname, knames in entries:
        old = data["models"].get(it["key"])
        data["models"][it["key"]] = {"pack": pk, "coll": cname, "kids": knames, "model": it["model"],
                                     "src": [_note_file(data, s) for s in it["src"]]}
        keys.append(it["key"])
        if old is not None:
            _drop_from_pack(r, data, old["pack"], it["key"])
    data["packs"][pk] = keys
    _save_index(r, data)
    refresh_size()
    _log("saved %d models in %s (%.2f s)" % (len(keys), pk, time.perf_counter() - t0))


def _drop_from_pack(r, data, pk, key):
    lst = data["packs"].get(pk)
    if lst is None:
        return
    if key in lst:
        lst.remove(key)
    if not any(data["models"].get(k, {}).get("pack") == pk for k in lst):
        data["packs"].pop(pk, None)
        _remove_pack_files(r, pk)


class Loaded:
    """A cached model appended from its pack; its materials are added by finish_model()."""
    __slots__ = ("coll", "kids", "recipes")

    def __init__(self, coll, kids, recipes):
        self.coll, self.kids, self.recipes = coll, kids, recipes


def fetch(keys):
    """Append the cached models among keys, one libraries.load per pack.
    Returns {key: Loaded}; keys that aren't cached (or fail to load) are left out."""
    r = root()
    if r is None:
        return {}
    data = index(r)
    by_pack = {}
    for k in keys:
        e = data["models"].get(k)
        if e is not None:
            by_pack.setdefault(e["pack"], []).append(k)
    out = {}
    for pk, ks in by_pack.items():
        path = os.path.join(r, "models", pk + ".blend")
        try:
            with open(os.path.join(r, "models", pk + ".mats"), "rb") as f:
                recipes = pickle.load(f)
            names = []
            for k in ks:
                e = data["models"][k]
                names += [e["coll"]] + e["kids"]
            with bpy.data.libraries.load(path, link=False) as (src, dst):
                have = set(src.collections)
                req = [n for n in dict.fromkeys(names) if n in have]
                dst.collections = list(req)
            # appended names can get a .001 suffix: map by request order
            got = {n: c for n, c in zip(req, dst.collections) if c is not None}
        except Exception as e:            # noqa - missing / broken pack, ...
            _log("pack %s unusable (%s): rebuilding its models" % (pk, e))
            for k in data["packs"].get(pk, list(ks)):
                data["models"].pop(k, None)
            data["packs"].pop(pk, None)
            _remove_pack_files(r, pk)
            _save_index(r, data)
            continue
        for k in ks:
            e = data["models"][k]
            coll = got.get(e["coll"])
            if coll is None:
                continue
            out[k] = Loaded(coll, [got[n] for n in e["kids"] if n in got], recipes)
    return out


def finish_model(loaded, txd_images, link_to=None, share=None):
    """Give a fetched model its materials (rwbuild.material, the same code as a fresh build) and its
    embedded collision collections back. Returns the model's collection."""
    from . import rwbuild
    coll = loaded.coll
    for k in loaded.kids:                            # embedded collision collections
        if k.name not in coll.children:
            coll.children.link(k)
    share = {} if share is None else share
    imgs = txd_images if txd_images is not None else {}
    for ob in coll.all_objects:
        me = ob.data if ob.type == 'MESH' else None
        if me is None or "gtatk_rk" not in me:
            continue
        rcp = loaded.recipes[me["gtatk_rk"]]
        del me["gtatk_rk"]
        for i, desc in enumerate(rcp[1][:len(me.materials)]):
            me.materials[i] = rwbuild.material(desc, imgs, share)
    return _linked(coll, loaded, link_to)


def _linked(coll, loaded, link_to):
    """Link a finished cached model and hide its embedded collision, like a fresh build."""
    if link_to is not None and coll.name not in link_to.children:
        link_to.children.link(coll)
    for k in loaded.kids:
        for ob in list(k.objects):
            try:
                ob.hide_set(True)
            except RuntimeError:
                pass
    return coll


def discard(loaded_list):
    """Remove fetched models that were never used (import stopped early)."""
    ids = []
    for ld in loaded_list:
        try:
            if ld.coll.users == 0:
                ids += [ld.coll] + list(ld.coll.all_objects) + ld.kids
        except ReferenceError:
            pass
    if ids:
        bpy.data.batch_remove(set(ids))
