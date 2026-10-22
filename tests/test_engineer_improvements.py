import sys
from pathlib import Path
AGENT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_DIR))

import unittest
from src.bridge.schema import GameState, JokerModel, BlindModel, DeckSummaryModel, CardModel, ShopItemModel
from src.engine.serializer import (
    serialize_game_state_to_markdown,
    check_build_balance,
    get_conditional_joker_statuses,
    get_boss_card_debuff,
)
from src.engine.vouchers import format_voucher_impact, find_voucher_data


class TestEngineerImprovements(unittest.TestCase):
    def test_dynamic_joker_warnings(self):
        # 1. Green Joker
        gj = JokerModel(
            id="j_green_1",
            key="j_green_joker",
            label="Green Joker",
            mult=12,
            effect_desc="+1 Mult per hand, -1 Mult per discard"
        )
        # 2. Ride the Bus
        rtb = JokerModel(
            id="j_bus_1",
            key="j_ride_the_bus",
            label="Ride the Bus",
            mult=10,
            effect_desc="+1 Mult per hand without face cards"
        )
        # 3. Ice Cream
        ic = JokerModel(
            id="j_ice_1",
            key="j_ice_cream",
            label="Ice Cream",
            chips=65,
            effect_desc="+100 Chips, loses 5 chips per hand"
        )

        state = GameState(jokers=[gj, rtb, ic])
        notes = get_conditional_joker_statuses(state)
        notes_str = "\n".join(notes)

        self.assertIn("GREEN JOKER STATUS", notes_str)
        self.assertIn("LOSES -1 MULT PER DISCARD", notes_str)
        self.assertIn("+12 Mult", notes_str)

        self.assertIn("RIDE THE BUS STATUS", notes_str)
        self.assertIn("RESETS TO +0 MULT", notes_str)
        self.assertIn("+10 Mult", notes_str)

        self.assertIn("ICE CREAM MELTING", notes_str)
        self.assertIn("-5 chips", notes_str)
        self.assertIn("+65 Chips", notes_str)

    def test_chip_starved_alert(self):
        # Heavy mult, zero chip jokers
        j1 = JokerModel(
            id="j1",
            key="j_joker",
            label="Joker",
            effect_type="mult",
            mult=4
        )
        j2 = JokerModel(
            id="j2",
            key="j_half",
            label="Half Joker",
            effect_type="mult",
            mult=20
        )
        j3 = JokerModel(
            id="j3",
            key="j_cavendish",
            label="Cavendish",
            effect_type="x_mult",
            x_mult=3.0
        )
        state = GameState(ante=3, jokers=[j1, j2, j3])
        warnings = check_build_balance(state)
        warn_str = "\n".join(warnings)

        self.assertIn("CHIP-STARVED ALERT", warn_str)
        self.assertIn("+0 BONUS CHIPS", warn_str)
        self.assertIn("+200% to +350%", warn_str)

    def test_required_combat_pace(self):
        state = GameState(
            stage="SELECTING_HAND",
            current_score=1000,
            target_score=16000,
            hands_left=3,
            discards_left=2,
            hand=[
                CardModel(id="c1", rank="Ace", suit="Spades"),
                CardModel(id="c2", rank="Ace", suit="Hearts"),
                CardModel(id="c3", rank="10", suit="Clubs"),
                CardModel(id="c4", rank="8", suit="Diamonds"),
                CardModel(id="c5", rank="2", suit="Spades"),
            ]
        )
        md = serialize_game_state_to_markdown(state)
        self.assertIn("Required Combat Pace", md)
        # 15,000 deficit / 3 hands = 5,000 chips/hand
        self.assertIn("5,000 chips/hand", md)

    def test_deck_breakdown_the_goad_debuff(self):
        deck_summary = DeckSummaryModel(
            total_remaining=25,
            suits={"Spades": 10, "Hearts": 5, "Clubs": 5, "Diamonds": 5},
            ranks={"Ace": 4, "King": 4, "10": 4},
            face_cards=8,
            aces=4
        )
        blind = BlindModel(
            name="The Goad",
            is_boss=True,
            debuff_rule="All Spades debuffed",
            chips_needed=40000
        )
        state = GameState(
            stage="SELECTING_HAND",
            blind=blind,
            deck_summary=deck_summary,
            current_score=5000,
            target_score=40000
        )
        cat, desc = get_boss_card_debuff(blind)
        self.assertEqual(cat, "Spades")

        md = serialize_game_state_to_markdown(state)
        # 25 cards (15 EFFECTIVE / 10 DEBUFFED by The Goad)
        self.assertIn("15 EFFECTIVE / 10 DEBUFFED by The Goad", md)
        self.assertIn("⛔ DEBUFFED - 0 chips/mult!", md)

    def test_deck_breakdown_the_plant_debuff(self):
        deck_summary = DeckSummaryModel(
            total_remaining=30,
            suits={"Spades": 8, "Hearts": 8, "Clubs": 7, "Diamonds": 7},
            ranks={"Jack": 4, "Queen": 4, "King": 4, "Ace": 4},
            face_cards=12,
            aces=4
        )
        blind = BlindModel(
            name="The Plant",
            is_boss=True,
            debuff_rule="All Face cards debuffed",
            chips_needed=50000
        )
        state = GameState(
            stage="SELECTING_HAND",
            blind=blind,
            deck_summary=deck_summary,
            current_score=10000,
            target_score=50000
        )
        cat, desc = get_boss_card_debuff(blind)
        self.assertEqual(cat, "Face")

        md = serialize_game_state_to_markdown(state)
        # 30 cards (18 EFFECTIVE / 12 DEBUFFED by The Plant)
        self.assertIn("18 EFFECTIVE / 12 DEBUFFED by The Plant", md)
        self.assertIn("Faces: 12 (⛔ DEBUFFED - 0 chips/mult!)", md)

    def test_directors_cut_reality_check(self):
        v_data = find_voucher_data("v_directors_cut")
        self.assertIsNotNone(v_data)

        # Budget deficient: $12 (voucher costs $10, leaves $2, cannot reroll)
        res_poor = format_voucher_impact(v_data, cost=10, bank=12)
        self.assertIn("TOTAL COST REALITY CHECK", res_poor)
        self.assertIn("CANNOT reroll the boss immediately", res_poor)

        # Budget sufficient: $25 (>= $20)
        res_rich = format_voucher_impact(v_data, cost=10, bank=25)
        self.assertIn("BUDGET VERIFIED", res_rich)
        self.assertIn("sufficient funds to buy this voucher and immediately reroll", res_rich)

    def test_negative_joker_objective_tone(self):
        # 1. Booster pack
        pack_item = ShopItemModel(
            index=0,
            id="pack_8ball",
            label="8 Ball",
            item_type="Joker",
            cost=0,
            desc="Create tarot on 8",
            edition="Negative"
        )
        state_pack = GameState(
            stage="OPENING_BOOSTER",
            pack_cards=[pack_item],
            pack_choices=1
        )
        md_pack = serialize_game_state_to_markdown(state_pack)
        self.assertIn("NEGATIVE JOKER IN PACK", md_pack)
        self.assertIn("takes 0 Joker capacity", md_pack)
        self.assertNotIn("MANDATORY", md_pack)
        self.assertNotIn("ОБЯЗАТЕЛЬНО", md_pack)
        self.assertNotIn("MUST BUY", md_pack)

        # 2. Shop item
        shop_item = ShopItemModel(
            index=0,
            id="shop_8ball",
            label="8 Ball",
            item_type="Joker",
            cost=6,
            desc="Create tarot on 8",
            edition="Negative"
        )
        state_shop = GameState(
            stage="SHOP",
            shop={"items": [shop_item], "reroll_cost": 5}
        )
        md_shop = serialize_game_state_to_markdown(state_shop)
        self.assertIn("NEGATIVE JOKER IN SHOP", md_shop)
        self.assertIn("takes 0 Joker capacity", md_shop)
        self.assertNotIn("MUST BUY", md_shop)
        self.assertNotIn("MANDATORY", md_shop)

    def test_card_enhancement_edition_seal_descriptions(self):
        c = CardModel(
            id="c_test",
            rank="King",
            suit="Hearts",
            enhancement="Glass Card",
            edition="Polychrome",
            seal="Red"
        )
        from src.engine.serializer import format_card_compact
        c_str = format_card_compact(c)
        self.assertIn("Glass (X2 Mult", c_str)
        self.assertIn("Poly (X1.5 Mult)", c_str)
        self.assertIn("Red Seal (Retrigger 1x)", c_str)

    def test_active_joker_editions_display(self):
        j_neg = JokerModel(
            id="j_neg",
            key="j_8_ball",
            label="8 Ball",
            edition="Negative"
        )
        j_foil = JokerModel(
            id="j_foil",
            key="j_joker",
            label="Joker",
            edition="Foil"
        )
        state = GameState(jokers=[j_neg, j_foil])
        md = serialize_game_state_to_markdown(state)
        self.assertIn("[NEGATIVE (+1 JOKER SLOT)]", md)
        self.assertIn("[FOIL (+50 CHIPS)]", md)

    def test_scoring_engine_joker_editions(self):
        from src.engine.scoring import BalatroScoringEngine
        engine = BalatroScoringEngine()

        j_poly = JokerModel(
            id="j_poly",
            key="j_joker",
            label="Joker",
            mult=4,
            edition="Polychrome"
        )
        cards = [
            CardModel(id="c1", rank="Ace", suit="Spades"),
            CardModel(id="c2", rank="Ace", suit="Hearts"),
        ]
        state = GameState(jokers=[j_poly])
        res = engine.calculate_expected_hand_score(cards, hand_type="Pair", state=state)
        # Pair base: 10 chips, 2 mult
        # Aces: 11 + 11 = 22 chips
        # Joker: +4 mult -> (2 + 4) = 6 mult
        # Polychrome on Joker: X1.5 mult -> round(6 * 1.5) = 9 mult
        # Chips: 10 + 22 = 32 chips
        # Total score: 32 * 9 = 288
        self.assertEqual(res.mult, 9)
        self.assertEqual(res.score, 32 * 9)
        self.assertTrue(any("Poly" in b for b in res.breakdown))


if __name__ == "__main__":
    unittest.main()
