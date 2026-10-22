---
id: joker_blueprint
title: "📐 Джокер #12: Чертеж (Blueprint)"
category: "jokers"
keys: ["blueprint", "чертеж", "чертёж", "копия правого джокера", "j_blueprint"]
match_all: false
stages: ["SELECTING_HAND", "PLAYING_HAND", "SHOP"]
min_ante: 1
priority: 100
---
### Механика (Лучший Джокер в игре — Приоритет 100):
- Полностью копирует способность **джокера, стоящего непосредственно СПРАВА от него (`index + 1`)**!

### Что копирует, а что НЕТ (Совместимость):
- **Копирует (`Compatible`)**: все боевые `+Chips / +Mult / X-Mult`, ретриггеры (**Chad, Sock, Mime, Hack**), генераторы начала боя (**Burglar, Marble, Certificate**), **DNA**, **Perkeo**, **Hallucination**, **Burnt Joker**.
- **НЕ копирует (`Incompatible`)**: пассивные модификаторы правил (**Four Fingers, Shortcut, Pareidolia, Oops! All 6s, Smeared, Splash**) и выплаты в самом конце раунда (**Golden Joker, Cloud 9, Rocket, Satellite, To the Moon**), а также саморазрушение (**Mr. Bones, Шестое чувство**).
- Двигай Чертеж между фазами раунда для тройной выгоды!
