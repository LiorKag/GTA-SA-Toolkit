# GTA SA Studio - camera and render helpers (Scene tab, "Camera & Render").
#   Camera rig : "GTA Camera" aimed at a "GTA Camera Target" empty (Track To), lens from the preset.
#   Turntable  : "GTA Turntable" empty at the model's centre turning 360 degrees over N frames (linear
#                keys, so frame N+1 = frame 1 and playback loops), "GTA Turntable Camera" parented to it.
#   Map area   : "GTA Map Camera" framing the selected map import (core's api.import_bounds) from a
#                3/4 view, plus render settings (1920x1080, EEVEE).
# Everything lives in a "GTA Studio" collection; running a helper again reuses its objects.
import math

import bpy
from mathutils import Vector

from . import corelink

LENS_FOV = {'GAMEPLAY': 70.0, 'CINEMATIC': 40.0, 'ZOOM': 20.0, 'WIDE': 90.0}   # horizontal degrees
COLL_NAME = "GTA Studio"


def _collection(scene):
    coll = bpy.data.collections.get(COLL_NAME)
    if coll is None:
        coll = bpy.data.collections.new(COLL_NAME)
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)
    return coll


def _object(scene, name, data=None):
    """The helper object called name (created in the GTA Studio collection if missing)."""
    ob = bpy.data.objects.get(name)
    if ob is None:
        ob = bpy.data.objects.new(name, data)
        _collection(scene).objects.link(ob)
    elif scene.objects.get(name) is None:
        _collection(scene).objects.link(ob)
    return ob


def _camera(scene, name):
    ob = bpy.data.objects.get(name)
    if ob is not None and ob.type != 'CAMERA':
        ob.name = name + " (old)"
        ob = None
    if ob is None:
        ob = _object(scene, name, bpy.data.cameras.new(name))
    return ob


def set_fov(cam, scene):
    """Horizontal field of view from the lens preset."""
    cam.data.lens_unit = 'FOV'
    cam.data.sensor_fit = 'HORIZONTAL'
    cam.data.angle = math.radians(LENS_FOV.get(scene.gta_studio.lens_preset, 70.0))


def apply_lens(scene):
    """Lens preset changed: update the scene camera if it is one of Studio's."""
    cam = scene.camera
    if cam is not None and cam.type == 'CAMERA' and cam.name.startswith("GTA "):
        set_fov(cam, scene)


def _fit_distance(cam, scene, radius):
    """How far a camera must be for a sphere of this radius to fill the narrower side of the frame."""
    r = scene.render
    aspect = (r.resolution_x * r.pixel_aspect_x) / max(1.0, r.resolution_y * r.pixel_aspect_y)
    h = cam.data.angle
    v = 2.0 * math.atan(math.tan(h / 2.0) / aspect) if aspect >= 1.0 else h
    return radius / math.sin(min(h, v) / 2.0)


def _look_at(ob, target):
    """Aim an unparented object at target (uses .location: matrix_world is stale until the scene updates)."""
    ob.rotation_mode = 'QUATERNION'
    ob.rotation_quaternion = (Vector(target) - ob.location).to_track_quat('-Z', 'Y')


def bounds(objects):
    """(centre, radius, bottom z) of the visible meshes among objects (world space), or None.
    Hidden parts (vehicle damage/LOD models) are skipped: they aren't in the shot, and Blender doesn't
    update their matrix_world. Call bpy.context.view_layer.update() first after moving things."""
    pts = [o.matrix_world @ Vector(c) for o in objects
           if o.type == 'MESH' and o.visible_get() and not o.hide_render for c in o.bound_box]
    if not pts:
        return None
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return (mn + mx) / 2.0, max((mx - mn).length / 2.0, 0.25), mn.z


def _hierarchy(ob):
    """ob and everything parented under it (children_recursive walks the file once: fine per click)."""
    return [ob] + list(ob.children_recursive)


# ----------------------------------------------------------------------------- camera rig
def add_camera(context):
    """Camera looking at the selected model (or the 3D cursor) from the front-left, a little above."""
    scene = context.scene
    context.view_layer.update()
    ob = context.active_object
    b = bounds(_hierarchy(ob)) if ob is not None and ob.select_get() else None
    centre, radius = (b[0], b[1]) if b else (scene.cursor.location.copy(), 5.0)
    target = _object(scene, "GTA Camera Target")
    target.empty_display_type = 'SPHERE'
    target.empty_display_size = 0.3
    target.location = centre
    cam = _camera(scene, "GTA Camera")
    set_fov(cam, scene)
    dist = _fit_distance(cam, scene, radius) * 1.2
    el, az = math.radians(15.0), math.radians(-35.0)
    cam.parent = None
    cam.location = centre + Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el))) * dist
    con = cam.constraints.get("GTA Aim") or cam.constraints.new('TRACK_TO')
    con.name = "GTA Aim"
    con.target = target
    con.track_axis, con.up_axis = 'TRACK_NEGATIVE_Z', 'UP_Y'
    cam.data.clip_end = max(cam.data.clip_end, dist + 4 * radius)
    scene.camera = cam
    return cam


# ----------------------------------------------------------------------------- turntable
def _fcurves(ob):
    ad = ob.animation_data
    act = ad.action if ad else None
    if act is None:
        return []
    try:
        from bpy_extras import anim_utils
        cb = anim_utils.action_get_channelbag_for_slot(act, ad.action_slot)
        if cb is not None:
            return list(cb.fcurves)
    except (ImportError, AttributeError):
        pass
    return list(getattr(act, "fcurves", ()))


def turntable(context, ob, frames):
    """Camera circling ob (and its children) once over frames; returns (pivot, camera)."""
    scene = context.scene
    context.view_layer.update()
    b = bounds(_hierarchy(ob))
    if b is None:
        raise ValueError("%s has no geometry to turn around" % ob.name)
    centre, radius, _bottom = b
    pivot = _object(scene, "GTA Turntable")
    pivot.empty_display_type = 'CIRCLE'
    pivot.empty_display_size = radius
    pivot.animation_data_clear()
    pivot.rotation_mode = 'XYZ'
    pivot.location = centre
    pivot.rotation_euler = (0.0, 0.0, 0.0)
    cam = _camera(scene, "GTA Turntable Camera")
    set_fov(cam, scene)
    cam.constraints.clear()
    dist = _fit_distance(cam, scene, radius) * 1.1
    el = math.radians(12.0)
    cam.parent = pivot
    cam.matrix_parent_inverse.identity()
    cam.location = Vector((0.0, -math.cos(el), math.sin(el))) * dist
    cam.rotation_mode = 'QUATERNION'
    cam.rotation_quaternion = (-cam.location).to_track_quat('-Z', 'Y')    # at the pivot (local space)
    cam.data.clip_end = max(cam.data.clip_end, dist + 4 * radius)

    first, last = 1, 1 + frames                     # last key = first frame of the next turn
    pivot.keyframe_insert("rotation_euler", index=2, frame=first)
    pivot.rotation_euler.z = 2.0 * math.pi
    pivot.keyframe_insert("rotation_euler", index=2, frame=last)
    pivot.rotation_euler.z = 0.0
    for fc in _fcurves(pivot):
        for kp in fc.keyframe_points:
            kp.interpolation = 'LINEAR'
        fc.extrapolation = 'LINEAR'
    if pivot.animation_data and pivot.animation_data.action:
        pivot.animation_data.action.name = "GTA Turntable"
    scene.frame_start, scene.frame_end = first, first + frames - 1    # frames 1..N loop seamlessly
    scene.frame_set(first)
    scene.camera = cam
    return pivot, cam


# ----------------------------------------------------------------------------- map area
def _eevee_id():
    items = bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items.keys()
    for key in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'):
        if key in items:
            return key
    return None


def frame_map_area(context, num=None):
    """Camera + render settings for a map import (num=None: the one selected in the World tab).
    Returns (camera, bounds dict) or raises ValueError."""
    scene = context.scene
    api = corelink.api()
    info = api.import_bounds(num) if api else None
    if info is None:
        raise ValueError("No imported map to frame: import a map area first")
    mn, mx = Vector(info["min"]), Vector(info["max"])
    centre = (mn + mx) / 2.0
    radius = max((mx - mn).length / 2.0, 20.0) + 15.0      # object positions + room for their size
    r = scene.render
    r.resolution_x, r.resolution_y, r.resolution_percentage = 1920, 1080, 100
    r.pixel_aspect_x = r.pixel_aspect_y = 1.0
    engine = _eevee_id()
    if engine:
        r.engine = engine
    if hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
        scene.eevee.taa_render_samples = 64
    r.film_transparent = False
    cam = _camera(scene, "GTA Map Camera")
    set_fov(cam, scene)
    cam.constraints.clear()
    cam.parent = None
    dist = _fit_distance(cam, scene, radius)
    el, az = math.radians(35.0), math.radians(-45.0)        # from the south-west, looking north-east
    cam.location = centre + Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el))) * dist
    _look_at(cam, centre)
    cam.data.clip_start = max(0.1, dist * 0.001)
    cam.data.clip_end = dist + 3.0 * radius
    if scene.gta_studio.fog and hasattr(scene, "eevee") and hasattr(scene.eevee, "volumetric_end"):
        scene.eevee.volumetric_end = max(scene.eevee.volumetric_end, cam.data.clip_end)
    scene.camera = cam
    return cam, info
