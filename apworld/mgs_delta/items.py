"""Item tables: game items from data/items.json, clearances, traps, benefits, and their AP ids and classifications."""
from BaseClasses import Item, ItemClassification

from .data_loader import load
from .options import BENEFITS, EMPTY_MAGAZINE_ID, MEDICAL_IDS, NEVER_OFFERED, TRAPS

GAME = "Metal Gear Solid Delta"
ITEM_ID_BASE = 7_710_000

ITEM_TABLE = load("items.json")          # game weapons/items/camos: {"kind", "id", "name", "group"?}
SECTIONS = load("sections.json")
ITEM_BY_KEY = {(entry["kind"], entry["id"]): entry for entry in ITEM_TABLE}
ITEM_BY_NAME = {entry["name"]: entry for entry in ITEM_TABLE}

# The Volgin / Shagohod section needs 4 C3 Charges: the 4 fuel tanks in the Main Wing take one charge each and the
# story only goes on once all are planted (C3 can only be planted there). The client gives no C3 until all 4 are
# received, then 4 minus the ones planted; the game's own C3 hand-out is removed.
C3_CHARGE = "C3 Charge"
C3_CHARGES = 4
# Raikov's disguise, worn for the Groznyj Grad infiltration (raikov_disguise randomized: pool items, logic before the capture)
DISGUISE = ["Officer Camo", "Mask Face Paint"]
CLEARANCES = [s["unlock"] for s in SECTIONS if s.get("unlock")]   # single-item unlocks; counted ones use their own item
CAMO_GROUPS = ("camo", "facepaint")
FIRST_EQUIPMENT_ID = 5          # EItemName ids below are Life Medicine, Pentazemin and the two pills (see PILL_IDS)
PILL_IDS = (3, 4)               # Fake Death Pill, Revival Pill

# Weapons usable to fight bosses (any firearm or explosive); capability logic uses "any of".
FIREARMS = {"Mk22", "M1911A1", "SAA", "Patriot", "Scorpion", "XM16E1", "AK-47", "M63", "M37", "SVD",
            "Mosin-Nagant", "RPG-7"}
EXPLOSIVE_OR_FIRE = {"Grenade", "WP Grenade", "TNT", "Claymore", "Torch"}     # (C3: only the fuel tanks take it)
FIGHTING_GEAR = FIREARMS | EXPLOSIVE_OR_FIRE
# Pool tiers (in priority order): fighting gear, equipment, other weapons (melee, throwables, ...), camos / face paints.
# EQUIPMENT_ITEMS is everything the pool can contain from the "item" table (equipment, medical items, pills).
EQUIPMENT_ITEMS = [entry["name"] for entry in ITEM_TABLE if entry.get("group") == "item"
                   and (entry["id"] >= FIRST_EQUIPMENT_ID or entry["id"] in PILL_IDS)
                   and entry["name"] not in NEVER_OFFERED]
REVIVAL_PILL = "Revival Pill"       # required to continue after The Sorrow (Snake dies at the end of the river)
FAKE_DEATH_PILL = "Fake Death Pill"
MEDICAL_ITEMS = [entry["name"] for entry in ITEM_TABLE
                 if entry.get("group") == "item" and entry["id"] in MEDICAL_IDS]
# Equipment of the normal pool tier: no pills (see below), no medical items.
PLAIN_EQUIPMENT = [n for n in EQUIPMENT_ITEMS if n not in MEDICAL_ITEMS + [REVIVAL_PILL, FAKE_DEATH_PILL]]
OTHER_WEAPONS = [entry["name"] for entry in ITEM_TABLE if entry["kind"] == "weapon" and entry["id"] != EMPTY_MAGAZINE_ID
                 and entry["name"] not in FIGHTING_GEAR | NEVER_OFFERED]
CAMO_ITEMS = [entry["name"] for entry in ITEM_TABLE if entry.get("group") in CAMO_GROUPS]


def game_item_id(entry):
    """Stable AP id for a game item: weapons 1..30, items/camos 100 + EItemName id."""
    return ITEM_ID_BASE + (entry["id"] if entry["kind"] == "weapon" else 100 + entry["id"])


ITEM_NAME_TO_ID = {e["name"]: game_item_id(e) for e in ITEM_TABLE}
ITEM_NAME_TO_ID.update({name: ITEM_ID_BASE + 600 + i for i, name in enumerate(CLEARANCES)})
ITEM_NAME_TO_ID.update({f"Trap: {name}": ITEM_ID_BASE + 700 + i for i, name in enumerate(TRAPS)})
ITEM_NAME_TO_ID.update({name: ITEM_ID_BASE + 800 + i for i, name in enumerate(BENEFITS)})
ITEM_NAME_TO_ID[C3_CHARGE] = ITEM_ID_BASE + 899

TRAP_ITEMS = {f"Trap: {name}" for name in TRAPS}
BENEFIT_ITEMS = set(BENEFITS)

ITEM_GROUPS = {
    "Clearances": set(CLEARANCES),
    "Traps": TRAP_ITEMS,
    "Firearms": FIREARMS,
    "Explosives": EXPLOSIVE_OR_FIRE,
    "Camos": set(CAMO_ITEMS),
    "Medical": set(MEDICAL_ITEMS),
    "Pills": {REVIVAL_PILL},
}


def classification(name):
    """AP classification of an item name: logic items progression, traps, camos and benefits filler, rest useful."""
    if name in CLEARANCES or name in (C3_CHARGE, REVIVAL_PILL) or name in FIGHTING_GEAR:
        return ItemClassification.progression
    if name in TRAP_ITEMS:
        return ItemClassification.trap
    if name in BENEFIT_ITEMS or ITEM_BY_NAME.get(name, {}).get("group") in CAMO_GROUPS:
        return ItemClassification.filler
    return ItemClassification.useful


class MGSDeltaItem(Item):
    """An item of this world."""
    game = GAME
