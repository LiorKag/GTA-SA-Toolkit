# GTA SA Toolkit - IFP (GTA animation package) reader / writer
# Pure python (no bpy / mathutils) so it can be tested outside Blender.
#
# Supported:
#   ANP3  (GTA San Andreas)          read + write
#   ANPK  (GTA III / Vice City / SA)  read + write
#
# Conventions used by this module (all "file space", i.e. RenderWare frame space):
#   Keyframe.time : seconds (float)
#   Keyframe.rot  : quaternion (w, x, y, z) = local rotation of the frame relative to its parent
#   Keyframe.pos  : (x, y, z) local translation relative to the parent frame, or None
#   Keyframe.scale: (x, y, z) or None
#
# ANPK files store the conjugate quaternion; ANP3 stores it as-is.  Both are
# normalised to the same convention on read and converted back on write.

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

ANP3_ROT_SCALE = 4096.0
ANP3_POS_SCALE = 1024.0
ANP3_TIME_SCALE = 60.0     # ANP3 key times are stored in 1/60 s


@dataclass
class Keyframe:
    time: float
    rot: Tuple[float, float, float, float]
    pos: Optional[Tuple[float, float, float]] = None
    scale: Optional[Tuple[float, float, float]] = None


@dataclass
class BoneAnim:
    name: str
    bone_id: int = -1            # -1 = unknown / not stored
    keyframes: List[Keyframe] = field(default_factory=list)
    # ANPK sibling data (only kept for round-tripping GTA3/VC files)
    sibling: Tuple[int, int] = (0, 0)

    @property
    def has_pos(self):
        return any(k.pos is not None for k in self.keyframes)

    @property
    def has_scale(self):
        return any(k.scale is not None for k in self.keyframes)


@dataclass
class Animation:
    name: str
    bones: List[BoneAnim] = field(default_factory=list)

    @property
    def duration(self):
        t = 0.0
        for b in self.bones:
            if b.keyframes:
                t = max(t, b.keyframes[-1].time)
        return t


@dataclass
class IfpFile:
    version: str = "ANP3"        # 'ANP3' or 'ANPK'
    name: str = ""
    animations: List[Animation] = field(default_factory=list)

    def find(self, name):
        low = name.lower()
        for a in self.animations:
            if a.name.lower() == low:
                return a
        return None

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            return cls.from_bytes(f.read())

    @classmethod
    def from_bytes(cls, data):
        r = _Reader(data)
        magic = r.fourcc()
        if magic == "ANP3":
            return _read_anp3(r)
        if magic == "ANPK":
            return _read_anpk(r)
        raise ValueError("Not an IFP file (header %r)" % magic)

    def to_bytes(self, pad=True):
        if self.version == "ANP3":
            out = _write_anp3(self)
        elif self.version == "ANPK":
            out = _write_anpk(self)
        else:
            raise ValueError("Unknown IFP version %r" % self.version)
        if pad and len(out) % 2048:
            out += b"\0" * (2048 - len(out) % 2048)
        return out

    def save(self, path):
        with open(path, "wb") as f:
            f.write(self.to_bytes())

    def list_names(self):
        """Cheap: returns [(name, num_bones, duration)]"""
        return [(a.name, len(a.bones), a.duration) for a in self.animations]


def scan_names(data):
    """[(animation name, duration in s)] without decoding keyframes: ANP3 skips over the key data and
    reads only each bone's last key time (the IFP browser's all-animations index); other versions
    fall back to a full read."""
    if data[:4] != b"ANP3":
        return [(a.name, a.duration) for a in IfpFile.from_bytes(data).animations]
    unpack = struct.unpack_from
    p = 4 + 4 + 24
    count = unpack("<I", data, p)[0]
    p += 4
    out = []
    for _ in range(count):
        name = _cstr(data[p:p + 24])
        num_bones = unpack("<I", data, p + 24)[0]
        p += 24 + 12
        dur = 0.0
        for _ in range(num_bones):
            ftype, nkeys, _bone = unpack("<3i", data, p + 24)
            p += 24 + 12
            size = 16 if ftype == 4 else 10
            if nkeys > 0:
                dur = max(dur, unpack("<h", data, p + (nkeys - 1) * size + 8)[0] / ANP3_TIME_SCALE)
            p += nkeys * size
        out.append((name, dur))
    return out


# ======================================================================
class _Reader:
    __slots__ = ("d", "p")

    def __init__(self, data, pos=0):
        self.d = data
        self.p = pos

    def read(self, n):
        v = self.d[self.p:self.p + n]
        if len(v) < n:
            raise EOFError("Unexpected end of IFP data")
        self.p += n
        return v

    def fourcc(self):
        return self.read(4).decode("latin-1")

    def u32(self):
        v = struct.unpack_from("<I", self.d, self.p)[0]
        self.p += 4
        return v

    def i32(self):
        v = struct.unpack_from("<i", self.d, self.p)[0]
        self.p += 4
        return v

    def fixed_str(self, n):
        return _cstr(self.read(n))

    def unpack(self, fmt):
        v = struct.unpack_from(fmt, self.d, self.p)
        self.p += struct.calcsize(fmt)
        return v


def _cstr(b):
    i = b.find(b"\0")
    if i >= 0:
        b = b[:i]
    return b.decode("latin-1", errors="replace")


def _fixed(s, n):
    b = s.encode("latin-1", errors="replace")[:n - 1]
    return b + b"\0" * (n - len(b))


def _qnorm(q):
    w, x, y, z = q
    l = (w * w + x * x + y * y + z * z) ** 0.5
    if l < 1e-8:
        return (1.0, 0.0, 0.0, 0.0)
    return (w / l, x / l, y / l, z / l)


def _clamp16(v):
    v = int(round(v))
    return -32768 if v < -32768 else 32767 if v > 32767 else v


# ---------------------------------------------------------------- ANP3
def _read_anp3(r):
    r.u32()                              # size to end
    ifp = IfpFile("ANP3", r.fixed_str(24))
    count = r.u32()
    for _ in range(count):
        anim = Animation(r.fixed_str(24))
        num_bones, _frame_data_size, _unk = r.unpack("<3I")
        for _ in range(num_bones):
            bname = r.fixed_str(24)
            ftype, nkeys, bone_id = r.unpack("<3i")
            has_t = ftype == 4
            bone = BoneAnim(bname, bone_id)
            for _ in range(nkeys):
                qx, qy, qz, qw, t = r.unpack("<5h")
                key = Keyframe(t / ANP3_TIME_SCALE,
                               _qnorm((qw / ANP3_ROT_SCALE, qx / ANP3_ROT_SCALE,
                                       qy / ANP3_ROT_SCALE, qz / ANP3_ROT_SCALE)))
                if has_t:
                    px, py, pz = r.unpack("<3h")
                    key.pos = (px / ANP3_POS_SCALE, py / ANP3_POS_SCALE, pz / ANP3_POS_SCALE)
                bone.keyframes.append(key)
            anim.bones.append(bone)
        ifp.animations.append(anim)
    return ifp


def _write_anp3(ifp):
    body = bytearray()
    for anim in ifp.animations:
        bones_blob = bytearray()
        frame_bytes = 0
        for b in anim.bones:
            has_t = b.has_pos
            bones_blob += _fixed(b.name, 24)
            bones_blob += struct.pack("<3i", 4 if has_t else 3, len(b.keyframes), b.bone_id)
            for k in b.keyframes:
                w, x, y, z = _qnorm(k.rot)
                bones_blob += struct.pack(
                    "<5h",
                    _clamp16(x * ANP3_ROT_SCALE), _clamp16(y * ANP3_ROT_SCALE),
                    _clamp16(z * ANP3_ROT_SCALE), _clamp16(w * ANP3_ROT_SCALE),
                    _clamp16(k.time * ANP3_TIME_SCALE))
                frame_bytes += 10
                if has_t:
                    p = k.pos if k.pos is not None else (0.0, 0.0, 0.0)
                    bones_blob += struct.pack("<3h", *(_clamp16(c * ANP3_POS_SCALE) for c in p))
                    frame_bytes += 6
        body += _fixed(anim.name, 24)
        body += struct.pack("<3I", len(anim.bones), frame_bytes, 1)
        body += bones_blob
    head = _fixed(ifp.name, 24) + struct.pack("<I", len(ifp.animations))
    payload = head + body
    return b"ANP3" + struct.pack("<I", len(payload)) + payload


# ---------------------------------------------------------------- ANPK
def _read_anpk(r):
    end_of_file = r.u32() + r.p
    assert r.fourcc() == "INFO"
    info_len = r.u32()
    info_start = r.p
    count = r.i32()
    name = _cstr(r.d[r.p:info_start + info_len])
    r.p = info_start + ((info_len + 3) & ~3)
    ifp = IfpFile("ANPK", name)

    for _ in range(count):
        assert r.fourcc() == "NAME"
        nlen = r.u32()
        aname = _cstr(r.d[r.p:r.p + nlen])
        r.p += (nlen + 3) & ~3
        anim = Animation(aname)

        assert r.fourcc() == "DGAN"
        dgan_len = r.u32()
        dgan_end = r.p + dgan_len
        assert r.fourcc() == "INFO"
        ilen = r.u32()
        istart = r.p
        nbones = r.i32()
        r.p = istart + ((ilen + 3) & ~3)

        for _ in range(nbones):
            assert r.fourcc() == "CPAN"
            cpan_len = r.u32()
            cpan_end = r.p + cpan_len
            assert r.fourcc() == "ANIM"
            anim_len = r.u32()
            anim_start = r.p
            bname = r.fixed_str(28)
            nkeys = r.i32()
            r.i32()                           # unknown / 0
            r.i32()                           # last frame index
            bone_id, sib = -1, (0, 0)
            if anim_len == 44:
                bone_id = r.i32()
            elif anim_len >= 48:
                sib = (r.i32(), r.i32())
            r.p = anim_start + anim_len
            bone = BoneAnim(bname, bone_id, sibling=sib)
            if nkeys:
                kfrm = r.fourcc()
                r.u32()
                has_t = kfrm[2] == "T"
                has_s = kfrm[3] == "S"
                for _ in range(nkeys):
                    qx, qy, qz, qw = r.unpack("<4f")
                    key = Keyframe(0.0, _qnorm((qw, -qx, -qy, -qz)))   # stored conjugated
                    if has_t:
                        key.pos = r.unpack("<3f")
                    if has_s:
                        key.scale = r.unpack("<3f")
                    key.time = r.unpack("<f")[0]
                    bone.keyframes.append(key)
            r.p = cpan_end
            anim.bones.append(bone)
        r.p = dgan_end
        ifp.animations.append(anim)
        if r.p >= end_of_file:
            break
    return ifp


def _padded_str(s):
    b = s.encode("latin-1", errors="replace") + b"\0"
    return b + b"\0" * ((4 - len(b) % 4) % 4), len(s) + 1


def _write_anpk(ifp):
    out = bytearray()
    for anim in ifp.animations:
        nb, nlen = _padded_str(anim.name)
        out += b"NAME" + struct.pack("<I", nlen) + nb
        dgan = bytearray()
        dgan += b"INFO" + struct.pack("<Iii", 8, len(anim.bones), 0)
        for b in anim.bones:
            has_t, has_s = b.has_pos, b.has_scale
            kfrm = "KR" + ("T" if has_t else "0") + ("S" if has_s else "0")
            keys = bytearray()
            for k in b.keyframes:
                w, x, y, z = _qnorm(k.rot)
                keys += struct.pack("<4f", -x, -y, -z, w)
                if has_t:
                    keys += struct.pack("<3f", *(k.pos or (0.0, 0.0, 0.0)))
                if has_s:
                    keys += struct.pack("<3f", *(k.scale or (1.0, 1.0, 1.0)))
                keys += struct.pack("<f", k.time)
            n = len(b.keyframes)
            if b.bone_id >= 0:
                anim_blk = _fixed(b.name, 28) + struct.pack("<4i", n, 0, max(n - 1, 0), b.bone_id)
            else:
                anim_blk = _fixed(b.name, 28) + struct.pack("<5i", n, 0, max(n - 1, 0), *b.sibling)
            cpan = b"ANIM" + struct.pack("<I", len(anim_blk)) + anim_blk
            if n:
                cpan += kfrm.encode() + struct.pack("<I", len(keys)) + keys
            dgan += b"CPAN" + struct.pack("<I", len(cpan)) + cpan
        out += b"DGAN" + struct.pack("<I", len(dgan)) + dgan
    nb, nlen = _padded_str(ifp.name)
    info = struct.pack("<i", len(ifp.animations)) + nb
    payload = b"INFO" + struct.pack("<I", nlen + 4) + info + out
    return b"ANPK" + struct.pack("<I", len(payload)) + payload
