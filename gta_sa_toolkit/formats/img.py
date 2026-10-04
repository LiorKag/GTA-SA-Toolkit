# GTA SA Toolkit - IMG archive reader (v1 .img+.dir for GTA3/VC, v2 "VER2" for SA)
import os
import struct

SECTOR = 2048


class ImgEntry:
    __slots__ = ("name", "offset", "size", "archive")

    def __init__(self, name, offset, size, archive):
        self.name = name
        self.offset = offset
        self.size = size
        self.archive = archive


class ImgArchive:
    def __init__(self, path):
        self.path = path
        self.entries = {}          # lower-case name -> ImgEntry
        self.order = []
        self._fh = None
        self.mtime = os.path.getmtime(path)
        self._read_directory()

    def _read_directory(self):
        self.mtime = os.path.getmtime(self.path)
        with open(self.path, "rb") as f:
            head = f.read(8)
            if head[:4] == b"VER2":
                count = struct.unpack("<I", head[4:8])[0]
                raw = f.read(32 * count)
                for i in range(count):
                    off, stream_sz, arch_sz = struct.unpack_from("<IHH", raw, i * 32)
                    name = _cstr(raw[i * 32 + 8:i * 32 + 32])
                    size = (arch_sz or stream_sz) * SECTOR
                    self._add(name, off * SECTOR, size)
                return
        dir_path = os.path.splitext(self.path)[0] + ".dir"
        if not os.path.isfile(dir_path):
            raise ValueError("Unknown IMG format and no .dir file: %s" % self.path)
        with open(dir_path, "rb") as f:
            raw = f.read()
        for i in range(len(raw) // 32):
            off, sz = struct.unpack_from("<II", raw, i * 32)
            self._add(_cstr(raw[i * 32 + 8:i * 32 + 32]), off * SECTOR, sz * SECTOR)

    def _add(self, name, offset, size):
        e = ImgEntry(name, offset, size, self)
        self.entries[name.lower()] = e
        self.order.append(e)

    def read(self, entry):
        if isinstance(entry, str):
            entry = self.entries[entry.lower()]
        if self._fh is None:
            # the archive may have been edited (IMG Tool, Alci's, modding) since we indexed it:
            # offsets would be wrong, so re-read the directory when the file changed
            try:
                mt = os.path.getmtime(self.path)
            except OSError:
                mt = self.mtime
            if mt != self.mtime:
                self.entries, self.order = {}, []
                self._read_directory()
            self._fh = open(self.path, "rb")
        cur = self.entries.get(entry.name.lower())
        if cur is None:
            return None
        self._fh.seek(cur.offset)
        return self._fh.read(cur.size)

    def close(self):
        if self._fh:
            self._fh.close()
            self._fh = None

    def __contains__(self, name):
        return name.lower() in self.entries


class ImgSet:
    """Several IMG archives searched in order (later archives override earlier ones,
    like the game does for gta3.img -> gta_int.img -> custom imgs)."""

    def __init__(self):
        self.archives = []
        self.index = {}

    def add(self, path):
        arc = ImgArchive(path)
        self.archives.append(arc)
        for k, e in arc.entries.items():
            self.index[k] = e
        return arc

    def find(self, name):
        return self.index.get(name.lower())

    def read(self, name):
        e = self.find(name)
        return e.archive.read(e) if e else None

    def names_with_ext(self, ext):
        ext = ext.lower()
        return [e.name for e in self.index.values() if e.name.lower().endswith(ext)]

    def close(self):
        for a in self.archives:
            a.close()


def _cstr(b):
    i = b.find(b"\0")
    if i >= 0:
        b = b[:i]
    return b.decode("latin-1", errors="replace")
