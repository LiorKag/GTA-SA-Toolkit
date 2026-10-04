# GTA SA Studio - reads the game's data/timecyc.dat (weather x time of day lighting table).
#
# 23 weather blocks, each with 8 rows for the fixed times in SLOT_HOURS. A row is a list of numbers
# in FIELDS order; the stock PC file has 51 per row (no DirMult), some mods 52. Other times of day are
# a straight blend of the two nearest rows (10 PM blends into midnight), like the game does.
# A row with fewer numbers than the file's usual count (the stock file has one: RAINY_COUNTRYSIDE
# 8 PM, two numbers missing) is replaced by the blend of its neighbouring rows, and noted.
# A file's fingerprint tells the stock PC file from modified ones (numbers only: spacing,
# line endings and comments don't count), files for another game / version get a clear message, and
# the numbers can be kept in the .blend (to_store / from_store) so a moved file doesn't change the sky.
import hashlib
import json
import os

WEATHERS = (
    "EXTRASUNNY_LA", "SUNNY_LA", "EXTRASUNNY_SMOG_LA", "SUNNY_SMOG_LA", "CLOUDY_LA",
    "SUNNY_SF", "EXTRASUNNY_SF", "CLOUDY_SF", "RAINY_SF", "FOGGY_SF",
    "SUNNY_VEGAS", "EXTRASUNNY_VEGAS", "CLOUDY_VEGAS",
    "EXTRASUNNY_COUNTRYSIDE", "SUNNY_COUNTRYSIDE", "CLOUDY_COUNTRYSIDE", "RAINY_COUNTRYSIDE",
    "EXTRASUNNY_DESERT", "SUNNY_DESERT", "SANDSTORM_DESERT",
    "UNDERWATER", "EXTRACOLOURS_1", "EXTRACOLOURS_2",
)
PLAYABLE = 20                   # the last three are special (underwater, colour sets for interiors)
LABELS = {
    "EXTRASUNNY_LA": "Extra Sunny (LA)", "SUNNY_LA": "Sunny (LA)", "EXTRASUNNY_SMOG_LA": "Extra Sunny Smog (LA)",
    "SUNNY_SMOG_LA": "Sunny Smog (LA)", "CLOUDY_LA": "Cloudy (LA)", "SUNNY_SF": "Sunny (SF)",
    "EXTRASUNNY_SF": "Extra Sunny (SF)", "CLOUDY_SF": "Cloudy (SF)", "RAINY_SF": "Rainy (SF)",
    "FOGGY_SF": "Foggy (SF)", "SUNNY_VEGAS": "Sunny (Vegas)", "EXTRASUNNY_VEGAS": "Extra Sunny (Vegas)",
    "CLOUDY_VEGAS": "Cloudy (Vegas)", "EXTRASUNNY_COUNTRYSIDE": "Extra Sunny (Countryside)",
    "SUNNY_COUNTRYSIDE": "Sunny (Countryside)", "CLOUDY_COUNTRYSIDE": "Cloudy (Countryside)",
    "RAINY_COUNTRYSIDE": "Rainy (Countryside)", "EXTRASUNNY_DESERT": "Extra Sunny (Desert)",
    "SUNNY_DESERT": "Sunny (Desert)", "SANDSTORM_DESERT": "Sandstorm (Desert)",
}
SLOT_HOURS = (0.0, 5.0, 6.0, 7.0, 12.0, 19.0, 20.0, 22.0)

FIELDS = (
    ("amb", 3), ("amb_obj", 3), ("dir", 3), ("sky_top", 3), ("sky_bot", 3), ("sun_core", 3),
    ("sun_corona", 3), ("sun_size", 1), ("sprite_size", 1), ("sprite_bright", 1), ("shadow", 1),
    ("light_shadow", 1), ("pole_shadow", 1), ("far_clip", 1), ("fog_start", 1), ("light_on_ground", 1),
    ("low_clouds", 3), ("bottom_clouds", 3), ("water", 4), ("post1", 4), ("post2", 4),
    ("cloud_alpha", 1), ("intensity_limit", 1), ("water_fog_alpha", 1), ("dir_mult", 1),
)
DEFAULTS = {"cloud_alpha": 0.0, "intensity_limit": 0.0, "water_fog_alpha": 0.0, "dir_mult": 1.0}
MIN_NUMBERS = sum(n for _k, n in FIELDS[:-4])          # up to post2: 48


# fingerprint (see fingerprint()) of the stock PC timecyc.dat: two separate unmodified installs agree
STOCK_FINGERPRINTS = frozenset(("9eed992e267e481ede0dd05e49147cf0f69477ad",))
SA_ROWS = 23 * 8
SA_NUMBERS = (51, 52)           # numbers per row: stock PC 51, some mods 52


class TimecycError(Exception):
    pass


def _number_lines(text):
    """The file's number rows as token lists (comments and blank lines left out)."""
    out = []
    for raw in text.splitlines():
        s = raw.split("//", 1)[0].strip()
        if s:
            out.append(s.split())
    return out


def fingerprint(text):
    """Of the numbers only (spacing, line endings, comments don't count)."""
    return hashlib.sha1("\n".join(" ".join(t) for t in _number_lines(text)).encode("latin-1", "replace")).hexdigest()


def is_stock(text):
    return fingerprint(text) in STOCK_FINGERPRINTS


def check_layout(text):
    """Raise TimecycError saying clearly what's wrong when this isn't a San Andreas PC timecyc."""
    lines = _number_lines(text)
    if not lines:
        raise TimecycError("Not a timecyc file (no rows of numbers)")
    bad = 0
    counts = []
    for t in lines:
        try:
            [float(x) for x in t]
            counts.append(len(t))
        except ValueError:
            bad += 1
    if bad > len(lines) // 4 or not counts:
        raise TimecycError("Not a timecyc file (%d of %d lines aren't numbers)" % (bad, len(lines)))
    usual = sorted(counts)[len(counts) // 2]
    if len(counts) != SA_ROWS or not MIN_NUMBERS <= usual <= 60:
        raise TimecycError("This looks like a timecyc for another game or version (%d rows of %d numbers; "
                           "San Andreas PC has %d rows of 51-52)" % (len(counts), usual, SA_ROWS))


class Timecyc:
    """rows[weather index][slot index] = {field: float or tuple}. notes: repairs made while reading."""

    def __init__(self, rows, notes, path=""):
        self.rows, self.notes, self.path = rows, notes, path
        vals = sorted(r["dir_mult"] for w in rows for r in w)
        self.dir_mult_typical = vals[len(vals) // 2] or 1.0

    def sample(self, weather, hour):
        """Values for a weather (index or name) at any hour (float, wraps at 24): blend of the two
        nearest rows. Colours stay 0-255 like in the file."""
        w = WEATHERS.index(weather) if isinstance(weather, str) else int(weather)
        rows = self.rows[w]
        h = float(hour) % 24.0
        n = len(SLOT_HOURS)
        i = max(k for k in range(n) if SLOT_HOURS[k] <= h)
        j = (i + 1) % n
        h0, h1 = SLOT_HOURS[i], SLOT_HOURS[j] + (24.0 if j == 0 else 0.0)
        t = (h - h0) / (h1 - h0)
        return _blend(rows[i], rows[j], t)


def _blend(a, b, t):
    out = {}
    for k, va in a.items():
        vb = b[k]
        if isinstance(va, tuple):
            out[k] = tuple(x + (y - x) * t for x, y in zip(va, vb))
        else:
            out[k] = va + (vb - va) * t
    return out


def _row(nums):
    out, i = {}, 0
    for key, n in FIELDS:
        if i + n > len(nums):
            out[key] = DEFAULTS[key]
            continue
        out[key] = tuple(nums[i:i + n]) if n > 1 else nums[i]
        i += n
    return out


def parse(text, path=""):
    check_layout(text)
    lines = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("//"):
            continue
        try:
            lines.append([float(x) for x in s.split()])
        except ValueError:
            raise TimecycError("not a number in line %r" % s[:60])
    need = len(WEATHERS) * len(SLOT_HOURS)
    if len(lines) < need:
        raise TimecycError("%d rows, expected %d (23 weathers x 8 times)" % (len(lines), need))
    lines = lines[:need]
    counts = sorted(len(l) for l in lines)
    usual = counts[len(counts) // 2]
    if usual < MIN_NUMBERS:
        raise TimecycError("rows have %d numbers, expected at least %d" % (usual, MIN_NUMBERS))
    n = len(SLOT_HOURS)
    rows = [[_row(lines[w * n + s]) for s in range(n)] for w in range(len(WEATHERS))]
    notes = []
    for w in range(len(WEATHERS)):
        for s in range(n):
            if len(lines[w * n + s]) < usual:
                prev, nxt = rows[w][(s - 1) % n], rows[w][(s + 1) % n]
                rows[w][s] = _blend(prev, nxt, 0.5)
                notes.append("%s %s: %d numbers instead of %d, used the blend of the rows around it"
                             % (WEATHERS[w], _hour_text(SLOT_HOURS[s]), len(lines[w * n + s]), usual))
    return Timecyc(rows, notes, path)


def _hour_text(h):
    return "%02d:%02d" % (int(h), round((h - int(h)) * 60))


def to_store(text):
    """The numbers of a file as a compact string for the .blend (about 60 KB)."""
    return json.dumps([[float(x) for x in t] for t in _number_lines(text)], separators=(",", ":"))


def from_store(data, path=""):
    """A Timecyc from to_store()'s string."""
    rows = json.loads(data)
    return parse("\n".join(" ".join(repr(x) for x in r) for r in rows), path)


def sky_colour(values):
    """What the sky looks like at those values: the mix of its overhead and horizon colours, 0-1 sRGB."""
    top, bot = values["sky_top"], values["sky_bot"]
    return tuple(max(0.0, min(1.0, (a + b) / 510.0)) for a, b in zip(top[:3], bot[:3]))


_CACHE = {"key": None, "tc": None}


def read_text(path):
    with open(path, "r", encoding="latin-1") as f:
        return f.read()


_FILES = {}             # normcase path -> (mtime, size, Timecyc, fingerprint)


def load(path):
    """Read path (cached until the file changes). Raises OSError / TimecycError."""
    st = os.stat(path)
    k = os.path.normcase(os.path.abspath(path))
    hit = _FILES.get(k)
    if hit is None or hit[0] != st.st_mtime_ns or hit[1] != st.st_size:
        text = read_text(path)
        hit = (st.st_mtime_ns, st.st_size, parse(text, path), fingerprint(text))
        _FILES[k] = hit
    _CACHE["key"], _CACHE["tc"] = k, hit[2]
    return hit[2]


def fingerprint_of(path):
    """Fingerprint of a readable timecyc file (cached with load()), or None."""
    try:
        load(path)
    except (OSError, TimecycError):
        return None
    return _FILES[os.path.normcase(os.path.abspath(path))][3]
