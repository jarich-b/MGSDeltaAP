"""Persistent per-run state of the client and the game-state (GM) variables it reads.

RunState is stored as JSON per (seed, slot) so reconnects, client restarts and game restarts never repeat the run
start (inventory wipe), never re-apply one-shot items and never re-count acquisition checks.
"""
import json
import os
import struct

from .constants import GM_POINTER, read_pointer

CATEGORIES = ("weapon", "equipment", "camo", "medical")


class RunState:
    """Everything the client must remember about a run; load() on creation, save() writes only when it changed."""

    VERSION = 1

    def __init__(self, path=None):
        self.path = path
        self._saved = None
        self.initialized = False          # run start (wipe + loadout) done
        self.applied_count = 0            # received items already applied (effects are one-shot)
        self.acquired = {category: [] for category in CATEGORIES}   # ids counted as "New <category> k", in order
        self.taken_back = []              # [kind, id] keys taken back after a counted box pickup
        self.kept = []                    # [kind, id] keys counted but kept (story gifts, boss rewards)
        self.stripped = []                # [kind, id] keys removed from a game hand-out (given back later: no gift)
        self.completed = []               # section ids whose "Complete" check was sent
        self.bosses_beaten = []           # boss names (data/bosses.json) beaten in this run (fights stay replayable)
        self.nonlethal = []               # boss names whose non-lethal flag was seen ("Defeat <boss> Non-Lethally")
        self.story_max = None             # highest story progress ($var[4]) seen in this run; older saves never lower
        self.visited = []                 # area prefixes reached in this run (checkpoint warps unlock on arrival)
        self.cqc_total = 0                # CQC takedowns counted in this run (events, so reloads can't lower it)
        self.clean_areas = []             # area prefixes counted as "No-Alert Area k" (first clean visit), in order
        self.kerotans = []                # Kerotan indices (0..63) shot in this run
        self.kerotan_bits = []            # Kerotan indices set in the $var flags when last read (new bits = shots)
        self.kerotan_zeroed = False       # the $var Kerotan flags were cleared (or baselined) for this run
        self.kerotan_profile = []         # Kerotan indices the run start cleared (the profile's lifetime frogs)
        self.gako = []                    # distinct Gako duck indices broken in this run
        self.animals = []                 # species kinds (normalised food kinds, data/animals.json) captured
        self.inventory = None             # [kind, id] category keys owned when last seen (offline gain detection)
        self.goal_reached = False         # finale end seen (goal held until enough pieces)
        self.goal_sent = False
        self.counters = {"deaths_sent": 0, "deaths_received": 0, "locks": 0}
        if path:
            self.load()

    # --- persistence ------------------------------------------------------------------------------
    def load(self):
        """Read the state file if there is one; fields missing from older files keep their defaults."""
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        self.initialized = bool(data.get("initialized", False))
        self.applied_count = int(data.get("applied_count", 0))
        for category in CATEGORIES:
            self.acquired[category] = list(data.get("acquired", {}).get(category, []))
        self.taken_back = [list(key) for key in data.get("taken_back", [])]
        self.kept = [list(key) for key in data.get("kept", [])]
        self.stripped = [list(key) for key in data.get("stripped", [])]
        self.completed = list(data.get("completed", []))
        self.bosses_beaten = list(data.get("bosses_beaten", []))
        self.visited = list(data.get("visited", []))
        self.cqc_total = int(data.get("cqc_total", 0))
        self.clean_areas = list(data.get("clean_areas", []))
        self.kerotans = [int(i) for i in data.get("kerotans", [])]
        self.kerotan_bits = [int(i) for i in data.get("kerotan_bits", [])]
        self.kerotan_zeroed = bool(data.get("kerotan_zeroed", False))
        self.kerotan_profile = [int(i) for i in data.get("kerotan_profile", [])]
        self.gako = [int(i) for i in data.get("gako", [])]
        self.animals = [int(i) for i in data.get("animals", []) if int(i) > 0]
        self.nonlethal = list(data.get("nonlethal", []))
        story = data.get("story_max")
        self.story_max = int(story) if story is not None else None
        saved_inventory = data.get("inventory")
        self.inventory = [list(key) for key in saved_inventory] if saved_inventory is not None else None
        self.goal_reached = bool(data.get("goal_reached", False))
        self.goal_sent = bool(data.get("goal_sent", False))
        self.counters.update(data.get("counters", {}))

    def to_dict(self):
        """The JSON form of the state (the file format; keys must stay stable)."""
        return {"version": self.VERSION, "initialized": self.initialized, "applied_count": self.applied_count,
                "acquired": self.acquired, "taken_back": self.taken_back, "kept": self.kept,
                "stripped": self.stripped,
                "completed": self.completed, "bosses_beaten": self.bosses_beaten, "nonlethal": self.nonlethal,
                "visited": self.visited, "cqc_total": self.cqc_total,
                "clean_areas": self.clean_areas, "kerotans": self.kerotans, "kerotan_bits": self.kerotan_bits,
                "kerotan_zeroed": self.kerotan_zeroed, "kerotan_profile": self.kerotan_profile,
                "gako": self.gako, "animals": self.animals,
                "story_max": self.story_max, "inventory": self.inventory, "goal_reached": self.goal_reached,
                "goal_sent": self.goal_sent, "counters": self.counters}

    def save(self):
        """Write the state atomically (temp file + replace); a no-op when nothing changed."""
        if not self.path:
            return
        text = json.dumps(self.to_dict(), indent=1)
        if text == self._saved:
            return
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, self.path)
            self._saved = text
        except OSError:
            pass

    # --- helpers ----------------------------------------------------------------------------------
    def is_taken_back(self, key):
        """Whether the item (kind, id) was found in a box and taken back."""
        return list(key) in self.taken_back

    def add_taken_back(self, key):
        """Remember a found box item that was taken back."""
        if list(key) not in self.taken_back:
            self.taken_back.append(list(key))

    def add_stripped(self, key):
        """Remember an item removed from a game hand-out."""
        if list(key) not in self.stripped:
            self.stripped.append(list(key))

    def is_stripped(self, key):
        """Whether the item (kind, id) was removed from a game hand-out in this run."""
        return list(key) in self.stripped

    def add_kept(self, key):
        """Remember a counted item that was kept (a story gift or boss reward)."""
        if list(key) not in self.kept:
            self.kept.append(list(key))

    def taken_back_keys(self):
        """The taken-back items as a set of (kind, id) tuples."""
        return {tuple(key) for key in self.taken_back}


class GameVars:
    """Reads/writes Snake's state in the legacy GM block (*GM_POINTER + offset); offsets from data/gamestate.json.
    Derived statically, untested in-game; every access may raise OSError."""

    FIELDS = {"life": ("GMPlayerLife", 0x7B4, "<h"), "life_max": ("GMPlayerLifeMax", 0x7B6, "<h"),
              "continue_count": ("GMContinueCount", 0x34, "<h"),
              "worn_camo": ("GMWornCamo", 0x7AE, "<B"), "worn_paint": ("GMWornFacePaint", 0x7AF, "<B"),
              # equipped item id (0 = none), next to the equipped weapon at +0x704
              "equipped_item": ("GMEquippedItem", 0x706, "<h")}

    def __init__(self, game, gamestate=None):
        self.game = game
        self.fields = {}
        for key, (name, off, fmt) in self.FIELDS.items():
            entry = (gamestate or {}).get(name)
            if entry:
                off = int(entry["offset"], 16)
                fmt = "<h" if entry.get("type") == "int16" else "<i"
            self.fields[key] = (off, fmt)

    def _gm(self):
        return read_pointer(self.game, GM_POINTER, "GM pointer")

    def get(self, key):
        """Read a GM field by its short name (see FIELDS)."""
        off, fmt = self.fields[key]
        return struct.unpack(fmt, self.game.read(self._gm() + off, struct.calcsize(fmt)))[0]

    def set(self, key, value):
        """Write a GM field by its short name (see FIELDS)."""
        off, fmt = self.fields[key]
        self.game.write(self._gm() + off, struct.pack(fmt, value))

    # Active items (the ones Snake carries outside the backpack): 8 i16 item ids, 0 = empty slot [V 2026-10-15]. The
    # active weapons are the 8 slots before them (+0x6E4).
    ACTIVE_ITEMS = 0x6F4
    ACTIVE_ITEM_SLOTS = 8

    def active_items(self):
        """Item ids of the active item slots (0 = empty)."""
        data = self.game.read(self._gm() + self.ACTIVE_ITEMS, 2 * self.ACTIVE_ITEM_SLOTS)
        return list(struct.unpack(f"<{self.ACTIVE_ITEM_SLOTS}h", data))

    def set_active_item(self, slot, item_id):
        """Put an item id into one active item slot."""
        self.game.write(self._gm() + self.ACTIVE_ITEMS + 2 * slot, struct.pack("<h", item_id))
