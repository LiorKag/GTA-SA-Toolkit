# GTA SA Toolkit - TXD (RenderWare texture dictionary) reader for PC textures: DXT1/3/5 (and the
# premultiplied DXT2/4), 8888/888/565/555/1555/4444, luminance, and 4/8-bit paletted. Decoding is numpy
# (formats/dxt.py for the compressed ones). Written from the GTAMods wiki (Texture Native Struct,
# Texture Dictionary). Console dictionaries (PS2, Xbox, GameCube, PSP) give a clear message instead.
#
# load(data) -> Txd (raises only rw.RWError); Txd.textures[i].decode(level) -> (h, w, 4) uint8 RGBA,
# rows top to bottom, or raises RWError for a format it doesn't know.
import struct

import numpy as np

from . import dxt, rw
from .rw import RWError

D3D8, D3D9 = 8, 9
PS2_FOURCC, XBOX, GC_TAG = 0x00325350, 5, 6
FOURCC = {0x31545844: 1, 0x32545844: 2, 0x33545844: 3, 0x34545844: 4, 0x35545844: 5}   # 'DXT1'...'DXT5'
D3D_8888, D3D_888, D3D_565, D3D_555, D3D_1555, D3D_4444, D3D_L8, D3D_A8L8 = 21, 22, 23, 24, 25, 26, 50, 51
R_1555, R_565, R_4444, R_LUM, R_8888, R_888, R_555 = 1, 2, 3, 4, 5, 6, 10
PAL_NONE, PAL_8, PAL_4 = 0, 1, 2
CONSOLE = {PS2_FOURCC: "PS2", XBOX: "Xbox", GC_TAG: "GameCube", 10: "PSP"}


class NativeTexture:
    __slots__ = ("name", "mask", "platform", "filters", "addressing", "raster_format", "d3d_format",
                 "width", "height", "depth", "levels", "raster_type", "props", "palette", "pixels")

    def __init__(self):
        self.name = self.mask = ""
        self.platform = self.filters = self.addressing = self.raster_format = self.d3d_format = 0
        self.width = self.height = self.depth = self.levels = self.raster_type = self.props = 0
        self.palette = b""
        self.pixels = []                # bytes per mip level

    # raster format bits
    def format_type(self):
        return (self.raster_format >> 8) & 0xF

    def palette_type(self):
        return (self.raster_format >> 13) & 0x3

    def dxt(self):
        """1-5 for DXT textures, else 0."""
        if self.platform == D3D8:
            return self.props if 1 <= self.props <= 5 else 0
        if self.platform == D3D9:
            return FOURCC.get(self.d3d_format, 0)
        return 0

    def has_alpha(self):
        if self.platform == D3D9:
            return bool(self.props & 1)
        return self.format_type() not in (R_565, R_LUM, R_888, R_555)

    def size(self, level=0):
        return max(self.width >> level, 1), max(self.height >> level, 1)

    def bytes_needed(self, level=0):
        """Bytes the pixels of a level take, from its size and format."""
        w, h = self.size(level)
        if self.palette:
            return (w * h + 1) // 2 if self.palette_type() != PAL_8 and self.depth == 4 else w * h
        kind = self.dxt()
        if kind:
            return ((w + 3) // 4) * ((h + 3) // 4) * (8 if kind == 1 else 16)
        return w * h * max(self.depth // 8, 1)

    def decode(self, level=0):
        if level >= len(self.pixels):
            raise RWError("%s: no mip level %d" % (self.name, level))
        w, h = self.size(level)
        px = self.pixels[level]
        need = self.bytes_needed(level)         # a broken size (would need gigabytes): refuse; the game
        if need > 1024 and len(px) * 4 < need:  # leaves its tiniest mip levels empty (decoded as zeros)
            raise RWError("%s: %dx%d doesn't fit its %d bytes of pixels" % (self.name, w, h, len(px)))
        if self.palette:
            return _paletted(self, px, w, h)
        kind = self.dxt()
        if kind:
            img = {1: dxt.bc1, 2: dxt.bc2, 3: dxt.bc2, 4: dxt.bc3, 5: dxt.bc3}[kind](px, w, h)
            if kind in (2, 4):
                img = _unpremultiply(img)
            return img
        f = self.d3d_format if self.platform == D3D9 else 0
        if f == D3D_8888:
            return _raw32(px, w, h, True)
        if f == D3D_888:
            return _raw32(px, w, h, False)
        if f in (D3D_565, D3D_555, D3D_1555, D3D_4444):
            return _raw16(px, w, h, {D3D_565: R_565, D3D_555: R_555, D3D_1555: R_1555, D3D_4444: R_4444}[f])
        if f == D3D_L8:
            return _lum(px, w, h, False)
        if f == D3D_A8L8:
            return _lum(px, w, h, True)
        t = self.format_type()
        if t == R_8888:
            return _raw32(px, w, h, True)
        if t == R_888:
            return _raw32(px, w, h, False)
        if t in (R_565, R_555, R_1555, R_4444):
            return _raw16(px, w, h, t)
        if t == R_LUM:
            return _lum(px, w, h, False)
        raise RWError("%s: texture format %d / %d is not supported" % (self.name, t, self.d3d_format))


class Txd:
    __slots__ = ("textures", "device", "version", "warnings")

    def __init__(self):
        self.textures, self.device, self.version, self.warnings = [], 0, 0, []

    def names(self):
        return [t.name for t in self.textures]


def load(data):
    t = Txd()
    try:
        _load(data, t)
    except RWError:
        raise
    except (struct.error, ValueError, IndexError, OverflowError, MemoryError) as e:
        raise RWError("Not a readable texture file (%s)" % (e or type(e).__name__))
    return t


def _load(data, t):
    first = rw.chunk_at(data, 0, len(data))
    if first is None:
        raise RWError("Not a texture file (too small)")
    if first.type != rw.TEXTURE_DICTIONARY:
        if first.type == rw.CLUMP:
            raise RWError("This is a model (.dff), not a texture dictionary")
        if first.type == 0x23:
            raise RWError("PS2 'PI' texture dictionary: only PC textures can be read")
        raise RWError("Not a texture file (unknown start 0x%X)" % first.type)
    t.version = first.version
    console = None
    for c in rw.children(data, first.start, first.end):
        if c.type == rw.STRUCT and c.size >= 4:
            _count, t.device = struct.unpack_from("<2H", data, c.start)
        elif c.type == rw.TEXTURE_NATIVE:
            s = rw.find(data, c.start, c.end, rw.STRUCT)
            if s is None or s.size < 4:
                t.warnings.append("A texture without data was left out")
                continue
            platform = struct.unpack_from("<I", data, s.start)[0]
            if platform not in (D3D8, D3D9):
                console = CONSOLE.get(platform, CONSOLE.get(platform >> 24, "unknown (%d)" % platform))
                continue
            try:
                t.textures.append(_native(data, s, platform))
            except (RWError, struct.error, ValueError, IndexError) as e:
                t.warnings.append("A texture was left out: %s" % (e or type(e).__name__))
    if console and not t.textures:
        raise RWError("%s textures: only PC texture dictionaries can be read" % console)
    if console:
        t.warnings.append("%s textures were left out" % console)


def _native(data, s, platform):
    rw.need(data, s.start, 88, "texture")
    x = NativeTexture()
    x.platform = platform
    x.filters, x.addressing = data[s.start + 4], data[s.start + 5]
    x.name = rw.cstr(data[s.start + 8:s.start + 40])
    x.mask = rw.cstr(data[s.start + 40:s.start + 72])
    (x.raster_format, x.d3d_format, x.width, x.height, x.depth, x.levels, x.raster_type,
     x.props) = struct.unpack_from("<IIHHBBBB", data, s.start + 72)
    pos = s.start + 88
    pt = x.palette_type()
    if pt != PAL_NONE:
        n = 1024 if pt == PAL_8 else (64 if x.depth == 4 else 128)
        rw.need(data, pos, n, "palette")
        x.palette = bytes(data[pos:pos + n])
        pos += n
    for _ in range(max(x.levels, 1)):
        if pos + 4 > s.end:
            break
        n = struct.unpack_from("<I", data, pos)[0]
        if pos + 4 + n > s.end:
            break
        x.pixels.append(bytes(data[pos + 4:pos + 4 + n]))
        pos += 4 + n
    if not x.pixels:
        raise RWError("%s has no pixels" % x.name)
    return x


# -------------------------------------------------------------------------------------- decoders

def _arr(px, n, dtype=np.uint8):
    b = np.frombuffer(px, dtype, min(n, len(px) // np.dtype(dtype).itemsize))
    if b.size < n:
        b = np.concatenate([b, np.zeros(n - b.size, dtype)])
    return b


def _raw32(px, w, h, alpha):
    a = _arr(px, w * h * 4).reshape(h, w, 4)
    out = a[:, :, [2, 1, 0, 3]].copy()
    if not alpha:
        out[:, :, 3] = 255
    return out


def _scale(v, bits):
    return (v * 0xFF // ((1 << bits) - 1)).astype(np.uint8)


def _raw16(px, w, h, kind):
    v = _arr(px, w * h, np.dtype("<u2")).astype(np.int32).reshape(h, w)
    out = np.empty((h, w, 4), np.uint8)
    if kind == R_565:
        out[..., 0], out[..., 1], out[..., 2] = _scale(v >> 11 & 31, 5), _scale(v >> 5 & 63, 6), _scale(v & 31, 5)
        out[..., 3] = 255
    elif kind == R_555:
        out[..., 0], out[..., 1], out[..., 2] = _scale(v >> 10 & 31, 5), _scale(v >> 5 & 31, 5), _scale(v & 31, 5)
        out[..., 3] = 255
    elif kind == R_1555:
        out[..., 0], out[..., 1], out[..., 2] = _scale(v >> 10 & 31, 5), _scale(v >> 5 & 31, 5), _scale(v & 31, 5)
        out[..., 3] = (v >> 15 & 1) * 255
    else:                                   # 4444
        out[..., 0], out[..., 1], out[..., 2] = _scale(v >> 8 & 15, 4), _scale(v >> 4 & 15, 4), _scale(v & 15, 4)
        out[..., 3] = _scale(v >> 12 & 15, 4)
    return out


def _lum(px, w, h, alpha):
    if alpha:
        a = _arr(px, w * h * 2).reshape(h, w, 2)
        c, al = a[..., 0], a[..., 1]
    else:
        c = _arr(px, w * h).reshape(h, w)
        al = np.full((h, w), 255, np.uint8)
    return np.stack((c, c, c, al), axis=2)


def _paletted(t, px, w, h):
    pal = np.frombuffer(t.palette, np.uint8).reshape(-1, 4).copy()
    if not t.has_alpha():
        pal[:, 3] = 255
    if t.palette_type() != PAL_8 and t.depth == 4:      # two pixels per byte, high nibble first
        b = _arr(px, (w * h + 1) // 2)
        idx = np.empty(b.size * 2, np.intp)
        idx[0::2], idx[1::2] = b >> 4, b & 15
        idx = idx[:w * h]
    else:
        idx = _arr(px, w * h).astype(np.intp)
    idx = np.minimum(idx, len(pal) - 1)
    return pal[idx].reshape(h, w, 4)


def _unpremultiply(img):
    a = img[..., 3:4].astype(np.float64)
    rgb = np.where(a > 0, np.minimum(np.round(img[..., :3] * 255.0 / np.maximum(a, 1)), 255), img[..., :3])
    out = img.copy()
    out[..., :3] = rgb.astype(np.uint8)
    return out
