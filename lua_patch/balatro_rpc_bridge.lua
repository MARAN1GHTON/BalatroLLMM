--[[
    Balatro Lua JSON-RPC 2.0 Bridge
    Target Engine: LÖVE2D (Balatro PC & Android)
    Listens on TCP 0.0.0.0:12346 (non-blocking) during love.update()
]]

local socket = require("socket")

-- Self-contained pure Lua JSON encoder and parser (Zero external dependencies, 100% compatible with Balatro 1.9+)
local json = {}

function json.encode(val)
    local t = type(val)
    if t == "nil" then
        return "null"
    elseif t == "boolean" then
        return val and "true" or "false"
    elseif t == "number" then
        if val ~= val then return "null" end -- NaN
        if val >= math.huge then return "1e999" end
        if val <= -math.huge then return "-1e999" end
        return tostring(val)
    elseif t == "string" then
        return string.format("%q", val):gsub("\\\n", "\\n")
    elseif t == "table" then
        local is_array = false
        local n = #val
        if n > 0 or next(val) == nil then
            is_array = true
            for k, _ in pairs(val) do
                if type(k) ~= "number" or k < 1 or k > n or math.floor(k) ~= k then
                    is_array = false
                    break
                end
            end
        end

        if is_array then
            local parts = {}
            for i = 1, n do
                parts[i] = json.encode(val[i])
            end
            return "[" .. table.concat(parts, ",") .. "]"
        else
            local parts = {}
            for k, v in pairs(val) do
                table.insert(parts, string.format("%q:%s", tostring(k), json.encode(v)))
            end
            return "{" .. table.concat(parts, ",") .. "}"
        end
    end
    return "null"
end

-- Minimalist JSON parser for JSON-RPC requests
function json.decode(str)
    local pos = 1
    local len = #str

    local function skip_whitespace()
        while pos <= len and str:sub(pos, pos):match("%s") do
            pos = pos + 1
        end
    end

    local parse_value

    local function parse_string()
        pos = pos + 1 -- skip opening quote
        local start = pos
        while pos <= len do
            local c = str:sub(pos, pos)
            if c == '"' then
                local res = str:sub(start, pos - 1)
                pos = pos + 1
                return res
            elseif c == '\\' then
                pos = pos + 2
            else
                pos = pos + 1
            end
        end
        return str:sub(start)
    end

    local function parse_number()
        local start = pos
        if str:sub(pos, pos) == '-' then pos = pos + 1 end
        while pos <= len and str:sub(pos, pos):match("[%d%.eE%+%-%_]") do
            pos = pos + 1
        end
        return tonumber(str:sub(start, pos - 1)) or 0
    end

    local function parse_array()
        pos = pos + 1 -- skip '['
        local arr = {}
        skip_whitespace()
        if str:sub(pos, pos) == ']' then
            pos = pos + 1
            return arr
        end
        while pos <= len do
            skip_whitespace()
            table.insert(arr, parse_value())
            skip_whitespace()
            local c = str:sub(pos, pos)
            if c == ']' then
                pos = pos + 1
                return arr
            elseif c == ',' then
                pos = pos + 1
            end
        end
        return arr
    end

    local function parse_object()
        pos = pos + 1 -- skip '{'
        local obj = {}
        skip_whitespace()
        if str:sub(pos, pos) == '}' then
            pos = pos + 1
            return obj
        end
        while pos <= len do
            skip_whitespace()
            if str:sub(pos, pos) == '"' then
                local key = parse_string()
                skip_whitespace()
                if str:sub(pos, pos) == ':' then
                    pos = pos + 1
                end
                skip_whitespace()
                obj[key] = parse_value()
            end
            skip_whitespace()
            local c = str:sub(pos, pos)
            if c == '}' then
                pos = pos + 1
                return obj
            elseif c == ',' then
                pos = pos + 1
            end
        end
        return obj
    end

    parse_value = function()
        skip_whitespace()
        if pos > len then return nil end
        local c = str:sub(pos, pos)
        if c == '"' then
            return parse_string()
        elseif c == '{' then
            return parse_object()
        elseif c == '[' then
            return parse_array()
        elseif c:match("[%d%-]") then
            return parse_number()
        elseif str:sub(pos, pos + 3) == "true" then
            pos = pos + 4
            return true
        elseif str:sub(pos, pos + 4) == "false" then
            pos = pos + 5
            return false
        elseif str:sub(pos, pos + 3) == "null" then
            pos = pos + 4
            return nil
        end
        pos = pos + 1
        return nil
    end

    return parse_value()
end

local BalatroBridge = {
    server = nil,
    client = nil,
    port = 12346,
    buffer = ""
}

function BalatroBridge.init()
    local s, err = socket.bind("0.0.0.0", BalatroBridge.port)
    if not s then
        print("[BalatroBridge] Failed to bind to port " .. BalatroBridge.port .. ": " .. tostring(err))
        return
    end
    s:settimeout(0) -- Non-blocking
    BalatroBridge.server = s
    print("[BalatroBridge] Listening on 0.0.0.0:" .. BalatroBridge.port)

    -- Guard G.FUNCS.select_blind against nil G.blind_prompt_box crashes (official Balatro engine bug on Android / continue)
    if G and G.FUNCS then
        G.FUNCS.select_blind = function(e)
            if G.GAME and G.GAME.facing_blind then return end
            if stop_use then pcall(stop_use) end
            if G.blind_select then 
                G.GAME.facing_blind = true
                if G.blind_prompt_box then
                    pcall(function()
                        local t1 = G.blind_prompt_box:get_UIE_by_ID('prompt_dynatext1')
                        if t1 and t1.config and t1.config.object then t1.config.object.pop_delay = 0; t1.config.object:pop_out(5) end
                        local t2 = G.blind_prompt_box:get_UIE_by_ID('prompt_dynatext2')
                        if t2 and t2.config and t2.config.object then t2.config.object.pop_delay = 0; t2.config.object:pop_out(5) end
                    end)
                end

                G.E_MANAGER:add_event(Event({
                  trigger = 'before', delay = 0.2,
                  func = function()
                    if G.blind_prompt_box and G.blind_prompt_box.alignment and G.blind_prompt_box.alignment.offset then G.blind_prompt_box.alignment.offset.y = -10 end
                    if G.blind_select and G.blind_select.alignment and G.blind_select.alignment.offset then G.blind_select.alignment.offset.y = 40; G.blind_select.alignment.offset.x = 0 end
                    return true
                end}))
                G.E_MANAGER:add_event(Event({
                  trigger = 'immediate',
                  func = function()
                    if ease_round then pcall(function() ease_round(1) end) end
                    if inc_career_stat then pcall(function() inc_career_stat('c_rounds', 1) end) end
                    if _DEMO then
                      G.SETTINGS.DEMO_ROUNDS = (G.SETTINGS.DEMO_ROUNDS or 0) + 1
                      if inc_steam_stat then pcall(function() inc_steam_stat('demo_rounds') end) end
                      if G.save_settings then pcall(function() G:save_settings() end) end
                    end
                    if e and e.config and e.config.ref_table and G.GAME and G.GAME.round_resets then
                        G.GAME.round_resets.blind = e.config.ref_table
                    end
                    if G.GAME and G.GAME.round_resets and G.GAME.round_resets.blind_states and G.GAME.blind_on_deck then
                        G.GAME.round_resets.blind_states[G.GAME.blind_on_deck] = 'Current'
                    end
                    if G.blind_select then pcall(function() G.blind_select:remove() end); G.blind_select = nil end
                    if G.blind_prompt_box then pcall(function() G.blind_prompt_box:remove() end); G.blind_prompt_box = nil end
                    if delay then pcall(function() delay(0.2) end) end
                    return true
                end}))
                G.E_MANAGER:add_event(Event({
                  trigger = 'immediate',
                  func = function()
                    if new_round then pcall(new_round) end
                    return true
                  end
                }))
            end
        end
    end
end

-- Helper to extract clean human-readable text for any card/consumable/pack/voucher
local function get_card_desc(c)
    if not c then return "" end
    local key = c.config and c.config.center and c.config.center.key
    local set = (c.ability and c.ability.set) or (c.config and c.config.center and c.config.center.set) or "Tarot"
    if G and G.localization and G.localization.descriptions and G.localization.descriptions[set] and key and G.localization.descriptions[set][key] then
        local entry = G.localization.descriptions[set][key]
        if entry.text then
            local lines = {}
            for _, line in ipairs(entry.text) do
                local clean = tostring(line):gsub("%b{}", "")
                table.insert(lines, clean)
            end
            local res = table.concat(lines, " ")
            if #res > 0 then return res end
        end
    end
    if c.ability and c.ability.text and type(c.ability.text) == "string" and #c.ability.text > 0 then
        return c.ability.text
    end
    if c.config and c.config.center and c.config.center.text then
        local lines = {}
        for _, line in ipairs(c.config.center.text) do
            table.insert(lines, tostring(line):gsub("%b{}", ""))
        end
        local res = table.concat(lines, " ")
        if #res > 0 then return res end
    end
    return ""
end

-- Helper to reliably detect and normalize card/joker editions (Foil, Holographic, Polychrome, Negative)
local function get_card_edition(c)
    if not c or not c.edition then return nil end
    if c.edition.type then return c.edition.type end
    if c.edition.foil then return "Foil" end
    if c.edition.holo then return "Holographic" end
    if c.edition.polychrome then return "Polychrome" end
    if c.edition.negative then return "Negative" end
    if c.edition.key then
        if c.edition.key:find("foil") then return "Foil" end
        if c.edition.key:find("holo") then return "Holographic" end
        if c.edition.key:find("poly") then return "Polychrome" end
        if c.edition.key:find("negative") then return "Negative" end
    end
    return "Special"
end

-- Helper to extract detailed enhancements, editions and seals for playing cards
local function get_playing_card_desc(c)
    if not c then return "" end
    local parts = {}
    if c.ability and c.ability.name and c.ability.name ~= "Default" then
        local enh_desc = {
            ["Bonus Card"] = "+30 extra chips when played",
            ["Mult Card"] = "+4 Mult when played",
            ["Wild Card"] = "Can be used as any suit",
            ["Glass Card"] = "x2 Mult when scored (1 in 4 chance to break)",
            ["Steel Card"] = "x1.5 Mult while held in hand",
            ["Stone Card"] = "+50 chips, no rank or suit",
            ["Gold Card"] = "+$3 if held in hand at end of round",
            ["Lucky Card"] = "1 in 5 chance for +20 Mult, 1 in 15 chance for +$20"
        }
        local d = enh_desc[c.ability.name] or c.ability.name
        table.insert(parts, "[" .. c.ability.name .. ": " .. d .. "]")
    end

    if c.edition then
        local ed_type = get_card_edition(c) or "Special"
        local ed_desc = {
            Foil = "+50 Chips",
            Holographic = "+10 Mult",
            Polychrome = "x1.5 Mult",
            Negative = "+1 Slot"
        }
        table.insert(parts, "[" .. ed_type .. ": " .. (ed_desc[ed_type] or "") .. "]")
    end

    if c.seal then
        local seal_desc = {
            Red = "Retrigger this card 1 time",
            Blue = "Creates Planet card of final played hand if held in hand at end of round",
            Purple = "Creates a Tarot card when discarded",
            Gold = "Earn $3 when this card is scored"
        }
        table.insert(parts, "[" .. c.seal .. " Seal: " .. (seal_desc[c.seal] or "Special Seal") .. "]")
    end

    if #parts > 0 then
        return table.concat(parts, " ")
    end
    return "Standard card (No enhancements/editions/seals)"
end

-- Helper to check if a card object is active, alive, and available for gameplay/RPC
local function is_card_valid(c)
    if not c then return false end
    if c.removed or c.REMOVED or c.destroyed or c.shattered then return false end
    -- Active cards in player's Joker or Consumable inventory are always valid items
    if c.area and (c.area == G.jokers or c.area == G.consumeables) then
        return true
    end
    if c._rpc_consumed then return false end
    if c.states and c.states.visible == false then return false end
    if c.ability and c.ability.discarded then return false end
    return true
end

local _last_cashed_out_key = nil

-- Helper to reliably find the cash_out_button across all UI layers and containers
local function find_cash_out_btn()
    -- 1. Scan G.I.UIBOX via get_UIE_by_ID
    if G.I and G.I.UIBOX then
        for _, uibox in ipairs(G.I.UIBOX) do
            if uibox.get_UIE_by_ID then
                local b = uibox:get_UIE_by_ID('cash_out_button')
                if b then return b end
            end
        end
    end

    -- 2. Scan G.round_eval via get_UIE_by_ID
    if G.round_eval and G.round_eval.get_UIE_by_ID then
        local b = G.round_eval:get_UIE_by_ID('cash_out_button')
        if b then return b end
    end

    -- 3. Recursive tree scan across G.round_eval and G.I.UIBOX
    local function scan_node(node)
        if not node then return nil end
        if node.config and (node.config.id == 'cash_out_button' or node.config.button == 'cash_out') then
            return node
        end
        if node.children then
            for _, child in pairs(node.children) do
                local found = scan_node(child)
                if found then return found end
            end
        end
        return nil
    end

    if G.round_eval and G.round_eval.UIRoot then
        local b = scan_node(G.round_eval.UIRoot)
        if b then return b end
    end

    if G.I and G.I.UIBOX then
        for _, uibox in ipairs(G.I.UIBOX) do
            if uibox.UIRoot then
                local b = scan_node(uibox.UIRoot)
                if b then return b end
            end
        end
    end

    return nil
end

-- Extracts compact, clean GameState table from global G object
function BalatroBridge.get_game_state()
    if not G or not G.GAME then
        return {
            stage = "UNKNOWN",
            ante = 1,
            round = 1,
            dollars = 0,
            hands_left = 0,
            discards_left = 0,
            hand_size = 8,
            current_score = 0,
            target_score = 300,
            blind = { name = "Unknown", chips_needed = 300, reward = 3, is_boss = false },
            hand = {},
            jokers = {},
            consumables = {},
            max_jokers = 5,
            max_consumables = 2,
            shop = nil,
            deck_remaining = 52,
            is_animating = false,
            game_over = false,
            victory = false
        }
    end

    local stage = "UNKNOWN"
    if G.STATE == G.STATES.SELECTING_HAND then
        stage = "SELECTING_HAND"
    elseif G.STATE == G.STATES.HAND_PLAYED or G.STATE == G.STATES.DRAW_TO_HAND then
        stage = "PLAYING_HAND"
    elseif G.STATE == G.STATES.ROUND_EVAL then
        stage = "ROUND_EVAL"
    elseif G.STATE == G.STATES.SHOP then
        stage = "SHOP"
    elseif G.STATE == G.STATES.BLIND_SELECT then
        if G.GAME and G.GAME.facing_blind then
            stage = "PLAYING_HAND"
        else
            stage = "BLIND_SELECT"
        end
    elseif G.STATE == G.STATES.TAROT_PACK or G.STATE == G.STATES.PLANET_PACK or G.STATE == G.STATES.SPECTRAL_PACK or G.STATE == G.STATES.STANDARD_PACK or G.STATE == G.STATES.BUFFOON_PACK then
        stage = "OPENING_BOOSTER"
    elseif G.STATE == G.STATES.PLAY_TAROT or G.TAROT_INTERRUPT ~= nil then
        stage = "PLAYING_TAROT"
    elseif G.STATE == G.STATES.GAME_OVER then
        stage = "GAME_OVER"
    elseif G.shop and not G.shop.REMOVED and not (G.CONTROLLER and G.CONTROLLER.locks and G.CONTROLLER.locks.toggle_shop) then
        stage = "SHOP"
    elseif G.round_eval and not G.round_eval.REMOVED then
        stage = "ROUND_EVAL"
    elseif G.blind_select and not G.blind_select.REMOVED and not (G.GAME and G.GAME.facing_blind) then
        stage = "BLIND_SELECT"
    end

    local hand_cards = {}
    if G.hand and G.hand.cards then
        for i, card in ipairs(G.hand.cards) do
            if is_card_valid(card) then
                local suit = "None"
                if card.base and card.base.suit then
                    suit = card.base.suit
                end
                local rank = "None"
                if card.base and card.base.value then
                    rank = tostring(card.base.value)
                end

                local is_face_down = card.facing == 'back'
                local is_the_mark = G.GAME and G.GAME.blind and G.GAME.blind.name == "The Mark"
                table.insert(hand_cards, {
                    id = "c_" .. (card.ID or i),
                    suit = is_face_down and "None" or suit,
                    rank = is_face_down and (is_the_mark and "Face" or "?") or rank,
                    nominal = is_face_down and (is_the_mark and 10 or 0) or (card.base and card.base.nominal or 0),
                    chip_bonus = is_face_down and 0 or (card.ability and card.ability.bonus or 0),
                    mult_bonus = is_face_down and 0 or (card.ability and card.ability.mult or 0),
                    x_mult = is_face_down and 1.0 or (card.ability and card.ability.x_mult or 1.0),
                    enhancement = is_face_down and nil or (card.ability and card.ability.name or nil),
                    edition = is_face_down and nil or get_card_edition(card),
                    seal = is_face_down and nil or (card.seal or nil),
                    debuffed = card.debuff or false,
                    highlighted = card.highlighted or false,
                    face_down = is_face_down,
                    revealed_name = nil
                })
            end
        end
    end

    local jokers = {}
    if G.jokers and G.jokers.cards then
        local j_order = 0
        for i, j in ipairs(G.jokers.cards) do
            if is_card_valid(j) then
                local effect_type = "mult"
                if j.ability then
                    if j.ability.x_mult and j.ability.x_mult > 1.0 then
                        effect_type = "x_mult"
                    elseif j.ability.chips and j.ability.chips > 0 then
                        effect_type = "chips"
                    elseif j.ability.dollars or j.ability.extra_dollars then
                        effect_type = "econ"
                    end
                end

                local rarity_names = {"Common", "Uncommon", "Rare", "Legendary"}
                local raw_rarity = j.config and j.config.center and j.config.center.rarity
                local rarity = (type(raw_rarity) == "number" and rarity_names[raw_rarity]) or (type(raw_rarity) == "string" and raw_rarity) or "Common"
                local target_suit = nil
                local target_rank = nil
                if j.ability then
                    if j.ability.extra and type(j.ability.extra) == "table" then
                        target_suit = j.ability.extra.suit
                        target_rank = j.ability.extra.rank
                    end
                    if not target_suit and j.ability.suit then target_suit = j.ability.suit end
                    if not target_rank and j.ability.rank then target_rank = j.ability.rank end
                end

                local desc_txt = get_card_desc(j)
                if target_suit and desc_txt:find("#2#") then
                    desc_txt = desc_txt:gsub("#2#", tostring(target_suit))
                end
                if target_rank and desc_txt:find("#2#") then
                    desc_txt = desc_txt:gsub("#2#", tostring(target_rank))
                end
                if target_suit and desc_txt:find("#1#") and j.config and j.config.center and j.config.center.key == "j_ancient" then
                    desc_txt = desc_txt:gsub("#1#", "1.5")
                end

                local ed_type = get_card_edition(j)

                table.insert(jokers, {
                    id = "j_" .. (j.ID or i),
                    key = j.config and j.config.center and j.config.center.key or "unknown",
                    label = j.ability and j.ability.name or (j.config and j.config.center and j.config.center.name or "Joker"),
                    rarity = rarity,
                    order = j_order,
                    sell_cost = j.sell_cost or 1,
                    effect_type = effect_type,
                    effect_desc = desc_txt,
                    edition = ed_type,
                    target_suit = target_suit,
                    target_rank = target_rank,
                    x_mult = (j.ability and (j.ability.x_mult or j.ability.Xmult)) or 1.0,
                    mult = (j.ability and j.ability.mult) or 0,
                    chips = (j.ability and (j.ability.chips or j.ability.t_chips)) or 0,
                    pinned = j.pinned or false,
                    eternal = (j.ability and j.ability.eternal) or false,
                    perishable = (j.ability and j.ability.perishable) or false,
                    perish_rounds_left = (j.ability and j.ability.perish_tally) or 5,
                    rental = (j.ability and j.ability.rental) or false,
                    debuffed = j.debuff or false
                })
                j_order = j_order + 1
            end
        end
    end

    local consumables = {}
    if G.consumeables and G.consumeables.cards then
        for idx, c in ipairs(G.consumeables.cards) do
            if is_card_valid(c) then
                local c_type = (c.ability and c.ability.set) or (c.config and c.config.center and c.config.center.set) or "Tarot"
                table.insert(consumables, {
                    id = "cons_" .. (c.ID or idx),
                    key = (c.config and c.config.center and c.config.center.key) or "unknown",
                    label = (c.ability and c.ability.name) or "Consumable",
                    card_type = c_type,
                    cost = c.cost or 3,
                    desc = get_card_desc(c)
                })
            end
        end
    end

    local pack_cards = {}
    if stage == "OPENING_BOOSTER" and G.booster_pack and not G.booster_pack.REMOVED and G.pack_cards and G.pack_cards.cards then
        local p_idx = 0
        for idx, c in ipairs(G.pack_cards.cards) do
            if is_card_valid(c) then
                local item_type = (c.ability and c.ability.set) or (c.config and c.config.center and c.config.center.set) or "Card"
                local is_playing_card = (item_type == "Default" or item_type == "Enhanced" or c.base ~= nil)
                local lbl = ""
                local desc = ""
                if is_playing_card and c.base and c.base.value and c.base.suit then
                    item_type = "PlayingCard"
                    lbl = tostring(c.base.value) .. " of " .. tostring(c.base.suit)
                    desc = get_playing_card_desc(c)
                else
                    lbl = (c.ability and c.ability.name) or (c.config and c.config.center and c.config.center.name) or "Pack Card"
                    desc = get_card_desc(c)
                end

                local ed_type = get_card_edition(c)

                table.insert(pack_cards, {
                    index = p_idx,
                    id = "pack_" .. (c.ID or idx),
                    key = (c.config and c.config.center and c.config.center.key) or nil,
                    label = lbl,
                    item_type = item_type,
                    cost = 0,
                    desc = desc,
                    edition = ed_type
                })
                p_idx = p_idx + 1
            end
        end
    end

    local shop_data = nil
    if stage == "SHOP" and G.shop then
        local items = {}
        local current_slot = 0

        -- 1. Regular shop cards (Jokers, Tarot, Planets)
        if G.shop_jokers and G.shop_jokers.cards then
            for idx, c in ipairs(G.shop_jokers.cards) do
                if is_card_valid(c) then
                    local itype = (c.ability and c.ability.set) or "Joker"
                    local ed_type = get_card_edition(c)
                    table.insert(items, {
                        index = current_slot,
                        id = "shop_" .. (c.ID or current_slot),
                        key = (c.config and c.config.center and c.config.center.key) or nil,
                        label = (c.ability and c.ability.name) or "Shop Card",
                        item_type = itype,
                        cost = c.cost or 4,
                        desc = get_card_desc(c),
                        edition = ed_type
                    })
                    current_slot = current_slot + 1
                end
            end
        end

        -- 2. Booster Packs
        if G.shop_booster and G.shop_booster.cards then
            for idx, c in ipairs(G.shop_booster.cards) do
                if is_card_valid(c) then
                    table.insert(items, {
                        index = current_slot,
                        id = "booster_" .. (c.ID or current_slot),
                        key = (c.config and c.config.center and c.config.center.key) or nil,
                        label = (c.ability and c.ability.name) or "Booster Pack",
                        item_type = "BoosterPack",
                        cost = c.cost or 6,
                        desc = get_card_desc(c)
                    })
                    current_slot = current_slot + 1
                end
            end
        end

        -- 3. Vouchers
        if G.shop_vouchers and G.shop_vouchers.cards then
            for idx, c in ipairs(G.shop_vouchers.cards) do
                if is_card_valid(c) then
                    table.insert(items, {
                        index = current_slot,
                        id = "voucher_" .. (c.ID or current_slot),
                        key = (c.config and c.config.center and c.config.center.key) or nil,
                        label = (c.ability and c.ability.name) or "Voucher",
                        item_type = "Voucher",
                        cost = c.cost or 10,
                        desc = get_card_desc(c)
                    })
                    current_slot = current_slot + 1
                end
            end
        end

        shop_data = {
            items = items,
            reroll_cost = G.GAME.current_round and G.GAME.current_round.reroll_cost or 5
        }
    end

    local blind_name = "Small Blind"
    local blind_chips = 300
    local is_boss = false
    local debuff_rule = nil
    local reward = 3

    -- 1. Extract upcoming Boss Blind info for this Ante (visible across the entire Ante)
    local upcoming_boss_name = nil
    local upcoming_boss_debuff = nil
    if G.GAME and G.GAME.round_resets and G.GAME.round_resets.blind_choices and G.GAME.round_resets.blind_choices.Boss then
        local b_key = G.GAME.round_resets.blind_choices.Boss
        local ref = G.P_BLINDS and G.P_BLINDS[b_key]
        if ref then
            upcoming_boss_name = ref.name or "Boss Blind"
        end
        if G.localization and G.localization.descriptions and G.localization.descriptions.Blind and G.localization.descriptions.Blind[b_key] then
            local desc_data = G.localization.descriptions.Blind[b_key]
            upcoming_boss_name = desc_data.name or upcoming_boss_name
            if desc_data.text and type(desc_data.text) == "table" then
                upcoming_boss_debuff = table.concat(desc_data.text, " ")
            end
        end
    end

    -- 2. Determine currently active/selected Blind
    local skip_tag_name = nil
    local skip_tag_desc = nil
    if stage == "BLIND_SELECT" then
        local b_type = G.GAME.blind_on_deck or "Small"
        is_boss = (b_type == "Boss")
        reward = (b_type == "Small" and 3) or (b_type == "Big" and 4) or 5
        local b_key = G.GAME.round_resets and G.GAME.round_resets.blind_choices and G.GAME.round_resets.blind_choices[b_type]
        if b_key and G.P_BLINDS and G.P_BLINDS[b_key] then
            local ref = G.P_BLINDS[b_key]
            blind_name = ref.name or (b_type .. " Blind")
            local ante_num = (G.GAME.round_resets and G.GAME.round_resets.ante) or 1
            local base_blind = 300
            if get_blind_amount then
                base_blind = get_blind_amount(ante_num)
            end
            local scaling = (G.GAME.starting_params and G.GAME.starting_params.ante_scaling) or 1
            blind_chips = math.floor(base_blind * (ref.mult or 1) * scaling)
        else
            blind_name = b_type .. " Blind"
            blind_chips = 300
        end

        if is_boss then
            blind_name = upcoming_boss_name or blind_name
            debuff_rule = upcoming_boss_debuff
        else
            -- Extract skip Tag info
            local tag_key = G.GAME.round_resets and G.GAME.round_resets.blind_tags and G.GAME.round_resets.blind_tags[b_type]
            if tag_key then
                local proto = G.P_TAGS and G.P_TAGS[tag_key]
                skip_tag_name = (proto and proto.name) or tag_key
                if G.localization and G.localization.descriptions and G.localization.descriptions.Tag and G.localization.descriptions.Tag[tag_key] then
                    local t_data = G.localization.descriptions.Tag[tag_key]
                    skip_tag_name = t_data.name or skip_tag_name
                    if t_data.text and type(t_data.text) == "table" then
                        skip_tag_desc = table.concat(t_data.text, " ")
                    end
                end
            end
        end
    elseif G.GAME.blind then
        blind_name = G.GAME.blind.name or "Small Blind"
        blind_chips = math.floor(G.GAME.blind.chips or 300)
        is_boss = G.GAME.blind.boss or false
        reward = G.GAME.blind.dollars or 3
        
        if G.GAME.blind.disabled then
            debuff_rule = "DISABLED (Boss ability negated)"
        elseif G.GAME.blind.loc_debuff_text and #G.GAME.blind.loc_debuff_text > 0 then
            debuff_rule = G.GAME.blind.loc_debuff_text
        elseif G.GAME.blind.loc_debuff_lines and #G.GAME.blind.loc_debuff_lines > 0 then
            debuff_rule = table.concat(G.GAME.blind.loc_debuff_lines, " ")
        elseif is_boss then
            debuff_rule = upcoming_boss_debuff
        end
    end

    -- 2b. Extract full Ante Blind Schedule (Small, Big, Boss) with both Skip Tags & statuses
    local ante_blinds = {}
    local b_types = {"Small", "Big", "Boss"}
    local cur_ante = (G.GAME.round_resets and G.GAME.round_resets.ante) or 1
    local sched_base = 300
    if get_blind_amount then
        sched_base = get_blind_amount(cur_ante)
    end
    local sched_scaling = (G.GAME.starting_params and G.GAME.starting_params.ante_scaling) or 1

    for _, bt in ipairs(b_types) do
        local b_key = G.GAME.round_resets and G.GAME.round_resets.blind_choices and G.GAME.round_resets.blind_choices[bt]
        local ref = (b_key and G.P_BLINDS and G.P_BLINDS[b_key]) or nil
        local b_name = bt .. " Blind"
        local b_chips = 300
        local b_reward = (bt == "Small" and 3) or (bt == "Big" and 4) or 5
        local b_debuff = nil
        local b_tag_name = nil
        local b_tag_desc = nil
        local b_state = (G.GAME.round_resets and G.GAME.round_resets.blind_states and G.GAME.round_resets.blind_states[bt]) or "Upcoming"

        if ref then
            b_name = ref.name or b_name
            b_chips = math.floor(sched_base * (ref.mult or 1) * sched_scaling)
        end

        if bt == "Boss" then
            b_name = upcoming_boss_name or b_name
            b_debuff = upcoming_boss_debuff
        else
            local tag_key = G.GAME.round_resets and G.GAME.round_resets.blind_tags and G.GAME.round_resets.blind_tags[bt]
            if tag_key then
                local proto = G.P_TAGS and G.P_TAGS[tag_key]
                b_tag_name = (proto and proto.name) or tag_key
                if G.localization and G.localization.descriptions and G.localization.descriptions.Tag and G.localization.descriptions.Tag[tag_key] then
                    local t_data = G.localization.descriptions.Tag[tag_key]
                    b_tag_name = t_data.name or b_tag_name
                    if t_data.text and type(t_data.text) == "table" then
                        b_tag_desc = table.concat(t_data.text, " ")
                    end
                end
            end
        end

        table.insert(ante_blinds, {
            type = bt,
            name = b_name,
            chips = b_chips,
            reward = b_reward,
            status = b_state,
            tag_name = b_tag_name,
            tag_desc = b_tag_desc,
            debuff_rule = b_debuff
        })
    end

    local deck_name = (G.GAME.selected_back and G.GAME.selected_back.name) or "Red Deck"
    local stake_names = {"White Stake", "Red Stake", "Green Stake", "Black Stake", "Blue Stake", "Purple Stake", "Orange Stake", "Gold Stake"}
    local stake = (G.GAME.stake and stake_names[G.GAME.stake]) or "White Stake"
    local challenge_name = (G.GAME.challenge and G.GAME.challenge.name) or nil

    -- 3. Extract poker hands levels and scoring
    local poker_hands = {}
    if G.GAME and G.GAME.hands then
        for hand_name, hand_info in pairs(G.GAME.hands) do
            if hand_info.visible then
                table.insert(poker_hands, {
                    name = hand_name,
                    level = hand_info.level or 1,
                    chips = hand_info.chips or 0,
                    mult = hand_info.mult or 0,
                    played = hand_info.played or 0,
                    order = hand_info.order or 99
                })
            end
        end
        table.sort(poker_hands, function(a, b) return a.order < b.order end)
    end

    -- 4. Extract remaining deck summary from draw pile with full card granularity
    local deck_summary = {
        total_remaining = G.deck and #G.deck.cards or 52,
        suits = { Spades = 0, Hearts = 0, Clubs = 0, Diamonds = 0 },
        ranks = {
            ["2"] = 0, ["3"] = 0, ["4"] = 0, ["5"] = 0, ["6"] = 0,
            ["7"] = 0, ["8"] = 0, ["9"] = 0, ["10"] = 0,
            ["Jack"] = 0, ["Queen"] = 0, ["King"] = 0, ["Ace"] = 0
        },
        face_cards = 0,
        aces = 0,
        enhancements = {},
        special_cards = {},
        cards = {}
    }
    if G.playing_cards then
        for i, c in ipairs(G.playing_cards) do
            if c.area == G.deck then
                local suit = c.base and c.base.suit
                if suit and deck_summary.suits[suit] ~= nil then
                    deck_summary.suits[suit] = deck_summary.suits[suit] + 1
                end
                local rank = c.base and tostring(c.base.value)
                if rank then
                    deck_summary.ranks[rank] = (deck_summary.ranks[rank] or 0) + 1
                    if rank == "Jack" or rank == "Queen" or rank == "King" then
                        deck_summary.face_cards = deck_summary.face_cards + 1
                    elseif rank == "Ace" then
                        deck_summary.aces = deck_summary.aces + 1
                    end
                end

                local enh = (c.ability and c.ability.name and c.ability.name ~= "Default") and c.ability.name or nil
                if enh then
                    deck_summary.enhancements[enh] = (deck_summary.enhancements[enh] or 0) + 1
                end

                local ed_type = get_card_edition(c)
                local seal = c.seal or nil

                table.insert(deck_summary.cards, {
                    id = "dc_" .. (c.ID or i),
                    suit = suit or "Spades",
                    rank = rank or "2",
                    nominal = c.base and c.base.nominal or 0,
                    enhancement = enh,
                    edition = ed_type,
                    seal = seal
                })

                if enh or ed_type or seal then
                    table.insert(deck_summary.special_cards, {
                        suit = suit or "Spades",
                        rank = rank or "Unknown",
                        enhancement = enh,
                        edition = ed_type,
                        seal = seal,
                        desc = get_playing_card_desc(c)
                    })
                end
            end
        end
    end

    local has_directors_cut = (G.GAME.used_vouchers and G.GAME.used_vouchers["v_directors_cut"]) or false
    local has_retcon = (G.GAME.used_vouchers and G.GAME.used_vouchers["v_retcon"]) or false
    local boss_rerolled = (G.GAME.round_resets and G.GAME.round_resets.boss_rerolled) or false
    local can_reroll_boss = (has_retcon or (has_directors_cut and not boss_rerolled)) and ((G.GAME.dollars or 0) >= 10) and (stage == "BLIND_SELECT")

    local is_sliding_off = G.round_eval and G.round_eval.alignment and G.round_eval.alignment.offset and G.round_eval.alignment.offset.y and G.round_eval.alignment.offset.y >= ((G.ROOM and G.ROOM.T and G.ROOM.T.y or 0) + 10)
    local cash_out_ready = false
    if (G.STATE == G.STATES.ROUND_EVAL or stage == "ROUND_EVAL") and not is_sliding_off then
        cash_out_ready = (find_cash_out_btn() ~= nil)
    end

    local is_animating = false
    if G.STATE == G.STATES.ROUND_EVAL or stage == "ROUND_EVAL" then
        is_animating = not cash_out_ready
    elseif stage == "PLAYING_TAROT" or G.STATE == G.STATES.PLAY_TAROT or G.TAROT_INTERRUPT ~= nil then
        is_animating = true
    elseif G.STATE == G.STATES.DRAW_TO_HAND or G.STATE == G.STATES.HAND_PLAYED or stage == "PLAYING_HAND" then
        is_animating = true
    elseif stage == "OPENING_BOOSTER" then
        local pack_closing = (G.booster_pack == nil) or (G.GAME.pack_choices and G.GAME.pack_choices <= 0)
        is_animating = (pack_closing or #pack_cards == 0 or ((G.STATE == G.STATES.TAROT_PACK or G.STATE == G.STATES.SPECTRAL_PACK) and #hand_cards == 0 and G.deck and #G.deck.cards > 0))
    elseif stage == "SHOP" then
        local shop_sliding = (G.shop and G.shop.alignment and G.shop.alignment.offset and G.shop.alignment.offset.y and G.shop.alignment.offset.y > 0)
        local shop_locked = (G.CONTROLLER and G.CONTROLLER.locks and (G.CONTROLLER.locks.toggle_shop or G.CONTROLLER.locks.shop_reroll or G.CONTROLLER.locks.selling_card or G.CONTROLLER.locks.use))
        is_animating = shop_sliding or (shop_locked and true or false)
    elseif stage == "BLIND_SELECT" then
        local blind_locked = (G.CONTROLLER and G.CONTROLLER.locks and (G.CONTROLLER.locks.skip_blind or G.CONTROLLER.locks.boss_reroll))
        local entering_blind = (G.GAME and G.GAME.facing_blind) and true or false
        is_animating = entering_blind or (blind_locked and true or false)
    elseif stage == "SELECTING_HAND" then
        local play_active = (G.play and G.play.cards and #G.play.cards > 0)
        local cards_discarding = false
        if G.hand and G.hand.cards then
            for _, hc in ipairs(G.hand.cards) do
                if (hc.ability and hc.ability.discarded) or hc._rpc_consumed then
                    cards_discarding = true
                    break
                end
            end
        end
        is_animating = play_active or cards_discarding
    elseif G.STATE ~= G.STATES.SELECTING_HAND and G.STATE ~= G.STATES.SHOP and G.STATE ~= G.STATES.BLIND_SELECT then
        is_animating = true
    end

    return {
        bridge_version = "v1.9.14",
        stage = stage,
        can_reroll_boss = can_reroll_boss,
        ante = G.GAME.round_resets and G.GAME.round_resets.ante or 1,
        round = G.GAME.round or 1,
        deck_name = deck_name,
        stake = stake,
        challenge_name = challenge_name,
        dollars = G.GAME.dollars or 0,
        hands_left = G.GAME.current_round and G.GAME.current_round.hands_left or 0,
        discards_left = G.GAME.current_round and G.GAME.current_round.discards_left or 0,
        hand_size = G.hand and G.hand.config and G.hand.config.card_limit or 8,
        current_score = math.floor(G.GAME.chips or 0),
        target_score = math.floor(blind_chips),
        blind = {
            name = blind_name,
            chips_needed = math.floor(blind_chips),
            reward = reward,
            is_boss = is_boss,
            debuff_rule = debuff_rule,
            upcoming_boss_name = upcoming_boss_name,
            upcoming_boss_debuff = upcoming_boss_debuff,
            skip_tag_name = skip_tag_name,
            skip_tag_desc = skip_tag_desc,
            defeated = (G.GAME.chips or 0) >= blind_chips
        },
        ante_blinds = ante_blinds,
        hand = hand_cards,
        jokers = jokers,
        consumables = consumables,
        pack_cards = pack_cards,
        pack_choices = G.GAME.pack_choices or 1,
        max_jokers = G.jokers and G.jokers.config and G.jokers.config.card_limit or 5,
        max_consumables = G.consumeables and G.consumeables.config and G.consumeables.config.card_limit or 2,
        shop = shop_data,
        deck_remaining = G.deck and #G.deck.cards or 52,
        poker_hands = poker_hands,
        deck_summary = deck_summary,
        interest_cap = (G.GAME and G.GAME.interest_cap) or 25,
        vouchers = (function()
            local v_list = {}
            if G.GAME and G.GAME.used_vouchers then
                for k, v in pairs(G.GAME.used_vouchers) do
                    if v then table.insert(v_list, k) end
                end
            end
            table.sort(v_list)
            return v_list
        end)(),
        is_animating = is_animating,
        game_over = G.STATE == G.STATES.GAME_OVER,
        victory = G.GAME.won or false
    }
end

-- Dispatches executable action to Balatro internal functions
local function to_id_table(val)
    if type(val) == "table" then
        return val
    elseif type(val) == "string" then
        local res = {}
        for item in string.gmatch(val, "[%w_-]+") do
            table.insert(res, item)
        end
        return res
    end
    return {}
end

function BalatroBridge.dispatch_action(method, params)
    params = params or {}
    if method == "get_game_state" then
        return BalatroBridge.get_game_state()
    elseif method == "play_cards" then
        if G.STATE ~= G.STATES.SELECTING_HAND then
            return { success = false, error = "Cannot play cards: game is not in SELECTING_HAND stage (current: " .. tostring(G.STATE) .. ")" }
        end
        if G.GAME and G.GAME.current_round and (G.GAME.current_round.hands_left or 0) <= 0 then
            return { success = false, error = "Cannot play cards: 0 hands left this round" }
        end
        if G.play and G.play.cards and #G.play.cards > 0 then
            return { success = false, error = "Cannot play cards: cards already in play/scoring area" }
        end
        if G.hand and G.hand.cards and G.FUNCS and G.FUNCS.play_cards_from_highlighted then
            if G.hand.unhighlight_all then
                G.hand:unhighlight_all()
            end
            local targets = to_id_table(params.card_ids or params.card_id or params.cards)
            local selected_cards = {}
            for _, tid in ipairs(targets) do
                for _, c in ipairs(G.hand.cards) do
                    if is_card_valid(c) and ("c_" .. (c.ID or "")) == tid then
                        table.insert(selected_cards, c)
                        break
                    end
                end
            end
            local base_x = (G.hand.T and G.hand.T.x) or 0
            for idx, c in ipairs(selected_cards) do
                c.T.x = base_x + idx * 50
                if G.hand.add_to_highlighted then
                    G.hand:add_to_highlighted(c, true)
                else
                    c.highlighted = true
                end
            end
            if G.hand.highlighted and #G.hand.highlighted > 0 then
                G.FUNCS.play_cards_from_highlighted()
                return { success = true }
            else
                return { success = false, error = "No matching cards highlighted" }
            end
        end
    elseif method == "discard_cards" then
        if G.STATE ~= G.STATES.SELECTING_HAND then
            return { success = false, error = "Cannot discard cards: game is not in SELECTING_HAND stage (current: " .. tostring(G.STATE) .. ")" }
        end
        if G.GAME and G.GAME.current_round and (G.GAME.current_round.discards_left or 0) <= 0 then
            return { success = false, error = "Cannot discard cards: 0 discards left this round" }
        end
        if G.play and G.play.cards and #G.play.cards > 0 then
            return { success = false, error = "Cannot discard cards: cards currently in play/scoring area" }
        end
        if G.hand and G.hand.cards and G.FUNCS and G.FUNCS.discard_cards_from_highlighted then
            if G.hand.unhighlight_all then
                G.hand:unhighlight_all()
            end
            local targets = to_id_table(params.card_ids or params.card_id or params.cards)
            for _, tid in ipairs(targets) do
                for _, c in ipairs(G.hand.cards) do
                    if is_card_valid(c) and ("c_" .. (c.ID or "")) == tid then
                        if G.hand.add_to_highlighted then
                            G.hand:add_to_highlighted(c, true)
                        else
                            c.highlighted = true
                        end
                    end
                end
            end
            if G.hand.highlighted and #G.hand.highlighted > 0 then
                G.FUNCS.discard_cards_from_highlighted()
                return { success = true }
            else
                return { success = false, error = "No matching cards highlighted" }
            end
        end
    elseif method == "reorder_jokers" then
        if G.jokers and G.jokers.cards then
            local new_cards = {}
            local order = to_id_table(params.joker_order or params.jokers or params.order)
            for _, jid in ipairs(order) do
                for _, j in ipairs(G.jokers.cards) do
                    if ("j_" .. (j.ID or "")) == jid then
                        table.insert(new_cards, j)
                        break
                    end
                end
            end
            if #new_cards == #G.jokers.cards then
                G.jokers.cards = new_cards
                return { success = true }
            end
        end
    elseif method == "reorder_hand" then
        if G.hand and G.hand.cards then
            local new_cards = {}
            local matched = {}
            local order = to_id_table(params.card_order or params.cards or params.order)
            for _, cid in ipairs(order) do
                for _, c in ipairs(G.hand.cards) do
                    local full_id = "c_" .. (c.ID or "")
                    if full_id == cid and not matched[c] then
                        table.insert(new_cards, c)
                        matched[c] = true
                        break
                    end
                end
            end
            for _, c in ipairs(G.hand.cards) do
                if not matched[c] then
                    table.insert(new_cards, c)
                end
            end
            if #new_cards == #G.hand.cards then
                G.hand.cards = new_cards
                local base_x = (G.hand.T and G.hand.T.x) or 0
                for idx, c in ipairs(G.hand.cards) do
                    c.T.x = base_x + idx * 10
                end
                if G.hand.set_ranks then pcall(function() G.hand:set_ranks() end) end
                if G.hand.align_cards then pcall(function() G.hand:align_cards() end) end
                return { success = true }
            else
                return { success = false, error = "Mismatch in card count when reordering hand" }
            end
        else
            return { success = false, error = "Hand cards not available" }
        end
    elseif method == "select_blind" then
        if G.GAME and G.GAME.facing_blind then
            return { success = true }
        end
        if G.STATE == G.STATES.BLIND_SELECT or G.blind_select then
            local blind_type = G.GAME.blind_on_deck or 'Small'
            local opt = G.blind_select_opts and G.blind_select_opts[blind_type:lower()]
            local btn = opt and opt.get_UIE_by_ID and opt:get_UIE_by_ID('select_blind_button')
            if not btn and G.blind_select and G.blind_select.get_UIE_by_ID then
                btn = G.blind_select:get_UIE_by_ID('select_blind_button')
            end

            local blind_key = G.GAME.round_resets and G.GAME.round_resets.blind_choices and G.GAME.round_resets.blind_choices[blind_type]
            local ref = (blind_key and G.P_BLINDS and G.P_BLINDS[blind_key]) or (G.P_BLINDS and G.P_BLINDS['bl_small'])
            if not btn or not btn.config or not btn.config.ref_table then
                btn = { config = { ref_table = ref } }
            elseif not btn.config.ref_table then
                btn.config.ref_table = ref
            end

            local executed = false
            if G.FUNCS and G.FUNCS.select_blind then
                local ok, err = pcall(function() G.FUNCS.select_blind(btn) end)
                if ok then executed = true end
            end

            if not executed then
                if G.GAME and G.GAME.round_resets then
                    G.GAME.round_resets.blind = ref
                    if G.GAME.round_resets.blind_states then
                        G.GAME.round_resets.blind_states[blind_type] = 'Current'
                    end
                end
                G.GAME.facing_blind = true
                if G.blind_select then
                    pcall(function() G.blind_select:remove() end)
                    G.blind_select = nil
                end
                if G.blind_prompt_box then
                    pcall(function() G.blind_prompt_box:remove() end)
                    G.blind_prompt_box = nil
                end
                if new_round then
                    pcall(new_round)
                end
            end

            return { success = true }
        else
            return { success = false, error = "Blind select UI not active" }
        end
    elseif method == "skip_blind" then
        if G.GAME and G.GAME.facing_blind then
            return { success = false, error = "Cannot skip blind: already entering round" }
        end
        if G.CONTROLLER and G.CONTROLLER.locks and G.CONTROLLER.locks.skip_blind then
            return { success = true }
        end
        if G.STATE == G.STATES.BLIND_SELECT or G.blind_select then
            local blind_type = G.GAME.blind_on_deck or 'Small'
            if blind_type == 'Boss' then
                return { success = false, error = "Cannot skip Boss Blind" }
            end

            local skipped = blind_type
            local skip_to = (skipped == 'Small' and 'Big') or 'Boss'

            -- 1. Locate or create tag object
            local tag_key = G.GAME.round_resets and G.GAME.round_resets.blind_tags and G.GAME.round_resets.blind_tags[blind_type]
            local tag_obj = nil
            if tag_key and Tag then
                local ok_t, res_t = pcall(function() return Tag(tag_key, nil, blind_type) end)
                if ok_t then tag_obj = res_t end
            end

            -- 2. Try invoking official G.FUNCS.skip_blind
            local called_official = false
            if G.FUNCS and G.FUNCS.skip_blind then
                local opt = G.blind_select_opts and G.blind_select_opts[blind_type:lower()]
                local tag_uie = opt and opt.get_UIE_by_ID and opt:get_UIE_by_ID('tag_container')
                if not tag_uie and tag_obj then
                    tag_uie = { config = { ref_table = tag_obj } }
                end

                local fake_uibox = {
                    get_UIE_by_ID = function(self, id)
                        if id == 'tag_container' then return tag_uie end
                        if opt and opt.get_UIE_by_ID then return opt:get_UIE_by_ID(id) end
                        return nil
                    end
                }

                local ok, err = pcall(function()
                    G.FUNCS.skip_blind({ UIBox = fake_uibox, config = {} })
                end)
                if ok then
                    called_official = true
                end
            end

            -- 3. Robust direct fallback if official function did not execute or state didn't update
            if not called_official or (G.GAME.round_resets and G.GAME.round_resets.blind_states and G.GAME.round_resets.blind_states[blind_type] ~= 'Skipped') then
                if stop_use then pcall(stop_use) end
                G.GAME.skips = (G.GAME.skips or 0) + 1
                if tag_obj and add_tag then
                    pcall(function() add_tag(tag_obj) end)
                end
                if G.GAME.round_resets and G.GAME.round_resets.blind_states then
                    G.GAME.round_resets.blind_states[skipped] = 'Skipped'
                    G.GAME.round_resets.blind_states[skip_to] = 'Select'
                end
                G.GAME.blind_on_deck = skip_to
                if play_sound then pcall(function() play_sound('generic1') end) end
                if G.E_MANAGER and Event then
                    G.E_MANAGER:add_event(Event({
                        trigger = 'immediate',
                        func = function()
                            if delay then delay(0.3) end
                            if G.jokers and G.jokers.cards then
                                for i = 1, #G.jokers.cards do
                                    if G.jokers.cards[i].calculate_joker then
                                        pcall(function() G.jokers.cards[i]:calculate_joker({skip_blind = true}) end)
                                    end
                                end
                            end
                            if save_run then pcall(save_run) end
                            if G.GAME.tags then
                                for i = 1, #G.GAME.tags do
                                    if G.GAME.tags[i].apply_to_run then
                                        pcall(function() G.GAME.tags[i]:apply_to_run({type = 'immediate'}) end)
                                    end
                                end
                                for i = 1, #G.GAME.tags do
                                    if G.GAME.tags[i].apply_to_run then
                                        local res = false
                                        pcall(function() res = G.GAME.tags[i]:apply_to_run({type = 'new_blind_choice'}) end)
                                        if res then break end
                                    end
                                end
                            end
                            return true
                        end
                    }))
                end
            end

            -- 4. Update UI choice handler if active
            if G.blind_select and G.blind_select_opts and G.FUNCS and G.FUNCS.blind_choice_handler then
                local next_opt = G.blind_select_opts[string.lower(skip_to)]
                if next_opt then
                    pcall(function()
                        G.FUNCS.blind_choice_handler({
                            config = { id = skip_to, ref_table = { deck = nil } },
                            UIBox = next_opt
                        })
                    end)
                end
            end

            return { success = true, skipped = skipped, skip_to = skip_to }
        else
            return { success = false, error = "Blind select UI not active" }
        end
    elseif method == "reroll_boss" or method == "reroll_boss_blind" then
        if G.CONTROLLER and G.CONTROLLER.locks and G.CONTROLLER.locks.boss_reroll then
            return { success = false, error = "Cannot reroll boss: boss reroll animation in progress" }
        end
        if G.STATE == G.STATES.BLIND_SELECT or G.blind_select then
            local has_cut = (G.GAME.used_vouchers and G.GAME.used_vouchers["v_directors_cut"]) or false
            local has_retcon = (G.GAME.used_vouchers and G.GAME.used_vouchers["v_retcon"]) or false
            local boss_rerolled = (G.GAME.round_resets and G.GAME.round_resets.boss_rerolled) or false
            local can_reroll = has_retcon or (has_cut and not boss_rerolled)
            if not can_reroll then
                return { success = false, error = "Boss reroll voucher not owned or already used this Ante" }
            end
            if (G.GAME.dollars or 0) < 10 then
                return { success = false, error = "Cannot reroll boss: insufficient funds ($" .. tostring(G.GAME.dollars or 0) .. " < $10)" }
            end
            if G.FUNCS and G.FUNCS.reroll_boss then
                G.FUNCS.reroll_boss()
                return { success = true }
            else
                return { success = false, error = "G.FUNCS.reroll_boss function not available" }
            end
        else
            return { success = false, error = "Boss reroll is only available during BLIND_SELECT phase" }
        end
    elseif method == "cash_out" then
        if G.STATE ~= G.STATES.ROUND_EVAL and not G.round_eval then
            return { success = true, info = "Not in ROUND_EVAL" }
        end
        -- If already sliding away off-screen, it's already in progress
        if G.round_eval and G.round_eval.alignment and G.round_eval.alignment.offset and G.round_eval.alignment.offset.y and G.round_eval.alignment.offset.y >= ((G.ROOM and G.ROOM.T and G.ROOM.T.y or 0) + 10) then
            return { success = true, info = "Already sliding off" }
        end

        local btn = find_cash_out_btn()
        local ev_arg = btn or { config = { button = 'cash_out' } }
        if not ev_arg.config then ev_arg.config = { button = 'cash_out' } end

        if G.FUNCS and G.FUNCS.cash_out then
            if btn and btn.juice_up then
                pcall(function() btn:juice_up(0.3, 0.2) end)
            end
            local ok, err = pcall(function()
                G.FUNCS.cash_out(ev_arg)
            end)
            if ok then
                return { success = true }
            else
                return { success = false, error = "Error in G.FUNCS.cash_out: " .. tostring(err) }
            end
        else
            return { success = false, error = "G.FUNCS.cash_out not available" }
        end
    elseif method == "reroll_shop" then
        if G.STATE ~= G.STATES.SHOP then
            return { success = false, error = "Cannot reroll: game is not in SHOP stage" }
        end
        if G.CONTROLLER and G.CONTROLLER.locks and G.CONTROLLER.locks.shop_reroll then
            return { success = false, error = "Cannot reroll: shop reroll animation in progress" }
        end
        if G.FUNCS and G.FUNCS.reroll_shop then
            local cost = G.GAME.current_round and G.GAME.current_round.reroll_cost or 5
            if (G.GAME.dollars or 0) < cost then
                return { success = false, error = "Cannot reroll: insufficient funds ($" .. tostring(G.GAME.dollars or 0) .. " < cost $" .. tostring(cost) .. ")" }
            end
            G.FUNCS.reroll_shop({ config = {} })
            return { success = true }
        end
    elseif method == "buy_card" then
        if G.CONTROLLER and G.CONTROLLER.locks and (G.CONTROLLER.locks.toggle_shop or G.CONTROLLER.locks.shop_reroll or G.CONTROLLER.locks.use) then
            return { success = false, error = "Cannot buy: game is busy with animation/lock" }
        end
        if G.shop and (G.shop.REMOVED or (G.shop.alignment and G.shop.alignment.offset and G.shop.alignment.offset.y and G.shop.alignment.offset.y > 0)) then
            return { success = false, error = "Cannot buy: shop is closing" }
        end

        local target_index = params.shop_index or 0
        local target_card = nil
        local current_slot = 0

        -- 1. Check shop_jokers
        if G.shop_jokers and G.shop_jokers.cards then
            for _, c in ipairs(G.shop_jokers.cards) do
                if is_card_valid(c) then
                    if current_slot == target_index then
                        target_card = c
                        break
                    end
                    current_slot = current_slot + 1
                end
            end
        end

        -- 2. Check shop_booster
        if not target_card and G.shop_booster and G.shop_booster.cards then
            for _, c in ipairs(G.shop_booster.cards) do
                if is_card_valid(c) then
                    if current_slot == target_index then
                        target_card = c
                        break
                    end
                    current_slot = current_slot + 1
                end
            end
        end

        -- 3. Check shop_vouchers
        if not target_card and G.shop_vouchers and G.shop_vouchers.cards then
            for _, c in ipairs(G.shop_vouchers.cards) do
                if is_card_valid(c) then
                    if current_slot == target_index then
                        target_card = c
                        break
                    end
                    current_slot = current_slot + 1
                end
            end
        end

        if not target_card then
            return { success = false, error = "Item not found at shop index " .. tostring(target_index) }
        end

        local cost = target_card.cost or 4
        if (G.GAME.dollars or 0) < cost then
            return { success = false, error = "Cannot buy: insufficient funds ($" .. tostring(G.GAME.dollars or 0) .. " < cost $" .. tostring(cost) .. ")" }
        end

        -- Space check for regular Jokers and Consumables
        if G.FUNCS and G.FUNCS.check_for_buy_space and target_card.ability and target_card.ability.set ~= 'Booster' and target_card.ability.set ~= 'Voucher' then
            if not G.FUNCS.check_for_buy_space(target_card) then
                local is_cons = target_card.ability.consumeable or (target_card.ability.set == 'Tarot' or target_card.ability.set == 'Planet' or target_card.ability.set == 'Spectral')
                local err_name = is_cons and ("consumable slots are full (" .. tostring(#G.consumeables.cards) .. "/" .. tostring(G.consumeables.config.card_limit) .. ") - use or sell an existing consumable first!") or ("joker slots are full (" .. tostring(#G.jokers.cards) .. "/" .. tostring(G.jokers.config.card_limit) .. ") - sell a joker first!")
                return { success = false, error = "Cannot buy: " .. err_name }
            end
        end

        if target_card.ability and target_card.ability.set == 'Booster' then
            if G.booster_pack or (G.GAME and G.GAME.PACK_INTERRUPT) then
                return { success = false, error = "A booster pack is already open or opening" }
            end
            target_card._rpc_consumed = true
            G.FUNCS.use_card({ config = { ref_table = target_card } })
            return { success = true }
        elseif target_card.ability and target_card.ability.set == 'Voucher' then
            target_card._rpc_consumed = true
            if G.FUNCS and G.FUNCS.use_card then
                G.FUNCS.use_card({ config = { ref_table = target_card } })
            elseif target_card.redeem then
                target_card:redeem()
            end
            return { success = true }
        else
            target_card._rpc_consumed = true
            G.FUNCS.buy_from_shop({ config = { ref_table = target_card, id = 'buy' } })
            return { success = true }
        end
    elseif method == "end_shop" or method == "toggle_shop" then
        if G.STATE ~= G.STATES.SHOP and not G.shop then
            return { success = true }
        end
        if G.CONTROLLER and G.CONTROLLER.locks and G.CONTROLLER.locks.toggle_shop then
            return { success = true }
        end
        if G.shop and (G.shop.REMOVED or (G.shop.alignment and G.shop.alignment.offset and G.shop.alignment.offset.y and G.shop.alignment.offset.y > 0)) then
            return { success = true }
        end
        if G.FUNCS and G.FUNCS.toggle_shop then
            G.FUNCS.toggle_shop({ config = {} })
            return { success = true }
        end
    elseif method == "use_consumable" then
        if G.STATE == G.STATES.PLAY_TAROT or G.TAROT_INTERRUPT ~= nil then
            return { success = false, error = "Cannot use consumable: tarot animation in progress" }
        end
        if G.CONTROLLER and G.CONTROLLER.locks and G.CONTROLLER.locks.use then
            return { success = false, error = "Cannot use consumable: use animation in progress" }
        end
        local cid = params.card_id or ""
        local target_card = nil
        if G.consumeables and G.consumeables.cards then
            for idx, c in ipairs(G.consumeables.cards) do
                if ("cons_" .. (c.ID or idx)) == cid or (c.config and c.config.center and c.config.center.key == cid) or (c.ability and c.ability.name == cid) or (idx - 1 == params.index) then
                    target_card = c
                    break
                end
            end
        end

        if not target_card then
            return { success = false, error = "Consumable card '" .. tostring(cid) .. "' not found in inventory" }
        end
        if not is_card_valid(target_card) then
            return { success = false, error = "Consumable is already used or dissolving" }
        end

        -- Clear any old highlights first
        if G.hand and G.hand.unhighlight_all then
            G.hand:unhighlight_all()
        end

        -- Highlight target cards in hand if specified (e.g. for Tarot modifying cards)
        local target_ids = to_id_table(params.target_card_ids or params.target_cards)
        if #target_ids > 0 and G.hand and G.hand.cards then
            local base_x = (G.hand.T and G.hand.T.x) or 0
            for idx, tid in ipairs(target_ids) do
                for _, hc in ipairs(G.hand.cards) do
                    if ("c_" .. (hc.ID or "")) == tid then
                        hc.T.x = base_x + idx * 50
                        if G.hand.add_to_highlighted then
                            G.hand:add_to_highlighted(hc, true)
                        else
                            hc.highlighted = true
                        end
                        break
                    end
                end
            end
        end

        if target_card.can_use_consumeable and not target_card:can_use_consumeable() then
            return { success = false, error = "Consumable cannot be used right now (check required target cards in hand)" }
        end

        target_card._rpc_consumed = true
        G.FUNCS.use_card({ config = { ref_table = target_card } })
        return { success = true }
    elseif method == "select_pack_card" then
        if not (G.pack_cards and G.pack_cards.cards and #G.pack_cards.cards > 0) then
            return { success = false, error = "No booster pack is currently open" }
        end
        if G.GAME.pack_choices and G.GAME.pack_choices <= 0 then
            return { success = false, error = "No pack choices remaining" }
        end
        if G.CONTROLLER and G.CONTROLLER.locks and G.CONTROLLER.locks.use then
            return { success = false, error = "Cannot select card: use animation in progress" }
        end

        local cid = params.card_id or ""
        local target_card = nil
        local p_idx = 0
        for idx, c in ipairs(G.pack_cards.cards) do
            if is_card_valid(c) then
                if ("pack_" .. (c.ID or idx)) == cid or (c.config and c.config.center and c.config.center.key == cid) or (c.ability and c.ability.name == cid) or (p_idx == params.pack_index) then
                    target_card = c
                    break
                end
                p_idx = p_idx + 1
            end
        end

        if not target_card then
            return { success = false, error = "Card not found in booster pack" }
        end
        if not is_card_valid(target_card) then
            return { success = false, error = "Card is already chosen or dissolving" }
        end

        -- Clear any old highlights first
        if G.hand and G.hand.unhighlight_all then
            G.hand:unhighlight_all()
        end

        -- Highlight target hand cards if needed (e.g. Arcana pack Tarot card requiring target cards in hand)
        local pack_target_ids = to_id_table(params.target_card_ids or params.target_cards)
        if #pack_target_ids > 0 and G.hand and G.hand.cards then
            local base_x = (G.hand.T and G.hand.T.x) or 0
            for idx, tid in ipairs(pack_target_ids) do
                for _, hc in ipairs(G.hand.cards) do
                    if ("c_" .. (hc.ID or "")) == tid then
                        hc.T.x = base_x + idx * 50
                        if G.hand.add_to_highlighted then
                            G.hand:add_to_highlighted(hc, true)
                        else
                            hc.highlighted = true
                        end
                        break
                    end
                end
            end
        end

        if target_card.ability and target_card.ability.consumeable and target_card.can_use_consumeable and not target_card:can_use_consumeable() then
            return { success = false, error = "Consumable from pack cannot be used (check required target cards in hand)" }
        end

        -- Prevent exceeding Joker slot limit from Buffoon pack Jokers
        local is_pack_joker = target_card.ability and (target_card.ability.set == 'Joker' or (target_card.config and target_card.config.center and target_card.config.center.set == 'Joker'))
        if is_pack_joker and G.jokers and G.jokers.cards and G.jokers.config and G.jokers.config.card_limit then
            local valid_jokers_count = 0
            for _, j in ipairs(G.jokers.cards) do
                if is_card_valid(j) then valid_jokers_count = valid_jokers_count + 1 end
            end
            if valid_jokers_count >= G.jokers.config.card_limit then
                return { success = false, error = "Cannot take Joker from pack: joker slots are full (" .. tostring(valid_jokers_count) .. "/" .. tostring(G.jokers.config.card_limit) .. ") - skip pack or sell a joker first!" }
            end
        end

        if not G.GAME.PACK_INTERRUPT then
            G.GAME.PACK_INTERRUPT = G.STATES.SHOP
        end

        target_card._rpc_consumed = true
        local ok, err = pcall(function()
            G.FUNCS.use_card({ config = { ref_table = target_card } })
        end)
        if not ok then
            target_card._rpc_consumed = nil
            return { success = false, error = "Failed to use card from pack: " .. tostring(err) }
        end
        return { success = true }
    elseif method == "skip_pack" or method == "skip_booster" then
        local is_in_pack = (G.STATE == G.STATES.TAROT_PACK or G.STATE == G.STATES.PLANET_PACK or 
                            G.STATE == G.STATES.SPECTRAL_PACK or G.STATE == G.STATES.STANDARD_PACK or 
                            G.STATE == G.STATES.BUFFOON_PACK)
        if not is_in_pack and not G.booster_pack then
            return { success = true }
        end
        if G.GAME.pack_choices and G.GAME.pack_choices <= 0 then
            return { success = true }
        end
        if G.booster_pack and (G.booster_pack.REMOVED or (G.booster_pack.alignment and G.booster_pack.alignment.offset and G.booster_pack.alignment.offset.y and G.booster_pack.alignment.offset.y > 0)) then
            return { success = true }
        end
        if not G.GAME.PACK_INTERRUPT then
            G.GAME.PACK_INTERRUPT = G.STATES.SHOP
        end
        local ok, err = pcall(function()
            if G.FUNCS and G.FUNCS.skip_booster then
                local dummy_e = {
                    UIBox = G.booster_pack,
                    config = { button = 'skip_booster' }
                }
                G.FUNCS.skip_booster(dummy_e)
            elseif G.FUNCS and G.FUNCS.end_consumeable then
                G.FUNCS.end_consumeable()
            end
        end)
        return { success = ok, error = (not ok and tostring(err)) or nil }
    elseif method == "sell_joker" then
        if G.CONTROLLER and G.CONTROLLER.locks and G.CONTROLLER.locks.selling_card then
            return { success = false, error = "Cannot sell: sell animation in progress" }
        end
        local jid = params.joker_id or ""
        if G.jokers and G.jokers.cards then
            for idx, j in ipairs(G.jokers.cards) do
                if ("j_" .. (j.ID or idx)) == jid or (j.ability and j.ability.name == jid) then
                    if not is_card_valid(j) then
                        return { success = false, error = "Joker is already sold or dissolving" }
                    end
                    if j.ability and j.ability.eternal then
                        return { success = false, error = "Cannot sell Eternal Joker" }
                    end
                    j._rpc_consumed = true
                    G.FUNCS.sell_card({ config = { ref_table = j } })
                    return { success = true }
                end
            end
        end
        return { success = false, error = "Joker not found: " .. tostring(jid) }
    elseif method == "sell_consumable" then
        if G.CONTROLLER and G.CONTROLLER.locks and G.CONTROLLER.locks.selling_card then
            return { success = false, error = "Cannot sell: sell animation in progress" }
        end
        local cid = params.card_id or ""
        if G.consumeables and G.consumeables.cards then
            for idx, c in ipairs(G.consumeables.cards) do
                if ("cons_" .. (c.ID or idx)) == cid or (c.ability and c.ability.name == cid) then
                    if not is_card_valid(c) then
                        return { success = false, error = "Consumable is already sold or dissolving" }
                    end
                    c._rpc_consumed = true
                    G.FUNCS.sell_card({ config = { ref_table = c } })
                    return { success = true }
                end
            end
        end
        return { success = false, error = "Consumable not found: " .. tostring(cid) }
    end

    return { success = false, error = "Action not executable in current state" }
end

local function safe_send(sock, data)
    if not sock then return false end
    local total = #data
    local sent = 0
    pcall(function() sock:settimeout(0.5) end)
    while sent < total do
        local ok, s, err, last = pcall(function() return sock:send(data, sent + 1) end)
        if not ok then break end
        if s then
            sent = s
        elseif err == "timeout" and last then
            sent = last
        else
            break
        end
    end
    pcall(function() sock:settimeout(0) end)
    return sent == total
end

-- Called inside love.update(dt)
function BalatroBridge.update()
    -- State Guard: Ensure G.STATE never remains nil/broken while in active gameplay
    if G and G.GAME and G.STATES and (G.STATE == nil or G.STATE == 0) then
        if G.shop and not G.shop.REMOVED then
            G.STATE = G.STATES.SHOP
        elseif G.round_eval and not G.round_eval.REMOVED then
            G.STATE = G.STATES.ROUND_EVAL
        elseif G.blind_select and not G.blind_select.REMOVED then
            G.STATE = G.STATES.BLIND_SELECT
        elseif G.booster_pack and not G.booster_pack.REMOVED then
            G.STATE = G.STATES.TAROT_PACK
        end
    end

    if not BalatroBridge.server then return end

    -- Always check for new client: if a new connection arrives, drop any stale client and accept the new one
    local new_client = BalatroBridge.server:accept()
    if new_client then
        if BalatroBridge.client then
            pcall(function() BalatroBridge.client:close() end)
        end
        new_client:settimeout(0)
        BalatroBridge.client = new_client
        BalatroBridge.buffer = ""
    end

    if not BalatroBridge.client then return end

    -- Non-blocking line receive
    local ok, line, err, partial = pcall(function()
        return BalatroBridge.client:receive("*l")
    end)

    if not ok or err == "closed" then
        pcall(function() BalatroBridge.client:close() end)
        BalatroBridge.client = nil
        BalatroBridge.buffer = ""
        return
    end

    if line then
        local full_line = (BalatroBridge.buffer or "") .. line
        BalatroBridge.buffer = ""

        local clean_line = full_line:match("^%s*(.-)%s*$")
        if clean_line and #clean_line > 0 then
            local parse_ok, req = pcall(json.decode, clean_line)
            if parse_ok and type(req) == "table" and req.method then
                local act_ok, res = pcall(BalatroBridge.dispatch_action, req.method, req.params)
                if not act_ok then
                    res = { success = false, error = tostring(res) }
                end

                local resp_obj = {
                    jsonrpc = "2.0",
                    id = req.id,
                    result = res
                }

                local enc_ok, out = pcall(json.encode, resp_obj)
                if enc_ok and out then
                    safe_send(BalatroBridge.client, out .. "\n")
                else
                    safe_send(BalatroBridge.client, string.format('{"jsonrpc":"2.0","id":%s,"error":"Encode error"}\n', tostring(req.id or "null")))
                end
            else
                safe_send(BalatroBridge.client, '{"jsonrpc":"2.0","id":null,"error":"Invalid JSON"}\n')
            end
        end
    elseif partial and #partial > 0 then
        BalatroBridge.buffer = (BalatroBridge.buffer or "") .. partial
    end
end

return BalatroBridge
