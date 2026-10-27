"""Finite State Machine (FSM) Autonomous Agent Runner for Balatro."""

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any
from rich.console import Console
from rich.panel import Panel

from ..config import settings
from ..bridge.client import BalatroBridgeClient, BridgeConnectionError, normalize_id_list
from ..bridge.schema import GameState
from ..engine.serializer import serialize_game_state_to_markdown, get_joker_role, is_xmult_joker
from ..engine.tools import (
    BALATRO_TOOLS,
    CALCULATION_TOOLS,
    GAME_ACTION_TOOLS,
    convert_tools_for_anthropic,
    get_available_tools,
    ToolCallAction,
)
from ..engine.calculator import execute_calculation_tool, resolve_card_ids
from ..engine.scoring import BalatroScoringEngine
from ..engine.recorder import BalatroRunRecorder
from .strategy import SYSTEM_STRATEGY_PROMPT

logger = logging.getLogger("balatro_agent")
console = Console()


def _detect_expected_bridge_version() -> str:
    try:
        lua_path = Path(__file__).resolve().parent.parent.parent / "lua_patch" / "balatro_rpc_bridge.lua"
        if lua_path.exists():
            m = re.search(r'bridge_version\s*=\s*"(v?[0-9\.]+)"', lua_path.read_text(encoding="utf-8"))
            if m:
                ver = m.group(1)
                return ver if ver.startswith("v") else f"v{ver}"
    except Exception:
        pass
    return "v1.9.14"


EXPECTED_BRIDGE_VERSION = _detect_expected_bridge_version()



class AdaptiveBackoff:
    """Manages geometric/exponential backoff delays for error mitigation and token protection."""

    def __init__(
        self,
        base_delays: list[float] | None = None,
        max_delay: float = 86400.0,  # 24 hours maximum
    ):
        # Progresses geometrically from seconds up to 24 hours:
        # 2s -> 5s -> 15s -> 45s -> 2m (120s) -> 5m (300s) -> 15m (900s) -> 1h (3600s) -> 4h (14400s) -> 24h (86400s)
        self.delays = base_delays or [
            2.0, 5.0, 15.0, 45.0, 120.0, 300.0, 900.0, 3600.0, 14400.0, 86400.0
        ]
        self.max_delay = max_delay
        self.consecutive_failures = 0
        self.last_error_reason: str | None = None

    def record_failure(self, reason: str = "") -> float:
        self.consecutive_failures += 1
        self.last_error_reason = reason
        idx = min(self.consecutive_failures - 1, len(self.delays) - 1)
        delay = self.delays[idx]
        return min(delay, self.max_delay)

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.last_error_reason = None

    @property
    def is_backing_off(self) -> bool:
        return self.consecutive_failures > 0

    @staticmethod
    def format_duration(seconds: float) -> str:
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            m = int(seconds // 60)
            s = int(seconds % 60)
            return f"{m}m {s}s" if s > 0 else f"{m}m"
        else:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            return f"{h}h {m}m" if m > 0 else f"{h}h"


class BalatroAgentRunner:
    """Orchestrates the autonomous perception-reasoning-action loop."""

    def __init__(self, bridge_client: BalatroBridgeClient | None = None):
        self.bridge = bridge_client or BalatroBridgeClient()
        self.recorder = BalatroRunRecorder(
            model=settings.MODEL,
            provider=settings.PROVIDER,
            temperature=settings.TEMPERATURE,
            reasoning_effort=settings.REASONING_EFFORT,
        )
        self.running = False
        self._openai_client: Any = None
        self._anthropic_client: Any = None
        self._round_eval_anim_retries = 0
        self._last_known_dollars: int | None = None
        self._loyalty_baseline_hands: int | None = None
        self._last_seen_state: GameState | None = None
        self._resumed_checkpoint_checked: bool = False
        self._strategic_scratchpad: str = "Survive Ante 1: build economy toward $25 interest, acquire early flat chips and flat mult Jokers."
        self._strategic_scratchpad_ante: int = 1
        self._tracked_round_key: tuple[int, int] | None = None
        self._current_round_played_hands: list[str] = []
        self._tracked_ante: int | None = None
        self._current_ante_played_card_ids: set[str] = set()

        # Adaptive backoff controllers for geometric protection
        self.llm_backoff = AdaptiveBackoff()
        self.action_backoff = AdaptiveBackoff()
        self._bridge_first_disconnect_time: float | None = None
        self.bridge_backoff = AdaptiveBackoff(
            base_delays=[15.0, 30.0, 45.0, 60.0, 90.0]
        )

        self._init_llm_client()

    async def _adaptive_sleep(self, delay: float, check_interval: float = 1.0) -> None:
        """Sleeps for delay seconds while checking self.running every second for clean interruption."""
        elapsed = 0.0
        while elapsed < delay and self.running:
            step = min(check_interval, delay - elapsed)
            await asyncio.sleep(step)
            elapsed += step

    def _init_llm_client(self) -> None:
        """Initializes OpenAI, Anthropic, OpenRouter, or Custom OpenAI-compatible SDK client based on settings."""
        if settings.PROVIDER == "openrouter":
            from openai import AsyncOpenAI

            api_key = settings.OPENROUTER_API_KEY or settings.CUSTOM_API_KEY or settings.OPENAI_API_KEY
            if not api_key:
                raise ValueError("OPENROUTER_API_KEY is not set in environment or .env file.")

            self._openai_client = AsyncOpenAI(
                api_key=api_key,
                base_url=settings.OPENROUTER_BASE_URL,
                timeout=settings.LLM_TIMEOUT,
                max_retries=1,
                default_headers={
                    "HTTP-Referer": "https://github.com/balatrollm",
                    "X-Title": "BalatroLLM Agent",
                },
            )
            logger.info("Initialized OpenRouter client with model: %s", settings.MODEL)
        elif settings.PROVIDER in ("openai", "custom"):
            from openai import AsyncOpenAI
            import re

            api_key = (settings.CUSTOM_API_KEY or settings.OPENAI_API_KEY or "").strip()
            raw_base_url = (settings.CUSTOM_BASE_URL or settings.OPENAI_BASE_URL or "").strip()
            base_url = None
            if raw_base_url:
                base_url = re.sub(r"/chat/completions/?$", "", raw_base_url).strip()

            if not api_key:
                raise ValueError(
                    f"API key is not set for provider '{settings.PROVIDER}'. "
                    "Please set CUSTOM_API_KEY or OPENAI_API_KEY in your .env file."
                )

            self._openai_client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=settings.LLM_TIMEOUT,
                max_retries=1,
            )
            logger.info(
                "Initialized %s client with base_url=%s, model=%s",
                settings.PROVIDER,
                base_url or "https://api.openai.com/v1",
                settings.MODEL,
            )
        elif settings.PROVIDER == "anthropic":
            from anthropic import AsyncAnthropic

            self._anthropic_client = AsyncAnthropic(
                api_key=settings.ANTHROPIC_API_KEY,
                base_url=settings.ANTHROPIC_BASE_URL,
                timeout=settings.LLM_TIMEOUT,
            )
            logger.info("Initialized Anthropic client with model: %s", settings.MODEL)

    def _get_safe_fallback_action(self, state: GameState | None) -> ToolCallAction | None:
        """Determines a safe, valid game action if the model runs out of calculation turns."""
        if not state:
            return None
        from ..engine.scoring import BalatroScoringEngine

        max_calc = getattr(settings, "MAX_CALCULATION_TURNS", 8)
        stage = getattr(state, "stage", "")

        # 1. Round Eval / Playing Hand
        if (stage in ("SELECTING_HAND", "ROUND_EVAL") or getattr(state, "hands_left", 0) > 0) and state.current_score < state.target_score:
            if state.hand:
                scoring_engine = BalatroScoringEngine()
                eval_hand = [c for c in state.hand if not c.face_down]
                candidate_hands = scoring_engine.evaluate_candidate_hands(eval_hand, state=state) if eval_hand else []
                if candidate_hands:
                    best = candidate_hands[0]
                    card_ids = [c.id for c in best.cards]
                    logger.warning("Calculation turns exhausted. Fallback action: playing top detected hand %s (%s)", best.hand_type, card_ids)
                    return ToolCallAction(
                        name="play_cards",
                        arguments={"card_ids": card_ids, "reason": f"Fallback: Playing best detected {best.hand_type} (~{best.score:,} pts) after calculation limit"},
                        reason=f"Fallback: Playing best detected {best.hand_type}",
                        calculation_turns=max_calc,
                    )
                card_ids = [c.id for c in state.hand[:5]]
                return ToolCallAction(
                    name="play_cards",
                    arguments={"card_ids": card_ids, "reason": "Fallback: Playing available cards after calculation limit"},
                    reason="Fallback: Playing available cards",
                    calculation_turns=max_calc,
                )

        # 2. Cash out (Round eval when target score cleared)
        if stage == "ROUND_EVAL" or (state.current_score >= state.target_score and state.target_score > 0):
            return ToolCallAction(
                name="cash_out",
                arguments={"reason": "Fallback: Cashing out after calculation limit"},
                reason="Fallback: Cashing out",
                calculation_turns=max_calc,
            )

        # 3. Shop phase
        if stage == "SHOP":
            return ToolCallAction(
                name="end_shop",
                arguments={"reason": "Fallback: Ending shop after calculation limit"},
                reason="Fallback: Ending shop after calculation limit",
                calculation_turns=max_calc,
            )

        # 4. Booster Pack
        if stage == "OPENING_BOOSTER":
            return ToolCallAction(
                name="skip_pack",
                arguments={"reason": "Fallback: Skipping booster pack after calculation limit"},
                reason="Fallback: Skipping booster pack",
                calculation_turns=max_calc,
            )

        # 5. Blind Select
        if stage == "BLIND_SELECT":
            return ToolCallAction(
                name="select_blind",
                arguments={"reason": "Fallback: Selecting blind after calculation limit"},
                reason="Fallback: Selecting blind",
                calculation_turns=max_calc,
            )

        return None

    async def _query_llm_action(
        self,
        state_markdown: str,
        state: GameState | None = None,
        error_feedback: str | None = None,
    ) -> ToolCallAction:
        """Submits the serialized state to the LLM and runs an Inner Reflection Loop.
        If the model invokes local calculation/simulation tools (python_eval, simulate_hand,
        simulate_shop_buy, calculate_hand_odds), they are executed on the host, the results
        are fed back to the conversation, and the model continues reasoning until it commits
        to an irreversible game action.
        """
        user_content = state_markdown
        if error_feedback:
            user_content += f"\n\n🚨 **PREVIOUS ACTION FAILED**: {error_feedback}\nPlease choose a different, valid action."
        max_calc_turns = getattr(settings, "MAX_CALCULATION_TURNS", 8)
        user_content += (
            "\n\n🧠 **TACTICAL REASONING GUIDELINES**:\n"
            f"1. **Calculation & Simulation Budget**: You have direct access to `python_eval(code)`, `simulate_hand(card_ids)`, "
            "`simulate_shop_buy(shop_index, sell_joker_id)`, and `calculate_hand_odds(...)`. "
            f"You have a budget of up to {max_calc_turns} calculation rounds: "
            "test your candidate lines, discard odds, and score thresholds, then decisively commit to your game action!\n"
            "2. **Quick Math**: In hand play, verify your expected (Chips × Mult) vs the remaining blind score.\n"
            "3. **Trade-offs**: In Shop, evaluate Joker upgrades, Joker sequence (+Mult before xMult), and $25 interest threshold.\n"
            "4. **Scratchpad**: If your strategic direction evolves or pivots, update `plan_notes` in your tool call!\n"
            "5. **Hidden Cards (The Fish / The Mark)**: If cards in hand are face-down, DO NOT overthink or calculate probability trees! Commit 5 cards immediately with brief reasoning (<50 words)."
        )

        accum_p_tokens = 0
        accum_c_tokens = 0
        accum_tot_tokens = 0
        calculation_turns = 0
        MAX_CALCULATION_TURNS = max_calc_turns

        if settings.PROVIDER in ("openai", "openrouter", "custom"):
            messages = [
                {"role": "system", "content": SYSTEM_STRATEGY_PROMPT},
                {"role": "user", "content": user_content},
            ]

            while calculation_turns <= MAX_CALCULATION_TURNS:
                current_tools = get_available_tools(
                    state, calculation_turns=calculation_turns, max_calc_turns=MAX_CALCULATION_TURNS
                )
                kwargs: dict[str, Any] = {
                    "model": settings.MODEL,
                    "messages": messages,
                    "tools": current_tools,
                    "tool_choice": "required" if (settings.PROVIDER == "openai" and "gpt" in settings.MODEL.lower() and calculation_turns == 0) else "auto",
                    "temperature": settings.TEMPERATURE,
                }
                if settings.MAX_TOKENS is not None:
                    kwargs["max_tokens"] = settings.MAX_TOKENS
                if settings.REASONING_EFFORT:
                    kwargs["reasoning_effort"] = settings.REASONING_EFFORT

                try:
                    response = await self._openai_client.chat.completions.create(**kwargs)
                except Exception as e:
                    err_str = str(e).lower()
                    if "reasoning_effort" in kwargs and (
                        "reasoning" in err_str
                        or "502" in err_str
                        or "500" in err_str
                        or "upstream" in err_str
                        or "bad gateway" in err_str
                        or "time" in err_str
                        or "timeout" in err_str
                    ):
                        logger.warning(
                            "Provider failed with reasoning_effort='%s' (%s). Retrying without reasoning_effort...",
                            kwargs.get("reasoning_effort"),
                            e,
                        )
                        kwargs.pop("reasoning_effort", None)
                        response = await self._openai_client.chat.completions.create(**kwargs)
                    elif "tool_choice" in err_str or "not supported" in err_str or "unrecognized request argument" in err_str:
                        logger.warning("tool_choice='required' rejected by provider (%s). Retrying with 'auto'...", e)
                        kwargs["tool_choice"] = "auto"
                        response = await self._openai_client.chat.completions.create(**kwargs)
                    elif "time" in err_str or "timeout" in err_str:
                        logger.warning("LLM request timed out (%s). Retrying once...", e)
                        kwargs.pop("reasoning_effort", None)
                        response = await self._openai_client.chat.completions.create(**kwargs)
                    elif "expecting value" in err_str or "jsondecodeerror" in err_str or "char 0" in err_str:
                        logger.warning("Provider returned empty/malformed HTTP body (%s). Retrying once in 1s...", e)
                        await asyncio.sleep(1.0)
                        response = await self._openai_client.chat.completions.create(**kwargs)
                    elif "502" in err_str or "503" in err_str or "504" in err_str or "bad gateway" in err_str:
                        logger.warning("Provider gateway error (%s). Retrying once in 2s...", e)
                        await asyncio.sleep(2.0)
                        response = await self._openai_client.chat.completions.create(**kwargs)
                    else:
                        raise

                choices = response.choices
                if not choices and hasattr(response, "model_extra") and response.model_extra and "data" in response.model_extra:
                    choices = response.model_extra["data"].get("choices", [])

                if not choices:
                    raise ValueError(f"LLM returned no choices in response: {response}")

                choice_0 = choices[0]
                if isinstance(choice_0, dict):
                    msg = choice_0.get("message", {})
                    finish_reason = choice_0.get("finish_reason", "unknown")
                else:
                    msg = choice_0.message
                    finish_reason = getattr(choice_0, "finish_reason", "unknown")

                # Token usage extraction
                usage = getattr(response, "usage", None)
                if not usage and hasattr(response, "model_extra") and response.model_extra and "data" in response.model_extra:
                    usage = response.model_extra["data"].get("usage")
                if isinstance(usage, dict):
                    p_tokens = usage.get("prompt_tokens", 0)
                    c_tokens = usage.get("completion_tokens", 0)
                    tot_tokens = usage.get("total_tokens", p_tokens + c_tokens)
                else:
                    p_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
                    c_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
                    tot_tokens = getattr(usage, "total_tokens", 0) if usage else (p_tokens + c_tokens)

                accum_p_tokens += p_tokens
                accum_c_tokens += c_tokens
                accum_tot_tokens += tot_tokens

                tool_calls = msg.get("tool_calls") if isinstance(msg, dict) else getattr(msg, "tool_calls", None)
                content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
                reasoning_content = msg.get("reasoning_content") if isinstance(msg, dict) else getattr(msg, "reasoning_content", None)
                if not reasoning_content and hasattr(msg, "model_extra") and msg.model_extra:
                    reasoning_content = msg.model_extra.get("reasoning_content") or msg.model_extra.get("reasoning")
                if not reasoning_content and content and "<think>" in content:
                    import re
                    th_m = re.search(r"<think>(.*?)(?:</think>|$)", content, re.DOTALL)
                    if th_m:
                        reasoning_content = th_m.group(1).strip()

                if tool_calls:
                    # Check if all tool calls are calculation tools or if there is a game action
                    has_game_action = any(
                        ((tc.get("function", {}).get("name") if isinstance(tc, dict) else tc.function.name) not in CALCULATION_TOOLS)
                        for tc in tool_calls
                    )

                    if not has_game_action and calculation_turns < MAX_CALCULATION_TURNS:
                        # ALL tool calls are local calculations!
                        calculation_turns += 1
                        asst_tool_calls = []
                        tool_msgs = []
                        # Cap parallel calculations to max 12 (x2.5 from 5) to prevent token bloat
                        executed_calls = tool_calls[:12]
                        skipped_calls = tool_calls[12:]
                        for idx, tc in enumerate(executed_calls):
                            if isinstance(tc, dict):
                                t_id = tc.get("id") or f"call_{calculation_turns}_{idx}_{int(time.time())}"
                                t_name = tc.get("function", {}).get("name")
                                t_raw_args = tc.get("function", {}).get("arguments", "{}")
                            else:
                                t_id = getattr(tc, "id", None) or f"call_{calculation_turns}_{idx}_{int(time.time())}"
                                t_name = tc.function.name
                                t_raw_args = tc.function.arguments

                            t_args = json.loads(t_raw_args) if isinstance(t_raw_args, str) else (t_raw_args or {})
                            console.print(f"[bold magenta]🧮 [Calculation #{calculation_turns}]:[/bold magenta] `{t_name}`({t_args})")
                            c_res = execute_calculation_tool(t_name, t_args, state=state)
                            c_prev = (c_res[:400] + "...") if len(c_res) > 400 else c_res
                            console.print(Panel(f"[cyan]{c_prev}[/cyan]", title=f"🧮 {t_name} Output", border_style="magenta"))

                            asst_tool_calls.append({
                                "id": t_id,
                                "type": "function",
                                "function": {
                                    "name": t_name,
                                    "arguments": t_raw_args if isinstance(t_raw_args, str) else json.dumps(t_raw_args),
                                },
                            })
                            tool_msgs.append({
                                "role": "tool",
                                "tool_call_id": t_id,
                                "name": t_name,
                                "content": str(c_res),
                            })

                        for idx, tc in enumerate(skipped_calls, start=len(executed_calls)):
                            if isinstance(tc, dict):
                                t_id = tc.get("id") or f"call_{calculation_turns}_{idx}_{int(time.time())}"
                                t_name = tc.get("function", {}).get("name")
                                t_raw_args = tc.get("function", {}).get("arguments", "{}")
                            else:
                                t_id = getattr(tc, "id", None) or f"call_{calculation_turns}_{idx}_{int(time.time())}"
                                t_name = tc.function.name
                                t_raw_args = tc.function.arguments
                            asst_tool_calls.append({
                                "id": t_id,
                                "type": "function",
                                "function": {
                                    "name": t_name,
                                    "arguments": t_raw_args if isinstance(t_raw_args, str) else json.dumps(t_raw_args),
                                },
                            })
                            tool_msgs.append({
                                "role": "tool",
                                "tool_call_id": t_id,
                                "name": t_name,
                                "content": "Skipped: Tool call limit per round exceeded (max 12 parallel calculations). Focus on top candidates.",
                            })

                        messages.append({
                            "role": "assistant",
                            "content": content or None,
                            "tool_calls": asst_tool_calls,
                        })
                        messages.extend(tool_msgs)

                        if calculation_turns >= MAX_CALCULATION_TURNS:
                            messages.append({
                                "role": "user",
                                "content": f"⚡ Calculation limit reached ({MAX_CALCULATION_TURNS} rounds completed). Calculation tools are now CLOSED. Please now pick and execute your final game action (e.g. play_cards, discard_cards, buy_shop_item, end_shop, etc.).",
                            })
                        continue

                    # Either a game action was called or calculation turns hit limit
                    # Pick the first game action tool call (or fallback to tool_calls[0])
                    action_tc = next(
                        (tc for tc in tool_calls if (tc.get("function", {}).get("name") if isinstance(tc, dict) else tc.function.name) not in CALCULATION_TOOLS),
                        tool_calls[0],
                    )

                    if isinstance(action_tc, dict):
                        func_name = action_tc.get("function", {}).get("name")
                        raw_args = action_tc.get("function", {}).get("arguments", "{}")
                    else:
                        func_name = action_tc.function.name
                        raw_args = action_tc.function.arguments

                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                    reason = args.get("reason", "")
                    if reasoning_content and isinstance(reasoning_content, str) and reasoning_content.strip():
                        r_clean = reasoning_content.strip()
                        reason = f"🧠 [Reasoning Trace]:\n{r_clean}\n\n[Action Summary]: {reason}" if reason else f"🧠 [Reasoning Trace]:\n{r_clean}"
                    plan_notes = args.get("plan_notes")

                    # If model called a calculation tool at max turns, execute it and ask for game action
                    if func_name in CALCULATION_TOOLS:
                        c_res = execute_calculation_tool(func_name, args, state=state)
                        console.print(f"[bold magenta]🧮 [Final Calculation]:[/bold magenta] `{func_name}` -> {c_res[:150]}")
                        calculation_turns += 1
                        if calculation_turns > MAX_CALCULATION_TURNS:
                            fallback = self._get_safe_fallback_action(state)
                            if fallback:
                                return fallback
                        messages.append({
                            "role": "assistant",
                            "content": content or None,
                            "tool_calls": [{
                                "id": "final_calc",
                                "type": "function",
                                "function": {"name": func_name, "arguments": json.dumps(args)},
                            }],
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": "final_calc",
                            "name": func_name,
                            "content": str(c_res) + "\n\nCRITICAL: Maximum calculation steps reached! Output a final GAME ACTION now.",
                        })
                        continue

                    return ToolCallAction(
                        name=func_name,
                        arguments=args,
                        reason=reason,
                        plan_notes=plan_notes,
                        prompt_tokens=accum_p_tokens,
                        completion_tokens=accum_c_tokens,
                        total_tokens=accum_tot_tokens,
                        calculation_turns=calculation_turns,
                    )

                # Fallback: attempt to parse tool call from markdown or text JSON if model emitted it directly
                if content:
                    import re
                    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
                    json_str = m.group(1) if m else content.strip()
                    try:
                        parsed = json.loads(json_str)
                        if isinstance(parsed, dict) and "name" in parsed:
                            func_name = parsed["name"]
                            args = parsed.get("arguments", {})
                            if isinstance(args, str):
                                args = json.loads(args)

                            if func_name in CALCULATION_TOOLS and calculation_turns < MAX_CALCULATION_TURNS:
                                calculation_turns += 1
                                c_res = execute_calculation_tool(func_name, args, state=state)
                                console.print(f"[bold magenta]🧮 [Calculation #{calculation_turns} (Text)]:[/bold magenta] `{func_name}`({args})")
                                console.print(Panel(f"[cyan]{c_res[:300]}[/cyan]", title=f"🧮 {func_name} Output", border_style="magenta"))
                                messages.append({"role": "assistant", "content": content})
                                messages.append({
                                    "role": "user",
                                    "content": f"Calculation Result for `{func_name}`:\n{c_res}\n\nPlease proceed with your next calculation or take a game action.",
                                })
                                continue

                            return ToolCallAction(
                                name=func_name,
                                arguments=args,
                                reason=args.get("reason", ""),
                                plan_notes=args.get("plan_notes"),
                                prompt_tokens=accum_p_tokens,
                                completion_tokens=accum_c_tokens,
                                total_tokens=accum_tot_tokens,
                                calculation_turns=calculation_turns,
                            )
                    except Exception:
                        pass

                # If model emitted pure text after calculation turns, guide it to emit a tool call
                if calculation_turns > 0 and content and content.strip():
                    messages.append({"role": "assistant", "content": content})
                    messages.append({
                        "role": "user",
                        "content": "Please emit a Tool Call now to execute your chosen game action or perform another calculation.",
                    })
                    calculation_turns += 1
                    continue

                # If model returned no tool call on first turn, attempt immediate concise retry
                logger.warning(
                    "LLM returned no tool call (finish_reason: %s, content: %s). Attempting immediate concise retry...",
                    finish_reason,
                    (content[:80] + "...") if content else "None",
                )
                try:
                    retry_messages = list(messages)
                    retry_messages.append({
                        "role": "user",
                        "content": "⚡ URGENT: Output ONLY a valid Tool Call immediately! Do not output long explanations; pick and execute the action now.",
                    })
                    retry_kwargs = dict(kwargs)
                    retry_kwargs["messages"] = retry_messages
                    retry_kwargs["temperature"] = 0.0
                    retry_kwargs.pop("reasoning_effort", None)
                    retry_response = await self._openai_client.chat.completions.create(**retry_kwargs)
                    r_choices = retry_response.choices
                    if not r_choices and hasattr(retry_response, "model_extra") and retry_response.model_extra and "data" in retry_response.model_extra:
                        r_choices = retry_response.model_extra["data"].get("choices", [])
                    if r_choices:
                        r_choice_0 = r_choices[0]
                        r_msg = r_choice_0.get("message", {}) if isinstance(r_choice_0, dict) else r_choice_0.message
                        r_tc = r_msg.get("tool_calls") if isinstance(r_msg, dict) else getattr(r_msg, "tool_calls", None)
                        if r_tc:
                            r_tool = r_tc[0]
                            if isinstance(r_tool, dict):
                                r_func_name = r_tool.get("function", {}).get("name")
                                r_raw_args = r_tool.get("function", {}).get("arguments", "{}")
                            else:
                                r_func_name = r_tool.function.name
                                r_raw_args = r_tool.function.arguments
                            r_args = json.loads(r_raw_args) if isinstance(r_raw_args, str) else (r_raw_args or {})
                            r_usage = getattr(retry_response, "usage", None)
                            if not r_usage and hasattr(retry_response, "model_extra") and retry_response.model_extra and "data" in retry_response.model_extra:
                                r_usage = retry_response.model_extra["data"].get("usage")
                            if isinstance(r_usage, dict):
                                r_p = r_usage.get("prompt_tokens", 0)
                                r_c = r_usage.get("completion_tokens", 0)
                            else:
                                r_p = getattr(r_usage, "prompt_tokens", 0) if r_usage else 0
                                r_c = getattr(r_usage, "completion_tokens", 0) if r_usage else 0
                            return ToolCallAction(
                                name=r_func_name,
                                arguments=r_args,
                                reason=r_args.get("reason", ""),
                                plan_notes=r_args.get("plan_notes"),
                                prompt_tokens=accum_p_tokens + r_p,
                                completion_tokens=accum_c_tokens + r_c,
                                total_tokens=accum_tot_tokens + (r_p + r_c),
                                calculation_turns=calculation_turns,
                            )
                except Exception as retry_err:
                    logger.warning("Concise retry failed: %s", retry_err)

                raise ValueError(f"LLM did not return any tool call (finish_reason: {finish_reason}). Message content: {content}")

            fallback = self._get_safe_fallback_action(state)
            if fallback:
                return fallback
            raise ValueError("Exceeded maximum calculation loop iterations without committing to a game action.")

        elif settings.PROVIDER == "anthropic":
            messages = [{"role": "user", "content": user_content}]
            calculation_turns = 0
            MAX_CALCULATION_TURNS = max_calc_turns
            accum_p_tokens = 0
            accum_c_tokens = 0
            accum_tot_tokens = 0

            while calculation_turns <= MAX_CALCULATION_TURNS:
                current_anthropic_tools = convert_tools_for_anthropic(
                    get_available_tools(state, calculation_turns=calculation_turns, max_calc_turns=MAX_CALCULATION_TURNS)
                )
                response = await self._anthropic_client.messages.create(
                    model=settings.MODEL,
                    system=SYSTEM_STRATEGY_PROMPT,
                    messages=messages,
                    tools=current_anthropic_tools,
                    tool_choice={"type": "any"},
                    max_tokens=settings.MAX_TOKENS or 1536,
                    temperature=settings.TEMPERATURE,
                )
                usage = getattr(response, "usage", None)
                p_tokens = getattr(usage, "input_tokens", 0) if usage else 0
                c_tokens = getattr(usage, "output_tokens", 0) if usage else 0
                accum_p_tokens += p_tokens
                accum_c_tokens += c_tokens
                accum_tot_tokens += (p_tokens + c_tokens)

                tool_use_blocks = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
                if not tool_use_blocks:
                    text_blocks = [b.text for b in response.content if getattr(b, "type", None) == "text"]
                    full_text = " ".join(text_blocks)
                    if calculation_turns > 0 and full_text.strip():
                        messages.append({"role": "assistant", "content": response.content})
                        messages.append({
                            "role": "user",
                            "content": "Please now emit a tool call to perform your next calculation or take a game action."
                        })
                        calculation_turns += 1
                        continue
                    raise ValueError("Anthropic LLM did not return a tool_use block.")

                # Check if tool is a calculation tool
                tb = tool_use_blocks[0]
                func_name = tb.name
                args = tb.input if isinstance(tb.input, dict) else {}
                reason = args.get("reason", "")
                plan_notes = args.get("plan_notes")

                if func_name in CALCULATION_TOOLS and calculation_turns < MAX_CALCULATION_TURNS:
                    calculation_turns += 1
                    console.print(f"[bold magenta]🧮 [Calculation #{calculation_turns}]:[/bold magenta] `{func_name}`({args})")
                    calc_result = execute_calculation_tool(func_name, args, state=state)
                    calc_preview = (calc_result[:400] + "...") if len(calc_result) > 400 else calc_result
                    console.print(
                        Panel(
                            f"[cyan]{calc_preview}[/cyan]",
                            title=f"🧮 {func_name} Output",
                            border_style="magenta",
                        )
                    )
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tb.id,
                                "content": calc_result,
                            }
                        ],
                    })
                    if calculation_turns >= MAX_CALCULATION_TURNS:
                        messages.append({
                            "role": "user",
                            "content": f"⚡ Calculation limit reached ({MAX_CALCULATION_TURNS} rounds completed). Calculation tools are now CLOSED. Please now pick and execute your final game action (e.g. play_cards, discard_cards, buy_shop_item, end_shop, etc.)."
                        })
                    continue
                elif func_name in CALCULATION_TOOLS:
                    fallback = self._get_safe_fallback_action(state)
                    if fallback:
                        return fallback
                else:
                    return ToolCallAction(
                        name=func_name,
                        arguments=args,
                        reason=reason,
                        plan_notes=plan_notes,
                        prompt_tokens=accum_p_tokens,
                        completion_tokens=accum_c_tokens,
                        total_tokens=accum_tot_tokens,
                        calculation_turns=calculation_turns,
                    )

            fallback = self._get_safe_fallback_action(state)
            if fallback:
                return fallback
            raise ValueError("Exceeded maximum calculation loop iterations without committing to a game action.")

        raise ValueError(f"Unsupported provider: {settings.PROVIDER}")

    async def _conduct_post_match_interview(self, victory: bool, state: GameState) -> str | None:
        """Conducts an authentic esports-style post-match interview with the model."""
        try:
            console.print("\n[bold cyan]🎙️ Журналист спешит к игроку для послематчевого флеш-интервью...[/bold cyan]")

            # Extract key summary points from run
            last_step = self.recorder.steps[-1] if self.recorder.steps else None
            final_ante = last_step.ante if last_step else state.ante
            final_round = last_step.round if last_step else state.round
            max_score = self.recorder.max_single_hand_score

            # Format jokers
            jokers_str = ", ".join(f"{j.label} ({j.rarity})" for j in state.jokers) if state.jokers else "Нет джокеров"

            # Format last thoughts / actions
            last_steps_summary = []
            for s in self.recorder.steps[-3:]:
                clean_reason = (s.reasoning[:200] + "...") if s.reasoning and len(s.reasoning) > 200 else (s.reasoning or "Без комментария")
                last_steps_summary.append(f"- [Шаг {s.step}, Ante {s.ante} Rnd {s.round}, {s.stage}]: Действие `{s.action_name}`, Мысли: \"{clean_reason}\"")
            last_context_str = "\n".join(last_steps_summary) if last_steps_summary else "Начало забега"

            outcome_str = "ТРИУМФАЛЬНАЯ ПОБЕДА (Анте 8 взят!)" if victory else f"ПОРАЖЕНИЕ (Вылет на Ante {final_ante}, Раунд {final_round})"
            dossier_text = self.recorder.generate_post_match_dossier()
            total_steps = len(self.recorder.steps)
            calc_calls = self.recorder.total_calculation_turns

            system_prompt = (
                "Ты — профессиональный киберспортсмен и аналитик Balatro, который только что завершил реальный матч. "
                "Перед тобой лежит полное доказательное досье твоего забега с витринами магазинов, картами в руке и твоими собственными цитатами с каждого шага. "
                "ПРАВИЛА ИНТЕРВЬЮ:\n"
                "1. НИКАКОЙ ВОДЫ И БАНАЛЬНОСТЕЙ: Запрещено отделываться общими фразами вроде «математика Balatro беспощадна, мне просто не выпал xMult». "
                "Опирайся строго на конкретные номера шагов (`Шаг N`), суммы в банке (`$X`), названия карт и свои реальные цитаты из досье.\n"
                "2. НЕ ВЫДУМЫВАЙ ОТСУТСТВИЕ ИНСТРУМЕНТОВ: Во время игры в твоём HUD на каждом шаге УЖЕ БЫЛИ: "
                "автоматический расчёт топ-4 комбинаций в руке (`Detected Hand Opportunities: ~Chips × Mult = ~Score`), "
                "прогноз влияния каждого товара в лавке и потери процентов банка, значки `⚠️DEBUFFED` на ослабленных картах, "
                "а также интерактивные калькуляторы `simulate_hand`, `simulate_shop_buy`, `calculate_hand_odds` и `python_eval`. "
                "Не проси добавить то, что уже было перед глазами — объясняй, почему ты этим воспользовался или проигнорировал!\n"
                "3. ТОЧНОСТЬ МЕХАНИК BALATRO: Никогда не выдумывай несуществующие правила! Без джокера `Splash` в комбинациях `High Card`, `Pair`, `Two Pair` и `Three of a Kind` очки и триггеры джокеров дают ТОЛЬКО зачётные карты самой комбинации (незачётные кикеры НЕ скорятся и НЕ дают очков!). Если ты сыграл 1–2 карты при `0 сбросов` и `hands_left > 1`, ошибка такого хода заключается НЕ в потере очков на текущей руке, а в потере бесплатной прокрутки колоды (псевдо-сброса ненужных карт балластом, чтобы добрать больше свежих карт к следующей руке).\n"
                "4. Отвечай на русском языке от первого лица, живо, честно и предметно."
            )

            user_prompt = f"""🎙️ **ЭКСКЛЮЗИВНОЕ ПОСЛЕМАТЧЕВОЕ ИНТЕРВЬЮ И РАЗБОР ПОЛЁТОВ ({settings.MODEL})**

Итог матча: {outcome_str}
Финальные джокеры: {jokers_str}
Использование калькуляторов за матч: **{calc_calls} вызовов** на **{total_steps} шагов**

📋 **ПОЛНОЕ ДОКАЗАТЕЛЬНОЕ ДОСЬЕ ТВОЕГО ЗАБЕГА (С ВИТРИНАМИ И ТВОИМИ ЦИТАТАМИ):**
{dossier_text}

Твоя финальная записная книжка стратегии (Scratchpad):
{self._strategic_scratchpad}

Журналист-аналитик положил перед тобой распечатку твоих ходов и задаёт 5 конкретных вопросов:

1. 🎙️ **Точка перелома и главная иллюзия (По фактам лога):**
   «Оцени свой забег без общих фраз. Посмотри на эволюцию своих джокеров и записной книжки (`Scratchpad`): на каком конкретно шаге (`Шаг N`, `Ante X-Y`) твой план разошёлся с реальностью? Какая твоя ставка или синергия в этом матче работала, а какая оказалась самообманом?»

2. 🎙️ **Аудит магазинов и экономики (Ответ за свои цитаты и витрины):**
   «Посмотри на раздел "Аудит магазинов, покупок и продаж" и "Автоматический детектор острых моментов", где приведены витрины лавки и твои собственные слова. Разбери 1–2 своих самых спорных решения в магазинах (покупка, продажа джокера, реролл или отказ от товара на витрине): почему ты принял их тогда, и как это ударило по твоему банку и процентам (`$5/$10/$15/$20/$25`)?»

3. 🎙️ **Покадровый разбор финального боя (Карты в руке и розыгрыш):**
   «Посмотри на "Подробную покадровую хронику финального раунда" (какие именно карты были у тебя в руке на каждом шаге, что ты сбрасывал и что играл). Были ли в последнем бою тактические ошибки в выборе карт, порядке их выкладывания или расходе сбросов, или исход был предрешён ещё до входа в блайнд?»

4. 🎙️ **Когнитивный рентген (Работа с HUD и калькуляторами):**
   «За {total_steps} шагов матча ты вызвал калькуляторы (`simulate_hand`, `simulate_shop_buy`, `calculate_hand_odds`, `python_eval`) ровно **{calc_calls} раз(а)**, а в HUD у тебя всегда были готовые расчёты очков и дебаффов. Скажи честно: какой сигнал, расчёт или предупреждение в HUD ты проигнорировал или неправильно истолковал в процессе игры? Что именно в формулировках интерфейса или в твоей собственной логике сбило тебя с толку?»

5. 🎙️ **Конкретный план переигровки этого сида (3 тайминга):**
   «Если прямо сейчас перезапустить этот же сид с Анте 1, зная все витрины магазинов и боссов из досье — назови ровно 3 конкретных шага (`Шаг N`), где ты бы принял противоположное решение, и покажи на числах, как бы это изменило твой финальный счёт и банк.»

Ответь развёрнуто, самокритично и строго по фактам из своего досье по каждому из 5 пунктов."""

            interview_kwargs: dict[str, Any] = {
                "model": settings.MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.4,
            }
            if settings.MAX_TOKENS:
                interview_kwargs["max_tokens"] = settings.MAX_TOKENS

            content = None
            if settings.PROVIDER in ("openai", "custom", "openrouter") and self._openai_client:
                response = await self._openai_client.chat.completions.create(**interview_kwargs)
                choices = response.choices
                if not choices and hasattr(response, "model_extra") and response.model_extra and "data" in response.model_extra:
                    choices = response.model_extra["data"].get("choices", [])
                if choices:
                    choice_0 = choices[0]
                    msg = choice_0.get("message", {}) if isinstance(choice_0, dict) else choice_0.message
                    content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
            elif settings.PROVIDER == "anthropic" and self._anthropic_client:
                response = await self._anthropic_client.messages.create(
                    model=settings.MODEL,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                    max_tokens=2048,
                    temperature=0.7,
                )
                for block in response.content:
                    if block.type == "text":
                        content = block.text
                        break

            if content and content.strip():
                interview_text = content.strip()
                console.print(
                    Panel(
                        f"[italic white]{interview_text}[/italic white]",
                        title="🎙️ ПОСЛЕМАТЧЕВОЕ ИНТЕРВЬЮ С ИГРОКОМ",
                        border_style="cyan",
                        subtitle=f"[dim]Игрок: {settings.MODEL} | Режим: Post-match Reflection[/dim]",
                    )
                )
                return interview_text
            return None
        except Exception as e:
            logger.warning("Post-match interview failed: %s", e)
            return None

    async def _execute_tool_action(self, action: ToolCallAction, state: GameState | None = None) -> bool:
        """Dispatches the tool action to the bridge client."""
        name = action.name
        args = action.arguments

        if settings.DRY_RUN:
            console.print(f"[bold yellow][DRY RUN][/bold yellow] Would execute: {name}({args})")
            return True

        if name == "play_cards":
            if state and (state.hands_left or 0) <= 0:
                logger.error("ILLEGAL ACTION BLOCKED: Model attempted play_cards with 0 hands left!")
                console.print("[bold red]⛔ ILLEGAL ACTION BLOCKED:[/bold red] Model attempted play_cards with 0 hands left! Action rejected.")
                return False
            raw_cards = args.get("card_ids") or args.get("card_id") or args.get("cards", [])
            card_ids = resolve_card_ids(raw_cards, state.hand if state else None)
            played_hand_type = None
            if state and state.hand and card_ids:
                id_map = {c.id: c for c in state.hand}
                chosen_cards = [id_map[cid] for cid in card_ids if cid in id_map]
                if chosen_cards:
                    scoring_engine = BalatroScoringEngine()
                    played_hand_type = scoring_engine.identify_poker_hand(chosen_cards)[0]
                    opt_cards = scoring_engine.optimize_card_order_for_scoring(
                        chosen_cards, hand_type=played_hand_type, state=state
                    )
                    opt_ids = [c.id for c in opt_cards]
                    if opt_ids != card_ids and set(opt_ids) == set(card_ids):
                        logger.info("Auto-optimized played card order for scoring: %s -> %s", card_ids, opt_ids)
                        card_ids = opt_ids
            ok = await self.bridge.play_cards(card_ids)
            if ok:
                if played_hand_type and played_hand_type not in self._current_round_played_hands:
                    self._current_round_played_hands.append(played_hand_type)
                for cid in card_ids:
                    self._current_ante_played_card_ids.add(str(cid))
            return ok
        elif name == "discard_cards":
            if state and (state.discards_left or 0) <= 0:
                logger.error("ILLEGAL ACTION BLOCKED: Model attempted discard_cards with 0 discards left!")
                console.print("[bold red]⛔ ILLEGAL ACTION BLOCKED:[/bold red] Model attempted discard_cards with 0 discards left! Discard cheat blocked.")
                return False
            raw_cards = args.get("card_ids") or args.get("card_id") or args.get("cards", [])
            card_ids = resolve_card_ids(raw_cards, state.hand if state else None)
            return await self.bridge.discard_cards(card_ids)
        elif name == "reorder_jokers":
            raw_order = args.get("joker_order") or args.get("jokers") or args.get("order", [])
            return await self.bridge.reorder_jokers(normalize_id_list(raw_order))
        elif name == "reorder_hand":
            raw_order = args.get("card_order") or args.get("cards") or args.get("order", [])
            card_ids = resolve_card_ids(raw_order, state.hand if state else None)
            return await self.bridge.reorder_hand(card_ids)
        elif name == "buy_shop_item":
            sell_jid = str(args.get("sell_joker_id") or "").strip()
            if sell_jid:
                logger.info("Executing atomic sell_joker_id='%s' before buy_shop_item...", sell_jid)
                sold_ok = await self.bridge.sell_joker(sell_jid)
                if sold_ok:
                    await asyncio.sleep(0.6)
                else:
                    logger.warning(
                        "Pre-buy sell_joker('%s') returned False (%s), attempting buy anyway...",
                        sell_jid,
                        self.bridge.last_error,
                    )
            return await self.bridge.buy_card(args.get("shop_index", 0))
        elif name == "reroll_shop":
            return await self.bridge.reroll_shop()
        elif name == "sell_joker":
            return await self.bridge.sell_joker(args.get("joker_id", ""))
        elif name == "select_blind":
            return await self.bridge.select_blind()
        elif name == "skip_blind":
            return await self.bridge.skip_blind()
        elif name == "reroll_boss_blind":
            return await self.bridge.reroll_boss_blind()
        elif name == "cash_out":
            return await self.bridge.cash_out()
        elif name == "end_shop":
            return await self.bridge.end_shop()
        elif name == "use_consumable":
            raw_targets = args.get("target_card_ids") or args.get("target_cards") or args.get("card_ids")
            target_ids = resolve_card_ids(raw_targets, state.hand if state else None)
            if not target_ids and state and state.hand and state.consumables:
                cid = args.get("card_id", "")
                chosen_cons = next((it for it in state.consumables if it.id == cid), None)
                if chosen_cons:
                    lbl = (chosen_cons.label or "").lower()
                    if any(k in lbl for k in ("empress", "hierophant", "magician", "lovers", "chariot", "justice", "tower", "devil", "strength", "aura", "cryptid", "talisman", "deja vu", "trance", "medium", "moon", "sun", "star", "world")):
                        count = 2 if any(k in lbl for k in ("empress", "hierophant", "magician", "strength")) else (3 if any(k in lbl for k in ("moon", "sun", "star", "world")) else 1)
                        unenhanced = [c.id for c in state.hand if not getattr(c, "enhancement", None)]
                        target_ids = unenhanced[:count] if len(unenhanced) >= count else [c.id for c in state.hand[:count]]
                        logger.info("Auto-assigned target_card_ids=%s for consumable '%s'", target_ids, chosen_cons.label)
            return await self.bridge.use_consumable(
                args.get("card_id", ""),
                target_ids,
            )
        elif name == "sell_consumable":
            return await self.bridge.sell_consumable(args.get("card_id", ""))
        elif name == "select_pack_card":
            raw_targets = args.get("target_card_ids") or args.get("target_cards") or args.get("card_ids")
            target_ids = resolve_card_ids(raw_targets, state.hand if state else None)
            if not target_ids and state and state.hand and state.pack_cards:
                cid = args.get("card_id", "")
                chosen_item = next((it for it in state.pack_cards if it.id == cid), None)
                if chosen_item:
                    lbl = (chosen_item.label or "").lower()
                    if any(k in lbl for k in ("empress", "hierophant", "magician", "lovers", "chariot", "justice", "tower", "devil", "strength", "aura", "cryptid", "talisman", "deja vu", "trance", "medium", "moon", "sun", "star", "world")):
                        count = 2 if any(k in lbl for k in ("empress", "hierophant", "magician", "strength")) else (3 if any(k in lbl for k in ("moon", "sun", "star", "world")) else 1)
                        unenhanced = [c.id for c in state.hand if not getattr(c, "enhancement", None)]
                        target_ids = unenhanced[:count] if len(unenhanced) >= count else [c.id for c in state.hand[:count]]
                        logger.info("Auto-assigned target_card_ids=%s for booster card '%s'", target_ids, chosen_item.label)
            card_id = args.get("card_id", "")
            for attempt in range(4):
                ok = await self.bridge.select_pack_card(card_id, target_ids)
                if ok:
                    return True
                err_lower = (self.bridge.last_error or "").lower()
                if attempt < 3 and ("animation" in err_lower or "locks" in err_lower or "busy" in err_lower):
                    logger.info(
                        "select_pack_card blocked by animation lock (attempt %d/4). Waiting 0.6s...",
                        attempt + 1,
                    )
                    await asyncio.sleep(0.6)
                    continue
                break
            return False
        elif name == "skip_pack":
            return await self.bridge.skip_pack()
        else:
            logger.warning("Unrecognized tool action name: %s", name)
            return False

    async def _run_preflight_health_check(self) -> bool:
        """Runs 3 quick diagnostic probe questions to the LLM before starting the game.
        Verifies API key validity, network latency, and model availability.
        """
        console.print(
            Panel.fit(
                f"[bold cyan]🔍 PRE-FLIGHT LLM HEALTH CHECK[/bold cyan]\n"
                f"[dim]Тестирование работоспособности нейросети перед стартом игры...[/dim]\n"
                f"[cyan]Модель:[/cyan] {settings.MODEL} | [cyan]Провайдер:[/cyan] {settings.PROVIDER} | [cyan]Таймаут:[/cyan] {settings.LLM_TIMEOUT}s",
                border_style="cyan",
            )
        )

        test_questions = [
            ("2 + 2 = ?", "Сколько будет 2 + 2? Ответь только цифрой."),
            ("Столица Франции?", "Столица Франции? Ответь одним словом."),
            ("Цвет травы летом?", "Какого цвета трава летом? Ответь одним словом."),
        ]

        success_count = 0

        for idx, (short_label, prompt) in enumerate(test_questions, 1):
            console.print(f"[dim]  Тест {idx}/3: Запрос к LLM ({short_label})...[/dim]", end=" ")
            t0 = asyncio.get_event_loop().time()
            try:
                if settings.PROVIDER in ("openai", "openrouter", "custom"):
                    assert self._openai_client is not None
                    kwargs: dict[str, Any] = {
                        "model": settings.MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 200,
                        "temperature": 0.0,
                    }
                    if settings.REASONING_EFFORT:
                        kwargs["reasoning_effort"] = settings.REASONING_EFFORT

                    try:
                        resp = await self._openai_client.chat.completions.create(**kwargs)
                    except Exception as e:
                        if "reasoning_effort" in kwargs:
                            kwargs.pop("reasoning_effort", None)
                            resp = await self._openai_client.chat.completions.create(**kwargs)
                        else:
                            raise e

                    ans_text = (resp.choices[0].message.content or "").strip()
                elif settings.PROVIDER == "anthropic":
                    assert self._anthropic_client is not None
                    resp = await self._anthropic_client.messages.create(
                        model=settings.MODEL,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=200,
                        temperature=0.0,
                    )
                    ans_text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
                else:
                    ans_text = "OK"

                dur = asyncio.get_event_loop().time() - t0
                display_ans = ans_text.replace("\n", " ").strip()
                if len(display_ans) > 40:
                    display_ans = display_ans[:37] + "..."
                console.print(f"[bold green]✅ Ответ:[/bold green] \"{display_ans}\" [dim]({dur:.2f}s)[/dim]")
                success_count += 1

            except Exception as exc:
                dur = asyncio.get_event_loop().time() - t0
                console.print(f"[bold red]❌ СБОЙ ({dur:.1f}s):[/bold red] {exc}")
                logger.warning("Pre-flight health check test %d failed: %s", idx, exc)

        if success_count == len(test_questions):
            console.print(
                Panel.fit(
                    f"[bold green]✅ LLM HEALTH CHECK ПРОЙДЕН (3/3)![/bold green]\n"
                    f"[dim]Нейросеть отвечает быстро и корректно. Начинаем забег...[/dim]",
                    border_style="green",
                )
            )
            return True
        else:
            console.print(
                Panel.fit(
                    f"[bold red]⚠️ ВНИМАНИЕ: LLM Health Check пройден частично ({success_count}/3)![/bold red]\n"
                    f"[yellow]Возможны проблемы с провайдером или задержками сети.[/yellow]",
                    border_style="yellow",
                )
            )
            if success_count == 0:
                raise RuntimeError("Pre-flight LLM Health Check полностью провален (0/3). Проверьте API ключ и доступность провайдера.")
            return False

    async def run(self) -> None:
        """Main autonomous execution loop."""
        self.running = True
        console.print("[bold green]Starting Balatro Autonomous LLM Agent...[/bold green]")

        # Run 3 pre-flight health checks to verify LLM responsiveness before starting the match
        try:
            await self._run_preflight_health_check()
        except Exception as preflight_err:
            console.print(f"[bold red]Pre-flight LLM check failed:[/bold red] {preflight_err}")
            return

        # Initial connection to Balatro bridge with grace period (up to 5 min or BRIDGE_MAX_DISCONNECT_FAILURES)
        connected = False
        connect_start = time.time()
        startup_attempt = 0
        startup_delays = [10.0, 20.0, 30.0, 45.0, 60.0]

        while not connected and self.running:
            startup_attempt += 1
            try:
                console.print(
                    f"[cyan]🔌 Подключение к Balatro ({settings.BRIDGE_HOST}:{settings.BRIDGE_PORT}) "
                    f"[Попытка #{startup_attempt}]...[/cyan]"
                )
                await self.bridge.connect()
                connected = True
                console.print("[bold green]✅ Успешно подключено к Balatro! Начинаем забег...[/bold green]\n")
            except (BridgeConnectionError, ConnectionError) as exc:
                elapsed = time.time() - connect_start
                if (
                    startup_attempt >= settings.BRIDGE_MAX_DISCONNECT_FAILURES
                    or elapsed >= settings.BRIDGE_MAX_DISCONNECT_SECONDS
                ):
                    console.print(
                        f"\n[bold red]🛑 Не удалось подключиться к Balatro "
                        f"({startup_attempt} попыток / прошло {elapsed:.0f}с из {settings.BRIDGE_MAX_DISCONNECT_SECONDS:.0f}с).[/bold red]\n"
                        f"[yellow]📱 Убедитесь, что игра запущена на телефоне, IP верный ({settings.BRIDGE_HOST}:{settings.BRIDGE_PORT}) "
                        f"и телефон подключён к той же Wi-Fi сети.[/yellow]\n"
                    )
                    return

                delay_idx = min(startup_attempt - 1, len(startup_delays) - 1)
                retry_wait = startup_delays[delay_idx]
                time_left = max(0, int(settings.BRIDGE_MAX_DISCONNECT_SECONDS - elapsed))
                dur_str = AdaptiveBackoff.format_duration(time_left)

                console.print(
                    f"[yellow]⚠️ Balatro пока не отвечает на {settings.BRIDGE_HOST}:{settings.BRIDGE_PORT}. "
                    f"Ожидание {int(retry_wait)}с... "
                    f"(Запустите игру на телефоне. Осталось попыток: {settings.BRIDGE_MAX_DISCONNECT_FAILURES - startup_attempt}, "
                    f"таймаут: {dur_str})[/yellow]"
                )
                await self._adaptive_sleep(retry_wait)

        if not connected:
            return

        error_feedback: str | None = None

        try:
            while self.running:
                try:
                    # 1. Fetch current game state
                    state: GameState = await self.bridge.fetch_state()
                    self.bridge_backoff.record_success()
                    self._bridge_first_disconnect_time = None
                    self._last_seen_state = state

                    if not self._resumed_checkpoint_checked:
                        self._resumed_checkpoint_checked = True
                        is_initial_start = (state.ante == 1 and state.round <= 1 and state.current_score == 0 and len(state.jokers) == 0)
                        if is_initial_start:
                            self.recorder.clear_active_checkpoint()
                        else:
                            if self.recorder.load_active_checkpoint():
                                last_s = self.recorder.steps[-1] if self.recorder.steps else None
                                last_loc = f"Ante {last_s.ante}, Round {last_s.round}" if last_s else f"Ante {state.ante}, Round {state.round}"
                                console.print(
                                    Panel(
                                        f"[bold green]🔄 ВОССТАНОВЛЕНА ИСТОРИЯ ЗАБЕГА ИЗ КЭША[/bold green]\n"
                                        f"• Забег ID: `{self.recorder.run_id}` (Старт: {self.recorder.start_time.strftime('%H:%M:%S')})\n"
                                        f"• Загружено шагов: [bold cyan]{len(self.recorder.steps)}[/bold cyan] (последний шаг: {last_loc})\n"
                                        f"• Сыграно рук: {self.recorder.total_hands_played} | Сбросов: {self.recorder.total_discards_used}\n"
                                        f"• Пиковый банк: ${self.recorder.peak_bank} | Рекорд удара: {self.recorder.max_single_hand_score:,} фишек\n"
                                        f"[dim]Текущая игра в Balatro будет бесшовно продолжена с сохранением всей сквозной статистики.[/dim]",
                                        title="Active Game Session Resumed",
                                        border_style="cyan",
                                    )
                                )
                                if self.recorder.scratchpad_history:
                                    self._strategic_scratchpad = self.recorder.scratchpad_history[-1]["note"]

                    if not getattr(self, "_logged_bridge_version", False):
                        self._logged_bridge_version = True
                        actual_version = state.bridge_version or "unknown (< v1.9.0)"
                        if state.bridge_version == EXPECTED_BRIDGE_VERSION:
                            console.print(
                                Panel(
                                    f"[bold green]✅ Connected to Balatro Bridge {actual_version} (Up-to-date!)[/bold green]\n"
                                    f"[dim]Bridge Lua version matches runner expectation ({EXPECTED_BRIDGE_VERSION}).[/dim]",
                                    border_style="green",
                                    title="Bridge Version Verified",
                                )
                            )
                            logger.info("Connected to Balatro Bridge %s (matches expected %s)", actual_version, EXPECTED_BRIDGE_VERSION)
                        else:
                            console.print(
                                Panel(
                                    f"[bold red]⚠️ VERSION MISMATCH DETECTED![/bold red]\n\n"
                                    f"• Phone Bridge version:   [bold yellow]{actual_version}[/bold yellow]\n"
                                    f"• Python Agent expects:   [bold green]{EXPECTED_BRIDGE_VERSION}[/bold green]\n\n"
                                    f"[bold yellow]👉 Action required:[/bold yellow] Install [bold cyan]BalatroLLM-{EXPECTED_BRIDGE_VERSION}-clone-signed.apk[/bold cyan] "
                                    f"on the phone via USB wire to ensure bugfixes (joker sync, Flint scoring, tarot stages) are active!",
                                    border_style="red",
                                    title="⚠️ CRITICAL VERSION MISMATCH",
                                )
                            )
                            logger.warning(
                                "VERSION MISMATCH: Phone is running Bridge '%s', but Python expects '%s'! "
                                "Install BalatroLLM-%s-clone-signed.apk on the device.",
                                actual_version,
                                EXPECTED_BRIDGE_VERSION,
                                EXPECTED_BRIDGE_VERSION,
                            )

                    # 1b. Real-time Capital & Cash Flow Auditing
                    if self._last_known_dollars is None:
                        self._last_known_dollars = state.dollars
                        if state.dollars > 0 and not self.recorder.cash_flow:
                            self.recorder.record_cash_flow(
                                ante=state.ante,
                                round_num=state.round,
                                stage=state.stage,
                                event_type="STARTING_CAPITAL",
                                dollars_before=0,
                                dollars_after=state.dollars,
                                details="Initial bank balance at start of run",
                            )
                    elif state.dollars != self._last_known_dollars:
                        delta = state.dollars - self._last_known_dollars
                        event_type = "ROUND_PAYOUT" if state.stage in ("ROUND_EVAL", "SHOP") else "BALANCE_DELTA"
                        desc = (
                            "Round reward & interest payout"
                            if state.stage in ("ROUND_EVAL", "SHOP")
                            else f"Bank shift ({'+' if delta > 0 else ''}${delta})"
                        )
                        self.recorder.record_cash_flow(
                            ante=state.ante,
                            round_num=state.round,
                            stage=state.stage,
                            event_type=event_type,
                            dollars_before=self._last_known_dollars,
                            dollars_after=state.dollars,
                            details=desc,
                        )
                        self._last_known_dollars = state.dollars

                    # 2. Check for game terminal conditions
                    if state.game_over:
                        if not self.recorder.steps:
                            console.print("[dim yellow]⏳ Игра на экране Game Over / Меню. Начните 'Новую игру' на телефоне...[/dim yellow]")
                            await asyncio.sleep(2.0)
                            continue
                        console.print(Panel("[bold red]GAME OVER: Run ended in defeat.[/bold red]", title="Result"))
                        interview = await self._conduct_post_match_interview(victory=False, state=state)
                        self.recorder.interview = interview
                        self.recorder.save_run(victory=False)
                        break
                    if state.victory:
                        console.print(Panel("[bold green]VICTORY: Ante 8 Boss Defeated! Continuing Endless...[/bold green]", title="Result"))
                        if not self.recorder.victory_achieved:
                            self.recorder.victory_achieved = True
                            interview = await self._conduct_post_match_interview(victory=True, state=state)
                            self.recorder.interview = interview
                            self.recorder.save_run(victory=True)

                    # 3. Handle Round Evaluation (Cash Out / Обналичить) with human-first grace period
                    if state.stage == "ROUND_EVAL":
                        current_round_key = (state.ante, state.round)
                        if getattr(self, "_last_cashed_out_key", None) == current_round_key:
                            self._round_eval_waiting_cycles = getattr(self, "_round_eval_waiting_cycles", 0) + 1
                            if self._round_eval_waiting_cycles < 8:
                                logger.debug("Cash out verified/executed for ante %d round %d, waiting for stage change (%d/8)...", state.ante, state.round, self._round_eval_waiting_cycles)
                                await asyncio.sleep(0.4)
                                continue
                            else:
                                logger.warning("Still in ROUND_EVAL after verified cash_out. Re-enabling cash_out retry...")
                                self._last_cashed_out_key = None
                                self._round_eval_waiting_cycles = 0

                        # If dollar tally animation is still playing, wait for cash_out_button to spawn
                        if state.is_animating:
                            self._round_eval_anim_retries += 1
                            if self._round_eval_anim_retries < 10:
                                logger.info(
                                    "Dollar tally animation in progress (%d/10). Waiting...",
                                    self._round_eval_anim_retries,
                                )
                                await asyncio.sleep(0.5)
                                continue
                            else:
                                logger.warning(
                                    "Dollar tally animation wait timed out after 5s. Proceeding to cash out."
                                )
                                self._round_eval_anim_retries = 0
                        else:
                            self._round_eval_anim_retries = 0

                        wait_seconds = max(0, int(settings.CASH_OUT_DELAY))
                        player_cashed_out = False

                        if wait_seconds > 0:
                            console.print(
                                Panel(
                                    f"[bold green]🎉 BLIND WON! Round Evaluation Active (Score: {state.current_score:,}/{state.target_score:,})[/bold green]\n"
                                    f"[cyan]Waiting up to {wait_seconds}s for player to inspect victory and tap 'Обналичить' (Cash Out)...[/cyan]\n"
                                    f"[dim](If no input is detected within {wait_seconds}s, the bot will cash out automatically)[/dim]",
                                    title="Round Evaluation (Cash Out)",
                                    border_style="green",
                                )
                            )
                            remaining_wait = wait_seconds
                            while remaining_wait > 0 and self.running:
                                await asyncio.sleep(1.0)
                                remaining_wait -= 1
                                try:
                                    poll_state = await self.bridge.fetch_state()
                                    if poll_state.stage != "ROUND_EVAL":
                                        console.print("[bold green]Player tapped 'Обналичить' (Cash Out)! Proceeding...[/bold green]")
                                        player_cashed_out = True
                                        self._last_cashed_out_key = current_round_key
                                        self._round_eval_waiting_cycles = 0
                                        if self._last_known_dollars is not None and poll_state.dollars != self._last_known_dollars:
                                            self.recorder.record_cash_flow(
                                                ante=state.ante,
                                                round_num=state.round,
                                                stage="ROUND_EVAL",
                                                event_type="ROUND_PAYOUT",
                                                dollars_before=self._last_known_dollars,
                                                dollars_after=poll_state.dollars,
                                                details="Blind payout: reward + remaining hands + interest",
                                            )
                                            self._last_known_dollars = poll_state.dollars
                                        break
                                except Exception:
                                    pass

                                if remaining_wait % 5 == 0 and remaining_wait > 0:
                                    console.print(f"[dim]⏳ Cash Out timer: {remaining_wait}s remaining before automatic cash out...[/dim]")

                        if not player_cashed_out and self.running:
                            console.print(
                                "[bold yellow]⏱️ Bot is executing automatic `cash_out()`...[/bold yellow]"
                            )
                            try:
                                await self.bridge.cash_out()
                            except Exception as co_err:
                                logger.debug("Bridge cash_out call failed: %s", co_err)

                            cashed_out_verified = False
                            for _ in range(16):
                                await asyncio.sleep(0.4)
                                try:
                                    poll_state = await self.bridge.fetch_state()
                                    if poll_state.stage != "ROUND_EVAL":
                                        console.print(f"[green]Bot successfully cashed out! (Stage: {poll_state.stage})[/green]")
                                        cashed_out_verified = True
                                        self._last_cashed_out_key = current_round_key
                                        self._round_eval_waiting_cycles = 0
                                        if self._last_known_dollars is not None and poll_state.dollars != self._last_known_dollars:
                                            self.recorder.record_cash_flow(
                                                ante=state.ante,
                                                round_num=state.round,
                                                stage="ROUND_EVAL",
                                                event_type="ROUND_PAYOUT",
                                                dollars_before=self._last_known_dollars,
                                                dollars_after=poll_state.dollars,
                                                details="Blind payout: reward + remaining hands + interest",
                                            )
                                            self._last_known_dollars = poll_state.dollars
                                        break
                                except Exception as err:
                                    logger.debug("Polling state after cash_out: %s", err)

                            if not cashed_out_verified:
                                self._round_eval_retries = getattr(self, "_round_eval_retries", 0) + 1
                                if self._round_eval_retries <= 3:
                                    console.print(f"[yellow]⚠️ Stage still in ROUND_EVAL. Retrying cash_out (attempt {self._round_eval_retries}/3)...[/yellow]")
                                    try:
                                        await self.bridge.cash_out()
                                    except Exception:
                                        pass
                                else:
                                    console.print("[bold red]⚠️ Stage still in ROUND_EVAL. Please tap 'Обналичить' (Cash Out) on phone screen![/bold red]")
                                    self._round_eval_retries = 0

                        continue
                    else:
                        self._round_eval_anim_retries = 0
                        self._round_eval_waiting_cycles = 0
                        self._round_eval_retries = 0
                        if getattr(self, "_last_cashed_out_key", None) is not None:
                            self._last_cashed_out_key = None

                    # 3b. Handle animations / transitions (only when not in ROUND_EVAL which has its own countdown)
                    if (state.is_animating or state.stage == "PLAYING_TAROT") and state.stage != "ROUND_EVAL":
                        self._anim_retries = getattr(self, "_anim_retries", 0) + 1
                        if self._anim_retries < 15:
                            logger.debug("Game is animating/dealing (stage: %s, %d/15). Waiting...", state.stage, self._anim_retries)
                            await asyncio.sleep(settings.ANIMATION_DELAY)
                            continue
                        else:
                            logger.warning("Animation wait timed out after 15s (stage: %s). Proceeding.", state.stage)
                            self._anim_retries = 0
                    else:
                        self._anim_retries = 0

                    # If in OPENING_BOOSTER:
                    # 1) Wait for pack cards to finish populating
                    # 2) For Tarot/Spectral packs, wait for hand cards to finish being drawn from deck
                    is_hand_targeted_pack = any(
                        c.item_type in ("Tarot", "Spectral") or "Tarot" in c.label or "Spectral" in c.label
                        for c in state.pack_cards
                    )
                    pack_still_dealing = (
                        len(state.pack_cards) == 0
                        or (is_hand_targeted_pack and len(state.hand) == 0 and state.deck_remaining > 0)
                    )

                    if state.stage == "OPENING_BOOSTER" and pack_still_dealing:
                        self._booster_empty_retries = getattr(self, "_booster_empty_retries", 0) + 1
                        if self._booster_empty_retries < 16:
                            logger.info(
                                "Booster pack dealing cards (%d/16, pack_cards: %d, hand: %d). Waiting...",
                                self._booster_empty_retries,
                                len(state.pack_cards),
                                len(state.hand),
                            )
                            await asyncio.sleep(0.5)
                            continue
                        else:
                            logger.warning("Booster pack dealing timed out after 8s. Proceeding.")
                            self._booster_empty_retries = 0
                    else:
                        self._booster_empty_retries = 0

                    # If in SHOP: wait for shop items to finish populating before querying LLM
                    current_shop_key = f"{state.ante}_{state.round}"
                    if getattr(self, "_active_shop_key", None) != current_shop_key:
                        self._active_shop_key = current_shop_key
                        self._shop_actions_in_visit = 0

                    is_fresh_shop = getattr(self, "_shop_actions_in_visit", 0) == 0
                    shop_has_items = state.shop is not None and len(state.shop.items) > 0
                    shop_still_dealing = (
                        state.stage == "SHOP"
                        and is_fresh_shop
                        and not shop_has_items
                    )
                    if shop_still_dealing:
                        self._shop_empty_retries = getattr(self, "_shop_empty_retries", 0) + 1
                        if self._shop_empty_retries < 25:
                            logger.info(
                                "Shop cards dealing / tag animating (%d/25). Waiting for items to appear...",
                                self._shop_empty_retries,
                            )
                            await asyncio.sleep(0.5)
                            continue
                        else:
                            logger.info("Shop items confirmed empty after 12.5s wait. Proceeding.")
                            self._shop_empty_retries = 0
                    else:
                        self._shop_empty_retries = 0

                    # 3c. Sync local round/ante tracking for Boss Blinds (The Mouth, The Eye, The Pillar) & Card Sharp
                    round_key = (state.ante, state.round)
                    if self._tracked_round_key != round_key:
                        self._tracked_round_key = round_key
                        self._current_round_played_hands = []
                    if self._current_round_played_hands:
                        merged_hands = list(state.played_hand_types_this_round or [])
                        for h_name in self._current_round_played_hands:
                            if h_name not in merged_hands:
                                merged_hands.append(h_name)
                        state.played_hand_types_this_round = merged_hands

                    if self._tracked_ante != state.ante:
                        self._tracked_ante = state.ante
                        self._current_ante_played_card_ids = set()
                    if self._current_ante_played_card_ids:
                        for c in state.hand:
                            if str(c.id) in self._current_ante_played_card_ids:
                                c.played_this_ante = True
                        if state.deck_summary and state.deck_summary.cards:
                            for c in state.deck_summary.cards:
                                if str(c.id) in self._current_ante_played_card_ids:
                                    c.played_this_ante = True

                    # 4. Track Loyalty Card cycle & build indicators
                    has_loyalty = any("loyalty" in (j.key or "").lower() or "loyalty" in (j.label or "").lower() for j in state.jokers)
                    loyalty_remaining: int | None = None
                    if has_loyalty:
                        total_hands = sum(h.played for h in state.poker_hands) if state.poker_hands else 0
                        if self._loyalty_baseline_hands is None:
                            self._loyalty_baseline_hands = total_hands
                        hands_since = total_hands - self._loyalty_baseline_hands
                        cycle_step = (hands_since % 6) + 1
                        loyalty_remaining = (6 - cycle_step) % 6
                    else:
                        self._loyalty_baseline_hands = None

                    # Serialize state to dense Markdown (with persistent scratchpad, loyalty tracker, and best hand benchmark)
                    dense_md = serialize_game_state_to_markdown(
                        state,
                        scratchpad=self._strategic_scratchpad,
                        scratchpad_ante=self._strategic_scratchpad_ante,
                        loyalty_remaining=loyalty_remaining,
                        max_hand_score=self.recorder.max_single_hand_score,
                        best_hand_desc=self.recorder.get_best_hand_desc(),
                        best_hand_jokers=self.recorder.get_best_hand_joker_labels(),
                    )

                    # 5. Display state summary and live indicators in console
                    status_badges: list[str] = []
                    if self.recorder.max_single_hand_score > 0:
                        status_badges.append(f"[bold cyan]⚖️ Best Hand: {self.recorder.max_single_hand_score:,}[/bold cyan]")
                    has_blackboard = any("blackboard" in (j.key or "").lower() or "blackboard" in (j.label or "").lower() for j in state.jokers)
                    if has_blackboard and state.stage in ("SELECTING_HAND", "PLAYING_HAND"):
                        red_cnt = sum(1 for c in state.hand if c.suit in ("Hearts", "Diamonds"))
                        if red_cnt > 0:
                            status_badges.append(f"[yellow]🖤 Blackboard: ❌ {red_cnt} red held[/yellow]")
                        else:
                            status_badges.append("[bold green]🖤 Blackboard: ✅ ACTIVE (X3)![/bold green]")

                    if has_loyalty and loyalty_remaining is not None:
                        if loyalty_remaining == 0:
                            status_badges.append("[bold green]💳 Loyalty: 🔥 ACTIVE (X4)![/bold green]")
                        else:
                            status_badges.append(f"[cyan]💳 Loyalty: ⏳ {loyalty_remaining} hand(s) left[/cyan]")

                    chip_cnt = sum(1 for j in state.jokers if not j.debuffed and get_joker_role(j) in ("chips", "hybrid"))
                    xmult_cnt = sum(1 for j in state.jokers if not j.debuffed and (get_joker_role(j) == "x_mult" or is_xmult_joker(j)))
                    if xmult_cnt > 0 and chip_cnt == 0 and state.ante >= 2:
                        status_badges.append("[bold red]🚨 Chip Deficit (0 Chips vs xMult!)[/bold red]")

                    badge_line = f"\n[dim]⚡ Indicators:[/dim] {' | '.join(status_badges)}" if status_badges else ""

                    console.print(
                        Panel(
                            f"[cyan]Ante: {state.ante}/8[/cyan] | [yellow]Blind: {state.blind.name}[/yellow] | "
                            f"[green]Score: {state.current_score:,}/{state.target_score:,}[/green] | "
                            f"[magenta]Bank: ${state.dollars}[/magenta] | Phase: [bold]{state.stage}[/bold]{badge_line}",
                            title="Balatro State",
                            subtitle=f"[dim]🧠 Note: {self._strategic_scratchpad[:75]}{'...' if len(self._strategic_scratchpad) > 75 else ''}[/dim]",
                        )
                    )

                    # 6. Ask LLM to reason and pick an action
                    console.print("[dim]LLM is thinking and evaluating strategic choices...[/dim]")
                    try:
                        action = await self._query_llm_action(dense_md, state=state, error_feedback=error_feedback)
                    except Exception as llm_err:
                        delay = self.llm_backoff.record_failure(str(llm_err))
                        dur_str = AdaptiveBackoff.format_duration(delay)
                        err_str = str(llm_err).lower()
                        is_quota = (
                            getattr(llm_err, "status_code", None) == 402
                            or " 402" in err_str
                            or "code: 402" in err_str
                            or "insufficient_quota" in err_str
                            or "payment required" in err_str
                        ) and "502" not in err_str
                        err_label = "Баланс/квота исчерпаны (402 Payment Required)" if is_quota else f"Сбой запроса LLM: {llm_err}"
                        console.print(
                            f"[bold red]⏳ Адаптивная защита токенов [Повтор #{self.llm_backoff.consecutive_failures}]: "
                            f"{err_label}. Пауза: {dur_str} (увеличивается геометрически до 24ч)...[/bold red]"
                        )

                        # Re-initialize LLM client to drop any stale keepalive sockets in httpx connection pool
                        try:
                            self._init_llm_client()
                        except Exception as init_err:
                            logger.debug("Client re-init notice: %s", init_err)

                        # Check consecutive errors limit for watchdog restart
                        if self.llm_backoff.consecutive_failures >= settings.MAX_CONSECUTIVE_LLM_ERRORS:
                            console.print(
                                f"\n[bold red]🛑 Превышен лимит последовательных ошибок LLM ({settings.MAX_CONSECUTIVE_LLM_ERRORS}). "
                                "Завершение процесса (exit 42) для перезапуска супервизором...[/bold red]"
                            )
                            self.stop()
                            import sys
                            sys.exit(42)

                        await self._adaptive_sleep(delay)
                        continue

                    # Reset LLM backoff upon successful query
                    self.llm_backoff.record_success()

                    # 6b. Live Token Usage display
                    if action.total_tokens > 0:
                        cumul_tokens = self.recorder.total_tokens + action.total_tokens
                        calc_badge = (
                            f" [bold magenta]({action.calculation_turns} calculation turn{'s' if action.calculation_turns > 1 else ''})[/bold magenta]"
                            if action.calculation_turns > 0
                            else ""
                        )
                        console.print(
                            f"[dim cyan]🪙 Tokens:[/dim cyan] [cyan]{action.prompt_tokens:,}[/cyan] in / "
                            f"[cyan]{action.completion_tokens:,}[/cyan] out "
                            f"([bold cyan]{action.total_tokens:,}[/bold cyan] step{calc_badge}) | "
                            f"[dim]Run total:[/dim] [yellow]{cumul_tokens:,}[/yellow] tokens"
                        )

                    # 7. Print LLM Chain-of-Thought & Scratchpad Updates
                    if settings.VERBOSE_COT and action.reason:
                        console.print(f"[italic blue]Chain-of-Thought:[/italic blue] {action.reason}")

                    if action.plan_notes and action.plan_notes.strip():
                        # Only accept macro-strategy updates during SHOP or BLIND_SELECT phases
                        # so combat micro-turns (play_cards / discard_cards) never overwrite the run's strategic plan.
                        if state.stage in ("SHOP", "BLIND_SELECT") and action.name not in (
                            "play_cards",
                            "discard_cards",
                            "reorder_hand",
                            "select_pack_card",
                            "skip_pack",
                        ):
                            new_plan = action.plan_notes.strip()
                            if new_plan != self._strategic_scratchpad:
                                self._strategic_scratchpad = new_plan
                                self._strategic_scratchpad_ante = state.ante
                                console.print(
                                    Panel(
                                        f"[italic cyan]{self._strategic_scratchpad}[/italic cyan]",
                                        title=f"📝 Strategic Scratchpad Updated (Ante {state.ante} · {state.stage})",
                                        border_style="cyan",
                                    )
                                )
                        else:
                            logger.debug(
                                "Ignored plan_notes update during %s (%s) to preserve macro strategy.",
                                state.stage,
                                action.name,
                            )
                            action.plan_notes = None

                    console.print(f"[bold yellow]Executing Action:[/bold yellow] `{action.name}` {action.arguments}")

                    # 8. Execute action via RPC
                    success = await self._execute_tool_action(action, state=state)
                    if not success:
                        last_err = self.bridge.last_error or "unsuccessful status from game engine"
                        error_feedback = f"Action '{action.name}' failed: {last_err}"
                        console.print(f"[red]{error_feedback}[/red]")

                        action_sig = f"{state.stage}:{action.name}:{str(action.arguments)}"
                        if getattr(self, "_last_failed_sig", None) == action_sig:
                            delay = self.action_backoff.record_failure(action_sig)
                        else:
                            self.action_backoff.record_success()
                            delay = self.action_backoff.record_failure(action_sig)
                        self._last_failed_sig = action_sig

                        if self.action_backoff.consecutive_failures > 1:
                            dur_str = AdaptiveBackoff.format_duration(delay)
                            console.print(
                                f"[bold yellow]⏳ Адаптивная защита игры [Сбой #{self.action_backoff.consecutive_failures}]: "
                                f"Действие '{action.name}' в фазе {state.stage} не выполняется ({last_err}). "
                                f"Пауза: {dur_str} (увеличивается геометрически до 24ч)...[/bold yellow]"
                            )
                            await self._adaptive_sleep(delay)
                    else:
                        error_feedback = None
                        self.action_backoff.record_success()
                        self._last_failed_sig = None
                        if action.name in ("buy_shop_item", "reroll_shop", "end_shop"):
                            self._shop_actions_in_visit = getattr(self, "_shop_actions_in_visit", 0) + 1

                    # 9. Wait for game animation & resolve state delta
                    next_state: GameState | None = None
                    if action.name in ("play_cards", "discard_cards"):
                        await asyncio.sleep(1.2)
                        for _ in range(20):
                            try:
                                poll_state = await self.bridge.fetch_state()
                                if not poll_state.is_animating and poll_state.stage in ("SELECTING_HAND", "SHOP", "BLIND_SELECT", "ROUND_EVAL", "GAME_OVER"):
                                    next_state = poll_state
                                    break
                            except Exception:
                                pass
                            await asyncio.sleep(0.4)
                    elif action.name == "use_consumable":
                        await asyncio.sleep(0.8)
                        for _ in range(15):
                            try:
                                poll_state = await self.bridge.fetch_state()
                                if not poll_state.is_animating and poll_state.stage != "PLAYING_TAROT":
                                    next_state = poll_state
                                    break
                            except Exception:
                                pass
                            await asyncio.sleep(0.4)
                    elif action.name == "buy_shop_item":
                        await asyncio.sleep(0.8)
                        for _ in range(15):
                            try:
                                poll_state = await self.bridge.fetch_state()
                                if poll_state.stage == "OPENING_BOOSTER":
                                    if not poll_state.is_animating and len(poll_state.pack_cards) > 0:
                                        next_state = poll_state
                                        break
                                elif not poll_state.is_animating:
                                    next_state = poll_state
                                    break
                            except Exception:
                                pass
                            await asyncio.sleep(0.4)
                    elif action.name == "select_pack_card":
                        await asyncio.sleep(1.0)
                        for _ in range(16):
                            try:
                                poll_state = await self.bridge.fetch_state()
                                if not poll_state.is_animating and poll_state.stage != "PLAYING_TAROT":
                                    if poll_state.stage == "OPENING_BOOSTER":
                                        if len(poll_state.pack_cards) > 0:
                                            next_state = poll_state
                                            break
                                    elif poll_state.stage in ("SHOP", "SELECTING_HAND", "BLIND_SELECT", "ROUND_EVAL", "GAME_OVER"):
                                        next_state = poll_state
                                        break
                            except Exception:
                                pass
                            await asyncio.sleep(0.4)
                    elif action.name == "select_blind":
                        await asyncio.sleep(0.8)
                        for _ in range(20):
                            try:
                                poll_state = await self.bridge.fetch_state()
                                if not poll_state.is_animating and poll_state.stage in ("SELECTING_HAND", "ROUND_EVAL", "GAME_OVER"):
                                    next_state = poll_state
                                    break
                            except Exception:
                                pass
                            await asyncio.sleep(0.4)
                    elif action.name == "skip_blind":
                        await asyncio.sleep(0.8)
                        for _ in range(15):
                            try:
                                poll_state = await self.bridge.fetch_state()
                                if not poll_state.is_animating and poll_state.stage in ("SELECTING_HAND", "BLIND_SELECT", "ROUND_EVAL", "GAME_OVER"):
                                    next_state = poll_state
                                    break
                            except Exception:
                                pass
                            await asyncio.sleep(0.4)
                    elif action.name in ("end_shop", "skip_pack"):
                        await asyncio.sleep(0.6)
                        for _ in range(12):
                            try:
                                poll_state = await self.bridge.fetch_state()
                                if not poll_state.is_animating:
                                    next_state = poll_state
                                    break
                            except Exception:
                                pass
                            await asyncio.sleep(0.4)
                    else:
                        await asyncio.sleep(settings.ACTION_COOLDOWN)
                        try:
                            next_state = await self.bridge.fetch_state()
                        except Exception:
                            pass

                    if not next_state:
                        try:
                            next_state = await self.bridge.fetch_state()
                        except Exception:
                            pass

                    # 10. Record step in Run Recorder
                    last_error_for_record = None
                    if not success:
                        last_error_for_record = self.bridge.last_error or "unsuccessful status from game engine"
                    self.recorder.record_step(
                        state, action, success, next_state, error_message=last_error_for_record
                    )
                    if next_state is not None:
                        self._last_known_dollars = next_state.dollars

                except (BridgeConnectionError, ConnectionError) as exc:
                    if self._bridge_first_disconnect_time is None:
                        self._bridge_first_disconnect_time = time.time()
                    elapsed = time.time() - self._bridge_first_disconnect_time

                    delay = self.bridge_backoff.record_failure(str(exc))
                    failures = self.bridge_backoff.consecutive_failures
                    dur_str = AdaptiveBackoff.format_duration(delay)

                    console.print(
                        f"[yellow]🔌 Потеря связи с Balatro [Попытка #{failures}/{settings.BRIDGE_MAX_DISCONNECT_FAILURES}]: "
                        f"{exc}.[/yellow]"
                    )

                    if (
                        failures >= settings.BRIDGE_MAX_DISCONNECT_FAILURES
                        or elapsed >= settings.BRIDGE_MAX_DISCONNECT_SECONDS
                    ):
                        console.print(
                            f"\n[bold red]🛑 Превышен лимит ожидания связи с Balatro "
                            f"({failures} попытки подряд / прошло {elapsed:.0f}с из {settings.BRIDGE_MAX_DISCONNECT_SECONDS:.0f}с).[/bold red]\n"
                            f"[yellow]📱 Игра была закрыта на телефоне. Агент безопасно завершает работу без лишнего спама в терминал.\n"
                            f"💾 Текущий прогресс забега сохранён! Запустите программу снова, когда вернётесь в игру.[/yellow]\n"
                        )
                        self.running = False
                        break

                    console.print(
                        f"[dim yellow]⏳ Повтор попытки подключения через {dur_str}... "
                        f"(Осталось попыток: {settings.BRIDGE_MAX_DISCONNECT_FAILURES - failures}, забег не сбрасывается)[/dim yellow]"
                    )
                    await self._adaptive_sleep(delay)
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.exception("Unexpected error in main agent loop: %s", exc)
                    await self._adaptive_sleep(3.0)
        finally:
            if getattr(self, "_last_seen_state", None) and (self._last_seen_state.game_over or self._last_seen_state.victory or self.recorder.victory_achieved):
                if self.recorder.steps and not self.recorder.saved:
                    self.recorder.save_run(victory=self.recorder.victory_achieved)
            else:
                if self.recorder.steps:
                    self.recorder.save_active_checkpoint()
                    curr_loc = f"Ante {self._last_seen_state.ante}, Round {self._last_seen_state.round}" if getattr(self, "_last_seen_state", None) else "mid-game"
                    console.print(
                        f"[dim yellow]⏸️ Сессия приостановлена на {curr_loc}. "
                        f"История забега ({len(self.recorder.steps)} шагов) сохранена в кэш активного забега. "
                        f"При следующем запуске или рестарте история будет бесшовно продолжена.[/dim yellow]"
                    )
            await self.bridge.disconnect()
            console.print("[bold cyan]Balatro Agent stopped safely.[/bold cyan]")

    def stop(self) -> None:
        """Signal the agent loop to gracefully terminate."""
        self.running = False
