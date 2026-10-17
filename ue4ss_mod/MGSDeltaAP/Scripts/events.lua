-- Event writer: appends one JSON object per line to events.jsonl (the external client tails it).
local util = require("modutil")
local flatjson = require("flatjson")

local M = {}

local eventPath = util.modDir .. "\\events.jsonl"
local seq = 0

M.path = eventPath

function M.emit(fields)
    seq = seq + 1
    fields.seq = seq
    fields.t = os.time()
    local parts = {}
    for k, v in pairs(fields) do parts[#parts + 1] = flatjson.encode(k) .. ":" .. flatjson.encode(v) end
    local f = io.open(eventPath, "a")
    if not f then util.log("cannot open " .. eventPath); return end
    f:write("{" .. table.concat(parts, ",") .. "}\n")
    f:close()
end

return M
