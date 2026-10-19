# Architecture

The randomizer has two parts:

- **The mod** (`ue4ss_mod/MGSDeltaAP`): Lua scripts that run inside the game through UE4SS. It tells the client what
  happens in the game and runs the few things that only work from inside Unreal.
- **The client** (`apworld/mgs_delta`): Python code that ships in the `.apworld`. It talks to the Archipelago server and
  holds all the logic. It also reads and writes the game's memory directly.

## How the parts connect

```mermaid
flowchart LR
    server[("Archipelago server")]
    subgraph client["MGS Delta Client (Python, in the APWorld)"]
        ctx["MGSDeltaContext<br/>client.py"]
        session["Session<br/>game/session.py"]
        bridge["Bridge<br/>game/bridge.py"]
        mem["Game<br/>game/mgsmem.py"]
        link["ModLink<br/>game/modlink.py"]
        reader["EventReader<br/>game/events.py"]
    end
    subgraph files["Mod folder (ue4ss/Mods/MGSDeltaAP)"]
        ev[/"events.jsonl"/]
        cmd[/"commands.jsonl"/]
    end
    subgraph game["MGS Delta (the game process)"]
        mod["MGSDeltaAP mod<br/>Lua, UE4SS"]
        ue["Unreal Engine 5<br/>(display, actors, UI)"]
        legacy["Legacy MGS3 engine<br/>(inventory, story, stage scripts)"]
    end

    server <-- "websocket" --> ctx
    ctx --> session
    session --> bridge
    bridge --> mem
    session -- "warps, traps,<br/>story values" --> mem
    session --> link
    bridge --> reader

    mem <-- "read / write process memory" --> legacy
    mod -- "writes events" --> ev
    ev -- "reads" --> reader
    link -- "appends commands" --> cmd
    cmd -- "polls" --> mod
    mod <-- "hooks and UE function calls" --> ue
    ue <-- "mirrors" --> legacy
```

So there are three channels between the client and the game:

1. **Memory** (client to game, both ways): the inventory, life, stamina, the story value, warp positions and a few flags.
2. **`events.jsonl`** (mod to client): one JSON line per event, like an item box spawning or ending, a stage change, or a
   codec call.
3. **`commands.jsonl`** (client to mod): one JSON line per command, like showing a popup, running a stage proc, or
   changing the weather.

## One tick of the client

The client checks the game every 50 ms. Each check runs `Session.tick()`, which does the steps below in order.

```mermaid
flowchart TD
    A["Read the inventory and the stage"] --> B{"Run started?"}
    B -- no --> B1{"Backpack picked up?"}
    B1 -- no --> X["End of this tick"]
    B1 -- yes --> B2["Run start:<br/>wipe and give the loadout"]
    B2 --> C
    B -- yes --> C{"Stage changed?"}
    C -- yes --> C1["Locks and masking"]
    C -- no --> C2["Next stage announced?<br/>mask early"]
    C1 & C2 --> D["Inventory changes and pickups<br/>(new finds, take-backs)"]
    D --> E["Items received from Archipelago<br/>(give new ones once in play, regrant missing ones),<br/>forced heals"]
    E --> F["Story, bosses, codec warps, CQC, no-alert areas,<br/>Kerotans, Gako, animals"]
    F --> G["Goal, DeathLink, codec pages"]
    G --> H["Show held popups once Snake is in play"]
    H --> I["Save the run state"]
```

## Example: you pick up a weapon

```mermaid
sequenceDiagram
    participant Game
    participant Mod
    participant Client
    participant Server as AP server
    Game->>Game: Snake picks up the M37 box
    Mod->>Client: item_end (box model, position) in events.jsonl
    Client->>Game: reads inventory: M37 is now owned
    Note over Client: up to 1.5 s: one new item, not a hand-out
    Client->>Server: location check "New Weapon 3"
    Note over Client: 4 s with no stage change: the box ending was a pickup,<br/>it matches the M37 box
    Client->>Game: takes the M37 back (it was not received)
    Server-->>Client: item for you, e.g. "Thermal Goggles"
    Client->>Game: writes Thermal Goggles into the item table
    Client->>Mod: popup "Received Thermal Goggles" in commands.jsonl
    Mod->>Game: shows the label on screen
```

## Design decisions

**The mod only reports, the client decides.** All rules (what counts as a check, what to take back, which doors are
locked) are in Python. Lua under UE4SS is hard to debug, every hook runs in its own Lua state, and there are no
tests. Python has the Archipelago libraries, unit tests and a log. Keeping the mod small means fewer things break
inside the game.

**Files instead of a socket.** UE4SS Lua has no networking. Appending lines to a file works from every Lua state and
survives restarts on both sides. The mod remembers how far it read in `commands.offset`, so all of its Lua states share
one read position and a command never runs twice. On start, the mod skips commands left from the last session.

**The client writes memory itself.** No Unreal function gives or takes a single item. The game's inventory lives in
the legacy engine, in two plain tables (weapons and items), and Unreal only mirrors them. Writing those tables works at
once: the item can be equipped, fired, reloaded and saved like a normal one. The other way, a native C++ UE4SS mod,
is possible but would tie the mod to one UE4SS build. It is kept as an idea for later.

**The mod does what memory can't.** Showing text on screen, the codec pages, weather, wounds, refreshing the camo,
running a stage proc (doors) and loading a stage. These are Unreal functions, so they go through the mod.

**Offline only.** The client changes memory. Online that is cheating. See the disclaimer in the README.

**Doors, not teleports.** When the client must move Snake (a locked door, a codec warp), it runs the area's own exit
proc or writes a real gate position first. A bare stage load can put Snake under the map.

**Never take away what the story needs.** Some gifts are needed by scripted scenes (C3, RPG-7, SVD, Key C, disguises).
The client never takes those back, even when they would count as a find.

**The run state is saved per seed and slot.** Reconnecting, restarting the client or the game never repeats the
inventory wipe, never gives one-shot items twice and never counts a check again.
