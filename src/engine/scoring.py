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


def lucky_trigger_chance(active_jokers: Sequence[JokerModel] | None = None) -> float:
    """Chance that a Lucky card's +20 Mult fires on one trigger: 1 in 5, doubled per 'Oops! All 6s'."""
    oops = sum(
        1 for j in (active_jokers or [])
        if (j.key or "").lower() in ("j_oops", "j_oops_all_6s") or "oops" in (j.label or "").lower()
    )
    return min(1.0, 0.2 * (2 ** oops))


def effective_card_mult_bonus(card: CardModel, active_jokers: Sequence[JokerModel] | None = None) -> float:
    """Per-trigger +Mult from a card. Lucky cards report ability.mult=20 from the bridge, but it is a
    probabilistic 1-in-5 effect, so use its expected value instead of treating it as guaranteed."""
    mb = float(card.mult_bonus or 0)
    if mb and (card.enhancement or "").lower().startswith("lucky"):
        return mb * lucky_trigger_chance(active_jokers)
    return mb

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

    @staticmethod
    def _resolve_effective_jokers(active_jokers: Sequence[Any]) -> list[Any]:
        """Resolves Blueprint (copies Joker to the right) and Brainstorm (copies leftmost Joker)."""
        if not active_jokers:
            return []
        resolved = [j.model_copy(deep=True) if hasattr(j, "model_copy") else j for j in active_jokers]
        n = len(resolved)
        for _ in range(min(n, 5)):
            changed = False
            for idx, j in enumerate(resolved):
                k = (getattr(j, "key", "") or "").lower()
                lbl = (getattr(j, "label", "") or "").lower()
                target = None
                if k == "j_blueprint" or "blueprint" in lbl or "чертеж" in lbl or "чертёж" in lbl:
                    if idx + 1 < n:
                        target = resolved[idx + 1]
                elif k == "j_brainstorm" or "brainstorm" in lbl or "мозговой штурм" in lbl:
                    if idx > 0:
                        target = resolved[0]
                if target is not None:
                    tk = (getattr(target, "key", "") or "").lower()
                    tlbl = (getattr(target, "label", "") or "").lower()
                    if tk not in ("j_blueprint", "j_brainstorm") and "blueprint" not in tlbl and "brainstorm" not in tlbl:
                        if hasattr(j, "model_copy") and hasattr(target, "model_copy"):
                            copy_j = target.model_copy(deep=True)
                            copy_j.id = j.id
                            copy_j.edition = j.edition
                            copy_j.order = j.order
                            resolved[idx] = copy_j
                            changed = True
            if not changed:
                break
        return resolved

    def optimize_card_order_for_scoring(
        self,
        combo: Sequence[CardModel],
        hand_type: str | None = None,
        state: GameState | None = None,
    ) -> list[CardModel]:
        """Orders played cards left-to-right to maximize Phase-1 scoring (e.g. placing the Photograph
        face card, Fibonacci/Scholar/Ancient/Idol target, or Glass card at index 0 when Hanging Chad is
        active, or placing +Mult cards before XMult cards when Hanging Chad is not active)."""
        if len(combo) <= 1:
            return list(combo)
        if hand_type is None:
            hand_type, _, _ = self.identify_poker_hand(combo)

        active_jokers = self._resolve_effective_jokers([j for j in (state.jokers if state else []) if not j.debuffed])
        has_splash = any((j.key or "").lower() == "j_splash" or "splash" in (j.label or "").lower() for j in active_jokers)
        has_chad = any(
            (j.key or "").lower() == "j_hanging_chad"
            or "hanging chad" in (j.label or "").lower()
            or (j.label or "").lower() == "chad"
            for j in active_jokers
        )
        has_photo = any((j.key or "").lower() == "j_photograph" or "photograph" in (j.label or "").lower() for j in active_jokers)
        has_pareidolia = any((j.key or "").lower() == "j_pareidolia" or "pareidolia" in (j.label or "").lower() for j in active_jokers)

        scoring_cards = list(combo) if has_splash else self.get_scoring_cards(combo, hand_type)
        scoring_ids = {c.id for c in scoring_cards}
        non_scoring = [c for c in combo if c.id not in scoring_ids]

        def card_metrics(c: CardModel) -> tuple[float, float, int]:
            if c.debuffed:
                return (1.0, 0.0, 0)
            c_rank = c.effective_rank if hasattr(c, "effective_rank") else c.rank
            c_suit = c.effective_suit if hasattr(c, "effective_suit") else c.suit
            c_nom = c.effective_nominal if hasattr(c, "effective_nominal") else c.nominal
            is_face = has_pareidolia or (c.is_face_card if hasattr(c, "is_face_card") else c_rank in ("Jack", "Queen", "King"))
            is_even = c_rank in ("2", "4", "6", "8", "10")
            is_odd = c_rank in ("3", "5", "7", "9", "Ace")
            is_fib = c_rank in ("Ace", "2", "3", "5", "8")
            is_wild = c_suit == "Wild" or c.enhancement == "Wild"

            xm = float(c.x_mult) if c.x_mult > 1.0 else 1.0
            if c.edition and ("poly" in c.edition.lower() or "polychrome" in c.edition.lower()):
                xm *= 1.5
            m_add = effective_card_mult_bonus(c, active_jokers)
            if c.edition and ("holo" in c.edition.lower() or "holographic" in c.edition.lower()):
                m_add += 10.0
            c_add = int(c_nom + c.chip_bonus + (50 if (c.edition and "foil" in c.edition.lower()) else 0))

            for j in active_jokers:
                k = (j.key or "").lower()
                lbl = (j.label or "").lower()

                if is_face:
                    if "scary face" in lbl or k == "j_scary_face":
                        c_add += 30
                    if "smiley face" in lbl or k == "j_smiley_face":
                        m_add += 5.0
                    if "photograph" in lbl or k == "j_photograph":
                        xm *= 2.0
                        if c.seal == "Red":
                            xm *= 1.5  # Prioritize Red Seal face card as first_face_card_id for Photograph
                    if ("triboulet" in lbl or k == "j_triboulet") and c_rank in ("King", "Queen"):
                        xm *= 2.0

                if is_even and ("even steven" in lbl or k == "j_even_steven"):
                    m_add += 4.0
                if is_odd and ("odd todd" in lbl or k == "j_odd_todd"):
                    c_add += 31
                if is_fib and ("fibonacci" in lbl or k == "j_fibonacci"):
                    m_add += 8.0
                if c_rank == "Ace" and ("scholar" in lbl or k == "j_scholar"):
                    c_add += 20
                    m_add += 4.0
                if c_rank in ("10", "4") and ("walkie talkie" in lbl or k == "j_walkie_talkie"):
                    c_add += 10
                    m_add += 4.0
                if "hiker" in lbl or k == "j_hiker":
                    c_add += 5

                if c_suit == "Diamonds" or is_wild:
                    if "greedy" in lbl or k == "j_greedy_joker":
                        m_add += 3.0
                if c_suit == "Hearts" or is_wild:
                    if "lusty" in lbl or k == "j_lusty_joker":
                        m_add += 3.0
                    if "bloodstone" in lbl or k == "j_bloodstone":
                        xm *= 1.5
                if c_suit == "Spades" or is_wild:
                    if "wrathful" in lbl or k == "j_wrathful_joker":
                        m_add += 3.0
                    if "arrowhead" in lbl or k == "j_arrowhead":
                        c_add += 50
                if c_suit == "Clubs" or is_wild:
                    if "gluttonous" in lbl or k == "j_gluttonous_joker":
                        m_add += 3.0
                    if "onyx agate" in lbl or k == "j_onyx_agate":
                        m_add += 7.0

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
                        xm *= 1.5

                if "idol" in lbl or k == "j_idol":
                    idol_suit = getattr(j, "target_suit", None)
                    idol_rank = getattr(j, "target_rank", None)
                    if idol_suit and idol_rank and (c_suit == idol_suit or is_wild) and c_rank == idol_rank:
                        xm *= 2.0

            return (xm, m_add, c_add)

        if has_chad and scoring_cards:
            # Put the card that benefits most from +2 retriggers at index 0
            best_first = max(scoring_cards, key=lambda c: (card_metrics(c)[0], card_metrics(c)[1], card_metrics(c)[2]))
            rest = [c for c in scoring_cards if c.id != best_first.id]
            # Order rest so +Mult cards come before XMult cards (and strongest face card first if Photograph is active)
            rest.sort(
                key=lambda c: (
                    1 if card_metrics(c)[0] > 1.0 else 0,
                    -card_metrics(c)[1],
                    -card_metrics(c)[0] if has_photo else card_metrics(c)[0],
                    -card_metrics(c)[2],
                )
            )
            return [best_first] + rest + non_scoring
        else:
            # Order so non-XMult (+Mult) cards trigger before XMult cards
            ordered_scoring = sorted(
                scoring_cards,
                key=lambda c: (
                    1 if card_metrics(c)[0] > 1.0 else 0,
                    -card_metrics(c)[1],
                    -card_metrics(c)[0],
                    -card_metrics(c)[2],
                ),
            )
            return ordered_scoring + non_scoring

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

        # Boss Blind 0-score rule checks (The Mouth, The Eye, The Psychic)
        is_boss_blind = bool(
            state
            and state.blind
            and (
                state.blind.is_boss
                or (
                    state.blind.name
                    and state.blind.name.lower()
                    not in ("small blind", "big blind", "малый блайнд", "большой блайнд")
                )
            )
        )
        boss_name = (state.blind.name or "").lower() if is_boss_blind else ""
        boss_debuff_lc = (state.blind.debuff_rule or "").lower() if is_boss_blind else ""
        is_disabled = "disabled" in boss_debuff_lc

        if is_boss_blind and not is_disabled:
            played_types = [t for t in getattr(state, "played_hand_types_this_round", []) if t]
            played_types_lc = [t.lower() for t in played_types]
            if ("mouth" in boss_name or "рот" in boss_name) and played_types_lc and hand_type.lower() not in played_types_lc:
                locked = played_types[0]
                return HandEvaluationResult(
                    hand_type=hand_type,
                    cards=list(cards),
                    chips=0,
                    mult=0,
                    score=0,
                    breakdown=[f"⛔ BLOCKED BY THE MOUTH (Locked to '{locked}')"],
                    threshold_status=f"⛔ [0 SCORE: The Mouth only allows '{locked}' this round!]",
                )
            if ("eye" in boss_name or "око" in boss_name) and hand_type.lower() in played_types_lc:
                return HandEvaluationResult(
                    hand_type=hand_type,
                    cards=list(cards),
                    chips=0,
                    mult=0,
                    score=0,
                    breakdown=[f"⛔ BLOCKED BY THE EYE ('{hand_type}' already played this round)"],
                    threshold_status=f"⛔ [0 SCORE: The Eye forbids repeating '{hand_type}'!]",
                )
            if ("psychic" in boss_name or "экстрасенс" in boss_name) and len(cards) < 5:
                return HandEvaluationResult(
                    hand_type=hand_type,
                    cards=list(cards),
                    chips=0,
                    mult=0,
                    score=0,
                    breakdown=["⛔ BLOCKED BY THE PSYCHIC (Must play 5 cards!)"],
                    threshold_status="⛔ [0 SCORE: The Psychic requires playing 5 cards!]",
                )

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
        if not is_disabled and ("flint" in boss_name or "кремень" in boss_name):
            base_chips = max(1, base_chips // 2)
            base_mult = max(1, base_mult // 2)
            breakdown.append("TheFlint: -50%Base")

        # Boss card debuff filters
        is_plant = not is_disabled and ("plant" in boss_name or "растение" in boss_name)
        is_club_boss = not is_disabled and ("club" in boss_name or "треф" in boss_name)
        is_goad = not is_disabled and ("goad" in boss_name or "пик" in boss_name)
        is_window = not is_disabled and ("window" in boss_name or "буб" in boss_name)
        is_head = not is_disabled and ("head" in boss_name or "черв" in boss_name)

        raw_active_jokers = [j for j in (state.jokers if state else []) if not j.debuffed]
        active_jokers = self._resolve_effective_jokers(raw_active_jokers)
        has_pareidolia = any((j.key or "").lower() == "j_pareidolia" or "pareidolia" in (j.label or "").lower() for j in active_jokers)

        def is_card_debuffed(c: CardModel) -> bool:
            if c.debuffed:
                return True
            c_rank = c.effective_rank if hasattr(c, "effective_rank") else c.rank
            c_suit = c.effective_suit if hasattr(c, "effective_suit") else c.suit
            is_f = has_pareidolia or (c.is_face_card if hasattr(c, "is_face_card") else c_rank in ("Jack", "Queen", "King"))
            if is_plant and is_f:
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

        has_splash = any((j.key or "").lower() == "j_splash" or "splash" in (j.label or "").lower() for j in active_jokers)
        if has_splash:
            scoring_cards = list(cards)
        else:
            scoring_cards = self.get_scoring_cards(cards, hand_type)

        total_chips = float(base_chips)
        total_mult = float(base_mult)

        card_chips_added = 0
        card_mult_added = 0
        # Track first scored face card ID for Photograph (triggers on base scoring and all its retriggers)
        first_face_card_id: str | None = None

        # Count retrigger Jokers (supports Blueprint / Brainstorm duplicating a retrigger Joker)
        hack_count = sum(1 for j in active_jokers if (j.key or "").lower() == "j_hack" or "hack" in (j.label or "").lower())
        sock_count = sum(1 for j in active_jokers if (j.key or "").lower() == "j_sock_and_buskin" or "sock and buskin" in (j.label or "").lower())
        dusk_count = sum(
            1 for j in active_jokers
            if ((j.key or "").lower() == "j_dusk" or "dusk" in (j.label or "").lower()) and (state and state.hands_left == 1)
        )
        chad_count = sum(
            1 for j in active_jokers
            if (j.key or "").lower() == "j_hanging_chad"
            or "hanging chad" in (j.label or "").lower()
            or (j.label or "").lower() == "chad"
        )
        seltzer_count = sum(
            1 for j in active_jokers
            if (j.key or "").lower() in ("j_selzer", "j_seltzer") or "seltzer" in (j.label or "").lower()
        )
        mime_count = sum(
            1 for j in active_jokers
            if (j.key or "").lower() == "j_mime" or "mime" in (j.label or "").lower()
        )

        # 1. Phase 1: Scored Cards Evaluation (Sequential left-to-right, trigger-by-trigger)
        for card_idx, card in enumerate(scoring_cards):
            if is_card_debuffed(card):
                continue

            c_rank = card.effective_rank if hasattr(card, "effective_rank") else card.rank
            c_suit = card.effective_suit if hasattr(card, "effective_suit") else card.suit
            c_nom = card.effective_nominal if hasattr(card, "effective_nominal") else card.nominal
            is_face = has_pareidolia or (card.is_face_card if hasattr(card, "is_face_card") else c_rank in ("Jack", "Queen", "King"))

            if is_face and first_face_card_id is None:
                first_face_card_id = card.id or f"face_{card_idx}"

            triggers = 1
            if card.seal == "Red":
                triggers += 1
            if card_idx == 0 and chad_count > 0:
                triggers += 2 * chad_count
            if hack_count > 0 and c_rank in ("2", "3", "4", "5"):
                triggers += hack_count
            if sock_count > 0 and is_face:
                triggers += sock_count
            if dusk_count > 0:
                triggers += dusk_count
            if seltzer_count > 0:
                triggers += seltzer_count

            for _ in range(triggers):
                c_chips = c_nom + card.chip_bonus
                if card.edition and "foil" in card.edition.lower():
                    c_chips += 50
                total_chips += c_chips
                card_chips_added += c_chips

                if card.mult_bonus:
                    eff_mb = effective_card_mult_bonus(card, active_jokers)
                    total_mult += eff_mb
                    card_mult_added += eff_mb
                    if eff_mb != card.mult_bonus and _ == 0:
                        breakdown.append(
                            f"Lucky {c_rank}: EV {lucky_trigger_chance(active_jokers) * 100:.0f}% x +{card.mult_bonus:g}m per trigger "
                            f"(x{triggers} triggers; NOT guaranteed, worst case +0m)"
                        )

                if card.x_mult > 1.0:
                    total_mult *= card.x_mult
                    breakdown.append(f"CardX{card.x_mult:g}")

                if card.edition and ("holo" in card.edition.lower() or "holographic" in card.edition.lower()):
                    total_mult += 10
                    card_mult_added += 10
                if card.edition and ("poly" in card.edition.lower() or "polychrome" in card.edition.lower()):
                    total_mult *= 1.5
                    breakdown.append("PolyCard: X1.5")

                is_even = c_rank in ("2", "4", "6", "8", "10")
                is_odd = c_rank in ("3", "5", "7", "9", "Ace")
                is_fib = c_rank in ("Ace", "2", "3", "5", "8")

                for j in active_jokers:
                    k = (j.key or "").lower()
                    lbl = (j.label or "").lower()

                    if is_face:
                        if "scary face" in lbl or k == "j_scary_face":
                            total_chips += 30
                            card_chips_added += 30
                        if "smiley face" in lbl or k == "j_smiley_face":
                            total_mult += 5
                            card_mult_added += 5
                        if ("photograph" in lbl or k == "j_photograph") and ((card.id or f"face_{card_idx}") == first_face_card_id):
                            total_mult *= 2.0
                            breakdown.append("Photograph: X2")
                        if ("triboulet" in lbl or k == "j_triboulet") and c_rank in ("King", "Queen"):
                            total_mult *= 2.0
                            breakdown.append("Triboulet: X2")

                    if is_even and ("even steven" in lbl or k == "j_even_steven"):
                        total_mult += 4
                        card_mult_added += 4
                    if is_odd and ("odd todd" in lbl or k == "j_odd_todd"):
                        total_chips += 31
                        card_chips_added += 31
                    if is_fib and ("fibonacci" in lbl or k == "j_fibonacci"):
                        total_mult += 8
                        card_mult_added += 8
                    if c_rank == "Ace" and ("scholar" in lbl or k == "j_scholar"):
                        total_chips += 20
                        total_mult += 4
                        card_chips_added += 20
                        card_mult_added += 4
                    if c_rank in ("10", "4") and ("walkie talkie" in lbl or k == "j_walkie_talkie"):
                        total_chips += 10
                        total_mult += 4
                        card_chips_added += 10
                        card_mult_added += 4
                    if "hiker" in lbl or k == "j_hiker":
                        total_chips += 5
                        card_chips_added += 5

                    is_wild = c_suit == "Wild" or card.enhancement == "Wild"
                    if c_suit == "Diamonds" or is_wild:
                        if "greedy" in lbl or k == "j_greedy_joker":
                            total_mult += 3
                            card_mult_added += 3
                    if c_suit == "Hearts" or is_wild:
                        if "lusty" in lbl or k == "j_lusty_joker":
                            total_mult += 3
                            card_mult_added += 3
                        if "bloodstone" in lbl or k == "j_bloodstone":
                            total_mult *= 1.5
                            breakdown.append("Bloodstone: X1.5")
                    if c_suit == "Spades" or is_wild:
                        if "wrathful" in lbl or k == "j_wrathful_joker":
                            total_mult += 3
                            card_mult_added += 3
                        if "arrowhead" in lbl or k == "j_arrowhead":
                            total_chips += 50
                            card_chips_added += 50
                    if c_suit == "Clubs" or is_wild:
                        if "gluttonous" in lbl or k == "j_gluttonous_joker":
                            total_mult += 3
                            card_mult_added += 3
                        if "onyx agate" in lbl or k == "j_onyx_agate":
                            total_mult += 7
                            card_mult_added += 7

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
                            total_mult *= 1.5
                            breakdown.append(f"Ancient({ancient_suit}): X1.5")

                    if "idol" in lbl or k == "j_idol":
                        idol_suit = getattr(j, "target_suit", None)
                        idol_rank = getattr(j, "target_rank", None)
                        if idol_suit and idol_rank and (c_suit == idol_suit or is_wild) and c_rank == idol_rank:
                            total_mult *= 2.0
                            breakdown.append(f"Idol({idol_rank} {idol_suit}): X2")

        if card_chips_added > 0:
            breakdown.append(f"Cards: +{card_chips_added}c")
        if card_mult_added > 0:
            breakdown.append(f"Triggers: +{card_mult_added}m")

        # 2. Phase 2: Held-in-Hand Card Effects (Raised Fist, Shoot the Moon, Baron, Steel, with Red Seal & Mime)
        if state and state.hand:
            played_ids = {c.id for c in cards}
            held = [c for c in state.hand if c.id not in played_ids]
            valid_held = [hc for hc in held if not is_card_debuffed(hc) and hc.enhancement != "Stone"]

            # Raised Fist: double the rank of the lowest-ranked card held in hand
            raised_fist_count = sum(
                1 for j in active_jokers if (j.key or "").lower() == "j_raised_fist" or "raised fist" in (j.label or "").lower()
            )
            if raised_fist_count > 0 and valid_held:
                lowest_hc = min(
                    valid_held,
                    key=lambda hc: RANK_ORDER.get(hc.effective_rank if hasattr(hc, "effective_rank") else hc.rank, 99),
                )
                rf_triggers = 1 + (1 if lowest_hc.seal == "Red" else 0) + mime_count
                rf_nom = lowest_hc.effective_nominal if hasattr(lowest_hc, "effective_nominal") else lowest_hc.nominal
                rf_bonus = 2 * rf_nom * raised_fist_count * rf_triggers
                if rf_bonus > 0:
                    total_mult += rf_bonus
                    breakdown.append(f"RaisedFist: +{rf_bonus}m")

            for hc in held:
                if not is_card_debuffed(hc):
                    hc_triggers = 1 + (1 if hc.seal == "Red" else 0) + mime_count
                    hc_rank = hc.effective_rank if hasattr(hc, "effective_rank") else hc.rank
                    for _ in range(hc_triggers):
                        if hc.enhancement == "Steel":
                            total_mult *= 1.5
                            breakdown.append("HeldSteel: X1.5")
                        for j in active_jokers:
                            jk = (j.key or "").lower()
                            jlbl = (j.label or "").lower()
                            if (jk == "j_shoot_the_moon" or "shoot the moon" in jlbl) and hc_rank == "Queen":
                                total_mult += 13
                                breakdown.append("ShootMoonQueen: +13m")
                            elif (jk == "j_baron" or "baron" in jlbl) and hc_rank == "King":
                                total_mult *= 1.5
                                breakdown.append("BaronKing: X1.5")

        # 3. Phase 3: Joker Bar Evaluation (Strictly left-to-right!)
        contains_pair = hand_type in ("Pair", "Two Pair", "Three of a Kind", "Full House", "Four of a Kind", "Five of a Kind", "Flush House", "Flush Five")
        contains_two_pair = hand_type in ("Two Pair", "Full House", "Flush House")
        contains_three = hand_type in ("Three of a Kind", "Full House", "Flush House", "Four of a Kind", "Five of a Kind", "Flush Five")
        contains_straight = hand_type in ("Straight", "Straight Flush")
        contains_flush = hand_type in ("Flush", "Straight Flush", "Flush House", "Flush Five")
        contains_four = hand_type in ("Four of a Kind", "Five of a Kind", "Flush Five")
        has_scored_face = any(
            (has_pareidolia or (sc.is_face_card if hasattr(sc, "is_face_card") else sc.rank in ("Jack", "Queen", "King")))
            for sc in scoring_cards
            if not is_card_debuffed(sc)
        )
        phase12_xmult_keys = {
            "j_photograph", "j_ancient", "j_triboulet", "j_bloodstone", "j_idol", "j_baron"
        }
        baseball_count = sum(
            1 for j in active_jokers if (j.key or "").lower() == "j_baseball" or "baseball" in (j.label or "").lower()
        )

        for j in active_jokers:
            k = (j.key or "").lower()
            lbl = (j.label or "").lower()
            desc = (j.effect_desc or "")
            ed = (getattr(j, "edition", None) or "").lower()

            # Pre-ability edition bonuses (Foil +50c, Holographic +10m)
            if "foil" in ed:
                total_chips += 50
                breakdown.append(f"{j.label}(Foil): +50c")
            elif "holo" in ed:
                total_mult += 10
                breakdown.append(f"{j.label}(Holo): +10m")

            # Hand-Type Specific Jokers (use exact label / full joker name to avoid collisions like "mad" in "madness")
            if contains_pair:
                if "jolly joker" in lbl or lbl == "jolly" or k == "j_jolly":
                    total_mult += 8
                    breakdown.append("Jolly: +8m")
                elif "sly joker" in lbl or lbl == "sly" or k == "j_sly":
                    total_chips += 50
                    breakdown.append("Sly: +50c")
                elif "the duo" in lbl or lbl == "duo" or k in ("j_duo", "j_the_duo"):
                    total_mult *= 2.0
                    breakdown.append("The Duo: X2")

            if contains_two_pair:
                if "mad joker" in lbl or lbl == "mad" or k == "j_mad":
                    total_mult += 10
                    breakdown.append("Mad: +10m")
                elif "clever joker" in lbl or lbl == "clever" or k == "j_clever":
                    total_chips += 80
                    breakdown.append("Clever: +80c")

            if contains_three:
                if "zany joker" in lbl or lbl == "zany" or k == "j_zany":
                    total_mult += 12
                    breakdown.append("Zany: +12m")
                elif "wily joker" in lbl or lbl == "wily" or k == "j_wily":
                    total_chips += 100
                    breakdown.append("Wily: +100c")
                elif "the trio" in lbl or lbl == "trio" or k in ("j_trio", "j_the_trio"):
                    total_mult *= 3.0
                    breakdown.append("The Trio: X3")

            if contains_straight:
                if "crazy joker" in lbl or lbl == "crazy" or k == "j_crazy":
                    total_mult += 12
                    breakdown.append("Crazy: +12m")
                elif "devious joker" in lbl or lbl == "devious" or k == "j_devious":
                    total_chips += 100
                    breakdown.append("Devious: +100c")
                elif "the order" in lbl or lbl == "order" or k in ("j_order", "j_the_order"):
                    total_mult *= 3.0
                    breakdown.append("The Order: X3")

            if contains_flush:
                if "droll joker" in lbl or lbl == "droll" or k == "j_droll":
                    total_mult += 10
                    breakdown.append("Droll: +10m")
                elif "crafty joker" in lbl or lbl == "crafty" or k == "j_crafty":
                    total_chips += 80
                    breakdown.append("Crafty: +80c")
                elif "the tribe" in lbl or lbl == "tribe" or k in ("j_tribe", "j_the_tribe"):
                    total_mult *= 2.0
                    breakdown.append("The Tribe: X2")

            if contains_four and ("the family" in lbl or lbl == "family" or k in ("j_family", "j_the_family")):
                total_mult *= 4.0
                breakdown.append("The Family: X4")

            if len(cards) <= 3 and ("half joker" in lbl or lbl == "half" or k == "j_half"):
                total_mult += 20
                breakdown.append("Half: +20m")

            # Flat & Scaling Chips / Mult Jokers
            if k == "j_joker" or lbl == "joker":
                total_mult += 4
                breakdown.append("Joker: +4m")
            elif "abstract" in lbl or k == "j_abstract":
                count = len(state.jokers) if state else 1
                bonus = 3 * count
                total_mult += bonus
                breakdown.append(f"Abstract: +{bonus}m")
            elif "supernova" in lbl or k == "j_supernova":
                sn_mult = hand_played + 1
                total_mult += sn_mult
                breakdown.append(f"Supernova: +{sn_mult}m")
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
            elif "stuntman" in lbl or k == "j_stuntman":
                total_chips += 250
                breakdown.append("Stuntman: +250c")
            elif "ice cream" in lbl or k == "j_ice_cream":
                m = re.search(r'\+?(\d+)\s*[Cc]hips', desc)
                bonus = j.chips if j.chips > 0 else (int(m.group(1)) if m else 100)
                total_chips += bonus
                breakdown.append(f"IceCream: +{bonus}c")
            elif k in ("j_runner", "j_castle", "j_square", "j_wee", "j_stone") or any(
                w in lbl for w in ("runner", "castle", "square joker", "wee joker", "stone joker")
            ):
                bonus = j.chips
                if bonus <= 0:
                    m = re.search(r'\+(\d+)\s*[Cc]hips', desc)
                    bonus = int(m.group(1)) if m else 0
                if (k == "j_square" or "square joker" in lbl) and len(cards) == 4:
                    bonus += 4
                elif (k == "j_runner" or "runner" in lbl) and contains_straight:
                    bonus += 15
                if bonus > 0:
                    total_chips += bonus
                    breakdown.append(f"{j.label}: +{bonus}c")
            elif "popcorn" in lbl or k == "j_popcorn":
                m = re.search(r'\+?(\d+)\s*[Mm]ult', desc)
                bonus = j.mult if j.mult > 0 else (int(m.group(1)) if m else 20)
                total_mult += bonus
                breakdown.append(f"Popcorn: +{bonus}m")
            elif "green joker" in lbl or k == "j_green_joker":
                bonus = max(0, j.mult) + 1
                total_mult += bonus
                breakdown.append(f"GreenJoker: +{bonus}m")
            elif "ride the bus" in lbl or k == "j_ride_the_bus":
                if has_scored_face:
                    breakdown.append("RideTheBus: RESET(0m)")
                else:
                    bonus = max(0, j.mult) + 1
                    total_mult += bonus
                    breakdown.append(f"RideTheBus: +{bonus}m")
            elif "spare trousers" in lbl or k == "j_trousers":
                bonus = max(0, j.mult) + (2 if contains_two_pair else 0)
                if bonus > 0:
                    total_mult += bonus
                    breakdown.append(f"SpareTrousers: +{bonus}m")
            elif k in ("j_red_card", "j_fortune_teller", "j_flash", "j_erosion") or any(
                w in lbl for w in ("red card", "fortune teller", "flash card", "erosion")
            ):
                bonus = j.mult
                if bonus <= 0:
                    m = re.search(r'\+(\d+)\s*[Mm]ult', desc)
                    bonus = int(m.group(1)) if m else 0
                if bonus > 0:
                    total_mult += bonus
                    breakdown.append(f"{j.label}: +{bonus}m")
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

            # Independent Phase-3 XMult Jokers
            if "stencil" in lbl or k == "j_stencil":
                max_slots = state.max_jokers if state else 5
                cur_count = len(state.jokers) if state else 1
                empty_slots = max(0, max_slots - cur_count)
                stencil_mult = 1.0 + empty_slots
                if stencil_mult > 1.0:
                    total_mult *= stencil_mult
                    breakdown.append(f"Stencil: X{stencil_mult:g}")
            elif "blackboard" in lbl or k == "j_blackboard":
                if state and state.hand:
                    played_ids = {c.id for c in cards}
                    held = [c for c in state.hand if c.id not in played_ids]
                    is_bb_active = all(c.suit in ("Spades", "Clubs", "Wild") or c.enhancement == "Wild" for c in held)
                    if is_bb_active:
                        total_mult *= 3.0
                        breakdown.append("Blackboard: X3")
            elif "cavendish" in lbl or k == "j_cavendish":
                total_mult *= 3.0
                breakdown.append("Cavendish: X3")
            elif "acrobat" in lbl or k == "j_acrobat":
                if state and state.hands_left == 1:
                    total_mult *= 3.0
                    breakdown.append("Acrobat: X3")
            elif "card sharp" in lbl or k == "j_card_sharp":
                played_this_round = [t.lower() for t in getattr(state, "played_hand_types_this_round", []) if t] if state else []
                if hand_type.lower() in played_this_round:
                    total_mult *= 3.0
                    breakdown.append("CardSharp: X3")
            elif "seeing double" in lbl or k == "j_seeing_double":
                suits_scored = [sc.effective_suit for sc in scoring_cards if not is_card_debuffed(sc)]
                has_club = any(s in ("Clubs", "Wild") for s in suits_scored)
                has_other = any(s in ("Hearts", "Diamonds", "Spades", "Wild") for s in suits_scored)
                if has_club and has_other and len(scoring_cards) >= 2:
                    total_mult *= 2.0
                    breakdown.append("SeeingDouble: X2")
            elif "flower pot" in lbl or k == "j_flower_pot":
                suits_scored = {sc.effective_suit for sc in scoring_cards if not is_card_debuffed(sc)}
                if len(suits_scored.intersection({"Spades", "Hearts", "Clubs", "Diamonds"})) == 4:
                    total_mult *= 3.0
                    breakdown.append("FlowerPot: X3")
            elif j.effect_type == "x_mult" and k not in phase12_xmult_keys and not any(
                w in lbl for w in ("photograph", "ancient", "triboulet", "bloodstone", "idol", "baron", "the duo", "the trio", "the family", "the order", "the tribe")
            ):
                val = getattr(j, "x_mult", 1.0)
                if val > 1.0:
                    total_mult *= val
                    breakdown.append(f"{j.label}: X{val:g}")
                else:
                    m = re.search(r'[Xx]([\d\.]+)\s*[Mm]ult', desc)
                    if m:
                        val_m = float(m.group(1))
                        if val_m > 1.0:
                            total_mult *= val_m
                            breakdown.append(f"{j.label}: X{val_m:g}")

            # Baseball Card trigger on Uncommon Jokers
            if baseball_count > 0 and getattr(j, "rarity", "") == "Uncommon":
                for _ in range(baseball_count):
                    total_mult *= 1.5
                    breakdown.append(f"Baseball({j.label}): X1.5")

            # Post-ability Polychrome edition (X1.5)
            if "poly" in ed:
                total_mult *= 1.5
                breakdown.append(f"{j.label}(Poly): X1.5")

        final_chips = int(round(total_chips))
        final_mult = int(round(total_mult))
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

                ordered_combo = self.optimize_card_order_for_scoring(combo, hand_type=hand_type, state=state)
                res = self.calculate_expected_hand_score(ordered_combo, hand_type=hand_type, state=state)
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
                    CardModel(id="sim_c1", rank="King", suit=dominant_suit, nominal=10),
                    CardModel(id="sim_c2", rank="Queen", suit=dominant_suit, nominal=10),
                    CardModel(id="sim_c3", rank="Jack", suit=dominant_suit, nominal=10),
                    CardModel(id="sim_c4", rank="10", suit=dominant_suit, nominal=10),
                    CardModel(id="sim_c5", rank="9", suit=dominant_suit, nominal=9),
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
                CardModel(id="sim_c1", rank="King", suit=dominant_suit, nominal=10),
                CardModel(id="sim_c2", rank="Queen", suit=s1, nominal=10),
                CardModel(id="sim_c3", rank="Jack", suit=s2, nominal=10),
                CardModel(id="sim_c4", rank="10", suit=s3, nominal=10),
                CardModel(id="sim_c5", rank="9", suit=dominant_suit, nominal=9),
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
            has_face_synergy = False
            has_scholar = False
            if state and state.jokers:
                for j in state.jokers:
                    if j.debuffed:
                        continue
                    jk = (j.key or "").lower()
                    jl = (j.label or "").lower()
                    if jk in ("j_photograph", "j_sock_and_buskin", "j_scary_face", "j_smiley_face", "j_triboulet") or any(
                        w in jl for w in ("photograph", "sock and buskin", "scary face", "smiley face", "triboulet")
                    ):
                        has_face_synergy = True
                    if jk == "j_scholar" or "scholar" in jl:
                        has_scholar = True
            if has_face_synergy and not has_scholar:
                return [
                    CardModel(id="sim_c1", rank="King", suit=dominant_suit, nominal=10),
                    CardModel(id="sim_c2", rank="Queen", suit=s1, nominal=10),
                    CardModel(id="sim_c3", rank="8", suit=s2, nominal=8),
                    CardModel(id="sim_c4", rank="5", suit=s3, nominal=5),
                    CardModel(id="sim_c5", rank="2", suit=dominant_suit, nominal=2),
                ]
            return [
                CardModel(id="sim_c1", rank="Ace", suit=dominant_suit, nominal=11),
                CardModel(id="sim_c2", rank="King", suit=s1, nominal=10),
                CardModel(id="sim_c3", rank="8", suit=s2, nominal=8),
                CardModel(id="sim_c4", rank="5", suit=s3, nominal=5),
                CardModel(id="sim_c5", rank="2", suit=dominant_suit, nominal=2),
            ]
