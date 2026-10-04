# GTA SA Toolkit - pick the map import area on the radar map, drawn over the 3D view.
# The view switches to top ortho framed on San Andreas; the radar map is a temporary GPU overlay
# (no image, object or other data in the .blend). The circle follows the mouse; Alt + wheel (or Alt +
# two-finger trackpad scroll) changes its radius, Shift+Alt for fine steps; dragging with the wheel
# button pressed moves the map. A click (confirmed on release, so it never reaches the scene as a
# selection) or Enter applies the area at the mouse; Esc / right-click cancels and leaves the old area. Areas already imported show as faint green
# circles. The draw handler is removed and the previous view restored on every exit, including errors.
import bpy
from bpy_extras import view3d_utils
from mathutils import Quaternion, Vector

from . import mapimport, overlay, radar

WHEEL_STEP = 1.10           # radius factor per Alt+wheel notch
FINE_STEP = 1.02            # ... with Shift+Alt
TRACKPAD_PX = 10.0          # two-finger trackpad travel that counts as one wheel notch
MIN_RADIUS, MAX_RADIUS = 1.0, 10000.0
HINT = "Alt+Scroll: size · Click: confirm · Esc: cancel · Scroll: zoom"
IMPORTED_COLOR = (0.45, 0.95, 0.55, 0.5)     # areas already imported into the scene
FIT = 0.92                  # the map fills this much of the shorter side of the view


class RegionView:
    """World <-> region-pixel conversions and view save/restore for one 3D view."""

    def __init__(self, region, rv3d):
        self.region, self.rv3d = region, rv3d
        self.ptr = region.as_pointer()

    def size(self):
        return self.region.width, self.region.height

    def inside(self, px):
        return 0 <= px[0] < self.region.width and 0 <= px[1] < self.region.height

    def to_px(self, x, y):
        v = view3d_utils.location_3d_to_region_2d(self.region, self.rv3d, Vector((x, y, 0.0)))
        return (v.x, v.y) if v is not None else None

    def to_world(self, px):
        o = view3d_utils.region_2d_to_origin_3d(self.region, self.rv3d, px)
        d = view3d_utils.region_2d_to_vector_3d(self.region, self.rv3d, px)
        if abs(d.z) < 1e-9:
            return None
        p = o + d * (-o.z / d.z)                     # where the view ray meets the ground (z = 0)
        return p.x, p.y

    def save(self):
        r = self.rv3d
        return r.view_perspective, r.view_rotation.copy(), r.view_location.copy(), r.view_distance

    def restore(self, saved):
        r = self.rv3d
        r.view_perspective, r.view_rotation, r.view_location, r.view_distance = saved
        r.update()

    def top_view(self):
        self.frame_top(-radar.HALF, -radar.HALF, radar.HALF, radar.HALF)

    def frame_top(self, x0, y0, x1, y1, z=0.0):
        """Top ortho view with the rectangle (x0, y0)-(x1, y1) filling FIT of the shorter side."""
        r = self.rv3d
        r.view_perspective = 'ORTHO'
        r.view_rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
        r.view_location = ((x0 + x1) / 2, (y0 + y1) / 2, z)
        r.view_distance = max(x1 - x0, y1 - y0, 10.0)
        r.update()
        a, b = self.to_px(x0, y0), self.to_px(x1, y1)
        if a and b:                                  # ortho scale is linear in view_distance
            span = max(abs(b[0] - a[0]), abs(b[1] - a[1]), 1.0)
            r.view_distance *= span / (min(self.size()) * FIT)
            r.update()

    def pan(self, a, b):
        """Move the view so the ground point under pixel a ends up under pixel b."""
        wa, wb = self.to_world(a), self.to_world(b)
        if wa is None or wb is None:
            return
        r = self.rv3d
        r.view_location = (r.view_location.x + wa[0] - wb[0], r.view_location.y + wa[1] - wb[1],
                           r.view_location.z)
        r.update()

    def redraw(self):
        self.region.tag_redraw()


class AreaPicker:
    """Picking state + drawing. The operator feeds it events; tests feed it a fake view."""

    def __init__(self, view, centre, radius, tex=None, imported=()):
        self.view = view
        self.tex = tex
        self.imported = list(imported)          # (x, y, radius) of the scene's map imports, drawn faint
        self.old = (tuple(centre), radius)
        self.centre, self.radius = tuple(centre), radius
        self.pressed = False                    # a left press inside the view, waiting for its release
        self.drag_px = None                     # last mouse pixel while the wheel button drags the map
        self.hover = None
        self.picked = False
        self.saved = None
        self.handle = None

    # ---------------------------------------------------------------- lifetime
    def start(self, draw=True):
        self.saved = self.view.save()
        self.view.top_view()
        if draw:
            self.handle = bpy.types.SpaceView3D.draw_handler_add(self.draw, (), 'WINDOW', 'POST_PIXEL')
        self.view.redraw()

    def cleanup(self):
        """Safe to call more than once and after errors: handler off, view back, texture freed."""
        h, self.handle = self.handle, None
        if h is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
            except (ValueError, RuntimeError):
                pass
        saved, self.saved = self.saved, None
        try:
            if saved is not None:
                self.view.restore(saved)
            self.view.redraw()
        except ReferenceError:               # the 3D view was closed meanwhile
            pass
        self.tex = None

    # ---------------------------------------------------------------- events
    def handle_event(self, etype, value, px, shift=False, ctrl=False, alt=False, delta=0.0):
        """Returns 'RUNNING', 'PASS' (let Blender zoom/pan), 'DONE' or 'CANCEL'.
        delta = vertical trackpad travel in pixels (TRACKPADPAN only)."""
        if etype in ('MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'):
            if self.drag_px is not None:
                self.view.pan(self.drag_px, px)
                self.drag_px = px
            self.move(px)
            return 'RUNNING'
        if etype == 'LEFTMOUSE':             # Alt+click too (Emulate 3 Button Mouse); press and release consumed
            if value == 'PRESS':
                self.pressed = self.view.inside(px)
            elif value == 'RELEASE' and self.pressed:
                self.pressed = False
                if self.confirm(px):
                    return 'DONE'
            return 'RUNNING'
        if etype in ('RET', 'NUMPAD_ENTER') and value == 'PRESS':
            if self.hover is not None:
                self.centre, self.picked = self.hover, True
            return 'DONE'                    # no mouse over the map yet: keep the old area
        if etype in ('ESC', 'RIGHTMOUSE') and value == 'PRESS':
            return 'CANCEL'
        if etype in ('WHEELUPMOUSE', 'WHEELDOWNMOUSE'):
            if alt:                          # not passed on: Blender's Alt+wheel changes the frame
                self.resize(1 if etype == 'WHEELUPMOUSE' else -1, shift)
                return 'RUNNING'
            return 'PASS'
        if etype == 'TRACKPADPAN' and alt:
            self.resize(delta / TRACKPAD_PX, shift)
            return 'RUNNING'
        if etype == 'TRACKPADZOOM':
            return 'PASS'
        if etype == 'MIDDLEMOUSE' and not (shift or ctrl):     # wheel button: drag the map (Blender would orbit)
            if value == 'PRESS':
                self.drag_px = px if self.view.inside(px) else None
            elif value == 'RELEASE':
                self.drag_px = None
            return 'RUNNING'
        if etype in ('MIDDLEMOUSE', 'TRACKPADPAN') and (shift or ctrl):
            return 'PASS'                    # Shift = pan, Ctrl = zoom; plain would orbit away from top
        return 'RUNNING'                     # everything else (orbit, shortcuts, undo) is blocked

    def move(self, px):
        self.hover = self.view.to_world(px)

    def resize(self, notches, fine=False):
        self.radius = min(max(self.radius * (FINE_STEP if fine else WHEEL_STEP) ** notches, MIN_RADIUS),
                          MAX_RADIUS)

    def confirm(self, px):
        w = self.view.to_world(px)
        if w is None:
            return False
        self.centre = self.hover = w
        self.picked = True
        return True

    def shown(self):
        """(centre, radius) the circle shows: it follows the mouse."""
        if self.hover is not None:
            return self.hover, self.radius
        return self.centre, self.radius

    def result(self):
        """(x, y, radius) to store, or None when nothing was picked."""
        if not self.picked:
            return None
        return self.centre[0], self.centre[1], self.radius

    def text_lines(self):
        (x, y), r = self.shown()
        return ["Centre  %d, %d     Radius  %d m" % (round(x), round(y), round(r)),
                HINT,
                "Wheel-button drag: move map · Shift+Alt+Scroll: fine size · Enter: confirm · Ctrl+Z: undo a confirmed area"]

    # ---------------------------------------------------------------- drawing
    def draw(self):
        try:
            if self.view.ptr is not None and bpy.context.region.as_pointer() != self.view.ptr:
                return                       # another 3D view
            self.draw_2d()
        except ReferenceError:
            self.cleanup()                   # our view is gone: stop drawing
        except Exception as e:               # noqa - never break Blender's drawing
            print("[GTA SA Toolkit] area picker draw:", e)

    def draw_2d(self):
        v = self.view
        H = radar.HALF
        size = v.size()
        overlay.begin()
        try:
            if self.tex is not None:
                quad = [v.to_px(-H, -H), v.to_px(H, -H), v.to_px(H, H), v.to_px(-H, H)]
                if all(quad):
                    overlay.draw_image(self.tex, quad)
            for ix, iy, ir in self.imported:         # what's already in the scene
                pts = [v.to_px(x, y) for x, y in overlay.circle_points(ix, iy, ir)]
                if all(pts):
                    overlay.draw_lines(pts, size, color=IMPORTED_COLOR, width=1.5, closed=True)
            (ox, oy), orad = self.old
            old = [v.to_px(x, y) for x, y in overlay.circle_points(ox, oy, orad)]
            if all(old):
                overlay.draw_lines(old, size, color=(1.0, 1.0, 1.0, 0.45), width=1.5, closed=True)
            (cx, cy), r = self.shown()
            pts = [v.to_px(x, y) for x, y in overlay.circle_points(cx, cy, r)]
            c = v.to_px(cx, cy)
            if all(pts) and c:
                overlay.draw_lines(pts, size, width=2.5, closed=True)
                overlay.draw_lines([(c[0] - 8, c[1]), (c[0] + 8, c[1])], size, width=2.0)
                overlay.draw_lines([(c[0], c[1] - 8), (c[0], c[1] + 8)], size, width=2.0)
            overlay.draw_text(self.text_lines(), 20 + overlay.left_inset(), 24, size=14)
        finally:
            overlay.end()


def apply_area(props, x, y, radius):
    props.area_center = 'COORDS'
    props.area_x, props.area_y, props.area_radius = x, y, radius
    if props.map_source == 'SECTIONS' and not props.limit_to_area:
        props.map_source = 'AREA'


def _window_region(area):
    for r in area.regions:
        if r.type == 'WINDOW':
            return r
    return None


class GTATK_OT_pick_area(bpy.types.Operator):
    bl_idname = "gtatk.pick_area"
    bl_label = "Pick Area on Map"
    bl_description = ("Show the San Andreas radar map in this 3D view: the circle follows the mouse, "
                      "Alt+Scroll sizes it, click to confirm")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from . import ui
        return context.area is not None and context.area.type == 'VIEW_3D' and ui._JOB["job"] is None

    def invoke(self, context, event):
        p = context.scene.gta_tk
        region = _window_region(context.area)
        rv3d = context.area.spaces.active.region_3d
        if region is None or rv3d is None:
            self.report({'ERROR'}, "Run this from a 3D view")
            return {'CANCELLED'}
        self.picker = None
        try:
            game = mapimport.get_game(p.game_root)
            arr, _how = radar.get_map(game)
            tex = overlay.texture_from_array(arr)
            centre = (p.area_x, p.area_y) if p.area_center == 'COORDS' else tuple(context.scene.cursor.location.xy)
            self.region = region
            self.picker = AreaPicker(RegionView(region, rv3d), centre, p.area_radius, tex,
                                     imported=mapimport.imported_areas(context.scene, p))
            self.picker.start()
        except Exception as e:           # noqa
            if self.picker is not None:
                self.picker.cleanup()
            self.report({'ERROR'}, "Could not show the map: %s" % e)
            return {'CANCELLED'}
        context.window_manager.modal_handler_add(self)
        _status(context, "Pick area: " + HINT + " · Wheel-button drag: move map · "
                         "Ctrl+Z undoes a confirmed area")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        result = {'RUNNING_MODAL'}
        try:
            px = (event.mouse_x - self.region.x, event.mouse_y - self.region.y)
            delta = 0.0
            if event.type == 'TRACKPADPAN':          # for trackpad events prev -> current is the scroll travel
                delta = event.mouse_y - event.mouse_prev_y
                if getattr(event, "is_direction_inverted", False):    # "natural" scrolling
                    delta = -delta
            r = self.picker.handle_event(event.type, event.value, px, event.shift, event.ctrl, event.alt, delta)
            if r == 'PASS':
                result = {'PASS_THROUGH'}
            elif r == 'DONE':
                picked = self.picker.result()
                if picked is not None:
                    apply_area(context.scene.gta_tk, *picked)
                    self.report({'INFO'}, "Area: centre %d, %d, radius %d m" % tuple(round(v) for v in picked))
                result = {'FINISHED'}
            elif r == 'CANCEL':
                result = {'CANCELLED'}
            if r == 'RUNNING':
                self.picker.view.redraw()
        except Exception as e:           # noqa
            print("[GTA SA Toolkit] area picker stopped: %r" % e)
            self.report({'ERROR'}, "Area picker stopped: %s" % e)
            result = {'CANCELLED'}
        finally:
            if result not in ({'RUNNING_MODAL'}, {'PASS_THROUGH'}):
                self._end(context)
        return result

    def cancel(self, context):           # Blender ended the operator (file load, window closed)
        self._end(context)

    def _end(self, context):
        if getattr(self, "picker", None) is not None:
            self.picker.cleanup()
            self.picker = None
        _status(context, None)
        for a in context.screen.areas if context.screen else ():
            if a.type == 'PROPERTIES' or a.type == 'VIEW_3D':
                a.tag_redraw()


def _status(context, text):
    ws = getattr(context, "workspace", None)
    if ws is not None:
        try:
            ws.status_text_set(text)
        except Exception:                 # noqa
            pass


classes = (GTATK_OT_pick_area,)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
