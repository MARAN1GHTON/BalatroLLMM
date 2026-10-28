import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_DIR))

from src.bridge.schema import (
    AnteBlindScheduleModel,
    BlindModel,
    GameState,
    ShopItemModel,
    ShopModel,
)
from src.engine.scoring import BalatroScoringEngine
from src.engine.serializer import (
    get_boss_eval_state,
    get_estimated_boss_target,
    get_upcoming_blind_target,
    project_shop_joker_impact,
    serialize_game_state_to_markdown,
)


def test_ante_runway_uses_exact_bridge_targets():
    state = GameState(
        stage="SHOP",
        ante=8,
        ante_targets={"8": 100000, "12": 100000000, "16": 100000000000},
    )
    assert state.ante_targets == {8: 100000, 12: 100000000, 16: 100000000000}

    md = serialize_game_state_to_markdown(state, max_hand_score=300000)
    assert "ANTE RUNWAY" in md
    assert "Ante 8: **100,000**" in md
    assert "Ante 12: **100,000,000**" in md
    assert "Ante 16: **100,000,000,000**" in md
    assert "before each blind/boss multiplier" in md
    assert "Endless keeps scaling after Ante 8" in md
    assert "unknown" not in next(line for line in md.splitlines() if "ANTE RUNWAY" in line)


def test_old_bridge_marks_endless_targets_unknown_instead_of_guessing():
    state = GameState(
        stage="BLIND_SELECT",
        ante=12,
        target_score=0,
        blind=BlindModel(name="Boss Blind", chips_needed=0),
    )
    md = serialize_game_state_to_markdown(state, max_hand_score=300000)
    runway = next(line for line in md.splitlines() if "ANTE RUNWAY" in line)
    assert runway.count("unknown — update bridge") == 3
    assert "Ante 12 Boss (Boss): **5,000 chips**" not in md
    assert get_estimated_boss_target(state) is None
    _, _, boss_chips = get_boss_eval_state(state)
    assert boss_chips == 0


def test_exact_current_boss_schedule_remains_authoritative():
    state = GameState(
        stage="SHOP",
        ante=11,
        ante_targets={8: 100000, 12: 100000000, 16: 100000000000},
        ante_blinds=[
            AnteBlindScheduleModel(type="Boss", name="Small Blind", chips=7200000)
        ],
        blind=BlindModel(name="Big Blind", upcoming_boss_name="Small Blind"),
    )
    md = serialize_game_state_to_markdown(state, max_hand_score=1000000)
    assert "Ante 11 Boss (Small Blind): **7,200,000 chips**" in md
    _, _, boss_chips = get_boss_eval_state(state)
    assert boss_chips == 7200000


def test_invalid_ante_targets_are_ignored():
    state = GameState(ante_targets={"bad": "1", "12": "invalid", "16": 0, "8": "100000"})
    assert state.ante_targets == {8: 100000}


def test_unknown_target_cannot_produce_false_one_shot_claim():
    state = GameState(stage="SHOP", ante=12, target_score=0)
    assert get_upcoming_blind_target(state) == ("Next Blind", 0)
    impact = project_shop_joker_impact(
        ShopItemModel(
            index=0,
            id="j_joker",
            key="j_joker",
            label="Joker",
            item_type="Joker",
            cost=2,
            desc="+4 Mult",
        ),
        state,
        "Pair",
        BalatroScoringEngine(),
    )
    assert impact is not None
    assert "Next target unknown — update bridge" in impact
    assert "ONE-SHOTS" not in impact

    state.shop = ShopModel(
        items=[
            ShopItemModel(
                index=0,
                id="j_joker",
                key="j_joker",
                label="Joker",
                item_type="Joker",
                cost=2,
                desc="+4 Mult",
            )
        ]
    )
    md = serialize_game_state_to_markdown(state)
    assert "no readiness claim" in md
    assert "sufficient)" not in md
