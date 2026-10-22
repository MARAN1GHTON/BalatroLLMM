# 🃏 BalatroLLM: Autonomous LLM Agent for Balatro

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Pydantic: v2](https://img.shields.io/badge/Pydantic-v2-green.svg)](https://docs.pydantic.dev/)
[![JSON-RPC: 2.0](https://img.shields.io/badge/Bridge-JSON--RPC%202.0-orange.svg)](#architecture)

**BalatroLLM** is an autonomous, high-frequency AI agent designed to master [Balatro](https://store.steampowered.com/app/2379780/Balatro/) on **Mobile (Android / Termux / ADB)** and **Desktop (PC / Mac)**.

It pairs state-of-the-art Large Language Models (OpenAI GPT-4o, Anthropic Claude 3.5 Sonnet, Nemotron, Llama 3) with a deterministic, local poker and joker scoring simulator, giving models full real-time game telemetry, boss stress-testing, and combinatorial evaluation without hallucinated moves.

---

## 🌟 Key Capabilities

### 1. 🧮 Deterministic Poker & Joker Scoring Engine
- **Combinatorial Hand Evaluator**: Automatically finds and ranks the top legal hands (Straight Flush, Flush, Full House, Four of a Kind, etc.).
- **Accurate Point Simulation**: Simulates base chips & mult, card enhancements (*Glass, Steel, Bonus, Mult, Lucky*), card editions (*Foil, Holographic, Polychrome*), and joker triggering sequences.
- **Boss Debuff Awareness**: Automatically zeroes out chips and mult for cards disabled by boss blinds (*The Goad, The Window, The Club, The Head, The Pillar*).

### 2. 🛡️ Boss Stress-Test & Reality Check
- **Boss Ceiling Reality Check**: Computes maximum possible damage output against the upcoming boss before combat starts.
- **Deficit & Pace Calculation**: Tells the agent the exact damage-per-hand pace required so it doesn't waste hands or play sub-optimally.
- **Deck Outs Tracker**: Displays live remaining cards categorized by suit and highlights dead / debuffed cards.

### 3. 🧠 Token-Dense Strategic Markdown Serializer
- Compresses large Lua state tables into concise, high-signal Markdown prompts.
- Emits real-time build alerts:
  - `⚠️ CHIP-STARVED ALERT`: Warns when high multipliers (+40 Mult, x2 Mult) are multiplying low base chips (+0 Bonus chips).
  - `⚠️ VOLATILITY WARNING`: Highlights risks with perishable or reset jokers (*Green Joker, Gros Michel, Ice Cream*).
  - `🌌 JOKER EDITION NOTICES`: Informs the model about Foil, Holographic, Polychrome, and Negative jokers without forcing hallucinations.

### 4. 🎙️ Post-Match Journalism & Financial Telemetry
- **Flash Interviews**: After every run (win or defeat), an AI sports reporter conducts a deep, in-character post-match interview with the runner analyzing key turning points, regrets, and missed synergies.
- **Cash Flow Tracking**: Complete transaction ledger records every dollar earned, interest gained, reroll penalty, and shop purchase to analyze economic discipline.
- **Run Lorebook**: Summarizes lessons learned and persists them across runs for long-term strategic evolution.

### 5. 🐕 Autonomous Watchdog Supervisor
- Monitors agent process stability.
- Automatically handles game pauses, animation delays, and network retries.
- 3-strikes restart mechanism with cooldown prevents runaway API token consumption.

---

## 🏗️ Architecture

```
BalatroLLM/
├── pyproject.toml              # Modern package manifest
├── requirements.txt            # Pinned dependencies
├── watchdog.py                 # Autonomous supervisor process
├── run_watchdog.bat            # One-click Windows runner
├── patch_apk.py                # Android APK patcher for the LuaSocket bridge
├── build_balatrollm_clone.py   # Isolated APK package cloner
├── lua_patch/
│   ├── balatro_rpc_bridge.lua  # Non-blocking LuaSocket RPC server for Balatro
│   └── README_PATCH.md         # Step-by-step mobile/desktop patching guide
├── knowledge/                  # Strategy guides (Bosses, Decks, Jokers, Synergies)
├── src/
│   ├── config.py               # Pydantic Settings & environment manager
│   ├── main.py                 # Rich interactive CLI entrypoint
│   ├── bridge/
│   │   ├── client.py           # Asynchronous JSON-RPC 2.0 TCP client with retries
│   │   └── schema.py           # Pydantic models for Cards, Jokers, Shop & Game State
│   ├── engine/
│   │   ├── scoring.py          # Deterministic combinatorial scoring engine
│   │   ├── serializer.py       # High-density Markdown prompt serializer
│   │   └── tools.py            # Strict Function Calling schemas for LLMs
│   └── agent/
│       ├── runner.py           # Finite State Machine (FSM) autonomous loop
│       ├── strategy.py         # Tactical rules, poker theory, and system prompts
│       ├── lorebook.py         # Cross-run memory & strategic adaptation
│       └── leaderboard.py      # SQLite analytics & run persistence
└── tests/                      # Comprehensive pytest test suite
```

---

## 🚀 Quick Start

### 1. Prerequisites

- Python 3.11+
- [Balatro](https://store.steampowered.com/app/2379780/Balatro/) (PC / Mac) or Balatro Mobile (Android)
- An API key for [OpenRouter](https://openrouter.ai/), [OpenAI](https://platform.openai.com/), or [Anthropic](https://console.anthropic.com/)

### 2. Installation

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/BalatroLLM.git
cd BalatroLLM

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration

Copy the example environment template:

```bash
cp .env.example .env
```

Edit `.env` with your preferred provider and credentials:

```env
# Bridge Connection (127.0.0.1 for local / Termux or phone IP over Wi-Fi)
BRIDGE_HOST=127.0.0.1
BRIDGE_PORT=12346

# LLM Provider Configuration
PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-your-key-here
MODEL=anthropic/claude-3.5-sonnet

# Agent Behavior
DRY_RUN=false
VERBOSE_COT=true
TEMPERATURE=0.2
```

### 4. Injecting the Lua Bridge

Follow the instructions in [`lua_patch/README_PATCH.md`](lua_patch/README_PATCH.md) to inject `balatro_rpc_bridge.lua` into your game:

- **Desktop (Steam / Love2D)**: Add `require("balatro_rpc_bridge")` inside `game.lua`.
- **Mobile (Android APK)**: Run `python build_balatrollm_clone.py` or use Termux / APKTool to inject the bridge and repack.

Forward the port over ADB if playing on a USB-connected phone:

```bash
adb forward tcp:12346 tcp:12346
```

### 5. Running the Agent

Launch the agent with the autonomous watchdog supervisor:

```bash
# Windows
run_watchdog.bat

# Linux / macOS
python watchdog.py
```

Or run the agent directly:

```bash
python -m src.main
```

---

## 🧪 Running Tests

BalatroLLM includes a comprehensive suite of unit tests validating hand evaluation, boss mechanics, edition multipliers, and tool guardrails:

```bash
pytest
```

---

## 📊 Telemetry & Reports

Every run automatically writes full telemetry to the `runs/` directory:
- `runs/run_<timestamp>.json`: Complete state dumps, transaction history, and metrics.
- `runs/run_<timestamp>_ante<N>.md`: Detailed round-by-round recap and after-match journalism interview.
- `runs/leaderboard.db`: SQLite database storing win/loss records, peak bank, and maximum hand scores.

---

## 📜 License

This project is licensed under the [MIT License](LICENSE). Balatro is a trademark of LocalThunk. This project is an unofficial research tool and is not affiliated with or endorsed by LocalThunk or Playstack.
