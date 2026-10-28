"""Unit tests for tool guardrails and cheat prevention in Balatro agent."""

from __future__ import annotations

import sys
from pathlib import Path
AGENT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_DIR))

import unittest
from unittest.mock import AsyncMock

from src.agent.runner import BalatroAgentRunner
from src.bridge.client import normalize_id_list
from src.bridge.schema import GameState
from src.engine.tools import ToolCallAction, get_available_tools


class TestToolGuardrails(unittest.TestCase):
    """Verifies that illegal actions (cheats) cannot be called or emitted by LLM."""

    def test_normalize_id_list_string_and_variants(self) -> None:
        """Verifies that normalize_id_list correctly coerces raw strings, comma-separated values, and lists."""
        # Single string id (as emitted when LLM passes card_ids="c_30990")
        self.assertEqual(normalize_id_list("c_30990"), ["c_30990"])

        # Comma-separated string
        self.assertEqual(normalize_id_list("c_30990, c_30991"), ["c_30990", "c_30991"])

        # Stringified python/json list
        self.assertEqual(normalize_id_list("['c_30990', 'c_30991']"), ["c_30990", "c_30991"])
        self.assertEqual(normalize_id_list('["c_30990", "c_30991"]'), ["c_30990", "c_30991"])

        # Standard list
        self.assertEqual(normalize_id_list(["c_30990", "c_30991"]), ["c_30990", "c_30991"])

        # List with embedded comma-separated items
        self.assertEqual(normalize_id_list(["c_30990, c_30991"]), ["c_30990", "c_30991"])

        # Empty / None / invalid inputs
        self.assertEqual(normalize_id_list(None), [])
        self.assertEqual(normalize_id_list(""), [])
        self.assertEqual(normalize_id_list([]), [])

    def test_string_shop_index_is_normalized_before_bridge_and_recording(self) -> None:
        """Providers may return "1" despite the integer JSON schema."""
        action = ToolCallAction(
            name="buy_shop_item",
            arguments={"shop_index": "1"},
            reason="Buy the second item",
        )
        self.assertEqual(action.arguments["shop_index"], 1)

        runner = BalatroAgentRunner.__new__(BalatroAgentRunner)
        runner.bridge = AsyncMock()
        runner.bridge.buy_card.return_value = True
        import asyncio
        self.assertTrue(asyncio.run(runner._execute_tool_action(action)))
        runner.bridge.buy_card.assert_awaited_once_with(1)

    def test_discard_blocked_when_zero_discards_left(self) -> None:
        """When discards_left == 0, discard_cards must be excluded from tool definitions."""
        state = GameState(stage="SELECTING_HAND", discards_left=0, hands_left=3)
        tools = [t["function"]["name"] for t in get_available_tools(state)]
        self.assertNotIn("discard_cards", tools)
        self.assertIn("play_cards", tools)

    def test_discard_allowed_when_discards_available(self) -> None:
        """When discards_left > 0, discard_cards must be available in SELECTING_HAND."""
        state = GameState(stage="SELECTING_HAND", discards_left=2, hands_left=3)
        tools = [t["function"]["name"] for t in get_available_tools(state)]
        self.assertIn("discard_cards", tools)
        self.assertIn("play_cards", tools)

    def test_play_blocked_when_zero_hands_left(self) -> None:
        """When hands_left == 0, play_cards must be excluded."""
        state = GameState(stage="SELECTING_HAND", discards_left=2, hands_left=0)
        tools = [t["function"]["name"] for t in get_available_tools(state)]
        self.assertNotIn("play_cards", tools)

    def test_shop_stage_filtering(self) -> None:
        """In SHOP stage, play_cards and discard_cards must never be exposed."""
        state = GameState(stage="SHOP", dollars=15)
        tools = [t["function"]["name"] for t in get_available_tools(state)]
        self.assertNotIn("play_cards", tools)
        self.assertNotIn("discard_cards", tools)
        self.assertIn("buy_shop_item", tools)
        self.assertIn("reroll_shop", tools)
        self.assertIn("end_shop", tools)

    def test_blind_select_stage_filtering(self) -> None:
        """In BLIND_SELECT stage, combat actions and shop buys must be excluded."""
        state = GameState(stage="BLIND_SELECT", dollars=10)
        tools = [t["function"]["name"] for t in get_available_tools(state)]
        self.assertNotIn("play_cards", tools)
        self.assertNotIn("discard_cards", tools)
        self.assertNotIn("buy_shop_item", tools)
        self.assertIn("select_blind", tools)
        self.assertIn("skip_blind", tools)

    def test_calculation_turns_exhaustion(self) -> None:
        """When calculation budget is hit, calculation tools must be removed."""
        state = GameState(stage="SELECTING_HAND", discards_left=2, hands_left=3)
        tools = [t["function"]["name"] for t in get_available_tools(state, calculation_turns=8, max_calc_turns=8)]
        self.assertNotIn("python_eval", tools)
        self.assertNotIn("simulate_hand", tools)
        self.assertNotIn("calculate_hand_odds", tools)
        self.assertIn("play_cards", tools)
        self.assertIn("discard_cards", tools)


if __name__ == "__main__":
    unittest.main()
