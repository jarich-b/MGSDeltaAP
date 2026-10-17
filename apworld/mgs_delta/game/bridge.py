"""Game-side primitives of the MGS Delta client, independent of Archipelago networking.

- inventory access with a per-tick cache; every client write updates the "known" owned set, so inventory diffs
  only show changes the game made (pickups, story gifts, save loads)
- matches pickup events from the UE4SS mod to box locations (area + box model + nearest position)
- the box table: only to explain inventory gains as finds (pickups); boxes are not checks
- stage-load masking: hides owned items so their box spawns, or temporarily grants taken-back items so their box
  spawns as the ammo variant / not at all; everything is restored once the spawns have settled
"""
import math
import time

from . import warp
from .constants import CAMO_ITEM_BASE, MEDICAL_IDS, STAGE_ADDR, rebase
from .events import DEFAULT_EVENT_PATH, EventReader, PickupDetector

MATCH_DISTANCE = 120          # max UE units between a pickup and its box's locator
MASK_SETTLE = 1.5             # restore masked items this long after the last item spawn
MASK_TIMEOUT = 8.0            # ...or after this long without spawns
EARLY_MASK_LIMIT = 30.0       # an early mask waits this long at most for its stage to load (VM loads: ~10 s)
CBOX_IDS = {14, 15, 16, 17}
WEAPON_IDS = range(1, 31)
ITEM_IDS = range(1, 148)
NEVER_CHECK_WEAPONS = {24}    # the throwable "Empty Magazine": the game hands it out when a magazine runs dry
NEXT_STAGE_VAR = 0x250        # $var: GCL strcode of the stage being loaded, written 0.3-0.8 s before the stage name
STAGE_SUFFIXES = "abcdefgh"


def strcode(text):
    """The GCL's 24-bit string hash (stage names in the script variables)."""
    code = 0
    for byte in text.encode():
        code = ((code >> 19) | (code << 5)) & 0xFFFFFF
        code = (code + byte) & 0xFFFFFF
    return code


def box_model(class_name):
    """'ibox_weapon_riflel_body_hd_En_bp_C' -> 'ibox_weapon_riflel_body'"""
    base = class_name or ""
    for suffix in ("_hd_En_bp_C", "_hd_bp_C", "_bp_C"):
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def category(kind, item_id, medical=False):
    """Acquisition category of an inventory key: weapon / equipment / camo (and medical when asked), or None."""
    if kind == "weapon":
        return "weapon" if 1 <= item_id <= 30 and item_id not in NEVER_CHECK_WEAPONS else None
    if kind == "item":
        if CAMO_ITEM_BASE <= item_id <= 147:
            return "camo"
        if 5 <= item_id <= 35 or 48 <= item_id <= 50:
            return "equipment"
        if medical and item_id in MEDICAL_IDS:
            return "medical"
    return None


def model_category(model):
    """Which acquisition category a box model can give (fallback when a pickup matches no known box)."""
    if model.startswith("ibox_weapon_sp"):
        return "equipment"          # suppressors
    if model.startswith(("ibox_weapon", "ibox_fork", "ibox_torch")):
        return "weapon"
    if model.startswith(("ibox_cbox", "ibox_item")):
        return "equipment"
    if model.startswith("ibox_uniform"):
        return "camo"
    if model.startswith("ibox_medicine"):
        return "medical"
    return None


# Box models whose contents differ by difficulty or story beyond their fallback category (a medicine box holds Bug
# Juice on Very Easy in the Virtuous Mission): these categories may also be explained by such a box.
MODEL_EXTRA_CATEGORIES = {"medical": {"equipment"}}


def default_game_factory():
    """Attach to the running game with write access (imported late: mgsmem needs Windows)."""
    from .mgsmem import Game
    return Game(writable=True)


class Bridge:
    """The game as the client sees it: attachment, cached inventory, owned-set diffs, pickups, stage loads, masking."""

    def __init__(self, locations, location_ids, item_by_key, log=print, event_path=DEFAULT_EVENT_PATH,
                 game_factory=default_game_factory, clock=time.monotonic):
        self.log = log
        self.clock = clock
        self.game_factory = game_factory
        self.game = None
        self.detector = PickupDetector(EventReader(event_path))
        self.by_area = {}
        self.vanilla_of = {}
        for box in locations:
            box_id = location_ids[box["name"]]
            entry = dict(box, id=box_id)
            self.by_area.setdefault(box["area"], []).append(entry)
            self.vanilla_of[box_id] = (box["vanilla"]["kind"], box["vanilla"]["id"])
        self.item_by_key = item_by_key
        # the stages with boxes, by strcode: the script variables announce the next one before it loads
        self.stage_by_code = {strcode(area + suffix): area + suffix for area in self.by_area for suffix in STAGE_SUFFIXES}
        self.stage_by_code.update({strcode(stage): stage for boxes in self.by_area.values() for box in boxes
                                   for stage in box.get("stages") or ()})
        self.next_code = None
        self.spawned_models = set()    # box models that spawned (placed) since the last stage change
        self.stage = None
        self.previous_stage = None
        self.mod_restarted = False
        self.cqc_events = []         # CQC state/takedown events from the mod, consumed by the session
        self.gako_events = []        # Gako duck broken events from the mod ({"index"}), consumed by the session
        self.codec_events = []       # codec_call / codec_closed events from the mod, consumed by the session
        self.masked = {}           # (kind, id) -> value to restore
        self.mask_started = None
        self.mask_stage = None         # an early mask's stage, until that stage's load starts (see Session.mask_early)
        self.last_spawn = None
        self.last_mod_stage = None     # (stage, time) of the mod's last stage event
        self.weapons = {}              # wid -> (stock, max)
        self.items = {}                # iid -> (count, max)
        self.known = None              # category keys owned as last seen/written by us
        self.track_medical = False     # medicine is a collection type (medical checks on): part of owned_keys/diff

    # --- connection -----------------------------------------------------------------------------
    def connected(self):
        """Attach to the game if not attached yet; False while it is not running."""
        if self.game is None:
            try:
                self.game = self.game_factory()
                self.log(f"Attached to MGS Delta (pid {getattr(self.game, 'pid', '?')})")
            except Exception:      # not running, starting up or exiting: try again on the next tick
                self.game = None
                return False
            self.stage = None
            self.known = None
            self.masked.clear()
        return True

    def drop(self):
        """Forget the game process (it exited or restarted)."""
        self.game = None
        self.known = None
        self.masked.clear()

    def read_stage(self):
        """Current stage name from the game's legacy stage string."""
        raw = self.game.read(rebase(self.game, STAGE_ADDR), 8)
        return raw.split(b"\0")[0].decode(errors="replace")

    # --- inventory --------------------------------------------------------------------------------
    def refresh(self):
        """Read both inventory tables once; value/maximum/owned use this cache until the next refresh."""
        self.weapons = {weapon_id: (stock, maximum) for weapon_id, stock, maximum in self.game.weapons()}
        self.items = {item_id: (count, maximum) for item_id, _, count, maximum in self.game.items()}

    def value(self, kind, item_id):
        """Cached stock/count of a weapon or item (-1 / 0 when not in the table)."""
        return (self.weapons if kind == "weapon" else self.items).get(item_id, (-1 if kind == "weapon" else 0, 0))[0]

    def maximum(self, kind, item_id):
        """Cached stack maximum of a weapon or item."""
        return (self.weapons if kind == "weapon" else self.items).get(item_id, (0, 0))[1]

    def owned(self, kind, item_id):
        """Weapons are owned at stock >= 0 (0 = empty magazine), items at count > 0."""
        value = self.value(kind, item_id)
        return value >= 0 if kind == "weapon" else value > 0

    def category(self, kind, item_id):
        """category() with the medicine type switched on by track_medical."""
        return category(kind, item_id, self.track_medical)

    def owned_keys(self):
        """Owned keys of the acquisition categories (weapons, equipment, camos, medicine with track_medical)."""
        keys = {("weapon", weapon_id) for weapon_id, (stock, _) in self.weapons.items()
                if stock >= 0 and self.category("weapon", weapon_id)}
        keys |= {("item", item_id) for item_id, (count, _) in self.items.items()
                 if count > 0 and self.category("item", item_id)}
        return keys

    def diff(self):
        """Category keys the game added since the last call (client writes excluded). First call: baseline."""
        now = self.owned_keys()
        if self.known is None:
            self.known = now
            return set()
        gained = now - self.known
        self.known = now
        return gained

    def rebaseline(self):
        """Take the current inventory as the baseline: nothing in it counts as a gain."""
        self.known = self.owned_keys()

    def set_value(self, kind, item_id, value):
        """Write a weapon stock / item count, keeping the cache and the known set in step (a write is no gain)."""
        if kind == "weapon":
            self.game.give_weapon(item_id, value)
            self.weapons[item_id] = (value, self.maximum(kind, item_id))
        else:
            self.game.give_item(item_id, value)
            self.items[item_id] = (value, self.maximum(kind, item_id))
        if self.known is not None and self.category(kind, item_id):
            if self.owned(kind, item_id):
                self.known.add((kind, item_id))
            else:
                self.known.discard((kind, item_id))

    def set_maximum(self, kind, item_id, value):
        """Write a weapon's / item's stack maximum (the game refuses a box while the count is at the maximum)."""
        self.game.set_maximum(kind, item_id, value)
        table = self.weapons if kind == "weapon" else self.items
        table[item_id] = (self.value(kind, item_id), value)

    def unown(self, kind, item_id):
        """Remove a weapon or item from the inventory."""
        self.set_value(kind, item_id, -1 if kind == "weapon" else 0)

    def grant_amount(self, kind, item_id):
        """How much of an item a grant gives: a quarter stack (cardboard boxes: a full stack), at least 1."""
        maximum = self.maximum(kind, item_id)
        if kind == "weapon":
            return max(1, maximum // 4) if maximum > 0 else 1
        if item_id in CBOX_IDS:
            return max(maximum, 1)
        return max(1, maximum // 4) if maximum > 1 else 1

    def grant(self, kind, item_id):
        """Give an AP item if the player doesn't have it (idempotent)."""
        if self.owned(kind, item_id):
            return False
        self.set_value(kind, item_id, self.grant_amount(kind, item_id))
        return True

    def wipe(self, keep_camos=True, keep_camo=None, keep_items=()):
        """Run start: no weapons, no items. Camos/face paints are kept unless the worn one is known; keep_items
        (item ids, e.g. the keys with keys = vanilla) are left alone."""
        for weapon_id in WEAPON_IDS:
            if self.value("weapon", weapon_id) != -1:
                self.set_value("weapon", weapon_id, -1)
        for item_id in ITEM_IDS:
            if CAMO_ITEM_BASE <= item_id <= 147 and (keep_camos or item_id == keep_camo):
                continue
            if item_id in keep_items:
                continue
            if self.value("item", item_id) != 0:
                self.set_value("item", item_id, 0)

    # --- pickups ----------------------------------------------------------------------------------
    def match(self, event):
        """The box of the event's area and model nearest to the pickup position (within MATCH_DISTANCE), or None."""
        area = (event.get("stage") or "")[:4]
        model = box_model(event.get("class"))
        best, best_distance = None, MATCH_DISTANCE
        for box in self.by_area.get(area, []):
            if not box.get("pos"):
                continue
            if model and box.get("models") and model not in box["models"]:
                continue
            x, y, _ = box["pos"]
            distance = math.hypot((event.get("x") or 0) - x, (event.get("y") or 0) - y)
            if distance < best_distance:
                best, best_distance = box, distance
        return best

    def poll_events(self):
        """Returns pickups as [(location entry or None, event)]; tracks item spawns and mod stage events."""
        pickups = []
        for kind, payload in self.detector.poll():
            if kind == "pickup":
                box = self.match(payload)
                # "dropped" = spawned at runtime: an enemy drop (ignored), or a placed box the game re-spawned at its
                # own spot (seen with medicine boxes) — that one is a pickup of the placed box
                if payload.get("origin") != "dropped" or box is not None:
                    pickups.append((box, payload))
            elif kind == "stage":
                self.last_mod_stage = (payload.get("stage"), self.clock())
            elif kind == "other":
                self.on_mod_event(payload)
        return pickups

    def on_mod_event(self, event):
        """Route a non-pickup mod event: spawns update the masking clock, the rest is queued for the session."""
        kind = event.get("type")
        if kind == "item_spawn":
            self.last_spawn = self.clock()
            if event.get("origin") == "placed":
                self.spawned_models.add(box_model(event.get("class")))
        elif kind in ("codec_call", "codec_closed"):
            self.codec_events.append(event)
        elif kind == "gako":
            self.gako_events.append(event)
        elif kind == "cqc":
            self.cqc_events.append(event)
        elif kind == "hello":
            self.mod_restarted = True      # the mod skips old commands on load: state must be sent again

    def boxes_in_stage(self, stage):
        """The boxes of a stage (entries without a "stages" list sit in every stage of their area)."""
        return [box for box in self.by_area.get(stage[:4], [])
                if not box.get("stages") or stage in box["stages"]]

    # --- stage loads and masking --------------------------------------------------------------------
    def check_stage(self):
        """Returns the new stage name when a stage load just started (None otherwise or on first read)."""
        stage = self.read_stage()
        if stage == self.stage:
            return None
        previous, self.stage = self.stage, stage
        self.spawned_models = set()
        if previous is None or not stage:
            return None
        self.previous_stage = previous
        return stage

    def clear_box_timers(self, stage, keys):
        """Clear the respawn timer of the boxes of these items in a stage (a byte in the script variables, data
        "timers"): a weapon, ammo, grenade or medicine box picked up earlier stays away for a while even while its item
        is not owned (a picked-up SVD ammo box kept the SVD's weapon box away). Returns the keys whose timer was set."""
        cleared = []
        for box in self.boxes_in_stage(stage):
            offset = (box.get("timers") or {}).get(stage)
            key = self.vanilla_of[box["id"]]
            if offset is None or key not in keys:
                continue
            try:
                script_vars = warp.ScriptVars(self.game)
                if script_vars.read_u8(offset):
                    script_vars.write_u8(offset, 0)
                    cleared.append(key)
            except (OSError, RuntimeError):
                pass
        return cleared

    def check_next_stage(self):
        """The stage with boxes that the game just announced as the next one ($var[NEXT_STAGE_VAR], 0.3-0.8 s before the
        stage name changes), or None."""
        try:
            code = warp.ScriptVars(self.game).read_i32(NEXT_STAGE_VAR) & 0xFFFFFF
        except (OSError, RuntimeError, KeyError):
            return None
        if code == self.next_code:
            return None
        first, self.next_code = self.next_code is None, code
        stage = self.stage_by_code.get(code)
        return None if first or stage == self.stage else stage

    def begin_mask(self, hide=(), show=()):
        """hide: owned keys made unowned so their box spawns; show: unowned keys granted so their box doesn't
        spawn (or spawns as ammo). Both are restored by maybe_unmask()."""
        for key in hide:
            if key in self.masked or not self.owned(*key):
                continue
            self.masked[key] = self.value(*key)
            self.unown(*key)
        for key in show:
            if key in self.masked or self.owned(*key):
                continue
            self.masked[key] = self.value(*key)
            self.set_value(*key, self.grant_amount(*key))
        if self.masked:
            self.mask_started, self.last_spawn = self.clock(), None

    def maybe_unmask(self):
        """Restore the masked items once the spawns settled (or timed out). True if something was restored."""
        if not self.masked:
            return False
        now = self.clock()
        if self.mask_stage is not None:       # early mask: its stage has not started loading yet
            if now - self.mask_started <= EARLY_MASK_LIMIT:
                return False
        else:
            settled = self.last_spawn is not None and now - self.last_spawn > MASK_SETTLE
            if not (settled or now - self.mask_started > MASK_TIMEOUT):
                return False
        self.mask_stage = None
        for (kind, item_id), original in self.masked.items():
            maximum = self.maximum(kind, item_id)
            # at max count the game refuses the pickup ("FULL"), so leave room for it
            value = min(original, maximum - 1) if kind == "item" and maximum > 1 and original > 0 else original
            self.set_value(kind, item_id, value)
        self.masked.clear()
        return True

    @property
    def masking(self):
        """Whether a stage-load mask is active."""
        return bool(self.masked)
