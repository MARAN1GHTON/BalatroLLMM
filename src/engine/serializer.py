"""Token-dense state serializer formatting Balatro GameState into clean Markdown for LLMs."""

import collections
import math
import re
from ..bridge.schema import GameState, CardModel, JokerModel, PokerHandModel, ShopItemModel
from .scoring import BalatroScoringEngine, POKER_HAND_BASE
from .vouchers import find_voucher_data, format_voucher_impact, format_owned_voucher_line
from .lorebook import get_lorebook_insights_for_state


CARD_ENHANCEMENT_DESCS: dict[str, str] = {
    "bonus card": "Bonus (+30 Chips)",
    "bonus": "Bonus (+30 Chips)",
    "mult card": "Mult (+4 Mult)",
    "mult": "Mult (+4 Mult)",
    "wild card": "Wild (Any Suit)",
    "wild": "Wild (Any Suit)",
    "glass card": "Glass (X2 Mult, 1/4 break)",
    "glass": "Glass (X2 Mult, 1/4 break)",
    "steel card": "Steel (X1.5 Mult held)",
    "steel": "Steel (X1.5 Mult held)",
    "stone card": "Stone (+50 Chips, no rank/suit)",
    "stone": "Stone (+50 Chips, no rank/suit)",
    "gold card": "Gold (+$3 end of round)",
    "gold": "Gold (+$3 end of round)",
    "lucky card": "Lucky (1/5 +20 Mult, 1/15 +$20)",
    "lucky": "Lucky (1/5 +20 Mult, 1/15 +$20)",
}

CARD_EDITION_DESCS: dict[str, str] = {
    "foil": "Foil (+50 Chips)",
    "holographic": "Holo (+10 Mult)",
    "holo": "Holo (+10 Mult)",
    "polychrome": "Poly (X1.5 Mult)",
    "poly": "Poly (X1.5 Mult)",
    "negative": "Negative (+1 Joker Slot)",
}

CARD_SEAL_DESCS: dict[str, str] = {
    "red": "Red Seal (Retrigger 1x)",
    "blue": "Blue Seal (Creates Planet if held)",
    "purple": "Purple Seal (Creates Tarot on discard)",
    "gold": "Gold Seal (+$3 scored)",
}


def format_card_compact(card: CardModel, is_the_mark: bool = False, is_pillar_ante: bool = False) -> str:
    """Format single card into dense representation: [ID: Rank Suit | Mods]."""
    suit_symbols = {
        "Hearts": "♥",
        "Diamonds": "♦",
        "Clubs": "♣",
        "Spades": "♠",
        "Wild": "★Wild",
        "None": "?",
    }
    if card.face_down:
        if is_the_mark or card.rank == "Face":
            return f"[{card.id}: 👑Face(?) | FACE-DOWN (The Mark - J/Q/K, 10 chips, unknown suit)]"
        return f"[{card.id}: ❓(?) | FACE-DOWN (Unknown Card)]"

    symbol = suit_symbols.get(card.suit, card.suit)
    mods: list[str] = []

    if card.enhancement:
        enh_text = CARD_ENHANCEMENT_DESCS.get(card.enhancement.lower(), f"Enh:{card.enhancement}")
        mods.append(enh_text)
    if card.edition:
        ed_text = CARD_EDITION_DESCS.get(card.edition.lower(), f"Ed:{card.edition}")
        mods.append(ed_text)
    if card.seal:
        seal_text = CARD_SEAL_DESCS.get(card.seal.lower(), f"Seal:{card.seal}")
        mods.append(seal_text)
    if card.chip_bonus > 0 and not (card.enhancement and "bonus" in card.enhancement.lower()):
        mods.append(f"+{card.chip_bonus}c")
    if card.mult_bonus > 0 and not (card.enhancement and "mult" in card.enhancement.lower()):
        mods.append(f"+{card.mult_bonus}m")
    if card.x_mult > 1.0 and not (card.enhancement and "glass" in card.enhancement.lower()):
        mods.append(f"x{card.x_mult}m")
    if card.debuffed:
        mods.append("⚠️DEBUFFED")
    elif is_pillar_ante and getattr(card, "played_this_ante", False):
        mods.append("🏛️PLAYED_THIS_ANTE")

    mod_str = f" | {', '.join(mods)}" if mods else ""
    return f"[{card.id}: {card.rank}{symbol}{mod_str}]"


KNOWN_JOKER_CLEAN_DESCS: dict[str, str] = {
    # Scaling xMult Jokers (Crucial: warn about base X1.0 unscaled!)
    "j_constellation": "Gains X0.1 Mult per Planet card used (⚠️ Starts at base X1.0 Mult; gives NO boost until Planet cards are used!)",
    "j_hologram": "Gains X0.25 Mult per playing card added to deck (⚠️ Starts at base X1.0 Mult; gives NO boost until cards are added!)",
    "j_obelisk": "Gains X0.2 Mult per consecutive hand played without playing your most played hand (⚠️ Starts at base X1.0 Mult)",
    "j_vampire": "Gains X0.1 Mult per scoring Enhanced card played, removes card enhancement (⚠️ Starts at base X1.0 Mult)",
    "j_madness": "Gains X0.5 Mult when Small or Big Blind is selected, destroys a random Joker (⚠️ Starts at base X1.0 Mult)",
    "j_campfire": "Gains X0.25 Mult for each card sold, resets when Boss Blind is defeated (⚠️ Starts at base X1.0 Mult)",
    "j_throwback": "Gains X0.25 Mult for each Blind skipped this run (⚠️ Starts at base X1.0 Mult)",
    "j_glass": "Gains X0.75 Mult for every Glass Card that breaks (⚠️ Starts at base X1.0 Mult)",
    "j_lucky_cat": "Gains X0.25 Mult each time a Lucky card successfully triggers (⚠️ Starts at base X1.0 Mult)",
    "j_steel_joker": "Gives X0.2 Mult for each Steel Card in your full deck",
    "j_cavendish": "X3 Mult. 1 in 1000 chance to go extinct at end of round",
    "j_card_sharp": "X3 Mult if played poker hand has already been played this round (X1.0 on first play!)",
    "j_blackboard": "X3 Mult if all unplayed cards held in hand are Spades ♠ or Clubs ♣",
    "j_acrobat": "X3 Mult on final hand of round (X1.0 on earlier hands!)",
    "j_duo": "X2 Mult if played hand contains a Pair",
    "j_the_duo": "X2 Mult if played hand contains a Pair",
    "j_trio": "X3 Mult if played hand contains Three of a Kind",
    "j_the_trio": "X3 Mult if played hand contains Three of a Kind",
    "j_family": "X4 Mult if played hand contains Four of a Kind",
    "j_the_family": "X4 Mult if played hand contains Four of a Kind",
    "j_order": "X3 Mult if played hand contains a Straight",
    "j_the_order": "X3 Mult if played hand contains a Straight",
    "j_tribe": "X2 Mult if played hand contains a Flush",
    "j_the_tribe": "X2 Mult if played hand contains a Flush",
    "j_baron": "Each King held in hand gives X1.5 Mult",
    "j_photograph": "First played face card gives X2 Mult when scored",
    "j_ancient": "Each played card with target suit gives X1.5 Mult when scored (suit changes at end of round)",
    "j_idol": "Each played card of target rank and suit gives X2 Mult when scored (changes every round)",
    "j_seeing_double": "X2 Mult if played hand has a scoring Club ♣ card and a scoring card of any other suit",
    "j_flower_pot": "X3 Mult if poker hand contains a Diamond ♦, Club ♣, Heart ♥, and Spade ♠ card",
    "j_hit_the_road": "Gains X0.5 Mult for every Jack discarded this round (resets at end of round)",
    "j_drivers_license": "X3 Mult if you have at least 16 Enhanced cards in your full deck",
    "j_baseball": "Uncommon Jokers each give X1.5 Mult",
    "j_stencil": "X1 Mult for each empty Joker slot (Joker Stencil included)",
    "j_ramen": "X2 Mult, loses X0.01 Mult per card discarded",

    # Legendary Jokers (Spawned strictly by The Soul)
    "j_caino": "Gains X1 Mult when a face card is destroyed (⚠️ Starts at base X1.0 Mult; destroy face cards to scale!)",
    "j_canio": "Gains X1 Mult when a face card is destroyed (⚠️ Starts at base X1.0 Mult; destroy face cards to scale!)",
    "j_triboulet": "Played Kings and Queens each give X2 Mult when scored (Exponential: up to X32 Mult on 5 face cards!)",
    "j_yorick": "Gains X1 Mult every 23 cards discarded (⚠️ Starts at base X1.0 Mult; discard actively to scale!)",
    "j_chicot": "Disables the effect of every Boss Blind permanently",
    "j_perkeo": "Creates a Negative copy of 1 random consumable in your possession at the end of the shop",

    # Flat Chips & Mult Jokers with placeholders
    "j_joker": "+4 Mult",
    "j_greedy_joker": "Played cards with Diamond ♦ suit give +3 Mult when scored",
    "j_lusty_joker": "Played cards with Heart ♥ suit give +3 Mult when scored",
    "j_wrathful_joker": "Played cards with Spade ♠ suit give +3 Mult when scored",
    "j_gluttonous_joker": "Played cards with Club ♣ suit give +3 Mult when scored",
    "j_sly": "+50 Chips if played hand contains a Pair",
    "j_wily": "+100 Chips if played hand contains Three of a Kind",
    "j_clever": "+80 Chips if played hand contains Two Pair",
    "j_devious": "+100 Chips if played hand contains a Straight",
    "j_crafty": "+80 Chips if played hand contains a Flush",
    "j_jolly": "+8 Mult if played hand contains a Pair",
    "j_zany": "+12 Mult if played hand contains Three of a Kind",
    "j_mad": "+10 Mult if played hand contains Two Pair",
    "j_crazy": "+12 Mult if played hand contains a Straight",
    "j_droll": "+10 Mult if played hand contains a Flush",
    "j_half": "+20 Mult if played hand contains 3 or fewer cards",
    "j_scary_face": "Played face cards (J, Q, K) give +30 Chips when scored",
    "j_smiley_face": "Played face cards (J, Q, K) give +5 Mult when scored",
    "j_chaos": "1 free Reroll per shop",
    "j_fortune_teller": "+1 Mult per Tarot card used this run",
    "j_flash": "+2 Mult per reroll in the shop",
    "j_green_joker": "+1 Mult per hand played, -1 Mult per discard",
    "j_ride_the_bus": "+1 Mult per consecutive hand played without scoring a face card",
    "j_red_card": "+3 Mult when any Booster Pack is skipped",
    "j_supernova": "Adds the number of times played poker hand has been played this run to Mult",
    "j_ice_cream": "+100 Chips, -5 Chips for every hand played",
    "j_popcorn": "+20 Mult, -4 Mult per round played",
    "j_gros_michel": "+15 Mult. 1 in 6 chance to go extinct at end of round",
    "j_even_steven": "Played cards with even rank (2, 4, 6, 8, 10) give +4 Mult when scored",
    "j_odd_todd": "Played cards with odd rank (3, 5, 7, 9, A) give +31 Chips when scored",
    "j_scholar": "Played Aces give +20 Chips and +4 Mult when scored",
    "j_fibonacci": "Played Ace, 2, 3, 5, or 8 gives +8 Mult when scored",
    "j_walkie_talkie": "Played 10s and 4s give +10 Chips and +4 Mult when scored",
    "j_hiker": "Every played card permanently gains +5 Chips when scored",
    "j_blue_joker": "+2 Chips for each remaining card in deck",
    "j_bull": "+2 Chips for each dollar you have in bank",
    "j_bootstraps": "+2 Mult for each $5 you have in bank",
    "j_swashbuckler": "Adds sell value of all other owned Jokers to Mult",
    "j_misprint": "+0 to +23 Mult (random each hand)",
    "j_banner": "+30 Chips for each remaining discard",
    "j_mystic_summit": "+15 Mult when 0 discards remain",
    "j_loyalty_card": "X4 Mult every 6 hands played",
    "j_stone_joker": "+25 Chips for each Stone Card in your full deck",
    "j_stone": "+25 Chips for each Stone Card in your full deck",
    "j_arrowhead": "Played cards with Spade ♠ suit give +50 Chips when scored",
    "j_onyx_agate": "Played cards with Club ♣ suit give +7 Mult when scored",
    "j_bloodstone": "1 in 2 chance for played Heart ♥ cards to give X1.5 Mult when scored",
    "j_rough_gem": "Played cards with Diamond ♦ suit earn $1 when scored",
    "j_erosion": "+4 Mult for each card below starting deck size",
    # Common Utility, Econ & Scaling Chip Jokers
    "j_splash": "Every played card counts in scoring (even non-combo kickers)",
    "j_luchador": "Sell this card during a Boss Blind to disable the active Boss Blind",
    "j_todo_list": "Earn $4 if played poker hand matches the target hand (changes at end of round)",
    "j_faceless": "Earn $5 if 3 or more face cards (J, Q, K) are discarded at the same time",
    "j_runner": "Gains +15 Chips permanently if played hand contains a Straight",
    "j_square": "Gains +4 Chips permanently if played hand has exactly 4 cards",
    "j_wee": "Gains +8 Chips permanently when each played 2 is scored",
    "j_castle": "Gains +3 Chips per discarded card of target suit (suit changes each round)",
    "j_stuntman": "+250 Chips, -2 hand size",
    "j_shortcut": "Allows Straights to be made with gaps of 1 rank (e.g. 10, 8, 6, 5, 3)",
    "j_four_fingers": "All Flushes and Straights can be made with 4 cards",
    "j_pareidolia": "All cards are considered face cards",
    "j_smeared": "Hearts and Diamonds count as the same suit; Spades and Clubs count as the same suit",
    "j_hanging_chad": "Retrigger first played card used in scoring 2 additional times",
    "j_sock_and_buskin": "Retrigger all played face cards (J, Q, K)",
    "j_hack": "Retrigger each played 2, 3, 4, or 5",
    "j_dusk": "Retrigger all played cards in final hand of round",
    "j_mime": "Retrigger all card held in hand abilities (Steel cards, Baron, Shoot the Moon, Seals)",
    "j_selzer": "Retrigger all cards played for the next 10 hands",
    "j_seltzer": "Retrigger all cards played for the next 10 hands",
    "j_blueprint": "Copies ability of Joker to the right",
    "j_brainstorm": "Copies ability of leftmost Joker",
    "j_juggler": "+1 hand size",
    "j_drunkard": "+1 discard each round",
    "j_troubadour": "+2 hand size, -1 hand each round",
    "j_turtle_bean": "+5 hand size, reduces by 1 every round",
    "j_merry_andy": "+3 discards each round, -1 hand size",
    "j_burglar": "When Blind is selected, gain +3 Hands and lose all discards",
    "j_riff_raff": "When Blind is selected, create 2 Common Jokers (must have room)",
    "j_hallucination": "1 in 2 chance to create a Tarot card when any Booster Pack is opened",
    "j_space": "1 in 4 chance to upgrade level of played poker hand",
    "j_burnt": "Upgrade the level of the first discarded poker hand each round",
    "j_dna": "If first hand of round has only 1 card, add a permanent copy to deck and draw it to hand",
    "j_sixth_sense": "If first hand of round is a single 6, destroy it and create a Spectral card",
    "j_vagabond": "Create a Tarot card if hand is played with $4 or less",
    "j_superposition": "Create a Tarot card if poker hand contains an Ace and a Straight",
    "j_seance": "If poker hand is a Straight Flush, create a random Spectral card",
    "j_8_ball": "1 in 4 chance for each played 8 to create a Tarot card when scored",
    "j_cartomancer": "Create a Tarot card when Blind is selected",
    "j_astronomer": "All Planet cards and Celestial Packs in the shop are free ($0)",
    "j_marble": "Adds 1 Stone card to deck when Blind is selected",
    "j_certificate": "When round begins, add a random playing card with a random seal to your hand",
    "j_mr_bones": "Prevents Death if chips scored are at least 25% of required chips (self-destructs)",
    "j_invisible": "After 2 rounds, sell this card to Duplicate a random Joker",
    "j_ring_master": "Joker, Tarot, Planet, and Spectral cards may appear multiple times",
    "j_diet_cola": "Sell this card to create a free Double Tag",
    "j_oops": "Doubles all listed probabilities (e.g. 1 in 4 becomes 2 in 4)",
    "j_business": "Played face cards have a 1 in 2 chance to give $2 when scored",
    "j_mail": "Earn $5 for each discarded card of target rank (rank changes each round)",
    "j_golden": "Earn $4 at end of round",
    "j_delayed_grat": "Earn $2 per discard if no discards are used by end of round",
    "j_credit_card": "Go up to -$20 in debt",
    "j_egg": "Gains $3 of sell value at end of round",
    "j_to_the_moon": "Earn an extra $1 of interest for every $5 you have at end of round",
    "j_cloud_9": "Earn $1 for each 9 in your full deck at end of round",
    "j_rocket": "Earn $1 at end of round. Payout increases by $2 when Boss Blind is defeated",
    "j_gift": "Adds $1 of sell value to every Joker and Consumable card at end of round",
    "j_reserved_parking": "Each face card held in hand has a 1 in 2 chance to give $1",
    "j_midas_mask": "All played face cards become Gold cards when scored",
    "j_trading": "If first discard of round has only 1 card, destroy it and earn $3",
    "j_ticket": "Played Gold cards earn $4 when scored",
    "j_matador": "Earn $8 if played hand triggers the Boss Blind ability",
    "j_satellite": "Earn $1 at end of round per unique Planet card used this run",
    "j_abstract": "+3 Mult for each Joker card currently owned",
    "j_raised_fist": "Adds double the rank of lowest ranked card held in hand to Mult",
    "j_shoot_the_moon": "Each Queen held in hand gives +13 Mult",
    "j_spare_trousers": "Gains +2 Mult if played hand contains a Two Pair",
    "j_trousers": "Gains +2 Mult if played hand contains a Two Pair",
}

HYBRID_JOKER_IDENTIFIERS: set[str] = {
    "j_scholar", "scholar",
    "j_walkie_talkie", "walkie talkie",
}

UTILITY_JOKER_IDENTIFIERS: set[str] = {
    "j_splash", "splash",
    "j_luchador", "luchador",
    "j_shortcut", "shortcut",
    "j_four_fingers", "four fingers",
    "j_pareidolia", "pareidolia",
    "j_smeared", "smeared joker",
    "j_oops", "oops! all 6s",
    "j_hanging_chad", "hanging chad",
    "j_sock_and_buskin", "sock and buskin",
    "j_hack", "hack",
    "j_dusk", "dusk",
    "j_mime", "mime",
    "j_selzer", "seltzer",
    "j_juggler", "juggler",
    "j_drunkard", "drunkard",
    "j_troubadour", "troubadour",
    "j_turtle_bean", "turtle bean",
    "j_merry_andy", "merry andy",
    "j_burglar", "burglar",
    "j_dna", "dna",
    "j_sixth_sense", "sixth sense",
    "j_seance", "séance", "seance",
    "j_superposition", "superposition",
    "j_vagabond", "vagabond",
    "j_8_ball", "8 ball",
    "j_cartomancer", "cartomancer",
    "j_astronomer", "astronomer",
    "j_burnt", "burnt joker",
    "j_space", "space joker",
    "j_marble", "marble joker",
    "j_certificate", "certificate",
    "j_hallucination", "hallucination",
    "j_chaos", "chaos the clown",
    "j_mr_bones", "mr. bones",
    "j_invisible", "invisible joker",
    "j_ring_master", "showman",
    "j_blueprint", "blueprint",
    "j_brainstorm", "brainstorm",
    "j_chicot", "chicot",
    "j_perkeo", "perkeo",
    "j_diet_cola", "diet cola",
}

ECON_JOKER_IDENTIFIERS: set[str] = {
    "j_todo_list", "to do list",
    "j_faceless", "faceless joker",
    "j_business", "business card",
    "j_mail", "mail-in rebate",
    "j_golden", "golden joker",
    "j_cloud_9", "cloud 9",
    "j_rocket", "rocket",
    "j_delayed_grat", "delayed gratification",
    "j_egg", "egg",
    "j_gift", "gift card",
    "j_reserved_parking", "reserved parking",
    "j_to_the_moon", "to the moon",
    "j_midas_mask", "midas mask",
    "j_trading", "trading card",
    "j_ticket", "golden ticket",
    "j_matador", "matador",
    "j_satellite", "satellite",
    "j_rough_gem", "rough gem",
    "j_credit_card", "credit card",
}

CHIP_JOKER_IDENTIFIERS: set[str] = {
    "j_runner", "runner",
    "j_ice_cream", "ice cream",
    "j_blue_joker", "blue joker",
    "j_banner", "banner",
    "j_scary_face", "scary face",
    "j_odd_todd", "odd todd",
    "j_sly", "sly joker",
    "j_wily", "wily joker",
    "j_clever", "clever joker",
    "j_devious", "devious joker",
    "j_crafty", "crafty joker",
    "j_hiker", "hiker",
    "j_bull", "bull",
    "j_stone_joker", "stone joker", "j_stone",
    "j_arrowhead", "arrowhead",
    "j_wee", "wee joker",
    "j_castle", "castle",
    "j_square", "square joker",
    "j_stuntman", "stuntman",
}

XMULT_JOKER_IDENTIFIERS: set[str] = {
    "j_photograph", "photograph",
    "j_bloodstone", "bloodstone",
    "j_loyalty_card", "loyalty card",
    "j_steel_joker", "steel joker",
    "j_stencil", "joker stencil",
    "j_ramen", "ramen",
    "j_baseball", "baseball card",
    "j_flower_pot", "flower pot",
    "j_blackboard", "blackboard",
    "j_cavendish", "cavendish",
    "j_card_sharp", "card sharp",
    "j_constellation", "constellation",
    "j_duo", "j_the_duo", "the duo",
    "j_trio", "j_the_trio", "the trio",
    "j_family", "j_the_family", "the family",
    "j_order", "j_the_order", "the order",
    "j_tribe", "j_the_tribe", "the tribe",
    "j_baron", "baron",
    "j_obelisk", "obelisk",
    "j_vampire", "vampire",
    "j_madness", "madness",
    "j_drivers_license", "driver's license",
    "j_caino", "j_canio", "canio",
    "j_triboulet", "triboulet",
    "j_yorick", "yorick",
    "j_idol", "the idol",
    "j_ancient", "ancient joker",
    "j_hologram", "hologram",
    "j_seeing_double", "seeing double",
    "j_hit_the_road", "hit the road",
    "j_glass", "glass joker",
    "j_lucky_cat", "lucky cat",
    "j_campfire", "campfire",
    "j_throwback", "throwback",
    "j_acrobat", "acrobat",
}


def get_joker_role(j: JokerModel) -> str:
    """Deterministically classifies a Joker into its true functional role:
    'x_mult', 'hybrid', 'chips', 'econ', 'utility', or 'mult'.
    Prevents Lua's fallback 'mult' default from misclassifying utility/econ/chip jokers.
    """
    k = (getattr(j, "key", "") or "").strip().lower()
    lbl = (getattr(j, "label", "") or "").strip().lower()

    if k in HYBRID_JOKER_IDENTIFIERS or lbl in HYBRID_JOKER_IDENTIFIERS:
        return "hybrid"
    if k in UTILITY_JOKER_IDENTIFIERS or lbl in UTILITY_JOKER_IDENTIFIERS:
        return "utility"
    if k in ECON_JOKER_IDENTIFIERS or lbl in ECON_JOKER_IDENTIFIERS:
        return "econ"
    if (
        k in XMULT_JOKER_IDENTIFIERS
        or lbl in XMULT_JOKER_IDENTIFIERS
        or getattr(j, "effect_type", "") == "x_mult"
        or (getattr(j, "x_mult", 1.0) or 1.0) > 1.0
    ):
        return "x_mult"
    if (
        k in CHIP_JOKER_IDENTIFIERS
        or lbl in CHIP_JOKER_IDENTIFIERS
        or getattr(j, "effect_type", "") == "chips"
        or (getattr(j, "chips", 0) or 0) > 0
    ):
        return "chips"

    eff = getattr(j, "effect_type", "mult") or "mult"
    return eff if eff in ("chips", "mult", "x_mult", "econ", "utility", "hybrid") else "mult"


def clean_joker_desc(
    key: str = "",
    label: str = "",
    raw_desc: str = "",
    effect_type: str = "",
    x_mult: float = 1.0,
    mult: int = 0,
    chips: int = 0,
    target_suit: str | None = None,
    target_rank: str | None = None,
    target_hand: str | None = None,
) -> str:
    """Sanitizes raw Balatro localization strings, eliminating raw '#1#', '#2#' placeholders
    and crystalizing base vs scaled values and rotating targets for Jokers."""
    k = (key or "").strip().lower()
    lbl = (label or "").strip().lower()

    # Match by key or longest label substring
    clean_base: str | None = None
    if k in KNOWN_JOKER_CLEAN_DESCS:
        clean_base = KNOWN_JOKER_CLEAN_DESCS[k]
    elif lbl == "joker":
        clean_base = KNOWN_JOKER_CLEAN_DESCS["j_joker"]
    else:
        for jk, desc in sorted(KNOWN_JOKER_CLEAN_DESCS.items(), key=lambda x: len(x[0]), reverse=True):
            if jk == "j_joker":
                continue
            core_name = jk.replace("j_", "").replace("_", " ")
            if core_name and (core_name in lbl or (lbl and len(lbl) > 3 and lbl in core_name)):
                clean_base = desc
                break

    if clean_base:
        if target_suit and target_rank:
            clean_base += f" [🎯 Active Target: {target_rank} of {target_suit}]"
        elif target_suit:
            clean_base += f" [🎯 Active Target Suit: {target_suit}]"
        elif target_rank:
            clean_base += f" [🎯 Active Target Rank: {target_rank}]"
        elif target_hand:
            clean_base += f" [🎯 Active Target Hand: {target_hand}]"

        if x_mult > 1.05:
            if "⚠️" in clean_base:
                clean_base = re.sub(r"\(⚠️[^)]+\)", f"(🔥 CURRENTLY SCALED: X{x_mult:g} Mult!)", clean_base)
            elif f"X{x_mult:g}" not in clean_base:
                clean_base += f" (🔥 CURRENTLY: X{x_mult:g} Mult)"
        if mult > 0 and (
            k in ("j_green_joker", "j_ride_the_bus", "j_trousers", "j_spare_trousers", "j_red_card", "j_fortune_teller", "j_flash", "j_erosion", "j_abstract", "j_swashbuckler", "j_bootstraps", "j_popcorn", "j_supernova")
            or any(w in lbl for w in ("green joker", "ride the bus", "spare trousers", "red card", "fortune teller", "flash card", "erosion", "abstract", "swashbuckler", "bootstraps", "popcorn", "supernova"))
        ):
            if "popcorn" in lbl or k == "j_popcorn":
                clean_base = re.sub(r"\+\d+ Mult", f"+{mult} Mult", clean_base)
            else:
                clean_base += f" (Currently: +{mult} Mult)"
        if chips > 0 and (
            k in ("j_runner", "j_castle", "j_square", "j_wee", "j_stone", "j_stone_joker", "j_blue_joker", "j_bull", "j_banner", "j_ice_cream")
            or any(w in lbl for w in ("runner", "castle", "square joker", "wee joker", "stone joker", "blue joker", "bull", "banner", "ice cream"))
        ):
            if "ice cream" in lbl or k == "j_ice_cream":
                clean_base = re.sub(r"\+\d+ Chips", f"+{chips} Chips", clean_base)
            else:
                clean_base += f" (Currently: +{chips} Chips)"
        return clean_base

    # Fallback: clean raw_desc with regex
    desc = raw_desc or ""
    if target_suit:
        desc = desc.replace("#2#", str(target_suit))
    if target_rank:
        desc = desc.replace("#2#", str(target_rank))
    if target_hand:
        desc = desc.replace("#2#", str(target_hand))
    desc = re.sub(r"\(Сейчас:[^)]*x#\d+#[^)]*\)", "(Сейчас: базовый множ. X1.0 unscaled)", desc, flags=re.I)
    desc = re.sub(r"[Xx]#\d+#", "X1.0 (unscaled)", desc)
    desc = re.sub(r"\+#\d+#", "+Bonus", desc)
    desc = re.sub(r"#\d+#", "", desc)
    desc = re.sub(r"\s+", " ", desc).strip()

    is_scaling_xmult = any(w in lbl for w in ("constellation", "hologram", "obelisk", "vampire", "madness", "campfire", "throwback", "lucky cat", "glass"))
    if is_scaling_xmult:
        if x_mult > 1.05:
            desc += f" (🔥 CURRENTLY SCALED: X{x_mult:g} Mult!)"
        else:
            desc += " (⚠️ CURRENTLY BASE X1.0 UNSCALED — gives NO mult boost until scaled!)"

    return desc or "(Standard Joker effect)"


def format_joker_compact(index: int, joker: JokerModel) -> str:
    """Format single joker with effect category and order."""
    role = get_joker_role(joker)
    type_badge = {
        "chips": "[+Chips]",
        "mult": "[+FlatMult]",
        "hybrid": "[+Chips/+FlatMult]",
        "x_mult": "[xMultiMult]",
        "econ": "[$Econ]",
        "utility": "[*Util]",
    }.get(role, "[Joker]")

    ed_badge = ""
    if getattr(joker, "edition", None):
        ed_desc = CARD_EDITION_DESCS.get(joker.edition.lower(), joker.edition)
        ed_badge = f" **[{ed_desc.upper()}]**"

    pin_badge = " [PINNED]" if joker.pinned else ""
    eternal_badge = " [🔒ETERNAL - Cannot sell]" if joker.eternal else ""
    perish_badge = f" [⏳PERISHABLE - {joker.perish_rounds_left}r left]" if joker.perishable else ""
    rental_badge = " [💸RENTAL - $3/round]" if joker.rental else ""
    debuff_badge = " [🚨DEBUFFED / INACTIVE!]" if joker.debuffed else ""

    desc = clean_joker_desc(
        key=joker.key,
        label=joker.label,
        raw_desc=joker.effect_desc,
        effect_type=role,
        x_mult=getattr(joker, "x_mult", 1.0),
        mult=getattr(joker, "mult", 0),
        chips=getattr(joker, "chips", 0),
        target_suit=getattr(joker, "target_suit", None),
        target_rank=getattr(joker, "target_rank", None),
        target_hand=getattr(joker, "target_hand", None),
    )

    return (
        f"{index + 1}. `{joker.id}` {joker.label} {type_badge}{ed_badge} ({joker.rarity}) - "
        f"{desc} (Sell: ${joker.sell_cost}){pin_badge}{eternal_badge}{perish_badge}{rental_badge}{debuff_badge}"
    )


def check_joker_ordering_warnings(jokers: list[JokerModel]) -> list[str]:
    """Detect if +Mult is positioned after xMult in the current joker arrangement."""
    warnings: list[str] = []
    seen_xmult = False
    first_xmult_name = ""

    # Card-level xMult triggers (Phase 1/2) happen before board-level Joker bar triggers (Phase 3)
    card_phase_xmult = {
        "j_photograph", "photograph",
        "j_triboulet", "triboulet",
        "j_bloodstone", "bloodstone",
        "j_ancient", "ancient joker",
        "j_idol", "the idol",
        "j_baron", "baron",
    }

    for j in jokers:
        role = get_joker_role(j)
        k_low = (j.key or "").lower()
        l_low = (j.label or "").lower()
        if role == "x_mult" and k_low not in card_phase_xmult and l_low not in card_phase_xmult:
            seen_xmult = True
            first_xmult_name = j.label
        elif role in ("mult", "hybrid") and seen_xmult:
            warnings.append(
                f"Suboptimal Joker Order: Flat Mult '{j.label}' is placed AFTER '{first_xmult_name}' (xMult). "
                f"Move '{j.label}' to the left of '{first_xmult_name}' using reorder_jokers!"
            )
    return warnings


def check_build_balance(state: GameState) -> list[str]:
    """Evaluates the 'Three-Legged Stool' of Balatro scoring: Chips, Flat Mult, and xMult.
    Alerts the LLM if there is a fatal imbalance (e.g. massive xMult with no Chips base)."""
    if not state.jokers or state.ante < 2:
        return []

    warnings: list[str] = []
    chip_jokers = [j for j in state.jokers if not j.debuffed and get_joker_role(j) in ("chips", "hybrid")]
    flat_mult_jokers = [j for j in state.jokers if not j.debuffed and get_joker_role(j) in ("mult", "hybrid")]
    xmult_jokers = [j for j in state.jokers if not j.debuffed and (get_joker_role(j) == "x_mult" or is_xmult_joker(j))]

    # 1. Chip Deficit (Chip-Starved Alert)
    # Occurs when player has xMult or heavy flat Mult, but ZERO Chip Jokers.
    has_heavy_mult = len(xmult_jokers) > 0 or len(flat_mult_jokers) >= 2
    if has_heavy_mult and len(chip_jokers) == 0:
        mult_desc_items = []
        for j in (flat_mult_jokers + xmult_jokers):
            if get_joker_role(j) == "x_mult" or is_xmult_joker(j):
                mult_desc_items.append(f"`{j.label}` (xMult)")
            else:
                m_val = getattr(j, "mult", 0)
                m_str = f"+{m_val} Mult" if m_val > 0 else "+Mult"
                mult_desc_items.append(f"`{j.label}` ({m_str})")
        summary_mult = ", ".join(mult_desc_items[:3])
        warnings.append(
            f"⚠️ **CHIP-STARVED ALERT (BUILD IMBALANCE)**:\n"
            f"> • Your jokers provide heavy multiplier power ({summary_mult}), but **+0 BONUS CHIPS**!\n"
            f"> • Without a Chip Joker, base card chips average only ~30-40 chips (`~35 chips × High Mult`).\n"
            f"> • 💡 **CRITICAL MATH**: Adding ANY +Chips Joker (e.g. Ice Cream, Blue Joker, Banner, Scary Face, Odd Todd, Sly/Crafty/Clever +50-100 chips) "
            f"will multiply your total scoring output by **+200% to +350%**! Prioritize buying a +Chips Joker (or Bonus/Foil cards) in the shop."
        )

    # 2. Mult Deficit (Heavy Chips, Zero Mult)
    if len(chip_jokers) >= 2 and len(flat_mult_jokers) == 0 and len(xmult_jokers) == 0:
        warnings.append(
            f"ℹ️ **BUILD BALANCE NOTE (MULT DEFICIT)**:\n"
            f"> • Status: Chip Jokers active, with 0 +Mult or xMult Jokers in build.\n"
            f"> • Math note: `Final Score = Chips × Mult`. High chips with base ~4 Mult gives limited total score (~1,200). +Mult or xMult scales total score."
        )

    # 3. Late-Game xMult Deficit (Ante 5+)
    if state.ante >= 5:
        has_major_xmult = len(xmult_jokers) >= 2 or any(
            k in (j.label or "").lower() for j in xmult_jokers for k in ("constellation", "hologram", "cavendish", "card sharp")
        )
        if not has_major_xmult and state.dollars >= 30:
            warnings.append(
                f"📊 **LATE-GAME SCALING NOTICE (Ante {state.ante})**:\n"
                f"> • Status: {len(xmult_jokers)} xMult Joker ({', '.join(j.label for j in xmult_jokers) or 'None'}).\n"
                f"> • Boss Scaling: Blinds scale to 30k – 50k+ chips! Flat +Mult plateaus without multipliers.\n"
                f"> • Capital: Bank is ${state.dollars} (interest maxed at $5/rnd). Surplus capital can fund shop rerolls to locate multiplicative Jokers."
            )

    return warnings


HAND_LOCKED_JOKERS = {
    # Two Pair
    "j_clever": ("Two Pair", "+80 Chips"),
    "j_mad": ("Two Pair", "+10 Mult"),
    "j_spare_trousers": ("Two Pair", "Gains +2 Mult if hand contains Two Pair"),
    # Pair
    "j_sly": ("Pair", "+50 Chips"),
    "j_jolly": ("Pair", "+8 Mult"),
    "j_the_duo": ("Pair", "x2 Mult"),
    # Three of a Kind
    "j_wily": ("Three of a Kind", "+100 Chips"),
    "j_zany": ("Three of a Kind", "+12 Mult"),
    "j_the_trio": ("Three of a Kind", "x3 Mult"),
    # Four of a Kind
    "j_the_family": ("Four of a Kind", "x4 Mult"),
    # Straight
    "j_devious": ("Straight", "+100 Chips"),
    "j_crazy": ("Straight", "+12 Mult"),
    "j_the_order": ("Straight", "x3 Mult"),
    "j_runner": ("Straight", "Gains +15 Chips if hand contains Straight"),
    "j_superposition": ("Straight", "Create Tarot if Straight contains Ace"),
    # Flush
    "j_crafty": ("Flush", "+80 Chips"),
    "j_droll": ("Flush", "+10 Mult"),
    "j_the_tribe": ("Flush", "x2 Mult"),
    # Full House
    "j_half": ("Three or fewer cards", "+20 Mult"),
}


def check_hand_locked_jokers(state: GameState, primary_hand: str) -> list[str]:
    """Checks if active Jokers require specific hand types that differ from the current plan or hand."""
    locked = {}
    for j in state.jokers:
        k = (j.key or "").lower()
        lbl = (j.label or "").lower()
        for key, (h_type, bonus_txt) in HAND_LOCKED_JOKERS.items():
            if k == key or key.replace("j_", "").replace("_", " ") in lbl:
                locked[j.label] = (h_type, bonus_txt)
                break
    alerts = []
    if locked:
        hand_types_supported = {h_type for h_type, _ in locked.values()}
        summary = ", ".join(f"**{j_name}** (requires *{h_type}* for {bonus})" for j_name, (h_type, bonus) in locked.items())
        alerts.append(f"🔒 **HAND-LOCKED JOKERS IN BUILD**: {summary}.")
        if primary_hand and primary_hand not in hand_types_supported:
            alerts.append(
                f"⚠️ **SYNERGY MISMATCH**: Your primary hand is **{primary_hand}**, but you have Jokers locked to **{', '.join(hand_types_supported)}**! "
                f"Playing {primary_hand} will DEACTIVATE these jokers (+0 effect)! Either build towards {', '.join(hand_types_supported)} or replace them in the shop."
            )
    return alerts


def get_conditional_joker_statuses(state: GameState, loyalty_remaining: int | None = None) -> list[str]:
    """Generates real-time live status reminders for conditional and trigger-based Jokers."""
    notes: list[str] = []
    if not state.jokers:
        return notes

    # 1. Blackboard Live Status
    has_blackboard = any("blackboard" in (j.key or "").lower() or "blackboard" in (j.label or "").lower() for j in state.jokers)
    if has_blackboard and state.stage in ("SELECTING_HAND", "PLAYING_HAND", "OPENING_BOOSTER"):
        red_cards = [c for c in state.hand if c.suit in ("Hearts", "Diamonds")]
        if red_cards:
            red_list = ", ".join(format_card_compact(c) for c in red_cards)
            notes.append(
                f"> 🖤 **BLACKBOARD LIVE STATUS**: ❌ **INACTIVE (0x Boost)** — Holding {len(red_cards)} Red card(s): {red_list}!\n"
                f">   - Rule: ALL cards remaining unplayed / held in hand MUST be Spades ♠ or Clubs ♣ for Blackboard's **X3 Mult** to trigger.\n"
                f">   - 👉 **HOW TO ACTIVATE X3 MULT**: You MUST either DISCARD all {len(red_cards)} red card(s), OR PLAY them in your scored hand "
                f"(as scoring cards or junk kickers) so that ONLY black cards remain held in hand!"
            )
        else:
            notes.append(
                f"> 🖤 **BLACKBOARD LIVE STATUS**: ✅ **ACTIVE (X3 Mult)!** All {len(state.hand)} cards currently in hand are black (♠/♣). "
                f"As long as unplayed cards stay all-black, you will receive the full X3 Mult!"
            )

    # 2. Loyalty Card Status & Cycle Tracker
    has_loyalty = any("loyalty" in (j.key or "").lower() or "loyalty" in (j.label or "").lower() for j in state.jokers)
    if has_loyalty:
        total_hands_played = sum(h.played for h in state.poker_hands) if state.poker_hands else 0
        if loyalty_remaining is not None:
            rem = loyalty_remaining
            step = 6 - rem if rem > 0 else 6
        else:
            step = (total_hands_played % 6) + 1
            rem = (6 - step) % 6

        if rem == 0 or step == 6:
            loyalty_text = "🔥 **ACTIVE ON THIS HAND!** (X4 Mult will trigger on this played hand!)"
        else:
            loyalty_text = f"⏳ **INACTIVE** ({rem} hand{'s' if rem > 1 else ''} left until X4 Mult triggers, currently Hand {step}/6 in cycle)"

        notes.append(
            f"> 💳 **LOYALTY CARD TRACKER**: {loyalty_text}\n"
            f">   - Periodic Rule: X4 Mult triggers on EVERY 6TH HAND PLAYED (Total hands played this run: {total_hands_played}).\n"
            f">   - ⚠️ **TACTICAL WARNING**: When inactive, Loyalty Card gives ZERO multiplier boost (X1)! "
            f"Do NOT waste high-value consumable resources or risk your run expecting X4 on a non-trigger turn!"
        )

    # 3. Mystic Summit (+15 Mult at 0 discards)
    has_mystic = any("mystic summit" in (j.label or "").lower() or "mystic_summit" in (j.key or "").lower() for j in state.jokers)
    if has_mystic and state.stage in ("SELECTING_HAND", "PLAYING_HAND"):
        if state.discards_left == 0:
            notes.append("> 🏔️ **MYSTIC SUMMIT STATUS**: ✅ **ACTIVE (+15 Mult)!** You have 0 discards remaining.")
        else:
            notes.append(f"> 🏔️ **MYSTIC SUMMIT STATUS**: ⏳ **INACTIVE** (+0 Mult). Requires 0 discards left (currently {state.discards_left} discards left). Use discards first if you need this +15 Mult!")

    # 4. Acrobat (X3 Mult on final hand of round)
    has_acrobat = any("acrobat" in (j.label or "").lower() or "acrobat" in (j.key or "").lower() for j in state.jokers)
    if has_acrobat and state.stage in ("SELECTING_HAND", "PLAYING_HAND"):
        if state.hands_left == 1:
            notes.append("> 🤸 **ACROBAT STATUS**: ✅ **ACTIVE (X3 Mult)!** This is your FINAL hand of the round!")
        else:
            notes.append(f"> 🤸 **ACROBAT STATUS**: ⏳ **INACTIVE** (Triggers X3 Mult ONLY on final hand of round, currently {state.hands_left} hands left).")

    # 5. Ancient Joker (X1.5 per card of rotating suit)
    ancient_joker = next((j for j in state.jokers if "ancient" in (j.key or "").lower() or "ancient" in (j.label or "").lower()), None)
    if ancient_joker:
        suit_match = None
        if ancient_joker.target_suit:
            ts = ancient_joker.target_suit.lower()
            if "heart" in ts or "черв" in ts:
                suit_match = "Hearts ♥"
            elif "diamond" in ts or "бубн" in ts:
                suit_match = "Diamonds ♦"
            elif "club" in ts or "треф" in ts:
                suit_match = "Clubs ♣"
            elif "spade" in ts or "пик" in ts:
                suit_match = "Spades ♠"
            else:
                suit_match = ancient_joker.target_suit
        if not suit_match and ancient_joker.effect_desc:
            m = re.search(r"\b(Hearts|Diamonds|Clubs|Spades|черв[ыа]|бубн[ыа]|треф[ыа]|пик[иа])\b", ancient_joker.effect_desc, re.I)
            if m:
                raw_s = m.group(1).lower()
                if "черв" in raw_s or "heart" in raw_s:
                    suit_match = "Hearts ♥"
                elif "бубн" in raw_s or "diamond" in raw_s:
                    suit_match = "Diamonds ♦"
                elif "треф" in raw_s or "club" in raw_s:
                    suit_match = "Clubs ♣"
                elif "пик" in raw_s or "spade" in raw_s:
                    suit_match = "Spades ♠"
        if suit_match:
            notes.append(
                f"> 🎲 **ANCIENT JOKER ACTIVE TARGET SUIT**: 🎯 **{suit_match}**! "
                f"Each played card of {suit_match} triggers **X1.5 Mult**! "
                f"(A 5-card {suit_match} Flush triggers X1.5^5 ≈ **X7.6 Mult**!)"
            )
        else:
            notes.append(
                "> 🎲 **ANCIENT JOKER ROTATION WARNING**: Ancient Joker's target suit rotates **EVERY ROUND** (X1.5 per card of target suit).\n"
                ">   • ⚠️ **CRITICAL TACTICAL RULE**: If your played cards do NOT match the target suit, Ancient Joker gives **1.0x (ZERO BOOST)**!\n"
                ">   • NEVER discard your entire hand gambling on an unknown suit. Rely on leveled base hands (Jupiter) and guaranteed mult!"
            )

    # 6. Mail-In Rebate (+$5 per discarded rank)
    mail_joker = next((j for j in state.jokers if "mail" in (j.key or "").lower() or "mail" in (j.label or "").lower()), None)
    if mail_joker:
        target_rank = mail_joker.target_rank
        if not target_rank and mail_joker.effect_desc:
            m = re.search(r"\b(Ace|King|Queen|Jack|10|[2-9]|туз|корол[ья]|дам[ыа]|валет[а]?)\b", mail_joker.effect_desc, re.I)
            target_rank = m.group(1) if m else "random rank"
        if target_rank:
            notes.append(
                f"> 📬 **MAIL-IN REBATE ACTIVE**: Earns **+$5 bank cash** for each `{target_rank}` discarded this round! "
                f"Discard any useless `{target_rank}` to print free money before playing!"
            )

    # 7. Green Joker (+1 Mult per hand, -1 Mult per discard)
    green_joker = next((j for j in state.jokers if "green" in (j.key or "").lower() or "green joker" in (j.label or "").lower()), None)
    if green_joker:
        current_m = getattr(green_joker, "mult", 0)
        mult_str = f" (Currently at +{current_m} Mult)" if current_m > 0 else ""
        notes.append(
            f"> ⚠️ **GREEN JOKER STATUS**{mult_str}: Gains +1 Mult per hand played, but **LOSES -1 MULT PER DISCARD**! "
            f"Every discard permanently degrades Green Joker's mult. Avoid casual discarding!"
        )

    # 8. Ride the Bus (+1 Mult per hand without scoring face cards)
    bus_joker = next((j for j in state.jokers if "ride the bus" in (j.label or "").lower() or "ride_the_bus" in (j.key or "").lower()), None)
    if bus_joker:
        current_m = getattr(bus_joker, "mult", 0)
        mult_str = f" (Currently at +{current_m} Mult)" if current_m > 0 else ""
        notes.append(
            f"> ⚠️ **RIDE THE BUS STATUS**{mult_str}: Gains +1 Mult per consecutive hand without scoring face cards. "
            f"🚨 **RESETS TO +0 MULT** if ANY scoring face card (Jack, Queen, King) is played! "
            f"Play only numbered cards (2-10, Ace) or keep face cards as uncounted kickers!"
        )

    # 9. Ice Cream (+100 Chips, loses -5 chips per hand played)
    ice_cream = next((j for j in state.jokers if "ice cream" in (j.label or "").lower() or "ice_cream" in (j.key or "").lower()), None)
    if ice_cream:
        current_c = getattr(ice_cream, "chips", 0)
        chips_str = f" (Currently at +{current_c} Chips)" if current_c > 0 else ""
        notes.append(
            f"> 🍦 **ICE CREAM MELTING**{chips_str}: Loses **-5 chips** for every played hand! "
            f"Expires and is destroyed when chips reach 0. Plan ahead for a replacement chip joker."
        )

    return notes


def is_xmult_joker(j: JokerModel) -> bool:
    """Returns True if the joker provides multiplicative mult (xMult) or scaling xMult."""
    if j.debuffed:
        return False
    if j.effect_type == "x_mult":
        return True
    if (j.x_mult or 1.0) > 1.0:
        return True
    edition = getattr(j, "edition", None)
    if edition and "poly" in edition.lower():
        return True
    l_key = (j.key or "").lower()
    l_lbl = (j.label or "").lower()
    l_desc = (j.effect_desc or "").lower()
    if l_key in XMULT_JOKER_IDENTIFIERS or l_lbl in XMULT_JOKER_IDENTIFIERS:
        return True
    xmult_keywords = (
        "cavendish", "card sharp", "constellation", "duo", "trio", "family", "order",
        "tribe", "baron", "obelisk", "vampire", "madness", "polychrome", "driver's license",
        "canio", "triboulet", "yorick", "idol", "ancient", "hologram", "seeing double",
        "hit the road", "the idol", "glass joker", "lucky cat", "campfire", "throwback",
        "acrobat", "photograph", "bloodstone", "loyalty card", "steel joker", "stencil",
        "ramen", "baseball card", "flower pot", "blackboard",
    )
    if any(k in l_key or k in l_lbl for k in xmult_keywords):
        return True
    if "x" in l_desc and any(w in l_desc for w in ("mult", "множ")):
        return True
    return False


def get_shop_recommendations_for_balance(state: GameState) -> list[str]:
    """Scans shop items and alerts LLM to purchases that cure critical build bottlenecks."""
    if state.stage != "SHOP" or not state.shop or not state.shop.items:
        return []

    lines: list[str] = []
    chip_jokers = [j for j in state.jokers if not j.debuffed and get_joker_role(j) in ("chips", "hybrid")]
    flat_mult_jokers = [j for j in state.jokers if not j.debuffed and get_joker_role(j) in ("mult", "hybrid") and not is_xmult_joker(j)]
    xmult_jokers = [j for j in state.jokers if is_xmult_joker(j)]

    # 1. SCALING & ECONOMIC TELEMETRY (ANTE 3+)
    if state.ante >= 3:
        boss_target = 0
        if state.ante_blinds:
            for b in state.ante_blinds:
                if b.type == "Boss":
                    boss_target = b.chips
                    break
        if boss_target <= 0:
            base_ante_targets = {1: 600, 2: 1500, 3: 4000, 4: 10000, 5: 20000, 6: 35000, 7: 60000, 8: 100000}
            boss_target = base_ante_targets.get(state.ante, 10000)

        dollars = state.dollars
        eff_cap = state.interest_cap if getattr(state, "interest_cap", None) else 25
        interest_per_round = min(5, max(0, dollars) // 5)
        gap_to_interest_cap = max(0, eff_cap - dollars)
        r_cost = state.shop.reroll_cost if state.shop else 5

        xmult_count = len(xmult_jokers)
        mult_status = f"{xmult_count} active ({', '.join(j.label for j in xmult_jokers)})" if xmult_count > 0 else "0 xMult Jokers (Pure additive/flat build)"

        telemetry_lines = [
            f"> 📊 **SHOP SCALING TELEMETRY (Ante {state.ante} | Boss Target: {boss_target:,} chips)**:",
            f"> • **Joker Structure**: {len(chip_jokers)} Chips, {len(flat_mult_jokers)} +Mult, {mult_status}.",
            f"> • **Capital & Interest**: Bank: **${dollars}** | Interest: **+${interest_per_round}/round** "
            f"({'At $' + str(eff_cap) + ' cap (+$5/rnd max)' if gap_to_interest_cap == 0 else f'${gap_to_interest_cap} to ${eff_cap} cap for max +$5/round'}).",
            f"> • **Scaling Dynamic**: Additive bonuses (Planets & flat +Mult) grow linearly (+15-20 chips / +2-3 mult per card). "
            f"Exponential boss scaling ({boss_target:,} chips) relies on multiplicative xMult and re-triggers."
        ]

        # Highlight multipliers (xMult) in shop
        xmult_items = [
            it for it in state.shop.items
            if (it.item_type == "Joker" and any(k in it.label.lower() for k in (
                "cavendish", "card sharp", "constellation", "duo", "trio", "family", "order",
                "tribe", "baron", "obelisk", "vampire", "madness", "ancient", "hologram", "blackboard", "driver", "photograph"
            )))
        ]
        if xmult_items:
            recs = ", ".join(f"Slot [{it.index}] `{it.label}` (${it.cost})" for it in xmult_items)
            telemetry_lines.append(f"> • 🌟 **Available Multipliers in Shop**: {recs}")

        if dollars > eff_cap:
            surplus = dollars - eff_cap
            telemetry_lines.append(f"> • 💡 **SURPLUS CASH TO SPEND**: You have **+${surplus}** above the ${eff_cap} interest cap. Available for purchases or shop rerolls (${r_cost}) without forfeiting max interest.")

        lines.append("\n".join(telemetry_lines))

    # Check for Chip Deficit (strictly recommend true +Chips Jokers, NOT Celestial Packs or Planets!)
    has_heavy_mult = len(xmult_jokers) > 0 or len(flat_mult_jokers) >= 2
    if has_heavy_mult and len(chip_jokers) == 0 and state.ante >= 2:
        chip_items = [
            it for it in state.shop.items
            if it.item_type == "Joker" and (
                any(k in it.label.lower() for k in (
                    "ice cream", "blue joker", "bull", "stuntman", "banner", "castle",
                    "scary face", "odd todd", "runner", "wee", "hiker", "arrowhead", "stone",
                    "sly", "wily", "clever", "devious", "crafty", "scholar", "walkie"
                ))
                or "+chips" in (it.desc or "").lower()
            )
        ]
        if chip_items:
            recs = ", ".join(f"Slot [{it.index}] `{it.label}` (${it.cost})" for it in chip_items)
            lines.append(
                f"> ⚠️ **CHIP-STARVED SHOP ALERT**: You have heavy mult with 0 chip jokers! Available +Chips Jokers to multiply your score: {recs}"
            )

    # Check for Mult Deficit
    if len(chip_jokers) >= 2 and len(flat_mult_jokers) == 0 and len(xmult_jokers) == 0 and state.ante >= 2:
        mult_items = [
            it for it in state.shop.items
            if it.item_type == "Joker" and (
                any(k in it.label.lower() for k in (
                    "joker", "gros michel", "half", "popcorn", "mystic summit", "supernova",
                    "blackboard", "cavendish", "card sharp", "the duo", "the trio", "constellation"
                ))
                or "mult" in (it.desc or "").lower()
            )
        ]
        if mult_items:
            recs = ", ".join(f"Slot [{it.index}] `{it.label}` (${it.cost})" for it in mult_items)
            lines.append(
                f"> 💡 **AVAILABLE MULT SOURCES IN SHOP**: {recs}"
            )

    return lines
 
 
PLANET_HAND_MAP = {
    "pluto": ("High Card", 10, 1),
    "mercury": ("Pair", 15, 1),
    "uranus": ("Two Pair", 20, 1),
    "venus": ("Three of a Kind", 20, 2),
    "saturn": ("Straight", 30, 3),
    "jupiter": ("Flush", 15, 2),
    "earth": ("Full House", 25, 2),
    "mars": ("Four of a Kind", 30, 3),
    "neptune": ("Straight Flush", 40, 4),
    "planet x": ("Five of a Kind", 35, 3),
    "ceres": ("Flush House", 40, 4),
    "eris": ("Flush Five", 40, 3),
}


def get_primary_hand_type(state: GameState) -> str:
    """Finds player's most played or highest level poker hand."""
    if state.poker_hands:
        sorted_hands = sorted(state.poker_hands, key=lambda h: (h.played, h.level), reverse=True)
        if sorted_hands and (sorted_hands[0].played > 0 or sorted_hands[0].level > 1):
            return sorted_hands[0].name
    return "Two Pair"


def get_upcoming_blind_target(state: GameState) -> tuple[str, int]:
    """Finds the name and target chip count of the next upcoming blind."""
    if state.ante_blinds:
        for b in state.ante_blinds:
            if b.status in ("Select", "Upcoming"):
                return b.name, b.chips
    if state.blind and state.blind.upcoming_boss_name:
        bn = (state.blind.name or "").lower()
        if "big" in bn or "большой" in bn:
            base_ante_targets = {1: 600, 2: 1500, 3: 4000, 4: 5000, 5: 15000, 6: 30000, 7: 60000, 8: 100000}
            return state.blind.upcoming_boss_name, base_ante_targets.get(state.ante, 5000)
    return "Next Blind", (state.target_score or 3000)


def get_boss_eval_state(state: GameState) -> tuple[GameState, str, int]:
    """Returns a GameState configured as if actively fighting the current Ante's Boss Blind,
    so Boss debuffs (e.g. The Flint -50% base, The Plant face debuff, suit debuffs) are applied."""
    boss_name = ""
    boss_chips = 0
    boss_debuff = None
    if state.blind and state.blind.is_boss:
        return state, state.blind.name, state.target_score
    if state.ante_blinds:
        for b in state.ante_blinds:
            if b.type == "Boss":
                boss_name = b.name
                boss_chips = b.chips
                boss_debuff = b.debuff_rule
                break
    if not boss_name and state.blind and state.blind.upcoming_boss_name:
        boss_name = state.blind.upcoming_boss_name
        boss_debuff = state.blind.upcoming_boss_debuff
    if boss_chips <= 0:
        base_ante_targets = {1: 600, 2: 1500, 3: 4000, 4: 5000, 5: 15000, 6: 30000, 7: 60000, 8: 100000}
        boss_chips = base_ante_targets.get(state.ante, 5000)
    if not boss_name:
        return state, "Boss Blind", boss_chips
    sim_blind = state.blind.model_copy(
        update={
            "name": boss_name,
            "chips_needed": boss_chips,
            "is_boss": True,
            "debuff_rule": boss_debuff or state.blind.upcoming_boss_debuff,
        }
    )
    return state.model_copy(update={"blind": sim_blind, "target_score": boss_chips}), boss_name, boss_chips


def project_shop_joker_impact(
    item: ShopItemModel,
    state: GameState,
    primary_hand: str,
    scoring_engine: BalatroScoringEngine,
) -> str | None:
    """Calculates deterministic scoring impact of buying this shop Joker."""
    next_blind_name, target_chips = get_upcoming_blind_target(state)
    boss_eval_state, boss_name, _ = get_boss_eval_state(state)
    is_next_boss = bool(boss_name and next_blind_name.lower() == boss_name.lower())
    eval_base_state = boss_eval_state if is_next_boss else state

    sample_cards = scoring_engine.get_representative_hand(primary_hand, eval_base_state)
    current_res = scoring_engine.calculate_expected_hand_score(sample_cards, hand_type=primary_hand, state=eval_base_state)
    curr_score = max(1, current_res.score)

    sim_joker = JokerModel(
        id=f"sim_{item.index}",
        key=getattr(item, "key", None) or item.id,
        label=item.label,
        effect_desc=item.desc,
        edition=getattr(item, "edition", None),
    )

    hands_curr = math.ceil(target_chips / curr_score)
    is_negative = (getattr(item, "edition", None) or "").lower() == "negative"

    # Case A: Open slot available OR Negative edition (takes 0 slots)
    if len(state.jokers) < state.max_jokers or is_negative:
        st = eval_base_state.model_copy(update={"jokers": state.jokers + [sim_joker]})
        new_res = scoring_engine.calculate_expected_hand_score(sample_cards, hand_type=primary_hand, state=st)
        new_score = new_res.score
        delta = new_score - curr_score
        ratio = new_score / curr_score
        hands_new = math.ceil(target_chips / max(1, new_score))

        target_note = f"🎯 [ONE-SHOTS {next_blind_name}!]" if new_score >= target_chips else f"Cuts hands needed from {hands_curr} to {hands_new}"
        sign = "+" if delta >= 0 else ""
        return (
            f"  └─ 📈 **IMPACT ON '{primary_hand}'**: ~{curr_score:,} -> **~{new_score:,} score** "
            f"({sign}{delta:,} | {ratio:.1f}x) | {target_note}"
        )

    # Case B: Slots are full (5/5) — simulate replacing non-eternal jokers
    non_eternal = [j for j in state.jokers if not j.eternal]
    if not non_eternal:
        return "  └─ ℹ️ All owned Jokers are Eternal and cannot be sold to buy this."

    best_score = -1
    best_replaced = None

    for oj in non_eternal:
        test_jokers = [j for j in state.jokers if j.id != oj.id] + [sim_joker]
        st = eval_base_state.model_copy(update={"jokers": test_jokers})
        res = scoring_engine.calculate_expected_hand_score(sample_cards, hand_type=primary_hand, state=st)
        if res.score > best_score:
            best_score = res.score
            best_replaced = oj

    if best_replaced and best_score > curr_score:
        delta = best_score - curr_score
        ratio = best_score / curr_score
        hands_new = math.ceil(target_chips / max(1, best_score))
        target_note = f"🎯 [ONE-SHOTS {next_blind_name}!]" if best_score >= target_chips else f"Cuts hands needed from {hands_curr} to {hands_new}"
        return (
            f"  └─ 📈 **UPGRADE PROJECTION**: Sell `{best_replaced.label}` & buy `{item.label}` -> "
            f"~{curr_score:,} to **~{best_score:,} score** (+{delta:,} | **{ratio:.1f}x boost!**) | {target_note}"
        )
    elif best_replaced:
        return (
            f"  └─ 📉 Projection: Current Jokers perform better or equal on '{primary_hand}' (~{curr_score:,} score vs best replacement ~{best_score:,})."
        )
    return None


def project_planet_impact(
    item: ShopItemModel,
    state: GameState,
    scoring_engine: BalatroScoringEngine,
) -> str | None:
    """Calculates deterministic scoring impact of buying a Planet card."""
    lbl = item.label.lower()
    matched = None
    for p_name, data in PLANET_HAND_MAP.items():
        if p_name in lbl or p_name in item.id.lower():
            matched = data
            break
    if not matched:
        return None

    target_hand, add_chips, add_mult = matched
    sample_cards = scoring_engine.get_representative_hand(target_hand, state)
    curr_res = scoring_engine.calculate_expected_hand_score(sample_cards, hand_type=target_hand, state=state)

    sim_poker_hands = []
    found = False
    for ph in state.poker_hands:
        if ph.name.lower() == target_hand.lower():
            sim_poker_hands.append(ph.model_copy(update={
                "level": ph.level + 1,
                "chips": ph.chips + add_chips,
                "mult": ph.mult + add_mult,
            }))
            found = True
        else:
            sim_poker_hands.append(ph)
    if not found:
        base_c, base_m = POKER_HAND_BASE.get(target_hand, (10, 2))
        sim_poker_hands.append(PokerHandModel(
            name=target_hand, level=2, chips=base_c + add_chips, mult=base_m + add_mult
        ))

    sim_state = state.model_copy(update={"poker_hands": sim_poker_hands})
    new_res = scoring_engine.calculate_expected_hand_score(sample_cards, hand_type=target_hand, state=sim_state)
    delta = new_res.score - curr_res.score

    extra_note = ""
    boss_name = ""
    boss_chips = 0
    if state.ante_blinds:
        for b in state.ante_blinds:
            if b.type == "Boss":
                boss_name = b.name
                boss_chips = b.chips
                break
    if not boss_name and state.blind and state.blind.upcoming_boss_name:
        boss_name = state.blind.upcoming_boss_name
    if boss_chips <= 0:
        base_ante_targets = {1: 600, 2: 1500, 3: 4000, 4: 5000, 5: 15000, 6: 30000, 7: 60000, 8: 100000}
        boss_chips = base_ante_targets.get(state.ante, 5000)

    if boss_name and ("needle" in boss_name.lower() or "игла" in boss_name.lower()):
        if curr_res.score < boss_chips and new_res.score >= boss_chips:
            extra_note = f" — 🎯 **ONE-SHOTS THE NEEDLE BOSS!** (Reaches ~{new_res.score:,} vs {boss_chips:,} target in 1 hand)"
        elif curr_res.score < boss_chips:
            extra_note = f" — 🛡️ **CRITICAL NEEDLE BOOST**: Cuts Needle deficit from -{boss_chips - curr_res.score:,} to -{max(0, boss_chips - new_res.score):,} chips!"

    return (
        f"  └─ 🪐 **PERMANENT PLANET UPGRADE**: Levels up '{target_hand}' (+{add_chips}c, +{add_mult}m) -> "
        f"~{curr_res.score:,} to **~{new_res.score:,} score** (+{delta:,} chips/hand!){extra_note}"
    )


def get_boss_readiness_alert(state: GameState, primary_hand: str, primary_hand_score: int) -> list[str]:
    """Generates proactive tactical alerts for the upcoming Boss Blind during Shop and Blind Select."""
    alerts: list[str] = []
    boss_name = ""
    boss_chips = 0

    if state.ante_blinds:
        for b in state.ante_blinds:
            if b.type == "Boss":
                boss_name = b.name
                boss_chips = b.chips
                break

    if not boss_name and state.blind and state.blind.upcoming_boss_name:
        boss_name = state.blind.upcoming_boss_name
        boss_chips = getattr(state.blind, "upcoming_boss_chips", 0)

    if not boss_name:
        return alerts

    if boss_chips <= 0:
        base_ante_targets = {1: 600, 2: 1500, 3: 4000, 4: 5000, 5: 15000, 6: 30000, 7: 60000, 8: 100000}
        boss_chips = base_ante_targets.get(state.ante, 5000)

    n = boss_name.lower()
    is_needle = "needle" in n or "игла" in n
    is_flint = "flint" in n or "кремень" in n

    boss_eval_state, _, _ = get_boss_eval_state(state)
    scoring_engine = BalatroScoringEngine()
    sample_cards = scoring_engine.get_representative_hand(primary_hand, boss_eval_state)
    boss_res = scoring_engine.calculate_expected_hand_score(sample_cards, hand_type=primary_hand, state=boss_eval_state)
    eff_boss_score = max(1, boss_res.score)

    if is_flint:
        hands_req = math.ceil(boss_chips / eff_boss_score)
        avail_hands = max(1, state.hands_left if state.hands_left > 0 else 4)
        status_icon = "🚨 **CRITICAL BOSS DEFICIT**" if hands_req > avail_hands else "⚠️ **BOSS READINESS: THE FLINT**"
        alerts.append(
            f"> {status_icon} (**The Flint / Кремень** — Ante {state.ante} Boss, Target: **{boss_chips:,} chips**):\n"
            f">   • **Halved Base Stats**: The Flint halves base poker hand Chips & Mult! Your '{primary_hand}' drops from ~{primary_hand_score:,} to **~{eff_boss_score:,} chips/hand** against The Flint!\n"
            f">   • **Projected Pace**: Requires **~{hands_req} hands** to clear {boss_chips:,} chips ({avail_hands} hands available; max 4-hand ceiling vs Flint is **~{eff_boss_score * avail_hands:,} chips**).\n"
            f">   • **Shop Priority**: Flat +Chips and +Mult Jokers (and xMult) are NOT halved by The Flint — buy scoring Jokers now if {eff_boss_score * avail_hands:,} < {boss_chips:,}!"
        )
    elif is_needle:
        if eff_boss_score < boss_chips:
            deficit = boss_chips - eff_boss_score
            alerts.append(
                f"> ℹ️ **BOSS READINESS: THE NEEDLE (Ante {state.ante} Boss)**:\n"
                f">   • Restriction: The Needle allows **ONLY 1 HAND TOTAL** (Must score {boss_chips:,} chips in 1 hit)!\n"
                f">   • Current Output: Your primary hand ('{primary_hand}') yields at most **~{eff_boss_score:,} chips** "
                f"(Leaves a **{deficit:,} deficit** vs {boss_chips:,} needed).\n"
                f">   • Shop Priority: Options to increase single-hand output include Planet cards, +Mult, or xMult."
            )
        else:
            margin = eff_boss_score - boss_chips
            alerts.append(
                f"> 🛡️ **BOSS READINESS: THE NEEDLE (Ante {state.ante} Boss)**: "
                f"Target: {boss_chips:,} in 1 hand. Your '{primary_hand}' reaches **~{eff_boss_score:,} chips** "
                f"(Safety margin: +{margin:,} chips). Ready!"
            )
    elif "fish" in n or "рыб" in n:
        hands_req = math.ceil(boss_chips / eff_boss_score)
        if hands_req >= 3:
            alerts.append(
                f"> ⚠️ **BOSS READINESS: THE FISH (Ante {state.ante} Boss)**:\n"
                f">   • Target: **{boss_chips:,} chips** | Primary Hand: **~{eff_boss_score:,} chips** (Requires **{hands_req} hands** to win).\n"
                f">   • 🚨 **HIGH RISK**: The Fish deals ALL replacement cards face-down after Hand 1! Requiring {hands_req} hands means playing mostly hidden cards in later hands!\n"
                f">   • Shop Priority: Upgrade hand levels (Celestial Packs) or acquire xMult Jokers to win in 1–2 hands max before face-down cards accumulate!"
            )
        else:
            alerts.append(
                f"> 🛡️ **BOSS READINESS: THE FISH (Ante {state.ante} Boss)**: "
                f"Target: {boss_chips:,} chips. Your '{primary_hand}' reaches ~{eff_boss_score:,} chips "
                f"({hands_req} hand{'s' if hands_req > 1 else ''} needed). Safe range to win before blind draws become critical."
            )
    else:
        hands_req = math.ceil(boss_chips / eff_boss_score)
        avail_hands = max(1, state.hands_left if state.hands_left > 0 else 4)
        if hands_req > avail_hands or eff_boss_score < primary_hand_score:
            debuff_note = f" (reduced from ~{primary_hand_score:,} by Boss debuff)" if eff_boss_score < primary_hand_score else ""
            alerts.append(
                f"> ℹ️ **BOSS READINESS: {boss_name.upper()} (Ante {state.ante} Boss)**: "
                f"Target: **{boss_chips:,} chips**. At your Boss-adjusted pace (**~{eff_boss_score:,}/hand**{debuff_note}), "
                f"clearing the boss takes **~{hands_req} hands** (available: {avail_hands} hands)."
            )
    return alerts


STAKE_HIERARCHY = [
    ("White", 1, "White Stake", "Base game difficulty."),
    ("Red", 2, "Red Stake", "Small Blind gives no payout money ($0)."),
    ("Green", 3, "Green Stake", "Required score scales faster for each Ante."),
    ("Black", 4, "Black Stake", "Shop can spawn [ETERNAL] Jokers (cannot be sold or destroyed)."),
    ("Blue", 5, "Blue Stake", "-1 Discard available each round."),
    ("Purple", 6, "Purple Stake", "Required score scales significantly faster."),
    ("Orange", 7, "Orange Stake", "Shop can spawn [PERISHABLE] Jokers (debuffed after 5 rounds) and Booster packs cost +$1/ante."),
    ("Gold", 8, "Gold Stake", "Shop can spawn [RENTAL] Jokers (costs $3 at end of every round)."),
]


def get_cumulative_stake_modifiers(current_stake_str: str) -> list[str]:
    """Returns all cumulative modifiers active up to the current Stake tier."""
    current_tier = 1
    for name, tier, _, _ in STAKE_HIERARCHY:
        if name.lower() in current_stake_str.lower():
            current_tier = tier
            break

    active_modifiers: list[str] = []
    for name, tier, _, desc in STAKE_HIERARCHY:
        if 1 < tier <= current_tier:
            active_modifiers.append(f"[{name} Stake Modifier]: {desc}")

    return active_modifiers


def get_boss_rule_reminder(boss_name: str | None) -> str | None:
    """Returns accurate, non-hallucinatory rules for Boss Blinds (supports English & Russian names)."""
    if not boss_name:
        return None
    n = boss_name.lower()
    if "psychic" in n or "экстрасенс" in n:
        return (
            "Must play EXACTLY 5 cards! TACTIC: Do NOT hunt for Flush or Straight! "
            "You can play Pair, Two Pair, or Three of a Kind by simply padding it with 1-3 useless junk cards to reach 5 cards. "
            "Playing your core hand padded to 5 cards keeps all your Joker synergies active!"
        )
    elif "needle" in n or "игла" in n:
        return "Can ONLY play 1 hand this round! Use discards first to dig for maximum score."
    elif "eye" in n or "око" in n:
        return "No repeated hand types! Each hand played this round must be a different poker hand."
    elif "mouth" in n or "рот" in n:
        return "Can ONLY play 1 hand type this round! All hands played must be the same poker hand."
    elif "plant" in n or "растение" in n:
        return "All face cards (J, Q, K) are debuffed to 0! Score with Aces and numbered cards (Aces are NOT face cards)."
    elif "mark" in n or "знак" in n:
        return "All face cards (J, Q, K) are drawn face down! NOTE: Face cards STILL score normal chips and mult AND trigger face-card Jokers (Smiley Face, Scary Face, Photograph, Sock and Buskin)! They are simply hidden visually."
    elif "fish" in n or "рыб" in n:
        return (
            "Cards drawn after each played hand are drawn face down! NOTE: Cards STILL score normal chips and mult, they are simply hidden. "
            "TACTIC: Keep visible anchor cards, pad hands with face-down cards up to 5 cards to trigger Jokers and passive bonuses, and check your draw pile profile."
        )
    elif "pillar" in n or "столп" in n:
        return "Cards played previously in this Ante are debuffed (give 0 chips/mult)."
    elif "flint" in n or "кремень" in n:
        return "Base chips and mult are halved. Flat Mult and flat Chip Jokers remain full strength."
    elif "leaf" in n or "verdant" in n or "лист" in n:
        return "All cards are debuffed until 1 Joker is sold with `sell_joker`."
    elif "ox" in n or "вол" in n:
        return "Playing your most-played hand sets bank to $0."
    elif "club" in n or "треф" in n:
        return "Clubs are debuffed (0 chips/mult)."
    elif "goad" in n or "пик" in n:
        return "Spades are debuffed (0 chips/mult)."
    elif "window" in n or "буб" in n:
        return "Diamonds are debuffed (0 chips/mult)."
    elif "head" in n or "черв" in n:
        return "Hearts are debuffed (0 chips/mult)."
    elif "arm" in n or "рука" in n:
        return "Decreases level of played poker hand by 1."
    elif "water" in n or "вода" in n:
        return "Start with 0 discards."
    elif "manacle" in n or "кандалы" in n:
        return "-1 Hand size this round."
    return None


def get_upcoming_boss_tactical_advice(boss_name: str | None) -> str | None:
    """Returns proactive tactical advice for upcoming boss during Small/Big Blinds."""
    if not boss_name:
        return None
    n = boss_name.lower()
    if "pillar" in n or "столп" in n:
        return (
            "Every card played in this Ante becomes PERMANENTLY DEBUFFED (0 score) against The Pillar! "
            "Win Small/Big blind with as few cards as possible (e.g. 2-3 cards or low junk) to save your highest cards fresh for the boss!"
        )
    elif "psychic" in n or "экстрасенс" in n:
        return (
            "The Psychic requires EXACTLY 5 cards per played hand! TACTICAL DIRECTIVE: You do NOT need a natural 5-card combo (Flush/Straight)! "
            "Play your primary hand (Pair, Two Pair, Three of a Kind) and pad it with extra junk cards up to 5 cards. "
            "Never abandon your Joker build just to force a 5-card hand type!"
        )
    elif "needle" in n or "игла" in n:
        return "The Needle allows ONLY 1 hand all round! Dig aggressively with discards for your strongest possible combo."
    elif "eye" in n or "око" in n:
        return "The Eye forbids playing the same hand type twice. Prepare 2 distinct hand types (e.g. Flush + Two Pair)."
    elif "mouth" in n or "рот" in n:
        return "The Mouth allows ONLY 1 hand type all round. Commit to one consistent hand type."
    elif "plant" in n or "растение" in n:
        return "The Plant debuffs all face cards (J, Q, K) to 0. Prioritize numbered cards or Aces!"
    elif "flint" in n or "кремень" in n:
        return "The Flint halves base chips and mult. Flat chip and flat mult jokers are essential."
    elif "water" in n or "вода" in n:
        return "The Water starts with 0 discards. Prepare high consistency hands."
    elif "arm" in n or "рука" in n:
        return "The Arm decreases the level of played hand by 1. Have backup hand types or extra score margin."
    elif "ox" in n or "вол" in n:
        return "The Ox sets bank to $0 if you play your most-played hand. Prepare an alternate hand type."
    elif "leaf" in n or "verdant" in n or "лист" in n:
        return "Verdant Leaf debuffs all cards until 1 Joker is sold. Keep a cheap/expendable Joker to sell."
    elif "fish" in n or "рыб" in n:
        return (
            "The Fish draws all replacement cards face-down after each played hand! "
            "TACTIC: Try to clear the boss in as few high-scoring hands as possible before face-down cards accumulate. "
            "Ensure you have leveled hand stats or xMult so you don't need 3+ hands of blind draws."
        )
    return None


def get_pack_category(label: str) -> str:
    """Returns the item type contained in a booster pack to avoid pack confusion."""
    l = label.lower()
    if "buffoon" in l or "шут" in l:
        return "Jokers"
    elif "celestial" in l or "небес" in l:
        return "Planets"
    elif "arcana" in l or "аркан" in l:
        return "Tarots"
    elif "spectral" in l or "спектр" in l:
        return "Spectrals"
    elif "standard" in l or "стандарт" in l:
        return "Playing Cards (NO Jokers)"
    return "Booster Pack"


def project_booster_impact(item: ShopItemModel, state: GameState, primary_hand: str, engine: BalatroScoringEngine) -> str | None:
    """Provides concise, objective description and interest-cost trade-off for Booster Packs."""
    l = item.label.lower()
    is_pack = item.item_type in ("Booster", "BoosterPack") or "pack" in l
    if not is_pack:
        return None

    eff_cap = getattr(state, "interest_cap", 25) or 25
    cur_interest = min(eff_cap // 5, max(0, state.dollars) // 5)
    after_bank = max(0, state.dollars - item.cost)
    after_interest = min(eff_cap // 5, after_bank // 5)
    interest_diff = cur_interest - after_interest
    econ_note = (
        f" (Costs -${interest_diff}/rnd interest: bank ${state.dollars} → ${after_bank})"
        if interest_diff > 0 and "green" not in (state.deck_name or "").lower()
        else f" (Bank ${state.dollars} → ${after_bank})"
    )

    if "celestial" in l or "небес" in l:
        ph = next((p for p in state.poker_hands if p.name.lower() == primary_hand.lower()), None)
        cur_lvl = ph.level if ph else 1
        return (
            f"  > 🪐 **Celestial Pack**: Random Planet cards (+15-30 base chips / +1-3 base mult if `{primary_hand}` Lvl {cur_lvl} appears; "
            f"does NOT replace a +Chips or xMult Joker).{econ_note}"
        )
    elif "buffoon" in l or "шут" in l:
        open_slots = max(0, state.max_jokers - len(state.jokers))
        slot_status = f"{open_slots} open slot(s)" if open_slots > 0 else "slots full (must sell a Joker before opening to pick)"
        return (
            f"  > 🃏 **Buffoon Pack**: Choose from 2-4 Jokers ({slot_status}).{econ_note}"
        )
    elif "arcana" in l or "аркан" in l:
        return (
            f"  > 🔮 **Arcana Pack**: Choose from Tarot cards (deck sculpting, card enhancements, or cash via Hermit/Temperance).{econ_note}"
        )
    elif "spectral" in l or "спектр" in l:
        return (
            f"  > ⚡ **Spectral Pack**: High-variance Spectral cards (strong buffs/clones, often with penalties like destroying jokers or -1 hand size).{econ_note}"
        )
    elif "standard" in l or "стандарт" in l:
        return (
            f"  > 🎴 **Standard Pack**: Adds 1 playing card to deck (skip plain unbuffed cards to avoid deck dilution).{econ_note}"
        )
    return None


def project_voucher_impact(item: ShopItemModel, state: GameState) -> str | None:
    """Highlights the permanent run-wide effect and Tier 2 evolution of shop vouchers."""
    l = item.label.lower()
    if item.item_type != "Voucher" and "voucher" not in item.id.lower() and "voucher" not in l:
        return None

    raw_id = getattr(item, "key", None) or item.label or item.id
    v_data = find_voucher_data(raw_id)
    if not v_data and item.label:
        v_data = find_voucher_data(item.label)

    if v_data:
        return format_voucher_impact(v_data, cost=item.cost, bank=state.dollars)

    return f"  > 🎟️ **RUN VOUCHER (PERMANENT UPGRADE)**: One-time investment that permanently buffs your run rules, limits, or shop for all remaining Antes."



def get_money_scaling_status(state: GameState, scratchpad: str | None = None) -> tuple[bool, str]:
    """Checks whether the player's build or active plan relies on hoarding cash

    (e.g. Bull, Bootstraps, To the Moon, or explicit scratchpad notes).
    Returns (is_money_scaling, reason_str).
    """
    reasons = []

    for j in state.jokers:
        k = (j.key or "").lower()
        lbl = (j.label or "").lower()
        desc = (j.effect_desc or "").lower()

        if k == "j_bull" or "bull" in lbl:
            bonus = state.dollars * 2
            reasons.append(f"Bull (+{bonus} Chips from ${state.dollars})")
        elif k == "j_bootstraps" or "bootstraps" in lbl:
            bonus = (state.dollars // 5) * 2
            reasons.append(f"Bootstraps (+{bonus} Mult from ${state.dollars})")
        elif k == "j_to_the_moon" or "to the moon" in lbl:
            reasons.append("To the Moon (doubled interest)")
        elif "for each $" in desc or "for every $" in desc:
            reasons.append(f"{j.label} (scales with cash)")

    if scratchpad:
        sc_lower = scratchpad.lower()
        if any(kw in sc_lower for kw in ("bull", "bootstraps", "hoard", "money scaling", "scaling cash", "scaling money", "economy build")):
            if not reasons:
                reasons.append(f"Active Plan Note ('{scratchpad[:40]}...')")

    if reasons:
        return True, ", ".join(reasons)
    return False, ""


def get_boss_card_debuff(blind: BlindModel) -> tuple[str | None, str]:
    """Detects if the active boss blind debuffs specific suits or card categories.
    Returns (category, description) where category is in:
    ['Spades', 'Hearts', 'Diamonds', 'Clubs', 'Face', 'Pillar', None]
    """
    if not getattr(blind, "is_boss", False):
        return None, ""

    name = (getattr(blind, "name", "") or "").lower()
    rule = (getattr(blind, "debuff_rule", "") or "").lower()

    if "goad" in name or "стрекало" in name or "spade" in rule:
        return "Spades", "All Spades debuffed"
    if "head" in name or "голова" in name or "heart" in rule:
        return "Hearts", "All Hearts debuffed"
    if "window" in name or "окно" in name or "diamond" in rule:
        return "Diamonds", "All Diamonds debuffed"
    if "club" in name or "дубина" in name or "треф" in rule:
        return "Clubs", "All Clubs debuffed"
    if "plant" in name or "растение" in name or "face" in rule:
        return "Face", "All Face cards (J, Q, K) debuffed"
    if "pillar" in name or "столп" in name:
        return "Pillar", "Previously played cards this Ante debuffed"

    return None, ""


def format_draw_profile_intel(state: GameState, face_down_count: int, is_fish: bool = False) -> list[str]:
    """Generates empirical draw profile and pairing probabilities for face-down cards under Fog of War."""
    lines: list[str] = []
    header_tag = "🐟 **THE FISH (РЫБА) TACTICAL INTEL & DRAW PROFILE**" if is_fish else "🙈 **FACE-DOWN CARDS DRAW PROFILE**"

    ds = state.deck_summary
    total_rem = ds.total_remaining if ds else state.deck_remaining
    suit_syms = {"Spades": "♠", "Hearts": "♥", "Clubs": "♣", "Diamonds": "♦"}
    rank_abbr = {"Jack": "J", "Queen": "Q", "King": "K", "Ace": "A"}
    rank_order = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "Jack", "Queen", "King", "Ace"]

    suits_str = " ".join(f"{suit_syms.get(s, s)}:{c}" for s, c in (ds.suits.items() if ds else {})) or "Unknown"
    face_cards = ds.face_cards if ds else 0
    aces = ds.aces if ds else 0
    face_pct = round(100.0 * face_cards / max(1, total_rem), 1) if total_rem > 0 else 0.0
    aces_pct = round(100.0 * aces / max(1, total_rem), 1) if total_rem > 0 else 0.0

    lines.append(f"> {header_tag} ({face_down_count}/{len(state.hand)} cards face-down):")
    if is_fish:
        lines.append("> • **BOSS MECHANIC**: Any replacement cards drawn after playing a hand are dealt FACE-DOWN. They score full chips and mult, only visuals are hidden!")

    lines.append(f"> • **REMAINING DRAW PILE ({total_rem} cards)**: Suits: [{suits_str}] | Faces: {face_cards} ({face_pct}%) | Aces: {aces} ({aces_pct}%)")

    if ds and ds.ranks:
        ranks_str = " ".join(f"{rank_abbr.get(r, r)}:{ds.ranks.get(r, 0)}" for r in rank_order if r in ds.ranks and ds.ranks[r] > 0)
        if ranks_str:
            lines.append(f"> • **REMAINING RANKS IN PILE**: {ranks_str}")

    # Pairing potential with visible cards
    visible_cards = [c for c in state.hand if not c.face_down]
    visible_ranks = [c.rank for c in visible_cards if c.rank and c.rank not in ("?", "Face", "None")]

    if visible_ranks and ds and ds.ranks:
        rank_counts_held = collections.Counter(visible_ranks)
        match_details = []
        total_matching_cards = 0
        for r, held_c in rank_counts_held.items():
            deck_c = ds.ranks.get(r, 0)
            if deck_c > 0:
                total_matching_cards += deck_c
                abbr = rank_abbr.get(r, r)
                match_details.append(f"{abbr} (held {held_c}, {deck_c} in deck)")

        N = max(total_rem + face_down_count, face_down_count + 1)
        M = total_matching_cards
        k = face_down_count
        if M > 0 and N >= k and (N - M) >= k:
            prob_no_match = math.comb(N - M, k) / math.comb(N, k)
            pairing_prob = max(0.0, min(100.0, (1.0 - prob_no_match) * 100.0))
        elif M > 0 and (N - M) < k:
            pairing_prob = 100.0
        else:
            pairing_prob = 0.0

        match_desc = ", ".join(match_details) if match_details else "No direct rank copies remaining in deck"
        lines.append(
            f"> • **PAIRING ODDS WITH HELD ANCHORS**: You hold [{', '.join(rank_abbr.get(r, r) for r in visible_ranks)}]. "
            f"Matching copies in draw pile: [{match_desc}]. "
            f"There is a **~{pairing_prob:.1f}% probability** that your {face_down_count} face-down cards contain at least one matching card to complete a Pair, Two Pair, or Trips!"
        )
    elif face_down_count == len(state.hand):
        lines.append(
            f"> • **BLIND 5-CARD ODDS**: All {face_down_count} cards are face-down. "
            f"A random 5-card draw from your deck has a **~45–50% probability** of containing a natural Pair or Two Pair. "
            f"Playing 5 cards together maximizes scoring cards and activates all passive Jokers!"
        )

    # Active Joker Synergies
    synergies: list[str] = []
    if ds and ds.ranks:
        evens_count = sum(ds.ranks.get(r, 0) for r in ("2", "4", "6", "8", "10"))
        even_pct = round(100.0 * evens_count / max(1, total_rem), 1)
        walkie_count = ds.ranks.get("10", 0) + ds.ranks.get("4", 0)
        walkie_pct = round(100.0 * walkie_count / max(1, total_rem), 1)
        odds_count = sum(ds.ranks.get(r, 0) for r in ("Ace", "9", "7", "5", "3"))
        odd_pct = round(100.0 * odds_count / max(1, total_rem), 1)

        if any("even steven" in (j.label or "").lower() for j in state.jokers if not j.debuffed):
            synergies.append(f"Even Steven (+4 Mult): {evens_count}/{total_rem} cards in deck are even (~{even_pct}%). Each hidden card has ~{even_pct}% chance to trigger +4 Mult.")
        if any("odd todd" in (j.label or "").lower() for j in state.jokers if not j.debuffed):
            synergies.append(f"Odd Todd (+31 Chips): {odds_count}/{total_rem} cards in deck are odd (~{odd_pct}%).")
        if any("walkie talkie" in (j.label or "").lower() for j in state.jokers if not j.debuffed):
            synergies.append(f"Walkie Talkie (+10c/+4m): {walkie_count}/{total_rem} cards are 10s or 4s (~{walkie_pct}%).")
        if any(any(k in (j.label or "").lower() for k in ("scary face", "smiley face", "photograph", "sock and buskin")) for j in state.jokers if not j.debuffed):
            synergies.append(f"Face Card Jokers: {face_cards}/{total_rem} cards are face cards (~{face_pct}%).")

    if any("hiker" in (j.label or "").lower() for j in state.jokers if not j.debuffed):
        synergies.append("Hiker (+5 chips permanent): ALL played hidden cards trigger +5 permanent chips when scored.")

    if synergies:
        lines.append("> • **JOKER TRIGGER SYNERGIES**:\n>   - " + "\n>   - ".join(synergies))

    # Tactical directives
    discard_note = (
        f"You have **{state.discards_left} discards left**. If hunting specific combos, discard unaligned cards — but remember newly drawn cards will ALSO be face-down!"
        if state.discards_left > 0 else
        "You have **0 discards left**. Commit to playing your best combination."
    )
    lines.append(
        f"> • **TACTICAL DIRECTIVE (AVOID BLIND PANIC)**:\n"
        f">   1. **Do NOT Play 1-2 Cards In A Rush**: Playing single cards yields minimal score (e.g. 80–270 chips) and forces The Fish to draw even more face-down cards! Always play full 4–5 card hands.\n"
        f">   2. **Pad Visible Anchors**: Keep your strongest visible combo and pad with face-down cards to reach 5 cards. Hidden cards score their nominal chips, trigger Hiker and passives, and may secretly form Trips or Two Pair!\n"
        f">   3. **Discards**: {discard_note}"
    )

    return lines


def serialize_game_state_to_markdown(
    state: GameState,
    scratchpad: str | None = None,
    scratchpad_ante: int | None = None,
    loyalty_remaining: int | None = None,
    max_hand_score: int = 0,
    best_hand_desc: str | None = None,
) -> str:
    """Transforms raw GameState into a token-efficient, context-rich Markdown document."""
    lines: list[str] = []

    # 1. Header & General Context
    interest = min(5, state.dollars // 5)
    remaining_score = max(0, state.target_score - state.current_score)
    pct = (state.current_score / state.target_score * 100) if state.target_score > 0 else 100.0

    lines.append(f"### BALATRO STATE: Ante {state.ante}/8 | Round {state.round} | Phase: **{state.stage}**")

    deck_info = f"- **Deck**: `{state.deck_name}` | **Difficulty**: `{state.stake}`"
    if state.challenge_name:
        deck_info += f" | 🏆 **Challenge Mode**: `{state.challenge_name}`"
    lines.append(deck_info)

    # Tactical Deck Hints for LLM across all 15 Balatro decks
    deck_rules = {
        "Plasma": "> ℹ️ **PLASMA DECK RULE**: Total Chips and Total Mult are balanced before multiplying: `((Chips + Mult) / 2)^2`. Blinds scale 2x higher. Pure flat Chip Jokers (Stuntman, Bull, Castle, Blue Joker) give immense exponential score!",
        "Green": "> ℹ️ **GREEN DECK RULE**: Zero interest earned at end of round. Earns +$2 per remaining hand and +$1 per remaining discard.",
        "Abandoned": "> ℹ️ **ABANDONED DECK RULE**: Deck contains NO Face cards (No Jacks, Queens, Kings - only 40 cards total). Disregard all Face Card jokers (Photograph, Smiley Face, Scary Face); focus on low numbers and Straight/Flush/Pair!",
        "Checkered": "> ℹ️ **CHECKERED DECK RULE**: Deck contains only Spades and Hearts (26 Spades, 26 Hearts; zero Clubs or Diamonds). Flushes and Straight Flushes are extremely consistent to build!",
        "Red": "> ℹ️ **RED DECK BONUS**: +1 Discard every round (4 discards base). Gives extra digging power to sculpt winning hands!",
        "Blue": "> ℹ️ **BLUE DECK BONUS**: +1 Hand every round (5 hands base). Grants extra scoring safety and +$1 bonus cash for each unused hand upon clearing blinds!",
        "Yellow": "> ℹ️ **YELLOW DECK BONUS**: Started with +$10 extra cash ($14 bank).",
        "Black": "> ℹ️ **BLACK DECK RULE**: +1 Joker slot (6 slots total!), but -1 Hand every round (only 3 hands base). Extreme danger in Ante 1-2: ensure every hand scores reliably, then build a 6-Joker powerhouse!",
        "Magic": "> ℹ️ **MAGIC DECK BONUS**: Started with Crystal Ball voucher (+1 consumable slot) and 2 copies of The Fool. Hold up to 3 consumables and duplicate top Tarots/Planets!",
        "Nebula": "> ℹ️ **NEBULA DECK RULE**: Started with Telescope voucher (Celestial packs always offer planet for most played hand), but -1 consumable slot (only 1 slot).",
        "Ghost": "> ℹ️ **GHOST DECK BONUS**: Spectral cards appear in the shop! Started with The Hex in inventory. Leverage game-breaking Spectral cards throughout the run!",
        "Zodiac": "> ℹ️ **ZODIAC DECK BONUS**: Started with Tarot Merchant, Planet Merchant, and Overstock vouchers. Shop has 3 slots and 2x higher appearance rate for Tarots and Planets!",
        "Painted": "> ℹ️ **PAINTED DECK RULE**: +2 Hand Size (10 cards in hand!), but -1 Joker slot (4 slots total). Huge hand size makes finding complex hands easy, but requires 4 exceptionally strong Jokers!",
        "Anaglyph": "> ℹ️ **ANAGLYPH DECK BONUS**: Grants a Double Tag after defeating each Boss Blind! Stack Double Tags to multiply high-value skip tags (like Investment Tag $25, Negative Joker, or Coupon tags)!",
        "Erratic": "> ℹ️ **ERRATIC DECK RULE**: All card ranks and suits in starting deck are completely randomized! Review the Draw Pile Deck breakdown to see which suits and ranks dominate!",
    }
    for d_name, rule_text in deck_rules.items():
        if d_name.lower() in state.deck_name.lower():
            lines.append(rule_text)
            break

    # Cumulative Stake Modifiers for LLM
    cumulative_mods = get_cumulative_stake_modifiers(state.stake)
    if cumulative_mods:
        lines.append(f"> ⚠️ **CUMULATIVE STAKE DEBUFFS ACTIVE ({state.stake})**:")
        for mod in cumulative_mods:
            lines.append(f">   - {mod}")

    if state.blind.is_boss:
        lines.append(f"- **BOSS BLIND ENCOUNTER**: 🚨 **{state.blind.name}** | Reward: ${state.blind.reward}")
        boss_rule = get_boss_rule_reminder(state.blind.name)
        if boss_rule:
            lines.append(f"> 🚨 **BOSS RULE**: {boss_rule}")
        elif state.blind.debuff_rule:
            lines.append(f"> 🚨 **BOSS DEBUFF RULE**: `{state.blind.debuff_rule}`")
        b_name_lc = (state.blind.name or "").lower()
        played_types = [t for t in getattr(state, "played_hand_types_this_round", []) if t]
        if ("mouth" in b_name_lc or "рот" in b_name_lc) and played_types:
            lines.append(
                f"> 🔒 **THE MOUTH LOCKED HAND TYPE**: You already played **{played_types[0]}** this round! "
                f"ONLY **{played_types[0]}** will score points — ALL other hand types will score **0 CHIPS**!"
            )
        elif ("eye" in b_name_lc or "око" in b_name_lc) and played_types:
            lines.append(
                f"> 👁️ **THE EYE FORBIDDEN HAND TYPES**: Already played this round: **{', '.join(played_types)}**! "
                f"Repeating any of these hand types will score **0 CHIPS**! Play a different hand type!"
            )
    else:
        lines.append(f"- **Current Blind**: {state.blind.name} | Reward: ${state.blind.reward} (Standard Blind — No Boss Debuffs Active)")
        if state.blind.upcoming_boss_name:
            if state.stage != "SELECTING_HAND":
                upcoming_rule = get_boss_rule_reminder(state.blind.upcoming_boss_name)
                rule_suffix = f" — ⚠️ {upcoming_rule}" if upcoming_rule else ""
                lines.append(f"- **Upcoming Ante {state.ante} Boss Blind**: 👁️ `{state.blind.upcoming_boss_name}` ({state.blind.upcoming_boss_debuff or 'Special ability'}){rule_suffix}")
                boss_advice = get_upcoming_boss_tactical_advice(state.blind.upcoming_boss_name)
                if boss_advice:
                    lines.append(f"> 🛡️ **TACTICAL PRE-BOSS ADVICE (FOR SHOP/PLANNING)**: {boss_advice}")
            else:
                lines.append(f"- **Upcoming Ante {state.ante} Boss**: `{state.blind.upcoming_boss_name}` (Future Boss only — NOT active in this blind)")

    lines.append(
        f"- **Score**: {state.current_score:,} / {state.target_score:,} ({pct:.1f}%) | "
        f"Remaining Needed: **{remaining_score:,} chips**"
    )
    draw_cards_count = state.deck_summary.total_remaining if state.deck_summary else state.deck_remaining
    lines.append(
        f"- **Resources**: Hands: **{state.hands_left}** | Discards: **{state.discards_left}** | "
        f"Bank: **${state.dollars}** (Est. Interest: +${interest}) | Draw Pile: {draw_cards_count} cards"
    )
    if state.stage == "SELECTING_HAND" and remaining_score > 0:
        req_pace = math.ceil(remaining_score / max(1, state.hands_left))
        lines.append(
            f"- **Required Combat Pace**: **~{req_pace:,} chips/hand** across {state.hands_left} remaining hands to clear this blind"
        )

    # 1b. Benchmark Metric: Best Hand Record vs Boss Blind Target
    boss_name = ""
    boss_chips = 0
    if state.blind.is_boss:
        boss_name = state.blind.name
        boss_chips = state.target_score
    elif state.ante_blinds:
        for b in state.ante_blinds:
            if b.type == "Boss":
                boss_name = b.name
                boss_chips = b.chips
                break
    if not boss_name and state.blind.upcoming_boss_name:
        boss_name = state.blind.upcoming_boss_name
    if boss_chips <= 0:
        base_ante_targets = {1: 600, 2: 1500, 3: 4000, 4: 5000, 5: 15000, 6: 30000, 7: 60000, 8: 100000}
        boss_chips = base_ante_targets.get(state.ante, 5000)

    if max_hand_score > 0 and boss_chips > 0 and state.stage in ("SHOP", "BLIND_SELECT"):
        cov_pct = (max_hand_score / boss_chips) * 100
        gap = boss_chips - max_hand_score
        hands_req = max(1, math.ceil(boss_chips / max_hand_score))
        desc_str = f" ({best_hand_desc})" if best_hand_desc else ""
        lines.append(
            f"- **🎯 REALITY CHECK (BEST HAND vs BOSS TARGET)**: "
            f"Run Peak Hand: **{max_hand_score:,} chips**{desc_str} | "
            f"Ante {state.ante} Boss ({boss_name or 'Boss'}): **{boss_chips:,} chips** "
            f"(Peak covers **{cov_pct:.1f}%** of Boss)"
        )
        if gap > 0:
            if max_hand_score * 4 < boss_chips:
                lines.append(
                    f"> ⚠️ **Boss Deficit**: 4x peak hands ({max_hand_score * 4:,}) cannot reach Boss target ({boss_chips:,}). Need scoring upgrades (Planets, +Mult, xMult) in Shop."
                )
            else:
                lines.append(
                    f"> ℹ️ **Boss Deficit**: Best hand leaves -{gap:,} vs Boss. Requires ~{hands_req} clean hands to clear."
                )
        else:
            lines.append(
                f"> ✅ **Boss Readiness**: Single best hand ({max_hand_score:,}) exceeds Boss requirement ({boss_chips:,})."
            )
    elif max_hand_score > 0 and state.stage == "SELECTING_HAND" and state.blind.is_boss:
        cov_pct = (max_hand_score / boss_chips) * 100
        desc_str = f" ({best_hand_desc})" if best_hand_desc else ""
        lines.append(
            f"- **Boss Benchmark**: Run Peak Hand: **{max_hand_score:,} chips**{desc_str} vs Boss Target: **{boss_chips:,} chips** ({cov_pct:.1f}% coverage)"
        )

    # 1c. Macro-Strategic Scratchpad (placed AFTER objective facts & Reality Check to prevent self-anchoring bias)
    if scratchpad:
        if scratchpad_ante and state.ante > scratchpad_ante:
            lines.append(
                f"\n#### 📝 YOUR PREVIOUS ANTE NOTE (⚠️ WROTE IN ANTE {scratchpad_ante} — AUDIT AGAINST ANTE {state.ante} FACTS ABOVE!):\n"
                f"> *Saved Hypothesis (Ante {scratchpad_ante})*: \"{scratchpad}\"\n"
                f"> 🚨 **STALE PLAN WARNING**: Blind scaling has jumped in Ante {state.ante} (Boss target: **{boss_chips:,} chips**). "
                f"Do NOT blindly follow this old note if your damage or Jokers have changed — update `plan_notes` in Shop/Blind Select!"
            )
        else:
            update_hint = (
                "Update `plan_notes` if your build, economy target, or Boss plan changes."
                if state.stage in ("SHOP", "BLIND_SELECT")
                else "Read-only during combat: always prioritize live hand math and Boss rules above over this note!"
            )
            lines.append(
                f"\n#### 📝 YOUR MACRO-STRATEGY NOTE (SAVED IN ANTE {state.ante} SHOP / BLIND SELECT):\n"
                f"> *Working Hypothesis*: \"{scratchpad}\"\n"
                f"> *(🔍 Anti-Tunnel-Vision Check: This is your own past note, NOT a mandatory rule. {update_hint})*"
            )

    # Remaining Draw Pile Breakdown
    if state.deck_summary:
        ds = state.deck_summary
        suit_syms = {"Spades": "♠", "Hearts": "♥", "Clubs": "♣", "Diamonds": "♦"}
        rank_order = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "Jack", "Queen", "King", "Ace"]
        rank_abbr = {"Jack": "J", "Queen": "Q", "King": "K", "Ace": "A"}
        ranks_str = " ".join(f"{rank_abbr.get(r, r)}:{ds.ranks.get(r, 0)}" for r in rank_order if r in ds.ranks)

        debuff_cat, debuff_desc = get_boss_card_debuff(state.blind)
        if debuff_cat in ("Spades", "Hearts", "Diamonds", "Clubs"):
            deb_count = ds.suits.get(debuff_cat, 0)
            eff_count = max(0, ds.total_remaining - deb_count)
            suit_parts = []
            for s, c in ds.suits.items():
                if s == debuff_cat:
                    suit_parts.append(f"{suit_syms.get(s, s)}:{c} (⛔ DEBUFFED - 0 chips/mult!)")
                else:
                    suit_parts.append(f"{suit_syms.get(s, s)}:{c}")
            suits_str = " | ".join(suit_parts)
            lines.append(
                f"- **Draw Pile Deck**: **{ds.total_remaining} cards ({eff_count} EFFECTIVE / {deb_count} DEBUFFED by {state.blind.name})**: Suits: [{suits_str}] | Faces: {ds.face_cards} | Aces: {ds.aces}"
            )
            lines.append(
                f"> ⛔ **BOSS DEBUFF ALERT ({state.blind.name})**: {debuff_desc}! Debuffed cards score 0 chips and 0 mult. Do not rely on them for scoring!"
            )
        elif debuff_cat == "Face":
            deb_count = ds.face_cards
            eff_count = max(0, ds.total_remaining - deb_count)
            suits_str = " ".join(f"{suit_syms.get(s, s)}:{c}" for s, c in ds.suits.items())
            lines.append(
                f"- **Draw Pile Deck**: **{ds.total_remaining} cards ({eff_count} EFFECTIVE / {deb_count} DEBUFFED by The Plant)**: Suits: [{suits_str}] | Faces: {ds.face_cards} (⛔ DEBUFFED - 0 chips/mult!) | Aces: {ds.aces}"
            )
            lines.append(
                f"> ⛔ **BOSS DEBUFF ALERT (The Plant)**: Debuffs ALL Face cards (J, Q, K)! They score 0 chips and 0 mult. Prioritize numbered cards or Aces!"
            )
        else:
            suits_str = " ".join(f"{suit_syms.get(s, s)}:{c}" for s, c in ds.suits.items())
            lines.append(
                f"- **Draw Pile Deck ({ds.total_remaining} cards)**: Suits: [{suits_str}] | Faces: {ds.face_cards} | Aces: {ds.aces}"
            )
        if ranks_str:
            lines.append(f"  - *Remaining Ranks*: {ranks_str}")

        if ds.special_cards:
            spec_list = []
            for sc in ds.special_cards:
                s_icon = suit_syms.get(sc.suit, sc.suit)
                r_code = rank_abbr.get(sc.rank, sc.rank)
                mods = []
                if sc.enhancement:
                    mods.append(sc.enhancement.replace(" Card", ""))
                if sc.edition:
                    mods.append(sc.edition)
                if sc.seal:
                    mods.append(f"{sc.seal} Seal")
                mod_str = f" ({', '.join(mods)})" if mods else ""
                spec_list.append(f"**{r_code}{s_icon}**{mod_str}")
            lines.append(f"  - ✨ **Modified Deck Cards**: {', '.join(spec_list)}")
        elif ds.enhancements:
            lines.append(f"  - ✨ *Special*: {', '.join(f'{k}x{v}' for k, v in ds.enhancements.items())}")

    # Poker Hand Levels & Planetary Upgrades
    if state.poker_hands:
        upgraded = [h for h in state.poker_hands if h.level > 1]
        lines.append("\n#### 📊 Poker Hand Levels (Planetary Upgrades):")
        if upgraded:
            for h in upgraded:
                lines.append(f"- 🪐 **{h.name}**: **Lvl {h.level}** ({h.chips} chips x {h.mult} mult = ~{h.chips * h.mult:,} base) | Played: {h.played}x")
        else:
            # Display prominent standard hands
            common = [h for h in state.poker_hands if h.name in ("Flush", "Full House", "Straight", "Two Pair", "Pair")][:4]
            lines.append("- Base Hands: " + " | ".join(f"{h.name} Lvl {h.level} ({h.chips}x{h.mult})" for h in common))

    # Full Ante Blinds Roadmap & Skip Tags
    if state.ante_blinds:
        lines.append(f"\n#### 🗺️ ANTE {state.ante} BLINDS ROADMAP & SKIP REWARDS:")
        for b in state.ante_blinds:
            status_symbol = {
                "Select": "🎯 [CURRENT]",
                "Defeated": "✅ [DEFEATED]",
                "Skipped": "⏩ [SKIPPED]",
                "Upcoming": "⏳ [UPCOMING]",
            }.get(b.status, f"[{b.status}]")

            lines.append(f"- **{b.type} Blind: {b.name}** ({b.chips:,} chips | ${b.reward}) — {status_symbol}")
            if b.type == "Boss":
                boss_rule = get_boss_rule_reminder(b.name) or b.debuff_rule or "Special Boss effect"
                lines.append(f"  🚨 **Boss Ability**: {boss_rule} *(Cannot be skipped)*")
            elif b.tag_name:
                tag_desc = f" — {b.tag_desc}" if b.tag_desc else ""
                lines.append(f"  🎁 **Skip Reward**: **{b.tag_name}**{tag_desc}")
            else:
                lines.append("  🎁 **Skip Reward**: _None_")

    if state.stage == "BLIND_SELECT":
        lines.append("\n#### ⚔️ BLIND SELECTION:")
        lines.append("- Actions: Call `select_blind()` to play the round, or `skip_blind()` to skip and claim the Tag reward.")
        if state.blind.skip_tag_name:
            lines.append(f"- **Current Offered Skip Tag**: 🎁 **{state.blind.skip_tag_name}** — {state.blind.skip_tag_desc}")
        elif state.blind.is_boss:
            lines.append("- **Skip Tag**: _Boss Blind cannot be skipped._")
        if state.can_reroll_boss:
            lines.append("> 🎲 **BOSS REROLL AVAILABLE**: You own Director's Cut / Retcon voucher and have $10+! Call `reroll_boss_blind()` to reroll this Boss Blind if it counters your deck!")

        scoring_engine = BalatroScoringEngine()
        primary_hand = get_primary_hand_type(state)
        sample_primary = scoring_engine.get_representative_hand(primary_hand, state)
        base_primary_res = scoring_engine.calculate_expected_hand_score(sample_primary, hand_type=primary_hand, state=state)
        boss_alerts = get_boss_readiness_alert(state, primary_hand, base_primary_res.score)
        for ba in boss_alerts:
            lines.append(ba)

    # 2. Active Jokers & Synergy Analysis
    jokers_full = len(state.jokers) >= state.max_jokers
    lines.append(f"\n#### Active Jokers ({len(state.jokers)}/{state.max_jokers}):")
    if jokers_full:
        lines.append(f"> ℹ️ **Joker Slots ({len(state.jokers)}/{state.max_jokers})**: Slots are full. If you want to acquire an upgraded Joker from the shop, sell a weaker owned Joker first (`sell_joker`).")
    if not state.jokers:
        lines.append("_No Jokers owned._")
    else:
        for idx, j in enumerate(state.jokers):
            lines.append(format_joker_compact(idx, j))

        warnings = check_joker_ordering_warnings(state.jokers)
        for warn in warnings:
            lines.append(f"> ⚠️ **WARNING**: {warn}")

        # Build Imbalance / The Three-Legged Stool Warnings
        balance_warnings = check_build_balance(state)
        for warn in balance_warnings:
            lines.append(f"> {warn}")

        # Hand-Locked Jokers Warning (Clever, Sly, Crafty, etc.)
        primary_hand_type = get_primary_hand_type(state)
        locked_alerts = check_hand_locked_jokers(state, primary_hand_type)
        for alert in locked_alerts:
            lines.append(f"> {alert}")

        # Live Status for Conditional Jokers (Blackboard, Loyalty Card, Mystic Summit, Acrobat)
        conditional_statuses = get_conditional_joker_statuses(state, loyalty_remaining=loyalty_remaining)
        for status in conditional_statuses:
            lines.append(status)

        for j in state.jokers:
            j_name = (getattr(j, "label", "") or getattr(j, "name", "") or getattr(j, "key", "")).lower()
            if "even steven" in j_name or "j_even_steven" in j_name:
                lines.append("> ℹ️ **EVEN STEVEN NOTE**: Triggers +4 Mult ONLY on played even numbered cards (2, 4, 6, 8, 10). Face cards (J, Q, K) and Aces are NOT even!")
            elif "odd todd" in j_name or "j_odd_todd" in j_name:
                lines.append("> ℹ️ **ODD TODD NOTE**: Triggers +30 Chips ONLY on played odd cards (3, 5, 7, 9, A). Face cards (J, Q, K) are NOT odd!")
            elif "droll" in j_name or "j_droll" in j_name:
                lines.append("> ℹ️ **DROLL JOKER NOTE**: Requires FLUSH (5 cards of same suit) for +10 Mult. Does NOT trigger on Pairs!")
            elif "jolly" in j_name or "j_jolly" in j_name:
                lines.append("> ℹ️ **JOLLY JOKER NOTE**: Requires PAIR for +8 Mult.")

    # Check for Luchador in Jokers
    has_luchador = any("luchador" in getattr(j, "name", "").lower() or "luchador" in getattr(j, "key", "").lower() for j in state.jokers)
    if has_luchador:
        if state.blind.is_boss:
            lines.append("> 🥊 **LUCHADOR READY**: You are fighting the Boss Blind! Selling Luchador now (`sell_joker`) will DISABLE this active Boss Blind!")
        else:
            lines.append("> 🥊 **LUCHADOR NOTE**: Luchador only disables the Boss Blind while actively fighting the Boss Blind itself. Do not sell it during Small/Big Blind.")

    # 3. Consumables
    cons_full = len(state.consumables) >= state.max_consumables
    lines.append(f"\n#### Consumables ({len(state.consumables)}/{state.max_consumables} slots used):")
    if cons_full:
        lines.append(f"> ℹ️ **Consumable Slots ({len(state.consumables)}/{state.max_consumables})**: Slots are full. To buy a consumable, use (`use_consumable`) or sell (`sell_consumable`) one first.")
    if not state.consumables:
        lines.append("_No consumables owned._")
    else:
        target_tarot_keywords = (
            "empress", "hierophant", "magician", "lovers", "chariot", "justice",
            "devil", "tower", "strength", "death", "hanged", "star", "moon", "sun", "world",
            "cryptid", "aura", "talisman", "deja vu", "trance", "medium",
            "императрица", "иерофант", "маг", "влюблен", "колесница", "правосудие",
            "дьявол", "башня", "сила", "смерть", "повешен", "звезда", "луна", "солнце", "мир",
        )
        for c in state.consumables:
            c_lbl_lc = (c.label or "").lower()
            shop_warn = ""
            if state.stage == "SHOP" and any(kw in c_lbl_lc for kw in target_tarot_keywords):
                shop_warn = " **[⚠️ REQUIRES HAND CARDS — Cannot use in bare SHOP! Wait for SELECTING_HAND or open an Arcana/Standard Pack first]**"
            lines.append(f"- `{c.id}`: **{c.label}** ({c.card_type}) - {c.desc}{shop_warn}")

        planets = [c for c in state.consumables if c.card_type == "Planet"]
        if planets:
            lines.append(
                f"> 🪐 **PLANET CARDS IN INVENTORY**: You own {', '.join(f'`{p.id}` ({p.label})' for p in planets)}. "
                f"Planets provide permanent hand level upgrades to base chips and mult."
            )

        soul_card = next((c for c in state.consumables if "soul" in (c.label or "").lower() or "c_soul" in (c.id or "").lower()), None)
        if soul_card:
            if len(state.jokers) >= state.max_jokers:
                lines.append(
                    f"> 🌟 **THE SOUL IN INVENTORY (`{soul_card.id}`)**: Creates a game-winning LEGENDARY JOKER (Triboulet, Canio, Yorick, Chicot, Perkeo)!\n"
                    f"> ⚠️ **SLOT LIMIT WARNING**: Joker slots are FULL ({len(state.jokers)}/{state.max_jokers})! In Balatro, The Soul requires room ('Must have room') and will fail if used now!\n"
                    f"> 👉 **MANDATORY ACTION**: Call `sell_joker(weakest_joker_id)` first to free a slot, then call `use_consumable('{soul_card.id}')`!"
                )
            else:
                lines.append(
                    f"> 🌟 **THE SOUL IN INVENTORY (`{soul_card.id}`)**: Creates a game-winning LEGENDARY JOKER (Triboulet, Canio, Yorick, Chicot, Perkeo)!\n"
                    f"> 👉 **ACTION**: You have room ({len(state.jokers)}/{state.max_jokers}). Call `use_consumable('{soul_card.id}')` now to summon your Legendary Joker!"
                )

        if state.stage == "SELECTING_HAND":
            has_deck_mod = any(
                c.card_type == "Tarot" and any(k in c.label.lower() for k in ("moon", "sun", "star", "world", "strength", "death", "empress", "hierophant", "chariot", "justice", "tower", "lovers", "hanged"))
                for c in state.consumables
            )
            if has_deck_mod:
                lines.append("> 💡 **PROACTIVE USE**: You can use deck-modifying Tarots (The Moon, Sun, Star, World, etc.) now (`use_consumable`) before playing your winning hand. This permanently improves your deck for the run and frees consumable slots for the next shop!")

    # 3b. Redeemed Vouchers
    vouchers_owned = getattr(state, "vouchers", []) or []
    if vouchers_owned:
        lines.append(f"\n#### Redeemed Vouchers ({len(vouchers_owned)} active):")
        for v in vouchers_owned:
            lines.append(format_owned_voucher_line(v))

    # 4. Current Hand (visible in gameplay and during booster packs for Tarot/Spectral targets)
    if state.stage in ("SELECTING_HAND", "PLAYING_HAND", "OPENING_BOOSTER"):
        is_the_mark = bool(state.blind.is_boss and any(k in (state.blind.name or "").lower() for k in ("mark", "знак")))
        is_the_fish = bool(state.blind.is_boss and any(k in (state.blind.name or "").lower() for k in ("fish", "рыб")))
        upcoming_or_active_boss = (state.blind.name if state.blind.is_boss else (state.blind.upcoming_boss_name or "")).lower()
        is_pillar_ante = any(k in upcoming_or_active_boss for k in ("pillar", "столп"))
        hand_title = "Current Hand" if state.stage != "OPENING_BOOSTER" else "Current Hand (Target Candidates for Tarots / Spectrals)"
        lines.append(f"\n#### {hand_title} ({len(state.hand)} cards):")
        card_chunks = [format_card_compact(c, is_the_mark=is_the_mark, is_pillar_ante=is_pillar_ante) for c in state.hand]
        lines.append(" ".join(card_chunks))

        has_wild = any(c.enhancement == "Wild" or c.suit == "Wild" for c in state.hand)
        if has_wild:
            lines.append("> ℹ️ **WILD CARD NOTICE**: Wild cards match all 4 suits for Flushes, but retain their original numerical rank/value. Wild 3 is still rank 3, NEVER an Ace or other rank.")

        # Detect candidate poker hands only in battle
        if state.stage in ("SELECTING_HAND", "PLAYING_HAND"):
            face_down_count = sum(1 for c in state.hand if c.face_down)
            if face_down_count > 0:
                if is_the_mark:
                    lines.append(
                        f"> 👑 **THE MARK TACTICAL INTEL ({face_down_count}/{len(state.hand)} cards face-down)**:\n"
                        f"> • **BOSS RULE**: The Mark draws ALL Face Cards (J, Q, K) face-down. Non-face cards stay face-up!\n"
                        f"> • **CRITICAL FACT**: Every single face-down card is **100% GUARANTEED to be a Face Card (Jack, Queen, or King)** with 10 nominal chips!\n"
                        f"> • **JOKER SYNERGY**: Face-down face cards STILL score full chips/mult and trigger face-card Jokers (Scary Face +30c, Smiley Face +5m, Photograph X2, Sock and Buskin)!\n"
                        f"> • **DO NOT DISCARD FACE-DOWN CARDS AS 'JUNK'**: On The Mark, they are guaranteed 10 chips and trigger face-card Jokers! However, exact ranks and suits are hidden under Fog of War, so they cannot be paired with visible cards."
                    )
                else:
                    lines.extend(format_draw_profile_intel(state, face_down_count, is_fish=is_the_fish))

            # Active or Pre-Boss debuff audit for The Pillar
            if state.blind.is_boss and any(k in (state.blind.name or "").lower() for k in ("pillar", "столп")):
                debuffed_in_hand = [c for c in state.hand if c.debuffed]
                clean_in_hand = [c for c in state.hand if not c.debuffed and not c.face_down]
                clean_str = ", ".join(format_card_compact(c, is_the_mark=is_the_mark) for c in clean_in_hand) if clean_in_hand else "_None!_"
                deb_str = ", ".join(format_card_compact(c, is_the_mark=is_the_mark) for c in debuffed_in_hand) if debuffed_in_hand else "_None_"
                lines.append(
                    f"> 🏛️ **THE PILLAR AUDIT**: Hand has {len(clean_in_hand)} FRESH card(s) and {len(debuffed_in_hand)} DEBUFFED card(s)!\n"
                    f">   • Fresh/Clean cards: [{clean_str}]\n"
                    f">   • ⚠️ Debuffed cards (give 0 chips/mult, do NOT build hands around them!): [{deb_str}]"
                )
            elif not state.blind.is_boss and is_pillar_ante:
                played_in_hand = [c for c in state.hand if getattr(c, "played_this_ante", False)]
                fresh_in_hand = [c for c in state.hand if not getattr(c, "played_this_ante", False) and not c.face_down]
                fresh_deck = sum(1 for dc in (state.deck_summary.cards if state.deck_summary else []) if not getattr(dc, "played_this_ante", False))
                played_deck = sum(1 for dc in (state.deck_summary.cards if state.deck_summary else []) if getattr(dc, "played_this_ante", False))
                lines.append(
                    f"> 🏛️ **THE PILLAR FRESHNESS TRACKER (Upcoming Boss: {state.blind.upcoming_boss_name})**:\n"
                    f">   • Hand: **{len(fresh_in_hand)} FRESH** vs **{len(played_in_hand)} ALREADY PLAYED THIS ANTE** | Draw Pile: **{fresh_deck} fresh** / {played_deck} already played.\n"
                    f">   • 💡 Any card you **PLAY** now will be debuffed on The Pillar (discarded cards stay FRESH!). Clear this blind first, and prefer playing already-used (`🏛️PLAYED_THIS_ANTE`) or low cards when possible."
                )

            remaining_score = max(0, state.target_score - state.current_score)
            scoring_engine = BalatroScoringEngine()
            eval_hand = [c for c in state.hand if not c.face_down]
            candidate_hands = scoring_engine.evaluate_candidate_hands(eval_hand, state=state)
            if candidate_hands and remaining_score > 0:
                top = candidate_hands[0]
                top_score = max(1, top.score)
                avg_req = remaining_score // max(1, state.hands_left)
                projected_total = top_score * state.hands_left

                if top_score >= remaining_score:
                    traj_str = f"🎯 Clears blind immediately in 1 hand with {top.hand_type} (~{top_score:,} pts)."
                elif projected_total < remaining_score:
                    deficit = remaining_score - projected_total
                    if state.discards_left > 0:
                        traj_str = (
                            f"🚨 BEHIND PACE: Current best ({top.hand_type} ~{top_score:,} pts) yields max ~{projected_total:,} across {state.hands_left} hands "
                            f"(-{deficit:,} deficit vs {remaining_score:,} needed; required pace is ~{avg_req:,} pts/hand). Dig with discards ({state.discards_left} left) for higher tier hand!"
                        )
                    else:
                        traj_str = (
                            f"🚨 CRITICAL DEFICIT: Max ~{projected_total:,} across {state.hands_left} hands "
                            f"(-{deficit:,} deficit vs {remaining_score:,} needed, 0 discards left; required pace was ~{avg_req:,} pts/hand)."
                        )
                else:
                    hands_needed = math.ceil(remaining_score / top_score)
                    margin = projected_total - remaining_score
                    traj_str = (
                        f"✅ ON PACE: Clears in ~{hands_needed}/{state.hands_left} hands at ~{top_score:,} pts/hand with {top.hand_type} "
                        f"(required pace: ~{avg_req:,} pts/hand, safety margin: +{margin:,})."
                    )

                lines.append(
                    f"> 📊 **Round Trajectory**: {traj_str} (Target: {remaining_score:,} chips remaining, need ~{avg_req:,}/hand across {state.hands_left} hands)"
                )

                if state.discards_left == 1 and state.hands_left >= 2 and top_score < remaining_score:
                    made_five_card = top.hand_type in (
                        "Flush",
                        "Straight",
                        "Full House",
                        "Four of a Kind",
                        "Straight Flush",
                        "Five of a Kind",
                        "Flush House",
                        "Flush Five",
                    )
                    made_hint = (
                        f" You already hold a made **{top.hand_type}** (~{top_score:,} pts)—do NOT burn your last discard chasing marginal kicker upgrades if you will need to draw another hand afterward!"
                        if made_five_card and top_score >= avg_req * 0.6
                        else " Keep in mind that using this discard will leave you with 0 discards for all remaining hands."
                    )
                    lines.append(
                        f"> ⚠️ **RESOURCE PACING REMINDER**: You have only **1 discard remaining** with **{state.hands_left} hands left**!{made_hint}"
                    )
                elif state.discards_left == 0 and state.hands_left >= 2 and top_score < remaining_score:
                    has_half_joker = any(
                        not j.debuffed and ((j.key or "").lower() == "j_half" or "half joker" in (j.label or "").lower())
                        for j in (state.jokers or [])
                    )
                    max_pad = 3 if has_half_joker else 5
                    lines.append(
                        f"> 💡 **CARD CYCLING / PSEUDO-DISCARD REMINDER (0 discards left, {state.hands_left} hands left)**: "
                        f"You have **0 discards remaining** and cannot one-shot the blind this hand. "
                        f"If you play a fallback hand (`High Card`, `Pair`, `Two Pair`), **NEVER play 1–2 cards alone 'for cleanliness'**! "
                        f"Always pad `play_cards` up to {max_pad} cards by attaching dead/off-suit trash cards you don't want to keep "
                        f"(for `High Card`, attach trash cards of **lower rank** than your scoring card so it stays the High Card) "
                        f"to cycle them out for free and draw fresh cards for your remaining {state.hands_left - 1} hand(s)."
                    )

            if candidate_hands:
                has_splash = any(
                    not j.debuffed and ((j.key or "").lower() == "j_splash" or "splash" in (j.label or "").lower())
                    for j in (state.jokers or [])
                )
                lines.append("\n**Detected Hand Opportunities (Calculated with Active Jokers & Upgrades):**")
                for cand in candidate_hands[:4]:
                    card_ids_str = ", ".join(c.id for c in cand.cards)
                    breakdown_str = " | ".join(cand.breakdown)
                    status_part = f" — {cand.threshold_status}" if cand.threshold_status else ""
                    scoring_core = list(cand.cards) if has_splash else scoring_engine.get_scoring_cards(cand.cards, cand.hand_type)
                    if 0 < len(scoring_core) < len(cand.cards):
                        core_ids_set = {c.id for c in scoring_core}
                        core_ids_str = ", ".join(c.id for c in scoring_core)
                        kicker_ids_str = ", ".join(c.id for c in cand.cards if c.id not in core_ids_set)
                        cards_line = (
                            f"  └─ Cards: `[{card_ids_str}]` (Scoring Core: `[{core_ids_str}]` | "
                            f"Non-Scoring Throwaways: `[{kicker_ids_str}]` — keep cards needed for next hand, attach dead cards to cycle!) | Synergy: [{breakdown_str}]"
                        )
                    else:
                        cards_line = f"  └─ Cards: `[{card_ids_str}]` | Synergy: [{breakdown_str}]"
                    lines.append(
                        f"- **{cand.hand_type}** (~{cand.chips:,}c × {cand.mult:,}m = **~{cand.score:,} score**){status_part}\n"
                        f"{cards_line}"
                    )

            # Check if held-in-hand triggers or cards exist
            has_held_triggers = any(
                c.enhancement == "Steel" for c in state.hand
            ) or any(
                getattr(j, "key", "") in ("j_baron", "j_shoot_the_moon", "j_mime", "j_raised_fist", "j_reserve_parking")
                for j in state.jokers
            )
            if has_held_triggers:
                lines.append(
                    "> 💡 **HELD-IN-HAND CARD ORDER**: Unplayed cards held in hand trigger left-to-right! "
                    "Use `reorder_hand(card_order=...)` if you need to place flat mult cards (Shoot the Moon Queens) left of xMult (Baron Kings / Steel cards)."
                )

    # 5. Open Booster Pack Options
    if state.stage == "OPENING_BOOSTER":
        lines.append(f"\n#### 📦 OPEN BOOSTER PACK (Picks Remaining: {state.pack_choices}):")
        lines.append("> Actions available: `select_pack_card(card_id, target_card_ids=...)` or `skip_pack()`.")
        if len(state.jokers) >= state.max_jokers and any(item.item_type in ("Joker",) or "Joker" in item.item_type or "Buffoon" in item.label for item in state.pack_cards):
            lines.append(
                f"> ⛔ **JOKER SLOTS FULL ({len(state.jokers)}/{state.max_jokers})**: "
                "Your Joker slots are already at capacity! You CANNOT select a Joker from this pack. "
                "Call `skip_pack()` immediately to exit without violating slot limits."
            )
        if not state.pack_cards:
            lines.append("_Cards are dealing or none remaining._")
        else:
            for item in state.pack_cards:
                pack_desc = clean_joker_desc(
                    key=item.id,
                    label=item.label,
                    raw_desc=item.desc,
                    effect_type="x_mult" if any(k in item.label.lower() for k in ("constellation", "hologram", "cavendish", "card sharp", "duo", "trio", "family", "order", "tribe", "baron", "obelisk", "vampire", "madness")) else "",
                ) if (item.item_type in ("Joker", "joker") or "buffoon" in item.label.lower()) else item.desc
                if getattr(item, "edition", None):
                    ed_desc = CARD_EDITION_DESCS.get(item.edition.lower(), item.edition)
                    ed_badge = f" **[{ed_desc.upper()}]**"
                else:
                    ed_badge = ""
                lines.append(
                    f"- Slot [{item.index}] `{item.id}`: **{item.label}**{ed_badge} ({item.item_type}) - {pack_desc}"
                )

            neg_in_pack = next((item for item in state.pack_cards if (getattr(item, "edition", None) or "").lower() == "negative" or (item.desc and "negative" in item.desc.lower())), None)
            if neg_in_pack:
                lines.append(
                    f"> 🌌 **NEGATIVE JOKER IN PACK (Slot [{neg_in_pack.index}] `{neg_in_pack.id}` - {neg_in_pack.label})**:\n"
                    f"> • Edition is **Negative** (+1 permanent Joker slot, takes 0 Joker capacity).\n"
                    f"> • Note: Taking a Negative Joker expands your total Joker capacity without occupying standard slots."
                )
            if any(item.item_type == "Spectral" or "Spectral" in item.label for item in state.pack_cards):
                lines.append(
                    "> 🔮 **SPECTRAL RULES**: Cards modifying 1 card (Cryptid, Aura, Talisman, Deja Vu, Trance, Medium) require `target_card_ids=['c_...']`! "
                    "Beware side effects: Ankh/Hex destroy all other Jokers; Wraith wipes cash to $0; Ouija/Ectoplasm reduce hand size -1."
                )
            if any(item.item_type == "Tarot" or "Arcana" in item.label for item in state.pack_cards):
                lines.append(
                    "> 🃏 **TAROT RULES**: Card-modifying Tarots (Empress, Hierophant, Magician, Lovers, Chariot, Justice, Tower, Devil, Strength, Death, Hanged Man, Moon, Sun, Star, World) "
                    "REQUIRE `target_card_ids=['c_...']` from your Current Hand cards listed above! E.g. for Empress select 2 cards: `target_card_ids=['c_...', 'c_...']`."
                )

            soul_in_pack = next((item for item in state.pack_cards if "soul" in (item.label or "").lower() or "c_soul" in (item.id or "").lower()), None)
            if soul_in_pack:
                lines.append(
                    f"> 🌟🌟 **LEGENDARY DISCOVERY: THE SOUL (Slot [{soul_in_pack.index}])**! 🌟🌟\n"
                    f"> • Creates a random game-breaking LEGENDARY JOKER (Triboulet, Canio, Yorick, Chicot, Perkeo)!\n"
                    f"> • **MANDATORY ACTION**: Select `{soul_in_pack.id}` immediately (`select_pack_card('{soul_in_pack.id}')`)! Highest-value card in the entire game!"
                )
            black_hole_in_pack = next((item for item in state.pack_cards if "black hole" in (item.label or "").lower() or "c_black_hole" in (item.id or "").lower()), None)
            if black_hole_in_pack:
                lines.append(
                    f"> 🌌 **SPECTRAL PHENOMENON: BLACK HOLE (Slot [{black_hole_in_pack.index}])**!\n"
                    f"> • Instantly levels up EVERY poker hand by +1 permanently!\n"
                    f"> • **MANDATORY ACTION**: Select `{black_hole_in_pack.id}` immediately (`select_pack_card('{black_hole_in_pack.id}')`)!"
                )

            # Planet hand match recommendations in booster
            planet_items = [it for it in state.pack_cards if it.item_type == "Planet" or "planet" in it.label.lower()]
            if planet_items:
                primary_hand = get_primary_hand_type(state)
                match_planet = next((it for it in planet_items if PLANET_HAND_MAP.get((it.label or "").lower(), (None,))[0] == primary_hand or primary_hand.lower() in (it.desc or "").lower()), None)
                if match_planet:
                    lines.append(
                        f"> 🪐 **TARGET PLANET RECOMMENDATION**: Slot [{match_planet.index}] `{match_planet.label}` directly levels up your primary hand **{primary_hand}**! Prioritize this pick to scale your base score."
                    )

            # Resource Tarot recommendations in booster
            tarot_items = [it for it in state.pack_cards if it.item_type == "Tarot"]
            if tarot_items:
                open_cons = max(0, state.max_consumables - len(state.consumables))
                for it in tarot_items:
                    l_it = (it.label or "").lower()
                    if "high priestess" in l_it or "жрица" in l_it:
                        if open_cons < 2:
                            lines.append(f"> 🔮 **THE HIGH PRIESTESS (Slot [{it.index}])**: Generates 2 random Planet cards! ⚠️ Warning: you have only {open_cons} open consumable slot(s).")
                        else:
                            lines.append(f"> 🔮 **THE HIGH PRIESTESS (Slot [{it.index}])**: Generates 2 random Planet cards! Excellent value with your {open_cons} open consumable slots.")
                    elif "emperor" in l_it or "император" in l_it:
                        if open_cons < 2:
                            lines.append(f"> 🔮 **THE EMPEROR (Slot [{it.index}])**: Generates 2 random Tarot cards! ⚠️ Warning: you have only {open_cons} open consumable slot(s).")
                        else:
                            lines.append(f"> 🔮 **THE EMPEROR (Slot [{it.index}])**: Generates 2 random Tarot cards! High utility with your {open_cons} open consumable slots.")
                    elif "judgement" in l_it or "суд" in l_it:
                        open_jokers = max(0, state.max_jokers - len(state.jokers))
                        if open_jokers == 0:
                            lines.append(f"> 🔮 **JUDGEMENT (Slot [{it.index}])**: Generates 1 random Joker. ⚠️ Warning: Joker slots are FULL ({len(state.jokers)}/{state.max_jokers})!")
                        else:
                            lines.append(f"> 🔮 **JUDGEMENT (Slot [{it.index}])**: Generates 1 random Joker! You have {open_jokers} open Joker slot(s).")
                    elif "hermit" in l_it or "отшельник" in l_it:
                        gain = min(20, max(0, state.dollars))
                        lines.append(f"> 💰 **THE HERMIT (Slot [{it.index}])**: Instantly doubles current cash by +${gain} (max +$20)! Instant high-EV economy pick.")

            # Standard Pack playing cards advice
            playing_cards = [it for it in state.pack_cards if it.item_type == "PlayingCard"]
            if playing_cards:
                def is_card_special(desc_str: str) -> bool:
                    d = (desc_str or "").lower()
                    if "no enhancements" in d:
                        return False
                    return any(k in d for k in ("steel", "glass", "polychrome", "foil", "holographic", "seal:", "gold card", "bonus card", "mult card", "wild card", "lucky card", "stone card"))

                special_cards = [it for it in playing_cards if is_card_special(it.desc)]
                if special_cards:
                    spec_names = ", ".join(f"Slot [{it.index}] {it.label} ({it.desc})" for it in special_cards[:2])
                    lines.append(f"> 🎴 **ENHANCED PLAYING CARDS**: Premium modified cards detected: {spec_names}. Excellent to add to deck!")
                else:
                    lines.append(f"> ⚠️ **STANDARD PACK - ALL UNBUFFED CARDS**: None of the offered cards have enhancements, editions, or seals. Adding plain cards causes deck dilution (harder to draw key pairs/combos). If none synergize, call `skip_pack()` to keep your deck lean!")

    # 6. Shop Options
    if state.stage == "SHOP" and state.shop:
        lines.append(f"\n#### Shop Offerings (Reroll Cost: ${state.shop.reroll_cost}):")
        if jokers_full:
            lines.append(f"> ℹ️ **Joker Slots ({len(state.jokers)}/{state.max_jokers})**: Slots are full. To buy a shop Joker, sell a weaker owned Joker (`sell_joker`) first to replace it.")
        if cons_full:
            lines.append(f"> ℹ️ **Consumable Slots ({len(state.consumables)}/{state.max_consumables})**: Slots are full. To buy a consumable, use or sell an owned consumable first.")
        if not state.shop.items:
            lines.append("_Shop is sold out._")
        else:
            scoring_engine = BalatroScoringEngine()
            primary_hand = get_primary_hand_type(state)
            next_blind_name, target_chips = get_upcoming_blind_target(state)
            boss_eval_state, boss_name_eval, _ = get_boss_eval_state(state)
            is_next_boss = bool(boss_name_eval and next_blind_name.lower() == boss_name_eval.lower())
            eval_state_for_next = boss_eval_state if is_next_boss else state
            sample_primary = scoring_engine.get_representative_hand(primary_hand, eval_state_for_next)
            base_primary_res = scoring_engine.calculate_expected_hand_score(sample_primary, hand_type=primary_hand, state=eval_state_for_next)
            normal_primary_res = (
                scoring_engine.calculate_expected_hand_score(sample_primary, hand_type=primary_hand, state=state)
                if is_next_boss
                else base_primary_res
            )

            hands_count = state.hands_left if state.hands_left > 0 else 4
            max_round_pot = base_primary_res.score * hands_count
            deficit_pot = target_chips - max_round_pot
            if deficit_pot > 0:
                pot_str = f" | ⚠️ Max Round Ceiling ({hands_count} hands): **~{max_round_pot:,} chips** (-{deficit_pot:,} deficit vs {target_chips:,})"
            else:
                pot_str = f" | Max Round Potential ({hands_count} hands): **~{max_round_pot:,} chips** (sufficient)"

            lines.append(
                f"> 📊 **CURRENT BUILD BENCHMARK**: Primary Hand: **{primary_hand}** "
                f"(~{base_primary_res.score:,} score/hand) | Target to Beat: **{next_blind_name}** ({target_chips:,} chips){pot_str}"
            )

            boss_alerts = get_boss_readiness_alert(state, primary_hand, normal_primary_res.score)
            for ba in boss_alerts:
                lines.append(ba)

            is_money_build, money_build_desc = get_money_scaling_status(state, scratchpad)

            vouchers_owned = set(getattr(state, "vouchers", []) or [])
            if "v_money_tree" in vouchers_owned:
                eff_cap = 100
                max_interest_amt = 20
            elif "v_seed_money" in vouchers_owned:
                eff_cap = 50
                max_interest_amt = 10
            else:
                eff_cap = getattr(state, "interest_cap", 25) or 25
                max_interest_amt = eff_cap // 5

            if is_money_build:
                lines.append(
                    f"> 💎 **MONEY-SCALING BUILD ACTIVE**: {money_build_desc}! "
                    f"Your bank balance (${state.dollars}) directly powers your scoring Jokers. "
                    f"Do NOT spend money lightly — spending cash directly reduces your combat Chips/Mult! "
                    f"Only buy items or packs if their permanent value strictly exceeds the lost combat stats."
                )
            elif state.dollars > eff_cap and "green" not in (state.deck_name or "").lower():
                surplus = state.dollars - eff_cap
                lines.append(
                    f"> 💰 **SURPLUS CAPITAL NOTICE (${state.dollars})**: You have **${surplus}** surplus above the ${eff_cap} max interest threshold! "
                    f"You earn the maximum ${max_interest_amt} interest every round as long as you maintain ${eff_cap}+. "
                    f"Unspent surplus above ${eff_cap} earns $0 in interest — investing surplus into Booster Packs (Celestial for Planet levels, Arcana for Tarots) or Shop upgrades scales your deck without costing a single dollar of interest!"
                )

            for item in state.shop.items:
                if state.dollars < item.cost:
                    afford_badge = "❌ (Too expensive)"
                elif item.item_type == "Joker" and jokers_full:
                    afford_badge = "(Slots full - sell a joker to buy)"
                elif item.item_type in ("Tarot", "Planet", "Spectral") and cons_full:
                    afford_badge = "(Slots full - use/sell a consumable to buy)"
                else:
                    afford_badge = "✅"
                pack_tag = f" [{get_pack_category(item.label)}]" if (item.item_type in ("Booster", "BoosterPack") or "pack" in item.label.lower()) else ""
                item_desc = clean_joker_desc(
                    key=item.id,
                    label=item.label,
                    raw_desc=item.desc,
                    effect_type="x_mult" if any(k in item.label.lower() for k in ("constellation", "hologram", "cavendish", "card sharp", "duo", "trio", "family", "order", "tribe", "baron", "obelisk", "vampire", "madness")) else "",
                ) if item.item_type == "Joker" else item.desc
                if getattr(item, "edition", None):
                    ed_desc = CARD_EDITION_DESCS.get(item.edition.lower(), item.edition)
                    ed_badge = f" **[{ed_desc.upper()}]**"
                else:
                    ed_badge = ""
                lines.append(
                    f"- Slot [{item.index}] `{item.id}`: **{item.label}**{ed_badge} ({item.item_type}{pack_tag}) - "
                    f"${item.cost} {afford_badge} | {item_desc}"
                )
                if (getattr(item, "edition", None) or "").lower() == "negative":
                    lines.append(
                        f"> 🌌 **NEGATIVE JOKER IN SHOP (Slot [{item.index}] `{item.label}`)**: "
                        f"Has Negative edition (+1 Joker slot, takes 0 Joker capacity). Adds free slot capacity if purchased."
                    )

                if item.item_type == "Joker":
                    proj = project_shop_joker_impact(item, state, primary_hand, scoring_engine)
                    if proj:
                        lines.append(proj)
                elif item.item_type == "Planet":
                    proj = project_planet_impact(item, state, scoring_engine)
                    if proj:
                        lines.append(proj)
                elif item.item_type in ("Booster", "BoosterPack") or "pack" in item.label.lower():
                    proj = project_booster_impact(item, state, primary_hand, scoring_engine)
                    if proj:
                        lines.append(proj)
                elif item.item_type == "Voucher" or "voucher" in item.id.lower() or "voucher" in item.label.lower():
                    proj = project_voucher_impact(item, state)
                    if proj:
                        lines.append(proj)

            shop_recs = get_shop_recommendations_for_balance(state)
            for rec in shop_recs:
                lines.append(rec)

    # 7. Lorebook Situational Insights
    if not state.game_over and not state.victory:
        lorebook_md = get_lorebook_insights_for_state(state, max_entries=2)
        if lorebook_md:
            lines.append("\n" + lorebook_md)

    # 8. Victory / Defeat status
    if state.game_over:
        lines.append("\n🚨 **GAME OVER**: You have run out of hands!")
    elif state.victory:
        lines.append("\n🏆 **VICTORY**: Ante 8 Defeated!")

    return "\n".join(lines)
