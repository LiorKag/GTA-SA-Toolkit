# GTA SA Toolkit - vehicle import (straight from the IMG) with wheels, paint and lights
import math
import random

from mathutils import Matrix

from . import events, mapimport, rwbuild

# RenderWare placeholder material colours used by GTA SA vehicles
PAINT_SLOTS = {
    (60, 255, 0): 0,      # primary
    (255, 0, 175): 1,     # secondary
    (0, 255, 255): 2,     # third
    (255, 0, 255): 3,     # fourth
}
LIGHT_SLOTS = {
    (255, 175, 0): "head", (0, 255, 200): "head",
    (185, 255, 0): "tail", (255, 60, 0): "tail",
}
WHEEL_DUMMIES = ("wheel_lf_dummy", "wheel_rf_dummy", "wheel_lb_dummy", "wheel_rb_dummy",
                 "wheel_lm_dummy", "wheel_rm_dummy")


def _base_name(ob):
    n = ob.name
    return n.rsplit(".", 1)[0] if len(n) > 4 and n[-4] == "." and n[-3:].isdigit() else n


# ----------------------------------------------------------------------------- materials
def _principled(mat):
    if not mat or not mat.use_nodes:
        return None
    for n in mat.node_tree.nodes:
        if n.type == 'BSDF_PRINCIPLED':
            return n
    return None


def material_rgb(mat):
    """Original DFF material colour (0-255 ints)."""
    if "gta_orig_color" in mat:
        return tuple(int(x) for x in mat["gta_orig_color"])
    p = _principled(mat)
    c = p.inputs["Base Color"].default_value if p else mat.diffuse_color
    rgb = tuple(int(round(v * 255)) for v in c[:3])
    mat["gta_orig_color"] = rgb
    return rgb


def tint_material(mat, rgb01):
    """Multiply the material's texture by a colour (how GTA paints cars)."""
    p = _principled(mat)
    if p is None:
        mat.diffuse_color = (*rgb01, 1.0)
        return
    nt = mat.node_tree
    inp = p.inputs["Base Color"]
    tint = nt.nodes.get("GTA Paint")
    if tint is None:
        if inp.is_linked:
            src = inp.links[0].from_socket
            tint = nt.nodes.new("ShaderNodeMix")
            tint.name = tint.label = "GTA Paint"
            tint.data_type = 'RGBA'
            tint.blend_type = 'MULTIPLY'
            tint.inputs[0].default_value = 1.0
            tint.location = (p.location.x - 220, p.location.y)
            nt.links.new(src, tint.inputs[6])            # A
            nt.links.new(tint.outputs[2], inp)           # Result
        else:
            inp.default_value = (*rgb01, 1.0)
            mat.diffuse_color = (*rgb01, 1.0)
            return
    tint.inputs[7].default_value = (*rgb01, 1.0)         # B
    mat.diffuse_color = (*rgb01, 1.0)


def set_emission(mat, rgb01, strength):
    p = _principled(mat)
    if p is None:
        return
    col = p.inputs.get("Emission Color") or p.inputs.get("Emission")
    if col is not None:
        col.default_value = (*rgb01, 1.0)
    st = p.inputs.get("Emission Strength")
    if st is not None:
        st.default_value = strength


# ----------------------------------------------------------------------------- vehicle
def paint_vehicle(objects, colours01):
    """colours01: list of up to 4 (r, g, b) floats."""
    done = set()
    for ob in objects:
        for slot in getattr(ob, "material_slots", []):
            mat = slot.material
            if not mat or mat.name in done:
                continue
            done.add(mat.name)
            rgb = material_rgb(mat)
            idx = PAINT_SLOTS.get(rgb)
            if idx is not None and idx < len(colours01):
                tint_material(mat, colours01[idx])
                mat["gta_paint_slot"] = idx
            kind = LIGHT_SLOTS.get(rgb)
            if kind:
                col = (1.0, 0.95, 0.85) if kind == "head" else (0.9, 0.05, 0.03)
                tint_material(mat, col)
                mat["gta_vehicle_light"] = kind
                set_emission(mat, col, 0.0)


def _is_wheel_mesh(ob):
    """Wheel geometry: a mesh named wheel / wheel1 / wheel2 / wheela / wheel_rf ... (not a dummy)."""
    n = _base_name(ob).lower()
    return ob.type == 'MESH' and n.startswith("wheel") and not n.endswith("_dummy")


def _duplicate_wheels(objs, vdef):
    by_name = {}
    for ob in objs:
        by_name.setdefault(_base_name(ob).lower(), ob)
    # the game stores one wheel (usually under wheel_rf_dummy) and clones it onto the other dummies;
    # its name varies per model: wheel, wheel2 (buccanee), wheel1 (beagle), wheela (raindanc), wheel_rf
    wheel = None
    for dname in ("wheel_rf_dummy",) + WHEEL_DUMMIES:
        d = by_name.get(dname)
        if d is not None:
            wheel = next((c for c in d.children if _is_wheel_mesh(c)), None)
            if wheel is not None:
                break
    if wheel is None:
        wheel = by_name.get("wheel")
        if wheel is None or wheel.type != 'MESH':
            return 0
    ratio = (vdef.wheel_scale_r / vdef.wheel_scale_f) if vdef and vdef.wheel_scale_f else 1.0
    made = 0
    for dname in WHEEL_DUMMIES:
        dummy = by_name.get(dname)
        if dummy is None or dummy == wheel.parent:
            continue
        if any(_is_wheel_mesh(c) for c in dummy.children):     # stunt: own wheel_l / wheel_r
            continue
        w = wheel.copy()                                   # shares mesh data
        for c in wheel.users_collection:
            c.objects.link(w)
        w.parent = dummy
        m = wheel.matrix_basis.copy()
        if dname[6] == "l":                                # left side: mirrored
            m = m @ Matrix.Rotation(math.pi, 4, 'Z')
        if dname[7] in "bm" and abs(ratio - 1.0) > 1e-3:   # rear / middle wheels
            m = m @ Matrix.Scale(ratio, 4)
        w.matrix_parent_inverse = wheel.matrix_parent_inverse.copy()
        w.matrix_basis = m
        made += 1
    return made


def _hide(ob, state=True):
    ob.hide_viewport = state
    ob.hide_render = state


def import_vehicle(context, game, name, colours=None, colour_set=-1, hide_damage=True,
                   hide_lod=True, pack=True, location=None, rotation_z=0.0):
    """Import a vehicle by model name or ID. colours: list of carcols indices (overrides colour_set)."""
    vdef = None
    key = str(name).strip()
    if key.isdigit():
        vdef = game.vehicles.get(int(key))
    else:
        vdef = next((v for v in game.vehicles.values() if v.model.lower() == key.lower()), None)
    model = vdef.model if vdef else key
    if game.imgs.find(model + ".dff") is None:
        raise RuntimeError("'%s.dff' not found in the IMG archives" % model)
    txd = vdef.txd if vdef else model
    opts = {'connect_bones': False, 'use_mat_split': False, 'remove_doubles': False}
    coll, _hit = mapimport.build_model(game, model, txd, opts, mapimport.TxdCache(game, False),
                                       extra_parents=("vehicle",))
    game.imgs.close()
    root = finish_vehicle(coll, game, vdef, model, colours, colour_set, hide_damage, hide_lod, location,
                          rotation_z)
    events.import_done(list(coll.all_objects))
    return coll, root, vdef


def finish_vehicle(coll, game, vdef, model, colours=None, colour_set=-1, hide_damage=True, hide_lod=True,
                   location=None, rotation_z=0.0, name=None):
    """What every vehicle gets after it was built (game or custom): the wheel copies, damaged /
    low-detail / collision parts hidden, paint from vdef's colour sets, and the root's tags (gta_vehicle
    = model, whose game data Studio's vehicle sections use). Returns the root."""
    objs = list(coll.all_objects)
    _duplicate_wheels(objs, vdef)
    objs = list(coll.all_objects)
    for child in coll.children_recursive:          # embedded collision / shadow meshes
        for ob in child.objects:
            _hide(ob)
    for ob in objs:
        n = _base_name(ob).lower()
        if hide_damage and n.endswith("_dam"):
            _hide(ob)
        if hide_lod and (n.endswith("_vlo") or n.endswith("_lod")):
            _hide(ob)
        if ob.type == 'MESH' and rwbuild.is_collision(ob):
            _hide(ob)

    # paint
    if colours is None:
        sets = game.car_colour_sets.get(model.lower()) or [(1, 1)]
        pick = sets[colour_set] if 0 <= colour_set < len(sets) else random.choice(sets)
        colours = list(pick)
    rgb = [tuple(c / 255.0 for c in game.colour_rgb(int(i))) for i in colours]
    paint_vehicle(objs, rgb)

    root = next((o for o in objs if o.parent is None and o.type == 'EMPTY'), None) or \
        next((o for o in objs if o.parent is None), None)
    if root is not None:
        root["gta_vehicle"] = model
        root["gta_vehicle_name"] = name or (vdef.name if vdef else model)     # in-game name, shown by Studio
        root["gta_id"] = vdef.id if vdef else -1
        root["gta_colours"] = [int(c) for c in colours]
        if location is not None:
            root.location = location
        root.rotation_mode = 'XYZ'
        root.rotation_euler = (0.0, 0.0, rotation_z)
    return root


DOOR_DUMMIES = ("door_lf_dummy", "door_rf_dummy", "door_lr_dummy", "door_rr_dummy", "bonnet_dummy", "boot_dummy")
BIKE_WHEEL_FRAMES = ("wheel_front", "wheel_rear", "forks_front", "forks_rear")


def vehicle_checks(coll, vkind='CAR'):
    """What won't work on a (custom) vehicle: [(short, full sentence)]. vkind: CAR / BIKE / BOAT / HELI /
    PLANE (only cars are expected to have doors)."""
    objs = list(coll.all_objects)
    names = {_base_name(o).lower() for o in objs}
    out = []
    painted = any(sl.material and "gta_paint_slot" in sl.material for o in objs for sl in getattr(o, "material_slots", ()))
    if not painted:
        out.append(("No paint colours", "No GTA paint colours on it: Colours won't change this vehicle"))
    if vkind in ('CAR', 'BIKE') and not names & (set(WHEEL_DUMMIES) | set(BIKE_WHEEL_FRAMES)):
        out.append(("No wheel dummies", "No wheel dummies: its wheels won't turn or steer in Follow Path"))
    if vkind == 'CAR' and not names & set(DOOR_DUMMIES):
        out.append(("No door dummies", "No door / bonnet / boot dummies: no door sliders"))
    return out


# ----------------------------------------------------------------------------- paintjobs, upgrades
# A paintjob is <model>1.txd, <model>2.txd ... in the IMG, holding one texture that replaces the
# model's "remap..." body texture (Wheel Arch Angels / Loco Low Co. cars and the camper).
def paintjob_count(game, model):
    n = 0
    while n < 9 and game.imgs.find("%s%d.txd" % (model.lower(), n + 1)) is not None:
        n += 1
    return n


def paintjob_image(game, model, n, pack=True):
    """The paintjob's texture as a bpy image (loaded once, reused), or None."""
    imgs = mapimport.TxdCache(game, pack)._load_one("%s%d" % (model.lower(), n))
    game.imgs.close()
    for key in sorted(imgs):
        return imgs[key][0]
    return None


def vehicle_data(game, model):
    """What the game knows about a vehicle model's colours, paintjobs and upgrades."""
    from .formats import carmods
    key = model.lower()
    vdef = next((v for v in game.vehicles.values() if v.model.lower() == key), None)
    cm = game.car_mods
    parts = list(cm.mods.get(key, ()))
    if vdef is not None and vdef.upgrade_class >= 0:
        parts += [w for w in cm.wheels.get(vdef.upgrade_class, ()) if w not in parts]
    upgrades = {}
    for p in parts:
        if game.imgs.find(p + ".dff") is None:
            continue
        upgrades.setdefault(carmods.upgrade_kind(p), []).append(p)
    for lst in upgrades.values():
        lst.sort()
    return {
        "model": vdef.model if vdef else model,
        "id": vdef.id if vdef else -1,
        "type": vdef.type if vdef else "",
        "colour_sets": list(game.car_colour_sets.get(key, ())),
        "paintjobs": paintjob_count(game, key),
        "upgrades": upgrades,
        "links": {p: cm.links[p] for p in parts if p in cm.links},
        "model_flags": game.handling_flags.get(vdef.handling.upper(), 0) if vdef else 0,
    }


def import_part(game, name, pack=True):
    """Build an upgrade model (spoiler, wheel, ...) from the IMG into a new collection. Its embedded
    collision is hidden. Returns (collection, objects)."""
    od = game.by_name.get(name.lower())
    txd = od.txd if od else "vehicle"
    opts = {'connect_bones': False, 'use_mat_split': False, 'remove_doubles': False}
    coll, _hit = mapimport.build_model(game, od.model if od else name, txd, opts,
                                       mapimport.TxdCache(game, pack), extra_parents=("vehicle",))
    game.imgs.close()
    if coll is None:
        raise RuntimeError("'%s.dff' not found in the IMG archives" % name)
    for child in coll.children_recursive:
        for ob in child.objects:
            _hide(ob)
    for ob in coll.all_objects:
        if ob.type == 'MESH' and rwbuild.is_collision(ob):
            _hide(ob)
    return coll, list(coll.all_objects)


def repaint_selected(context, game, colours):
    rgb = [tuple(c / 255.0 for c in game.colour_rgb(int(i))) for i in colours]
    roots = {o for o in context.selected_objects}
    objs = set()
    for r in roots:
        objs.add(r)
        objs.update(r.children_recursive)
    for o in objs:
        for slot in getattr(o, "material_slots", []):
            m = slot.material
            if m and "gta_paint_slot" in m and m["gta_paint_slot"] < len(rgb):
                tint_material(m, rgb[m["gta_paint_slot"]])
    return len(objs)
