"""Automated, lightweight APK patcher for Balatro on resource-constrained machines."""

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
UNPACKED_DIR = BASE_DIR / "balatro_unpacked"
LUA_BRIDGE = BASE_DIR / "lua_patch" / "balatro_rpc_bridge.lua"
UNSIGNED_APK = BASE_DIR / "Balatro-v1.9-patched-unsigned.apk"


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


def patch_manifest(manifest_path: Path) -> None:
    print("\n[+] Patching AndroidManifest.xml...")
    content = manifest_path.read_text(encoding="utf-8")

    # Add INTERNET and ACCESS_NETWORK_STATE permissions if missing
    perms = ""
    if "android.permission.INTERNET" not in content:
        perms += '    <uses-permission android:name="android.permission.INTERNET" />\n'
    if "android.permission.ACCESS_NETWORK_STATE" not in content:
        perms += '    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />\n'

    if perms:
        content = re.sub(r"(<manifest[^>]*>)", r"\1\n" + perms, content, count=1)
        print("    -> Added network permissions.")

    # Add usesCleartextTraffic="true" to <application
    if "android:usesCleartextTraffic" not in content:
        content = re.sub(
            r"<application(\s+)",
            r'<application\1android:usesCleartextTraffic="true" ',
            content,
            count=1,
        )
        print("    -> Added android:usesCleartextTraffic='true'.")

    manifest_path.write_text(content, encoding="utf-8")


def patch_main_lua(main_lua_path: Path) -> None:
    print("\n[+] Injecting hooks into assets/main.lua...")
    content = main_lua_path.read_text(encoding="utf-8")

    if 'require("balatro_rpc_bridge")' in content:
        print("    -> Hook already present in main.lua.")
        return

    # Add require at top
    header = 'local BalatroBridge = require("balatro_rpc_bridge")\n'
    content = header + content

    # Hook love.load()
    if "function love.load()" in content:
        content = content.replace(
            "function love.load()",
            "function love.load()\n    BalatroBridge.init()",
            1,
        )
        print("    -> Injected BalatroBridge.init() into love.load().")
    else:
        print("    [!] Warning: 'function love.load()' not found exactly.")

    # Hook love.update( dt ) or love.update(dt)
    update_pat = re.compile(r"function\s+love\.update\s*\(\s*dt\s*\)")
    m = update_pat.search(content)
    if m:
        orig = m.group(0)
        content = content.replace(orig, f"{orig}\n    BalatroBridge.update()", 1)
        print("    -> Injected BalatroBridge.update() into love.update(dt).")
    else:
        print("    [!] Warning: 'love.update(dt)' pattern not found.")

    main_lua_path.write_text(content, encoding="utf-8")


def main() -> None:
    if not SRC_APK.exists():
        print(f"[!] Source APK not found: {SRC_APK}")
        sys.exit(1)

    # 1. Unpack APK in fast no-src mode (avoids decompiling DEX/smali)
    run_cmd(
        ["java", "-jar", str(APKTOOL_JAR), "d", "-s", "-f", str(SRC_APK), "-o", str(UNPACKED_DIR)],
        "Unpacking APK (no-src mode, lightweight on CPU/RAM)",
    )

    # 2. Patch AndroidManifest.xml
    manifest_file = UNPACKED_DIR / "AndroidManifest.xml"
    if manifest_file.exists():
        patch_manifest(manifest_file)
    else:
        print("[!] AndroidManifest.xml not found!")

    # 3. Copy Lua bridge into assets
    assets_dir = UNPACKED_DIR / "assets"
    shutil.copy(str(LUA_BRIDGE), str(assets_dir / "balatro_rpc_bridge.lua"))
    print(f"\n[+] Copied balatro_rpc_bridge.lua to {assets_dir}")

    # 4. Patch main.lua
    main_lua = assets_dir / "main.lua"
    if main_lua.exists():
        patch_main_lua(main_lua)
    else:
        print("[!] assets/main.lua not found!")

    # 5. Build modified APK
    run_cmd(
        ["java", "-jar", str(APKTOOL_JAR), "b", str(UNPACKED_DIR), "-o", str(UNSIGNED_APK)],
        "Building patched APK",
    )

    # 6. Sign and zip-align APK
    run_cmd(
        ["java", "-jar", str(SIGNER_JAR), "--apks", str(UNSIGNED_APK), "--allowResign"],
        "Signing and zip-aligning APK with debug key",
    )

    # 7. Clean up temp files
    print("\n[+] Cleaning up temporary unpack directory...")
    if UNPACKED_DIR.exists():
        shutil.rmtree(UNPACKED_DIR, ignore_errors=True)
    if UNSIGNED_APK.exists():
        UNSIGNED_APK.unlink(missing_ok=True)

    print("\n" + "=" * 60)
    print("SUCCESS! Patched and signed APK is ready:")
    signed_apk = BASE_DIR / "Balatro-v1.9-patched-aligned-debugSigned.apk"
    if signed_apk.exists():
        print(f"-> {signed_apk} ({signed_apk.stat().st_size // (1024*1024)} MB)")
    print("=" * 60)


if __name__ == "__main__":
    main()
