"""Safe warps: land at a real gate instead of a stage's leftover spawn pose.

A stage's exit proc writes the entry pose and gate into the legacy GCL script variable store, then loads the target:
  $var[ef4] x, $var[ef8] y, $var[efc] z, $var[f00] rotation (int32), $var[ef1] flag (byte), $var[d8] gate strcode,
  $var[24c] / $var[250] previous / current stage strcode (int32).
The store is pointed to by 0x14C532028 (next to the GM/linkvar pointer) and holds values big-endian (PS2 layout),
see the GCL variable resolver at 0x147AD141E and the typed reader at 0x147AD2C00. A bare Mgs3LoadCmd keeps whatever
pose the last exit left, which put Snake under the map in testing. Writing the pose first reproduces the exit proc.
"""
import struct

try:
    from .constants import FREE_STAGE, IMAGE_BASE, VAR_POINTER, read_pointer  # noqa: F401 (IMAGE_BASE: tests)
except ImportError:      # used as a plain top-level module by the tools
    from constants import FREE_STAGE, IMAGE_BASE, VAR_POINTER, read_pointer  # noqa: F401

POSE_OFFSETS = (0xEF4, 0xEF8, 0xEFC, 0xF00)
FLAG_OFFSET = 0xEF1
GATE_OFFSET = 0xD8
PREV_STAGE_OFFSET, CUR_STAGE_OFFSET = 0x24C, 0x250
BOX_RIDE_OFFSET = 0x95B       # Groznyj Grad box delivery marker: 1 = East Wing (s101), 2 = Main Wing (s121a)
STORY_OFFSET, VM_STORY_OFFSET = 0x4, 0x2     # int16: main story progress ($var[4]) and Virtuous Mission ($var[2])


def strcode(text):
    """The game's string hash (GCL string code) of a stage name or gate name."""
    code = 0
    for byte in text.encode():
        code = ((code >> 19) | (code << 5)) & 0xFFFFFFFF
        code = (code + byte) & 0x00FFFFFF
    return code or 1


class ScriptVars:
    """The GCL script variable store ($var[...]): big-endian values at offsets from the store pointer."""

    def __init__(self, game):
        self.game = game

    def base(self):
        return read_pointer(self.game, VAR_POINTER, "script variable store")

    def read_i32(self, off):
        return struct.unpack(">i", self.game.read(self.base() + off, 4))[0]

    def write_i32(self, off, value):
        self.game.write(self.base() + off, struct.pack(">i", value))

    def read_i16(self, off):
        return struct.unpack(">h", self.game.read(self.base() + off, 2))[0]

    def write_i16(self, off, value):
        self.game.write(self.base() + off, struct.pack(">h", value))

    def read_u8(self, off):
        return self.game.read(self.base() + off, 1)[0]

    def write_u8(self, off, value):
        self.game.write(self.base() + off, bytes([value & 0xFF]))

    def pose(self):
        """The current entry pose: x, y, z, rotation and the flag byte."""
        return [self.read_i32(o) for o in POSE_OFFSETS] + [self.read_u8(FLAG_OFFSET)]


def choose_entry(entries, target, via=None, proc=None):
    """The entry into target to land at: from `via` if given (its exit proc `proc` if given: a cutscene stage can have
    several), else the first one from a playable stage."""
    options = entries.get(target, [])
    if via:
        options = [e for e in options if e["from"] == via] or options
    if proc is not None:
        options = [e for e in options if e["proc"] == proc] or options
    playable = [e for e in options if FREE_STAGE.match(e["from"])]
    return (playable or options or [None])[0]


def prepare_warp(game, entries, target, current=None, via=None, proc=None):
    """Write the gate pose of an entry into target; the next Mgs3LoadCmd(target) lands there.
    Returns the entry used (None: no known gate)."""
    entry = choose_entry(entries, target, via, proc)
    if entry is None:
        return None
    sv = ScriptVars(game)
    x, y, z, rot, flag = entry["pose"]
    for off, value in zip(POSE_OFFSETS, (x, y, z, rot)):
        sv.write_i32(off, value)
    sv.write_u8(FLAG_OFFSET, flag)
    if entry.get("gate") is not None:
        sv.write_i32(GATE_OFFSET, entry["gate"])
    if current:
        sv.write_i32(PREV_STAGE_OFFSET, strcode(current))
    sv.write_i32(CUR_STAGE_OFFSET, strcode(target))
    return entry


def safe_warp(game, modlink, entries, target, current=None, via=None):
    """Write the gate pose of an entry into target, then warp. Returns the entry used (None: no known gate)."""
    entry = prepare_warp(game, entries, target, current, via)
    if entry is not None:
        modlink.warp(target)
    return entry
