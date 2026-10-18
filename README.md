# Metal Gear Solid Delta: Snake Eater for Archipelago

An [Archipelago](https://archipelago.gg) randomizer for **Metal Gear Solid Delta: Snake Eater** (PC, Steam).

The weapons, equipment and camos you find are checks. Your items come from the multiworld and go straight into Snake's
inventory. You play the story in order, and a few doors stay locked until you get their item.

**Early alpha.** A full run has been played from start to goal, but expect bugs.

> **Play offline story mode only.** Never use this mod in the game's online modes. Start a **new game** for every seed:
> the client changes your inventory and story progress, and your in-game saves keep those changes. Your profile
> (Kerotan and Gako records, achievements) is not changed. This project is not affiliated with Konami and includes no
> game files.

## Features

- **Goal:** win the final duel with Ocelot.
- **Locked doors:** Cave Exit (Cave to Ponizovje) and Sokrovenno Exit (Sokrovenno to Krasnogorje), each optional. The
  4 C3 Charges are needed to blow up the fuel tanks before Volgin.
- **Checks:** story chapters and bosses, and the first time you get each weapon, piece of equipment, camo or medicine.
  Optional: Kerotan frogs, Gako ducks, animals, guarded areas you get through without an alert (a caution is fine)
  and CQC takedowns.
- **Items:** weapons, equipment, camos and face paints, plus benefits (refills, food, temporary Stealth Camo) and traps
  (Hunger, Alert, Disarm, Snake Bite, Injury and more).
- **Codec:** call **143.40** for your status, and **143.41 and up** to warp back to chapters you have reached.
- **DeathLink** and **release on goal**.
- **Tracker:** a [PopTracker pack](https://github.com/jarich-b/MGSDeltaAP-PopTracker) with maps of every area.

## Requirements

- Metal Gear Solid Delta: Snake Eater on PC (Steam), the version from 2026-02-01 (`MGSDelta-Win64-Shipping.exe`,
  221,809,664 bytes). A game update can break the client.
- Windows.
- [Archipelago](https://github.com/ArchipelagoMW/Archipelago/releases) 0.6.8 or newer.
- The **experimental-latest** build of [UE4SS](https://github.com/UE4SS-RE/RE-UE4SS/releases/tag/experimental-latest)
  (`UE4SS_v3.0.1-…zip`), not the older v3.0.1 "Latest" release. From the same page, also download
  `zCustomGameConfigs.zip`: the game settings for MGS Delta are in there, not in the UE4SS zip.

The client finds the game in your Steam libraries. If it is installed somewhere else, set the environment variable
`MGSDELTA_GAME_DIR` to the game folder (the one with `MGSDelta.exe`).

## Setup

Install while the game is closed.

1. **UE4SS:** extract the UE4SS zip into the game's `MGSDelta\Binaries\Win64` folder. Then open
   `zCustomGameConfigs.zip` (a separate download) and copy everything in its `MetalGearSolidDelta` folder
   (`UE4SS-settings.ini` and `UE4SS_Signatures`) into `ue4ss\`, replacing `UE4SS-settings.ini`.
   Optional: to hide the UE4SS console windows, set `ConsoleEnabled`, `GuiConsoleEnabled` and `GuiConsoleVisible` to `0`
   in `ue4ss\UE4SS-settings.ini`.
2. **Mod:** unzip `MGSDeltaAP_mod.zip` into `ue4ss\Mods\`. In `ue4ss\Mods\mods.txt`, add the line `MGSDeltaAP : 1`
   above the `Keybinds` line.
3. **APWorld:** double-click `mgs_delta.apworld` (or put it in Archipelago's `custom_worlds` folder).
4. **YAML:** edit [`mgsdelta.yaml`](mgsdelta.yaml). Every option is explained in it.
5. **Tracker (optional):** put `MGSDeltaAP_poptracker_maps.zip` from the
   [tracker releases](https://github.com/jarich-b/MGSDeltaAP-PopTracker/releases) into PopTracker's `packs`
   folder.

## Playing

> **Before you pick up anything:**
> - Start a **new game** for every seed.
> - Connect the client **before you pick up the backpack**.
> - **Pick up the backpack first.** The run starts there and the client resets Snake's inventory. Anything you picked
>   up before it does not count.

1. Start a new game in offline story mode.
2. Open **MGS Delta Client** in the Archipelago Launcher and connect to your room. Wait until it says it is connected
   to the game.
3. In the Virtuous Mission, pick up the backpack before anything else.
4. To continue later, load a save from the same run and connect again.

Client commands: `/game` shows the game connection, `/goal` sends the goal by hand, and `/startrun` starts the run on a
later save.

## Known issues

- **Box contents depend on the difficulty.** The tracker's pickup spots come from one difficulty, so a few can differ.
  The checks are not affected.
- The **Weather** trap only shows outdoors. The **Alert** trap starts a caution phase, but guards don't search.
- Warps and traps wait until you close the pause menu or a game menu.
- If a codec page does not change, call the frequency again.
- **A weapon you already received shows up as an ammo box** where its weapon box would be. Picking up that ammo box
  still sends the check.
- If a received item's box doesn't appear, leave the area and come back. The check stays open until you pick it up.

## License

[MIT](LICENSE). Metal Gear Solid Delta: Snake Eater is (c) Konami. No game files are included.
