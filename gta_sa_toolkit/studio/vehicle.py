# GTA SA Studio - the Vehicle tab's work on an imported vehicle (the selected one).
#   setup()      once per vehicle (after import, or the Set Up Vehicle button): stores which paint
#                slots, lights, doors, extras, paintjobs and upgrade slots it has on its root
#                object (Object.gta_vehicle_studio), so the tab only draws stored values.
#   apply_*()    called by the settings' update callbacks; they touch only that vehicle.
# Game data (palette, colour sets, paintjob textures, upgrade models) comes from core's api.
# Enum lists with swatches / previews are built once per session (lazily through a one-shot timer
# when first drawn) and cached here; Blender only borrows the strings, so the lists must stay alive.
import math

import bpy
import bpy.utils.previews
from mathutils import Matrix, Vector

from . import corelink

_MUTE = {"on": False}              # set while setup() fills in settings (no apply per property)
_C = {"palette": None, "pal_rgb": [], "sets": {}, "pj": {}, "upg": {}, "data": {}}
_PC = {"pc": None}
_QUEUE = {"items": set(), "timer": False}

PAINT_LABELS = ("Primary", "Secondary", "Third", "Fourth")
DOORS = (("door_lf_dummy", "Front Left Door"), ("door_rf_dummy", "Front Right Door"),
         ("door_lr_dummy", "Rear Left Door"), ("door_rr_dummy", "Rear Right Door"))
DOOR_ANGLE, BONNET_ANGLE, BOOT_ANGLE, TAILGATE_ANGLE = math.radians(70), math.radians(55), \
    math.radians(70), math.radians(85)
# where each kind of upgrade goes: frame name on the car, and the stock part it replaces (hidden)
UPGRADE_SPOTS = {
    'SPOILER': ("ug_spoiler", None), 'ROOF': ("ug_roof", None), 'NITRO': ("ug_nitro", None),
    'WING_L': ("ug_wing_left", None), 'WING_R': ("ug_wing_right", None),
    'BONNET': ("ug_bonnet", None), 'BONNET_L': ("ug_bonnet_left", None), 'BONNET_R': ("ug_bonnet_right", None),
    'LIGHTS': ("ug_lights", None), 'FRONT_BULLBAR': ("ug_frontbullbar", None),
    'BACK_BULLBAR': ("ug_backbullbar", None), 'EXHAUST': ("exhaust_ok", "exhaust_ok"),
    'FRONT_BUMPER': ("bump_front_dummy", "bump_front_ok"), 'REAR_BUMPER': ("bump_rear_dummy", "bump_rear_ok"),
}
# no visible part in the game, and the right-hand halves go on with their left partner
SKIP_KINDS = ('HYDRAULICS', 'STEREO', 'WING_R', 'BONNET_R')


def _is_collision(ob):
    """Collision / shadow object: the add-on tags it gta_col; files made with DragonFF have dff.type
    (read safely: DragonFF may not be installed any more)."""
    if ob.get("gta_col") is not None:
        return True
    try:
        return ob.dff.type in ('COL', 'SHA')
    except AttributeError:
        return False


def base_name(ob):
    n = ob.name
    return n.rsplit(".", 1)[0] if len(n) > 4 and n[-4] == "." and n[-3:].isdigit() else n


def find_root(ob):
    """The imported vehicle an object belongs to (its root has gta_vehicle), or None. Cheap."""
    for _ in range(12):
        if ob is None:
            return None
        if ob.get("gta_vehicle"):
            return ob
        ob = ob.parent
    return None


def vehicle_objects(root):
    return [root] + list(root.children_recursive)


def _hide(ob, state):
    ob.hide_viewport = state
    ob.hide_render = state


def _materials(objs):
    seen, out = set(), []
    for o in objs:
        for sl in getattr(o, "material_slots", ()):
            m = sl.material
            if m is not None and m.name not in seen:
                seen.add(m.name)
                out.append(m)
    return out


# ----------------------------------------------------------------------------- previews / lists
def _pc():
    if _PC["pc"] is None:
        _PC["pc"] = bpy.utils.previews.new()
    return _PC["pc"]


def _swatch(key, stripes, size=32):
    """A preview icon of vertical colour stripes (rgb 0-1). Returns its icon id."""
    pc = _pc()
    pv = pc.get(key) or pc.new(key)
    n = max(1, len(stripes))
    row = []
    for x in range(size):
        r, g, b = stripes[min(n - 1, x * n // size)]
        row += (r, g, b, 1.0)
    px = row * size
    pv.image_size = (size, size)
    pv.image_pixels_float = px
    pv.icon_size = (size, size)
    pv.icon_pixels_float = px
    return pv.icon_id


def _image_preview(key, img, size=96):
    """A preview icon from a bpy image (nearest-pixel downscale). Returns its icon id."""
    pc = _pc()
    pv = pc.get(key) or pc.new(key)
    w, h = img.size
    src = img.pixels[:]
    px = []
    for y in range(size):
        sy = min(h - 1, y * h // size)
        for x in range(size):
            i = (sy * w + min(w - 1, x * w // size)) * 4
            px += (src[i], src[i + 1], src[i + 2], 1.0)
    pv.image_size = (size, size)
    pv.image_pixels_float = px
    pv.icon_size = (size, size)
    pv.icon_pixels_float = px
    return pv.icon_id


def _game_ready():
    api = corelink.api()
    if api is None:
        return None
    try:
        return api.get_game()
    except Exception:              # noqa - no game folder set
        return None


def build_palette():
    game = _game_ready()
    if game is None:
        return False
    rgb = [tuple(c / 255.0 for c in col) for col in game.car_colours] or [(0.8, 0.8, 0.8)]
    items = []
    for i, c in enumerate(rgb):
        items.append((str(i), "%d" % i, "carcols.dat colour %d" % i, _swatch("pal%d" % i, [c]), i))
    _C["pal_rgb"], _C["palette"] = rgb, items
    return True


def vehicle_data(model):
    key = model.lower()
    if key not in _C["data"]:
        _C["data"][key] = corelink.api().vehicle_data(model)
    return _C["data"][key]


def build_sets(model):
    if _C["palette"] is None and not build_palette():
        return False
    pal = _C["pal_rgb"]
    items = []
    for i, cs in enumerate(vehicle_data(model)["colour_sets"]):
        stripes = [pal[c] if 0 <= c < len(pal) else (0.8, 0.8, 0.8) for c in cs]
        items.append((str(i), "Set %d" % (i + 1), "Colours " + ", ".join(str(c) for c in cs),
                      _swatch("set:%s:%d" % (model.lower(), i), stripes), i))
    _C["sets"][model.lower()] = items or [('0', "No sets", "carcols.dat has no colour sets for this model", 'NONE', 0)]
    return True


def build_paintjobs(model):
    api = corelink.api()
    n = vehicle_data(model)["paintjobs"]
    items = [('0', "Plain Paint", "The body colours, no paintjob", 'BRUSH_DATA', 0)]
    for i in range(1, n + 1):
        img = api.paintjob_image(model, i)
        icon = _image_preview("pj:%s:%d" % (model.lower(), i), img) if img is not None else 'IMAGE_DATA'
        items.append((str(i), "Paintjob %d" % i, "%s%d.txd" % (model.lower(), i), icon, i))
    _C["pj"][model.lower()] = items
    return True


def _queue(job):
    """Build a list after this redraw (draw code must not load game data)."""
    _QUEUE["items"].add(job)
    if not _QUEUE["timer"]:
        _QUEUE["timer"] = True
        bpy.app.timers.register(_run_queue, first_interval=0.01)


def _run_queue():
    jobs, _QUEUE["items"] = list(_QUEUE["items"]), set()
    _QUEUE["timer"] = False
    for kind, model in jobs:
        try:
            if kind == "palette":
                build_palette()
            elif kind == "sets":
                build_sets(model)
            elif kind == "pj":
                build_paintjobs(model)
        except Exception as e:     # noqa
            print("[GTA SA Toolkit] vehicle lists:", e)
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
    return None


_LOADING = [('0', "Loading...", "", 'TIME', 0)]


def palette_items():
    if _C["palette"] is None:
        _queue(("palette", ""))
        return _LOADING
    return _C["palette"]


def colour_set_items(root):
    key = root.gta_vehicle_studio.model.lower()
    items = _C["sets"].get(key)
    if items is None:
        _queue(("sets", key))
        return _LOADING
    return items


def paintjob_items(root):
    key = root.gta_vehicle_studio.model.lower()
    items = _C["pj"].get(key)
    if items is None:
        _queue(("pj", key))
        return _LOADING
    return items


def upgrade_items(root, kind):
    key = (root.gta_vehicle_studio.model.lower(), kind)
    items = _C["upg"].get(key)
    if items is None:
        items = [('NONE', "Stock", "No upgrade", 'NONE', 0)]
        names = _C["data"].get(key[0], {}).get("upgrades", {}).get(kind, [])
        for i, n in enumerate(names):
            items.append((n, n, "%s.dff (carmods.dat)" % n, 'NONE', i + 1))
        if key[0] in _C["data"]:            # only cache once the model's data is known
            _C["upg"][key] = items
    return items


def free():
    if _PC["pc"] is not None:
        bpy.utils.previews.remove(_PC["pc"])
        _PC["pc"] = None
    _C.update(palette=None, pal_rgb=[], sets={}, pj={}, upg={}, data={})


# ----------------------------------------------------------------------------- setup
def _local(ob):
    """ob's matrix in its parent's space (no depsgraph needed)."""
    return ob.matrix_parent_inverse @ ob.matrix_basis


def _mesh_centre(ob):
    bb = [Vector(c) for c in ob.bound_box]
    return sum(bb, Vector()) / 8.0


def _hinge(dummy, child, axis, angle, rule):
    """Signed opening angle: the direction that moves the part's centre farther from the car's
    centre ('OUT', doors and tailgates) or higher ('UP', bonnet and boot)."""
    c = _local(child) @ _mesh_centre(child)                 # part centre in the hinge's space
    pos = _local(dummy)
    best, best_val = angle, None
    for a in (angle, -angle):
        p = pos @ (Matrix.Rotation(a, 4, "XYZ"[axis]) @ c)
        val = p.z if rule == 'UP' else Vector((p.x, p.y)).length
        if best_val is None or val > best_val:
            best, best_val = a, val
    return best


def _visible_child(dummy, children, suffix="_ok"):
    for c in children.get(dummy.name, ()):
        if c.type == 'MESH' and base_name(c).lower().endswith(suffix) and not c.hide_render:
            return c
    return None


def _make_own(objs):
    """A vehicle copied with Shift+D shares meshes and materials with the original: give it its
    own, so painting or opening one doesn't change the other. A fresh import owns everything."""
    by_mesh = {}
    for o in objs:
        if o.type == 'MESH' and o.data is not None:
            by_mesh.setdefault(o.data, []).append(o)
    for me, users in list(by_mesh.items()):
        if me.users > len(users):
            cp = me.copy()
            for o in users:
                o.data = cp
            by_mesh[cp] = by_mesh.pop(me)
    uses = {}
    for me in by_mesh:
        for m in me.materials:
            if m is not None:
                uses[m] = uses.get(m, 0) + 1
    for m, n in uses.items():
        if m.users > n:
            cp = m.copy()
            for me in by_mesh:
                for i, mm in enumerate(me.materials):
                    if mm == m:
                        me.materials[i] = cp


def is_current(root):
    """setup() ran on this very object (not on the vehicle it was copied from, or before a rename)."""
    vs = root.gta_vehicle_studio
    return vs.ready and vs.owner == root.name and vs.model.lower() == str(root["gta_vehicle"]).lower()


def setup(root, objs=None):
    """Store what this vehicle has (see the top of the file). objs: its objects when known (an
    import hands them over), else they are collected from the root."""
    api = corelink.api()
    vs = root.gta_vehicle_studio
    objs = vehicle_objects(root) if objs is None else [o for o in objs if o is root or find_root(o) is root]
    model = root["gta_vehicle"]
    data = vehicle_data(model)
    if _C["palette"] is None:
        build_palette()
    build_sets(model)
    names, children = {}, {}
    for o in objs:
        names.setdefault(base_name(o).lower(), o)
        if o.parent is not None:
            children.setdefault(o.parent.name, []).append(o)
    _make_own(objs)
    mats = _materials(objs)
    keep_parts = {p.label: (p.open, p.shown) for p in vs.parts}      # a second setup keeps the state
    keep_upg = {u.kind: u.choice for u in vs.upgrades}
    _MUTE["on"] = True
    try:
        vs.model = model
        vs.owner = root.name
        slots = 0
        for m in mats:
            if "gta_paint_slot" in m:
                slots |= 1 << int(m["gta_paint_slot"])
        vs.slots = slots
        cols = list(root.get("gta_colours", [1, 1]))
        n = len(_C["pal_rgb"])
        for i in range(4):
            c = int(cols[i]) if i < len(cols) else 0
            setattr(vs, "col%d" % (i + 1), str(c if 0 <= c < n else 0))
        kinds = {m.get("gta_vehicle_light") for m in mats}
        vs.has_head, vs.has_tail = "head" in kinds, "tail" in kinds
        vs.paintjob_count = data["paintjobs"]
        # doors, bonnet, boot (only parts with a visible mesh on their hinge), extras
        vs.parts.clear()
        flags = data["model_flags"]
        hinges = list(DOORS) + [("bonnet_dummy", "Bonnet")]
        if not flags & api.FLAG_NOSWING_BOOT:
            hinges.append(("boot_dummy", "Tailgate" if flags & api.FLAG_TAILGATE_BOOT else "Boot"))
        for dname, label in hinges:
            d = names.get(dname)
            part = _visible_child(d, children) if d is not None else None
            if part is None:
                continue
            if dname.startswith("door"):
                kind, axis, ang = 'DOOR', 2, _hinge(d, part, 2, DOOR_ANGLE, 'OUT')
            elif dname == "bonnet_dummy":
                kind, axis, ang = 'BONNET', 0, _hinge(d, part, 0, BONNET_ANGLE, 'UP')
            elif flags & api.FLAG_TAILGATE_BOOT:
                kind, axis, ang = 'BOOT', 0, _hinge(d, part, 0, TAILGATE_ANGLE, 'OUT')
            else:
                kind, axis, ang = 'BOOT', 0, _hinge(d, part, 0, BOOT_ANGLE, 'UP')
            if "gta_rest" not in d:
                d["gta_rest"] = [v for row in d.matrix_basis for v in row]
            it = vs.parts.add()
            it.kind, it.label, it.obj, it.axis, it.max_angle = kind, label, d, axis, ang
            it.open = keep_parts.get(label, (0.0, True))[0]
        for i in range(1, 7):
            e = names.get("extra%d" % i)
            if e is not None:
                it = vs.parts.add()
                it.kind, it.label, it.obj, it.shown = 'EXTRA', "Extra %d" % i, e, not e.hide_viewport
        # upgrade slots, in the game's order
        vs.upgrades.clear()
        for kind, label, _prefix in api.UPGRADE_KINDS:
            if kind in data["upgrades"] and kind not in SKIP_KINDS:
                it = vs.upgrades.add()
                it.kind = kind
                it.label = {"WING_L": "Side Skirts", "BONNET_L": "Bonnet Vents"}.get(kind, label)
                if keep_upg.get(kind, "NONE") in data["upgrades"][kind]:
                    it.choice = keep_upg[kind]
        vs.ready = True
    finally:
        _MUTE["on"] = False
    if vs.own_lights:                  # a copy's light materials still pointed at the original
        apply_lights(root, bpy.context.scene)
    return vs


def on_import(objects):
    """core's on_import_done (through lighting._on_import): set up the vehicles this import made."""
    roots = [o for o in objects if o.get("gta_vehicle") and o.parent is None]
    if not roots or corelink.api() is None:
        return
    groups = {r: [] for r in roots}          # one pass: SA-MP map code can bring hundreds of cars
    for o in objects:
        r = find_root(o)
        if r in groups:
            groups[r].append(o)
    for r in roots:
        try:
            setup(r, groups[r])
        except Exception as e:     # noqa - never break an import over the Vehicle tab
            print("[GTA SA Toolkit] vehicle %s: %s" % (r.name, e))


# ----------------------------------------------------------------------------- paint
def current_colours(root):
    """The four paint colours (rgb 0-1) and the carcols indices (-1 = custom) of a vehicle."""
    vs = root.gta_vehicle_studio
    pal = _C["pal_rgb"]
    rgb, idx = [], []
    for i in range(4):
        if getattr(vs, "custom%d" % (i + 1)):
            rgb.append(tuple(getattr(vs, "custom_rgb%d" % (i + 1))))
            idx.append(-1)
        else:
            try:
                c = int(getattr(vs, "col%d" % (i + 1)))
            except ValueError:
                c = 0
            rgb.append(pal[c] if 0 <= c < len(pal) else (0.8, 0.8, 0.8))
            idx.append(c)
    return rgb, idx


def apply_paint(root, objs=None):
    if _MUTE["on"]:
        return
    api = corelink.api()
    if api is None:
        return
    if _C["palette"] is None:
        build_palette()
    rgb, idx = current_colours(root)
    pj = root.gta_vehicle_studio.paintjob not in ("", "0")
    for m in _materials(vehicle_objects(root) if objs is None else objs):
        if "gta_paint_slot" not in m:
            continue
        slot = int(m["gta_paint_slot"])
        # a paintjob texture carries its own colours: the game doesn't tint it
        api.paint_vehicle_material(m, (1.0, 1.0, 1.0) if (pj and "gta_stock_image" in m) else rgb[slot])
    root["gta_colours"] = idx[:4 if root.gta_vehicle_studio.slots > 0b11 else 2]


def upd_colour_set(self, context):
    if _MUTE["on"]:
        return
    root = self.id_data
    try:
        cs = vehicle_data(self.model)["colour_sets"][int(self.colour_set)]
    except (ValueError, IndexError):
        return
    _MUTE["on"] = True
    try:
        for i, c in enumerate(cs[:4]):
            setattr(self, "col%d" % (i + 1), str(c))
            setattr(self, "custom%d" % (i + 1), False)
    finally:
        _MUTE["on"] = False
    apply_paint(root)


def start_custom(root, i):
    """Custom colour switched on: start from the palette colour it had."""
    vs = root.gta_vehicle_studio
    if _MUTE["on"]:
        return
    if getattr(vs, "custom%d" % (i + 1)):
        try:
            c = int(getattr(vs, "col%d" % (i + 1)))
        except ValueError:
            c = 0
        pal = _C["pal_rgb"]
        _MUTE["on"] = True
        try:
            if 0 <= c < len(pal):
                setattr(vs, "custom_rgb%d" % (i + 1), pal[c])
        finally:
            _MUTE["on"] = False
    apply_paint(root)


# ----------------------------------------------------------------------------- lights
def light_strength(vs, kind, hour):
    from .lighting import night_factor
    on = vs.head_on if kind == "head" else vs.tail_on
    s = vs.head_strength if kind == "head" else vs.tail_strength
    return (s * 4.0 * (night_factor(hour) if vs.follow_time else 1.0)) if on else 0.0


def light_material(m, root, scene):
    """Set one light material from its vehicle's own settings."""
    vs = root.gta_vehicle_studio
    kind = m.get("gta_vehicle_light")
    tint = vs.head_tint if kind == "head" else vs.tail_tint
    corelink.api().set_vehicle_light(m, tuple(tint), light_strength(vs, kind, scene.gta_studio.hour))


def apply_lights(root, scene):
    if _MUTE["on"] or corelink.api() is None:
        return
    vs = root.gta_vehicle_studio
    mats = [m for m in _materials(vehicle_objects(root)) if m.get("gta_vehicle_light")]
    for m in mats:
        if vs.own_lights:
            m["gta_own_lights"] = True
            m["gta_vehicle_root"] = root
            light_material(m, root, scene)
        else:
            for k in ("gta_own_lights", "gta_vehicle_root"):
                if k in m:
                    del m[k]
    if not vs.own_lights and mats:
        from . import lighting
        lighting.apply(scene, lights=(), materials=mats)


# ----------------------------------------------------------------------------- paintjobs
def _is_remap(img):
    return img is not None and img.name.rsplit("/", 1)[-1].lower().startswith("remap")


def apply_paintjob(root):
    if _MUTE["on"]:
        return
    api = corelink.api()
    vs = root.gta_vehicle_studio
    try:
        n = int(vs.paintjob)
    except ValueError:
        return
    img = api.paintjob_image(vs.model, n) if n > 0 else None
    if n > 0 and img is None:
        return
    for m in _materials(vehicle_objects(root)):
        if not m.use_nodes:
            continue
        for node in m.node_tree.nodes:
            if node.type != 'TEX_IMAGE':
                continue
            if n > 0:
                if "gta_stock_image" not in m and _is_remap(node.image):
                    m["gta_stock_image"] = node.image.name
                if m.get("gta_stock_image") == getattr(node.image, "name", None) or \
                        (node.image is not None and node.image.get("gta_paintjob")):
                    node.image = img
                    img["gta_paintjob"] = True
            elif "gta_stock_image" in m and node.image is not None and node.image.get("gta_paintjob"):
                stock = bpy.data.images.get(m["gta_stock_image"])
                if stock is not None:
                    node.image = stock
        if n == 0 and "gta_stock_image" in m:
            del m["gta_stock_image"]
    apply_paint(root)


# ----------------------------------------------------------------------------- doors, extras
def apply_part(root, part):
    if _MUTE["on"] or part.obj is None:
        return
    ob = part.obj
    if part.kind == 'EXTRA':
        _hide(ob, not part.shown)
        return
    rest = ob.get("gta_rest")
    if rest is None:
        return
    m = Matrix([rest[0:4], rest[4:8], rest[8:12], rest[12:16]])
    ob.matrix_basis = m @ Matrix.Rotation(part.open * part.max_angle, 4, "XYZ"[part.axis])


# ----------------------------------------------------------------------------- upgrades
def upgrade_kind(name):
    n = name.lower()
    for kind, _label, prefix in corelink.api().UPGRADE_KINDS:
        if n.startswith(prefix):
            return kind
    return 'MISC'


def remove_upgrade(root, kind):
    """Take off the parts of one upgrade slot and bring back what they replaced."""
    objs = vehicle_objects(root)
    for o in objs:
        if o.get("gta_upgrade_slot") == kind:
            mesh = o.data if o.type == 'MESH' else None
            bpy.data.objects.remove(o)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
    for o in objs:
        try:
            if o.get("gta_hidden_by") == kind:
                _hide(o, False)
                del o["gta_hidden_by"]
            if kind == 'WHEELS' and "gta_stock_mesh" in o:
                stock = bpy.data.meshes.get(o["gta_stock_mesh"])
                old = o.data
                if stock is not None:
                    o.data = stock
                del o["gta_stock_mesh"]
                if old is not None and old.users == 0:
                    bpy.data.meshes.remove(old)
        except ReferenceError:             # removed above
            continue


def _take_in(root, coll, objs):
    """Move a freshly built part's objects into the vehicle's collection; drop its collision."""
    target = root.users_collection[0] if root.users_collection else bpy.context.scene.collection
    keep = []
    for child in list(coll.children_recursive):
        for o in list(child.objects):
            bpy.data.objects.remove(o)
        bpy.data.collections.remove(child)
    for o in objs:
        try:
            if o.type == 'MESH' and _is_collision(o):
                bpy.data.objects.remove(o)
                continue
        except ReferenceError:
            continue
        target.objects.link(o)
        coll.objects.unlink(o)
        keep.append(o)
    bpy.data.collections.remove(coll)
    return keep


def _stock_wheels(objs):
    return [o for o in objs if o.type == 'MESH' and base_name(o).lower().startswith("wheel")
            and not base_name(o).lower().endswith(("_dummy", "_vlo", "_lod")) and not o.hide_render
            and o.parent is not None and base_name(o.parent).lower().startswith("wheel_")]


def _size(mesh):
    zs = [v.co.z for v in mesh.vertices] or [0.0]
    ys = [v.co.y for v in mesh.vertices] or [0.0]
    return max(max(zs) - min(zs), max(ys) - min(ys))


def _put_wheels(root, name, objs):
    api = corelink.api()
    coll, new = api.import_vehicle_part(name)
    src = next((o for o in new if o.type == 'MESH' and not _is_collision(o)), None)
    if src is None:
        raise RuntimeError("%s has no wheel mesh" % name)
    mesh = src.data
    stock = _stock_wheels(objs)
    if stock:
        first = stock[0].get("gta_stock_mesh")
        ref = bpy.data.meshes.get(first) if first else stock[0].data
        s_new, s_old = _size(mesh), _size(ref)
        if s_new > 1e-6 and s_old > 1e-6:
            mesh.transform(Matrix.Scale(s_old / s_new, 4))
    mesh["gta_upgrade"] = name
    for o in stock:
        if "gta_stock_mesh" not in o:
            o["gta_stock_mesh"] = o.data.name
        o.data = mesh
    api.paint_vehicle_objects(stock[:1], current_colours(root)[0])
    for o in list(coll.all_objects):
        bpy.data.objects.remove(o)
    for child in list(coll.children_recursive):
        bpy.data.collections.remove(child)
    bpy.data.collections.remove(coll)
    return len(stock)


def put_upgrade(root, slot_kind, name, objs):
    """Build one part and put it on its spot. Returns the objects added."""
    api = corelink.api()
    kind = upgrade_kind(name)
    if kind == 'MISC':
        letter = name.lower().split("_")[1] if name.count("_") >= 2 else "a"
        spot, hides = "misc_" + letter, None
    else:
        spot, hides = UPGRADE_SPOTS.get(kind, (None, None))
    names = {}
    for o in objs:
        names.setdefault(base_name(o).lower(), o)
    target = names.get(spot) if spot else None
    coll, new = api.import_vehicle_part(name)
    added = _take_in(root, coll, new)
    # like the game, only the part's geometry is used: it goes on the frame of the stock part it
    # replaces (bumpers, exhaust) or on the car's ug_* spot; the part file's own frames are dropped
    # (bumper files have an offset group frame that would put them a metre off)
    stock = names.get(hides) if hides else None
    if stock is not None:
        parent, local = stock.parent, _local(stock)
    else:
        parent, local = (target or root), Matrix.Identity(4)
    # bumpers, spoilers and bonnet vents also carry a "<name>_dam" copy that the game only shows
    # once the part is damaged: dropped, like the frames
    meshes = [o for o in added if o.type == 'MESH' and not base_name(o).lower().endswith("_dam")]
    for o in meshes:
        o.parent = parent
        o.matrix_parent_inverse = Matrix.Identity(4)
        o.matrix_basis = local.copy()
    for o in added:
        if o not in meshes:
            bpy.data.objects.remove(o)
    added = meshes
    for o in added:
        o["gta_upgrade"] = name
        o["gta_upgrade_slot"] = slot_kind
    if stock is not None and not stock.hide_render:
        _hide(stock, True)
        stock["gta_hidden_by"] = slot_kind
    api.paint_vehicle_objects(added, current_colours(root)[0])
    return added


def apply_upgrade(root, slot):
    if _MUTE["on"] or corelink.api() is None:
        return
    remove_upgrade(root, slot.kind)
    name = slot.choice
    if name in ("", "NONE"):
        return
    objs = vehicle_objects(root)
    if slot.kind == 'WHEELS':
        _put_wheels(root, name, objs)
        return
    put_upgrade(root, slot.kind, name, objs)
    partner = vehicle_data(root.gta_vehicle_studio.model)["links"].get(name)
    if partner:
        put_upgrade(root, slot.kind, partner, objs)
    apply_paint(root)
    if root.gta_vehicle_studio.paintjob not in ("", "0"):
        apply_paintjob(root)
