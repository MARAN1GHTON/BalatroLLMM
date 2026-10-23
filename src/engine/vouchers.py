"""Balatro Voucher Knowledge Base & Tier 2 Evolution Engine.

In Balatro, every voucher belongs to a 2-Tier evolution tree (16 pairs, 32 vouchers).
Tier 2 upgraded vouchers NEVER appear in the shop pool until their corresponding
Tier 1 base voucher has been purchased in that run.
"""

from typing import Any

VOUCHER_PAIRS: dict[str, dict[str, Any]] = {
    # 1. Overstock / Overstock Plus
    "v_overstock_norm": {
        "tier": 1,
        "name": "Overstock",
        "key": "v_overstock_norm",
        "aliases": ["overstock", "перегрузка"],
        "effect": "+1 Card slot in shop (increases from 2 to 3 slots)",
        "upgrade_key": "v_overstock_plus",
        "upgrade_name": "Overstock Plus",
        "upgrade_effect": "+1 more card slot in shop (4 slots total!)",
        "priority": "HIGH",
        "advice": "Expands shop selection by +50%. Excellent if bank >= $25 to discover key Jokers & Planets.",
    },
    "v_overstock_plus": {
        "tier": 2,
        "name": "Overstock Plus",
        "key": "v_overstock_plus",
        "aliases": ["overstock plus", "overstock_plus", "сверхзапасы"],
        "effect": "+1 more card slot in shop (4 slots total!)",
        "base_key": "v_overstock_norm",
        "base_name": "Overstock",
        "priority": "HIGH",
        "advice": "Doubles base shop capacity (4 slots vs standard 2). Maximum chance to find build upgrades.",
    },

    # 2. Clearance Sale / Liquidation
    "v_clearance_sale": {
        "tier": 1,
        "name": "Clearance Sale",
        "key": "v_clearance_sale",
        "aliases": ["clearance sale", "clearance_sale", "распродажа"],
        "effect": "All items and packs in shop are 25% cheaper",
        "upgrade_key": "v_liquidation",
        "upgrade_name": "Liquidation",
        "upgrade_effect": "All items and packs in shop are 50% cheaper!",
        "priority": "HIGH",
        "advice": "Saves $1-$2 on every shop purchase; quickly pays for itself across Antes.",
    },
    "v_liquidation": {
        "tier": 2,
        "name": "Liquidation",
        "key": "v_liquidation",
        "aliases": ["liquidation", "ликвидация"],
        "effect": "All items and packs in shop are 50% cheaper!",
        "base_key": "v_clearance_sale",
        "base_name": "Clearance Sale",
        "priority": "VERY HIGH",
        "advice": "Half-price shop. Massive economy boost — Booster Packs cost $2-$4, Jokers $2-$5.",
    },

    # 3. Hone / Glow Up
    "v_hone": {
        "tier": 1,
        "name": "Hone",
        "key": "v_hone",
        "aliases": ["hone", "заточка"],
        "effect": "Foil (+50c), Holographic (+10m), and Polychrome (x1.5m) cards appear 2x more often",
        "upgrade_key": "v_glow_up",
        "upgrade_name": "Glow Up",
        "upgrade_effect": "Foil, Holographic, and Polychrome cards appear 4x more often!",
        "priority": "MEDIUM",
        "advice": "Increases special edition drop rates in shop and booster packs.",
    },
    "v_glow_up": {
        "tier": 2,
        "name": "Glow Up",
        "key": "v_glow_up",
        "aliases": ["glow up", "glow_up", "сияние"],
        "effect": "Foil, Holographic, and Polychrome cards appear 4x more often!",
        "base_key": "v_hone",
        "base_name": "Hone",
        "priority": "MEDIUM-HIGH",
        "advice": "Heavy edition multiplier. High chance for Polychrome x1.5 Jokers and Foil/Holo cards.",
    },

    # 4. Reroll Surplus / Reroll Glut
    "v_reroll_surplus": {
        "tier": 1,
        "name": "Reroll Surplus",
        "key": "v_reroll_surplus",
        "aliases": ["reroll surplus", "reroll_surplus", "скидка на реролл", "скидка на сброс"],
        "effect": "Rerolls cost $2 less (base cost $3 instead of $5)",
        "upgrade_key": "v_reroll_glut",
        "upgrade_name": "Reroll Glut",
        "upgrade_effect": "Rerolls cost additional $2 less (base cost $1!)",
        "priority": "MEDIUM-HIGH",
        "advice": "Substantially lowers the cost of digging for game-winning Jokers in mid-to-late game.",
    },
    "v_reroll_glut": {
        "tier": 2,
        "name": "Reroll Glut",
        "key": "v_reroll_glut",
        "aliases": ["reroll glut", "reroll_glut", "избыток рероллов", "избыток сбросов"],
        "effect": "Rerolls cost $1 base instead of $5!",
        "base_key": "v_reroll_surplus",
        "base_name": "Reroll Surplus",
        "priority": "HIGH",
        "advice": "Extremely cheap rerolls allow digging aggressively through shop pools with surplus cash.",
    },

    # 5. Crystal Ball / Omen Globe
    "v_crystal_ball": {
        "tier": 1,
        "name": "Crystal Ball",
        "key": "v_crystal_ball",
        "aliases": ["crystal ball", "crystal_ball", "хрустальный шар"],
        "effect": "+1 Consumable slot (hold up to 3 Tarots/Planets/Spectrals)",
        "upgrade_key": "v_omen_globe",
        "upgrade_name": "Omen Globe",
        "upgrade_effect": "Spectral cards can now appear in any Arcana Pack!",
        "priority": "HIGH",
        "advice": "Lets you hold utility cards (Death, Hanged Man, Hermit, Fool) without clogging inventory.",
    },
    "v_omen_globe": {
        "tier": 2,
        "name": "Omen Globe",
        "key": "v_omen_globe",
        "aliases": ["omen globe", "omen_globe", "сфера знамений"],
        "effect": "Spectral cards can now appear in any Arcana Pack!",
        "base_key": "v_crystal_ball",
        "base_name": "Crystal Ball",
        "priority": "VERY HIGH",
        "advice": "Turns cheap Arcana packs into sources of run-defining Spectral cards (Ankh, Hex, Cryptid, Imm拾te).",
    },

    # 6. Telescope / Observatory
    "v_telescope": {
        "tier": 1,
        "name": "Telescope",
        "key": "v_telescope",
        "aliases": ["telescope", "телескоп"],
        "effect": "Celestial Packs always contain the Planet card for your most played poker hand",
        "upgrade_key": "v_observatory",
        "upgrade_name": "Observatory",
        "upgrade_effect": "Planet cards held in consumable slots give x1.5 Mult each for their specified poker hand!",
        "priority": "VERY HIGH",
        "advice": "Guarantees reliable level-up scaling for your primary hand from every Celestial Pack.",
    },
    "v_observatory": {
        "tier": 2,
        "name": "Observatory",
        "key": "v_observatory",
        "aliases": ["observatory", "обсерватория"],
        "effect": "Planet cards held in consumable slots grant x1.5 Mult each when their poker hand is played!",
        "base_key": "v_telescope",
        "base_name": "Telescope",
        "priority": "RUN-WINNING",
        "advice": "ONE OF THE STRONGEST xMULT ENGINES IN BALATRO. Holding 2-3 target planets gives x1.5 × x1.5 × x1.5 = x3.375 Mult on every hand played!",
    },

    # 7. Grabber / Nacho Tong
    "v_grabber": {
        "tier": 1,
        "name": "Grabber",
        "key": "v_grabber",
        "aliases": ["grabber", "хват"],
        "effect": "+1 Hand per round (permanently 5 hands instead of 4)",
        "upgrade_key": "v_nacho_tong",
        "upgrade_name": "Nacho Tong",
        "upgrade_effect": "+1 additional Hand per round (6 hands total!)",
        "priority": "VERY HIGH",
        "advice": "Direct survival insurance against big Boss Blinds, plus +$1 cash for every unused hand upon winning!",
    },
    "v_nacho_tong": {
        "tier": 2,
        "name": "Nacho Tong",
        "key": "v_nacho_tong",
        "aliases": ["nacho tong", "nacho_tong", "щипцы для начо"],
        "effect": "+1 additional Hand per round (6 hands total!)",
        "base_key": "v_grabber",
        "base_name": "Grabber",
        "priority": "VERY HIGH",
        "advice": "6 hands per round trivializes hand-quota Bosses and provides extra cash payout.",
    },

    # 8. Wasteful / Recyclomancy
    "v_wasteful": {
        "tier": 1,
        "name": "Wasteful",
        "key": "v_wasteful",
        "aliases": ["wasteful", "расточитель"],
        "effect": "+1 Discard per round (permanently 4 discards instead of 3)",
        "upgrade_key": "v_recyclomancy",
        "upgrade_name": "Recyclomancy",
        "upgrade_effect": "+1 additional Discard per round (5 discards total!)",
        "priority": "HIGH",
        "advice": "Crucial for hand consistency, digging for flushes/straights/pairs, and triggers like Castle/Ramen/Yorick.",
    },
    "v_recyclomancy": {
        "tier": 2,
        "name": "Recyclomancy",
        "key": "v_recyclomancy",
        "aliases": ["recyclomancy", "переработка"],
        "effect": "+1 additional Discard per round (5 discards total!)",
        "base_key": "v_wasteful",
        "base_name": "Wasteful",
        "priority": "HIGH",
        "advice": "Enables digging through almost half the deck each round.",
    },

    # 9. Tarot Merchant / Tarot Tycoon
    "v_tarot_merchant": {
        "tier": 1,
        "name": "Tarot Merchant",
        "key": "v_tarot_merchant",
        "aliases": ["tarot merchant", "tarot_merchant", "торговец таро"],
        "effect": "Tarot cards appear 2x more frequently in the shop",
        "upgrade_key": "v_tarot_tycoon",
        "upgrade_name": "Tarot Tycoon",
        "upgrade_effect": "Tarot cards appear 4x more frequently in the shop!",
        "priority": "MEDIUM",
        "advice": "Enhances deck manipulation and economy Tarots (Hermit, Temperance).",
    },
    "v_tarot_tycoon": {
        "tier": 2,
        "name": "Tarot Tycoon",
        "key": "v_tarot_tycoon",
        "aliases": ["tarot tycoon", "tarot_tycoon", "магнат таро"],
        "effect": "Tarot cards appear 4x more frequently in the shop!",
        "base_key": "v_tarot_merchant",
        "base_name": "Tarot Merchant",
        "priority": "MEDIUM-HIGH",
        "advice": "Shop flooded with Tarots for rapid deck thinning and card enhancement.",
    },

    # 10. Planet Merchant / Planet Tycoon
    "v_planet_merchant": {
        "tier": 1,
        "name": "Planet Merchant",
        "key": "v_planet_merchant",
        "aliases": ["planet merchant", "planet_merchant", "торговец планетами"],
        "effect": "Planet cards appear 2x more frequently in the shop",
        "upgrade_key": "v_planet_tycoon",
        "upgrade_name": "Planet Tycoon",
        "upgrade_effect": "Planet cards appear 4x more frequently in the shop!",
        "priority": "MEDIUM",
        "advice": "Helps level up poker hands directly from standard shop slots.",
    },
    "v_planet_tycoon": {
        "tier": 2,
        "name": "Planet Tycoon",
        "key": "v_planet_tycoon",
        "aliases": ["planet tycoon", "planet_tycoon", "магнат планет"],
        "effect": "Planet cards appear 4x more frequently in the shop!",
        "base_key": "v_planet_merchant",
        "base_name": "Planet Merchant",
        "priority": "MEDIUM",
        "advice": "Constant stream of direct hand upgrades in shop.",
    },

    # 11. Seed Money / Money Tree
    "v_seed_money": {
        "tier": 1,
        "name": "Seed Money",
        "key": "v_seed_money",
        "aliases": ["seed money", "seed_money", "стартовый капитал"],
        "effect": "Max interest cap raised from $25 to $50 (earns up to $10 interest per round!)",
        "upgrade_key": "v_money_tree",
        "upgrade_name": "Money Tree",
        "upgrade_effect": "Max interest cap raised to $100 (earns up to $20 interest per round!)",
        "priority": "VERY HIGH",
        "advice": "Exponential economy engine. If your bank reaches $50+, awards +$10 free cash EVERY round.",
    },
    "v_money_tree": {
        "tier": 2,
        "name": "Money Tree",
        "key": "v_money_tree",
        "aliases": ["money tree", "money_tree", "денежное дерево"],
        "effect": "Max interest cap raised to $100 (earns up to $20 interest per round!)",
        "base_key": "v_seed_money",
        "base_name": "Seed Money",
        "priority": "VERY HIGH",
        "advice": "Peak economy voucher. +$20 passive income per round finances infinite rerolls and packs.",
    },

    # 12. Blank / Antimatter (THE CRITICAL SECRET)
    "v_blank": {
        "tier": 1,
        "name": "Blank",
        "key": "v_blank",
        "aliases": ["blank", "пустышка"],
        "effect": "In-game text says 'Does nothing?'. BUT THIS IS A SECRET PREREQUISITE!",
        "upgrade_key": "v_antimatter",
        "upgrade_name": "Antimatter",
        "upgrade_effect": "+1 JOKER SLOT PERMANENTLY (6 total)! ONE OF THE STRONGEST UPGRADES IN BALATRO!",
        "priority": "VERY HIGH (GATEWAY)",
        "advice": "⚠️ CRITICAL BALATRO SECRET: Blank has no immediate effect, but buying it is the MANDATORY REQUIREMENT to unlock Antimatter (+1 Joker slot) in subsequent shops! ALWAYS BUY IF BANK >= $25!",
    },
    "v_antimatter": {
        "tier": 2,
        "name": "Antimatter",
        "key": "v_antimatter",
        "aliases": ["antimatter", "антиматерия"],
        "effect": "+1 JOKER SLOT PERMANENTLY (Allows holding 6 Jokers instead of 5)!",
        "base_key": "v_blank",
        "base_name": "Blank",
        "priority": "RUN-WINNING",
        "advice": "TOP-TIER VOUCHER IN THE ENTIRE GAME. An extra Joker slot breaks the scoring ceiling. Buy immediately on sight!",
    },

    # 13. Magic Trick / Illusion
    "v_magic_trick": {
        "tier": 1,
        "name": "Magic Trick",
        "key": "v_magic_trick",
        "aliases": ["magic trick", "magic_trick", "фокус"],
        "effect": "Playing cards can now be purchased directly from the shop",
        "upgrade_key": "v_illusion",
        "upgrade_name": "Illusion",
        "upgrade_effect": "Playing cards in shop may have Enhancements, Editions, and Seals!",
        "priority": "LOW-MEDIUM",
        "advice": "Adds playing cards to shop slots. Can crowd out Joker slots if you don't need deck modification.",
    },
    "v_illusion": {
        "tier": 2,
        "name": "Illusion",
        "key": "v_illusion",
        "aliases": ["illusion", "иллюзия"],
        "effect": "Playing cards in shop may have Enhancements, Editions, and Seals!",
        "base_key": "v_magic_trick",
        "base_name": "Magic Trick",
        "priority": "MEDIUM",
        "advice": "Allows picking up pre-buffed Purple/Gold seals or Poly/Steel cards directly.",
    },

    # 14. Hieroglyph / Petroglyph
    "v_hieroglyph": {
        "tier": 1,
        "name": "Hieroglyph",
        "key": "v_hieroglyph",
        "aliases": ["hieroglyph", "иероглиф"],
        "effect": "-1 Ante (gives you 3 extra rounds to build economy/deck!), -1 Hand per round",
        "upgrade_key": "v_petroglyph",
        "upgrade_name": "Petroglyph",
        "upgrade_effect": "-1 Ante (gives another 3 rounds!), -1 Discard per round",
        "priority": "SITUATIONAL",
        "advice": "Buys extra time and shops by rolling back Ante by 1. Take if ahead on scoring but need shops.",
    },
    "v_petroglyph": {
        "tier": 2,
        "name": "Petroglyph",
        "key": "v_petroglyph",
        "aliases": ["petroglyph", "петроглиф"],
        "effect": "-1 Ante (rolls back Ante by 1), -1 Discard per round",
        "base_key": "v_hieroglyph",
        "base_name": "Hieroglyph",
        "priority": "SITUATIONAL",
        "advice": "Adds 3 more rounds. Only take if scoring is robust enough to sacrifice 1 discard.",
    },

    # 15. Director's Cut / Retcon
    "v_directors_cut": {
        "tier": 1,
        "name": "Director's Cut",
        "key": "v_directors_cut",
        "aliases": ["directors cut", "director's cut", "directors_cut", "режиссерская версия"],
        "effect": "Allows rerolling the Boss Blind 1 time per Ante for $10",
        "upgrade_key": "v_retcon",
        "upgrade_name": "Retcon",
        "upgrade_effect": "Allows rerolling the Boss Blind unlimited times for $10 each!",
        "priority": "HIGH (BOSS INSURANCE)",
        "advice": "Protects against run-killer Boss Blinds (The Plant if you rely on faces, The Needle, The Eye).",
    },
    "v_retcon": {
        "tier": 2,
        "name": "Retcon",
        "key": "v_retcon",
        "aliases": ["retcon", "реткон"],
        "effect": "Reroll Boss Blind unlimited times ($10 each)!",
        "base_key": "v_directors_cut",
        "base_name": "Director's Cut",
        "priority": "VERY HIGH",
        "advice": "Guarantees you never die to a counter Boss Blind if you have cash.",
    },

    # 16. Paint Brush / Palette
    "v_paint_brush": {
        "tier": 1,
        "name": "Paint Brush",
        "key": "v_paint_brush",
        "aliases": ["paint brush", "paint_brush", "кисть"],
        "effect": "+1 Hand size (hold 9 cards in hand instead of 8)",
        "upgrade_key": "v_palette",
        "upgrade_name": "Palette",
        "upgrade_effect": "+1 additional Hand size (hold 10 cards in hand!)",
        "priority": "VERY HIGH",
        "advice": "Massively improves poker hand consistency (Flushes, Straights, Full Houses) and held-in-hand Steel/Baron triggers.",
    },
    "v_palette": {
        "tier": 2,
        "name": "Palette",
        "key": "v_palette",
        "aliases": ["palette", "палитра"],
        "effect": "+1 additional Hand size (hold 10 cards in hand!)",
        "base_key": "v_paint_brush",
        "base_name": "Paint Brush",
        "priority": "VERY HIGH",
        "advice": "10-card hand makes high-tier poker hands virtually guaranteed every draw.",
    },
}


def find_voucher_data(raw_identifier: str | None) -> dict[str, Any] | None:
    """Finds voucher metadata by internal key, label, or alias (case-insensitive)."""
    if not raw_identifier:
        return None

    cleaned = str(raw_identifier).strip().lower()

    # 1. Direct key match (e.g. v_blank, v_overstock_norm)
    if cleaned in VOUCHER_PAIRS:
        return VOUCHER_PAIRS[cleaned]

    # Normalize key (e.g. overstock_norm -> v_overstock_norm)
    prefixed = f"v_{cleaned}"
    if prefixed in VOUCHER_PAIRS:
        return VOUCHER_PAIRS[prefixed]

    # Strip prefix (e.g. v_blank -> blank)
    unprefixed = cleaned[2:] if cleaned.startswith("v_") else cleaned

    # 2. Match aliases and names
    for v_data in VOUCHER_PAIRS.values():
        if unprefixed == v_data["name"].lower():
            return v_data
        for alias in v_data.get("aliases", []):
            if alias in cleaned or cleaned in alias:
                return v_data

    return None


def format_voucher_impact(v_data: dict[str, Any], cost: int = 10, bank: int = 0) -> str:
    """Formats a concise, bank-aware markdown analysis of a shop voucher."""
    name = v_data["name"]
    tier = v_data["tier"]
    effect = v_data["effect"]
    priority = v_data["priority"]
    advice = v_data["advice"]
    v_key = v_data.get("key", "")

    # Bank-aware priority overrides for delayed-payoff vouchers
    if v_key == "v_blank" and bank < 25:
        priority = "SKIP FOR NOW (Bank < $25)"
        advice = (
            f"Your bank is ${bank} (< $25 interest cap). Blank gives ZERO immediate combat or economy value! "
            f"Only buy Blank when you have surplus capital ($25+)."
        )
    elif v_key == "v_seed_money" and bank < 25:
        priority = "SKIP FOR NOW (Bank < $25)"
        advice = (
            f"Your bank is ${bank} (< $25). Seed Money only increases interest when you hold $30-$50! "
            f"Buying it now drops your bank to ${max(0, bank - cost)} and reduces your current interest."
        )

    cur_interest = min(5, max(0, bank) // 5)
    after_bank = max(0, bank - cost)
    after_interest = min(5, after_bank // 5)
    interest_loss = cur_interest - after_interest
    econ_suffix = (
        f" (Costs -${interest_loss}/rnd interest: bank ${bank} → ${after_bank})"
        if bank >= cost and interest_loss > 0
        else ""
    )

    lines = []
    if tier == 1:
        up_name = v_data.get("upgrade_name", "Upgraded Version")
        up_effect = v_data.get("upgrade_effect", "")
        lines.append(f"  > 🎟️ **VOUCHER ANALYSIS: {name} (Tier 1 Base)** [${cost}]{econ_suffix}:")
        lines.append(f"  > • **Current Effect**: {effect} (Unlocks Tier 2: **{up_name}** — {up_effect})")
        lines.append(f"  > • **Verdict**: {priority} — {advice}")
    else:
        base_name = v_data.get("base_name", "Tier 1")
        lines.append(f"  > 👑 **TIER 2 UPGRADED VOUCHER: {name}** [${cost}]{econ_suffix}:")
        lines.append(f"  > • **Elite Effect**: {effect} (Unlocked by {base_name})")
        lines.append(f"  > • **Verdict**: {priority} — {advice}")

    if v_key in ("v_directors_cut", "v_retcon"):
        reroll_cost = 10
        total_needed = cost + reroll_cost
        if bank < total_needed:
            lines.append(
                f"  > • ⚠️ **TOTAL COST REALITY CHECK**: Buying this voucher (${cost}) only gives the *permission* to reroll; "
                f"each Boss reroll costs an ADDITIONAL ${reroll_cost} (${total_needed} total)! "
                f"With ${bank} in bank, you will have only ${bank - cost} left and CANNOT reroll the boss immediately!"
            )
        else:
            lines.append(
                f"  > • 💡 **BUDGET VERIFIED**: You have ${bank} (>= ${total_needed} needed for voucher + ${reroll_cost} boss reroll). "
                f"You have sufficient funds to buy this voucher and immediately reroll the Boss Blind."
            )

    return "\n".join(lines)


def format_owned_voucher_line(v_key_or_name: str) -> str:
    """Formats a concise 1-line display for an owned voucher and what Tier 2 it unlocked."""
    v_data = find_voucher_data(v_key_or_name)
    if not v_data:
        clean_name = str(v_key_or_name).replace("v_", "").replace("_", " ").title()
        return f"- **{clean_name}**: Active run-wide perk"

    name = v_data["name"]
    tier = v_data["tier"]
    effect = v_data["effect"]

    if tier == 1:
        up_name = v_data.get("upgrade_name")
        up_effect = v_data.get("upgrade_effect")
        return f"- **{name}**: {effect} | 🔓 _Unlocked in shop pool_: **{up_name}** ({up_effect})"
    else:
        return f"- 👑 **{name}** (Tier 2): {effect}"
