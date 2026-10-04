# GTA SA Toolkit - RenderWare binary stream basics shared by the DFF / TXD readers (formats/dff.py,
# formats/txd.py). Written from the format notes on the GTAMods wiki (https://gtamods.com/wiki/RenderWare).
#
# A RenderWare file is a tree of chunks: type (u32), size (u32), library id (u32), then `size` bytes.
# The readers never trust a size or a count: everything is checked against the bytes really there, and
# anything they don't know is skipped. A file that can't be used at all raises RWError with a message
# meant for people ("not a model file", "PS2 model: only PC models can be imported").
import struct

STRUCT, STRING, EXTENSION, UNICODE_STRING = 0x01, 0x02, 0x03, 0x13
TEXTURE, MATERIAL, MATERIAL_LIST, FRAME_LIST, GEOMETRY, CLUMP, LIGHT, ATOMIC = 0x06, 0x07, 0x08, 0x0E, 0x0F, 0x10, 0x12, 0x14
TEXTURE_NATIVE, TEXTURE_DICTIONARY, GEOMETRY_LIST, ANIM_ANIMATION, RIGHT_TO_RENDER = 0x15, 0x16, 0x1A, 0x1B, 0x1F
UV_ANIM_DICT = 0x2B
MORPH_PLG, ANIMATION_PLG, BONE_PLG, SKIN_PLG, HANIM_PLG, USER_DATA_PLG = 0x105, 0x108, 0x10E, 0x116, 0x11E, 0x11F
MATFX_PLG, DELTA_MORPH_PLG, UV_ANIM_PLG, BIN_MESH_PLG, NATIVE_DATA_PLG = 0x120, 0x122, 0x135, 0x50E, 0x510
SKYGFX = 0xEDED
PIPELINE_SET, SPECULAR_MAT, EFFECT_2D, EXTRA_VERT_COLOUR = 0x253F2F3, 0x253F2F6, 0x253F2F8, 0x253F2F9
COLLISION_MODEL, REFLECTION_MAT, FRAME_NAME, SAMP_COLLISION = 0x253F2FA, 0x253F2FC, 0x253F2FE, 0x253F2FF

# names for messages
CHUNK_NAMES = {CLUMP: "model (clump)", TEXTURE_DICTIONARY: "texture dictionary", ATOMIC: "atomic",
               UV_ANIM_DICT: "UV animation dictionary", GEOMETRY: "geometry"}

MAX_DEPTH = 16


class RWError(Exception):
    """A file (or a part of it) can't be read. The message is shown to people as it is."""


def rw_version(library_id):
    """The RenderWare version (0x36003 = 3.6.0.3, GTA SA) from a chunk's library id."""
    if library_id & 0xFFFF0000:
        return ((library_id >> 14) & 0x3FF00) + 0x30000 | ((library_id >> 16) & 0x3F)
    return library_id << 8                       # very old files (GTA III's first builds) store it plainly


def game_of(version):
    """A rough guess at the game a RenderWare version belongs to (for messages only)."""
    if version >= 0x35000:
        return "GTA San Andreas"
    if version >= 0x33000:
        return "GTA Vice City"
    if version >= 0x30000:
        return "GTA III"
    return "an unknown RenderWare game"


def cstr(b):
    """A zero-terminated byte string -> str (UTF-8, else Latin-1 so any bytes give a name)."""
    b = bytes(b).split(b"\0", 1)[0]
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("latin-1")


class Chunk:
    __slots__ = ("type", "size", "version", "start", "end")

    def __init__(self, ctype, size, lib, start):
        self.type, self.size, self.version = ctype, size, rw_version(lib)
        self.start, self.end = start, start + size          # start = first byte after the 12-byte header


def chunk_at(data, pos, end):
    """The chunk whose header is at pos, or None when there's no room for one. A chunk running past
    `end` is cut to it (some tools write slightly wrong sizes; the bytes that are there still count)."""
    if pos + 12 > end:
        return None
    ctype, size, lib = struct.unpack_from("<3I", data, pos)
    c = Chunk(ctype, size, lib, pos + 12)
    if c.end > end:
        c.end = end
        c.size = end - c.start
    return c


def children(data, start, end):
    """The chunks one after another between start and end (the inside of a container chunk)."""
    pos = start
    while True:
        c = chunk_at(data, pos, end)
        if c is None:
            return
        yield c
        if c.end <= pos:                # zero-size header loop guard
            return
        pos = c.end


def find(data, start, end, ctype):
    for c in children(data, start, end):
        if c.type == ctype:
            return c
    return None


def string_chunk(data, c):
    """Text of a String / UnicodeString chunk ('' for anything else)."""
    if c is None:
        return ""
    if c.type == UNICODE_STRING:
        try:
            return bytes(data[c.start:c.end]).decode("utf-16-le").split("\0", 1)[0]
        except UnicodeDecodeError:
            return ""
    if c.type != STRING:
        return ""
    return cstr(data[c.start:c.end])


def need(data, pos, n, what):
    """Raise RWError when fewer than n bytes are left at pos."""
    if n < 0 or pos + n > len(data):
        raise RWError("%s: the file ends early" % what)
