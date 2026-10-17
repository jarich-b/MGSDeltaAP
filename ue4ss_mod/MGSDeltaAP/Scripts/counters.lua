-- Counter events for the client: CQC engagements and Gako duck breaks (the client does the counting).
local util = require("modutil")
local events = require("events")

local log, emit = util.log, events.emit

local M = {}

-- CQC takedowns: no memory counter exists, and the Blueprint CQC classes (BP_PlayerCqcAction_C,
-- BP_EneWorCqcAction_C) can't be hooked. The native player/enemy
-- CQC handshake signal fires from the start, every frame while Snake holds someone (~60/s). Its action name is always
-- "CqcAction" and its data (UGsrHandshakeCqcMasterData) can't be read from Lua (GetData gives no callable object),
-- so count engagements: a new burst after CQC_GAP seconds of silence = one CQC. The client counts the takedowns.
local CQC_TRACE = true        -- one log line per engagement
local CQC_GAP = 1.5
local lastCqcSignal = -1000

local function installCqc()
    pcall(RegisterHook, "/Script/Gsr.GsrPlayerCqcAction:OnProcessCommunication", function()
        local now = os.clock()
        local fresh = now - lastCqcSignal > CQC_GAP
        lastCqcSignal = now
        if not fresh then return end
        if CQC_TRACE then log("CQC engagement") end
        emit({ type = "cqc", side = "grab", proc = 1 })
    end)
end

-- Gako ducks: UGakoComponent:GakoSetCollected runs on every duck break (also for ducks the profile already has); the
-- component's GakoIndex (int32, +0xB0) identifies the duck. The client counts distinct indices per run. Native hook: no
-- Blueprint class has to be loaded first, but a failed registration is retried when the first GakoComponent is created.
local GAKO_FUNCTION = "/Script/MGS3.GakoComponent:GakoSetCollected"
local gakoHooked = false
local function onGakoCollected(self)
    local index
    pcall(function() index = self:get().GakoIndex end)
    if index == nil then pcall(function() index = self:get():GetPropertyValue("GakoIndex") end) end
    log("Gako collected: index " .. tostring(index))
    emit({ type = "gako", index = tonumber(index) or -1 })
end
local function hookGako()
    if gakoHooked then return end
    local ok, err = pcall(RegisterHook, GAKO_FUNCTION, onGakoCollected)
    gakoHooked = ok
    log(ok and "Gako hook installed" or ("Gako hook failed: " .. tostring(err)))
end

function M.install()
    installCqc()
    hookGako()
    if not gakoHooked then pcall(NotifyOnNewObject, "/Script/MGS3.GakoComponent", function() hookGako() end) end
end

return M
