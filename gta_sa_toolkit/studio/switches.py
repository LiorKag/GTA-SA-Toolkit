# GTA SA Studio - its switches in the add-on's preferences: "GTA SA Studio" (all of it) and one per
# area (studio_<id>). A switch only shows or hides Studio's tabs and sections and whether imports set up
# lights and vehicles; Studio's classes are always registered with the rest of the add-on.

# area id -> (label, what it adds); the preference switches are studio_<id>
AREAS = (
    ("scene", "Scene Tab", "Time & weather, sky, map lights, camera & render"),
    ("animate", "Animate Tab", "Combine IFP clips into one animation, bake and export it"),
    ("vehicle", "Vehicle Sections", "Colours, lights, paintjobs, doors and upgrades in the Vehicles tab"),
    ("toys", "Toy Editor", "Edit attached SA-MP toys (Peds tab)"),
    ("pawn", "Pawn Export", "Export map objects as SA-MP Pawn code (World tab)"),
    ("paths", "Traffic & Ped Paths", "Draw the game's paths, make a vehicle or ped follow them (World tab)"),
)


def prefs():
    """The add-on's preferences (Studio's settings live there: timecyc list, switches), or None."""
    from .. import library
    return library.prefs()


def studio_on():
    p = prefs()
    return p is None or bool(p.studio_on)


def area_on(area):
    p = prefs()
    return p is None or (bool(p.studio_on) and bool(getattr(p, "studio_" + area, True)))


def areas_on():
    return tuple(a for a, _l, _d in AREAS if area_on(a))
