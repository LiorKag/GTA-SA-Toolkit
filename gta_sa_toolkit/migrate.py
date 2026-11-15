# GTA SA Toolkit - opens .blend files made while the add-on used DragonFF (its earlier, private versions) without
# needing DragonFF. Two kinds of DragonFF data mattered to the add-on and Studio:
#   - collision / shadow objects: Object.dff.type 'COL' / 'SHA'  -> ob["gta_col"] "COL" / "SHA"
#     (+ gta_col_surface for collision spheres / boxes)
#   - 2DFX lights: Light.ext_2dfx (show mode, range, corona size, day / night / blink flags...)
#     -> light["gta_2dfx"], the same fields rwbuild writes
# Without DragonFF registered, Blender keeps those property groups as plain stored data (4.x: the ID's
# own custom properties; 5.x: its system properties), which is what is read here. Enum values are
# stored as the item's position. Nothing runs by itself: the "Update Older File" button in the add-on's
# preferences (gtatk.update_old_file) does it for the open file, together with Studio's old lighting
# settings. Objects already tagged are skipped, so pressing it twice is harmless.
import bpy

DFF_TYPES = {1: "COL", 2: "SHA"}          # DragonFF DFFObjectProps.type: OBJ, COL, SHA, 2DFX, CULL, NON


def stored(idb, name):
    """The stored property group `name` of an ID (an add-on's PointerProperty), read without the add-on:
    a dict-like IDPropertyGroup, or None."""
    getter = getattr(idb, "bl_system_properties_get", None)
    if getter is not None:
        try:
            sp = getter()
            g = sp.get(name) if sp is not None else None
            if g is not None:
                return g
        except (TypeError, RuntimeError):
            pass
    try:
        g = idb.get(name)
    except (TypeError, AttributeError):
        return None
    return g if hasattr(g, "get") else None


def _col_type(ob):
    g = stored(ob, "dff")
    if g is not None:
        return DFF_TYPES.get(int(g.get("type", 0)))
    try:                                   # DragonFF still installed: its property is live
        t = ob.dff.type
        return t if t in ("COL", "SHA") else None
    except AttributeError:
        return None


def light_2dfx(light_data):
    """gta_2dfx fields (rwbuild.LIGHT_FIELDS + alpha, look_direction) from a light's DragonFF settings,
    or None when it has none."""
    g = stored(light_data, "ext_2dfx")
    if g is None:
        return None

    def num(k, d=0):
        v = g.get(k, d)
        return v if isinstance(v, (int, float)) else d

    f1 = (bool(num("flag1_corona_check_obstacles")) * 1 | (int(num("flag1_fog_type")) & 1) * 2 |
          (int(num("flag1_fog_type")) >> 1 & 1) * 4 | bool(num("flag1_without_corona")) * 8 |
          bool(num("flag1_corona_only_at_long_distance")) * 16 | bool(num("flag1_at_day")) * 32 |
          bool(num("flag1_at_night")) * 64 | bool(num("flag1_blinking1")) * 128)
    f2 = (bool(num("flag2_corona_only_from_below")) * 1 | bool(num("flag2_blinking2")) * 2 |
          bool(num("flag2_update_height_above_ground")) * 4 | bool(num("flag2_check_view_vector")) * 8 |
          bool(num("flag2_blinking3")) * 16)
    fx = {"corona_far_clip": float(num("corona_far_clip", 0.0)), "point_light_range": float(num("point_light_range", 0.0)),
          "corona_size": float(num("corona_size", 0.0)), "shadow_size": float(num("shadow_size", 0.0)),
          "corona_show_mode": int(num("corona_show_mode")), "corona_reflection": int(bool(num("corona_enable_reflection"))),
          "corona_flare_type": int(num("corona_flare_type")), "shadow_color_mult": int(num("shadow_color_multiplier")),
          "flags1": int(f1), "corona_tex": str(g.get("corona_tex_name", "")), "shadow_tex": str(g.get("shadow_tex_name", "")),
          "shadow_z_distance": int(num("shadow_z_distance")), "flags2": int(f2),
          "alpha": int(round(float(num("alpha", 200 / 255)) * 255))}
    if num("export_view_vector"):
        vv = g.get("view_vector")
        if vv is not None:
            fx["look_direction"] = [int(x) for x in vv]
    return fx


def migrate_file():
    """Tag what DragonFF left in the open file. Returns (collision objects, lights) tagged."""
    n_col = n_light = 0
    for ob in bpy.data.objects:
        if ob.library is not None or ob.get("gta_col") is not None:
            continue
        t = _col_type(ob)
        if t is None:
            continue
        ob["gta_col"] = t
        g = stored(ob, "dff")
        if ob.type == 'EMPTY' and g is not None:
            day, night = int(g.get("col_day_light", 0)), int(g.get("col_night_light", 0))
            ob["gta_col_surface"] = [int(g.get("col_material", 0)), int(g.get("col_flags", 0)),
                                     int(g.get("col_brightness", 0)), day | (night << 4)]
        n_col += 1
    for d in bpy.data.lights:
        if d.library is not None or d.get("gta_2dfx") is not None:
            continue
        fx = light_2dfx(d)
        if fx is not None:
            d["gta_2dfx"] = fx
            n_light += 1
    return n_col, n_light


def update_file():
    """Everything an older file needs: DragonFF-era tags + the lighting settings of core 1.x files
    (Scene.gta_studio). Returns (collision objects, 2DFX lights, scenes) updated."""
    n_col, n_light = migrate_file()
    n_sc = 0
    from .studio import lighting
    for sc in bpy.data.scenes:
        if getattr(sc, "gta_studio", None) is not None:
            if lighting.migrate_old_settings(sc):
                n_sc += 1
            lighting.refresh_counts(sc)
    return n_col, n_light, n_sc


class GTATK_OT_update_old_file(bpy.types.Operator):
    bl_idname = "gtatk.update_old_file"
    bl_label = "Update Older File"
    bl_description = ("Only for .blend files made with an older, pre-release version of this add-on: tag its "
                      "collision objects and 2DFX lights the way this version does and bring over its old "
                      "lighting settings. New files don't need it")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        n_col, n_light, n_sc = update_file()
        if not (n_col or n_light or n_sc):
            self.report({'INFO'}, "Nothing to update: this file is already up to date")
        else:
            self.report({'INFO'}, "Updated %d collision objects, %d lights and %d scene settings"
                        % (n_col, n_light, n_sc))
        return {'FINISHED'}


def register():
    bpy.utils.register_class(GTATK_OT_update_old_file)


def unregister():
    bpy.utils.unregister_class(GTATK_OT_update_old_file)
