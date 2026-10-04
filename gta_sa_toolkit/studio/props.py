# GTA SA Studio - scene settings (Scene.gta_studio). Moved from core's Scene.gta_tk_x by an earlier version;
# lighting.migrate_old_settings carries over what a core 1.x file had saved.
import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, IntVectorProperty,
                       PointerProperty, StringProperty)

from . import timecyc

# light groups Studio has settings for (keys of core's api.LIGHT_CATEGORIES)
CAT_KEYS = ('STREET', 'TRAFFIC', 'NEON', 'BUILDING', 'BLINK', 'VEHICLE')

_MUTE = {"on": False}          # set while settings are copied in bulk (no apply per property)


def _upd_lights(self, context):
    if _MUTE["on"]:
        return
    from . import lighting
    try:
        lighting.apply(context.scene)
    except Exception as e:        # noqa
        print("[GTA SA Toolkit] lighting:", e)


def _upd_time(self, context):
    if _MUTE["on"]:
        return
    from . import lighting
    try:
        lighting.apply_time_of_day(context.scene)
        lighting.apply(context.scene)
    except Exception as e:        # noqa
        print("[GTA SA Toolkit] lighting:", e)


def _upd_sky(self, context):
    """Sky-only settings (fog, sky strength, sun heading): the map lights don't change."""
    if _MUTE["on"]:
        return
    from . import lighting
    try:
        lighting.apply_time_of_day(context.scene)
    except Exception as e:        # noqa
        print("[GTA SA Toolkit] sky:", e)


def _upd_shadow_pool(self, context):
    ee = context.scene.eevee
    if not hasattr(ee, "shadow_pool_size"):
        return
    if self.big_shadow_pool:
        from .compat import largest_shadow_pool
        big = largest_shadow_pool(ee)                  # 2 GB (1 GB in Blender 4.4)
        if ee.shadow_pool_size != big:
            self.shadow_pool_prev = ee.shadow_pool_size
        ee.shadow_pool_size = big
    else:
        ee.shadow_pool_size = self.shadow_pool_prev or '512'


def _upd_lens(self, context):
    if _MUTE["on"]:
        return
    from . import render
    render.apply_lens(context.scene)


def _minutes(self):
    return int(round(self.hour * 60.0)) % 1440


def _set_minutes(self, m):
    self.hour = min(23.99, max(0.0, m / 60.0))         # assigning `hour` runs its update (sky, lights)


def _get_clock_h(self):
    return _minutes(self) // 60


def _set_clock_h(self, v):
    _set_minutes(self, int(v) * 60 + _minutes(self) % 60)


def _get_clock_m(self):
    return _minutes(self) % 60


def _set_clock_m(self, v):
    _set_minutes(self, _minutes(self) // 60 * 60 + int(v))


WEATHER_ITEMS = [(w, timecyc.LABELS[w], "timecyc.dat weather %d" % i, i)
                 for i, w in enumerate(timecyc.WEATHERS[:timecyc.PLAYABLE])]
PRESET_HOURS = {'DAY': 12.0, 'DUSK': 19.0, 'NIGHT': 0.0}


# groups whose lamps cast shadows unless changed (traffic lights, blinking and neon lights are small and
# many: 1,158 traffic lights in LAe+LAe2 alone, so their shadows cost most and show least)
SHADOW_GROUPS = ('STREET', 'BUILDING')


def _group_key(pg):
    """'STREET' for the group at gta_studio.cat_street."""
    try:
        return pg.path_from_id().rsplit("cat_", 1)[1].upper()
    except (ValueError, IndexError):
        return ""


def _get_cast(self):
    return (_group_key(self) in SHADOW_GROUPS) if self.shadow_mode == 0 else self.shadow_mode == 1


def _set_cast(self, v):
    self.shadow_mode = 1 if v else 2


def _swatch(i):
    return lambda self: (self.red[i], self.green[i], self.blue[i])


# 24 read-only colour swatches (the sky at each hour for the scene's weather), from three stored lists
# (a vector property holds at most 32 numbers)
_SWATCHES = {c: FloatVectorProperty(size=24, options={'HIDDEN'}) for c in ("red", "green", "blue")}
for _h in range(24):
    _SWATCHES["h%02d" % _h] = FloatVectorProperty(
        name="%02d:00" % _h, subtype='COLOR_GAMMA', size=3, get=_swatch(_h),
        description="The sky at %02d:00 with the chosen timecyc and weather" % _h)
GTASTUDIO_Swatches = type("GTASTUDIO_Swatches", (bpy.types.PropertyGroup,), {"__annotations__": _SWATCHES})


def _upd_timecyc(self, context):
    _upd_time(self, context)


class GTASTUDIO_LightGroup(bpy.types.PropertyGroup):
    enabled: BoolProperty(name="On", default=True, update=_upd_lights)
    shadow_mode: IntProperty(options={'HIDDEN'}, update=_upd_lights,
                             description="0 = the group's default (SHADOW_GROUPS), 1 = casts, 2 = doesn't")
    cast_shadows: BoolProperty(name="Cast Shadows", get=_get_cast, set=_set_cast,
                               description="This group's lamps cast shadows while the Scene tab's Shadows is on")
    strength: FloatProperty(name="Strength", default=1.0, min=0.0, soft_max=10.0, update=_upd_lights)
    tint: FloatVectorProperty(name="Tint", subtype='COLOR', size=3, min=0.0, max=1.0,
                              default=(1.0, 0.85, 0.6), update=_upd_lights)
    tint_amount: FloatProperty(name="Tint Amount", default=0.0, min=0.0, max=1.0,
                               subtype='FACTOR', update=_upd_lights)


class GTASTUDIO_SceneProps(bpy.types.PropertyGroup):
    # map lights
    lights_enabled: BoolProperty(name="Map Lights", default=True, update=_upd_lights)
    light_master: FloatProperty(name="Master", default=1.0, min=0.0, soft_max=10.0, update=_upd_lights)
    light_radius: FloatProperty(name="Softness", default=0.25, min=0.0, soft_max=3.0, unit='LENGTH',
                                update=_upd_lights)
    light_shadows: BoolProperty(name="Shadows", default=False, update=_upd_lights,
                                description="Shadows from the map lights of groups set to Cast Shadows. They look "
                                            "nicer but are much slower with many lights")
    big_shadow_pool: BoolProperty(name="More Shadow Memory", default=False, update=_upd_shadow_pool,
                                  description="Give EEVEE 2 GB for shadows (Shadow Pool) so fewer shadows go "
                                              "missing with many lights. Uses more graphics memory, not faster. "
                                              "Off puts the old setting back")
    shadow_pool_prev: StringProperty(options={'HIDDEN'})
    lights_follow_time: BoolProperty(name="Follow Time of Day", default=True, update=_upd_lights,
                                     description="Night-only lamps switch off by day, like in the game")
    # time and weather (sky, sun and fog from the game's timecyc.dat)
    hour: FloatProperty(name="Hour", default=0.0, min=0.0, max=23.99, step=25, precision=2,
                        description="Time of day, 0-24 (12.5 = half past noon). Sky, sun, fog and map lights follow",
                        update=_upd_time)
    # the Scene tab shows the hour as Hour + Min fields (a float field can't read "12:30"); both only
    # read and write `hour`, so nothing extra is stored
    clock_h: IntProperty(name="Hour", min=0, max=23, get=_get_clock_h, set=_set_clock_h,
                         description="Hour of the day. Sky, sun, fog and map lights follow")
    clock_m: IntProperty(name="Min", min=0, max=59, get=_get_clock_m, set=_set_clock_m,
                         description="Minutes past the hour")
    weather: EnumProperty(name="Weather", items=WEATHER_ITEMS, default='SUNNY_LA', update=_upd_time,
                          description="Weather type from the game's timecyc.dat")
    fog: BoolProperty(name="Fog", default=False, update=_upd_sky,
                      description="Haze in the sky's horizon colour, thicker towards the game's view distance. "
                                  "Slow in Cycles")
    control_sky: BoolProperty(name="Set Sky & Sun", default=True, update=_upd_time)
    sky_strength: FloatProperty(name="Sky", default=1.0, min=0.0, soft_max=5.0, update=_upd_sky)
    sun_heading: FloatProperty(name="Sun Heading", default=0.0, min=-180, max=360, update=_upd_sky,
                               description="Turns the sun's path: 0 = rises in the east, sets in the west")
    timecyc_note: StringProperty(options={'HIDDEN'})     # problem reading timecyc.dat, shown in the tab
    # which timecyc drives the sky (timecycs.py): "" = the older default, GAME, STOCK, "F:<path>", "M:<path>"
    timecyc_choice: StringProperty(options={'HIDDEN'}, update=_upd_timecyc)
    timecyc_label: StringProperty(options={'HIDDEN'})
    timecyc_data: StringProperty(options={'HIDDEN'})     # the chosen file's numbers, kept in the .blend
    timecyc_data_sig: StringProperty(options={'HIDDEN'})
    swatches: PointerProperty(type=GTASTUDIO_Swatches)
    swatch_key: StringProperty(options={'HIDDEN'})
    # camera & render
    lens_preset: EnumProperty(name="Lens", update=_upd_lens, default='GAMEPLAY', items=(
        ('GAMEPLAY', "Gameplay (70°)", "The game's normal camera field of view", 0),
        ('CINEMATIC', "Cinematic (40°)", "Narrower, cutscene-like framing", 1),
        ('ZOOM', "Zoom (20°)", "Long lens, like the camera/sniper zoom", 2),
        ('WIDE', "Wide (90°)", "Very wide, for interiors and tight spots", 3)))
    turntable_frames: IntProperty(name="Frames", default=120, min=12, soft_max=600,
                                  description="Length of one full turn")
    # Edit tab: Pawn export
    pawn_function: EnumProperty(name="Function", default='OBJECT', items=(
        ('OBJECT', "CreateObject", "CreateObject lines: plain SA-MP objects (1000 per server)", 0),
        ('DYNAMIC', "DynamicObject", "CreateDynamicObject lines: Streamer plugin objects (no limit)", 1)))
    pawn_path: StringProperty(subtype='FILE_PATH', options={'HIDDEN'})      # last saved .pwn
    # Edit tab: traffic and ped paths
    path_area: EnumProperty(name="Area", default='IMPORT', items=(
        ('IMPORT', "Imported Map", "Around the map import selected in the World tab", 0),
        ('CURSOR', "Around 3D Cursor", "A circle around the 3D cursor", 1)))
    path_radius: FloatProperty(name="Radius", default=300.0, min=20.0, soft_max=3000.0, unit='LENGTH',
                               description="How far from the 3D cursor to draw paths")
    path_roads: BoolProperty(name="Roads", default=True, description="Draw every traffic lane (orange)")
    path_sidewalks: BoolProperty(name="Sidewalks", default=True, description="Draw the ped paths (cyan)")
    path_info: StringProperty(options={'HIDDEN'})      # what Draw Paths made, shown in the tab
    follow_speed: FloatProperty(name="Speed", default=40.0, min=1.0, soft_max=160.0, max=400.0,
                                description="Top speed in km/h (the vehicle speeds up and slows down at the ends)")
    follow_ease: FloatProperty(name="Ease In/Out", default=0.5, min=0.0, soft_max=3.0, max=10.0, unit='TIME_ABSOLUTE',
                               description="Seconds to speed up at the start and slow down at the end (0 = full speed "
                                           "from the first frame). Shorter on short routes, so the top speed is reached")
    follow_ped_speed: FloatProperty(name="Speed", default=1.0, min=0.25, max=4.0, precision=2,
                                    description="Times the animation's own speed: the walk cycle plays faster too, "
                                                "so the feet still don't slide")
    follow_gait: EnumProperty(name="Gait", default='WALK', items=(
        ('WALK', "Walk", "WALK_civi from ped.ifp, at its own speed", 0),
        ('RUN', "Run", "RUN_civi from ped.ifp, at its own speed", 1),
        ('SPRINT', "Sprint", "sprint_civi from ped.ifp, at its own speed", 2)))
    # light groups
    cat_street: PointerProperty(type=GTASTUDIO_LightGroup)
    cat_traffic: PointerProperty(type=GTASTUDIO_LightGroup)
    cat_neon: PointerProperty(type=GTASTUDIO_LightGroup)
    cat_building: PointerProperty(type=GTASTUDIO_LightGroup)
    cat_blink: PointerProperty(type=GTASTUDIO_LightGroup)
    cat_vehicle: PointerProperty(type=GTASTUDIO_LightGroup)
    # tagged lights per CAT_KEYS entry, stored so the Scene tab never counts in draw()
    light_counts: IntVectorProperty(size=len(CAT_KEYS), default=(0,) * len(CAT_KEYS), options={'HIDDEN'})
    # core 1.x lighting settings of this file were looked at (and copied over if there were any)
    migrated: BoolProperty(default=False, options={'HIDDEN'})


classes = (GTASTUDIO_Swatches, GTASTUDIO_LightGroup, GTASTUDIO_SceneProps)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.gta_studio = PointerProperty(type=GTASTUDIO_SceneProps)


def unregister():
    del bpy.types.Scene.gta_studio
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
