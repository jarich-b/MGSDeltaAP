-- Minimal JSON helpers: encode one value for events.jsonl, and parse one flat object (string / number / boolean values)
-- from commands.jsonl.
local M = {}

function M.encode(v)
    local t = type(v)
    if t == "string" then
        return '"' .. v:gsub('[%c"\\]', function(c) return string.format("\\u%04x", c:byte()) end) .. '"'
    elseif t == "number" then
        return (v ~= v or v == math.huge or v == -math.huge) and "null" or string.format("%.14g", v)
    elseif t == "boolean" then
        return tostring(v)
    end
    return "null"
end

local ESCAPES = { n = "\n", t = "\t", r = "\r", f = "\f", b = "\b", ['"'] = '"', ["\\"] = "\\", ["/"] = "/" }

local function readString(s, i)          -- s:sub(i, i) == '"'; returns value, index after closing quote
    local out, j = {}, i + 1
    while j <= #s do
        local c = s:sub(j, j)
        if c == '"' then return table.concat(out), j + 1 end
        if c == "\\" then
            local e = s:sub(j + 1, j + 1)
            if e == "u" then
                out[#out + 1] = utf8.char(tonumber(s:sub(j + 2, j + 5), 16)); j = j + 6
            else
                out[#out + 1] = ESCAPES[e] or e; j = j + 2
            end
        else
            out[#out + 1] = c; j = j + 1
        end
    end
    return table.concat(out), j
end

function M.parseFlat(s)
    local obj, i = {}, (s:find("{", 1, true) or 0) + 1
    while true do
        i = s:find('"', i, true)
        if not i then return obj end
        local key; key, i = readString(s, i)
        i = s:find(":", i, true) + 1
        i = s:find("%S", i)
        local c = s:sub(i, i)
        if c == '"' then
            obj[key], i = readString(s, i)
        else
            local raw = s:match("^[^,}]+", i)
            i = i + #raw
            raw = raw:match("^%s*(.-)%s*$")
            obj[key] = (raw == "true") or (raw ~= "false" and tonumber(raw)) or nil
        end
    end
end

return M
