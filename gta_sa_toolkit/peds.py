# GTA SA Toolkit - the ped list: every ped in peds.ide / SAMP.ide by type, plus the story characters
# (loose DFFs in gta3.img that the game loads into its special01-10 slots; they have no IDE entry).
# The game has no display names for peds (american.gxt has none), so the list shows model names.

# (id, label, description, peds.ide types) - enum numbers are the tuple order
CATEGORIES = (
    ('ALL', "All", "Every ped", ()),
    ('CIV', "Civilians", "Street peds", ("CIVMALE", "CIVFEMALE", "PROSTITUTE")),
    ('GANG', "Gangs", "Ballas, Grove Street, Vagos, Rifa, Da Nang, Mafia, Triads, Aztecas",
     tuple("GANG%d" % i for i in range(1, 11))),
    ('CRIM', "Criminals", "Street criminals", ("CRIMINAL",)),
    ('COP', "Cops & Army", "Police, sheriffs, SWAT, FBI, army", ("COP",)),
    ('SERVICE', "Medics & Firemen", "Paramedics and firefighters", ("MEDIC", "FIREMAN")),
    ('STORY', "Story Characters", "CJ's friends and enemies (Sweet, Cesar, Tenpenny...)", ()),
    ('MOD', "Mods", "Custom / modded peds from your Mod Folders (Preferences)", ()),
)

TYPE_LABELS = {
    "CIVMALE": "Civilian", "CIVFEMALE": "Civilian", "PROSTITUTE": "Civilian", "CRIMINAL": "Criminal",
    "COP": "Cop", "MEDIC": "Medic", "FIREMAN": "Fireman", "GANG1": "Ballas", "GANG2": "Grove Street",
    "GANG3": "Vagos", "GANG4": "Rifa", "GANG5": "Da Nang", "GANG6": "Mafia", "GANG7": "Triads",
    "GANG8": "Aztecas", "GANG9": "Russian Mafia", "PLAYER1": "Player",
}

# story characters and girlfriends (only those actually in the IMGs are listed; tests check each one is
# a skinned ped with formats.dffprobe - bloodrb (a car) and stalks were wrongly here in earlier versions)
STORY = (
    "andre", "bb", "bbthin", "cat", "cdeput", "cesar", "claude", "copgrl1", "copgrl2", "crogrl1",
    "crogrl2", "dwayne", "emmet", "forelli", "gangrl1", "gangrl2", "gungrl1", "gungrl2", "hern", "janitor",
    "jethro", "jizzy", "kendl", "maccer", "maddogg", "mecgrl1", "mecgrl2", "mediatr", "nurgrl1", "nurgrl2",
    "ogloc", "paul", "poolguy", "psycho", "pulaski", "rose", "ryder", "ryder2", "ryder3", "sindaco", "smoke",
    "smokev", "suzie", "sweet", "tbone", "tenpen", "torino", "truth", "wuzimu", "zero",
)


def category_of(ped_type):
    for cid, _l, _d, types in CATEGORIES:
        if ped_type in types:
            return cid
    return 'CIV' if ped_type.startswith("CIV") else 'ALL'


def list_peds(game):
    """[(model, id or -1, category id, type label)] sorted by model; ids 0 (player) and the special slots
    are left out. Story characters come from the IMG listing."""
    rows = []
    for oid, ptype in game.ped_types.items():
        od = game.objects.get(oid)
        if od is None or oid == 0 or od.model.lower().startswith("special"):
            continue
        rows.append((od.model, oid, category_of(ptype), TYPE_LABELS.get(ptype, ptype.title())))
    have = {n[:-4].lower() for n in game.imgs.names_with_ext(".dff")} if game.imgs else set()
    rows += [(m, -1, 'STORY', "Story") for m in STORY if m in have and m not in game.by_name]
    rows.sort(key=lambda r: r[0].lower())
    return rows
