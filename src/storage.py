# src/storage.py - SQLite storage for deduplication and persistence
"""Storage layer for tracking seen items and preventing duplicate notifications."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from src.config import StorageConfig
    from src.parser import VintedItem

logger = logging.getLogger(__name__)


class Storage:
    """Async SQLite storage for item deduplication.

    Tracks seen items with timestamps to implement notification cooldowns
    and prevent duplicate alerts.
    """

    def __init__(self, db_path: str | Path = "data/vinted.db", config: StorageConfig | None = None):
        """Initialize storage.

        Args:
            db_path: Path to SQLite database file
            config: Storage configuration (cooldown, cleanup settings)
        """
        from src.config import StorageConfig

        self.db_path = Path(db_path)
        self.config = config or StorageConfig()
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> Storage:
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    async def connect(self) -> None:
        """Connect to database and ensure schema exists."""
        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._create_schema()
        logger.info(f"Connected to database: {self.db_path}")

    async def close(self) -> None:
        """Close database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    async def _create_schema(self) -> None:
        """Create database tables if they don't exist."""
        if not self._db:
            raise RuntimeError("Database not connected")

        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen_items (
                item_id TEXT PRIMARY KEY,
                filter_name TEXT,
                title TEXT,
                price REAL,
                url TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                notified_at TEXT,
                notification_count INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_seen_items_last_seen
            ON seen_items(last_seen_at);

            CREATE INDEX IF NOT EXISTS idx_seen_items_filter
            ON seen_items(filter_name);

            CREATE TABLE IF NOT EXISTS fetch_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filter_name TEXT,
                fetched_at TEXT NOT NULL,
                items_found INTEGER,
                items_new INTEGER,
                items_matched INTEGER,
                duration_ms REAL,
                error TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_fetch_history_fetched_at
            ON fetch_history(fetched_at);
            """
        )
        await self._db.commit()

    async def is_seen(
        self, item_id: str, filter_name: str | None = None
    ) -> bool:
        """Check if an item has been seen recently.

        Args:
            item_id: Vinted item ID
            filter_name: Optional filter name for scoped lookup

        Returns:
            True if item was seen within cooldown period
        """
        if not self._db:
            raise RuntimeError("Database not connected")

        cooldown_threshold = datetime.now(timezone.utc) - timedelta(
            hours=self.config.cooldown_hours
        )

        async with self._lock:
            query = """
                SELECT last_seen_at, notified_at FROM seen_items
                WHERE item_id = ?
            """
            params: list = [item_id]

            if filter_name:
                query += " AND filter_name = ?"
                params.append(filter_name)

            async with self._db.execute(query, params) as cursor:
                row = await cursor.fetchone()

            if not row:
                return False

            # Check if notified within cooldown
            notified_at = row["notified_at"]
            if notified_at:
                notified_dt = datetime.fromisoformat(notified_at)
                if notified_dt > cooldown_threshold:
                    return True

            return False

    async def mark_seen(
        self,
        item: VintedItem,
        filter_name: str,
        notified: bool = True,
    ) -> None:
        """Mark an item as seen.

        Args:
            item: VintedItem that was seen
            filter_name: Name of the filter that matched
            notified: Whether a notification was sent
        """
        if not self._db:
            raise RuntimeError("Database not connected")

        now = datetime.now(timezone.utc).isoformat()

        async with self._lock:
            # Check if exists
            async with self._db.execute(
                "SELECT item_id, notification_count FROM seen_items WHERE item_id = ? AND filter_name = ?",
                [item.id, filter_name],
            ) as cursor:
                existing = await cursor.fetchone()

            if existing:
                # Update existing record
                await self._db.execute(
                    """
                    UPDATE seen_items SET
                        last_seen_at = ?,
                        title = ?,
                        price = ?,
                        url = ?,
                        notified_at = CASE WHEN ? THEN ? ELSE notified_at END,
                        notification_count = notification_count + CASE WHEN ? THEN 1 ELSE 0 END
                    WHERE item_id = ? AND filter_name = ?
                    """,
                    [
                        now,
                        item.title,
                        item.price,
                        item.url,
                        notified,
                        now if notified else None,
                        notified,
                        item.id,
                        filter_name,
                    ],
                )
            else:
                # Insert new record
                await self._db.execute(
                    """
                    INSERT INTO seen_items
                    (item_id, filter_name, title, price, url, first_seen_at, last_seen_at, notified_at, notification_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        item.id,
                        filter_name,
                        item.title,
                        item.price,
                        item.url,
                        now,
                        now,
                        now if notified else None,
                        1 if notified else 0,
                    ],
                )

            await self._db.commit()

    async def get_unseen_items(
        self, items: list[VintedItem], filter_name: str
    ) -> list[VintedItem]:
        """Filter out items that have been seen recently.

        Args:
            items: List of items to check
            filter_name: Filter name for scoped lookup

        Returns:
            List of items not seen within cooldown period
        """
        unseen = []
        for item in items:
            if not await self.is_seen(item.id, filter_name):
                unseen.append(item)
        return unseen

    async def log_fetch(
        self,
        filter_name: str,
        items_found: int,
        items_new: int,
        items_matched: int,
        duration_ms: float,
        error: str | None = None,
    ) -> None:
        """Log a fetch operation for monitoring.

        Args:
            filter_name: Name of the filter
            items_found: Total items in response
            items_new: New items not seen before
            items_matched: Items matching filter criteria
            duration_ms: Fetch duration in milliseconds
            error: Error message if fetch failed
        """
        if not self._db:
            raise RuntimeError("Database not connected")

        await self._db.execute(
            """
            INSERT INTO fetch_history
            (filter_name, fetched_at, items_found, items_new, items_matched, duration_ms, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                filter_name,
                datetime.now(timezone.utc).isoformat(),
                items_found,
                items_new,
                items_matched,
                duration_ms,
                error,
            ],
        )
        await self._db.commit()

    async def cleanup_old_records(self) -> int:
        """Remove records older than cleanup_days.

        Returns:
            Number of records deleted
        """
        if not self._db:
            raise RuntimeError("Database not connected")

        threshold = datetime.now(timezone.utc) - timedelta(days=self.config.cleanup_days)

        async with self._lock:
            # Clean seen items
            cursor = await self._db.execute(
                "DELETE FROM seen_items WHERE last_seen_at < ?",
                [threshold.isoformat()],
            )
            items_deleted = cursor.rowcount

            # Clean fetch history
            await self._db.execute(
                "DELETE FROM fetch_history WHERE fetched_at < ?",
                [threshold.isoformat()],
            )

            await self._db.commit()

        logger.info(f"Cleaned up {items_deleted} old records")
        return items_deleted

    async def get_stats(self) -> dict:
        """Get storage statistics."""
        if not self._db:
            raise RuntimeError("Database not connected")

        stats = {}

        # Total seen items
        async with self._db.execute("SELECT COUNT(*) as count FROM seen_items") as cursor:
            row = await cursor.fetchone()
            stats["total_items"] = row["count"] if row else 0

        # Items seen today
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
        async with self._db.execute(
            "SELECT COUNT(*) as count FROM seen_items WHERE first_seen_at >= ?",
            [today.isoformat()],
        ) as cursor:
            row = await cursor.fetchone()
            stats["items_today"] = row["count"] if row else 0

        # Total notifications
        async with self._db.execute(
            "SELECT SUM(notification_count) as total FROM seen_items"
        ) as cursor:
            row = await cursor.fetchone()
            stats["total_notifications"] = row["total"] if row and row["total"] else 0

        # Recent fetches
        async with self._db.execute(
            """
            SELECT COUNT(*) as count, AVG(duration_ms) as avg_duration
            FROM fetch_history
            WHERE fetched_at >= datetime('now', '-1 hour')
            """
        ) as cursor:
            row = await cursor.fetchone()
            stats["fetches_last_hour"] = row["count"] if row else 0
            stats["avg_fetch_duration_ms"] = round(row["avg_duration"], 2) if row and row["avg_duration"] else 0

        return stats
