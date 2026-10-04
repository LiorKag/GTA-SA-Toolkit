# GTA SA Toolkit - apply IFP animations to armatures and export actions back to IFP.
#
# Works with armatures the add-on builds (rwbuild.py, and DragonFF in older files: bones carry a
# 'bone_id' custom property and their rest matrices match the RenderWare frame matrices) and, by name, with any
# other armature whose bones use GTA frame names.
#
# Maths: an IFP key is the frame's local transform relative to its parent frame:
#     L = T(pos) @ R(rot)
# Blender pose bones store  matrix_basis  such that
#     local_pose = rest_rel @ matrix_basis     (rest_rel = parent.matrix_local^-1 @ bone.matrix_local)
# so  matrix_basis = rest_rel^-1 @ L   and on export   L = rest_rel @ matrix_basis.

import re

import bpy
from mathutils import Matrix, Quaternion, Vector

from .formats import ifp as ifp_fmt

_suffix = re.compile(r"\.\d{3}$")


def norm_name(n):
    return _suffix.sub("", n).strip().lower()


# ----------------------------------------------------------------------------- lookup
def build_bone_lookup(arm_obj):
    by_id, by_name = {}, {}
    for b in arm_obj.data.bones:
        bid = b.get("bone_id")
        if bid is not None:
            by_id.setdefault(int(bid), b)
        by_name.setdefault(norm_name(b.name), b)
        if b.get("ifp_name"):
            by_name.setdefault(norm_name(b["ifp_name"]), b)
    return by_id, by_name


def find_bone(lookup, name, bone_id):
    by_id, by_name = lookup
    b = None
    if bone_id is not None and bone_id >= 0:
        b = by_id.get(bone_id)
    if b is None:
        b = by_name.get(norm_name(name))
    return b


def rest_rel(bone):
    if bone.parent:
        return bone.parent.matrix_local.inverted_safe() @ bone.matrix_local
    return bone.matrix_local.copy()


def root_bone_of(arm_obj):
    for b in arm_obj.data.bones:
        if b.get("bone_id") == 0:
            return b
    roots = [b for b in arm_obj.data.bones if b.parent is None]
    return roots[0] if roots else None


# ----------------------------------------------------------------------------- fcurves
def _fcurve(action, arm_obj, path, index, group):
    """The F-Curve for path[index] on arm_obj's slot, in the bone's group. Blender 4.5+ takes the group
    name directly; 4.4 doesn't, so the curve is put in the group through the slot's channelbag."""
    try:
        return action.fcurve_ensure_for_datablock(arm_obj, path, index=index, group_name=group)
    except TypeError:                                   # Blender 4.4: no group_name
        fc = action.fcurve_ensure_for_datablock(arm_obj, path, index=index)
        if group and fc.group is None:
            try:
                from bpy_extras import anim_utils
                cb = anim_utils.action_get_channelbag_for_slot(action, arm_obj.animation_data.action_slot)
                grp = cb.groups.get(group) or cb.groups.new(group)
                fc.group = grp
            except Exception:                           # noqa - grouping is only for the Graph Editor's tidiness
                pass
        return fc


def _write_curve(fc, frames, values, frame_min, frame_max):
    # drop existing keys inside the new range, then append and let Blender sort
    kps = fc.keyframe_points
    for i in range(len(kps) - 1, -1, -1):
        if frame_min - 1e-4 <= kps[i].co[0] <= frame_max + 1e-4:
            kps.remove(kps[i], fast=True)
    start = len(kps)
    n = len(frames)
    kps.add(n)
    total = start + n
    co = [0.0] * (total * 2)
    if start:
        kps.foreach_get("co", co)
    for i, (f, v) in enumerate(zip(frames, values)):
        co[(start + i) * 2] = f
        co[(start + i) * 2 + 1] = v
    kps.foreach_set("co", co)
    try:
        interp = [0] * total
        kps.foreach_get("interpolation", interp)
        lin = bpy.types.Keyframe.bl_rna.properties["interpolation"].enum_items["LINEAR"].value
        for i in range(start, total):
            interp[i] = lin
        kps.foreach_set("interpolation", interp)
    except Exception:        # noqa - very old API fallback
        for i in range(start, total):
            kps[i].interpolation = 'LINEAR'
    fc.update()


# ----------------------------------------------------------------------------- import
class ApplyOptions:
    fps = 30.0
    start_frame = 1.0
    snap = False
    root_mode = 'BONE'        # 'BONE' | 'OBJECT' | 'INPLACE' | 'NONE'
    new_action = True
    clear_pose = True
    skip_child_pos = True     # ignore translation keys on non-root bones (like the game)


def apply_animation(arm_obj, anim, opt, action_name=None):
    """Bake an ifp_fmt.Animation onto arm_obj. Returns (action, missing_bone_names)."""
    if arm_obj.animation_data is None:
        arm_obj.animation_data_create()
    ad = arm_obj.animation_data

    if opt.new_action or ad.action is None:
        act = bpy.data.actions.new(action_name or anim.name)
        act.use_fake_user = True
        ad.action = act
    act = ad.action
    act["ifp_anim_name"] = anim.name

    if opt.clear_pose:
        for pb in arm_obj.pose.bones:
            pb.location = (0, 0, 0)
            pb.rotation_quaternion = (1, 0, 0, 0)
            pb.scale = (1, 1, 1)

    lookup = build_bone_lookup(arm_obj)
    root = root_bone_of(arm_obj)
    missing = []

    def to_frame(t):
        f = opt.start_frame + t * opt.fps
        return float(round(f)) if opt.snap else f

    obj_frames, obj_locs = [], []

    for banim in anim.bones:
        if not banim.keyframes:
            continue
        bone = find_bone(lookup, banim.name, banim.bone_id)
        if bone is None:
            missing.append(banim.name.strip())
            continue
        bone["ifp_name"] = banim.name          # remembered for export
        pb = arm_obj.pose.bones[bone.name]
        pb.rotation_mode = 'QUATERNION'

        rr = rest_rel(bone)
        rr_inv = rr.inverted_safe()
        rest_t = rr.to_translation()
        is_root = bone == root
        use_pos = banim.has_pos and (is_root or not opt.skip_child_pos)

        frames, rots, locs = [], [], []
        prev = None
        p0 = None
        for k in banim.keyframes:
            q = Quaternion(k.rot)
            t = Vector(k.pos) if (use_pos and k.pos is not None) else rest_t.copy()
            if is_root and use_pos:
                if p0 is None:
                    p0 = t.copy()
                if opt.root_mode in ('INPLACE', 'OBJECT'):
                    delta = t - p0
                    # remove horizontal travel in armature space (Z stays)
                    delta_arm = (bone.parent.matrix_local.to_3x3() if bone.parent else Matrix.Identity(3)) @ delta
                    t = t - ((bone.parent.matrix_local.to_3x3().inverted_safe() if bone.parent else Matrix.Identity(3))
                             @ Vector((delta_arm.x, delta_arm.y, 0.0)))
                    if opt.root_mode == 'OBJECT':
                        obj_frames.append(to_frame(k.time))
                        obj_locs.append(Vector((delta_arm.x, delta_arm.y, 0.0)))
                elif opt.root_mode == 'NONE':
                    t = p0.copy()
            basis = rr_inv @ (Matrix.Translation(t) @ q.to_matrix().to_4x4())
            bq = basis.to_quaternion()
            if prev is not None and prev.dot(bq) < 0:
                bq.negate()
            prev = bq
            frames.append(to_frame(k.time))
            rots.append(bq)
            locs.append(basis.to_translation())

        fmin, fmax = min(frames), max(frames)
        base = 'pose.bones["%s"].' % bpy.utils.escape_identifier(bone.name)
        for i in range(4):
            _write_curve(_fcurve(act, arm_obj, base + "rotation_quaternion", i, bone.name),
                         frames, [r[i] for r in rots], fmin, fmax)
        if use_pos or any(l.length > 1e-5 for l in locs):
            for i in range(3):
                _write_curve(_fcurve(act, arm_obj, base + "location", i, bone.name),
                             frames, [l[i] for l in locs], fmin, fmax)

    if obj_frames:
        base_loc = arm_obj.location.copy()
        if "gta_rest_loc" not in arm_obj:
            arm_obj["gta_rest_loc"] = list(base_loc)     # so "Unlink Action" can put it back
        rot = arm_obj.matrix_world.to_3x3().normalized()
        vals = [base_loc + rot @ d for d in obj_locs]
        fmin, fmax = min(obj_frames), max(obj_frames)
        for i in range(3):
            _write_curve(_fcurve(act, arm_obj, "location", i, "Object Transforms"),
                         obj_frames, [v[i] for v in vals], fmin, fmax)
    return act, missing


# ----------------------------------------------------------------------------- export
def max_translation(anim):
    """Largest absolute translation component in an animation (ANP3 stores +/-32 m)."""
    m = 0.0
    for b in anim.bones:
        for k in b.keyframes:
            if k.pos is not None:
                m = max(m, max(abs(c) for c in k.pos))
    return m


ANP3_MAX_MOVE = 32767 / ifp_fmt.ANP3_POS_SCALE - 0.01      # just under 32 m (int16 / 1024)


def choose_format(anims, requested='AUTO'):
    """Pick the IFP format for these animations. ANP3 clips translations beyond +/-32 m, so a
    request for ANP3 (or AUTO) becomes ANPK when a move is longer than that.
    Returns (format, largest_move_m, switched) - switched is True when ANP3 was asked for but
    ANPK is needed."""
    far = max((max_translation(a) for a in anims), default=0.0)
    if requested == 'ANPK':
        return 'ANPK', far, False
    if far > ANP3_MAX_MOVE:
        return 'ANPK', far, requested == 'ANP3'
    return 'ANP3', far, False


def export_ifp(anims, path, fmt='AUTO', mode='NEW', base=None, ifp_name="custom"):
    """Write animations to an IFP.
    mode 'NEW': a new file in fmt ('AUTO' | 'ANP3' | 'ANPK').
    mode 'REPLACE' / 'APPEND': load base, replace same-named animations (REPLACE) or add them,
    keep the base file's format; writing over base makes base + '.bak' first.
    Either way ANP3 becomes ANPK when a move exceeds ANP3's +/-32 m.
    Returns a dict: path, format, switched, move (largest translation, m), notes (list of str)."""
    import os
    import shutil
    notes = []
    if mode == 'NEW':
        fmt_used, far, switched = choose_format(anims, fmt)
        f = ifp_fmt.IfpFile(fmt_used, ifp_name or "custom", list(anims))
    else:
        if not base or not os.path.isfile(base):
            raise RuntimeError("Base IFP not found")
        try:
            f = ifp_fmt.IfpFile.load(base)
        except Exception as e:           # noqa
            raise RuntimeError("Could not read the base IFP: %s" % e)
        for anim in anims:
            old = f.find(anim.name)
            if mode == 'REPLACE' and old is not None:
                f.animations[f.animations.index(old)] = anim
            else:
                if mode == 'REPLACE':
                    notes.append("'%s' not in base IFP - appended instead" % anim.name)
                f.animations.append(anim)
        # the base file's own animations already fit its format; only the new ones can need ANPK
        fmt_used, far, switched = choose_format(anims, f.version if f.version in ('ANP3', 'ANPK') else 'ANP3')
        f.version = fmt_used
        if os.path.abspath(base) == os.path.abspath(path):
            shutil.copy2(base, base + ".bak")
    if switched:
        notes.append("Root moves %.1f m, beyond ANP3's +/-32 m: saved as ANPK instead%s" % (
            far, "" if mode == 'NEW' else " (the whole file; the game reads both)"))
    try:
        f.save(path)
    except Exception as e:               # noqa
        raise RuntimeError("Write failed: %s" % e)
    return {"path": path, "format": fmt_used, "switched": switched, "move": far, "notes": notes}


def _action_fcurves(act, arm_obj):
    try:
        from bpy_extras import anim_utils
        slot = arm_obj.animation_data.action_slot if arm_obj.animation_data else None
        cb = anim_utils.action_get_channelbag_for_slot(act, slot) if slot else None
        if cb:
            return list(cb.fcurves)
    except Exception:               # noqa
        pass
    return list(act.fcurves) if hasattr(act, "fcurves") else []


def action_moves_object(act, arm_obj):
    return any(fc.data_path == "location" for fc in _action_fcurves(act, arm_obj))


def action_bone_names(act, arm_obj):
    """Bones that have fcurves in the action (for the armature's slot)."""
    names = set()
    fcurves = _action_fcurves(act, arm_obj)
    loc_bones = set()
    for fc in fcurves:
        m = re.match(r'pose\.bones\["(.+?)"\]\.(\w+)', fc.data_path)
        if m:
            names.add(m.group(1))
            if m.group(2) == "location":
                loc_bones.add(m.group(1))
    return names, loc_bones


def bake_action_to_anim(context, arm_obj, act, anim_name, fps, frame_start, frame_end,
                        step=1, only_keyed_bones=True):
    scene = context.scene
    ad = arm_obj.animation_data
    old_action = ad.action if ad else None
    old_frame = scene.frame_current
    if ad is None:
        ad = arm_obj.animation_data_create()
    ad.action = act

    names, loc_bones = action_bone_names(act, arm_obj)
    bones = [b for b in arm_obj.data.bones if (b.name in names or not only_keyed_bones)]
    if not bones:
        bones = [b for b in arm_obj.data.bones if b.get("bone_id") is not None]
    root = root_bone_of(arm_obj)
    # keep hierarchy order (parents first) - the game does not care, but it is tidy
    order = {b.name: i for i, b in enumerate(arm_obj.data.bones)}
    bones.sort(key=lambda b: order[b.name])

    anim = ifp_fmt.Animation(anim_name)
    tracks = {}
    for b in bones:
        ba = ifp_fmt.BoneAnim(b.get("ifp_name") or b.name, int(b.get("bone_id", -1)))
        tracks[b.name] = (b, rest_rel(b), ba, b == root or b.name in loc_bones)
        anim.bones.append(ba)

    frames = list(range(int(frame_start), int(frame_end) + 1, max(1, int(step))))
    if frames[-1] != int(frame_end):
        frames.append(int(frame_end))
    # root motion that was moved onto the armature OBJECT ("Armature Object" mode) goes back
    # into the root bone, otherwise the exported animation would walk on the spot
    obj_motion = root is not None and root.name in tracks and action_moves_object(act, arm_obj)
    loc0 = None
    prev = {}
    for f in frames:
        scene.frame_set(f)
        t = (f - frame_start) / fps
        extra = None
        if obj_motion:
            loc = arm_obj.matrix_world.to_translation()
            if loc0 is None:
                loc0 = loc.copy()
            delta = arm_obj.matrix_world.to_3x3().normalized().inverted_safe() @ (loc - loc0)
            if root.parent:
                delta = root.parent.matrix_local.to_3x3().inverted_safe() @ delta
            extra = delta
        for name, (b, rr, ba, has_pos) in tracks.items():
            pb = arm_obj.pose.bones[name]
            local = rr @ pb.matrix_basis
            q = local.to_quaternion().normalized()
            p = prev.get(name)
            if p is not None and p.dot(q) < 0:
                q.negate()
            prev[name] = q
            key = ifp_fmt.Keyframe(t, (q.w, q.x, q.y, q.z))
            if has_pos:
                tr = local.to_translation()
                if extra is not None and b == root:
                    tr = tr + extra
                key.pos = tuple(tr)
            ba.keyframes.append(key)

    ad.action = old_action
    scene.frame_set(old_frame)
    return anim
