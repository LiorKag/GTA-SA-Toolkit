# GTA SA Toolkit - weapon models (default.ide 'weap' section, models in gta3.img)
from . import mapimport, samp

CATEGORIES = (
    ('ALL', "All", ""),
    ('MELEE', "Melee", "Fists, bats, knives, katana, chainsaw..."),
    ('GIFT', "Gifts", "Flowers, cane, dildos..."),
    ('THROWN', "Thrown", "Grenades, tear gas, molotov, satchel"),
    ('HANDGUN', "Handguns", "Colt 45, silenced 9mm, Desert Eagle"),
    ('SHOTGUN', "Shotguns", ""),
    ('SMG', "SMGs", "Uzi, MP5, Tec-9"),
    ('ASSAULT', "Assault Rifles", "AK-47, M4"),
    ('RIFLE', "Rifles", "Country rifle, sniper"),
    ('HEAVY', "Heavy", "RPG, heat-seeker, flamethrower, minigun"),
    ('EQUIP', "Equipment", "Camera, spray can, extinguisher, goggles, parachute..."),
    ('OTHER', "Other", ""),
)

# model id -> (SA-MP / game weapon id, display name, category)
WEAPONS = {
    331: (1, "Brass Knuckles", 'MELEE'), 333: (2, "Golf Club", 'MELEE'), 334: (3, "Nightstick", 'MELEE'),
    335: (4, "Knife", 'MELEE'), 336: (5, "Baseball Bat", 'MELEE'), 337: (6, "Shovel", 'MELEE'),
    338: (7, "Pool Cue", 'MELEE'), 339: (8, "Katana", 'MELEE'), 341: (9, "Chainsaw", 'MELEE'),
    321: (10, "Purple Dildo", 'GIFT'), 322: (11, "Dildo", 'GIFT'), 323: (12, "Vibrator", 'GIFT'),
    324: (13, "Silver Vibrator", 'GIFT'), 325: (14, "Flowers", 'GIFT'), 326: (15, "Cane", 'GIFT'),
    342: (16, "Grenade", 'THROWN'), 343: (17, "Tear Gas", 'THROWN'), 344: (18, "Molotov Cocktail", 'THROWN'),
    346: (22, "Colt 45", 'HANDGUN'), 347: (23, "Silenced 9mm", 'HANDGUN'), 348: (24, "Desert Eagle", 'HANDGUN'),
    349: (25, "Shotgun", 'SHOTGUN'), 350: (26, "Sawnoff Shotgun", 'SHOTGUN'), 351: (27, "Combat Shotgun", 'SHOTGUN'),
    352: (28, "Micro Uzi", 'SMG'), 353: (29, "MP5", 'SMG'), 372: (32, "Tec-9", 'SMG'),
    355: (30, "AK-47", 'ASSAULT'), 356: (31, "M4", 'ASSAULT'),
    357: (33, "Country Rifle", 'RIFLE'), 358: (34, "Sniper Rifle", 'RIFLE'),
    359: (35, "Rocket Launcher", 'HEAVY'), 360: (36, "Heat-Seeking Rocket Launcher", 'HEAVY'),
    361: (37, "Flamethrower", 'HEAVY'), 362: (38, "Minigun", 'HEAVY'),
    363: (39, "Satchel Charge", 'THROWN'), 364: (40, "Detonator", 'EQUIP'), 365: (41, "Spray Can", 'EQUIP'),
    366: (42, "Fire Extinguisher", 'EQUIP'), 367: (43, "Camera", 'EQUIP'), 368: (44, "Night Vision Goggles", 'EQUIP'),
    369: (45, "Thermal Goggles", 'EQUIP'), 371: (46, "Parachute", 'EQUIP'),
    330: (-1, "Cellphone", 'EQUIP'), 354: (-1, "Flare", 'THROWN'), 345: (-1, "Missile", 'HEAVY'),
    327: (-1, "Gift Box (small)", 'GIFT'), 328: (-1, "Gift Box (big)", 'GIFT'),
}
NAMES_BY_MODEL = {"jetpack": ("Jetpack", 'EQUIP'), "armour": ("Body Armour", 'EQUIP')}


def _hide_flashes(coll):
    """Weapon DFFs contain a muzzle-flash plane ('gunflash') that the game only shows when firing."""
    for ob in list(coll.all_objects):
        n = ob.name.lower()
        if "gunflash" in n or "muzzle" in n:
            ob.hide_viewport = ob.hide_render = True


def list_weapons(game):
    """[(model_id, model_name, display_name, category, weapon_id)] for every 'weap' entry found."""
    out = []
    for oid, od in game.objects.items():
        if od.kind != 'weap' and oid not in WEAPONS:
            continue
        if not game.imgs.find(od.model + ".dff"):
            continue
        wid, disp, cat = WEAPONS.get(oid, (-1,) + NAMES_BY_MODEL.get(od.model.lower(), (od.model, 'OTHER')))
        out.append((oid, od.model, disp, cat, wid))
    order = [c[0] for c in CATEGORIES]
    out.sort(key=lambda w: (order.index(w[3]) if w[3] in order else 99, w[4] if w[4] >= 0 else 99, w[0]))
    return out


def import_weapon(context, game, model_id, location=None):
    od = game.objects[int(model_id)]
    coll = mapimport.import_model_from_img(context, game, od.model, True, True)
    _hide_flashes(coll)
    for ob in list(coll.objects):
        if ob.parent is None:
            ob["gta_weapon"] = int(model_id)
            if location is not None:
                ob.location = ob.location + location
    return coll


def give_weapon(context, game, arm, model_id, hand="RIGHT"):
    """Attach a weapon to the ped's hand (weapon models have their origin at the grip)."""
    bone = 6 if hand == "RIGHT" else 5          # SA-MP bone ids: 6 right hand, 5 left hand
    coll, b = samp.attach_toy(context, game, arm, int(model_id), bone)
    _hide_flashes(coll)
    for ob in list(coll.objects):
        if ob.parent == arm:
            ob["gta_weapon"] = int(model_id)
    return coll, b
