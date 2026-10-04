# GTA SA Toolkit - path node reader (nodes0.dat ... nodes63.dat in gta3.img): road and sidewalk graph
#
# The map is cut into 8 x 8 squares of 750 m ("areas"), numbered west -> east, then south -> north
# (area 0 = south-west corner at (-3000, -3000)). Each area has one file:
#   header      5 x uint32: nodes, car nodes, ped nodes, navi nodes, links
#   path nodes  28 bytes each, car nodes first then ped nodes:
#               uint32 x2 (unused), int16 x/y/z (1/8 m), int16 (cost, unused), uint16 first link,
#               uint16 area, uint16 node, uint8 width (1/8 m), uint8 flood-fill group, uint32 flags
#               (bits 0-3 link count, 4-5 traffic level, 6 roadblock, 7 boats, 8 emergency only,
#               12 not highway, 13 highway, 16-19 spawn probability)
#   navi nodes  14 bytes each, one halfway along every car link: int16 x/y (1/8 m), uint16 area,
#               uint16 node (the link end the direction points to), int8 dir x/y (1/100), uint32 flags
#               (bits 0-7 median width (1/8 m), 8-10 left lanes, 11-13 right lanes, 14 traffic light
#               direction, 16-17 traffic light behaviour, 18 train crossing)
#   links       4 bytes each: uint16 area, uint16 node (a node's links start at its "first link")
#   filler      768 bytes
#   navi links  uint16 per link: bits 0-9 navi node, 10-15 area (car links only)
#   lengths     uint8 per link (metres)
#   link flags  uint8 per link (bit 0 road crossing, bit 1 ped traffic light)
# Right lanes run along the navi direction (on its right), left lanes against it: traffic keeps right.
import struct

AREA_SIZE = 750.0
GRID = 8
ORIGIN = -3000.0
LANE_WIDTH = 5.0            # metres between lane centres, like the game

F_BOATS = 0x80
F_EMERGENCY = 0x100
F_HIGHWAY = 0x2000


def area_of(x, y):
    """Area number (0-63) of a map position; positions off the map count as the nearest area."""
    col = min(max(int((x - ORIGIN) // AREA_SIZE), 0), GRID - 1)
    row = min(max(int((y - ORIGIN) // AREA_SIZE), 0), GRID - 1)
    return row * GRID + col


def area_bounds(area):
    """((min x, min y), (max x, max y)) of an area."""
    col, row = area % GRID, area // GRID
    x0, y0 = ORIGIN + col * AREA_SIZE, ORIGIN + row * AREA_SIZE
    return (x0, y0), (x0 + AREA_SIZE, y0 + AREA_SIZE)


def areas_in(mn, mx):
    """Areas touching the rectangle mn..mx (x, y)."""
    a0, a1 = area_of(mn[0], mn[1]), area_of(mx[0], mx[1])
    c0, r0, c1, r1 = a0 % GRID, a0 // GRID, a1 % GRID, a1 // GRID
    return [r * GRID + c for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)]


class PathNode:
    """A point on a road (ped=False) or sidewalk (ped=True). key = (area, node)."""
    __slots__ = ("key", "pos", "ped", "width", "flags", "group", "links", "navis", "lengths")

    def __init__(self, key, pos, ped, width, flags, group):
        self.key = key
        self.pos = pos                  # (x, y, z) metres
        self.ped = ped
        self.width = width              # metres
        self.flags = flags
        self.group = group
        self.links = []                 # keys of connected nodes
        self.navis = []                 # per link: navi node key (car links) or None
        self.lengths = []               # per link: metres (as stored, whole metres)

    @property
    def traffic(self):
        return (self.flags >> 4) & 3

    @property
    def boats(self):
        return bool(self.flags & F_BOATS)

    @property
    def emergency(self):
        return bool(self.flags & F_EMERGENCY)

    @property
    def highway(self):
        return bool(self.flags & F_HIGHWAY)


class NaviNode:
    """Lane information halfway along a car link. dir points towards the link end `target`."""
    __slots__ = ("key", "pos", "target", "dir", "width", "left", "right", "flags")

    def __init__(self, key, pos, target, dir, flags):
        self.key = key
        self.pos = pos                  # (x, y)
        self.target = target            # node key
        self.dir = dir                  # (dx, dy), about unit length
        self.flags = flags
        self.width = (flags & 0xFF) / 8.0
        self.left = (flags >> 8) & 7
        self.right = (flags >> 11) & 7


def parse(data, area):
    """(nodes, navis) of one nodes<area>.dat. Links still point at (area, node) keys that may be in
    other areas; PathGraph joins them."""
    num, veh, _ped, n_navi, n_links = struct.unpack_from("<5I", data, 0)
    need = 20 + 28 * num + 14 * n_navi + 4 * n_links + 768 + 4 * n_links
    if len(data) < need:
        raise ValueError("nodes%d.dat is too short (%d bytes, needs %d)" % (area, len(data), need))
    off_navi = 20 + 28 * num
    off_links = off_navi + 14 * n_navi
    off_nlinks = off_links + 4 * n_links + 768
    off_len = off_nlinks + 2 * n_links
    links = struct.unpack_from("<%dH" % (2 * n_links), data, off_links)
    nlinks = struct.unpack_from("<%dH" % n_links, data, off_nlinks)
    lengths = data[off_len:off_len + n_links]

    nodes = []
    rec = struct.Struct("<8xhhh2xHHHBBI")
    for i in range(num):
        x, y, z, first, _a, _n, width, group, flags = rec.unpack_from(data, 20 + 28 * i)
        ped = i >= veh
        n = PathNode((area, i), (x / 8.0, y / 8.0, z / 8.0), ped, width / 8.0, flags, group)
        for k in range(first, min(first + (flags & 15), n_links)):
            n.links.append((links[2 * k], links[2 * k + 1]))
            n.navis.append(None if ped else (nlinks[k] >> 10, nlinks[k] & 1023))
            n.lengths.append(lengths[k])
        nodes.append(n)

    navis = []
    rec = struct.Struct("<hhHHbbI")
    for i in range(n_navi):
        x, y, ta, tn, dx, dy, flags = rec.unpack_from(data, off_navi + 14 * i)
        navis.append(NaviNode((area, i), (x / 8.0, y / 8.0), (ta, tn), (dx / 100.0, dy / 100.0), flags))
    return nodes, navis


class PathGraph:
    """Every road and sidewalk node of the areas read. nodes / navis: {key: node}. Links to areas
    that were not read are dropped."""

    def __init__(self):
        self.nodes = {}
        self.navis = {}
        self.areas = set()

    def add(self, area, data):
        nodes, navis = parse(data, area)
        for n in nodes:
            self.nodes[n.key] = n
        for v in navis:
            self.navis[v.key] = v
        self.areas.add(area)

    def finish(self):
        """Drop links whose other end wasn't read (call after the last add)."""
        for n in self.nodes.values():
            if any(k not in self.nodes for k in n.links):
                keep = [i for i, k in enumerate(n.links) if k in self.nodes]
                n.links = [n.links[i] for i in keep]
                n.navis = [n.navis[i] for i in keep]
                n.lengths = [n.lengths[i] for i in keep]
        return self

    def navi_between(self, a, b):
        """The navi node of the car link a -> b (keys), or None."""
        n = self.nodes.get(a)
        if n is None:
            return None
        for k, nv in zip(n.links, n.navis):
            if k == b:
                return self.navis.get(nv) if nv is not None else None
        return None

    def lanes(self, a, b):
        """Lanes going a -> b and coming b -> a on the car link, and the median width:
        (with, against, median). (1, 1, 0) when the link has no lane information."""
        nv = self.navi_between(a, b)
        if nv is None:
            return 1, 1, 0.0
        if nv.target == b:
            return nv.right, nv.left, nv.width
        return nv.left, nv.right, nv.width


def load(read, areas=range(GRID * GRID)):
    """PathGraph of these areas; read(name) returns a file's bytes or None (e.g. ImgSet.read)."""
    g = PathGraph()
    for area in areas:
        data = read("nodes%d.dat" % area)
        if data:
            g.add(area, data)
    return g.finish()
