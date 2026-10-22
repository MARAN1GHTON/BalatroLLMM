# -*- coding: utf-8 -*-
"""Unit tests for Lorebook Engine integration in Balatro Agent."""

import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_DIR))

from src.bridge.schema import (
    GameState,
    CardModel,
    JokerModel,
    BlindModel,
    ShopModel,
    ShopItemModel,
    PokerHandModel,
)
from src.engine.lorebook import LorebookEngine, get_lorebook_insights_for_state
from src.engine.serializer import serialize_game_state_to_markdown


def test_lorebook_loading():
    """Verify Lorebook correctly parses all markdown knowledge cards."""
    engine = LorebookEngine.get_instance()
    count = len(engine.entries)
    print(f"[OK] Lorebook entries loaded: {count}")
    assert count >= 330, f"Expected at least 330 entries, got {count}"


def test_lorebook_boss_matching():
    """Verify active and upcoming boss cards are matched."""
    engine = LorebookEngine.get_instance()
    state = GameState(
        stage="SELECTING_HAND",
        ante=3,
        blind=BlindModel(name="The Needle", chips_needed=4000, reward=5, is_boss=True),
        hand=[CardModel(id="c_1", rank="Ace", suit="Spades")],
        poker_hands=[PokerHandModel(name="High Card", level=1, chips=5, mult=1, order=1)],
    )
    matched = engine.match_state(state)
    titles = [e.title for e in matched]
    print("[OK] Matched Needle titles count:", len(matched))
    assert any("Needle" in t or "Игла" in t for t in titles), f"Expected The Needle in {titles}"


def test_lorebook_shop_matching():
    """Verify shop items like Cavendish and Tier 2 vouchers are matched in SHOP stage."""
    engine = LorebookEngine.get_instance()
    state = GameState(
        stage="SHOP",
        ante=4,
        dollars=35,
        shop=ShopModel(items=[
            ShopItemModel(index=1, id="j_cavendish", label="Cavendish", item_type="Joker", cost=4),
            ShopItemModel(index=2, id="v_antimatter", label="Antimatter", item_type="Voucher", cost=10),
        ]),
    )
    matched = engine.match_state(state)
    titles = [e.title for e in matched]
    print("[OK] Matched Shop items count:", len(matched))
    has_cavendish = any("Cavendish" in t or "Кэвендиш" in t for t in titles)
    has_antimatter = any("Antimatter" in t or "Антиматерия" in t for t in titles)
    assert has_cavendish or has_antimatter, f"Expected Cavendish or Antimatter in {titles}"


def test_lorebook_serializer_integration():
    """Verify serialize_game_state_to_markdown includes Lorebook section."""
    state = GameState(
        stage="SHOP",
        ante=4,
        dollars=20,
        shop=ShopModel(items=[
            ShopItemModel(index=1, id="j_cavendish", label="Cavendish", item_type="Joker", cost=4),
        ]),
    )
    md = serialize_game_state_to_markdown(state)
    assert "Lorebook Tactical Insights" in md
    assert "Cavendish" in md or "Кэвендиш" in md
    print("[OK] Lorebook Serializer Integration passed!")


def test_lorebook_telemetry_and_reporting(tmp_path):
    """Verify LorebookEngine tracks usage statistics for reports."""
    from src.engine.lorebook import LorebookEngine
    from src.engine.recorder import BalatroRunRecorder

    engine = LorebookEngine.get_instance()
    engine.reset_telemetry()
    assert engine.get_usage_summary()["total_activations"] == 0

    state = GameState(
        stage="SHOP",
        ante=4,
        dollars=20,
        shop=ShopModel(items=[
            ShopItemModel(index=1, id="j_cavendish", label="Cavendish", item_type="Joker", cost=4),
        ]),
    )
    # Trigger formatted insights
    _ = serialize_game_state_to_markdown(state)

    summary = engine.get_usage_summary()
    assert summary["total_activations"] >= 1
    assert summary["unique_cards_matched"] >= 1
    assert any("Cavendish" in k or "Кэвендиш" in k for k in summary["activation_counts"].keys())

    # Verify report includes the Lorebook section
    recorder = BalatroRunRecorder(output_dir=tmp_path, model="test-model", provider="test")
    # Manually re-trigger one insight to ensure recorder picks it up
    _ = serialize_game_state_to_markdown(state)
    report_md = recorder.generate_markdown_report(victory=False)
    assert "## 📖 Tactical Lorebook Intelligence Report" in report_md
    assert "Cavendish" in report_md or "Кэвендиш" in report_md


if __name__ == "__main__":
    test_lorebook_loading()
    test_lorebook_boss_matching()
    test_lorebook_shop_matching()
    test_lorebook_serializer_integration()
    print("\n[ALL LOREBOOK TESTS PASSED SUCCESSFULLY!]")
