# GTA SA Toolkit - GXT text reader (San Andreas format), only the MAIN table.
# Layout: u16 version (4), u16 bits per char (8 or 16), "TABL" + size + 12-byte entries (name[8], offset);
# at MAIN's offset: "TKEY" + size + 8-byte entries (string offset into TDAT, key hash), then "TDAT" + size
# + the strings. SA keeps no key names, only JAMCRC hashes of the upper-case key (CRC32 without the final
# flip), so a key like vehicles.ide's "INFERNU" is hashed and looked up.
import struct
import zlib


def key_hash(key):
    return zlib.crc32(key.upper().encode("ascii", "replace")) ^ 0xFFFFFFFF


class Gxt:
    def __init__(self, strings):
        self._s = strings           # hash -> text

    def get(self, key, default=None):
        return self._s.get(key_hash(key), default) if key else default

    def __len__(self):
        return len(self._s)

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            d = f.read()
        _ver, bits = struct.unpack_from("<HH", d, 0)
        if d[4:8] != b"TABL":
            raise ValueError("not a San Andreas GXT")
        n = struct.unpack_from("<I", d, 8)[0] // 12
        main = None
        for i in range(n):
            name = d[12 + i * 12:20 + i * 12].rstrip(b"\0")
            if name == b"MAIN":
                main = struct.unpack_from("<I", d, 20 + i * 12)[0]
                break
        if main is None:
            raise ValueError("no MAIN table")
        if d[main:main + 4] != b"TKEY":
            raise ValueError("MAIN table has no TKEY block")
        ksize = struct.unpack_from("<I", d, main + 4)[0]
        t = main + 8 + ksize
        if d[t:t + 4] != b"TDAT":
            raise ValueError("MAIN table has no TDAT block")
        base, end = t + 8, t + 8 + struct.unpack_from("<I", d, t + 4)[0]
        wide = bits == 16
        strings = {}
        for i in range(ksize // 8):
            off, h = struct.unpack_from("<II", d, main + 8 + i * 8)
            p = base + off
            if wide:
                e = p
                while e + 1 < end and d[e:e + 2] != b"\0\0":
                    e += 2
                strings[h] = d[p:e].decode("utf-16-le", "replace")
            else:
                e = d.find(b"\0", p, end)
                strings[h] = d[p:e if e >= 0 else end].decode("latin-1")
        return cls(strings)
