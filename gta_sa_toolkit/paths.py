# GTA SA Toolkit - the game's road and sidewalk graph (formats/nodes.py), read once per game and
# kept until an IMG archive changes. The whole map is ~68k nodes and reads in about 0.3 s.
import os

from .formats import nodes

_CACHE = {"key": None, "graph": None}


def _key(game):
    out = [getattr(game, "root", None)]
    for a in game.imgs.archives:
        try:
            out.append((a.path, os.path.getmtime(a.path)))
        except OSError:
            out.append((a.path, None))
    return tuple(out)


def graph(game, reload=False):
    """PathGraph of all 64 areas of this game."""
    key = _key(game)
    if reload or _CACHE["graph"] is None or _CACHE["key"] != key:
        try:
            g = nodes.load(game.imgs.read)
        finally:
            game.imgs.close()             # don't keep gta3.img locked
        _CACHE.update(key=key, graph=g)
    return _CACHE["graph"]


def clear():
    _CACHE.update(key=None, graph=None)
