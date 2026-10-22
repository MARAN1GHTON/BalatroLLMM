# -*- coding: utf-8 -*-
"""Unit tests verifying Scaling Ceiling Warning in Shop and The Fish Draw Profile."""

import sys
from pathlib import Path

# Ensure balatro_agent src is on PYTHONPATH
AGENT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_DIR))

from src.bridge.schema import (
    GameState,
    CardModel,
    JokerModel,
    BlindModel,
    ShopModel,
    ShopItemModel,
    DeckSummaryModel,
    AnteBlindScheduleModel,
    PokerHandModel,
)
from src.engine.serializer import (
    is_xmult_joker,
    get_shop_recommendations_for_balance,
    get_boss_readiness_alert,
    format_draw_profile_intel,
    serialize_game_state_to_markdown,
)


def test_is_xmult_joker():
    """Verify accurate detection of xMult multipliers across labels, keys, and editions."""
    cavendish = JokerModel(id="j_1", key="j_cavendish", label="Cavendish", effect_desc="X3 Mult. 1 in 1000 chance to go extinct.")
    assert is_xmult_joker(cavendish) is True, "Cavendish should be xMult"

    ancient = JokerModel(id="j_2", key="j_ancient", label="Ancient Joker", effect_desc="Each played card of Spades gives X1.5 Mult.")
    assert is_xmult_joker(ancient) is True, "Ancient Joker should be xMult"

    poly_joker = JokerModel(id="j_3", key="j_joker", label="Joker", edition="Polychrome", effect_desc="+4 Mult.")
    assert is_xmult_joker(poly_joker) is True, "Polychrome Joker should be xMult"

    flat_mystic = JokerModel(id="j_4", key="j_mystic_summit", label="Mystic Summit", effect_type="mult", effect_desc="+15 Mult when 0 discards remain.")
    assert is_xmult_joker(flat_mystic) is False, "Mystic Summit is flat mult, not xMult"

    flat_walkie = JokerModel(id="j_5", key="j_walkie_talkie", label="Walkie Talkie", effect_type="mult", effect_desc="+10 chips and +4 mult for 10s and 4s.")
    assert is_xmult_joker(flat_walkie) is False, "Walkie Talkie is flat mult/chips, not xMult"

    print("[OK] test_is_xmult_joker passed!")


def test_shop_scaling_ceiling_warning():
    """Verify that Ante 3+ with 0 xMult jokers triggers the SCALING CEILING WARNING and surplus cash alert."""
    flat_jokers = [
        JokerModel(id="j_1", key="j_ice_cream", label="Ice Cream", effect_type="chips", effect_desc="+100 Chips"),
        JokerModel(id="j_2", key="j_mystic_summit", label="Mystic Summit", effect_type="mult", effect_desc="+15 Mult"),
        JokerModel(id="j_3", key="j_walkie_talkie", label="Walkie Talkie", effect_type="mult", effect_desc="+4 Mult"),
        JokerModel(id="j_4", key="j_even_steven", label="Even Steven", effect_type="mult", effect_desc="+4 Mult on evens"),
        JokerModel(id="j_5", key="j_hiker", label="Hiker", effect_type="chips", effect_desc="+5 Chips per card"),
    ]
    shop_items = [
        ShopItemModel(index=1, id="c_uranus", label="Uranus", item_type="Planet", cost=3, desc="+1 Mult, +20 Chips to Two Pair"),
        ShopItemModel(index=2, id="p_celestial", label="Celestial Pack", item_type="Booster", cost=4, desc="Choose 1 of 3 Planet cards"),
    ]
    state = GameState(
        stage="SHOP",
        ante=4,
        dollars=41,
        jokers=flat_jokers,
        shop=ShopModel(items=shop_items, reroll_cost=5),
        blind=BlindModel(name="The Fish", chips_needed=10000, reward=5, is_boss=True),
        ante_blinds=[
            AnteBlindScheduleModel(type="Boss", name="The Fish", chips=10000, reward=5),
        ],
    )

    recs = get_shop_recommendations_for_balance(state)
    recs_text = "\n".join(recs)

    assert "SHOP SCALING TELEMETRY" in recs_text, "Should trigger SHOP SCALING TELEMETRY"
    assert "0 xMult Jokers" in recs_text, "Should report 0 xMult Jokers"
    assert "10,000 chips" in recs_text, "Should mention 10,000 chips target"
    assert "SURPLUS CASH TO SPEND" in recs_text, "Should alert to surplus cash above $25"
    assert "$16" in recs_text, "Should calculate $41 - $25 = $16 surplus"

    print("[OK] test_shop_scaling_ceiling_warning passed!")


def test_boss_readiness_the_fish():
    """Verify that get_boss_readiness_alert issues high-risk alert for The Fish when requiring >= 3 hands."""
    state = GameState(
        stage="SHOP",
        ante=4,
        dollars=41,
        hands_left=4,
        blind=BlindModel(name="The Fish", chips_needed=10000, reward=5, is_boss=True),
        ante_blinds=[
            AnteBlindScheduleModel(type="Boss", name="The Fish", chips=10000, reward=5),
        ],
    )
    # Primary hand score ~3,200 chips -> requires 4 hands to reach 10,000 chips
    alerts = get_boss_readiness_alert(state, primary_hand="Two Pair", primary_hand_score=3200)
    alerts_text = "\n".join(alerts)

    assert "BOSS READINESS: THE FISH" in alerts_text, "Should mention The Fish readiness"
    assert "HIGH RISK" in alerts_text, "Should flag HIGH RISK when requiring 4 hands"
    assert "4 hands" in alerts_text, "Should state 4 hands needed"
    assert "Celestial Packs" in alerts_text, "Should recommend hand levels or xMult"

    print("[OK] test_boss_readiness_the_fish passed!")


def test_fish_draw_profile_intel():
    """Verify that format_draw_profile_intel computes empirical draw pile odds, pairing chances, and synergies."""
    hand = [
        CardModel(id="c_1", rank="King", suit="Spades", face_down=False),
        CardModel(id="c_2", rank="King", suit="Hearts", face_down=False),
        CardModel(id="c_3", rank="Queen", suit="Clubs", face_down=False),
        CardModel(id="c_4", rank="?", suit="None", face_down=True),
        CardModel(id="c_5", rank="?", suit="None", face_down=True),
        CardModel(id="c_6", rank="?", suit="None", face_down=True),
        CardModel(id="c_7", rank="?", suit="None", face_down=True),
        CardModel(id="c_8", rank="?", suit="None", face_down=True),
    ]
    deck_summary = DeckSummaryModel(
        total_remaining=25,
        suits={"Spades": 6, "Hearts": 7, "Clubs": 6, "Diamonds": 6},
        ranks={
            "2": 2, "3": 1, "4": 2, "5": 2, "6": 2, "7": 2, "8": 2,
            "9": 2, "10": 3, "Jack": 2, "Queen": 1, "King": 2, "Ace": 2
        },
        face_cards=5,
        aces=2,
    )
    jokers = [
        JokerModel(id="j_1", key="j_even_steven", label="Even Steven", effect_type="mult", effect_desc="+4 Mult on evens"),
        JokerModel(id="j_2", key="j_walkie_talkie", label="Walkie Talkie", effect_type="mult", effect_desc="+10c/+4m on 10s and 4s"),
        JokerModel(id="j_3", key="j_hiker", label="Hiker", effect_type="chips", effect_desc="+5 Chips permanent"),
    ]
    state = GameState(
        stage="SELECTING_HAND",
        ante=4,
        dollars=35,
        hands_left=3,
        discards_left=0,
        blind=BlindModel(name="The Fish", chips_needed=10000, reward=5, is_boss=True),
        hand=hand,
        jokers=jokers,
        deck_summary=deck_summary,
        deck_remaining=25,
    )

    lines = format_draw_profile_intel(state, face_down_count=5, is_fish=True)
    intel_text = "\n".join(lines)

    assert "THE FISH" in intel_text
    assert "TACTICAL INTEL & DRAW PROFILE" in intel_text
    assert "(5/8 cards face-down)" in intel_text
    assert "REMAINING DRAW PILE (25 cards)" in intel_text
    assert "PAIRING ODDS WITH HELD ANCHORS" in intel_text
    assert "K (held 2, 2 in deck)" in intel_text
    assert "Q (held 1, 1 in deck)" in intel_text
    assert "probability" in intel_text.lower()
    assert "Even Steven (+4 Mult)" in intel_text
    assert "Walkie Talkie (+10c/+4m)" in intel_text
    assert "Hiker (+5 chips permanent)" in intel_text
    assert "TACTICAL DIRECTIVE (AVOID BLIND PANIC)" in intel_text
    assert "Pad Visible Anchors" in intel_text

    print("[OK] test_fish_draw_profile_intel passed!")


def test_full_markdown_serialization():
    """Verify serialize_game_state_to_markdown integrates both features without error."""
    hand = [
        CardModel(id="c_1", rank="King", suit="Spades", face_down=False),
        CardModel(id="c_2", rank="?", suit="None", face_down=True),
    ]
    state = GameState(
        stage="SELECTING_HAND",
        ante=4,
        dollars=20,
        hands_left=2,
        discards_left=1,
        blind=BlindModel(name="The Fish", chips_needed=10000, reward=5, is_boss=True),
        hand=hand,
        deck_remaining=30,
        poker_hands=[PokerHandModel(name="Pair", level=1, chips=10, mult=2, order=1)],
    )
    md = serialize_game_state_to_markdown(state)
    assert "THE FISH" in md
    assert "TACTICAL INTEL & DRAW PROFILE" in md

    print("[OK] test_full_markdown_serialization passed!")


if __name__ == "__main__":
    test_is_xmult_joker()
    test_shop_scaling_ceiling_warning()
    test_boss_readiness_the_fish()
    test_fish_draw_profile_intel()
    test_full_markdown_serialization()
    print("\n[ALL TESTS PASSED SUCCESSFULLY!]")
