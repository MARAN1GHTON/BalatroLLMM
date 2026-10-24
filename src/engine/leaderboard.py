import sqlite3
import json
import re
from pathlib import Path
from typing import Any

def update_leaderboard_from_run(json_data: dict[str, Any], runs_dir: Path) -> dict[str, Any]:
    """Updates leaderboard.db and leaderboard.sql whenever a completed run is saved."""
    db_path = runs_dir / "leaderboard.db"
    sql_path = runs_dir / "leaderboard.sql"
    
    rid = json_data.get("run_id", "")
    ai_prof = json_data.get("ai_profile", {})
    model = ai_prof.get("model", "unknown")
    provider = ai_prof.get("provider", "unknown")
    start_time = json_data.get("start_time", "")
    end_time = json_data.get("end_time", "")
    victory = 1 if json_data.get("victory") else 0
    peak_bank = json_data.get("peak_bank", 0)
    total_hands = json_data.get("total_hands_played", len(json_data.get("played_hands", [])))
    total_discards = json_data.get("total_discards_used", 0)
    total_rerolls = json_data.get("total_rerolls", 0)
    max_score = json_data.get("max_single_hand_score", 0)
    
    max_ante = 1
    max_round = 1
    for s in json_data.get("steps", []):
        max_ante = max(max_ante, s.get("ante", 1))
        max_round = max(max_round, s.get("round", 1))
    for h in json_data.get("played_hands", []):
        max_ante = max(max_ante, h.get("ante", 1))
        max_round = max(max_round, h.get("round", 1))
        sc = h.get("score_gained", 0)
        if sc > max_score:
            max_score = sc
            
    token_usage = json_data.get("token_usage", {})
    total_tokens = token_usage.get("total_tokens", 0)
    prompt_tokens = token_usage.get("prompt_tokens", 0)
    completion_tokens = token_usage.get("completion_tokens", 0)
    reasoning_effort = ai_prof.get("reasoning_effort") or json_data.get("reasoning_effort") or "standard"
    json_filename = json_data.get("json_file") or (f"run_{rid}.json" if not victory else f"victory_run_{rid}.json")

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        model TEXT,
        provider TEXT,
        start_time TEXT,
        end_time TEXT,
        furthest_ante INTEGER,
        furthest_round INTEGER,
        victory INTEGER,
        max_single_hand_score INTEGER,
        peak_bank INTEGER,
        total_hands_played INTEGER,
        total_discards_used INTEGER,
        total_rerolls INTEGER,
        total_tokens INTEGER,
        prompt_tokens INTEGER,
        completion_tokens INTEGER,
        json_file TEXT,
        reasoning_effort TEXT DEFAULT 'standard'
    )
    """)
    
    # Auto-migrate if column missing in older DB
    cur.execute("PRAGMA table_info(runs)")
    cols = [col[1] for col in cur.fetchall()]
    if "reasoning_effort" not in cols:
        cur.execute("ALTER TABLE runs ADD COLUMN reasoning_effort TEXT DEFAULT 'standard'")
        conn.commit()
    
    cur.execute("""
    INSERT OR REPLACE INTO runs (
        run_id, model, provider, start_time, end_time,
        furthest_ante, furthest_round, victory, max_single_hand_score,
        peak_bank, total_hands_played, total_discards_used, total_rerolls,
        total_tokens, prompt_tokens, completion_tokens, json_file,
        reasoning_effort
    ) VALUES (
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
    )
    """, (
        rid, model, provider, start_time, end_time,
        max_ante, max_round, victory, max_score,
        peak_bank, total_hands, total_discards, total_rerolls,
        total_tokens, prompt_tokens, completion_tokens, json_filename,
        reasoning_effort
    ))
    conn.commit()
    
    # Export SQL dump
    with open(sql_path, "w", encoding="utf-8") as f:
        for line in conn.iterdump():
            f.write(line + "\n")
            
    # Calculate specific run rank across all historical runs
    cur.execute("""
    SELECT run_id, furthest_ante, furthest_round, max_single_hand_score
    FROM runs
    ORDER BY furthest_ante DESC, furthest_round DESC, max_single_hand_score DESC
    """)
    all_runs = cur.fetchall()
    run_pos = None
    for idx, r in enumerate(all_runs, 1):
        if r[0] == rid:
            run_pos = idx
            break

    # Calculate model tier rank grouped by model and reasoning effort
    cur.execute("""
    SELECT model, COALESCE(reasoning_effort, 'standard') as r_effort,
           MAX(furthest_ante) as b_ante, MAX(furthest_round) as b_rnd, MAX(max_single_hand_score) as b_sc
    FROM runs
    GROUP BY model, COALESCE(reasoning_effort, 'standard')
    ORDER BY b_ante DESC, b_rnd DESC, b_sc DESC
    """)
    ranks = cur.fetchall()
    conn.close()
    
    model_pos = None
    model_best_ante = max_ante
    model_best_rnd = max_round
    for idx, r in enumerate(ranks, 1):
        if r[0] == model and r[1] == reasoning_effort:
            model_pos = idx
            model_best_ante = r[2]
            model_best_rnd = r[3]
            break
            
    return {
        "model": model,
        "reasoning_effort": reasoning_effort,
        "model_display": f"{model} [{reasoning_effort}]",
        "run_id": rid,
        "run_rank": run_pos,
        "total_runs": len(all_runs),
        "rank": run_pos,
        "model_rank": model_pos,
        "total_models": len(ranks),
        "model_best_ante": model_best_ante,
        "model_best_round": model_best_rnd,
        "ante": max_ante,
        "round": max_round,
        "max_score": max_score,
    }


def render_leaderboard_table(runs_dir: Path | str | None = None) -> str:
    """Renders a formatted Markdown table of all models and their reasoning levels."""
    if runs_dir is None:
        runs_dir = Path(__file__).resolve().parent.parent.parent / "runs"
    db_path = Path(runs_dir) / "leaderboard.db"
    if not db_path.exists():
        return "_No leaderboard database found._"

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("""
    SELECT 
        model, 
        COALESCE(reasoning_effort, 'standard') as r_effort,
        COUNT(*) as runs_count,
        SUM(victory) as victories,
        MAX(furthest_ante) as best_ante, 
        MAX(furthest_round) as best_round, 
        MAX(max_single_hand_score) as peak_hand
    FROM runs
    GROUP BY model, COALESCE(reasoning_effort, 'standard')
    ORDER BY best_ante DESC, best_round DESC, peak_hand DESC
    """)
    rows = cur.fetchall()
    conn.close()

    lines = [
        "| № | Модель | Уровень рассуждений | Забегов | Побед | Лучший результат | Рекорд руки |",
        "|---|---|:---:|:---:|:---:|:---:|:---:|",
    ]
    for idx, (model, r_effort, runs_cnt, wins, b_ante, b_rnd, peak_h) in enumerate(rows, 1):
        lines.append(
            f"| **{idx}** | `{model}` | `{r_effort}` | {runs_cnt} | {wins} | **Ante {b_ante} — R{b_rnd}** | {peak_h:,} |"
        )
    return "\n".join(lines)
