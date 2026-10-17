"""Addresses, stage-name patterns and item-id ranges shared by the game modules.

No imports from the package: effects/warp/mgsmem are also used as plain top-level modules by the tools.
"""
import re
import struct

IMAGE_BASE = 0x140000000
GM_POINTER = 0x14C532038        # legacy GM block (Snake's state)
VAR_POINTER = 0x14C532028       # legacy GCL script variable store ($var[...]), next to the GM pointer
STAGE_ADDR = 0x1535D7290        # legacy stage name; changes when a stage load starts
PHASE_ADDR = 0x154522208        # live game phase: 0 sneak, 1 alert, 2 evasion, 3 caution
PHASE_TIMER_ADDR = 0x15452220C  # frames left of the current phase (an output of the phase manager)
PHASE_MIRROR_ADDR = 0x154522210 # second phase dword, set together with PHASE_ADDR by the engine's transitions
PHASE_MGR_ADDR = 0x1535C00C0    # legacy phase manager struct

FREE_STAGE = re.compile(r"^[sv]\d{3}[a-z]$")    # playable stage (cutscenes contain "_")
IN_GAME_STAGE = re.compile(r"^[sv]\d{3}[a-z]")  # playable or story cutscene stage (not title/menus)
AREA_PREFIX = re.compile(r"^[sv]\d{3}$")        # the first four characters of a stage name

CAMO_ITEM_BASE, FACEPAINT_ITEM_BASE = 51, 117   # item id = base + ECamouflageType / EFacePaintType
FACEPAINT_ITEM_END = 148        # one past the last face paint (= the item table size)
MEDICAL_IDS = (1, 2, *range(36, 48))   # Life Medicine, Pentazemin, Blood Serum .. Battery: AP items and a collection
                                       # type only with medical_checks (same as options.MEDICAL_IDS)


def rebase(game, va):
    """Address in the running process of the static image address va."""
    return va - IMAGE_BASE + game.base


def static_u32(game, va, value=None):
    """Read (and first write, if value is given) a static uint32 global. Raises OSError."""
    addr = rebase(game, va)
    if value is not None:
        game.write(addr, struct.pack("<I", value))
    return struct.unpack("<I", game.read(addr, 4))[0]


def read_pointer(game, va, what="pointer"):
    """The pointer stored at the static address va. Raises OSError if it is null (not allocated yet)."""
    ptr = struct.unpack("<Q", game.read(rebase(game, va), 8))[0]
    if not ptr:
        raise OSError(f"{what} is null")
    return ptr
