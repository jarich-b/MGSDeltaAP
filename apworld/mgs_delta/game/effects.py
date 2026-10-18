"""Traps and benefits: memory effects on the running game.

Interface used by the client:
    apply_effect(game, name, difficulty=2, modlink=None, stage=None, options=None) -> str | None
        game: mgsmem.Game (writable); name: AP item name ("Trap: Hunger", "Ammo Refill", ...);
        difficulty: 0 very easy .. 5 european extreme; modlink: game.modlink.ModLink (for effects that need the mod);
        stage: current stage name; options: slot data options (Injury trap wounds). Returns a short description for
        the popup, or None if `name` is not an effect.
    tick(game)  -> call every client loop; ends timed effects (temporary Infinity Face Paint / Stealth Camo), runs
                   deferred fallbacks (Disarm write), refreshes a held Alert caution and restores the Weather weather.

Offsets: data/gamestate.json (GM struct pointer at 0x14C532038), weapon table fields (+0x28 stock, +0x2A max,
+0x2C loaded magazine, +0x50 pointer to {food kind, capture time}). Effects marked CALIBRATE use values from the
static research; set one to None to switch that trap off.
Traps that need the UE side (Disarm, Fashion, Weather, Injury) send a command through `modlink`
(game/modlink.py -> MGSDeltaAP mod), the memory part is done here; the Alert trap is memory only and holds the caution
phase (display, music) without sending the guards searching.
"""
import random
import struct
import time

try:
    # noqa: F401 (IMAGE_BASE, PHASE_TIMER_ADDR: re-exported for the effect tests)
    from .constants import (CAMO_ITEM_BASE, FACEPAINT_ITEM_BASE, GM_POINTER, IMAGE_BASE, PHASE_ADDR, PHASE_MGR_ADDR,
                            PHASE_MIRROR_ADDR, PHASE_TIMER_ADDR, rebase, static_u32)  # noqa: F401
except ImportError:      # used as a plain top-level module by the tools
    from constants import (CAMO_ITEM_BASE, FACEPAINT_ITEM_BASE, GM_POINTER, IMAGE_BASE, PHASE_ADDR, PHASE_MGR_ADDR,
                           PHASE_MIRROR_ADDR, PHASE_TIMER_ADDR, rebase, static_u32)  # noqa: F401

WEAPON_TABLE, WEAPON_STRIDE = 0x1535B7CF8, 0x58

GM = {  # offset, struct format
    "life": (0x7B4, "<h"), "life_max": (0x7B6, "<h"),
    "stamina": (0xB7A, "<h"), "stamina_max": (0xB7C, "<h"),
    "battery": (0xB7E, "<h"), "battery_max": (0xB80, "<h"),
    "weapon": (0x704, "<h"), "weapon_load": (0x70C, "<h"),
    "phase_level": (0x19C4, "<i"), "weather": (0x19C0, "<h"),
    "prev_weapon": (0x708, "<h"), "worn_camo": (0x7AE, "<B"), "worn_paint": (0x7AF, "<B"),
}

GRENADES = range(19, 24)
CURE_ITEMS = range(36, 47)
MEDICINE_ITEMS = range(1, 5)
INFINITY_FACE_PAINT, STEALTH_CAMO = 130, 23
TIMED_SECONDS = 180

CAMO_ITEMS = range(CAMO_ITEM_BASE, FACEPAINT_ITEM_BASE)    # uniform item id = 51 + ECamouflageType
FACEPAINT_ITEMS = range(FACEPAINT_ITEM_BASE, FACEPAINT_ITEM_BASE + 31)   # face paint item id = 117 + EFacePaintType
FASHION_NO_PAINT = {INFINITY_FACE_PAINT, 127}   # never from Fashion: the timed benefit, the Mask (Raikov's disguise)
FOOD_SLOTS = range(31, 47)           # Snake's food slots in the weapon table (dead food, definitions 50..97)
ANIMAL_SLOTS = range(47, 50)         # his three live-animal slots (definitions 98..130)
FOOD_CAPTURE_SHIFT = 600000          # seconds; > 2 x 259200 (rotten after 3 game days, half-speed kinds at 6 days)
FOOD_SPOILABLE = range(50, 98)       # food definitions that rot ...
FOOD_NEVER_ROT = range(0x5D, 0x61)   # ... except 0x5D..0x60
DISARM_FALLBACK_SECONDS = 1.0        # then write the weapon field if ChangeWeaponSlot(0) did not empty it
# EGsrPlayerWoundType values the debug menu's add-wound callback accepts (no 5)
WOUNDS = {1: "burn", 2: "cut", 3: "bullet", 4: "shotgun", 6: "bee sting", 7: "leech", 8: "arrow", 9: "needle"}
WOUND_BY_NAME = {"Burn": 1, "Cut": 2, "Bullet": 3, "Shotgun": 4, "Bee Sting": 6, "Leech": 7, "Arrow": 8, "Needle": 9}
INJURY_SPACING = 1.5                 # seconds between the wounds of one trap (a burst of calls froze the game once)

# BP_Weather_System_C E_WeatherType index -> legacy EGsrWeather value (GM+0x19C0)
WEATHER_LEGACY = {0: 0, 1: 5, 2: 4, 3: 1, 4: 2, 5: 7, 6: 8, 7: 9}
WEATHER_FROM_LEGACY = {v: k for k, v in WEATHER_LEGACY.items() if v}
WEATHER_SECONDS = 60                 # the old weather returns after this long

# Alert trap: the legacy phase manager, its enemy groups and enemies.
MGR_GROUP_COUNT, MGR_GROUPS, MGR_FLAGS, MGR_NOTIFY = 0x58, 0x60, 0x88, 0x90     # offsets from PHASE_MGR_ADDR
GROUP_COUNT, GROUP_MEMBERS = 0x24, 0x28                                          # offsets from a group
# offsets from an enemy; the three timers (alert, evasion, caution) are consecutive dwords from ENEMY_TIMERS
ENEMY_FLAGS, ENEMY_FLAGS2, ENEMY_STATE, ENEMY_TIMERS = 0x9578, 0x95A8, 0x922C, 0x95CC
ENEMY_CAUTION_TIMER = ENEMY_TIMERS + 8
ENEMY_ACTIVE_MASK = 0xDE             # an enemy is active when flags & mask == 0
ENEMY_CAUTION_STATE = 3              # 0 sneak, 1 alert, 2 evasion, 3 caution
ENEMY_CAUTION_FLAG = 0x2000000       # ENEMY_FLAGS2 bit set on entering caution
CAUTION_FRAMES = 0x4650              # the engine's timer value on entering a state (300 s at 60 fps)
NOTIFY_CAUTION = 4                   # MGR_NOTIFY bit 2: caution entered
MAX_GROUPS, MAX_MEMBERS, MAX_ALERT_ENEMIES = 64, 64, 16    # sanity limits: beyond them the layout is unexpected
ALERT_MODES = ("hold", "caution", "caution+hold")
ALERT_HOLD_SECONDS = 60              # "hold": the caution is kept up this long
ALERT_REFRESH_SECONDS = 1.0

# Values from the static research. None switches the trap off ("inactive").
CALIBRATE = {"alert_phase_value": 3,                  # Caution
             "alert_mode": "caution+hold",            # hold: keep one enemy's caution timer up; caution: put the
                                                      # enemies into the caution state; caution+hold: both
             "weather_values": (1, 2, 5, 6)}          # BP indices: light rain, heavy rain, fog, snow

_timed = []   # (expires_at, item_id)
_deferred = []   # (due, callable(game))
_alert_hold = None       # {"until", "next", "value"} while an Alert trap keeps the caution up
_weather_restore = None  # {"due", "old" (BP index or None), "modlink"} while a Weather trap is running


_rebase = rebase
_static_u32 = static_u32


def _gm(game):
    """Address of the live GM struct (Snake's state)."""
    return struct.unpack("<Q", game.read(_rebase(game, GM_POINTER), 8))[0]


def _get(game, field):
    """Read a GM field (see GM)."""
    off, fmt = GM[field]
    return struct.unpack(fmt, game.read(_gm(game) + off, struct.calcsize(fmt)))[0]


def _set(game, field, value):
    """Write a GM field (see GM)."""
    off, fmt = GM[field]
    game.write(_gm(game) + off, struct.pack(fmt, value))


def _weapon_entry(game, weapon_id):
    return _rebase(game, WEAPON_TABLE) + weapon_id * WEAPON_STRIDE


# --- traps ----------------------------------------------------------------------------------------------
def hunger(game, **_):
    """Trap: halve Snake's stamina."""
    maximum = max(_get(game, "stamina_max"), 1)
    _set(game, "stamina", max(0, _get(game, "stamina") - maximum // 2))
    return "Snake's stomach growls (stamina halved)"


def empty_magazine(game, **_):
    weapon_id = _get(game, "weapon")
    if not 0 < weapon_id < 31:
        return "Empty magazine (no weapon equipped)"
    game.write(_weapon_entry(game, weapon_id) + 0x2C, struct.pack("<h", 0))
    _set(game, "weapon_load", 0)
    return "Your magazine is empty - reload!"


def battery_drain(game, **_):
    _set(game, "battery", 0)
    return "Battery drained"


def snake_bite(game, **_):
    maximum = max(_get(game, "life_max"), 1)
    _set(game, "life", max(1, _get(game, "life") - maximum // 5))
    return "Snake bite! (life -20%)"


def butterfingers(game, **_):
    stacks = [("item", i) for i, _, c, _ in game.items() if c > 0 and (i in MEDICINE_ITEMS or i in CURE_ITEMS)]
    stacks += [("weapon", weapon_id) for weapon_id, stock, _ in game.weapons() if weapon_id in GRENADES and stock > 0]
    if not stacks:
        return "Butterfingers (nothing to drop)"
    kind, iid = random.choice(stacks)
    if kind == "item":
        game.give_item(iid, 0)
    else:
        game.give_weapon(iid, 0)
    return "Butterfingers! You dropped a stack"


def _playable(stage):
    return bool(stage) and "_" not in stage


def caution_flags(flags):
    """Phase flag word (PHASE_MGR_ADDR + MGR_FLAGS) as the engine leaves it when the phase becomes caution: bit 5 set,
    the alert / "all clear" / evasion bits (4, 13, 14) cleared so the enemies' sync loop does not reset them."""
    return ((flags & 0xFFFF9FEF) | 0x20) & 0xE7FFFA7F


def _ptr_ok(pointer):
    return 0x10000 <= pointer < 0x800000000000


def _u32(game, addr):
    return struct.unpack("<I", game.read(addr, 4))[0]


def _u64(game, addr):
    return struct.unpack("<Q", game.read(addr, 8))[0]


def active_enemies(game, limit=MAX_ALERT_ENEMIES):
    """Addresses of up to `limit` active enemies, found through the phase manager's group table (group pointers at
    M+0x60, member pointers at group+0x28). Counts beyond the sanity limits and implausible pointers are skipped;
    unreadable memory ends the search with what was found so far."""
    mgr = rebase(game, PHASE_MGR_ADDR)
    out = []
    try:
        groups = _u32(game, mgr + MGR_GROUP_COUNT)
        if groups > MAX_GROUPS:
            return out
        for i in range(groups):
            group = _u64(game, mgr + MGR_GROUPS + 8 * i)
            if not _ptr_ok(group):
                continue
            count = _u32(game, group + GROUP_COUNT)
            if count > MAX_MEMBERS:
                continue
            for j in range(count):
                enemy = _u64(game, group + GROUP_MEMBERS + 8 * j)
                if _ptr_ok(enemy) and _u32(game, enemy + ENEMY_FLAGS) & ENEMY_ACTIVE_MASK == 0:
                    out.append(enemy)
                    if len(out) >= limit:
                        return out
    except OSError:
        pass
    return out


def _enter_caution(game, enemy):
    """The engine's own entry into caution for one enemy: state 3, alert/evasion timers 0, full caution timer, flag."""
    flags2 = _u32(game, enemy + ENEMY_FLAGS2)
    game.write(enemy + ENEMY_STATE, struct.pack("<I", ENEMY_CAUTION_STATE))
    game.write(enemy + ENEMY_TIMERS, struct.pack("<III", 0, 0, CAUTION_FRAMES))
    game.write(enemy + ENEMY_FLAGS2, struct.pack("<I", flags2 | ENEMY_CAUTION_FLAG))


def _set_phase(game, value):
    _static_u32(game, PHASE_ADDR, value)
    _static_u32(game, PHASE_MIRROR_ADDR, value)


def alert(game, difficulty=2, stage=None, mode=None, **_):
    """Caution through the legacy phase manager (memory only). The phase global alone is recomputed every frame from
    the enemies, so they are changed too. Modes: "caution" puts every active enemy (up to MAX_ALERT_ENEMIES) into the
    caution state the way the engine does and sets the flag word and phase; "hold" keeps one enemy's caution timer and
    the phase up for ALERT_HOLD_SECONDS (refreshed by tick); "caution+hold" (default) does both. Skipped without
    active enemies."""
    global _alert_hold
    mode = mode or CALIBRATE["alert_mode"]
    if difficulty >= 5:
        return "Alert trap skipped (European Extreme)"
    if not _playable(stage):
        return "Alert trap skipped (not in a playable stage)"
    value = CALIBRATE["alert_phase_value"]
    if value is None or mode not in ALERT_MODES:
        return "Alert trap (inactive: not calibrated yet)"
    if _static_u32(game, PHASE_ADDR) != 0:
        return "Enemies are already on alert"
    enemies = active_enemies(game)
    if not enemies:
        return "Alert trap skipped (no active enemies)"
    try:
        if "caution" in mode:
            for enemy in enemies:                     # enemy fields first, then the flag word, then the phase
                _enter_caution(game, enemy)
            mgr = rebase(game, PHASE_MGR_ADDR)
            game.write(mgr + MGR_FLAGS, struct.pack("<I", caution_flags(_u32(game, mgr + MGR_FLAGS))))
            game.write(mgr + MGR_NOTIFY, struct.pack("<I", _u32(game, mgr + MGR_NOTIFY) | NOTIFY_CAUTION))
        else:
            game.write(enemies[0] + ENEMY_CAUTION_TIMER, struct.pack("<I", CAUTION_FRAMES))
        _set_phase(game, value)
    except OSError:
        return "Alert trap failed (game memory not writable)"
    if "hold" in mode:
        now = time.monotonic()
        _alert_hold = {"until": now + ALERT_HOLD_SECONDS, "next": now + ALERT_REFRESH_SECONDS, "value": value}
    return "Enemies are on alert!"


def _hold_alert(game, now):
    """Refresh the held caution: one active enemy's caution timer (keeps the manager's timer above 0) and the phase
    dwords. Ends when the time is up, the phase turned into a real alert/evasion, or no enemy is active any more."""
    global _alert_hold
    hold = _alert_hold
    if now >= hold["until"]:
        _alert_hold = None
        return
    if now < hold["next"]:
        return
    hold["next"] = now + ALERT_REFRESH_SECONDS
    enemies = active_enemies(game, 1)
    if _static_u32(game, PHASE_ADDR) not in (0, hold["value"]) or not enemies:
        _alert_hold = None
        return
    game.write(enemies[0] + ENEMY_CAUTION_TIMER, struct.pack("<I", CAUTION_FRAMES))
    _set_phase(game, hold["value"])


def weather(game, modlink=None, stage=None, **_):
    """BP_Weather_System_C:SwitchWeather(n) through the mod (the visible change) plus the legacy GM weather field.
    After WEATHER_SECONDS the old weather is switched back (tick sends the restore command)."""
    global _weather_restore
    values = CALIBRATE["weather_values"]
    if not values:
        return "Weather trap (inactive: not calibrated yet)"
    if modlink is None or not _playable(stage):
        return "Weather trap skipped (needs the mod and a playable stage)"
    old = WEATHER_FROM_LEGACY.get(_get(game, "weather"))
    weather_type = random.choice([v for v in values if v != old] or list(values))
    _set(game, "weather", WEATHER_LEGACY[weather_type])
    modlink.weather(weather_type)
    due = time.monotonic() + WEATHER_SECONDS
    if _weather_restore is None:
        _weather_restore = {"due": due, "old": old, "modlink": modlink}
    else:
        _weather_restore["due"] = due                  # a second trap while one runs keeps the original weather
    return "The weather turns"


def disarm(game, modlink=None, stage=None, **_):
    """AGsrPlayer:ChangeWeaponSlot(0) through the mod; if the weapon field is still set a second later, write it."""
    if modlink is None or not _playable(stage):
        return "Disarm skipped (needs the mod and a playable stage)"
    if _get(game, "weapon") == 0:
        return "Disarm (already unarmed)"
    modlink.disarm()

    def fallback(current_game):
        if _get(current_game, "weapon") != 0:
            _set(current_game, "weapon", 0)
            _set(current_game, "prev_weapon", 0)
    _deferred.append((time.monotonic() + DISARM_FALLBACK_SECONDS, fallback))
    return "Snake's weapon is taken away (re-equip it from the menu)"


def fashion(game, modlink=None, stage=None, **_):
    """Wear a random owned camouflage and face paint, not both the worn ones: write the worn bytes, then ask the mod
    to refresh the model. (The session may add a wearable item on top, see Session.fashion_wearable.)"""
    if modlink is None or not _playable(stage):
        return "Fashion trap skipped (needs the mod and a playable stage)"
    worn = (_get(game, "worn_camo"), _get(game, "worn_paint"))
    owned = [(i, count) for i, _, count, _ in game.items() if count > 0]
    camos = [i - CAMO_ITEM_BASE for i, _ in owned if i in CAMO_ITEMS]
    paints = [i - FACEPAINT_ITEM_BASE for i, _ in owned if i in FACEPAINT_ITEMS and i not in FASHION_NO_PAINT]
    if worn[1] not in paints:
        paints.append(worn[1])               # the worn one (e.g. the Mask) may stay
    options = [(c, p) for c in camos for p in paints if (c, p) != worn]
    if not options:
        return "Fashion trap (no other camouflage owned)"
    camo, paint = random.choice(options)
    _set(game, "worn_camo", camo)
    _set(game, "worn_paint", paint)
    modlink.camo_refresh(camo, paint)
    return "A new look! (random camouflage)"


def normalize_food_kind(kind):
    """Dead-food kind of a stored kind: live animals (definitions 98..129) are the food ids + 48, Tsuchinoko alive
    (130) is 97."""
    if 98 <= kind <= 129:
        return kind - 48
    return 97 if kind == 130 else kind


def food_kinds(game):
    """Set of food keys in Snake's food and live-animal slots: the normalised kind of each occupied slot, or -slot if
    its data pointer is unreadable. Raises OSError if the weapon table is unreadable."""
    out = set()
    for slot in (*FOOD_SLOTS, *ANIMAL_SLOTS):
        entry = _weapon_entry(game, slot)
        if struct.unpack("<h", game.read(entry + 0x28, 2))[0] <= 0:
            continue
        try:
            ptr = struct.unpack("<Q", game.read(entry + 0x50, 8))[0]
            out.add(normalize_food_kind(struct.unpack("<i", game.read(ptr, 4))[0]) if ptr >= 0x10000 else -slot)
        except OSError:
            out.add(-slot)
    return out


def spoiled_food(game, **_):
    """Age every spoilable food in Snake's food slots by moving its capture time into the past."""
    spoiled = 0
    for slot in FOOD_SLOTS:
        entry = _weapon_entry(game, slot)
        try:
            if struct.unpack("<h", game.read(entry + 0x28, 2))[0] <= 0:
                continue
            ptr = struct.unpack("<Q", game.read(entry + 0x50, 8))[0]
            if ptr < 0x10000:
                continue
            kind, captured = struct.unpack("<ii", game.read(ptr, 8))
            if kind not in FOOD_SPOILABLE or kind in FOOD_NEVER_ROT:
                continue
            game.write(ptr + 4, struct.pack("<i", max(-0x80000000, captured - FOOD_CAPTURE_SHIFT)))
            spoiled += 1
        except OSError:
            continue          # unreadable pointer: skip this slot
    if not spoiled:
        return "Spoiled food (no food to spoil)"
    return f"Your food has gone bad ({spoiled} item{'s' if spoiled != 1 else ''})"


def injury(game, modlink=None, stage=None, options=None, **_):
    """injury_count random wounds from injury_wounds (YAML), through the debug menu's add-wound callback in the mod.
    Several wounds are spaced INJURY_SPACING apart."""
    if modlink is None or not _playable(stage):
        return "Injury trap skipped (needs the mod and a playable stage)"
    options = options or {}
    chosen_names = options.get("injury_wounds") or WOUND_BY_NAME
    pool = [WOUND_BY_NAME[name] for name in chosen_names if name in WOUND_BY_NAME] or list(WOUND_BY_NAME.values())
    wounds = [random.choice(pool) for _ in range(max(1, int(options.get("injury_count", 1))))]
    modlink.injury(wounds[0])
    now = time.monotonic()
    for k, wound in enumerate(wounds[1:], 1):
        _deferred.append((now + k * INJURY_SPACING, lambda _game, w=wound: modlink.injury(w)))
    return f"Snake is injured! ({', '.join(WOUNDS[w] for w in wounds)})"


def area_reset(game, modlink=None, stage=None, **_):
    """Reload the current playable stage through the mod."""
    if modlink is None or not stage or "_" in stage:
        return "Area reset skipped"
    modlink.warp(stage)
    return "Area reset!"


# --- benefits -------------------------------------------------------------------------------------------
def _refill_weapons(game, ids):
    for weapon_id, stock, maximum in game.weapons():
        if weapon_id in ids and stock >= 0 and maximum > 1:
            game.give_weapon(weapon_id, min(maximum, stock + max(1, maximum // 4)))


def ammo_refill(game, **_):
    _refill_weapons(game, set(range(1, 31)) - set(GRENADES))
    return "Ammo refilled"


def grenade_refill(game, **_):
    _refill_weapons(game, set(GRENADES))
    return "Grenades refilled"


def suppressor_recovery(game, **_):
    for iid, _, count, maximum in game.items():
        if iid in (48, 49, 50) and count > 0 and maximum > 0:
            game.give_item(iid, maximum)
    return "Suppressors restored"


def life_refill(game, **_):
    _set(game, "life", _get(game, "life_max"))
    return "Life restored"


def stamina_refill(game, **_):
    _set(game, "stamina", _get(game, "stamina_max"))
    return "Stamina restored"


def battery_refill(game, **_):
    _set(game, "battery", _get(game, "battery_max"))
    return "Battery recharged"


def cure_kit(game, **_):
    for iid, _, count, maximum in game.items():
        if iid in CURE_ITEMS and maximum > 0:
            game.give_item(iid, min(maximum, max(count, 0) + 2))
    return "Cure kit received"


def food(game, **_):
    # TODO: real food slots (FD_* table); until then food restores stamina.
    stamina_refill(game)
    return "Food! (stamina restored)"


def _timed_item(item_id, label):
    def effect(game, **_):
        for iid, _, count, _ in game.items():
            if iid == item_id and count > 0:
                return f"{label} (already owned)"
        game.give_item(item_id, 1)
        _timed.append((time.monotonic() + TIMED_SECONDS, item_id))
        return f"{label} for {TIMED_SECONDS // 60} minutes"
    return effect


EFFECTS = {
    "Trap: Hunger": hunger, "Trap: Empty Magazine": empty_magazine, "Trap: Alert": alert,
    "Trap: Battery Drain": battery_drain, "Trap: Fashion": fashion, "Trap: Disarm": disarm,
    "Trap: Butterfingers": butterfingers, "Trap: Weather": weather, "Trap: Snake Bite": snake_bite,
    "Trap: Injury": injury, "Trap: Spoiled Food": spoiled_food, "Trap: Area Reset": area_reset,
    "Ammo Refill": ammo_refill, "Grenade Refill": grenade_refill, "Suppressor Recovery": suppressor_recovery,
    "Food": food, "Life Refill": life_refill, "Stamina Refill": stamina_refill, "Battery Refill": battery_refill,
    "Cure Kit": cure_kit,
    "Infinity Face Paint (temporary)": _timed_item(INFINITY_FACE_PAINT, "Infinity Face Paint"),
    "Stealth Camo (temporary)": _timed_item(STEALTH_CAMO, "Stealth Camo"),
}


def apply_effect(game, name, difficulty=2, modlink=None, stage=None, options=None):
    """Run the trap/benefit called name; returns its popup text, or None if name is not an effect."""
    effect = EFFECTS.get(name)
    if effect is None:
        return None
    return effect(game, difficulty=difficulty, modlink=modlink, stage=stage, options=options)


def tick(game):
    """Per client loop: refresh the held Alert, restore the Weather trap's weather, run due deferred writes and end
    the timed items."""
    global _weather_restore
    now = time.monotonic()
    if _alert_hold is not None:
        try:
            _hold_alert(game, now)
        except OSError:
            pass
    if _weather_restore is not None and now >= _weather_restore["due"]:
        restore, _weather_restore = _weather_restore, None
        restore["modlink"].weather_restore(restore["old"])
        if restore["old"] is not None:
            try:
                _set(game, "weather", WEATHER_LEGACY[restore["old"]])
            except OSError:
                pass
    for entry in [d for d in _deferred if d[0] <= now]:
        _deferred.remove(entry)
        try:
            entry[1](game)
        except OSError:
            pass
    for entry in [t for t in _timed if t[0] <= now]:
        _timed.remove(entry)
        game.give_item(entry[1], 0)
