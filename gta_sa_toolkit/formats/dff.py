# GTA SA Toolkit - DFF (RenderWare clump) reader: frames and dummies, geometry (vertices, normals, UVs,
# prelit and night vertex colours, triangle lists and strips), materials with texture names and effects,
# atomics, HAnim bone ids, skin weights, 2DFX effects (lights in full, the rest kept raw), UV animations
# and embedded collision. Only reading: the add-on never writes DFF files.
# Written from the GTAMods wiki (RenderWare, RpClump, RpGeometry, Bin Mesh PLG, Skin PLG, HAnim PLG,
# 2d Effect, Extra Vert Colour pages). Bulk data comes back as numpy arrays (fast to hand to Blender).
#
# load(data) -> Dff. It never raises anything but rw.RWError (a clear message: nothing usable in the
# file); parts it can't read are skipped and described in Dff.warnings. PC models of GTA III, Vice City
# and San Andreas (RenderWare 3.1-3.6) are read; console models (PS2 / Xbox / GameCube native geometry)
# give a message instead.
import struct

import numpy as np

from . import rw
from .rw import RWError

# geometry flags
TRISTRIP, POSITIONS, TEXTURED, PRELIT, NORMALS, LIGHT, MODULATE, TEXTURED2 = 1, 2, 4, 8, 16, 32, 64, 128
NATIVE = 0x01000000

# 2DFX effect types (GTA SA)
FX_LIGHT, FX_PARTICLE, FX_PED_ATTRACTOR, FX_SUN_GLARE, FX_ENTER_EXIT, FX_ROAD_SIGN = 0, 1, 3, 4, 6, 7
FX_TRIGGER, FX_COVER, FX_ESCALATOR = 8, 9, 10

MAX_COUNT = 1 << 22             # no real model has more vertices / faces / frames than this


class Texture:
    __slots__ = ("name", "mask", "filters", "addressing")

    def __init__(self):
        self.name, self.mask, self.filters, self.addressing = "", "", 0, 0


class Material:
    __slots__ = ("flags", "color", "textured", "surface", "texture", "env_map", "bump_map", "dual",
                 "matfx", "specular", "reflection", "uv_anims", "user_data")

    def __init__(self):
        self.flags, self.color, self.textured = 0, (255, 255, 255, 255), False
        self.surface = None             # (ambient, specular, diffuse) or None
        self.texture = None             # Texture or None
        self.env_map = None             # (coefficient, use_fb_alpha, Texture or None)
        self.bump_map = None            # (intensity, bump Texture or None, height Texture or None)
        self.dual = None                # (src_blend, dst_blend, Texture or None)
        self.matfx = 0                  # MatFX effect type (0 = none; 1 bump, 2 env, 4 dual, 5 uv transform...)
        self.specular = None            # (level, texture name)
        self.reflection = None          # (scale x, scale y, offset x, offset y, intensity)
        self.uv_anims = []              # names of UV animations (Dff.uv_anims)
        self.user_data = None


class Skin:
    __slots__ = ("num_bones", "used_bones", "max_weights", "indices", "weights", "inverse")

    def __init__(self):
        self.num_bones, self.used_bones, self.max_weights = 0, [], 0
        self.indices = None             # (n, 4) uint8: per vertex 4 bone indices (into the HAnim bone list)
        self.weights = None             # (n, 4) float32
        self.inverse = []               # per bone 16 floats (bone space from model space, column-major rows)


class Geometry:
    __slots__ = ("flags", "num_verts", "verts", "normals", "uvs", "prelit", "night", "tris", "tri_mats",
                 "split_tris", "split_mats", "split_strip", "split_order", "materials", "skin", "sphere", "surface",
                 "morph_targets", "native", "user_data", "version")

    def __init__(self):
        self.flags = self.num_verts = 0
        self.verts = self.normals = None            # (n, 3) float32
        self.uvs = []                               # list of (n, 2) float32 (u, v as stored: v down)
        self.prelit = self.night = None             # (n, 4) uint8 RGBA: day prelight, night (extra) colours
        self.tris = np.zeros((0, 3), np.int32)      # (m, 3) faces from the geometry itself (a, b, c)
        self.tri_mats = np.zeros(0, np.int32)       # (m,) material of each
        self.split_tris = None                      # faces from the Bin Mesh PLG (what the game draws)
        self.split_mats = None
        self.split_strip = False                    # Bin Mesh PLG stored triangle strips
        self.split_order = []                       # material of each Bin Mesh PLG split, in file order
        self.materials = []
        self.skin = None
        self.sphere = (0.0, 0.0, 0.0, 0.0)
        self.surface = None
        self.morph_targets = 1
        self.native = False
        self.user_data = None
        self.version = 0

    def faces(self):
        """(faces, material per face): the triangle list when the file has one, else the Bin Mesh PLG's."""
        if len(self.tris) or self.split_tris is None:
            return self.tris, self.tri_mats
        return self.split_tris, self.split_mats


class Frame:
    __slots__ = ("name", "rot", "pos", "parent", "flags", "bone_id", "hanim", "anim_id", "user_data")

    def __init__(self):
        self.name = ""
        self.rot = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)   # rows: right, up, at
        self.pos = (0.0, 0.0, 0.0)
        self.parent, self.flags = -1, 0
        self.bone_id = None             # HAnim node id (what IFP animations and SA-MP use), None = not a bone
        self.hanim = None               # root of a skeleton: [(node id, index, type), ...] for every bone
        self.anim_id = None             # old (pre-HAnim) Animation PLG id
        self.user_data = None

    def matrix(self):
        """4x4 row-major list: the frame relative to its parent (RenderWare stores the axes as rows)."""
        r = self.rot
        return [[r[0], r[3], r[6], self.pos[0]],
                [r[1], r[4], r[7], self.pos[1]],
                [r[2], r[5], r[8], self.pos[2]],
                [0.0, 0.0, 0.0, 1.0]]


class Atomic:
    __slots__ = ("frame", "geometry", "flags", "pipeline", "right_to_render", "sky_gfx")

    def __init__(self):
        self.frame = self.geometry = self.flags = 0
        self.pipeline = self.right_to_render = self.sky_gfx = None


class Effect2D:
    """One 2DFX entry: every type is kept (type, position relative to the model, bytes in .raw), also the
    ones the builder doesn't make objects for. Lights (type 0) are read into fields at load; info()
    decodes the other known types on demand (a dict; {} for unknown types or short data)."""
    __slots__ = ("type", "pos", "raw", "geometry", "color", "corona_far_clip", "point_light_range",
                 "corona_size", "shadow_size", "corona_show_mode", "corona_reflection", "corona_flare_type",
                 "shadow_color_mult", "flags1", "corona_tex", "shadow_tex", "shadow_z_distance", "flags2",
                 "look_direction")

    def __init__(self, etype, pos, raw, geometry):
        self.type, self.pos, self.raw, self.geometry = etype, pos, raw, geometry
        self.look_direction = None

    def info(self):
        r, t = self.raw, self.type
        try:
            if t == FX_PARTICLE:
                return {"effect": rw.cstr(r[:24])}
            if t == FX_PED_ATTRACTOR:
                v = struct.unpack_from("<I9f", r, 0)
                return {"kind": v[0], "queue_dir": v[1:4], "use_dir": v[4:7], "forward_dir": v[7:10],
                        "script": rw.cstr(r[40:48]), "probability": struct.unpack_from("<I", r, 48)[0]}
            if t == FX_SUN_GLARE:
                return {}
            if t == FX_ENTER_EXIT:
                ang, rx, ry, ex, ey, ez, eang, interior, f1, sky = struct.unpack_from("<7fHBB", r, 0)
                on, off, f2, _u = struct.unpack_from("<4B", r, 40)
                return {"enter_angle": ang, "radius": (rx, ry), "exit": (ex, ey, ez), "exit_angle": eang,
                        "interior": interior, "flags1": f1, "sky_colour": sky, "name": rw.cstr(r[32:40]),
                        "time_on": on, "time_off": off, "flags2": f2}
            if t == FX_ROAD_SIGN:
                sx, sy, rx, ry, rz, flags = struct.unpack_from("<5fH", r, 0)
                return {"size": (sx, sy), "rotation": (rx, ry, rz), "flags": flags,
                        "lines": [rw.cstr(r[22 + 16 * k:38 + 16 * k]) for k in range(4)]}
            if t == FX_TRIGGER:
                return {"point_id": struct.unpack_from("<I", r, 0)[0]}
            if t == FX_COVER:
                dx, dy, kind = struct.unpack_from("<ffI", r, 0)
                return {"direction": (dx, dy), "kind": kind}
            if t == FX_ESCALATOR:
                v = struct.unpack_from("<9fI", r, 0)
                return {"bottom": v[0:3], "top": v[3:6], "end": v[6:9], "direction": v[9]}
        except struct.error:
            pass
        return {}


class UVAnim:
    __slots__ = ("name", "duration", "flags", "node_to_uv", "frames")

    def __init__(self):
        self.name, self.duration, self.flags, self.node_to_uv, self.frames = "", 0.0, 0, [0.0] * 8, []


class Dff:
    __slots__ = ("frames", "geometries", "atomics", "effects", "uv_anims", "collisions", "version",
                 "warnings", "clumps", "more")

    def __init__(self):
        self.frames, self.geometries, self.atomics, self.effects = [], [], [], []
        self.uv_anims = []              # UVAnim (file-level dictionary)
        self.collisions = []            # bytes of embedded COL data (formats/col.py)
        self.version = 0
        self.warnings = []              # parts skipped, in plain words
        self.clumps = 0                 # models (clumps) in the file
        self.more = []                  # the 2nd, 3rd... model as Dff objects of their own (CJ's clothes in
                                        # player.img hold 3); the importers build the first one

    def lights(self):
        return [e for e in self.effects if e.type == FX_LIGHT]

    def texture_names(self):
        out = []
        for g in self.geometries:
            for m in g.materials:
                if m.texture is not None and m.texture.name and m.texture.name not in out:
                    out.append(m.texture.name)
        return out


# --------------------------------------------------------------------------------------------- load

def load(data):
    """Dff from a DFF file's bytes (see the module comment)."""
    data = bytes(data) if not isinstance(data, (bytes, memoryview)) else data
    d = Dff()
    try:
        _load(data, d)
    except RWError:
        raise
    except (struct.error, ValueError, IndexError, OverflowError, MemoryError) as e:
        raise RWError("Not a readable model file (%s)" % (e or type(e).__name__))
    if not d.frames and not d.geometries:
        if d.warnings:
            raise RWError(d.warnings[0])
        raise RWError("No model in this file (not a DFF?)")
    if not d.frames:                    # an atomic on its own (very old files): give it a frame
        f = Frame()
        f.name = "root"
        d.frames.append(f)
    return d


def _load(data, d):
    end = len(data)
    if end < 12:
        raise RWError("Not a model file (too small)")
    first = rw.chunk_at(data, 0, end)
    if first.type not in (rw.CLUMP, rw.UV_ANIM_DICT, rw.ATOMIC):
        if first.type == rw.TEXTURE_DICTIONARY:
            raise RWError("This is a texture dictionary (.txd), not a model")
        if data[:3] == b"COL":
            raise RWError("This is a collision file (.col), not a model")
        raise RWError("Not a model file (unknown start 0x%X)" % first.type)
    for c in rw.children(data, 0, end):
        if c.type == rw.CLUMP:
            if d.clumps:
                d.clumps += 1
                extra = Dff()
                extra.clumps, extra.version, extra.uv_anims = 1, c.version, d.uv_anims
                try:
                    _clump(data, c, extra)
                except (RWError, struct.error, ValueError, IndexError, OverflowError, MemoryError) as e:
                    extra.warnings.append("Model %d can't be read: %s" % (d.clumps, e or type(e).__name__))
                d.more.append(extra)
                if d.clumps == 2:
                    d.warnings.append("The file holds more than one model; the first is imported")
                continue
            d.clumps = 1
            d.version = c.version
            _clump(data, c, d)
        elif c.type == rw.UV_ANIM_DICT:
            _guard(d, "UV animations", _uv_anim_dict, data, c, d)
        elif c.type == rw.ATOMIC and not d.clumps:
            d.version = c.version
            _guard(d, "atomic", _atomic, data, c, d)


def _guard(d, what, fn, *args):
    """Run a part's reader; a broken part becomes a warning instead of failing the whole file."""
    try:
        return fn(*args)
    except (RWError, struct.error, ValueError, IndexError, OverflowError, MemoryError) as e:
        d.warnings.append("%s skipped: %s" % (what[:1].upper() + what[1:], e or type(e).__name__))
        return None


def _clump(data, c, d):
    for k in rw.children(data, c.start, c.end):
        if k.type == rw.FRAME_LIST:
            _guard(d, "frames", _frame_list, data, k, d)
        elif k.type == rw.GEOMETRY_LIST:
            _guard(d, "geometry list", _geometry_list, data, k, d)
        elif k.type == rw.ATOMIC:
            _guard(d, "atomic", _atomic, data, k, d)
        elif k.type in (rw.COLLISION_MODEL, rw.SAMP_COLLISION):
            d.collisions.append(bytes(data[k.start:k.end]))
        elif k.type == rw.EXTENSION:
            for e in rw.children(data, k.start, k.end):
                if e.type in (rw.COLLISION_MODEL, rw.SAMP_COLLISION):
                    d.collisions.append(bytes(data[e.start:e.end]))
    _legacy_skin_bones(d)
    if d.version and d.version < 0x30000:
        d.warnings.append("Very old RenderWare version (%X)" % d.version)


# ------------------------------------------------------------------------------------------ frames

def _frame_list(data, c, d):
    s = rw.find(data, c.start, c.end, rw.STRUCT)
    if s is None:
        raise RWError("no frame data")
    rw.need(data, s.start, 4, "frames")
    n = struct.unpack_from("<I", data, s.start)[0]
    if n > MAX_COUNT or s.start + 4 + 56 * n > s.end:
        raise RWError("frame count %d doesn't fit the file" % n)
    frames = []
    for i in range(n):
        f = Frame()
        v = struct.unpack_from("<12fiI", data, s.start + 4 + 56 * i)
        f.rot, f.pos, f.parent, f.flags = v[0:9], v[9:12], v[12], v[13]
        if f.parent >= n or f.parent == i:
            f.parent = -1
        frames.append(f)
    i = 0
    for e in rw.children(data, s.end, c.end):          # one Extension per frame, in order
        if e.type != rw.EXTENSION:
            continue
        if i >= n:
            break
        f = frames[i]
        for x in rw.children(data, e.start, e.end):
            if x.type == rw.FRAME_NAME:
                f.name = rw.cstr(data[x.start:x.end])
            elif x.type == rw.HANIM_PLG:
                _hanim(data, x, f)
            elif x.type == rw.USER_DATA_PLG:
                f.user_data = _user_data(data, x)
                nm = f.user_data.get("name") if f.user_data else None
                if nm and isinstance(nm[0], str) and not f.name:
                    f.name = nm[0]
            elif x.type == rw.ANIMATION_PLG and x.size >= 4:
                f.anim_id = struct.unpack_from("<i", data, x.start)[0]
        i += 1
    for f in frames:
        if not f.name:
            f.name = "unnamed"
    d.frames = frames


def _hanim(data, x, f):
    if x.size < 12:
        return
    _ver, node_id, count = struct.unpack_from("<3i", data, x.start)
    f.bone_id = node_id
    if 0 < count <= 4096 and x.start + 20 + 12 * count <= x.end:
        f.hanim = [struct.unpack_from("<3i", data, x.start + 20 + 12 * k) for k in range(count)]


def _user_data(data, x):
    """User Data PLG -> {section name: [values]} (ints, floats or strings)."""
    out, pos, end = {}, x.start, x.end
    rw.need(data, pos, 4, "user data")
    n = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    for _ in range(min(n, 256)):
        ln = struct.unpack_from("<I", data, pos)[0]
        if pos + 4 + ln > end:
            break
        name = rw.cstr(data[pos + 4:pos + 4 + ln])
        pos += 4 + ln
        etype, count = struct.unpack_from("<II", data, pos)
        pos += 8
        if count > 65536:
            break
        if etype == 1:
            vals = list(struct.unpack_from("<%di" % count, data, pos))
            pos += 4 * count
        elif etype == 2:
            vals = list(struct.unpack_from("<%df" % count, data, pos))
            pos += 4 * count
        elif etype == 3:
            vals = []
            for _k in range(count):
                sl = struct.unpack_from("<I", data, pos)[0]
                vals.append(rw.cstr(data[pos + 4:pos + 4 + sl]))
                pos += 4 + sl
        else:
            break
        out[name] = vals
    return out


def _legacy_skin_bones(d):
    """Old skinned models (before HAnim) keep the skeleton in the Skin PLG and bone ids in Animation
    PLGs: give their frames bone ids like HAnim files have."""
    root = next((f for f in d.frames if f.hanim), None)
    if root is None:
        return
    by_anim = {f.anim_id: f for f in d.frames if f.anim_id is not None and f.bone_id is None}
    for bid, _idx, _typ in root.hanim:
        f = by_anim.get(bid)
        if f is not None:
            f.bone_id = bid


# ---------------------------------------------------------------------------------------- geometry

def _geometry_list(data, c, d):
    s = rw.find(data, c.start, c.end, rw.STRUCT)
    count = struct.unpack_from("<I", data, s.start)[0] if s is not None and s.size >= 4 else 0
    k = 0
    for g in rw.children(data, s.end if s is not None else c.start, c.end):
        if g.type != rw.GEOMETRY:
            continue
        k += 1
        geo = _guard(d, "geometry %d" % (k - 1), _geometry, data, g, d)
        d.geometries.append(geo if geo is not None else Geometry())
    if k != count:
        d.warnings.append("The geometry list says %d parts, %d found" % (count, k))


def _geometry(data, c, d):
    geo = Geometry()
    geo.version = c.version
    s = rw.find(data, c.start, c.end, rw.STRUCT)
    if s is None:
        raise RWError("no geometry data")
    rw.need(data, s.start, 16, "geometry")
    flags, ntris, nverts, nmorph = struct.unpack_from("<4I", data, s.start)
    if ntris > MAX_COUNT or nverts > MAX_COUNT:
        raise RWError("%d vertices / %d triangles is not a real model" % (nverts, ntris))
    geo.flags, geo.num_verts, geo.morph_targets = flags, nverts, nmorph
    pos = s.start + 16
    if c.version < 0x34000:
        geo.surface = struct.unpack_from("<3f", data, pos)
        pos += 12
    if flags & NATIVE:
        geo.native = True
    else:
        if flags & PRELIT:
            rw.need(data, pos, 4 * nverts, "vertex colours")
            geo.prelit = np.frombuffer(data, np.uint8, 4 * nverts, pos).reshape(nverts, 4)
            pos += 4 * nverts
        if flags & (TEXTURED | TEXTURED2):
            sets = (flags >> 16) & 0xFF or (2 if flags & TEXTURED2 else 1)
            for _ in range(sets):
                rw.need(data, pos, 8 * nverts, "UVs")
                geo.uvs.append(np.frombuffer(data, np.float32, 2 * nverts, pos).reshape(nverts, 2))
                pos += 8 * nverts
        rw.need(data, pos, 8 * ntris, "triangles")
        t = np.frombuffer(data, np.uint16, 4 * ntris, pos).reshape(ntris, 4)      # b, a, material, c
        geo.tris = np.ascontiguousarray(t[:, [1, 0, 3]]).astype(np.int32)
        geo.tri_mats = t[:, 2].astype(np.int32)
        pos += 8 * ntris
    if nmorph:
        rw.need(data, pos, 24, "morph target")
        geo.sphere = struct.unpack_from("<4f", data, pos)
        has_verts, has_normals = struct.unpack_from("<II", data, pos + 16)
        pos += 24
        if has_verts:
            rw.need(data, pos, 12 * nverts, "vertices")
            geo.verts = np.frombuffer(data, np.float32, 3 * nverts, pos).reshape(nverts, 3)
            pos += 12 * nverts
        if has_normals:
            rw.need(data, pos, 12 * nverts, "normals")
            geo.normals = np.frombuffer(data, np.float32, 3 * nverts, pos).reshape(nverts, 3)
            pos += 12 * nverts
        if nmorph > 1:
            d.warnings.append("Morph targets after the first are ignored")
    for k in rw.children(data, s.end, c.end):
        if k.type == rw.MATERIAL_LIST:
            geo.materials = _guard(d, "materials", _material_list, data, k, d) or []
        elif k.type == rw.EXTENSION:
            for x in rw.children(data, k.start, k.end):
                _geometry_ext(data, x, geo, d)
    if geo.native:
        raise RWError("Console model (native geometry): only PC models can be imported")
    if geo.tris is not None and len(geo.tris) and nverts and int(geo.tris.max()) >= nverts:
        bad = (geo.tris >= nverts).any(axis=1)
        geo.tris, geo.tri_mats = geo.tris[~bad], geo.tri_mats[~bad]
        d.warnings.append("%d triangles pointed at missing vertices and were left out" % int(bad.sum()))
    if geo.split_tris is not None and len(geo.split_tris) and int(geo.split_tris.max()) >= nverts:
        bad = (geo.split_tris >= nverts).any(axis=1)
        geo.split_tris, geo.split_mats = geo.split_tris[~bad], geo.split_mats[~bad]
        d.warnings.append("%d triangles pointed at missing vertices and were left out" % int(bad.sum()))
    return geo


def _geometry_ext(data, x, geo, d):
    if x.type == rw.BIN_MESH_PLG:
        _guard(d, "mesh split", _bin_mesh, data, x, geo)
    elif x.type == rw.SKIN_PLG:
        geo.skin = _guard(d, "skin", _skin, data, x, geo)
    elif x.type == rw.EXTRA_VERT_COLOUR:
        if x.size >= 4 and struct.unpack_from("<I", data, x.start)[0] and x.start + 4 + 4 * geo.num_verts <= x.end:
            geo.night = np.frombuffer(data, np.uint8, 4 * geo.num_verts, x.start + 4).reshape(geo.num_verts, 4)
    elif x.type == rw.EFFECT_2D:
        _guard(d, "2DFX effects", _effects_2d, data, x, d, len(d.geometries))
    elif x.type == rw.USER_DATA_PLG:
        geo.user_data = _guard(d, "user data", _user_data, data, x)
    elif x.type == rw.NATIVE_DATA_PLG:
        geo.native = True


def _bin_mesh(data, x, geo):
    rw.need(data, x.start, 12, "mesh split")
    strip, nmesh, total = struct.unpack_from("<3I", data, x.start)
    if nmesh > 65536 or total > MAX_COUNT * 3:
        raise RWError("mesh split counts don't fit")
    wide = 12 + 8 * nmesh + 4 * total <= x.size            # 32-bit indices (PC); 16-bit = OpenGL ports
    if geo.native and 12 + 8 * nmesh >= x.size:
        geo.split_order = [struct.unpack_from("<2I", data, x.start + 12 + 8 * k)[1] for k in range(nmesh)]
        return
    pos = x.start + 12
    faces, mats = [], []
    geo.split_order = []
    for _ in range(nmesh):
        n, mat = struct.unpack_from("<2I", data, pos)
        pos += 8
        geo.split_order.append(mat)
        size = 4 if wide else 2
        rw.need(data, pos, n * size, "mesh split")
        idx = np.frombuffer(data, np.uint32 if wide else np.uint16, n, pos).astype(np.int64)
        pos += n * size
        if strip:
            if n < 3:
                continue
            a, b, c = idx[:-2], idx[1:-1], idx[2:]
            odd = (np.arange(n - 2) & 1).astype(bool)
            f = np.stack((np.where(odd, b, a), np.where(odd, a, b), c), axis=1)
        else:
            m = n // 3
            f = idx[:m * 3].reshape(m, 3)
        faces.append(f)
        mats.append(np.full(len(f), mat, np.int32))
    geo.split_strip = bool(strip)
    geo.split_tris = np.concatenate(faces).astype(np.int32) if faces else np.zeros((0, 3), np.int32)
    geo.split_mats = np.concatenate(mats) if mats else np.zeros(0, np.int32)


def _skin(data, x, geo):
    if geo.native:
        raise RWError("console skin data")
    rw.need(data, x.start, 4, "skin")
    nb, nused, maxw = struct.unpack_from("<3B", data, x.start)
    sk = Skin()
    sk.num_bones, sk.max_weights = nb, maxw
    pos = x.start + 4
    sk.used_bones = list(data[pos:pos + nused])
    pos += nused
    n = geo.num_verts
    rw.need(data, pos, 20 * n, "skin weights")
    sk.indices = np.frombuffer(data, np.uint8, 4 * n, pos).reshape(n, 4)
    pos += 4 * n
    sk.weights = np.frombuffer(data, np.float32, 4 * n, pos).reshape(n, 4)
    pos += 16 * n
    old = nused == 0                        # old files: 4 extra bytes before every matrix
    for _ in range(nb):
        if old:
            pos += 4
        if pos + 64 > x.end:
            break
        m = list(struct.unpack_from("<16f", data, pos))
        m[3] = m[7] = m[11] = 0.0
        m[15] = 1.0
        sk.inverse.append(m)
        pos += 64
    return sk


def _effects_2d(data, x, d, geometry):
    rw.need(data, x.start, 4, "2DFX")
    n = struct.unpack_from("<I", data, x.start)[0]
    pos = x.start + 4
    for _ in range(min(n, 4096)):
        if pos + 20 > x.end:
            break
        px, py, pz, etype, size = struct.unpack_from("<3f2I", data, pos)
        pos += 20
        if pos + size > x.end:
            d.warnings.append("A 2DFX effect runs past the end and was left out")
            break
        e = Effect2D(etype, (px, py, pz), bytes(data[pos:pos + size]), geometry)
        if etype == FX_LIGHT and size >= 76:
            (r, g, b, a, e.corona_far_clip, e.point_light_range, e.corona_size, e.shadow_size,
             e.corona_show_mode, e.corona_reflection, e.corona_flare_type, e.shadow_color_mult, e.flags1,
             ctex, stex, e.shadow_z_distance, e.flags2) = struct.unpack_from("<4B4f5B24s24sBB", data, pos)
            e.color = (r, g, b, a)
            e.corona_tex, e.shadow_tex = rw.cstr(ctex), rw.cstr(stex)
            if size > 76:
                e.look_direction = struct.unpack_from("<3b", data, pos + 75)
        d.effects.append(e)
        pos += size


# --------------------------------------------------------------------------------------- materials

def _material_list(data, c, d):
    s = rw.find(data, c.start, c.end, rw.STRUCT)
    if s is None:
        raise RWError("no material list data")
    n = struct.unpack_from("<I", data, s.start)[0]
    if n > 65536 or s.start + 4 + 4 * n > s.end:
        raise RWError("material count %d doesn't fit" % n)
    refs = struct.unpack_from("<%di" % n, data, s.start + 4)
    chunks = [k for k in rw.children(data, s.end, c.end) if k.type == rw.MATERIAL]
    out, ci = [], 0
    for r in refs:
        if r < 0:                           # -1: a material of its own, the next Material chunk
            if ci >= len(chunks):
                d.warnings.append("Missing materials: %d listed, %d found" % (n, len(chunks)))
                break
            out.append(_guard(d, "material", _material, data, chunks[ci], d) or Material())
            ci += 1
        else:                               # the same material as an earlier entry
            out.append(out[r] if r < len(out) else Material())
    return out


def _material(data, c, d):
    m = Material()
    s = rw.find(data, c.start, c.end, rw.STRUCT)
    if s is None or s.size < 16:
        raise RWError("no material data")
    m.flags = struct.unpack_from("<I", data, s.start)[0]
    m.color = tuple(data[s.start + 4:s.start + 8])
    m.textured = bool(struct.unpack_from("<I", data, s.start + 12)[0])
    if s.size >= 28:
        m.surface = struct.unpack_from("<3f", data, s.start + 16)
    for k in rw.children(data, s.end, c.end):
        if k.type == rw.TEXTURE and m.texture is None:
            m.texture = _texture(data, k)
        elif k.type == rw.EXTENSION:
            for x in rw.children(data, k.start, k.end):
                if x.type == rw.MATFX_PLG:
                    _guard(d, "material effects", _matfx, data, x, m)
                elif x.type == rw.SPECULAR_MAT and x.size >= 4:
                    lvl = struct.unpack_from("<f", data, x.start)[0]
                    m.specular = (lvl, rw.cstr(data[x.start + 4:min(x.end, x.start + 28)]))
                elif x.type == rw.REFLECTION_MAT and x.size >= 20:
                    m.reflection = struct.unpack_from("<5f", data, x.start)
                elif x.type == rw.UV_ANIM_PLG:
                    _guard(d, "UV animation", _uv_anim_plg, data, x, m)
                elif x.type == rw.USER_DATA_PLG:
                    m.user_data = _guard(d, "user data", _user_data, data, x)
    return m


def _texture(data, c):
    t = Texture()
    strings = []
    for k in rw.children(data, c.start, c.end):
        if k.type == rw.STRUCT and k.size >= 2:
            t.filters, t.addressing = data[k.start], data[k.start + 1]
        elif k.type in (rw.STRING, rw.UNICODE_STRING):
            strings.append(rw.string_chunk(data, k))
    if strings:
        t.name = strings[0]
    if len(strings) > 1:
        t.mask = strings[1]
    return t


def _matfx(data, x, m):
    """MatFX: the effect type, then two effect slots (bump map, environment map, dual texture, UV
    transform); env maps are how GTA's shiny car paint and glass work."""
    rw.need(data, x.start, 4, "material effects")
    m.matfx = struct.unpack_from("<I", data, x.start)[0]
    pos = x.start + 4
    for _slot in range(2):
        if pos + 4 > x.end:
            break
        etype = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        if etype == 1:                                      # bump map
            intensity, has = struct.unpack_from("<fI", data, pos)
            pos += 8
            bump, pos = _fx_texture(data, pos, x.end, has)
            has_h = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            height, pos = _fx_texture(data, pos, x.end, has_h)
            m.bump_map = (intensity, bump, height)
        elif etype == 2:                                    # environment map
            coef, fb, has = struct.unpack_from("<fII", data, pos)
            pos += 12
            tex, pos = _fx_texture(data, pos, x.end, has)
            m.env_map = (coef, bool(fb), tex)
        elif etype == 4:                                    # dual texture
            src, dst, has = struct.unpack_from("<3I", data, pos)
            pos += 12
            tex, pos = _fx_texture(data, pos, x.end, has)
            m.dual = (src, dst, tex)
        # 0 = no effect, 5 = UV transform (no data); anything else: nothing more we can read
        elif etype not in (0, 5):
            break


def _fx_texture(data, pos, end, has):
    if not has:
        return None, pos
    k = rw.chunk_at(data, pos, end)
    if k is None or k.type != rw.TEXTURE:
        raise RWError("effect texture missing")
    return _texture(data, k), k.end


def _uv_anim_plg(data, x, m):
    s = rw.find(data, x.start, x.end, rw.STRUCT)
    if s is None or s.size < 4:
        return
    mask = struct.unpack_from("<I", data, s.start)[0]
    pos = s.start + 4
    for _bit in range(bin(mask & 0xFF).count("1")):        # one name per channel used
        if pos + 32 > s.end:
            break
        m.uv_anims.append(rw.cstr(data[pos:pos + 32]))
        pos += 32


def _uv_anim_dict(data, c, d):
    for k in rw.children(data, c.start, c.end):
        if k.type != rw.ANIM_ANIMATION or k.size < 88:
            continue
        a = UVAnim()
        # version, type id, number of frames, flags, duration, unused, name[32], node-to-UV table, frames
        _type_id, nframes, a.flags, a.duration = struct.unpack_from("<iiif", data, k.start + 4)
        a.name = rw.cstr(data[k.start + 24:k.start + 56])
        a.node_to_uv = list(struct.unpack_from("<8f", data, k.start + 56))
        pos = k.start + 88
        for _ in range(min(nframes, 65536)):
            if pos + 32 > k.end:
                break
            t = struct.unpack_from("<7fi", data, pos)
            a.frames.append((t[0], t[1:7], t[7]))
            pos += 32
        d.uv_anims.append(a)


# ------------------------------------------------------------------------------------------ atomic

def _atomic(data, c, d):
    a = Atomic()
    s = rw.find(data, c.start, c.end, rw.STRUCT)
    if s is None or s.size < 12:
        raise RWError("no atomic data")
    if s.size >= 16:
        a.frame, a.geometry, a.flags = struct.unpack_from("<3I", data, s.start)
    else:                                   # old files: the geometry follows inside the atomic
        a.frame, a.flags = struct.unpack_from("<2I", data, s.start)
        a.geometry = -1
    for k in rw.children(data, s.end, c.end):
        if k.type == rw.GEOMETRY and a.geometry == -1:
            geo = _geometry(data, k, d)
            d.geometries.append(geo)
            a.geometry = len(d.geometries) - 1
        elif k.type == rw.EXTENSION:
            for x in rw.children(data, k.start, k.end):
                if x.type == rw.RIGHT_TO_RENDER and x.size >= 8:
                    a.right_to_render = struct.unpack_from("<2I", data, x.start)
                elif x.type == rw.PIPELINE_SET and x.size >= 4:
                    a.pipeline = struct.unpack_from("<I", data, x.start)[0]
                elif x.type == rw.SKYGFX and x.size >= 1:
                    a.sky_gfx = data[x.start]
                elif x.type == rw.SKIN_PLG:
                    _guard(d, "skin", _legacy_skin, data, x, a, d)
    if a.geometry < 0 or a.geometry >= len(d.geometries):
        d.warnings.append("An atomic points at a missing geometry and was left out")
        return
    if a.frame >= len(d.frames) and d.frames:
        d.warnings.append("An atomic points at a missing frame and was left out")
        return
    d.atomics.append(a)


def _legacy_skin(data, x, a, d):
    """Skin PLG inside an atomic (RenderWare before 3.3): bone list, weights and matrices together."""
    if a.geometry >= len(d.geometries) or a.frame >= len(d.frames):
        return
    geo, frame = d.geometries[a.geometry], d.frames[a.frame]
    nb, n = struct.unpack_from("<2I", data, x.start)
    if nb > 256 or n != geo.num_verts:
        raise RWError("old skin data doesn't match its geometry")
    sk = Skin()
    sk.num_bones, sk.max_weights = nb, 4
    pos = x.start + 8
    rw.need(data, pos, 20 * n + 76 * nb, "old skin")
    sk.indices = np.frombuffer(data, np.uint8, 4 * n, pos).reshape(n, 4)
    pos += 4 * n
    sk.weights = np.frombuffer(data, np.float32, 4 * n, pos).reshape(n, 4)
    pos += 16 * n
    bones = []
    for _ in range(nb):
        bid, idx, typ = struct.unpack_from("<3i", data, pos)
        bones.append((bid, idx, typ & 3))
        m = list(struct.unpack_from("<16f", data, pos + 12))
        m[3] = m[7] = m[11] = 0.0
        m[15] = 1.0
        sk.inverse.append(m)
        pos += 76
    geo.skin = sk
    frame.hanim = bones
    if frame.bone_id is None:
        frame.bone_id = 0
