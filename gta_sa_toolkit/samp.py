# GTA SA Toolkit - SA-MP support: "toys" (attached objects), object browser and Pawn map code.
#
# In SA-MP, "toys" are the extra objects shipped in SAMP\SAMP.img (IDs 18631-19999: hats, glasses,
# masks, helmets, bags, weapons props...) that servers attach to players with
# SetPlayerAttachedObject (the /toys menus on role-play servers). Mappers also use SA-MP objects in
# CreateObject / CreateDynamicObject map code.
import math
import re

import bpy
from mathutils import Euler, Matrix, Vector

from . import mapimport
from .formats import mapdata

# SetPlayerAttachedObject bone ids -> GTA ped skeleton bone ids (HAnim ids, the bones' bone_id)
SAMP_BONES = (
    (1, "Spine", 3), (2, "Head", 5), (3, "Left upper arm", 32), (4, "Right upper arm", 22),
    (5, "Left hand", 34), (6, "Right hand", 24), (7, "Left thigh", 41), (8, "Right thigh", 51),
    (9, "Left foot", 43), (10, "Right foot", 53), (11, "Right calf", 52), (12, "Left calf", 42),
    (13, "Left forearm", 33), (14, "Right forearm", 23), (15, "Left clavicle", 31),
    (16, "Right clavicle", 21), (17, "Neck", 4), (18, "Jaw", 8),
)
SAMP_TO_GTA = {s: g for s, _, g in SAMP_BONES}


def samp_euler_to_quat(rx, ry, rz):
    """SA-MP/open.mp euler degrees -> Blender quaternion (matches open.mp's GTAQuat maths)."""
    return Euler((math.radians(rx), math.radians(ry), math.radians(rz)), 'YXZ').to_quaternion()


def quat_to_samp_euler(q):
    """Exact reverse of samp_euler_to_quat: rotation -> SA-MP euler degrees (rx, ry, rz).
    Canonical answer: rx in [-90, 90], ry and rz in (-180, 180]; any other input angles that give
    the same rotation come back in this form (e.g. rz 450 -> 90)."""
    rx, ry, rz = (math.degrees(a) for a in q.to_euler('YXZ'))
    if abs(rx) > 90.0:                    # Blender may pick the other of the two answers:
        rx, ry, rz = 180.0 - rx, ry + 180.0, rz + 180.0      # (x, y, z) = (180 - x, y + 180, z + 180)
    return tuple(_tidy(_wrap(a)) for a in (rx, ry, rz))


def _wrap(deg):
    return deg - 360.0 * math.floor((deg + 180.0) / 360.0)


def _tidy(deg):
    deg = round(deg, 6) + 0.0             # no -0.0 / 1e-15 noise in exported code
    return 180.0 if deg == -180.0 else deg


# ----------------------------------------------------------------------------- pawn parsing
_CALL = re.compile(r"\b(CreateDynamicObjectEx|CreateDynamicObject|CreateObject|CreatePlayerObject|"
                   r"AddStaticVehicleEx|AddStaticVehicle|CreateVehicle|RemoveBuildingForPlayer|"
                   r"SetPlayerAttachedObject)\s*\(([^;]*?)\)\s*;", re.S)


def _nums(args):
    out = []
    for a in args:
        a = a.strip().rstrip("f")
        try:
            out.append(float(a))
        except ValueError:
            out.append(None)
    return out


class PawnMap:
    def __init__(self):
        self.objects = []      # (model, x, y, z, rx, ry, rz)
        self.vehicles = []     # (model, x, y, z, angle, c1, c2)
        self.removes = []      # (model, x, y, z, radius)
        self.attachments = []  # (index, model, bone, ox, oy, oz, rx, ry, rz, sx, sy, sz)
        self.skipped = 0

    def has_content(self):
        """Anything the map-code import places or removes."""
        return bool(self.objects or self.vehicles or self.removes)


def parse_pawn(text):
    pm = PawnMap()
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    for fn, argstr in _CALL.findall(text):
        args = [a for a in re.split(r",(?![^(]*\))", argstr)]
        if fn in ("CreatePlayerObject", "RemoveBuildingForPlayer", "SetPlayerAttachedObject"):
            args = args[1:]                       # drop playerid
        n = _nums(args)
        try:
            if fn in ("CreateObject", "CreatePlayerObject", "CreateDynamicObject", "CreateDynamicObjectEx"):
                v = n[:7]
                if None in v or len(v) < 7:
                    pm.skipped += 1
                    continue
                pm.objects.append((int(v[0]),) + tuple(v[1:7]))
            elif fn in ("AddStaticVehicle", "AddStaticVehicleEx", "CreateVehicle"):
                v = n[:5]
                if None in v or len(v) < 5:
                    pm.skipped += 1
                    continue
                c1 = n[5] if len(n) > 5 and n[5] is not None else -1
                c2 = n[6] if len(n) > 6 and n[6] is not None else -1
                pm.vehicles.append((int(v[0]),) + tuple(v[1:5]) + (int(c1), int(c2)))
            elif fn == "RemoveBuildingForPlayer":
                v = n[:5]
                if None in v or len(v) < 5:
                    pm.skipped += 1
                    continue
                pm.removes.append((int(v[0]),) + tuple(v[1:5]))
            elif fn == "SetPlayerAttachedObject":
                v = n + [None] * 12
                if None in v[:3]:
                    pm.skipped += 1
                    continue
                d = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
                vals = [x if x is not None else d[i] for i, x in enumerate(v[3:12])]
                pm.attachments.append((int(v[0]), int(v[1]), int(v[2])) + tuple(vals))
        except (IndexError, ValueError, TypeError):
            pm.skipped += 1
    return pm


def pawn_plan(game, pm, section_name="SA-MP map"):
    plan = mapimport.Plan()
    sec = mapdata.Section(section_name, "")
    for model, x, y, z, rx, ry, rz in pm.objects:
        od = game.objects.get(model)
        if od is None:
            continue
        qb = samp_euler_to_quat(rx, ry, rz)
        inst = mapdata.Inst(model, od.model, 0, (x, y, z), (qb.x, qb.y, qb.z, -qb.w), -1, sec)
        sec.insts.append(inst)
        plan.insts.append(inst)
    seen = set()
    for i in plan.insts:
        if i.id not in seen:
            seen.add(i.id)
            plan.models.append(i.id)
    return plan


def apply_removes(pm):
    """RemoveBuildingForPlayer: hide already-imported map objects (model -1 = everything)."""
    hidden = 0
    if not pm.removes:
        return 0
    for ob in list(bpy.data.objects):
        gid = ob.get("gta_id")
        if gid is None:
            continue
        p = ob.matrix_world.translation
        for model, x, y, z, r in pm.removes:
            if model != -1 and model != gid:
                continue
            if (p - Vector((x, y, z))).length <= r:
                ob.hide_viewport = ob.hide_render = True
                ob["gta_removed"] = True
                hidden += 1
                break
    return hidden


# ----------------------------------------------------------------------------- toys
def find_bone_for_samp(arm, samp_bone):
    gid = SAMP_TO_GTA.get(int(samp_bone))
    for b in arm.data.bones:
        if b.get("bone_id") == gid:
            return b
    return None


def attach_toy(context, game, arm, model_id, samp_bone, offset=(0, 0, 0), rot=(0, 0, 0),
               scale=(1, 1, 1), pack=True, index=0):
    od = game.objects.get(int(model_id))
    if od is None:
        raise RuntimeError("Unknown object model id %s" % model_id)
    bone = find_bone_for_samp(arm, samp_bone)
    if bone is None:
        raise RuntimeError("The armature has no bone for SA-MP bone %s (needs a GTA SA ped)" % samp_bone)
    coll = mapimport.import_model_from_img(context, game, od.model, True, pack)
    roots = [o for o in coll.objects if o.parent is None]
    local = toy_matrix(offset, rot, scale)
    for r in roots:
        base = r.matrix_basis.copy()
        r.parent = arm
        r.parent_type = 'BONE'
        r.parent_bone = bone.name
        r.matrix_parent_inverse = Matrix.Translation((0.0, -bone.length, 0.0))
        r.matrix_basis = local @ base
        r["gta_toy"] = int(model_id)
        r["gta_samp_bone"] = int(samp_bone)
        r["gta_samp_index"] = int(index)
        if not _is_identity(base):         # the model's own root transform, kept for the reverse
            r["gta_toy_base"] = [v for row in base for v in row]
    return coll, bone


def toy_matrix(offset=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1)):
    """SetPlayerAttachedObject offset/rotation/scale -> matrix in the bone's space."""
    return (Matrix.Translation(Vector(offset)) @ samp_euler_to_quat(*rot).to_matrix().to_4x4() @
            Matrix.Diagonal((*scale, 1.0)))


def _is_identity(m):
    return all(abs(m[i][j] - (1.0 if i == j else 0.0)) < 1e-7 for i in range(4) for j in range(4))


def _toy_base(ob):
    b = ob.get("gta_toy_base")
    if b is None or len(b) != 16:
        return Matrix.Identity(4)         # toys attached by older versions: root was at the origin
    return Matrix([b[0:4], b[4:8], b[8:12], b[12:16]])


def toy_root(ob):
    """The attached toy an object belongs to (the object with gta_toy on a bone), or None."""
    while ob is not None:
        if ob.get("gta_toy") is not None and ob.parent is not None and ob.parent_type == 'BONE':
            return ob
        ob = ob.parent
    return None


def toy_values(ob):
    """Exact reverse of attach_toy for a toy root: dict model, index, bone, offset, rot, scale
    (SetPlayerAttachedObject values), read from where the toy is now."""
    local = ob.matrix_basis @ _toy_base(ob).inverted_safe()
    loc, q, sc = local.decompose()
    bone = samp_bone_of(ob.parent, ob.parent_bone)     # the bone it's on now
    if bone is None:
        bone = ob.get("gta_samp_bone")
    return {"model": int(ob["gta_toy"]), "index": int(ob.get("gta_samp_index", 0)),
            "bone": int(bone) if bone is not None else 0,
            "offset": tuple(_tidy(v) for v in loc), "rot": quat_to_samp_euler(q),
            "scale": tuple(_tidy(v) for v in sc)}


def set_toy_values(ob, offset=None, rot=None, scale=None, index=None, bone=None):
    """Move a toy root to these SetPlayerAttachedObject values (None = keep the current one).
    A new SA-MP bone moves the toy onto that bone with the same offset/rotation/scale, like the game."""
    cur = toy_values(ob)
    if bone is not None and int(bone) != cur["bone"]:
        b = find_bone_for_samp(ob.parent, bone) if ob.parent is not None and ob.parent.type == 'ARMATURE' else None
        if b is None:
            raise ValueError("The ped has no bone for SA-MP bone %s" % bone)
        ob.parent_bone = b.name
        ob.matrix_parent_inverse = Matrix.Translation((0.0, -b.length, 0.0))
        ob["gta_samp_bone"] = int(bone)
    local = toy_matrix(cur["offset"] if offset is None else offset, cur["rot"] if rot is None else rot,
                       cur["scale"] if scale is None else scale)
    ob.matrix_basis = local @ _toy_base(ob)
    if index is not None:
        ob["gta_samp_index"] = int(index)


def samp_bone_of(arm, bone_name):
    if arm is None or arm.type != 'ARMATURE':
        return None
    b = arm.data.bones.get(bone_name)
    gid = b.get("bone_id") if b is not None else None
    return next((s for s, _n, g in SAMP_BONES if g == gid), None)


def attached_line(values, playerid="playerid"):
    """SetPlayerAttachedObject(...) line for toy_values() output."""
    v = values
    nums = ", ".join(_fmt(x) for x in (*v["offset"], *v["rot"], *v["scale"]))
    return "SetPlayerAttachedObject(%s, %d, %d, %d, %s);" % (playerid, v["index"], v["model"], v["bone"], nums)


def _fmt(x, digits=4):
    s = "%.*f" % (digits, x)
    return s[1:] if s.startswith("-") and float(s) == 0.0 else s


# ----------------------------------------------------------------------------- map objects -> pawn
def map_object_root(ob):
    """The placed map object (with gta_id) an object belongs to, or None. Vehicles, weapons and
    toys also carry gta_id but aren't map objects."""
    while ob is not None:
        if ob.get("gta_id") is not None:
            if ob.get("gta_vehicle") is not None or ob.get("gta_weapon") is not None or toy_root(ob) is not None:
                return None
            return ob
        ob = ob.parent
    return None


def library_roots():
    """{("data", mesh name) / ("name", object name): world matrix} of the model library's root
    objects, built once per export. A map object placed as a copy (not an instance) shares its mesh
    with the library root it was copied from (an empty root: same name + ".001") and carries that
    root's own transform: placed = IPL matrix @ library root."""
    out = {}
    lib = bpy.data.collections.get(mapimport.LIB_NAME)
    for c in (lib.children if lib is not None else ()):
        for o in list(c.objects):
            if o.parent is None:
                out[("name", o.name)] = o.matrix_world.copy()
                if o.data is not None:
                    out[("data", o.data.name)] = out[("name", o.name)]
    return out


def _base(name):
    head, dot, tail = name.rpartition(".")
    return head if dot and tail.isdigit() else name


def placed_values(ob, roots=None):
    """Exact reverse of a map/Pawn import for a placed map object (see map_object_root):
    (model, (x, y, z), (rx, ry, rz) SA-MP degrees, (sx, sy, sz)). roots: library_roots(), pass it
    when exporting many objects."""
    m = ob.matrix_world.copy()
    if ob.instance_type != 'COLLECTION':
        roots = library_roots() if roots is None else roots
        src = roots.get(("data", ob.data.name)) if ob.data is not None else roots.get(("name", _base(ob.name)))
        if src is not None and not _is_identity(src):
            m = m @ src.inverted_safe()
    loc, q, sc = m.decompose()
    return (int(ob["gta_id"]), tuple(_tidy(v) for v in loc), quat_to_samp_euler(q), tuple(_tidy(v) for v in sc))


def object_line(model, pos, rot, dynamic=False):
    """CreateObject / CreateDynamicObject line (7 values; draw/stream distance left at default)."""
    fn = "CreateDynamicObject" if dynamic else "CreateObject"
    return "%s(%d, %s);" % (fn, model, ", ".join(_fmt(x) for x in (*pos, *rot)))


def search_objects(game, query, limit=300):
    q = query.strip().lower()
    out = []
    if not q:
        return out
    ids = None
    if q.isdigit():
        ids = int(q)
    for oid, od in sorted(game.objects.items()):
        if ids is not None:
            if str(oid).startswith(q):
                out.append(od)
        elif q in od.model.lower():
            out.append(od)
        if len(out) >= limit:
            break
    return out
