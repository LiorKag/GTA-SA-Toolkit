# GTA SA Toolkit - minimal PNG writer (8-bit RGBA, no filtering, fast zlib level). Used to pack decoded
# game textures into the .blend straight away: Blender's own Image.pack() re-encodes every image, which
# took about 3x longer for the same textures.
import struct
import zlib

import numpy as np


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def rgba(pixels, level=1):
    """PNG bytes of a (h, w, 4) uint8 array, top row first."""
    h, w = pixels.shape[:2]
    raw = np.zeros((h, w * 4 + 1), np.uint8)          # filter byte 0 (none) at the start of every row
    raw[:, 1:] = pixels.reshape(h, w * 4)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw.tobytes(), level)) + _chunk(b"IEND", b""))
