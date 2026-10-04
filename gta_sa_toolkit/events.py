# GTA SA Toolkit - events other add-ons (GTA SA Studio) can listen to.
#   import_done(objects)  once per finished import (map, model, vehicle, weapon, toy, SA-MP code)
#   import_removed()      after "Remove Imported Map" deleted an import
#   game_scanned()        after "Scan Game Files"
# The importers call import_done() for what they create. An operator that runs several importers
# wraps them in `with events.import_batch():` so listeners hear about the whole job once.
import functools
import traceback

_LISTENERS = {"import_done": [], "import_removed": [], "game_scanned": []}
_BATCH = {"depth": 0, "objects": [], "fired": False}


def add(event, fn):
    lst = _LISTENERS[event]
    if fn not in lst:
        lst.append(fn)
    return fn


def remove(fn):
    for lst in _LISTENERS.values():
        while fn in lst:
            lst.remove(fn)


def clear():
    for lst in _LISTENERS.values():
        lst.clear()


def _call(event, *args):
    for fn in list(_LISTENERS[event]):
        try:
            fn(*args)
        except Exception:                 # noqa - a listener must never break an import
            print("[GTA SA Toolkit] %s listener %r failed:" % (event, fn))
            traceback.print_exc()


def _unique_live(objects):
    out, seen = [], set()
    for o in objects:
        try:
            k = o.as_pointer()         # also raises ReferenceError for deleted objects
        except ReferenceError:
            continue
        if k not in seen:
            seen.add(k)
            out.append(o)
    return out


def _finish_import(objects):
    _call("import_done", _unique_live(objects))


def import_done(objects):
    objects = [o for o in objects if o is not None]
    if _BATCH["depth"]:
        _BATCH["objects"].extend(objects)
        _BATCH["fired"] = True
        return
    _finish_import(objects)


class import_batch:
    """Collect import_done calls and send them as one event when the outermost batch ends
    (only if something was imported)."""

    def __enter__(self):
        if _BATCH["depth"] == 0:
            _BATCH["objects"], _BATCH["fired"] = [], False
        _BATCH["depth"] += 1
        return self

    def __exit__(self, *exc):
        _BATCH["depth"] -= 1
        if _BATCH["depth"] == 0 and _BATCH["fired"]:
            objs = _BATCH["objects"]
            _BATCH["objects"], _BATCH["fired"] = [], False
            _finish_import(objs)
        return False


def batched(execute):
    """Decorator for an operator's execute(): everything it imports is one import_done event."""
    @functools.wraps(execute)
    def wrapper(self, context):          # Blender checks execute() has exactly (self, context)
        with import_batch():
            return execute(self, context)
    return wrapper


def import_removed():
    _call("import_removed")


def game_scanned():
    _call("game_scanned")
