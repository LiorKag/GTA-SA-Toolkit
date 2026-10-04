# GTA SA Toolkit - vehicle upgrade data: carmods.dat (which parts fit which car) and the model flags
# in handling.cfg (how the bonnet and boot open). Pure Python, no bpy.
#
# carmods.dat has three sections:
#   link    pairs of parts that always go on together (left/right side skirts, bonnet vents)
#   mods    <car model>, <part>, <part>, ...
#   wheel   <group>, <wheel>, ... ; a car's group is the last column of vehicles.ide (-1 = none)
# The kind of a part (and so where it goes on the car) comes from its name prefix, like the game.

# (kind, label, name prefix); longer prefixes first so "bntl_" isn't taken for "bnt_"
UPGRADE_KINDS = (
    ('BONNET_L', "Bonnet Vent (left)", "bntl_"),
    ('BONNET_R', "Bonnet Vent (right)", "bntr_"),
    ('BONNET', "Bonnet Scoop", "bnt_"),
    ('SPOILER', "Spoiler", "spl_"),
    ('WING_L', "Side Skirt (left)", "wg_l_"),
    ('WING_R', "Side Skirt (right)", "wg_r_"),
    ('FRONT_BULLBAR', "Front Bull Bar", "fbb_"),
    ('BACK_BULLBAR', "Rear Bull Bar", "bbb_"),
    ('LIGHTS', "Lights", "lgt_"),
    ('ROOF', "Roof", "rf_"),
    ('NITRO', "Nitro", "nto_"),
    ('HYDRAULICS', "Hydraulics", "hydralics"),
    ('STEREO', "Stereo", "stereo"),
    ('WHEELS', "Wheels", "wheel_"),
    ('EXHAUST', "Exhaust", "exh_"),
    ('FRONT_BUMPER', "Front Bumper", "fbmp_"),
    ('REAR_BUMPER', "Rear Bumper", "rbmp_"),
    ('MISC', "Misc", "misc_"),
)

# handling.cfg modelFlags (hex; "1st digit" in the file's notes is the lowest one)
FLAG_IS_VAN = 0x1
FLAG_REVERSE_BONNET = 0x10
FLAG_HANGING_BOOT = 0x20
FLAG_TAILGATE_BOOT = 0x40
FLAG_NOSWING_BOOT = 0x80
FLAG_NO_DOORS = 0x100
FLAG_IS_BIKE = 0x1000000


def upgrade_kind(name):
    n = name.lower()
    for kind, _label, prefix in UPGRADE_KINDS:
        if n.startswith(prefix):
            return kind
    return 'MISC'


class CarMods:
    def __init__(self):
        self.links = {}        # lower part -> lower partner (both directions)
        self.mods = {}         # lower car model -> [lower part] (file order, no repeats)
        self.wheels = {}       # group -> [lower wheel part]


def _clean(line):
    return line.split("#", 1)[0].strip()


def read_carmods(text):
    cm = CarMods()
    section = None
    for raw in text.replace("\r", "").split("\n"):
        line = _clean(raw)
        if not line:
            continue
        low = line.lower()
        if low in ("link", "mods", "wheel"):
            section = low
            continue
        if low == "end":
            section = None
            continue
        parts = [p.strip().lower() for p in line.replace("\t", " ").split(",") if p.strip()]
        if len(parts) < 2 or section is None:
            continue
        if section == "link":
            a, b = parts[0], parts[1]
            cm.links[a], cm.links[b] = b, a
        elif section == "mods":
            lst = cm.mods.setdefault(parts[0], [])
            for p in parts[1:]:
                if p not in lst:
                    lst.append(p)
        elif section == "wheel":
            try:
                cm.wheels[int(parts[0])] = parts[1:]
            except ValueError:
                pass
    return cm


def read_handling_flags(text):
    """{HANDLING ID (upper case): modelFlags int} from the main car lines of handling.cfg."""
    out = {}
    for raw in text.replace("\r", "").split("\n"):
        line = raw.split(";", 1)[0].strip()
        if not line or not line[0].isalpha():         # '%' boats, '!' bikes, '$' planes, '^' anims
            continue
        p = line.split()
        if len(p) < 30:
            continue
        try:
            out[p[0].upper()] = int(p[-5], 16)
        except ValueError:
            continue
    return out
