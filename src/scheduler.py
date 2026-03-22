# src/scheduler.py - APScheduler-based job scheduler for periodic fetching
"""Scheduler for periodic Vinted fetching and notification jobs."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

if TYPE_CHECKING:
    from src.config import Config, FilterConfig
    from src.fetcher import VintedFetcher
    from src.notifier import NotificationManager
    from src.storage import Storage

logger = logging.getLogger(__name__)


@dataclass
class FetchStats:
    """Statistics for a single fetch cycle."""

    filter_name: str
    items_found: int = 0
    items_new: int = 0
    items_matched: int = 0
    notifications_sent: int = 0
    duration_ms: float = 0
    error: str | None = None


class VintedScheduler:
    """Scheduler for periodic Vinted monitoring.

    Manages scheduled jobs for each configured filter, handles concurrent
    fetching with limits, and coordinates with notification and storage layers.
    """

    def __init__(
        self,
        config: Config,
        fetcher: VintedFetcher,
        storage: Storage,
        notifier: NotificationManager,
    ):
        """Initialize scheduler.

        Args:
            config: Application configuration
            fetcher: VintedFetcher instance
            storage: Storage instance
            notifier: NotificationManager instance
        """
        self.config = config
        self.fetcher = fetcher
        self.storage = storage
        self.notifier = notifier

        self._scheduler = AsyncIOScheduler()
        self._semaphore = asyncio.Semaphore(config.app.scheduler.max_concurrent_fetches)
        self._running = False
        self._fetch_tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """Start the scheduler with all configured jobs."""
        if self._running:
            logger.warning("Scheduler already running")
            return

        logger.info("Starting Vinted scheduler...")

        # Connect storage and fetcher
        await self.storage.connect()
        await self.fetcher.start()

        # Schedule periodic fetches for each filter
        for filter_config in self.config.app.filters:
            if not filter_config.enabled:
                logger.debug(f"Filter '{filter_config.name}' is disabled, skipping")
                continue

            self._scheduler.add_job(
                self._fetch_job,
                trigger=IntervalTrigger(seconds=self.config.app.scheduler.interval_seconds),
                args=[filter_config],
                id=f"fetch_{filter_config.name}",
                name=f"Fetch: {filter_config.name}",
                replace_existing=True,
                max_instances=1,
            )
            logger.info(
                f"Scheduled fetch job for '{filter_config.name}' "
                f"every {self.config.app.scheduler.interval_seconds}s"
            )

        # Schedule periodic cleanup
        self._scheduler.add_job(
            self._cleanup_job,
            trigger=IntervalTrigger(hours=6),
            id="cleanup",
            name="Cleanup old records",
            replace_existing=True,
        )

        self._scheduler.start()
        self._running = True

        # Run initial fetch after startup delay
        await asyncio.sleep(self.config.app.scheduler.startup_delay_seconds)
        await self.run_all_now()

        logger.info("Scheduler started successfully")

    async def stop(self) -> None:
        """Stop the scheduler and cleanup resources."""
        if not self._running:
            return

        logger.info("Stopping scheduler...")

        # Cancel any running fetch tasks
        for task in self._fetch_tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self._scheduler.shutdown(wait=False)
        self._running = False

        # Cleanup resources
        await self.fetcher.close()
        await self.storage.close()
        await self.notifier.close()

        logger.info("Scheduler stopped")

    async def run_all_now(self) -> list[FetchStats]:
        """Run all fetch jobs immediately.

        Returns:
            List of FetchStats for each filter
        """
        logger.info("Running all fetch jobs now...")

        tasks = []
        for filter_config in self.config.app.filters:
            if filter_config.enabled:
                task = asyncio.create_task(self._fetch_job(filter_config))
                tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        stats = []
        for result in results:
            if isinstance(result, FetchStats):
                stats.append(result)
            elif isinstance(result, Exception):
                logger.error(f"Fetch job failed: {result}")

        return stats

    async def run_filter_now(self, filter_name: str) -> FetchStats | None:
        """Run a specific filter's fetch job immediately.

        Args:
            filter_name: Name of the filter to run

        Returns:
            FetchStats if filter found and ran, None otherwise
        """
        for filter_config in self.config.app.filters:
            if filter_config.name == filter_name:
                return await self._fetch_job(filter_config)

        logger.warning(f"Filter not found: {filter_name}")
        return None

    async def _fetch_job(self, filter_config: FilterConfig) -> FetchStats:
        """Execute a fetch job for a single filter.

        Args:
            filter_config: Filter configuration to use

        Returns:
            FetchStats with results
        """
        from src.filters import FilterEngine, apply_filters
        from src.parser import get_parser

        stats = FetchStats(filter_name=filter_config.name)
        start_time = time.monotonic()

        try:
            async with self._semaphore:
                logger.debug(f"Starting fetch for filter '{filter_config.name}'")

                # Determine fetch method based on config
                if filter_config.search_url:
                    # Use provided search URL
                    result = await self.fetcher.fetch_html_search(filter_config.search_url)
                else:
                    # Build API query from filter config
                    result = await self.fetcher.fetch_search(
                        domain=self.config.app.default_domain,
                        search_text=(
                            filter_config.keywords[0] if filter_config.keywords else None
                        ),
                        catalog_ids=(
                            [filter_config.catalog_id] if filter_config.catalog_id else None
                        ),
                        price_from=filter_config.price_min,
                        price_to=filter_config.price_max,
                        currency=filter_config.currency,
                    )

                # Log fetch result details
                logger.debug(
                    f"Fetch result: url={result.url}, status={result.status_code}, "
                    f"content_type={result.content_type}, content_length={len(result.content)}"
                )

                # Parse response
                parser = get_parser(result.content_type)
                base_url = f"https://{self.config.app.default_domain}"
                parse_result = parser.parse(result.content, base_url)

                stats.items_found = len(parse_result.items)

                if parse_result.parse_errors:
                    logger.warning(
                        f"Parse errors for '{filter_config.name}': {parse_result.parse_errors}"
                    )

                # Filter out seen items
                unseen_items = await self.storage.get_unseen_items(
                    parse_result.items, filter_config.name
                )
                stats.items_new = len(unseen_items)

                # Apply filter criteria
                matches = apply_filters(unseen_items, [filter_config])
                stats.items_matched = len(matches)

                logger.info(
                    f"Filter '{filter_config.name}': found={stats.items_found}, "
                    f"new={stats.items_new}, matched={stats.items_matched}"
                )

                # Send notifications for matches
                for item, filter_result in matches:
                    # Determine which backends to use
                    backends = []
                    if filter_config.notify_discord:
                        backends.append("discord")

                    results = await self.notifier.notify(
                        item=item,
                        filter_result=filter_result,
                        backends=backends if backends else None,
                        dry_run=self.config.env.dry_run,
                    )

                    # Mark as seen and notified
                    notified = any(results.values())
                    await self.storage.mark_seen(item, filter_config.name, notified=notified)

                    if notified:
                        stats.notifications_sent += 1

        except Exception as e:
            stats.error = str(e)
            logger.error(f"Fetch job failed for '{filter_config.name}': {e}", exc_info=True)

        finally:
            stats.duration_ms = (time.monotonic() - start_time) * 1000

            # Log fetch to database
            await self.storage.log_fetch(
                filter_name=filter_config.name,
                items_found=stats.items_found,
                items_new=stats.items_new,
                items_matched=stats.items_matched,
                duration_ms=stats.duration_ms,
                error=stats.error,
            )

        return stats

    async def _cleanup_job(self) -> None:
        """Run periodic cleanup of old records."""
        try:
            deleted = await self.storage.cleanup_old_records()
            logger.info(f"Cleanup completed, removed {deleted} old records")
        except Exception as e:
            logger.error(f"Cleanup job failed: {e}")

    async def get_status(self) -> dict:
        """Get scheduler status and statistics.

        Returns:
            Dict with status information
        """
        storage_stats = await self.storage.get_stats()

        jobs_info = []
        for job in self._scheduler.get_jobs():
            jobs_info.append({
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            })

        return {
            "running": self._running,
            "jobs": jobs_info,
            "storage": storage_stats,
            "filters_count": len(self.config.app.filters),
            "enabled_filters": sum(1 for f in self.config.app.filters if f.enabled),
        }
