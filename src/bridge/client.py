"""Asynchronous JSON-RPC 2.0 Client for communicating with the Balatro Lua bridge."""

import asyncio
import json
import logging
import re
from typing import Any
from pydantic import ValidationError

from ..config import settings
from .schema import GameState

logger = logging.getLogger("balatro_bridge")


def normalize_id_list(val: Any) -> list[str]:
    """Coerce various LLM output formats (string, list, comma-separated, bracketed) into a clean list of string IDs."""
    if not val:
        return []
    if isinstance(val, (list, tuple, set)):
        result = []
        for x in val:
            if isinstance(x, str):
                clean = x.strip().strip("'\"")
                if "," in clean:
                    result.extend([s.strip().strip("'\"") for s in clean.split(",") if s.strip()])
                elif clean:
                    result.append(clean)
            elif x is not None:
                result.append(str(x).strip())
        return result
    if isinstance(val, dict):
        return [str(k).strip() for k in val.keys() if str(k).strip()]
    if isinstance(val, str):
        val = val.strip()
        if (val.startswith("[") and val.endswith("]")) or (val.startswith("(") and val.endswith(")")):
            val = val[1:-1].strip()
        parts = re.split(r"[\s,;]+", val)
        return [p.strip().strip("'\"") for p in parts if p.strip().strip("'\"")]
    return [str(val).strip()]


class BridgeConnectionError(Exception):
    """Raised when communication with the Balatro Lua bridge fails."""


class BalatroBridgeClient:
    """Handles low-level TCP/JSON-RPC communication with the Balatro modded process."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        timeout: float | None = None,
    ):
        self.host = host or settings.BRIDGE_HOST
        self.port = port or settings.BRIDGE_PORT
        self.timeout = timeout or settings.BRIDGE_TIMEOUT
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._request_id = 0
        self._lock = asyncio.Lock()
        self.last_error: str | None = None

    async def connect(self) -> None:
        """Establish a TCP connection with the Balatro bridge server."""
        for attempt in range(1, settings.BRIDGE_MAX_RETRIES + 1):
            try:
                logger.info("Connecting to Balatro bridge at %s:%d (attempt %d/%d)...", self.host, self.port, attempt, settings.BRIDGE_MAX_RETRIES)
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    timeout=self.timeout,
                )
                logger.info("Connected to Balatro bridge successfully.")
                return
            except (ConnectionRefusedError, TimeoutError, OSError) as exc:
                logger.warning("Connection to %s:%d failed: %s", self.host, self.port, exc)
                if attempt < settings.BRIDGE_MAX_RETRIES:
                    await asyncio.sleep(settings.BRIDGE_RETRY_DELAY)
                else:
                    raise BridgeConnectionError(
                        f"Could not connect to Balatro bridge at {self.host}:{self.port} after {settings.BRIDGE_MAX_RETRIES} attempts. "
                        "Ensure Balatro is running with the Lua RPC patch active."
                    ) from exc

    async def disconnect(self) -> None:
        """Close current connection safely."""
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception as exc:
                logger.debug("Error while closing writer: %s", exc)
            finally:
                self._reader = None
                self._writer = None
                logger.info("Disconnected from Balatro bridge.")

    async def _send_raw_jsonrpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send a JSON-RPC 2.0 message with lock and read line-delimited response."""
        async with self._lock:
            if self._writer is None or self._reader is None:
                await self.connect()

            self._request_id += 1
            payload = {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
                "params": params or {},
            }

            raw_bytes = json.dumps(payload).encode("utf-8") + b"\n"

            try:
                assert self._writer is not None
                assert self._reader is not None
                self._writer.write(raw_bytes)
                await self._writer.drain()

                line = await asyncio.wait_for(self._reader.readline(), timeout=self.timeout)
                if not line:
                    await self.disconnect()
                    raise BridgeConnectionError("Bridge closed the connection prematurely.")

                data = json.loads(line.decode("utf-8").strip())
                if "error" in data and data["error"] is not None:
                    err = data["error"]
                    err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                    raise BridgeConnectionError(f"RPC Error [{method}]: {err_msg}")

                result = data.get("result")
                if isinstance(result, dict) and result.get("success") is False:
                    self.last_error = result.get("error", f"Action '{method}' failed")
                else:
                    self.last_error = None
                return result

            except (TimeoutError, asyncio.TimeoutError) as exc:
                logger.error("RPC method '%s' timed out after %.1fs", method, self.timeout)
                await self.disconnect()
                raise BridgeConnectionError(f"RPC call '{method}' timed out") from exc
            except (ConnectionResetError, BrokenPipeError, OSError) as exc:
                logger.error("Socket error on '%s': %s", method, exc)
                await self.disconnect()
                raise BridgeConnectionError(f"Socket error on '{method}': {exc}") from exc

    async def fetch_state(self) -> GameState:
        """Retrieve and parse current game state into a validated Pydantic model."""
        raw_state = await self._send_raw_jsonrpc("get_game_state")
        try:
            return GameState.model_validate(raw_state)
        except ValidationError as val_err:
            logger.error("Failed to parse game state schema: %s", val_err)
            raise BridgeConnectionError(f"Invalid game state payload received from bridge: {val_err}") from val_err

    async def play_cards(self, card_ids: list[str] | str) -> bool:
        """Submit highlighted cards to play."""
        clean_ids = normalize_id_list(card_ids)
        logger.info("Executing action: play_cards(%s)", clean_ids)
        result = await self._send_raw_jsonrpc("play_cards", {"card_ids": clean_ids})
        return bool(result.get("success", False)) if isinstance(result, dict) else bool(result)

    async def discard_cards(self, card_ids: list[str] | str) -> bool:
        """Discard highlighted cards."""
        clean_ids = normalize_id_list(card_ids)
        logger.info("Executing action: discard_cards(%s)", clean_ids)
        result = await self._send_raw_jsonrpc("discard_cards", {"card_ids": clean_ids})
        return bool(result.get("success", False)) if isinstance(result, dict) else bool(result)

    async def reorder_jokers(self, joker_order: list[str] | str) -> bool:
        """Rearrange active Jokers by id from left to right."""
        clean_order = normalize_id_list(joker_order)
        logger.info("Executing action: reorder_jokers(%s)", clean_order)
        result = await self._send_raw_jsonrpc("reorder_jokers", {"joker_order": clean_order})
        return bool(result.get("success", False)) if isinstance(result, dict) else bool(result)

    async def buy_card(self, shop_index: int) -> bool:
        """Purchase an item from the current shop."""
        logger.info("Executing action: buy_card(index=%d)", shop_index)
        result = await self._send_raw_jsonrpc("buy_card", {"shop_index": shop_index})
        return bool(result.get("success", False)) if isinstance(result, dict) else bool(result)

    async def sell_joker(self, joker_id: str) -> bool:
        """Sell an active Joker by id."""
        logger.info("Executing action: sell_joker(%s)", joker_id)
        result = await self._send_raw_jsonrpc("sell_joker", {"joker_id": joker_id})
        return bool(result.get("success", False)) if isinstance(result, dict) else bool(result)

    async def sell_consumable(self, card_id: str) -> bool:
        """Sell an active Consumable (Tarot, Planet, Spectral) by id."""
        logger.info("Executing action: sell_consumable(%s)", card_id)
        result = await self._send_raw_jsonrpc("sell_consumable", {"card_id": card_id})
        return bool(result.get("success", False)) if isinstance(result, dict) else bool(result)

    async def select_blind(self) -> bool:
        """Select and begin the next blind."""
        logger.info("Executing action: select_blind()")
        result = await self._send_raw_jsonrpc("select_blind")
        return bool(result.get("success", False)) if isinstance(result, dict) else bool(result)

    async def skip_blind(self) -> bool:
        """Skip the upcoming blind to claim the Tag."""
        logger.info("Executing action: skip_blind()")
        result = await self._send_raw_jsonrpc("skip_blind")
        return bool(result.get("success", False)) if isinstance(result, dict) else bool(result)

    async def reroll_shop(self) -> bool:
        """Reroll items in the current shop."""
        logger.info("Executing action: reroll_shop()")
        result = await self._send_raw_jsonrpc("reroll_shop")
        return bool(result.get("success", False)) if isinstance(result, dict) else bool(result)

    async def cash_out(self) -> bool:
        """Proceed past the round victory cash-out screen into the shop."""
        logger.info("Executing action: cash_out()")
        result = await self._send_raw_jsonrpc("cash_out")
        return bool(result.get("success", False)) if isinstance(result, dict) else bool(result)

    async def end_shop(self) -> bool:
        """Leave the shop and proceed to next blind selection."""
        logger.info("Executing action: end_shop()")
        result = await self._send_raw_jsonrpc("end_shop")
        return bool(result.get("success", False)) if isinstance(result, dict) else bool(result)

    async def use_consumable(self, card_id: str, target_card_ids: list[str] | str | None = None) -> bool:
        """Use an active Tarot / Planet / Spectral card."""
        clean_targets = normalize_id_list(target_card_ids)
        logger.info("Executing action: use_consumable(%s, targets=%s)", card_id, clean_targets)
        result = await self._send_raw_jsonrpc(
            "use_consumable",
            {"card_id": str(card_id), "target_card_ids": clean_targets},
        )
        return bool(result.get("success", False)) if isinstance(result, dict) else bool(result)

    async def select_pack_card(self, card_id: str, target_card_ids: list[str] | str | None = None) -> bool:
        """Select and use/acquire a card from an open Booster pack."""
        clean_targets = normalize_id_list(target_card_ids)
        logger.info("Executing action: select_pack_card(%s, targets=%s)", card_id, clean_targets)
        result = await self._send_raw_jsonrpc(
            "select_pack_card",
            {"card_id": str(card_id), "target_card_ids": clean_targets},
        )
        return bool(result.get("success", False)) if isinstance(result, dict) else bool(result)

    async def skip_pack(self) -> bool:
        """Skip remaining selections in the open Booster pack."""
        logger.info("Executing action: skip_pack()")
        result = await self._send_raw_jsonrpc("skip_pack")
        return bool(result.get("success", False)) if isinstance(result, dict) else bool(result)

    async def reroll_boss_blind(self) -> bool:
        """Reroll current Boss Blind for $10 (requires Director's Cut or Retcon voucher)."""
        logger.info("Executing action: reroll_boss_blind()")
        result = await self._send_raw_jsonrpc("reroll_boss_blind")
        return bool(result.get("success", False)) if isinstance(result, dict) else bool(result)

    async def reorder_hand(self, card_order: list[str] | str) -> bool:
        """Reorder cards currently in hand (affects held-in-hand trigger order)."""
        clean_order = normalize_id_list(card_order)
        logger.info("Executing action: reorder_hand(%s)", clean_order)
        result = await self._send_raw_jsonrpc("reorder_hand", {"card_order": clean_order})
        return bool(result.get("success", False)) if isinstance(result, dict) else bool(result)

