# src/notifier.py - Notification backends (Discord)
"""Notification backends for sending alerts about matched Vinted items."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from src.config import EnvSettings, NotificationConfig
    from src.filters import FilterResult
    from src.parser import VintedItem

logger = logging.getLogger(__name__)


@dataclass
class NotificationPayload:
    """Payload for notification backends."""

    item: VintedItem
    filter_result: FilterResult
    config: NotificationConfig

    @property
    def title(self) -> str:
        return self.item.title

    @property
    def description(self) -> str:
        desc = self.item.description or ""
        if len(desc) > self.config.max_description_length:
            return desc[: self.config.max_description_length] + "..."
        return desc

    @property
    def price_display(self) -> str:
        currency_symbols = {"EUR": "€", "GBP": "£", "USD": "$", "PLN": "zł"}
        symbol = currency_symbols.get(self.item.currency, self.item.currency)
        return f"{self.item.price:.2f} {symbol}"

    @property
    def matched_fields_display(self) -> str:
        fields = self.filter_result.matched_fields
        return ", ".join(fields) if fields else "filter criteria"


class NotificationBackend(ABC):
    """Abstract base class for notification backends."""

    @abstractmethod
    async def send(self, payload: NotificationPayload) -> bool:
        """Send a notification.

        Args:
            payload: NotificationPayload with item and match info

        Returns:
            True if notification was sent successfully
        """
        pass

    @abstractmethod
    async def send_batch(self, payloads: list[NotificationPayload]) -> int:
        """Send multiple notifications.

        Args:
            payloads: List of notification payloads

        Returns:
            Number of successful notifications
        """
        pass

    async def close(self) -> None:
        """Cleanup resources."""
        pass


class ConsoleBackend(NotificationBackend):
    """Console notification backend for testing/dry-run mode."""

    async def send(self, payload: NotificationPayload) -> bool:
        """Print notification to console."""
        print("\n" + "=" * 60)
        print(f"🔔 NEW MATCH: {payload.title}")
        print("-" * 60)
        print(f"💰 Price: {payload.price_display}")
        if payload.item.brand:
            print(f"🏷️  Brand: {payload.item.brand}")
        if payload.item.size:
            print(f"📐 Size: {payload.item.size}")
        if payload.item.location:
            print(f"📍 Location: {payload.item.location}")
        if payload.item.condition:
            print(f"✨ Condition: {payload.item.condition}")
        print(f"🔗 URL: {payload.item.url}")
        print(f"📋 Filter: {payload.filter_result.filter_name}")
        print(f"✅ Matched: {payload.matched_fields_display}")
        print("=" * 60 + "\n")
        return True

    async def send_batch(self, payloads: list[NotificationPayload]) -> int:
        """Send multiple notifications to console."""
        successful = 0
        for payload in payloads:
            if await self.send(payload):
                successful += 1
        return successful


class DiscordWebhookBackend(NotificationBackend):
    """Discord webhook notification backend."""

    def __init__(self, webhook_url: str, notification_config: NotificationConfig):
        """Initialize Discord webhook backend.

        Args:
            webhook_url: Discord webhook URL
            notification_config: Notification display configuration
        """
        self.webhook_url = webhook_url
        self.config = notification_config
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def send(self, payload: NotificationPayload) -> bool:
        """Send Discord embed notification."""
        client = await self._get_client()

        # Build embed
        embed = {
            "title": payload.title[:256],  # Discord limit
            "url": payload.item.url,
            "color": 0x09B1BA,  # Vinted teal color
            "fields": [
                {"name": "💰 Price", "value": payload.price_display, "inline": True},
            ],
            "footer": {
                "text": f"Filter: {payload.filter_result.filter_name} • Matched: {payload.matched_fields_display}"
            },
        }

        # Add optional fields
        if payload.item.brand:
            embed["fields"].append(
                {"name": "🏷️ Brand", "value": payload.item.brand, "inline": True}
            )
        if payload.item.size:
            embed["fields"].append(
                {"name": "📐 Size", "value": payload.item.size, "inline": True}
            )
        if payload.item.location:
            embed["fields"].append(
                {"name": "📍 Location", "value": payload.item.location, "inline": True}
            )
        if payload.item.condition:
            embed["fields"].append(
                {"name": "✨ Condition", "value": payload.item.condition, "inline": True}
            )

        # Add description
        if self.config.include_description and payload.description:
            embed["description"] = payload.description

        # Add image
        if self.config.include_image and payload.item.image_urls:
            embed["thumbnail"] = {"url": payload.item.image_urls[0]}

        webhook_payload = {
            "username": "Vinted Notifier",
            "embeds": [embed],
        }

        try:
            response = await client.post(self.webhook_url, json=webhook_payload)

            if response.status_code == 429:
                # Rate limited - get retry-after
                retry_after = response.json().get("retry_after", 5)
                logger.warning(f"Discord rate limited, waiting {retry_after}s")
                await asyncio.sleep(retry_after)
                return await self.send(payload)

            if response.status_code >= 400:
                logger.error(f"Discord webhook failed: {response.status_code} - {response.text}")
                return False

            logger.debug("Discord notification sent successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to send Discord notification: {e}")
            return False

    async def send_batch(self, payloads: list[NotificationPayload]) -> int:
        """Send multiple Discord notifications with rate limit handling.

        Discord webhooks have a rate limit of 30 requests per minute.
        """
        successful = 0

        for i, payload in enumerate(payloads):
            if await self.send(payload):
                successful += 1

            # Respect Discord rate limits
            if i < len(payloads) - 1:
                await asyncio.sleep(2)  # ~30 per minute

        return successful


class NotificationManager:
    """Manages multiple notification backends."""

    def __init__(self, env: EnvSettings, notification_config: NotificationConfig):
        """Initialize notification manager.

        Args:
            env: Environment settings with credentials
            notification_config: Notification display configuration
        """
        self.env = env
        self.config = notification_config
        self.backends: dict[str, NotificationBackend] = {}
        self._setup_backends()

    def _setup_backends(self) -> None:
        """Set up available notification backends."""
        # Always add console backend for dry-run
        self.backends["console"] = ConsoleBackend()

        # Discord webhook
        if self.env.discord_webhook_url:
            self.backends["discord"] = DiscordWebhookBackend(
                self.env.discord_webhook_url, self.config
            )
            logger.info("Discord webhook backend enabled")

    async def notify(
        self,
        item: VintedItem,
        filter_result: FilterResult,
        backends: list[str] | None = None,
        dry_run: bool = False,
    ) -> dict[str, bool]:
        """Send notification to specified backends.

        Args:
            item: VintedItem to notify about
            filter_result: FilterResult with match details
            backends: List of backend names to use (default: all configured)
            dry_run: If True, only use console backend

        Returns:
            Dict mapping backend name to success status
        """
        payload = NotificationPayload(
            item=item,
            filter_result=filter_result,
            config=self.config,
        )

        results: dict[str, bool] = {}

        if dry_run:
            results["console"] = await self.backends["console"].send(payload)
            return results

        target_backends = backends or [
            name for name in self.backends.keys() if name != "console"
        ]

        # If no backends configured, use console
        if not target_backends:
            logger.warning("No notification backends configured, using console")
            target_backends = ["console"]

        for backend_name in target_backends:
            if backend_name in self.backends:
                results[backend_name] = await self.backends[backend_name].send(payload)
            else:
                logger.warning(f"Unknown backend: {backend_name}")
                results[backend_name] = False

        return results

    async def close(self) -> None:
        """Close all backends."""
        for backend in self.backends.values():
            await backend.close()
