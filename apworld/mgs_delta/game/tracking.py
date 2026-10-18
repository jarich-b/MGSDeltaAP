"""Per-run counters that have their own checks: no-alert areas, Kerotans, Gako ducks, animals and CQC takedowns.

TrackingMixin is mixed into Session (game/session.py) and uses its state: self.bridge, self.state, self.log,
self.opt(), self.vars_get(), self.connected(), self.location_ids, self.slot_locations, self.pending_lock,
self.restore_until, self.gain_window and self.handout_t.
"""
import re

from . import effects, warp
from .constants import AREA_PREFIX, FREE_STAGE, IN_GAME_STAGE, PHASE_ADDR, rebase, static_u32

CQC_ENTER = 1                # EIcsStateControllerProc.Enter
CQC_TAKEDOWN_STATES = {1, 12, 17, 18, 24}   # EGsrCqcActionState: DirectThrow, ChokeSuccess, Kill, HoldThrow, NeckBreak
CQC_ENEMY_ACTIONS = {"HoldThrow", "DirectThrow", "ChokeSuccess", "NeckBreak", "Kill"}
CQC_DEBOUNCE = 1.0
NO_ALERT_GRACE = 3.0         # a visit's first seconds are ignored (an alert carried over a border returns as caution)
NO_ALERT_MIN_VISIT = 10.0    # shorter visits are just walking through
ARENA_PREFIXES = {"s023", "s032", "s201"}    # boss arenas, when bosses.json has no "arena" lists
KEROTAN_FLAGS = 0x242        # $var[0x242..0x24A]: 72-bit flag array, Kerotan index i = bit i + 1 (LSB first)
KEROTAN_FLAG_BYTES = 9
KEROTAN_COUNT = 64
# The game copies the profile's KerotanList into the $var flags (legacy command 0xB3, an assignment of all 64 bits), so
# Kerotans shot in any earlier game are built as already shot and can't be shot for this run. Blanking that command's
# entry in the legacy dispatcher table makes the copy a no-op (the dispatcher returns 0 for a NULL entry; verified in
# the exe at 0x147A5BCC0). Memory only: the profile is untouched. Restored when the client exits; a game restart
# rebuilds the table anyway.
KEROTAN_PUSH_ENTRY = 0x154522C38              # dispatcher table 0x1545226A0 + 0xB3 * 8
KEROTAN_PUSH_HANDLER = 0x147BAC940
GAKO_COUNT = 64
GAKO_NAME = re.compile(r"^Gako (\d+)\b")
KEROTAN_NAME = re.compile(r"^Kerotan (\d+)\b")
FOOD_READ_INTERVAL = 0.5
HANDOUT_GRACE = 3.0          # food kinds appearing this long after a hand-out are part of it
SPOTTED_PHASES = (1, 2)      # alert, evasion: a no-alert visit fails; caution (3) is fine


class TrackingMixin:
    """Counters of the optional checks (no-alert, Kerotans, Gako, animals, CQC) of the Session."""

    def init_tracking(self, location_ids, bosses, animals=None, guarded=None, dead_ends=None):
        self.cqc_player_seen = False       # player-side CQC events arrive: ignore the enemy-side fallback
        self.last_cqc = -1000.0
        self.trap_caution = False          # an Alert trap's caution is active: ignored by the no-alert tracker
        self.visit = None                  # current no-alert area visit (see handle_no_alert)
        self.food_present = None           # food kinds in Snake's food and live-animal slots at the last read
        self.food_read_t = -1000.0
        self.kerotan_names = {}            # Kerotan index + 1 -> location name ("Kerotan 7: ...")
        self.gako_names = {}               # GakoIndex + 1 -> location name ("Gako 9: ...")
        for name in location_ids:
            match = KEROTAN_NAME.match(name)
            if match:
                self.kerotan_names[int(match[1])] = name
            match = GAKO_NAME.match(name)
            if match:
                self.gako_names[int(match[1])] = name
        self.animal_species = {animal["kind"]: animal["name"] for animal in animals or []}   # species kind -> name
        self.animal_names = {kind: f"Captured {name}" for kind, name in self.animal_species.items()}   # -> check name
        self.arenas = {area for boss in bosses for area in boss.get("arena", [])} or set(ARENA_PREFIXES)
        self.guarded = set(guarded) if guarded is not None else None   # no-alert areas: only those with guards
        self.dead_ends = set(dead_ends or ())    # no-alert areas left back the way they were entered still count

    # --- sending checks -------------------------------------------------------------------------------
    def send_location(self, result, name):
        """Queue the location called name if this slot has it."""
        lid = self.location_ids.get(name)
        if lid is not None and lid in self.slot_locations:
            result.locations.append(lid)

    def send_counted(self, result, prefix, count):
        """Queue "<prefix> 1" .. "<prefix> <count>": every check up to the run's total, so none is ever lost."""
        for number in range(1, count + 1):
            self.send_location(result, f"{prefix} {number}")

    # --- no-alert areas -------------------------------------------------------------------------------
    def read_phase(self):
        """Live game phase (0 sneak, 1 alert, 2 evasion, 3 caution), or None if unreadable."""
        try:
            return static_u32(self.bridge.game, PHASE_ADDR)
        except OSError:
            return None

    def handle_no_alert(self, result, stage, new_stage, now):
        """A visit = from entering a playable stage until it is left through a door/exit. It is a clean area when the
        phase was never alert or evasion (after NO_ALERT_GRACE; a caution is fine), it lasted NO_ALERT_MIN_VISIT,
        Snake did not die, both the entry and the exit were normal transitions (not a save load / warp) and the exit
        leads to another area than the entry came from (except in dead ends). Each area prefix counts once per run."""
        if int(self.opt("no_alert_areas", 0)) <= 0:
            self.visit = None
            return
        free = bool(stage) and bool(FREE_STAGE.match(stage))
        locked = bool(self.pending_lock) and self.pending_lock["stage"] == stage
        if new_stage:
            normal = self.connected(self.bridge.previous_stage, new_stage)
            visit = self.visit
            if visit and free and new_stage[:4] == visit["area"] and normal:
                pass                                     # another stage of the same area (a/b/c): the visit goes on
            else:
                came_from = (self.bridge.previous_stage or "")[:4]
                self.end_visit(normal, now, new_stage[:4])
                if free and not locked:
                    self.start_visit(new_stage, now, invalid=not normal, came_from=came_from)
        elif free and self.visit is None and not locked:
            self.start_visit(stage, now, invalid=True)   # attached mid-area: the entry was missed
        visit = self.visit
        if visit and free and stage[:4] == visit["area"]:
            self.update_visit(visit, now)
        self.send_counted(result, "No-Alert Area", len(self.state.clean_areas))

    def start_visit(self, stage, now, invalid, came_from=None):
        self.visit = {"area": stage[:4], "start": now, "dirty": False, "invalid": invalid, "continue": None,
                      "from": came_from}

    def update_visit(self, visit, now):
        """Void the visit on a death (life 0 or a continue); mark it dirty on an alert or evasion (not a caution)."""
        values = self.vars_get("life", "continue_count")
        if values is not None:
            life, continue_count = values
            if life <= 0 or (visit["continue"] is not None and continue_count != visit["continue"]):
                visit["invalid"] = True
            visit["continue"] = continue_count
        if visit["dirty"] or now - visit["start"] < NO_ALERT_GRACE:
            return
        phase = self.read_phase()
        if self.trap_caution:
            if not phase:
                self.trap_caution = False    # the Alert trap's caution is over; real ones count again
        elif phase in SPOTTED_PHASES:
            visit["dirty"] = True
            self.log(f"Area {visit['area']} is not a no-alert area (phase {phase})")

    def end_visit(self, left_normally, now, going_to=None):
        visit, self.visit = self.visit, None
        if not visit:
            return
        area = visit["area"]
        if (visit["invalid"] or visit["dirty"] or not left_normally or now - visit["start"] < NO_ALERT_MIN_VISIT
                or area in self.arenas or area in self.state.clean_areas or not AREA_PREFIX.match(area)):
            return
        if self.guarded is not None and area not in self.guarded:
            self.log(f"Area {area} has no guards: not a no-alert area")
            return
        if going_to and going_to == visit.get("from") and area not in self.dead_ends:
            self.log(f"Area {area} was left back to {going_to}, where it was entered from: not a no-alert area")
            return
        self.state.clean_areas.append(area)
        self.state.save()
        self.log(f"No-alert area {len(self.state.clean_areas)}: {area}")

    # --- Kerotans (per run, from the $var flags) -------------------------------------------------------------
    def kerotan_base(self):
        return warp.ScriptVars(self.bridge.game).base() + KEROTAN_FLAGS

    def read_kerotans(self):
        """Set of Kerotan indices (0..63) whose flag is set in $var[0x242..0x24A], or None if unreadable."""
        try:
            raw = self.bridge.game.read(self.kerotan_base(), KEROTAN_FLAG_BYTES)
        except OSError:
            return None
        out = set()
        for index in range(KEROTAN_COUNT):
            pos = index + 1
            if raw[pos >> 3] & (1 << (pos & 7)):
                out.add(index)
        return out

    def write_kerotans(self, indices):
        """Set the $var Kerotan flags to exactly these indices (0..63). Raises OSError."""
        raw = bytearray(KEROTAN_FLAG_BYTES)
        for index in indices:
            pos = index + 1
            raw[pos >> 3] |= 1 << (pos & 7)
        self.bridge.game.write(self.kerotan_base(), bytes(raw))

    def clear_reloaded_kerotans(self, current, extra):
        """Clear Kerotan flags a reload brought back that are not this run's shots. Returns the flags left."""
        try:
            self.write_kerotans(current - extra)
        except OSError:
            return current
        self.log(f"Kerotan flags restored by a reload, not this run's shots: cleared "
                 f"{[i + 1 for i in sorted(extra)]}")
        return current - extra

    def zero_kerotans(self):
        """Run start: clear the flags so only this run's shots count (the profile's lifetime counter is untouched)."""
        before = self.read_kerotans()
        try:
            self.bridge.game.write(self.kerotan_base(), bytes(KEROTAN_FLAG_BYTES))
        except OSError:
            return False                                  # store not allocated yet: handle_kerotans baselines instead
        if before:
            self.log(f"Run start: Kerotan flags cleared: {[i + 1 for i in sorted(before)]}")
        self.state.kerotan_profile = sorted(before or ())
        self.state.kerotans = []
        self.state.kerotan_bits = []
        self.state.kerotan_zeroed = True
        return True

    def block_kerotan_push(self):
        """Keep the profile -> $var Kerotan copy disabled (see KEROTAN_PUSH_ENTRY). True while it is disabled."""
        game = self.bridge.game
        try:
            addr = rebase(game, KEROTAN_PUSH_ENTRY)          # the exe is relocated (ASLR): address and value both
            entry = int.from_bytes(game.read(addr, 8), "little")
            if entry == rebase(game, KEROTAN_PUSH_HANDLER):
                game.write(addr, bytes(8))
                entry = int.from_bytes(game.read(addr, 8), "little")
                if entry == 0:
                    # a copy may have run since the run start: clear its flags once more (shots already counted stay)
                    copied = self.read_kerotans() or set()
                    self.state.kerotan_profile = sorted(set(self.state.kerotan_profile) | copied)
                    game.write(self.kerotan_base(), bytes(KEROTAN_FLAG_BYTES))
                    self.state.kerotan_bits = []
                    self.log("Kerotan profile copy disabled for this run (memory only); flags cleared")
        except OSError:
            return False
        return entry == 0

    def restore_kerotan_push(self):
        """Put the game's Kerotan profile copy back (client exit)."""
        try:
            game = self.bridge.game
            addr = rebase(game, KEROTAN_PUSH_ENTRY)
            if int.from_bytes(game.read(addr, 8), "little") == 0:
                game.write(addr, rebase(game, KEROTAN_PUSH_HANDLER).to_bytes(8, "little"))
        except (OSError, AttributeError):
            pass

    def handle_kerotans(self, result, stage, now):
        """Every flag that goes 0 -> 1 between two reads is a Kerotan shot. Reads right after a save load or warp only
        re-baseline (an older save carries older flags), as do reads during a stage load. While the game's profile copy
        is disabled every new flag counts; otherwise several flags flipping together are that copy, not shots.
        While it is disabled, flags a reload brings back that are not this run's shots are cleared: a checkpoint from
        before the run start holds the profile's lifetime flags (copied at the new game), and a death's continue
        restored all of them (report 2: their checks sent when that came after the restore window, and those frogs
        could no longer be shot for a check). Several profile flags at once are such a reload whenever they come.
        Checks already earned are never withdrawn."""
        state = self.state
        blocked = bool(self.opt("kerotan_checks")) and self.block_kerotan_push()
        if stage and IN_GAME_STAGE.match(stage):
            current = self.read_kerotans()
            if current is not None:
                if not state.kerotan_zeroed:            # no clean slate (run started earlier): keep flags, count new
                    state.kerotan_zeroed = True
                    state.kerotan_bits = sorted(current)
                elif now < self.restore_until or self.bridge.masking:
                    extra = current - set(state.kerotans)
                    if blocked and extra:
                        current = self.clear_reloaded_kerotans(current, extra)
                    state.kerotan_bits = sorted(current)
                else:
                    new = current - set(state.kerotan_bits) - set(state.kerotans)
                    if blocked and len(new) > 1 and new <= set(state.kerotan_profile):
                        # a shot flips one bit: several of the run start's cleared flags at once = a late reload
                        current = self.clear_reloaded_kerotans(current, new)
                        new = set()
                    state.kerotan_bits = sorted(current)
                    if len(new) > 1 and not blocked:   # several at once = the game's copy
                        self.log(f"Kerotan flags synced by the game ({len(new)}), not counted")
                        new = set()
                    for index in sorted(new):
                        state.kerotans.append(index)
                        self.log(f"Kerotan {index + 1} shot ({len(state.kerotans)}/{KEROTAN_COUNT})")
        for index in state.kerotans:
            self.send_location(result, self.kerotan_names.get(index + 1, ""))

    # --- Gako ducks (per run, from the mod's GakoSetCollected hook) ------------------------------------------
    def handle_gako(self, result):
        events, self.bridge.gako_events = self.bridge.gako_events, []
        for event in events:
            index = event.get("index")
            if isinstance(index, (int, float)) and 0 <= index < GAKO_COUNT and int(index) not in self.state.gako:
                self.state.gako.append(int(index))
                self.state.save()
                self.log(f"Gako duck {int(index)} broken ({len(self.state.gako)} this run)")
        for index in self.state.gako:
            self.send_location(result, self.gako_names.get(index + 1, ""))

    # --- animals (per run, from the food slots) ----------------------------------------------------------------
    def read_food_kinds(self):
        """Food keys in Snake's food slots: the kind (int32 at [entry+0x50]) of each occupied slot, or -slot
        where the data pointer is unreadable. None if the table is unreadable."""
        try:
            return effects.food_kinds(self.bridge.game)
        except OSError:
            return None

    def handle_animals(self, result, stage, now):
        """"Captured <species>" for every species (effects.normalize_food_kind: live captures count as their dead-food
        kind) that appears in the food slots during the run. Kinds present at the first read, during save loads,
        masking and game hand-outs only baseline; kinds without a check (food, mushrooms, the last area's snakes)
        are ignored. Checks already earned are never withdrawn."""
        if not self.opt("animal_checks", 0):
            return
        state = self.state
        if stage and FREE_STAGE.match(stage) and now - self.food_read_t >= FOOD_READ_INTERVAL:
            self.food_read_t = now
            current = self.read_food_kinds()
            if current is not None:
                previous, self.food_present = self.food_present, current
                quiet = (previous is not None and now >= self.restore_until and not self.bridge.masking
                         and self.gain_window is None and now - self.handout_t >= HANDOUT_GRACE)
                if quiet:
                    for key in sorted(current - previous):
                        if key in self.animal_names and key not in state.animals:
                            state.animals.append(key)
                            state.save()
                            self.log(f"Captured {self.animal_species[key]} ({len(state.animals)} this run)")
        for key in state.animals:
            self.send_location(result, self.animal_names.get(key, ""))

    # --- CQC takedowns ----------------------------------------------------------------------------------
    def handle_cqc(self, result, now):
        """Count CQC takedowns from mod events: one per CQC engagement (the native handshake burst; in game the only
        source that fires). The Blueprint state/enemy events are kept for when their hooks attach.
        Sends "CQC Takedown k" for every k up to the run's total."""
        events, self.bridge.cqc_events = self.bridge.cqc_events, []
        for event in events:
            if event.get("proc") != CQC_ENTER:
                continue
            if event.get("side") == "grab":
                takedown = True              # one CQC engagement (native handshake burst) = one takedown
            elif event.get("side") == "player":
                self.cqc_player_seen = True
                takedown = event.get("state") in CQC_TAKEDOWN_STATES
            else:
                takedown = not self.cqc_player_seen and event.get("action") in CQC_ENEMY_ACTIONS
            if not takedown or now - self.last_cqc < CQC_DEBOUNCE:
                continue
            self.last_cqc = now
            self.state.cqc_total += 1
            self.state.save()
            self.log(f"CQC takedown {self.state.cqc_total} ({event.get('state', event.get('action'))})")
        self.send_counted(result, "CQC Takedown", self.state.cqc_total)
