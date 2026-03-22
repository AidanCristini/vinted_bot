# src/main.py - Application entry point and CLI
"""Main entry point for Vinted Notifier application."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from src.config import Config, setup_logging
from src.fetcher import VintedFetcher
from src.notifier import NotificationManager
from src.scheduler import VintedScheduler
from src.storage import Storage

logger = logging.getLogger(__name__)


class Application:
    """Main application class."""

    def __init__(self, config: Config):
        self.config = config
        self.scheduler: VintedScheduler | None = None
        self._shutdown_event = asyncio.Event()

    async def run(self) -> None:
        """Run the application."""
        logger.info("Starting Vinted Notifier...")
        logger.info(f"Dry run mode: {self.config.env.dry_run}")
        logger.info(f"Loaded {len(self.config.app.filters)} filter(s)")

        # Initialize components
        fetcher = VintedFetcher(rate_limit_config=self.config.app.rate_limit)

        # Parse database path from URL
        db_url = self.config.env.database_url
        if db_url.startswith("sqlite"):
            db_path = db_url.split("///")[-1]
        else:
            db_path = "data/vinted.db"

        storage = Storage(db_path=db_path, config=self.config.app.storage)
        notifier = NotificationManager(self.config.env, self.config.app.notification)

        self.scheduler = VintedScheduler(
            config=self.config,
            fetcher=fetcher,
            storage=storage,
            notifier=notifier,
        )

        # Register signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._signal_handler)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                signal.signal(sig, lambda s, f: self._signal_handler())

        try:
            # Start scheduler
            await self.scheduler.start()

            # Wait for shutdown signal
            await self._shutdown_event.wait()

        except Exception as e:
            logger.error(f"Application error: {e}", exc_info=True)
            raise
        finally:
            if self.scheduler:
                await self.scheduler.stop()
            logger.info("Vinted Notifier stopped")

    def _signal_handler(self) -> None:
        """Handle shutdown signals."""
        logger.info("Shutdown signal received...")
        self._shutdown_event.set()


async def run_once(config: Config) -> None:
    """Run a single fetch cycle and exit.

    Useful for testing or cron-based scheduling.
    """
    logger.info("Running single fetch cycle...")

    fetcher = VintedFetcher(rate_limit_config=config.app.rate_limit)

    db_url = config.env.database_url
    if db_url.startswith("sqlite"):
        db_path = db_url.split("///")[-1]
    else:
        db_path = "data/vinted.db"

    storage = Storage(db_path=db_path, config=config.app.storage)
    notifier = NotificationManager(config.env, config.app.notification)

    scheduler = VintedScheduler(
        config=config,
        fetcher=fetcher,
        storage=storage,
        notifier=notifier,
    )

    try:
        await storage.connect()
        await fetcher.start()

        stats = await scheduler.run_all_now()

        # Print summary
        print("\n" + "=" * 60)
        print("FETCH SUMMARY")
        print("=" * 60)
        for stat in stats:
            print(f"\nFilter: {stat.filter_name}")
            print(f"  Items found: {stat.items_found}")
            print(f"  New items: {stat.items_new}")
            print(f"  Matched: {stat.items_matched}")
            print(f"  Notifications: {stat.notifications_sent}")
            print(f"  Duration: {stat.duration_ms:.1f}ms")
            if stat.error:
                print(f"  Error: {stat.error}")
        print("=" * 60 + "\n")

    finally:
        await fetcher.close()
        await storage.close()
        await notifier.close()


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Vinted Notifier - Monitor Vinted listings and get notified",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                    # Run with default config
  %(prog)s -c myconfig.yaml   # Run with custom config
  %(prog)s --once             # Run single fetch cycle and exit
  %(prog)s --dry-run          # Console output only (no notifications)
        """,
    )

    parser.add_argument(
        "-c", "--config",
        type=str,
        default=None,
        help="Path to configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run single fetch cycle and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print notifications to console instead of sending",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Logging level",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 1.0.0",
    )

    args = parser.parse_args()

    # Load configuration
    try:
        config = Config.load(args.config)
    except Exception as e:
        print(f"Error loading configuration: {e}", file=sys.stderr)
        return 1

    # Override with CLI arguments
    if args.dry_run:
        config.env.dry_run = True
    if args.log_level:
        config.env.log_level = args.log_level

    # Setup logging
    setup_logging(config.env.log_level)

    # Validate configuration
    if not config.app.filters:
        logger.error("No filters configured. Create a config file with at least one filter.")
        return 1

    enabled_filters = [f for f in config.app.filters if f.enabled]
    if not enabled_filters:
        logger.error("All filters are disabled. Enable at least one filter.")
        return 1

    # Check for keywords or search URL
    for f in enabled_filters:
        if not f.keywords and not f.search_url and not f.keywords_regex:
            logger.warning(
                f"Filter '{f.name}' has no keywords, regex, or search URL. "
                "It may match nothing or be very broad."
            )

    try:
        if args.once:
            asyncio.run(run_once(config))
        else:
            app = Application(config)
            asyncio.run(app.run())
        return 0
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 0
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
