# GTA SA Studio - traffic and ped paths (Edit tab): the game's road lanes and sidewalks as curves, and
# vehicles / peds following a route between two picked points.
#
# The graph comes from core (api.path_graph: nodes0-63.dat). Here:
#   Draw     roads and sidewalks of an area (the selected map import, or around the 3D cursor) become
#            one curve object each, in "GTA Paths" > "GTA Roads" / "GTA Sidewalks". Road links are
#            joined into chains between junctions (split where the lane count changes); every lane is
#            one line, offset from the centre like the game (LANE_WIDTH apart, outside the median;
#            one-way roads centred) and running in its traffic direction.
#   Route    shortest way (A*) from the node nearest the start to the node nearest the end: roads
#            only along lanes that go that way, in the rightmost lane; sidewalks for peds. Smoothed
#            (corner cutting), shown as a "GTA Route <name>" curve.
#   Follow   keyframes every frame from the current one: position and heading along the route at the
#            chosen speed, with a short ease (seconds) at both ends. Vehicles pitch with the slope, wheels roll by distance / radius,
#            front wheels steer by the route's curvature over the wheelbase. Peds walk / run / sprint
#            with the game's ped.ifp cycle on an NLA track whose time follows the distance travelled
#            (no foot sliding, slows with the ease); their speed is the animation's own x Speed.
#            Everything created is tagged; Stop Following puts the objects back.
import heapq
import math

import bpy
from mathutils import Matrix, Vector

from . import corelink

COLL_NAME = "GTA Paths"
ROADS_NAME = "GTA Roads"
WALKS_NAME = "GTA Sidewalks"
ROUTES_NAME = "GTA Routes"
COLOURS = {ROADS_NAME: (1.0, 0.42, 0.04), WALKS_NAME: (0.05, 0.7, 1.0), ROUTES_NAME: (1.0, 0.9, 0.1)}
BEVEL = {ROADS_NAME: 0.12, WALKS_NAME: 0.08, ROUTES_NAME: 0.25}
LIFT = 0.15                     # curves float this far above the road so they don't flicker
IMPORT_MARGIN = 30.0
GAITS = {'WALK': "WALK_civi", 'RUN': "RUN_civi", 'SPRINT': "sprint_civi"}
MAX_RAMP_SHARE = 0.25          # speeding up (and slowing down) takes at most this share of the route
MAX_STEER = math.radians(35.0)


def _api():
    return corelink.api()


# ----------------------------------------------------------------------------- collections
def _collection(scene, name=None):
    top = bpy.data.collections.get(COLL_NAME)
    if top is None:
        top = bpy.data.collections.new(COLL_NAME)
    if top.name not in scene.collection.children:
        scene.collection.children.link(top)
    if name is None:
        return top
    c = bpy.data.collections.get(name)
    if c is None:
        c = bpy.data.collections.new(name)
    if c.name not in top.children:
        top.children.link(c)
    return c


def _material(name):
    m = bpy.data.materials.get(name)
    if m is not None:
        return m
    rgb = COLOURS[name]
    m = bpy.data.materials.new(name)
    m.diffuse_color = rgb + (1.0,)             # solid view
    m.use_nodes = True
    bsdf = next((n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = rgb + (1.0,)
        for key in ("Emission Color", "Emission"):
            if key in bsdf.inputs:
                bsdf.inputs[key].default_value = rgb + (1.0,)
                break
        if "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = 1.0
    return m


def _curve_object(scene, kind, obname, polylines):
    """(Re)make a curve object in the kind's collection with one poly spline per polyline."""
    old = bpy.data.objects.get(obname)
    if old is not None:
        data = old.data
        bpy.data.objects.remove(old)
        if data is not None and data.users == 0:
            bpy.data.curves.remove(data)
    cu = bpy.data.curves.new(obname, 'CURVE')
    cu.dimensions = '3D'
    cu.bevel_depth = BEVEL[kind]
    cu.bevel_resolution = 0
    cu.use_fill_caps = False
    for pts in polylines:
        if len(pts) < 2:
            continue
        sp = cu.splines.new('POLY')
        sp.points.add(len(pts) - 1)
        flat = []
        for p in pts:
            flat += (p[0], p[1], p[2], 1.0)
        sp.points.foreach_set("co", flat)
    cu.materials.append(_material(kind))
    ob = bpy.data.objects.new(obname, cu)
    ob.color = COLOURS[kind] + (1.0,)
    ob.hide_render = True
    ob["gta_paths"] = kind
    _collection(scene, kind).objects.link(ob)
    return ob


# ----------------------------------------------------------------------------- geometry
def _right(a, b):
    """Unit vector to the right of a -> b (horizontal)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    ln = math.hypot(dx, dy)
    return (dy / ln, -dx / ln) if ln > 1e-6 else (0.0, 0.0)


def offset_line(pts, offsets, lift=0.0):
    """pts moved sideways: offsets[i] metres to the right of segment i (positive = right). Corners use
    the mitred mean of both segments (limited, so hairpins don't spike)."""
    n = len(pts)
    if n < 2:
        return [tuple(p) for p in pts]
    rights = [_right(pts[i], pts[i + 1]) for i in range(n - 1)]
    out = []
    for j in range(n):
        if j == 0:
            r, o = rights[0], offsets[0]
        elif j == n - 1:
            r, o = rights[-1], offsets[-1]
        else:
            a, b = rights[j - 1], rights[j]
            r = (a[0] + b[0], a[1] + b[1])
            ln = math.hypot(*r)
            if ln < 1e-6:
                r = b
            else:
                r = (r[0] / ln, r[1] / ln)
                r = (r[0] / max(0.5, r[0] * b[0] + r[1] * b[1]), r[1] / max(0.5, r[0] * b[0] + r[1] * b[1]))
            o = (offsets[j - 1] + offsets[j]) * 0.5
        p = pts[j]
        out.append((p[0] + r[0] * o, p[1] + r[1] * o, p[2] + lift))
    return out


def lane_offset(i, lanes, other, median):
    """Metres to the right of the road's centre line for lane i (0 = nearest the centre) of the
    `lanes` going our way, with `other` lanes coming the other way."""
    lw = _api().LANE_WIDTH
    if other == 0:
        return (i - (lanes - 1) * 0.5) * lw
    return median * 0.5 + (i + 0.5) * lw


def smooth(pts, rounds=3):
    """Chaikin corner cutting, keeping both ends."""
    pts = [tuple(p) for p in pts]
    for _ in range(rounds):
        if len(pts) < 3:
            break
        out = [pts[0]]
        for a, b in zip(pts, pts[1:]):
            out.append(tuple(a[k] * 0.75 + b[k] * 0.25 for k in range(3)))
            out.append(tuple(a[k] * 0.25 + b[k] * 0.75 for k in range(3)))
        out[1] = pts[0]
        out[-1] = pts[-1]
        out = [out[0]] + out[2:]
        pts = out
    return pts


class Polyline:
    """A route measured along its length: at(s) -> point, heading(s, h) -> direction."""

    def __init__(self, pts):
        clean = [Vector(pts[0])]
        for p in pts[1:]:
            if (Vector(p) - clean[-1]).length > 1e-4:
                clean.append(Vector(p))
        self.pts = clean
        self.s = [0.0]
        for a, b in zip(clean, clean[1:]):
            self.s.append(self.s[-1] + (b - a).length)
        self.length = self.s[-1]

    def at(self, s):
        s = min(max(s, 0.0), self.length)
        lo, hi = 0, len(self.s) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.s[mid] <= s:
                lo = mid
            else:
                hi = mid
        if hi == lo:
            return self.pts[lo].copy()
        seg = self.s[hi] - self.s[lo]
        t = (s - self.s[lo]) / seg if seg > 1e-9 else 0.0
        return self.pts[lo].lerp(self.pts[hi], t)

    def direction(self, s, h):
        a, b = self.at(s - h), self.at(s + h)
        d = b - a
        if d.length < 1e-6:
            d = self.pts[-1] - self.pts[0] if len(self.pts) > 1 else Vector((0, 1, 0))
        return d


def heading(dx, dy):
    """Angle about Z that turns +Y (GTA's forward) towards (dx, dy)."""
    return math.atan2(-dx, dy)


def _unwrap(angles):
    out = []
    for a in angles:
        if out:
            while a - out[-1] > math.pi:
                a -= 2 * math.pi
            while a - out[-1] < -math.pi:
                a += 2 * math.pi
        out.append(a)
    return out


def speed_profile(length, vmax, ramp, fps):
    """Distance along the route at each frame (from 0): speed up over `ramp` seconds, cruise at
    exactly vmax (m/s), slow down over `ramp` seconds. The ramps shrink on short routes (each covers
    at most MAX_RAMP_SHARE of the length) so the top speed is always reached; ramp 0 = full speed
    from the first frame to the last."""
    if length <= 1e-6:
        return [0.0]
    v = max(vmax, 1e-3)
    t_acc = max(0.0, min(ramp, 2.0 * MAX_RAMP_SHARE * length / v))
    d_acc = 0.5 * v * t_acc
    t_cruise = (length - 2 * d_acc) / v
    total = 2 * t_acc + t_cruise
    n = max(1, int(math.ceil(total * fps - 1e-6)))
    out = []
    for i in range(n + 1):
        t = min(i / fps, total)
        if t < t_acc:
            d = 0.5 * v * t * t / t_acc
        elif t <= t_acc + t_cruise:
            d = d_acc + v * (t - t_acc)
        else:
            r = total - t
            d = length - 0.5 * v * r * r / t_acc
        out.append(min(max(d, 0.0), length))
    out[-1] = length
    return out


# ----------------------------------------------------------------------------- area and drawing
def area_filter(scene):
    """(test(pos) -> bool, description) for the Draw Paths area, or raises ValueError."""
    st = scene.gta_studio
    if st.path_area == 'CURSOR':
        c = scene.cursor.location
        r2 = st.path_radius ** 2
        cx, cy = c.x, c.y
        return (lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2 <= r2), "%d m around the 3D cursor" % st.path_radius
    b = _api().import_bounds()
    if b is None:
        raise ValueError("No map imported yet: import a map, or use Around 3D Cursor")
    x0, y0 = b["min"][0] - IMPORT_MARGIN, b["min"][1] - IMPORT_MARGIN
    x1, y1 = b["max"][0] + IMPORT_MARGIN, b["max"][1] + IMPORT_MARGIN
    return (lambda p: x0 <= p[0] <= x1 and y0 <= p[1] <= y1), "map import #%d %s" % (b["num"], b["name"])


def _car_node(n):
    return not n.ped and not n.boats


def chains(g, keys):
    """Node key lists covering every link between these nodes once, broken at junctions and dead ends."""
    adj = {k: [m for m in g.nodes[k].links if m in keys] for k in keys}
    seen = set()
    out = []

    def edge(a, b):
        return (a, b) if a < b else (b, a)

    def walk(a, b):
        chain = [a, b]
        seen.add(edge(a, b))
        while len(adj[chain[-1]]) == 2:
            cur = chain[-1]
            nxt = adj[cur][0] if adj[cur][0] != chain[-2] else adj[cur][1]
            if edge(cur, nxt) in seen:
                break
            seen.add(edge(cur, nxt))
            chain.append(nxt)
        out.append(chain)

    for k in sorted(keys):
        if len(adj[k]) != 2:
            for m in adj[k]:
                if edge(k, m) not in seen:
                    walk(k, m)
    for k in sorted(keys):                     # loops with no junction
        for m in adj[k]:
            if edge(k, m) not in seen:
                walk(k, m)
    return out


def road_lanes(g, keys, lift=LIFT):
    """Lane polylines (in traffic direction) for the road nodes `keys`."""
    lines = []
    for chain in chains(g, keys):
        # split where the lane layout changes
        start = 0
        sig = [g.lanes(a, b) for a, b in zip(chain, chain[1:])]
        for i in range(1, len(sig) + 1):
            if i == len(sig) or sig[i] != sig[start]:
                part = [g.nodes[k].pos for k in chain[start:i + 1]]
                fwd, back, med = sig[start]
                segs = len(part) - 1
                for lane in range(fwd):
                    lines.append(offset_line(part, [lane_offset(lane, fwd, back, med)] * segs, lift))
                rev = part[::-1]
                for lane in range(back):
                    lines.append(offset_line(rev, [lane_offset(lane, back, fwd, med)] * segs, lift))
                start = i
    return lines


def sidewalk_lines(g, keys, lift=LIFT):
    return [[(p[0], p[1], p[2] + lift) for p in (g.nodes[k].pos for k in chain)] for chain in chains(g, keys)]


def draw_paths(scene):
    """Draw the chosen area's roads / sidewalks (replacing earlier ones). Returns (lanes, sidewalks)."""
    st = scene.gta_studio
    inside, where = area_filter(scene)
    g = _api().path_graph()
    lanes = walks = 0
    clear_paths(routes=False)
    if st.path_roads:
        keys = {k for k, n in g.nodes.items() if _car_node(n) and inside(n.pos)}
        lines = road_lanes(g, keys)
        if lines:
            _curve_object(scene, ROADS_NAME, "GTA Road Lanes", lines)
        lanes = len(lines)
    if st.path_sidewalks:
        keys = {k for k, n in g.nodes.items() if n.ped and inside(n.pos)}
        lines = sidewalk_lines(g, keys)
        if lines:
            _curve_object(scene, WALKS_NAME, "GTA Sidewalk Paths", lines)
        walks = len(lines)
    st.path_info = "%d lanes, %d sidewalk paths (%s)" % (lanes, walks, where)
    return lanes, walks


def clear_paths(routes=False):
    """Remove drawn roads / sidewalks (and route curves too when routes=True)."""
    kinds = {ROADS_NAME, WALKS_NAME} | ({ROUTES_NAME} if routes else set())
    obs = [o for o in bpy.data.objects if o.get("gta_paths") in kinds]
    curves = [o.data for o in obs if o.data is not None]
    for o in obs:
        bpy.data.objects.remove(o)
    for c in curves:
        if c.users == 0:
            bpy.data.curves.remove(c)
    for name in kinds:
        c = bpy.data.collections.get(name)
        if c is not None and not c.all_objects:
            bpy.data.collections.remove(c)
    top = bpy.data.collections.get(COLL_NAME)
    if top is not None and not top.children and not top.objects:
        bpy.data.collections.remove(top)


# ----------------------------------------------------------------------------- routes
def nearest_node(g, point, ped):
    """Key of the road (ped=False) or sidewalk node nearest to point (x, y, z), or None."""
    px, py, pz = point
    best, best_d = None, float("inf")
    for k, n in g.nodes.items():
        if n.ped != ped or (not ped and n.boats):
            continue
        x, y, z = n.pos
        d = (x - px) ** 2 + (y - py) ** 2 + 4.0 * (z - pz) ** 2       # height counts more (bridges)
        if d < best_d:
            best, best_d = k, d
    return best


def node_near_ray(g, origin, direction, ped):
    """Node closest to a view ray (for clicks that hit no geometry), or None."""
    d = Vector(direction).normalized()
    o = Vector(origin)
    best, best_d = None, float("inf")
    for k, n in g.nodes.items():
        if n.ped != ped or (not ped and n.boats):
            continue
        v = Vector(n.pos) - o
        t = v.dot(d)
        if t <= 0:
            continue
        dist = (v - d * t).length / (1.0 + 0.002 * t)       # far away, a bigger miss is fine
        if dist < best_d:
            best, best_d = k, dist
    return best


def find_route(g, a, b, ped):
    """Node keys from a to b along roads (only the way the lanes go) or sidewalks; None if unreachable."""
    if a is None or b is None:
        return None
    if a == b:
        return [a]
    goal = Vector(g.nodes[b].pos)
    dist = {a: 0.0}
    prev = {}
    heap = [((Vector(g.nodes[a].pos) - goal).length, 0.0, a)]
    done = set()
    while heap:
        _f, d, k = heapq.heappop(heap)
        if k in done:
            continue
        if k == b:
            break
        done.add(k)
        n = g.nodes[k]
        for m in n.links:
            nm = g.nodes[m]
            if nm.ped != ped or (not ped and (nm.boats or g.lanes(k, m)[0] == 0)):
                continue
            nd = d + math.dist(n.pos, nm.pos)
            if nd < dist.get(m, float("inf")):
                dist[m] = nd
                prev[m] = k
                heapq.heappush(heap, (nd + (Vector(nm.pos) - goal).length, nd, m))
    if b not in prev:
        return None
    out = [b]
    while out[-1] != a:
        out.append(prev[out[-1]])
    return out[::-1]


def route_points(g, keys, ped):
    """Smoothed route through these nodes: sidewalk centre, or the rightmost lane of the road."""
    pts = [g.nodes[k].pos for k in keys]
    if len(pts) < 2:
        return pts
    if not ped:
        offs = []
        for a, b in zip(keys, keys[1:]):
            fwd, back, med = g.lanes(a, b)
            fwd = max(fwd, 1)
            offs.append(lane_offset(fwd - 1, fwd, back, med))
        pts = offset_line(pts, offs)
    return smooth(pts, 3 if not ped else 2)


# ----------------------------------------------------------------------------- what moves
def ped_armature(ob):
    """The ped armature ob belongs to (itself, a parent or a child of its root), or None."""
    x = ob
    while x is not None:
        if x.type == 'ARMATURE':
            return x
        x = x.parent
    return None


def mover_of(ob):
    """(object to move, 'VEHICLE' | 'PED') for the selection, or (None, None)."""
    if ob is None:
        return None, None
    from . import vehicle
    root = vehicle.find_root(ob)
    if root is not None:
        return root, 'VEHICLE'
    arm = ped_armature(ob)
    if arm is not None:
        top = arm
        while top.parent is not None:
            top = top.parent
        return top, 'PED'
    return None, None


def _to_local(root, ob):
    return root.matrix_world.inverted_safe() @ ob.matrix_world


def _visible_meshes(objs):
    return [o for o in objs if o.type == 'MESH' and not o.hide_viewport and not o.hide_get()
            and not o.name.lower().split(".")[0].endswith(("_dam", "_vlo", "_lod"))]


def _low_z(root, objs):
    zs = []
    for o in objs:
        m = _to_local(root, o)
        zs += [(m @ Vector(c)).z for c in o.bound_box]
    return min(zs) if zs else 0.0


def vehicle_rig(root):
    """Wheels (object, radius, spin sign), steering objects, wheelbase, height of the origin above ground."""
    bpy.context.view_layer.update()           # matrix_world of parts just imported / moved is stale
    objs = [root] + list(root.children_recursive)
    by = {o.name.lower().split(".")[0]: o for o in objs}
    wheels, ys = [], []
    for o in _visible_meshes(objs):
        if not o.name.lower().startswith("wheel"):
            continue
        m = _to_local(root, o)
        corners = [m @ Vector(c) for c in o.bound_box]
        zs = [c.z for c in corners]
        radius = max((max(zs) - min(zs)) * 0.5, 0.05)
        axis = m.to_3x3() @ Vector((1, 0, 0))
        wheels.append((o, radius, -1.0 if axis.x >= 0 else 1.0))
        ys.append(sum(c.y for c in corners) / 8.0)       # the wheel's centre (bike wheels have no dummy)
    steer = [by[n] for n in ("wheel_lf_dummy", "wheel_rf_dummy", "forks_front") if n in by]
    wheelbase = (max(ys) - min(ys)) if len(ys) >= 2 else 2.6
    low = _low_z(root, [w[0] for w in wheels]) if wheels else _low_z(root, _visible_meshes(objs))
    return {"wheels": wheels, "steer": steer, "wheelbase": max(abs(wheelbase), 0.5), "height": -low}


# ----------------------------------------------------------------------------- keyframes
def _new_action(ob, name):
    act = bpy.data.actions.new(name)
    act["gta_path"] = True
    slot = act.slots.new(id_type='OBJECT', name=ob.name)
    ad = ob.animation_data or ob.animation_data_create()
    ad.action = act
    try:
        ad.action_slot = slot
    except Exception:                 # noqa - assigned automatically
        pass
    from .compat import ensure_channelbag
    return ensure_channelbag(act, slot)


def _write(cb, path, index, frames, values):
    fc = cb.fcurves.new(path, index=index)
    n = len(frames)
    fc.keyframe_points.add(n)
    co = [0.0] * (2 * n)
    co[0::2] = frames
    co[1::2] = values
    fc.keyframe_points.foreach_set("co", co)
    fc.keyframe_points.foreach_set("interpolation", [1] * n)       # LINEAR
    fc.update()
    return fc


def _remember(ob):
    """Keep the transform to put back on Stop Following (once)."""
    if "gta_path_rest" not in ob:
        ob["gta_path_rest"] = [v for row in ob.matrix_basis for v in row]
        ob["gta_path_mode"] = ob.rotation_mode
        ad = ob.animation_data
        if ad is not None and ad.action is not None and not ad.action.get("gta_path"):
            ob["gta_path_prev_action"] = ad.action.name


def _rest(ob):
    v = list(ob["gta_path_rest"])
    return Matrix([v[0:4], v[4:8], v[8:12], v[12:16]])


# ----------------------------------------------------------------------------- walking
def gait_cycle(arm, gait):
    """(anim, frames per cycle at fps, metres per cycle, forward (x, y) in armature space) of the
    ped.ifp animation for this gait."""
    a = _api()
    path = a.game_file("anim/ped.ifp")
    if path is None:
        raise ValueError("anim/ped.ifp not found in the game folder")
    f = a.ifp_load(path)
    name = GAITS[gait]
    anim = f.find(name) or next((x for x in f.animations if x.name.lower() == name.lower()), None)
    if anim is None:
        raise ValueError("%s is not in ped.ifp" % name)
    from . import animate
    rb = animate.root_bone(arm)
    track = None
    for b in anim.bones:
        if b.keyframes and b.has_pos and (b.bone_id == 0 or (rb is not None and b.name.strip().lower()
                                                               == rb.name.lower())):
            track = b
            break
    if track is None or rb is None:
        raise ValueError("%s has no root motion" % name)
    k0, k1 = track.keyframes[0], track.keyframes[-1]
    d = Vector(k1.pos) - Vector(k0.pos)
    pm = rb.parent.matrix_local.to_3x3() if rb.parent else Matrix.Identity(3)
    d = pm @ d
    dist = math.hypot(d.x, d.y)
    if dist < 0.05:
        raise ValueError("%s doesn't move forward" % name)
    return anim, anim.duration, dist, (d.x / dist, d.y / dist)


def _walk_strip(arm, anim, fps, frame0, n_frames, times):
    """NLA track 'GTA Path: <anim>' with the in-place cycle; its time is keyed from `times`."""
    a = _api()
    ad = arm.animation_data or arm.animation_data_create()
    keep, keep_slot = ad.action, (ad.action_slot if ad.action is not None else None)
    act, _missing = a.ifp_apply(arm, anim, start_frame=1.0, fps=fps, root_mode='INPLACE', new_action=True,
                                action_name="GTA Path " + anim.name, clear_pose=False)
    act.use_fake_user = False
    act["gta_path"] = True
    ad.action = keep
    if keep is not None and keep_slot is not None:
        try:
            ad.action_slot = keep_slot
        except Exception:             # noqa
            pass
    tr = ad.nla_tracks.new()
    tr.name = "GTA Path: " + anim.name
    s = tr.strips.new(anim.name, int(frame0), act)
    length = max(act.frame_range[1] - act.frame_range[0], 1.0)
    s.repeat = max(1.0, min(1000.0, n_frames / length + 1.0))
    s.extrapolation = 'HOLD'
    s.use_animated_time = True
    s.use_animated_time_cyclic = True
    fc = s.fcurves.find("strip_time")
    if fc is not None:
        while len(fc.keyframe_points):
            fc.keyframe_points.remove(fc.keyframe_points[0], fast=True)
        f0 = act.frame_range[0]
        n = len(times)
        fc.keyframe_points.add(n)
        co = [0.0] * (2 * n)
        co[0::2] = [frame0 + i for i in range(n)]
        co[1::2] = [f0 + t * length for t in times]
        fc.keyframe_points.foreach_set("co", co)
        fc.keyframe_points.foreach_set("interpolation", [1] * n)
        fc.update()
    return tr, act


# ----------------------------------------------------------------------------- follow
def follow(scene, ob, start, end, speed_kmh=None, gait=None, frame=None):
    """Make the vehicle / ped `ob` belongs to drive / walk from start to end (points near the path).
    Returns a dict: route (node keys), length, frames, kind, curve (route object)."""
    mover, kind = mover_of(ob)
    if mover is None:
        raise ValueError("Select a vehicle or a ped first")
    st = scene.gta_studio
    ped = kind == 'PED'
    g = _api().path_graph()
    a, b = nearest_node(g, start, ped), nearest_node(g, end, ped)
    keys = find_route(g, a, b, ped)
    if keys is None:
        raise ValueError("No %s connects those points (one-way streets count)" % ("sidewalk" if ped else "road"))
    pts = route_points(g, keys, ped)
    line = Polyline(pts)
    if line.length < 1.0:
        raise ValueError("The start and end are the same spot on the %s" % ("sidewalk" if ped else "road"))
    stop_follow(mover, keep_route=False)
    bpy.context.view_layer.update()
    from . import animate
    fps = animate.fps_of(scene)
    frame0 = scene.frame_current if frame is None else frame

    if ped:
        arm = ped_armature(ob)
        anim, cycle_s, cycle_m, fwd = gait_cycle(arm, gait or st.follow_gait)
        vmax = cycle_m / max(cycle_s, 1e-3) * st.follow_ped_speed
        dist = speed_profile(line.length, vmax, st.follow_ease, fps)
        rel = _to_local(mover, arm).to_3x3() if arm is not mover else Matrix.Identity(3)
        f3 = rel @ Vector((fwd[0], fwd[1], 0.0))
        yaw_off = heading(f3.x, f3.y)
        height = -_low_z(mover, _visible_meshes([mover] + list(mover.children_recursive)))
        h = 0.6
    else:
        rig = vehicle_rig(mover)
        vmax = max(1.0, speed_kmh if speed_kmh is not None else st.follow_speed) / 3.6
        dist = speed_profile(line.length, vmax, st.follow_ease, fps)
        yaw_off = 0.0
        height = rig["height"]
        h = max(1.0, rig["wheelbase"] * 0.5)

    frames = [frame0 + i for i in range(len(dist))]
    locs, yaws, pitches = [], [], []
    for s in dist:
        p = line.at(s)
        d = line.direction(s, h)
        locs.append((p.x, p.y, p.z + height))
        yaws.append(heading(d.x, d.y) - yaw_off)
        horiz = math.hypot(d.x, d.y)
        pitches.append(0.0 if ped else math.atan2(d.z, max(horiz, 1e-6)))
    yaws = _unwrap(yaws)

    _remember(mover)
    mover.rotation_mode = 'XYZ'
    cb = _new_action(mover, "GTA Path " + mover.name)
    for i in range(3):
        _write(cb, "location", i, frames, [v[i] for v in locs])
    _write(cb, "rotation_euler", 0, frames, pitches)
    _write(cb, "rotation_euler", 1, frames, [0.0] * len(frames))
    _write(cb, "rotation_euler", 2, frames, yaws)
    mover["gta_path_kind"] = kind
    mover["gta_path_range"] = [frames[0], frames[-1]]

    if ped:
        _walk_strip(arm, anim, fps, frame0, len(frames), [s / cycle_m for s in dist])
        if arm is not mover:
            _remember(arm)
        if arm.animation_data is not None and arm.animation_data.action is not None \
                and not arm.animation_data.action.get("gta_path"):
            arm["gta_path_prev_action"] = arm.animation_data.action.name
            arm.animation_data.action = None
        arm["gta_path_arm"] = True
    else:
        _vehicle_keys(mover, rig, line, dist, frames)

    curve = _curve_object(scene, ROUTES_NAME, "GTA Route " + mover.name,
                          [[(p.x, p.y, p.z + LIFT) for p in line.pts]])
    curve.hide_render = True
    curve["gta_route_of"] = mover.name
    mover["gta_path_route"] = curve.name
    if frames[-1] > scene.frame_end:
        scene.frame_end = int(math.ceil(frames[-1]))
    return {"route": keys, "length": line.length, "frames": len(frames), "kind": kind, "curve": curve,
            "speed": vmax}


def curvature_at(line, s, w):
    """Signed turn per metre (left positive) around s, measured over w metres."""
    d0, d1 = line.direction(s - w * 0.5, w * 0.25), line.direction(s + w * 0.5, w * 0.25)
    a0, a1 = heading(d0.x, d0.y), heading(d1.x, d1.y)
    da = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
    return da / w


def _vehicle_keys(root, rig, line, dist, frames):
    wb = rig["wheelbase"]
    for w, radius, sign in rig["wheels"]:
        _remember(w)
        rest = w.rotation_euler.copy() if w.rotation_mode == 'XYZ' else None
        w.rotation_mode = 'XYZ'
        if rest is None:
            rest = w.rotation_euler.copy()
        cb = _new_action(w, "GTA Path " + w.name)
        _write(cb, "rotation_euler", 0, frames, [rest.x + sign * s / radius for s in dist])
        w["gta_path_part"] = root.name
    steer = [max(-MAX_STEER, min(MAX_STEER, math.atan(wb * curvature_at(line, s, max(wb, 2.0))))) for s in dist]
    for o in rig["steer"]:
        _remember(o)
        o.rotation_mode = 'ZYX'                  # euler Z last in the product = turn about its own Z
        z0 = o.rotation_euler.z
        cb = _new_action(o, "GTA Path " + o.name)
        _write(cb, "rotation_euler", 2, frames, [z0 + a for a in steer])
        o["gta_path_part"] = root.name


def _restore(ob):
    ad = ob.animation_data
    if ad is not None:
        act = ad.action
        if act is not None and act.get("gta_path"):
            ad.action = None
            if act.users == 0:
                bpy.data.actions.remove(act)
        for tr in list(ad.nla_tracks):
            if tr.name.startswith("GTA Path: "):
                acts = [s.action for s in tr.strips if s.action is not None]
                ad.nla_tracks.remove(tr)
                for a in acts:
                    if a.users == 0 and a.get("gta_path"):
                        bpy.data.actions.remove(a)
        prev = ob.get("gta_path_prev_action")
        if prev and prev in bpy.data.actions:
            ad.action = bpy.data.actions[prev]
    if "gta_path_rest" in ob:
        ob.rotation_mode = ob.get("gta_path_mode", ob.rotation_mode)
        ob.matrix_basis = _rest(ob)
    for key in ("gta_path_rest", "gta_path_mode", "gta_path_prev_action", "gta_path_part", "gta_path_kind",
                "gta_path_range", "gta_path_arm"):
        if key in ob:
            del ob[key]


def stop_follow(mover, keep_route=False):
    """Undo follow() on a vehicle / ped root: animation removed, transforms put back."""
    if mover is None:
        return False
    had = "gta_path_rest" in mover
    objs = [mover] + list(mover.children_recursive)
    for o in objs:
        if "gta_path_rest" in o or o.get("gta_path_arm") or o.get("gta_path_part") == mover.name:
            _restore(o)
    route = mover.get("gta_path_route")
    if route is not None:
        if not keep_route:
            ob = bpy.data.objects.get(route)
            if ob is not None and ob.get("gta_paths") == ROUTES_NAME:
                data = ob.data
                bpy.data.objects.remove(ob)
                if data is not None and data.users == 0:
                    bpy.data.curves.remove(data)
            c = bpy.data.collections.get(ROUTES_NAME)
            if c is not None and not c.all_objects:
                bpy.data.collections.remove(c)
        del mover["gta_path_route"]
    return had


def following(mover):
    return mover is not None and "gta_path_route" in mover


# ----------------------------------------------------------------------------- picking two points
class RoutePicker:
    """Two clicks in the 3D view: start, then end. click(depsgraph, origin, direction) takes a view
    ray; the point is where it hits the scene (not the moving object or path curves), else the path
    node nearest the ray. Tests drive it with rays directly."""

    def __init__(self, scene, ob):
        self.scene = scene
        self.mover, self.kind = mover_of(ob)
        self.points = []

    def point_for_ray(self, depsgraph, origin, direction):
        g = _api().path_graph()
        ped = self.kind == 'PED'
        skip = set()
        if self.mover is not None:
            skip = {self.mover.name} | {o.name for o in self.mover.children_recursive}
        if depsgraph is not None:
            o, d = Vector(origin), Vector(direction).normalized()
            for _ in range(8):                      # step through the moving object itself
                hit, loc, _n, _i, ob, _m = self.scene.ray_cast(depsgraph, o, d, distance=20000.0)
                if not hit:
                    break
                if ob is not None and ob.name not in skip and not ob.get("gta_paths"):
                    return tuple(loc)
                o = loc + d * 0.01
        k = node_near_ray(g, origin, direction, ped)
        return tuple(g.nodes[k].pos) if k is not None else None

    def click(self, depsgraph, origin, direction):
        """Returns 'START', 'END' (both picked), or None when nothing could be picked."""
        p = self.point_for_ray(depsgraph, origin, direction)
        if p is None:
            return None
        self.points.append(p)
        return 'START' if len(self.points) == 1 else 'END'

    def snapped(self):
        """The picked points moved onto the nearest path node (where the route will start / end)."""
        g = _api().path_graph()
        ped = self.kind == 'PED'
        out = []
        for p in self.points:
            k = nearest_node(g, p, ped)
            out.append(g.nodes[k].pos if k is not None else p)
        return out


_DRAW = {"handle": None, "points": []}


def _draw_markers():
    import gpu
    from gpu_extras.batch import batch_for_shader
    pts = _DRAW["points"]
    if not pts:
        return
    lines, cols = [], []
    for i, p in enumerate(pts):
        c = (0.2, 1.0, 0.3, 1.0) if i == 0 else (1.0, 0.25, 0.2, 1.0)
        x, y, z = p
        for a, b in (((x - 2, y, z), (x + 2, y, z)), ((x, y - 2, z), (x, y + 2, z)), ((x, y, z), (x, y, z + 6))):
            lines += (a, b)
            cols += (c, c)
    try:
        sh = gpu.shader.from_builtin('SMOOTH_COLOR')
    except Exception:                 # noqa
        return
    gpu.state.depth_test_set('NONE')
    gpu.state.line_width_set(3.0)
    batch = batch_for_shader(sh, 'LINES', {"pos": lines, "color": cols})
    sh.bind()
    batch.draw(sh)
    gpu.state.line_width_set(1.0)


def _markers(points):
    """Show start (green) / end (red) markers while picking; [] removes the draw handler."""
    _DRAW["points"] = list(points)
    if points and _DRAW["handle"] is None:
        _DRAW["handle"] = bpy.types.SpaceView3D.draw_handler_add(_draw_markers, (), 'WINDOW', 'POST_VIEW')
    elif not points and _DRAW["handle"] is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_DRAW["handle"], 'WINDOW')
        _DRAW["handle"] = None
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


# ----------------------------------------------------------------------------- operators
class _NeedsCore:
    @classmethod
    def poll(cls, context):
        return corelink.api() is not None


class GTASTUDIO_OT_draw_paths(_NeedsCore, bpy.types.Operator):
    bl_idname = "gtastudio.draw_paths"
    bl_label = "Draw Paths"
    bl_description = "Draw the game's road lanes and sidewalks in the chosen area as curves"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        st = context.scene.gta_studio
        if not st.path_roads and not st.path_sidewalks:
            self.report({'ERROR'}, "Tick Roads or Sidewalks first")
            return {'CANCELLED'}
        try:
            lanes, walks = draw_paths(context.scene)
        except (ValueError, RuntimeError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        if not lanes and not walks:
            self.report({'WARNING'}, "No paths in this area")
        else:
            self.report({'INFO'}, "Drew " + st.path_info)
        return {'FINISHED'}


class GTASTUDIO_OT_clear_paths(_NeedsCore, bpy.types.Operator):
    bl_idname = "gtastudio.clear_paths"
    bl_label = "Clear"
    bl_description = "Remove the drawn roads and sidewalks (routes stay)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        clear_paths(routes=False)
        context.scene.gta_studio.path_info = ""
        return {'FINISHED'}


class GTASTUDIO_OT_follow_path(_NeedsCore, bpy.types.Operator):
    bl_idname = "gtastudio.follow_path"
    bl_label = "Pick Route"
    bl_description = ("Click the start, then the end, in the 3D view: the selected vehicle or ped follows "
                      "the roads / sidewalks between them from the current frame")
    bl_options = {'REGISTER', 'UNDO'}

    start: bpy.props.FloatVectorProperty(size=3, options={'HIDDEN', 'SKIP_SAVE'})
    end: bpy.props.FloatVectorProperty(size=3, options={'HIDDEN', 'SKIP_SAVE'})
    use_points: bpy.props.BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        if not self.use_points:
            self.report({'ERROR'}, "Pick the start and end in the 3D view")
            return {'CANCELLED'}
        try:
            res = follow(context.scene, context.active_object, tuple(self.start), tuple(self.end))
        except (ValueError, RuntimeError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        what = "Drives" if res["kind"] == 'VEHICLE' else "Walks"
        self.report({'INFO'}, "%s %d m in %d frames" % (what, res["length"], res["frames"]))
        return {'FINISHED'}

    def invoke(self, context, event):
        mover, _kind = mover_of(context.active_object)
        if mover is None:
            self.report({'ERROR'}, "Select a vehicle or a ped first")
            return {'CANCELLED'}
        if context.screen is None or not any(a.type == 'VIEW_3D' for a in context.screen.areas):
            self.report({'ERROR'}, "Needs a 3D view")
            return {'CANCELLED'}
        try:
            _api().path_graph()                       # read now, not on the first click
        except (ValueError, RuntimeError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self._picker = RoutePicker(context.scene, context.active_object)
        context.window_manager.modal_handler_add(self)
        self._status(context, "Click the START of the route in the 3D view (Esc / right-click: cancel)",
                     "Click the start point")
        return {'RUNNING_MODAL'}

    def _status(self, context, text, step=""):
        _PICK_STEP["text"] = step               # the panel's short version
        if context.workspace is not None:
            context.workspace.status_text_set(text)
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.header_text_set(text)
                area.tag_redraw()

    def _end(self, context):
        _markers([])
        _PICK_STEP["text"] = ""
        if context.workspace is not None:
            context.workspace.status_text_set(None)
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.header_text_set(None)
                area.tag_redraw()

    def _view_under(self, context, event):
        for area in context.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            for r in area.regions:
                if r.type == 'WINDOW' and r.x <= event.mouse_x < r.x + r.width \
                        and r.y <= event.mouse_y < r.y + r.height:
                    return r, area.spaces.active.region_3d
        return None, None

    def modal(self, context, event):
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._end(context)
            return {'CANCELLED'}
        if event.type != 'LEFTMOUSE' or event.value != 'PRESS':
            return {'PASS_THROUGH'}                   # navigate the view while picking
        region, rv3d = self._view_under(context, event)
        if region is None:
            return {'PASS_THROUGH'}                   # clicks on the sidebar etc. work as usual
        from bpy_extras import view3d_utils
        co = (event.mouse_x - region.x, event.mouse_y - region.y)
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, co)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, co)
        state = self._picker.click(context.evaluated_depsgraph_get(), origin, direction)
        if state is None:
            self.report({'WARNING'}, "No road or sidewalk there")
            return {'RUNNING_MODAL'}
        _markers(self._picker.snapped())
        if state == 'START':
            self._status(context, "Now click the END of the route (Esc / right-click: cancel)",
                         "Click the end point")
            return {'RUNNING_MODAL'}
        self._end(context)
        a, b = self._picker.points
        self.start, self.end, self.use_points = a, b, True
        return self.execute(context)


class GTASTUDIO_OT_stop_follow(_NeedsCore, bpy.types.Operator):
    bl_idname = "gtastudio.stop_follow"
    bl_label = "Stop Following"
    bl_description = "Remove the route animation from the selected vehicle or ped and put it back where it was"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        mover, _kind = mover_of(context.active_object)
        if mover is None or not following(mover):
            self.report({'ERROR'}, "The selection isn't following a path")
            return {'CANCELLED'}
        stop_follow(mover)
        return {'FINISHED'}


_PICK_STEP = {"text": ""}      # Follow Path's current step while it waits for clicks, for the panel


def draw_section(layout, context):
    st = context.scene.gta_studio
    col = layout.column(align=True)
    col.prop(st, "path_area", text="")
    if st.path_area == 'CURSOR':
        col.prop(st, "path_radius")
    r = layout.row(align=True)
    r.prop(st, "path_roads", toggle=True)
    r.prop(st, "path_sidewalks", toggle=True)
    r = layout.row(align=True)
    r.scale_y = 1.2
    r.operator("gtastudio.draw_paths", icon='CURVE_PATH')
    r.operator("gtastudio.clear_paths", icon='X')
    if st.path_info:
        layout.label(text=st.path_info, icon='INFO')

    box = layout.box()
    box.label(text="Follow Path", icon='TRACKING')
    mover, kind = mover_of(context.active_object)
    if mover is None:
        box.label(text="Select a vehicle or a ped", icon='INFO')
        return
    box.label(text="%s  ·  %s" % (mover.name, "vehicle" if kind == 'VEHICLE' else "ped"),
              icon='AUTO' if kind == 'VEHICLE' else 'ARMATURE_DATA')
    if kind == 'VEHICLE':
        box.prop(st, "follow_speed", text="Speed (km/h)")
    else:
        box.row().prop(st, "follow_gait", expand=True)
        box.prop(st, "follow_ped_speed")
    box.prop(st, "follow_ease")
    r = box.row(align=True)
    r.scale_y = 1.3
    r.operator("gtastudio.follow_path", icon='RESTRICT_SELECT_OFF')
    if following(mover):
        r.operator("gtastudio.stop_follow", icon='CANCEL')
        rng = mover.get("gta_path_range")
        if rng is not None:
            box.label(text="Frames %d - %d" % (rng[0], rng[1]), icon='TIME')
    if _PICK_STEP["text"]:                      # only while Follow Path waits for a click
        box.label(text=_PICK_STEP["text"], icon='MOUSE_LMB')


classes = (GTASTUDIO_OT_draw_paths, GTASTUDIO_OT_clear_paths, GTASTUDIO_OT_follow_path, GTASTUDIO_OT_stop_follow)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    _markers([])
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
