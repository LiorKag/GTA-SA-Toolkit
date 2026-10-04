# GTA SA Toolkit - Library: favorites, recent imports and search across all game data.
#
# Favorites and recents live in the add-on preferences (they follow the user across .blend files),
# mirrored to library.json in the add-on's user folder so they survive even when Blender's
# "Auto-Save Preferences" is off. Each change bumps a revision number stored in both; at start-up
# the newer copy wins (Blender doesn't always save its preferences on quit, so they can be older
# than library.json). Every import that should show up in Recent goes through the
# place_* / apply_anim functions below; the Items/Characters operators and the Library tab share them.
#
# An item is (kind, key): models use their IDE id as key, animations "<source>|<anim name>" where
# source is "ped.ifp", "anim.img/<file>.ifp" (both inside the game folder) or an absolute IFP path.
import json
import math
import os
import time
from types import SimpleNamespace

import bpy
from bpy.props import BoolProperty, CollectionProperty, FloatProperty, IntProperty, PointerProperty, StringProperty

from . import events, mapimport, vehicles, weapons
from .formats import ifp as ifp_fmt
from .formats import img as img_fmt
from .formats import mapdata

# kind, group label, single label, icon
KINDS = (
    ('VEHICLE', "Vehicles", "Vehicle", 'AUTO'),
    ('WEAPON', "Weapons", "Weapon", 'MOD_PHYSICS'),
    ('PED', "Peds", "Ped", 'OUTLINER_OB_ARMATURE'),
    ('TOY', "Toys", "Toy", 'MOD_CLOTH'),
    ('OBJECT', "Objects", "Object", 'OBJECT_DATA'),
    ('ANIM', "Animations", "Animation", 'ACTION'),
    ('IFP', "IFP Files", "IFP File", 'FILE_BLANK'),
)
KIND_LABEL = {k[0]: k[2] for k in KINDS}
KIND_ICON = {k[0]: k[3] for k in KINDS}
KIND_ORDER = {k[0]: i for i, k in enumerate(KINDS)}
MAX_RECENT = 30
MAX_RESULTS = 200
RECENT_ROWS = 10                # shown until "Show all" is ticked

# IFP options a recent animation remembers (names of GTATK_Props fields)
ANIM_SETTINGS = ("ifp_fps_mode", "ifp_custom_fps", "ifp_snap", "ifp_start", "ifp_root", "ifp_new_action",
                 "ifp_clear_pose", "ifp_adjust_range", "ifp_set_scene_fps")

_CACHE = {"favs": None, "groups": None}         # rebuilt after every change (panels read these)
_INDEX = {"key": None, "rows": []}
_ANIM_IMG = {"key": None, "files": {}}
_SRC = {}


# ============================================================================= storage
class GTATK_LibItem(bpy.types.PropertyGroup):
    """A favorite or recent import (name = what the lists show)."""
    kind: StringProperty()
    key: StringProperty()
    detail: StringProperty()        # model id, or IFP file for animations
    note: StringProperty()          # short summary of the saved settings
    settings: StringProperty()      # JSON of the settings of the latest import (favorites too)
    when: FloatProperty()


class GTATK_ModFolder(bpy.types.PropertyGroup):
    """A folder with custom / modded models (loose .dff + .txd, and .img archives) - mods.py."""
    path: StringProperty(name="Folder", subtype='DIR_PATH',
                         description="Folder with .dff/.txd files and/or .img archives of custom models")
    subfolders: BoolProperty(name="Subfolders", default=True, description="Also look in its subfolders")
    enabled: BoolProperty(name="Use", default=True, description="Include this folder when scanning")


class GTATK_TimecycFile(bpy.types.PropertyGroup):
    path: StringProperty(name="File", subtype='FILE_PATH')


def _studio_switch(self, context):
    from . import studiolink
    studiolink.switch_changed()


def _studio_areas(self, context):
    from . import studiolink
    studiolink.areas_changed()


class GTATK_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    # GTA SA Studio (built in; studiolink.py): switches and its settings (kept when it's off)
    studio_on: BoolProperty(name="GTA SA Studio", default=True, update=_studio_switch,
                            description="Scene, lighting, vehicle, toy, path and animation tools. Off: none of "
                                        "them are loaded")
    studio_scene: BoolProperty(name="Scene Tab", default=True, update=_studio_areas,
                               description="Time & weather, sky, map lights, camera & render")
    studio_animate: BoolProperty(name="Animate Tab", default=True, update=_studio_areas,
                                 description="Combine IFP clips into one animation, bake and export it")
    studio_vehicle: BoolProperty(name="Vehicle Sections", default=True, update=_studio_areas,
                                 description="Colours, lights, paintjobs, doors and upgrades in the Vehicles tab")
    studio_toys: BoolProperty(name="Toy Editor", default=True, update=_studio_areas,
                              description="Edit attached SA-MP toys (Peds tab)")
    studio_pawn: BoolProperty(name="Pawn Export", default=True, update=_studio_areas,
                              description="Export map objects as SA-MP Pawn code (World tab)")
    studio_paths: BoolProperty(name="Traffic & Ped Paths", default=True, update=_studio_areas,
                               description="Draw the game's paths, make a vehicle or ped follow them (World tab)")
    timecyc_files: CollectionProperty(type=GTATK_TimecycFile)          # added with Add File... (studio/timecycs.py)
    timecyc_hidden: StringProperty(options={'HIDDEN'})                  # Mod Folder finds removed from the list
    timecyc_path: StringProperty(
        name="timecyc.dat", subtype='FILE_PATH',
        description="Optional: a timecyc.dat to use instead of the game folder's data/timecyc.dat "
                    "(e.g. an unmodified copy when the game has a weather mod)")
    studio_prefs_copied: BoolProperty(options={'HIDDEN'},
                                      description="The separate GTA SA Studio's settings were copied (studiolink.py)")

    favorites: CollectionProperty(type=GTATK_LibItem)
    recents: CollectionProperty(type=GTATK_LibItem)
    revision: IntProperty(options={'HIDDEN'}, description="Bumped on every change (see library.json)")
    recent_ifps: StringProperty(options={'HIDDEN'},
                                description="IFP files picked lately, newest first, one per line (ifplist.py)")
    mod_folders: CollectionProperty(type=GTATK_ModFolder)
    mods_show_all: BoolProperty(
        name="Show all models in .img archives", default=False,
        description="List every model inside a mod .img, not only the ones that are new or differ from the "
                    "game's (compared by name and size)")
    mod_bases: StringProperty(options={'HIDDEN'},
                              description="Game models chosen as 'based on' for mod models, JSON {key: model} (mods.py)")
    mod_overrides: StringProperty(options={'HIDDEN'},
                                  description="Types chosen by hand for mod models, JSON {key: kind} (mods.py)")
    cache_enabled: BoolProperty(
        name="Asset Cache", default=False, update=lambda self, ctx: _cache_changed(),
        description="Save built models (.blend) and decoded textures (PNG) in the cache folder and reuse "
                    "them on later imports. Rebuilt automatically when the game's IMG files change")
    cache_dir: StringProperty(
        name="Cache Folder", subtype='DIR_PATH', update=lambda self, ctx: _cache_changed(),
        description="Folder for the asset cache (a 'GTA SA Toolkit Cache' folder is made inside it)")

    def draw(self, context):
        l = self.layout
        l.label(text="Library: %d favorites, %d recent imports (see the sidebar's Library tab)" % (
            len(self.favorites), len(self.recents)), icon='SOLO_ON')
        r = l.row()
        r.operator("gtatk.library_clear_recent", icon='TRASH')
        l.separator()
        draw_mod_folders(l, self)
        l.separator()
        draw_studio_prefs(l, self)


def draw_studio_prefs(layout, pr):
    from . import studiolink
    from .studio.corelink import AREAS
    studiolink.draw_old_message(layout)
    box = layout.box()
    box.prop(pr, "studio_on")
    col = box.column(align=True)
    col.active = pr.studio_on and not studiolink.old()
    for area, _label, _desc in AREAS:
        col.prop(pr, "studio_" + area)
    col.separator()
    col.prop(pr, "timecyc_path")


def draw_mod_folders(layout, pr):
    """Mod Folders: the preferences' list (add / remove / subfolders), show-all switch, rescan."""
    from . import mods
    box = layout.box()
    r = box.row()
    r.label(text="Mod Folders (custom models)", icon='FILE_FOLDER')
    r.operator("gtatk.mod_folder_add", text="Add Folder", icon='ADD')
    if not pr.mod_folders:
        box.label(text="Add a folder with .dff/.txd files or .img archives of custom models", icon='INFO')
    for i, f in enumerate(pr.mod_folders):
        r = box.row(align=True)
        r.prop(f, "enabled", text="")
        c = r.row(align=True)
        c.active = f.enabled
        c.prop(f, "path", text="")
        c.prop(f, "subfolders", text="", icon='OUTLINER')
        r.operator("gtatk.mod_folder_remove", text="", icon='X').index = i
    box.prop(pr, "mods_show_all")
    n_folders, c = mods.summary()
    r = box.row()
    r.label(text="%d peds, %d vehicles, %d objects" % (c['PED'], c['VEHICLE'], c['OBJECT']))
    r.operator("gtatk.mods_rescan", icon='FILE_REFRESH')


def _cache_changed():
    from . import cache
    cache.refresh_size()
    if cache.enabled():
        cache.validate()


def prefs():
    try:
        return bpy.context.preferences.addons[__package__].preferences
    except (KeyError, AttributeError):
        return None


def _item_dict(it):
    return {"kind": it.kind, "key": it.key, "name": it.name, "detail": it.detail, "note": it.note,
            "settings": it.settings, "when": it.when}


def _fill(coll, rows):
    coll.clear()
    for d in rows:
        it = coll.add()
        it.kind, it.key, it.name = d.get("kind", ""), d.get("key", ""), d.get("name", "")
        it.detail, it.note, it.settings = d.get("detail", ""), d.get("note", ""), d.get("settings", "")
        it.when = float(d.get("when", 0.0))


def backup_path():
    d = os.environ.get("GTATK_LIBRARY_DIR")         # set by the test runner: tests never touch the real one
    if d:
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "library.json")
    return os.path.join(bpy.utils.extension_path_user(__package__, path="library", create=True), "library.json")


def save_backup():
    pr = prefs()
    if pr is None:
        return
    data = {"version": 1, "revision": pr.revision, "favorites": [_item_dict(i) for i in pr.favorites],
            "recents": [_item_dict(i) for i in pr.recents]}
    path = backup_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def restore_backup(force=False):
    """Load library.json into the preferences when it is newer (higher revision) than them, when
    they are empty, or always with force. Returns True if something was loaded."""
    pr = prefs()
    if pr is None:
        return False
    try:
        with open(backup_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    rev = int(data.get("revision", 0))
    empty = not (len(pr.favorites) or len(pr.recents))
    if not (force or empty or rev > pr.revision):
        return False
    _fill(pr.favorites, data.get("favorites", []))
    _fill(pr.recents, data.get("recents", [])[:MAX_RECENT])
    pr.revision = max(rev, pr.revision)
    _invalidate()
    return True


def _invalidate():
    _CACHE["favs"] = _CACHE["groups"] = None


def _changed():
    _invalidate()
    pr = prefs()
    if pr is not None:
        pr.revision += 1
    try:
        bpy.context.preferences.is_dirty = True      # so Blender's auto-save keeps it
    except Exception:                                # noqa
        pass
    try:
        save_backup()
    except Exception as e:                           # noqa
        print("[GTA SA Toolkit] library backup:", e)


# ============================================================================= favorites
def _fav_set():
    if _CACHE["favs"] is None:
        pr = prefs()
        _CACHE["favs"] = {(i.kind, i.key) for i in pr.favorites} if pr else set()
    return _CACHE["favs"]


def is_favorite(kind, key):
    return (kind, str(key)) in _fav_set()


def favorite_groups():
    """[(kind, [favorite items])] in KINDS order, only kinds that have favorites."""
    if _CACHE["groups"] is None:
        pr = prefs()
        g = {}
        for i, it in enumerate(pr.favorites if pr else ()):
            g.setdefault(it.kind, []).append(i)
        _CACHE["groups"] = [(k[0], g[k[0]]) for k in KINDS if k[0] in g]
    pr = prefs()
    return [(k, [pr.favorites[i] for i in idx if i < len(pr.favorites)]) for k, idx in _CACHE["groups"]]


def set_favorite(kind, key, name, detail="", state=None):
    """Star (state True), unstar (False) or toggle (None). Returns the new state.
    A new favorite takes the settings of its latest import, if it is in Recent."""
    pr = prefs()
    if pr is None:
        raise RuntimeError("GTA SA Toolkit preferences are not available")
    key = str(key)
    idx = next((i for i, it in enumerate(pr.favorites) if it.kind == kind and it.key == key), -1)
    if state is None:
        state = idx < 0
    if state and idx < 0:
        it = pr.favorites.add()
        it.kind, it.key, it.name, it.detail, it.when = kind, key, name, detail, time.time()
        rec = next((r for r in pr.recents if r.kind == kind and r.key == key), None)
        if rec is not None:
            it.settings, it.note = rec.settings, rec.note
    elif not state and idx >= 0:
        pr.favorites.remove(idx)
    _changed()
    return state


def draw_star(layout, kind, key, name, detail=""):
    fav = is_favorite(kind, key)
    op = layout.operator("gtatk.favorite", text="", icon='SOLO_ON' if fav else 'SOLO_OFF', emboss=False)
    op.kind, op.key, op.name, op.detail = kind, str(key), name, detail


# ============================================================================= recents
def record(kind, key, name, detail="", settings=None, note=""):
    """Put an import at the top of Recent (the same item moves up; its settings are replaced).
    A favorite of the same item also takes these settings, so it imports the way it was last used."""
    pr = prefs()
    if pr is None:
        return
    key = str(key)
    for i in range(len(pr.recents) - 1, -1, -1):
        if pr.recents[i].kind == kind and pr.recents[i].key == key:
            pr.recents.remove(i)
    text = json.dumps(settings or {}, sort_keys=True)
    it = pr.recents.add()
    it.kind, it.key, it.name, it.detail, it.note = kind, key, name, detail, note
    it.settings, it.when = text, time.time()
    pr.recents.move(len(pr.recents) - 1, 0)      # 'it' now points at another row: don't use it below
    while len(pr.recents) > MAX_RECENT:
        pr.recents.remove(len(pr.recents) - 1)
    for fav in pr.favorites:
        if fav.kind == kind and fav.key == key:
            fav.settings, fav.note = text, note
    _changed()


def clear_recent():
    pr = prefs()
    if pr is not None:
        pr.recents.clear()
        _changed()


# ============================================================================= game items
def kind_of(game, oid):
    od = game.objects.get(int(oid))
    if int(oid) in game.vehicles:
        return 'VEHICLE'
    if od is not None and (od.kind == 'weap' or int(oid) in weapons.WEAPONS):
        return 'WEAPON'
    if od is not None and od.kind == 'peds':
        return 'PED'
    if int(oid) in game.samp_ids:
        return 'TOY'
    return 'OBJECT'


def display_name(game, oid):
    od = game.objects.get(int(oid))
    if int(oid) in weapons.WEAPONS:
        return weapons.WEAPONS[int(oid)][1]
    v = game.vehicles.get(int(oid))
    if v is not None:
        return v.name                   # in-game name ("Ambulance"), model name if american.gxt lacks it
    return od.model if od else str(oid)


def model_item(game, name_or_id):
    """(kind, key, name, detail) for a model name or ID, or None if the game doesn't know it."""
    k = str(name_or_id).strip().lower()
    if not k:
        return None
    od = game.objects.get(int(k)) if k.isdigit() else game.by_name.get(k)
    if od is None:
        return None
    return kind_of(game, od.id), str(od.id), display_name(game, od.id), str(od.id)


def loaded_game():
    """The game index if it is already loaded (never loads it: safe to call from draw())."""
    return mapimport._GAME.get("data")


def _root(context):
    return bpy.path.abspath(context.scene.gta_tk.game_root).rstrip("\\/")


def anim_source(game_root, path):
    """Short, file-independent name for an IFP path ("ped.ifp" for the game's own)."""
    k = (game_root, path)
    src = _SRC.get(k)
    if src is None:
        full = os.path.normcase(os.path.abspath(bpy.path.abspath(path)))
        ped = os.path.normcase(os.path.join(bpy.path.abspath(game_root).rstrip("\\/"), "anim", "ped.ifp"))
        src = "ped.ifp" if full == ped else os.path.abspath(bpy.path.abspath(path))
        _SRC[k] = src
    return src


def source_label(src):
    return src.split("/", 1)[1] if src.startswith("anim.img/") else os.path.basename(src)


def _anim_img(root):
    path = mapdata.find_ci(root, "anim/anim.img")
    if not path:
        return None, {}
    key = (path, os.path.getmtime(path))
    if _ANIM_IMG["key"] != key:
        _ANIM_IMG.update(key=key, files={})
    return path, _ANIM_IMG["files"]


def ifp_sources(root):
    """The game's IFP files as [(source, label)]: ped.ifp first, then the IFPs inside anim\\anim.img
    (only the listing is read, cached until anim.img changes)."""
    root = bpy.path.abspath(root).rstrip("\\/")
    out = []
    if os.path.isfile(os.path.join(root, "anim", "ped.ifp")):
        out.append(("ped.ifp", "ped.ifp"))
    path, _files = _anim_img(root)
    if path:
        if _ANIM_IMG.get("list_key") != _ANIM_IMG["key"]:
            arc = img_fmt.ImgArchive(path)
            try:
                names = sorted((e.name for e in arc.order if e.name.lower().endswith(".ifp")), key=str.lower)
            finally:
                arc.close()
            _ANIM_IMG.update(list_key=_ANIM_IMG["key"], names=names)
        out += [("anim.img/" + n, n) for n in _ANIM_IMG["names"]]
    return out


def load_anim_file(context, src):
    """The IfpFile an animation source points at."""
    from . import ui
    root = _root(context)
    if src == "ped.ifp":
        return ui.load_ifp(os.path.join(root, "anim", "ped.ifp"))
    if src.startswith("anim.img/"):
        path, files = _anim_img(root)
        if not path:
            raise RuntimeError("anim.img not found in the game's anim folder")
        name = src.split("/", 1)[1]
        if name not in files:
            arc = img_fmt.ImgArchive(path)
            try:
                data = arc.read(name) if name in arc else None
            finally:
                arc.close()
            if not data:
                raise RuntimeError("%s is not in anim.img" % name)
            files[name] = ifp_fmt.IfpFile.from_bytes(data)
        return files[name]
    return ui.load_ifp(src)


# ============================================================================= search
def _anim_rows(root):
    rows = []
    ped = os.path.join(root, "anim", "ped.ifp")
    if os.path.isfile(ped):
        from . import ui
        for a in ui.load_ifp(ped).animations:
            rows.append(("ped.ifp", a.name))
    path, _files = _anim_img(root)
    if path:
        arc = img_fmt.ImgArchive(path)
        try:
            for e in arc.order:
                if not e.name.lower().endswith(".ifp"):
                    continue
                try:
                    f = ifp_fmt.IfpFile.from_bytes(arc.read(e))
                except Exception as ex:       # noqa - one broken IFP must not stop the index
                    print("[GTA SA Toolkit] anim.img %s: %s" % (e.name, ex))
                    continue
                rows.extend(("anim.img/" + e.name, a.name) for a in f.animations)
        finally:
            arc.close()
    return rows


def _index(game, root):
    key = (id(game), root)
    if _INDEX["key"] == key:
        return _INDEX["rows"]
    rows = []                   # (kind, key, name, detail, id or -1, lower search text)
    for oid, od in game.objects.items():
        kind = kind_of(game, oid)
        name = display_name(game, oid)
        text = name.lower()
        if od.model.lower() != text:
            text += " " + od.model.lower()
        v = game.vehicles.get(oid)
        if v is not None and v.game_name:
            text += " " + v.game_name.lower()
        rows.append((kind, str(oid), name, str(oid), oid, text))
    for src, an in _anim_rows(root):
        rows.append(('ANIM', src + "|" + an, an, source_label(src), -1, an.lower()))
    from . import mods
    for m in mods.models():                     # custom / modded models, tagged "Mod"
        rows.append((m.kind, m.key, m.name, "Mod", -1, m.name.lower() + " mod"))
    _INDEX.update(key=key, rows=rows)
    return rows


def search(game, root, query, limit=MAX_RESULTS):
    """Items whose name or ID matches query, best first: exact ID/name, then IDs/names that start
    with it, then names that contain it. Returns [(kind, key, name, detail)]."""
    q = query.strip().lower()
    if not q:
        return []
    digits = q.isdigit()
    hits = []
    for kind, key, name, detail, oid, text in _index(game, root):
        sid = str(oid) if oid >= 0 else ""
        if digits and sid == q:
            score = 0
        elif text == q or name.lower() == q:
            score = 0
        elif digits and sid.startswith(q):
            score = 1
        elif text.startswith(q) or any(w.startswith(q) for w in text.split()):
            score = 2
        elif q in text:
            score = 3
        else:
            continue
        hits.append((score, KIND_ORDER[kind], oid if oid >= 0 else 1 << 30, name.lower(), (kind, key, name, detail)))
    hits.sort(key=lambda h: h[:4])
    return [h[4] for h in hits[:limit]]


# ============================================================================= importing
def _game(context):
    return mapimport.get_game(context.scene.gta_tk.game_root)


def _select_only(context, ob):
    for o in context.selected_objects:
        o.select_set(False)
    ob.select_set(True)
    context.view_layer.objects.active = ob


def place_vehicle(context, game, model, colours=None, colour_set=-1, hide_damage=True, hide_lod=True):
    """Vehicle at the 3D cursor (wheels, paint, textures). Returns (collection, root)."""
    coll, root, vdef = vehicles.import_vehicle(
        context, game, model, colours=colours, colour_set=colour_set, hide_damage=hide_damage,
        hide_lod=hide_lod, location=context.scene.cursor.location.copy())
    if root:
        _select_only(context, root)
    cols = [int(c) for c in root.get("gta_colours", [])] if root else []
    vid = vdef.id if vdef else -1
    name = vdef.name if vdef else model
    record('VEHICLE', vid if vid >= 0 else model, name, str(vid) if vid >= 0 else "",
           {"colours": cols, "hide_damage": bool(hide_damage), "hide_lod": bool(hide_lod)},
           note="colours %s" % ",".join(str(c) for c in cols) if cols else "")
    return coll, root


def place_model(context, game, name, pose=None, tag_id=False):
    """Any ped/object/toy DFF at the 3D cursor (vehicles: use place_vehicle). Peds stand up when
    pose is True (None = the 'Stand peds up' setting). Returns the collection."""
    from . import ui
    item = model_item(game, name)
    if item and str(name).strip().isdigit():
        name = game.objects[int(item[1])].model
    coll = mapimport.import_model_from_img(context, game, name, True, context.scene.gta_tk.pack_images)
    cur = context.scene.cursor.location
    for ob in list(coll.objects):
        if ob.parent is None:
            ob.location = ob.location + cur
            if tag_id and item:
                ob["gta_id"] = int(item[1])
    arm = next((o for o in coll.objects if o.type == 'ARMATURE'), None)
    if pose is None:
        pose = bool(getattr(context.scene, "gta_tk_x", None) and context.scene.gta_tk_x.ped_auto_pose)
    if arm and pose:
        ui.stand_up(context, arm, game)
    if arm:
        _select_only(context, arm)
    if item:
        kind, key, disp, detail = item
        record(kind, key, disp, detail, {"pose": bool(pose)} if kind == 'PED' else {})
    elif arm is not None:                       # a story character (no IDE entry): keyed by its name
        record('PED', str(name).lower(), str(name).lower(), "", {"pose": bool(pose)})
    return coll


def place_weapon(context, game, oid, arm=None, hand='RIGHT'):
    """Weapon at the 3D cursor, or in the ped's hand when arm is given. Returns the collection."""
    name = display_name(game, oid)
    if arm is None:
        coll = weapons.import_weapon(context, game, int(oid), context.scene.cursor.location.copy())
        record('WEAPON', oid, name, str(oid), {})
    else:
        coll, _b = weapons.give_weapon(context, game, arm, int(oid), hand)
        context.view_layer.objects.active = arm
        record('WEAPON', oid, name, str(oid), {"hand": hand},
               note="%s hand" % ("left" if hand == 'LEFT' else "right"))
    return coll


def attach_toy(context, game, arm, oid, bone, offset=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1), index=0):
    """Object on a ped's bone, SA-MP style. Returns (collection, bone)."""
    from . import samp
    coll, b = samp.attach_toy(context, game, arm, int(oid), int(bone), tuple(offset), tuple(rot), tuple(scale),
                              index=int(index))
    context.view_layer.objects.active = arm
    kind = kind_of(game, oid)
    record(kind, oid, display_name(game, oid), str(oid),
           {"bone": int(bone), "offset": [round(v, 5) for v in offset], "rot": [round(v, 4) for v in rot],
            "scale": [round(v, 4) for v in scale], "index": int(index)}, note="on bone %d" % int(bone))
    return coll, b


def anim_settings(p):
    return {k: getattr(p, k) for k in ANIM_SETTINGS}


def apply_anim(context, arm, src, name, settings=None):
    """Apply one IFP animation to arm. settings: ANIM_SETTINGS values (missing ones come from the
    IFP panel). Returns (action, missing bones, animation)."""
    from . import anim as anim_ops
    from . import ui
    p = context.scene.gta_tk
    opts = anim_settings(p)
    opts.update({k: v for k, v in (settings or {}).items() if k in ANIM_SETTINGS})
    ns = SimpleNamespace(**opts)
    f = load_anim_file(context, src)
    a = f.find(name)
    if a is None:
        raise RuntimeError("'%s' is not in %s any more" % (name, source_label(src)))
    opt = ui._apply_options(context, arm, ns)
    act, missing = anim_ops.apply_animation(arm, a, opt)
    sc = context.scene
    if ns.ifp_set_scene_fps and ns.ifp_fps_mode in ('30', '60'):
        sc.render.fps = int(opt.fps)
        sc.render.fps_base = 1.0
    if ns.ifp_adjust_range:
        fr = act.frame_range
        sc.frame_start = int(math.floor(fr[0]))
        sc.frame_end = max(int(math.ceil(fr[1])), sc.frame_start + 1)
    sc.frame_set(int(opt.start_frame))
    record('ANIM', src + "|" + name, name, source_label(src), opts)
    return act, missing, a


def import_item(context, kind, key, settings=None):
    """Import a favorite / recent / search result with the right importer. settings: what a recent
    import saved ({} = use the current panel settings). Returns a message for the status bar."""
    from . import ui
    settings = settings or {}
    x = context.scene.gta_tk_x
    if kind == 'ANIM':
        arm = ui.get_armature(context)
        if arm is None:
            raise RuntimeError("Select a ped to apply the animation to")
        src, _, name = key.rpartition("|")
        _act, missing, a = apply_anim(context, arm, src, name, settings)
        msg = "Applied '%s' to %s" % (a.name, arm.name)
        return msg + (" (bones not found: %s)" % ", ".join(missing[:8]) if missing else "")
    if str(key).startswith("mod|"):                 # a custom / modded model (mods.py)
        from . import mods
        coll, warnings = mods.import_mod(context, _game(context), key, pose=settings.get("pose"))
        return "Imported %s (custom model)%s" % (coll.name, "; " + warnings[0] if warnings else "")
    if kind == 'IFP':                           # a starred IFP file: list it in the shared browser
        f = ui.choose_source(context, key)
        return "Listing %s: %d animations" % (source_label(key), len(f.animations))
    game = _game(context)
    if not key.isdigit() and kind == 'VEHICLE':
        key = str(next((v.id for v in game.vehicles.values() if v.model.lower() == key.lower()), key))
    if kind == 'PED' and not key.isdigit():     # story character: a loose DFF, imported by name
        coll = place_model(context, game, key, pose=settings.get("pose"))
        return "Imported %s" % coll.name
    if not key.isdigit() or int(key) not in game.objects:
        raise RuntimeError("Model %s is not in the scanned game files" % key)
    oid = int(key)
    if kind == 'VEHICLE' or oid in game.vehicles:
        if "colours" in settings and settings["colours"]:
            cols, cset = settings["colours"], -1
            hd, hl = settings.get("hide_damage", True), settings.get("hide_lod", True)
        else:
            from . import ui_extra
            cols, cset = ui_extra._veh_colours(x, game, key)
            hd, hl = x.veh_hide_damage, x.veh_hide_lod
        coll, _root = place_vehicle(context, game, game.objects[oid].model, cols, cset, hd, hl)
        return "Imported %s" % coll.name
    arm = ui.get_armature(context)
    if kind == 'WEAPON':
        hand = settings.get("hand")
        if hand and arm is not None:
            place_weapon(context, game, oid, arm, hand)
            return "%s -> %s" % (display_name(game, oid), arm.name)
        place_weapon(context, game, oid)
        extra = " at the cursor (select a ped to put it in the hand)" if hand else ""
        return "Imported %s%s" % (display_name(game, oid), extra)
    if "bone" in settings:
        if arm is not None:
            _c, b = attach_toy(context, game, arm, oid, settings["bone"], settings.get("offset", (0, 0, 0)),
                               settings.get("rot", (0, 0, 0)), settings.get("scale", (1, 1, 1)),
                               settings.get("index", 0))
            return "Attached %s to %s" % (display_name(game, oid), b.name.strip())
        place_model(context, game, key, tag_id=True)
        return "Imported %s at the cursor (select a ped to attach it)" % display_name(game, oid)
    coll = place_model(context, game, key, pose=settings.get("pose"), tag_id=True)
    return "Imported %s" % coll.name


# ============================================================================= operators
def _settings_json(s):
    try:
        d = json.loads(s) if s else {}
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


class GTATK_OT_favorite(bpy.types.Operator):
    bl_idname = "gtatk.favorite"
    bl_label = "Favorite"
    bl_description = "Add to / remove from the Library's favorites"
    bl_options = {'INTERNAL'}

    kind: StringProperty()
    key: StringProperty()
    name: StringProperty()
    detail: StringProperty()

    def execute(self, context):
        try:
            on = set_favorite(self.kind, self.key, self.name, self.detail)
        except Exception as e:        # noqa
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, "%s %s favorites" % (self.name, "added to" if on else "removed from"))
        return {'FINISHED'}


class GTATK_OT_favorite_typed(bpy.types.Operator):
    bl_idname = "gtatk.favorite_typed"
    bl_label = "Favorite"
    bl_description = "Add the typed model to / remove it from the Library's favorites"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        try:
            item = model_item(_game(context), context.scene.gta_tk.model_name)
        except Exception as e:        # noqa
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        if item is None:
            self.report({'ERROR'}, "Type a model name or ID the game knows first")
            return {'CANCELLED'}
        on = set_favorite(*item)
        self.report({'INFO'}, "%s %s favorites" % (item[2], "added to" if on else "removed from"))
        return {'FINISHED'}


class GTATK_OT_library_import(bpy.types.Operator):
    bl_idname = "gtatk.library_import"
    bl_label = "Import"
    bl_description = "Import this item (at the 3D cursor; animations go on the selected ped)"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    kind: StringProperty()
    key: StringProperty()
    settings: StringProperty(description="JSON settings saved with a recent import")

    @classmethod
    def description(cls, context, props):
        if props.settings and props.settings != "{}":
            return "Import with the same settings as last time (at the 3D cursor)"
        if props.kind == 'ANIM':
            return "Apply to the selected ped with the IFP Animation settings"
        return "Import at the 3D cursor with the current panel settings"

    @events.batched
    def execute(self, context):
        try:
            msg = import_item(context, self.kind, self.key, _settings_json(self.settings))
        except Exception as e:        # noqa
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class GTATK_OT_library_clear_recent(bpy.types.Operator):
    bl_idname = "gtatk.library_clear_recent"
    bl_label = "Clear Recent"
    bl_description = "Forget the list of recent imports (favorites stay)"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        clear_recent()
        return {'FINISHED'}


class GTATK_OT_library_search_clear(bpy.types.Operator):
    bl_idname = "gtatk.library_search_clear"
    bl_label = "Clear Search"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        st = context.scene.gta_tk_lib
        st.query = ""           # the update clears the results
        st.show_more = False
        return {'FINISHED'}


# ============================================================================= asset cache
class GTATK_OT_cache_toggle(bpy.types.Operator):
    bl_idname = "gtatk.cache_toggle"
    bl_label = "Asset Cache"
    bl_description = ("Turn the asset cache on or off. When on, built models and textures are saved in the "
                      "cache folder and reused, so importing the same things again is faster")
    bl_options = {'INTERNAL'}

    directory: StringProperty(subtype='DIR_PATH', options={'SKIP_SAVE'})

    def invoke(self, context, event):
        pr = prefs()
        if pr is not None and not pr.cache_enabled and not pr.cache_dir.strip():
            context.window_manager.fileselect_add(self)      # first time on: choose the folder
            return {'RUNNING_MODAL'}
        return self.execute(context)

    def execute(self, context):
        from . import cache
        pr = prefs()
        if pr is None:
            return {'CANCELLED'}
        if self.directory:
            if not os.path.isdir(bpy.path.abspath(self.directory)):
                self.report({'ERROR'}, "Folder not found: %s" % self.directory)
                return {'CANCELLED'}
            pr.cache_dir = self.directory
            pr.cache_enabled = True
        elif not pr.cache_enabled and not pr.cache_dir.strip():
            self.report({'ERROR'}, "Choose a cache folder first")
            return {'CANCELLED'}
        else:
            pr.cache_enabled = not pr.cache_enabled
        if pr.cache_enabled:
            self.report({'INFO'}, "Asset cache on: %s" % cache.root())
        else:
            self.report({'INFO'}, "Asset cache off (its files are kept; Clear Cache deletes them)")
        return {'FINISHED'}


class GTATK_OT_cache_clear(bpy.types.Operator):
    bl_idname = "gtatk.cache_clear"
    bl_label = "Clear Cache"
    bl_description = ("Delete every cached model, texture and thumbnail. Your .blend files are not affected "
                      "(they keep their own copies)")
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        from . import cache
        before = cache.size_text()
        cache.clear()
        self.report({'INFO'}, "Asset cache cleared (%s freed)" % before)
        return {'FINISHED'}


def draw_cache(layout):
    """Library tab: on/off, folder, size, Clear. Shows stored values only (the size is refreshed after
    imports, Clear and folder changes)."""
    from . import cache
    pr = prefs()
    if pr is None:
        return
    on = pr.cache_enabled
    r = layout.row(align=True)
    r.operator("gtatk.cache_toggle", text="On" if on else "Off",
               icon='CHECKBOX_HLT' if on else 'CHECKBOX_DEHLT', depress=on)
    r.prop(pr, "cache_dir", text="")
    if on and not pr.cache_dir.strip():
        layout.label(text="Choose a folder to start caching", icon='ERROR')
    r = layout.row(align=True)
    r.label(text="Size: %s" % cache.size_text() if pr.cache_dir.strip() else "No folder yet", icon='DISK_DRIVE')
    sub = r.row(align=True)
    sub.enabled = cache._SIZE["bytes"] > 0
    sub.operator("gtatk.cache_clear", icon='TRASH')
    if not on:
        layout.label(text="Off: every import builds models from the game files", icon='INFO')


# ============================================================================= search state (per scene)
def run_search(state, context):
    state.results.clear()
    state.results_index = 0
    state.status = ""
    q = state.query.strip()
    if not q:
        return 0
    try:
        game = _game(context)
        res = search(game, _root(context), q)
    except Exception as e:            # noqa
        state.status = str(e)
        return 0
    for kind, key, name, detail in res:
        it = state.results.add()
        it.kind, it.key, it.name, it.detail = kind, key, name, detail
    state.status = "%d results%s" % (len(res), " (first %d)" % MAX_RESULTS if len(res) >= MAX_RESULTS else "") \
        if res else "Nothing matches '%s'" % q
    return len(res)


def _upd_query(self, context):
    run_search(self, context)


class GTATK_LibResult(bpy.types.PropertyGroup):
    kind: StringProperty()
    key: StringProperty()
    detail: StringProperty()


class GTATK_LibState(bpy.types.PropertyGroup):
    query: StringProperty(name="Search", update=_upd_query, options={'SKIP_SAVE'},
                          description="Name or ID of any vehicle, object, toy, weapon, ped or animation "
                                      "(e.g. infernus, 411, 18645, WALK_civi). Press Enter to search")
    results: CollectionProperty(type=GTATK_LibResult)
    results_index: IntProperty()
    status: StringProperty()
    show_all_recent: BoolProperty(name="Show all", default=False)
    show_more: BoolProperty(name="Show more", default=False, options={'SKIP_SAVE'},
                            description="Show more search results (the list stays short so the tab below "
                                        "isn't pushed down)")


class GTATK_UL_lib_results(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        draw_item_row(layout, item.kind, item.key, item.name, item.detail, show_type=True)


def _shown_name(kind, key, name):
    """How a Library row names its item: peds in lower case like every other model (peds.ide has them
    upper case), vehicles by their in-game name even when stored by older versions. Draw-safe."""
    if kind == 'PED':
        return name.lower()
    if kind == 'VEHICLE' and key.isdigit():
        g = loaded_game()
        v = g.vehicles.get(int(key)) if g is not None else None
        if v is not None:
            return v.name
    return name


def draw_item_row(layout, kind, key, name, detail, show_type=False, settings="", star=True, note="", big=False):
    """One Library row. With the asset cache on, a cached model shows its thumbnail: bigger in the
    Favorites / Recent rows (big=True), in place of the type icon in lists (fixed row height)."""
    from . import thumbs
    iid = thumbs.item_icon(kind, key)
    shown = _shown_name(kind, key, name)
    r = layout.row(align=True)
    if iid and big:
        r.template_icon(icon_value=iid, scale=2.0)
        r.label(text=shown)
    elif iid:
        r.label(text=shown, icon_value=iid)
    else:
        r.label(text=shown, icon=KIND_ICON.get(kind, 'QUESTION'))
    sub = r.row(align=True)
    sub.alignment = 'RIGHT'
    txt = note or ("ID " + detail if detail.isdigit() else detail)
    if show_type:
        txt = "%s  %s" % (txt, KIND_LABEL.get(kind, kind)) if txt else KIND_LABEL.get(kind, kind)
    sub.label(text=txt)
    op = r.operator("gtatk.library_import", text="", icon='IMPORT', emboss=True)
    op.kind, op.key, op.settings = kind, key, settings
    if star:
        draw_star(r, kind, key, name, detail)


classes = (
    GTATK_LibItem, GTATK_ModFolder, GTATK_TimecycFile, GTATK_Preferences, GTATK_LibResult, GTATK_LibState,
    GTATK_OT_favorite, GTATK_OT_favorite_typed, GTATK_OT_library_import, GTATK_OT_library_clear_recent,
    GTATK_OT_library_search_clear, GTATK_UL_lib_results, GTATK_OT_cache_toggle, GTATK_OT_cache_clear,
)


def fill_favorite_settings():
    """Favorites starred before they remembered settings take them from Recent. Returns how many."""
    pr = prefs()
    if pr is None:
        return 0
    recent = {(r.kind, r.key): r for r in pr.recents}
    n = 0
    for fav in pr.favorites:
        rec = recent.get((fav.kind, fav.key))
        if not fav.settings and rec is not None and rec.settings:
            fav.settings, fav.note = rec.settings, rec.note
            n += 1
    if n:
        _changed()
    return n


def _restore_later():
    try:
        restore_backup()
        fill_favorite_settings()
    except Exception as e:            # noqa
        print("[GTA SA Toolkit] library restore:", e)
    try:
        from . import cache
        cache.refresh_size()          # one walk of the cache folder at start-up
        cache.index()                 # read index.json now, not in the first draw
    except Exception as e:            # noqa
        print("[GTA SA Toolkit] cache size:", e)
    return None                       # one shot


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.gta_tk_lib = PointerProperty(type=GTATK_LibState)
    _invalidate()
    if prefs() is not None:
        _restore_later()
    else:                             # first enable: the preferences entry appears after register()
        bpy.app.timers.register(_restore_later, first_interval=0.1)


def unregister():
    if bpy.app.timers.is_registered(_restore_later):
        bpy.app.timers.unregister(_restore_later)
    del bpy.types.Scene.gta_tk_lib
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    _invalidate()
    _INDEX.update(key=None, rows=[])
    _ANIM_IMG.update(key=None, files={})
    _SRC.clear()
