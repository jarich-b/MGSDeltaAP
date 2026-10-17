-- MGSDeltaAP: in-game event reporter and command executor for the MGS Delta Archipelago client.
-- Events go to events.jsonl next to this mod (one JSON object per line; the external client tails it); the client
-- sends commands through commands.jsonl. Game-state changes are made by the client, except for the UE-side calls
-- the commands below perform.
--
-- Events (mod -> client):
--   {"type":"hello"}                                       mod (re)loaded
--   {"type":"stage","stage":"s006a"}                       current stage changed
--   {"type":"item_spawn","class":...,"stage":...,"origin":"placed"|"dropped"}   world item actor began play
--   {"type":"item_end","class":...,"stage":...,"origin":...,"x","y","z","loading":bool,"reason":n}
--                                                          world item actor left the world (pickup or unload)
--   {"type":"codec_call","freq":14340}                     the player called a frequency the client set up
--   {"type":"codec_closed"}                                the codec window closed
--   {"type":"cqc","side":"grab","proc":1}                  one CQC engagement (the client counts takedowns)
--   {"type":"gako","index":n}                              a Gako duck was broken (n = GakoIndex, -1 if unreadable)
--
-- Commands (client -> mod) are documented at the top of commands.lua.
--
-- Note: the MGS Delta UE4SS config disables the EngineTick hook, so ExecuteInGameThread never runs, and
-- LoopInGameThreadWithDelay does not run reliably either. Commands are executed from the
-- BeginPlay hook polling (see Startup); the loop is only a best-effort extra. Hook callbacks run in separate Lua
-- states, so state that must be shared between them lives in files (commands.offset).
--
-- Layout: main.lua wires the modules together; the code lives next to it in Scripts/ (UE4SS puts that folder on
-- package.path, so `require` finds the files):
--   modutil.lua   mod folder path, log, valid, player lookups     flatjson.lua  JSON encode / flat-object parser
--   events.lua    events.jsonl writer                              commands.lua  commands.jsonl reader + shared offset
--   stage.lua     stage + item events, exec_proc / warp           labels.lua    on-screen labels, popup / notify_clear
--   codec.lua     codec frequencies, paging, codec events         counters.lua  CQC + Gako events
--   traps.lua     disarm / weather / injury / camo_refresh
-- Every module is required here, at the top, so its module-level code runs in this state exactly once.

local commands = require("commands")
local events = require("events")
local labels = require("labels")
local codec = require("codec")
local counters = require("counters")
local stage = require("stage")
local traps = require("traps")
local util = require("modutil")

local log = util.log

-- Command dispatch: command name (the "cmd" field in commands.jsonl) -> handler(cmd table).
local handlers = {
    exec_proc = stage.execProc,
    warp = stage.warp,
    popup = labels.onPopup,
    notify_clear = labels.onNotifyClear,
    codec_menu = codec.onMenu,
    codec_set = codec.onSet,
    disarm = traps.disarm,
    weather = traps.weather,
    weather_restore = traps.weatherRestore,
    injury = traps.injury,
    camo_refresh = traps.camoRefresh,
}

local function pollCommands() commands.poll(handlers) end

-- Hooks (registered once, at load) ----------------------------------------------------------------------------------
stage.install()        -- item_spawn / item_end from the global BeginPlay hook
commands.init()        -- start after the previous session's commands; stores the shared read position
labels.install()       -- apnotify console command
codec.install()
counters.install()     -- CQC + Gako

-- Startup ---------------------------------------------------------------------------------------------------------
-- Commands are polled from BeginPlay hook callbacks (one per spawning actor). The loop is
-- kept as a best-effort extra: it did not run in every situation (no command ran for minutes with
-- only the loop). The shared read position (commands.offset) keeps each command to one run across Lua states.
local pollOk = pcall(function()
    LoopInGameThreadWithDelay(200, function()
        pcall(codec.tryHookRadioByPath); pcall(pollCommands); pcall(labels.runCleanups)
    end)
end)
log("command loop " .. (pollOk and "started" or "unavailable (polling from hooks only)"))
RegisterBeginPlayPostHook(function() pcall(pollCommands) end)
RegisterConsoleCommandHandler("apcmd", function() pcall(pollCommands); log("commands polled"); return true end)

events.emit({ type = "hello" })
log("loaded, writing events to " .. events.path)
