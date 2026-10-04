# GTA SA Studio - the Scene tab (inside the "GTA SA Toolkit" panel) and plugging Studio's tabs and
# sections into the panel. Draw code only reads stored values.
import os

import bpy


from . import corelink, lighting, ops
from .props import PRESET_HOURS

ICON_DIR = os.path.join(os.path.dirname(__file__), "icons")
TAB_IDS = ("SCENE", "ANIMATE")


def _section(layout, idname, title, icon='NONE', closed=False):
    """Collapsible sub-section (Blender 4.2+ layout panels). Returns the body layout or None."""
    header, body = layout.panel("GTASTUDIO_" + idname, default_closed=closed)
    header.label(text=title, icon=icon)
    return body


# ----------------------------------------------------------------------------- Scene tab
# EEVEE draws at most this many lights (tested in Blender 5.2: 65,536 prints "Too many lights in the
# scene" and the extra ones are simply not drawn); more lights casting shadows than SHADOW_WARN fill its
# shadow memory (LAe+LAe2 with all shadows: 12,300 of 2,048 pages, 1.35 s per sample from the air)
EEVEE_MAX_LIGHTS = 65535
SHADOW_WARN = 250


def draw_scene(layout, context):
    p = context.scene.gta_studio
    api = corelink.api()
    c = lighting.stored_counts(context.scene)      # stored after imports / Find Lights, never counted here
    total = sum(v for k, v in c.items() if k != 'VEHICLE')
    b = _section(layout, "time", "Time & Weather", 'TIME')
    if b:
        r = b.row(align=True)
        r.scale_y = 1.2
        for key, lab, ic in (('DAY', "Day", 'LIGHT_SUN'), ('DUSK', "Dusk", 'LIGHT_HEMI'), ('NIGHT', "Night", 'SOLO_ON')):
            r.operator("gtastudio.time_preset", text=lab, icon=ic,
                       depress=abs(p.hour - PRESET_HOURS[key]) < 0.01).when = key
        sp = getattr(context, "space_data", None)
        if sp is not None and sp.type == 'VIEW_3D':
            lit = ops.shows_scene_lighting(sp.shading)
            r = b.row(align=True)
            r.operator("gtastudio.preview_lighting", icon='SHADING_RENDERED' if lit else 'SHADING_TEXTURE',
                       depress=lit)
            if not lit:
                b.label(text="Time and weather don't show in this view", icon='INFO')
        # which timecyc drives the sky, and the sky across the day for this weather with it
        r = b.row(align=True)
        r.operator_menu_enum("gtastudio.timecyc_choose", "choice", icon='WORLD',
                             text=p.timecyc_label or "Game: timecyc.dat")
        r.operator("gtastudio.timecyc_add", text="", icon='FILEBROWSER')
        if p.timecyc_choice[:2] in ("F:", "M:"):
            r.operator("gtastudio.timecyc_remove", text="", icon='X')
        if p.swatch_key:
            s = b.row(align=True)
            s.scale_y = 0.6
            for h in range(24):
                s.prop(p.swatches, "h%02d" % h, text="")
        col = b.column(align=True)
        r = col.row(align=True)                 # the time once: Hour slider | Min
        s = r.row(align=True)
        s.scale_x = 2.0
        s.prop(p, "clock_h", slider=True)
        r.prop(p, "clock_m")
        col.prop(p, "weather", text="")
        b.prop(p, "fog")
        if p.timecyc_note:
            b.label(text=p.timecyc_note, icon='ERROR')

    b = _section(layout, "sky", "Sky & Sun", 'WORLD', closed=True)
    if b:
        b.prop(p, "control_sky")
        col = b.column(align=True)
        col.active = p.control_sky
        col.prop(p, "sky_strength", text="Sky Strength")
        col.prop(p, "sun_heading")

    b = _section(layout, "lights", "Map Lights (%d)" % total, 'LIGHT')
    if b:
        r = b.row(align=True)
        r.prop(p, "lights_enabled", text="On", toggle=True)
        r.prop(p, "light_master", text="Master")
        col = b.column(align=True)
        col.prop(p, "light_radius", text="Softness")
        r = col.row(align=True)
        r.prop(p, "light_shadows", toggle=True)
        r.prop(p, "lights_follow_time", text="Follow Time", toggle=True)
        if p.light_shadows:
            casting = sum(c.get(k, 0) for k in c if k != 'VEHICLE' and getattr(p, "cat_" + k.lower()).enabled
                          and getattr(p, "cat_" + k.lower()).cast_shadows)
            if casting > SHADOW_WARN:
                col2 = b.column(align=True)
                col2.label(text="%d lights cast shadows: very slow" % casting, icon='ERROR')
                col2.label(text="and some shadows will be missing", icon='BLANK1')
            b.prop(p, "big_shadow_pool")
        if total > EEVEE_MAX_LIGHTS:
            col2 = b.column(align=True)
            col2.label(text="EEVEE shows at most %s lights:" % format(EEVEE_MAX_LIGHTS, ","), icon='ERROR')
            col2.label(text="%s won't light up" % format(total - EEVEE_MAX_LIGHTS, ","), icon='BLANK1')
        b.operator("gtastudio.find_lights", text="Find Lights in Scene", icon='VIEWZOOM')

    b = _section(layout, "groups", "Light Groups", 'OUTLINER_OB_LIGHT')
    if b:
        for key, label, _ in (api.LIGHT_CATEGORIES if api else ()):
            cp = getattr(p, "cat_" + key.lower(), None)
            if cp is None:                     # a group newer than this Studio
                continue
            # one row per group: on/off · name (count) · strength · tint; tint amount folds out
            h, body = b.panel("GTASTUDIO_grp_" + key.lower(), default_closed=True)
            h.prop(cp, "enabled", text="")
            h.label(text=label if key == 'VEHICLE' else "%s (%d)" % (label, c.get(key, 0)))
            r = h.row(align=True)
            r.active = cp.enabled
            s = r.row(align=True)
            s.ui_units_x = 3.2
            s.prop(cp, "strength", text="")
            s = r.row(align=True)
            s.ui_units_x = 1.4
            s.prop(cp, "tint", text="")
            if body:
                body.active = cp.enabled
                body.prop(cp, "tint_amount", text="Tint Amount", slider=True)
                if key != 'VEHICLE':
                    body.prop(cp, "cast_shadows")

    draw_camera(layout, context)


def draw_camera(layout, context):
    p = context.scene.gta_studio
    b = _section(layout, "camera", "Camera & Render", 'CAMERA_DATA', closed=True)
    if not b:
        return
    b.prop(p, "lens_preset")
    b.operator("gtastudio.add_camera", icon='OUTLINER_OB_CAMERA')
    r = b.row(align=True)
    r.operator("gtastudio.turntable", icon='FILE_REFRESH')
    r.prop(p, "turntable_frames")
    b.operator("gtastudio.frame_map_area", icon='RENDER_STILL')


def _tab(api, draw_fn):
    api.register_tab("SCENE", "Scene", "Scene", os.path.join(ICON_DIR, "tab_scene.png"), draw_fn, order=60,
                     icon_active=os.path.join(ICON_DIR, "tab_scene_on.png"),
                     description="Time of day and map lighting (GTA SA Studio)")


def add_tabs(api, areas):
    """Studio's own tabs (Animate, Scene) and its sections in core's tabs (Vehicles, Peds, World), for the
    areas switched on (corelink.AREAS)."""
    from . import edit, ui_vehicle
    if "scene" in areas:
        _tab(api, draw_scene)
    if "animate" in areas:
        from .ui_animate import draw_animate
        api.register_tab("ANIMATE", "Animate", "Animate", os.path.join(ICON_DIR, "tab_animate.png"), draw_animate,
                         order=50, icon_active=os.path.join(ICON_DIR, "tab_animate_on.png"),
                         description="Combine IFP clips into one animation for a ped, bake and export it (GTA SA Studio)")
    if "vehicle" in areas:
        ui_vehicle.add_sections(api)
    edit.add_sections(api, areas)


def remove_tabs(api):
    for t in TAB_IDS:
        api.unregister_tab(t)
    from . import edit, ui_vehicle
    ui_vehicle.remove_sections(api)
    edit.remove_sections(api)


classes = ()


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
