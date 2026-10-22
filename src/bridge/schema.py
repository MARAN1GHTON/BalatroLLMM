"""Pydantic v2 schemas representing Balatro game state and RPC interactions."""

from typing import Literal, Any
from pydantic import BaseModel, Field, field_validator, model_validator


class CardModel(BaseModel):
    """Represents a playing card in hand or deck."""

    id: str = Field(description="Unique card identifier (e.g. 'c_H_A_1')")
    suit: Literal["Hearts", "Diamonds", "Clubs", "Spades", "Wild", "None"] = Field(
        default="None", description="Card suit"
    )
    rank: str = Field(description="Card rank: '2'..'10', 'Jack', 'Queen', 'King', 'Ace'")
    nominal: int = Field(default=0, description="Base chip value of the rank")
    chip_bonus: int = Field(default=0, description="Additional chip bonus (e.g. Foil, Bonus card)")
    mult_bonus: int = Field(default=0, description="Additional additive Mult (e.g. Mult card)")
    x_mult: float = Field(default=1.0, description="Multiplicative Mult modifier (e.g. Polychrome, Glass card)")
    enhancement: str | None = Field(default=None, description="Bonus, Mult, Wild, Glass, Steel, Stone, Gold, Lucky")
    edition: str | None = Field(default=None, description="Foil (+50 chips), Holographic (+10 mult), Polychrome (x1.5 mult), Negative")
    seal: str | None = Field(default=None, description="Gold, Red, Blue, Purple seal")
    debuffed: bool = Field(default=False, description="True if card is debuffed by Boss Blind")
    highlighted: bool = Field(default=False, description="True if currently selected in hand")
    face_down: bool = Field(default=False, description="True if facedown due to blind/curse")
    revealed_name: str | None = Field(default=None, description="Actual card identity for human audit report only")

    @field_validator("suit", mode="before")
    @classmethod
    def normalize_suit(cls, v: Any) -> str:
        valid = {"Hearts", "Diamonds", "Clubs", "Spades", "Wild", "None"}
        if isinstance(v, str) and v.capitalize() in valid:
            return v.capitalize()
        return "None"

    @property
    def effective_rank(self) -> str:
        """Returns visible card rank, strictly respecting face-down fog of war."""
        if self.face_down:
            return "Face" if self.rank == "Face" else "?"
        return self.rank

    @property
    def effective_suit(self) -> str:
        """Returns visible card suit, strictly respecting face-down fog of war."""
        if self.face_down:
            return "None"
        return self.suit

    @property
    def effective_nominal(self) -> int:
        """Returns base chip value, strictly respecting face-down fog of war."""
        if self.face_down:
            return 10 if self.is_face_card else 0
        if self.nominal > 0:
            return self.nominal
        er = self.effective_rank
        if er in ("Jack", "Queen", "King", "10", "Face"):
            return 10
        if er == "Ace":
            return 11
        if er.isdigit():
            return int(er)
        return self.nominal

    @property
    def is_face_card(self) -> bool:
        """Returns True if known to be a Jack, Queen, King, or face card on The Mark."""
        return self.effective_rank in ("Jack", "Queen", "King", "Face")


class JokerModel(BaseModel):
    """Represents an active Joker in the player's lineup."""

    id: str = Field(description="Unique Joker card instance id")
    key: str = Field(description="Game code name (e.g. 'j_joker', 'j_blueprint', 'j_cavendish')")
    label: str = Field(description="Human readable name (e.g. 'Joker', 'Blueprint', 'Cavendish')")
    rarity: Literal["Common", "Uncommon", "Rare", "Legendary"] = Field(default="Common")
    order: int = Field(default=0, description="Position index from left to right (0-indexed)")
    sell_cost: int = Field(default=1, description="Cash received if sold")
    effect_type: Literal["chips", "mult", "x_mult", "econ", "utility"] = Field(
        default="mult", description="Primary mechanic classification for optimal ordering"
    )
    effect_desc: str = Field(default="", description="Summary of the Joker's ability and values")
    x_mult: float = Field(default=1.0, description="Current scaled x_mult value if applicable")
    mult: int = Field(default=0, description="Current scaled flat mult value if applicable")
    chips: int = Field(default=0, description="Current scaled chip value if applicable")
    pinned: bool = Field(default=False, description="Cannot be moved if pinned by certain effects")
    eternal: bool = Field(default=False, description="Cannot be sold if eternal (Black Stake+)")
    perishable: bool = Field(default=False, description="Debuffed after 5 rounds (Orange Stake+)")
    perish_rounds_left: int = Field(default=5, description="Rounds remaining until debuffed if perishable")
    rental: bool = Field(default=False, description="Costs $3 every round (Gold Stake+)")
    debuffed: bool = Field(default=False, description="True if Joker is disabled/debuffed by Boss Blind")
    edition: str | None = Field(default=None, description="Joker edition (Foil, Holographic, Polychrome, Negative)")
    target_suit: str | None = Field(default=None, description="Active rotating target suit (e.g. for Ancient Joker, Castle, Idol)")
    target_rank: str | None = Field(default=None, description="Active rotating target rank (e.g. for Mail-In Rebate, Idol)")

    @field_validator("rarity", mode="before")
    @classmethod
    def normalize_rarity(cls, v: Any) -> str:
        rarity_map = {1: "Common", 2: "Uncommon", 3: "Rare", 4: "Legendary"}
        if isinstance(v, int):
            return rarity_map.get(v, "Common")
        if isinstance(v, str):
            if v.isdigit():
                return rarity_map.get(int(v), "Common")
            v_title = v.capitalize()
            if v_title in ("Common", "Uncommon", "Rare", "Legendary"):
                return v_title
        return "Common"

    @field_validator("effect_type", mode="before")
    @classmethod
    def normalize_effect_type(cls, v: Any) -> str:
        valid = {"chips", "mult", "x_mult", "econ", "utility"}
        if isinstance(v, str) and v.lower() in valid:
            return v.lower()
        return "mult"

    @model_validator(mode="after")
    def refine_effect_type(self) -> "JokerModel":
        """Refines effect_type based on known Joker keys, labels, and abilities."""
        k = (self.key or "").lower()
        lbl = (self.label or "").lower()
        desc = (self.effect_desc or "").lower()

        xmult_keys = {
            "j_blackboard", "j_loyalty_card", "j_cavendish", "j_constellation",
            "j_card_sharp", "j_duo", "j_the_duo", "j_trio", "j_the_trio",
            "j_family", "j_the_family", "j_order", "j_the_order", "j_tribe",
            "j_the_tribe", "j_baron", "j_obelisk", "j_photograph", "j_ancient",
            "j_idol", "j_seeing_double", "j_hit_the_road", "j_driver_license",
            "j_astronomer", "j_hologram", "j_vampire", "j_madness", "j_glass",
            "j_steel_joker", "j_ramen", "j_campfire", "j_acrobat", "j_stencil",
            "j_flower_pot", "j_throwback", "j_bloodstone", "j_triboulet", "j_yorick", "j_canio", "j_lucky_cat"
        }
        chips_keys = {
            "j_ice_cream", "j_blue_joker", "j_bull", "j_stuntman", "j_banner",
            "j_castle", "j_scary_face", "j_odd_todd", "j_runner", "j_wee",
            "j_hiker", "j_arrowhead", "j_stone", "j_sly", "j_wily", "j_clever",
            "j_devious", "j_crafty"
        }
        econ_keys = {
            "j_to_the_moon", "j_golden", "j_cloud_9", "j_rocket", "j_reserved_parking",
            "j_faceless", "j_business", "j_delayed_grat", "j_trading", "j_rough_gem"
        }

        if k in xmult_keys or any(term in lbl for term in (
            "blackboard", "loyalty card", "cavendish", "constellation", "card sharp",
            "baron", "obelisk", "photograph", "hologram", "vampire", "madness",
            "acrobat", "stencil", "throwback", "bloodstone", "triboulet", "yorick",
            "canio", "lucky cat", "flower pot", "campfire", "ramen", "the duo",
            "the trio", "the family", "the order", "the tribe"
        )):
            self.effect_type = "x_mult"
        elif k in chips_keys or any(term in lbl for term in (
            "ice cream", "blue joker", "bull", "stuntman", "banner", "castle",
            "scary face", "odd todd", "runner", "wee joker", "hiker", "arrowhead",
            "stone joker", "sly joker", "wily joker", "clever joker", "devious joker", "crafty joker"
        )):
            self.effect_type = "chips"
        elif k in econ_keys or any(term in lbl for term in (
            "to the moon", "golden joker", "cloud 9", "rocket", "delayed gratification", "business card"
        )):
            self.effect_type = "econ"
        elif any(marker in desc for marker in ("xmult", "x mult", "xm", "x4", "x3", "x2", "x1.5", "x0.", "×")):
            self.effect_type = "x_mult"
        elif "+chips" in desc or ("chips" in desc and "mult" not in desc):
            self.effect_type = "chips"

        return self


class ConsumableModel(BaseModel):
    """Represents a Tarot, Planet, or Spectral card in the consumable inventory."""

    id: str = Field(description="Unique card id")
    key: str = Field(description="Internal key (e.g. 'c_fool', 'c_pluto')")
    label: str = Field(description="Name (e.g. 'The Fool', 'Pluto', 'Immolate')")
    card_type: str = Field(default="Tarot", description="Card type: Tarot, Planet, Spectral")
    cost: int = Field(default=3, description="Sell value or cost")
    desc: str = Field(default="", description="Effect description")


class BlindModel(BaseModel):
    """Represents the current Blind encounter."""

    name: str = Field(default="Small Blind", description="Small Blind, Big Blind, or Boss Blind name")
    chips_needed: int = Field(default=300, description="Target score required to defeat the blind")
    reward: int = Field(default=3, description="Base payout dollars on victory")
    is_boss: bool = Field(default=False, description="True if this is an Ante Boss Blind")
    debuff_rule: str | None = Field(default=None, description="Boss ability/restriction description")
    upcoming_boss_name: str | None = Field(default=None, description="Name of upcoming Boss Blind in current Ante")
    upcoming_boss_debuff: str | None = Field(default=None, description="Debuff restriction of upcoming Boss Blind")
    skip_tag_name: str | None = Field(default=None, description="Name of Tag awarded for skipping this blind")
    skip_tag_desc: str | None = Field(default=None, description="Description of skip Tag effect")
    defeated: bool = Field(default=False, description="True if score >= chips_needed")

    @field_validator("chips_needed", "reward", mode="before")
    @classmethod
    def coerce_blind_ints(cls, v: Any) -> int:
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return 0


class AnteBlindScheduleModel(BaseModel):
    """Represents a scheduled Blind in the current Ante (Small, Big, Boss)."""

    type: Literal["Small", "Big", "Boss"] = Field(description="Blind tier: Small, Big, Boss")
    name: str = Field(default="Blind", description="Blind display name")
    chips: int = Field(default=300, description="Target score required")
    reward: int = Field(default=3, description="Payout in dollars on victory")
    status: str = Field(default="Upcoming", description="Status: Select, Upcoming, Defeated, Skipped")
    tag_name: str | None = Field(default=None, description="Skip tag reward name (Small/Big)")
    tag_desc: str | None = Field(default=None, description="Skip tag description")
    debuff_rule: str | None = Field(default=None, description="Boss ability/restriction (Boss only)")

    @field_validator("chips", "reward", mode="before")
    @classmethod
    def coerce_schedule_ints(cls, v: Any) -> int:
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return 0


class ShopItemModel(BaseModel):
    """An item available for purchase in the shop or selection in a booster pack."""

    index: int = Field(description="Slot index (0-indexed)")
    id: str = Field(description="Unique item id")
    key: str | None = Field(default=None, description="Center key (e.g. v_blank, j_joker)")
    label: str = Field(description="Item display name")
    item_type: str = Field(default="Joker", description="Item category: Joker, Tarot, Planet, Spectral, Voucher, BoosterPack, PlayingCard")
    cost: int = Field(default=4, description="Purchase cost in dollars (0 in packs)")
    desc: str = Field(default="", description="Effect description or contents")
    edition: str | None = Field(default=None, description="Edition: Foil (+50 chips), Holographic (+10 mult), Polychrome (x1.5 mult), Negative (+1 joker slot)")


class ShopModel(BaseModel):
    """Represents current Shop contents between rounds."""

    items: list[ShopItemModel] = Field(default_factory=list)
    reroll_cost: int = Field(default=5, description="Cost to reroll available items")


class PokerHandModel(BaseModel):
    """Represents a poker hand type, its level, base scoring stats, and play count."""

    name: str = Field(description="Hand name (e.g. Flush, Full House, High Card)")
    level: int = Field(default=1, description="Current hand level upgraded via Planet cards")
    chips: int = Field(default=0, description="Base chips for this hand")
    mult: int = Field(default=0, description="Base mult for this hand")
    played: int = Field(default=0, description="Number of times played this run")
    order: int = Field(default=99, description="Display order")


class SpecialDeckCard(BaseModel):
    """Specific card in draw pile with enhancements, editions, or seals."""

    rank: str = Field(default="Unknown", description="Card rank")
    suit: str = Field(default="Unknown", description="Card suit")
    enhancement: str | None = Field(default=None, description="Enhancement type (Glass, Steel, etc.)")
    edition: str | None = Field(default=None, description="Edition type (Foil, Holo, Polychrome)")
    seal: str | None = Field(default=None, description="Seal type (Red, Blue, Gold, Purple)")
    desc: str | None = Field(default=None, description="Human-readable description of special card")


class DeckSummaryModel(BaseModel):
    """Breakdown of cards remaining in the player's draw pile."""

    total_remaining: int = Field(default=52, description="Total cards in draw pile")
    suits: dict[str, int] = Field(default_factory=dict, description="Suit distribution in draw pile")
    ranks: dict[str, int] = Field(default_factory=dict, description="Rank distribution in draw pile")
    face_cards: int = Field(default=0, description="Total face cards (J, Q, K) remaining in draw pile")
    aces: int = Field(default=0, description="Total Aces remaining in draw pile")
    enhancements: dict[str, int] = Field(default_factory=dict, description="Enhanced cards in draw pile")
    special_cards: list[SpecialDeckCard] = Field(default_factory=list, description="Specific modified cards in draw pile")
    cards: list[CardModel] = Field(default_factory=list, description="All cards currently in draw pile")


class GameState(BaseModel):
    """Comprehensive typed snapshot of the Balatro game state."""

    stage: Literal[
        "BLIND_SELECT",
        "SELECTING_HAND",
        "PLAYING_HAND",
        "PLAYING_TAROT",
        "ROUND_EVAL",
        "SHOP",
        "OPENING_BOOSTER",
        "GAME_OVER",
        "VICTORY",
        "UNKNOWN",
    ] = Field(default="SELECTING_HAND", description="Current FSM game phase")
    ante: int = Field(default=1, description="Current Ante (1..8+)")
    round: int = Field(default=1, description="Current overall round number")
    deck_name: str = Field(default="Red Deck", description="Active deck name (e.g. Plasma Deck, Green Deck, Abandoned Deck)")
    stake: str = Field(default="White Stake", description="Difficulty stake (White, Red, Green, Black, Blue, Purple, Orange, Gold)")
    challenge_name: str | None = Field(default=None, description="Active Challenge run name if playing a challenge")
    dollars: int = Field(default=4, description="Current bank balance in dollars")
    hands_left: int = Field(default=4, description="Hands remaining for this blind")
    discards_left: int = Field(default=3, description="Discards remaining for this blind")
    hand_size: int = Field(default=8, description="Maximum cards in hand")
    current_score: int = Field(default=0, description="Current accumulated chips this round")
    target_score: int = Field(default=300, description="Score required to beat the active blind")

    @field_validator(
        "target_score",
        "current_score",
        "dollars",
        "hands_left",
        "discards_left",
        "ante",
        "round",
        mode="before",
    )
    @classmethod
    def coerce_gamestate_ints(cls, v: Any) -> int:
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return 0
    blind: BlindModel = Field(default_factory=BlindModel)
    ante_blinds: list[AnteBlindScheduleModel] = Field(
        default_factory=list, description="Full schedule of blinds for current Ante (Small, Big, Boss)"
    )
    hand: list[CardModel] = Field(default_factory=list, description="Cards currently in player's hand")
    jokers: list[JokerModel] = Field(default_factory=list, description="Currently owned Jokers")
    consumables: list[ConsumableModel] = Field(default_factory=list, description="Owned consumables")
    pack_cards: list[ShopItemModel] = Field(default_factory=list, description="Available cards in opened booster pack")
    pack_choices: int = Field(default=1, description="Number of picks remaining in opened booster pack")
    max_jokers: int = Field(default=5, description="Maximum Joker slots")
    max_consumables: int = Field(default=2, description="Maximum consumable slots")
    shop: ShopModel | None = Field(default=None, description="Available shop items if in shop phase")
    deck_remaining: int = Field(default=52, description="Cards remaining in the draw pile")
    poker_hands: list[PokerHandModel] = Field(default_factory=list, description="All discovered poker hand levels and stats")
    deck_summary: DeckSummaryModel | None = Field(default=None, description="Detailed breakdown of remaining draw pile")
    interest_cap: int = Field(default=25, description="Current interest threshold cap ($25, $50 with Seed Money, $100 with Money Tree)")
    vouchers: list[str] = Field(default_factory=list, description="All vouchers owned/redeemed in this run")
    is_animating: bool = Field(default=False, description="True if game is dealing or scoring animations")
    bridge_version: str | None = Field(default=None, description="Balatro RPC Bridge version string")
    can_reroll_boss: bool = Field(default=False, description="True if Director's Cut / Retcon voucher owned and can reroll Boss Blind for $10")
    game_over: bool = Field(default=False, description="True if player lost")
    victory: bool = Field(default=False, description="True if Ante 8 Boss defeated")


# Type Aliases for convenient importing
Card = CardModel
Joker = JokerModel
Consumable = ConsumableModel
Blind = BlindModel
AnteBlindSchedule = AnteBlindScheduleModel
Shop = ShopModel
ShopItem = ShopItemModel
PokerHand = PokerHandModel
DeckSummary = DeckSummaryModel
