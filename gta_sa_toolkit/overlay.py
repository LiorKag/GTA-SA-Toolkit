# GTA SA Toolkit - 2D viewport drawing shared by the area picker and the import preview.
# Everything is drawn in region pixels (POST_PIXEL) with the depth test off, so imported terrain
# never hides it. Nothing here creates Blender data (no images): textures come from numpy arrays.
import math

import blf
import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

MAP_ALPHA = 0.6
CIRCLE_COLOR = (1.0, 0.85, 0.1, 1.0)
DOT_COLOR = (1.0, 0.35, 0.1, 0.9)
TEXT_COLOR = (1.0, 1.0, 1.0, 1.0)
SHADOW = (0.0, 0.0, 0.0, 0.75)


def texture_from_array(arr):
    """(h, w, 4) uint8, top row first -> GPU texture (row 0 at the bottom, as the GPU expects)."""
    h, w = arr.shape[:2]
    data = np.ascontiguousarray(arr[::-1], dtype=np.float32).ravel() * (1.0 / 255.0)
    return gpu.types.GPUTexture((w, h), format='RGBA8', data=gpu.types.Buffer('FLOAT', data.size, data))


def begin():
    gpu.state.depth_test_set('NONE')
    gpu.state.blend_set('ALPHA')


def end():
    gpu.state.blend_set('NONE')


def draw_image(tex, quad, alpha=MAP_ALPHA):
    """quad: 4 pixel points for the image's bottom-left, bottom-right, top-right, top-left."""
    sh = gpu.shader.from_builtin('IMAGE_COLOR')
    b = batch_for_shader(sh, 'TRI_FAN', {"pos": quad, "texCoord": ((0, 0), (1, 0), (1, 1), (0, 1))})
    sh.bind()
    sh.uniform_sampler("image", tex)
    sh.uniform_float("color", (1.0, 1.0, 1.0, alpha))
    b.draw(sh)


def draw_lines(points, size, color=CIRCLE_COLOR, width=2.0, closed=False):
    if len(points) < 2:
        return
    sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    pts = list(points) + ([points[0]] if closed else [])
    b = batch_for_shader(sh, 'LINE_STRIP', {"pos": pts})
    sh.bind()
    sh.uniform_float("viewportSize", size)
    sh.uniform_float("lineWidth", width)
    sh.uniform_float("color", color)
    b.draw(sh)


def draw_points(points, color=DOT_COLOR, px=4.0):
    if not points:
        return
    sh = gpu.shader.from_builtin('POINT_UNIFORM_COLOR')
    gpu.state.point_size_set(px)
    b = batch_for_shader(sh, 'POINTS', {"pos": points})
    sh.bind()
    sh.uniform_float("color", color)
    b.draw(sh)
    gpu.state.point_size_set(1.0)


def left_inset():
    """Pixels the toolbar (or a sidebar docked left) covers at the left edge of the 3D view region being
    drawn: with region overlap they sit on top of it. 0 outside a draw callback."""
    try:
        area, win = bpy.context.area, bpy.context.region
    except AttributeError:
        return 0
    if area is None or win is None:
        return 0
    inset = 0
    for r in area.regions:
        if r.type in ('TOOLS', 'UI') and r.width > 1 and r.x <= win.x + 2 and r.x + r.width > win.x:
            inset = max(inset, r.x + r.width - win.x)
    return inset


def draw_text(lines, x, y, size=14):
    """lines: top to bottom, drawn upwards from (x, y) = bottom-left of the last line."""
    blf.size(0, size)
    step = size * 1.5
    for i, text in enumerate(reversed(lines)):
        yy = y + i * step
        blf.color(0, *SHADOW)
        blf.position(0, x + 1, yy - 1, 0)
        blf.draw(0, text)
        blf.color(0, *TEXT_COLOR)
        blf.position(0, x, yy, 0)
        blf.draw(0, text)


def circle_points(cx, cy, r, segments=72):
    return [(cx + r * math.cos(a), cy + r * math.sin(a))
            for a in (2 * math.pi * i / segments for i in range(segments))]
