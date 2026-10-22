"""Lorebook Engine for Balatro LLM Agent.

Provides Just-In-Time (JIT) situational knowledge injection by scanning
the game state against a structured knowledge base of Markdown cards with
YAML frontmatter (bosses, jokers, vouchers, synergies, decks, etc.).
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..bridge.schema import GameState

logger = logging.getLogger("balatro_agent.lorebook")


@dataclass
class LorebookEntry:
    """Represents a single situational knowledge card parsed from markdown."""

    id: str
    title: str
    category: str
    keys: list[str]
    match_all: bool = False
    stages: list[str] = field(default_factory=list)
    min_ante: int = 1
    priority: int = 50
    content: str = ""
    file_path: str = ""


class LorebookEngine:
    """Loads, indexes, and queries markdown knowledge cards against live GameState."""

    _instance: LorebookEngine | None = None

    def __init__(self, knowledge_dir: Path | str | None = None) -> None:
        if knowledge_dir is None:
            # Default to <project_root>/knowledge
            knowledge_dir = Path(__file__).resolve().parent.parent.parent / "knowledge"
        self.knowledge_dir = Path(knowledge_dir)
        self.entries: list[LorebookEntry] = []
        self._loaded = False
        self.activation_counts: dict[str, int] = {}
        self.activation_history: list[dict[str, Any]] = []

    @classmethod
    def get_instance(cls, knowledge_dir: Path | str | None = None) -> LorebookEngine:
        """Returns singleton instance of LorebookEngine."""
        if cls._instance is None:
            cls._instance = cls(knowledge_dir)
            cls._instance.load_entries()
        return cls._instance

    def load_entries(self, force_reload: bool = False) -> int:
        """Scans and parses all .md files in the knowledge directory."""
        if self._loaded and not force_reload:
            return len(self.entries)

        self.entries.clear()
        if not self.knowledge_dir.exists() or not self.knowledge_dir.is_dir():
            logger.warning("Lorebook knowledge directory not found at: %s", self.knowledge_dir)
            self._loaded = True
            return 0

        count = 0
        for root, _, files in os.walk(self.knowledge_dir):
            for filename in sorted(files):
                if filename.endswith(".md"):
                    file_path = os.path.join(root, filename)
                    try:
                        entry = self._parse_file(file_path)
                        if entry:
                            self.entries.append(entry)
                            count += 1
                    except Exception as exc:
                        logger.warning("Failed to parse lorebook card %s: %s", file_path, exc)

        self._loaded = True
        logger.info("Loaded %d Lorebook knowledge entries from %s", count, self.knowledge_dir)
        return count

    def _parse_file(self, file_path: str) -> LorebookEntry | None:
        """Parses a single Markdown file with YAML frontmatter."""
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()

        if not text.startswith("---"):
            return None

        parts = text.split("---", 2)
        if len(parts) < 3:
            return None

        frontmatter_raw = parts[1]
        body = parts[2].strip()

        meta = self._parse_frontmatter(frontmatter_raw)
        entry_id = str(meta.get("id") or Path(file_path).stem)
        title = str(meta.get("title") or entry_id)
        category = str(meta.get("category") or Path(file_path).parent.name)

        raw_keys = meta.get("keys")
        if isinstance(raw_keys, list):
            keys = [str(k).strip().lower() for k in raw_keys if str(k).strip()]
        elif isinstance(raw_keys, str):
            keys = [raw_keys.strip().lower()]
        else:
            keys = [entry_id.lower()]

        match_all = bool(meta.get("match_all", False))

        raw_stages = meta.get("stages")
        if isinstance(raw_stages, list):
            stages = [str(s).strip() for s in raw_stages if str(s).strip()]
        else:
            stages = []

        min_ante = int(meta.get("min_ante", 1))
        priority = int(meta.get("priority", 50))

        return LorebookEntry(
            id=entry_id,
            title=title,
            category=category,
            keys=keys,
            match_all=match_all,
            stages=stages,
            min_ante=min_ante,
            priority=priority,
            content=body,
            file_path=file_path,
        )

    @staticmethod
    def _parse_frontmatter(fm_text: str) -> dict[str, Any]:
        """Zero-dependency parser for standard YAML frontmatter blocks."""
        meta: dict[str, Any] = {}
        for line in fm_text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip()
                v = v.strip()
                if v.startswith("[") and v.endswith("]"):
                    try:
                        meta[k] = json.loads(v)
                    except Exception:
                        items = [x.strip().strip("\"'") for x in v[1:-1].split(",") if x.strip()]
                        meta[k] = items
                elif v.lower() in ("true", "false"):
                    meta[k] = v.lower() == "true"
                elif v.isdigit():
                    meta[k] = int(v)
                else:
                    meta[k] = v.strip("\"'")
        return meta

    def match_state(
        self,
        state: GameState,
        max_entries: int = 3,
        max_chars: int = 2400,
    ) -> list[LorebookEntry]:
        """Matches entries against the live GameState and returns top relevant entries."""
        if not self._loaded:
            self.load_entries()

        if not self.entries:
            return []

        # 1. Build Search Space from Live GameState
        stage = state.stage or "SELECTING_HAND"
        ante = state.ante or 1

        state_tokens: set[str] = set()
        text_blobs: list[str] = []

        # Blinds & Bosses
        blind_name = (state.blind.name or "").lower() if state.blind else ""
        text_blobs.append(blind_name)
        if state.blind and getattr(state.blind, "upcoming_boss_name", None):
            text_blobs.append(str(state.blind.upcoming_boss_name).lower())
        if state.ante_blinds:
            for b in state.ante_blinds:
                if b.name:
                    text_blobs.append(b.name.lower())

        # Jokers
        for j in state.jokers or []:
            if j.label:
                text_blobs.append(j.label.lower())
                state_tokens.add(j.label.lower())
            if j.key:
                text_blobs.append(j.key.lower())
                state_tokens.add(j.key.lower())
            if getattr(j, "edition", None):
                text_blobs.append(f"{j.edition} edition".lower())

        # Consumables
        for c in state.consumables or []:
            if c.label:
                text_blobs.append(c.label.lower())
                state_tokens.add(c.label.lower())
            if getattr(c, "key", None):
                text_blobs.append(str(c.key).lower())
            if c.id:
                text_blobs.append(c.id.lower())

        # Shop Items
        if state.stage == "SHOP" and state.shop and state.shop.items:
            for it in state.shop.items:
                if it.label:
                    text_blobs.append(it.label.lower())
                    state_tokens.add(it.label.lower())
                if it.id:
                    text_blobs.append(it.id.lower())
                    state_tokens.add(it.id.lower())
                if getattr(it, "key", None):
                    text_blobs.append(str(it.key).lower())

        # Booster Pack Options
        if state.stage == "OPENING_BOOSTER" and getattr(state, "pack_cards", None):
            for p in state.pack_cards or []:
                if getattr(p, "label", None):
                    text_blobs.append(str(p.label).lower())
                if getattr(p, "id", None):
                    text_blobs.append(str(p.id).lower())

        # Vouchers Owned
        for v in getattr(state, "vouchers", []) or []:
            text_blobs.append(v.lower())
            state_tokens.add(v.lower())

        # Deck Name
        if state.deck_name:
            text_blobs.append(state.deck_name.lower())

        # Hand card modifications (Seals, Editions, Enhancements)
        for c in state.hand or []:
            if c.seal:
                text_blobs.append(f"{c.seal} seal".lower())
            if c.edition:
                text_blobs.append(f"{c.edition} edition".lower())
            if c.enhancement:
                text_blobs.append(f"{c.enhancement}".lower())

        search_blob = " ".join(text_blobs)

        # 2. Score and Filter Candidate Entries
        matched_candidates: list[tuple[int, LorebookEntry]] = []

        for entry in self.entries:
            # Check stage restrictions
            if entry.stages and stage not in entry.stages:
                continue

            # Check ante restrictions
            if ante < entry.min_ante:
                continue

            # Check keys
            if entry.match_all:
                all_match = all(
                    k in search_blob or k in state_tokens
                    for k in entry.keys
                )
                if not all_match:
                    continue
            else:
                any_match = any(
                    k in search_blob or k in state_tokens
                    for k in entry.keys
                )
                if not any_match:
                    continue

            # Calculate Effective Priority Score
            score = entry.priority

            # Relevance Boost 1: Active Boss Blind in Battle
            if entry.category == "bosses" and state.blind and state.blind.is_boss:
                if any(k in blind_name for k in entry.keys):
                    score += 25

            # Relevance Boost 2: Synergies where multiple required jokers are held
            if entry.category == "synergies":
                matching_keys = sum(1 for k in entry.keys if k in search_blob)
                if matching_keys >= 2:
                    score += 20

            # Relevance Boost 3: Item for sale in shop right now
            if state.stage == "SHOP" and entry.category in ("jokers", "vouchers", "tarot", "spectral", "planets"):
                if state.shop and any(any(k in (it.label or "").lower() or k in (it.id or "").lower() for k in entry.keys) for it in state.shop.items):
                    score += 15

            matched_candidates.append((score, entry))

        # 3. Sort by priority descending and deduplicate by ID
        matched_candidates.sort(key=lambda x: x[0], reverse=True)

        chosen: list[LorebookEntry] = []
        seen_ids: set[str] = set()
        accumulated_chars = 0

        for _, entry in matched_candidates:
            if entry.id in seen_ids:
                continue
            entry_len = len(entry.content) + len(entry.title) + 50
            if len(chosen) >= max_entries and len(chosen) > 0:
                break
            if accumulated_chars + entry_len > max_chars and len(chosen) > 0:
                continue

            chosen.append(entry)
            seen_ids.add(entry.id)
            accumulated_chars += entry_len

        return chosen

    def format_insights_markdown(
        self,
        state: GameState,
        max_entries: int = 3,
        max_chars: int = 2400,
    ) -> str:
        """Generates formatted Markdown block containing matched Lorebook insights."""
        matched = self.match_state(state, max_entries=max_entries, max_chars=max_chars)
        if not matched:
            return ""

        # Record usage telemetry for run reports
        ante = getattr(state, "ante", 1) or 1
        round_num = getattr(state, "round", 1) or 1
        stage = getattr(state, "stage", "UNKNOWN") or "UNKNOWN"
        for entry in matched:
            self.activation_counts[entry.title] = self.activation_counts.get(entry.title, 0) + 1
            self.activation_history.append({
                "ante": ante,
                "round": round_num,
                "stage": stage,
                "title": entry.title,
                "category": entry.category,
                "id": entry.id,
            })

        lines: list[str] = []
        lines.append(f"#### 📖 Lorebook Tactical Insights ({len(matched)} situational cards matched):")

        for entry in matched:
            lines.append(f"> ### {entry.title}")
            for body_line in entry.content.splitlines():
                if body_line.strip():
                    lines.append(f"> {body_line}")
                else:
                    lines.append(">")
            lines.append(">")

        return "\n".join(lines).rstrip()

    def get_usage_summary(self) -> dict[str, Any]:
        """Returns aggregated telemetry on lorebook consultations for reports."""
        top_cards = sorted(self.activation_counts.items(), key=lambda x: x[1], reverse=True)
        return {
            "total_activations": sum(self.activation_counts.values()),
            "unique_cards_matched": len(self.activation_counts),
            "top_consulted_cards": top_cards[:10],
            "activation_counts": dict(top_cards),
            "recent_activations": self.activation_history[-50:],
        }

    def reset_telemetry(self) -> None:
        """Clears telemetry counters between game runs."""
        self.activation_counts.clear()
        self.activation_history.clear()


def get_lorebook_insights_for_state(state: GameState, max_entries: int = 3) -> str:
    """Convenience helper to retrieve formatted lorebook insights for GameState."""
    engine = LorebookEngine.get_instance()
    return engine.format_insights_markdown(state, max_entries=max_entries)
