"""Where the MGSDeltaAP mod folder lives: the client talks to the mod through files in it.

Order: the MGSDELTA_GAME_DIR environment variable (the game's install folder, the one holding MGSDelta.exe), then every
Steam library listed in Steam's libraryfolders.vdf, then the default Steam library."""
import os
import re

GAME_FOLDER = "MGSDelta"
MOD_SUBDIR = os.path.join("MGSDelta", "Binaries", "Win64", "ue4ss", "Mods", "MGSDeltaAP")
DEFAULT_STEAM = r"C:\Program Files (x86)\Steam"


def steam_root():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            return os.path.normpath(winreg.QueryValueEx(key, "SteamPath")[0])
    except (ImportError, OSError):
        return DEFAULT_STEAM


def steam_libraries(root):
    libraries = [root]
    try:
        with open(os.path.join(root, "steamapps", "libraryfolders.vdf"), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return libraries
    for path in re.findall(r'"path"\s+"([^"]+)"', text):
        path = os.path.normpath(path.replace("\\\\", "\\"))
        if os.path.normcase(path) not in map(os.path.normcase, libraries):
            libraries.append(path)
    return libraries


def game_dir():
    override = os.environ.get("MGSDELTA_GAME_DIR")
    if override:
        return override
    root = steam_root()
    for library in steam_libraries(root):
        candidate = os.path.join(library, "steamapps", "common", GAME_FOLDER)
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(DEFAULT_STEAM, "steamapps", "common", GAME_FOLDER)


def mod_file(name):
    return os.path.join(game_dir(), MOD_SUBDIR, name)
