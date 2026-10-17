"""Options of the world (YAML), plus the item/collection tables that bound their values."""
from dataclasses import dataclass

from Options import Choice, DeathLink, DefaultOnToggle, OptionGroup, OptionSet, PerGameCommonOptions, Range, Toggle

from .data_loader import load

TRAPS = ["Hunger", "Empty Magazine", "Alert", "Battery Drain", "Fashion", "Disarm", "Butterfingers", "Weather",
         "Snake Bite", "Injury", "Spoiled Food", "Area Reset"]
BENEFITS = ["Ammo Refill", "Grenade Refill", "Suppressor Recovery", "Food", "Life Refill", "Stamina Refill",
            "Battery Refill", "Cure Kit", "Infinity Face Paint (temporary)", "Stealth Camo (temporary)"]


class Difficulty(Choice):
    """The game difficulty you will play on. It changes what is in the item boxes. European Extreme has no Alert trap,
    since an alert ends the game there."""
    display_name = "Difficulty"
    option_very_easy = 0
    option_easy = 1
    option_normal = 2
    option_hard = 3
    option_extreme = 4
    option_european_extreme = 5
    default = 2


class Loadout(Choice):
    """What Snake starts with. empty: nothing. knife: the Survival Knife. pistol: the knife and the Mk22. vanilla: the
    game's normal starting gear. random_kit: one random weapon, one item and one utility item.
    Tip: the Survival Knife is needed to treat some wounds (The Pain's bees, The Fear's bolts, The End's darts).
    Without it, those wounds stay until it arrives."""
    display_name = "Starting Loadout"
    option_empty = 0
    option_knife = 1
    option_pistol = 2
    option_vanilla = 3
    option_random_kit = 4
    default = 0


class StartingCamo(Choice):
    """Snake's starting camo and face paint. vanilla: the game's normal ones. randomized: a random camo and face paint
    (never Infinity Face Paint), or sometimes a wearable item instead (AT Camo, a cap or a mask)."""
    display_name = "Starting Camo"
    option_vanilla = 0
    option_randomized = 1
    default = 0


ACQ_TYPES = ("weapon", "equipment", "camo")          # types with extra_* options and item pools of their own
COLLECTION_TYPES = ACQ_TYPES + ("medical",)          # every type with "new item" collection checks
EMPTY_MAGAZINE_ID = 24      # the empty magazine the game hands out (weapon id): never a check or an item
# Medical items (EItemName 1 Life Medicine, 2 Pentazemin, 36..47 Blood Serum ... Battery). They are only in the pool with
# medical_checks, since the vanilla boxes already supply them otherwise. Never extras (outside the equipment type).
MEDICAL_IDS = (1, 2, *range(36, 48))
# Suppressor boxes only appear while Snake owns the matching gun: suppressor id -> gun. The logic counts these pickups
# last, and a check that needs them needs as many of the guns (received, not picked up: a found gun is taken back).
GUN_BOUND_PICKUPS = {48: "M1911A1", 49: "Mk22", 50: "XM16E1"}
_ITEMS = load("items.json")
_SECTIONS = load("sections.json")


def acq_category(kind, iid, medical=False):
    """Collection type of a game item: weapon / equipment / camo, or None (ammo, medicine unless asked for, ...).
    Same rule as game/bridge.py. Medicine (MEDICAL_IDS) is "medical" when asked for."""
    if kind == "weapon":
        return None if iid == EMPTY_MAGAZINE_ID else "weapon"
    if kind == "item":
        if 51 <= iid <= 147:
            return "camo"
        if 5 <= iid <= 35 or 48 <= iid <= 50:
            return "equipment"
        if medical and iid in MEDICAL_IDS:
            return "medical"
    return None


# Items of each type the game places in the world (the union of the per-section "new_*" lists of sections.json,
# derived from the vanilla contents of the item boxes). The client counts exactly these.
PICKABLE = {acq_type: sorted({item_id for section in _SECTIONS for item_id in section.get(f"new_{acq_type}", [])})
            for acq_type in COLLECTION_TYPES}
# Boss rewards that only appear after a non-lethal (stamina) win (Hornet Stripe 68 The Pain, Spider 69 The Fear, Fire 71
# The Fury, Animal 80 Ocelot) are never collection camos: the boss has its own "Defeat <boss> Non-Lethally" check
# (option nonlethal_boss). The End's reward is the Mosin-Nagant box in Sokrovenno North (only placed after a non-lethal
# win; it stays a weapon check). His Moss Camo (70) is a hold-up drop and never a collection camo either.
# Never in the item pool or any check: the game hands the EZ Gun out itself on Very Easy (the client never strips it);
# the Bandana is a reward item that shows "NO USE" when granted by the client; Key A/B/C stay vanilla (the three key
# doors turned out to be gated by the story only, so the keys never matter; the client never strips or takes them);
# the Fake Death Pill is vanilla too (the game gives it in the cell, cut out with the fork); so is the Camera (its box
# in the cell has no respawn timer, so after the escape it is gone and only a first-visit pickup could count).
NEVER_OFFERED = {"EZ Gun", "Bandana", "Key A", "Key B", "Key C", "C3",     # (C3: the C3 Charges stand for it)
                 "Fake Death Pill", "Camera",
                 # unused camo slots: no collection or reward row in the game's tables
                 "Usmx Camo", "Bonsai Camo"}
# Sneaking DLC Pack items: only in the pool (and the random start) with dlc_items (granting them works without the pack
# installed, but they are paid content).
DLC_ITEMS = {"Battle Dress PW Camo", "Sneaking PW Camo", "Naked Woodland Camo", "Naked Beltlink Camo", "Gold Camo",
             "Glasses Face Paint", "Sunglasses Face Paint", "Croc Suit", "One-Eyed Croc Cap"}
# Items Snake wears (equipped item slot): with starting_camo randomized the start may roll one of them, worn at once.
WEARABLE_ITEMS = ["AT Camo", "Croc Suit", "Croc Cap", "One-Eyed Croc Cap", "Banana Cap", "Golden Banana Cap",
                  "Gako Mask", "Kerotan Mask"]
# Every game item of each type by name (for the extra_* options; includes reward-only camos).
ITEMS_OF_TYPE = {acq_type: sorted(entry["name"] for entry in _ITEMS
                                  if acq_category(entry["kind"], entry["id"]) == acq_type
                                  and entry["name"] not in NEVER_OFFERED) for acq_type in ACQ_TYPES}


class ChecksMode(Choice):
    """Shared base of the collection check modes."""
    option_off = 0
    option_all = 1
    option_count = 2


class WeaponChecks(ChecksMode):
    """Which weapons you find are checks. off: none. all: every weapon in the world (22). count: only the first Weapon
    Count different weapons you find. Weapons you find are taken back: you only keep the weapons Archipelago
    sends you."""
    display_name = "Weapon Checks"
    default = ChecksMode.option_count


class WeaponCount(Range):
    """With Weapon Checks set to count: how many different weapons are checks."""
    display_name = "Weapon Count"
    range_start = 1
    range_end = len(PICKABLE["weapon"])
    default = 10


class WeaponBundle(Toggle):
    """Group weapon checks: one check for every Weapon Bundle Size new weapons, instead of one per weapon. Example: 10
    weapons in bundles of 3 give 4 checks (at the 3rd, 6th, 9th and 10th weapon)."""
    display_name = "Weapon Bundles"


class WeaponBundleSize(Range):
    """New weapons per check when Weapon Bundles is on."""
    display_name = "Weapon Bundle Size"
    range_start = 2
    range_end = 20
    default = 5


class EquipmentChecks(ChecksMode):
    """Which equipment you find is a check (goggles, sensors, cardboard boxes and so on). off: none. all: every piece
    in the world. count: only the first Equipment Count different pieces. Equipment you find is taken back: you
    only keep what Archipelago sends you. Suppressors only count with Suppressor Checks on."""
    display_name = "Equipment Checks"
    default = ChecksMode.option_count


class EquipmentCount(Range):
    """With Equipment Checks set to count: how many different pieces of equipment are checks."""
    display_name = "Equipment Count"
    range_start = 1
    range_end = len(PICKABLE["equipment"])
    default = 5


class EquipmentBundle(Toggle):
    """Group equipment checks: one check ("Item Bundle") for every Equipment Bundle Size new pieces, instead of one per
    piece. Example: 10 pieces in bundles of 3 give 4 checks."""
    display_name = "Equipment Bundles"


class EquipmentBundleSize(Range):
    """New pieces of equipment per check when Equipment Bundles is on."""
    display_name = "Equipment Bundle Size"
    range_start = 2
    range_end = 20
    default = 5


class SuppressorChecks(Toggle):
    """Suppressors you pick up count as equipment checks. A suppressor box only appears while you own its gun, and you
    only own a gun once Archipelago sends it. So you often go back for them later with a codec warp. Logic
    expects them last."""
    display_name = "Suppressor Checks"


class CamoChecks(ChecksMode):
    """Which camos and face paints you find are checks. off: none. all: every one in the world (15). count: only the
    first Camo Count. Boss reward camos never count here."""
    display_name = "Camo Checks"
    default = ChecksMode.option_off


class CamoCount(Range):
    """With Camo Checks set to count: how many different camos and face paints are checks."""
    display_name = "Camo Count"
    range_start = 1
    range_end = len(PICKABLE["camo"])
    default = 5


class CamoBundle(Toggle):
    """Group camo checks: one check for every Camo Bundle Size new camos, instead of one per camo. Example: 10 camos in
    bundles of 3 give 4 checks."""
    display_name = "Camo Bundles"


class CamoBundleSize(Range):
    """New camos per check when Camo Bundles is on."""
    display_name = "Camo Bundle Size"
    range_start = 2
    range_end = 20
    default = 5


class MossCamoCheck(Toggle):
    """A check for holding up The End during his fight until he drops the Moss Camo. If you already have the Moss Camo,
    the client hides it during the fight and gives it back after."""
    display_name = "Moss Camo Check"


class SpiritCamoCheck(Toggle):
    """A check for the Spirit Camo, which EVA gives you after The Sorrow. If you already have it, the client hides it
    until then and gives it back after."""
    display_name = "Spirit Camo Check"


class NonlethalBoss(Toggle):
    """Experimental. Beating a boss without killing it (by draining its stamina) is an extra check: "Defeat <boss> Non-
    Lethally" for Ocelot, The Pain, The Fear, The End, The Fury and Volgin. Picking up the reward a boss leaves
    after a non-lethal win also sends it. Boss fights can be replayed with the codec warps.
    Warning: if you snipe The End early at Ponizovje, his fight is gone for the rest of the run."""
    display_name = "Non-Lethal Boss Checks"


# Weapons that knock a boss out (stamina): logic for "Defeat <boss> Non-Lethally". CQC also drains stamina but is not
# reliable for every boss; the EZ Gun is the game's own on Very Easy.
NONLETHAL_WEAPONS = ["Mk22", "Mosin-Nagant", "Stun Grenade"]


class NonlethalLogic(Choice):
    """With Non-Lethal Boss Checks: how you get a non-lethal weapon (Mk22, Mosin-Nagant or Stun Grenade). in_logic: the
    checks are only expected once you have one, so you may refight a boss later. early: one is placed in an
    early check. starting: you start with one. On Very Easy the game's own EZ Gun also counts."""
    display_name = "Non-Lethal Weapon Logic"
    option_in_logic = 0
    option_early = 1
    option_starting = 2
    default = 1


class MedicalChecks(ChecksMode):
    """Which medicine you find is a check. off: none, and you keep the medicine you find. all: every medical item in
    the world. count: only the first Medical Count. When on, medicine is in the item pool and you start without
    any."""
    display_name = "Medical Checks"
    default = ChecksMode.option_off


class MedicalCount(Range):
    """With Medical Checks set to count: how many different medical items are checks."""
    display_name = "Medical Count"
    range_start = 1
    range_end = len(PICKABLE["medical"])
    default = 5


class MedicalBundle(Toggle):
    """Group medical checks: one check for every Medical Bundle Size new items, instead of one per item. Example: 10
    items in bundles of 3 give 4 checks."""
    display_name = "Medical Bundles"


class MedicalBundleSize(Range):
    """New medical items per check when Medical Bundles is on."""
    display_name = "Medical Bundle Size"
    range_start = 2
    range_end = 11
    default = 3


class ExtraWeapon(OptionSet):
    """Weapons that are always in your item pool. With few checks some items don't fit in the pool; extras make sure
    these do. They are items you receive, not checks, and take the place of filler first. If there isn't enough
    room, generation fails. Default: the Survival Knife (it treats some wounds; skipped if you start with it)."""
    display_name = "Extra Weapons"
    valid_keys = ITEMS_OF_TYPE["weapon"]
    default = frozenset({"Survival Knife"})


class ExtraWeaponRandom(Range):
    """How many random weapons to add to your item pool, besides the ones already in it."""
    display_name = "Extra Random Weapons"
    range_start = 0
    range_end = 30
    default = 0


class ExtraEquipment(OptionSet):
    """Equipment that is always in your item pool. Works like Extra Weapons."""
    display_name = "Extra Equipment"
    valid_keys = ITEMS_OF_TYPE["equipment"]


class ExtraEquipmentRandom(Range):
    """How many random pieces of equipment to add to your item pool, besides the ones already in it."""
    display_name = "Extra Random Equipment"
    range_start = 0
    range_end = len(ITEMS_OF_TYPE["equipment"])
    default = 0


class ExtraCamo(OptionSet):
    """Camos and face paints that are always in your item pool, including camos you can only get as rewards. Works like
    Extra Weapons."""
    display_name = "Extra Camos"
    valid_keys = ITEMS_OF_TYPE["camo"]


class ExtraCamoRandom(Range):
    """How many random camos and face paints to add to your item pool, besides the ones already in it."""
    display_name = "Extra Random Camos"
    range_start = 0
    range_end = len(ITEMS_OF_TYPE["camo"])
    default = 0


class KerotanChecks(Toggle):
    """Each of the 64 Kerotan frogs you shoot is a check. Only frogs shot in this run count. Warp 143.41 takes you back
    to the Virtuous Mission frogs."""
    display_name = "Kerotan Checks"


class GakoChecks(Toggle):
    """Each of the 64 Gako ducks you break is a check. Only ducks broken in this run count. Warp 143.41 takes you back
    to the Virtuous Mission ducks."""
    display_name = "Gako Checks"


class AnimalChecks(Toggle):
    """Capturing each of 28 animal species for the first time is a check, alive or dead. The snakes in the last area
    and the gavial don't count."""
    display_name = "Animal Checks"


class NoAlertAreas(Range):
    """This many checks for areas you get through unseen (0 = off). Stay at least 10 seconds, never get spotted and
    leave through an exit. Each area counts once."""
    display_name = "No-Alert Area Checks"
    range_start = 0
    range_end = 30
    default = 0


class CqcTakedowns(Range):
    """This many checks for CQC takedowns, such as a throw or a choke (0 = off)."""
    display_name = "CQC Takedown Checks"
    range_start = 0
    range_end = 30
    default = 0


class RaikovDisguise(Choice):
    """Raikov's disguise (the Officer Camo and the Mask face paint) is needed in Groznyj Grad. vanilla: you get them as
    in the normal game. randomized: both are items in the pool, and the game's own copies are taken back."""
    display_name = "Raikov Disguise"
    option_vanilla = 0
    option_randomized = 1
    default = 0


class ShagohodRpg(Choice):
    """The RPG-7 the game gives you for the Shagohod fight. vanilla: you keep it. randomized: you only have it if
    Archipelago sent it, and logic expects it before the Shagohod."""
    display_name = "Shagohod RPG-7"
    option_vanilla = 0
    option_randomized = 1
    default = 0


class CaveExitGate(DefaultOnToggle):
    """The door from the Cave to Ponizovje stays locked until you get the "Cave Exit" item. Off: the door is open."""
    display_name = "Cave Exit Gate"


class SokrovennoExitGate(DefaultOnToggle):
    """The way from Sokrovenno to Krasnogorje stays locked until you get the "Sokrovenno Exit" item. Off: the way is
    open."""
    display_name = "Sokrovenno Exit Gate"


class ReleaseOnGoal(Toggle):
    """When you reach the goal, the client sends !release, which gives your remaining items to their players. The
    server's release setting still applies."""
    display_name = "Release on Goal"


class FillerPercentage(Range):
    """How much of the item pool is filler (benefits and traps), in percent of the room left after the required items.
    The rest is game items. 0: filler only when the game items run out. 100: almost only filler. Extra items
    replace filler first."""
    display_name = "Filler Percentage"
    range_start = 0
    range_end = 100
    default = 33


class TrapPercentage(Range):
    """How much of the filler is traps; the rest is benefits. Example: 40 gives 4 traps for every 6 benefits."""
    display_name = "Trap Percentage"
    range_start = 0
    range_end = 100
    default = 20


class EnabledTraps(OptionSet):
    """Traps that can appear. Weather only shows outdoors. Alert starts a caution phase, but guards don't search."""
    display_name = "Enabled Traps"
    valid_keys = TRAPS
    default = frozenset(TRAPS)


# Wound types of the Injury trap (the client maps them to the game's add-wound callback values; all verified in game).
WOUNDS = ["Burn", "Cut", "Bullet", "Shotgun", "Bee Sting", "Leech", "Arrow", "Needle"]


class InjuryWounds(OptionSet):
    """Wounds the Injury trap can give, picked at random. Treat them in the Cure menu."""
    display_name = "Injury Trap Wounds"
    valid_keys = WOUNDS
    default = frozenset(WOUNDS)


class InjuryCount(Range):
    """How many wounds one Injury trap gives."""
    display_name = "Injury Trap Wound Count"
    range_start = 1
    range_end = 5
    default = 1


class DlcItems(Toggle):
    """Put the Sneaking DLC Pack items in the item pool and the random start (Battle Dress PW, Sneaking PW, Naked
    Woodland, Naked Beltlink and Gold camos, Glasses and Sunglasses face paint, Croc Suit and One-Eyed Croc
    Cap). Only turn this on if you own the pack."""
    display_name = "Sneaking DLC Pack Items"


class EnabledBenefits(OptionSet):
    """Benefits (filler) that can appear."""
    display_name = "Enabled Benefits"
    valid_keys = BENEFITS
    default = frozenset(BENEFITS)


@dataclass
class MGSDeltaOptions(PerGameCommonOptions):
    difficulty: Difficulty
    loadout: Loadout
    starting_camo: StartingCamo
    dlc_items: DlcItems
    weapon_checks: WeaponChecks
    weapon_count: WeaponCount
    weapon_bundle: WeaponBundle
    weapon_bundle_size: WeaponBundleSize
    equipment_checks: EquipmentChecks
    equipment_count: EquipmentCount
    equipment_bundle: EquipmentBundle
    equipment_bundle_size: EquipmentBundleSize
    suppressor_checks: SuppressorChecks
    camo_checks: CamoChecks
    camo_count: CamoCount
    camo_bundle: CamoBundle
    camo_bundle_size: CamoBundleSize
    nonlethal_boss: NonlethalBoss
    moss_camo_check: MossCamoCheck
    spirit_camo_check: SpiritCamoCheck
    nonlethal_logic: NonlethalLogic
    medical_checks: MedicalChecks
    medical_count: MedicalCount
    medical_bundle: MedicalBundle
    medical_bundle_size: MedicalBundleSize
    extra_weapon: ExtraWeapon
    extra_weapon_random: ExtraWeaponRandom
    extra_equipment: ExtraEquipment
    extra_equipment_random: ExtraEquipmentRandom
    extra_camo: ExtraCamo
    extra_camo_random: ExtraCamoRandom
    kerotan_checks: KerotanChecks
    gako_checks: GakoChecks
    animal_checks: AnimalChecks
    no_alert_areas: NoAlertAreas
    cqc_takedowns: CqcTakedowns
    cave_exit_gate: CaveExitGate
    sokrovenno_exit_gate: SokrovennoExitGate
    raikov_disguise: RaikovDisguise
    shagohod_rpg: ShagohodRpg
    release_on_goal: ReleaseOnGoal
    filler_percentage: FillerPercentage
    trap_percentage: TrapPercentage
    enabled_traps: EnabledTraps
    injury_wounds: InjuryWounds
    injury_count: InjuryCount
    enabled_benefits: EnabledBenefits
    death_link: DeathLink


# Sections of the options page / YAML template (options not listed land under "Game Options").
OPTION_GROUPS = [
    OptionGroup("Game Setup", [Difficulty, Loadout, StartingCamo, DlcItems]),
    OptionGroup("Goal", [CaveExitGate, SokrovennoExitGate, RaikovDisguise, ShagohodRpg, ReleaseOnGoal]),
    OptionGroup("Weapon Checks", [WeaponChecks, WeaponCount, WeaponBundle, WeaponBundleSize]),
    OptionGroup("Equipment Checks", [EquipmentChecks, EquipmentCount, EquipmentBundle, EquipmentBundleSize,
                                         SuppressorChecks]),
    OptionGroup("Camo Checks", [CamoChecks, CamoCount, CamoBundle, CamoBundleSize]),
    OptionGroup("Medical Checks", [MedicalChecks, MedicalCount, MedicalBundle, MedicalBundleSize]),
    OptionGroup("Optional Checks", [NonlethalBoss, NonlethalLogic, MossCamoCheck, SpiritCamoCheck, KerotanChecks,
                                    GakoChecks, AnimalChecks,
                                    NoAlertAreas, CqcTakedowns]),
    OptionGroup("Extra Items", [ExtraWeapon, ExtraWeaponRandom, ExtraEquipment, ExtraEquipmentRandom, ExtraCamo,
                                ExtraCamoRandom]),
    OptionGroup("Traps & Benefits", [FillerPercentage, TrapPercentage, EnabledTraps, InjuryWounds, InjuryCount,
                                     EnabledBenefits]),
]
