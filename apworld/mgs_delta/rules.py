"""Access rules.

Sections are visited in story order. Entering a section needs its unlock (if it has one) and the previous section
reachable. An unlock is one item ("unlock": "<item>", a Clearance: Cave Exit or Sokrovenno Exit) or a counted item
("unlock_count": {"item": "<item>", "count": n}: n copies, the 4 C3 Charges for the fuel tanks before Volgin).
Boss sections additionally need a way to fight: any firearm, or an explosive/fire weapon. So does every
"Defeat <boss>" and "Defeat <boss> Non-Lethally" location.
The Revival Pill is needed to enter the section after The Sorrow's (and by the Sorrow and the checks after the cell).
Key A/B/C are not logic: the three key doors are gated by the story only (verified in game), the keys stay vanilla.
Finishing the game needs every section (so the 4 C3 Charges too).
"""
from .items import DISGUISE, FIGHTING_GEAR, FIREARMS, REVIVAL_PILL, SECTIONS
from .locations import LOCATION_AREA, LOCATION_INFO, location_section, suppressor_guns, suppressor_guns_needed
from .options import NONLETHAL_WEAPONS, Difficulty

SECTION_BY_ID = {s["id"]: s for s in SECTIONS}

# --- Revival Pill: Snake dies at the end of The Sorrow's river (s141) and the game over screen needs the pill to go on.
SORROW_SECTION = "escape"                    # section of s141; the sections after it need the pill to be entered
CAPTURE_SECTION = "escape"                   # starts with the capture: Raikov's disguise must be worn to get there
SHAGOHOD_SECTION = "shagohod"                # shagohod_rpg random: the RPG-7 is needed to go there
RPG = "RPG-7"
# Areas only reachable after The Sorrow: the Sorrow, Tikhogornyj, and the Groznyj Grad Main Wing (s121), whose gate stays
# locked on the first infiltration and opens by story on the return (seen in game, 2026-10-17)
POST_SORROW_AREAS = {"s141", "s151", "s152", "s121"}


def can_fight(state, player):
    """Whether the player has any firearm or explosive/fire weapon."""
    return state.has_any(FIGHTING_GEAR, player)


# The single-item chapter gates can be switched off in the YAML: then the door is open and the item is not in the pool.
GATE_OPTIONS = {"Cave Exit": "cave_exit_gate", "Sokrovenno Exit": "sokrovenno_exit_gate"}


def gate_enabled(item, options):
    """False if a chapter gate's item is switched off in the YAML (options None: the gate counts as on)."""
    option = GATE_OPTIONS.get(item)
    return not option or options is None or bool(getattr(options, option, True))


def section_requirement(section, options):
    """(item, count) needed to enter a section, or None."""
    if section.get("unlock"):
        return (section["unlock"], 1) if gate_enabled(section["unlock"], options) else None
    counted = section.get("unlock_count")
    if counted and counted["count"] > 0:
        return counted["item"], counted["count"]
    return None


def area_items(area, options):
    """Items a location in this area needs on top of its section."""
    return {REVIVAL_PILL} if area in POST_SORROW_AREAS else set()


def section_area_items(section_id, options):
    """Items needed by some area of a section: required by checks that are only known to be "somewhere in" it
    (story completion, collection, no-alert, animals), since any of its items may sit behind the gated area."""
    section = SECTION_BY_ID[section_id]
    needed = set()
    for area in section["areas"]:
        needed |= area_items(area, options)
    return needed


def entry_items(section, options):
    """Items needed to enter a section besides its unlock: the Revival Pill after The Sorrow's section; Raikov's
    disguise for the capture (raikov_disguise randomized); the RPG-7 for the Shagohod (shagohod_rpg random)."""
    order = [s["id"] for s in SECTIONS]
    position = order.index(section["id"])
    needed = {REVIVAL_PILL} if position > 0 and order[position - 1] == SORROW_SECTION else set()
    if section["id"] == CAPTURE_SECTION and getattr(getattr(options, "raikov_disguise", None), "value", 0):
        needed |= set(DISGUISE)
    if section["id"] == SHAGOHOD_SECTION and getattr(getattr(options, "shagohod_rpg", None), "value", 0):
        needed.add(RPG)
    return needed


def location_items(name, options):
    """Items a location needs beyond its section's entry: [] for most."""
    info = LOCATION_INFO[name]
    kind = info["kind"]
    if kind in ("boss", "nonlethal"):
        return {REVIVAL_PILL} if info["extra"]["boss"] == "The Sorrow" else set()
    if name in LOCATION_AREA:
        return set(area_items(LOCATION_AREA[name], options))
    if kind == "cqc":
        return set()
    return section_area_items(location_section(name, options), options)


def section_rule(section, world):
    """Entrance rule of a section: its unlock, the entry items, and a way to fight if it has a boss."""
    player = world.player
    need = section_requirement(section, world.options)
    boss = section.get("boss")
    extra = entry_items(section, world.options)

    def rule(state):
        if need and not state.has(need[0], player, need[1]):
            return False
        if extra and not state.has_all(extra, player):
            return False
        return not boss or can_fight(state, player)
    return rule


# Collection boxes the game only places after a non-lethal boss win: The End's Mosin-Nagant (s065a script:
# $var[228][16][3] == 1, the same flag as his reward), so they need what "Defeat <boss> Non-Lethally" needs.
NONLETHAL_REWARD_BOXES = {"Sokrovenno North: Mosin-Nagant"}


def location_rule(name, world):
    """Access rule of a location (None if its section's entry is all it needs)."""
    player = world.player
    items = location_items(name, world.options)
    kind = LOCATION_INFO[name]["kind"]
    nonlethal = kind == "nonlethal" or name in NONLETHAL_REWARD_BOXES
    fight = kind == "boss" or nonlethal
    # a stamina knock-out needs a non-lethal weapon (the EZ Gun is the game's own on Very Easy)
    firearm = kind == "special_camo" and LOCATION_INFO[name]["extra"].get("firearm")   # a hold-up needs a gun
    stun = nonlethal and getattr(world.options, "difficulty", 2) != Difficulty.option_very_easy
    # collection checks that need suppressors: as many guns (a suppressor box only appears with its gun)
    guns = suppressor_guns_needed(name, world.options)
    if not items and not fight and not guns and not firearm:
        return None
    gun_list = suppressor_guns()
    return lambda state: (state.has_all(items, player) and (not fight or can_fight(state, player))
                          and (not stun or state.has_any(NONLETHAL_WEAPONS, player))
                          and (not guns or state.has_from_list(gun_list, player, guns))
                          and (not firearm or state.has_any(FIREARMS, player)))


def goal_rule(world):
    """Completion condition: the final location (behind every section, the C3 Charges included)."""
    player = world.player
    return lambda state: state.has("Victory", player)
