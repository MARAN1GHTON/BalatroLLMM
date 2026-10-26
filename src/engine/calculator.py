"""Interactive Balatro Calculation and Simulation Engine.

Enables LLM agents to execute local Python scratchpad computations, simulate exact hand scoring,
project shop purchasing trade-offs, and calculate combinatorial draw probabilities before
committing to irreversible game actions.
"""

import ast
import collections
from collections import Counter
import io
import itertools
import math
import random
import re
import sys
from typing import Any, Sequence

from ..bridge.schema import CardModel, GameState, JokerModel, PokerHandModel, ShopItemModel
from ..bridge.client import normalize_id_list
from .scoring import BalatroScoringEngine, POKER_HAND_BASE, PLANET_HAND_UPGRADES
from .vouchers import find_voucher_data, format_voucher_impact

# Map planet names to poker hand types
PLANET_TO_HAND = {
    "pluto": "High Card",
    "mercury": "Pair",
    "uranus": "Two Pair",
    "venus": "Three of a Kind",
    "saturn": "Straight",
    "jupiter": "Flush",
    "earth": "Full House",
    "mars": "Four of a Kind",
    "neptune": "Straight Flush",
    "planet x": "Five of a Kind",
    "ceres": "Flush House",
    "eris": "Flush Five",
}

# NOTE: PLANET_HAND_UPGRADES now lives in scoring.py (imported above) so that
# The Arm's pre-scoring level downgrade uses the exact same upgrade steps.


def resolve_card_ids(raw_ids: Any, hand: Sequence[CardModel] | None) -> list[str]:
    """Resolves a list of card identifiers, automatically mapping human-readable rank/suit
    strings (e.g. 'K♠', '10♥', 'King of Spades', 'A♦') to actual `c_...` IDs in `hand`."""
    clean_ids = normalize_id_list(raw_ids)
    if not clean_ids or not hand:
        return clean_ids

    hand_map = {c.id: c for c in hand}
    if all(cid in hand_map for cid in clean_ids):
        return clean_ids

    rank_aliases = {
        "a": "Ace", "ace": "Ace", "туз": "Ace",
        "k": "King", "king": "King", "король": "King",
        "q": "Queen", "queen": "Queen", "дама": "Queen",
        "j": "Jack", "jack": "Jack", "валет": "Jack",
        "t": "10", "10": "10",
        "9": "9", "8": "8", "7": "7", "6": "6", "5": "5", "4": "4", "3": "3", "2": "2",
    }
    suit_aliases = {
        "♠": "Spades", "s": "Spades", "spade": "Spades", "spades": "Spades", "пик": "Spades",
        "♥": "Hearts", "h": "Hearts", "heart": "Hearts", "hearts": "Hearts", "черв": "Hearts",
        "♦": "Diamonds", "d": "Diamonds", "diamond": "Diamonds", "diamonds": "Diamonds", "буб": "Diamonds",
        "♣": "Clubs", "c": "Clubs", "club": "Clubs", "clubs": "Clubs", "треф": "Clubs",
    }

    used_ids: set[str] = {cid for cid in clean_ids if cid in hand_map}
    resolved: list[str] = []

    for token in clean_ids:
        if token in hand_map:
            resolved.append(token)
            continue

        t_str = str(token).strip()
        # Try extracting c_digits if embedded like "[c_123: K♠]"
        m_cid = re.search(r"\bc_\w+\b", t_str)
        if m_cid and m_cid.group(0) in hand_map:
            cid_match = m_cid.group(0)
            resolved.append(cid_match)
            used_ids.add(cid_match)
            continue

        # Parse rank and optional suit from human-readable token (e.g. "K♠", "10H", "King of Spades")
        target_rank: str | None = None
        target_suit: str | None = None
        t_low = t_str.lower()

        for sym, s_name in [("♠", "Spades"), ("♥", "Hearts"), ("♦", "Diamonds"), ("♣", "Clubs")]:
            if sym in t_str:
                target_suit = s_name
                t_low = t_low.replace(sym, " ").strip()
                break

        if not target_suit:
            for s_key, s_name in suit_aliases.items():
                if len(s_key) > 1 and s_key in t_low:
                    target_suit = s_name
                    t_low = re.sub(r"\b(of\s+)?" + re.escape(s_key) + r"\w*\b", " ", t_low).strip()
                    break

        # Compact 2-3 char format like "ks", "10h", "ad"
        m_compact = re.match(r"^(10|[2-9akqjt])([shdc])$", t_low)
        if m_compact:
            target_rank = rank_aliases.get(m_compact.group(1))
            target_suit = suit_aliases.get(m_compact.group(2))
        else:
            for r_key, r_name in sorted(rank_aliases.items(), key=lambda x: len(x[0]), reverse=True):
                if re.search(r"(?:^|\b)" + re.escape(r_key) + r"(?:\b|$)", t_low):
                    target_rank = r_name
                    break

        matched_card = None
        if target_rank:
            for c in hand:
                if c.id in used_ids or c.face_down:
                    continue
                if c.rank == target_rank and (target_suit is None or c.suit == target_suit):
                    matched_card = c
                    break

        if matched_card is not None:
            resolved.append(matched_card.id)
            used_ids.add(matched_card.id)
        else:
            resolved.append(token)

    return resolved


def execute_python_eval(code: str, state: GameState | None = None) -> str:
    """Executes Python code in a sandboxed namespace pre-populated with math, combinatorics,

    and current game state variables. Captures stdout and expression returns.
    """
    clean_code = (code or "").strip()
    if not clean_code:
        return "Error: Empty Python code string provided."

    # Strip markdown code fences if model enclosed code in ```python ... ```
    if clean_code.startswith("```"):
        lines = clean_code.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        clean_code = "\n".join(lines).strip()

    # Pre-populate helper functions and game state
    def calc_interest(dollars: int) -> int:
        return min(5, max(0, dollars) // 5)

    def hands_to_beat(needed_chips: int, hand_score: int) -> int:
        return math.ceil(needed_chips / max(1, hand_score))

    ns: dict[str, Any] = {
        "math": math,
        "itertools": itertools,
        "collections": collections,
        "random": random,
        "Counter": Counter,
        "combinations": itertools.combinations,
        "permutations": itertools.permutations,
        "comb": math.comb,
        "calc_interest": calc_interest,
        "hands_to_beat": hands_to_beat,
    }

    class _AttrDict(dict):
        """Dictionary that supports both key indexing d['k'] and attribute access d.k."""

        def __getattr__(self, name: str):
            try:
                return self[name]
            except KeyError:
                raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    if state:
        ns["dollars"] = state.dollars
        ns["ante"] = state.ante
        ns["round_num"] = state.round
        ns["game_round"] = state.round
        ns["hands_left"] = state.hands_left
        ns["discards_left"] = state.discards_left
        ns["target_score"] = state.target_score
        ns["current_score"] = state.current_score
        ns["score_needed"] = max(0, state.target_score - state.current_score)
        ns["deck_remaining"] = state.deck_remaining
        ns["deck_name"] = state.deck_name
        ns["is_boss"] = bool(state.blind and state.blind.is_boss)
        ns["boss_name"] = state.blind.name if state.blind else ""

        # Represent hand cards as readable dicts supporting both d['rank'] and d.rank
        ns["hand"] = [
            _AttrDict(
                {
                    "id": c.id,
                    "rank": c.rank,
                    "suit": c.suit,
                    "nominal": c.nominal,
                    "enhancement": c.enhancement,
                    "edition": c.edition,
                    "seal": c.seal,
                    "debuffed": c.debuffed,
                    "played_this_ante": getattr(c, "played_this_ante", False),
                }
            )
            for c in (state.hand or [])
        ]

        # Represent jokers as readable dicts supporting both j['label'] and j.label
        ns["jokers"] = [
            _AttrDict(
                {
                    "id": j.id,
                    "key": j.key,
                    "label": j.label,
                    "effect_type": j.effect_type,
                    "x_mult": j.x_mult,
                    "mult": j.mult,
                    "chips": j.chips,
                    "sell_cost": j.sell_cost,
                    "debuffed": j.debuffed,
                    "target_suit": getattr(j, "target_suit", None),
                    "target_rank": getattr(j, "target_rank", None),
                    "target_hand": getattr(j, "target_hand", None),
                }
            )
            for j in (state.jokers or [])
        ]

        # Represent deck suit breakdown and granular cards
        if state.deck_summary:
            ns["deck_suits"] = _AttrDict(state.deck_summary.suits)
            ns["deck_ranks"] = _AttrDict(state.deck_summary.ranks)
            ns["deck_specials"] = [_AttrDict(sc.model_dump()) for sc in state.deck_summary.special_cards]
            ns["deck_cards"] = [_AttrDict(c.model_dump()) for c in state.deck_summary.cards]
            ns["deck_remaining"] = state.deck_summary.total_remaining or state.deck_remaining
        else:
            ns["deck_suits"] = _AttrDict()
            ns["deck_ranks"] = _AttrDict()
            ns["deck_specials"] = []
            ns["deck_cards"] = []
            ns["deck_remaining"] = state.deck_remaining

        # Represent poker hand levels
        ns["poker_hands"] = _AttrDict(
            {
                ph.name: _AttrDict(
                    {
                        "level": ph.level,
                        "chips": ph.chips,
                        "mult": ph.mult,
                        "played": ph.played,
                    }
                )
                for ph in (state.poker_hands or [])
            }
        )
        ns["played_hands"] = list(getattr(state, "played_hand_types_this_round", []) or [])
    else:
        ns["dollars"] = 0
        ns["hands_left"] = 4
        ns["discards_left"] = 3
        ns["target_score"] = 300
        ns["current_score"] = 0
        ns["score_needed"] = 300
        ns["hand"] = []
        ns["jokers"] = []
        ns["deck_suits"] = {}
        ns["deck_ranks"] = {}
        ns["deck_specials"] = []
        ns["deck_cards"] = []
        ns["deck_remaining"] = 52
        ns["poker_hands"] = {}
        ns["played_hands"] = []

    # Convenient aliases so models don't hit NameError on common variable names
    ns["cards"] = ns["hand"]
    ns["bank"] = ns["dollars"]
    ns["cash"] = ns["dollars"]
    ns["hands"] = ns["hands_left"]
    ns["discards"] = ns["discards_left"]
    ns["score_left"] = ns["score_needed"]
    ns["remaining_score"] = ns["score_needed"]

    initial_keys = set(ns.keys())
    stdout_capture = io.StringIO()
    old_stdout = sys.stdout

    try:
        sys.stdout = stdout_capture

        try:
            parsed = ast.parse(clean_code)
            if parsed.body and isinstance(parsed.body[-1], ast.Expr):
                # Interactive REPL / Jupyter mode: execute leading statements, eval and print final expression
                *leading, last_expr = parsed.body
                if leading:
                    exec(compile(ast.Module(body=leading, type_ignores=[]), "<exec>", "exec"), ns, ns)
                res = eval(compile(ast.Expression(body=last_expr.value), "<eval>", "eval"), ns, ns)
                if res is not None:
                    print(repr(res))
            else:
                exec(compile(parsed, "<exec>", "exec"), ns, ns)
        except SyntaxError:
            # Fallback to standard exec if AST parsing of snippet fails
            exec(compile(clean_code, "<exec>", "exec"), ns, ns)

        output = stdout_capture.getvalue().strip()
        if not output:
            # If code produced no print output, automatically surface newly calculated variables
            new_vars = {k: v for k, v in ns.items() if k not in initial_keys and not k.startswith("_")}
            if new_vars:
                output = "Calculated variables:\n" + "\n".join(f"{k} = {repr(v)}" for k, v in new_vars.items())
            else:
                output = "Code executed successfully (no stdout or new variables produced)."
        if len(output) > 2000:
            output = output[:2000] + "\n... [Output truncated at 2000 characters]"
        return output
    except Exception as e:
        return f"Python Execution Error [{type(e).__name__}]: {e}"
    finally:
        sys.stdout = old_stdout


_HAND_LOCKED_SPECS: dict[str, tuple[str, tuple[str, ...]]] = {
    "j_clever": ("Two Pair", ("clever joker", "clever")),
    "j_mad": ("Two Pair", ("mad joker", "mad")),
    "j_spare_trousers": ("Two Pair", ("spare trousers",)),
    "j_sly": ("Pair", ("sly joker", "sly")),
    "j_jolly": ("Pair", ("jolly joker", "jolly")),
    "j_the_duo": ("Pair", ("the duo", "duo")),
    "j_wily": ("Three of a Kind", ("wily joker", "wily")),
    "j_zany": ("Three of a Kind", ("zany joker", "zany")),
    "j_the_trio": ("Three of a Kind", ("the trio", "trio")),
    "j_the_family": ("Four of a Kind", ("the family", "family")),
    "j_devious": ("Straight", ("devious joker", "devious")),
    "j_crazy": ("Straight", ("crazy joker", "crazy")),
    "j_the_order": ("Straight", ("the order", "order")),
    "j_runner": ("Straight", ("runner",)),
    "j_crafty": ("Flush", ("crafty joker", "crafty")),
    "j_droll": ("Flush", ("droll joker", "droll")),
    "j_the_tribe": ("Flush", ("the tribe", "tribe")),
}


def _match_hand_locked_joker(joker: Any) -> str | None:
    """Returns required hand type if joker is a hand-locked joker (without substring collisions like 'mad' in 'madness')."""
    k = (getattr(joker, "key", "") or "").lower().strip()
    lbl = (getattr(joker, "label", "") or "").lower().strip()
    for key, (req_hand, aliases) in _HAND_LOCKED_SPECS.items():
        if k == key or lbl in aliases or any(a in lbl for a in aliases if " " in a):
            return req_hand
    return None


def _hand_contains_required_type(played_hand: str, req_hand: str) -> bool:
    """Checks if `played_hand` contains `req_hand` under Balatro's hand-containment rules (e.g. Full House contains Pair, Two Pair, Three of a Kind)."""
    ph = (played_hand or "").strip().lower()
    rh = (req_hand or "").strip().lower()
    if ph == rh:
        return True
    if rh == "pair":
        return ph in ("pair", "two pair", "three of a kind", "full house", "four of a kind", "five of a kind", "flush house", "flush five")
    if rh == "two pair":
        return ph in ("two pair", "full house", "flush house")
    if rh == "three of a kind":
        return ph in ("three of a kind", "full house", "four of a kind", "five of a kind", "flush house", "flush five")
    if rh == "four of a kind":
        return ph in ("four of a kind", "five of a kind", "flush five")
    if rh == "straight":
        return ph in ("straight", "straight flush", "royal flush")
    if rh == "flush":
        return ph in ("flush", "straight flush", "royal flush", "flush house", "flush five")
    return False


def execute_simulate_hand(card_ids: Any, state: GameState | None = None) -> str:
    """Simulates playing a candidate card combination from hand, projecting exact

    detected hand type, sequential card/Joker triggers, and final score vs blind.
    """
    if not state or not state.hand:
        return "Error: No active hand cards available to simulate."

    clean_ids = resolve_card_ids(card_ids, state.hand)
    if not clean_ids:
        return "Error: `card_ids` must be a non-empty list of 1 to 5 card IDs."

    if len(clean_ids) > 5:
        return f"Error: Cannot play more than 5 cards (received {len(clean_ids)})."

    hand_map = {c.id: c for c in state.hand}
    chosen_cards: list[CardModel] = []
    missing_ids: list[str] = []

    for cid in clean_ids:
        if cid in hand_map:
            chosen_cards.append(hand_map[cid])
        else:
            missing_ids.append(cid)

    if missing_ids:
        avail = [f"{c.id} ({c.rank}{c.suit[0]})" for c in state.hand]
        return f"Error: Card IDs {missing_ids} not found in current hand. Available hand: {', '.join(avail)}"

    engine = BalatroScoringEngine()
    res = engine.calculate_expected_hand_score(chosen_cards, state=state)
    opt_cards = engine.optimize_card_order_for_scoring(chosen_cards, hand_type=res.hand_type, state=state)
    opt_res = engine.calculate_expected_hand_score(opt_cards, hand_type=res.hand_type, state=state)

    cards_desc = ", ".join(
        f"{c.rank}{c.suit[0]}" + (f"[{c.enhancement}]" if c.enhancement else "") for c in chosen_cards
    )
    scoring_cards = engine.get_scoring_cards(chosen_cards, res.hand_type)
    scoring_desc = ", ".join(f"{c.rank}{c.suit[0]}" for c in scoring_cards)

    needed = max(0, state.target_score - state.current_score)
    one_shot = res.score >= needed
    pct = (res.score / max(1, needed)) * 100

    breakdown_str = " -> ".join(res.breakdown) if res.breakdown else "Standard"

    output = [
        f"📊 [HAND SIMULATION RESULT]",
        f"Cards: {cards_desc}",
        f"Hand Detected: **{res.hand_type}** (Scoring cards: {scoring_desc})",
        f"Trigger Chain: {breakdown_str}",
        f"Projected Score: **{res.chips:,} Chips × {res.mult:,} Mult = {res.score:,} Total Points**",
        f"Blind Progress: Target {state.target_score:,} | Current {state.current_score:,} | Needed: {needed:,}",
    ]

    if opt_res.score > res.score:
        opt_ids_str = ", ".join(f"'{c.id}'" for c in opt_cards)
        opt_desc = ", ".join(f"{c.rank}{c.suit[0]}" for c in opt_cards)
        output.append(
            f"💡 **OPTIMAL CARD ORDER AVAILABLE**: Passing `[{opt_ids_str}]` ({opt_desc}) instead yields "
            f"**{opt_res.chips:,} Chips × {opt_res.mult:,} Mult = {opt_res.score:,} Total Points** "
            f"(+{opt_res.score - res.score:,} pts from left-to-right trigger / Hanging Chad / Photograph ordering)!"
        )

    if needed == 0:
        output.append("Status: 🎯 Blind is ALREADY beaten!")
    elif one_shot:
        output.append(
            f"Verdict: ✅ **ONE-SHOT KILL!** Exceeds needed points by {res.score - needed:,} (+{pct - 100:.1f}% overkill)."
        )
    else:
        hands_req = math.ceil(needed / max(1, res.score))
        output.append(
            f"Verdict: ⚠️ Does NOT defeat blind in 1 hand ({pct:.1f}% of needed). Deficit: {needed - res.score:,}. "
            f"Requires ~{hands_req} hands at this exact scoring pace (Hands remaining: {state.hands_left})."
        )
        has_half_joker = any(
            not j.debuffed and ((j.key or "").lower() == "j_half" or "half joker" in (j.label or "").lower())
            for j in (state.jokers or [])
        )
        max_pad = 3 if has_half_joker else 5
        if (
            state.discards_left == 0
            and state.hands_left > 1
            and len(chosen_cards) < max_pad
            and len(state.hand) > len(chosen_cards)
        ):
            extra_slots = min(max_pad - len(chosen_cards), len(state.hand) - len(chosen_cards))
            rank_hint = " (of LOWER rank than your scoring card so it stays the High Card)" if res.hand_type == "High Card" else ""
            output.append(
                f"💡 **CARD CYCLING / PSEUDO-DISCARD TIP (0 discards left, {state.hands_left} hands left)**: "
                f"Playing only {len(chosen_cards)} card(s) will draw only {len(chosen_cards)} replacement card(s) next turn. "
                f"Do NOT play a {len(chosen_cards)}-card {res.hand_type} 'for cleanliness'—attach up to {extra_slots} dead/off-suit trash card(s){rank_hint} "
                f"to `play_cards` so they get cycled out for free and draw fresh cards for your remaining {state.hands_left - 1} hand(s)!"
            )

    # Inactive Hand-Locked Jokers Warning
    inactive_jokers = []
    for j in state.jokers:
        req_hand = _match_hand_locked_joker(j)
        if req_hand and not _hand_contains_required_type(res.hand_type, req_hand):
            inactive_jokers.append(f"{j.label} (requires {req_hand}, but played {res.hand_type})")

    if inactive_jokers:
        output.append(f"⚠️ **INACTIVE JOKERS IN THIS HAND**: {', '.join(inactive_jokers)}!")

    # The Psychic Boss padding alert
    if state.blind.is_boss and "psychic" in (state.blind.name or "").lower():
        if len(chosen_cards) != 5:
            output.append(
                f"🚨 **BOSS THE PSYCHIC WARNING**: You selected {len(chosen_cards)} cards, but The Psychic REQUIRES EXACTLY 5 CARDS! "
                f"You MUST pad your {res.hand_type} with {5 - len(chosen_cards)} extra junk card(s) from hand or your score will be 0!"
            )

    return "\n".join(output)


def execute_simulate_shop_buy(
    shop_index: int,
    sell_joker_id: str = "",
    state: GameState | None = None,
) -> str:
    """Projects financial, interest, and scoring impact of purchasing a shop item

    (and optionally selling an existing Joker to free up slot or raise funds).
    """
    if not state or state.stage != "SHOP":
        if not (state and state.shop and state.shop.items):
            return "Error: Not currently in the Shop phase."

    shop_items = state.shop.items if (state.shop and state.shop.items) else []
    if not shop_items:
        return "Error: Current shop has no available items."

    item = None
    # 1. Match by explicit item.index
    for it in shop_items:
        if it.index == shop_index:
            item = it
            break
    # 2. Match by 0-indexed position
    if item is None and 0 <= shop_index < len(shop_items):
        item = shop_items[shop_index]
    # 3. Match by 1-indexed position
    if item is None and 1 <= shop_index <= len(shop_items):
        item = shop_items[shop_index - 1]

    if item is None:
        avail_indices = [f"Slot [{it.index}] ({it.label})" for it in shop_items]
        return f"Error: shop_index {shop_index} not found in available shop items: {', '.join(avail_indices)}."
    engine = BalatroScoringEngine()

    # 1. Financial Projection
    current_cash = state.dollars
    item_cost = item.cost

    sell_joker: JokerModel | None = None
    sell_cash = 0
    if sell_joker_id:
        sell_joker = next((j for j in state.jokers if j.id == sell_joker_id), None)
        if not sell_joker:
            avail_j = [f"{j.id} ({j.label})" for j in state.jokers]
            return f"Error: sell_joker_id '{sell_joker_id}' not found in owned Jokers: {', '.join(avail_j)}"
        sell_cash = sell_joker.sell_cost

    net_cash = current_cash - item_cost + sell_cash
    can_afford = net_cash >= 0

    interest_before = min(5, max(0, current_cash) // 5)
    interest_after = min(5, max(0, net_cash) // 5) if can_afford else interest_before
    interest_delta = interest_after - interest_before

    lines = [
        f"🛒 [SHOP PURCHASE SIMULATION]: Slot #{shop_index} - **{item.label}** ({item.item_type})",
        f"Item Cost: ${item_cost} | Current Bank: ${current_cash}",
    ]

    if sell_joker:
        lines.append(f"Selling Joker: {sell_joker.label} (+${sell_cash} cash)")
    sign_str = f"+${interest_delta}" if interest_delta > 0 else (f"-${abs(interest_delta)}" if interest_delta < 0 else "$0")
    lines.append(f"Net Bank After Transaction: ${net_cash} (Interest: ${interest_after}/rnd, Delta: {sign_str})")

    has_bull = any(j.key == "j_bull" or "bull" in (j.label or "").lower() for j in state.jokers if j.id != sell_joker_id)
    has_bootstraps = any(j.key == "j_bootstraps" or "bootstraps" in (j.label or "").lower() for j in state.jokers if j.id != sell_joker_id)
    if can_afford and (has_bull or has_bootstraps):
        cash_spent = max(0, current_cash - net_cash)
        penalties = []
        if has_bull and cash_spent > 0:
            penalties.append(f"-{cash_spent * 2} Chips (Bull)")
        if has_bootstraps:
            mult_loss = ((current_cash // 5) - (net_cash // 5)) * 2
            if mult_loss > 0:
                penalties.append(f"-{mult_loss} Mult (Bootstraps)")
        if penalties:
            lines.append(f"⚠️ **MONEY-SCALING COMBAT IMPACT**: Spending ${cash_spent} incurs {', '.join(penalties)}!")

    if not can_afford:
        lines.append(
            f"🚨 **UNSUFFICIENT FUNDS**: You need ${item_cost} but have only ${current_cash + sell_cash} "
            f"(Deficit: ${-net_cash}). Cannot purchase!"
        )
        return "\n".join(lines)

    # 2. Gameplay & Scoring Projection
    if item.item_type == "Joker":
        cur_cnt = len(state.jokers)
        max_cnt = state.max_jokers
        is_neg = (getattr(item, "edition", None) or "").lower() == "negative"
        will_fit = is_neg or (cur_cnt - (1 if sell_joker else 0)) < max_cnt

        if not will_fit:
            lines.append(
                f"🚨 **JOKER CAPACITY EXCEEDED**: You currently have {cur_cnt}/{max_cnt} Jokers! "
                f"You MUST provide `sell_joker_id` to sell an existing Joker to make space."
            )
            return "\n".join(lines)

        # Build simulated new Joker roster
        sim_jokers = [j for j in state.jokers if j.id != sell_joker_id]
        new_joker_model = JokerModel(
            id="sim_new_joker",
            key=getattr(item, "key", None) or item.id,
            label=item.label,
            effect_desc=item.desc,
            edition=getattr(item, "edition", None),
            sell_cost=max(1, item_cost // 2),
        )
        sim_jokers.append(new_joker_model)

        # Find player's most played hand for benchmark testing
        tested_hand_name = "Flush"
        if state.poker_hands:
            most_played = max(state.poker_hands, key=lambda ph: (ph.played, ph.level), default=None)
            if most_played and (most_played.played > 0 or most_played.level > 1):
                tested_hand_name = most_played.name

        sample_cards = engine.get_representative_hand(tested_hand_name, state=state)

        # Score before
        old_res = engine.calculate_expected_hand_score(sample_cards, hand_type=tested_hand_name, state=state)

        # Score after
        temp_state = state.model_copy(update={"jokers": sim_jokers, "dollars": net_cash})
        new_res = engine.calculate_expected_hand_score(sample_cards, hand_type=tested_hand_name, state=temp_state)

        delta_score = new_res.score - old_res.score
        pct_change = ((new_res.score / max(1, old_res.score)) - 1) * 100

        lines.extend([
            f"\n📈 **SCORING PROJECTION ON BENCHMARK HAND ({tested_hand_name})**:",
            f"- Current Loadout: {old_res.chips:,}c × {old_res.mult:,}m = **{old_res.score:,} pts**",
            f"- After Purchase: {new_res.chips:,}c × {new_res.mult:,}m = **{new_res.score:,} pts**",
            f"- Net Score Impact: **{delta_score:+,} pts ({pct_change:+.1f}%)**",
            f"- New Joker Roster ({len(sim_jokers)}/{max_cnt}): {', '.join(j.label for j in sim_jokers)}",
        ])

        # Also check if upcoming Boss is The Flint or has a score-altering debuff
        boss_name = state.blind.upcoming_boss_name if state.blind else ""
        if not boss_name and state.ante_blinds:
            for b in state.ante_blinds:
                if b.type == "Boss":
                    boss_name = b.name
                    break
        if boss_name and ("flint" in boss_name.lower() or "кремень" in boss_name.lower()):
            flint_blind = state.blind.model_copy(update={"name": boss_name, "is_boss": True})
            old_flint = engine.calculate_expected_hand_score(
                sample_cards, hand_type=tested_hand_name, state=state.model_copy(update={"blind": flint_blind})
            )
            new_flint = engine.calculate_expected_hand_score(
                sample_cards, hand_type=tested_hand_name, state=temp_state.model_copy(update={"blind": flint_blind})
            )
            lines.append(
                f"- ⚠️ **VS UPCOMING BOSS THE FLINT (-50% Base)**: {old_flint.score:,} pts -> **{new_flint.score:,} pts** ({new_flint.score - old_flint.score:+,} pts/hand)"
            )

    elif item.item_type == "Planet":
        lbl_lower = item.label.lower()
        matched_hand = None
        for p_name, h_name in PLANET_TO_HAND.items():
            if p_name in lbl_lower:
                matched_hand = h_name
                break

        if matched_hand:
            upgrade_chips, upgrade_mult = PLANET_HAND_UPGRADES.get(matched_hand, (20, 2))
            # Current level
            cur_ph = next((ph for ph in state.poker_hands if ph.name.lower() == matched_hand.lower()), None)
            cur_lvl = cur_ph.level if cur_ph else 1
            cur_chips = cur_ph.chips if cur_ph else POKER_HAND_BASE.get(matched_hand, (10, 2))[0]
            cur_mult = cur_ph.mult if cur_ph else POKER_HAND_BASE.get(matched_hand, (10, 2))[1]

            new_lvl = cur_lvl + 1
            new_chips = cur_chips + upgrade_chips
            new_mult = cur_mult + upgrade_mult

            sample_cards = engine.get_representative_hand(matched_hand, state=state)
            old_res = engine.calculate_expected_hand_score(sample_cards, hand_type=matched_hand, state=state)

            # Build temp poker hands
            new_poker_hands = []
            for ph in state.poker_hands:
                if ph.name.lower() == matched_hand.lower():
                    new_poker_hands.append(
                        ph.model_copy(update={"level": new_lvl, "chips": new_chips, "mult": new_mult})
                    )
                else:
                    new_poker_hands.append(ph)

            temp_state = state.model_copy(update={"poker_hands": new_poker_hands, "dollars": net_cash})
            new_res = engine.calculate_expected_hand_score(sample_cards, hand_type=matched_hand, state=temp_state)

            lines.extend([
                f"\n🪐 **PLANET UPGRADE IMPACT ({item.label} -> {matched_hand})**:",
                f"- Hand Level: Lvl {cur_lvl} -> **Lvl {new_lvl}** (+{upgrade_chips} Chips, +{upgrade_mult} Mult)",
                f"- Base Stats: {cur_chips}c × {cur_mult}m -> **{new_chips}c × {new_mult}m**",
                f"- Representative Score: {old_res.score:,} pts -> **{new_res.score:,} pts** ({new_res.score - old_res.score:+,} pts)",
            ])
        else:
            lines.append(f"\n🪐 **PLANET EFFECT**: {item.desc or 'Levels up poker hand base chips & mult.'}")

    elif item.item_type in ("Tarot", "Spectral"):
        lines.append(f"\n🔮 **CARD EFFECT**: {item.desc or 'Deck manipulation or enhancement card.'}")
    elif item.item_type == "Voucher":
        v_data = find_voucher_data(getattr(item, "key", None) or item.label)
        if v_data:
            lines.append("\n" + format_voucher_impact(v_data, cost=item.cost, bank=net_cash).lstrip())
        else:
            lines.append(f"\n🎟️ **VOUCHER PERK**: {item.desc or 'Permanent run-wide enhancement.'}")
    elif item.item_type in ("BoosterPack", "Booster") or "pack" in item.label.lower():
        l_name = item.label.lower()
        interest_pres = " (✅ 100% max $5 interest is preserved!)" if net_cash >= 25 else f" (Interest: ${interest_after}/rnd)"
        lines.append(f"\n📦 **BOOSTER PACK IMPACT ({item.label})**:")
        lines.append(f"- Financial: Costs ${item_cost}, leaves ${net_cash} in bank{interest_pres}")

        if "celestial" in l_name or "небес" in l_name:
            primary_hand = "Two Pair"
            if state.poker_hands:
                most_played = max(state.poker_hands, key=lambda ph: ph.played, default=None)
                if most_played and most_played.played > 0:
                    primary_hand = most_played.name

            up_c, up_m = PLANET_HAND_UPGRADES.get(primary_hand, (20, 2))
            sample_cards = engine.get_representative_hand(primary_hand, state=state)
            old_res = engine.calculate_expected_hand_score(sample_cards, hand_type=primary_hand, state=state)

            cur_ph = next((ph for ph in state.poker_hands if ph.name.lower() == primary_hand.lower()), None)
            cur_lvl = cur_ph.level if cur_ph else 1
            cur_chips = cur_ph.chips if cur_ph else POKER_HAND_BASE.get(primary_hand, (10, 2))[0]
            cur_mult = cur_ph.mult if cur_ph else POKER_HAND_BASE.get(primary_hand, (10, 2))[1]

            new_poker_hands = []
            for ph in state.poker_hands:
                if ph.name.lower() == primary_hand.lower():
                    new_poker_hands.append(
                        ph.model_copy(update={"level": cur_lvl + 1, "chips": cur_chips + up_c, "mult": cur_mult + up_m})
                    )
                else:
                    new_poker_hands.append(ph)

            temp_state = state.model_copy(update={"poker_hands": new_poker_hands, "dollars": net_cash})
            new_res = engine.calculate_expected_hand_score(sample_cards, hand_type=primary_hand, state=temp_state)

            lines.extend([
                f"- Contents: Planet cards (permanently upgrade poker hand base stats).",
                f"- Scaling Projection for primary hand '{primary_hand}' (currently Lvl {cur_lvl}):",
                f"  * Level: Lvl {cur_lvl} -> **Lvl {cur_lvl + 1}** (+{up_c} Chips, +{up_m} Mult)",
                f"  * Expected Hand Score: {old_res.score:,} -> **{new_res.score:,} pts** (+{new_res.score - old_res.score:,} pts)",
                f"  * Recommendation: HIGH EV! This permanent upgrade scales every hand for the rest of the game.",
            ])
        elif "buffoon" in l_name or "шут" in l_name:
            open_slots = max(0, state.max_jokers - len(state.jokers))
            lines.extend([
                f"- Contents: Offers 2-4 Jokers to choose from.",
                f"- Capacity: {len(state.jokers)}/{state.max_jokers} Joker slots filled ({open_slots} open).",
                f"- Strategic Value: Top vehicle to discover xMult multipliers (Cavendish, Constellation, Card Sharp) or replace weak flat-stat Jokers.",
            ])
        elif "arcana" in l_name or "аркан" in l_name:
            lines.extend([
                f"- Contents: Offers 3 Tarot cards (choose 1).",
                f"- High-EV Pool: Resource generators (The High Priestess = 2 Planets, The Emperor = 2 Tarots, Judgement = 1 Joker), cash doubling (The Hermit +$20), deck trimming (The Hanged Man destroys 2 junk cards), cloning (Death), card buffs (Steel x1.5 / Glass x2), and ultra-rare The Soul (Legendary Joker)!",
            ])
        elif "spectral" in l_name or "спектр" in l_name:
            lines.extend([
                f"- Contents: Offers 2-4 Spectral cards (choose 1).",
                f"- Strategic Value: Run-defining legendary modifications (Ankh copies strongest Joker, Hex gives Polychrome x1.5 Mult, Cryptid duplicates key card, Immolate destroys 5 cards for +$20, The Soul spawns Legendary Joker).",
            ])
        elif "standard" in l_name or "стандарт" in l_name:
            lines.extend([
                f"- Contents: Offers playing cards with guaranteed special attributes (Enhancements, Editions, or Seals).",
                f"- Premium Targets: Steel Cards (x1.5 Mult in hand), Glass (x2 Mult), Polychrome (x1.5 Mult), Red Seals (retrigger), Purple Seals (Tarot on discard).",
                f"- ⚠️ Rule: Do not take plain un-enhanced cards to avoid deck dilution!",
            ])
        else:
            lines.append("- Contents: Booster selection pack.")

    return "\n".join(lines)


def execute_calculate_hand_odds(
    target_poker_hand: str,
    discard_card_ids: Any = None,
    state: GameState | None = None,
) -> str:
    """Calculates combinatorial probabilities of drawing into target poker hand

    from the remaining deck cards given a specific discard choice.
    """
    if not state or not state.hand:
        return "Error: No active hand cards available to evaluate odds."

    target_clean = (target_poker_hand or "").strip()
    if not target_clean:
        return "Error: Please specify `target_poker_hand` (e.g. 'Flush', 'Straight', 'Full House', 'Pair')."

    discard_ids = resolve_card_ids(discard_card_ids, state.hand)
    if len(discard_ids) > 5:
        return f"Error: Cannot discard more than 5 cards (requested {len(discard_ids)})."

    hand_map = {c.id: c for c in state.hand}
    for cid in discard_ids:
        if cid not in hand_map:
            return f"Error: Discard card ID '{cid}' is not in your current hand."

    kept_cards = [c for c in state.hand if c.id not in discard_ids]
    k_draw = len(discard_ids)
    deck_rem = (
        state.deck_summary.total_remaining
        if (state.deck_summary and state.deck_summary.total_remaining > 0)
        else max(1, state.deck_remaining)
    )
    k_draw = min(k_draw, deck_rem)

    # 1. Exact Hypergeometric Math for Flushes
    if target_clean.lower() == "flush":
        suits_held = Counter(c.suit for c in kept_cards)
        best_suit, held_in_suit = suits_held.most_common(1)[0] if suits_held else ("Spades", 0)
        needed_draw = max(0, 5 - held_in_suit)

        suit_rem = (
            state.deck_summary.suits.get(best_suit, 0)
            if (state.deck_summary and state.deck_summary.suits)
            else round(deck_rem / 4)
        )

        if needed_draw == 0:
            prob_exact = 100.0
        elif needed_draw > k_draw or suit_rem < needed_draw:
            prob_exact = 0.0
        else:
            # Hypergeometric: P(draw >= needed_draw cards of best_suit from deck_rem)
            total_ways = math.comb(deck_rem, k_draw)
            fav_ways = 0
            for i in range(needed_draw, min(k_draw, suit_rem) + 1):
                ways_suit = math.comb(suit_rem, i)
                ways_other = math.comb(deck_rem - suit_rem, k_draw - i)
                fav_ways += ways_suit * ways_other
            prob_exact = (fav_ways / max(1, total_ways)) * 100.0

        flush_lines = [
            f"🎲 [DRAW ODDS FOR FLUSH ({best_suit} ♥♦♣♠)]",
            f"Kept in Hand: {held_in_suit} {best_suit} cards (Need {needed_draw} more)",
            f"Discarding: {k_draw} cards | Drawing: {k_draw} cards from {deck_rem} remaining deck cards",
            f"Remaining {best_suit} in Deck: {suit_rem}/{deck_rem}",
            f"Exact Mathematical Probability: **{prob_exact:.1f}%** "
            + ("(Highly Favorable)" if prob_exact >= 60 else ("(Moderate Odds)" if prob_exact >= 35 else "(Low Odds)")),
        ]

        # Check for hand-locked jokers incompatible with Flush
        synergy_mismatches = []
        for j in state.jokers:
            req_hand = _match_hand_locked_joker(j)
            if req_hand and not _hand_contains_required_type("Flush", req_hand):
                synergy_mismatches.append(f"{j.label} (requires {req_hand})")
        if synergy_mismatches:
            flush_lines.append(
                f"\n⚠️ **SYNERGY MISMATCH WARNING**: You are digging for a **Flush**, but your build has: {', '.join(synergy_mismatches)}! "
                f"Completing a Flush will NOT trigger these jokers (+0 chips/mult)!"
            )
        return "\n".join(flush_lines)

    # 2. Fast Monte Carlo Simulation for any Arbitrary Poker Hand (Straight, Full House, Sets)
    engine = BalatroScoringEngine()

    # Use exact remaining deck cards if available from bridge, else fallback to approximation
    if state.deck_summary and state.deck_summary.cards:
        pool = list(state.deck_summary.cards)
    else:
        suits = ["Spades", "Hearts", "Clubs", "Diamonds"]
        ranks = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "Jack", "Queen", "King", "Ace"]
        full_deck = [
            CardModel(id=f"sim_{r}_{s}", rank=r, suit=s, nominal=BalatroScoringEngine.get_rank_value(r))
            for s in suits
            for r in ranks
        ]
        # Remove cards already in hand
        hand_sigs = {(c.rank, c.suit) for c in state.hand}
        pool = [c for c in full_deck if (c.rank, c.suit) not in hand_sigs]
        if len(pool) > deck_rem:
            pool = pool[:deck_rem]

    trials = 3000
    hit_target = 0
    all_outcomes: Counter[str] = Counter()

    for _ in range(trials):
        drawn = random.sample(pool, min(k_draw, len(pool))) if k_draw > 0 else []
        combined_hand = kept_cards + drawn

        # Find best 5-card hand
        best_hand_name = "High Card"
        best_score = 0
        sizes = [5] if len(combined_hand) >= 5 else [len(combined_hand)]
        for sz in sizes:
            for combo in itertools.combinations(combined_hand, sz):
                hname, bchips, bmult = engine.identify_poker_hand(combo)
                score = bchips * bmult
                if score > best_score:
                    best_score = score
                    best_hand_name = hname

        all_outcomes[best_hand_name] += 1
        if target_clean.lower() in best_hand_name.lower():
            hit_target += 1

    prob_target = (hit_target / trials) * 100.0

    lines = [
        f"🎲 [DRAW ODDS SIMULATION ({trials:,} Trials)]",
        f"Discarding: {k_draw} cards -> Drawing {k_draw} replacements from {deck_rem} cards in deck",
        f"Kept Cards ({len(kept_cards)}): {', '.join(c.rank + c.suit[0] for c in kept_cards) if kept_cards else 'None'}",
        f"Target Hand: **{target_clean}** -> Probability: **{prob_target:.1f}%**",
        f"\nExpected Hand Distribution from this Discard:",
    ]

    for hname, cnt in all_outcomes.most_common(5):
        p = (cnt / trials) * 100.0
        lines.append(f"  • {hname}: {p:.1f}%")

    if prob_target >= 60.0:
        lines.append("Assessment: 🟢 **Favorable Discard** (High probability of hitting target).")
    elif prob_target >= 30.0:
        lines.append("Assessment: 🟡 **Moderate Risk** (Viable if target hand is essential for score).")
    else:
        lines.append("Assessment: 🔴 **High Risk** (<30% chance). Consider keeping existing pairs/chips instead.")

    # Check for hand-locked jokers incompatible with target_clean
    synergy_mismatches = []
    for j in state.jokers:
        req_hand = _match_hand_locked_joker(j)
        if req_hand and not _hand_contains_required_type(target_clean, req_hand):
            synergy_mismatches.append(f"{j.label} (requires {req_hand})")
    if synergy_mismatches:
        lines.append(
            f"\n⚠️ **SYNERGY MISMATCH WARNING**: You are digging for **{target_clean}**, but your Joker build has: {', '.join(synergy_mismatches)}! "
            f"Hitting {target_clean} will NOT trigger these jokers (+0 chips/mult)!"
        )

    return "\n".join(lines)


def execute_calculation_tool(
    tool_name: str,
    arguments: dict[str, Any],
    state: GameState | None = None,
) -> str:
    """Dispatches a calculation or simulation tool call to its respective local handler."""
    if tool_name == "python_eval":
        return execute_python_eval(arguments.get("code", ""), state=state)
    elif tool_name == "simulate_hand":
        return execute_simulate_hand(arguments.get("card_ids", []), state=state)
    elif tool_name == "simulate_shop_buy":
        return execute_simulate_shop_buy(
            arguments.get("shop_index", 0),
            arguments.get("sell_joker_id", ""),
            state=state,
        )
    elif tool_name == "calculate_hand_odds":
        return execute_calculate_hand_odds(
            arguments.get("target_poker_hand", ""),
            arguments.get("discard_card_ids", []),
            state=state,
        )
    else:
        return f"Error: Unrecognized calculation tool '{tool_name}'."
