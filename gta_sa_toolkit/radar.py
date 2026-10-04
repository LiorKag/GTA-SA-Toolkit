# GTA SA Toolkit - top-down San Andreas map built from the game's radar tiles.
# gta3.img holds radar00.txd .. radar143.txd: a 12 x 12 grid of 500 m tiles covering -3000..3000 m.
# radar00 is the north-west corner; index = row * 12 + column, rows run north to south.
# The built map is cached as a PNG in the add-on's user folder (never in the .blend).
import json
import os
import struct
import zlib

import numpy as np

GRID = 12
TILE_M = 500.0
HALF = GRID * TILE_M / 2.0          # 3000 m: the map spans -HALF..HALF on X and Y
CACHE_VERSION = 1

_MAP = {"stamp": None, "arr": None}     # built map kept in memory between picks


# ----------------------------------------------------------------------------- coordinates
def tile_to_world(index, px=0.0, py=0.0, tile_px=128):
    """World (x, y) of pixel (px, py) inside radar tile `index` (px right, py down from the tile's top)."""
    row, col = divmod(index, GRID)
    x = -HALF + (col + px / tile_px) * TILE_M
    y = HALF - (row + py / tile_px) * TILE_M
    return x, y


def world_to_tile(x, y):
    """Radar tile index under world (x, y), or None outside the map."""
    col = int((x + HALF) // TILE_M)
    row = int((HALF - y) // TILE_M)
    if 0 <= col < GRID and 0 <= row < GRID:
        return row * GRID + col
    return None


def world_to_pixel(x, y, size):
    """World (x, y) -> (column, row) pixel in the full map image (size x size, top row first)."""
    return (x + HALF) / (2 * HALF) * size, (HALF - y) / (2 * HALF) * size


def pixel_to_world(u, v, size):
    return u / size * 2 * HALF - HALF, HALF - v / size * 2 * HALF


# ----------------------------------------------------------------------------- building
def _img_path(game):
    for a in game.imgs.archives:
        if os.path.basename(a.path).lower() == "gta3.img":
            return a.path
    return None


def stamp(game):
    """What the cached map was built from: rebuilt when gta3.img changes."""
    p = _img_path(game)
    st = os.stat(p) if p else None
    return {"v": CACHE_VERSION, "img": os.path.normcase(os.path.abspath(p)) if p else "",
            "size": st.st_size if st else 0, "mtime_ns": st.st_mtime_ns if st else 0}


def _decode_tile(data):
    from .formats import txd
    return txd.load(data).textures[0].decode(0)        # (h, w, 4) uint8, top row first


def _fit(tile, n):
    """Nearest-neighbour resize of a (h, w, 4) tile to (n, n, 4)."""
    h, w = tile.shape[:2]
    if h == n and w == n:
        return tile
    return tile[(np.arange(n) * h // n)[:, None], (np.arange(n) * w // n)[None, :]]


def build_map(game):
    """(N, N, 4) uint8 RGBA, top row = north. Missing/broken tiles stay transparent."""
    tiles, n = {}, 0
    try:
        for i in range(GRID * GRID):
            data = game.imgs.read("radar%02d.txd" % i)
            if not data:
                continue
            try:
                tiles[i] = _decode_tile(data)
                n = max(n, tiles[i].shape[0], tiles[i].shape[1])
            except Exception as e:             # noqa
                print("[GTA SA Toolkit] radar%02d: %s" % (i, e))
    finally:
        game.imgs.close()
    if not tiles:
        raise RuntimeError("No radar tiles (radar00.txd ...) found in gta3.img")
    out = np.zeros((GRID * n, GRID * n, 4), np.uint8)
    for i, t in tiles.items():
        row, col = divmod(i, GRID)
        out[row * n:(row + 1) * n, col * n:(col + 1) * n] = _fit(t, n)[:, :, :4]
    out[:, :, 3] = np.where(out[:, :, 3] > 0, 255, 0)  # radar alpha is irrelevant; keep "has data"
    return out


# ----------------------------------------------------------------------------- PNG cache
def write_png(path, arr):
    h, w = arr.shape[:2]
    raw = np.zeros((h, w * 4 + 1), np.uint8)          # filter byte 0 per row
    raw[:, 1:] = arr.reshape(h, w * 4)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    body = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw.tobytes(), 6)) + chunk(b"IEND", b""))
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, path)


def read_png(path):
    """Reads the PNGs write_png makes (8-bit RGBA, filter 0). Raises ValueError on anything else."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, w, h = 8, [], 0, 0
    while pos < len(data):
        ln, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + ln]
        if tag == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            if depth != 8 or ctype != 6:
                raise ValueError("unexpected PNG format")
        elif tag == b"IDAT":
            idat.append(body)
        pos += 12 + ln
    raw = np.frombuffer(zlib.decompress(b"".join(idat)), np.uint8).reshape(h, w * 4 + 1)
    if raw[:, 0].any():
        raise ValueError("PNG uses row filters")
    return raw[:, 1:].reshape(h, w, 4).copy()


def cache_dir():
    import bpy
    return bpy.utils.extension_path_user(__package__, path="radar", create=True)


def get_map(game, folder=None):
    """The radar map array, from memory, the PNG cache, or built from gta3.img (then cached).
    Returns (array, how) with how in 'memory', 'cache', 'built'."""
    st = stamp(game)
    if _MAP["stamp"] == st and _MAP["arr"] is not None:
        return _MAP["arr"], 'memory'
    folder = folder or cache_dir()
    png, meta = os.path.join(folder, "radar_map.png"), os.path.join(folder, "radar_map.json")
    arr, how = None, 'built'
    try:
        with open(meta, encoding="utf-8") as f:
            if json.load(f) == st:
                arr, how = read_png(png), 'cache'
    except (OSError, ValueError, zlib.error):
        arr = None
    if arr is None:
        arr = build_map(game)
        try:
            write_png(png, arr)
            with open(meta, "w", encoding="utf-8") as f:
                json.dump(st, f)
        except OSError as e:
            print("[GTA SA Toolkit] could not cache the radar map:", e)
    _MAP.update(stamp=st, arr=arr)
    return arr, how


def forget():
    _MAP.update(stamp=None, arr=None)
