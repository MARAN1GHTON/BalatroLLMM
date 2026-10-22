"""Deterministic scoring calculator and poker hand evaluator for Balatro."""

from collections import Counter
from itertools import combinations
import math
import re
from typing import Sequence, NamedTuple
from ..bridge.schema import CardModel, GameState, JokerModel

# Standard Balatro base poker hands: (Base Chips, Base Mult)
POKER_HAND_BASE = {
    "Flush Five": (160, 16),
    "Flush House": (140, 14),
    "Five of a Kind": (120, 12),
    "Straight Flush": (100, 8),
    "Four of a Kind": (60, 7),
    "Full House": (40, 4),
    "Flush": (35, 4),
    "Straight": (30, 4),
    "Three of a Kind": (30, 3),
    "Two Pair": (20, 2),
    "Pair": (10, 2),
    "High Card": (5, 1),
}

RANK_VALUES = {
    "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
    "10": 10, "Jack": 10, "Queen": 10, "King": 10, "Ace": 11,
}

RANK_ORDER = {
    "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
    "10": 10, "Jack": 11, "Queen": 12, "King": 13, "Ace": 14,
}


class HandEvaluationResult(NamedTuple):
    """Complete scoring projection for a candidate poker hand."""
    hand_type: str
    cards: list[CardModel]
    chips: int
    mult: int
    score: int
    breakdown: list[str]
    threshold_status: str


class BalatroScoringEngine:
    """Evaluates played cards, detects poker hands, and estimates scoring with full Joker synergy."""

    @staticmethod
    def get_rank_value(rank: str) -> int:
        return RANK_VALUES.get(rank, 0)

    @staticmethod
    def get_rank_order(rank: str) -> int:
        return RANK_ORDER.get(rank, 0)

    def identify_poker_hand(self, cards: Sequence[CardModel]) -> tuple[str, int, int]:
        """Identifies poker hand type and returns (Hand Name, Base Chips, Base Mult)."""
        if not cards:
            return ("High Card", 5, 1)

        # Distinct ranks for face-down cards to prevent artificial pair matching
        def _get_scoring_rank(c: CardModel) -> str:
            if getattr(c, "face_down", False):
                return f"?_{getattr(c, 'id', id(c))}"
            er = c.effective_rank if hasattr(c, "effective_rank") else c.rank
            if er in ("?", "Face"):
                return f"?_{getattr(c, 'id', id(c))}"
            return er

        counts = Counter(_get_scoring_rank(c) for c in cards)
        suits = Counter(c.effective_suit if hasattr(c, "effective_suit") else c.suit for c in cards if (c.effective_suit if hasattr(c, "effective_suit") else c.suit) != "None")
        wild_count = sum(1 for c in cards if (c.effective_suit if hasattr(c, "effective_suit") else c.suit) == "Wild" or c.enhancement == "Wild")

        # Flush check
        is_flush = False
        if len(cards) >= 5:
            for s, count in suits.items():
                if count + wild_count >= 5:
                    is_flush = True
                    break

        # Straight check
        sorted_ranks = sorted({self.get_rank_order(c.effective_rank if hasattr(c, "effective_rank") else c.rank) for c in cards})
        is_straight = False
        if len(sorted_ranks) >= 5:
            # Check standard 5-in-a-row
            for i in range(len(sorted_ranks) - 4):
                if sorted_ranks[i + 4] - sorted_ranks[i] == 4:
                    is_straight = True
                    break
            # Ace-low straight (A, 2, 3, 4, 5 -> 14, 2, 3, 4, 5)
            if not is_straight and 14 in sorted_ranks:
                low_ranks = sorted([1 if r == 14 else r for r in sorted_ranks])
                for i in range(len(low_ranks) - 4):
                    if low_ranks[i + 4] - low_ranks[i] == 4:
                        is_straight = True
                        break

        freqs = sorted(counts.values(), reverse=True)

        # Classify hands
        if is_flush and freqs[0] == 5:
            return ("Flush Five", *POKER_HAND_BASE["Flush Five"])
        if is_flush and len(cards) == 5 and freqs[0] == 3 and freqs[1] == 2:
            return ("Flush House", *POKER_HAND_BASE["Flush House"])
        if freqs[0] == 5:
            return ("Five of a Kind", *POKER_HAND_BASE["Five of a Kind"])
        if is_flush and is_straight:
            return ("Straight Flush", *POKER_HAND_BASE["Straight Flush"])
        if freqs[0] == 4:
            return ("Four of a Kind", *POKER_HAND_BASE["Four of a Kind"])
        if len(freqs) >= 2 and freqs[0] == 3 and freqs[1] >= 2:
            return ("Full House", *POKER_HAND_BASE["Full House"])
        if is_flush:
            return ("Flush", *POKER_HAND_BASE["Flush"])
        if is_straight:
            return ("Straight", *POKER_HAND_BASE["Straight"])
        if freqs[0] == 3:
            return ("Three of a Kind", *POKER_HAND_BASE["Three of a Kind"])
        if len(freqs) >= 2 and freqs[0] == 2 and freqs[1] == 2:
            return ("Two Pair", *POKER_HAND_BASE["Two Pair"])
        if freqs[0] == 2:
            return ("Pair", *POKER_HAND_BASE["Pair"])

        return ("High Card", *POKER_HAND_BASE["High Card"])

    @staticmethod
    def get_scoring_cards(combo: Sequence[CardModel], hand_type: str) -> list[CardModel]:
        """Filters played cards down to only those that actually score chips in Balatro."""
        def _get_scoring_rank(c: CardModel) -> str:
            if getattr(c, "face_down", False):
                return f"?_{getattr(c, 'id', id(c))}"
            er = c.effective_rank if hasattr(c, "effective_rank") else c.rank
            if er in ("?", "Face"):
                return f"?_{getattr(c, 'id', id(c))}"
            return er

        counts = Counter(_get_scoring_rank(c) for c in combo)
        def card_rank(c: CardModel) -> str:
            return _get_scoring_rank(c)
        def card_nom(c: CardModel) -> int:
            return c.effective_nominal if hasattr(c, "effective_nominal") else c.nominal

        if hand_type == "High Card":
            scored = [max(combo, key=lambda c: (RANK_ORDER.get(card_rank(c), 0), card_nom(c)))]
            stone = [c for c in combo if c.enhancement == "Stone" and c not in scored]
            return scored + stone
        elif hand_type == "Pair":
            pair_rank = max((r for r, count in counts.items() if count >= 2), key=lambda r: RANK_ORDER.get(r, 0), default=None)
            scored = [c for c in combo if card_rank(c) == pair_rank][:2] if pair_rank else list(combo[:2])
            stone = [c for c in combo if c.enhancement == "Stone" and c not in scored]
            return scored + stone
        elif hand_type == "Two Pair":
            pair_ranks = sorted([r for r, count in counts.items() if count >= 2], key=lambda r: RANK_ORDER.get(r, 0), reverse=True)[:2]
            scored = []
            for r in pair_ranks:
                scored.extend([c for c in combo if card_rank(c) == r][:2])
            stone = [c for c in combo if c.enhancement == "Stone" and c not in scored]
            return scored + stone
        elif hand_type == "Three of a Kind":
            triplet_rank = max((r for r, count in counts.items() if count >= 3), key=lambda r: RANK_ORDER.get(r, 0), default=None)
            scored = [c for c in combo if card_rank(c) == triplet_rank][:3] if triplet_rank else list(combo[:3])
            stone = [c for c in combo if c.enhancement == "Stone" and c not in scored]
            return scored + stone
        elif hand_type == "Four of a Kind":
            quad_rank = max((r for r, count in counts.items() if count >= 4), key=lambda r: RANK_ORDER.get(r, 0), default=None)
            scored = [c for c in combo if card_rank(c) == quad_rank][:4] if quad_rank else list(combo[:4])
            stone = [c for c in combo if c.enhancement == "Stone" and c not in scored]
            return scored + stone
        else:
            # Full House, Flush, Straight, Straight Flush, Five of a Kind, Flush House, Flush Five
            return list(combo)

    def calculate_expected_hand_score(
        self,
        cards: Sequence[CardModel],
        hand_type: str | None = None,
        state: GameState | None = None,
    ) -> HandEvaluationResult:
        """Projects expected final Chips, Mult, and Total Score taking into account active Jokers,
        Planetary hand upgrades, card enhancements/editions, and Boss Blind constraints."""
        if not cards:
            return HandEvaluationResult("High Card", [], 5, 1, 5, ["Empty hand"], "")

        if hand_type is None:
            hand_type, def_chips, def_mult = self.identify_poker_hand(cards)
        else:
            def_chips, def_mult = POKER_HAND_BASE.get(hand_type, (5, 1))

        base_chips = def_chips
        base_mult = def_mult
        hand_played = 0

        # Incorporate Planet upgrades from state.poker_hands
        if state and state.poker_hands:
            for ph in state.poker_hands:
                if ph.name.lower() == hand_type.lower():
                    if ph.chips > 0 and ph.mult > 0:
                        base_chips = ph.chips
                        base_mult = ph.mult
                    hand_played = ph.played
                    break

        breakdown: list[str] = [f"Base {base_chips}x{base_mult}"]

        # Boss Blind debuff check on Base Chips/Mult
        boss_name = (state.blind.name or "").lower() if (state and state.blind and state.blind.is_boss) else ""
        if "flint" in boss_name or "кремень" in boss_name:
            base_chips = max(1, base_chips // 2)
            base_mult = max(1, base_mult // 2)
            breakdown.append("TheFlint: -50%Base")

        # Boss card debuff filters
        is_plant = "plant" in boss_name or "растение" in boss_name
        is_club_boss = "club" in boss_name or "треф" in boss_name
        is_goad = "goad" in boss_name or "пик" in boss_name
        is_window = "window" in boss_name or "буб" in boss_name
        is_head = "head" in boss_name or "черв" in boss_name

        def is_card_debuffed(c: CardModel) -> bool:
            if c.debuffed:
                return True
            c_rank = c.effective_rank if hasattr(c, "effective_rank") else c.rank
            c_suit = c.effective_suit if hasattr(c, "effective_suit") else c.suit
            if is_plant and (c.is_face_card if hasattr(c, "is_face_card") else c_rank in ("Jack", "Queen", "King")):
                return True
            if is_club_boss and (c_suit == "Clubs" or c.enhancement == "Wild"):
                return True
            if is_goad and (c_suit == "Spades" or c.enhancement == "Wild"):
                return True
            if is_window and (c_suit == "Diamonds" or c.enhancement == "Wild"):
                return True
            if is_head and (c_suit == "Hearts" or c.enhancement == "Wild"):
                return True
            return False

        active_jokers = [j for j in (state.jokers if state else []) if not j.debuffed]
        has_splash = any(j.key == "j_splash" or "splash" in j.label.lower() for j in active_jokers)
        if has_splash:
            scoring_cards = list(cards)
        else:
            scoring_cards = self.get_scoring_cards(cards, hand_type)

        total_chips = base_chips
        total_mult = float(base_mult)
        x_mult_factor = 1.0

        card_chips_added = 0
        card_mult_added = 0
        # Track first scored face card ID for Photograph (triggers on base scoring and all its retriggers)
        first_face_card_id: str | None = None

        # Check retrigger Jokers
        has_hack = any(j.key == "j_hack" or "hack" in j.label.lower() for j in active_jokers)
        has_sock = any(j.key == "j_sock_and_buskin" or "sock and buskin" in j.label.lower() for j in active_jokers)
        has_dusk = any(j.key == "j_dusk" or "dusk" in j.label.lower() for j in active_jokers) and (state and state.hands_left == 1)
        has_hanging_chad = any(j.key == "j_hanging_chad" or "hanging chad" in j.label.lower() or "chad" in j.label.lower() for j in active_jokers)

        # 1. Scored Cards Evaluation
        for card_idx, card in enumerate(scoring_cards):
            if is_card_debuffed(card):
                continue

            c_rank = card.effective_rank if hasattr(card, "effective_rank") else card.rank
            c_suit = card.effective_suit if hasattr(card, "effective_suit") else card.suit
            c_nom = card.effective_nominal if hasattr(card, "effective_nominal") else card.nominal
            is_face = card.is_face_card if hasattr(card, "is_face_card") else c_rank in ("Jack", "Queen", "King")

            if is_face and first_face_card_id is None:
                first_face_card_id = card.id or f"face_{card_idx}"

            triggers = 1
            if card.seal == "Red":
                triggers += 1
            if card_idx == 0 and has_hanging_chad:
                triggers += 2
            if has_hack and c_rank in ("2", "3", "4", "5"):
                triggers += 1
            if has_sock and is_face:
                triggers += 1
            if has_dusk:
                triggers += 1

            for _ in range(triggers):
                c_chips = c_nom + card.chip_bonus
                if card.edition and "foil" in card.edition.lower():
                    c_chips += 50
                card_chips_added += c_chips

                c_mult = card.mult_bonus
                if card.edition and ("holo" in card.edition.lower() or "holographic" in card.edition.lower()):
                    c_mult += 10
                card_mult_added += c_mult

                if card.x_mult > 1.0:
                    x_mult_factor *= card.x_mult
                if card.edition and ("poly" in card.edition.lower() or "polychrome" in card.edition.lower()):
                    x_mult_factor *= 1.5

                is_face = card.is_face_card if hasattr(card, "is_face_card") else c_rank in ("Jack", "Queen", "King")
                is_even = c_rank in ("2", "4", "6", "8", "10")
                is_odd = c_rank in ("3", "5", "7", "9", "Ace")
                is_fib = c_rank in ("Ace", "2", "3", "5", "8")

                for j in active_jokers:
                    k = (j.key or "").lower()
                    lbl = (j.label or "").lower()

                    if is_face:
                        if "scary face" in lbl or k == "j_scary_face":
                            card_chips_added += 30
                        if "smiley face" in lbl or k == "j_smiley_face":
                            card_mult_added += 5
                        if ("photograph" in lbl or k == "j_photograph") and ((card.id or f"face_{card_idx}") == first_face_card_id):
                            x_mult_factor *= 2.0
                            breakdown.append("Photograph: X2")
                        if ("triboulet" in lbl or k == "j_triboulet") and c_rank in ("King", "Queen"):
                            x_mult_factor *= 2.0
                            breakdown.append("Triboulet: X2")

                    if is_even and ("even steven" in lbl or k == "j_even_steven"):
                        card_mult_added += 4
                    if is_odd and ("odd todd" in lbl or k == "j_odd_todd"):
                        card_chips_added += 30
                    if is_fib and ("fibonacci" in lbl or k == "j_fibonacci"):
                        card_mult_added += 8
                    if c_rank == "Ace" and ("scholar" in lbl or k == "j_scholar"):
                        card_chips_added += 20
                        card_mult_added += 4
                    if c_rank in ("10", "4") and ("walkie talkie" in lbl or k == "j_walkie_talkie"):
                        card_chips_added += 10
                        card_mult_added += 4
                    if "hiker" in lbl or k == "j_hiker":
                        card_chips_added += 5

                    is_wild = c_suit == "Wild" or card.enhancement == "Wild"
                    if c_suit == "Diamonds" or is_wild:
                        if "greedy" in lbl or k == "j_greedy_joker":
                            card_mult_added += 3
                    if c_suit == "Hearts" or is_wild:
                        if "lusty" in lbl or k == "j_lusty_joker":
                            card_mult_added += 3
                        if "bloodstone" in lbl or k == "j_bloodstone":
                            x_mult_factor *= 1.5
                    if c_suit == "Spades" or is_wild:
                        if "wrathful" in lbl or k == "j_wrathful_joker":
                            card_mult_added += 3
                        if "arrowhead" in lbl or k == "j_arrowhead":
                            card_chips_added += 50
                        if "onyx agate" in lbl or k == "j_onyx_agate":
                            card_mult_added += 7
                    if c_suit == "Clubs" or is_wild:
                        if "gluttonous" in lbl or k == "j_gluttonous_joker":
                            card_mult_added += 3

                    if "ancient" in lbl or k == "j_ancient":
                        ancient_suit = getattr(j, "target_suit", None)
                        if not ancient_suit and j.effect_desc:
                            m_suit = re.search(r"\b(Hearts|Diamonds|Clubs|Spades|черв[ыа]|бубн[ыа]|треф[ыа]|пик[иа])\b", j.effect_desc, re.I)
                            if m_suit:
                                raw_s = m_suit.group(1).lower()
                                if "черв" in raw_s or "heart" in raw_s:
                                    ancient_suit = "Hearts"
                                elif "бубн" in raw_s or "diamond" in raw_s:
                                    ancient_suit = "Diamonds"
                                elif "треф" in raw_s or "club" in raw_s:
                                    ancient_suit = "Clubs"
                                elif "пик" in raw_s or "spade" in raw_s:
                                    ancient_suit = "Spades"
                        if ancient_suit and (c_suit == ancient_suit or is_wild):
                            x_mult_factor *= 1.5
                            breakdown.append(f"Ancient({ancient_suit}): X1.5")

        total_chips += card_chips_added
        total_mult += card_mult_added
        if card_chips_added > 0:
            breakdown.append(f"Cards: +{card_chips_added}c")
        if card_mult_added > 0:
            breakdown.append(f"Triggers: +{card_mult_added}m")

        # 2. Hand-Type Specific Jokers
        contains_pair = hand_type in ("Pair", "Two Pair", "Three of a Kind", "Full House", "Four of a Kind", "Five of a Kind", "Flush House", "Flush Five")
        contains_two_pair = hand_type in ("Two Pair", "Full House", "Flush House")
        contains_three = hand_type in ("Three of a Kind", "Full House", "Flush House", "Four of a Kind", "Five of a Kind", "Flush Five")
        contains_straight = hand_type in ("Straight", "Straight Flush")
        contains_flush = hand_type in ("Flush", "Straight Flush", "Flush House", "Flush Five")
        contains_four = hand_type in ("Four of a Kind", "Five of a Kind", "Flush Five")

        for j in active_jokers:
            k = (j.key or "").lower()
            lbl = (j.label or "").lower()

            if contains_pair:
                if "jolly" in lbl or k == "j_jolly":
                    total_mult += 8
                    breakdown.append("Jolly: +8m")
                if "sly" in lbl or k == "j_sly":
                    total_chips += 50
                    breakdown.append("Sly: +50c")
                if "the duo" in lbl or k in ("j_duo", "j_the_duo"):
                    x_mult_factor *= 2.0
                    breakdown.append("The Duo: X2")

            if contains_two_pair:
                if "mad" in lbl or k == "j_mad":
                    total_mult += 10
                    breakdown.append("Mad: +10m")
                if "clever" in lbl or k == "j_clever":
                    total_chips += 80
                    breakdown.append("Clever: +80c")

            if contains_three:
                if "zany" in lbl or k == "j_zany":
                    total_mult += 12
                    breakdown.append("Zany: +12m")
                if "wily" in lbl or k == "j_wily":
                    total_chips += 100
                    breakdown.append("Wily: +100c")
                if "the trio" in lbl or k in ("j_trio", "j_the_trio"):
                    x_mult_factor *= 3.0
                    breakdown.append("The Trio: X3")

            if contains_straight:
                if "crazy" in lbl or k == "j_crazy":
                    total_mult += 12
                    breakdown.append("Crazy: +12m")
                if "devious" in lbl or k == "j_devious":
                    total_chips += 100
                    breakdown.append("Devious: +100c")
                if "the order" in lbl or k in ("j_order", "j_the_order"):
                    x_mult_factor *= 3.0
                    breakdown.append("The Order: X3")

            if contains_flush:
                if "droll" in lbl or k == "j_droll":
                    total_mult += 10
                    breakdown.append("Droll: +10m")
                if "crafty" in lbl or k == "j_crafty":
                    total_chips += 80
                    breakdown.append("Crafty: +80c")
                if "the tribe" in lbl or k in ("j_tribe", "j_the_tribe"):
                    x_mult_factor *= 2.0
                    breakdown.append("The Tribe: X2")

            if contains_four:
                if "the family" in lbl or k in ("j_family", "j_the_family"):
                    x_mult_factor *= 4.0
                    breakdown.append("The Family: X4")

            if len(cards) <= 3 and ("half" in lbl or k == "j_half"):
                total_mult += 20
                breakdown.append("Half: +20m")

        # 3. Flat Chips and Flat Mult Jokers
        for j in active_jokers:
            k = (j.key or "").lower()
            lbl = (j.label or "").lower()
            desc = (j.effect_desc or "")

            if k == "j_joker" or lbl == "joker":
                total_mult += 4
                breakdown.append("Joker: +4m")
            elif "abstract" in lbl or k == "j_abstract":
                count = len(state.jokers) if state else 1
                bonus = 3 * count
                total_mult += bonus
                breakdown.append(f"Abstract: +{bonus}m")
            elif "supernova" in lbl or k == "j_supernova":
                if hand_played > 0:
                    total_mult += hand_played
                    breakdown.append(f"Supernova: +{hand_played}m")
            elif "bull" in lbl or k == "j_bull":
                dollars = max(0, state.dollars) if state else 0
                bonus = 2 * dollars
                if bonus > 0:
                    total_chips += bonus
                    breakdown.append(f"Bull: +{bonus}c")
            elif "banner" in lbl or k == "j_banner":
                discards = max(0, state.discards_left) if state else 0
                bonus = 30 * discards
                if bonus > 0:
                    total_chips += bonus
                    breakdown.append(f"Banner: +{bonus}c")
            elif "blue joker" in lbl or k == "j_blue_joker":
                deck_rem = state.deck_remaining if state else 52
                bonus = 2 * deck_rem
                total_chips += bonus
                breakdown.append(f"BlueJoker: +{bonus}c")
            elif "ice cream" in lbl or k == "j_ice_cream":
                m = re.search(r'\+?(\d+)\s*[Cc]hips', desc)
                bonus = int(m.group(1)) if m else 80
                total_chips += bonus
                breakdown.append(f"IceCream: +{bonus}c")
            elif "popcorn" in lbl or k == "j_popcorn":
                m = re.search(r'\+?(\d+)\s*[Mm]ult', desc)
                bonus = int(m.group(1)) if m else 20
                total_mult += bonus
                breakdown.append(f"Popcorn: +{bonus}m")
            elif "gros michel" in lbl or k == "j_gros_michel":
                total_mult += 15
                breakdown.append("GrosMichel: +15m")
            elif "mystic summit" in lbl or k == "j_mystic_summit":
                if state and state.discards_left == 0:
                    total_mult += 15
                    breakdown.append("MysticSummit: +15m")
            elif "swashbuckler" in lbl or k == "j_swashbuckler":
                if state and state.jokers:
                    bonus = sum(other.sell_cost for other in state.jokers if other.id != j.id)
                    if bonus > 0:
                        total_mult += bonus
                        breakdown.append(f"Swashbuckler: +{bonus}m")
            elif "misprint" in lbl or k == "j_misprint":
                total_mult += 12
                breakdown.append("Misprint: ~+12m")
            elif "bootstraps" in lbl or k == "j_bootstraps":
                dollars = max(0, state.dollars) if state else 0
                bonus = (dollars // 5) * 2
                if bonus > 0:
                    total_mult += bonus
                    breakdown.append(f"Bootstraps: +{bonus}m")

        # 4. Independent / Post-trigger xMult Jokers
        for j in active_jokers:
            k = (j.key or "").lower()
            lbl = (j.label or "").lower()
            desc = (j.effect_desc or "")

            if "stencil" in lbl or k == "j_stencil":
                max_slots = state.max_jokers if state else 5
                cur_count = len(state.jokers) if state else 1
                empty_slots = max(0, max_slots - cur_count)
                stencil_mult = 1.0 + empty_slots
                if stencil_mult > 1.0:
                    x_mult_factor *= stencil_mult
                    breakdown.append(f"Stencil: X{stencil_mult:g}")
            elif "blackboard" in lbl or k == "j_blackboard":
                if state and state.hand:
                    played_ids = {c.id for c in cards}
                    held = [c for c in state.hand if c.id not in played_ids]
                    is_bb_active = all(c.suit in ("Spades", "Clubs", "Wild") or c.enhancement == "Wild" for c in held)
                    if is_bb_active:
                        x_mult_factor *= 3.0
                        breakdown.append("Blackboard: X3")
            elif "cavendish" in lbl or k == "j_cavendish":
                x_mult_factor *= 3.0
                breakdown.append("Cavendish: X3")
            elif "acrobat" in lbl or k == "j_acrobat":
                if state and state.hands_left == 1:
                    x_mult_factor *= 3.0
                    breakdown.append("Acrobat: X3")
            elif "card sharp" in lbl or k == "j_card_sharp":
                if hand_played > 0:
                    x_mult_factor *= 3.0
                    breakdown.append("CardSharp: X3")
            elif j.effect_type == "x_mult":
                val = getattr(j, "x_mult", 1.0)
                if val > 1.0:
                    x_mult_factor *= val
                    breakdown.append(f"{j.label}: X{val:g}")
                else:
                    m = re.search(r'[Xx]([\d\.]+)\s*[Mm]ult', desc)
                    if m:
                        val_m = float(m.group(1))
                        if val_m > 1.0:
                            x_mult_factor *= val_m
                            breakdown.append(f"{j.label}: X{val_m:g}")

        # 4b. Joker Editions (Foil: +50c, Holographic: +10m, Polychrome: X1.5)
        for j in active_jokers:
            ed = (getattr(j, "edition", None) or "").lower()
            if "foil" in ed:
                total_chips += 50
                breakdown.append(f"{j.label}(Foil): +50c")
            elif "holo" in ed:
                total_mult += 10
                breakdown.append(f"{j.label}(Holo): +10m")
            elif "poly" in ed:
                x_mult_factor *= 1.5
                breakdown.append(f"{j.label}(Poly): X1.5")

        # 5. Held-in-Hand Card Effects
        if state and state.hand:
            played_ids = {c.id for c in cards}
            held = [c for c in state.hand if c.id not in played_ids]
            has_baron = any("baron" in (j.label or "").lower() or j.key == "j_baron" for j in active_jokers)
            has_shoot = any("shoot the moon" in (j.label or "").lower() or j.key == "j_shoot_the_moon" for j in active_jokers)

            for hc in held:
                if not is_card_debuffed(hc):
                    if hc.enhancement == "Steel":
                        x_mult_factor *= 1.5
                        breakdown.append("HeldSteel: X1.5")
                    if has_baron and hc.rank == "King":
                        x_mult_factor *= 1.5
                        breakdown.append("BaronKing: X1.5")
                    if has_shoot and hc.rank == "Queen":
                        total_mult += 13
                        breakdown.append("ShootMoonQueen: +13m")

        final_chips = int(total_chips)
        final_mult = int(round(total_mult * x_mult_factor))
        if final_mult < 1:
            final_mult = 1

        is_plasma = bool(state and state.deck_name and "plasma" in state.deck_name.lower())
        if is_plasma:
            final_score = ((final_chips + final_mult) // 2) ** 2
            breakdown.append("PlasmaBalance")
        else:
            final_score = final_chips * final_mult

        # Threshold Status Comparison
        threshold_status = ""
        if state and state.target_score > 0:
            remaining_score = max(0, state.target_score - state.current_score)
            if remaining_score == 0:
                threshold_status = "🎯 [CLEARED: Blind already beaten!]"
            elif final_score >= remaining_score:
                threshold_status = "🎯 [ONE-SHOT: Clears blind in 1 hand!]"
            else:
                deficit = remaining_score - final_score
                pct = (final_score / remaining_score) * 100
                hands_needed = math.ceil(remaining_score / max(1, final_score))
                threshold_status = f"⚠️ [Covers ~{pct:.0f}% of needed {remaining_score:,} chips (Deficit: {deficit:,}, ~{hands_needed} hands needed)]"

        return HandEvaluationResult(
            hand_type=hand_type,
            cards=list(cards),
            chips=final_chips,
            mult=final_mult,
            score=final_score,
            breakdown=breakdown,
            threshold_status=threshold_status,
        )

    def evaluate_candidate_hands(
        self,
        hand: Sequence[CardModel],
        state: GameState | None = None,
        max_results: int = 5,
    ) -> list[HandEvaluationResult]:
        """Scans current cards in hand to find the highest-scoring candidate card combinations,
        projecting full expected score with active Jokers, Planet levels, and thresholds."""
        if not hand:
            return []

        candidates: list[HandEvaluationResult] = []
        sizes = [5, 4, 3, 2, 1] if len(hand) >= 5 else list(range(len(hand), 0, -1))

        seen_signatures = set()
        for size in sizes:
            for combo in combinations(hand, size):
                hand_type, _, _ = self.identify_poker_hand(combo)
                combo_signature = (hand_type, tuple(sorted(c.id for c in combo)))
                if combo_signature in seen_signatures:
                    continue
                seen_signatures.add(combo_signature)

                res = self.calculate_expected_hand_score(combo, hand_type=hand_type, state=state)
                candidates.append(res)

        # Sort descending by projected final score, preferring 5-card plays
        candidates.sort(key=lambda r: (r.score, len(r.cards)), reverse=True)

        # Filter out redundant combinations that have identical hand type and score
        unique_candidates: list[HandEvaluationResult] = []
        seen_keys = set()
        for cand in candidates:
            k = (cand.hand_type, cand.score)
            if k not in seen_keys:
                seen_keys.add(k)
                unique_candidates.append(cand)

        return unique_candidates[:max_results]

    @staticmethod
    def get_representative_hand(hand_type: str, state: GameState | None = None) -> list[CardModel]:
        """Constructs a clean 5-card sample hand of the requested poker hand type for simulations."""
        dominant_suit = "Spades"
        if state and state.deck_summary and state.deck_summary.suits:
            dominant_suit = max(state.deck_summary.suits.items(), key=lambda x: x[1])[0]

        other_suits = [s for s in ("Spades", "Hearts", "Clubs", "Diamonds") if s != dominant_suit]
        s1, s2, s3 = other_suits[0], other_suits[1], other_suits[2]

        if hand_type in ("Flush", "Straight Flush", "Flush House", "Flush Five"):
            if hand_type == "Flush":
                return [
                    CardModel(id="sim_c1", rank="King", suit=dominant_suit, nominal=10),
                    CardModel(id="sim_c2", rank="Queen", suit=dominant_suit, nominal=10),
                    CardModel(id="sim_c3", rank="10", suit=dominant_suit, nominal=10),
                    CardModel(id="sim_c4", rank="8", suit=dominant_suit, nominal=8),
                    CardModel(id="sim_c5", rank="5", suit=dominant_suit, nominal=5),
                ]
            elif hand_type == "Straight Flush":
                return [
                    CardModel(id="sim_c1", rank="10", suit=dominant_suit, nominal=10),
                    CardModel(id="sim_c2", rank="9", suit=dominant_suit, nominal=9),
                    CardModel(id="sim_c3", rank="8", suit=dominant_suit, nominal=8),
                    CardModel(id="sim_c4", rank="7", suit=dominant_suit, nominal=7),
                    CardModel(id="sim_c5", rank="6", suit=dominant_suit, nominal=6),
                ]
            elif hand_type == "Flush House":
                return [
                    CardModel(id="sim_c1", rank="King", suit=dominant_suit, nominal=10),
                    CardModel(id="sim_c2", rank="King", suit=dominant_suit, nominal=10),
                    CardModel(id="sim_c3", rank="King", suit=dominant_suit, nominal=10),
                    CardModel(id="sim_c4", rank="10", suit=dominant_suit, nominal=10),
                    CardModel(id="sim_c5", rank="10", suit=dominant_suit, nominal=10),
                ]
            else:  # Flush Five
                return [
                    CardModel(id="sim_c1", rank="King", suit=dominant_suit, nominal=10),
                    CardModel(id="sim_c2", rank="King", suit=dominant_suit, nominal=10),
                    CardModel(id="sim_c3", rank="King", suit=dominant_suit, nominal=10),
                    CardModel(id="sim_c4", rank="King", suit=dominant_suit, nominal=10),
                    CardModel(id="sim_c5", rank="King", suit=dominant_suit, nominal=10),
                ]
        elif hand_type == "Five of a Kind":
            return [
                CardModel(id="sim_c1", rank="King", suit=dominant_suit, nominal=10),
                CardModel(id="sim_c2", rank="King", suit=s1, nominal=10),
                CardModel(id="sim_c3", rank="King", suit=s2, nominal=10),
                CardModel(id="sim_c4", rank="King", suit=s3, nominal=10),
                CardModel(id="sim_c5", rank="King", suit=dominant_suit, nominal=10),
            ]
        elif hand_type == "Four of a Kind":
            return [
                CardModel(id="sim_c1", rank="King", suit=dominant_suit, nominal=10),
                CardModel(id="sim_c2", rank="King", suit=s1, nominal=10),
                CardModel(id="sim_c3", rank="King", suit=s2, nominal=10),
                CardModel(id="sim_c4", rank="King", suit=s3, nominal=10),
                CardModel(id="sim_c5", rank="5", suit=s1, nominal=5),
            ]
        elif hand_type == "Full House":
            return [
                CardModel(id="sim_c1", rank="King", suit=dominant_suit, nominal=10),
                CardModel(id="sim_c2", rank="King", suit=s1, nominal=10),
                CardModel(id="sim_c3", rank="King", suit=s2, nominal=10),
                CardModel(id="sim_c4", rank="10", suit=s1, nominal=10),
                CardModel(id="sim_c5", rank="10", suit=s2, nominal=10),
            ]
        elif hand_type == "Straight":
            return [
                CardModel(id="sim_c1", rank="10", suit=dominant_suit, nominal=10),
                CardModel(id="sim_c2", rank="9", suit=s1, nominal=9),
                CardModel(id="sim_c3", rank="8", suit=s2, nominal=8),
                CardModel(id="sim_c4", rank="7", suit=s3, nominal=7),
                CardModel(id="sim_c5", rank="6", suit=dominant_suit, nominal=6),
            ]
        elif hand_type == "Three of a Kind":
            return [
                CardModel(id="sim_c1", rank="King", suit=dominant_suit, nominal=10),
                CardModel(id="sim_c2", rank="King", suit=s1, nominal=10),
                CardModel(id="sim_c3", rank="King", suit=s2, nominal=10),
                CardModel(id="sim_c4", rank="8", suit=s1, nominal=8),
                CardModel(id="sim_c5", rank="5", suit=s2, nominal=5),
            ]
        elif hand_type == "Two Pair":
            return [
                CardModel(id="sim_c1", rank="King", suit=dominant_suit, nominal=10),
                CardModel(id="sim_c2", rank="King", suit=s1, nominal=10),
                CardModel(id="sim_c3", rank="10", suit=s1, nominal=10),
                CardModel(id="sim_c4", rank="10", suit=s2, nominal=10),
                CardModel(id="sim_c5", rank="5", suit=s3, nominal=5),
            ]
        elif hand_type == "Pair":
            return [
                CardModel(id="sim_c1", rank="King", suit=dominant_suit, nominal=10),
                CardModel(id="sim_c2", rank="King", suit=s1, nominal=10),
                CardModel(id="sim_c3", rank="10", suit=s1, nominal=10),
                CardModel(id="sim_c4", rank="8", suit=s2, nominal=8),
                CardModel(id="sim_c5", rank="5", suit=s3, nominal=5),
            ]
        else:  # High Card
            return [
                CardModel(id="sim_c1", rank="Ace", suit=dominant_suit, nominal=11),
                CardModel(id="sim_c2", rank="King", suit=s1, nominal=10),
                CardModel(id="sim_c3", rank="8", suit=s2, nominal=8),
                CardModel(id="sim_c4", rank="5", suit=s3, nominal=5),
                CardModel(id="sim_c5", rank="2", suit=dominant_suit, nominal=2),
            ]
