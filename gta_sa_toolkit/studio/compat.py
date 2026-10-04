# GTA SA Studio - small helpers for API differences between the Blender versions the add-on supports
# (4.4 and newer).


def ensure_channelbag(action, slot):
    """The action's channelbag for slot (made if missing). bpy_extras.anim_utils has a helper for this
    from Blender 4.5; 4.4 gets it through the action's first layer and keyframe strip."""
    from bpy_extras import anim_utils
    fn = getattr(anim_utils, "action_ensure_channelbag_for_slot", None)
    if fn is not None:
        return fn(action, slot)
    layer = action.layers[0] if len(action.layers) else action.layers.new("Layer")
    strip = layer.strips[0] if len(layer.strips) else layer.strips.new(type='KEYFRAME')
    return strip.channelbag(slot, ensure=True)


def largest_shadow_pool(eevee, wanted='2048'):
    """The wanted EEVEE shadow pool size, or the largest this Blender offers (4.4 stops at 1024 MB)."""
    items = [i.identifier for i in eevee.bl_rna.properties["shadow_pool_size"].enum_items]
    return wanted if wanted in items else max(items, key=int)


def new_fcurve(channelbag, data_path, index=0, group=None):
    """channelbag.fcurves.new() in a group: Blender 4.5+ takes group_name; 4.4 doesn't, so the group is
    made / found and set afterwards."""
    if not group:
        return channelbag.fcurves.new(data_path, index=index)
    try:
        return channelbag.fcurves.new(data_path, index=index, group_name=group)
    except TypeError:                                   # Blender 4.4
        fc = channelbag.fcurves.new(data_path, index=index)
        try:
            fc.group = channelbag.groups.get(group) or channelbag.groups.new(group)
        except (AttributeError, RuntimeError):          # grouping only tidies the Graph Editor
            pass
        return fc
