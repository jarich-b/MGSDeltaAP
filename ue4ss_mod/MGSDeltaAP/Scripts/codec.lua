-- Archipelago codec frequencies: codec_set / codec_menu messages, paging (press to continue), and the codec_call /
-- codec_closed events. Hooks the codec UI lazily (the Radio_C Blueprint only exists once the codec has been opened).
local util = require("modutil")
local events = require("events")

local log, valid, emit = util.log, util.valid, events.emit

local M = {}

-- Calling a frequency the client set up (143.40 status, 143.41+ warps) shows its messages instead of
-- "No response." and reports the call; closing the codec is reported too, so the client warps only afterwards.
-- The message/frequency events are implemented by the codec Blueprint (Radio_C), so hooks on the native
-- CRadioMenuState never fire for them; Radio_C only exists once the codec has been opened, so it is hooked lazily.
local codecTexts = {}     -- frequency (x100, e.g. 14340) -> messages shown (as one multi-line message)
local lastFreq = nil
local AP_FREQ = 14340     -- 143.40 status; 143.41+ checkpoint warps (unused by the game)
local MESSAGE_SEPARATOR = "\30"

-- Command handlers
function M.onMenu(c) codecTexts[AP_FREQ] = { c.text } end
function M.onSet(c)
    local freq = tonumber(c.freq)
    if not freq then return end
    if not c.text or c.text == "" then codecTexts[freq] = nil; return end
    local items = {}
    for item in (c.text .. MESSAGE_SEPARATOR):gmatch("(.-)" .. MESSAGE_SEPARATOR) do
        if item ~= "" then items[#items + 1] = item end
    end
    codecTexts[freq] = items
    log("codec_set " .. tostring(freq) .. " (" .. #items .. " messages)")
end

local injecting = false
local function onFreq(_, freq) lastFreq = freq:get() end
local PAGE_LINES = 4
local PAGE_BREAK = "\f"                 -- a message item that ends the page (form feed, from the client)
local PAGE_REPEAT_SECONDS = 60          -- calling the same frequency again within this time shows the next page
-- open: our multi-page talk is active (set in onMessage, cleared on OnTalkEnd / OnTalkInit); widget: the Radio_C
-- (or CRadioMenuState) object that received the injected message, used to show the next page from the press hook.
local paging = { freq = nil, pages = nil, index = 0, at = -1000, open = false, widget = nil }
-- PRESS_PAGING: pages advance on a confirm press (CallSaveSkipTalk(4) is cancelled and the next page shown); the hint
-- says so. Set to false if the press path doesn't work in game: the hint then says "Call again for more".
local PRESS_PAGING = true
local CODEC_TRACE = true                -- log every CallSaveSkipTalk value, OnTalkEnd / OnTalkInit and press-shown pages

local function pageText()
    local text = paging.pages[paging.index]
    if #paging.pages > 1 then
        -- the codec text shows no line breaks: keep the page counter visibly apart from the page's own numbers
        local hint = "[page " .. paging.index .. " of " .. #paging.pages
        if paging.index == #paging.pages then
            hint = hint .. "]"
        elseif PRESS_PAGING then
            hint = hint .. " - press to continue]"
        else
            hint = hint .. " - call again for more]"
        end
        text = text .. "      " .. hint
    end
    return text
end

-- Talk boundaries (Radio_C OnTalkEnd / OnTalkInit): our paged talk is over, a press is no longer ours to cancel.
local function onTalkBoundary(name)
    if CODEC_TRACE then log("codec " .. name .. " (paged talk was " .. (paging.open and "open" or "closed") .. ")") end
    paging.open = false
end

-- Press paging: a confirm press during our "No response." talk is CallSaveSkipTalk(4). Pre-hook only traces; the post hook
-- undoes the press (CallSaveSkipTalk(0) resets the legacy skip-talk state) and shows the next page. On the last page
-- the press goes through and the call ends normally.
local cancelling = false
local function skipValue(v)
    local ok, n = pcall(function() return tonumber(v:get()) end)
    return ok and n or nil
end

local function onSkipTalkPre(_, v)
    if CODEC_TRACE then
        log("CallSaveSkipTalk(" .. tostring(skipValue(v)) .. ") open=" .. tostring(paging.open)
            .. (cancelling and " [ours]" or ""))
    end
end

local function onSkipTalkPost(self, v)
    if cancelling or not paging.open or not paging.pages then return end
    if skipValue(v) ~= 4 then return end
    if paging.index >= #paging.pages then return end        -- last page: let the press through, the call ends
    cancelling = true
    local state = self:get()
    pcall(function() state:CallSaveSkipTalk(0) end)
    cancelling = false
    paging.index = paging.index + 1
    paging.at = os.clock()
    local widget = paging.widget
    if not (widget and pcall(valid, widget) and valid(widget)) then widget = state end
    injecting = true
    local ok, err = pcall(function() widget:OnShowMessage(pageText()) end)
    injecting = false
    if CODEC_TRACE then
        log("codec page " .. paging.index .. "/" .. #paging.pages .. " (press)" .. (ok and "" or (" FAILED: " .. tostring(err))))
    end
end

-- Pages end at a PAGE_BREAK item (inserted by the client) and hold at most PAGE_LINES lines each.
local function buildPages(items)
    local pages, lines = {}, {}
    local function flushPage()
        if #lines > 0 then pages[#pages + 1] = table.concat(lines, "   "); lines = {} end   -- no line breaks shown
    end
    for _, item in ipairs(items) do
        if item == PAGE_BREAK then
            flushPage()
        else
            if #lines >= PAGE_LINES then flushPage() end
            lines[#lines + 1] = item
        end
    end
    flushPage()
    if #pages == 0 then pages[1] = "" end
    return pages
end

local function onMessage(self, msg)
    if injecting then return end
    local text = msg:get():ToString()
    if not lastFreq then
        -- hooks installed after the codec opened on its last frequency: read the dial text ("143.40") instead
        pcall(function()
            local shown = self:get().Text_Freq:GetText():ToString()
            local whole, frac = shown:match("(%d+)%.(%d%d)")
            if whole then lastFreq = tonumber(whole) * 100 + tonumber(frac) end
        end)
    end
    if not lastFreq then return end
    local items = codecTexts[lastFreq]
    if not items or text ~= "No response." then return end
    -- Our answer is the engine's one-line "No response." call, so a plain press ends the call instead of asking for a next line
    -- (showing a page on the press only flashed it): onSkipTalkPost cancels that press while the
    -- talk is armed (paging.open), and calling the frequency again also advances (PAGE_REPEAT_SECONDS).
    local pages = buildPages(items)
    if paging.freq == lastFreq and os.clock() - paging.at < PAGE_REPEAT_SECONDS then
        paging.index = paging.index % #pages + 1
    else
        paging.index = 1
    end
    paging.freq, paging.pages, paging.at = lastFreq, pages, os.clock()
    paging.widget = self:get()
    paging.open = PRESS_PAGING and #pages > 1
    injecting = true
    pcall(function() self:get():OnShowMessage(pageText()) end)
    injecting = false
    log("codec " .. tostring(lastFreq) .. ": page " .. paging.index .. "/" .. #pages
        .. (paging.open and " (press paging armed)" or ""))
    emit({ type = "codec_call", freq = lastFreq })
end

local RADIO_CLASS = "/CobraUI/Blueprint/Radio/Radio.Radio_C"
local radioHooked = {}
local function hookRadioPath(path)
    if radioHooked[path] then return end
    radioHooked[path] = true
    local hooked = pcall(RegisterHook, path .. ":OnFreqUpdate", onFreq)
    hooked = pcall(RegisterHook, path .. ":OnShowMessage", onMessage) and hooked
    -- Blueprint path: the single callback runs after the event
    pcall(RegisterHook, path .. ":OnTalkEnd", function() onTalkBoundary("OnTalkEnd") end)
    pcall(RegisterHook, path .. ":OnTalkInit", function() onTalkBoundary("OnTalkInit") end)
    log("codec hooks on " .. path .. (hooked and "" or " failed"))
end

local function hookRadioClass(obj)
    local ok, path = pcall(function() return obj:GetClass():GetFullName():match("%S+$") end)
    if ok and path then hookRadioPath(path) end
end

-- The codec Blueprint is only loaded when the codec first opens, and the poll loop is paused while it is open, so
-- load the class ourselves once (game thread) so the very first call after starting the game is answered.
-- At the title screen the class can't be loaded yet, so keep retrying every 2 s until it is.
local radioLoadAt, radioLoadFailures = 0, 0
function M.tryHookRadioByPath()
    if radioHooked[RADIO_CLASS] then return end
    local cls = StaticFindObject(RADIO_CLASS)
    if not valid(cls) and os.clock() >= radioLoadAt then
        radioLoadAt = os.clock() + 2
        for _, path in ipairs({ RADIO_CLASS, "/CobraUI/Blueprint/Radio/Radio.Radio", "/CobraUI/Blueprint/Radio/Radio" }) do
            pcall(function() LoadAsset(path) end)
        end
        cls = StaticFindObject(RADIO_CLASS)
        if not valid(cls) then
            radioLoadFailures = radioLoadFailures + 1
            if radioLoadFailures == 1 then log("codec Blueprint not loadable yet; retrying every 2 s") end
        end
    end
    if valid(cls) then
        log("codec Blueprint found after " .. radioLoadFailures .. " failed load attempt(s)")
        hookRadioPath(RADIO_CLASS)
    end
end

function M.install()
    local function hook(path, fn) pcall(RegisterHook, path, fn) end
    hook("/Script/CobraUI.CRadioMenuState:OnFreqUpdate", onFreq)
    hook("/Script/CobraUI.CRadioMenuState:OnShowMessage", onMessage)
    local lastClose = 0
    local function closed(self)
        pcall(function() hookRadioClass(self:get()) end)    -- fallback: hook Radio_C on the first close
        if os.time() - lastClose < 2 then return end
        lastClose = os.time()
        emit({ type = "codec_closed" })
    end
    hook("/Script/CobraUI.CRadioMenuState:OnCloseRadioWindow", closed)
    hook("/Script/CobraUI.CRadioMenuState:OnCloseAnimEnd", closed)
    hook("/Script/CobraUI.CRadioMenuState:Exit", closed)
    -- Press paging (native path: callback #1 = pre-hook, #2 = post-hook)
    pcall(RegisterHook, "/Script/CobraUI.CRadioMenuState:CallSaveSkipTalk", onSkipTalkPre, onSkipTalkPost)
    pcall(NotifyOnNewObject, "/Script/CobraUI.CRadioMenuState", function(obj) pcall(hookRadioClass, obj) end)
    pcall(M.tryHookRadioByPath)   -- also retried from the poll loop until the Blueprint class is loaded
    -- Native functions the codec Blueprint calls while the codec is open (native hooks fire from the start, unlike
    -- the Blueprint events): the first time one runs, hook the Blueprint class, before the player can dial and send.
    for _, fn in ipairs({ "GetMGS3GamepadInputState", "IsFastForwardEnabledState", "GetIsFieldExpansion",
                          "GetMGS3CouldSave", "OnShowRadioWindow", "OnTalkInit" }) do
        hook("/Script/CobraUI.CRadioMenuState:" .. fn, function(self)
            if next(radioHooked) then return end
            pcall(function() hookRadioClass(self:get()) end)
            log("codec Blueprint hooked from native " .. fn)
        end)
    end
    pcall(function()
        local radio = FindFirstOf("Radio_C")                 -- already created (mod reloaded in game)
        if valid(radio) then hookRadioClass(radio) end
    end)
end

return M
