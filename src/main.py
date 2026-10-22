"""CLI Entrypoint for Balatro Autonomous LLM Agent."""

import argparse
import asyncio
import logging
import signal
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from rich.console import Console
from rich.panel import Panel

from src.config import settings
from src.agent.runner import BalatroAgentRunner

console = Console()


def parse_args() -> argparse.Namespace:
    """Parse CLI command line flags."""
    parser = argparse.ArgumentParser(
        prog="balatro-agent",
        description="Autonomous LLM Agent playing mobile & desktop Balatro via JSON-RPC bridge",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=settings.BRIDGE_HOST,
        help=f"Bridge server host (default: {settings.BRIDGE_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=settings.BRIDGE_PORT,
        help=f"Bridge server port (default: {settings.BRIDGE_PORT})",
    )
    parser.add_argument(
        "--provider",
        type=str,
        choices=["openai", "anthropic", "openrouter", "custom"],
        default=settings.PROVIDER,
        help=f"LLM API provider: openai, anthropic, openrouter, custom (default: {settings.PROVIDER})",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=settings.MODEL,
        help=f"LLM model identifier (default: {settings.MODEL})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=settings.DRY_RUN,
        help="Simulate and print actions without dispatching to game",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=settings.POLL_INTERVAL,
        help=f"Polling interval in seconds (default: {settings.POLL_INTERVAL})",
    )
    return parser.parse_args()


async def async_main() -> None:
    """Async application bootstrap and signal handling."""
    args = parse_args()

    # Apply CLI overrides to settings
    settings.BRIDGE_HOST = args.host
    settings.BRIDGE_PORT = args.port
    settings.PROVIDER = args.provider
    settings.MODEL = args.model
    settings.DRY_RUN = args.dry_run
    settings.POLL_INTERVAL = args.interval

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    console.print(
        Panel.fit(
            f"[bold magenta]🃏 BALATROLLM MOBILE / DESKTOP AGENT[/bold magenta]\n"
            f"[cyan]Target Bridge:[/cyan] {settings.BRIDGE_HOST}:{settings.BRIDGE_PORT}\n"
            f"[cyan]LLM Provider:[/cyan] {settings.PROVIDER} | [cyan]Model:[/cyan] {settings.MODEL}\n"
            f"[cyan]Dry Run:[/cyan] {settings.DRY_RUN}",
            border_style="magenta",
        )
    )

    runner = BalatroAgentRunner()

    # Register signal handlers for clean exit
    loop = asyncio.get_running_loop()

    def handle_shutdown_signal() -> None:
        console.print("\n[yellow]Shutdown signal received. Stopping agent...[/yellow]")
        runner.stop()

    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, handle_shutdown_signal)

    try:
        await runner.run()
    except KeyboardInterrupt:
        console.print("\n[yellow]Keyboard interrupt. Exiting...[/yellow]")
        runner.stop()


def main() -> None:
    """Synchronous console entrypoint."""
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
