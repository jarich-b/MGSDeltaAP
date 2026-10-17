"""Run logic of the MGS Delta client: run start, collection/story checks, section locks, re-entry masking,
received items, goal and DeathLink detection. Independent of Archipelago networking: the client feeds it the
received items and checked locations each tick and sends what tick() returns.

Session is the entry point; the counters with their own checks (no-alert areas, Kerotans, Gako, animals, CQC) live in
game/tracking.py and the codec status/warps in game/codec.py, both mixed into Session.
"""
import os
import random
import time
from collections import Counter

from . import effects, warp
from .bridge import MODEL_EXTRA_CATEGORIES, box_model, model_category
from .codec import AP_STATUS_FREQ, CodecMixin    # noqa: F401 (AP_STATUS_FREQ: tests)
from .constants import (CAMO_ITEM_BASE, FACEPAINT_ITEM_BASE, FACEPAINT_ITEM_END, FREE_STAGE, IN_GAME_STAGE,
                        MEDICAL_IDS)
from .forced_heal import ForcedHeal
from .probe import EscapeProbe
from .state import GameVars
from .tracking import TrackingMixin

# Dev testing of received-item boxes (env MGSDELTA_BOX_TEST=1): never count on arrival (a missing box stays visible
# and can be retried), and hide received items even after their check was sent, so their box can be checked again.
BOX_TEST = os.environ.get("MGSDELTA_BOX_TEST") == "1"
ACQ_LABELS = {"weapon": "New Weapon", "equipment": "New Equipment", "camo": "New Camo", "medical": "New Medicine"}
ACQ_BUNDLE_LABELS = {"weapon": "Weapon Bundle", "equipment": "Item Bundle", "camo": "Camo Bundle",
                     "medical": "Medicine Bundle"}
ACQ_OPTIONS = {"weapon": "weapon_checks", "equipment": "equipment_checks", "camo": "camo_checks",
               "medical": "medical_checks"}
C3_CHARGE, C3_CHARGES = "C3 Charge", 4         # = items.C3_CHARGE(S) (the game package cannot import the world)
C3 = ("weapon", 26)                           # the C3 weapon: its stock = C3 Charges received - planted (keep_c3)
C3_TANK_STAGE, C3_TANK_STORY = "s121a", 137   # the 4 fuel tanks: Main Wing from story 137 (s121a "C3 Set Stage")
GOAL_BOSS = "Ocelot (Duel)"  # the goal: winning the final gun duel with Ocelot (data/bosses.json)
PICKUP_DECIDE = 10.0         # a gain with no matching pickup event within this time is a gift (animation ~3-4 s + 2 s)
LOCK_FALLBACK = 1.5          # bounce a locked stage this long after load start if no item spawned before
BULK_GAINS = 4               # this many gains together outside a save load = the game handing out gear (backpack, ...)
GAIN_WINDOW = 1.5            # new gains wait this long before they count as finds (a spread-out hand-out is seen whole)
DEATH_DEBOUNCE = 20.0
DEATHLINK_GRACE = 10.0       # ignore our own death detection after a received DeathLink
REGRANT_SECONDS = 2.0
NO_REGRANT_SECTIONS = {"escape"}    # sections.json id of "Capture and Escape"
SHAGOHOD_SECTION = "shagohod"
SHAGOHOD_RPG = ("weapon", 17)    # the RPG-7: the story gift for the Shagohod fight (shagohod_rpg)
WIG_PREFIX = "s211"              # the WIG: the Ocelot duel's SAA comes from its box there (a full SAA can't take it)
DUEL_SAA = ("weapon", 8)
GEAR_BACK_STORY = 135        # EVA's gear return behind the waterfall (s152a_0 chain -> s152a at 135): the escape is over
MAX_POPUPS_PER_TICK = 5
ATTACH_SETTLE = 3        # s in one playable stage after (re)attaching before items are written
POPUP_SETTLE = 2        # s after the mod's stage event before labels are shown
POPUP_FALLBACK = 12      # s after a stage change when the mod's stage event is missing
# Items the game hands out itself (EZ Gun on Very Easy, re-given on every area load): starting gear, never checks.
# Not tied to the YAML difficulty, since the in-game difficulty is what matters; elsewhere it is an ending reward.
AUTO_GIFTS = {("weapon", 7)}
KEY_ITEMS = {("item", 19), ("item", 20), ("item", 21)}    # Key A, B, C: vanilla, never stripped, taken back or counted
# The Fake Death Pill is vanilla too (never in the pool): the game gives it in the cell when Snake cuts out the
# tracker with the fork, so the client never strips, takes back or counts it
FAKE_DEATH_PILL = ("item", 3)
# The Camera is vanilla as well: its box in the cell is never respawned (no timer), so it is no check
CAMERA = ("item", 9)
VANILLA_ITEMS = KEY_ITEMS | {FAKE_DEATH_PILL, CAMERA}
# Mask Face Paint (Raikov's face): handed out in the Virtuous Mission, needed worn with the Officer Camo for Raikov's
# disguise check in Groznyj Grad (s111a proc248), and given nowhere else, so the run start never wipes it
MASK_PAINT = 127
# Optional camo checks handed out without a box (= locations.SPECIAL_CAMOS): their areas, where an owned copy is hidden
SPECIAL_CAMOS = [
    {"key": ("item", 70), "location": "Hold Up The End (Moss Camo)", "option": "moss_camo_check",
     "areas": ("s063", "s064", "s065")},
    # (handed out with EVA's gear return in s152a_0 after The Sorrow, if Snake reached him at the river's end)
    {"key": ("item", 72), "location": "The Sorrow: Spirit Camo", "option": "spirit_camo_check",
     "areas": ("s141", "s151", "s152")},
]
NONLETHAL_FLAGS = 0x228       # script bit array of the non-lethal boss wins (see Session.check_nonlethal_flags)
SPECIAL_RELOAD_GRACE = 3.0    # s: an area load can put a hidden camo back a moment after the stage name changed
DISGUISE_KEYS = (("item", 65), ("item", MASK_PAINT))     # Officer Camo, Mask Face Paint (raikov_disguise randomized)
OFFICER_CAMO_TYPE, MASK_PAINT_TYPE = 65 - 51, MASK_PAINT - 117    # their worn-camo / worn-face-paint values
# Utility items a received copy is put into the active items for, and equipped (not medicine, pills, keys, suppressors;
# guns stay as they are). Cardboard boxes are made active but not equipped: equipping one puts Snake in the box.
ACTIVE_UTILITY_IDS = set(range(5, 36)) - {item_id for _, item_id in KEY_ITEMS}
NO_AUTO_EQUIP = {14, 15, 16, 17}
# Suppressor item -> its gun (weapon id): a suppressor box only appears while Snake owns that gun, so a suppressor that
# turns up without it is the game's own doing (it put back a stripped hand-out suppressor in Rassvet), never a find.
SUPPRESSOR_GUN = {("item", 48): 6, ("item", 49): 5, ("item", 50): 11}     # M1911A1, Mk22, XM16E1
REVIVAL_PILL = ("item", 4)       # needed at the end of The Sorrow's river (s141), where Snake dies on purpose
SORROW_STAGE = "s141"
STORY_STAGES = {"kyle_op"}   # non-playable stages inside a story chain (Virtuous Mission end -> kyle_op -> s000a_0 ...)
START_PREFIX = "v001"        # Virtuous Mission first area: the run starts after Snake's backpack hand-out
START_SETTLE = 1.0           # the hand-out must be quiet this long (no further gains) before the run starts
START_FALLBACK = 120.0       # seconds of play in v001 after which the run starts even without a hand-out
# Camos that only come as a boss's non-lethal reward (Hornet Stripe, Spider, Moss, Fire, Animal): never collections.
# With nonlethal_boss they are pool items (the reward comes from Archipelago), so a picked-up reward box is taken back
# like any found item unless the camo was received; without the option the game's reward is simply kept.
NONLETHAL_REWARD_CAMOS = {("item", i) for i in (68, 69, 70, 71, 73, 80)}   # + Cold War (Volgin; in no box)
# Dev testing: area prefixes (e.g. "s012") listed one per line in this file are treated as locked.
TEST_LOCK_FILE = os.environ.get("MGSDAP_TEST_LOCKS")


def test_locks():
    """Area prefixes locked for testing (MGSDAP_TEST_LOCKS file), empty normally."""
    if not TEST_LOCK_FILE:
        return set()
    try:
        with open(TEST_LOCK_FILE, encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip()}
    except OSError:
        return set()   # unknown: with it, the run start could wipe every camo except the worn one


def gate_dict(gate):
    """A section gate in dict form (slot data may carry the short [from, into] list form)."""
    return dict(gate) if isinstance(gate, dict) else {"from": gate[0], "into": gate[1]}


def count_up(counters, name):
    """Increment a named counter of the run state."""
    counters[name] = counters.get(name, 0) + 1


class TickResult:
    """What one Session.tick() asks the client to send."""

    def __init__(self):
        self.locations = []      # location ids to send
        self.goal = False
        self.death = None        # death cause text to send via DeathLink


def is_camo_key(key):
    """A uniform or face paint (item ids 51..147)."""
    return key[0] == "item" and CAMO_ITEM_BASE <= key[1] < FACEPAINT_ITEM_END


class Session(TrackingMixin, CodecMixin):
    """One run of the randomizer: feed it received items and checked locations every tick (tick()), send its result."""

    def __init__(self, bridge, modlink, state, slot_data, location_ids, item_keys, sections, exits,
                 log=print, gamestate=None, clock=time.monotonic, game_vars_factory=None, entries=None, bosses=None,
                 checkpoints=None, animals=None, detail=None):
        """location_ids: {location name: id} of the whole game; item_keys: {item name: (kind, id)};
        sections: ordered [{"id", "name", "unlock", "unlock_item", "unlock_count", "areas", "gates"}] (a section
        needs unlock_count copies of unlock_item; without those keys, "unlock" once);
        exits: {script stage: {target stage: proc}}.
        A gate is [from area, into area] or {"from", "into", "min_story"?, "any_from"?, "back"?, "back_to"?,
        "settle"?}: min_story = only while story ($var[4]) >= it; any_from = also bounce arrivals in the
        into area from any other area; back = exit-back proc of the into stage (default: from exits), back_to = the
        area it loads (log text); settle = seconds after the load start before the bounce.
        entries: {stage: [{"from", "proc", "pose", "gate"}]} for safe warps; bosses: [{"boss", "location",
        "story_at_least", "flag"?, "arena"?}]; checkpoints: the codec warps (see game/codec.py);
        animals: [{"kind", "name"}] of the capturable species (data/animals.json)."""
        self.bridge = bridge
        self.modlink = modlink
        # medicine for the two scenes that require the Cure menu
        self.forced_heal = ForcedHeal(bridge, log=log, medical_in_pool=lambda: self.medical_in_pool())
        self.state = state
        self.slot_data = slot_data or {}
        self.options = self.slot_data.get("options", {})
        bridge.track_medical = self.medical_in_pool()     # medicine gains count as finds only with medical checks
        self.location_ids = location_ids
        self.item_keys = item_keys
        self.sections = sections
        self.section_index = {section["id"]: index for index, section in enumerate(sections)}
        self.section_of_area = {area: section["id"] for section in sections for area in section["areas"]}
        # Locks only apply at gates: doors (from area, into area) with a scripted exit back. Borders entered through
        # cutscenes or one-way doors can't be bounced safely, so their section is gated at an earlier door instead.
        self.gates = [dict(gate_dict(gate), section=section["id"])
                      for section in sections for gate in section.get("gates", [])]
        self.exits = exits or {}
        self.bosses = bosses or []         # fights stay replayable
        # a boss's non-lethal reward (data/bosses.json "reward": a camo, or The End's Mosin-Nagant, "reward_kind"
        # weapon) -> the boss: only a non-lethal win makes it appear (The End's Moss Camo is a hold-up drop)
        self.reward_boss = {(boss.get("reward_kind", "item"), boss["reward"]): boss for boss in self.bosses
                            if "reward" in boss and "nonlethal" in boss}
        end_boss = next((boss for boss in self.bosses if boss.get("flag")), None)   # The End: dead by story or flag
        self.log = log
        self.detail = detail or log     # log file only: items taken back or removed (no checks)
        self.clock = clock
        self.gamestate = gamestate
        self.game_vars_factory = game_vars_factory or (lambda game: GameVars(game, gamestate))
        self.game_vars = None
        self.vars_game = None
        self.slot_locations = set()
        self.pending = []            # [{"key", "t", "counted"}]: gains waiting for a pickup event
        self.pending_lock = None     # {"stage", "prev", "t", "section"}
        self.recent_pickups = []
        self.raised = {}             # (kind, id) -> (count, maximum) before raising the maximum (see open_boxes)
        self.boxes_spawned = {}      # received key -> area: its box spawned there in this visit (only the pickup counts it)
        self.load_keys = set()       # received keys owned when the current area loaded (see count_received_boxes)
        self.box_counts = {}         # received key -> its count before its box could be picked up (see open_boxes)
        self.picked_received = {}    # received key -> time its box was picked up (see settle_received_pickups)
        self.allow_later_save = False   # /startrun: start the run on a save from later in the story
        self.later_save_warned = False     # [(time, box or None, event)]: pickups kept for PICKUP_DECIDE (see handle_pickups)
        self.stage_changed_at = float("-inf")
        self.in_wig = False                # arrived in the WIG (the SAA was removed); cleared in a playable stage outside
        self.attach_settle = ATTACH_SETTLE   # see attach_settled
        self.settled = False
        self.settle_since = None
        self.restore_until = 0.0     # gains before this time come from a save load, not from finding items
        self.gain_window = None      # {"keys", "t"}: gains not yet counted (see settle_gains)
        self.start_gains = set()     # inventory gains seen in v001 before the run started
        self.start_gain_t = 0.0
        self.start_play = 0.0        # seconds of play in v001 before the run started
        self.start_last = None
        self.handout_t = -1000.0     # time of the last stripped hand-out
        self.dress_pending = None    # legit keys of a hand-out: dress from them once in play (see dress_random)
        self.stripped_logged = set()  # hand-out gear already logged as taken back (see take_back_stripped)
        self.owned_seen = None       # keys the player legitimately owned at some point (survives capture strips)
        self.last_free_stage = None
        self.last_regrant = 0.0
        self.received_counts = Counter()
        self.death_lock_until = 0.0
        self.last_life = None
        self.last_continue = None
        self.init_tracking(location_ids, self.bosses, animals)
        self.init_codec(checkpoints or [], entries or {}, end_boss)
        self.probe = EscapeProbe(self)       # read-only logging of the Capture and Escape section ("[probe]")

    # --- option helpers -----------------------------------------------------------------------------
    def opt(self, name, default=0):
        return self.options.get(name, default)

    def acq_plan(self, acq_type):
        """(N, X): N different items of the category count (0 = off), one check per X of them (check k is sent when
        the min(k * X, N)-th new item is found). Old slot data without "acquisition": the legacy count option, X = 1."""
        plan = (self.slot_data.get("acquisition") or {}).get(acq_type)
        if plan is None:
            return int(self.opt(ACQ_OPTIONS[acq_type], 0)), 1
        return int(plan.get("total", 0)), max(1, int(plan.get("size", 1)))

    def acq_countable(self, acq_type, item_id):
        """Only items that can be picked up in the world count (slot data "items"); the game also hands out items on
        its own (e.g. cleared-game reward camos like White Tuxedo), which never count. Old slot data: everything."""
        if acq_type == "camo" and ("item", item_id) in NONLETHAL_REWARD_CAMOS:
            return False
        items = ((self.slot_data.get("acquisition") or {}).get(acq_type) or {}).get("items")
        return items is None or item_id in items

    def acq_label(self, acq_type):
        """Check name prefix of a type: bundled types use the bundle names (slot data "bundled", else size > 1)."""
        plan = (self.slot_data.get("acquisition") or {}).get(acq_type) or {}
        bundled = plan.get("bundled", int(plan.get("size", 1)) > 1)
        return (ACQ_BUNDLE_LABELS if bundled else ACQ_LABELS)[acq_type]

    def medical_in_pool(self):
        """Medical items are AP items (and finds are checks) only with medical_checks; else they stay the game's."""
        return int(self.opt("medical_checks", 0)) != 0

    def acq_open(self, acq_type):
        return len(self.state.acquired[acq_type]) < self.acq_plan(acq_type)[0]

    def acq_full(self, acq_type):
        """The category has collection checks and all of them are earned: later finds are taken back, not kept."""
        total = self.acq_plan(acq_type)[0]
        return total > 0 and len(self.state.acquired[acq_type]) >= total

    def progress(self):
        """Progress toward the counted checks of this run, for data storage (see game/progress.py): per collection
        type found / total / size / bundled / ids (game item ids, weapon ids for "weapon"), plus Kerotans, Gako ducks,
        animals, CQC takedowns, no-alert areas and the unlocked codec warp frequencies."""
        state = self.state
        acq = {}
        for acq_type in state.acquired:
            total, size = self.acq_plan(acq_type)
            plan = (self.slot_data.get("acquisition") or {}).get(acq_type) or {}
            acq[acq_type] = {"found": len(state.acquired[acq_type]), "total": total, "size": size,
                             "bundled": bool(plan.get("bundled", size > 1)),
                             "ids": sorted(state.acquired[acq_type])}   # game ids of the counted items (pickup spots)
        return {"v": 1, "acq": acq, "kerotan": len(state.kerotans), "gako": len(state.gako),
                "animal": len(state.animals), "cqc": state.cqc_total, "noalert": len(state.clean_areas),
                "warps": sorted(checkpoint["freq"] for checkpoint in self.unlocked_checkpoints())}   # codec warps

    # --- stages and sections --------------------------------------------------------------------------
    @staticmethod
    def in_game(stage):
        return bool(IN_GAME_STAGE.match(stage)) or stage in STORY_STAGES

    def connected(self, prev, stage):
        """True if stage is reached from prev by a door, an exit proc or a story cutscene; False for a save load
        (from the title/menus or another save) or a warp."""
        if not (prev and self.in_game(prev) and self.in_game(stage)):
            return False
        if not (FREE_STAGE.match(prev) and FREE_STAGE.match(stage)):
            return True                       # cutscene stages chain into each other and into the story stage
        return stage in self.exits.get(prev, {}) or prev in self.exits.get(stage, {}) or prev[:4] == stage[:4]

    def section_of(self, stage):
        return self.section_of_area.get((stage or "")[:4])

    @staticmethod
    def section_need(section):
        """(item, count) needed to enter a section, or None."""
        item = section.get("unlock_item", section.get("unlock"))
        count = section.get("unlock_count", 1 if item else 0)
        return (item, count) if item and count > 0 else None

    def section_locked(self, section_id, received_counts):
        """received_counts: {item name: copies received}."""
        need = next((self.section_need(section) for section in self.sections if section["id"] == section_id), None)
        return bool(need) and received_counts.get(need[0], 0) < need[1]

    def need_text(self, section):
        """What a locked section needs, e.g. "Sokrovenno Exit" or "4 C3 Charges (2/4)"."""
        need = self.section_need(section)
        if not need:
            return section.get("unlock") or "nothing"
        item, count = need
        if section.get("unlock") == item:
            return item
        short = f"{item}s"
        return f"{count} {short} ({min(self.received_counts.get(item, 0), count)}/{count})"

    def tick(self, received, checked, slot_locations):
        """received: [(item name, sender name)] in order; checked: checked location ids; slot_locations: ids of
        every location of this slot. Returns a TickResult."""
        result = TickResult()
        self.slot_locations = slot_locations
        self.checked_ids = set(checked)
        bridge = self.bridge
        now = self.clock()
        received_counts = self.received_counts = Counter(name for name, _ in received)
        received_keys = self.received_item_keys(received_counts)

        bridge.refresh()
        new_stage = bridge.check_stage()
        stage = bridge.stage
        self.probe.tick(stage, "game")

        # Run start: once per seed/slot (see start_ready). Nothing counts before it.
        if not self.state.initialized:
            if not self.start_ready(stage, now):
                return result
            self.run_start()

        if new_stage:
            self.on_stage_change(new_stage, received_counts, received_keys, now)
            self.forget_spawned_boxes(new_stage)
        else:
            upcoming = bridge.check_next_stage()
            if upcoming and FREE_STAGE.match(upcoming):
                self.mask_early(upcoming, received_keys)
        self.note_free_stage(stage)
        self.update_inventory(result, stage, received_keys, now)
        if not new_stage and not bridge.masking and self.in_play(stage, now) and self.state.initialized:
            self.count_received_boxes(result, stage, received_keys)
            self.apply_dress()
        if not bridge.masking and self.attach_settled(stage, new_stage, now):
            # new items only in play: a weapon granted while the next area loaded crashed the game (SVD, s041a -> s043a)
            if not new_stage and self.in_play(stage, now):
                self.apply_received(received, stage)
            # regrant only what was already applied: a new item waits for apply_received (in play)
            applied = Counter(name for name, _ in received[:self.state.applied_count])
            self.regrant(self.received_item_keys(applied), stage, now)
            label = self.forced_heal.tick(stage, self.read_story(), self.read_vm_story())
            if label:
                self.modlink.popup("Archipelago", label)
        if stage and FREE_STAGE.match(stage):
            self.track_story()
        self.handle_bosses(result, stage)
        self.handle_codec_warp(stage, now)
        self.handle_cqc(result, now)
        self.handle_no_alert(result, stage, new_stage, now)
        self.handle_kerotans(result, stage, now)
        self.handle_gako(result)
        self.handle_animals(result, stage, now)
        self.handle_story(result, stage, received_counts)
        self.check_nonlethal_flags(result, stage)
        self.handle_goal(result, stage, received)
        self.handle_death(result, now)
        self.update_codec(received_counts)
        if new_stage:
            self.stage_changed_at = now
        self.modlink.flush(self.in_play(stage, now))
        self.modlink.clear_due()
        if stage and FREE_STAGE.match(stage):           # the offline-gain baseline: playable stages only (see above)
            self.state.inventory = sorted(list(key) for key in bridge.known or ())
        self.probe.tick(stage, "client")
        self.state.save()
        return result

    def attach_settled(self, stage, new_stage, now):
        """After (re)attaching, item writes wait until Snake has stood in one playable stage for attach_settle
        seconds: a client that reconnected in the middle of a stage load and granted items at once crashed the game
        (a null component in a Blueprint while the next area loaded)."""
        if self.settled:
            return True
        if new_stage or self.settle_since is None or not (stage and FREE_STAGE.match(stage)):
            self.settle_since = now if stage and FREE_STAGE.match(stage) else None
            return self.attach_settle <= 0 and self.settle_since is not None
        if now - self.settle_since >= self.attach_settle:
            self.settled = True
        return self.settled

    def in_play(self, stage, now):
        """Whether an on-screen label would be seen: a playable stage, settled after its load (the mod's stage event
        for it is POPUP_SETTLE old, or the load started POPUP_FALLBACK ago when that event is missing)."""
        if not stage or not FREE_STAGE.match(stage):
            return False
        if self.pending_lock and self.pending_lock["stage"] == stage:
            return False         # a locked stage is left at once: its "Locked" popup would be lost in the bounce load
        mod = self.bridge.last_mod_stage
        if mod is not None and mod[0] == stage and mod[1] >= self.stage_changed_at - 2:   # read in the same tick
            return now - mod[1] >= POPUP_SETTLE
        return now - self.stage_changed_at >= POPUP_FALLBACK

    def received_item_keys(self, received_counts):
        """(kind, id) keys of the received items that exist in the game, including the starting camo."""
        keys = {self.item_keys[name] for name in received_counts if name in self.item_keys}
        for start in ("starting_camo", "starting_facepaint", "starting_wearable"):
            camo = self.slot_data.get(start)
            if camo in self.item_keys:
                keys.add(self.item_keys[camo])
        return keys

    def on_stage_change(self, new_stage, received_counts, received_keys, now):
        """A stage load just started: note jumps (save load / warp), then set up locks and masking."""
        if not self.connected(self.bridge.previous_stage, new_stage):
            self.restore_until = now + PICKUP_DECIDE   # story_max is kept: an older save never lowers it
            self.log(f"Jumped from {self.bridge.previous_stage} to {new_stage} (save load or warp); "
                     "inventory changes now are not checks")
        self.on_load_start(new_stage, received_counts, received_keys)

    def note_free_stage(self, stage):
        """Remember the last playable stage (the bounce target of a lock) and the areas visited in this run."""
        if stage and FREE_STAGE.match(stage) and not (self.pending_lock and self.pending_lock["stage"] == stage):
            self.last_free_stage = stage
            if stage[:4] not in self.state.visited:
                self.state.visited.append(stage[:4])

    def update_inventory(self, result, stage, received_keys, now):
        """Pickups and inventory gains (collection checks), then the lock bounce and the end of masking."""
        bridge = self.bridge
        pickups = bridge.poll_events()
        self.probe.pickups(stage, pickups)
        if stage and self.in_game(stage):
            # before the gains: a hidden Moss / Spirit Camo handed out is restored first, never seen as a find
            self.keep_special_camos(result, stage, received_keys, now)
            self.handle_inventory(result, pickups, received_keys, now, stage)
        else:
            bridge.known = None      # title screen / menus: re-baseline (against the saved inventory) later
        self.keep_c3(stage)
        self.keep_disguise(stage, received_keys)
        self.handle_lock(now)
        if bridge.maybe_unmask():
            bridge.rebaseline()
        self.settle_received_pickups(self.clock())
        self.keep_raised()

    def regrant(self, received_keys, stage, now):
        """Re-grant received items after save reloads. Not during the capture: the story strips Snake's gear on purpose
        and gives it back later; received items return once EVA gave the gear back (story 135, behind the waterfall)."""
        stripped = self.gear_taken(stage)
        sorrow = bool(stage) and stage.startswith(SORROW_STAGE)     # gear is back; Snake dies here and needs the pill
        if (not stripped or sorrow) and now - self.last_regrant > REGRANT_SECONDS:
            self.last_regrant = now
            wig = bool(stage) and stage.startswith(WIG_PREFIX)
            for key in received_keys:
                if (not stripped or key == REVIVAL_PILL) and not (wig and key == DUEL_SAA):
                    self.bridge.grant(*key)

    def gear_taken(self, stage):
        """Whether Snake is in the capture and escape without his gear: the Capture and Escape section before EVA's
        gear return (story GEAR_BACK_STORY; an unreadable story counts as before)."""
        if not stage or self.section_of(stage) not in NO_REGRANT_SECTIONS:
            return False
        story = self.read_story()
        return story is None or story < GEAR_BACK_STORY

    # --- run start ----------------------------------------------------------------------------------
    def start_ready(self, stage, now):
        """Whether the run starts now. In the Virtuous Mission's first area (v001) it starts right after the game's
        backpack hand-out (>= BULK_GAINS gains, quiet for START_SETTLE), or after START_FALLBACK seconds of play there
        (a save from later in the mission); in any other playable stage it starts at once."""
        bridge = self.bridge
        if not (stage and self.in_game(stage)):
            bridge.known = None          # title screen / menus: baseline again later
            return False
        free = bool(FREE_STAGE.match(stage))
        if not stage.startswith("v") and not self.allow_later_save:
            # A seed's run starts in a new game. A save from later in the story would be wiped and played from the
            # middle; the player can force it with /startrun.
            if free and not self.later_save_warned:
                self.later_save_warned = True
                self.log("No run started for this seed yet: start a NEW game (the run begins after the Virtuous "
                         "Mission backpack). To start the run on this save anyway, use /startrun.")
                self.modlink.popup("Archipelago", "Start a new game for this seed", style="warning")
            return False
        if stage[:4] != START_PREFIX:
            return free and not bridge.masking
        last, self.start_last = self.start_last, now
        if free and last is not None:
            self.start_play += min(now - last, 1.0)
        gained = bridge.diff()
        if gained:
            self.start_gains |= gained
            self.start_gain_t = now
        if not free or bridge.masking:
            return False
        if len(self.start_gains) >= BULK_GAINS and now - self.start_gain_t >= START_SETTLE:
            self.log(f"Backpack hand-out seen ({len(self.start_gains)} items); starting the run")
            return True
        if self.start_play >= START_FALLBACK:
            self.log("No backpack hand-out seen; starting the run anyway")
            return True
        return False

    def run_start(self):
        """Wipe the inventory (except the camo to keep), grant the starting gear and zero the per-run counters."""
        bridge = self.bridge
        keep_camo = self.camo_to_keep()
        keep_items = {item_id for _, item_id in VANILLA_ITEMS}   # the game's (key doors are story-gated; the pill)
        if not self.opt("raikov_disguise", 0):
            keep_items.add(MASK_PAINT)               # (randomized: the disguise comes from Archipelago)
        if not self.medical_in_pool():
            keep_items |= set(MEDICAL_IDS)       # medicine is vanilla unless medical checks put it in the pool
        bridge.wipe(keep_camos=keep_camo is None, keep_camo=keep_camo, keep_items=keep_items)
        names = self.starting_gear_names()
        for name in names:
            if name in self.item_keys:
                bridge.grant(*self.item_keys[name])
        worn_text = self.wear_starting_camo(keep_camo) + self.wear_starting_item()
        self.zero_kerotans()
        bridge.rebaseline()
        self.state.initialized = True
        self.state.inventory = sorted(list(key) for key in bridge.known)
        self.state.save()
        self.log(f"Run start: inventory wiped, starting gear: {', '.join(names) or 'nothing'}{worn_text}")
        self.modlink.popup("Archipelago", "Inventory reset for the randomizer. Good luck, Snake.")

    def camo_to_keep(self):
        """Item id of the one camo the run start keeps (None: keep every camo, the worn state is unreadable).
        Camo wipe: no camos or face paints except the starting camo (vanilla start: the one worn now) and "no paint"."""
        start_camo = self.slot_data.get("starting_camo")
        start_key = self.item_keys.get(start_camo) if start_camo else None
        if start_key and not (start_key[0] == "item" and CAMO_ITEM_BASE <= start_key[1] < FACEPAINT_ITEM_BASE):
            start_key = None
        worn = self.read_worn()                       # (camo type, face paint type) or None
        if start_key:
            return start_key[1]
        if worn and worn[0] < FACEPAINT_ITEM_BASE - CAMO_ITEM_BASE:
            return CAMO_ITEM_BASE + worn[0]
        return None

    def starting_gear_names(self):
        """Names granted at the run start: the starting items, camo, wearable item and face paint."""
        names = list(self.slot_data.get("starting_items") or [])
        if self.slot_data.get("starting_camo"):
            names.append(self.slot_data["starting_camo"])
        for slot_key in ("starting_wearable", "starting_facepaint"):
            name = self.slot_data.get(slot_key)
            if name and name in self.item_keys:
                names.append(name)
        return names

    def wear_starting_camo(self, keep_camo):
        """Own and wear the kept camo with the starting face paint. Returns the log text ("" without a kept camo)."""
        if keep_camo is None:
            return ""
        bridge = self.bridge
        start_paint = self.slot_data.get("starting_facepaint")
        paint_key = self.item_keys.get(start_paint) if start_paint else None
        bridge.grant("item", keep_camo)
        bridge.grant("item", FACEPAINT_ITEM_BASE)                      # "No Paint" stays owned
        paint_type = paint_key[1] - FACEPAINT_ITEM_BASE if paint_key else 0
        if self.write_worn(keep_camo - CAMO_ITEM_BASE, paint_type):
            self.modlink.camo_refresh()
        text = f"; worn camo {bridge.item_by_key.get(('item', keep_camo), {}).get('name', keep_camo)}"
        if paint_key:
            text += f", face paint {start_paint}"
        return text

    def wear_starting_item(self):
        """Equip the starting wearable item (AT Camo, a cap, a mask, ...) at once. Returns the log text."""
        wearable = self.slot_data.get("starting_wearable")
        wear_key = self.item_keys.get(wearable) if wearable else None
        if not wear_key or not self.ready_item(wear_key[1]):
            return ""
        return f"; wearing {wearable}"

    def ready_item(self, item_id):
        """Put a received utility item into the active items (the first free slot; with all slots taken, the last one
        that isn't equipped) and equip it, so it isn't only in the backpack. Returns whether it was written."""
        if item_id not in ACTIVE_UTILITY_IDS:
            return False
        try:
            game_vars = self.vars()
            slots = game_vars.active_items()
            if item_id not in slots:
                equipped = game_vars.get("equipped_item")
                free = [i for i, slot_item in enumerate(slots) if slot_item == 0]
                taken = [i for i, slot_item in enumerate(slots) if slot_item != equipped]
                if not free and not taken:
                    return False
                game_vars.set_active_item(free[0] if free else taken[-1], item_id)
            if item_id not in NO_AUTO_EQUIP:
                game_vars.set("equipped_item", item_id)
            return True
        except (OSError, KeyError):
            return False

    def read_worn(self):
        """(worn camo type, worn face paint type) from GM+0x7AE / GM+0x7AF, or None if unreadable."""
        return self.vars_get("worn_camo", "worn_paint")

    def write_worn(self, camo_type, paint_type=0):
        """Equip a camo / face paint by type (the items must be owned). The model needs a camo_refresh afterwards."""
        try:
            game_vars = self.vars()
            game_vars.set("worn_camo", camo_type)
            game_vars.set("worn_paint", paint_type)
            return True
        except (OSError, KeyError):
            return False

    # --- stage load start: locks and masking ---------------------------------------------------------
    def on_load_start(self, stage, received_counts, received_keys):
        bridge = self.bridge
        self.pending_lock = None
        if FREE_STAGE.match(stage) and stage[:4] in test_locks():
            prev = self.last_free_stage
            if prev and prev[:4] != stage[:4]:
                self.pending_lock = {"stage": stage, "prev": prev, "t": self.clock(), "section": None}
        elif FREE_STAGE.match(stage) and self.last_free_stage:
            prev = self.last_free_stage
            gate = self.closed_gate(prev, stage, received_counts)
            if gate:
                self.pending_lock = {"stage": stage, "prev": prev, "t": self.clock(), "section": gate["section"],
                                     "proc": gate.get("back"), "to": gate.get("back_to"),
                                     "settle": gate.get("settle", 0)}
        self.mask_for(stage, received_keys, "Masking")
        self.load_keys = {key for key in received_keys if key in bridge.masked or bridge.owned(*key)}
        if bridge.masked:          # the spawns come after the stage change: settle from here (also after mask_early)
            bridge.mask_started, bridge.last_spawn = bridge.clock(), None
        bridge.mask_stage = None
        self.open_boxes(stage, received_keys)

    def mask_early(self, stage, received_keys):
        """The game announced the next stage (0.3-0.8 s before its name changes; ~10 s for Virtuous Mission loads): mask
        for it now, ahead of the area script that places its boxes (masking only at the stage change loses that race
        now and then). The mask holds until that stage's load starts (bridge.mask_stage, at most EARLY_MASK_LIMIT)."""
        self.mask_for(stage, received_keys, "Early masking")
        if self.bridge.masked:
            self.bridge.mask_stage = stage

    def mask_for(self, stage, received_keys, label):
        bridge = self.bridge
        hide, show, ammo = self.mask_plan(stage, received_keys)
        hide = {key for key in hide if key not in bridge.masked}
        show = {key for key in show if key not in bridge.masked}
        names = lambda keys: ", ".join(bridge.item_by_key.get(key, {}).get("name", str(key)) for key in sorted(keys))
        if hide or show:
            bridge.begin_mask(hide, show)
            self.log(f"{label} for {stage} at {time.time():.2f}: hidden {names(hide) or '-'}; shown {names(show) or '-'}")
        timers = bridge.clear_box_timers(stage, hide | ammo)    # a box picked up earlier would stay away for a while
        if timers:
            self.log(f"  box respawn timer cleared ({stage}): {names(timers)}")

    def keep_c3(self, stage):
        """C3 only fits the 4 fuel tanks in the Main Wing (story >= 137), and the story only goes on once all 4 are
        planted, so the C3 Charges are gate 3. No C3 until all 4 charges are received, then all 4 at once: planting
        some and warping back would lose them (the Main Wing script resets the tanks at story 137, "C3 Clear"). The
        stock is then 4 minus the ones planted; the game's own C3 (the gear it gives back after the escape) is replaced
        by it. A planting is the stock going down by one on the tanks' stage; the planted count is reset whenever the
        story is below 137 (the tanks are reset when Snake gets there again), and any other change by the game (a save
        reload, the hand-out) can only lower it: an uncertain case gives a charge too many (harmless: only the tanks
        take C3), never one too few. Not during the capture: the story takes Snake's gear and gives it back itself."""
        if not stage or not FREE_STAGE.match(stage) or self.bridge.masking:
            return
        if self.gear_taken(stage):
            self.c3_last = None
            return
        bridge, counters = self.bridge, self.state.counters
        complete = self.received_counts.get(C3_CHARGE, 0) >= C3_CHARGES
        planted = counters.get("c3_planted", 0)
        try:
            stock = bridge.value(*C3)
            story = self.read_story()
            last = getattr(self, "c3_last", None)
            if story is not None and story < C3_TANK_STORY:
                planted = 0
            elif last is not None and stock != last:
                if stock == last - 1 and stage.startswith(C3_TANK_STAGE) and story is not None:
                    planted += 1
                    self.log(f"C3 Charge planted ({planted}/{C3_CHARGES})")
                else:
                    planted = min(planted, max(0, C3_CHARGES - max(stock, 0)))
            if planted != counters.get("c3_planted", 0):
                counters["c3_planted"] = planted
                self.state.save()
            target = C3_CHARGES - planted if complete else -1
            if stock != target:
                bridge.set_value(*C3, target)
                if complete and last == -1:
                    self.log(f"All {C3_CHARGES} C3 Charges received: C3 given")
            self.c3_last = target
        except OSError:
            pass

    def keep_disguise(self, stage, received_keys):
        """raikov_disguise randomized: the Officer Camo and the Mask face paint are Archipelago items. The game's own
        copies (the Mask from the Virtuous Mission, the uniform from knocking out Raikov) are taken back unless they
        were received, and taken off if worn (the disguise check in s111a only reads what is worn)."""
        if not self.opt("raikov_disguise", 0) or not stage or not FREE_STAGE.match(stage) or self.bridge.masking:
            return
        bridge = self.bridge
        try:
            for key in DISGUISE_KEYS:
                if key in received_keys or not bridge.owned(*key):
                    continue
                bridge.unown(*key)
                self.detail(f"Took back {bridge.item_by_key.get(key, {}).get('name', key)} (Raikov's disguise comes "
                         f"from Archipelago)")
            game_vars = self.vars()
            changed = False
            if game_vars.get("worn_camo") == OFFICER_CAMO_TYPE and ("item", 65) not in received_keys:
                fallback = next((item_id - 51 for item_id in range(51, 117)
                                 if item_id != 65 and bridge.owned("item", item_id)), 0)
                game_vars.set("worn_camo", fallback)
                changed = True
            if game_vars.get("worn_paint") == MASK_PAINT_TYPE and ("item", MASK_PAINT) not in received_keys:
                game_vars.set("worn_paint", 0)            # No Paint
                changed = True
            if changed:
                self.modlink.camo_refresh()
        except (OSError, KeyError):
            pass

    def special_camo(self, key):
        """The SPECIAL_CAMOS entry of key if its option is on and its check is still open, else None."""
        for camo in SPECIAL_CAMOS:
            if camo["key"] == key and self.opt(camo["option"], 0):
                lid = self.location_ids.get(camo["location"])
                if lid in self.slot_locations and lid not in getattr(self, "checked_ids", ()):
                    return camo
        return None

    def special_found(self, result, camo):
        self.send_location(result, camo["location"])
        self.checked_ids = getattr(self, "checked_ids", set()) | {self.location_ids.get(camo["location"])}
        self.log(f"{camo['location']}: found")

    def keep_special_camos(self, result, stage, received_keys, now):
        """Moss / Spirit Camo checks: the game drops or hands out these camos only while none is owned, so in their
        areas an owned copy is hidden (count kept in the run state, also when worn: the game keeps it on) until the
        check is done. Every area load puts the inventory table back, so the copy is hidden again after a load; the
        camo coming back on the same stage later than that is the game handing it out: the check, and the hidden count
        is restored (the copy found is not the player's)."""
        if not stage or not FREE_STAGE.match(stage) or self.bridge.masking:
            return
        bridge, counters = self.bridge, self.state.counters
        if getattr(self, "special_seen", None) is None:
            self.special_seen = {}               # key -> (stage, time Snake got there): arrivals and loads
        for camo in SPECIAL_CAMOS:
            key, field = camo["key"], f"hidden_{camo['key'][1]}"
            hidden = counters.get(field, 0)
            open_check = self.special_camo(key) is not None
            here = stage[:4] in camo["areas"]
            if self.special_seen.get(key, (None,))[0] != stage:
                self.special_seen[key] = (stage, now)
            arrived = now - self.special_seen[key][1] <= SPECIAL_RELOAD_GRACE   # (the load may restore the table late)
            try:
                if open_check and here and bridge.owned(*key):
                    if arrived:                          # owned on arrival (or put back by the load): hide it
                        counters[field] = max(hidden, bridge.value(*key))
                        bridge.unown(*key)
                        if not hidden:
                            self.log(f"{bridge.item_by_key.get(key, {}).get('name', key)} hidden here: "
                                     f"{camo['location']} is a check")
                        self.state.save()
                    elif hidden:                         # handed out while the copy was hidden: the check
                        self.special_found(result, camo)
                        bridge.set_value(*key, hidden)
                        counters[field] = 0
                        self.state.save()
                    # (not hidden, later: a camo the player did not own came: the gain counts it, see count_acquisitions)
                elif hidden and (not here or not open_check):
                    bridge.set_value(*key, max(bridge.value(*key), hidden))
                    counters[field] = 0
                    self.state.save()
            except OSError:
                pass

    def closed_gate(self, prev, stage, received_counts):
        """The locked gate that the load prev -> stage passes, or None."""
        for gate in self.gates:
            if stage[:4] != gate["into"] or prev[:4] == gate["into"]:
                continue
            if prev[:4] != gate["from"] and not gate.get("any_from"):
                continue
            if not self.section_locked(gate["section"], received_counts):
                continue
            if gate.get("min_story") is not None:
                story = self.read_story()
                if story is None or story < gate["min_story"]:
                    continue
            return gate
        return None

    def mask_plan(self, stage, received_keys):
        """hide: owned items whose box should spawn (collection still open for an item the player owns without having
        found it); show: taken-back items, granted during the load so their box spawns as ammo or not at all;
        ammo: received weapons in the same situation. They are not hidden: the game refuses a weapon box while the
        weapon is owned (any stock, any maximum), so their box comes as the ammo box and that pickup counts them."""
        bridge = self.bridge
        hide, show, ammo = set(), set(), set()
        taken = self.state.taken_back_keys()
        for box in bridge.boxes_in_stage(stage):
            key = bridge.vanilla_of[box["id"]]
            if key[0] not in ("weapon", "item") or key[1] is None or key not in bridge.item_by_key:
                continue
            acq_type = bridge.category(*key)
            if acq_type is None:
                continue
            owned = bridge.owned(*key)
            if (owned and key in received_keys and self.acq_open(acq_type) and self.acq_countable(acq_type, key[1])
                    and (key[1] not in self.state.acquired[acq_type] or BOX_TEST)):
                (ammo if key[0] == "weapon" else hide).add(key)
            if not owned and key in taken and key not in received_keys:
                show.add(key)
        # an owned boss reward camo (received, or the starting camo): its box is only placed at count 0, like any box
        # (The End's camo is a drop, no box: nothing to hide)
        boxed = {bridge.vanilla_of[box["id"]] for box in bridge.boxes_in_stage(stage)}
        for key, boss in self.reward_boss.items():
            if (key[0] == "item" and key in boxed and self.opt("nonlethal_boss", 0)
                    and boss["boss"] not in self.state.nonlethal and bridge.owned(*key)):
                hide.add(key)
            if key in boxed and boss["boss"] not in self.state.nonlethal:
                show.discard(key)    # a taken-back reward shown during the load would keep the box away
        show -= hide
        return hide, show, ammo

    def handle_lock(self, now):
        """Bounce Snake back through the exit of a locked stage once it has loaded (or LOCK_FALLBACK passed)."""
        lock = self.pending_lock
        if not lock:
            return
        bridge = self.bridge
        if bridge.stage != lock["stage"]:
            self.pending_lock = None
            return
        spawned = bridge.last_spawn is not None and bridge.last_spawn >= lock["t"]
        mod_loaded = bridge.last_mod_stage is not None and bridge.last_mod_stage[0] == lock["stage"] \
            and bridge.last_mod_stage[1] >= lock["t"]
        if lock.get("done") or not (spawned or mod_loaded or now - lock["t"] >= LOCK_FALLBACK):
            return
        if now - lock["t"] < lock.get("settle", 0):
            return               # fallback gates wait for the stage to settle (its entry cutscene may be racing)
        lock["done"] = True          # kept until the stage changes, so the locked stage never becomes last_free_stage
        section = next((candidate for candidate in self.sections if candidate["id"] == lock["section"]),
                       {"name": f"test lock {lock['stage']}", "unlock": "test unlock"})
        proc = lock.get("proc") or self.exits.get(lock["stage"], {}).get(lock["prev"])
        if proc is None:
            # Never warp: a bare warp spawns at a leftover pose (often under the map) and races loads/menus.
            self.modlink.popup("Locked", f"needs {self.need_text(section)}", style="warning")
            self.log(f"{section['name']} is locked but {lock['stage']} has no exit back to {lock['prev']}; "
                     "not bouncing")
            return
        self.modlink.exec_proc(proc)
        self.modlink.popup("Locked", f"needs {self.need_text(section)}", style="warning")
        count_up(self.state.counters, "locks")
        self.log(f"{section['name']} is locked ({self.need_text(section)}); "
                 f"sending Snake back to {lock.get('to') or lock['prev']}")

    # --- inventory: collection checks -----------------------------------------------------
    def handle_inventory(self, result, pickups, received_keys, now, stage):
        """Turn inventory gains and pickup events into checks: collection counts and take-backs.
        "Owned before" (kept by hand-out strips) is only learnt in playable stages: the title screen and story stages
        like kyle_op hold the profile's default kit, which once survived the Operation Snake Eater hand-out."""
        bridge = self.bridge
        first = bridge.known is None
        playable = bool(FREE_STAGE.match(stage))
        if self.owned_seen is None:
            self.owned_seen = {tuple(key) for key in self.state.inventory or ()}
            if playable:
                self.owned_seen |= set(bridge.known or ())
        gained, t_gain = self.settle_gains(first, pickups, received_keys, now, result)
        gained = self.shagohod_gift(gained, received_keys, stage)
        gained, pickups = self.duel_saa(gained, pickups, stage)
        self.count_acquisitions(result, gained, t_gain, received_keys, pickup_seen=bool(pickups or self.recent_pickups))
        self.handle_pickups(pickups, received_keys, now, result)
        self.check_owned_rewards(result)
        self.expire_pending(now)
        if self.gain_window is None and playable:
            self.owned_seen |= bridge.known

    def settle_gains(self, first, pickups, received_keys, now, result=None):
        """Read the game's inventory gains. Returns (gains that are finds or story gifts, the time they first
        appeared): gains wait in gain_window until a pickup event or GAIN_WINDOW passes, a bulk of them is a
        hand-out (stripped) and gains right after a save load are restored gear."""
        bridge = self.bridge
        state = self.state
        gained = bridge.diff()
        if first and state.inventory is not None:
            # first read after (re)attaching or leaving a menu: compare with what we saw last time
            gained = bridge.known - {tuple(key) for key in state.inventory}
        if gained and (first or now < self.restore_until):
            # save load / foreign save: not acquisitions, and the save's gear stays. Re-take known box items.
            for key in gained:
                if state.is_taken_back(key) and key not in received_keys:
                    bridge.unown(*key)
            self.log(f"Inventory restored ({len(gained)} items); not counted as checks")
            self.gain_window = None
            gained = set()
        elif first:
            self.gain_window = None
        window = self.gain_window
        if gained:
            if window is None:
                window = self.gain_window = {"keys": set(), "t": now}
            window["keys"] |= gained
        gained = set()
        if window:
            # boxes picked up while the gains came in: these are finds, however many (four quick pickups at Bolshaya
            # Past once looked like a hand-out); the game's hand-outs come without pickup events
            picked = bool(pickups) or any(t >= window["t"] - GAIN_WINDOW for t, _, _ in self.recent_pickups)
            if len(window["keys"]) >= BULK_GAINS and not picked:
                # a hand-out by the game, not finds: strip what the player should not have, count nothing
                self.strip_handout(window["keys"], received_keys, result)
                self.gain_window = None
            elif pickups or now - window["t"] >= GAIN_WINDOW:
                gained, self.gain_window = window["keys"], None     # settled: these are finds (or story gifts)
        return gained - AUTO_GIFTS - VANILLA_ITEMS, (window["t"] if window else now)

    def duel_saa(self, gained, pickups, stage):
        """The WIG: the SAA is removed once on arrival from outside (a received SAA is not given back in the WIG), so
        the duel's SAA box can be picked up; that SAA is the game's, counts nothing and stays through the duel (its
        cutscenes reload the WIG: removing it again there left Snake without a gun). Leaving regrants a received SAA.
        Returns the gains and pickups left to handle."""
        if not (stage and stage.startswith(WIG_PREFIX)):
            if stage and FREE_STAGE.match(stage):
                self.in_wig = False
            return gained, pickups
        if FREE_STAGE.match(stage) and not self.in_wig:
            self.in_wig = True
            if self.bridge.owned(*DUEL_SAA):
                self.bridge.unown(*DUEL_SAA)
                self.detail("Removed the SAA in the WIG (the duel's SAA box can be picked up; given back on leaving)")
        pickups = [(box, event) for box, event in pickups
                   if not (box and self.bridge.vanilla_of.get(box["id"]) == DUEL_SAA)]
        return gained - {DUEL_SAA}, pickups

    def shagohod_gift(self, gained, received_keys, stage):
        """The RPG-7 the game gives for the Shagohod fight. shagohod_rpg vanilla: a story gift that stays, even though
        EVA's gear hand-out marked it stripped (it is counted like any gift). randomized: taken back unless received.
        Returns the gains left to count."""
        if SHAGOHOD_RPG not in gained or self.section_of(stage) != SHAGOHOD_SECTION:
            return gained
        state = self.state
        if not self.opt("shagohod_rpg", 0):
            for marks in (state.stripped, state.taken_back):
                while list(SHAGOHOD_RPG) in marks:
                    marks.remove(list(SHAGOHOD_RPG))
            state.add_kept(SHAGOHOD_RPG)
            return gained
        if SHAGOHOD_RPG in received_keys:
            return gained
        self.bridge.unown(*SHAGOHOD_RPG)
        state.add_taken_back(SHAGOHOD_RPG)
        self.detail("Took back the RPG-7 (the Shagohod gift: not received)")
        return gained - {SHAGOHOD_RPG}

    def count_acquisitions(self, result, gained, t_gain, received_keys=(), pickup_seen=False):
        """New items of an acquisition category count towards "New <type> k" and wait for a pickup event.
        Hand-out gear the game gives back without a pickup (it restored the stripped M1911A1 and suppressor after the
        Rassvet Ocelot scene) is taken back again and never counted."""
        state = self.state
        for key in sorted(gained):
            if state.is_stripped(key) and key not in received_keys and not pickup_seen:
                # wait for a pickup event like any find (it can come seconds late): counted then, else given back.
                # A camo goes at once (the game puts a stripped White Tuxedo back on every area load), logged once.
                taken = is_camo_key(key)
                if taken:
                    self.take_back_stripped(key)
                self.pending.append({"key": key, "t": t_gain, "counted": False, "stripped": True, "taken": taken})
                continue
            gun = SUPPRESSOR_GUN.get(key)
            if gun and key not in received_keys and not self.bridge.owned("weapon", gun):
                self.bridge.unown(*key)
                self.detail(f"Removed {self.bridge.item_by_key.get(key, {}).get('name', key)} (no gun: not a find)")
                continue
            acq_type = self.bridge.category(*key)
            # a received item is the player's already: it counts from its box's pickup event (handle_pickups), never
            # from reappearing in the inventory (the game restored hidden goggles during a load: a false count)
            counted = key not in received_keys and self.count_find(result, key)
            reward = ((key in NONLETHAL_REWARD_CAMOS or key in self.reward_boss) and self.opt("nonlethal_boss", 0)
                      and key not in received_keys)
            special = self.special_camo(key)
            if special and key not in received_keys:
                self.special_found(result, special)        # not owned before: the camo's coming is the check
                reward = True                             # (taken back: the check's item comes from Archipelago)
            if reward and key in self.reward_boss:
                self.nonlethal_won(result, self.reward_boss[key])   # the reward only exists after a non-lethal win
            # a find after the category's checks are all earned: no check, but still not the player's (taken back on
            # its pickup event; without one it is a story gift and stays)
            after_quota = (acq_type and self.acq_full(acq_type) and self.acq_countable(acq_type, key[1])
                           and key not in received_keys)
            if counted or state.is_taken_back(key) or reward or after_quota:
                self.pending.append({"key": key, "t": t_gain, "counted": counted, "reward": bool(reward)})

    def countable_find(self, key):
        """Whether finding this item now earns collection progress (a new item of an open, counted category)."""
        acq_type = self.bridge.category(*key)
        return bool(acq_type) and (key[1] not in self.state.acquired[acq_type] and self.acq_open(acq_type)
                                   and self.acq_countable(acq_type, key[1]))

    def count_find(self, result, key):
        """Count a found item towards "New <type> k" (sent at every bundle size and at the last one). Returns whether it
        counted."""
        if not self.countable_find(key):
            return False
        acq_type = self.bridge.category(*key)
        acquired = self.state.acquired[acq_type]
        acquired.append(key[1])
        total, size = self.acq_plan(acq_type)
        found = len(acquired)
        item_name = self.bridge.item_by_key.get(key, {}).get("name", key)
        if found % size == 0 or found == total:
            name = f"{self.acq_label(acq_type)} {-(-found // size)}"
            self.send_location(result, name)
            self.log(f"{name}: {item_name} ({found}/{total})")
        else:
            self.log(f"{self.acq_label(acq_type)} progress {found}/{total}: {item_name}")
        return True

    def open_boxes(self, stage, received_keys):
        """The game refuses a box while its item is at the maximum ("UNIFORM FULL": camos, goggles, masks; a weapon at
        full ammo), so in the area of such a box the maximum is one higher:
        - an owned boss reward camo (received from Archipelago): the camo's count going up is the reward picked up
          (check_owned_rewards);
        - a received item not found yet: its box is still a find; the pickup is counted from the mod's event
          (handle_pickups) and the extra count or ammo is removed again.
        Whatever the previous area raised is put back first."""
        self.restore_raised()
        bridge = self.bridge
        for key, boss in self.reward_boss.items():          # an owned reward camo: its box / drop must be accepted
            if (key[0] == "item" and stage[:4] in boss.get("reward_areas", ()) and self.opt("nonlethal_boss", 0)
                    and boss["boss"] not in self.state.nonlethal):
                count = bridge.masked.get(key, bridge.value(*key))
                maximum = 1           # a camo's own maximum (a raise left over from a client that was killed: ignored)
                if count >= maximum:
                    self.raised[key] = (count, maximum)
                    bridge.set_maximum(*key, max(count, maximum) + 1)
        for box in bridge.boxes_in_stage(stage):
            key = bridge.vanilla_of[box["id"]]
            if key not in received_keys or key in self.raised or not self.countable_find(key):
                continue
            count = bridge.masked.get(key, bridge.value(*key))      # masking hides it during the load
            self.box_counts[key] = count
            maximum = bridge.maximum(*key)
            # full: at the maximum, or (unique equipment: goggles, caps, masks keep maximum 0) owned at all
            if count >= maximum if maximum > 0 else count > 0:
                self.raised[key] = (count, maximum)
                bridge.set_maximum(*key, max(count, maximum) + 1)

    def keep_raised(self):
        """Every tick: a raised maximum the game put back (an area or save load rebuilding the tables) is raised again,
        so the box stays acceptable for as long as Snake is in its area."""
        for key, (count, maximum) in self.raised.items():
            wanted = max(count, maximum) + 1
            if self.bridge.maximum(*key) != wanted:
                try:
                    self.bridge.set_maximum(*key, wanted)
                except OSError:
                    pass

    def restore_raised(self, key=None):
        """Put back the count and maximum of one raised item (or all of them)."""
        bridge = self.bridge
        for raised_key in ([key] if key else list(self.raised)):
            count, maximum = self.raised.pop(raised_key)
            try:
                bridge.set_maximum(*raised_key, maximum)
                if bridge.value(*raised_key) > maximum:
                    bridge.set_value(*raised_key, count)
            except OSError:
                pass

    def count_received_boxes(self, result, stage, received_keys):
        """A received item is still a find in the game, but its box may not appear: the area's script places a box only
        while Snake doesn't own the item (a weapon's turns into an ammo box), and hiding it at the load can lose that
        race. Once the area's spawns settled: if a box of its model spawned, only the pickup counts it (open_boxes keeps
        it acceptable; left behind, it stays a check and is masked again on the next visit); if none did, it counts now
        (nothing to pick up)."""
        bridge = self.bridge
        for box in bridge.boxes_in_stage(stage):
            key = bridge.vanilla_of[box["id"]]
            # only items owned when this area loaded: one received later found its box already placed (and after a
            # client restart the area's spawns were not seen)
            if (key not in received_keys or key not in self.load_keys or key in self.boxes_spawned
                    or not (self.countable_find(key) or BOX_TEST)):
                continue
            name = bridge.item_by_key.get(key, {}).get("name", key)
            if set(box.get("models") or ()) & bridge.spawned_models:
                self.boxes_spawned[key] = stage[:4]
                self.log(f"{name}: received, its box spawned in {stage} (the pickup counts it)")
            elif BOX_TEST:
                self.boxes_spawned[key] = stage[:4]
                self.log(f"BOX TEST: {name}: received, NO box of its model spawned in {stage} (not counted)")
            elif self.count_find(result, key):
                self.log(f"  ({name}: received, no box in {stage}: counted on reaching its box's area)")

    def forget_spawned_boxes(self, new_stage):
        """Leaving an area: its received boxes left behind stay checks (masked again on the next visit)."""
        for key, area in list(self.boxes_spawned.items()):
            if new_stage[:4] != area:
                del self.boxes_spawned[key]

    def handle_pickups(self, pickups, received_keys, now=None, result=None):
        """A pickup that explains a pending gain takes that item back (the vanilla item was found, not received).
        A pickup of a received item's box counts that item once (it stays the player's).
        Pickups are kept for PICKUP_DECIDE: the mod's event can arrive before the gain is counted (the gain waits in
        gain_window first), which once left a found Bug Juice in the inventory."""
        bridge = self.bridge
        state = self.state
        now = self.clock() if now is None else now
        self.recent_pickups = [pickup for pickup in self.recent_pickups if now - pickup[0] <= PICKUP_DECIDE]
        self.recent_pickups += [(now, box, event) for box, event in pickups]
        for item in list(self.recent_pickups):
            _t, box, event = item
            entry = self.match_pending(box, event)
            if entry:
                self.recent_pickups.remove(item)
                self.pending.remove(entry)
                key = entry["key"]
                if entry.get("stripped") and result is not None:
                    self.count_find(result, key)          # a real find after all (its event came late)
                if key not in received_keys and bridge.owned(*key):
                    bridge.unown(*key)
                    self.detail(f"Took back {bridge.item_by_key.get(key, {}).get('name', key)} (found, not received)")
                if key not in received_keys:
                    state.add_taken_back(key)
        for _t, box, _event in self.recent_pickups:     # a boss reward's box: it only exists after a non-lethal win
            key = bridge.vanilla_of.get(box["id"]) if box else None
            boss = self.reward_boss.get(key)
            if boss and box.get("area") in boss.get("reward_areas", ()) and result is not None:
                self.nonlethal_won(result, boss)       # (an owned Mosin-Nagant: its ammo box)
        for item in list(self.recent_pickups):          # received items: no gain counts, the box itself is the find
            _t, box, _event = item
            key = bridge.vanilla_of.get(box["id"]) if box else None
            if key and key in received_keys and self.countable_find(key) and result is not None:
                self.recent_pickups.remove(item)
                self.boxes_spawned.pop(key, None)
                if self.count_find(result, key):
                    self.log(f"  ({bridge.item_by_key.get(key, {}).get('name', key)}: received, its box picked up)")
                self.picked_received[key] = now      # the extra copy arrives after the pickup animation

    def settle_received_pickups(self, now):
        """A received item's box was picked up: once the extra copy arrived (or PICKUP_DECIDE passed), remove it and put
        the maximum back. Done at the event, the game's late +1 kept the extra copy (Mine Detector 2/2)."""
        bridge = self.bridge
        for key, t in list(self.picked_received.items()):
            before = self.raised[key][0] if key in self.raised else self.box_counts.get(key)
            arrived = before is not None and bridge.value(*key) > before
            if not arrived and now - t <= PICKUP_DECIDE:
                continue
            del self.picked_received[key]
            if key in self.raised:
                self.restore_raised(key)
            if key[0] == "item" and before is not None and bridge.value(*key) > before:
                bridge.set_value(*key, before)

    def expire_pending(self, now):
        """A gain no pickup explained within PICKUP_DECIDE is a story gift / boss reward: keep it (hand-out gear the
        game gives back is taken back instead)."""
        for entry in [gain for gain in self.pending if now - gain["t"] > PICKUP_DECIDE]:
            self.pending.remove(entry)
            key = entry["key"]
            if entry.get("reward"):         # a non-lethal reward picked up without a box event (dropped): not a gift
                self.bridge.unown(*key)
                self.detail(f"Took back {self.bridge.item_by_key.get(key, {}).get('name', key)} (non-lethal reward)")
            elif entry.get("stripped"):     # hand-out gear the game gave back without a pickup: no gift
                if not entry.get("taken"):
                    self.take_back_stripped(key)
            elif entry["counted"]:
                self.state.add_kept(key)

    def take_back_stripped(self, key):
        """Unown hand-out gear the game gave back; logged once per item and session (it can come back on every load)."""
        self.bridge.unown(*key)
        if key not in self.stripped_logged:
            self.stripped_logged.add(key)
            self.detail(f"Took back {self.bridge.item_by_key.get(key, {}).get('name', key)} (hand-out gear the game "
                     "gave back; taken back quietly from now on)")

    def strip_handout(self, keys, received_keys, result=None):
        """The game handed out gear (backpack, cleared-game extras): remove everything the player should not own.
        Kept: received AP items, starting gear, story gifts already kept, anything legitimately owned before (a
        capture strip returning gear), the worn camo and face paint, and "No Paint". A Moss / Spirit Camo in the
        hand-out (EVA's gear return brings the Spirit Camo) is its check: taken back unless received, a hidden
        received copy is put back."""
        bridge = self.bridge
        for key in sorted(keys):
            special = self.special_camo(key)
            hidden = self.state.counters.get(f"hidden_{key[1]}", 0) if special else 0
            if special and result is not None and (key not in received_keys or hidden):
                self.special_found(result, special)
                if hidden:
                    bridge.set_value(*key, hidden)
                    self.state.counters[f"hidden_{key[1]}"] = 0
                keys = keys - {key} if hidden else keys
        names = list(self.slot_data.get("starting_items") or [])
        for start in ("starting_camo", "starting_facepaint", "starting_wearable"):
            if self.slot_data.get(start):
                names.append(self.slot_data[start])
        legit = ({tuple(key) for key in received_keys} | {tuple(key) for key in self.state.kept}
                 | {("item", FACEPAINT_ITEM_BASE)} | AUTO_GIFTS | VANILLA_ITEMS
                 | {self.item_keys[name] for name in names if name in self.item_keys})
        # camos and face paints are never "owned before": the hand-out also dresses Snake in one of them (a worn
        # Tiger Stripe / Olive Drab / White Tuxedo survived the Operation Snake Eater hand-out)
        keep = legit | {key for key in self.owned_seen if not is_camo_key(key)}
        removed = []
        for key in sorted(keys - keep):
            if bridge.owned(*key):
                bridge.unown(*key)
                removed.append(bridge.item_by_key.get(key, {}).get("name", str(key)))
            self.state.add_taken_back(key)       # a save from before the strip brings it back: re-take it on load
            self.state.add_stripped(key)         # the game giving it back later is no gift either
        self.dress_random(legit)
        self.handout_t = self.clock()
        self.log(f"Game handed out {len(keys)} items at once; removed {len(removed)} not received"
                 + (f": {', '.join(removed)}" if removed else ""))

    def dress_random(self, legit):
        """After a hand-out: wear a random uniform and face paint from the legitimately owned ones (the hand-out dresses
        Snake in its own camo). Face paint may be "No Paint". Done in play (apply_dress): hand-outs come in story
        stages, and a camo refresh there landed on the next level load and crashed the game (EVA, s152a_0)."""
        self.dress_pending = set(legit)

    def apply_dress(self):
        legit, self.dress_pending = self.dress_pending, None
        if legit is None:
            return
        bridge = self.bridge
        owned_items = sorted(key[1] for key in legit if key[0] == "item" and bridge.owned(*key))
        uniforms = [item_id for item_id in owned_items if CAMO_ITEM_BASE <= item_id < FACEPAINT_ITEM_BASE]
        paints = [item_id for item_id in owned_items if FACEPAINT_ITEM_BASE <= item_id < FACEPAINT_ITEM_END]
        if not uniforms:
            return
        camo, paint = random.choice(uniforms), random.choice(paints or [FACEPAINT_ITEM_BASE])
        if self.write_worn(camo - CAMO_ITEM_BASE, paint - FACEPAINT_ITEM_BASE):
            self.modlink.camo_refresh()
            self.log(f"Now wearing {bridge.item_by_key.get(('item', camo), {}).get('name', camo)} and "
                     f"{bridge.item_by_key.get(('item', paint), {}).get('name', 'No Paint')}")

    def match_pending(self, box, event):
        """The pending gain a pickup explains: the one of its box's vanilla item, else the only one of its category."""
        if not self.pending:
            return None
        if box is not None:
            key = self.bridge.vanilla_of.get(box["id"])
            hit = next((gain for gain in self.pending if gain["key"] == key), None)
            if hit:
                return hit
        acq_type = model_category(box_model(event.get("class")))
        if acq_type is None:
            return None
        candidates = [gain for gain in self.pending if self.bridge.category(*gain["key"]) == acq_type]
        if not candidates:
            extra = MODEL_EXTRA_CATEGORIES.get(acq_type, set())
            candidates = [gain for gain in self.pending if self.bridge.category(*gain["key"]) in extra]
        return candidates[0] if len(candidates) == 1 else None

    # --- received items ----------------------------------------------------------------------------------
    def apply_received(self, received, stage):
        new = received[self.state.applied_count:]
        if not new:
            return
        bridge = self.bridge
        popups = 0
        difficulty = self.opt("difficulty", 2)
        for name, sender in new:
            desc = None
            if name in self.item_keys:
                kind, item_id = self.item_keys[name]
                if bridge.grant(kind, item_id) and kind == "item":
                    self.ready_item(item_id)
            elif name == C3_CHARGE:
                pass                      # the C3 stock follows the received count (keep_c3)
            else:
                desc = effects.apply_effect(bridge.game, name, difficulty, modlink=self.modlink, stage=stage,
                                            options=self.options)
                if name == "Trap: Alert":
                    self.trap_caution = True     # the caution it causes must not spoil the no-alert area check
                if name == "Stealth Camo (temporary)":
                    self.ready_item(effects.STEALTH_CAMO)
                bridge.refresh()          # effects may change the inventory; never count that as a find
                bridge.rebaseline()
            text = desc or (f"You found your {name}" if sender == "You" else
                            f"Received {name}" if sender in ("Archipelago", "Server")
                            else f"{sender} found your {name}")
            self.log(text)
            if popups < MAX_POPUPS_PER_TICK:
                self.modlink.popup(name, text, style="bad" if name.startswith("Trap:") else "")   # traps: red label
            popups += 1
            self.state.applied_count += 1
        if popups > MAX_POPUPS_PER_TICK:
            self.modlink.popup("Archipelago", f"...and {popups - MAX_POPUPS_PER_TICK} more items")

    # --- story progress and bosses ------------------------------------------------------------------------
    def read_story(self):
        """Current story progress ($var[4]), or None if unreadable."""
        try:
            return warp.ScriptVars(self.bridge.game).read_i16(warp.STORY_OFFSET)
        except OSError:
            return None

    def read_vm_story(self):
        """Virtuous Mission story progress ($var[2]), or None if unreadable."""
        try:
            return warp.ScriptVars(self.bridge.game).read_i16(warp.VM_STORY_OFFSET)
        except OSError:
            return None

    def read_flag(self, flag):
        """GCL bit flag $var[off.bit] given as [off, bit]: mask 1 << bit of the byte at off. None if unreadable."""
        try:
            return bool(warp.ScriptVars(self.bridge.game).read_u8(flag[0]) & (1 << flag[1]))
        except OSError:
            return None

    def track_story(self):
        """Highest story progress ($var[4]) seen in this run; persisted, never lowered by loading an older save."""
        story = self.read_story()
        if story is None:
            return
        if story > 0 and (self.state.story_max is None or story > self.state.story_max):
            self.state.story_max = story
            self.state.save()

    def write_story(self, value, why, offset=warp.STORY_OFFSET):
        """Set story progress ($var[4], or $var[2] for the Virtuous Mission); returns True if written."""
        try:
            script_vars = warp.ScriptVars(self.bridge.game)
            old = script_vars.read_i16(offset)
            script_vars.write_i16(offset, value)
        except OSError:
            return False
        name = "Virtuous Mission story" if offset == warp.VM_STORY_OFFSET else "Story progress"
        self.log(f"{name} set to {value} ({why}; was {old})")
        return True

    def boss_dead(self, boss, story):
        """Beaten once story >= its value, or (The End) once its flag is set (sniped early)."""
        if story is not None and story >= boss["story_at_least"]:
            return True
        return bool(boss.get("flag")) and bool(self.read_flag(boss["flag"]))

    def handle_bosses(self, result, stage):
        """Send "Defeat <boss>" once its rule holds. Fights are replayable: re-entering an arena restarts the fight,
        nothing is blocked, and a replay gives nothing new.
        TODO: skip while the DUEL (boss rush) flag $var[15e.6] is set; its setup resets and tests these values."""
        if not stage or not IN_GAME_STAGE.match(stage) or len(self.state.bosses_beaten) >= len(self.bosses):
            return
        story = self.read_story()
        for boss in self.bosses:
            if boss["boss"] in self.state.bosses_beaten or not self.boss_dead(boss, story):
                continue
            self.state.bosses_beaten.append(boss["boss"])
            self.send_location(result, boss["location"])
            self.log(f"{boss['boss']} defeated")

    def nonlethal_won(self, result, boss, proof="reward found"):
        """Send "Defeat <boss> Non-Lethally" once (with nonlethal_boss). Proofs: the boss's reward appearing (the game
        only spawns it after a non-lethal win), or the script's own flag (check_nonlethal_flags)."""
        if not self.opt("nonlethal_boss", 0) or boss["boss"] in self.state.nonlethal:
            return
        self.state.nonlethal.append(boss["boss"])
        self.send_location(result, f"Defeat {boss['boss']} Non-Lethally")
        self.log(f"{boss['boss']} defeated non-lethally ({proof})")

    def check_nonlethal_flags(self, result, stage):
        """The script's non-lethal win flags: the bit array $var[228][16][k] (the reward boxes' condition), bit k + 2 of
        the bits from byte 0x228 on, lowest bit first (verified 2026-10-16 for Ocelot 0, The Fear 2, The End 3, The Fury
        4 and Volgin 6 = byte 0x229 bit 0). Bits already set when the run started (a later save) never count."""
        if not self.opt("nonlethal_boss", 0) or not stage or not FREE_STAGE.match(stage):
            return
        try:
            sv = warp.ScriptVars(self.bridge.game)
            raw = sv.read_u8(NONLETHAL_FLAGS) | sv.read_u8(NONLETHAL_FLAGS + 1) << 8
        except OSError:
            return
        counters = self.state.counters
        if "nonlethal_flag_base" not in counters:
            counters["nonlethal_flag_base"] = raw
            self.state.save()
        new = raw & ~counters["nonlethal_flag_base"]
        for boss in self.bosses:
            if "nonlethal" in boss and new >> (boss["nonlethal"] + 2) & 1:
                self.nonlethal_won(result, boss, "the game's flag")

    def check_owned_rewards(self, result):
        """An owned reward camo whose maximum was raised in its area: its count going up is the reward picked up."""
        for key in [key for key in self.raised if key in self.reward_boss]:
            if self.bridge.value(*key) > self.raised[key][0]:
                self.nonlethal_won(result, self.reward_boss[key])
                self.restore_raised(key)

    # --- story and goal ----------------------------------------------------------------------------------
    def handle_story(self, result, stage, received_counts):
        """Entering an unlocked section completes every section before it ("Complete <section>")."""
        if not stage or not FREE_STAGE.match(stage) or self.pending_lock:
            return
        section_id = self.section_of(stage)
        if section_id is None or self.section_locked(section_id, received_counts):
            return
        for earlier in self.sections[: self.section_index[section_id]]:
            if earlier["id"] in self.state.completed:
                continue
            self.state.completed.append(earlier["id"])
            self.send_location(result, f"Complete {earlier['name']}")
            self.log(f"Completed {earlier['name']}")

    def handle_goal(self, result, stage, received):
        state = self.state
        if GOAL_BOSS in state.bosses_beaten and not state.goal_reached:
            state.goal_reached = True
            self.log("Reached the end of the game")
        if not state.goal_reached or state.goal_sent:
            return
        state.goal_sent = True
        result.goal = True
        self.log("Goal complete!")

    def force_goal(self):
        self.state.goal_reached = True

    # --- game variables (GM block) and DeathLink -------------------------------------------------------------
    def vars(self):
        if self.game_vars is None or self.vars_game is not self.bridge.game:
            self.game_vars = self.game_vars_factory(self.bridge.game)
            self.vars_game = self.bridge.game
            self.last_life = self.last_continue = None
        return self.game_vars

    def vars_get(self, *keys):
        """The GM variables keys as a tuple, or None if unreadable."""
        try:
            game_vars = self.vars()
            return tuple(game_vars.get(key) for key in keys)
        except (OSError, KeyError):
            return None

    def handle_death(self, result, now):
        if not self.opt("death_link", 0):
            return
        values = self.vars_get("life", "life_max", "continue_count")
        if values is None:
            self.last_life = self.last_continue = None
            return
        life, life_max, continue_count = values
        playable = bool(self.bridge.stage) and FREE_STAGE.match(self.bridge.stage) and life_max > 0
        died = False
        if playable and self.last_life is not None and self.last_life > 0 >= life:
            died = True
        if self.last_continue is not None and continue_count == self.last_continue + 1:   # bigger jumps = another save
            died = True
        self.last_life = life if playable else None
        self.last_continue = continue_count
        if died and now >= self.death_lock_until:
            self.death_lock_until = now + DEATH_DEBOUNCE
            count_up(self.state.counters, "deaths_sent")
            result.death = "Snake died. Snake? SNAAAKE!"

    def kill(self):
        """Received DeathLink."""
        self.death_lock_until = self.clock() + max(DEATHLINK_GRACE, DEATH_DEBOUNCE)
        count_up(self.state.counters, "deaths_received")
        try:
            self.vars().set("life", 0)
        except OSError:
            pass
