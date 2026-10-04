# GTA SA Toolkit - a quick look inside a DFF without importing it (custom / modded models):
# frame names, whether a mesh is skinned (Skin plugin) or has a bone hierarchy (HAnim), texture names.
# Walks the RenderWare chunk tree (type, size, version); only container chunks are entered.
import struct

CLUMP, STRUCT, STRING, EXTENSION = 0x10, 0x01, 0x02, 0x03
TEXTURE, MATERIAL, MATERIAL_LIST, FRAME_LIST, GEOMETRY, GEOMETRY_LIST, ATOMIC = 0x06, 0x07, 0x08, 0x0E, 0x0F, 0x1A, 0x14
FRAME_NAME, SKIN, HANIM = 0x253F2FE, 0x116, 0x11E
_CONTAINERS = {CLUMP, EXTENSION, TEXTURE, MATERIAL, MATERIAL_LIST, FRAME_LIST, GEOMETRY, GEOMETRY_LIST, ATOMIC}

# frames only vehicles have: wheels / chassis (cars, bikes, planes, helis, trains), boat hulls and props
# (boats have no wheels), seat dummies (every vehicle the player can sit in)
VEHICLE_FRAMES = ("wheel_lf_dummy", "wheel_rf_dummy", "wheel_lb_dummy", "wheel_rb_dummy", "chassis",
                  "chassis_dummy", "forks_front", "wheel_front", "wheel_rear", "boat", "boat_hi", "boat_vlo",
                  "boat_moving", "boat_moving_hi", "boat_rudder", "moving_prop", "static_prop",
                  "ped_frontseat", "ped_backseat")


# The GTA SA ped skeleton's bone ids (HAnim node ids: what IFP animations and SA-MP toys use), from the
# game's bmyst.dff; 348 of the game's peds have all 32. Names vary a lot (" Spine1" / " Spine 1"...).
GTA_BONE_IDS = frozenset((0, 1, 2, 3, 4, 5, 6, 7, 8, 21, 22, 23, 24, 25, 26, 31, 32, 33, 34, 35, 36, 41, 42, 43,
                          44, 51, 52, 53, 54, 201, 301, 302))
PED_MIN_BONES = 20      # a ped has at least this many of them; animated props (doors, nets, the parachute) don't


class DffInfo:
    __slots__ = ("frames", "skinned", "hanim", "textures", "bone_ids", "error")

    def __init__(self):
        self.frames, self.textures, self.bone_ids = [], [], []     # bone_ids: HAnim node ids (what IFPs use)
        self.skinned = self.hanim = False
        self.error = ""

    def kind(self):
        """'PED' (the GTA skeleton: >= PED_MIN_BONES of GTA_BONE_IDS), 'VEHICLE' (wheel / chassis / boat /
        seat frames) or 'OBJECT' (everything else, animated props with bones too)."""
        if len(GTA_BONE_IDS.intersection(self.bone_ids)) >= PED_MIN_BONES:
            return 'PED'
        low = {f.lower() for f in self.frames}
        if any(v in low for v in VEHICLE_FRAMES):
            return 'VEHICLE'
        return 'OBJECT'

    def vehicle_kind(self):
        """'BIKE', 'BOAT', 'HELI', 'PLANE' or 'CAR' from the frames (for a vehicle's default 'based on')."""
        low = {f.lower() for f in self.frames}
        if "forks_front" in low or ("wheel_front" in low and "wheel_rear" in low):
            return 'BIKE'
        if "moving_rotor" in low or "static_rotor" in low:
            return 'HELI'
        wheels = any(w in low for w in ("wheel_lf_dummy", "wheel_rf_dummy", "wheel_lb_dummy", "wheel_rb_dummy"))
        if not wheels and low & {"boat", "boat_hi", "boat_vlo", "boat_moving"}:
            return 'BOAT'
        if wheels and low & {"moving_prop", "static_prop", "elevators", "rudder"}:
            return 'PLANE'
        return 'CAR'


def txd_names(data):
    """Texture names in a TXD's bytes (native texture chunks), lower case; [] when unreadable."""
    out = []

    def walk(pos, end, depth):
        while pos + 12 <= end and depth < 4:
            ctype, size, _ver = struct.unpack_from("<3I", data, pos)
            if ctype == 0x15 and size >= 52:          # texture native: struct(12) + platform, filter, name[32]
                out.append(data[pos + 32:pos + 64].split(b"\0", 1)[0].decode("latin-1", "replace").lower())
            elif ctype == 0x16:                        # texture dictionary
                walk(pos + 12, pos + 12 + size, depth + 1)
            pos += 12 + size
    try:
        walk(0, len(data), 0)
    except (struct.error, ValueError):
        pass
    return out


def probe(data):
    """DffInfo of a DFF's bytes (never raises: a broken file gives .error and whatever was found)."""
    info = DffInfo()
    try:
        _walk(data, 0, len(data), info, 0)
    except (struct.error, IndexError, ValueError) as e:
        info.error = str(e) or type(e).__name__
    if not info.frames and not info.error:
        info.error = "no frames found (not a DFF?)"
    return info


def _walk(d, pos, end, info, depth):
    if depth > 12:
        return
    while pos + 12 <= end:
        ctype, size, _ver = struct.unpack_from("<3I", d, pos)
        body, nxt = pos + 12, pos + 12 + size
        if nxt > end or size > len(d):
            raise ValueError("chunk 0x%X runs past the end" % ctype)
        if ctype == FRAME_NAME:
            info.frames.append(d[body:nxt].split(b"\0", 1)[0].decode("latin-1", "replace"))
        elif ctype == SKIN:
            info.skinned = True
        elif ctype == HANIM:
            info.hanim = True
            if size >= 12:                  # version, node id, number of nodes (root: the whole hierarchy)
                _v, node_id, _n = struct.unpack_from("<3i", d, body)
                if node_id not in info.bone_ids:
                    info.bone_ids.append(node_id)
        elif ctype == TEXTURE:
            _texture(d, body, nxt, info)
        elif ctype in _CONTAINERS:
            _walk(d, body, nxt, info, depth + 1)
        pos = nxt


def _texture(d, pos, end, info):
    """A texture chunk: struct, then the texture name string (then the mask string, extension)."""
    while pos + 12 <= end:
        ctype, size, _ver = struct.unpack_from("<3I", d, pos)
        if ctype == STRING:
            name = d[pos + 12:pos + 12 + size].split(b"\0", 1)[0].decode("latin-1", "replace")
            if name and name not in info.textures:
                info.textures.append(name)
            return
        pos += 12 + size
