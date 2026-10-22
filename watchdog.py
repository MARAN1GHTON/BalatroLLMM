"""Autonomous Watchdog Supervisor for BalatroLLM Agent.

Monitors the agent process. If the agent encounters 3 consecutive LLM errors/timeouts,
it automatically stops and restarts the process.
If 3 consecutive sessions each fail with 3 errors (3 strikes rule), it halts permanently.
"""

import os
import sys
import time
import subprocess
from datetime import datetime

MAX_RESTART_SESSIONS = 3
RESTART_COOLDOWN_SECONDS = 3.0


def log_watchdog(msg: str, style: str = "white") -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        from rich.console import Console
        from rich.panel import Panel
        console = Console()
        console.print(f"[bold cyan][Watchdog {timestamp}][/bold cyan] {msg}")
    except Exception:
        print(f"[Watchdog {timestamp}] {msg}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    cli_args = sys.argv[1:]
    cmd = [sys.executable, "-m", "src.main", *cli_args]

    log_watchdog("🐕 [bold green]Автономный супервизор BalatroLLM запущен![/bold green]")
    log_watchdog(
        f"Правило перезапуска: 3 ошибки LLM подряд -> перезапуск процесса (макс. {MAX_RESTART_SESSIONS} сессии подряд)."
    )

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    consecutive_failed_sessions = 0

    while consecutive_failed_sessions < MAX_RESTART_SESSIONS:
        session_num = consecutive_failed_sessions + 1
        log_watchdog(
            f"🚀 [bold yellow]Запуск сессии агента #{session_num}/{MAX_RESTART_SESSIONS}...[/bold yellow]"
        )

        try:
            start_time = time.time()
            # Run the agent directly with inherited stdio for full terminal interactivity & colors
            proc = subprocess.run(cmd, env=env)
            session_duration = time.time() - start_time
            exit_code = proc.returncode

            # Exit code 42 indicates 3 consecutive LLM failures
            if exit_code == 42:
                consecutive_failed_sessions += 1
                if consecutive_failed_sessions < MAX_RESTART_SESSIONS:
                    log_watchdog(
                        f"⚠️ [bold red]Сессия #{session_num} завершилась из-за 3 ошибок LLM подряд.[/bold red]"
                    )
                    log_watchdog(
                        f"🔄 Перезапуск агента через {RESTART_COOLDOWN_SECONDS:.0f} сек... (Попытка {consecutive_failed_sessions + 1}/{MAX_RESTART_SESSIONS})"
                    )
                    time.sleep(RESTART_COOLDOWN_SECONDS)
                    continue
                else:
                    log_watchdog(
                        f"🛑 [bold red]ЛИМИТ ИСЧЕРПАН:[/bold red] {MAX_RESTART_SESSIONS} сессии подряд завершились 3 ошибками LLM."
                    )
                    log_watchdog(
                        "🛑 Окончательная остановка программы. Проверьте стабильность провайдера API или уровень reasoning_effort."
                    )
                    sys.exit(42)

            elif exit_code == 0:
                log_watchdog("✅ [bold green]Сессия агента завершилась штатно.[/bold green]")
                sys.exit(0)

            elif exit_code in (-2, 130, 3221225786):  # Ctrl+C
                log_watchdog("🛑 [yellow]Агент остановлен пользователем (Ctrl+C). Завершение супервизора.[/yellow]")
                sys.exit(0)

            else:
                log_watchdog(f"ℹ️ Процесс агента завершился с кодом {exit_code}.")
                # If the session ran for more than 2 minutes, it was a healthy run that exited
                if session_duration > 120:
                    sys.exit(exit_code)
                else:
                    consecutive_failed_sessions += 1
                    if consecutive_failed_sessions < MAX_RESTART_SESSIONS:
                        log_watchdog(
                            f"🔄 Неожиданный сбой. Перезапуск через {RESTART_COOLDOWN_SECONDS:.0f} сек..."
                        )
                        time.sleep(RESTART_COOLDOWN_SECONDS)
                    else:
                        sys.exit(exit_code)

        except KeyboardInterrupt:
            log_watchdog("\n🛑 [yellow]Прерывание с клавиатуры (Ctrl+C). Остановка супервизора.[/yellow]")
            sys.exit(0)


if __name__ == "__main__":
    main()
