-- Reads commands.jsonl (client -> mod) and runs each command once through the handler table that main.lua passes in.
-- The read position is shared between Lua states through commands.offset, never through a variable.
-- Commands (one flat JSON object per line):
-- {"cmd":"exec_proc","proc":126} | {"cmd":"warp","stage":"s005a"} | {"cmd":"popup","title":..,"text":..,"style":..}
-- {"cmd":"notify_clear"} | {"cmd":"codec_menu","text":..} | {"cmd":"codec_set","freq":14001,"text":"msg1\u001emsg2"}
-- (empty text: unset) | {"cmd":"disarm"} | {"cmd":"weather","type":n} (n = 0..7) | {"cmd":"weather_restore","old":n}
-- | {"cmd":"injury","wound":n} | {"cmd":"camo_refresh"}
local util = require("modutil")
local flatjson = require("flatjson")

local log = util.log

local M = {}

local commandPath = util.modDir .. "\\commands.jsonl"
local commandOffset = 0

-- Commands that start a stage load. They are dropped when they arrive late (polling stops while the game is paused or
-- in a menu, and area bounces are only valid right after the load that caused them), and they wait while the codec is
-- on screen: commands are also polled from hook callbacks, which fire with the codec open, and a load under the open
-- codec broke the game.
local LOAD_COMMANDS = { exec_proc = true, warp = true }
local MAX_COMMAND_AGE = 15

local MIN_NOTIFY_SECONDS = 3
local lastNotifyShown = -1000

-- Commands are polled from the BeginPlay hook (and the loop, when it runs), in separate Lua states. The read position is
-- shared through a small file so each command runs once: with a per-state offset every command ran twice, and a
-- state with an old offset replayed the whole file in one frame (crashed the game).
local offsetPath = util.modDir .. "\\commands.offset"

-- Returns the stored offset, or nil if it cannot be read right now. A state that fell back to its own old offset here
-- replayed earlier sessions' commands (labels, heals), so the caller skips that poll instead.
local function sharedOffset()
    local f = io.open(offsetPath, "rb")
    if not f then return nil end
    local n = tonumber(f:read("a"))
    f:close()
    return n
end

-- Overwritten in place with a fixed width: "wb" emptied the file for a moment, and a poll reading it then got nothing.
local function storeOffset(n)
    local f = io.open(offsetPath, "r+b") or io.open(offsetPath, "wb")
    if f then f:write(string.format("%20d", n)); f:close() end
end

-- Mod load: skip commands left over from a previous session (start at the end of commands.jsonl).
function M.init()
    local f = io.open(commandPath, "rb")
    if f then commandOffset = f:seek("end"); f:close() end
    storeOffset(commandOffset)
end

local function codecOnScreen()
    local ok, open = pcall(function()
        local r = FindFirstOf("Radio_C")
        return r ~= nil and r:IsValid() and r:IsInViewport()
    end)
    return ok and open
end

-- handlers: command name -> function(cmd table)
function M.poll(handlers)
    local f = io.open(commandPath, "rb")
    if not f then return end
    local size = f:seek("end")
    local shared = sharedOffset()
    if not shared then f:close(); return end      -- retried on the next poll
    commandOffset = shared
    if size < commandOffset then commandOffset = 0 end
    if size == commandOffset then f:close(); return end
    f:seek("set", commandOffset)
    local data = f:read("a")
    f:close()
    local consumed = 0
    for line in data:gmatch("([^\n]*)\n") do
        local parsed, cmd = pcall(flatjson.parseFlat, line)
        if not parsed then    -- a malformed line is skipped, never retried (it would block every later command)
            log("skipped unreadable command line: " .. tostring(cmd))
            cmd = {}
        end
        -- A clear that arrives together with its message (the game was paused or unfocused) waits until the message
        -- has been visible for MIN_NOTIFY_SECONDS: stop here and retry on a later poll.
        if cmd.cmd == "notify_clear" and os.clock() - lastNotifyShown < MIN_NOTIFY_SECONDS then break end
        if LOAD_COMMANDS[cmd.cmd] and codecOnScreen() then break end   -- retried once the codec is closed
        consumed = consumed + #line + 1
        storeOffset(commandOffset + consumed)      -- before running it: a handler error never repeats a command
        if cmd.cmd == "popup" then lastNotifyShown = os.clock() end
        local handler = handlers[cmd.cmd]
        if handler and LOAD_COMMANDS[cmd.cmd] and cmd.t and os.time() - cmd.t > (tonumber(cmd.ttl) or MAX_COMMAND_AGE) then
            log("dropped stale " .. cmd.cmd .. " (" .. (os.time() - cmd.t) .. " s old)")
        elseif handler then
            local ok, err = pcall(handler, cmd)
            if not ok then log("command " .. tostring(cmd.cmd) .. " failed: " .. tostring(err)) end
        end
    end
    commandOffset = commandOffset + consumed
end

return M
