# GTA SA Toolkit - builds Blender objects from our own DFF reader (formats/dff.py): meshes (numpy +
# foreach_set), materials (shared natively across a map import), frames as empties, 2DFX lights and
# road signs, armatures with skin weights (peds, animated props), embedded collision. Replaces
# DragonFF's importer (publishing plan steps 2-4).
#
# What it builds matches DragonFF's import (so existing files, the cache and Studio see the same
# things): one collection "<model>.dff"; per atomic a mesh named after its frame with every vertex of
# the geometry, faces cleaned like bmesh does (degenerate faces and repeats of a vertex set dropped; of
# two equal faces in a row the second is kept), UV layers "Float2", "Float2.001" (v flipped), prelit and
# night colours as corner byte colours "Col", "Col.001", custom normals when the file has normals,
# smooth shading; materials named like DragonFF's (frame.index, "primary", "glass"...) with a Principled
# BSDF (Base Color = RW colour, alpha in its 4th value, Specular IOR Level = RW specular, Roughness = RW
# diffuse) and an image node (label = texture name) feeding Base Color and Alpha; a frame holding one
# mesh is that mesh object, other frames are small cube empties; lights are point lights "2dfx_light".
# The other 2DFX helpers (particles, ped attractors, triggers...) are invisible markers: not built.
#
# Credit: the conventions copied here (material names and the vehicle colour table, the road sign text
# setup, which faces are kept) come from DragonFF by Parik and contributors (GPL-3.0-or-later,
# https://github.com/Parik27/DragonFF), the importer this add-on used in its earlier versions.
import math

import bpy
import numpy as np

from .formats import dff as rwdff

BUILD_VERSION = 1               # changes what's built -> part of the asset cache's identity
FONT = "DejaVu Sans Mono Book"

# material names DragonFF gives (DEF naming), kept so materials look the same as before
_PATTERNS = (("vehiclegeneric", "generic"), ("interior", "interior"), ("vehiclesteering", "steering"))
_COLOUR_NAMES = {
    (255, 60, 0, 255): "right rear light", (185, 255, 0, 255): "left rear light",
    (0, 255, 200, 255): "right front light", (255, 175, 0, 255): "left front light",
    (255, 0, 255, 255): "fourth", (0, 255, 255, 255): "third", (255, 0, 175, 255): "secondary",
    (60, 255, 0, 255): "primary", (184, 255, 0, 255): "breaklight l", (255, 59, 0, 255): "breaklight r",
    (255, 173, 0, 255): "revlight L", (0, 255, 198, 255): "revlight r", (255, 174, 0, 255): "foglight l",
    (0, 255, 199, 255): "foglight r", (183, 255, 0, 255): "indicator lf", (255, 58, 0, 255): "indicator rf",
    (182, 255, 0, 255): "indicator lm", (255, 57, 0, 255): "indicator rm", (181, 255, 0, 255): "indicator lr",
    (255, 56, 0, 255): "indicator rr", (0, 16, 255, 255): "light night", (0, 17, 255, 255): "light all-day",
    (0, 18, 255, 255): "default day"}


def can_build(d):
    """True for every model the reader could read (peds and animated props too)."""
    return True


def clean_name(name):
    """DragonFF's object name rule (a name with a dot gets '.001', so Blender's own numbering can't
    eat a part of it)."""
    return name + ".001" if "." in name else name


# --------------------------------------------------------------------------------------------- build

def build(d, name, images=None, opts=None, share=None, link_to=None):
    """Build Dff d as collection "<name>.dff". images: {texture name: [bpy image]} (case-insensitive
    lookup, may be empty); opts: use_mat_split / remove_doubles; share: {material key: material} kept
    across models (map imports) or None (shared within this model only). Returns (collection, recipes)
    where recipes = {mesh pointer: [material description, ...]} for the asset cache."""
    opts = opts or {}
    images = images if images is not None else {}
    share = {} if share is None else share
    coll = bpy.data.collections.new(name + ".dff")
    (link_to if link_to is not None else bpy.context.scene.collection).children.link(coll)
    recipes = {}
    meshes = {}                                         # frame index -> [mesh objects]
    skins = {}                                          # frame index -> Skin (first skinned atomic per frame)
    for a in d.atomics:
        frame = d.frames[a.frame] if a.frame < len(d.frames) else d.frames[0]
        geo = d.geometries[a.geometry]
        me, descs = _mesh(geo, frame, d, opts)
        for desc in descs:
            me.materials.append(material(desc, images, share))
        recipes[me.as_pointer()] = descs
        ob = bpy.data.objects.new("mesh", me)
        coll.objects.link(ob)
        ob.rotation_mode = 'QUATERNION'
        if geo.skin is not None:
            skins.setdefault(a.frame, geo.skin)
            _vertex_groups(ob, geo.skin)
        meshes.setdefault(a.frame, []).append(ob)
        if opts.get("remove_doubles"):
            _remove_doubles(ob)
    _frames(d, coll, meshes, skins, opts)
    for e in d.effects:
        ob = effect_object(e)
        if ob is not None:
            coll.objects.link(ob)
    if opts.get("collision", True):
        from .formats import col as rwcol
        for data in d.collisions:
            try:
                models = rwcol.load(data, embedded=True)
            except rwcol.RWError as e:
                print("[GTA SA Toolkit] %s.dff: collision skipped: %s" % (name, e))
                continue
            for c in build_collision(models, name + ".dff", link=False):
                coll.children.link(c)
                for ob in list(c.objects):
                    try:
                        ob.hide_set(True)
                    except RuntimeError:
                        pass
    return coll, recipes


def _frames(d, coll, meshes, skins, opts):
    """Objects for the frames, in file order (DragonFF's rules): the root of a skeleton (HAnim bone
    list) becomes an armature; other bone frames get no object of their own (their meshes hang on the
    bone); a frame with one mesh is that mesh object; others are small cube empties."""
    bone_ids = {b[0] for f in d.frames if f.hanim for b in f.hanim}
    bones = {}                                  # bone id -> frame index (the last frame with that id)
    for i, f in enumerate(d.frames):
        if f.bone_id is not None and f.bone_id in bone_ids:
            bones[f.bone_id] = i
    frame_bones = {}                            # frame index -> (armature object, bone name)
    objects = {}
    for i, f in enumerate(d.frames):
        ms = meshes.get(i, [])
        ob = None
        if f.hanim:
            ob = _armature(d, f, i, coll, bones, frame_bones, meshes, skins, opts)
            for m in ms:
                if not m.vertex_groups and i in frame_bones:
                    _parent_to_bone(m, *frame_bones[i])
        elif f.bone_id is not None and f.bone_id in bones:
            if i in frame_bones:
                for m in ms:
                    _parent_to_bone(m, *frame_bones[i])
            continue
        if ob is None:
            if len(ms) == 1:
                ob = ms[0]
                ob.name = clean_name(f.name)
            else:
                ob = bpy.data.objects.new(clean_name(f.name), None)
                ob.empty_display_type = 'CUBE'
                ob.empty_display_size = 0.05
                coll.objects.link(ob)
            ob.rotation_mode = 'QUATERNION'
            ob.matrix_basis = _frame_matrix(f)
        for m in ms:
            if m is not ob:
                m.parent = ob
        if f.parent != -1:
            if f.parent in frame_bones:
                _parent_to_bone(ob, *frame_bones[f.parent])
            elif f.parent in objects:
                ob.parent = objects[f.parent]
        objects[i] = ob


def _vertex_groups(ob, skin):
    """One vertex group per skin bone (renamed after its bone by _armature) and the 4 weights of every
    vertex added like DragonFF's vertex_groups[bone].add([v], w, 'ADD'): a zero weight still makes the
    vertex a member, a bone listed twice adds up."""
    for _ in range(skin.num_bones):
        ob.vertex_groups.new()
    if skin.indices is None or not skin.num_bones:
        return
    acc = {}
    for v in range(len(skin.indices)):
        for k in range(4):
            b = int(skin.indices[v, k])
            if b < skin.num_bones:
                acc[(b, v)] = acc.get((b, v), 0.0) + float(skin.weights[v, k])
    by = {}
    for (b, v), w in acc.items():
        by.setdefault((b, w), []).append(v)
    groups = ob.vertex_groups
    for (b, w), vs in by.items():
        groups[b].add(vs, w, 'REPLACE')


def _armature(d, frame, index, coll, bones, frame_bones, meshes, skins, opts):
    """The armature of a skeleton root frame: one bone per HAnim entry, named after its frame, 0.05 long,
    with bone_id / type properties. Skinned: placed by the inverse of the skin's bind matrix (roll aligned
    to its Z axis); not skinned: the frame chain. Skinned meshes get their vertex groups named after the
    bones and an Armature modifier."""
    from mathutils import Matrix, Vector
    name = clean_name(frame.name)
    arm = bpy.data.armatures.new(name)
    ob = bpy.data.objects.new(name, arm)
    coll.objects.link(ob)
    skin_frame = _skinned_frame(frame, index, skins)
    skin = skins[skin_frame] if skin_frame is not None else None
    skinned = [m for fi in skins for m in meshes.get(fi, [])]
    view = bpy.context.view_layer
    prev_active = view.objects.active
    view.objects.active = ob
    bpy.ops.object.mode_set(mode='EDIT', toggle=False)
    eb = arm.edit_bones
    bone_list = {}                                  # frame index -> [edit bone, connected yet]
    for k, (bid, bidx, btype) in enumerate(frame.hanim):
        fi = bones.get(bid)
        if fi is None:
            continue
        bf = d.frames[fi]
        for m in skinned:
            if k < len(m.vertex_groups):
                m.vertex_groups[k].name = bf.name
        e = eb.new(bf.name)
        e.tail = (0, 0.05, 0)
        e["bone_id"], e["type"] = bid, btype
        if skin is not None and 0 <= bidx < len(skin.inverse):
            mm = skin.inverse[bidx]
            mat = Matrix((mm[0:4], mm[4:8], mm[8:12], mm[12:16])).transposed()
            if abs(mat.determinant()) > 1e-8:
                mat.invert()
            else:
                mat.identity()
            e.transform(mat, scale=True, roll=False)
            e.roll = align_roll(e.vector, e.z_axis, mat.to_3x3() @ Vector((0, 0, 1)))
        else:
            e.matrix = _frame_matrix(bf)
        if bf.parent >= index and bf.parent in bone_list:
            e.parent = bone_list[bf.parent][0]
            if skin is None:
                e.matrix = e.parent.matrix @ e.matrix
            if opts.get("connect_bones") and not bone_list[bf.parent][1]:
                tri = Matrix([e.parent.head, e.parent.tail, e.head])
                if abs(tri.determinant()) < 0.0000001:
                    e.length = (e.parent.head - e.head).length
                    e.use_connect = True
                    bone_list[bf.parent][1] = True
        bone_list[fi] = [e, False]
        frame_bones[fi] = (ob, e.name)
    bpy.ops.object.mode_set(mode='OBJECT', toggle=False)
    if prev_active is not None:
        try:
            view.objects.active = prev_active
        except (ReferenceError, RuntimeError):
            pass
    for m in skinned:
        mod = m.modifiers.new("Armature", 'ARMATURE')
        mod.object = ob
    return ob


def _skinned_frame(frame, index, skins):
    """Which skinned frame drives this skeleton: the parent, the previous frame, the first, else any."""
    for fi in (frame.parent, index - 1, 0):
        if fi in skins:
            return fi
    return next(iter(skins), None)


def align_roll(vec, vecz, tarz):
    """The roll that turns a bone's Z axis (vecz) towards tarz around its own direction (vec)."""
    sine = vec.normalized().dot(vecz.normalized().cross(tarz.normalized()))
    if abs(sine) > 1:
        sine /= abs(sine)
    if vecz.dot(tarz) > 0:
        return math.asin(sine)
    if sine > 0:
        return -math.asin(sine) + math.pi
    return -math.asin(sine) - math.pi


def _parent_to_bone(ob, arm, bone_name):
    """Hang ob on a bone: skinned meshes (an Armature modifier on this armature) keep parent type OBJECT
    and take the pose bone's matrix; anything else is parented to the bone itself."""
    from mathutils import Matrix
    bone = arm.data.bones.get(bone_name)
    if bone is None:
        return
    ob.parent = arm
    ob.parent_bone = bone_name
    if ob.type == 'MESH' and any(m.type == 'ARMATURE' and m.object == arm for m in ob.modifiers):
        ob.rotation_mode = 'QUATERNION'
        ob.matrix_local = arm.pose.bones[bone_name].matrix.copy()
    else:
        ob.parent_type = 'BONE'
        ob.matrix_parent_inverse = Matrix.Translation((0, -bone.length, 0))


def _frame_matrix(f):
    from mathutils import Matrix
    r = f.rot
    return Matrix(((r[0], r[3], r[6], f.pos[0]), (r[1], r[4], r[7], f.pos[1]),
                   (r[2], r[5], r[8], f.pos[2]), (0.0, 0.0, 0.0, 1.0)))


# ---------------------------------------------------------------------------------------------- mesh

def clean_faces(faces, mats):
    """The faces bmesh keeps when DragonFF adds them one by one: of two faces with the same vertices in
    a row the second, no degenerate faces, no second face over a vertex set already used."""
    if not len(faces):
        return faces, mats
    key = np.sort(faces, axis=1)
    keep = np.ones(len(faces), bool)
    keep[:-1] = ~(key[:-1] == key[1:]).all(axis=1)                   # equal to the next one: skip
    keep &= (key[:, 0] != key[:, 1]) & (key[:, 1] != key[:, 2])      # fewer than 3 vertices
    idx = np.nonzero(keep)[0]
    _u, first = np.unique(key[idx], axis=0, return_index=True)        # first of each vertex set wins
    idx = np.sort(idx[first])
    return faces[idx], mats[idx]


def _mesh(geo, frame, d, opts):
    if opts.get("use_mat_split") or not len(geo.tris):
        faces, mats = geo.split_tris, geo.split_mats
        if faces is None:
            faces, mats = geo.tris, geo.tri_mats
    else:
        faces, mats = geo.tris, geo.tri_mats
    nmat = len(geo.materials)
    order = list(dict.fromkeys(list(geo.split_order) + list(range(nmat))))
    order = [m for m in order if m < nmat]
    if nmat and len(mats):
        pos = np.zeros(max(int(mats.max()), nmat - 1) + 1, np.int32)
        pos[order] = np.arange(len(order))
        bad = mats >= len(pos)
        mats = np.where(bad, 0, pos[np.minimum(mats, len(pos) - 1)])
    faces, mats = clean_faces(np.asarray(faces, np.int32), np.asarray(mats, np.int32))
    me = bpy.data.meshes.new(clean_name(frame.name))
    nv = geo.num_verts if geo.verts is not None else 0
    me.vertices.add(nv)
    if nv:
        me.vertices.foreach_set("co", np.ascontiguousarray(geo.verts, np.float32).ravel())
    nf = len(faces)
    corner = faces.ravel()
    me.loops.add(nf * 3)
    me.loops.foreach_set("vertex_index", corner)
    me.polygons.add(nf)
    me.polygons.foreach_set("loop_start", np.arange(0, nf * 3, 3, dtype=np.int32))
    if nmat:
        me.polygons.foreach_set("material_index", mats)
    me.polygons.foreach_set("use_smooth", np.ones(nf, bool))
    me.update(calc_edges=True)
    for k, uv in enumerate(geo.uvs):
        layer = me.uv_layers.new(name="Float2" if k == 0 else "Float2.%03d" % k, do_init=False)
        u = np.empty((len(corner), 2), np.float32)
        u[:, 0] = uv[corner, 0]
        u[:, 1] = 1.0 - uv[corner, 1]
        layer.data.foreach_set("uv", u.ravel())
    names = iter(("Col", "Col.001"))
    for cols in (geo.prelit, geo.night):
        if cols is not None:
            at = me.color_attributes.new(next(names), 'BYTE_COLOR', 'CORNER')
            at.data.foreach_set("color_srgb", (cols[corner].astype(np.float32) / 255.0).ravel())
    if geo.normals is not None and nv:
        me.normals_split_custom_set_from_vertices(np.ascontiguousarray(geo.normals, np.float32).reshape(-1, 3))
    descs = [describe(geo.materials[m], geo, frame.name, i, d) for i, m in enumerate(order)]
    return me, descs


def _remove_doubles(ob):
    """DragonFF's 'Remove Doubles' option: merge vertices closer than 0.01 mm, keep open edges sharp,
    add an Edge Split modifier."""
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    for e in bm.edges:
        if len(e.link_loops) == 1:
            e.smooth = False
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.00001)
    m = ob.modifiers.new("EdgeSplit", 'EDGE_SPLIT')
    m.use_edge_angle = False
    bm.to_mesh(ob.data)
    bm.free()


# ----------------------------------------------------------------------------------------- materials

def describe(m, geo, frame_name, index, d):
    """A plain description of a RenderWare material (tuples, numbers, strings: stored as is in the
    asset cache); "rwkey" is what decides sharing."""
    surf = m.surface if m.surface is not None else geo.surface
    tex = m.texture if m.textured and m.texture is not None else None
    uv = None
    if m.uv_anims:
        a = next((u for u in d.uv_anims if u.name == m.uv_anims[0]), None)
        if a is not None:
            uv = (a.name, tuple((f[0], tuple(f[1])) for f in a.frames))
    bump = None
    if m.bump_map is not None:
        b = m.bump_map[2] or m.bump_map[1]
        if b is not None:
            bump = (b.name, m.bump_map[0])
    t = m.texture
    rwkey = (tuple(m.color), (t.name, t.mask, t.filters, t.addressing) if t is not None else None,
             (m.bump_map[0], _tn(m.bump_map[1]), _tn(m.bump_map[2])) if m.bump_map else None,
             (m.env_map[0], m.env_map[1], _tn(m.env_map[2])) if m.env_map else None,
             m.dual is not None, tuple(m.specular) if m.specular else None,
             tuple(m.reflection) if m.reflection else None, tuple(m.uv_anims),
             repr(sorted(m.user_data.items())) if m.user_data else None)
    return {
        "rwkey": rwkey,
        "name": material_name(m, tex, "%s.%d" % (clean_name(frame_name), index)),
        "color": tuple(m.color),
        "tex": (tex.name, tex.filters, tex.addressing) if tex is not None else None,
        "surface": tuple(surf) if surf is not None else None,
        "env": (m.env_map[0], m.env_map[2].name if m.env_map[2] else "") if m.env_map else None,
        "spec": tuple(m.specular) if m.specular else None,
        "refl": tuple(m.reflection) if m.reflection else None,
        "bump": bump,
        "uv_anim": uv,
    }


def _tn(t):
    return t.name if t is not None else None


def material_name(m, tex, fallback):
    name = None
    if tex is not None:
        for pat, nm in _PATTERNS:
            if pat in tex.name:
                name = nm
    if m.color[3] < 200:
        name = "glass"
    return _COLOUR_NAMES.get(tuple(m.color), name) or fallback


def _key(desc, images):
    img = None
    if desc["tex"]:
        found = images.get(desc["tex"][0])
        img = found[0].name if found else "?" + desc["tex"][0].lower()
    # what DragonFF counted as "the same material" (its group_materials / our former MaterialShareBatch):
    # the material as RenderWare writes it, without the surface values (ambient / specular / diffuse:
    # the first material's are used), + the image the texture name found
    return (desc["rwkey"], img)


def material(desc, images, share):
    """The material for a description (shared through `share` when an equal one exists; UV-animated
    ones are never shared: each keeps its own animation)."""
    key = None if desc["uv_anim"] else _key(desc, images)
    if key is not None:
        mat = share.get(key)
        if mat is not None:
            try:
                mat.name                    # still valid (not deleted / undone)?
                return mat
            except ReferenceError:
                pass
    mat = _make_material(desc, images)
    if key is not None:
        share[key] = mat
    return mat


def _make_material(desc, images):
    mat = bpy.data.materials.new(desc["name"])
    mat.use_nodes = True
    try:
        mat.blend_method = 'CLIP'
    except TypeError:                       # gone in newer Blender versions
        pass
    nt = mat.node_tree
    p = nt.nodes.get("Principled BSDF") or next(n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED')
    c = desc["color"]
    p.inputs["Base Color"].default_value = (c[0] / 255, c[1] / 255, c[2] / 255, c[3] / 255)
    mat.diffuse_color = (c[0] / 255, c[1] / 255, c[2] / 255, c[3] / 255)
    if desc["surface"] is not None:
        _amb, spec, diff = desc["surface"]
        s = p.inputs.get("Specular IOR Level") or p.inputs.get("Specular")
        if s is not None:
            s.default_value = spec
        p.inputs["Roughness"].default_value = diff
    if desc["tex"]:
        tname = desc["tex"][0]
        node = nt.nodes.new("ShaderNodeTexImage")
        node.label = tname
        node.location = (p.location.x - 400, p.location.y)
        found = images.get(tname)
        node.image = found[0] if found else None
        nt.links.new(node.outputs["Color"], p.inputs["Base Color"])
        nt.links.new(node.outputs["Alpha"], p.inputs["Alpha"])
        if desc["uv_anim"]:
            _uv_animation(mat, node, desc["uv_anim"])
    if desc["bump"]:
        bname, strength = desc["bump"]
        found = images.get(bname)
        img = nt.nodes.new("ShaderNodeTexImage")
        img.label, img.image = bname, found[0] if found else None
        img.location = (p.location.x - 600, p.location.y - 300)
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nm.label = bname
        nm.inputs["Strength"].default_value = strength
        nm.location = (p.location.x - 250, p.location.y - 300)
        nt.links.new(img.outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], p.inputs["Normal"])
    return mat


def _uv_animation(mat, image_node, anim):
    """A UV animation (scrolling water, waterfalls, signs): Texture Coordinate -> Mapping -> image, the
    Mapping's location / scale keyed from the animation frames (times in seconds, scene fps)."""
    name, frames = anim
    nt = mat.node_tree
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.vector_type = 'POINT'
    tc.location = (image_node.location.x - 400, image_node.location.y)
    mp.location = (image_node.location.x - 200, image_node.location.y)
    nt.links.new(tc.outputs["UV"], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], image_node.inputs["Vector"])
    if not frames:
        return
    fps = bpy.context.scene.render.fps
    act = bpy.data.actions.new(name)
    ad = nt.animation_data_create()
    ad.action = act
    # uv values: [?, scale u, scale v, ?, offset u, offset v] (v flipped like the UVs)
    chans = ((1, 'nodes["%s"].inputs[3].default_value' % mp.name, 0), (2, 'nodes["%s"].inputs[3].default_value' % mp.name, 1),
             (4, 'nodes["%s"].inputs[1].default_value' % mp.name, 0), (5, 'nodes["%s"].inputs[1].default_value' % mp.name, 1))
    for src, path, idx in chans:
        fc = act.fcurve_ensure_for_datablock(nt, path, index=idx)
        prev_t = None
        for t, uv in frames:
            v = 1.0 - uv[src] if src == 5 else uv[src]
            if prev_t is not None and t == prev_t and len(fc.keyframe_points):
                fc.keyframe_points[-1].interpolation = 'CONSTANT'
            fc.keyframe_points.insert(t * fps, v, options={'FAST'})
            prev_t = t
        for kp in fc.keyframe_points:
            if kp.interpolation != 'CONSTANT':
                kp.interpolation = 'LINEAR'


# -------------------------------------------------------------------------------------------- 2DFX

LIGHT_FIELDS = ("corona_far_clip", "point_light_range", "corona_size", "shadow_size", "corona_show_mode",
                "corona_reflection", "corona_flare_type", "shadow_color_mult", "flags1", "corona_tex",
                "shadow_tex", "shadow_z_distance", "flags2")


def effect_object(e):
    """A Blender object for a 2DFX entry: lights and road signs; None for the invisible helpers."""
    if e.type == rwdff.FX_LIGHT and hasattr(e, "color"):
        data = bpy.data.lights.new("2dfx_light", 'POINT')
        data.color = tuple(c / 255 for c in e.color[:3])
        fx = {k: getattr(e, k) for k in LIGHT_FIELDS}
        fx["alpha"] = e.color[3]
        if e.look_direction is not None:
            fx["look_direction"] = list(e.look_direction)
        data["gta_2dfx"] = fx
        ob = bpy.data.objects.new("2dfx_light", data)
        ob.location = e.pos
        return ob
    if e.type == rwdff.FX_ROAD_SIGN and len(e.raw) >= 88:
        return _road_sign(e)
    return None


def _road_sign(e):
    import struct
    sx, sy, rx, ry, rz, flags = struct.unpack_from("<5fH", e.raw, 0)
    lines = [e.raw[22 + 16 * k:38 + 16 * k].split(b"\0", 1)[0].decode("latin-1") for k in range(4)]
    n_lines = {0: 4, 1: 1, 2: 2, 3: 3}[flags & 3]
    n_chars = {0: 16, 1: 2, 2: 4, 3: 8}[(flags >> 2) & 3]
    body = "\n".join(l.replace("_", " ")[:n_chars] for l in lines[:n_lines])
    data = bpy.data.curves.new("2dfx_road_sign", 'FONT')
    data.body = body
    data.align_x = data.align_y = 'CENTER'
    data.size = 0.5
    font = bpy.data.fonts.get(FONT)
    if font is None:
        import os
        path = os.path.join(bpy.utils.system_resource('DATAFILES'), "fonts", "DejaVuSansMono.woff2")
        if os.path.isfile(path):
            font = bpy.data.fonts.load(path)
    if font is not None:
        data.font = font
    data["gta_2dfx"] = {"size": [sx, sy], "colour": (flags >> 4) & 3}
    ob = bpy.data.objects.new("2dfx_road_sign", data)
    ob.rotation_mode = 'ZXY'
    ob.rotation_euler = (math.radians(rx), math.radians(ry), math.radians(rz))
    ob.location = e.pos
    return ob


# --------------------------------------------------------------------------------------- collision

def is_collision(ob):
    """True for collision / shadow objects: ours (gta_col) or DragonFF's (dff.type COL / SHA, files made
    in older files; read safely, DragonFF may be gone)."""
    if ob.get("gta_col") is not None:
        return True
    try:
        return ob.dff.type in ('COL', 'SHA')
    except AttributeError:
        return False


def build_collision(models, prefix, link=True):
    """Collections "<prefix>.<model name>" with the collision of each ColModel (formats/col.py): spheres
    (SPHERE empties scaled to the radius), boxes (CUBE empties), the collision mesh "<coll>.ColMesh" and
    the shadow mesh "<coll>.ShadowMesh", materials per surface named and coloured from formats/colmats.
    Objects are tagged gta_col = "COL" / "SHA" (spheres / boxes also keep gta_col_surface)."""
    from .formats import colmats
    out = []
    last_version = models[-1].version if models else 3       # DragonFF picks the name table by it
    for m in models:
        c = bpy.data.collections.new("%s.%s" % (prefix, m.name))
        if link:
            bpy.context.scene.collection.children.link(c)
        for i, (centre, r, _mat, surf) in enumerate(m.spheres):
            ob = bpy.data.objects.new("%s.ColSphere.%d" % (c.name, i), None)
            ob.location, ob.scale, ob.empty_display_type = centre, (r, r, r), 'SPHERE'
            ob["gta_col"], ob["gta_col_surface"] = "COL", list(surf)
            c.objects.link(ob)
        for i, (lo, hi, _mat, surf) in enumerate(m.boxes):
            ob = bpy.data.objects.new("%s.ColBox.%d" % (c.name, i), None)
            half = [(hi[k] - lo[k]) * 0.5 for k in range(3)]
            ob.location = [lo[k] + half[k] for k in range(3)]
            ob.scale, ob.empty_display_type = half, 'CUBE'
            ob["gta_col"], ob["gta_col_surface"] = "COL", list(surf)
            c.objects.link(ob)
        if len(m.verts):
            _col_mesh(c, c.name + ".ColMesh", m.verts, m.faces, m.face_surfaces,
                      m.face_groups if m.flags & 8 else None, False, last_version, colmats)
        if len(m.shadow_verts):
            _col_mesh(c, c.name + ".ShadowMesh", m.shadow_verts, m.shadow_faces, m.shadow_surfaces, None, True,
                      last_version, colmats)
        out.append(c)
    return out


def _col_mesh(c, name, verts, faces, surfaces, groups, shadow, version, colmats):
    faces = np.asarray(faces, np.int32)[:, [0, 2, 1]]               # DragonFF adds them as a, c, b
    key = np.sort(faces, axis=1)
    ok = (key[:, 0] != key[:, 1]) & (key[:, 1] != key[:, 2]) & (faces < len(verts)).all(axis=1)
    idx = np.nonzero(ok)[0]
    _u, first = np.unique(key[idx], axis=0, return_index=True)       # a face over a used vertex set fails
    idx = np.sort(idx[first])
    faces, surfaces = faces[idx], np.asarray(surfaces, np.int32)[idx]
    me = bpy.data.meshes.new(name)
    me.vertices.add(len(verts))
    me.vertices.foreach_set("co", np.ascontiguousarray(verts, np.float32).ravel())
    nf = len(faces)
    me.loops.add(nf * 3)
    me.loops.foreach_set("vertex_index", faces.ravel())
    me.polygons.add(nf)
    me.polygons.foreach_set("loop_start", np.arange(0, nf * 3, 3, dtype=np.int32))
    order = {}
    mi = np.empty(nf, np.int32)
    for k, sf in enumerate(map(tuple, surfaces.tolist())):
        mi[k] = order.setdefault(sf, len(order))
    me.polygons.foreach_set("material_index", mi)
    me.polygons.foreach_set("use_smooth", np.zeros(nf, bool))      # flat, like bmesh makes them
    me.update(calc_edges=True)
    if groups:
        at = me.attributes.new("face group", 'INT', 'FACE')
        vals = np.zeros(nf, np.int32)
        for g, (_lo, _hi, start, end) in enumerate(groups):
            vals[start:min(end + 1, nf)] = g
        at.data.foreach_set("value", vals)
    ob = bpy.data.objects.new(name, me)
    ob["gta_col"] = "SHA" if shadow else "COL"
    c.objects.link(ob)
    for sf in order:
        me.materials.append(_col_material(sf, version, colmats))


def _col_material(surface, version, colmats):
    mat_id = surface[0]
    group, name = colmats.default["group"], None
    try:
        table = colmats.sa_mats if version == 3 or mat_id >= 34 else colmats.vc_mats
        g, nm = table[mat_id]
        group, name = g, "%s - %s" % (colmats.groups[g][0], nm)
    except KeyError:
        pass
    hexc = colmats.groups[group][1]
    rgb = [int(hexc[k:k + 2], 16) / 255 for k in (0, 2, 4)]
    mat = bpy.data.materials.new(name or colmats.groups[group][0])
    mat.use_nodes = True
    p = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    p.inputs["Base Color"].default_value = (*rgb, 1.0)
    mat.diffuse_color = (*rgb, 1.0)
    mat["gta_col_surface"] = list(surface)
    return mat
