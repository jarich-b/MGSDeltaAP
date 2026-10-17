-- Shared helpers: mod folder path, logging, object validity, player lookups.
local M = {}

M.modDir = debug.getinfo(1, "S").source:match("^@(.*)[/\\]Scripts[/\\]")

function M.log(msg) print("[MGSDeltaAP] " .. msg .. "\n") end

function M.valid(o) return o ~= nil and o:IsValid() end

function M.playerController()
    local pc = FindFirstOf("PlayerController")
    if not M.valid(pc) then error("no PlayerController") end
    return pc
end

function M.playerPawn()
    local pc = FindFirstOf("PlayerController")
    if M.valid(pc) and M.valid(pc.Pawn) then return pc.Pawn end
    error("no player pawn")
end

return M
