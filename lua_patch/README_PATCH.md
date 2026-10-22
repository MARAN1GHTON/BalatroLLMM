# Balatro Lua Bridge & Android APK Patching Guide

This document explains how to patch mobile Balatro on Android (specifically tested on flagship devices like **Samsung Galaxy S24 Ultra**) to enable the JSON-RPC autonomous control bridge over loopback (`127.0.0.1:12346`) or local Wi-Fi.

---

## 1. How Balatro Mobile Works Internally

Mobile Balatro is packaged using **LÖVE2D Android** (`love-android`). Inside the APK file:
- Native binaries: `lib/arm64-v8a/liblove.so` (LÖVE engine with bundled **LuaSocket**).
- Game source: `assets/game.love` (which is simply a standard ZIP archive containing all Lua source files: `main.lua`, `game.lua`, `engine/`, `functions/`).

Because `LuaSocket` is already compiled into the mobile LÖVE binary, we can listen on a TCP port without adding any external C libraries.

---

## 2. Patching the APK (Step-by-Step)

### Prerequisites (on PC or Termux)
- `apktool` (or 7-Zip/unzip for asset extraction)
- `uber-apk-signer` (or standard Android `apksigner`)
- Java JDK 17+

### Step 2.1: Decompile or Extract APK
```bash
# Using apktool:
apktool d Balatro.apk -o balatro_unpacked
```

### Step 2.2: Copy Bridge Script
Copy `balatro_rpc_bridge.lua` from this repository into the game's Lua directory:
```bash
cp balatro_rpc_bridge.lua balatro_unpacked/assets/
# Or if game.love is a zip inside assets:
# Unzip game.love, place balatro_rpc_bridge.lua at root of game.love, and re-zip.
```

### Step 2.3: Hook into `main.lua`
Open `main.lua` (in `balatro_unpacked/assets/` or inside `game.love`):

1. At the very top of `main.lua`, add:
```lua
local BalatroBridge = require("balatro_rpc_bridge")
```

2. Inside `love.load(args)` (around game startup), initialize the listener:
```lua
BalatroBridge.init()
```

3. Inside `love.update(dt)` (around line 200–300 where the main game tick updates), add:
```lua
BalatroBridge.update()
```

### Step 2.4: Enable Android Network Cleartext Traffic
Open `balatro_unpacked/AndroidManifest.xml`:
1. Ensure the internet permissions are present inside `<manifest>`:
```xml
<uses-permission android:name="android.permission.INTERNET" />
<uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />
```
2. In `<application>`, enable cleartext loopback traffic:
```xml
<application
    ...
    android:usesCleartextTraffic="true">
```

### Step 2.5: Rebuild and Sign the APK
```bash
apktool b balatro_unpacked -o Balatro_patched_unsigned.apk
java -jar uber-apk-signer.jar --apks Balatro_patched_unsigned.apk
```
This produces `Balatro_patched_unsigned-aligned-debugSigned.apk`.

Install it on your S24 Ultra:
```bash
adb install -r Balatro_patched_unsigned-aligned-debugSigned.apk
```

---

## 3. Running on Samsung Galaxy S24 Ultra

### Option A: Fully Standalone on Phone (via Termux)
1. Install **Termux** from F-Droid or GitHub Releases.
2. In Termux, install Python and dependencies:
   ```bash
   pkg update && pkg install python git
   git clone <your-repo> balatro_agent
   cd balatro_agent
   pip install -e .
   ```
3. Create `.env` file with your API key:
   ```bash
   echo "OPENAI_API_KEY=your_key_here" > .env
   # Or for Anthropic:
   # echo "PROVIDER=anthropic\nANTHROPIC_API_KEY=your_key" > .env
   ```
4. Launch the patched **Balatro** on your phone.
5. Use Samsung One UI **Split Screen** or **Pop-up View (Floating Window)** to keep both Balatro and Termux open simultaneously.
6. Run the agent in Termux:
   ```bash
   python -m src.main --host 127.0.0.1 --port 12346
   ```

### Option B: Remote PC / Laptop Control over ADB (Recommended for Testing)
1. Enable USB Debugging in Developer Options on your S24 Ultra.
2. Connect phone to PC via USB cable or wireless ADB:
   ```bash
   adb forward tcp:12346 tcp:12346
   ```
3. Run `balatro-agent` directly on your PC:
   ```bash
   python -m src.main --host 127.0.0.1 --port 12346
   ```
4. Balatro on your S24 Ultra will respond to actions sent over ADB reverse socket in real time.

---

# Руководство по модификации APK Balatro и внедрению Lua-моста (Русская версия)

В этой инструкции подробно описан процесс патчинга мобильной версии Balatro на Android (протестировано на флагманах, включая **Samsung Galaxy S24 Ultra**) для включения автономного моста управления по протоколу JSON-RPC через локальный сокет (`127.0.0.1:12346`) или локальную сеть Wi-Fi.

---

## 1. Как устроена мобильная версия Balatro внутри

Мобильная версия Balatro собрана на базе игрового движка **LÖVE2D для Android** (`love-android`). Внутри установочного APK-файла находятся:
- Нативные бинарные библиотеки: `lib/arm64-v8a/liblove.so` (движок LÖVE со встроенной библиотекой **LuaSocket**).
- Исходный код игры: `assets/game.love` (стандартный ZIP-архив, содержащий все Lua-скрипты: `main.lua`, `game.lua`, папки `engine/`, `functions/` и т.д.).

Так как библиотека `LuaSocket` уже скомпилирована внутри мобильного бинарника LÖVE, мы можем открыть TCP-порт для приёма команд без подключения сторонних нативных C-библиотек.

---

## 2. Пошаговый патчинг APK

### Предварительные требования (на ПК или в Termux)
- Утилита `apktool` (или архиватор 7-Zip / unzip для распаковки ресурсов)
- Утилита `uber-apk-signer` (или стандартный Android `apksigner`)
- Установленная Java JDK 17+

### Шаг 2.1: Декомпиляция или распаковка APK
```bash
# С помощью apktool:
apktool d Balatro.apk -o balatro_unpacked
```

### Шаг 2.2: Копирование скрипта моста
Скопируйте файл `balatro_rpc_bridge.lua` из этого репозитория в папку с Lua-скриптами игры:
```bash
cp balatro_rpc_bridge.lua balatro_unpacked/assets/
# Или если внутри assets лежит архив game.love:
# Распакуйте game.love, скопируйте balatro_rpc_bridge.lua в корень game.love и заархивируйте обратно в zip.
```

### Шаг 2.3: Внедрение хука в `main.lua`
Откройте файл `main.lua` (в `balatro_unpacked/assets/` или внутри распакованного `game.love`):

1. В самом верху файла добавьте строку подключения:
```lua
local BalatroBridge = require("balatro_rpc_bridge")
```

2. Внутри функции `love.load(args)` (в момент инициализации игры) запустите прослушивание порта:
```lua
BalatroBridge.init()
```

3. Внутри функции `love.update(dt)` (в главном игровом цикле, примерно строки 200–300), добавьте вызов обработки сообщений:
```lua
BalatroBridge.update()
```

### Шаг 2.4: Разрешение незашифрованного сетевого трафика (Cleartext)
Откройте файл `balatro_unpacked/AndroidManifest.xml`:
1. Убедитесь, что внутри тега `<manifest>` присутствуют разрешения на работу с сетью:
```xml
<uses-permission android:name="android.permission.INTERNET" />
<uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />
```
2. Внутри тега `<application>` разрешите незашифрованный локальный трафик:
```xml
<application
    ...
    android:usesCleartextTraffic="true">
```

### Шаг 2.5: Сборка и подпись APK
```bash
apktool b balatro_unpacked -o Balatro_patched_unsigned.apk
java -jar uber-apk-signer.jar --apks Balatro_patched_unsigned.apk
```
В результате будет создан подписанный файл `Balatro_patched_unsigned-aligned-debugSigned.apk`.

Установите его на ваш S24 Ultra:
```bash
adb install -r Balatro_patched_unsigned-aligned-debugSigned.apk
```

---

## 3. Запуск на Samsung Galaxy S24 Ultra

### Вариант А: Полностью автономно на телефоне (через Termux)
1. Установите **Termux** (рекомендуется сборка из F-Droid или GitHub Releases).
2. В Termux установите Python и необходимые зависимости:
   ```bash
   pkg update && pkg install python git
   git clone <репозиторий> balatro_agent
   cd balatro_agent
   pip install -e .
   ```
3. Создайте файл `.env` с вашим API-ключом:
   ```bash
   echo "OPENAI_API_KEY=ваш_ключ_здесь" > .env
   # Или для Anthropic Claude:
   # echo "PROVIDER=anthropic\nANTHROPIC_API_KEY=ваш_ключ" > .env
   ```
4. Запустите пропатченную игру **Balatro** на телефоне.
5. Используйте возможности оболочки Samsung One UI: **Разделение экрана (Split Screen)** или **Режим всплывающего окна (Pop-up View)**, чтобы Balatro и Termux были активны одновременно.
6. Запустите агента в Termux:
   ```bash
   python -m src.main --host 127.0.0.1 --port 12346
   ```

### Вариант Б: Запуск агента на ПК с управлением игрой на телефоне (через ADB)
1. Включите **Отладку по USB** в меню «Для разработчиков» на вашем S24 Ultra.
2. Подключите телефон к ПК по кабелю или по беспроводному ADB:
   ```bash
   adb forward tcp:12346 tcp:12346
   ```
3. Запустите `balatro-agent` прямо на компьютере:
   ```bash
   python -m src.main --host 127.0.0.1 --port 12346
   ```
4. Игра на вашем смартфоне будет автоматически выполнять ходы, которые агент рассчитывает и отправляет через мост в реальном времени.

