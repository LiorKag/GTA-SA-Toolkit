# GTA SA Toolkit - tags GTA 2DFX lights (street lamps, traffic lights, neon...) at import time.
# Each light's data gets its group (gta_cat), base colour/strength and day/night flags; the lighting
# controls that use these tags (groups, strength, tint, Day/Dusk/Night) live in GTA SA Studio.
import colorsys

import bpy

CATEGORIES = (
    ('STREET', "Street Lamps", "Lamp posts and street lighting"),
    ('TRAFFIC', "Traffic Lights", "Traffic and rail-crossing signals"),
    ('NEON', "Neon & Signs", "Coloured signs, shop neons, decorative lights"),
    ('BUILDING', "Buildings & Windows", "Warm/white lights on buildings, windows, interiors"),
    ('BLINK', "Warning / Blinking", "Aircraft warnings, flashing and animated lights"),
    ('VEHICLE', "Vehicle Lights", "Head/tail light materials of imported vehicles"),
)
CAT_KEYS = [c[0] for c in CATEGORIES]

_STREET_WORDS = ("lamp", "streetlight", "strtlight", "lampost", "lamppost", "lght", "light_pole",
                 "gay_lamp", "hwaylamp", "vgs_lamp", "streetl")
_TRAFFIC_WORDS = ("traffic", "trfc", "trflight", "gate_light", "crossing")


class _Fx:
    """A light's 2DFX settings, read the same way from our own tag (data["gta_2dfx"], rwbuild) and from
    DragonFF's ext_2dfx property (older files; DragonFF may be gone, so read safely)."""
    __slots__ = ("corona_show_mode", "point_light_range", "corona_size", "flag1_at_day", "flag1_at_night",
                 "flag1_blinking1", "flag2_blinking2", "flag2_blinking3")


def fx(light_data):
    """_Fx of a GTA light's data, or None when it has no 2DFX settings."""
    d = light_data.get("gta_2dfx") if hasattr(light_data, "get") else None
    out = _Fx()
    if d is not None:
        f1, f2 = int(d.get("flags1", 0)), int(d.get("flags2", 0))
        out.corona_show_mode = int(d.get("corona_show_mode", 0))
        out.point_light_range = float(d.get("point_light_range", 0.0))
        out.corona_size = float(d.get("corona_size", 0.0))
        out.flag1_at_day, out.flag1_at_night, out.flag1_blinking1 = bool(f1 & 32), bool(f1 & 64), bool(f1 & 128)
        out.flag2_blinking2, out.flag2_blinking3 = bool(f2 & 2), bool(f2 & 16)
        return out
    s = getattr(light_data, "ext_2dfx", None)
    if s is None:
        return None
    out.corona_show_mode = int(getattr(s, "corona_show_mode", "0") or 0)
    out.point_light_range = float(getattr(s, "point_light_range", 0.0) or 0.0)
    out.corona_size = float(getattr(s, "corona_size", 0.0) or 0.0)
    for k in ("flag1_at_day", "flag1_at_night", "flag1_blinking1", "flag2_blinking2", "flag2_blinking3"):
        setattr(out, k, bool(getattr(s, k, False)))
    return out


def is_gta_light(ob):
    return ob.type == 'LIGHT' and (ob.data.get("gta_cat") is not None or
                                  ob.data.name.startswith("2dfx_light") or ob.name.startswith("2dfx_light"))


def classify(light_data, model_name=""):
    m = (model_name or "").lower()
    s = fx(light_data)
    mode = s.corona_show_mode if s else 0
    if mode in (7, 8) or any(w in m for w in _TRAFFIC_WORDS):
        return 'TRAFFIC'
    if mode in (1, 2, 3, 4, 5, 6, 11, 12, 13) or (s and (s.flag1_blinking1 or s.flag2_blinking2 or s.flag2_blinking3)):
        return 'BLINK'
    if any(w in m for w in _STREET_WORDS):
        return 'STREET'
    r, g, b = light_data.color[:3]
    _h, sat, _v = colorsys.rgb_to_hsv(r, g, b)
    if sat > 0.45:
        return 'NEON'
    rng = s.point_light_range if s else 0.0
    if rng >= 8.0 and s and s.flag1_at_night and not s.flag1_at_day:
        return 'STREET'
    return 'BUILDING'


def base_energy(light_data):
    s = fx(light_data)
    rng = s.point_light_range if s else 0.0
    corona = s.corona_size if s else 0.0
    if rng > 0.1:
        e = 6.0 * rng * rng
    else:
        e = 25.0 * max(corona, 0.5)          # corona-only lights: small glow
    return max(5.0, min(e, 4000.0))


def tag_light(ob, model_name=""):
    d = ob.data
    if d.get("gta_cat") is None:
        d["gta_cat"] = classify(d, model_name)
        d["gta_model"] = model_name
        d["gta_base_color"] = list(d.color[:3])
        d["gta_base_energy"] = base_energy(d)
        s = fx(d)
        d["gta_day"] = bool(s.flag1_at_day) if s else True
        d["gta_night"] = bool(s.flag1_at_night) if s else True
        if not d["gta_day"] and not d["gta_night"]:
            d["gta_day"] = d["gta_night"] = True
        if d["gta_cat"] in ('TRAFFIC', 'BLINK'):          # signals work day and night
            d["gta_day"] = d["gta_night"] = True
        cap = {'TRAFFIC': 300.0, 'BLINK': 400.0, 'NEON': 1500.0}.get(d["gta_cat"])
        if cap:
            d["gta_base_energy"] = min(d["gta_base_energy"], cap)
    return d["gta_cat"]


def tag_collection_lights(coll, model_name=""):
    n = 0
    for ob in coll.all_objects:
        if is_gta_light(ob):
            tag_light(ob, model_name)
            n += 1
    return n


def scan_scene_lights():
    """Tag 2DFX lights that were imported before this feature existed."""
    n = 0
    for ob in list(bpy.data.objects):
        if is_gta_light(ob) and ob.data.get("gta_cat") is None:
            model = ""
            for c in ob.users_collection:
                if c.name.lower().endswith(".dff"):
                    model = c.name[:-4]
                    break
            tag_light(ob, model)
            n += 1
    return n
