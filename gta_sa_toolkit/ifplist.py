# GTA SA Toolkit - the IFP browser's file menu and its all-animations search.
#   Menu: Recent files, Starred files, then the game's IFPs in headed columns (like Add Modifier):
#   GROUPS below decides the column of each file inside anim.img; ped.ifp is Main; anything not in the
#   table (modded files) goes to Other. "Other file..." opens a file browser.
#   Search: every animation of every game IFP (ped.ifp + anim.img) with its duration, built once at
#   Scan Game Files (index_for), searched by name.
import os
import time

import bpy

from . import library

GROUPS = (
    ("Main", "playidles fat muscular"),
    ("Weapons & Fighting", "colt45 python silenced shotgun rifle sniper uzi tec rocket flame grenade knife sword "
                           "chainsaw dildo flowers spraycan camera goggles weapons buddy fight_b fight_c fight_d "
                           "fight_e box"),
    ("Vehicles", "car car_chat drivebys cop_dvbyz player_dvbys lowrider van truck bus coach dozer tank train kart "
                 "quad quad_dbz bikes biked bikeh bikev bike_dbz bikeleap bmx mtb choppa nevada rustler shamal "
                 "vortex wayfarer"),
    ("Street & Gangs", "gangs ghands ghetto_db dealer crack graffiti rapping smoking poor on_lookers riot police "
                       "cop_ambient swat medic"),
    ("Activities", "airport attractors bar baseball beach benchpress bsktball carry casino clothes dancing gfunk "
                   "runningman wop dodge food freeweights gymnasium haircuts kissing otb park parachute pool shop "
                   "skate sunbathe swim tattoos vending scratching dam_jump bomber"),
    ("Adult", "lapdan1 lapdan2 lapdan3 strip blowjobz sex snm"),
    ("Interiors", "int_house int_office int_shop crib"),
    ("Story Characters", "sweet ryder wuzi paulnmac md_chase md_end heist9 finale finale2 graveyard rob_bank "
                         "bd_fire bf_injection jst_buisness"),
)
OTHER = "Other"
_GROUP_OF = {n: g for g, names in GROUPS for n in names.split()}
MAX_RECENT = 6


def group_of(source):
    """Menu column of an IFP source ("ped.ifp" -> Main, "anim.img/uzi.ifp" -> Weapons & Fighting)."""
    if source == "ped.ifp":
        return "Main"
    if source.startswith("anim.img/"):
        return _GROUP_OF.get(os.path.splitext(source[9:])[0].lower(), OTHER)
    return OTHER


# ----------------------------------------------------------------------------- recent / starred
def recent():
    pr = library.prefs()
    return [s for s in (pr.recent_ifps.split("\n") if pr is not None else ()) if s]


def push_recent(source):
    pr = library.prefs()
    if pr is None or not source:
        return
    lst = [source] + [s for s in recent() if s != source]
    pr.recent_ifps = "\n".join(lst[:MAX_RECENT])


def starred():
    pr = library.prefs()
    return [f.key for f in pr.favorites if f.kind == 'IFP'] if pr is not None else []


# ----------------------------------------------------------------------------- menu items
_ITEMS = []             # keeps the dynamic enum items alive (Blender only borrows them)


def menu_items(game_root):
    """Enum items for the file menu: headings ("" identifier) start the columns. The identifier of a
    recent/starred copy of a file is prefixed with a letter so every identifier stays unique."""
    srcs = library.ifp_sources(game_root) if game_root else []
    labels = dict(srcs)
    items, n = [], 0

    def heading(text):
        items.append(("", text, ""))

    def add(ident, label, desc, icon):
        nonlocal n
        items.append((ident, label, desc, icon, n))
        n += 1

    rec, star = recent(), starred()
    if rec or star:
        heading("Recent & Starred")
        for s in star:
            add("S" + s, library.source_label(s), "Starred: " + s, 'SOLO_ON')
        for s in rec:
            if s not in star:
                add("R" + s, library.source_label(s), "Recent: " + s, 'TIME')
    groups = {}
    for s, _label in srcs:
        groups.setdefault(group_of(s), []).append(s)
    for g in [g for g, _ in GROUPS] + [OTHER]:
        if not groups.get(g) and g != OTHER:
            continue
        heading(g)
        for s in groups.get(g, []):
            add(s, labels[s], "Animations in " + labels[s], 'ACTION')
        if g == OTHER:
            add("OTHER", "Other file…", "Pick an .ifp file on disk", 'FILEBROWSER')
    _ITEMS[:] = items
    return _ITEMS


def source_of(ident):
    """The source a menu identifier stands for (strips the recent/starred prefix)."""
    if ident[:1] in ("S", "R") and (ident[1:] == "ped.ifp" or ident[1:].startswith("anim.img/")
                                   or os.path.isabs(ident[1:]) or ident[1:].startswith("//")):
        return ident[1:]
    return ident


# ----------------------------------------------------------------------------- all-animations index
_IDX = {"key": None, "rows": [], "secs": 0.0}


def build_index(context):
    """[(animation name, source, duration)] for ped.ifp and every IFP in anim.img. Built once per game
    folder + anim.img/ped.ifp change (Scan Game Files builds it; a search builds it if needed)."""
    root = bpy.path.abspath(context.scene.gta_tk.game_root).rstrip("\\/")
    key = _index_key(root)
    if _IDX["key"] == key:
        return _IDX["rows"]
    t = time.perf_counter()
    from .formats import ifp as ifp_fmt
    from .formats import img as img_fmt
    rows = []

    def add(src, data):
        try:
            rows.extend((name, src, dur) for name, dur in ifp_fmt.scan_names(data))
        except Exception as e:           # noqa - one broken IFP must not stop the index
            print("[GTA SA Toolkit] IFP index %s: %s" % (src, e))

    ped = os.path.join(root, "anim", "ped.ifp")
    if os.path.isfile(ped):
        with open(ped, "rb") as f:
            add("ped.ifp", f.read())
    path, _files = library._anim_img(root)
    if path:
        arc = img_fmt.ImgArchive(path)
        try:
            for e in arc.order:
                if e.name.lower().endswith(".ifp"):
                    add("anim.img/" + e.name, arc.read(e))
        finally:
            arc.close()
    _IDX.update(key=key, rows=rows, secs=time.perf_counter() - t)
    return rows


def _index_key(root):
    out = [root]
    for rel in ("anim/ped.ifp", "anim/anim.img"):
        p = os.path.join(root, *rel.split("/"))
        out.append(os.path.getmtime(p) if os.path.isfile(p) else None)
    return tuple(out)


def search(context, query, limit=200):
    """Animations whose name contains query (every game IFP): exact names first, then names that start
    with it. [(name, source, duration)]."""
    q = query.strip().lower()
    if not q:
        return []
    hits = []
    for name, src, dur in build_index(context):
        low = name.lower()
        if q in low:
            hits.append((0 if low == q else (1 if low.startswith(q) else 2), low, name, src, dur))
    hits.sort(key=lambda h: (h[0], h[1], h[3]))
    return [(h[2], h[3], h[4]) for h in hits[:limit]]
