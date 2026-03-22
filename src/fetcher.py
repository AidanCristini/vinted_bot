# src/fetcher.py - HTTP client with rate limiting and backoff for Vinted API
"""Vinted HTTP fetcher with rate limiting, backoff, and session management."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

if TYPE_CHECKING:
    from src.config import RateLimitConfig

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded."""

    pass


class FetchError(Exception):
    """Raised when fetching fails after retries."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class RateLimiter:
    """Token bucket rate limiter with per-minute and per-hour limits."""

    requests_per_minute: int = 10
    requests_per_hour: int = 100
    _minute_tokens: int = field(init=False)
    _hour_tokens: int = field(init=False)
    _last_minute_refill: float = field(init=False)
    _last_hour_refill: float = field(init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def __post_init__(self) -> None:
        self._minute_tokens = self.requests_per_minute
        self._hour_tokens = self.requests_per_hour
        self._last_minute_refill = time.monotonic()
        self._last_hour_refill = time.monotonic()

    async def acquire(self) -> None:
        """Acquire a token, waiting if necessary."""
        async with self._lock:
            await self._refill()

            if self._minute_tokens <= 0 or self._hour_tokens <= 0:
                wait_time = self._get_wait_time()
                logger.debug(f"Rate limit hit, waiting {wait_time:.1f}s")
                await asyncio.sleep(wait_time)
                await self._refill()

            self._minute_tokens -= 1
            self._hour_tokens -= 1

    async def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()

        # Refill minute tokens
        minute_elapsed = now - self._last_minute_refill
        if minute_elapsed >= 60:
            self._minute_tokens = self.requests_per_minute
            self._last_minute_refill = now
        elif self._minute_tokens < self.requests_per_minute:
            # Gradual refill
            tokens_to_add = int(minute_elapsed * self.requests_per_minute / 60)
            if tokens_to_add > 0:
                self._minute_tokens = min(
                    self.requests_per_minute, self._minute_tokens + tokens_to_add
                )
                self._last_minute_refill = now

        # Refill hour tokens
        hour_elapsed = now - self._last_hour_refill
        if hour_elapsed >= 3600:
            self._hour_tokens = self.requests_per_hour
            self._last_hour_refill = now

    def _get_wait_time(self) -> float:
        """Calculate how long to wait for next token."""
        if self._minute_tokens <= 0:
            return max(0, 60 - (time.monotonic() - self._last_minute_refill))
        if self._hour_tokens <= 0:
            return max(0, 3600 - (time.monotonic() - self._last_hour_refill))
        return 0


@dataclass
class FetchResult:
    """Result of a fetch operation."""

    url: str
    status_code: int
    content: str
    content_type: str
    headers: dict[str, str]
    elapsed_ms: float
    from_cache: bool = False

    @property
    def is_json(self) -> bool:
        return "application/json" in self.content_type.lower()

    @property
    def is_html(self) -> bool:
        return "text/html" in self.content_type.lower()


class VintedFetcher:
    """Async HTTP client for fetching Vinted pages with rate limiting."""

    # Vinted's public API endpoint pattern (discovered from browser network inspection)
    # This is the public catalog search endpoint that doesn't require authentication
    API_SEARCH_PATH = "/api/v2/catalog/items"

    # Common headers to mimic browser behavior
    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }

    def __init__(
        self,
        rate_limit_config: RateLimitConfig | None = None,
        timeout: float = 30.0,
    ):
        from src.config import RateLimitConfig

        self.config = rate_limit_config or RateLimitConfig()
        self.timeout = timeout
        self.rate_limiter = RateLimiter(
            requests_per_minute=self.config.requests_per_minute,
            requests_per_hour=self.config.requests_per_hour,
        )
        self._client: httpx.AsyncClient | None = None
        self._session_cookies: dict[str, str] = {}

    async def __aenter__(self) -> VintedFetcher:
        await self.start()
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    async def start(self) -> None:
        """Initialize the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                follow_redirects=True,
                headers=self.DEFAULT_HEADERS,
            )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _init_session(self, domain: str) -> None:
        """Initialize session cookies by visiting the main page.

        Vinted requires a session cookie for API requests.
        """
        if domain in self._session_cookies:
            return

        url = f"https://{domain}"
        logger.debug(f"Initializing session for {domain}")

        try:
            await self.rate_limiter.acquire()
            response = await self._client.get(url)
            if response.status_code == 200:
                # Store any session cookies - handle different cookie jar formats
                for name, value in response.cookies.items():
                    self._session_cookies[name] = value
                logger.debug(f"Session initialized for {domain}")
        except Exception as e:
            logger.warning(f"Failed to init session for {domain}: {e}")

    def _add_jitter(self, base_wait: float) -> float:
        """Add random jitter to wait time."""
        jitter = base_wait * self.config.jitter_factor * random.random()
        return base_wait + jitter

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError, RateLimitExceeded)),
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=60, jitter=5),
        reraise=True,
    )
    async def fetch(self, url: str, params: dict | None = None) -> FetchResult:
        """Fetch a URL with rate limiting and retries.

        Args:
            url: The URL to fetch
            params: Optional query parameters

        Returns:
            FetchResult with response data
        """
        if self._client is None:
            await self.start()

        # Extract domain for session management
        from urllib.parse import urlparse

        parsed = urlparse(url)
        domain = parsed.netloc

        # Initialize session if needed
        if "vinted" in domain:
            await self._init_session(domain)

        # Apply rate limiting
        await self.rate_limiter.acquire()

        start_time = time.monotonic()

        try:
            response = await self._client.get(
                url,
                params=params,
                cookies=self._session_cookies,
            )

            elapsed_ms = (time.monotonic() - start_time) * 1000

            # Handle rate limit responses
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                if retry_after and self.config.respect_retry_after:
                    wait_time = float(retry_after)
                else:
                    wait_time = self._add_jitter(self.config.backoff_base * 2)
                logger.warning(f"Rate limited, waiting {wait_time:.1f}s")
                await asyncio.sleep(wait_time)
                raise RateLimitExceeded("Rate limit exceeded")

            # Handle server errors
            if response.status_code >= 500:
                raise FetchError(
                    f"Server error: {response.status_code}", status_code=response.status_code
                )

            return FetchResult(
                url=str(response.url),
                status_code=response.status_code,
                content=response.text,
                content_type=response.headers.get("Content-Type", ""),
                headers=dict(response.headers),
                elapsed_ms=elapsed_ms,
            )

        except httpx.TimeoutException as e:
            logger.error(f"Timeout fetching {url}: {e}")
            raise
        except httpx.NetworkError as e:
            logger.error(f"Network error fetching {url}: {e}")
            raise

    async def fetch_search(
        self,
        domain: str,
        search_text: str | None = None,
        catalog_ids: list[int] | None = None,
        brand_ids: list[int] | None = None,
        size_ids: list[int] | None = None,
        price_from: float | None = None,
        price_to: float | None = None,
        currency: str = "EUR",
        order: str = "newest_first",
        per_page: int = 24,
        page: int = 1,
    ) -> FetchResult:
        """Fetch Vinted catalog search results.

        This uses the public catalog API endpoint that doesn't require authentication.
        The endpoint is discovered from Vinted's frontend JavaScript.

        Args:
            domain: Vinted domain (e.g., www.vinted.fr)
            search_text: Search keywords
            catalog_ids: Category IDs to filter by
            brand_ids: Brand IDs to filter by
            size_ids: Size IDs to filter by
            price_from: Minimum price
            price_to: Maximum price
            currency: Currency code (EUR, GBP, etc.)
            order: Sort order (newest_first, price_low_to_high, price_high_to_low)
            per_page: Items per page (max usually 96)
            page: Page number

        Returns:
            FetchResult with JSON response
        """
        url = f"https://{domain}{self.API_SEARCH_PATH}"

        params: dict = {
            "page": page,
            "per_page": min(per_page, 96),
            "order": order,
            "currency": currency,
            "time": int(time.time()),
        }

        if search_text:
            params["search_text"] = search_text
        if catalog_ids:
            params["catalog_ids"] = ",".join(map(str, catalog_ids))
        if brand_ids:
            params["brand_ids"] = ",".join(map(str, brand_ids))
        if size_ids:
            params["size_ids"] = ",".join(map(str, size_ids))
        if price_from is not None:
            params["price_from"] = price_from
        if price_to is not None:
            params["price_to"] = price_to

        return await self.fetch(url, params=params)

    async def fetch_html_search(self, url: str) -> FetchResult:
        """Fetch HTML search results page.

        Fallback method when API access is blocked or for custom URLs.

        Args:
            url: Full Vinted search URL

        Returns:
            FetchResult with HTML content
        """
        # Override accept header for HTML
        if self._client:
            original_accept = self._client.headers.get("Accept")
            self._client.headers["Accept"] = "text/html,application/xhtml+xml"
            try:
                return await self.fetch(url)
            finally:
                if original_accept:
                    self._client.headers["Accept"] = original_accept
        else:
            await self.start()
            return await self.fetch_html_search(url)
