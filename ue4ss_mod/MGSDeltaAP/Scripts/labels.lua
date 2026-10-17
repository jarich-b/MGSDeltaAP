-- On-screen text for received items, traps and locked areas: the itembox label, the subtitle caption, their cleanup
-- queue, the popup / notify_clear command handlers and the `apnotify` console command.
local util = require("modutil")

local log, valid, playerController = util.log, util.valid, util.playerController

local M = {}

-- NOTIFY_METHOD: "log" (console only) | "caption" | "itembox". Test with the console command
-- `apnotify <method> <text>`. Every method is pcall-wrapped and falls back to log.
local NOTIFY_METHOD = "itembox"   -- a small floating label like the game's pickup text
local NOTIFY_SECONDS = 4
local METHOD_ALIASES = { pickup = "itembox", subtitle = "caption" }
local SLATE_VISIBILITY = { Visible = 0, Collapsed = 1, Hidden = 2, HitTestInvisible = 3, SelfHitTestInvisible = 4 }

-- Local cleanup queue for the notifiers below, run from the poll loop (and force-run before the next notification).
-- It does not run when the notification was shown from a hook or console context (see clearNotifications, which is
-- what the client relies on).
local pendingCleanups = {}   -- { at = os.clock() deadline, fn = cleanup }
local function scheduleCleanup(fn)
    pendingCleanups[#pendingCleanups + 1] = { at = os.clock() + NOTIFY_SECONDS, fn = fn }
end
local function runCleanups(force)
    local now = os.clock()
    for i = #pendingCleanups, 1, -1 do
        local entry = pendingCleanups[i]
        if force or now >= entry.at then
            table.remove(pendingCleanups, i)
            local ok, err = pcall(entry.fn)
            if not ok then log("notify cleanup failed: " .. tostring(err)) end
        end
    end
end
M.runCleanups = runCleanups

local notifiers = {}

-- The HUD subtitle widget (USubtitleWidget_C -> Paragraph -> RichMessage) is a non-modal text strip at the
-- bottom of the screen that normally shows radio/cutscene lines. Overwrite its text; the game rewrites it on the next line.
function notifiers.caption(text)
    runCleanups(true)   -- an older pending cleanup would otherwise blank this text early
    local sub = FindFirstOf("SubtitleWidget_C")
    if not valid(sub) then sub = FindFirstOf("CSubtitleWidget") end
    if not valid(sub) then error("no SubtitleWidget_C instance") end
    local para = sub.Paragraph
    if not valid(para) then error("subtitle has no Paragraph") end
    local rich = para.RichMessage
    if not valid(rich) then error("paragraph has no RichMessage") end
    pcall(function() para.SpeakerName:SetVisibility(SLATE_VISIBILITY.Collapsed) end)
    pcall(function() para.BackgroundBorder:SetVisibility(SLATE_VISIBILITY.HitTestInvisible) end)
    rich:SetText(FText(text))
    local wasInViewport = false
    pcall(function() wasInViewport = sub:IsInViewport() end)
    if not wasInViewport then pcall(function() sub:AddToViewport(60) end) end
    pcall(function() sub:SetVisibility(SLATE_VISIBILITY.HitTestInvisible) end)
    pcall(function() para:SetVisibility(SLATE_VISIBILITY.HitTestInvisible) end)
    pcall(function() rich:SetVisibility(SLATE_VISIBILITY.HitTestInvisible) end)
    log("caption: text set, subtitle was in viewport: " .. tostring(wasInViewport))
    scheduleCleanup(function()
        if valid(rich) then rich:SetText(FText("")) end
        if not wasInViewport and valid(sub) then sub:RemoveFromParent() end
    end)
end

-- The world-anchored item label ("LF MED") is UItemBoxBP_C:InitItemBox(id, name). Showing it at an ad-hoc id
-- above the player is the closest to the pickup look, but the game may reposition/release it on its own.
local itemBoxSeq = 0
local LABEL_DISTANCE = 300     -- uu in front of the camera
local LABEL_RISE = 40          -- uu above the view centre

-- Where the label goes: a point just in front of the camera (always on screen, whatever the camera does); the old
-- spot above Snake's head left the frame at some camera angles. Falls back to above the pawn.
local function labelPosition()
    local ok, pos = pcall(function()
        local camera = playerController().PlayerCameraManager
        local loc, rot = camera:GetCameraLocation(), camera:GetCameraRotation()
        local pitch, yaw = math.rad(rot.Pitch), math.rad(rot.Yaw)
        return { X = loc.X + math.cos(pitch) * math.cos(yaw) * LABEL_DISTANCE,
                 Y = loc.Y + math.cos(pitch) * math.sin(yaw) * LABEL_DISTANCE,
                 Z = loc.Z + math.sin(pitch) * LABEL_DISTANCE + LABEL_RISE }
    end)
    if ok and pos then return pos, "camera" end
    local pawn = playerController().Pawn
    if not valid(pawn) then error("no pawn") end
    local loc = pawn:K2_GetActorLocation()
    return { X = loc.X, Y = loc.Y, Z = loc.Z + 120 }, "player"
end

-- Label colours (the game's names): the widget reuses ten text slots that keep the colour the game last gave them
-- ("NVG FULL" red, "LF MED" white), so every label sets its own.
local LABEL_COLOURS = { normal = "hudtext_normal", bad = "hudtext_negative" }

function notifiers.itembox(text, colour)
    runCleanups(true)   -- one label at a time: drop the previous one now
    local box = FindFirstOf("ItemBoxBP_C")
    if not valid(box) then error("no ItemBoxBP_C instance") end
    local pos, anchor = labelPosition()
    itemBoxSeq = itemBoxSeq + 1
    local id = 900000 + itemBoxSeq
    box:InitItemBox(id, text)
    pcall(function() box:SetColorName(id, FName(LABEL_COLOURS[colour] or LABEL_COLOURS.normal)) end)
    pcall(function() box:SetDisplay(id, true) end)
    pcall(function() box:SetOpacity(id, 1.0) end)
    box:SetScreenPosition(id, pos)
    log("itembox: label " .. id .. " created in front of the " .. anchor)
    scheduleCleanup(function()
        if not valid(box) then return end
        -- ReleaseItemBox alone left the label on screen: hide it every way the widget offers
        pcall(function() box:SetDisplay(id, false) end)
        pcall(function() box:SetOpacity(id, 0.0) end)
        pcall(function() box:InitItemBox(id, "") end)
        pcall(function() box:ReleaseItemBox(id) end)
        log("itembox: label " .. id .. " removed")
    end)
end

function notifiers.log(text) log("NOTIFY " .. text) end

local function notify(text, method, colour)
    method = method or NOTIFY_METHOD
    method = METHOD_ALIASES[method] or method
    local fn = notifiers[method]
    if not fn then log("notify: unknown method '" .. tostring(method) .. "'"); fn = notifiers.log; method = "log" end
    local ok, err = pcall(fn, text, colour)
    if ok then return true end
    log("notify method '" .. method .. "' failed: " .. tostring(err) .. " (falling back to log)")
    notifiers.log(text)
    return false
end

-- Stateless clear, sent by the client a few seconds after each message: the mod's own cleanup queue lives in
-- whichever Lua context showed the message (poll loop, BeginPlay hook, console) and was never run, so clear by finding
-- the widgets instead.
local ITEMBOX_IDS = 200     -- how many label ids after 900000 are swept (labels are numbered 900001, 900002, ...)
local function clearNotifications()
    local cleared = 0
    pcall(function()
        local sub = FindFirstOf("SubtitleWidget_C")
        if valid(sub) and valid(sub.Paragraph) and valid(sub.Paragraph.RichMessage) then
            sub.Paragraph.RichMessage:SetText(FText(""))
            cleared = cleared + 1
        end
    end)
    pcall(function()
        local box = FindFirstOf("ItemBoxBP_C")
        if not valid(box) then return end
        for id = 900001, 900000 + ITEMBOX_IDS do
            pcall(function() box:SetDisplay(id, false) end)
            pcall(function() box:SetOpacity(id, 0.0) end)
            pcall(function() box:ReleaseItemBox(id) end)
        end
        cleared = cleared + 1
    end)
    log("notifications cleared (" .. cleared .. " widget(s))")
end

local function showPopup(title, text, style)
    local line = (text or "") ~= "" and text or title     -- the text already names the item ("Received X from Y")
    log("POPUP " .. line)
    if style == "warning" then
        -- locks: the subtitle strip reads as a clear warning, but it is often not on screen right after a load (the
        -- Cave exit bounce showed nothing), so the floating label shows it too
        notify(title .. ": " .. line, "caption")
        if NOTIFY_METHOD ~= "log" then notify(title .. ": " .. line, nil, "bad") end
    elseif NOTIFY_METHOD ~= "log" then
        notify(line, nil, style == "bad" and "bad" or "normal")
    end
end

-- Command handlers
function M.onPopup(c) showPopup(c.title or "", c.text or "", c.style) end
function M.onNotifyClear() clearNotifications() end

-- apnotify <method> <text...>   e.g. `apnotify caption Received M1911A1 from Alice`; `apnotify probe` lists what exists.
function M.install()
    RegisterConsoleCommandHandler("apnotify", function(_, params)
        local method = params and params[1]
        if not method or method == "" then log("usage: apnotify <caption|itembox|log|probe|clear> <text...>"); return true end
        if method == "clear" then runCleanups(true); clearNotifications(); return true end
        if method == "probe" then
            for _, name in ipairs({ "SubtitleWidget_C", "CSubtitleWidget", "ItemBoxBP_C", "HUD_C", "PlayerController" }) do
                local ok, list = pcall(FindAllOf, name)
                log("probe " .. name .. ": " .. ((ok and list) and #list or 0) .. " instance(s)")
            end
            return true
        end
        local words = {}
        for i = 2, #params do words[#words + 1] = params[i] end
        local text = #words > 0 and table.concat(words, " ") or "Received test item from AP"
        log("apnotify " .. method .. ": " .. text)
        local ok = notify(text, method)
        log("apnotify " .. method .. (ok and " ok" or " FAILED (see above)"))
        return true
    end)
end

return M
