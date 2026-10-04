# GTA SA Toolkit - sidebar layout: one "GTA" panel with section tabs
#   World      : Map import, SA-MP map code
#   Peds       : Ped import, IFP animation, IFP export, SA-MP toys, ped tools (tab id CHARS)
#   Objects    : Vehicles, weapons, objects (tab id ITEMS, its old name)
#   Library    : Search everywhere, favorites, recent imports
# GTA SA Studio adds its own tabs (Scene: lighting, time of day) through api.register_tab.
import os
from types import SimpleNamespace

import bpy

from . import library, tabs, texfix, ui, ui_extra


# ----------------------------------------------------------------------------- tab icons
# Blender draws icons inside buttons at a fixed 16 px, so the big tab icons are drawn with
# template_icon (which can scale) above each tab's label button. Each icon has an "_on" copy in the
# accent colour for the active tab. The tabs themselves live in tabs.py (Studio adds its own there).
ICON_SCALE = 2.0
ICON_DIR = os.path.join(os.path.dirname(__file__), "icons")


def draw_tabs(layout, x, narrow):
    lst = tabs.tabs()
    if not lst:
        return None
    cur = tabs.active(x)
    have = all(t["icon"] and t["icon_active"] for t in lst)
    # one row (4 core tabs + Studio's Vehicle and Scene); two rows when the sidebar is narrow
    cols = len(lst) if (not narrow or len(lst) <= 4) else (len(lst) + 1) // 2
    g = layout.grid_flow(row_major=True, columns=cols, even_columns=True, even_rows=True, align=True)
    for t in lst:
        c = g.column(align=True)
        if have:
            c.template_icon(icon_value=tabs.icon_id(t["icon_active"] if t is cur else t["icon"]),
                            scale=ICON_SCALE)
        r = c.row(align=True)
        r.scale_y = 1.15
        r.prop_enum(x, "ui_tab", t["id"], text=t["short"] if narrow else t["label"])
    return cur


def _shim_draw(panel_cls, layout, context):
    panel_cls.draw(SimpleNamespace(layout=layout), context)


# ----------------------------------------------------------------------------- sections
# Every tab is a list of sections (tabs.register_section); GTA SA Studio adds its own in between by order.
def sec_map(b, context):
    _shim_draw(ui.GTATK_PT_map, b, context)


def sec_pawn(b, context):
    _shim_draw(ui_extra.GTATK_PT_pawn, b, context)


def sec_ped(b, context):
    x = context.scene.gta_tk_x
    if not x.peds:
        b.label(text="Scan the game files to list peds", icon='INFO')
    r = b.row(align=True)
    ui.prop_ph(r, x, "ped_query", "Name, ID or type, e.g. grove", text="", icon='VIEWZOOM')
    s = r.row(align=True)
    s.ui_units_x = 5.5
    s.prop(x, "ped_cat", text="")
    b.template_list("GTATK_UL_peds", "", x, "peds", x, "peds_index", rows=6)
    if x.peds and x.peds[min(x.peds_index, len(x.peds) - 1)].mod_key:
        b.prop_search(x.peds[min(x.peds_index, len(x.peds) - 1)], "base", x, "peds", icon='LINKED')
    r = b.row(align=True)
    r.scale_y = 1.3
    r.operator("gtatk.import_ped", text="Import", icon='IMPORT')
    b.prop(x, "ped_auto_pose")
    b.label(text="Other model by name or ID:")
    _shim_draw(ui.GTATK_PT_model, b, context)


def sec_ifp(b, context):
    _shim_draw(ui.GTATK_PT_ifp, b, context)


def sec_ifp_export(b, context):
    _shim_draw(ui.GTATK_PT_ifp_export, b, context)


def sec_toys(b, context):
    x = context.scene.gta_tk_x
    draw_object_search(b, context, samp_default=True)
    bb = b.box()
    r = bb.row(align=True)
    ui.prop_ph(r, x, "toy_line", "Paste a SetPlayerAttachedObject line", text="")
    r.operator("gtatk.parse_toy_line", text="", icon='PASTEDOWN')
    bb.prop(x, "toy_bone")
    if tabs.has_section('CHARS', "st_toy"):     # GTA SA Studio's Toy Editor (below) edits them after attaching
        bb.label(text="Offset, rotation, scale: Toy Editor below", icon='INFO')
    else:
        c = bb.column(align=True)
        for key, lab in (("toy_offset", "Offset"), ("toy_rot", "Rotation"), ("toy_scale", "Scale")):
            s = c.split(factor=0.3, align=True)     # one X | Y | Z row each
            s.label(text=lab)
            s.row(align=True).prop(x, key, text="")
    bb.operator("gtatk.attach_toy", icon='BONE_DATA')


def sec_ped_tools(b, context):
    _shim_draw(ui.GTATK_PT_char, b, context)


def draw_object_search(layout, context, samp_default=False):
    x = context.scene.gta_tk_x
    r = layout.row(align=True)
    ui.prop_ph(r, x, "obj_query", "Name or ID, e.g. hat, 18645", text="", icon='VIEWZOOM')
    r.operator("gtatk.search_objects", text="", icon='VIEWZOOM')
    r.prop(x, "obj_only_samp", text="", icon='MOD_CLOTH', toggle=True)
    layout.template_list("GTATK_UL_objects", "", x, "objects", x, "objects_index", rows=5)


def sec_vehicles(b, context):
    _shim_draw(ui_extra.GTATK_PT_vehicles, b, context)


def sec_weapons(b, context):
    x = context.scene.gta_tk_x
    if not x.weapons:
        b.label(text="Press 'Scan' to list weapons", icon='INFO')
    b.prop(x, "weapon_cat")
    b.template_list("GTATK_UL_weapons", "", x, "weapons", x, "weapons_index", rows=6)
    r = b.row(align=True)
    r.scale_y = 1.2
    r.operator("gtatk.import_weapon", text="Import", icon='IMPORT')
    r = b.row(align=True)
    r.prop(x, "weapon_hand", text="")
    r.operator("gtatk.give_weapon", icon='HAND')


def sec_objects(b, context):
    draw_object_search(b, context)
    b.operator("gtatk.import_object", text="Import", icon='IMPORT')


def _fav_title(context):
    pr = library.prefs()
    return "Favorites (%d)" % (len(pr.favorites) if pr else 0)


def sec_favorites(b, context):
    groups = library.favorite_groups()
    if not groups:
        b.label(text="Click a star in any list to add a favorite", icon='SOLO_OFF')
    for kind, items in groups:
        label = next(k[1] for k in library.KINDS if k[0] == kind)
        h, body = b.panel("GTATK_libfav_" + kind, default_closed=False)
        h.label(text="%s (%d)" % (label, len(items)), icon=library.KIND_ICON[kind])
        if body:
            col = body.column(align=True)
            for it in items:
                library.draw_item_row(col, it.kind, it.key, it.name, it.detail, settings=it.settings,
                                      note=it.note, big=True)


def _recent_title(context):
    pr = library.prefs()
    return "Recent (%d)" % (len(pr.recents) if pr else 0)


def _recent_header(header, context):
    pr = library.prefs()
    if pr and pr.recents:
        header.operator("gtatk.library_clear_recent", text="", icon='TRASH', emboss=False)


def sec_recent(b, context):
    st = context.scene.gta_tk_lib
    pr = library.prefs()
    if not pr.recents:
        b.label(text="Imports show up here, newest first", icon='INFO')
    col = b.column(align=True)
    n = len(pr.recents) if st.show_all_recent else min(len(pr.recents), library.RECENT_ROWS)
    for it in pr.recents[:n]:
        library.draw_item_row(col, it.kind, it.key, it.name, it.detail, settings=it.settings, note=it.note,
                              big=True)
    if len(pr.recents) > library.RECENT_ROWS:
        b.prop(st, "show_all_recent", text="Show all %d" % len(pr.recents))


def _cache_header(header, context):
    pr = library.prefs()
    if pr and pr.cache_enabled and pr.cache_dir.strip():
        from . import cache
        header.label(text="On · %s" % cache.size_text())
    else:
        header.label(text="Off")


def sec_cache(b, context):
    library.draw_cache(b)


def _mods_header(header, context):
    from . import mods
    n = sum(mods.summary()[1].values())
    header.label(text="%d models" % n if n else "None")


def sec_mods(b, context):
    from . import mods
    pr = library.prefs()
    n_folders, c = mods.summary()
    if not pr.mod_folders:
        b.label(text="Custom / modded models from your folders", icon='INFO')
    else:
        b.label(text="%d folder%s: %d peds, %d vehicles, %d objects" % (
            len(pr.mod_folders), "" if len(pr.mod_folders) == 1 else "s", c['PED'], c['VEHICLE'], c['OBJECT']),
            icon='FILE_FOLDER')
    r = b.row(align=True)
    r.operator("gtatk.mods_rescan", icon='FILE_REFRESH')
    r.operator("preferences.addon_show", text="Folders…", icon='PREFERENCES').module = __package__


def _has_prefs(context):
    return library.prefs() is not None


SEARCH_ROWS = 6                 # header search: short list, "Show more" for the rest
SEARCH_ROWS_MORE = 16


def draw_search(layout, context):
    """The search box under the game folder, on every tab. Results only while something is typed."""
    st = context.scene.gta_tk_lib
    r = layout.row(align=True)
    ui.prop_ph(r, st, "query", "Search everything: name or ID", text="", icon='VIEWZOOM')
    if not st.query:
        return
    r.operator("gtatk.library_search_clear", text="", icon='X')
    if st.status:
        layout.label(text=st.status, icon='INFO')
    if st.results:
        n = SEARCH_ROWS_MORE if st.show_more else SEARCH_ROWS
        layout.template_list("GTATK_UL_lib_results", "", st, "results", st, "results_index", rows=n, maxrows=n)
        if len(st.results) > SEARCH_ROWS:
            layout.prop(st, "show_more", toggle=True, icon='TRIA_UP' if st.show_more else 'TRIA_DOWN',
                        text="Show less" if st.show_more else "Show more (%d results)" % len(st.results))


def draw_library(layout, context):
    if library.prefs() is None:
        layout.label(text="Preferences not available", icon='ERROR')
        return
    tabs.draw_sections(layout, context, 'LIBRARY')


# id, label, short label, icon file, draw (None = its sections), order, tooltip
CORE_TABS = (
    ('WORLD', "World", "World", "tab_world", None, 10, "Map sections/areas and SA-MP map code"),
    ('CHARS', "Peds", "Peds", "tab_chars", None, 20, "Peds, IFP animations, toys and ped tools"),
    ('VEHICLE', "Vehicles", "Vehicles", "tab_vehicles", None, 30,
     "Import vehicles (with GTA SA Studio: colours, lights, paintjobs, doors and upgrades too)"),
    ('ITEMS', "Objects", "Objects", "tab_items", None, 40, "Weapons and objects"),
    ('LIBRARY', "Library", "Library", "tab_library", draw_library, 90,
     "Favorites, recent imports and the asset cache"),
)

# tab, id (panel GTATK_<id>), title, draw, order, icon, closed, extra keyword arguments.
# Orders leave room for GTA SA Studio's sections in between.
CORE_SECTIONS = (
    ('WORLD', "map", "Map Import", sec_map, 10, 'WORLD', False, {}),
    ('WORLD', "pawn", "SA-MP Map Code", sec_pawn, 30, 'FILE_SCRIPT', True, {}),
    ('WORLD', "texfix", "Missing Textures", lambda b, c: texfix.draw_section(b, c), 90, 'TEXTURE', True, {}),
    ('CHARS', "ped", "Import Ped / Model", sec_ped, 10, 'OUTLINER_OB_ARMATURE', False, {}),
    ('CHARS', "ifp", "IFP Animation", sec_ifp, 20, 'ACTION', False, {}),
    ('CHARS', "ifpexp", "IFP Export", sec_ifp_export, 30, 'EXPORT', True, {}),
    ('CHARS', "toys", "SA-MP Toys (attach)", sec_toys, 40, 'MOD_CLOTH', True, {}),
    ('CHARS', "chartools", "Ped Tools", sec_ped_tools, 60, 'TOOL_SETTINGS', True, {}),
    ('VEHICLE', "veh", "Import Vehicle", sec_vehicles, 10, 'AUTO', False, {}),
    ('ITEMS', "weap", "Weapons", sec_weapons, 10, 'MOD_PHYSICS', False, {}),
    ('ITEMS', "obj", "Objects (GTA + SA-MP)", sec_objects, 20, 'OBJECT_DATA', True, {}),
    ('LIBRARY', "libfav", _fav_title, sec_favorites, 10, 'SOLO_ON', False, {"poll": _has_prefs}),
    ('LIBRARY', "librecent", _recent_title, sec_recent, 20, 'TIME', False,
     {"poll": _has_prefs, "draw_header": _recent_header}),
    ('LIBRARY', "libcache", "Asset Cache", sec_cache, 30, 'FILE_CACHE', True,
     {"poll": _has_prefs, "draw_header": _cache_header}),
    ('LIBRARY', "libmods", "Mods", sec_mods, 40, 'FILE_FOLDER', True,
     {"poll": _has_prefs, "draw_header": _mods_header}),
)


def register_core_tabs():
    for key, full, short, base, fn, order, desc in CORE_TABS:
        tabs.register_tab(key, full, short, os.path.join(ICON_DIR, base + ".png"), fn or tabs.tab_drawer(key),
                          order, icon_active=os.path.join(ICON_DIR, base + "_on.png"), description=desc)
    for tab, sid, title, fn, order, icon, closed, kw in CORE_SECTIONS:
        tabs.register_section(tab, sid, title, fn, order=order, icon=icon, closed=closed, **kw)


class GTATK_PT_main(bpy.types.Panel):
    bl_idname = "GTATK_PT_main"
    bl_label = "GTA SA Toolkit"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "GTA"

    def draw(self, context):
        p = context.scene.gta_tk
        x = context.scene.gta_tk_x
        l = self.layout
        from . import studiolink
        studiolink.draw_old_message(l)            # an old separate GTA SA Studio is still installed
        scanned = bool(p.sections or x.vehicles)
        col = l.column(align=True)
        col.prop(p, "game_root", text="")
        r = col.row(align=True)
        r.scale_y = 1.0 if scanned else 1.4
        r.operator("gtatk.scan_game", text="Rescan Game Files" if scanned else "Scan Game Files",
                   icon='VIEWZOOM')
        if not p.game_root:
            l.label(text="Pick your GTA San Andreas folder first", icon='INFO')
        draw_search(l.column(), context)
        l.separator(factor=0.6)
        cur = draw_tabs(l, x, context.region.width < 330)
        l.separator(factor=0.8)
        if cur is not None:
            try:
                cur["draw"](l.column(), context)
            except Exception as e:      # noqa - a broken tab (e.g. Studio's) must not hide the panel
                l.label(text="%s tab failed: %s" % (cur["label"], e), icon='ERROR')


classes = (GTATK_PT_main,)


def register():
    register_core_tabs()
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    tabs.free()
