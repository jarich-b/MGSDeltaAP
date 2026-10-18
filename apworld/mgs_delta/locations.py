"""Location tables. Every location is (name, id, section id, kind, extra); which ones exist depends on options."""
from BaseClasses import Location

from .data_loader import load
from .items import GAME, SECTIONS
from .options import GUN_BOUND_PICKUPS, PICKABLE, ChecksMode

LOCATION_ID_BASE = 7_720_000

BOX_TABLE = load("locations.json")       # item boxes: client data only (matching pickups, tracker markers)
BOX_ID_BASE = 1000                       # the client's own box ids (BOX_ID_BASE + index); not Archipelago ids
BOX_IDS = {box["name"]: BOX_ID_BASE + i for i, box in enumerate(BOX_TABLE)}
BOSSES = load("bosses.json")             # [{"boss", "location", "section", "story_at_least", "flag"?}]
SECTION_BY_ID = {s["id"]: s for s in SECTIONS}
SECTION_OF_AREA = {a: s["id"] for s in SECTIONS for a in s["areas"]}
SECTION_ORDER = [s["id"] for s in SECTIONS]

KEROTANS = load("kerotans.json")           # [{"index", "area", "stages", "name"}]: the 64 Kerotans
GAKOS = load("gakos.json")                 # [{"index", "area", "stages", "name"}]: the 64 Gako ducks
ANIMALS = load("animals.json")             # [{"kind", "name", "stages"}]: the 28 capturable species
ACQ_CATEGORIES = {"weapon": "New Weapon", "equipment": "New Equipment", "camo": "New Camo", "medical": "New Medicine"}
# Names of the same checks when the type is bundled (a bundle option on); each set has its own stable ids.
ACQ_BUNDLE_CATEGORIES = {"weapon": "Weapon Bundle", "equipment": "Item Bundle", "camo": "Camo Bundle",
                         "medical": "Medicine Bundle"}
GOAL_LOCATION = "Win the Duel with Ocelot"


def nonlethal_location(boss):
    """Name of the non-lethal check of a boss entry (data/bosses.json)."""
    return f"Defeat {boss['boss']} Non-Lethally"


def animal_location(animal):
    """Name of the capture check of an animal entry (data/animals.json)."""
    return f"Captured {animal['name']}"


def suppressors_counted(options):
    """Whether suppressor pickups count as equipment (option suppressor_checks; None = the full location table)."""
    return options is None or bool(getattr(options, "suppressor_checks", False))


def _acquisition_sections(category, options=None):
    """Section in which the k-th new item of a category is guaranteed reachable (cumulative availability).
    Suppressors (equipment) come last: their box only appears with the gun, so they are never needed before every
    other piece (see suppressor_guns_needed). Left out unless suppressors_counted."""
    section_ids, gun_bound = [], []
    for section in SECTIONS:
        for item_id in section.get(f"new_{category}", []):
            if category == "equipment" and item_id in GUN_BOUND_PICKUPS:
                gun_bound.append(section["id"])
            else:
                section_ids.append(section["id"])
    if suppressors_counted(options):
        last = section_ids[-1] if section_ids else SECTION_ORDER[0]
        section_ids += [max(last, sid, key=SECTION_ORDER.index) for sid in gun_bound]
    return section_ids


def counted_items(options, category):
    """Game ids of the items that count for a collection type (the client counts exactly these)."""
    return [item_id for item_id in PICKABLE[category]
            if category != "equipment" or item_id not in GUN_BOUND_PICKUPS or suppressors_counted(options)]


def suppressor_guns():
    """Guns whose suppressor lies in the world (one of them per suppressor pickup a check needs)."""
    return [GUN_BOUND_PICKUPS[item_id] for item_id in PICKABLE["equipment"] if item_id in GUN_BOUND_PICKUPS]


def suppressor_guns_needed(name, options):
    """How many of suppressor_guns() a collection check needs: the suppressors it can't do without."""
    info = LOCATION_INFO[name]
    if not info["kind"].startswith("acq_equipment") or not suppressors_counted(options):
        return 0
    counted, size = acq_plan(options, "equipment")
    needed_items = min(info["extra"] * size, counted)
    return max(0, needed_items - len(_acquisition_sections("equipment", options)) + len(suppressor_guns()))


def acq_plan(options, category):
    """(N, X) of a collection type: N = items that count (0 = off), X = new items per check (1 without bundles).
    There are ceil(N / X) checks "New <Type> k"; check k is earned at the min(k * X, N)-th new item."""
    total = len(_acquisition_sections(category, options))
    mode = getattr(options, f"{category}_checks").value
    if mode == ChecksMode.option_off:
        return 0, 1
    counted = total if mode == ChecksMode.option_all else min(total, getattr(options, f"{category}_count").value)
    size = getattr(options, f"{category}_bundle_size").value if getattr(options, f"{category}_bundle") else 1
    return counted, size


def acq_check_count(options, category):
    """Number of "New <type> k" / bundle checks of a collection type (ceil(N / X))."""
    counted, size = acq_plan(options, category)
    return -(-counted // size)


def _acq_category(kind):
    """Collection type of an "acq_<type>[_bundle]" location kind."""
    return kind[4:].removesuffix("_bundle")


def location_section(name, options):
    """Section a location is placed in. Collection checks depend on the bundle size: check k needs the
    min(k * X, N)-th new item to be reachable."""
    info = LOCATION_INFO[name]
    if info["kind"].startswith("acq_"):
        category = _acq_category(info["kind"])
        counted, size = acq_plan(options, category)
        return _acquisition_sections(category, options)[min(info["extra"] * size, counted) - 1]
    return info["section"]


NO_ALERT_TOTAL = 30        # = the no_alert_areas option maximum
CQC_TOTAL = 30             # = the cqc_takedowns option maximum
ARENA_AREAS = {area for boss in BOSSES for area in boss.get("arena", [])}   # forced phases: never a no-alert area
GUARDED_AREAS = load("guarded_areas.json")["areas"]   # the areas with guards: only these are no-alert areas


def _no_alert_sections():
    """Section in which the k-th no-alert area can be reached: the k-th (story order) area with guards
    (data/guarded_areas.json) of the cumulative section list."""
    section_ids = []
    for section in SECTIONS:
        guarded = sum(1 for area in section["areas"] if area in GUARDED_AREAS and area not in ARENA_AREAS)
        section_ids += [section["id"]] * guarded
    assert len(section_ids) >= NO_ALERT_TOTAL, "fewer guarded areas than No-Alert Area checks"
    return section_ids


NO_ALERT_SECTIONS = _no_alert_sections()

# Location id offsets (from LOCATION_ID_BASE) of the collection checks, per type; at most 30 checks per type.
ACQ_ID_BASE = {"weapon": 100, "equipment": 200, "camo": 300, "medical": 1700}
ACQ_BUNDLE_ID_BASE = {"weapon": 900, "equipment": 930, "camo": 960, "medical": 1730}

# name -> (id offset, section id, kind, extra)
_TABLE = {}


def _add(name, offset, section_id, kind, extra=None):
    _TABLE[name] = (offset, section_id, kind, extra)


for index, section in enumerate(SECTIONS):
    if section["id"] != "finale":
        _add(f"Complete {section['name']}", index, section["id"], "story")
for acq_type, label in ACQ_CATEGORIES.items():
    for k, section_id in enumerate(_acquisition_sections(acq_type), start=1):
        _add(f"{label} {k}", ACQ_ID_BASE[acq_type] + k, section_id, f"acq_{acq_type}", k)
for acq_type, label in ACQ_BUNDLE_CATEGORIES.items():      # ids 901.. / 931.. / 961.. / 1731..
    for k, section_id in enumerate(_acquisition_sections(acq_type), start=1):
        _add(f"{label} {k}", ACQ_BUNDLE_ID_BASE[acq_type] + k, section_id, f"acq_{acq_type}_bundle", k)
for kerotan in KEROTANS:                       # id 401..464 = index + 1 (the client sets the check from the $var bit)
    _add(kerotan["name"], 401 + kerotan["index"], SECTION_OF_AREA[kerotan["area"]], "kerotan", kerotan["index"] + 1)
for gako in GAKOS:                             # ids 1500..1563 = 1500 + index (the client counts ducks broken per run)
    _add(gako["name"], 1500 + gako["index"], SECTION_OF_AREA[gako["area"]], "gako", gako["index"] + 1)
for index, animal in enumerate(ANIMALS):       # ids 1600..1627; reachable from the first section that has the species
    areas = [stage[:4] for stage in animal["stages"] if stage[:4] in SECTION_OF_AREA]
    first_section = min((SECTION_OF_AREA[area] for area in areas), key=SECTION_ORDER.index)
    _add(animal_location(animal), 1600 + index, first_section, "animal", animal["kind"])
for k in range(1, NO_ALERT_TOTAL + 1):
    _add(f"No-Alert Area {k}", 600 + k, NO_ALERT_SECTIONS[min(k, len(NO_ALERT_SECTIONS)) - 1], "no_alert", k)
for k in range(1, CQC_TOTAL + 1):
    _add(f"CQC Takedown {k}", 700 + k, "dremuchij", "cqc", k)
for index, boss in enumerate(BOSSES, start=1):    # always included; the client awards them from story progress
    _add(boss["location"], 800 + index, boss["section"], "boss", boss)

for boss in BOSSES:                           # ids 1800 + k (k = the boss's non-lethal flag index in the script array)
    if "nonlethal" in boss:
        _add(nonlethal_location(boss), 1800 + boss["nonlethal"], boss["section"], "nonlethal", boss)

# Optional camo checks the game hands out without a box: The End drops the Moss Camo when held up and shaken, the
# Spirit Camo comes at the end of The Sorrow's river. The client hides an owned copy in their areas (the game gives
# neither while one is owned) and sends the check when the camo comes. ids 1901.. (game/session.py SPECIAL_CAMOS).
SPECIAL_CAMOS = [
    {"location": "Hold Up The End (Moss Camo)", "item": 70, "section": "sokrovenno", "option": "moss_camo_check",
     "firearm": True, "boss": "The End", "tracker": "moss"},
    {"location": "The Sorrow: Spirit Camo", "item": 72, "section": "escape", "option": "spirit_camo_check",
     "boss": "The Sorrow", "tracker": "spirit"},
]
for index, camo in enumerate(SPECIAL_CAMOS, start=1):
    _add(camo["location"], 1900 + index, camo["section"], "special_camo", camo)

# Area (s###) of the locations that sit in one: kerotans and gako ducks.
LOCATION_AREA = {kerotan["name"]: kerotan["area"] for kerotan in KEROTANS}
LOCATION_AREA.update({gako["name"]: gako["area"] for gako in GAKOS})

LOCATION_NAME_TO_ID = {name: LOCATION_ID_BASE + offset for name, (offset, _, _, _) in _TABLE.items()}
LOCATION_INFO = {name: {"section": section_id, "kind": kind, "extra": extra}
                 for name, (_, section_id, kind, extra) in _TABLE.items()}

LOCATION_GROUPS = {
    "Story": {n for n, i in LOCATION_INFO.items() if i["kind"] == "story"},
    "Bosses": {n for n, i in LOCATION_INFO.items() if i["kind"] == "boss"},
    "Non-Lethal Bosses": {n for n, i in LOCATION_INFO.items() if i["kind"] == "nonlethal"},
    "Kerotans": {n for n, i in LOCATION_INFO.items() if i["kind"] == "kerotan"},
    "Gako Ducks": {n for n, i in LOCATION_INFO.items() if i["kind"] == "gako"},
    "Animals": {n for n, i in LOCATION_INFO.items() if i["kind"] == "animal"},
    "Special Camos": {n for n, i in LOCATION_INFO.items() if i["kind"] == "special_camo"},
}


def included(name, options):
    """Whether a location exists with these options."""
    info = LOCATION_INFO[name]
    kind, number = info["kind"], info["extra"]
    if kind in ("story", "boss"):
        return True
    if kind == "nonlethal":
        return bool(options.nonlethal_boss)
    if kind.startswith("acq_"):
        category = _acq_category(kind)
        bundled = kind.endswith("_bundle")
        return (bundled == bool(getattr(options, f"{category}_bundle"))
                and number <= acq_check_count(options, category))
    if kind == "kerotan":
        return bool(options.kerotan_checks)
    if kind == "gako":
        return bool(options.gako_checks)
    if kind == "animal":
        return bool(options.animal_checks)
    if kind == "no_alert":
        return number <= options.no_alert_areas
    if kind == "cqc":
        return number <= options.cqc_takedowns
    if kind == "special_camo":
        return bool(getattr(options, number["option"]))
    return False


class MGSDeltaLocation(Location):
    game = GAME
