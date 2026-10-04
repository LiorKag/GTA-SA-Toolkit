# GTA SA Studio (part of GTA SA Toolkit) - how Studio plugs into the rest of the add-on.
#
# Studio still talks to core only through core's api.py (api()), so the two halves stay separate.
# connect() adds Studio's tabs and sections to core's panel (only the areas switched on in the add-on
# preferences: AREAS) and its listeners; disconnect() takes them out. Core's studiolink.py decides
# whether Studio is registered at all (the master switch, an old separate Studio still installed).
from .. import api as _core_api

# area id -> (label, what it adds); the preference switches are studio_<id>
AREAS = (
    ("scene", "Scene Tab", "Time & weather, sky, map lights, camera & render"),
    ("animate", "Animate Tab", "Combine IFP clips into one animation, bake and export it"),
    ("vehicle", "Vehicle Sections", "Colours, lights, paintjobs, doors and upgrades in the Vehicles tab"),
    ("toys", "Toy Editor", "Edit attached SA-MP toys (Peds tab)"),
    ("pawn", "Pawn Export", "Export map objects as SA-MP Pawn code (World tab)"),
    ("paths", "Traffic & Ped Paths", "Draw the game's paths, make a vehicle or ped follow them (World tab)"),
)

_S = {"linked": False, "areas": ()}


def api():
    """Core's api module while Studio is connected, else None."""
    return _core_api if _S["linked"] else None


def prefs():
    """The add-on's preferences (Studio's settings live there: timecyc list, switches), or None."""
    from .. import library
    return library.prefs()


def area_on(area):
    p = prefs()
    return p is None or bool(getattr(p, "studio_" + area, True))


def state():
    return {"status": 'OK' if _S["linked"] else 'OFF', "linked": _S["linked"], "areas": _S["areas"]}


def connect():
    """Add Studio's tabs / sections for the areas switched on, and its listeners. Safe to call again."""
    from . import lighting, ui_scene
    if _S["linked"]:
        disconnect()
    areas = tuple(a for a, _l, _d in AREAS if area_on(a))
    try:
        ui_scene.add_tabs(_core_api, areas)
        lighting.connect(_core_api)
        _S.update(linked=True, areas=areas)
    except Exception as e:             # noqa - never break Blender's startup over this
        print("[GTA SA Toolkit] could not plug in: %s" % e)
        _S["linked"] = False


def disconnect():
    from . import lighting, ui_scene
    if not _S["linked"]:
        return
    for fn in (lambda: ui_scene.remove_tabs(_core_api), lambda: lighting.disconnect(_core_api)):
        try:
            fn()
        except Exception:              # noqa - core may be half unregistered already
            pass
    _S.update(linked=False, areas=())
