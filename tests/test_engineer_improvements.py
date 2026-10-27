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

    def test_scoring_the_arm_downgrades_level_before_scoring(self):
        """The Arm decreases the level of the played poker hand by 1 BEFORE scoring
        (min level 1). Regression test from run 26.10.2026: simulate_hand projected
        10,336 for a Straight while the game scored 9,576."""
        from src.engine.scoring import BalatroScoringEngine
        from src.bridge.schema import PokerHandModel

        engine = BalatroScoringEngine()
        cards = [
            CardModel(id="c1", rank="King", suit="Spades"),
            CardModel(id="c2", rank="King", suit="Hearts"),
        ]
        # Pair leveled to 4 via Jupiter: base 10+15*3=55 chips, 2+1*3=5 mult
        leveled_pair = PokerHandModel(name="Pair", level=4, chips=55, mult=5, played=6)
        poker_hands = [leveled_pair]

        # Normal blind: leveled Pair counts at full level (55 x 5 base).
        state_normal = GameState(
            jokers=[],
            poker_hands=poker_hands,
            blind=BlindModel(name="Small Blind", chips_needed=300),
        )
        res_normal = engine.calculate_expected_hand_score(cards, hand_type="Pair", state=state_normal)

        # The Arm: level drops by 1 before scoring -> 55-15=40 chips, 5-1=4 mult base.
        state_arm = GameState(
            jokers=[],
            poker_hands=poker_hands,
            blind=BlindModel(name="The Arm", chips_needed=40000, is_boss=True),
        )
        res_arm = engine.calculate_expected_hand_score(cards, hand_type="Pair", state=state_arm)

        # Card chips are unaffected: K+K = 20. So chips drop by exactly 15,
        # and mult drops by exactly 1 (level step for Pair).
        self.assertEqual(res_normal.chips - res_arm.chips, 15)
        self.assertEqual(res_normal.mult - res_arm.mult, 1)
        self.assertTrue(any("TheArm" in b for b in res_arm.breakdown))
        self.assertFalse(any("TheArm" in b for b in res_normal.breakdown))

        # Level-1 hand must not be downgraded below level 1.
        pair_lvl1 = PokerHandModel(name="Pair", level=1, chips=10, mult=2, played=0)
        state_lvl1 = GameState(
            jokers=[],
            poker_hands=[pair_lvl1],
            blind=BlindModel(name="The Arm", chips_needed=40000, is_boss=True),
        )
        res_lvl1 = engine.calculate_expected_hand_score(cards, hand_type="Pair", state=state_lvl1)
        self.assertEqual(res_lvl1.chips, res_lvl1.chips)  # no crash
        self.assertTrue(any("level 1" in b for b in res_lvl1.breakdown))
        # Base must remain at level-1 stats: 10 chips + 20 card chips = 30.
        self.assertEqual(res_lvl1.chips, 30)
        self.assertEqual(res_lvl1.mult, 2)

    def test_hiker_only_upgrades_cards_that_actually_score(self):
        """Hiker grants +5 permanent Chips per SCORING trigger, not to every played card.
        Regression guard for Pair/High Card padding, persistent card bonuses, and Splash."""
        from src.engine.scoring import BalatroScoringEngine

        engine = BalatroScoringEngine()
        hiker = JokerModel(id="j_h", key="j_hiker", label="Hiker", effect_type="chips")
        splash = JokerModel(id="j_s", key="j_splash", label="Splash", effect_type="utility")
        state = GameState(
            stage="SELECTING_HAND",
            jokers=[hiker],
            blind=BlindModel(name="Small Blind", chips_needed=300),
        )
        pair_with_three_kickers = [
            CardModel(id="c1", rank="King", suit="Hearts"),
            CardModel(id="c2", rank="King", suit="Spades"),
            CardModel(id="c3", rank="2", suit="Clubs"),
            CardModel(id="c4", rank="3", suit="Diamonds"),
            CardModel(id="c5", rank="4", suit="Clubs"),
        ]

        # Pair base 10 + two Kings (10+10) + two Hiker triggers (5+5) = 40.
        # The 2/3/4 are played but do NOT score and receive no Hiker trigger.
        pair_result = engine.calculate_expected_hand_score(
            pair_with_three_kickers, hand_type="Pair", state=state
        )
        self.assertEqual(pair_result.chips, 40)
        self.assertEqual(pair_result.mult, 2)
        self.assertEqual(pair_result.score, 80)

        # High Card base 5 + Ace 11 + one Hiker trigger 5 = 21, not +5 for all five cards.
        high_card_with_padding = [
            CardModel(id="h1", rank="Ace", suit="Hearts"),
            CardModel(id="h2", rank="King", suit="Spades"),
            CardModel(id="h3", rank="9", suit="Clubs"),
            CardModel(id="h4", rank="8", suit="Diamonds"),
            CardModel(id="h5", rank="7", suit="Clubs"),
        ]
        high_result = engine.calculate_expected_hand_score(
            high_card_with_padding, hand_type="High Card", state=state
        )
        self.assertEqual(high_result.chips, 21)
        self.assertEqual(high_result.score, 21)

        # Splash makes all five played cards scoring cards, so the same Pair gains 5 triggers.
        state.jokers = [hiker, splash]
        splash_result = engine.calculate_expected_hand_score(
            pair_with_three_kickers, hand_type="Pair", state=state
        )
        self.assertEqual(splash_result.chips - pair_result.chips, 24)
        self.assertEqual(splash_result.chips, 64)

        # Permanent Hiker bonuses arrive in CardModel.chip_bonus from Lua and persist in projections.
        state.jokers = [hiker]
        upgraded_pair = [
            CardModel(id="u1", rank="King", suit="Hearts", chip_bonus=20),
            CardModel(id="u2", rank="King", suit="Spades", chip_bonus=5),
        ]
        upgraded_result = engine.calculate_expected_hand_score(
            upgraded_pair, hand_type="Pair", state=state
        )
        # 10 base + 20 existing + 5 existing + 20 rank + 10 new Hiker triggers = 65.
        self.assertEqual(upgraded_result.chips, 65)

    def test_peak_hand_provenance_warning(self):
        """The 'Run Peak Hand' benchmark must flag when the peak was set with Jokers
        that are no longer in the build (run 27.10: model sold Blackboard X3 but kept
        planning around the 22,464 peak it had set WITH Blackboard)."""
        from src.engine.serializer import serialize_game_state_to_markdown

        blackboard = JokerModel(id="j_bb", key="j_blackboard", label="Blackboard", effect_desc="X3 Mult if all cards held in hand are Spades or Clubs")
        trousers = JokerModel(id="j_st", key="j_spare_trousers", label="Spare Trousers", effect_desc="+2 Mult if hand contains a Two Pair")
        banner = JokerModel(id="j_bn", key="j_banner", label="Banner", effect_desc="+30 Chips for each remaining discard")

        state_after_sale = GameState(
            stage="BLIND_SELECT",
            ante=7,
            jokers=[trousers, banner],  # Blackboard sold
            blind=BlindModel(name="The Window", chips_needed=40000, is_boss=True),
        )
        md = serialize_game_state_to_markdown(
            state_after_sale,
            max_hand_score=22464,
            best_hand_desc="Two Pair: [A♥, A♦, Q♠, Q♦, 5♥]",
            best_hand_jokers=["Blackboard", "Spare Trousers", "Even Steven"],
        )
        self.assertIn("PEAK PROVENANCE", md)
        self.assertIn("Blackboard", md)
        self.assertIn("Even Steven", md)
        # The peak must still be shown (honest history), not hidden.
        self.assertIn("22,464", md)

        # Same build as peak -> no provenance warning.
        state_same_build = GameState(
            stage="BLIND_SELECT",
            ante=7,
            jokers=[blackboard, trousers],
            blind=BlindModel(name="The Window", chips_needed=40000, is_boss=True),
        )
        md2 = serialize_game_state_to_markdown(
            state_same_build,
            max_hand_score=22464,
            best_hand_desc="Two Pair: [A♥, A♦, Q♠, Q♦, 5♥]",
            best_hand_jokers=["Blackboard", "Spare Trousers"],
        )
        self.assertNotIn("PEAK PROVENANCE", md2)
        self.assertIn("22,464", md2)

    def test_utility_econ_and_hybrid_joker_roles(self):
        from src.engine.serializer import get_joker_role, format_joker_compact

        j_splash = JokerModel(id="j1", key="j_splash", label="Splash", effect_type="mult")
        j_luchador = JokerModel(id="j2", key="j_luchador", label="Luchador", effect_type="mult")
        j_todo = JokerModel(id="j3", key="j_todo_list", label="To Do List", effect_type="mult")
        j_scholar = JokerModel(id="j4", key="j_scholar", label="Scholar", effect_type="mult")
        j_runner = JokerModel(id="j5", key="j_runner", label="Runner", effect_type="mult", chips=0)
        j_photo = JokerModel(id="j6", key="j_photograph", label="Photograph", effect_type="mult")

        self.assertEqual(get_joker_role(j_splash), "utility")
        self.assertEqual(get_joker_role(j_luchador), "utility")
        self.assertEqual(get_joker_role(j_todo), "econ")
        self.assertEqual(get_joker_role(j_scholar), "hybrid")
        self.assertEqual(get_joker_role(j_runner), "chips")
        self.assertEqual(get_joker_role(j_photo), "x_mult")

        self.assertIn("[*Util]", format_joker_compact(0, j_splash))
        self.assertIn("[$Econ]", format_joker_compact(1, j_todo))
        self.assertIn("[+Chips/+FlatMult]", format_joker_compact(2, j_scholar))
        self.assertIn("[xMultiMult]", format_joker_compact(3, j_photo))

        # Mercury build: Splash + Luchador + Scholar must NOT trigger false CHIP-STARVED ALERT
        state_mercury = GameState(ante=3, jokers=[j_splash, j_luchador, j_scholar])
        warnings = check_build_balance(state_mercury)
        self.assertEqual(warnings, [])

    def test_chip_starved_excludes_celestial_packs(self):
        from src.engine.serializer import get_shop_recommendations_for_balance
        from src.bridge.schema import ShopModel

        j1 = JokerModel(id="j1", key="j_droll", label="Droll Joker", effect_type="mult", mult=10)
        j2 = JokerModel(id="j2", key="j_jolly", label="Jolly Joker", effect_type="mult", mult=8)
        shop_items = [
            ShopItemModel(index=1, id="p_celestial", label="Celestial Pack", item_type="Booster", cost=4, desc="Planets"),
            ShopItemModel(index=2, id="c_jupiter", label="Jupiter", item_type="Planet", cost=3, desc="Flush"),
            ShopItemModel(index=3, id="j_blue", label="Blue Joker", item_type="Joker", cost=5, desc="+2 Chips per card"),
        ]
        state = GameState(
            stage="SHOP",
            ante=3,
            dollars=10,
            jokers=[j1, j2],
            shop=ShopModel(items=shop_items, reroll_cost=5),
        )
        recs_str = "\n".join(get_shop_recommendations_for_balance(state))
        self.assertIn("CHIP-STARVED SHOP ALERT", recs_str)
        self.assertIn("Blue Joker", recs_str)
        self.assertNotIn("Celestial Pack", recs_str)
        self.assertNotIn("Jupiter", recs_str)

    def test_bank_aware_voucher_and_concise_booster(self):
        from src.engine.serializer import project_booster_impact
        from src.engine.scoring import BalatroScoringEngine

        v_blank = find_voucher_data("v_blank")
        res_low_bank = format_voucher_impact(v_blank, cost=10, bank=12)
        self.assertIn("SKIP FOR NOW (Bank < $25)", res_low_bank)

        res_high_bank = format_voucher_impact(v_blank, cost=10, bank=30)
        self.assertIn("VERY HIGH (GATEWAY)", res_high_bank)

        pack = ShopItemModel(index=1, id="p_cel", label="Celestial Pack", item_type="Booster", cost=4, desc="Planets")
        state = GameState(stage="SHOP", dollars=11)
        impact = project_booster_impact(pack, state, "Flush", BalatroScoringEngine())
        self.assertIsNotNone(impact)
        self.assertNotIn("MANDATORY", impact)
        self.assertIn("does NOT replace a +Chips or xMult Joker", impact)
        self.assertIn("Costs -$1/rnd interest: bank $11 → $7", impact)

    def test_post_match_dossier_evidence_and_audit(self):
        import tempfile
        from src.engine.recorder import BalatroRunRecorder
        from src.engine.tools import ToolCallAction
        from src.bridge.schema import ShopModel

        with tempfile.TemporaryDirectory() as tmp_dir:
            rec = BalatroRunRecorder(output_dir=Path(tmp_dir), model="test-model", provider="test")
            shop_state = GameState(
                stage="SHOP",
                ante=2,
                round=4,
                dollars=11,
                shop=ShopModel(
                    items=[
                        ShopItemModel(index=1, id="v_blank", label="Blank", item_type="Voucher", cost=10, desc="Does nothing?"),
                        ShopItemModel(index=2, id="j_blue", label="Blue Joker", item_type="Joker", cost=5, desc="+2 Chips per card"),
                    ],
                    reroll_cost=5,
                ),
            )
            after_shop = GameState(stage="SHOP", ante=2, round=4, dollars=1)
            buy_action = ToolCallAction(
                name="buy_shop_item",
                arguments={"shop_index": 1},
                reason="Buying Blank voucher for future Antimatter unlock.",
            )
            rec.record_step(shop_state, buy_action, True, after_shop)

            final_state = GameState(
                stage="SELECTING_HAND",
                ante=3,
                round=6,
                dollars=1,
                current_score=1200,
                target_score=4000,
                hands_left=1,
                discards_left=0,
                blind=BlindModel(name="The Window", is_boss=True, chips_needed=4000),
                hand=[
                    CardModel(id="c1", rank="Ace", suit="Spades"),
                    CardModel(id="c2", rank="King", suit="Diamonds", debuffed=True),
                ],
            )
            play_action = ToolCallAction(
                name="play_cards",
                arguments={"card_ids": ["c1", "c2"]},
                reason="Playing Ace and King as final hand.",
            )
            rec.record_step(final_state, play_action, True, final_state)

            dossier = rec.generate_post_match_dossier()
            self.assertIn("Витрина лавки:", dossier)
            self.assertIn("Blue Joker", dossier)
            self.assertIn("Потеря процентов банка", dossier)
            self.assertIn("Игнорирование калькуляторов", dossier)
            self.assertIn("Подробная покадровая хроника финального раунда", dossier)
            self.assertIn("Карты в руке:", dossier)


if __name__ == "__main__":
    unittest.main()
