# GTA SA Toolkit - game data loader: gta.dat / default.dat, IDE, text IPL, binary (stream) IPL
import math
import os
import struct
from . import carmods, gxt
from .img import ImgSet


class ObjDef:
    __slots__ = ("id", "model", "txd", "draw_dist", "flags", "ide", "kind")

    def __init__(self, id, model, txd, draw_dist, flags, ide, kind):
        self.id = id
        self.model = model
        self.txd = txd
        self.draw_dist = draw_dist
        self.flags = flags
        self.ide = ide
        self.kind = kind


# The game's standard map sections (gta.dat IPL names) as people say them; modded ones keep their code.
_CITY = {"la": "Los Santos", "sf": "San Fierro", "vegas": "Las Venturas", "country": "Countryside",
         "count": "Countryside"}
_DIR = {"n": "North", "s": "South", "e": "East", "w": "West", "se": "South-East", "wn": "North-West"}
SECTION_NAMES = {
    "lahills": "Los Santos – Hills",
    "int_la": "Interiors – Los Santos", "int_sf": "Interiors – San Fierro", "int_veg": "Interiors – Las Venturas",
    "int_cont": "Interiors – Countryside", "gen_intb": "Interiors – General B", "stadint": "Interiors – Stadiums",
    "samp": "SA-MP objects",
}


def section_label(code):
    """'LAe2' -> 'Los Santos – East 2'; unknown / modded sections -> the code itself."""
    low = code.lower()
    if low in SECTION_NAMES:
        return SECTION_NAMES[low]
    if low.startswith("gen_int") and low[7:].isdigit():
        return "Interiors – General %s" % low[7:]
    for pre in sorted(_CITY, key=len, reverse=True):
        if low.startswith(pre):
            rest = low[len(pre):]
            num = rest.lstrip("abcdefghijklmnopqrstuvwxyz")
            d = _DIR.get(rest[:len(rest) - len(num)])
            if d and (not num or num.isdigit()):
                return "%s – %s%s" % (_CITY[pre], d, " " + num if num else "")
    return code


def read_zones(path, text=None):
    """info.zon zones as (place name, x0, y0, x1, y1). text: a Gxt for the names (else the zone key)."""
    zones = []
    if not path:
        return zones
    try:
        lines = _clean_lines(open(path, "r", errors="replace").read())
    except OSError:
        return zones
    for line in lines:
        p = [v.strip() for v in line.split(",")]
        if len(p) < 10:
            continue
        try:
            x0, y0, x1, y1 = float(p[2]), float(p[3]), float(p[5]), float(p[6])
        except ValueError:
            continue
        key = p[9]
        name = (text.get(key) if text is not None else None) or key
        zones.append((name.strip(), min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
    return zones


class VehicleDef:
    __slots__ = ("id", "model", "txd", "type", "handling", "game_name", "name", "vclass",
                 "wheel_id", "wheel_scale_f", "wheel_scale_r", "upgrade_class", "ide")

    def __init__(self, p, ide):
        self.id = int(p[0])
        self.model, self.txd, self.type = p[1], p[2], p[3].lower()
        self.handling = p[4] if len(p) > 4 else ""
        self.game_name = p[5] if len(p) > 5 else p[1]      # GXT key, e.g. INFERNU
        self.name = self.model                  # in-game name from american.gxt (GameData._read_names)
        self.vclass = p[7] if len(p) > 7 else ""
        self.ide = ide

        def f(i, d):
            try:
                return float(p[i])
            except (IndexError, ValueError):
                return d
        self.wheel_id = int(f(11, -1))
        self.wheel_scale_f = f(12, 0.7)
        self.wheel_scale_r = f(13, self.wheel_scale_f)
        self.upgrade_class = int(f(14, -1))       # carmods.dat wheel group (-1 = no custom wheels)


class Inst:
    __slots__ = ("id", "model", "interior", "pos", "rot", "lod", "is_lod", "section", "stream")

    def __init__(self, id, model, interior, pos, rot, lod, section, stream=False):
        self.id = id
        self.model = model
        self.interior = interior
        self.pos = pos            # (x, y, z)
        self.rot = rot            # file quaternion (x, y, z, w)
        self.lod = lod
        self.is_lod = False
        self.section = section
        self.stream = stream


class Section:
    """One map section = a text IPL plus its binary *_streamN.ipl files from the IMGs."""

    def __init__(self, name, path):
        self.name = name
        self.path = path
        self.insts = []           # text IPL instances (order matters: lod indices)
        self.stream_insts = []
        self.stream_files = []
        self.bounds = None        # (minx, miny, maxx, maxy)

    def all_insts(self, include_stream=True):
        return self.insts + (self.stream_insts if include_stream else [])

    def compute(self):
        for i in self.insts + self.stream_insts:
            if 0 <= i.lod < len(self.insts):
                self.insts[i.lod].is_lod = True
        for i in self.insts:
            if i.model.lower().startswith("lod"):
                i.is_lod = True
        pts = [i.pos for i in self.insts + self.stream_insts if not i.is_lod] or \
              [i.pos for i in self.insts + self.stream_insts]
        if pts:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            self.bounds = (min(xs), min(ys), max(xs), max(ys))


def find_ci(root, rel):
    """Case-insensitive path resolve (needed off Windows; harmless on Windows)."""
    rel = rel.replace("\\", "/").strip().strip("/")
    direct = os.path.join(root, *rel.split("/"))
    if os.path.exists(direct):
        return direct
    cur = root
    for part in rel.split("/"):
        if not part:
            continue
        try:
            names = os.listdir(cur)
        except OSError:
            return None
        low = part.lower()
        match = next((n for n in names if n.lower() == low), None)
        if match is None:
            return None
        cur = os.path.join(cur, match)
    return cur


def _clean_lines(text):
    for line in text.replace("\r", "").split("\n"):
        line = line.split("#", 1)[0].strip()
        if line:
            yield line


def _split(line):
    return [p.strip() for p in line.replace("\t", " ").replace(",", " ").split() if p.strip()]


class GameData:
    def __init__(self, root, load_imgs=True):
        self.root = root
        self.ide_files = []
        self.ipl_files = []
        self.img_files = []
        self.objects = {}          # id -> ObjDef
        self.by_name = {}          # lower model name -> ObjDef
        self.txd_parent = {}       # lower txd -> lower parent txd
        self.sections = []
        self.imgs = ImgSet()
        self.warnings = []
        self.vehicles = {}         # id -> VehicleDef
        self.samp_ids = set()      # object ids that come from SA-MP's SAMP.ide
        self.samp_dir = find_ci(root, "SAMP")
        self.loose_txd_dirs = [d for d in (self.samp_dir, find_ci(root, "models/generic"),
                                           find_ci(root, "models/txd"), find_ci(root, "models")) if d]
        self.car_colours = []      # carcols 'col' palette: [(r, g, b)]
        self.car_colour_sets = {}  # lower model -> [(c1, c2, [c3, c4])]
        self.car_mods = carmods.CarMods()      # carmods.dat
        self.handling_flags = {}   # handling id (upper) -> modelFlags
        self.zones = []            # (place name, x0, y0, x1, y1) from info.zon, see place_at
        self.ped_types = {}        # ped id -> peds.ide type (CIVMALE, GANG2, COP...)

        self._read_dat("data/default.dat")
        self._read_dat("data/gta.dat")
        if load_imgs:
            self._open_imgs()
        for ide in self.ide_files:
            self._read_ide(ide)
        self._load_samp(load_imgs)
        self._read_carcols()
        self._read_carmods()
        self._read_names()

    def _read_names(self):
        """Vehicles' in-game names ("Ambulance" for ambulan) and place names ("Ganton") from
        text/american.gxt; model name / zone key if the file or a key is missing."""
        text = None
        path = find_ci(self.root, "text/american.gxt")
        if path:
            try:
                text = gxt.Gxt.load(path)
            except Exception as e:           # noqa - names are a nicety, never a reason to fail the scan
                self.warnings.append("american.gxt: %s" % e)
        if text is not None:
            for v in self.vehicles.values():
                n = text.get(v.game_name)
                if n and n.strip():
                    v.name = n.strip()
        self.zones = read_zones(find_ci(self.root, "data/info.zon"), text)

    def place_at(self, x, y, reach=300.0):
        """Name of the place at (x, y): the smallest info.zon zone holding it, else the nearest one
        within reach metres, else ""."""
        best, best_key = "", None
        for name, x0, y0, x1, y1 in self.zones:
            dx = max(x0 - x, 0.0, x - x1)
            dy = max(y0 - y, 0.0, y - y1)
            d = math.hypot(dx, dy)
            if d > reach:
                continue
            key = (d, (x1 - x0) * (y1 - y0))     # inside (d = 0) first, then the smallest zone
            if best_key is None or key < best_key:
                best, best_key = name, key
        return best

    # ------------------------------------------------------------------ SA-MP
    def _load_samp(self, load_imgs):
        """SA-MP ships extra objects (IDs 18631-19999: the 'toys' / attachable objects and
        mapping objects) in SAMP\\SAMP.img + SAMP.ide, plus a small SAMP.ipl."""
        if not self.samp_dir:
            return
        if load_imgs:
            for n in ("SAMP.img", "custom.img"):
                p = find_ci(self.samp_dir, n)
                if p and os.path.getsize(p) > 2048:
                    try:
                        self.imgs.add(p)
                    except Exception as e:        # noqa
                        self.warnings.append("IMG %s: %s" % (n, e))
        for n in ("SAMP.ide", "CUSTOM.ide"):
            p = find_ci(self.samp_dir, n)
            if p:
                before = set(self.objects)
                self.read_ide_text(open(p, "r", errors="replace").read(), "samp")
                self.samp_ids |= set(self.objects) - before

    def samp_ipl_path(self):
        return find_ci(self.samp_dir, "SAMP.ipl") if self.samp_dir else None

    def find_loose_txd(self, name):
        for d in self.loose_txd_dirs:
            p = find_ci(d, name + ".txd")
            if p and os.path.isfile(p):
                return p
        return None

    def read_txd_bytes(self, name):
        data = self.imgs.read(name + ".txd")
        if data:
            return data
        p = self.find_loose_txd(name)
        if p:
            with open(p, "rb") as f:
                return f.read()
        return None

    # ------------------------------------------------------------------ carcols
    def _read_carcols(self):
        p = find_ci(self.root, "data/carcols.dat")
        if not p:
            return
        section = None
        for raw in open(p, "r", errors="replace").read().replace("\r", "").split("\n"):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            low = line.lower()
            if low in ("col", "car", "car4"):
                section = low
                continue
            if low == "end":
                section = None
                continue
            parts = [x for x in line.replace(",", " ").replace(".", " ").split() if x]
            try:
                if section == "col":
                    self.car_colours.append((int(parts[0]), int(parts[1]), int(parts[2])))
                elif section in ("car", "car4"):
                    n = 2 if section == "car" else 4
                    nums = [int(x) for x in parts[1:]]
                    sets = [tuple(nums[i:i + n]) for i in range(0, len(nums) - n + 1, n)]
                    self.car_colour_sets[parts[0].lower()] = sets
            except (ValueError, IndexError):
                continue

    def _read_carmods(self):
        p = find_ci(self.root, "data/carmods.dat")
        if p:
            self.car_mods = carmods.read_carmods(open(p, "r", errors="replace").read())
        p = find_ci(self.root, "data/handling.cfg")
        if p:
            self.handling_flags = carmods.read_handling_flags(open(p, "r", errors="replace").read())

    def colour_rgb(self, index):
        if 0 <= index < len(self.car_colours):
            return self.car_colours[index]
        return (200, 200, 200)

    # ------------------------------------------------------------------ dat
    def _read_dat(self, rel):
        path = find_ci(self.root, rel)
        if not path:
            self.warnings.append("Missing %s" % rel)
            return
        with open(path, "r", errors="replace") as f:
            for line in _clean_lines(f.read()):
                parts = line.split(None, 1)
                if len(parts) < 2:
                    continue
                key, val = parts[0].upper(), parts[1].strip()
                if key == "IDE":
                    self.ide_files.append(val)
                elif key == "IPL":
                    if val.lower().endswith(".ipl"):
                        self.ipl_files.append(val)
                elif key == "IMG":
                    self.img_files.append(val)

    def _open_imgs(self):
        wanted = ["models/gta3.img", "models/gta_int.img"]
        for v in self.img_files:
            lv = v.lower().replace("\\", "/")
            if any(s in lv for s in ("paths/", "script/", "cutscene")):
                continue
            if lv not in wanted:
                wanted.append(lv)
        for rel in wanted:
            p = find_ci(self.root, rel)
            if p:
                try:
                    self.imgs.add(p)
                except Exception as e:           # noqa
                    self.warnings.append("IMG %s: %s" % (rel, e))
            else:
                self.warnings.append("IMG not found: %s" % rel)

    # ------------------------------------------------------------------ IDE
    def _read_ide(self, rel):
        path = find_ci(self.root, rel)
        if not path:
            self.warnings.append("IDE not found: %s" % rel)
            return
        ide_name = os.path.splitext(os.path.basename(path))[0].lower()
        self.read_ide_text(open(path, "r", errors="replace").read(), ide_name)

    def read_ide_text(self, text, ide_name="custom"):
        section = None
        for line in _clean_lines(text):
            low = line.lower()
            if low == "end":
                section = None
                continue
            if section is None:
                section = low.split()[0]
                continue
            p = _split(line)
            try:
                if section in ("objs", "tobj", "anim", "weap", "hier"):
                    oid = int(p[0])
                    dd = 0.0
                    if section in ("objs", "tobj") and len(p) >= 5:
                        # SA: id model txd drawdist flags [timeOn timeOff]
                        # old: id model txd meshcount drawdist... flags
                        idx = 4 if ("." not in p[3] and len(p) in (6, 8, 9)) else 3
                        try:
                            dd = float(p[idx])
                        except ValueError:
                            dd = 0.0
                    self._add_obj(ObjDef(oid, p[1], p[2], dd, p[-1] if len(p) > 3 else "0", ide_name, section))
                elif section in ("peds", "cars"):
                    self._add_obj(ObjDef(int(p[0]), p[1], p[2], 0.0, "0", ide_name, section))
                    if section == "peds" and len(p) > 3:
                        self.ped_types[int(p[0])] = p[3].upper()     # CIVMALE, GANG2, COP...
                    if section == "cars":
                        self.vehicles[int(p[0])] = VehicleDef(p, ide_name)
                elif section == "txdp":
                    self.txd_parent[p[0].lower()] = p[1].lower()
            except (IndexError, ValueError):
                continue

    def _add_obj(self, od):
        self.objects[od.id] = od
        self.by_name[od.model.lower()] = od

    # ------------------------------------------------------------------ IPL
    def load_sections(self, include_stream=True):
        self.sections = []
        stream_names = {}
        if include_stream:
            for n in self.imgs.names_with_ext(".ipl"):
                ln = n.lower()
                if "_stream" in ln:
                    base = ln.split("_stream")[0]
                    stream_names.setdefault(base, []).append(n)
        for rel in self.ipl_files:
            path = find_ci(self.root, rel)
            if not path:
                self.warnings.append("IPL not found: %s" % rel)
                continue
            name = os.path.splitext(os.path.basename(path))[0]
            sec = Section(name, path)
            sec.insts = self.parse_text_ipl(open(path, "r", errors="replace").read(), sec)
            for sn in sorted(stream_names.get(name.lower(), []), key=_stream_key):
                data = self.imgs.read(sn)
                if data:
                    sec.stream_files.append(sn)
                    sec.stream_insts += self.parse_binary_ipl(data, sec)
            sec.compute()
            self.sections.append(sec)
        sp = self.samp_ipl_path()
        if sp:
            try:
                self.add_custom_ipl(sp).name = "SAMP"
            except Exception as e:           # noqa
                self.warnings.append("SAMP.ipl: %s" % e)
        return self.sections

    def add_custom_ipl(self, path):
        name = os.path.splitext(os.path.basename(path))[0]
        sec = Section(name, path)
        with open(path, "rb") as f:
            raw = f.read()
        if raw[:4] == b"bnry":
            sec.stream_insts = self.parse_binary_ipl(raw, sec)
        else:
            sec.insts = self.parse_text_ipl(raw.decode("latin-1"), sec)
        sec.compute()
        self.sections.append(sec)
        return sec

    def _model_name(self, oid, fallback=""):
        od = self.objects.get(oid)
        return od.model if od else fallback

    def parse_text_ipl(self, text, sec):
        out = []
        section = None
        for line in _clean_lines(text):
            low = line.lower()
            if low == "end":
                section = None
                continue
            if section is None:
                section = low.split()[0]
                continue
            if section != "inst":
                continue
            p = _split(line)
            try:
                if len(p) >= 11:          # SA: id model interior x y z rx ry rz rw lod
                    oid = int(p[0])
                    out.append(Inst(oid, p[1], int(p[2]),
                                    (float(p[3]), float(p[4]), float(p[5])),
                                    (float(p[6]), float(p[7]), float(p[8]), float(p[9])),
                                    int(p[10]), sec))
                elif len(p) >= 10:      # VC-like without interior/lod variations
                    oid = int(p[0])
                    out.append(Inst(oid, p[1], 0,
                                    (float(p[2]), float(p[3]), float(p[4])),
                                    (float(p[-4]), float(p[-3]), float(p[-2]), float(p[-1])),
                                    -1, sec))
            except ValueError:
                continue
        return out

    def parse_binary_ipl(self, data, sec):
        if data[:4] != b"bnry":
            return []
        num_inst = struct.unpack_from("<i", data, 4)[0]
        inst_off = struct.unpack_from("<i", data, 28)[0]
        out = []
        for i in range(num_inst):
            o = inst_off + i * 40
            if o + 40 > len(data):
                break
            x, y, z, rx, ry, rz, rw, oid, interior, lod = struct.unpack_from("<7f3i", data, o)
            out.append(Inst(oid, self._model_name(oid, str(oid)), interior,
                            (x, y, z), (rx, ry, rz, rw), lod, sec, stream=True))
        return out

    # ------------------------------------------------------------------ txd
    def txd_chain(self, txd):
        chain, seen = [], set()
        t = txd.lower()
        while t and t not in seen:
            chain.append(t)
            seen.add(t)
            t = self.txd_parent.get(t)
        return chain                      # child first

    def close(self):
        self.imgs.close()


def _stream_key(n):
    try:
        return int(n.lower().split("_stream")[1].split(".")[0])
    except (IndexError, ValueError):
        return 0
