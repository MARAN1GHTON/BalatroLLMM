import re
import os
import sys
from pathlib import Path
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

runs_dir = Path(__file__).resolve().parent.parent / "runs"
# Reports live in per-model subdirectories: runs/<model>/<date>_<time>_ante<N>.md
# (optionally prefixed with "victory_"). Recurse so the analyzer actually finds them.
files = sorted(runs_dir.glob("*/*.md"), key=os.path.getmtime, reverse=True)

reports_data = []

# Pattern to capture Section 4
sec4_pattern = re.compile(
    r"(?:###?\s*4[.:\)]|\*\*4[.:\)]|[#*]*\s*4\.\s*[^#\n]+(?:штаб|аналитик|инженер|инструмент|чег)[^\n]*)(.*?)(?:###?\s*5[.:\)]|\*\*5[.:\)]|##\s*🪙|##\s*📜|\Z)",
    re.DOTALL | re.IGNORECASE,
)

model_pattern = re.compile(r"-\s*\*\*Model\*\*:\s*`?([^`\n\r]+)`?")

themes = {
    "boss_debuff_and_counters": [
        "босс", "босса", "дебафф", "дебаффа", "the goad", "the needle", "the hook",
        "the window", "the flint", "инвалидация", "перечеркнуть", "пик мертв", "уязвимост"
    ],
    "deck_composition_live_odds": [
        "состав колоды", "сколько червей", "сколько бубен", "живых карт", "остаток колоды",
        "шанс добора", "вероятност", "таблиц", "аут", "аутов"
    ],
    "multi_hand_pace_and_deficit": [
        "темп", "дефицит", "запас мощности", "средний урон", "на руку", "три руки таким темпом",
        "прогноз по боссу", "потолок", "минимальный урон", "гарантированный урон"
    ],
    "joker_synergy_and_math_eval": [
        "сила джокеров", "реальная сила", "плоский мульт", "xmult", "калькулятор джокеров",
        "сравнени", "вклад джокера", "эффективност", "синерги"
    ],
    "shop_opportunity_cost_and_vouchers": [
        "цена упущенной", "director's cut", "ваучер", "реролл босса", "стоимость реролла",
        "пакет", "buffoon pack", "celestial pack", "процент", "сохранить деньги"
    ],
    "discard_management_warnings": [
        "сброс", "сбросов", "последний сброс", "сжег", "риск", "рыбалка", "не сбрасывай"
    ]
}

theme_counts = Counter()
theme_samples = {k: [] for k in themes}

for f in files:
    content = f.read_text(encoding="utf-8", errors="ignore")
    m_sec = sec4_pattern.search(content)
    if not m_sec:
        # Fallback: search for "Инструменты и штаб разработки" or "Чего не хватило"
        m_fallback = re.search(r"(?:Чего не хватило|Штаб разработки|Инструменты|Штабу).*?(?=Работа над ошибками|5\.|\Z)", content, re.DOTALL | re.IGNORECASE)
        sec_text = m_fallback.group(0) if m_fallback else ""
    else:
        sec_text = m_sec.group(1).strip()

    if not sec_text:
        continue

    m_mod = model_pattern.search(content)
    model = m_mod.group(1).strip() if m_mod else "Unknown"

    reports_data.append((f.name, model, sec_text))

    sec_lower = sec_text.lower()
    for theme_name, keywords in themes.items():
        if any(kw in sec_lower for kw in keywords):
            theme_counts[theme_name] += 1
            if len(theme_samples[theme_name]) < 4:
                theme_samples[theme_name].append((f.name, model, sec_text[:400]))

print(f"Total reports analyzed: {len(files)}")
print(f"Reports with Section 4 extracted: {len(reports_data)}")
print("\n=== TOP REQUESTED CAPABILITIES BY MODELS (Theme Frequency) ===")
for t_name, count in theme_counts.most_common():
    pct = (count / max(1, len(reports_data))) * 100
    print(f"- {t_name:<38}: {count:2d} times ({pct:.1f}%)")

print("\n" + "="*80)
print("=== SAMPLES OF WHAT SPECIFIC MODELS ASKED ===")
for fname, model, sec in reports_data[:6]:
    print(f"\n--- [{model}] in {fname} ---")
    lines = [l.strip() for l in sec.splitlines() if l.strip()]
    for l in lines[:10]:
        print(f"  > {l}")
