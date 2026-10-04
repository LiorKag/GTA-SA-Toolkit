# GTA SA Studio - which timecyc file drives the sky (Scene tab > Time & Weather > Timecyc).
# Entries: the game's data/timecyc.dat ("(modified)" when its numbers aren't the stock PC file's),
# "Stock SA" (the stock numbers, kept in Studio's user data the first time a stock file is seen - the
# add-on doesn't ship the game's file), files added with Add File..., and timecyc files core found in
# the Mod Folders. The choice is per scene (Scene.gta_studio.timecyc_choice); the chosen numbers are
# also stored in the scene (timecyc_data), so the sky stays the same on a PC without that file.
# No choice made yet (older files) = what Studio did before: Preferences' timecyc.dat, else the game's.
import os

import bpy

from . import corelink, timecyc

GAME, STOCK = "GAME", "STOCK"


def _prefs():
    try:
        return corelink.prefs()
    except (KeyError, AttributeError):
        return None


def _game_path():
    api = corelink.api()
    return api.game_file("data/timecyc.dat") if api is not None else None


# ----------------------------------------------------------------------------- stock numbers
def _stock_path():
    d = os.environ.get("GTATK_LIBRARY_DIR")                  # tests: their own folder
    if not d:
        d = bpy.utils.extension_path_user(__package__.rpartition(".")[0], path="timecyc", create=True)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "stock_timecyc.json")


def stock_known():
    return os.path.isfile(_stock_path())


def _remember_stock(path):
    """A stock file was read: keep its numbers so 'Stock SA' works even without the file."""
    if stock_known():
        return
    try:
        with open(_stock_path(), "w", encoding="utf-8") as f:
            f.write(timecyc.to_store(timecyc.read_text(path)))
    except OSError as e:
        print("[GTA SA Toolkit] keeping the stock timecyc:", e)


def _stock():
    with open(_stock_path(), "r", encoding="utf-8") as f:
        return timecyc.from_store(f.read(), "Stock SA")


# ----------------------------------------------------------------------------- entries
def _added():
    pr = _prefs()
    return [bpy.path.abspath(f.path) for f in pr.timecyc_files] if pr is not None else []


def _hidden():
    pr = _prefs()
    return {h for h in (pr.timecyc_hidden.split("\n") if pr is not None else ()) if h}


def _found():
    api = corelink.api()
    if api is None or not hasattr(api, "mod_timecyc_files"):
        return []
    hid = _hidden()
    return [p for p in api.mod_timecyc_files() if os.path.normcase(p) not in hid]


def _short(path):
    """'Bright Timecyc/timecyc.dat'."""
    return "%s/%s" % (os.path.basename(os.path.dirname(path)), os.path.basename(path))


def label_of(choice):
    """What the dropdown shows for an entry (reads the file's fingerprint once; not for draw())."""
    if choice == GAME:
        p = _game_path()
        fp = timecyc.fingerprint_of(p) if p else None
        if fp is None:
            return "Game: timecyc.dat (not readable)"
        if fp in timecyc.STOCK_FINGERPRINTS:
            _remember_stock(p)
            return "Game: timecyc.dat"
        return "Game: timecyc.dat (modified)"
    if choice == STOCK:
        return "Stock SA"
    path = choice[2:]
    fp = timecyc.fingerprint_of(path)
    if fp is not None and fp in timecyc.STOCK_FINGERPRINTS:
        _remember_stock(path)
        return "Stock SA (%s)" % _short(path)
    state = "" if fp else (" (not readable)" if os.path.isfile(path) else " (not found)")
    return ("Mod: " if choice.startswith("M:") else "") + _short(path) + state


def entries():
    """[(choice id, label)]: game, Stock SA (when its numbers are known and no listed file is stock),
    added files ("F:<path>"), Mod Folder finds ("M:<path>")."""
    out = [(GAME, label_of(GAME))]
    out += [("F:" + p, label_of("F:" + p)) for p in _added()]
    seen = {os.path.normcase(p) for p in _added()}
    out += [("M:" + p, label_of("M:" + p)) for p in _found() if os.path.normcase(p) not in seen]
    if stock_known() and not any(lab.startswith("Stock SA") for _c, lab in out) and \
            out[0][1] != "Game: timecyc.dat":
        out.insert(1, (STOCK, "Stock SA"))
    return out


# ----------------------------------------------------------------------------- what drives the sky
def _legacy_path():
    pr = _prefs()
    own = bpy.path.abspath(pr.timecyc_path) if pr is not None and pr.timecyc_path else ""
    return own or _game_path()


def _file_of(choice):
    if choice == GAME:
        return _game_path()
    if choice[:2] in ("F:", "M:"):
        return choice[2:]
    return None


def resolve(scene):
    """(Timecyc or None, note) for the scene's choice. A missing or broken file falls back to the numbers
    stored in the scene, with a note. Keeps the stored copy up to date (call from updates / operators,
    not draw())."""
    p = scene.gta_studio
    choice = p.timecyc_choice
    if choice == STOCK:
        if stock_known():
            return _stock(), ""
        return _stored(p, "the stock numbers aren't known on this PC")
    path = _file_of(choice) if choice else _legacy_path()
    if not path:
        return _stored(p, "timecyc.dat not found (set the game folder)")
    try:
        tc = timecyc.load(path)
    except OSError:
        return _stored(p, "%s not found" % os.path.basename(path))
    except timecyc.TimecycError as e:
        return _stored(p, "Can't read %s: %s" % (os.path.basename(path), e))
    fp = timecyc.fingerprint_of(path)
    if fp in timecyc.STOCK_FINGERPRINTS:
        _remember_stock(path)
    if choice:
        _store(p, path)
    return tc, ""


def _stored(p, why):
    if p.timecyc_data:
        try:
            return timecyc.from_store(p.timecyc_data, "stored"), "%s: using the copy saved in this .blend" % why
        except (ValueError, timecyc.TimecycError):
            pass
    return None, why + ": simple sky used"


def _store(p, path):
    """Keep the file's numbers in the scene (again only when the file changed)."""
    st = os.stat(path)
    sig = "%s|%d|%d" % (os.path.normcase(path), st.st_mtime_ns, st.st_size)
    if p.timecyc_data_sig != sig:
        p.timecyc_data = timecyc.to_store(timecyc.read_text(path))
        p.timecyc_data_sig = sig


def choose(scene, choice):
    """Make an entry drive the scene's sky. The file is checked first: a file for another game / version,
    a broken or missing one raises ValueError with a clear message and nothing changes."""
    p = scene.gta_studio
    if choice == STOCK:
        if not stock_known():
            raise ValueError("The stock numbers aren't known yet: add an unmodified timecyc.dat once")
        with open(_stock_path(), "r", encoding="utf-8") as f:
            data = f.read()
        timecyc.from_store(data)
        p.timecyc_data, p.timecyc_data_sig = data, "STOCK"
    else:
        path = _file_of(choice)
        if not path or not os.path.isfile(path):
            raise ValueError("File not found: %s" % (path or choice))
        try:
            timecyc.load(path)
        except timecyc.TimecycError as e:
            raise ValueError("%s: %s. Kept %s" % (os.path.basename(path), e, p.timecyc_label or "the current one"))
        _store(p, path)
    p.timecyc_label = label_of(choice)
    p.timecyc_choice = choice                     # its update applies the sky


def refresh_label(scene):
    p = scene.gta_studio
    lab = label_of(p.timecyc_choice) if p.timecyc_choice else label_of(GAME) + \
        (" + Preferences' file" if _prefs() is not None and _prefs().timecyc_path else "")
    if p.timecyc_label != lab:
        p.timecyc_label = lab


# ----------------------------------------------------------------------------- the 24-hour strip
def update_swatches(scene, tc):
    """The sky colour at each hour for the scene's weather (tc: the Timecyc in use, or None)."""
    p = scene.gta_studio
    key = "%s|%s|%s" % (p.timecyc_choice, p.timecyc_data_sig, p.weather)
    if tc is None or p.swatch_key == key:
        return
    cols = [timecyc.sky_colour(tc.sample(p.weather, float(h))) for h in range(24)]
    p.swatches.red = [c[0] for c in cols]
    p.swatches.green = [c[1] for c in cols]
    p.swatches.blue = [c[2] for c in cols]
    p.swatch_key = key
