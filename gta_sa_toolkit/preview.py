# GTA SA Toolkit - preview a map import in the 3D views before running it.
# Draws the area outline and one dot per object that would be placed, with object/model counts and a
# time estimate. Reads only the already-scanned IPL/IDE data: no models, no IMG reads, no Blender data.
# Shown until: Import Map, a change to any plan setting (property update callbacks), the 3D cursor
# moving while it is the area centre, Hide Preview, or loading another file.
import time

import bpy

from . import mapimport, overlay

MARGIN_COLOR = (1.0, 0.85, 0.1, 0.35)
SECTION_COLOR = (0.3, 0.8, 1.0, 0.9)

_PV = {"data": None, "handles": None, "batch": None, "stale": False, "view": None}


class PreviewData:
    def __init__(self):
        self.points = []          # (x, y, z) of every object that would be placed
        self.outlines = []        # (points, colour) polylines at ground level, closed
        self.objects = 0
        self.models = 0
        self.new_models = 0
        self.cached = 0             # of the new models, how many the asset cache holds
        self.skipped_placed = 0
        self.seconds = 0.0
        self.cursor = None        # (x, y) when the area follows the 3D cursor
        self.text = ""


def format_text(d):
    t = "Preview: %d objects, %d models (%d new)" % (d.objects, d.models, d.new_models)
    if d.cached:
        t = t[:-1] + ", %d from cache)" % d.cached
    if d.skipped_placed:
        t += ", %d already placed skipped" % d.skipped_placed
    if not d.objects:
        return t + "  ·  nothing to import"
    if d.seconds < 1.0:
        return t + "  ·  under 1 s"
    return t + "  ·  about %s" % mapimport.format_duration(d.seconds)


def short_text(d):
    """The panel's one-liner (the full text is the Preview button's tooltip and the viewport text)."""
    if not d.objects:
        return "Nothing new to import" if d.skipped_placed else "Nothing to import"
    t = "%d objects · %d models" % (d.objects, d.models)
    return t + (" · under 1 s" if d.seconds < 1.0 else " · ~%s" % mapimport.format_duration(d.seconds))


def build(context, game=None):
    """Work out what Import Map would do right now. Loads nothing."""
    from . import ui
    p = context.scene.gta_tk
    game = game or mapimport.get_game(p.game_root)
    center = ui._area_center(context)
    plan = mapimport.build_plan(game, p, center)
    if p.skip_placed:
        mapimport.skip_already_placed(context.scene, plan)
    d = PreviewData()
    d.points = [tuple(i.pos) for i in plan.insts]
    d.objects, d.models = len(plan.insts), len(plan.models)
    new = mapimport.new_models(game, plan, mapimport.library_models())
    d.new_models = len(new)
    d.cached = mapimport.cached_count(game, new, mapimport.map_opts(p), p.load_textures)
    d.skipped_placed = plan.skipped_placed
    d.seconds = mapimport.estimate_seconds(d.new_models, d.objects, len(context.scene.objects), n_cached=d.cached)
    if p.map_source == 'AREA' or p.limit_to_area:
        cx, cy = center
        d.outlines.append(([(x, y, 0.0) for x, y in overlay.circle_points(cx, cy, p.area_radius)],
                           overlay.CIRCLE_COLOR))
        if p.area_margin > 0:
            d.outlines.append(([(x, y, 0.0) for x, y in overlay.circle_points(cx, cy, p.area_radius + p.area_margin)],
                               MARGIN_COLOR))
        if p.area_center == 'CURSOR':
            d.cursor = (cx, cy)
    if p.map_source == 'SECTIONS':
        chosen = {s.name for s in p.sections if s.use}
        for sec in game.sections:
            if sec.name in chosen and sec.bounds:
                x0, y0, x1, y1 = sec.bounds
                d.outlines.append(([(x0, y0, 0.0), (x1, y0, 0.0), (x1, y1, 0.0), (x0, y1, 0.0)], SECTION_COLOR))
    d.text = format_text(d)
    return d


# ----------------------------------------------------------------------------- show / clear
def shown():
    return _PV["data"] is not None


def data():
    return _PV["data"]


def bounds(d):
    """(x0, y0, x1, y1) around the dots and outlines, or None."""
    xy = [p[:2] for p in d.points] + [p[:2] for pts, _c in d.outlines for p in pts]
    if not xy:
        return None
    xs, ys = [p[0] for p in xy], [p[1] for p in xy]
    return min(xs), min(ys), max(xs), max(ys)


def show(d, view=None):
    """view: an areapick.RegionView (or a test stand-in) to switch to a top view framed on the preview;
    the view it had comes back when the preview is cleared."""
    clear()
    _PV.update(data=d, batch=None, stale=False)
    st = bpy.types.SpaceView3D
    _PV["handles"] = (st.draw_handler_add(_draw_view, (), 'WINDOW', 'POST_VIEW'),
                      st.draw_handler_add(_draw_pixel, (), 'WINDOW', 'POST_PIXEL'))
    if _on_load not in bpy.app.handlers.load_pre:        # only while a preview is shown
        bpy.app.handlers.load_pre.append(_on_load)
    b = bounds(d)
    if view is not None and b is not None:
        saved = view.save()
        view.frame_top(*b)
        _PV["view"] = (view, saved)
    _redraw()


def clear(*_args):
    """Remove the preview (cheap no-op when none is shown). Also used as a property update callback."""
    if _PV["data"] is None and _PV["handles"] is None:
        return None
    v, _PV["view"] = _PV.get("view"), None
    if v is not None:
        try:
            v[0].restore(v[1])                          # back to the view from before the preview
        except (ReferenceError, AttributeError, RuntimeError):
            pass                                        # that 3D view was closed meanwhile
    h, _PV["handles"] = _PV["handles"], None
    if h:
        for handle in h:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
            except (ValueError, RuntimeError):
                pass
    _PV.update(data=None, batch=None, stale=False)
    if _on_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_on_load)
    _redraw()
    return None                                         # (also a one-shot timer: don't repeat)


@bpy.app.handlers.persistent
def _on_load(*_):
    _PV["view"] = None               # the old file's 3D view is going away: nothing to restore
    clear()


def _redraw():
    wm = bpy.context.window_manager
    for win in getattr(wm, "windows", ()):
        for a in win.screen.areas:
            if a.type in ('VIEW_3D', 'UI', 'PROPERTIES'):
                a.tag_redraw()


def _cursor_moved(d):
    if d.cursor is None:
        return False
    c = bpy.context.scene.cursor.location
    return abs(c.x - d.cursor[0]) > 1e-4 or abs(c.y - d.cursor[1]) > 1e-4


def _check_stale(d):
    """The area follows the 3D cursor and it moved: stop drawing, remove the preview after this redraw
    (draw handlers can't be removed while Blender is running them)."""
    if _PV["stale"]:
        return True
    if _cursor_moved(d):
        _PV["stale"] = True
        bpy.app.timers.register(clear, first_interval=0.0)
        return True
    return False


# ----------------------------------------------------------------------------- drawing
def _draw_view():
    d = _PV["data"]
    if d is None:
        return
    try:
        if _check_stale(d):
            return
        region = bpy.context.region
        draw_3d(d, (region.width, region.height))
    except Exception as e:                       # noqa - never break Blender's drawing
        print("[GTA SA Toolkit] preview draw:", e)


def draw_3d(d, size):
    """Dots and outlines in world space (current view/projection matrices), depth test off."""
    import gpu
    from gpu_extras.batch import batch_for_shader
    overlay.begin()
    try:
        for pts, col in d.outlines:
            overlay.draw_lines(pts, size, color=col, width=2.0, closed=True)
        if d.points:
            sh = gpu.shader.from_builtin('POINT_UNIFORM_COLOR')
            if _PV["batch"] is None or _PV["batch"][0] is not d:
                _PV["batch"] = (d, batch_for_shader(sh, 'POINTS', {"pos": d.points}))
            gpu.state.point_size_set(4.0)
            sh.bind()
            sh.uniform_float("color", overlay.DOT_COLOR)
            _PV["batch"][1].draw(sh)
            gpu.state.point_size_set(1.0)
    finally:
        overlay.end()


def _draw_pixel():
    d = _PV["data"]
    if d is None or _PV["stale"]:
        return
    try:
        overlay.begin()
        overlay.draw_text([d.text, "Import Map, change the area or click Hide Preview to clear it"],
                          20 + overlay.left_inset(), 24)
        overlay.end()
    except Exception as e:                       # noqa
        print("[GTA SA Toolkit] preview text:", e)


# ----------------------------------------------------------------------------- operator
def _view_of(context):
    """The 3D view the Preview button was clicked in (its sidebar), as an areapick.RegionView."""
    from . import areapick
    area = context.area
    if area is None or area.type != 'VIEW_3D':
        return None
    region = areapick._window_region(area)
    rv3d = area.spaces.active.region_3d
    return areapick.RegionView(region, rv3d) if region is not None and rv3d is not None else None

class GTATK_OT_map_preview(bpy.types.Operator):
    bl_idname = "gtatk.map_preview"
    bl_label = "Preview"
    bl_description = ("Show in the 3D view what Import Map would place (area outline + a dot per object), "
                      "with object and model counts and an estimated time. Loads nothing. Click again to hide")

    @classmethod
    def description(cls, context, properties):
        d = _PV["data"]
        return d.text if d is not None else cls.bl_description

    def execute(self, context):
        if shown():
            clear()
            self.report({'INFO'}, "Preview hidden")
            return {'FINISHED'}
        t = time.perf_counter()
        try:
            d = build(context)
        except Exception as e:           # noqa
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        show(d, _view_of(context))
        self.report({'INFO'}, "%s (worked out in %.2f s)" % (d.text, time.perf_counter() - t))
        return {'FINISHED'}


classes = (GTATK_OT_map_preview,)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    clear()
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
