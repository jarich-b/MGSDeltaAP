"""External memory access to MGS Delta's legacy MGS3 inventory tables.

Layout recovered from the debug "get all weapons/items" routine (exe 0x147A7ED10):
  weapon table: 0x58-byte entries indexed by EWeaponName id; word +0x28 = stock (owned when > 0),
                word +0x2A = max.
  item table:   0x50-byte entries indexed by EItemName id; byte +0x20 = id, word +0x24 = count,
                word +0x26 = max. Camos/face paints (51..147) live in the same table.

Usage:
  python mgsmem.py dump
  python mgsmem.py giveitem <id> [count]
  python mgsmem.py giveweapon <id> [stock]
"""
import ctypes
import ctypes.wintypes as wt
import struct
import sys

try:
    from .constants import IMAGE_BASE
except ImportError:      # used as a plain top-level module by the tools
    from constants import IMAGE_BASE

PROCESS_NAME = "MGSDelta-Win64-Shipping.exe"
WEAPON_TABLE_RVA = 0x1535B7CF8 - IMAGE_BASE
ITEM_TABLE_RVA = 0x1535BAA00 - IMAGE_BASE
WEAPON_STRIDE, ITEM_STRIDE = 0x58, 0x50
PLAYER_WEAPON_COUNT = 31  # EWeaponName::WP_MAX_PLAYER
ITEM_COUNT = 148          # EItemName::IT_MAX_ITEMS

WEAPON_NAMES = ["None", "Knife", "Fork", "CigarPistol", "Handkerchief", "Mk22", "Gove", "EasyGun", "SAArmy",
                "PatriotPistol", "Scorpion", "M16A1", "AKM", "M63", "Ithaca", "SVD", "MosinNagant", "RPG", "Torch",
                "Grenade", "FireGrenade", "StunGrenade", "ChaffGrenade", "SmokeGrenade", "Magazine", "TNT", "C3",
                "Claymore", "Book", "RatTrap", "Microphone"]
ITEM_NAMES = ["None", "MedicalKit", "Pentazemin", "Asphyxia", "Analeptic", "Cigar", "Scope", "Thermal",
              "NightVision", "Camera", "MotionDetect", "ActiveSonar", "MineDetect", "VibrateDetect", "CBoxA",
              "CBoxB", "CBoxC", "CBoxD", "AlligatorCap", "KeyA", "KeyB", "KeyC", "Bandana", "Stealth", "Mothproof",
              "MonkeyMask", "ATCamo", "Compass", "AlligatorSuit", "GakoMask", "KerotanMask"]

PROCESS_VM_READ, PROCESS_VM_WRITE, PROCESS_VM_OPERATION = 0x10, 0x20, 0x8
PROCESS_QUERY_INFORMATION = 0x400
TH32CS_SNAPPROCESS, TH32CS_SNAPMODULE, TH32CS_SNAPMODULE32 = 0x2, 0x8, 0x10

k32 = ctypes.WinDLL("kernel32", use_last_error=True)


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", wt.WCHAR * 260)]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("th32ModuleID", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("GlblcntUsage", wt.DWORD), ("ProccntUsage", wt.DWORD), ("modBaseAddr", ctypes.c_void_p),
                ("modBaseSize", wt.DWORD), ("hModule", wt.HMODULE), ("szModule", wt.WCHAR * 256),
                ("szExePath", wt.WCHAR * 260)]


k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.OpenProcess.restype = wt.HANDLE
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_size_t)]
k32.WriteProcessMemory.argtypes = k32.ReadProcessMemory.argtypes


class Game:
    def __init__(self, writable=False):
        self.pid = self._find_pid()
        access = PROCESS_VM_READ | PROCESS_QUERY_INFORMATION
        if writable:
            access |= PROCESS_VM_WRITE | PROCESS_VM_OPERATION
        self.handle = k32.OpenProcess(access, False, self.pid)
        if not self.handle:
            raise OSError(f"OpenProcess failed: {ctypes.get_last_error()}")
        self.base = self._module_base()

    @staticmethod
    def _find_pid():
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        entry = PROCESSENTRY32W(dwSize=ctypes.sizeof(PROCESSENTRY32W))
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        try:
            while ok:
                if entry.szExeFile.lower() == PROCESS_NAME.lower():
                    return entry.th32ProcessID
                ok = k32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snap)
        raise RuntimeError(f"{PROCESS_NAME} is not running")

    def _module_base(self):
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, self.pid)
        entry = MODULEENTRY32W(dwSize=ctypes.sizeof(MODULEENTRY32W))
        ok = k32.Module32FirstW(snap, ctypes.byref(entry))
        try:
            while ok:
                if entry.szModule.lower() == PROCESS_NAME.lower():
                    return entry.modBaseAddr
                ok = k32.Module32NextW(snap, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snap)
        raise RuntimeError("game module not found")

    def read(self, addr, size):
        buf = ctypes.create_string_buffer(size)
        n = ctypes.c_size_t()
        if not k32.ReadProcessMemory(self.handle, addr, buf, size, ctypes.byref(n)):
            raise OSError(f"ReadProcessMemory {addr:#x} failed: {ctypes.get_last_error()}")
        return buf.raw

    def write(self, addr, data):
        n = ctypes.c_size_t()
        if not k32.WriteProcessMemory(self.handle, addr, data, len(data), ctypes.byref(n)):
            raise OSError(f"WriteProcessMemory {addr:#x} failed: {ctypes.get_last_error()}")

    def weapon_addr(self, wid):
        return self.base + WEAPON_TABLE_RVA + wid * WEAPON_STRIDE

    def item_addr(self, iid):
        return self.base + ITEM_TABLE_RVA + iid * ITEM_STRIDE

    def weapons(self):
        raw = self.read(self.weapon_addr(0), PLAYER_WEAPON_COUNT * WEAPON_STRIDE)
        for wid in range(PLAYER_WEAPON_COUNT):
            stock, maximum = struct.unpack_from("<hh", raw, wid * WEAPON_STRIDE + 0x28)
            yield wid, stock, maximum

    def items(self):
        raw = self.read(self.item_addr(0), ITEM_COUNT * ITEM_STRIDE)
        for iid in range(ITEM_COUNT):
            tag = raw[iid * ITEM_STRIDE + 0x20]
            count, maximum = struct.unpack_from("<hh", raw, iid * ITEM_STRIDE + 0x24)
            yield iid, tag, count, maximum

    def give_weapon(self, wid, stock=None):
        _, _, maximum = next(w for w in self.weapons() if w[0] == wid)
        value = stock if stock is not None else max(maximum, 1)
        self.write(self.weapon_addr(wid) + 0x28, struct.pack("<h", value))

    def give_item(self, iid, count=None):
        _, _, _, maximum = next(i for i in self.items() if i[0] == iid)
        value = count if count is not None else max(maximum, 1)
        self.write(self.item_addr(iid) + 0x24, struct.pack("<h", value))

    def set_maximum(self, kind, item_id, value):
        """Write the stack maximum of a weapon (+0x2A) or item (+0x26)."""
        addr = self.weapon_addr(item_id) + 0x2A if kind == "weapon" else self.item_addr(item_id) + 0x26
        self.write(addr, struct.pack("<h", value))


def name_of(names, idx):
    return names[idx] if idx < len(names) else f"#{idx}"


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "dump"
    game = Game(writable=cmd != "dump")
    print(f"pid {game.pid}, module base {game.base:#x}")
    if cmd == "dump":
        print("-- weapons (id name stock/max)")
        for wid, stock, maximum in game.weapons():
            print(f"{wid:3} {name_of(WEAPON_NAMES, wid):14} {stock:6}/{maximum}")
        print("-- items (id tag name count/max)")
        for iid, tag, count, maximum in game.items():
            print(f"{iid:3} {tag:3} {name_of(ITEM_NAMES, iid):14} {count:6}/{maximum}")
    elif cmd == "giveitem":
        game.give_item(int(argv[2]), int(argv[3]) if len(argv) > 3 else None)
        print("done")
    elif cmd == "giveweapon":
        game.give_weapon(int(argv[2]), int(argv[3]) if len(argv) > 3 else None)
        print("done")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
