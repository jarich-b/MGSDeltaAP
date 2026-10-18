-- Trap and benefit command handlers: disarm, weather (with restore), injury, camo refresh.
local util = require("modutil")
local stage = require("stage")

local log, valid, playerPawn = util.log, util.valid, util.playerPawn

local M = {}

-- Weather (with restore) and Injury are verified in game , disarm and
-- camo_refresh are untested; each handler is pcall-wrapped by commands.poll, so a failure is only logged
-- ("command <name> failed: ...").

-- Disarm: put the weapon away through the HUD's own call (AGsrPlayer::ChangeWeaponSlot); the client writes the legacy
-- weapon field a second later if the slot is still filled.
function M.disarm()
    local player = FindFirstOf("GsrPlayer")
    if not valid(player) then player = playerPawn() end
    player:ChangeWeaponSlot(0)
    log("disarm: ChangeWeaponSlot(0)")
end

-- Weather trap: BP_Weather_System_C:SwitchWeather(E_WeatherType, true) starts the visible change (the legacy
-- SetWeatherType only publishes the weather). Index: 0 default, 1 light rain, 2 heavy rain, 3 fine, 4 cloud, 5 fog,
-- 6 snow, 7 wind. The weather a trap replaces is remembered here (per Lua state: the commands may run in different
-- hook states, then weather_restore falls back to the "old" index the client sends) and put back by weather_restore,
-- which the client sends after the trap's duration; the stage load resets it anyway.
local savedWeather, savedWeatherStage

local function weatherSystem()
    local w = FindFirstOf("BP_Weather_System_C")
    if not valid(w) then error("no BP_Weather_System_C in this level") end
    return w
end

local function switchWeather(w, n)
    local ok, err = pcall(function() w:SwitchWeather(n, true) end)
    if not ok then
        log("weather: SwitchWeather failed (" .. tostring(err) .. "), trying Refresh Weather")
        pcall(function() w.WeatherType = n end)
        w["Refresh Weather"](w)
    end
    pcall(function() w:SetWeatherType(n) end)        -- publish to the legacy side (GetWeather, OnChangedWeather)
    local cur, updating
    pcall(function() cur = w.CurrentWeatherType; updating = w.IsUpdatingWeather end)
    log("weather: switched to " .. n .. " (CurrentWeatherType " .. tostring(cur) .. ", updating " .. tostring(updating) .. ")")
end

function M.weather(c)
    local n = math.floor(tonumber(c.type) or -1)
    if n < 0 or n > 7 then error("bad weather type " .. tostring(c.type)) end
    local w = weatherSystem()
    if savedWeather ~= nil and savedWeatherStage ~= stage.currentStage() then savedWeather = nil end   -- left over from another stage
    if savedWeather == nil then
        savedWeather = w.CurrentWeatherType
        savedWeatherStage = stage.currentStage()
        log("weather: replacing " .. tostring(savedWeather))
    end
    switchWeather(w, n)
end

function M.weatherRestore(c)
    local target, savedStage = savedWeather, savedWeatherStage
    savedWeather, savedWeatherStage = nil, nil
    if target == nil then
        local old = tonumber(c.old)
        if old == nil or old < 0 then log("weather_restore: nothing to restore"); return end
        target = old
    elseif savedStage ~= stage.currentStage() then
        log("weather_restore: the stage changed, the game has reset the weather")
        return
    end
    switchWeather(weatherSystem(), target)
end

-- Injury trap: the debug menu's add-wound callback reads the wound type from the Value of its enum object
-- m_playerWoundTypes (EGsrPlayerWoundType: 1 burn, 2 cut, 3 bullet, 4 shotgun, 6 bee, 7 leech, 8 arrow, 9 needle); the
-- shipping exe has real code for it. Like APProbe's debug handlers, build the enum object ourselves. The menu object
-- is an existing instance, else the class default object, else a new one; the first that works is used.
local WOUND_ENUM = "/Script/IcsDebugMenu.IcsDebugMenuEnum"
local WOUND_MENU = "/Script/Gsr.GsrPairingDebugMenuState"
local VALID_WOUNDS = { [1] = true, [2] = true, [3] = true, [4] = true, [6] = true, [7] = true, [8] = true, [9] = true }

local function woundMenus()
    return {
        function() return FindFirstOf("GsrPairingDebugMenuState") end,
        function() return StaticFindObject("/Script/Gsr.Default__GsrPairingDebugMenuState") end,
        function()
            local cls = StaticFindObject(WOUND_MENU)
            local outer = FindFirstOf("GsrPlayer")
            if not valid(cls) or not valid(outer) then error("class or GsrPlayer missing") end
            return StaticConstructObject(cls, outer)
        end,
    }
end

function M.injury(c)
    local wound = math.floor(tonumber(c.wound) or 2)
    if not VALID_WOUNDS[wound] then error("bad wound type " .. tostring(c.wound)) end
    local enumCls = StaticFindObject(WOUND_ENUM)
    if not valid(enumCls) then error("class not found: " .. WOUND_ENUM) end
    for i, getMenu in ipairs(woundMenus()) do
        local ok, err = pcall(function()
            local menu = getMenu()
            if not valid(menu) then error("no menu object") end
            local e = StaticConstructObject(enumCls, menu)
            if not valid(e) then error("could not construct IcsDebugMenuEnum") end
            e.Value = wound
            menu.m_playerWoundTypes = e
            menu:CallbackAddPlayerWound(e, FText(""), true)
        end)
        if ok then log("injury: wound type " .. wound .. " added (menu source " .. i .. ")"); return end
        log("injury: menu source " .. i .. " failed: " .. tostring(err))
    end
    error("injury: no menu source worked")
end

-- Camo refresh: the client writes the worn camo / face paint (GM+0x7AE / 0x7AF) and sends their types. The camouflage
-- manager keeps its own copy (CurrentInfo) and RefreshCamouflageBP alone redrew the old camo (seen in game), so the types
-- are set first: UpdateCamouflageByNoPairing(facepaint, camo), then RefreshCamouflageBP(bReinitPose, bForcereplace,
-- bHideMaskCap) rebuilds Snake's model / materials.
function M.camoRefresh(c)
    local camo, facepaint = tonumber(c and c.camo), tonumber(c and c.facepaint)
    local done = 0
    local list = FindAllOf("UE4PairingCamouflageManager")
    if list then
        for _, mgr in ipairs(list) do
            if valid(mgr) then
                local ok, err = pcall(function()
                    if camo and facepaint then
                        mgr:UpdateCamouflageByNoPairing(math.floor(facepaint), math.floor(camo))
                    end
                    mgr:RefreshCamouflageBP(true, true, false)
                end)
                if ok then done = done + 1 else log("camo_refresh failed: " .. tostring(err)) end
            end
        end
    end
    log("camo_refresh: " .. done .. " manager(s) refreshed (camo " .. tostring(camo) .. ", face paint "
        .. tostring(facepaint) .. ")")
end

return M
