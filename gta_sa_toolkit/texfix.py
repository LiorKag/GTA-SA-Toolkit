# GTA SA Toolkit - "Fix Missing GTA Textures": images of an older .blend whose files no longer exist
# (e.g. textures exported to <old game folder>\data\maps\gta3\<txd>\<texture>.bmp before the game folder
# moved) get the game's own pixels again, found by texture name in the game's TXDs, and are packed into
# the file. The image datablocks stay the same, so every material using them is fixed at once.
#
# Lookup: the TXD named after the missing file's folder first ("docklight\aascaff.bmp" -> docklight.txd /
# aascaff), else an index of every texture name in the game's TXDs (built once per session, only when
# needed; a name in several TXDs takes the first). Nothing runs on redraw.
import os

import bpy
import numpy as np

_INDEX = {"key": None, "names": None}         # texture name (lower) -> txd name, per game folder + IMG times


def missing_images():
    """Images that read from a file that isn't there (not packed, not linked from a library)."""
    out = []
    for img in bpy.data.images:
        if img.source != 'FILE' or img.packed_file is not None or img.library is not None or not img.filepath:
            continue
        if not os.path.isfile(bpy.path.abspath(img.filepath, library=img.library)):
            out.append(img)
    return out


def _parts(img):
    """(texture name, folder name) from the image's old file path ("…\\docklight\\aascaff.bmp")."""
    path = img.filepath.replace("\\", "/").rstrip("/")
    tex = os.path.splitext(os.path.basename(path))[0] or img.name
    folder = os.path.basename(os.path.dirname(path))
    return tex, folder


def _index(game):
    """{texture name: txd name} of every TXD in the game's IMGs (cached until an IMG changes)."""
    from .formats import dffprobe
    key = (game.root, tuple(sorted((a.path, a.mtime) for a in getattr(game.imgs, "archives", []))))
    if _INDEX["key"] == key and _INDEX["names"] is not None:
        return _INDEX["names"]
    names = {}
    for fname in game.imgs.names_with_ext(".txd"):
        data = game.imgs.read(fname)
        for t in dffprobe.txd_names(data or b""):
            names.setdefault(t, fname[:-4].lower())
    _INDEX.update(key=key, names=names)
    return names


def _texture(game, txd_name, tex_name, cache):
    """(h, w, 4) uint8 of a texture in a game TXD, or None."""
    from .formats import txd as rwtxd
    key = txd_name.lower()
    if key not in cache:
        data = game.read_txd_bytes(key)
        try:
            cache[key] = {t.name.lower(): t for t in rwtxd.load(data).textures} if data else {}
        except rwtxd.RWError:
            cache[key] = {}
    t = cache[key].get(tex_name.lower())
    if t is None:
        return None
    try:
        return t.decode(0)
    except rwtxd.RWError:
        return None


def fill(img, rgba):
    """Give an image these pixels (top row first) and pack it into the .blend."""
    h, w = rgba.shape[:2]
    img.source = 'GENERATED'
    img.generated_width, img.generated_height = w, h
    img.scale(w, h)
    img.pixels.foreach_set((rgba[::-1].astype(np.float32) * (1.0 / 255.0)).ravel())
    img.pack()
    img["gta_tk_tex"] = True


def fix(game, images=None):
    """Fix the missing images. Returns (fixed images, [names not found in the game])."""
    images = missing_images() if images is None else images
    cache, fixed, lost = {}, [], []
    index = None
    for img in images:
        tex, folder = _parts(img)
        rgba = _texture(game, folder, tex, cache) if folder else None
        if rgba is None:
            if index is None:
                index = _index(game)
            txd = index.get(tex.lower())
            rgba = _texture(game, txd, tex, cache) if txd else None
        if rgba is None:
            lost.append(tex)
            continue
        fill(img, rgba)
        fixed.append(img)
    game.imgs.close()
    return fixed, lost


class GTATK_OT_fix_missing_textures(bpy.types.Operator):
    bl_idname = "gtatk.fix_missing_textures"
    bl_label = "Fix Missing GTA Textures"
    bl_description = ("Images whose files are gone (e.g. textures saved under an old game folder) get the "
                      "game's own texture of the same name and are packed into this file")
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        n = len(missing_images())
        if not n:
            self.report({'INFO'}, "No missing textures in this file")
            return {'CANCELLED'}
        self.count = n
        kw = {"title": "Fix %d missing textures?" % n, "message": "They'll use the game's textures and be "
              "packed into this file", "confirm_text": "Fix"} if bpy.app.version >= (4, 1, 0) else {}
        return context.window_manager.invoke_confirm(self, event, **kw)

    def execute(self, context):
        from . import mapimport
        p = context.scene.gta_tk
        try:
            game = mapimport.get_game(p.game_root)
        except Exception as e:                 # noqa
            self.report({'ERROR'}, "Pick your GTA San Andreas folder first (%s)" % e)
            return {'CANCELLED'}
        missing = missing_images()
        fixed, lost = fix(game, missing)
        msg = "Fixed %d of %d missing textures" % (len(fixed), len(missing))
        if lost:
            msg += "; not in the game: %s%s" % (", ".join(sorted(set(lost))[:8]), "…" if len(set(lost)) > 8 else "")
        self.report({'WARNING' if lost else 'INFO'}, msg)
        return {'FINISHED'}


def draw_section(layout, context):
    col = layout.column(align=True)
    col.label(text="Textures of older files that point", icon='INFO')
    col.label(text="to a folder that's gone", icon='BLANK1')
    layout.operator("gtatk.fix_missing_textures", icon='TEXTURE')


def register():
    bpy.utils.register_class(GTATK_OT_fix_missing_textures)


def unregister():
    bpy.utils.unregister_class(GTATK_OT_fix_missing_textures)
