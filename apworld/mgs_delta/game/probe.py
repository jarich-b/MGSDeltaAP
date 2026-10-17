"""Read-only logging for the Capture and Escape section (the capture, the cell, the sewers, The Sorrow, Tikhogornyj,
EVA's gear return and the way back into Groznyj Grad): how the game takes and gives back gear, the backpack state and
what the client sees of pickups. Every line starts with "[probe]"; nothing here writes to the game."""
import struct

from . import warp
from .constants import GM_POINTER, read_pointer

PROBE_AREAS = ("s111", "s112", "s113", "s141", "s151", "s152")
PROBE_STORY = (99, 137)       # from the capture (Raikov's disguise check needs story <= 99) to the C3 tanks
# backpack candidates: $linkvar[7b0] (GMPlayerEquipment, bit 1024), $var[dca], $var[ea5]
EQUIPMENT_FLAGS = 0x7B0
SCRIPT_BYTES = (0xDCA, 0xEA5)
ACTIVE_WEAPONS, ACTIVE_ITEMS, SLOTS = 0x6E4, 0x6F4, 8
EQUIPPED_WEAPON, EQUIPPED_ITEM = 0x704, 0x706


class EscapeProbe:
    """Logs changes only (a snapshot when the probe starts): the game's at the start of each session tick, the
    client's at its end."""

    def __init__(self, session):
        self.session = session
        self.active = False
        self.last = {}

    def wanted(self, stage, story):
        if not stage or not self.session.in_game(stage):
            return False
        return stage[:4] in PROBE_AREAS or (story is not None and PROBE_STORY[0] <= story <= PROBE_STORY[1])

    def log(self, text):
        self.session.detail(f"[probe] {text}")

    def read_state(self):
        """{name: value} of the watched values; values that can't be read are left out."""
        game = self.session.bridge.game
        out = {}
        try:
            gm = read_pointer(game, GM_POINTER, "GM pointer")
            flags = struct.unpack("<i", game.read(gm + EQUIPMENT_FLAGS, 4))[0]
            out["linkvar7b0"] = f"0x{flags:x} (bit1024={'on' if flags & 1024 else 'off'})"
            out["active_weapons"] = list(struct.unpack(f"<{SLOTS}h", game.read(gm + ACTIVE_WEAPONS, 2 * SLOTS)))
            out["active_items"] = list(struct.unpack(f"<{SLOTS}h", game.read(gm + ACTIVE_ITEMS, 2 * SLOTS)))
            out["equipped"] = struct.unpack("<hh", game.read(gm + EQUIPPED_WEAPON, 4))
        except OSError:
            pass
        try:
            script = warp.ScriptVars(game)
            for off in SCRIPT_BYTES:
                out[f"var{off:x}"] = script.read_u8(off)
        except OSError:
            pass
        return out

    def inventory(self):
        """{name: (value, maximum)} of every weapon owned (stock >= 0) and every item held (count > 0)."""
        bridge = self.session.bridge
        out = {}
        for wid, (stock, maximum) in bridge.weapons.items():
            if stock >= 0:
                out[self.name(("weapon", wid))] = (stock, maximum)
        for iid, (count, maximum) in bridge.items.items():
            if count > 0:
                out[self.name(("item", iid))] = (count, maximum)
        return out

    def name(self, key):
        return self.session.bridge.item_by_key.get(key, {}).get("name", f"{key[0]} {key[1]}")

    def pickups(self, stage, pickups):
        """Pickup events from the mod, logged before the session uses them."""
        if not self.active:
            return
        for box, event in pickups:
            where = box["name"] if box else "no box matched"
            self.log(f"pickup event in {stage}: {where}; event {event}")

    def tick(self, stage, who):
        """Log what changed since the last call. who: "game" at the start of a session tick (after the inventory was
        read: the game's own changes), "client" at its end (the client's writes in this tick)."""
        session = self.session
        story = session.read_story()
        wanted = self.wanted(stage, story)
        if wanted != self.active:
            self.active = wanted
            self.log(f"{'start' if wanted else 'stop'} in {stage}, story {story}")
            self.last = {}
        if not wanted:
            return
        try:
            now = {"stage": stage, "story": story, **self.read_state(), "inventory": self.inventory()}
        except OSError:
            return
        last, self.last = self.last, now
        if not last:
            for key, value in now.items():
                if key != "inventory":
                    self.log(f"{key} = {value}")
            self.log("inventory: " + self.inventory_text(now["inventory"]))
            return
        for key, value in now.items():
            if key == "inventory" or last.get(key) == value:
                continue
            self.log(f"{who}: {key}: {last.get(key)} -> {value}")
        before, after = last.get("inventory", {}), now["inventory"]
        changes = []
        for item in sorted(set(before) | set(after)):
            old, new = before.get(item), after.get(item)
            if old != new:
                changes.append(f"{item} {self.value_text(old)} -> {self.value_text(new)}")
        if changes:
            self.log(f"{who}: inventory change ({len(changes)}): " + "; ".join(changes))

    @staticmethod
    def value_text(value):
        return "none" if value is None else f"{value[0]}/{value[1]}"

    def inventory_text(self, inventory):
        return ", ".join(f"{item} {self.value_text(value)}" for item, value in sorted(inventory.items())) or "empty"
