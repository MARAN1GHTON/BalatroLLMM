---
id: hand_economy_digging
title: "🃏 Концепция: Экономика Рук и Псевдо-Сбросы (Hands as Discards & Money)"
category: "concepts"
keys: ["hands", "discards", "delayed gratification", "burglar", "руки", "сбросы", "экономия рук"]
match_all: false
stages: ["SELECTING_HAND", "PLAYING_HAND"]
min_ante: 1
priority: 87
---
### Двойная роль рук (`hands_left`):
1. **Остаток рук = Деньги**: каждая неиспользованная рука приносит **+$1** в конце боя. Убить блайнд за 1 удар выгоднее на $2–$3, чем за 3–4 слабые руки.
2. **Руки как сбросы**: при `discards_left == 0` сыграй до 5 ненужных карт через `play_hand`, чтобы обновить руку из колоды.

### Когда ВЫГОДНО намеренно тянуть бой:
- Если у тебя есть растущие от рук джокеры (**Green Joker, Ride the Bus, Square Joker, Supernova, Spare Trousers, Obelisk, Vampire**) или генераторы денег (**Golden Ticket, Vagabond**): играй слабые руки первыми ходами, чтобы набить стаки перед финальным ударом!
