# GTA SA Studio - per-vehicle settings of the Vehicle tab (Object.gta_vehicle_studio, used on a
# vehicle's root object only). vehicle.setup() fills them in once per vehicle (import / Set Up
# Vehicle); the tab draws only these stored values. Update callbacks change just that vehicle.
import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, PointerProperty, StringProperty)

from . import vehicle

HEAD_RGB = (1.0, 0.95, 0.85)
TAIL_RGB = (0.9, 0.05, 0.03)


def _root(self):
    return self.id_data


def _upd_paint(self, context):
    vehicle.apply_paint(_root(self))


def _upd_lights(self, context):
    vehicle.apply_lights(_root(self), context.scene)


def _upd_paintjob(self, context):
    vehicle.apply_paintjob(_root(self))


def _upd_part(self, context):
    vehicle.apply_part(self.id_data, self)


def _upd_upgrade(self, context):
    vehicle.apply_upgrade(self.id_data, self)


def _palette_items(self, context):
    return vehicle.palette_items()


def _set_items(self, context):
    return vehicle.colour_set_items(self.id_data)


def _paintjob_items(self, context):
    return vehicle.paintjob_items(self.id_data)


def _upgrade_items(self, context):
    return vehicle.upgrade_items(self.id_data, self.kind)


def _custom(i):
    return BoolProperty(name="Custom", default=False, update=lambda self, ctx: vehicle.start_custom(self.id_data, i),
                        description="Pick any colour. It isn't in the game's palette, so the game (and SA-MP) "
                                    "can't show it")


def _custom_rgb(i):
    return FloatVectorProperty(name="Custom Colour %d" % (i + 1), subtype='COLOR_GAMMA', size=3, min=0.0, max=1.0,
                               default=(0.5, 0.5, 0.5), update=_upd_paint)


def _pick(i):
    return EnumProperty(name="Colour %d" % (i + 1), items=_palette_items, update=_upd_paint,
                        description="Colour from the game's carcols.dat palette")


class GTASTUDIO_VehPart(bpy.types.PropertyGroup):
    """A door / bonnet / boot (open slider) or an extra (on/off)."""
    kind: StringProperty()             # DOOR, BONNET, BOOT, EXTRA
    label: StringProperty()
    obj: PointerProperty(type=bpy.types.Object)     # the hinge dummy (or the extra itself)
    axis: IntProperty(default=2)       # 0 X, 1 Y, 2 Z in the dummy's own space
    max_angle: FloatProperty(default=1.2)            # radians, signed: the way it opens
    open: FloatProperty(name="Open", default=0.0, min=0.0, max=1.0, subtype='FACTOR', update=_upd_part)
    shown: BoolProperty(name="Show", default=True, update=_upd_part)


class GTASTUDIO_VehUpgrade(bpy.types.PropertyGroup):
    kind: StringProperty()             # api.UPGRADE_KINDS key
    label: StringProperty()
    choice: EnumProperty(name="Part", items=_upgrade_items, update=_upd_upgrade)


class GTASTUDIO_VehicleProps(bpy.types.PropertyGroup):
    ready: BoolProperty(default=False, options={'HIDDEN'})        # setup() ran on this vehicle
    model: StringProperty(options={'HIDDEN'})
    owner: StringProperty(options={'HIDDEN'})      # root name at setup: a Shift+D copy needs its own setup
    # paint: which slots the model uses (bits 0-3), palette picks, custom colours
    slots: IntProperty(default=0b11, options={'HIDDEN'})
    col1: _pick(0)
    col2: _pick(1)
    col3: _pick(2)
    col4: _pick(3)
    custom1: _custom(0)
    custom2: _custom(1)
    custom3: _custom(2)
    custom4: _custom(3)
    custom_rgb1: _custom_rgb(0)
    custom_rgb2: _custom_rgb(1)
    custom_rgb3: _custom_rgb(2)
    custom_rgb4: _custom_rgb(3)
    colour_set: EnumProperty(name="Colour Set", items=_set_items, update=vehicle.upd_colour_set,
                             description="One of this model's colour combinations from carcols.dat")
    # lights
    own_lights: BoolProperty(name="Own Light Settings", default=False, update=_upd_lights,
                             description="This vehicle's lights follow the settings below instead of the "
                                         "Scene tab's Vehicle Lights group")
    follow_time: BoolProperty(name="Follow Time of Day", default=True, update=_upd_lights,
                              description="Lights fade on at dusk and off at dawn with the Scene tab's hour")
    head_on: BoolProperty(name="Headlights", default=True, update=_upd_lights)
    head_strength: FloatProperty(name="Strength", default=1.0, min=0.0, soft_max=10.0, update=_upd_lights)
    head_tint: FloatVectorProperty(name="Tint", subtype='COLOR', size=3, min=0.0, max=1.0, default=HEAD_RGB,
                                   update=_upd_lights)
    tail_on: BoolProperty(name="Taillights", default=True, update=_upd_lights)
    tail_strength: FloatProperty(name="Strength", default=1.0, min=0.0, soft_max=10.0, update=_upd_lights)
    tail_tint: FloatVectorProperty(name="Tint", subtype='COLOR', size=3, min=0.0, max=1.0, default=TAIL_RGB,
                                   update=_upd_lights)
    has_head: BoolProperty(options={'HIDDEN'})
    has_tail: BoolProperty(options={'HIDDEN'})
    # paintjobs
    paintjob_count: IntProperty(options={'HIDDEN'})
    paintjob: EnumProperty(name="Paintjob", items=_paintjob_items, update=_upd_paintjob)
    # doors, bonnet, boot, extras, upgrades
    parts: CollectionProperty(type=GTASTUDIO_VehPart)
    upgrades: CollectionProperty(type=GTASTUDIO_VehUpgrade)


classes = (GTASTUDIO_VehPart, GTASTUDIO_VehUpgrade, GTASTUDIO_VehicleProps)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Object.gta_vehicle_studio = PointerProperty(type=GTASTUDIO_VehicleProps)


def unregister():
    del bpy.types.Object.gta_vehicle_studio
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
