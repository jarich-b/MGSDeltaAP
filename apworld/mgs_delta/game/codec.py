"""Archipelago codec frequencies: the status page (locks, goal) and the checkpoint warps.

CodecMixin is mixed into Session (game/session.py) and uses its state: self.bridge, self.state, self.modlink,
self.log, self.opt(), self.sections, self.section_locked(), self.need_text(),
self.read_story(), self.read_flag() and self.write_story().
"""
from . import warp
from .constants import FREE_STAGE
from .modlink import CODEC_PAGE_BREAK

AP_STATUS_FREQ = 14340       # 143.40: status and the list of unlocked warp frequencies (143.41+ are the warps;
                             # 143.33-143.55 is unused by the game scripts; 144.72, 144.74 and 144.75 are not: the prison cell call)
CODEC_WARP_TTL = 120         # a codec warp waits for the game to run again (the mod loop pauses in menus)
WARP_GUARD = 60              # s a sent warp blocks further codec warp calls unless Snake arrived in another area


class CodecMixin:
    """Codec status page and checkpoint warps of the Session."""

    def init_codec(self, checkpoints, entries, end_boss):
        # [{"name", "freq", "area", "stage", "via", "story"?, "vm_story"?, "latest"?, "stage_if_end_sniped"?,
        #   "story_if_end_dead"?, "story_if_end_alive"?}]
        self.checkpoints = checkpoints
        self.entries = entries             # {stage: [{"from", "proc", "pose", "gate"}]} for safe warps
        self.end_boss = end_boss           # The End's bosses.json entry (dead by story or flag), or None
        self.pending_warp = None           # (checkpoint, stage) chosen on the codec (pose written), warped on close
        self.codec_sent = {}               # freq -> messages last sent to the mod

    # --- checkpoints --------------------------------------------------------------------------------------
    def unlocked_checkpoints(self):
        """Warps whose area (or "unlock_area") was visited, once the run's story progress reached their story value: a
        warp never moves the story past what the run achieved (visiting s081 before The Fury doesn't unlock the
        post-Fury warp). The Virtuous Mission warp unlocks on reaching v003: its story value is after the backpack, and
        warping there before the backpack was really taken left Snake without the backpack."""
        reached = self.state.story_max
        return [checkpoint for checkpoint in self.checkpoints
                if checkpoint.get("unlock_area", checkpoint["area"]) in self.state.visited
                and (reached is None or reached >= int(checkpoint.get("story") or 0))]

    def is_latest(self, checkpoint):
        """The checkpoint marked "latest", or (none marked) the furthest unlocked one: the run's frontier."""
        if any(candidate.get("latest") for candidate in self.checkpoints):
            return bool(checkpoint.get("latest"))
        unlocked = self.unlocked_checkpoints()
        return bool(unlocked) and checkpoint is unlocked[-1]

    def end_state(self):
        """(dead, sniped) for The End, from the current or max story and his flag."""
        if not self.end_boss:
            return False, False
        story = max(self.read_story() or 0, self.state.story_max or 0)
        sniped = bool(self.read_flag(self.end_boss["flag"]))
        return sniped or story >= self.end_boss["story_at_least"], sniped

    def checkpoint_target(self, checkpoint):
        """(stage, story, why) of a warp to cp. story: story_max at the frontier, else its "story" value (None: keep).
        Checkpoints with end fields (Sokrovenno North) depend on The End: stage_if_end_sniped if he was sniped, story
        max(story_max, story_if_end_dead) if dead, else story_if_end_alive.
        TODO: clear the fight/old-age flags there too ($var[6a7.6], $var[958], $var[90b.0], $var[8f2.6])."""
        stage, story = checkpoint["stage"], checkpoint.get("story")
        if "story_if_end_dead" in checkpoint or "stage_if_end_sniped" in checkpoint:
            dead, sniped = self.end_state()
            if sniped and checkpoint.get("stage_if_end_sniped"):
                stage = checkpoint["stage_if_end_sniped"]
            if dead and checkpoint.get("story_if_end_dead") is not None:
                story = max(self.state.story_max or 0, checkpoint["story_if_end_dead"])
            elif not dead and checkpoint.get("story_if_end_alive") is not None:
                story = checkpoint["story_if_end_alive"]
        if self.is_latest(checkpoint) and self.state.story_max is not None:
            return stage, self.state.story_max, "latest checkpoint: run progress"
        if story is not None:
            return stage, int(story), f"checkpoint {checkpoint['name']}"
        return stage, None, None

    # --- codec events -------------------------------------------------------------------------------------
    def handle_codec_warp(self, stage, now):
        """A call to an unlocked checkpoint frequency writes that gate's pose (and the checkpoint's story progress)
        right away; the warp is sent when the codec closes. The mod's loop is paused while the codec is open, so the
        load starts once the player is back in the game (warping under an open menu soft-locked the game in testing)."""
        events, self.bridge.codec_events = self.bridge.codec_events, []
        in_flight = getattr(self, "warp_in_flight", None)
        mod = getattr(self.bridge, "last_mod_stage", None)
        # loaded since: a stage change, or the mod's stage event (a warp within the same area reloads the stage the
        # client already reads, and its codec calls were blocked for WARP_GUARD)
        arrived = in_flight and FREE_STAGE.match(stage or "") and (
            self.stage_changed_at > in_flight[0] or (mod is not None and mod[0] == stage and mod[1] > in_flight[0]))
        if in_flight and (arrived or now - in_flight[0] > WARP_GUARD):
            self.warp_in_flight = in_flight = None          # arrived (or gave up waiting)
        for event in events:
            if event.get("type") == "codec_call":
                if in_flight or not FREE_STAGE.match(stage or ""):
                    # a second call while a warp loads wrote its pose and story under the load: black screen
                    self.log("Codec: warp call ignored (a warp is still loading)")
                    continue
                self.prepare_codec_warp(event.get("freq"), stage)
            elif event.get("type") == "codec_closed" and self.pending_warp:
                (checkpoint, target), self.pending_warp = self.pending_warp, None
                self.modlink.warp(target, ttl=CODEC_WARP_TTL)
                self.warp_in_flight = (now, stage)          # (sent at, from stage)
                self.log(f"Warping to {checkpoint['name']}")
        detector = getattr(self.bridge, "detector", None)
        if detector is not None:
            detector.loading = bool(getattr(self, "warp_in_flight", None))   # items ending now are the warp's unload

    def prepare_codec_warp(self, freq, stage):
        """A call to freq: write the gate pose and story of its checkpoint and remember the warp for the codec close."""
        checkpoint = next((candidate for candidate in self.unlocked_checkpoints() if candidate["freq"] == freq), None)
        self.pending_warp = None
        if not checkpoint:
            return
        target, story, why = self.checkpoint_target(checkpoint)
        try:
            entry = warp.prepare_warp(self.bridge.game, self.entries, target, current=stage, via=checkpoint.get("via"),
                                      proc=checkpoint.get("via_proc"))
        except OSError:
            entry = None
        if entry:
            self.pending_warp = (checkpoint, target)
            if story is not None:
                self.write_story(story, why)
            if checkpoint.get("vm_story") is not None:
                self.write_story(int(checkpoint["vm_story"]), f"checkpoint {checkpoint['name']}", warp.VM_STORY_OFFSET)
        self.log(f"Codec: warp to {checkpoint['name']} when the codec closes" if entry
                 else f"Codec: no known gate for {checkpoint['name']}")

    # --- codec menus --------------------------------------------------------------------------------------
    def gate_page(self, section, received_counts):
        """Codec page of one gate as one sentence (the codec shows no line breaks), e.g.
        ["Cave Exit not found yet: Ponizovje to Svyatogornyj is locked."]."""
        item, count = self.section_need(section)
        have = received_counts.get(item, 0)
        if count > 1:
            head = f"{item}s: {min(have, count)} of {count} found"
        else:
            head = f"{item} {'found' if have >= count else 'not found yet'}"
        state = "is open" if have >= count else "is locked"
        return [f"{head}: {section['name']} {state}."]

    def update_codec(self, received_counts):
        """Send the status page (143.40) and the per-checkpoint warp pages to the mod, only where they changed."""
        if self.bridge.mod_restarted:
            self.bridge.mod_restarted = False
            self.codec_sent.clear()
        # One page per gate (Cave Exit, Sokrovenno Exit, the C3 Charges), then one page per warp; the mod pages on
        # the page-break items, the player presses to go on.
        pages = [self.gate_page(s, received_counts) for s in self.sections if self.section_need(s)]
        unlocked = self.unlocked_checkpoints()
        pages += [[f"Warp: call {checkpoint['freq'] / 100:.2f} for {checkpoint['name']}."] for checkpoint in unlocked]
        if not unlocked:
            pages.append(["No warps unlocked yet"])
        pages[0] = ["Archipelago status:"] + pages[0]
        status = []
        for i, page in enumerate(pages):
            status += ([CODEC_PAGE_BREAK] if i else []) + page
        menus = {AP_STATUS_FREQ: status}
        for checkpoint in self.checkpoints:
            has_gate = warp.choose_entry(self.entries, checkpoint["stage"], checkpoint.get("via")) is not None
            warpable = checkpoint in unlocked and has_gate
            menus[checkpoint["freq"]] = ([f"Warp to {checkpoint['name']}.", "Close the codec to go. Inventory is kept."]
                                         if warpable else [])
        for freq, messages in menus.items():
            if self.codec_sent.get(freq) != messages:
                self.codec_sent[freq] = messages
                self.modlink.codec_set(freq, messages)
