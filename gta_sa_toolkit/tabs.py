# GTA SA Toolkit - registry of the tabs shown in the "GTA SA Toolkit" sidebar panel.
# Core registers World / Peds (id CHARS) / Objects (id ITEMS) / Library; GTA SA Studio adds its own through api.py.
# The tab switch (Scene.gta_tk_x.ui_tab) is an enum whose items come from this registry. Each tab has
# a fixed enum number so the selected tab saved in a .blend survives add-ons being enabled/disabled.
import os
import zlib

import bpy
import bpy.utils.previews

_TABS = {}                      # id -> dict
_ICONS = {"pc": None}
_ITEMS = {"list": []}           # keeps the enum strings alive (Blender only borrows them)

# numbers the 1.4.1 static enum used, so old files open on the same tab. 3 was core's Lighting tab;
# Studio's Scene tab (where lighting moved) takes it over. 5 was Studio's Vehicle tab,
# 6 its Edit tab; 7 is kept for the Animate tab.
_FIXED_NUMBERS = {'WORLD': 0, 'CHARS': 1, 'ITEMS': 2, 'SCENE': 3, 'LIBRARY': 4, 'VEHICLE': 5, 'EDIT': 6,
                  'ANIMATE': 7}


def _pc():
    if _ICONS["pc"] is None:
        _ICONS["pc"] = bpy.utils.previews.new()
    return _ICONS["pc"]


def _load_icon(key, path):
    if not path or not os.path.isfile(path):
        return None
    pc = _pc()
    if key not in pc:
        pc.load(key, path, 'IMAGE')
    return key


def register_tab(id, label, short_label, icon, draw_fn, order=100, icon_active=None, description=""):
    """Add (or replace) a tab.
    icon / icon_active: PNG paths for the big tab icon (icon_active is shown on the selected tab;
    defaults to icon). draw_fn(layout, context) draws the tab's contents; it must only draw stored
    values (no scanning). Lower order = further left."""
    if not id or not id.isidentifier():
        raise ValueError("tab id must be a simple name like 'SCENE', got %r" % (id,))
    if not callable(draw_fn):
        raise TypeError("draw_fn must be callable")
    number = _FIXED_NUMBERS.get(id)
    if number is None:
        number = 1000 + zlib.crc32(id.encode()) % 1000000
    _TABS[id] = {
        "id": id, "label": label, "short": short_label or label, "draw": draw_fn, "order": order,
        "number": number, "description": description or label,
        "icon": _load_icon("tab:%s" % id, icon),
        "icon_active": _load_icon("tab:%s:on" % id, icon_active or icon),
    }
    _rebuild_items()


def unregister_tab(id):
    if _TABS.pop(id, None) is None:
        return
    _rebuild_items()
    fallback = tabs()[0]["id"] if _TABS else None
    try:                        # don't leave a scene pointing at a tab that no longer exists
        for sc in bpy.data.scenes:
            x = getattr(sc, "gta_tk_x", None)
            if x is not None and fallback and _raw_tab(x) not in _TABS:
                x.ui_tab = fallback
    except Exception:           # noqa - restricted context during add-on shutdown
        pass


def tabs():
    """Registered tabs, left to right."""
    return sorted(_TABS.values(), key=lambda t: (t["order"], t["id"]))


def get(id):
    return _TABS.get(id)


def _rebuild_items():
    _ITEMS["list"] = [(t["id"], t["label"], t["description"], 'NONE', t["number"]) for t in tabs()]


def enum_items(self, context):
    return _ITEMS["list"] or [('WORLD', "World", "", 'NONE', 0)]


def _raw_tab(x):
    try:
        return x.ui_tab
    except Exception:           # noqa
        return ""


def active(x):
    """The selected tab's entry, falling back to the first tab if it was removed."""
    t = _TABS.get(_raw_tab(x))
    if t is None:
        lst = tabs()
        t = lst[0] if lst else None
    return t


# ----------------------------------------------------------------------------- sections
# A tab's contents are sections (collapsible sub-panels) that any add-on can add: core fills its own
# tabs this way and GTA SA Studio adds its sections into them (Pawn Export into World, the Toy Editor
# into Peds, colours/lights/doors into Vehicles). Lower order = higher up.
_SECTIONS = {}                  # tab id -> {section id: dict}
STRICT = {"on": False}          # tests: a failing section raises instead of showing its error


def register_section(tab, id, title, draw_fn, order=100, icon='NONE', closed=False, poll=None,
                     draw_header=None, panel_id=None):
    """Add (or replace) a section in a tab (the tab may be registered later).
    title: text, or a function(context) -> text (e.g. with a count). draw_fn(layout, context) draws the
    body (stored values only). poll(context) -> False hides the section. draw_header(layout, context)
    adds to the header row after the title. panel_id: the layout-panel id that remembers open/closed
    (default "GTATK_<id>")."""
    if not callable(draw_fn):
        raise TypeError("draw_fn must be callable")
    _SECTIONS.setdefault(tab, {})[id] = {
        "tab": tab, "id": id, "title": title, "draw": draw_fn, "order": order, "icon": icon,
        "closed": closed, "poll": poll, "draw_header": draw_header, "panel_id": panel_id or "GTATK_" + id,
    }


def unregister_section(tab, id):
    _SECTIONS.get(tab, {}).pop(id, None)


def has_section(tab, id):
    return id in _SECTIONS.get(tab, {})


def sections(tab):
    """A tab's sections, top to bottom."""
    return sorted(_SECTIONS.get(tab, {}).values(), key=lambda s: (s["order"], s["id"]))


def draw_sections(layout, context, tab):
    """Draw a tab's sections. A section that fails shows its error and the others still draw."""
    for s in sections(tab):
        try:
            if s["poll"] is not None and not s["poll"](context):
                continue
            title = s["title"](context) if callable(s["title"]) else s["title"]
            header, body = layout.panel(s["panel_id"], default_closed=s["closed"])
            header.label(text=title, icon=s["icon"])
            if s["draw_header"] is not None:
                s["draw_header"](header, context)
            if body:
                s["draw"](body, context)
        except Exception as e:      # noqa - one broken section (e.g. Studio's) must not hide the tab
            if STRICT["on"]:
                raise
            layout.label(text="%s failed: %s" % (s["id"], e), icon='ERROR')


def tab_drawer(tab):
    """A draw_fn for register_tab that draws the tab's sections."""
    return lambda layout, context: draw_sections(layout, context, tab)


def icon_id(key):
    pc = _ICONS["pc"]
    return pc[key].icon_id if (pc is not None and key and key in pc) else 0


def free():
    _TABS.clear()
    _SECTIONS.clear()
    _ITEMS["list"] = []
    if _ICONS["pc"] is not None:
        bpy.utils.previews.remove(_ICONS["pc"])
        _ICONS["pc"] = None
