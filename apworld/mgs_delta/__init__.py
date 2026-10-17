"""Archipelago world for Metal Gear Solid Delta (MGS Delta: Snake Eater)."""
from BaseClasses import ItemClassification, Region, Tutorial
from Options import OptionError
from worlds.AutoWorld import WebWorld, World
from worlds.LauncherComponents import Component, Type, components, launch as launch_component

from .items import (CAMO_ITEMS, CLEARANCES, FIGHTING_GEAR, FIREARMS, GAME, C3_CHARGE, C3_CHARGES, DISGUISE,
                    ITEM_GROUPS, ITEM_NAME_TO_ID, ITEM_TABLE, MEDICAL_ITEMS, OTHER_WEAPONS, PLAIN_EQUIPMENT,
                    REVIVAL_PILL, MGSDeltaItem, classification)
from .locations import (GOAL_LOCATION, LOCATION_GROUPS, LOCATION_NAME_TO_ID, SECTION_BY_ID, SECTION_ORDER,
                        MGSDeltaLocation, acq_plan, counted_items, included, location_section,
                        suppressor_guns, suppressor_guns_needed)
from .options import (ACQ_TYPES, COLLECTION_TYPES, DLC_ITEMS, ITEMS_OF_TYPE, NEVER_OFFERED, NONLETHAL_WEAPONS,
                      OPTION_GROUPS, WEARABLE_ITEMS, ChecksMode, Loadout, MGSDeltaOptions, NonlethalLogic,
                      StartingCamo)
from .rules import entry_items, gate_enabled, goal_rule, location_rule, section_requirement, section_rule


def launch_client(*args):
    """Launcher entry: the client module is imported only when the client is started."""
    from .client import launch
    launch_component(launch, name="MGS Delta Client", args=args)


components.append(Component("MGS Delta Client", func=launch_client, component_type=Type.CLIENT,
                            description="Connects MGS Delta (with the MGSDeltaAP UE4SS mod) to Archipelago."))

WEARABLE_CHANCE = 5          # randomized start: one in this many rolls a wearable item instead of a uniform
STARTING_PAINT_EXCLUDED = "Infinity Face Paint"     # never the random starting paint: it makes Snake undetectable
LOADOUT_ITEMS = {
    Loadout.option_empty: [],
    Loadout.option_knife: ["Survival Knife"],
    Loadout.option_pistol: ["Survival Knife", "Mk22"],
    Loadout.option_vanilla: ["Survival Knife", "Mk22", "Cigar", "Binoculars"],
}
UTILITY = ["Thermal Goggles", "Night Vision Goggles", "Motion Detector", "Active Sonar", "Mine Detector",
           "Anti-Personnel Sensor", "Cardboard Box A", "Binoculars"]


class MGSDeltaWeb(WebWorld):
    theme = "jungle"
    option_groups = OPTION_GROUPS
    tutorials = [Tutorial("Setup Guide", "How to set up MGS Delta for Archipelago.", "English",
                          "setup_en.md", "setup/en", ["MGSDeltaAP contributors"])]


class MGSDeltaWorld(World):
    """Metal Gear Solid Delta: Snake Eater, the 2025 remake of MGS3. Weapons, equipment and camouflage
    found in the jungle are shuffled across the multiworld. The story chapters are gated by two clearance items
    (Cave Exit, Sokrovenno Exit) and four C3 Charges for the fuel tanks before the Volgin fight; the goal is the duel
    with Ocelot."""
    game = GAME
    web = MGSDeltaWeb()
    options_dataclass = MGSDeltaOptions
    options: MGSDeltaOptions
    item_name_to_id = ITEM_NAME_TO_ID
    location_name_to_id = LOCATION_NAME_TO_ID
    item_name_groups = ITEM_GROUPS
    location_name_groups = LOCATION_GROUPS

    def generate_early(self):
        """Decide the included checks, the starting kit (loadout, camo, face paint, wearable) and the stun weapon."""
        self.location_names = [n for n in LOCATION_NAME_TO_ID if included(n, self.options)]
        self.not_offered = set(NEVER_OFFERED) | (set() if self.options.dlc_items else DLC_ITEMS)
        # randomized Raikov disguise: required progression items, so never a starting camo or a normal pool camo
        self.disguise = list(DISGUISE) if self.options.raikov_disguise else []
        self.not_offered |= set(self.disguise)
        self.starting_items = list(LOADOUT_ITEMS.get(self.options.loadout.value, []))
        if self.options.loadout == Loadout.option_random_kit:
            weapons = sorted(FIREARMS)
            equipment = [n for n in PLAIN_EQUIPMENT if n not in UTILITY and n not in self.not_offered]
            self.starting_items = [self.random.choice(weapons), self.random.choice(equipment),
                                   self.random.choice(UTILITY)]
        self.starting_camo = self.starting_facepaint = self.starting_wearable = None
        if self.options.starting_camo == StartingCamo.option_randomized:
            # a uniform, or (one in WEARABLE_CHANCE) a wearable item over the vanilla uniform; plus a face paint
            uniforms = [entry["name"] for entry in ITEM_TABLE
                        if entry.get("group") == "camo" and entry["name"] not in self.not_offered]
            wearables = [n for n in WEARABLE_ITEMS if n not in self.not_offered and n not in self.starting_items]
            if wearables and self.random.randrange(WEARABLE_CHANCE) == 0:
                self.starting_wearable = self.random.choice(wearables)
            else:
                self.starting_camo = self.random.choice(uniforms)
            face_paints = [entry["name"] for entry in ITEM_TABLE
                           if entry.get("group") == "facepaint" and entry["name"] != STARTING_PAINT_EXCLUDED
                           and entry["name"] not in self.not_offered]
            self.starting_facepaint = self.random.choice(face_paints)
        # Non-lethal boss checks: one non-lethal weapon is always in the pool (or the starting kit), early if asked
        self.stun_item = None
        if self.options.nonlethal_boss and not set(NONLETHAL_WEAPONS) & set(self.starting_items):
            self.stun_item = self.random.choice(["Mk22", "Stun Grenade"])
            if self.options.nonlethal_logic == NonlethalLogic.option_starting:
                self.starting_items.append(self.stun_item)
                self.stun_item = None
            elif self.options.nonlethal_logic == NonlethalLogic.option_early:
                self.multiworld.early_items[self.player][self.stun_item] = 1
        for name in self.starting_items:
            self.multiworld.push_precollected(self.create_item(name))

        for acq_type in ACQ_TYPES:
            forbidden = sorted(getattr(self.options, f"extra_{acq_type}").value & self.not_offered)
            if forbidden:
                raise OptionError(f"{self.player_name}: extra_{acq_type} contains {', '.join(forbidden)}, which can "
                                  f"never be an extra item (the keys stay vanilla; DLC items need dlc_items).")

        required = len(self.required_items()) + 1      # + at least one game item
        if required > len(self.location_names):
            raise OptionError(f"{self.player_name}: {len(self.location_names)} checks are not enough for "
                              f"{required} required items; raise the check counts.")

    def create_regions(self):
        """One region per story section in a chain (Menu -> first section -> ...); the goal sits in the last one."""
        menu = Region("Menu", self.player, self.multiworld)
        self.multiworld.regions.append(menu)
        previous = menu
        regions = {}
        for section_id in SECTION_ORDER:
            section = SECTION_BY_ID[section_id]
            region = Region(section["name"], self.player, self.multiworld)
            self.multiworld.regions.append(region)
            previous.connect(region, f"Enter {section['name']}", section_rule(section, self))
            regions[section_id] = previous = region
        for name in self.location_names:
            region = regions[location_section(name, self.options)]
            location = MGSDeltaLocation(self.player, name, LOCATION_NAME_TO_ID[name], region)
            rule = location_rule(name, self)
            if rule:
                location.access_rule = rule
            region.locations.append(location)
        goal = MGSDeltaLocation(self.player, GOAL_LOCATION, None, previous)
        goal.place_locked_item(MGSDeltaItem("Victory", ItemClassification.progression, None, self.player))
        previous.locations.append(goal)

    def create_item(self, name):
        item_class = classification(name)
        if name in getattr(self, "disguise", ()):
            item_class = ItemClassification.progression      # worn for the way to the capture
        if name in NONLETHAL_WEAPONS and self.options.nonlethal_boss:
            item_class = ItemClassification.progression      # the non-lethal boss checks need one of them
        return MGSDeltaItem(name, item_class, ITEM_NAME_TO_ID[name], self.player)

    def medical_in_pool(self):
        """Medical items only exist with medical checks (else the vanilla boxes already supply them)."""
        return self.options.medical_checks.value != ChecksMode.option_off

    def required_items(self):
        """Items every seed needs: clearances, the C3 Charges, the Revival Pill (and the non-lethal weapon, the RPG-7
        with shagohod_rpg random)."""
        clearances = [item for item in CLEARANCES if gate_enabled(item, self.options)]
        return (clearances + [C3_CHARGE] * C3_CHARGES + [REVIVAL_PILL] + self.disguise
                + ([self.stun_item] if self.stun_item else []) + self.suppressor_guns_required() + self.rpg_required())

    def rpg_required(self):
        """The RPG-7 with shagohod_rpg random (the Shagohod section needs it), unless it is starting gear."""
        if not self.options.shagohod_rpg or "RPG-7" in self.starting_items or self.stun_item == "RPG-7":
            return []
        return ["RPG-7"]

    def suppressor_guns_required(self):
        """Guns that counted suppressor pickups need (the logic asks for them), unless they are starting gear."""
        needed = max((suppressor_guns_needed(name, self.options) for name in self.location_names), default=0)
        missing = [gun for gun in suppressor_guns() if gun not in self.starting_items and gun != self.stun_item]
        return missing if needed else []

    def create_items(self):
        """Fill the pool: required items, then game items by priority and extras, then filler up to the slot count."""
        slots = len(self.location_names)
        pool = self.required_items()
        owned = (set(self.starting_items) | {self.starting_wearable, self.stun_item} | self.not_offered
                 | set(self.suppressor_guns_required()) | set(self.rpg_required()))
        # Game items by priority: fighting gear first (logic needs it), then equipment, then camos.
        weapons = [n for n in sorted(FIGHTING_GEAR) if n not in owned]
        self.random.shuffle(weapons)
        equipment = [n for n in PLAIN_EQUIPMENT
                     + (MEDICAL_ITEMS if self.medical_in_pool() else []) if n not in owned]
        other_weapons = [n for n in OTHER_WEAPONS if n not in owned]
        camos = [n for n in CAMO_ITEMS if n not in (self.starting_camo, self.starting_facepaint) and n not in owned]
        for tier in (equipment, other_weapons, camos):
            self.random.shuffle(tier)
        free = slots - len(pool)
        game_room = max(1, free * (100 - self.options.filler_percentage.value) // 100)
        game_items = (weapons + equipment + other_weapons + camos)[:game_room]
        # Extra items (not checks): useful items that take the place of filler first, then of the normal game items.
        extras = self.extra_items(owned, set(game_items))
        if len(extras) > free:
            raise OptionError(f"{self.player_name}: {len(extras)} extra items do not fit next to the required items "
                              f"({free} free locations); lower the extra_* options or raise the check counts.")
        self.extra_names = set(extras)
        pool += extras + [n for n in game_items if n not in self.extra_names][:free - len(extras)]
        while len(pool) < slots:
            pool.append(self.get_filler_item_name())
        for name in pool[:slots]:
            item = self.create_item(name)
            if name in self.extra_names:        # an extra copy is never counted on by logic
                item.classification = ItemClassification.useful
            self.multiworld.itempool.append(item)

    def extra_items(self, owned, in_pool):
        """Names added by the extra_<type> / extra_<type>_random options: the chosen ones (not starting gear), then N
        random ones of the type that are neither in the pool nor starting gear."""
        extras = []
        blocked = owned | {self.starting_camo, self.starting_facepaint, self.starting_wearable} | self.not_offered
        for acq_type in ACQ_TYPES:
            # a logic item already in the pool keeps its progression copy; a second one would be redundant
            chosen = sorted(name for name in getattr(self.options, f"extra_{acq_type}").value
                            if name not in blocked
                            and not (name in in_pool and classification(name) == ItemClassification.progression))
            candidates = [name for name in ITEMS_OF_TYPE[acq_type] if name not in blocked | in_pool | set(chosen)]
            count = min(getattr(self.options, f"extra_{acq_type}_random").value, len(candidates))
            extras += chosen + self.random.sample(candidates, count)
        return extras

    def get_filler_item_name(self):
        """A trap (trap_percentage of the time, if any are enabled) or else a benefit."""
        traps = sorted(self.options.enabled_traps.value)
        if traps and self.random.randrange(100) < self.options.trap_percentage:
            return f"Trap: {self.random.choice(traps)}"
        benefits = sorted(self.options.enabled_benefits.value) or ["Life Refill"]
        return self.random.choice(benefits)

    def set_rules(self):
        self.multiworld.completion_condition[self.player] = goal_rule(self)

    def fill_slot_data(self):
        """What the client (and the tracker) need: options, collection plans, starting kit and the section table."""
        return {
            "options": self.options.as_dict(
                "difficulty", "loadout", "starting_camo", *self.acquisition_option_names(),
                "suppressor_checks", "nonlethal_boss", "moss_camo_check", "spirit_camo_check", "filler_percentage", "kerotan_checks", "gako_checks", "animal_checks",
                "no_alert_areas", "cqc_takedowns", "cave_exit_gate", "sokrovenno_exit_gate", "raikov_disguise", "shagohod_rpg",
                "enabled_traps", "injury_wounds", "injury_count", "death_link", "release_on_goal"),
            "acquisition": {acq_type: {**dict(zip(("total", "size"), acq_plan(self.options, acq_type))),
                                       "bundled": bool(getattr(self.options, f"{acq_type}_bundle")),
                                       "items": counted_items(self.options, acq_type)} for acq_type in COLLECTION_TYPES},
            "starting_items": self.starting_items,
            "starting_camo": self.starting_camo,
            "starting_facepaint": self.starting_facepaint,
            "starting_wearable": self.starting_wearable,
            "sections": {section_id: self.section_slot_data(SECTION_BY_ID[section_id])
                         for section_id in SECTION_ORDER},
        }

    @staticmethod
    def acquisition_option_names():
        return [f"{acq_type}_{suffix}" for acq_type in COLLECTION_TYPES
                for suffix in ("checks", "count", "bundle", "bundle_size")]

    def section_slot_data(self, section):
        requirement = section_requirement(section, self.options)
        return {"name": section["name"], "unlock": section.get("unlock"),
                "unlock_item": requirement[0] if requirement else None,
                "unlock_count": requirement[1] if requirement else 0,
                "entry_items": sorted(entry_items(section, self.options)),
                "warp": section.get("warp", False), "areas": section["areas"], "gates": section.get("gates", [])}
