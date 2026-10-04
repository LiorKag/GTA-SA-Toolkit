# GTA SA Studio - the Animate tab's engine: the animation combiner.
#
# A ped (armature) holds a list of clips (Object.gta_anim_studio.clips) in playback order. The rows
# are the source of truth; sync(arm) turns them into Blender NLA data:
#   - every clip's IFP animation is put on the ped once through core (api.ifp_apply) as a "source"
#     action, shared by rows using the same clip;
#   - every row gets its own action: the trimmed part of the source x repeats, starting at frame 1,
#     with its root bone moved and turned so it carries on from the clip before it (root matching);
#   - every row gets its own NLA track (later rows higher up) holding one strip: speed = strip scale,
#     placement = strip start, blend = blend in/out or an eased influence curve.
# A row's action is only rebuilt when something that changes it changed (built_sig), and a strip only
# when its settings changed (strip_sig), so dragging a slider stays cheap.
#
# Root matching (horizontal only, height stays the clip's own), for a row starting at frame F:
#   target = where the previous clip's root is at F (clamped to its end)
#   turn   = the previous clip's own turn by the end of our blend in (its end, for clips placed after it)
#            + the turn it was given. Only whole turns carry over: sway cancels out (a walk cycle ends
#            facing where it started), so a walk -> run join doesn't veer off.
#   M      = Move(target) . Turn(turn) . Move(-this clip's root at its trim start)
# Each repeat carries on from the end of the one before the same way.
import math
import os

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)
from mathutils import Matrix, Quaternion, Vector

from . import corelink
from .compat import ensure_channelbag, new_fcurve

_BUSY = {"on": False}          # set while sync writes computed values back (no nested syncs)


def _api():
    return corelink.api()


def fps_of(scene):
    return scene.render.fps / (scene.render.fps_base or 1.0)


def ped_of(ob):
    """The armature an object belongs to (itself or a parent), or None."""
    while ob is not None:
        if ob.type == 'ARMATURE':
            return ob
        ob = ob.parent
    return None


def ped_name(arm):
    """Name for the baked action: the ped's collection (the model name), else the armature's parent / name."""
    name = arm.parent.name if arm.parent is not None else arm.name
    for c in arm.users_collection:
        if c.name not in ("Scene Collection",) and not c.name.startswith("GTA "):
            name = c.name
            break
    base, ext = os.path.splitext(name)
    return base if ext.lower() == ".dff" else name


def root_bone(arm):
    for b in arm.data.bones:
        if b.get("bone_id") == 0:
            return b
    roots = [b for b in arm.data.bones if b.parent is None]
    return roots[0] if roots else None


# ----------------------------------------------------------------------------- properties
def _upd(self, context):
    if _BUSY["on"]:
        return
    try:
        sync(self.id_data)
    except Exception as e:        # noqa - a slider must never raise into the UI
        import traceback
        traceback.print_exc()
        print("[GTA SA Toolkit] animate:", e)


PLACES = (('AFTER', "After Previous", "Starts where the clip before it ends (overlapping by the blend length)"),
          ('AT', "At Frame", "Starts at a chosen frame"),
          ('REPLACE', "Replace Range", "Plays over a range of the clips underneath, then they carry on. "
                                       "Keeps its speed; cut at the end of the range"))
CURVES = (('LINEAR', "Linear", "Blend at an even pace"),
          ('SMOOTH', "Smooth", "Blend slowly at the ends, faster in the middle"))


class GTASTUDIO_AnimClip(bpy.types.PropertyGroup):
    uid: IntProperty()
    ifp_path: StringProperty(subtype='FILE_PATH')
    anim_name: StringProperty(name="Animation")
    source: PointerProperty(type=bpy.types.Action, description="The whole clip, as imported")
    action: PointerProperty(type=bpy.types.Action, description="This row's own action (trim x repeats, root moved)")
    length: FloatProperty(description="Frames in the whole clip")

    trim_start: FloatProperty(name="Start", min=0.0, step=100, precision=1, update=_upd,
                              description="First frame of the clip to use (its start pose)")
    trim_end: FloatProperty(name="End", min=0.0, step=100, precision=1, update=_upd,
                            description="Last frame of the clip to use (its end pose)")
    place: EnumProperty(name="Place", items=PLACES, default='AFTER', update=_upd)
    at_frame: IntProperty(name="Frame", default=1, update=_upd, description="Frame where the clip starts")
    range_end: IntProperty(name="Until", default=60, update=_upd,
                           description="Last frame of the range it replaces (the clip is cut there)")
    speed: FloatProperty(name="Speed", default=1.0, min=0.05, max=20.0, soft_min=0.1, soft_max=4.0, step=10,
                         update=_upd, description="Playback speed (2 = twice as fast)")
    repeats: IntProperty(name="Repeats", default=1, min=1, max=500, soft_max=50, update=_upd,
                         description="How many times the clip plays in a row")
    blend: IntProperty(name="Blend", default=6, min=0, max=200, soft_max=30, update=_upd,
                       description="Frames over which this clip fades in over the one before it")
    blend_curve: EnumProperty(name="Curve", items=CURVES, default='SMOOTH', update=_upd)
    match_root: BoolProperty(name="Match Root Motion", default=True, update=_upd,
                             description="Carry on from where the clip before it ends (position and facing). "
                                         "Off: the clip keeps its own path, like in the game")

    # computed by sync (shown, never typed)
    start: FloatProperty()
    end: FloatProperty()
    act_end: FloatProperty()          # last action frame the strip plays
    blend_used: IntProperty()         # blend length after clamping to the clips' lengths
    yaw0: FloatProperty()             # turn given to the first repeat (radians)
    built_sig: StringProperty()
    strip_sig: StringProperty()
    track: StringProperty()


class GTASTUDIO_AnimProps(bpy.types.PropertyGroup):
    clips: CollectionProperty(type=GTASTUDIO_AnimClip)
    active: IntProperty(name="Clip")
    next_uid: IntProperty(default=1)
    start_frame: IntProperty(name="Start Frame", default=1, update=_upd, description="Frame where the first clip starts")
    solo: BoolProperty(description="One clip is shown alone (Start/End Pose)")
    baked: PointerProperty(type=bpy.types.Action)
    is_baked: BoolProperty()
    export_name: StringProperty(name="Name", maxlen=23, description="Animation name inside the IFP (max 23 characters)")
    export_mode: EnumProperty(name="Export", items=(
        ('NEW', "New File", "Write a new IFP holding only this animation"),
        ('REPLACE', "Replace in File", "Put it into an existing IFP: replaces the animation with the same name "
                                       "(or adds it); the old file is kept as .bak")), default='NEW')
    export_path: StringProperty(name="File", subtype='FILE_PATH')
    export_format: EnumProperty(name="Format", items=(
        ('AUTO', "Auto", "ANP3 (San Andreas), or ANPK when the ped travels more than 32 m"),
        ('ANP3', "ANP3", "San Andreas format (switches to ANPK when the travel is too long for it)"),
        ('ANPK', "ANPK", "GTA III / Vice City format, also read by San Andreas")), default='AUTO')
    last_export: StringProperty()


class GTASTUDIO_AnimName(bpy.types.PropertyGroup):
    name: StringProperty()


class GTASTUDIO_AnimBrowser(bpy.types.PropertyGroup):
    # older versions had their own IFP browser; Add Clip now uses core's shared one
    # (api.draw_ifp_browser). Kept registered so older files open without warnings.
    ifp_path: StringProperty(name="IFP", subtype='FILE_PATH')
    names: CollectionProperty(type=GTASTUDIO_AnimName)
    index: IntProperty(name="Animation")
    note: StringProperty()


# ----------------------------------------------------------------------------- actions
def _slot(act):
    return act.slots[0] if len(act.slots) else None


def _fcurves(act):
    from bpy_extras import anim_utils
    s = _slot(act)
    cb = anim_utils.action_get_channelbag_for_slot(act, s) if s is not None else None
    return list(cb.fcurves) if cb is not None else []


def _keys(fc):
    n = len(fc.keyframe_points)
    co = [0.0] * (2 * n)
    fc.keyframe_points.foreach_get("co", co)
    return co[0::2], co[1::2]


def _src_key(source):
    """A clip's IFP source as stored in its action: core's "ped.ifp" / "anim.img/<name>.ifp" as they
    are, file paths absolute (what older versions stored, so their actions are still found)."""
    if source == "ped.ifp" or source.startswith("anim.img/"):
        return source.lower()
    return bpy.path.abspath(source).lower()


def make_source(arm, ifp_path, anim_name, fps):
    """The whole clip as an action (shared by rows using the same clip). Returns (action, missing bones)."""
    for act in bpy.data.actions:
        if (act.get("gta_combo_src") == "%s|%s|%g" % (_src_key(ifp_path), anim_name, fps)
                and act.get("gta_combo_arm") == arm.name):
            return act, []
    a = _api()
    anim = a.ifp_file(ifp_path).find(anim_name)
    if anim is None:
        raise ValueError("%s is not in %s" % (anim_name, ifp_path))
    ad = arm.animation_data or arm.animation_data_create()
    keep, keep_slot = ad.action, (ad.action_slot if ad.action is not None else None)
    act, missing = a.ifp_apply(arm, anim, start_frame=1.0, fps=fps, root_mode='BONE', new_action=True,
                               action_name="GTA clip " + anim_name, clear_pose=False)
    ad.action = keep
    if keep is not None and keep_slot is not None:
        try:
            ad.action_slot = keep_slot
        except Exception:            # noqa
            pass
    act.use_fake_user = False
    act["gta_combo_src"] = "%s|%s|%g" % (_src_key(ifp_path), anim_name, fps)
    act["gta_combo_arm"] = arm.name
    return act, missing


def _root_paths(arm):
    rb = root_bone(arm)
    if rb is None:
        return None, None
    base = 'pose.bones["%s"].' % bpy.utils.escape_identifier(rb.name)
    return rb, (base + "location", base + "rotation_quaternion")


def _root_matrix(ml, loc, quat):
    """Root bone pose in armature space from its basis channels."""
    return ml @ (Matrix.Translation(Vector(loc)) @ Quaternion(quat).normalized().to_matrix().to_4x4())


def _yaw(m):
    q = m.to_quaternion()
    return 2.0 * math.atan2(q.z, q.w)


def _wrap(a):
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def _match(p, target, yaw):
    """Move(target) . Turn(yaw) . Move(-p), horizontal only."""
    return (Matrix.Translation((target.x, target.y, 0.0)) @ Matrix.Rotation(yaw, 4, 'Z')
            @ Matrix.Translation((-p.x, -p.y, 0.0)))


class _Root:
    """Evaluates an action's root bone channels at any frame."""

    def __init__(self, act, arm):
        self.ok = False
        rb, paths = _root_paths(arm)
        if rb is None:
            return
        self.ml = rb.matrix_local.copy()
        by = {(fc.data_path, fc.array_index): fc for fc in _fcurves(act)}
        self.loc = [by.get((paths[0], i)) for i in range(3)]
        self.rot = [by.get((paths[1], i)) for i in range(4)]
        self.ok = all(self.loc) and all(self.rot)

    def at(self, f):
        return _root_matrix(self.ml, [c.evaluate(f) for c in self.loc], [c.evaluate(f) for c in self.rot])


def build_action(arm, row, m0, yaw0):
    """(Re)write the row's own action: source frames [trim] x repeats from frame 1, root moved by m0
    for the first repeat (identity = as in the clip); later repeats carry on when match_root is on."""
    src = row.source
    s, e = 1.0 + row.trim_start, 1.0 + row.trim_end
    span = e - s
    reps = row.repeats
    act = row.action
    if act is None:
        act = bpy.data.actions.new("GTA %s %s" % (ped_name(arm), row.anim_name))
        act.use_fake_user = False
        row.action = act
    # start clean: drop the slot's curves, keep the action (strips point at it)
    if len(act.slots) == 0:
        slot = act.slots.new(id_type='OBJECT', name=arm.name)
    else:
        slot = act.slots[0]
    cb = ensure_channelbag(act, slot)
    for fc in list(cb.fcurves):
        cb.fcurves.remove(fc)

    rb, paths = _root_paths(arm)
    root_set = set(paths) if paths else set()
    src_curves = _fcurves(src)

    # root transforms per repeat
    mats = [m0] * reps
    if rb is not None and reps > 1:
        root = _Root(src, arm)
        if root.ok and row.match_root:
            a_s, a_e = root.at(s), root.at(e)
            p = a_s.to_translation()
            turn = _wrap(_yaw(a_e) - _yaw(a_s))
            yaw = yaw0
            mats = [m0]
            for _k in range(1, reps):
                target = (mats[-1] @ a_e).to_translation()
                yaw += turn
                mats.append(_match(p, target, yaw))

    root_frames = None
    if root_set:
        fr = set()
        for fc in src_curves:
            if fc.data_path in root_set:
                fr.update(f for f in _keys(fc)[0] if s + 1e-4 < f < e - 1e-4)
        root_frames = [s] + sorted(fr) + [e]

    def unroll(frames, values):
        """Repeat (frame, value) pairs; a repeat's end key is left out where the next one starts."""
        fs, vs = [], []
        for k in range(reps):
            for f, v in zip(frames, values[k] if isinstance(values, tuple) else values):
                if f >= e - 1e-4 and k < reps - 1:
                    continue
                fs.append(1.0 + (f - s) + k * span)
                vs.append(v)
        return fs, vs

    def write(path, index, group, frames, values):
        fc = new_fcurve(cb, path, index, group)
        n = len(frames)
        fc.keyframe_points.add(n)
        co = [0.0] * (2 * n)
        co[0::2] = frames
        co[1::2] = values
        fc.keyframe_points.foreach_set("co", co)
        lin = bpy.types.Keyframe.bl_rna.properties["interpolation"].enum_items["LINEAR"].value
        fc.keyframe_points.foreach_set("interpolation", [lin] * n)
        fc.update()

    for fc in src_curves:
        if fc.data_path in root_set:
            continue
        kf, _kv = _keys(fc)
        frames = [s] + [f for f in kf if s + 1e-4 < f < e - 1e-4] + [e]
        write(fc.data_path, fc.array_index, fc.group.name if fc.group else "",
              *unroll(frames, [fc.evaluate(f) for f in frames]))

    if root_set:
        root = _Root(src, arm)
        if root.ok:
            ml_inv = root.ml.inverted_safe()
            raw = [root.at(f) for f in root_frames]
            of, bases = unroll(root_frames, tuple([ml_inv @ (m @ a) for a in raw] for m in mats))
            locs, rots, prev = [], [], None
            for basis in bases:
                q = basis.to_quaternion()
                if prev is not None and prev.dot(q) < 0:
                    q.negate()
                prev = q
                locs.append(basis.to_translation())
                rots.append(q)
            for i in range(3):
                write(paths[0], i, rb.name, of, [v[i] for v in locs])
            for i in range(4):
                write(paths[1], i, rb.name, of, [q[i] for q in rots])
    act["gta_combo_row"] = row.uid
    return act


# ----------------------------------------------------------------------------- sync
def _sig(*vals):
    return "|".join(("%.4f" % v) if isinstance(v, float) else str(v) for v in vals)


def _track_name(i, row):
    return "GTA %d: %s" % (i + 1, row.anim_name)


def _chain_state(arm, prev, frame, turn_frame):
    """(target, turn): where the previous row's root is at timeline frame `frame` (our start), and the
    facing it has reached by `turn_frame` (the end of our blend in: a turn still finishing while we
    fade in counts)."""
    root = _Root(prev.action, arm)
    if not root.ok:
        return None

    def af(f):
        return min(max(1.0 + (f - prev.start) * prev.speed, 1.0), prev.act_end)
    m_t, m_1 = root.at(af(turn_frame)), root.at(1.0)
    return root.at(af(frame)).to_translation(), prev.yaw0 + _wrap(_yaw(m_t) - _yaw(m_1))


def sync(arm):
    """Bring the NLA in line with the clip rows (see the top of the file)."""
    if arm is None or arm.type != 'ARMATURE':
        return
    st = arm.gta_anim_studio
    ad = arm.animation_data or arm.animation_data_create()
    rows = list(st.clips)
    _BUSY["on"] = True
    try:
        if st.is_baked:
            st.is_baked = False
        if ad.action is not None:
            ad.action.use_fake_user = True        # whatever played before is kept, not lost
            ad.action = None
        seq_prev = None
        seq_rows = []
        for i, row in enumerate(rows):
            if row.source is None:
                continue
            row.length = max(1.0, row.source.frame_range[1] - 1.0)
            if row.trim_end > row.length or row.trim_end <= 0.0:
                row.trim_end = row.length
            if row.trim_start > row.trim_end - 1.0:
                row.trim_start = max(0.0, row.trim_end - 1.0)
                if row.trim_end - row.trim_start < 1.0:
                    row.trim_end = min(row.length, row.trim_start + 1.0)
            span = (row.trim_end - row.trim_start) * row.repeats
            dur = span / row.speed
            blend = 0
            if row.place == 'AFTER':
                if seq_prev is None:
                    start = float(st.start_frame)
                else:
                    blend = min(row.blend, int(seq_prev.end - seq_prev.start), int(dur))
                    start = seq_prev.end - blend
            else:
                start = float(row.at_frame)
                blend = min(row.blend, int(dur))
            end = start + dur
            act_end = 1.0 + span
            if row.place == 'REPLACE':
                stop = float(max(row.range_end, row.at_frame + 1))
                if end > stop:
                    end = stop
                    act_end = 1.0 + (stop - start) * row.speed
                blend = min(row.blend, int((end - start) / 2))
            row.start, row.end, row.act_end = start, end, act_end

            # root matching: where the clip underneath / before is at our start frame
            prev = seq_prev
            if row.place == 'REPLACE':
                under = [r for r in seq_rows if r.start <= start <= r.end]
                prev = under[-1] if under else seq_prev
            m0, yaw0 = Matrix.Identity(4), 0.0
            if row.match_root and prev is not None and prev.action is not None:
                state = _chain_state(arm, prev, start, start + blend)
                root = _Root(row.source, arm)
                if state is not None and root.ok:
                    target, yaw0 = state
                    m0 = _match(root.at(1.0 + row.trim_start).to_translation(), target, yaw0)
            row.yaw0 = yaw0
            sig = _sig(row.source.name, row.trim_start, row.trim_end, row.repeats, row.match_root,
                       *[round(m0[r][c], 4) for r in range(3) for c in range(4)])
            if sig != row.built_sig or row.action is None:
                build_action(arm, row, m0, yaw0)
                row.built_sig = sig
                row.strip_sig = ""
            row.blend_used = blend
            if row.place != 'REPLACE':
                seq_prev = row
                seq_rows.append(row)
        _sync_tracks(arm, ad, rows)
    finally:
        _BUSY["on"] = False


def set_solo(arm, row=None):
    """Show only this row's track (None = all tracks again)."""
    ad = arm.animation_data
    st = arm.gta_anim_studio
    if ad is None:
        return
    if row is not None:
        t = ad.nla_tracks.get(row.track)
        if t is not None:
            t.is_solo = True          # Blender un-solos the others (setting False anywhere clears solo)
    else:
        for t in ad.nla_tracks:
            if t.is_solo:
                t.is_solo = False
    st.solo = row is not None


def _sync_tracks(arm, ad, rows):
    if arm.gta_anim_studio.solo:
        set_solo(arm, None)
    names = [_track_name(i, r) for i, r in enumerate(rows)]
    ours = {r.track for r in rows if r.track}
    have = [t.name for t in ad.nla_tracks if t.name in ours or t.name in names]
    live = [n for n, r in zip(names, rows) if r.source is not None]
    if have != live or any(r.track != n for r, n in zip(rows, names) if r.source is not None):
        for t in list(ad.nla_tracks):
            if t.name in ours or t.name in names:
                ad.nla_tracks.remove(t)
        prev = None
        for n, r in zip(names, rows):
            if r.source is None:
                continue
            t = ad.nla_tracks.new(prev=prev) if prev is not None else ad.nla_tracks.new()
            t.name = n
            r.track = t.name
            r.strip_sig = ""
            prev = t
    first = next((r for r in rows if r.source is not None and r.place != 'REPLACE'), None)
    for r in rows:
        if r.source is None:
            continue
        t = ad.nla_tracks.get(r.track)
        t.mute = False
        blend = r.blend_used
        extrap = 'NOTHING' if r.place == 'REPLACE' else ('HOLD' if r == first else 'HOLD_FORWARD')
        sig = _sig(r.action.name, r.start, r.end, r.act_end, r.speed, blend, r.blend_curve, extrap, r.place)
        if sig == r.strip_sig and len(t.strips) == 1:
            continue
        for s in list(t.strips):
            t.strips.remove(s)
        s = t.strips.new(r.anim_name, int(r.start), r.action)
        if getattr(s, "action_slot", None) is None and len(r.action.slots):
            s.action_slot = r.action.slots[0]
        s.use_sync_length = False
        s.action_frame_start = 1.0
        s.action_frame_end = r.act_end
        s.scale = 1.0 / r.speed
        s.repeat = 1.0
        s.frame_start_ui = r.start
        s.extrapolation = extrap
        s.blend_type = 'REPLACE'
        s.use_auto_blend = False
        _set_blend(s, r, blend)
        r.strip_sig = sig


def _set_blend(s, r, blend):
    out = blend if r.place == 'REPLACE' else 0
    if r.blend_curve == 'LINEAR' or blend <= 0:
        s.use_animated_influence = False
        s.blend_in, s.blend_out = float(blend), float(out)
        return
    s.blend_in = s.blend_out = 0.0
    s.use_animated_influence = True
    fc = s.fcurves.find("influence")
    if fc is None:
        return
    while len(fc.keyframe_points):
        fc.keyframe_points.remove(fc.keyframe_points[0], fast=True)
    pts = [(s.frame_start, 0.0), (s.frame_start + blend, 1.0)]
    if out:
        pts += [(s.frame_end - out, 1.0), (s.frame_end, 0.0)]
    for f, v in pts:
        k = fc.keyframe_points.insert(f, v, options={'FAST'})
        k.interpolation = 'BEZIER'
        k.handle_left_type = k.handle_right_type = 'AUTO_CLAMPED'
    fc.update()


# ----------------------------------------------------------------------------- rows
def add_clip(arm, ifp_path, anim_name, fps):
    """Add a row at the end. Returns (row, missing bone names)."""
    st = arm.gta_anim_studio
    src, missing = make_source(arm, ifp_path, anim_name, fps)
    _BUSY["on"] = True
    try:
        row = st.clips.add()
        row.uid = st.next_uid
        st.next_uid += 1
        row.ifp_path = ifp_path
        row.anim_name = anim_name
        row.source = src
        row.length = max(1.0, src.frame_range[1] - 1.0)
        row.trim_start, row.trim_end = 0.0, row.length
        last = st.clips[len(st.clips) - 2] if len(st.clips) > 1 else None
        row.at_frame = int(last.end) if last is not None else st.start_frame
        row.range_end = row.at_frame + int(round(row.length))
        if not st.export_name:
            st.export_name = ped_name(arm)[:23]
        st.active = len(st.clips) - 1
    finally:
        _BUSY["on"] = False
    sync(arm)
    return row, missing


def _drop_row_data(arm, row, keep_sources):
    ad = arm.animation_data
    if ad is not None and row.track:
        t = ad.nla_tracks.get(row.track)
        if t is not None:
            ad.nla_tracks.remove(t)
    act, src = row.action, row.source
    row.action = None
    row.source = None
    if act is not None and act.users == 0:
        bpy.data.actions.remove(act)
    if src is not None and src not in keep_sources and src.users == 0:
        bpy.data.actions.remove(src)


def remove_clip(arm, index):
    st = arm.gta_anim_studio
    if not 0 <= index < len(st.clips):
        return
    row = st.clips[index]
    keep = {r.source for i, r in enumerate(st.clips) if i != index and r.source is not None}
    _drop_row_data(arm, row, keep)
    st.clips.remove(index)
    st.active = min(st.active, len(st.clips) - 1)
    sync(arm)


def move_clip(arm, index, delta):
    st = arm.gta_anim_studio
    j = index + delta
    if not (0 <= index < len(st.clips) and 0 <= j < len(st.clips)):
        return
    st.clips.move(index, j)
    st.active = j
    sync(arm)


def clear_clips(arm):
    st = arm.gta_anim_studio
    rows = list(st.clips)
    for r in rows:
        _drop_row_data(arm, r, set())
    st.clips.clear()
    st.active = 0


def combo_range(arm):
    """(first frame, last frame) of the combo, whole frames, or None."""
    rows = [r for r in arm.gta_anim_studio.clips if r.source is not None]
    if not rows:
        return None
    return int(math.floor(min(r.start for r in rows))), int(math.ceil(max(r.end for r in rows)))


# ----------------------------------------------------------------------------- bake / export
def bake(arm, scene):
    """Play the combination frame by frame into one action named after the ped, make it the ped's
    action and mute the clips' tracks (kept for editing). Returns (action, first frame, last frame)."""
    sync(arm)
    rng = combo_range(arm)
    if rng is None:
        raise ValueError("Add a clip first")
    f0, f1 = rng
    st = arm.gta_anim_studio
    ad = arm.animation_data
    channels = {}                         # (path, index) -> group
    for r in st.clips:
        if r.action is not None:
            for fc in _fcurves(r.action):
                if fc.data_path.startswith("pose.bones["):
                    channels.setdefault((fc.data_path, fc.array_index), fc.group.name if fc.group else "")
    bones = {}                            # bone name -> (has location, has rotation)
    for (path, _i) in channels:
        name = path[len('pose.bones["'):path.index('"]')]
        loc, rot = bones.get(name, (False, False))
        bones[name] = (loc or path.endswith(".location"), rot or path.endswith(".rotation_quaternion"))
    pbs = {n: arm.pose.bones.get(bpy.utils.unescape_identifier(n)) for n in bones}     # n is the escaped name
    frames = list(range(f0, f1 + 1))
    data = {n: ([], []) for n in bones}
    prev = {}
    keep = scene.frame_current, scene.frame_subframe
    for f in frames:
        scene.frame_set(f)
        for n, pb in pbs.items():
            if pb is None:
                continue
            q = pb.rotation_quaternion.copy()
            p = prev.get(n)
            if p is not None and p.dot(q) < 0:
                q.negate()
            prev[n] = q
            data[n][0].append(pb.location.copy())
            data[n][1].append(q)
    scene.frame_set(keep[0], subframe=keep[1])

    act = st.baked
    if act is None:
        act = bpy.data.actions.new(ped_name(arm))
        st.baked = act
    act.use_fake_user = True
    act["ifp_anim_name"] = st.export_name or ped_name(arm)
    slot = act.slots[0] if len(act.slots) else act.slots.new(id_type='OBJECT', name=arm.name)
    cb = ensure_channelbag(act, slot)
    for fc in list(cb.fcurves):
        cb.fcurves.remove(fc)
    fr = [float(f) for f in frames]
    lin = bpy.types.Keyframe.bl_rna.properties["interpolation"].enum_items["LINEAR"].value
    for n, (has_loc, has_rot) in bones.items():
        pb = pbs[n]
        if pb is None:
            continue
        base = 'pose.bones["%s"].' % n
        locs, rots = data[n]
        chans = ([("location", i, [v[i] for v in locs]) for i in range(3)] if has_loc else []) +                 ([("rotation_quaternion", i, [q[i] for q in rots]) for i in range(4)] if has_rot else [])
        for prop, i, vals in chans:
            fc = new_fcurve(cb, base + prop, i, pb.name)
            fc.keyframe_points.add(len(fr))
            co = [0.0] * (2 * len(fr))
            co[0::2], co[1::2] = fr, vals
            fc.keyframe_points.foreach_set("co", co)
            fc.keyframe_points.foreach_set("interpolation", [lin] * len(fr))
            fc.update()
    ad.action = act
    try:
        ad.action_slot = slot
    except Exception:                    # noqa
        pass
    ours = {r.track for r in st.clips if r.track}
    for t in ad.nla_tracks:
        if t.name in ours:
            t.mute = True
    st.is_baked = True
    return act, f0, f1


def unbake(arm):
    """Back to the clips: tracks play again, the baked action stays in the file."""
    sync(arm)


def export(arm, scene, path, mode='NEW'):
    """Bake if needed, then write through core's IFP export. Returns (path, format used, notes)."""
    a = _api()
    st = arm.gta_anim_studio
    if not st.is_baked or st.baked is None:
        bake(arm, scene)
    f0, f1 = combo_range(arm)
    name = (st.export_name or ped_name(arm))[:23]
    anim = a.ifp_bake(arm, action=st.baked, name=name, fps=fps_of(scene), frame_start=f0, frame_end=f1)
    if mode == 'NEW':
        out, fmt = a.ifp_export(anim, path, format=st.export_format, mode='NEW', ifp_name=name)
    else:
        out, fmt = a.ifp_export(anim, path, mode='REPLACE', base=path)
    notes = []
    if fmt == 'ANPK' and (mode != 'NEW' or st.export_format != 'ANPK'):
        notes.append("The ped travels more than 32 m: saved as ANPK (San Andreas reads it too)")
    return out, fmt, notes


classes = (GTASTUDIO_AnimClip, GTASTUDIO_AnimProps, GTASTUDIO_AnimName, GTASTUDIO_AnimBrowser)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Object.gta_anim_studio = PointerProperty(type=GTASTUDIO_AnimProps)
    bpy.types.Scene.gta_anim_browser = PointerProperty(type=GTASTUDIO_AnimBrowser)


def unregister():
    del bpy.types.Scene.gta_anim_browser
    del bpy.types.Object.gta_anim_studio
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
