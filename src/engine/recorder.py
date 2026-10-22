"""Autonomous Balatro Run Recorder and Tactical Analyzer.

Captures all AI decisions, Joker build progressions, economy actions,
and highest scoring combos, generating rich Markdown & JSON victory reports.
"""

import json
import logging
from dataclasses import dataclass, field, asdict, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel

from ..bridge.schema import GameState, Card, Joker
from .scoring import BalatroScoringEngine
from .tools import ToolCallAction

logger = logging.getLogger("balatro_agent.recorder")
console = Console()


@dataclass
class PlayedHandLog:
    """Record of an individual played poker hand and its scoring impact."""
    ante: int
    round: int
    blind_name: str
    cards_played: list[str]
    score_before: int
    score_after: int
    score_gained: int
    active_jokers: list[str]
    reasoning: str
    poker_hand: str = "Hand"


@dataclass
class ShopPurchaseLog:
    """Record of a shop purchase or reroll."""
    ante: int
    round: int
    item_label: str
    cost: int
    bank_after: int
    reasoning: str


@dataclass
class CashFlowLog:
    """Audit record of any capital movement (earnings, payouts, purchases, rerolls, sales)."""
    step: int
    timestamp: str
    ante: int
    round: int
    stage: str
    event_type: str
    dollars_before: int
    dollars_after: int
    delta: int
    details: str


@dataclass
class StepLog:
    """Detailed log of a single perception-reasoning-action decision."""
    step: int
    timestamp: str
    ante: int
    round: int
    stage: str
    blind_name: str
    score: int
    target_score: int
    hands_left: int
    discards_left: int
    bank: int
    active_jokers: list[str]
    hand_cards: list[str]
    action_name: str
    action_args: dict[str, Any]
    reasoning: str
    success: bool
    score_delta: int = 0
    dollars_delta: int = 0
    error_message: str | None = None
    available_shop_items: list[str] = field(default_factory=list)
    available_pack_cards: list[str] = field(default_factory=list)
    consumables: list[str] = field(default_factory=list)
    offered_skip_tag: str | None = None
    plan_notes: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    calculation_turns: int = 0


class BalatroRunRecorder:
    """Tracks game progression and exports detailed analysis reports."""

    def __init__(
        self,
        output_dir: str | Path = "runs",
        model: str = "Unknown",
        provider: str = "Unknown",
        temperature: float = 0.2,
        reasoning_effort: str | None = None,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.start_time = datetime.now()
        
        self.model = model
        self.provider = provider
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        
        self.deck_name: str = "Unknown"
        self.stake: str = "White Stake"
        self.steps: list[StepLog] = []
        self.played_hands: list[PlayedHandLog] = []
        self.purchases: list[ShopPurchaseLog] = []
        self.cash_flow: list[CashFlowLog] = []
        self.total_earned: int = 0
        self.total_spent: int = 0
        self.joker_build_history: list[dict[str, Any]] = []
        self.scratchpad_history: list[dict[str, Any]] = []
        
        self.peak_bank: int = 0
        self.total_hands_played: int = 0
        self.total_discards_used: int = 0
        self.total_rerolls: int = 0
        self.max_single_hand_score: int = 0
        self.best_combo: PlayedHandLog | None = None
        
        # Token metrics
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0
        self.total_tokens: int = 0
        self.total_calculation_turns: int = 0
        self.interview: str | None = None
        
        self.last_state: GameState | None = None
        self.last_action: ToolCallAction | None = None
        self.victory_achieved: bool = False
        self.game_over: bool = False
        self.saved: bool = False
        self.step_counter: int = 0

        # Reset session lorebook telemetry
        try:
            from .lorebook import LorebookEngine
            LorebookEngine.get_instance().reset_telemetry()
        except Exception:
            pass

    @property
    def active_run_file(self) -> Path:
        return self.output_dir / ".active_run.json"

    def get_best_hand_desc(self) -> str | None:
        """Returns concise summary of highest scoring hand combo (e.g. 'Full House: [K♥, K♠, K♦, 10♣, 10♦]')."""
        if not self.best_combo or self.max_single_hand_score <= 0:
            return None
        combo = self.best_combo
        h_name = getattr(combo, "poker_hand", None) or "Hand"
        cards_summary = ", ".join(combo.cards_played[:5]) if combo.cards_played else ""
        return f"{h_name}: [{cards_summary}]"

    def save_active_checkpoint(self) -> None:
        """Saves active unfinalized run state to disk so it survives crashes/restarts."""
        if not self.steps:
            return
        try:
            data = {
                "run_id": self.run_id,
                "start_time": self.start_time.isoformat(),
                "model": self.model,
                "provider": self.provider,
                "temperature": self.temperature,
                "deck_name": self.deck_name,
                "stake": self.stake,
                "steps": [asdict(s) for s in self.steps],
                "played_hands": [asdict(h) for h in self.played_hands],
                "purchases": [asdict(p) for p in self.purchases],
                "cash_flow": [asdict(cf) for cf in self.cash_flow],
                "total_earned": self.total_earned,
                "total_spent": self.total_spent,
                "scratchpad_history": self.scratchpad_history,
                "peak_bank": self.peak_bank,
                "total_hands_played": self.total_hands_played,
                "total_discards_used": self.total_discards_used,
                "total_rerolls": self.total_rerolls,
                "max_single_hand_score": self.max_single_hand_score,
                "best_combo": asdict(self.best_combo) if self.best_combo else None,
                "total_prompt_tokens": self.total_prompt_tokens,
                "total_completion_tokens": self.total_completion_tokens,
                "total_tokens": self.total_tokens,
                "victory_achieved": self.victory_achieved,
            }
            temp_file = self.output_dir / ".active_run.json.tmp"
            temp_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            temp_file.replace(self.active_run_file)
        except Exception as e:
            logger.debug("Failed to save active run checkpoint: %s", e)

    def load_active_checkpoint(self) -> bool:
        """Restores state from .active_run.json if continuing an existing run."""
        if not self.active_run_file.exists():
            return False
        try:
            data = json.loads(self.active_run_file.read_text(encoding="utf-8"))
            self.run_id = data.get("run_id", self.run_id)
            if "start_time" in data:
                self.start_time = datetime.fromisoformat(data["start_time"])
            self.deck_name = data.get("deck_name", self.deck_name)
            self.stake = data.get("stake", self.stake)

            step_fields = {f.name for f in fields(StepLog)}
            self.steps = [StepLog(**{k: v for k, v in s.items() if k in step_fields}) for s in data.get("steps", [])]

            hand_fields = {f.name for f in fields(PlayedHandLog)}
            self.played_hands = [PlayedHandLog(**{k: v for k, v in h.items() if k in hand_fields}) for h in data.get("played_hands", [])]

            purchase_fields = {f.name for f in fields(ShopPurchaseLog)}
            self.purchases = [ShopPurchaseLog(**{k: v for k, v in p.items() if k in purchase_fields}) for p in data.get("purchases", [])]

            cf_fields = {f.name for f in fields(CashFlowLog)}
            self.cash_flow = [CashFlowLog(**{k: v for k, v in cf.items() if k in cf_fields}) for cf in data.get("cash_flow", [])]

            self.scratchpad_history = data.get("scratchpad_history", [])
            self.total_earned = data.get("total_earned", 0)
            self.total_spent = data.get("total_spent", 0)
            self.peak_bank = data.get("peak_bank", 0)
            self.total_hands_played = data.get("total_hands_played", 0)
            self.total_discards_used = data.get("total_discards_used", 0)
            self.total_rerolls = data.get("total_rerolls", 0)
            self.max_single_hand_score = data.get("max_single_hand_score", 0)
            if data.get("best_combo"):
                bc = data["best_combo"]
                self.best_combo = PlayedHandLog(**{k: v for k, v in bc.items() if k in hand_fields})
            self.total_prompt_tokens = data.get("total_prompt_tokens", 0)
            self.total_completion_tokens = data.get("total_completion_tokens", 0)
            self.total_tokens = data.get("total_tokens", 0)
            self.victory_achieved = data.get("victory_achieved", False)
            self.step_counter = len(self.steps)
            return True
        except Exception as e:
            logger.warning("Failed to load active run checkpoint: %s", e)
            return False

    def clear_active_checkpoint(self) -> None:
        """Deletes .active_run.json after run completion or when starting a fresh game."""
        try:
            if self.active_run_file.exists():
                self.active_run_file.unlink()
        except OSError:
            pass

    def record_cash_flow(
        self,
        ante: int,
        round_num: int,
        stage: str,
        event_type: str,
        dollars_before: int,
        dollars_after: int,
        details: str = "",
        step: int | None = None,
    ) -> None:
        """Records an explicit financial transaction (income, expenditure, payout)."""
        delta = dollars_after - dollars_before
        if delta == 0:
            return

        # Deduplicate identical consecutive event
        if (
            self.cash_flow
            and self.cash_flow[-1].dollars_after == dollars_after
            and self.cash_flow[-1].dollars_before == dollars_before
            and self.cash_flow[-1].stage == stage
            and self.cash_flow[-1].event_type == event_type
        ):
            return

        if delta > 0:
            self.total_earned += delta
        else:
            self.total_spent += abs(delta)

        self.peak_bank = max(self.peak_bank, dollars_after)

        entry = CashFlowLog(
            step=step if step is not None else self.step_counter,
            timestamp=datetime.now().strftime("%H:%M:%S"),
            ante=ante,
            round=round_num,
            stage=stage,
            event_type=event_type,
            dollars_before=dollars_before,
            dollars_after=dollars_after,
            delta=delta,
            details=details,
        )
        self.cash_flow.append(entry)

        # Immediate rich console badge (safe for Windows cp1251 legacy terminals)
        try:
            if delta > 0:
                console.print(
                    f"[bold green][+] CASH INFLOW (+${delta}):[/bold green] "
                    f"${dollars_before} -> [bold green]${dollars_after}[/bold green] | "
                    f"[{stage}] {event_type} - {details}"
                )
            else:
                console.print(
                    f"[bold red][-] CASH OUTFLOW (-${abs(delta)}):[/bold red] "
                    f"${dollars_before} -> [bold red]${dollars_after}[/bold red] | "
                    f"[{stage}] {event_type} - {details}"
                )
        except Exception:
            pass
        self.save_active_checkpoint()

    def record_step(
        self,
        state: GameState,
        action: ToolCallAction,
        success: bool,
        next_state: GameState | None = None,
        error_message: str | None = None,
    ) -> None:
        """Records an action and calculates deltas (score gained, economy spent)."""
        self.step_counter += 1
        self.deck_name = state.deck_name or self.deck_name
        self.stake = state.stake or self.stake
        self.peak_bank = max(self.peak_bank, state.dollars)

        # Format current cards & jokers
        jokers_summary = [
            f"{j.label} ({j.rarity}{', ' + j.effect_type if j.effect_type else ''}){': ' + j.effect_desc if j.effect_desc else ''}"
            for j in state.jokers
        ]
        hand_summary = []
        for c in state.hand:
            if c.face_down:
                card_name = f"{c.revealed_name} (Face-down 🙈)" if c.revealed_name else f"{c.id} (Face-down 🙈)"
                hand_summary.append(card_name)
            else:
                hand_summary.append(f"{c.rank} of {c.suit}")

        # Format available shop items, booster pack cards, owned consumables, and skip tags
        available_shop_items = [
            f"[{item.index}] {item.label} (${item.cost}, {item.item_type})"
            for item in state.shop.items
        ] if state.shop and state.shop.items else []

        available_pack_cards = [
            f"[{c.index}] {c.label} ({c.item_type})"
            for c in state.pack_cards
        ] if state.pack_cards else []

        consumables_summary = [
            f"{c.label} ({c.card_type})"
            for c in state.consumables
        ] if state.consumables else []

        offered_skip_tag = (
            f"{state.blind.skip_tag_name} ({state.blind.skip_tag_desc})"
            if state.stage == "BLIND_SELECT" and state.blind.skip_tag_name
            else None
        )

        # Calculate score delta if cards were played and next_state is known
        score_delta = 0
        if action.name == "play_cards":
            self.total_hands_played += 1
            if next_state:
                score_delta = max(0, next_state.current_score - state.current_score)
                played_card_names = []
                played_card_objs = []
                suit_syms = {"Spades": "♠", "Hearts": "♥", "Clubs": "♣", "Diamonds": "♦"}
                rank_abbr = {"Jack": "J", "Queen": "Q", "King": "K", "Ace": "A"}

                for cid in action.arguments.get("card_ids", []):
                    card_obj = next((c for c in state.hand if c.id == cid), None)
                    if card_obj:
                        played_card_objs.append(card_obj)
                        s_icon = suit_syms.get(card_obj.suit, card_obj.suit[0] if card_obj.suit else "")
                        r_code = rank_abbr.get(card_obj.rank, card_obj.rank)
                        if card_obj.face_down:
                            name = card_obj.revealed_name or f"{card_obj.rank} of {card_obj.suit}"
                            played_card_names.append(f"{name} (blind 🙈)")
                        else:
                            enh = f"[{card_obj.enhancement.replace(' Card', '')}]" if card_obj.enhancement else ""
                            played_card_names.append(f"{r_code}{s_icon}{enh}")
                    else:
                        played_card_names.append(cid)

                detected_hand = "Hand"
                if played_card_objs:
                    try:
                        detected_hand, _, _ = BalatroScoringEngine().identify_poker_hand(played_card_objs)
                    except Exception:
                        detected_hand = "Hand"

                hand_log = PlayedHandLog(
                    ante=state.ante,
                    round=state.round,
                    blind_name=state.blind.name,
                    cards_played=played_card_names,
                    score_before=state.current_score,
                    score_after=next_state.current_score,
                    score_gained=score_delta,
                    active_jokers=jokers_summary,
                    reasoning=action.reason,
                    poker_hand=detected_hand,
                )
                self.played_hands.append(hand_log)
                if score_delta > self.max_single_hand_score:
                    self.max_single_hand_score = score_delta
                    self.best_combo = hand_log

        elif action.name == "discard_cards":
            self.total_discards_used += 1

        elif action.name == "reroll_shop":
            self.total_rerolls += 1

        elif action.name == "buy_shop_item":
            item_name = "Shop Item"
            cost = 0
            idx = action.arguments.get("shop_index", 0)
            if state.shop and 0 <= idx < len(state.shop.items):
                item = state.shop.items[idx]
                item_name = item.label
                cost = item.cost

            self.purchases.append(
                ShopPurchaseLog(
                    ante=state.ante,
                    round=state.round,
                    item_label=item_name,
                    cost=cost,
                    bank_after=state.dollars - cost,
                    reasoning=action.reason,
                )
            )

        # Track joker roster changes
        current_joker_names = [j.label for j in state.jokers]
        if not self.joker_build_history or self.joker_build_history[-1]["jokers"] != current_joker_names:
            self.joker_build_history.append({
                "ante": state.ante,
                "round": state.round,
                "jokers": current_joker_names,
                "reason": action.reason if "joker" in action.name else "Joker roster update"
            })

        # Calculate financial delta and track cash flow
        dollars_delta = 0
        if next_state is not None:
            dollars_delta = next_state.dollars - state.dollars
            if dollars_delta != 0:
                event_type = "ACTION_EXPENSE" if dollars_delta < 0 else "ACTION_REWARD"
                details = action.reason or "Balance change after action"
                if action.name == "buy_shop_item":
                    item_name = "Shop Item"
                    idx = action.arguments.get("shop_index", 0)
                    if state.shop and 0 <= idx < len(state.shop.items):
                        item_name = state.shop.items[idx].label
                    event_type = "SHOP_BUY"
                    details = f"Purchased {item_name} for ${abs(dollars_delta)}"
                elif action.name == "reroll_shop":
                    event_type = "SHOP_REROLL"
                    details = f"Rerolled shop for ${abs(dollars_delta)}"
                elif action.name == "buy_booster":
                    event_type = "BUY_BOOSTER"
                    details = f"Purchased booster pack for ${abs(dollars_delta)}"
                elif action.name == "sell_joker":
                    event_type = "SELL_JOKER"
                    details = f"Sold Joker (+${dollars_delta})"
                elif action.name == "sell_card":
                    event_type = "SELL_CARD"
                    details = f"Sold consumable (+${dollars_delta})"
                elif action.name == "play_cards" and dollars_delta > 0:
                    event_type = "CARD_BONUS"
                    details = f"Card payout / Econ Joker (+${dollars_delta})"

                self.record_cash_flow(
                    ante=state.ante,
                    round_num=state.round,
                    stage=state.stage,
                    event_type=event_type,
                    dollars_before=state.dollars,
                    dollars_after=next_state.dollars,
                    details=details,
                    step=self.step_counter,
                )

        # Accumulate token usage
        calc_turns = getattr(action, "calculation_turns", 0)
        self.total_prompt_tokens += action.prompt_tokens
        self.total_completion_tokens += action.completion_tokens
        self.total_tokens += action.total_tokens
        self.total_calculation_turns += calc_turns

        # Track strategic scratchpad changes
        plan_notes = getattr(action, "plan_notes", None) or action.arguments.get("plan_notes")
        if plan_notes and str(plan_notes).strip():
            clean_note = str(plan_notes).strip()
            if not self.scratchpad_history or self.scratchpad_history[-1]["note"] != clean_note:
                self.scratchpad_history.append({
                    "step": self.step_counter,
                    "ante": state.ante,
                    "round": state.round,
                    "stage": state.stage,
                    "note": clean_note,
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                })

        log_entry = StepLog(
            step=self.step_counter,
            timestamp=datetime.now().strftime("%H:%M:%S"),
            ante=state.ante,
            round=state.round,
            stage=state.stage,
            blind_name=state.blind.name,
            score=state.current_score,
            target_score=state.target_score,
            hands_left=state.hands_left,
            discards_left=state.discards_left,
            bank=state.dollars,
            active_jokers=jokers_summary,
            hand_cards=hand_summary,
            action_name=action.name,
            action_args=action.arguments,
            reasoning=action.reason,
            success=success,
            score_delta=score_delta,
            dollars_delta=dollars_delta,
            error_message=error_message,
            available_shop_items=available_shop_items,
            available_pack_cards=available_pack_cards,
            consumables=consumables_summary,
            offered_skip_tag=offered_skip_tag,
            plan_notes=plan_notes,
            prompt_tokens=action.prompt_tokens,
            completion_tokens=action.completion_tokens,
            total_tokens=action.total_tokens,
            calculation_turns=calc_turns,
        )
        self.steps.append(log_entry)
        self.save_active_checkpoint()

    def generate_markdown_report(self, victory: bool = False) -> str:
        """Creates a comprehensive, beautiful Markdown analysis document."""
        duration_sec = int((datetime.now() - self.start_time).total_seconds())
        mins, secs = divmod(duration_sec, 60)

        status_emoji = "🏆" if victory else "💀"
        status_text = "VICTORY: Ante 8 Boss Defeated!" if victory else "RUN TERMINATED"
        last_step = self.steps[-1] if self.steps else None
        final_ante = last_step.ante if last_step else 1
        final_round = last_step.round if last_step else 1
        final_bank = last_step.bank if last_step else 0

        md = []
        md.append(f"# {status_emoji} Balatro Autonomous AI Run Report: {status_text}")
        md.append(f"**Run ID:** `{self.run_id}` | **Date:** {self.start_time.strftime('%Y-%m-%d %H:%M:%S')} | **Duration:** {mins}m {secs}s\n")
        
        md.append("## 🤖 AI Player Profile")
        md.append(f"- **Model**: `{self.model}`")
        md.append(f"- **Provider**: `{self.provider}`")
        md.append(f"- **Temperature**: `{self.temperature}`")
        effort_tag = self.reasoning_effort if self.reasoning_effort else "standard"
        md.append(f"- **Reasoning Effort**: `{effort_tag}`\n")

        net_delta = self.total_earned - self.total_spent
        net_sign = "+" if net_delta >= 0 else "-"
        md.append("## 📊 Run Overview")
        md.append(f"- **Result**: **{status_text}**")
        md.append(f"- **Deck / Stake**: `{self.deck_name}` | `{self.stake}`")
        md.append(f"- **Furthest Progress**: Ante **{final_ante}** / Round **{final_round}**")
        md.append(f"- **Peak Economy**: **${self.peak_bank}** (Final Bank: **${final_bank}**)")
        md.append(f"- **Cash Flow**: Earned **+${self.total_earned}** | Spent **-${self.total_spent}** (Net: **{net_sign}${abs(net_delta)}**)")
        md.append(f"- **Total Hands Played**: **{self.total_hands_played}** | **Total Discards Used**: **{self.total_discards_used}**")
        md.append(f"- **Shop Rerolls**: **{self.total_rerolls}** | **Items Purchased**: **{len(self.purchases)}**\n")

        # Exclusive Post-Game Interview with AI Player
        if self.interview:
            md.append("## 🎙️ Эксклюзивное послематчевое интервью с игроком")
            md.append(f"> *Флеш-интервью взято сразу после финала забега у модели `{self.model}`:*\n")
            md.append(self.interview)
            md.append("\n---\n")

        # Token Consumption & Efficiency Overview
        avg_tokens = self.total_tokens // max(1, len(self.steps))
        avg_prompt = self.total_prompt_tokens // max(1, len(self.steps))
        avg_comp = self.total_completion_tokens // max(1, len(self.steps))
        md.append("## 🪙 Token Consumption & API Efficiency")
        md.append(f"- **Total Input Tokens (Prompt)**: **{self.total_prompt_tokens:,}**")
        md.append(f"- **Total Output Tokens (Completion)**: **{self.total_completion_tokens:,}**")
        md.append(f"- **Total Tokens Consumed**: **{self.total_tokens:,}**")
        md.append(f"- **Total LLM Steps**: **{len(self.steps)}**")
        md.append(f"- **Total Tool Calculation Turns**: **{self.total_calculation_turns}**")
        md.append(f"- **Average Tokens per Step**: **~{avg_tokens:,}** (~{avg_prompt:,} in / ~{avg_comp:,} out)\n")

        # Best Combo / Highlight Hand
        md.append("## 🌟 Best Scoring Hand & Highlights")
        if self.best_combo:
            md.append(f"**Highest Single Hand Score:** **{self.best_combo.score_gained:,} Chips**")
            md.append(f"- **Location**: Ante {self.best_combo.ante}, Round {self.best_combo.round} vs *{self.best_combo.blind_name}*")
            md.append(f"- **Cards Played**: `{', '.join(self.best_combo.cards_played)}`")
            md.append(f"- **Active Jokers**: {', '.join(self.best_combo.active_jokers) if self.best_combo.active_jokers else 'None'}")
            md.append(f"- **AI Strategy / Reasoning**:\n  > *\"{self.best_combo.reasoning}\"*\n")
        else:
            md.append("_No scoring hands recorded yet._\n")

        # Top 5 Hands Table
        if len(self.played_hands) > 1:
            md.append("### 🔝 Top Scoring Hands Played")
            sorted_hands = sorted(self.played_hands, key=lambda h: h.score_gained, reverse=True)[:5]
            md.append("| # | Ante/Round | Blind | Cards Played | Score Gained |")
            md.append("|---|---|---|---|---|")
            for idx, h in enumerate(sorted_hands, 1):
                cards_str = ", ".join(h.cards_played)
                md.append(f"| {idx} | Ante {h.ante}-{h.round} | {h.blind_name} | `{cards_str}` | **{h.score_gained:,}** |")
            md.append("")

        # Joker Build & Synergy Evolution
        md.append("## 🃏 Joker Build Progression & Synergy")
        if self.joker_build_history:
            md.append("| Phase | Active Jokers | Key Rationale |")
            md.append("|---|---|---|")
            for jb in self.joker_build_history:
                jokers_str = " ➔ ".join(jb["jokers"]) if jb["jokers"] else "_Empty_"
                md.append(f"| Ante {jb['ante']} (R{jb['round']}) | {jokers_str} | {jb['reason'][:80]}... |")
            md.append("")
        else:
            md.append("_No jokers acquired._\n")

        # Strategic Scratchpad & Working Memory Evolution
        md.append("## 📝 Strategic Scratchpad & Working Memory")
        if self.scratchpad_history:
            md.append("Historical log of how the agent's build hypothesis and tactical direction evolved across the run:\n")
            md.append("| Step | Phase / Location | Time | Strategic Plan / Working Hypothesis |")
            md.append("|---|---|---|---|")
            for sp in self.scratchpad_history:
                md.append(f"| Step {sp['step']} | Ante {sp['ante']}-{sp['round']} (`{sp.get('stage', 'GAME')}`) | {sp['timestamp']} | *\"{sp['note']}\"* |")
            md.append("")
        else:
            md.append("_No custom scratchpad notes recorded._\n")

        # Financial Audit & Cash Flow Ledger Table
        md.append("## 💰 Financial Audit & Cash Flow Ledger")
        md.append(f"- **Total Inflow (Earned)**: **+${self.total_earned}**")
        md.append(f"- **Total Outflow (Spent)**: **-${self.total_spent}**")
        md.append(f"- **Net Economy Delta**: **{net_sign}${abs(net_delta)}**")
        md.append(f"- **Peak Bank Balance**: **${self.peak_bank}**\n")
        if self.cash_flow:
            md.append("| # | Time | Location | Stage | Event | Delta | Balance | Audit Details |")
            md.append("|---|---|---|---|---|---|---|---|")
            for idx, cf in enumerate(self.cash_flow, 1):
                sign = "+" if cf.delta > 0 else "-"
                delta_str = f"**{sign}${abs(cf.delta)}**"
                md.append(
                    f"| {idx} | {cf.timestamp} | Ante {cf.ante}-{cf.round} | `{cf.stage}` | **{cf.event_type}** | "
                    f"{delta_str} | ${cf.dollars_before} ➔ ${cf.dollars_after} | {cf.details} |"
                )
            md.append("")
        else:
            md.append("_No financial transactions recorded._\n")

        # Purchases Table
        md.append("## 🛒 Shopping & Purchase History")
        if self.purchases:
            md.append("| Ante/Round | Item Purchased | Cost | Bank After | Strategic Intent |")
            md.append("|---|---|---|---|---|")
            for p in self.purchases:
                md.append(f"| Ante {p.ante}-{p.round} | **{p.item_label}** | ${p.cost} | ${p.bank_after} | {p.reasoning[:70]}... |")
            md.append("")
        else:
            md.append("_No items purchased._\n")

        # Lorebook Tactical Intelligence Section
        try:
            from .lorebook import LorebookEngine
            lb_summary = LorebookEngine.get_instance().get_usage_summary()
            total_lb = lb_summary.get("total_activations", 0)
            if total_lb > 0:
                md.append("## 📖 Tactical Lorebook Intelligence Report")
                md.append(f"- **Total Situational Injections**: **{total_lb}** triggers")
                md.append(f"- **Unique Knowledge Cards Consulted**: **{lb_summary.get('unique_cards_matched', 0)}** cards")
                top_cards = lb_summary.get("top_consulted_cards", [])
                if top_cards:
                    md.append("\n### 🏆 Most Consulted Knowledge Cards")
                    md.append("| Card / Concept | Consultations | Role in Decision Prompts |")
                    md.append("|---|---|---|")
                    for card_name, count in top_cards:
                        md.append(f"| **{card_name}** | {count} | Active situational knowledge |")
                md.append("")
        except Exception as e:
            logger.debug("Failed to append lorebook summary to markdown report: %s", e)

        # Step-by-Step Tactical Chronicle
        md.append("## 📜 Chronological Turn-by-Turn Replay")
        md.append("Detailed audit trail of all AI decisions, including full Chain-of-Thought reasoning:\n")
        
        current_ante = None
        current_round = None
        for step in self.steps:
            if step.ante != current_ante or step.round != current_round:
                current_ante = step.ante
                current_round = step.round
                md.append(f"### 📍 Ante {current_ante} · Round {current_round} ({step.blind_name})")
            
            status_icon = "✅" if step.success else "❌"
            token_tag = f" | 🪙 **Tokens**: {step.prompt_tokens:,} in / {step.completion_tokens:,} out" if step.total_tokens > 0 else ""
            md.append(f"- **Step {step.step}** `[{step.timestamp}]` Phase: **{step.stage}** | Bank: `${step.bank}` | Score: `{step.score}/{step.target_score}`{token_tag}")
            md.append(f"  - **Action**: `{step.action_name}` {json.dumps(step.action_args, ensure_ascii=False)} {status_icon}")
            if not step.success and step.error_message:
                md.append(f"  - ❌ **Failure Reason**: `{step.error_message}`")
            if step.offered_skip_tag:
                md.append(f"  - **Offered Skip Tag**: 🎁 {step.offered_skip_tag}")
            if step.available_shop_items:
                md.append(f"  - **Available Shop Items**: {', '.join(step.available_shop_items)}")
            if step.available_pack_cards:
                md.append(f"  - **Available Pack Cards**: {', '.join(step.available_pack_cards)}")
            if step.consumables:
                md.append(f"  - **Inventory Consumables**: {', '.join(step.consumables)}")
            if step.dollars_delta != 0:
                d_sign = "+" if step.dollars_delta > 0 else "-"
                md.append(f"  - **Bank Delta**: **{d_sign}${abs(step.dollars_delta)}** (${step.bank} ➔ ${step.bank + step.dollars_delta})")
            if step.action_name == "play_cards" and step.score_delta > 0:
                md.append(f"  - **Chips Gained**: **+{step.score_delta:,} chips**")
            if step.plan_notes:
                md.append(f"  - 📝 **Scratchpad Note**: *\"{step.plan_notes}\"*")
            md.append(f"  - **AI Reasoning (CoT)**: *\"{step.reasoning}\"*")
            md.append("")

        return "\n".join(md)

    def generate_post_match_dossier(self, max_steps: int = 40) -> str:
        """Generates a dense, comprehensive analytical dossier of the entire match for the post-match LLM interview.
        Includes match overview, complete shopping & purchases history, joker progression, scratchpad evolution,
        and chronological key decision points across all antes.
        """
        last_step = self.steps[-1] if self.steps else None
        final_ante = last_step.ante if last_step else 1
        final_round = last_step.round if last_step else 1
        final_bank = last_step.bank if last_step else 0

        lines = []
        lines.append("### 📊 Сводка забега (Матч-репорт)")
        lines.append(f"- **Колода / Стейк**: `{self.deck_name}` | `{self.stake}`")
        lines.append(f"- **Достигнутый рубеж**: Ante **{final_ante}** / Round **{final_round}**")
        lines.append(f"- **Экономика**: Пиковый банк **${self.peak_bank}** (Финальный банк: **${final_bank}**)")
        lines.append(f"- **Денежный поток**: Заработано **+${self.total_earned}** | Потрачено **-${self.total_spent}**")
        lines.append(f"- **Статистика**: Рук сыграно **{self.total_hands_played}**, Сбросов использовано **{self.total_discards_used}**, Рероллов лавки **{self.total_rerolls}**, Покупок **{len(self.purchases)}**, Расчётов инструментами **{self.total_calculation_turns}**")
        lines.append(f"- **Расход токенов**: Input: **{self.total_prompt_tokens:,}** | Output: **{self.total_completion_tokens:,}** | Итого: **{self.total_tokens:,}** токенов")
        if self.best_combo:
            lines.append(f"- **Лучший удар с руки**: **{self.best_combo.score_gained:,} фишек** (Ante {self.best_combo.ante}-{self.best_combo.round} vs {self.best_combo.blind_name}, карты: `{', '.join(self.best_combo.cards_played)}`)")
        lines.append("")

        # 1. Shopping & Purchases History
        lines.append("### 🛒 История покупок и магазина:")
        if self.purchases:
            for p in self.purchases:
                lines.append(f"- Ante {p.ante}-{p.round}: **{p.item_label}** (${p.cost}) | Банк после: ${p.bank_after} | Замысел: {p.reasoning[:90]}...")
        else:
            lines.append("- _Ни одной покупки в магазине не совершено за весь забег!_")
        lines.append("")

        # 2. Joker Build Progression
        lines.append("### 🃏 Эволюция сборки джокеров:")
        if self.joker_build_history:
            for jb in self.joker_build_history:
                j_str = " ➔ ".join(jb["jokers"]) if jb["jokers"] else "Пусто (0 джокеров)"
                lines.append(f"- Ante {jb['ante']} (R{jb['round']}): [{j_str}] — {jb['reason'][:80]}")
        else:
            lines.append("- _Джокеры не приобретались (голая колода)._")
        lines.append("")

        # 3. Scratchpad Evolution
        if self.scratchpad_history:
            lines.append("### 📝 Эволюция стратегического блокнота (Scratchpad):")
            for sp in self.scratchpad_history:
                lines.append(f"- Шаг {sp['step']} (Ante {sp['ante']}-{sp['round']}, {sp.get('stage', 'GAME')}): *\"{sp['note']}\"*")
            lines.append("")

        # 3b. Lorebook Knowledge Consulted
        try:
            from .lorebook import LorebookEngine
            lb_summary = LorebookEngine.get_instance().get_usage_summary()
            if lb_summary.get("total_activations", 0) > 0:
                lines.append("### 📖 Использование базы знаний (Лорбука):")
                lines.append(f"- Всего обращений к карточкам знаний: {lb_summary.get('total_activations')}")
                lines.append(f"- Уникальных карточек знаний задействовано: {lb_summary.get('unique_cards_matched')}")
                top_items = [f"{title} ({cnt}x)" for title, cnt in lb_summary.get("top_consulted_cards", [])[:5]]
                lines.append(f"- Топ задействованных знаний: {', '.join(top_items)}")
                lines.append("")
        except Exception:
            pass

        # 4. Turn-by-Turn Key Chronicle
        lines.append("### 📜 Хронология ключевых решений (Turn-by-Turn):")
        total_s = len(self.steps)
        if total_s <= max_steps:
            steps_to_show = self.steps
        else:
            seen_step_nums = set()
            important_steps = []
            for s in self.steps:
                if s.stage in ("BLIND_SELECT", "SHOP", "OPENING_BOOSTER") or s.offered_skip_tag:
                    if s.step not in seen_step_nums:
                        seen_step_nums.add(s.step)
                        important_steps.append(s)
            for s in self.steps[-10:]:
                if s.step not in seen_step_nums:
                    seen_step_nums.add(s.step)
                    important_steps.append(s)
            steps_to_show = sorted(important_steps, key=lambda x: x.step)

        for s in steps_to_show:
            tag_str = f" [Тег: {s.offered_skip_tag}]" if s.offered_skip_tag else ""
            shop_str = f" [Товаров в лавке: {len(s.available_shop_items)}]" if s.stage == "SHOP" else ""
            score_delta_str = f" (+{s.score_delta:,} chips)" if s.score_delta > 0 else ""
            reason = (s.reasoning or "").replace("\n", " ").strip()
            if len(reason) > 130:
                reason = reason[:127] + "..."
            lines.append(
                f"- Шаг {s.step} [Ante {s.ante}-{s.round}, {s.stage}]: `{s.action_name}` | "
                f"Банк: ${s.bank} | Счёт: {s.score}/{s.target_score}{score_delta_str}{tag_str}{shop_str}\n"
                f"  Мысли: \"{reason}\""
            )

        return "\n".join(lines)

    def save_run(self, victory: bool = False) -> tuple[Path, Path] | tuple[None, None]:
        """Saves both formatted Markdown and structured JSON logs."""
        if not self.steps:
            logger.info("No gameplay steps were recorded in this run. Skipping empty report.")
            return None, None

        if self.saved:
            return None, None
        self.saved = True

        prefix = "victory_run" if victory else "run"
        last_step = self.steps[-1] if self.steps else None
        final_bank = last_step.bank if last_step else 0
        md_filename = f"{prefix}_{self.run_id}_ante{self.steps[-1].ante if self.steps else 1}.md"
        json_filename = f"{prefix}_{self.run_id}.json"

        md_path = self.output_dir / md_filename
        json_path = self.output_dir / json_filename

        # Write Markdown
        md_content = self.generate_markdown_report(victory=victory)
        md_path.write_text(md_content, encoding="utf-8")

        # Write JSON
        json_data = {
            "run_id": self.run_id,
            "start_time": self.start_time.isoformat(),
            "end_time": datetime.now().isoformat(),
            "victory": victory,
            "ai_profile": {
                "model": self.model,
                "provider": self.provider,
                "temperature": self.temperature,
                "reasoning_effort": self.reasoning_effort or "standard",
            },
            "reasoning_effort": self.reasoning_effort or "standard",
            "deck_name": self.deck_name,
            "stake": self.stake,
            "peak_bank": self.peak_bank,
            "interview": self.interview,
            "scratchpad_history": self.scratchpad_history,
            "financial_summary": {
                "starting_bank": self.cash_flow[0].dollars_before if self.cash_flow else 0,
                "final_bank": final_bank,
                "peak_bank": self.peak_bank,
                "total_earned": self.total_earned,
                "total_spent": self.total_spent,
                "net_delta": self.total_earned - self.total_spent,
                "transaction_count": len(self.cash_flow),
            },
            "cash_flow": [asdict(cf) for cf in self.cash_flow],
            "total_hands_played": self.total_hands_played,
            "total_discards_used": self.total_discards_used,
            "total_rerolls": self.total_rerolls,
            "max_single_hand_score": self.max_single_hand_score,
            "token_usage": {
                "prompt_tokens": self.total_prompt_tokens,
                "completion_tokens": self.total_completion_tokens,
                "total_tokens": self.total_tokens,
                "total_calls": len(self.steps),
                "avg_tokens_per_call": round(self.total_tokens / max(1, len(self.steps)), 1),
            },
            "purchases": [asdict(p) for p in self.purchases],
            "played_hands": [asdict(h) for h in self.played_hands],
            "lorebook_summary": None,
            "steps": [asdict(s) for s in self.steps],
        }
        try:
            from .lorebook import LorebookEngine
            json_data["lorebook_summary"] = LorebookEngine.get_instance().get_usage_summary()
        except Exception:
            pass
        json_path.write_text(json.dumps(json_data, indent=2, ensure_ascii=False), encoding="utf-8")

        # Dynamically update SQLite database & SQL dump
        lb_rank_info = ""
        try:
            from .leaderboard import update_leaderboard_from_run
            lb_info = update_leaderboard_from_run(json_data, self.output_dir)
            run_rank = lb_info.get("run_rank")
            total_runs = lb_info.get("total_runs", 1)
            model_rank = lb_info.get("model_rank")
            total_models = lb_info.get("total_models", 1)
            
            run_str = f"#{run_rank} of {total_runs}" if run_rank else "Unranked"
            model_str = f"#{model_rank} of {total_models}" if model_rank else "Unranked"
            
            effort_tag = f" [{lb_info.get('reasoning_effort')}]" if lb_info.get('reasoning_effort') and lb_info.get('reasoning_effort') != "none" else ""
            lb_rank_info = (
                f"\n[bold yellow]Run Rank:[/bold yellow] [bold cyan]{run_str}[/bold cyan] (Ante {lb_info['ante']}-R{lb_info['round']}) | "
                f"[bold yellow]Model Standing:[/bold yellow] [bold green]{model_str}{effort_tag}[/bold green] (Best: Ante {lb_info.get('model_best_ante', '?')})"
            )
        except Exception as lb_err:
            logger.warning("Failed to auto-update leaderboard: %s", lb_err)

        console.print(
            Panel.fit(
                f"[bold green]{'[VICTORY REPORT]' if victory else '[RUN REPORT]'} SAVED![/bold green]\n"
                f"[cyan]Markdown Analysis:[/cyan] {md_path.resolve()}\n"
                f"[cyan]Structured JSON:[/cyan] {json_path.resolve()}"
                f"{lb_rank_info}",
                border_style="green" if victory else "blue",
            )
        )
        self.clear_active_checkpoint()
        return md_path, json_path
