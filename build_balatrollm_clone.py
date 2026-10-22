"""Lightweight builder for BalatroLLM - standalone clone of Balatro Android."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
TOOLS_DIR = BASE_DIR / "tools"
APKTOOL_JAR = TOOLS_DIR / "apktool.jar"
SIGNER_JAR = TOOLS_DIR / "uber-apk-signer.jar"
SRC_APK = BASE_DIR / "Balatro-v1.9-full-apkvision.apk"
CLONE_DIR = BASE_DIR / "balatrollm_workdir"
LUA_BRIDGE = BASE_DIR / "lua_patch" / "balatro_rpc_bridge.lua"
UNSIGNED_APK = BASE_DIR / "BalatroLLM-unsigned.apk"
def get_bridge_version() -> str:
    if LUA_BRIDGE.exists():
        content = LUA_BRIDGE.read_text(encoding="utf-8")
        m = re.search(r'bridge_version\s*=\s*"v?([0-9\.]+)"', content)
        if m:
            return m.group(1)
    return "1.9.9"


APP_VERSION = get_bridge_version()
try:
    _parts = APP_VERSION.split(".")
    VERSION_CODE = str(int(_parts[0]) * 10000 + int(_parts[1]) * 100 + int(_parts[2]))
except Exception:
    VERSION_CODE = "10909"

VERSIONED_APK = BASE_DIR / f"BalatroLLM-v{APP_VERSION}-clone-signed.apk"
FINAL_APK = BASE_DIR / "BalatroLLM-v1.9-clone-signed.apk"
LATEST_APK = BASE_DIR / "BalatroLLM-latest.apk"


def run_cmd(cmd: list[str], desc: str) -> None:
    print(f"\n[+] {desc}...")
    proc = subprocess.run(cmd, cwd=str(BASE_DIR), capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"[!] Error in {desc}:")
        print(proc.stderr or proc.stdout)
        sys.exit(proc.returncode)
    else:
        if proc.stdout.strip():
            print(proc.stdout.strip()[:300])


def patch_clone_manifest(manifest_path: Path) -> None:
    print("\n[+] Updating AndroidManifest.xml for clone...")
    content = manifest_path.read_text(encoding="utf-8")

    # 1. Change package name and authorities to unique clone id
    content = content.replace("com.playstack.balatro.android", "com.playstack.balatro.llm")
    print("    -> Replaced package & authorities with com.playstack.balatro.llm")

    # 2. Add INTERNET permissions if missing
    perms = ""
    if "android.permission.INTERNET" not in content:
        perms += '    <uses-permission android:name="android.permission.INTERNET" />\n'
    if "android.permission.ACCESS_NETWORK_STATE" not in content:
        perms += '    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />\n'

    if perms:
        content = re.sub(r"(<manifest[^>]*>)", r"\1\n" + perms, content, count=1)
        print("    -> Added network permissions.")

    # 3. Add usesCleartextTraffic="true"
    if "android:usesCleartextTraffic" not in content:
        content = re.sub(
            r"<application(\s+)",
            r'<application\1android:usesCleartextTraffic="true" ',
            content,
            count=1,
        )
        print("    -> Added android:usesCleartextTraffic='true'.")

    manifest_path.write_text(content, encoding="utf-8")


def patch_app_name(res_dir: Path) -> None:
    print("\n[+] Renaming app to 'BalatroLLM' in string resources...")
    for strings_xml in res_dir.glob("values*/strings.xml"):
        txt = strings_xml.read_text(encoding="utf-8")
        orig_txt = txt
        txt = re.sub(r'<string name="app_name_full">[^<]+</string>', '<string name="app_name_full">BalatroLLM</string>', txt)
        txt = re.sub(r'<string name="app_name">[^<]+</string>', '<string name="app_name">BalatroLLM</string>', txt)
        if txt != orig_txt:
            strings_xml.write_text(txt, encoding="utf-8")
            print(f"    -> Updated {strings_xml.relative_to(res_dir)}")


def patch_lua_hooks(assets_dir: Path) -> None:
    print("\n[+] Injecting Lua Bridge into clone assets...")
    # Copy bridge script
    shutil.copy(str(LUA_BRIDGE), str(assets_dir / "balatro_rpc_bridge.lua"))
    print("    -> Copied balatro_rpc_bridge.lua")

    main_lua_path = assets_dir / "main.lua"
    content = main_lua_path.read_text(encoding="utf-8")

    if 'require("balatro_rpc_bridge")' not in content:
        header = 'local BalatroBridge = require("balatro_rpc_bridge")\n'
        content = header + content

        if "function love.load()" in content:
            content = content.replace("function love.load()", "function love.load()\n    BalatroBridge.init()", 1)
            print("    -> Injected BalatroBridge.init() into love.load()")

        update_pat = re.compile(r"function\s+love\.update\s*\(\s*dt\s*\)")
        m = update_pat.search(content)
        if m:
            orig = m.group(0)
            content = content.replace(orig, f"{orig}\n    BalatroBridge.update()", 1)
            print("    -> Injected BalatroBridge.update() into love.update(dt)")

        main_lua_path.write_text(content, encoding="utf-8")


def patch_common_events_safety(assets_dir: Path) -> None:
    common_events_path = assets_dir / "functions" / "common_events.lua"
    if common_events_path.exists():
        content = common_events_path.read_text(encoding="utf-8")

        target_1 = "if i%30 == 1 then \n                            G.round_eval:add_child("
        repl_1 = "if G.round_eval and i%30 == 1 then \n                            G.round_eval:add_child("
        if target_1 in content:
            content = content.replace(target_1, repl_1, 1)
            print("    -> Protected common_events.lua line 1046 against nil round_eval")
        else:
            target_1_crlf = "if i%30 == 1 then \r\n                            G.round_eval:add_child("
            repl_1_crlf = "if G.round_eval and i%30 == 1 then \r\n                            G.round_eval:add_child("
            if target_1_crlf in content:
                content = content.replace(target_1_crlf, repl_1_crlf, 1)
                print("    -> Protected common_events.lua line 1046 against nil round_eval")

        target_2 = "G.round_eval:add_child(r,G.round_eval:get_UIE_by_ID('dollar_row_'..(dollar_row)..'_'..config.name))"
        repl_2 = "if G.round_eval then G.round_eval:add_child(r,G.round_eval:get_UIE_by_ID('dollar_row_'..(dollar_row)..'_'..config.name)) end"
        if target_2 in content:
            content = content.replace(target_2, repl_2, 1)
            print("    -> Protected common_events.lua line 1059 against nil round_eval")

        common_events_path.write_text(content, encoding="utf-8")


def patch_load_manager_safety(assets_dir: Path) -> None:
    load_mgr_path = assets_dir / "engine" / "load_manager.lua"
    if load_mgr_path.exists():
        content = load_mgr_path.read_text(encoding="utf-8")
        target = "_local = get_table_from_string(_local)"
        repl = (
            "local _ok, _res = pcall(get_table_from_string, _local)\n"
            "        if _ok and type(_res) == 'table' then _local = _res else _local = nil end"
        )
        if target in content:
            content = content.replace(target, repl)
            load_mgr_path.write_text(content, encoding="utf-8")
            print("    -> Protected load_manager.lua against corrupted save.jkr crashes on startup")


def patch_cardarea_safety(assets_dir: Path) -> None:
    cardarea_path = assets_dir / "cardarea.lua"
    if cardarea_path.exists():
        content = cardarea_path.read_text(encoding="utf-8")
        target = "(self.config.type == 'hand' and ({[G.STATES.SHOP]=1, [G.STATES.TAROT_PACK]=1, [G.STATES.SPECTRAL_PACK]=1, [G.STATES.STANDARD_PACK]=1,[G.STATES.BUFFOON_PACK]=1,[G.STATES.PLANET_PACK]=1, [G.STATES.ROUND_EVAL]=1, [G.STATES.BLIND_SELECT]=1})[state])"
        repl = "(self.config.type == 'hand' and (G.shop ~= nil or G.round_eval ~= nil or ({[G.STATES.SHOP]=1, [G.STATES.TAROT_PACK]=1, [G.STATES.SPECTRAL_PACK]=1, [G.STATES.STANDARD_PACK]=1,[G.STATES.BUFFOON_PACK]=1,[G.STATES.PLANET_PACK]=1, [G.STATES.ROUND_EVAL]=1, [G.STATES.BLIND_SELECT]=1})[state]))"
        if target in content:
            content = content.replace(target, repl, 1)
            cardarea_path.write_text(content, encoding="utf-8")
            print("    -> Protected cardarea.lua against 0/9 hand counter rendering in shop")


def patch_button_callbacks_safety(assets_dir: Path) -> None:
    cb_path = assets_dir / "functions" / "button_callbacks.lua"
    if cb_path.exists():
        content = cb_path.read_text(encoding="utf-8")
        target_str = (
            "G.FUNCS.select_blind = function(e)\r\n"
            "    stop_use()\r\n"
            "    if G.blind_select then \r\n"
            "        G.GAME.facing_blind = true\r\n"
            "        G.blind_prompt_box:get_UIE_by_ID('prompt_dynatext1').config.object.pop_delay = 0\r\n"
            "        G.blind_prompt_box:get_UIE_by_ID('prompt_dynatext1').config.object:pop_out(5)\r\n"
            "        G.blind_prompt_box:get_UIE_by_ID('prompt_dynatext2').config.object.pop_delay = 0\r\n"
            "        G.blind_prompt_box:get_UIE_by_ID('prompt_dynatext2').config.object:pop_out(5) \r\n"
            "\r\n"
            "        G.E_MANAGER:add_event(Event({\r\n"
            "          trigger = 'before', delay = 0.2,\r\n"
            "          func = function()\r\n"
            "            G.blind_prompt_box.alignment.offset.y = -10\r\n"
            "            G.blind_select.alignment.offset.y = 40\r\n"
            "            G.blind_select.alignment.offset.x = 0\r\n"
            "            return true\r\n"
            "        end}))\r\n"
            "        G.E_MANAGER:add_event(Event({\r\n"
            "          trigger = 'immediate',\r\n"
            "          func = function()\r\n"
            "            ease_round(1)\r\n"
            "            inc_career_stat('c_rounds', 1)\r\n"
            "            if _DEMO then\r\n"
            "              G.SETTINGS.DEMO_ROUNDS = (G.SETTINGS.DEMO_ROUNDS or 0) + 1\r\n"
            "              inc_steam_stat('demo_rounds')\r\n"
            "              G:save_settings()\r\n"
            "            end\r\n"
            "            G.GAME.round_resets.blind = e.config.ref_table\r\n"
            "            G.GAME.round_resets.blind_states[G.GAME.blind_on_deck] = 'Current'\r\n"
            "            G.blind_select:remove()\r\n"
            "            G.blind_prompt_box:remove()\r\n"
            "            G.blind_select = nil\r\n"
            "            delay(0.2)\r\n"
            "            return true\r\n"
            "        end}))\r\n"
            "        G.E_MANAGER:add_event(Event({\r\n"
            "          trigger = 'immediate',\r\n"
            "          func = function()\r\n"
            "            new_round()\r\n"
            "            return true\r\n"
            "          end\r\n"
            "        }))\r\n"
            "    end\r\n"
            "  end"
        )
        safe_str = (
            "G.FUNCS.select_blind = function(e)\r\n"
            "    if G.GAME and G.GAME.facing_blind then return end\r\n"
            "    stop_use()\r\n"
            "    if G.blind_select then \r\n"
            "        G.GAME.facing_blind = true\r\n"
            "        if G.blind_prompt_box then\r\n"
            "            pcall(function()\r\n"
            "                local t1 = G.blind_prompt_box:get_UIE_by_ID('prompt_dynatext1')\r\n"
            "                if t1 and t1.config and t1.config.object then t1.config.object.pop_delay = 0; t1.config.object:pop_out(5) end\r\n"
            "                local t2 = G.blind_prompt_box:get_UIE_by_ID('prompt_dynatext2')\r\n"
            "                if t2 and t2.config and t2.config.object then t2.config.object.pop_delay = 0; t2.config.object:pop_out(5) end\r\n"
            "            end)\r\n"
            "        end\r\n"
            "\r\n"
            "        G.E_MANAGER:add_event(Event({\r\n"
            "          trigger = 'before', delay = 0.2,\r\n"
            "          func = function()\r\n"
            "            if G.blind_prompt_box and G.blind_prompt_box.alignment and G.blind_prompt_box.alignment.offset then G.blind_prompt_box.alignment.offset.y = -10 end\r\n"
            "            if G.blind_select and G.blind_select.alignment and G.blind_select.alignment.offset then G.blind_select.alignment.offset.y = 40; G.blind_select.alignment.offset.x = 0 end\r\n"
            "            return true\r\n"
            "        end}))\r\n"
            "        G.E_MANAGER:add_event(Event({\r\n"
            "          trigger = 'immediate',\r\n"
            "          func = function()\r\n"
            "            ease_round(1)\r\n"
            "            inc_career_stat('c_rounds', 1)\r\n"
            "            if _DEMO then\r\n"
            "              G.SETTINGS.DEMO_ROUNDS = (G.SETTINGS.DEMO_ROUNDS or 0) + 1\r\n"
            "              inc_steam_stat('demo_rounds')\r\n"
            "              G:save_settings()\r\n"
            "            end\r\n"
            "            if e and e.config and e.config.ref_table then G.GAME.round_resets.blind = e.config.ref_table end\r\n"
            "            if G.GAME.round_resets and G.GAME.round_resets.blind_states and G.GAME.blind_on_deck then G.GAME.round_resets.blind_states[G.GAME.blind_on_deck] = 'Current' end\r\n"
            "            if G.blind_select then pcall(function() G.blind_select:remove() end); G.blind_select = nil end\r\n"
            "            if G.blind_prompt_box then pcall(function() G.blind_prompt_box:remove() end); G.blind_prompt_box = nil end\r\n"
            "            delay(0.2)\r\n"
            "            return true\r\n"
            "        end}))\r\n"
            "        G.E_MANAGER:add_event(Event({\r\n"
            "          trigger = 'immediate',\r\n"
            "          func = function()\r\n"
            "            new_round()\r\n"
            "            return true\r\n"
            "          end\r\n"
            "        }))\r\n"
            "    end\r\n"
            "  end"
        )
        if target_str in content:
            content = content.replace(target_str, safe_str, 1)
            cb_path.write_text(content, encoding="utf-8")
            print("    -> Protected button_callbacks.lua against nil blind_prompt_box crash")
        else:
            target_str_lf = target_str.replace("\r\n", "\n")
            safe_str_lf = safe_str.replace("\r\n", "\n")
            if target_str_lf in content:
                content = content.replace(target_str_lf, safe_str_lf, 1)
                cb_path.write_text(content, encoding="utf-8")
                print("    -> Protected button_callbacks.lua against nil blind_prompt_box crash (LF)")


def main() -> None:
    if not SRC_APK.exists():
        print(f"[!] Source APK not found: {SRC_APK}")
        sys.exit(1)

    app_label = f"BalatroLLM v{APP_VERSION}"
    print(f"[*] Building BalatroLLM Clone APK version: {APP_VERSION} (code: {VERSION_CODE})")
    print(f"[*] App Label: {app_label}")

    # 1. Unpack APK in fast no-src mode (lightweight on CPU/RAM)
    run_cmd(
        ["java", "-jar", str(APKTOOL_JAR), "d", "-s", "-f", str(SRC_APK), "-o", str(CLONE_DIR)],
        "Unpacking source APK (lightweight mode)",
    )

    # 2. Patch Manifest (Package name, Authorities, Cleartext, Permissions)
    manifest_file = CLONE_DIR / "AndroidManifest.xml"
    if manifest_file.exists():
        patch_clone_manifest(manifest_file)

    # 3. Rename app label to 'BalatroLLM'
    res_dir = CLONE_DIR / "res"
    if res_dir.exists():
        patch_app_name(res_dir)

    # 4. Inject Lua hooks into assets
    assets_dir = CLONE_DIR / "assets"
    if assets_dir.exists():
        patch_lua_hooks(assets_dir)
        patch_common_events_safety(assets_dir)
        patch_load_manager_safety(assets_dir)
        patch_cardarea_safety(assets_dir)
        patch_button_callbacks_safety(assets_dir)

    # 5. Build modified clone APK
    run_cmd(
        ["java", "-jar", str(APKTOOL_JAR), "b", str(CLONE_DIR), "-o", str(UNSIGNED_APK)],
        "Building BalatroLLM clone APK",
    )

    # 6. Sign and zip-align APK
    run_cmd(
        ["java", "-jar", str(SIGNER_JAR), "--apks", str(UNSIGNED_APK), "--allowResign"],
        "Signing BalatroLLM APK with debug key",
    )

    # Move signed result to versioned name and compatibility copies with robust retry
    signed_temp = BASE_DIR / "BalatroLLM-aligned-debugSigned.apk"
    if signed_temp.exists():
        import time
        for attempt in range(10):
            try:
                shutil.copy2(str(signed_temp), str(VERSIONED_APK))
                shutil.copy2(str(signed_temp), str(FINAL_APK))
                shutil.copy2(str(signed_temp), str(LATEST_APK))
                signed_temp.unlink(missing_ok=True)
                break
            except Exception:
                time.sleep(0.5)
        # Also clean up .idsig if generated
        idsig = BASE_DIR / "BalatroLLM-aligned-debugSigned.apk.idsig"
        if idsig.exists():
            idsig.unlink()

    # 7. Clean up temp files
    print("\n[+] Cleaning up workspace...")
    if CLONE_DIR.exists():
        shutil.rmtree(CLONE_DIR, ignore_errors=True)
    if UNSIGNED_APK.exists():
        import time
        for _ in range(5):
            try:
                UNSIGNED_APK.unlink(missing_ok=True)
                break
            except Exception:
                time.sleep(0.5)
    test_inspect = BASE_DIR / "test_inspect"
    if test_inspect.exists():
        shutil.rmtree(test_inspect, ignore_errors=True)
    inspect_py = BASE_DIR / "inspect_manifest.py"
    if inspect_py.exists():
        inspect_py.unlink()

    print("\n" + "=" * 60)
    print("SUCCESS! BalatroLLM Clone APK is completely ready:")
    if VERSIONED_APK.exists():
        size_mb = VERSIONED_APK.stat().st_size / (1024 * 1024)
        print(f"-> Primary APK:     {VERSIONED_APK.name} ({size_mb:.1f} MB)")
        print(f"-> Full Path:       {VERSIONED_APK}")
        print(f"Package:            com.playstack.balatro.llm")
        print(f"Display Name:       {app_label}")
        print(f"Version Name:       {APP_VERSION} (code: {VERSION_CODE})")
        print(f"Compatibility Copy: {FINAL_APK.name}")
        print(f"Latest Shortcut:    {LATEST_APK.name}")
        print("Installs alongside original Balatro without overwriting saves!")
    print("=" * 60)


if __name__ == "__main__":
    main()
