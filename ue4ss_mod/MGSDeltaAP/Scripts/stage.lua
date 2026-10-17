-- Stage changes and world-item (pickup) events, plus the exec_proc / warp stage commands.
local util = require("modutil")
local events = require("events")

local log, valid, emit = util.log, util.valid, events.emit

local M = {}

local IBOX_CLASS = "/Game/Art/Environment/HD_GameplayAssets/Blueprint/BP_ibox_PropBase.BP_ibox_PropBase_C"
local LEVEL_MANAGER_CDO = "/Script/MGS3.Default__UE4PairingLevelManager"

local function levelManager()
    local ok, mgr = pcall(function() return StaticFindObject(LEVEL_MANAGER_CDO):Get() end)
    return ok and mgr or nil
end

function M.currentStage()
    local mgr = levelManager()
    if not mgr then return nil end
    local ok, stage = pcall(function() return mgr:GetMGS3StageName():ToString() end)
    return ok and stage or nil
end

local function isLoadingStage()
    local mgr = levelManager()
    if not mgr then return nil end
    local ok, loading = pcall(function() return mgr:IsLoadingStage() end)
    return ok and loading or nil
end

-- Items placed in a stage spawn in a burst while the stage loads; enemy drops spawn later during play.
-- An item spawning within PLACED_WINDOW seconds of the stage change counts as placed.
local PLACED_WINDOW = 2
local lastStage
local stageChangedAt = 0
local spawnKind = {}  -- actor full name -> "placed" | "dropped"

local function checkStage()
    local stage = M.currentStage()
    if stage and stage ~= "" and stage ~= lastStage then
        lastStage = stage
        stageChangedAt = os.time()
        spawnKind = {}
        emit({ type = "stage", stage = stage })
    end
    return stage
end

local function actorName(actor)
    local ok, name = pcall(function() return actor:GetFullName() end)
    return ok and name or nil
end

local function className(actor)
    local ok, name = pcall(function() return actor:GetClass():GetFName():ToString() end)
    return ok and name or "?"
end

local function onItemEnd(actor, reason)
    local stage = checkStage()
    local ok, loc = pcall(function() return actor:K2_GetActorLocation() end)
    local name = actorName(actor)
    emit({
        type = "item_end",
        class = className(actor),
        stage = stage,
        origin = name and spawnKind[name] or "unknown",
        x = ok and loc.X or nil, y = ok and loc.Y or nil, z = ok and loc.Z or nil,
        loading = isLoadingStage(),
        reason = reason,
    })
end

local itemHooksInstalled = false
local function installItemHooks()
    local ok, err = pcall(function()
        RegisterHook(IBOX_CLASS .. ":ReceiveEndPlay", function(self, reason)
            onItemEnd(self:get(), reason:get())
        end)
    end)
    itemHooksInstalled = ok
    log(ok and "item hooks installed" or ("item hook failed: " .. tostring(err)))
end

-- The ibox Blueprint class is only loaded once a stage with items is loaded, so install lazily
-- from the global BeginPlay hook the first time an ibox actor appears.
function M.install()
    RegisterBeginPlayPostHook(function(context)
        local actor = context:get()
        local name = className(actor)
        if not name:find("^ibox_") then return end
        if not itemHooksInstalled then installItemHooks() end
        local stage = checkStage()
        local origin = (os.time() - stageChangedAt) <= PLACED_WINDOW and "placed" or "dropped"
        local full = actorName(actor)
        if full then spawnKind[full] = origin end
        emit({ type = "item_spawn", class = name, stage = stage, origin = origin })
    end)
end

-- Commands -------------------------------------------------------------------------------------------------------------

local function utilLibrary()
    return StaticFindObject("/Script/MGS3.Default__Mgs3UtilLibrary")
end

function M.execProc(c) utilLibrary():ExecMgs3Proc(c.proc); log("exec_proc " .. tostring(c.proc)) end
function M.warp(c) utilLibrary():Mgs3LoadCmd(c.stage, "r_sna01"); log("warp " .. tostring(c.stage)) end

return M
