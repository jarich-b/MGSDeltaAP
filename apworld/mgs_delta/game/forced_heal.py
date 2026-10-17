"""Forced-heal scenes: top up the cure items two story scenes need, and nothing else.

The AP run start wipes the inventory and medicines are only in the item pool with medical checks, but two scenes of
the story cannot be finished without the right cure items:

1. Virtuous Mission ending, stage v007a, $var[2] == 51: Snake lands on the river bank with 2 fractures and 4 cuts and
   the stage only goes on once every wound is treated (Para-Medic's Cure tutorial).
2. The sidecar crash in Zaozyorje, stage s191a, $var[4] == 184: EVA has 2 cuts (the script ends in Game Over if they
   are still open); Snake gets a fracture and a cut as well (not checked by the script).

On entering a scene the module remembers how many of each cure item Snake owns, fills them to the stack maximum and
keeps them there while the scene lasts (so treating in the wrong order or wasting an item can never softlock it). On
leaving the scene every count is put back to the remembered amount: the scene costs and gives nothing.

Usage (see the integration note in the research document):

    heal = ForcedHeal(bridge, log=self.log)
    ...
    label = heal.tick(stage, story, vm_story)       # story = $var[4], vm_story = $var[2] (None if unreadable)
    if label: self.modlink.popup("Archipelago", label)

The module needs only the bridge's value(kind, id), maximum(kind, id) and set_value(kind, id, n) (bridge.refresh()
must have run this tick) and imports nothing from the package.
"""
import re


# --- item ids (EItemName, same as data/items.json) -----------------------------------------------------------------
SPLINT = 41           # "Splinter" in items.json
DISINFECTANT = 42
STYPTIC = 43
BANDAGE = 44
SUTURE = 45

# --- scene table -----------------------------------------------------------------------------------------------------
# Treatments per wound (CureWindowDataTable of the game): cut = Disinfectant + Styptic + Suture Kit + Bandage,
# fracture = Splint + Bandage; one unit of each item per treatment (assumed, see the research document).
#
# rules:    the scene is active when ANY rule matches: the stage is in `stages` and the story variable `var`
#           ("main" = $var[4], "vm" = $var[2]) is within [lo, hi].
# required: item id -> count the scene cannot be finished without.
# optional: item id -> extra count for treatments the script does not check (added when INCLUDE_OPTIONAL).
# label:    the on-screen message when items were granted on entering the scene.
SCENES = [
    {
        "id": "vm_bridge_cure",
        "name": "Virtuous Mission: treat the injuries after the bridge",
        "rules": [{"stages": ("v007a",), "var": "vm", "lo": 51, "hi": 51}],
        # wounds: 2 x fracture (joints 7, 1) + 4 x cut (joints 12, 6, 11, 1)
        "required": {SPLINT: 2, DISINFECTANT: 4, STYPTIC: 4, SUTURE: 4, BANDAGE: 6},
        "optional": {},
        "label": "Para-Medic sent field medicine",
        # The restore waits until Snake is playable in Operation Snake Eater (s0xx): cutscenes follow the scene, and
        # the Operation Snake Eater hand-out refills every medicine on the way.
        "restore_in": r"^s\d{3}[a-z]$",
    },
    {
        "id": "eva_crash_cure",
        "name": "Zaozyorje: treat EVA after the sidecar crash",
        "rules": [
            # the cutscene before the crash (stage s191a_0, chain 179 -> 183) and the playable stage from its start:
            # the items are in place before the Survival Viewer event opens.
            {"stages": ("s191a_0",), "var": "main", "lo": 179, "hi": 183},
            {"stages": ("s191a",), "var": "main", "lo": 183, "hi": 184},
        ],
        # EVA: 2 x cut (joints 0, 6), checked by the script (Game Over if still open when the pursuit demo ends)
        "required": {DISINFECTANT: 2, STYPTIC: 2, SUTURE: 2, BANDAGE: 2},
        # Snake: 1 fracture (joint 1) + 1 cut (joint 20). Not checked by the script, but if Snake treats himself first
        # EVA would be short, so the default covers both.
        "optional": {SPLINT: 1, DISINFECTANT: 1, STYPTIC: 1, SUTURE: 1, BANDAGE: 2},
        "label": "Field medicine ready for EVA",
    },
]

INCLUDE_OPTIONAL = True     # also fill the items of the scene's "optional" treatments
MEDICAL_IDS = (1, 2, *range(36, 48))   # every medical item (= constants.MEDICAL_IDS): restored too when medicine is an
                                       # AP item (hand-outs refill them all)
FILL_FALLBACK = 20          # fill target when the item's stack maximum is unreadable


def scene_needs(scene, include_optional=None):
    """item id -> total count the scene needs."""
    if include_optional is None:
        include_optional = INCLUDE_OPTIONAL
    needs = dict(scene["required"])
    if include_optional:
        for item_id, count in scene.get("optional", {}).items():
            needs[item_id] = needs.get(item_id, 0) + count
    return needs


class ForcedHeal:
    """Per-scene state machine: fill the scene's cure items on entry, put the counts back on leaving (see above)."""

    def __init__(self, bridge, log=print, scenes=None, include_optional=None, medical_in_pool=lambda: False):
        self.bridge = bridge
        self.log = log
        self.scenes = SCENES if scenes is None else scenes
        self.include_optional = include_optional
        self.medical_in_pool = medical_in_pool   # callable: medicine is an AP item (restore all cure items)
        self.state = {}          # scene id -> {"original": {iid: count on entry}, "told": bool}
        self.waiting = {}        # scene id -> state, left but restored later (scene "restore_in" stage pattern)

    # --- scene detection -------------------------------------------------------------------------------------
    @staticmethod
    def match(scene, stage, story, vm_story):
        """True: active, False: not active, None: the stage fits but its story value is unreadable (hold state)."""
        unknown = False
        for rule in scene["rules"]:
            if stage not in rule["stages"]:
                continue
            value = vm_story if rule["var"] == "vm" else story
            if value is None:
                unknown = True
            elif rule["lo"] <= value <= rule["hi"]:
                return True
        return None if unknown else False

    def active(self, stage, story, vm_story):
        """Ids of the scenes active right now."""
        return [s["id"] for s in self.scenes if self.match(s, stage, story, vm_story) is True]

    def reset(self):
        """Forget all scene progress (new game connection, stage jump)."""
        self.state.clear()
        self.waiting.clear()

    # --- per-tick call ---------------------------------------------------------------------------------------
    def tick(self, stage, story, vm_story):
        """Call every tick after bridge.refresh(). Returns an on-screen label when items were granted on entering a
        scene, else None."""
        label = None
        for scene in self.scenes:
            scene_id = scene["id"]
            hit = self.match(scene, stage, story, vm_story)
            if hit is False:
                scene_state = self.state.pop(scene_id, None)
                if scene_state is not None:
                    if scene.get("restore_in"):
                        self.waiting[scene_id] = scene_state
                        self.log(f"Forced heal: left '{scene['name']}'; restoring once Snake is back in play")
                    else:
                        self._restore(scene, scene_state)
                waiting = self.waiting.get(scene_id)
                if waiting is not None and re.match(scene["restore_in"], stage or ""):
                    del self.waiting[scene_id]
                    self._restore(scene, waiting)
                continue
            if hit is None:                      # story unreadable this tick: keep the state, do nothing
                continue
            scene_state = self.state.get(scene_id)
            if scene_state is None:
                scene_state = self._enter(scene, stage, story, vm_story)
                if scene_state is None:
                    continue                     # counts unreadable: try again next tick
                self.state[scene_id] = scene_state
            if self._fill(scene) and not scene_state["told"]:
                scene_state["told"] = True
                label = label or scene["label"]
        return label

    def _items(self, scene):
        """The cure item ids a scene fills, sorted."""
        return sorted(scene_needs(scene, self.include_optional))

    def _enter(self, scene, stage, story, vm_story):
        """Remember the counts to put back; None if the inventory is unreadable this tick."""
        ids = set(self._items(scene)) | (set(MEDICAL_IDS) if self.medical_in_pool() else set())
        try:
            original = {item_id: self.bridge.value("item", item_id) for item_id in sorted(ids)}
        except OSError:
            return None
        self.log(f"Forced heal: entered '{scene['name']}' (stage {stage}, story {story}, VM story {vm_story}); "
                 f"holding {self._list(original)}")
        return {"original": original, "told": False}

    def _fill(self, scene):
        """Keep every cure item of the scene at its stack maximum. True if anything was written."""
        bridge = self.bridge
        wrote = False
        for item_id in self._items(scene):
            target = bridge.maximum("item", item_id)
            if target <= 0:
                target = FILL_FALLBACK
            try:
                if bridge.value("item", item_id) < target:
                    bridge.set_value("item", item_id, target)
                    wrote = True
            except OSError as exc:               # game unreadable/unwritable: retry on the next tick
                self.log(f"Forced heal: could not fill item {item_id}: {exc}")
        return wrote

    def _restore(self, scene, scene_state):
        """Leaving the scene: put every cure item back to the count it had on entry."""
        for item_id, count in scene_state["original"].items():
            try:
                self.bridge.set_value("item", item_id, count)
            except OSError as exc:
                self.log(f"Forced heal: could not restore item {item_id}: {exc}")
        self.log(f"Forced heal: left '{scene['name']}', restored {self._list(scene_state['original'])}")

    def _list(self, counts):
        return ", ".join(f"{count} x {self._name(item_id)}" for item_id, count in sorted(counts.items()))

    def _name(self, item_id):
        info = getattr(self.bridge, "item_by_key", {}).get(("item", item_id))
        return info["name"] if info else f"item {item_id}"
