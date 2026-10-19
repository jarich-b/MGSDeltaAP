# Documentation

These pages explain how the randomizer works: the parts, how they talk to each other, and why it is built this way.
You don't need them to play. The [main README](../README.md) covers setup and play.

| Page | What it covers |
|---|---|
| [Architecture](architecture.md) | The big picture: the game, the mod, the client, the Archipelago server, and the decisions behind it. |
| [The mod (Lua)](mod.md) | Every Lua script in `ue4ss_mod/MGSDeltaAP/Scripts`. |
| [The APWorld (Python)](apworld.md) | Every Python module and class in `apworld/mgs_delta`, plus the data files. |
| [How the game works](game-research.md) | What we found out about the game: inventory, item boxes, ammo boxes, area loads, camos, locks, the codec. |
| [AI disclosure](ai-disclosure.md) | How AI was used in this project. |

A few words used everywhere:

- **Stage** or **area**: one map of the game, named like `s021a` (`v...` for the Virtuous Mission, `s...` for Operation
  Snake Eater). The last letter is a variant; the game picks it from the story progress.
- **Legacy engine**: MGS Delta runs the original MGS3 game code underneath. Unreal Engine 5 draws the game and copies
  the legacy state for display. The inventory, the story progress and the stage scripts all live in the legacy engine.
- **Story value**: a number the game raises as the story goes on. Bosses, warps and the goal are read from it.
- **Proc**: a small script function of a stage. Doors and exits are procs, so running one moves Snake like a real door.
- **Box**: an item lying in the world (`ibox_...` actors in Unreal).
