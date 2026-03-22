# src/parser.py - Parse Vinted API responses and HTML pages into normalized items
"""Parsers for Vinted JSON API responses and HTML fallback pages."""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class VintedItem(BaseModel):
    """Normalized Vinted item schema.

    All parsers should produce items conforming to this schema.
    """

    id: str
    title: str
    description: str | None = None
    price: float
    currency: str = "EUR"
    size: str | None = None
    brand: str | None = None
    location: str | None = None
    url: str
    image_urls: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Additional metadata
    condition: str | None = None
    seller_id: str | None = None
    seller_name: str | None = None
    favorite_count: int | None = None
    view_count: int | None = None

    @field_validator("id", mode="before")
    @classmethod
    def ensure_string_id(cls, v: Any) -> str:
        return str(v)

    @field_validator("price", mode="before")
    @classmethod
    def parse_price(cls, v: Any) -> float:
        if isinstance(v, dict):
            # Handle price as dict (e.g., {"amount": 10.0, "currency_code": "EUR"})
            return float(v.get("amount", 0))
        if isinstance(v, str):
            # Remove currency symbols and parse
            cleaned = re.sub(r"[^\d.,]", "", v)
            cleaned = cleaned.replace(",", ".")
            return float(cleaned) if cleaned else 0.0
        return float(v)

    @field_validator("created_at", mode="before")
    @classmethod
    def parse_created_at(cls, v: Any) -> datetime | None:
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            try:
                # Try ISO format
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                pass
            try:
                # Try Unix timestamp string
                return datetime.fromtimestamp(int(v), tz=timezone.utc)
            except (ValueError, OSError):
                pass
        if isinstance(v, (int, float)):
            try:
                return datetime.fromtimestamp(v, tz=timezone.utc)
            except (ValueError, OSError):
                pass
        return None


class ParseResult(BaseModel):
    """Result of parsing a Vinted response."""

    items: list[VintedItem] = Field(default_factory=list)
    total_count: int | None = None
    page: int = 1
    per_page: int = 24
    has_more: bool = False
    parse_errors: list[str] = Field(default_factory=list)


class BaseParser(ABC):
    """Abstract base parser for Vinted responses."""

    @abstractmethod
    def parse(self, content: str, base_url: str = "") -> ParseResult:
        """Parse content and return normalized items.

        Args:
            content: Raw response content (JSON or HTML)
            base_url: Base URL for constructing absolute URLs

        Returns:
            ParseResult with parsed items
        """
        pass


class JSONParser(BaseParser):
    """Parser for Vinted JSON API responses.

    Expected JSON structure (Vinted API v2):
    {
        "items": [
            {
                "id": 123456789,
                "title": "Item Title",
                "price": "15.00",
                "currency": "EUR",
                "brand_title": "Brand Name",
                "size_title": "M",
                "url": "/items/123456789-item-title",
                "photo": {
                    "url": "https://...",
                    "thumbnails": [...]
                },
                "user": {
                    "id": 12345,
                    "login": "username"
                },
                ...
            }
        ],
        "pagination": {
            "current_page": 1,
            "total_pages": 10,
            "total_entries": 240,
            "per_page": 24
        }
    }
    """

    def parse(self, content: str, base_url: str = "") -> ParseResult:
        """Parse JSON API response."""
        errors: list[str] = []
        items: list[VintedItem] = []

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON: {e}")
            logger.debug(f"Response content (first 500 chars): {content[:500]}")
            logger.debug(f"Response length: {len(content)} chars")
            return ParseResult(parse_errors=[f"JSON decode error: {e}"])

        # Handle different response structures
        raw_items = data.get("items", [])
        if not raw_items and isinstance(data, list):
            raw_items = data

        # Parse pagination
        pagination = data.get("pagination", {})
        total_count = pagination.get("total_entries")
        page = pagination.get("current_page", 1)
        per_page = pagination.get("per_page", 24)
        total_pages = pagination.get("total_pages", 1)
        has_more = page < total_pages if total_pages else False

        for raw_item in raw_items:
            try:
                item = self._parse_item(raw_item, base_url)
                if item:
                    items.append(item)
            except Exception as e:
                error_msg = f"Failed to parse item {raw_item.get('id', 'unknown')}: {e}"
                logger.warning(error_msg)
                errors.append(error_msg)

        return ParseResult(
            items=items,
            total_count=total_count,
            page=page,
            per_page=per_page,
            has_more=has_more,
            parse_errors=errors,
        )

    def _parse_item(self, data: dict[str, Any], base_url: str) -> VintedItem | None:
        """Parse a single item from JSON data."""
        item_id = data.get("id")
        if not item_id:
            return None

        # Build URL
        url_path = data.get("url", "")
        if url_path and not url_path.startswith("http"):
            url = f"{base_url}{url_path}" if base_url else url_path
        else:
            url = url_path or f"{base_url}/items/{item_id}"

        # Extract images
        image_urls: list[str] = []
        photo = data.get("photo") or data.get("photos", [{}])[0] if data.get("photos") else {}
        if isinstance(photo, dict):
            main_url = photo.get("url") or photo.get("full_size_url")
            if main_url:
                image_urls.append(main_url)
            # Add thumbnails
            for thumb in photo.get("thumbnails", []):
                if isinstance(thumb, dict) and thumb.get("url"):
                    image_urls.append(thumb["url"])
                elif isinstance(thumb, str):
                    image_urls.append(thumb)

        # Additional photos
        for extra_photo in data.get("photos", [])[1:]:
            if isinstance(extra_photo, dict) and extra_photo.get("url"):
                image_urls.append(extra_photo["url"])

        # Extract user info
        user = data.get("user", {}) or {}

        # Map condition
        status = data.get("status")
        condition = self._map_condition(status)

        return VintedItem(
            id=str(item_id),
            title=data.get("title", ""),
            description=data.get("description"),
            price=data.get("price", 0),
            currency=data.get("currency", "EUR"),
            size=data.get("size_title") or data.get("size"),
            brand=data.get("brand_title") or data.get("brand"),
            location=self._extract_location(data),
            url=url,
            image_urls=image_urls[:5],  # Limit to 5 images
            created_at=data.get("created_at_ts") or data.get("created_at"),
            condition=condition,
            seller_id=str(user.get("id")) if user.get("id") else None,
            seller_name=user.get("login"),
            favorite_count=data.get("favourite_count"),
            view_count=data.get("view_count"),
        )

    def _extract_location(self, data: dict[str, Any]) -> str | None:
        """Extract location from various fields."""
        # Try direct location field
        if location := data.get("location"):
            if isinstance(location, str):
                return location
            if isinstance(location, dict):
                city = location.get("city", "")
                country = location.get("country_title", "") or location.get("country", "")
                return f"{city}, {country}".strip(", ")

        # Try user location
        user = data.get("user", {}) or {}
        if user_city := user.get("city"):
            return user_city

        return None

    def _map_condition(self, status: str | None) -> str | None:
        """Map Vinted status to condition string."""
        if not status:
            return None
        status_map = {
            "1": "new_with_tags",
            "2": "new_without_tags",
            "3": "very_good",
            "4": "good",
            "5": "satisfactory",
            "new_with_tags": "new_with_tags",
            "new_no_tags": "new_without_tags",
            "very_good": "very_good",
            "good": "good",
            "satisfactory": "satisfactory",
        }
        return status_map.get(str(status).lower(), status)


class HTMLParser(BaseParser):
    """Parser for Vinted HTML search results pages.

    Fallback parser when JSON API is unavailable. Parses the HTML structure
    of search result pages which contain item cards with data attributes.
    """

    def parse(self, content: str, base_url: str = "") -> ParseResult:
        """Parse HTML search results page."""
        errors: list[str] = []
        items: list[VintedItem] = []

        soup = BeautifulSoup(content, "lxml")

        # Try to find embedded JSON data (Vinted often includes this)
        script_data = self._extract_script_data(soup)
        if script_data and "items" in script_data:
            logger.debug("Found embedded JSON in HTML, using JSON parser")
            json_parser = JSONParser()
            return json_parser.parse(json.dumps(script_data), base_url)

        # Parse HTML item cards - use specific selectors and deduplicate by ID
        item_cards = soup.select(
            '[data-testid="item-card"], '
            ".feed-grid__item, "
            ".item-card"
        )

        # Track seen IDs to avoid duplicates
        seen_ids: set[str] = set()

        for card in item_cards:
            try:
                item = self._parse_card(card, base_url)
                if item and item.id not in seen_ids:
                    items.append(item)
                    seen_ids.add(item.id)
            except Exception as e:
                error_msg = f"Failed to parse HTML card: {e}"
                logger.warning(error_msg)
                errors.append(error_msg)

        # Try to extract pagination info
        total_count = self._extract_total_count(soup)

        return ParseResult(
            items=items,
            total_count=total_count,
            parse_errors=errors,
        )

    def _extract_script_data(self, soup: BeautifulSoup) -> dict | None:
        """Extract JSON data from script tags."""
        # Look for __NUXT__ or similar data stores
        for script in soup.find_all("script"):
            text = script.get_text()
            if "__NUXT__" in text or "window.__INITIAL_STATE__" in text:
                # Try to extract JSON from the script
                json_match = re.search(r"(?:__NUXT__|__INITIAL_STATE__)\s*=\s*({.+?});?\s*$", text)
                if json_match:
                    try:
                        return json.loads(json_match.group(1))
                    except json.JSONDecodeError:
                        pass

            # Look for JSON-LD product data
            if script.get("type") == "application/ld+json":
                try:
                    data = json.loads(script.get_text())
                    if isinstance(data, dict) and data.get("@type") == "ItemList":
                        return {"items": data.get("itemListElement", [])}
                except json.JSONDecodeError:
                    pass

        return None

    def _parse_card(self, card, base_url: str) -> VintedItem | None:
        """Parse a single HTML item card."""
        # Extract URL and ID
        link = card if card.name == "a" else card.find("a", href=True)
        if not link:
            return None

        href = link.get("href", "")
        if not href or "/items/" not in href:
            return None

        # Extract ID from URL
        id_match = re.search(r"/items/(\d+)", href)
        if not id_match:
            return None

        item_id = id_match.group(1)
        url = href if href.startswith("http") else f"{base_url}{href}"

        # Extract title
        title_elem = card.select_one(
            '[data-testid="item-title"], '
            ".item-card__title, "
            '[class*="Title"], '
            "h3, h4"
        )
        title = title_elem.get_text(strip=True) if title_elem else ""

        # Extract price
        price_elem = card.select_one(
            '[data-testid="item-price"], '
            ".item-card__price, "
            '[class*="Price"], '
            'span[class*="price"]'
        )
        price_text = price_elem.get_text(strip=True) if price_elem else "0"
        price = self._parse_price(price_text)

        # Extract currency from price text
        currency = self._extract_currency(price_text)

        # Extract image
        img = card.find("img")
        image_urls = []
        if img:
            src = img.get("src") or img.get("data-src")
            if src:
                image_urls.append(src)

        # Extract brand
        brand_elem = card.select_one('[class*="brand"], [class*="Brand"]')
        brand = brand_elem.get_text(strip=True) if brand_elem else None

        # Extract size
        size_elem = card.select_one('[class*="size"], [class*="Size"]')
        size = size_elem.get_text(strip=True) if size_elem else None

        return VintedItem(
            id=item_id,
            title=title,
            price=price,
            currency=currency,
            brand=brand,
            size=size,
            url=url,
            image_urls=image_urls,
        )

    def _parse_price(self, text: str) -> float:
        """Extract numeric price from text."""
        cleaned = re.sub(r"[^\d.,]", "", text)
        cleaned = cleaned.replace(",", ".")
        try:
            return float(cleaned) if cleaned else 0.0
        except ValueError:
            return 0.0

    def _extract_currency(self, text: str) -> str:
        """Extract currency code from price text."""
        currency_map = {
            "€": "EUR",
            "£": "GBP",
            "$": "USD",
            "zł": "PLN",
            "PLN": "PLN",
        }
        for symbol, code in currency_map.items():
            if symbol in text:
                return code
        return "EUR"

    def _extract_total_count(self, soup: BeautifulSoup) -> int | None:
        """Extract total result count from page."""
        # Look for result count in page
        count_elem = soup.select_one('[class*="count"], [class*="results"]')
        if count_elem:
            match = re.search(r"(\d+)", count_elem.get_text())
            if match:
                return int(match.group(1))
        return None


def get_parser(content_type: str) -> BaseParser:
    """Get appropriate parser based on content type.

    Args:
        content_type: HTTP Content-Type header value

    Returns:
        Appropriate parser instance
    """
    if "application/json" in content_type.lower():
        return JSONParser()
    return HTMLParser()
