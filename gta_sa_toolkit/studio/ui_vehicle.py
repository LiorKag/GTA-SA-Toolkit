# GTA SA Studio - its sections in core's Vehicles tab: the selected vehicle, then its colours, lights,
# paintjobs, doors/extras and upgrades. Draws only what vehicle.setup() stored on the vehicle's root.
from . import vehicle
from .vehicle import PAINT_LABELS


def _root(context):
    return vehicle.find_root(context.active_object)


def _ready(context):
    """The selected vehicle's root when it is set up for these sections, else None."""
    root = _root(context)
    return root if root is not None and vehicle.is_current(root) else None


def sec_selected(layout, context):
    root = _root(context)
    if root is None:
        col = layout.column(align=True)
        col.label(text="Select an imported vehicle", icon='INFO')
        col.label(text="(any part of it)", icon='BLANK1')
        return
    vs = root.gta_vehicle_studio
    if root.get("gta_mod"):                # a custom model: its name, what it borrows from
        layout.label(text="%s  ·  custom" % (root.get("gta_vehicle_name") or root.name), icon='AUTO')
        if root.get("gta_mod_base_name"):
            layout.label(text="Based on the game's %s" % root["gta_mod_base_name"], icon='BLANK1')
        for w in root.get("gta_mod_warnings", []):
            layout.label(text=w, icon='ERROR')
    else:
        # in-game name (stored at import) · model
        layout.label(text="%s  ·  %s" % (root.get("gta_vehicle_name") or root.name, root["gta_vehicle"]), icon='AUTO')
    if not vehicle.is_current(root):
        layout.label(text="Copied, renamed or imported before the Vehicle tab:" if vs.ready else
                     "This vehicle was imported before the Vehicle tab", icon='INFO')
        r = layout.row()
        r.scale_y = 1.3
        r.operator("gtastudio.vehicle_setup", icon='TOOL_SETTINGS')


def sec_colours(b, context):
    vs = _ready(context).gta_vehicle_studio
    r = b.row(align=True)
    r.template_icon_view(vs, "colour_set", show_labels=True, scale=2.0, scale_popup=3.0)
    col = r.column(align=True)
    col.label(text="Game colour sets")
    col.operator("gtastudio.vehicle_random_set", text="Random Set", icon='FILE_REFRESH')
    pj = vs.paintjob not in ("", "0")
    for i in range(4):
        if not vs.slots & (1 << i):
            continue
        box = b.box()
        r = box.row(align=True)
        r.label(text=PAINT_LABELS[i])
        r.prop(vs, "custom%d" % (i + 1), text="Custom", toggle=True, icon='EYEDROPPER')
        if getattr(vs, "custom%d" % (i + 1)):
            box.prop(vs, "custom_rgb%d" % (i + 1), text="")
            box.label(text="Not in game palette", icon='ERROR')
        else:
            r = box.row()
            r.template_icon_view(vs, "col%d" % (i + 1), show_labels=True, scale=1.6, scale_popup=1.4)
    if pj:
        b.label(text="The paintjob covers the body colours", icon='INFO')


def sec_lights(b, context):
    vs = _ready(context).gta_vehicle_studio
    b.prop(vs, "own_lights")
    if not vs.own_lights:
        b.label(text="Uses Scene settings", icon='INFO')
        return
    b.prop(vs, "follow_time")
    for key, label, has in (("head", "Headlights", vs.has_head), ("tail", "Taillights", vs.has_tail)):
        if not has:
            continue
        box = b.box()
        box.prop(vs, key + "_on", text=label)
        col = box.column(align=True)
        col.active = getattr(vs, key + "_on")
        r = col.row(align=True)
        r.prop(vs, key + "_strength")
        r.prop(vs, key + "_tint", text="")


def sec_paintjobs(b, context):
    root = _ready(context)
    vs = root.gta_vehicle_studio
    if root.get("gta_mod"):                # custom model: the game car's paintjobs may not fit its textures
        col = b.column(align=True)
        col.label(text="From the game's %s:" % (root.get("gta_mod_base_name") or root["gta_vehicle"]), icon='INFO')
        col.label(text="may not fit this model", icon='BLANK1')
    b.template_icon_view(vs, "paintjob", show_labels=True, scale=6.0, scale_popup=5.0)
    if vs.paintjob not in ("", "0"):
        b.operator("gtastudio.vehicle_plain_paint", icon='LOOP_BACK')


def sec_parts(b, context):
    vs = _ready(context).gta_vehicle_studio
    moving = [p for p in vs.parts if p.kind != 'EXTRA']
    extras = [p for p in vs.parts if p.kind == 'EXTRA']
    if moving:
        col = b.column(align=True)
        for p in moving:
            col.prop(p, "open", text=p.label, slider=True)
        b.operator("gtastudio.vehicle_close_all", icon='LOOP_BACK')
    if extras:
        g = b.grid_flow(columns=3, even_columns=True, align=True)
        for p in extras:
            g.prop(p, "shown", text=p.label, toggle=True)


def sec_upgrades(b, context):
    vs = _ready(context).gta_vehicle_studio
    col = b.column(align=True)
    for u in vs.upgrades:
        col.prop(u, "choice", text=u.label)
    b.label(text="No hydraulics or stereo (not visible)", icon='INFO')


def _has(test):
    def poll(context):
        root = _ready(context)
        return root is not None and test(root.gta_vehicle_studio)
    return poll


def _paintjob_title(context):
    root = _ready(context)
    return "Paintjobs (%d)" % (root.gta_vehicle_studio.paintjob_count if root else 0)


# id, title, draw, order, icon, closed, poll - in core's Vehicles tab ('VEHICLE'), after its Import (10).
# Panel ids are the ones Studio 1.x used, so open/closed states carry over.
SECTIONS = (
    ("vsel", "Selected Vehicle", sec_selected, 20, 'RESTRICT_SELECT_OFF', False, None),
    ("paint", "Colours", sec_colours, 30, 'COLOR', False, _has(lambda vs: True)),
    ("lights", "Lights", sec_lights, 40, 'LIGHT_SUN', True, _has(lambda vs: vs.has_head or vs.has_tail)),
    ("pj", _paintjob_title, sec_paintjobs, 50, 'IMAGE_DATA', False, _has(lambda vs: vs.paintjob_count)),
    ("parts", "Doors & Extras", sec_parts, 60, 'MOD_BUILD', False, _has(lambda vs: len(vs.parts))),
    ("upg", "Upgrades", sec_upgrades, 70, 'MODIFIER', False, _has(lambda vs: len(vs.upgrades))),
)


def add_sections(api):
    for sid, title, fn, order, icon, closed, poll in SECTIONS:
        api.register_section('VEHICLE', "st_" + sid, title, fn, order=order, icon=icon, closed=closed, poll=poll,
                             panel_id="GTASTUDIO_veh_" + sid)


def remove_sections(api):
    for sid, *_rest in SECTIONS:
        api.unregister_section('VEHICLE', "st_" + sid)


def draw_vehicle(layout, context):
    """All of Studio's vehicle sections in one layout (tests; the panel draws them through core)."""
    for sid, title, fn, _order, _icon, _closed, poll in SECTIONS:
        if poll is None or poll(context):
            fn(layout, context)
