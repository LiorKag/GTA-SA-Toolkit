# GTA SA Toolkit - COL collision reader (COLL = GTA III / VC, COL2 / COL3 / COL4 = SA): spheres, boxes,
# the collision mesh and SA's shadow mesh, per model. A .col file (and a vehicle's embedded collision)
# is several models back to back. Written from the GTAMods wiki (Collision File).
#
# load(data, embedded=False) -> [ColModel] (raises only rw.RWError; a broken model after good ones ends
# the list and adds a note to the last good one's .warnings). embedded=True (collision inside a DFF)
# also accepts old headless data: a COLL body without its 32-byte header (a few game models have it).
import struct

import numpy as np

from . import rw
from .rw import RWError

VERSIONS = {b"COLL": 1, b"COL2": 2, b"COL3": 3, b"COL4": 4}


class ColModel:
    __slots__ = ("name", "model_id", "version", "bounds", "spheres", "boxes", "verts", "faces", "face_mats",
                 "face_surfaces", "face_groups", "shadow_verts", "shadow_faces", "shadow_surfaces", "flags",
                 "warnings")

    def __init__(self):
        self.name, self.model_id, self.version, self.flags = "", 0, 0, 0
        self.bounds = None              # (min xyz, max xyz, centre xyz, radius)
        # a surface = (material, flags, brightness, light); COL2+ faces store only material and light
        # (flags 0, brightness 1, like DragonFF)
        self.spheres = []               # (centre xyz, radius, material, surface)
        self.boxes = []                 # (min xyz, max xyz, material, surface)
        self.verts = np.zeros((0, 3), np.float32)
        self.faces = np.zeros((0, 3), np.int32)
        self.face_mats = np.zeros(0, np.int32)       # surface material per face
        self.face_surfaces = np.zeros((0, 4), np.int32)
        self.face_groups = []           # COL2+ with flag 8: (min xyz, max xyz, first face, last face)
        self.shadow_verts = np.zeros((0, 3), np.float32)
        self.shadow_faces = np.zeros((0, 3), np.int32)
        self.shadow_surfaces = np.zeros((0, 4), np.int32)
        self.warnings = []


def load(data, embedded=False):
    out, pos, end = [], 0, len(data)
    if embedded and len(data) >= 40 and bytes(data[:4]) not in VERSIONS:
        try:
            m = _model(data, -32, end, 1)
        except (RWError, struct.error, ValueError, IndexError) as e:
            raise RWError("Embedded collision can't be read: %s" % (e or type(e).__name__))
        m.name = "col"
        return [m]
    while pos + 32 <= end:
        magic = bytes(data[pos:pos + 4])
        if magic not in VERSIONS:
            if not out:
                raise RWError("Not a collision file")
            break                                   # padding at the end of the file
        size = struct.unpack_from("<I", data, pos + 4)[0]
        nxt = pos + 8 + size
        try:
            if nxt > end:
                raise RWError("the file ends early")
            out.append(_model(data, pos, nxt, VERSIONS[magic]))
        except (RWError, struct.error, ValueError, IndexError) as e:
            msg = "Collision model at byte %d skipped: %s" % (pos, e or type(e).__name__)
            if not out:
                raise RWError("Can't read this collision file: %s" % (e or type(e).__name__))
            out[-1].warnings.append(msg)
            break
        pos = nxt
    if not out:
        raise RWError("Not a collision file")
    return out


def _model(data, pos, end, version):
    m = ColModel()
    m.version = version
    if pos >= 0:                                     # pos = -32: headless (no magic / size / name / id)
        m.name = rw.cstr(data[pos + 8:pos + 30])
        m.model_id = struct.unpack_from("<H", data, pos + 30)[0]
    p = pos + 32
    if version == 1:
        r, cx, cy, cz, x0, y0, z0, x1, y1, z1 = struct.unpack_from("<10f", data, p)
        m.bounds = ((x0, y0, z0), (x1, y1, z1), (cx, cy, cz), r)
        p += 40
        n = _count(data, p, end, 20)
        for i in range(n):
            r, x, y, z, *sf = struct.unpack_from("<4f4B", data, p + 4 + 20 * i)
            m.spheres.append(((x, y, z), r, sf[0], tuple(sf)))
        p += 4 + 20 * n
        p += 4                                       # unused count
        n = _count(data, p, end, 28)
        for i in range(n):
            v = struct.unpack_from("<6f4B", data, p + 4 + 28 * i)
            m.boxes.append((v[0:3], v[3:6], v[6], tuple(v[6:10])))
        p += 4 + 28 * n
        n = _count(data, p, end, 12)
        m.verts = np.frombuffer(data, np.float32, 3 * n, p + 4).reshape(n, 3).copy()
        p += 4 + 12 * n
        n = _count(data, p, end, 16)
        raw = np.frombuffer(data, np.uint32, 4 * n, p + 4).reshape(n, 4)
        m.faces = raw[:, :3].astype(np.int32)
        m.face_mats = (raw[:, 3] & 0xFF).astype(np.int32)
        sb = np.ascontiguousarray(raw[:, 3]).view(np.uint8).reshape(n, 4)   # material, flags, brightness, light
        m.face_surfaces = sb.astype(np.int32)
    else:
        v = struct.unpack_from("<10f", data, p)
        m.bounds = (v[0:3], v[3:6], v[6:9], v[9])
        p += 40
        (ns, nb, nf, _nl, flags, o_sph, o_box, _o_lines, o_verts, o_faces,
         _o_tri) = struct.unpack_from("<HHHBxIIIIIII", data, p)
        m.flags = flags
        hdr = p + 36
        nsf = o_sv = o_sf = 0
        if version >= 3:
            nsf, o_sv, o_sf = struct.unpack_from("<3I", data, hdr)
        base = pos + 4                                # offsets count from just after the magic
        if ns:
            _fits(o_sph + base, 20 * ns, end)
            for i in range(ns):
                x, y, z, r, *sf = struct.unpack_from("<4f4B", data, base + o_sph + 20 * i)
                m.spheres.append(((x, y, z), r, sf[0], tuple(sf)))
        if nb:
            _fits(o_box + base, 28 * nb, end)
            for i in range(nb):
                b = struct.unpack_from("<6f4B", data, base + o_box + 28 * i)
                m.boxes.append((b[0:3], b[3:6], b[6], tuple(b[6:10])))
        if nf:
            _fits(o_faces + base, 8 * nf, end)
            raw = np.frombuffer(data, np.uint16, 4 * nf, base + o_faces).reshape(nf, 4)
            m.faces = raw[:, :3].astype(np.int32)
            m.face_mats = (raw[:, 3] & 0xFF).astype(np.int32)
            m.face_surfaces = _surfaces(raw[:, 3])
            if flags & 8:
                m.face_groups = _face_groups(data, base + o_faces, pos)
            nv = int(m.faces.max()) + 1
            _fits(o_verts + base, 6 * nv, end)
            m.verts = np.frombuffer(data, np.int16, 3 * nv, base + o_verts).reshape(nv, 3).astype(np.float32) / 128.0
        if version >= 3 and flags & 16 and nsf:
            _fits(o_sf + base, 8 * nsf, end)
            raw = np.frombuffer(data, np.uint16, 4 * nsf, base + o_sf).reshape(nsf, 4)
            m.shadow_faces = raw[:, :3].astype(np.int32)
            m.shadow_surfaces = _surfaces(raw[:, 3])
            nv = (o_sf - o_sv) // 6
            if nv <= 0 or int(m.shadow_faces.max()) >= nv:
                nv = int(m.shadow_faces.max()) + 1
            _fits(o_sv + base, 6 * nv, end)
            m.shadow_verts = np.frombuffer(data, np.int16, 3 * nv, base + o_sv).reshape(nv, 3).astype(np.float32) / 128.0
    return m


def _surfaces(word):
    """COL2+ face: material (low byte), light (high byte) -> (material, 0, 1, light) per face."""
    w = word.astype(np.int32)
    out = np.zeros((len(w), 4), np.int32)
    out[:, 0], out[:, 2], out[:, 3] = w & 0xFF, 1, (w >> 8) & 0xFF
    return out


def _face_groups(data, faces_at, start):
    """Face groups sit just before the faces: the groups, then their count (u32)."""
    if faces_at - 4 < start:
        return []
    n = struct.unpack_from("<I", data, faces_at - 4)[0]
    first = faces_at - 4 - 28 * n
    if n > 65536 or first < start:
        return []
    out = []
    for i in range(n):
        v = struct.unpack_from("<6f2H", data, first + 28 * i)
        out.append((v[0:3], v[3:6], v[6], v[7]))
    return out


def _count(data, p, end, item):
    n = struct.unpack_from("<I", data, p)[0]
    _fits(p + 4, n * item, end)
    return n


def _fits(start, n, end):
    if start < 0 or start + n > end:
        raise RWError("data runs past the end of the model")
