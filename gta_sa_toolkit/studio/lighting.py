# GTA SA Studio - map lighting and time/weather.
#   Map lights: per-group strength/tint for the GTA lights core tags at import (see core's api.py),
#   shadows, and night-only lamps following the hour. Studio hears about new lights from core's
#   on_import_done event and only touches those; the whole file is walked only when a setting changes.
#   Sky, sun and fog: from the game's timecyc.dat (timecyc.py) for the chosen hour and weather; the
#   sun's path is worked out from the hour (timecyc.dat doesn't store it). Without a readable
#   timecyc.dat, built-in day/dusk/night looks are blended by hour instead.
import math

import bpy
from mathutils import Vector

from . import corelink
from .props import _MUTE, CAT_KEYS


def _mix(a, b, t):
    return [x * (1 - t) + y * t for x, y in zip(a, b)]


def night_factor(hour):
    """0 by day, 1 at night: night-only lamps fade on 18:00-20:00 and off 05:00-07:00."""
    h = float(hour) % 24.0
    if 7.0 <= h < 18.0:
        return 0.0
    if 18.0 <= h < 20.0:
        return (h - 18.0) / 2.0
    if 5.0 <= h < 7.0:
        return 1.0 - (h - 5.0) / 2.0
    return 1.0


def apply(scene, lights=None, materials=None):
    """Push the Scene tab settings to tagged lights and vehicle-light materials.
    lights / materials: just these (a new import); None = every one in the file."""
    p = scene.gta_studio
    nf = night_factor(p.hour)
    master = p.light_master if p.lights_enabled else 0.0
    for d in (bpy.data.lights if lights is None else lights):
        cat = d.get("gta_cat")
        if cat not in CAT_KEYS or cat == 'VEHICLE':
            continue
        cp = getattr(p, "cat_" + cat.lower())
        on = cp.enabled and master > 0
        day, night = bool(d.get("gta_day", True)), bool(d.get("gta_night", True))
        time_mul = (nf if night else 0.0) + ((1.0 - nf) if day else 0.0)
        if day and night:
            time_mul = 1.0
        if p.lights_follow_time is False:
            time_mul = 1.0
        base = d.get("gta_base_energy", 50.0)
        d.energy = base * master * cp.strength * time_mul if on else 0.0
        bc = list(d.get("gta_base_color", d.color[:3]))
        d.color = _mix(bc, list(cp.tint), cp.tint_amount)
        d.shadow_soft_size = p.light_radius
        d.use_shadow = p.light_shadows and cp.cast_shadows
    # vehicle light materials (emission, set through core)
    api = corelink.api()
    if api is None:
        return
    vp = p.cat_vehicle
    strength = (vp.strength * master * 4.0 * max(nf, 0.0 if p.lights_follow_time else 1.0)) \
        if (vp.enabled and master > 0) else 0.0
    for m in (bpy.data.materials if materials is None else materials):
        kind = m.get("gta_vehicle_light")
        if not kind:
            continue
        if m.get("gta_own_lights"):                  # the vehicle's own settings (Vehicle tab)
            root = m.get("gta_vehicle_root")
            if root is not None and root.gta_vehicle_studio.own_lights:
                from . import vehicle
                try:
                    vehicle.light_material(m, root, scene)
                except Exception:        # noqa
                    pass
                continue
        col = (1.0, 0.95, 0.85) if kind == "head" else (0.9, 0.05, 0.03)
        try:
            api.set_vehicle_light(m, _mix(col, list(vp.tint), vp.tint_amount), strength)
        except Exception:        # noqa
            pass


# ----------------------------------------------------------------------------- sky, sun, fog
SKY_GAIN = 1.0            # timecyc sky colours are display colours: strength 1 looks like the game
SUN_W = 5.0               # W/m2 of a clear midday sun
AMBIENT_GREY = 0.6        # the sky's light is this much greyer than the sky you see (a pure blue fill
                          # turned shaded walls blue; the game's ambient light is near neutral)
SUNRISE, SUNSET = 6.0, 20.0
MAX_ELEVATION = 70.0

# used when timecyc.dat can't be read: (hour, sky top, sky bottom, sun colour), 0-255 like the file
_BUILTIN = (
    (0.0, (5, 8, 20), (10, 14, 30), (0, 0, 0)),
    (5.0, (5, 8, 20), (10, 14, 30), (0, 0, 0)),
    (7.0, (150, 190, 240), (230, 180, 140), (255, 200, 150)),
    (12.0, (110, 160, 235), (175, 205, 240), (255, 250, 240)),
    (18.0, (110, 160, 235), (175, 205, 240), (255, 250, 240)),
    (19.5, (120, 90, 110), (240, 140, 90), (255, 140, 70)),
    (21.0, (5, 8, 20), (10, 14, 30), (0, 0, 0)),
)


def _builtin_sample(hour):
    h = float(hour) % 24.0
    rows = _BUILTIN + ((24.0,) + _BUILTIN[0][1:],)
    i = max(k for k in range(len(rows) - 1) if rows[k][0] <= h)
    a, b = rows[i], rows[i + 1]
    t = (h - a[0]) / (b[0] - a[0])

    def lerp(x, y):
        return tuple(u + (v - u) * t for u, v in zip(x, y))
    return {"sky_top": lerp(a[1], b[1]), "sky_bot": lerp(a[2], b[2]), "sun_core": lerp(a[3], b[3]),
            "sun_size": 1.0, "far_clip": 800.0, "fog_start": 100.0, "dir_mult": 1.0}


def timecyc_path():
    """Studio's Preferences override, else the game folder's data/timecyc.dat (through core)."""
    try:
        prefs = corelink.prefs()
        own = bpy.path.abspath(prefs.timecyc_path) if prefs.timecyc_path else ""
    except (KeyError, AttributeError):
        own = ""
    if own:
        return own
    api = corelink.api()
    return api.game_file("data/timecyc.dat") if api is not None else None


def sample(scene):
    """(values, typical DirMult, note) for the scene's hour and weather, from the timecyc the scene
    chose (timecycs.py). note is "" when it was read from its file, else what was used instead."""
    from . import timecycs
    p = scene.gta_studio
    tc, note = timecycs.resolve(scene)
    if tc is None:
        return _builtin_sample(p.hour), 1.0, note
    timecycs.update_swatches(scene, tc)
    return tc.sample(p.weather, p.hour), tc.dir_mult_typical, note


def srgb_to_linear(c):
    c = max(0.0, min(1.0, c))
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgb(v255):
    return tuple(srgb_to_linear(x / 255.0) for x in v255[:3])


def sun_vector(hour, heading=0.0):
    """(unit vector towards the sun (x east, y north, z up), elevation in degrees): rises in the east
    at SUNRISE, highest in the south halfway to SUNSET, sets in the west, goes round below the
    horizon at night."""
    h = float(hour) % 24.0
    day_len = SUNSET - SUNRISE
    if SUNRISE <= h <= SUNSET:
        u = (h - SUNRISE) / day_len                     # 0..1 over the day
        elev = MAX_ELEVATION * math.sin(math.pi * u)
        az = 90.0 + 180.0 * u
    else:
        u = ((h - SUNSET) % 24.0) / (24.0 - day_len)     # 0..1 over the night
        elev = -MAX_ELEVATION * math.sin(math.pi * u)
        az = 270.0 + 180.0 * u
    a, e = math.radians(az + heading), math.radians(elev)
    return Vector((math.cos(e) * math.sin(a), math.cos(e) * math.cos(a), math.sin(e))), elev


def _smooth(e0, e1, x):
    t = max(0.0, min(1.0, (x - e0) / (e1 - e0)))
    return t * t * (3.0 - 2.0 * t)


def sun_strength(values, typical_mult, elevation):
    """Clear sky at noon = SUN_W. The file's sun colour is black in rain and near black when cloudy,
    so its brightness is the weather's say in how strong direct sunlight is."""
    core = max(values["sun_core"][:3]) / 255.0
    mult = float(values.get("dir_mult", 1.0)) / (typical_mult or 1.0)
    return SUN_W * core * mult * _smooth(-2.0, 8.0, elevation)


_NODES = ("GTA Coord", "GTA Split", "GTA Horizon", "GTA Sky Mix", "GTA Background", "GTA Fog", "GTA Output",
          "GTA Light Path", "GTA Ambient", "GTA Camera Mix")


def _sky_nodes(world):
    """The GTA Sky node setup (built once, then only values change). The camera sees the gradient
    from the horizon colour to the overhead colour (GTA Background); everything else is lit by a
    greyer average of the two (GTA Ambient), switched by Light Path > Is Camera Ray. Plus a fog volume
    linked only while Fog is on."""
    world.use_nodes = True
    nt = world.node_tree
    if all(n in nt.nodes for n in _NODES):
        return nt
    nt.nodes.clear()
    new = nt.nodes.new
    co = new("ShaderNodeTexCoord")
    sp = new("ShaderNodeSeparateXYZ")
    mr = new("ShaderNodeMapRange")
    mx = new("ShaderNodeMix")
    mx.data_type = 'RGBA'
    bg = new("ShaderNodeBackground")
    fog = new("ShaderNodeVolumePrincipled")
    out = new("ShaderNodeOutputWorld")
    lp = new("ShaderNodeLightPath")
    amb = new("ShaderNodeBackground")
    cm = new("ShaderNodeMixShader")
    for node, name, x, y in ((co, _NODES[0], -1000, 0), (sp, _NODES[1], -800, 0), (mr, _NODES[2], -600, 0),
                             (mx, _NODES[3], -400, 0), (bg, _NODES[4], -200, 0), (fog, _NODES[5], 0, -300),
                             (out, _NODES[6], 200, 0), (lp, _NODES[7], -200, 300), (amb, _NODES[8], -200, -150),
                             (cm, _NODES[9], 0, 0)):
        node.name = node.label = name
        node.location = (x, y)
    mr.inputs["From Min"].default_value = 0.0
    mr.inputs["From Max"].default_value = 0.35       # full overhead colour from ~20 degrees up
    mr.clamp = True
    nt.links.new(co.outputs["Generated"], sp.inputs[0])
    nt.links.new(sp.outputs["Z"], mr.inputs["Value"])
    nt.links.new(mr.outputs["Result"], mx.inputs[0])
    nt.links.new(mx.outputs[2], bg.inputs["Color"])
    nt.links.new(lp.outputs["Is Camera Ray"], cm.inputs[0])
    nt.links.new(amb.outputs[0], cm.inputs[1])        # lighting (not a camera ray)
    nt.links.new(bg.outputs[0], cm.inputs[2])         # what the camera sees
    nt.links.new(cm.outputs[0], out.inputs["Surface"])
    return nt


def apply_time_of_day(scene):
    """Sky, sun and fog for the scene's hour + weather (sky and sun untouched when Set Sky & Sun is
    off). Returns the timecyc values used."""
    p = scene.gta_studio
    values, typical, note = sample(scene)
    if p.timecyc_note != note:
        p.timecyc_note = note
    from . import timecycs
    timecycs.refresh_label(scene)
    if not p.control_sky:
        return values
    world = scene.world
    if world is None or not world.name.startswith("GTA Sky"):
        world = bpy.data.worlds.get("GTA Sky") or bpy.data.worlds.new("GTA Sky")
        scene.world = world
    nt = _sky_nodes(world)
    top, bot = rgb(values["sky_top"]), rgb(values["sky_bot"])
    mx = nt.nodes["GTA Sky Mix"]
    mx.inputs[6].default_value = (*bot, 1.0)          # A: horizon
    mx.inputs[7].default_value = (*top, 1.0)          # B: overhead
    nt.nodes["GTA Background"].inputs["Strength"].default_value = SKY_GAIN * p.sky_strength
    avg = [(a + b) / 2.0 for a, b in zip(top, bot)]
    grey = 0.2126 * avg[0] + 0.7152 * avg[1] + 0.0722 * avg[2]
    amb = nt.nodes["GTA Ambient"]
    amb.inputs["Color"].default_value = (*_mix(avg, [grey] * 3, AMBIENT_GREY), 1.0)
    amb.inputs["Strength"].default_value = SKY_GAIN * p.sky_strength
    world.color = bot                                 # solid-view / Workbench background

    fog, out = nt.nodes["GTA Fog"], nt.nodes["GTA Output"]
    fog.inputs["Color"].default_value = (*bot, 1.0)
    far = max(float(values["far_clip"]), 50.0)
    fog.inputs["Density"].default_value = 3.0 / far   # ~5% of the light gets through at the far clip
    linked = out.inputs["Volume"].is_linked
    if p.fog and not linked:
        nt.links.new(fog.outputs[0], out.inputs["Volume"])
    elif not p.fog and linked:
        for link in list(out.inputs["Volume"].links):
            nt.links.remove(link)
    if p.fog and hasattr(scene, "eevee") and hasattr(scene.eevee, "volumetric_end"):
        scene.eevee.volumetric_end = max(scene.eevee.volumetric_end, far)

    vec, elev = sun_vector(p.hour, p.sun_heading)
    sun = bpy.data.objects.get("GTA Sun")
    if sun is None:
        sun = bpy.data.objects.new("GTA Sun", bpy.data.lights.new("GTA Sun", 'SUN'))
        scene.collection.objects.link(sun)
    core = [c / 255.0 for c in values["sun_core"][:3]]
    peak = max(core) or 1.0
    # the file's sun colour is the disc's: used at full brightness, softened towards white
    sun.data.color = _mix([1.0, 1.0, 1.0], [c / peak for c in core], 0.6)
    sun.data.energy = sun_strength(values, typical, elev) * p.sky_strength
    sun.data.angle = math.radians(max(0.5, min(5.0, 0.5 + 0.5 * float(values["sun_size"]))))
    sun.rotation_mode = 'QUATERNION'
    sun.rotation_quaternion = Vector((0.0, 0.0, 1.0)).rotation_difference(vec)
    sun.hide_viewport = sun.hide_render = sun.data.energy <= 0.0
    return values


# ----------------------------------------------------------------------------- stored counts
def counts():
    """Map lights per group as EEVEE gets them: every copy placed through a collection instance counts
    (LAe+LAe2: 73 lamp objects, 2,291 lights). Evaluates the scene: never call this from draw()
    (~0.07 s for all of Los Santos, 20k objects)."""
    out = {k: 0 for k in CAT_KEYS}
    dg = bpy.context.evaluated_depsgraph_get()
    for oi in dg.object_instances:
        ob = oi.instance_object if oi.is_instance else oi.object
        if ob.type != 'LIGHT':
            continue
        c = ob.original.data.get("gta_cat")
        if c in out:
            out[c] += 1
    return out


def refresh_counts(scene):
    """Store counts() on the scene for the Scene tab."""
    c = counts()
    vals = [c[k] for k in CAT_KEYS]
    p = scene.gta_studio
    if list(p.light_counts) != vals:
        p.light_counts = vals
    return c


def stored_counts(scene):
    return dict(zip(CAT_KEYS, scene.gta_studio.light_counts))


# ----------------------------------------------------------------------------- core 1.x files
# sun_heading isn't copied: core 1.x turned the sun itself, Studio turns the sun's whole path
_OLD_KEYS = ("lights_enabled", "light_master", "light_radius", "light_shadows", "lights_follow_time",
             "control_sky", "sky_strength")
_OLD_HOURS = (12.0, 19.0, 0.0)            # core 1.x time_of_day: DAY, DUSK, NIGHT
_OLD_GROUP_KEYS = ("enabled", "strength", "tint", "tint_amount")


def _old_settings(scene):
    """What core 1.x stored under Scene.gta_tk_x (only values that were changed are stored)."""
    try:
        store = scene.bl_system_properties_get() if hasattr(scene, "bl_system_properties_get") else scene
        return store.get("gta_tk_x") if store is not None else None
    except Exception:            # noqa
        return None


def migrate_old_settings(scene):
    """Copy the lighting settings a core 1.x file had into Scene.gta_studio, once per scene.
    Lights themselves already look like that, so nothing is re-applied. Returns True if copied."""
    p = scene.gta_studio
    if p.migrated:
        return False
    old = _old_settings(scene)
    p.migrated = True
    if not old:
        return False
    moved = False
    _MUTE["on"] = True
    try:
        for k in _OLD_KEYS:
            if k in old:
                setattr(p, k, old[k])
                moved = True
        if "time_of_day" in old:
            i = int(old["time_of_day"])
            if 0 <= i < len(_OLD_HOURS):
                p.hour = _OLD_HOURS[i]
                moved = True
        for key in CAT_KEYS:
            og = old.get("cat_" + key.lower())
            if not og:
                continue
            g = getattr(p, "cat_" + key.lower())
            for k in _OLD_GROUP_KEYS:
                if k in og:
                    setattr(g, k, og[k][:] if k == "tint" else og[k])
                    moved = True
    finally:
        _MUTE["on"] = False
    return moved


# ----------------------------------------------------------------------------- core events
def _on_import(objects):
    """core's on_import_done: set up just the lights and vehicle lights this import created."""
    sc = bpy.context.scene
    if getattr(sc, "gta_studio", None) is None:
        return
    lights, mats = set(), set()
    for o in objects:
        t = o.type
        if t == 'LIGHT':
            d = o.data
            if d is not None and d.get("gta_cat") is not None:
                lights.add(d)
        elif t == 'MESH':
            for sl in o.material_slots:
                m = sl.material
                if m is not None and m.get("gta_vehicle_light"):
                    mats.add(m)
    if lights or mats:
        apply(sc, lights=lights, materials=mats)
    from . import vehicle
    vehicle.on_import(objects)
    refresh_counts(sc)              # every import: new copies of lamps already in the file count too


def _on_removed():
    sc = bpy.context.scene
    if getattr(sc, "gta_studio", None) is not None:
        refresh_counts(sc)


def _prepare_file():
    for sc in bpy.data.scenes:
        if getattr(sc, "gta_studio", None) is not None:
            migrate_old_settings(sc)
            refresh_counts(sc)


def connect(api):
    api.on_import_done(_on_import)
    api.on_import_removed(_on_removed)
    try:
        _prepare_file()
    except Exception:            # noqa - restricted context while Blender starts: load_post does it
        pass


def disconnect(api):
    api.remove_listener(_on_import)
    api.remove_listener(_on_removed)


@bpy.app.handlers.persistent
def _on_load(*_):
    try:
        _prepare_file()
    except Exception as e:       # noqa
        print("[GTA SA Toolkit] lighting:", e)


def register():
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
