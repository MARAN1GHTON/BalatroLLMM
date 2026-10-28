"""JSON Schema Tool / Function Calling definitions for LLMs to control Balatro."""

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# OpenAI & OpenRouter compatible tool specifications
BALATRO_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "play_cards",
            "description": "Submit up to 5 cards from hand to play and score. CRITICAL: Cards score strictly from left to right in the exact order specified in card_ids (e.g. put first face card on the left for Photograph x2 mult, or position Glass cards strategically). Must provide strategic Chain-of-Thought reasoning.",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of 1 to 5 card IDs from current hand to play, ordered left-to-right for sequential scoring",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Chain-of-thought rationale explaining hand choice, expected score, and Boss Blind consideration",
                    },
                },
                "required": ["card_ids", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "discard_cards",
            "description": "Discard up to 5 unhelpful cards to draw replacements from the deck.",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of 1 to 5 card IDs to discard",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why these specific cards are being discarded and what hand is being dug for",
                    },
                },
                "required": ["card_ids", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reorder_jokers",
            "description": "Rearrange active Jokers from left to right. Critical: +Chips and +Mult MUST be placed before xMult.",
            "parameters": {
                "type": "object",
                "properties": {
                    "joker_order": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Complete list of owned Joker IDs ordered from left (position 1) to right (position N)",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Explanation of the new ordering adhering to scoring mathematics (+Mult before xMult)",
                    },
                },
                "required": ["joker_order", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reorder_hand",
            "description": "Rearrange the full order of cards in your hand from left to right. CRITICAL: Cards held in hand trigger left-to-right during scoring (e.g. Shoot the Moon Queens granting +13 Mult should be positioned to the LEFT of Baron Kings or Steel Cards granting x1.5 Mult so flat mult is added before multiplying). Also controls unplayed card ordering.",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_order": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Complete list of all card IDs currently in hand ordered from left to right",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why cards are reordered (e.g. positioning held-in-hand flat mult cards before held-in-hand xMult cards)",
                    },
                },
                "required": ["card_order", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buy_shop_item",
            "description": "Purchase an item (Joker, Tarot, Planet, Pack, Voucher) from the current Shop.",
            "parameters": {
                "type": "object",
                "properties": {
                    "shop_index": {
                        "type": "integer",
                        "description": "Zero-based index of the shop slot to buy",
                    },
                    "sell_joker_id": {
                        "type": "string",
                        "description": "Optional ID of an owned Joker to sell first if Joker slots are full (5/5) or to raise cash for this purchase",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Synergy justification, economic impact, and deck strategy",
                    },
                },
                "required": ["shop_index", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sell_joker",
            "description": "Sell an owned Joker to free up space for a better Joker or generate emergency cash. Note: if selling Luchador to disable a Boss Blind, only sell it while actively fighting the Boss Blind itself.",
            "parameters": {
                "type": "object",
                "properties": {
                    "joker_id": {
                        "type": "string",
                        "description": "ID of the Joker to sell",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for selling (e.g. outscaled, rotating for a stronger synergy/xMult Joker, cash need)",
                    },
                },
                "required": ["joker_id", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reroll_shop",
            "description": "Reroll available shop cards for the stated dollar cost to search for stronger Jokers, Planets, or synergies.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Justification for rerolling (e.g. searching for +Mult/xMult, scaling hand levels, or build synergy)",
                    },
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "end_shop",
            "description": "Leave the Shop and proceed to Blind Selection for the next round. Use this when done shopping or saving money for interest.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why you are leaving the shop (e.g. saving for interest, nothing useful to buy, out of funds)",
                    },
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "select_blind",
            "description": "Choose and initiate the upcoming Small, Big, or Boss Blind.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Readiness assessment for the upcoming encounter",
                    },
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skip_blind",
            "description": "Skip the non-Boss Blind to immediately gain its Tag reward.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why skipping is superior to playing (analyzing the tag value vs shop & interest loss)",
                    },
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reroll_boss_blind",
            "description": "Reroll the Ante Boss Blind for $10 using Director's Cut or Retcon voucher. Use this during BLIND_SELECT if the Boss Blind debuff counters your deck (e.g. The Plant for face cards, The Needle, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Reason for rerolling the Boss Blind and confirmation that bank balance ($10+) allows it",
                    },
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cash_out",
            "description": "Collect round payout and proceed to the Shop phase.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Confirmation to advance to the Shop",
                    },
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "use_consumable",
            "description": "Use an active Tarot, Planet, or Spectral card from your inventory.\n- For Tarots modifying hand cards (Death, Strength, Empress, Hierophant, Hanged Man, Magician, Lovers, Chariot, Justice, Tower, Moon, Sun, Star, World), provide target_card_ids from hand (e.g. for Death: select exactly 2 cards [target_to_convert, template_to_copy]; for Hanged Man: up to 2 cards to destroy).\n- For Spectral cards targeting 1 hand card (Cryptid, Aura, Talisman, Deja Vu, Trance, Medium), provide target_card_ids with exactly 1 card ID (e.g. target_card_ids=['c_123'] to duplicate with Cryptid, add edition with Aura, or add seal).\n- For non-targeting Spectrals (Immolate, Sigil, Ouija, Familiar, Grim, Incantation, Wraith, The Soul, Black Hole, Ankh, Hex, Ectoplasm), Planets, and economy Tarots (Hermit, Temperance), leave target_card_ids empty or omitted.",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_id": {
                        "type": "string",
                        "description": "Consumable card ID to activate (e.g. 'cons_1')",
                    },
                    "target_card_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Target card IDs from current hand for Tarots or Spectrals that modify hand cards",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Strategic reason for using the consumable now and explanation of targets chosen",
                    },
                },
                "required": ["card_id", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sell_consumable",
            "description": "Sell an owned Tarot, Planet, or Spectral card to free up a consumable slot or earn cash.",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_id": {
                        "type": "string",
                        "description": "ID of the consumable to sell (e.g. 'cons_1')",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for selling the consumable (e.g. freeing slot to buy a better Tarot/Planet, or reaching interest breakpoint)",
                    },
                },
                "required": ["card_id", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "select_pack_card",
            "description": "Select and take/use a card from an opened Booster Pack (Arcana, Celestial, Buffoon, Standard, Spectral).\n- If selecting a Tarot or Spectral that modifies hand cards (e.g. Death, Strength, Empress, Hanged Man, Cryptid, Aura, Talisman, Deja Vu, Trance, Medium), provide target_card_ids from your current hand.\n- For all other pack cards (Jokers from Buffoon, Planets from Celestial, Playing cards from Standard, non-targeting Spectrals/Tarots), leave target_card_ids empty.",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_id": {
                        "type": "string",
                        "description": "ID of the card from the booster pack (e.g. 'pack_1')",
                    },
                    "target_card_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of card IDs from current hand to target if selecting a card-modifying Tarot or Spectral",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why this card is chosen from the pack and how any targets were chosen",
                    },
                },
                "required": ["card_id", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skip_pack",
            "description": "Skip remaining selections in the opened Booster Pack without choosing anything.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why skipping the booster pack is better than any available card choices",
                    },
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "python_eval",
            "description": "Execute Python code in an isolated local environment for math, odds calculations, interest simulations, and custom logic before choosing an action. Output is captured and returned to you. Standard modules (math, random, itertools, collections) and state variables (dollars, hands_left, discards_left, target_score, current_score, score_needed, hand, jokers, deck_suits, deck_ranks, deck_specials, deck_cards, deck_remaining) are pre-populated.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Valid Python code to evaluate (expressions like 'score_needed / hands_left' or multi-line scripts)",
                    },
                    "reason": {
                        "type": "string",
                        "description": "What specific math, odds, or strategy question you are computing",
                    },
                },
                "required": ["code", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "simulate_hand",
            "description": "Simulate playing 1 to 5 cards from your current hand before committing to play them. Returns exact detected poker hand, sequential card bonuses, Joker triggers (+Chips, +Mult, xMult), projected total score, and whether it defeats the remaining blind score in 1 hand.",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of 1 to 5 card IDs from current hand to test",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why you are testing this specific combination",
                    },
                },
                "required": ["card_ids", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "simulate_shop_buy",
            "description": "Simulate buying an item from the current shop (and optionally selling an existing Joker) before spending money. Projects net bank balance, interest earned/lost next round, and scoring difference on your primary poker hand with the new Joker loadout.",
            "parameters": {
                "type": "object",
                "properties": {
                    "shop_index": {
                        "type": "integer",
                        "description": "Zero-based index of the shop slot to test",
                    },
                    "sell_joker_id": {
                        "type": "string",
                        "description": "Optional: ID of owned Joker to simulate selling to free up a slot or raise funds",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Trade-off hypothesis you want to verify",
                    },
                },
                "required": ["shop_index", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_hand_odds",
            "description": "Calculate exact combinatorial probabilities of drawing into a target poker hand (e.g. 'Flush', 'Straight', 'Full House', 'Three of a Kind', 'Pair') from the remaining deck after discarding specific card IDs from your hand.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_poker_hand": {
                        "type": "string",
                        "description": "The poker hand type you are digging for (e.g. 'Flush', 'Full House', 'Straight')",
                    },
                    "discard_card_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of card IDs you are considering discarding (up to 5)",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why you want to test digging for this hand",
                    },
                },
                "required": ["target_poker_hand", "reason"],
            },
        },
    },
]

# Dedicated set of local non-game-action calculation & simulation tools
CALCULATION_TOOLS: set[str] = {
    "python_eval",
    "simulate_hand",
    "simulate_shop_buy",
    "calculate_hand_odds",
}

# Dedicated list of pure game-action tools (used when calculation budget is exhausted)
GAME_ACTION_TOOLS: list[dict[str, Any]] = [
    t for t in BALATRO_TOOLS if t.get("function", {}).get("name") not in CALCULATION_TOOLS
]

# Tools where macro-strategic scratchpad (plan_notes) updates are allowed.
# Combat micro-actions (play_cards, discard_cards, reorder_hand, select_pack_card) are excluded
# so tactical hand commentary never overwrites the run's macro strategy.
STRATEGIC_SCRATCHPAD_TOOLS: set[str] = {
    "buy_shop_item",
    "sell_joker",
    "reroll_shop",
    "end_shop",
    "select_blind",
    "skip_blind",
    "reroll_boss_blind",
}

# Attach optional plan_notes property ONLY to macro-strategic Shop / Blind Select tools
for _tool in BALATRO_TOOLS:
    _fn = _tool.get("function", {})
    if _fn.get("name") not in STRATEGIC_SCRATCHPAD_TOOLS:
        continue
    _params = _fn.get("parameters", {})
    _props = _params.get("properties", {})
    if "plan_notes" not in _props:
        _props["plan_notes"] = {
            "type": "string",
            "description": (
                "Optional: Update your persistent macro-strategy note for this Ante "
                "(concise format: '[Engine: ... | Bottleneck/Shop Goal: ... | Boss Counter: ...]'). "
                "Do NOT write single-hand combat descriptions here; only update when your build, economy target, or boss plan changes."
            ),
        }


def convert_tools_for_anthropic(tools: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Transforms OpenAI tool schema format into Anthropic Claude tool schema format."""
    anthropic_tools = []
    source = tools if tools is not None else BALATRO_TOOLS
    for t in source:
        fn = t["function"]
        anthropic_tools.append({
            "name": fn["name"],
            "description": fn["description"],
            "input_schema": fn["parameters"],
        })
    return anthropic_tools


def get_available_tools(
    state: Any = None,
    calculation_turns: int = 0,
    max_calc_turns: int = 8,
) -> list[dict[str, Any]]:
    """Filters available tools dynamically based on game state, remaining resources, and calculation turns.
    
    Hard constraints:
    - If calculation_turns >= max_calc_turns: exclude all CALCULATION_TOOLS.
    - If discards_left <= 0: EXCLUDE 'discard_cards' (strictly prevents illegal discard exploits).
    - If hands_left <= 0: EXCLUDE 'play_cards'.
    - Stage-based tool filtering prevents illegal actions across game phases.
    """
    tools = list(BALATRO_TOOLS)
    if calculation_turns >= max_calc_turns:
        tools = [t for t in tools if t.get("function", {}).get("name") not in CALCULATION_TOOLS]

    if state is None:
        return tools

    stage = getattr(state, "stage", None) or "UNKNOWN"
    discards_left = getattr(state, "discards_left", 0) or 0
    hands_left = getattr(state, "hands_left", 0) or 0

    disallowed_names: set[str] = set()

    # Resource constraints
    if discards_left <= 0:
        disallowed_names.add("discard_cards")
    if hands_left <= 0:
        disallowed_names.add("play_cards")

    # Stage-specific tool filtering
    if stage == "SELECTING_HAND":
        disallowed_names.update({
            "buy_shop_item", "reroll_shop", "end_shop",
            "select_blind", "skip_blind", "reroll_boss_blind",
            "cash_out", "select_pack_card", "skip_pack",
        })
    elif stage == "SHOP":
        disallowed_names.update({
            "play_cards", "discard_cards", "reorder_hand",
            "select_blind", "skip_blind", "reroll_boss_blind",
            "cash_out", "select_pack_card", "skip_pack",
        })
    elif stage == "BLIND_SELECT":
        disallowed_names.update({
            "play_cards", "discard_cards", "reorder_hand",
            "buy_shop_item", "reroll_shop", "end_shop",
            "cash_out", "select_pack_card", "skip_pack",
        })
    elif stage == "ROUND_EVAL":
        disallowed_names.update({
            "play_cards", "discard_cards", "reorder_hand",
            "buy_shop_item", "reroll_shop", "end_shop",
            "select_blind", "skip_blind", "reroll_boss_blind",
            "select_pack_card", "skip_pack",
        })
    elif stage == "OPENING_BOOSTER":
        disallowed_names.update({
            "play_cards", "discard_cards", "reorder_hand",
            "buy_shop_item", "reroll_shop", "end_shop",
            "select_blind", "skip_blind", "reroll_boss_blind",
            "cash_out",
        })

    return [t for t in tools if t.get("function", {}).get("name") not in disallowed_names]


@dataclass
class ToolCallAction:
    """Represents a validated tool execution instruction parsed from an LLM response."""

    name: str
    arguments: dict[str, Any]
    reason: str = ""
    plan_notes: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    calculation_turns: int = 0

    def __post_init__(self) -> None:
        """Normalize numeric tool arguments across OpenAI-compatible providers."""
        if "shop_index" not in self.arguments:
            return
        raw_index = self.arguments["shop_index"]
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            logger.warning("Invalid shop_index %r; using default index 0", raw_index)
            index = 0
        if index != raw_index:
            logger.info("Normalized shop_index from %r to %d", raw_index, index)
        self.arguments["shop_index"] = index
