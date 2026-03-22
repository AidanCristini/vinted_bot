# tests/test_integration.py - Integration tests using HTTP fixtures
"""Integration tests for Vinted Notifier using recorded HTTP fixtures."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from src.config import Config, FilterConfig, RateLimitConfig
from src.fetcher import FetchResult, VintedFetcher
from src.filters import apply_filters
from src.parser import JSONParser, get_parser


@pytest.fixture
def mock_config() -> Config:
    """Create a mock configuration for testing."""
    config = MagicMock(spec=Config)
    config.env = MagicMock()
    config.env.dry_run = True
    config.env.discord_webhook_url = None
    config.app = MagicMock()
    config.app.filters = [
        FilterConfig(
            name="sneakers",
            keywords=["nike", "adidas"],
            price_max=100.00,
        )
    ]
    config.app.rate_limit = RateLimitConfig(
        requests_per_minute=60,
        requests_per_hour=1000,
    )
    config.app.default_domain = "www.vinted.fr"
    return config


class TestFetcherWithFixtures:
    """Integration tests for VintedFetcher."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_fetch_api_response(self, sample_api_response: str):
        """Test fetching and parsing API response."""
        # Mock the API endpoint
        respx.get("https://www.vinted.fr/api/v2/catalog/items").mock(
            return_value=httpx.Response(
                200,
                content=sample_api_response,
                headers={"Content-Type": "application/json"},
            )
        )

        # Mock session initialization
        respx.get("https://www.vinted.fr").mock(
            return_value=httpx.Response(200, content="<html></html>")
        )

        fetcher = VintedFetcher(
            rate_limit_config=RateLimitConfig(
                requests_per_minute=60,
                requests_per_hour=1000,
            )
        )

        async with fetcher:
            result = await fetcher.fetch_search(
                domain="www.vinted.fr",
                search_text="sneakers",
            )

        assert result.status_code == 200
        assert result.is_json
        assert "items" in result.content

    @pytest.mark.asyncio
    @respx.mock
    async def test_fetch_html_fallback(self, sample_html_response: str):
        """Test fetching HTML when API returns HTML."""
        # Mock HTML response
        respx.get("https://www.vinted.fr/catalog").mock(
            return_value=httpx.Response(
                200,
                content=sample_html_response,
                headers={"Content-Type": "text/html"},
            )
        )

        # Mock session initialization
        respx.get("https://www.vinted.fr").mock(
            return_value=httpx.Response(200, content="<html></html>")
        )

        fetcher = VintedFetcher(
            rate_limit_config=RateLimitConfig(
                requests_per_minute=60,
                requests_per_hour=1000,
            )
        )

        async with fetcher:
            result = await fetcher.fetch_html_search("https://www.vinted.fr/catalog")

        assert result.status_code == 200
        assert result.is_html

    @pytest.mark.asyncio
    @respx.mock
    async def test_rate_limit_handling(self):
        """Test handling of rate limit responses."""
        # First request returns 429, second succeeds
        route = respx.get("https://www.vinted.fr/api/v2/catalog/items")
        route.side_effect = [
            httpx.Response(
                429,
                json={"error": "rate_limited"},
                headers={"Retry-After": "1"},
            ),
            httpx.Response(
                200,
                json={"items": []},
                headers={"Content-Type": "application/json"},
            ),
        ]

        # Mock session initialization
        respx.get("https://www.vinted.fr").mock(
            return_value=httpx.Response(200, content="<html></html>")
        )

        fetcher = VintedFetcher(
            rate_limit_config=RateLimitConfig(
                requests_per_minute=60,
                requests_per_hour=1000,
                respect_retry_after=True,
            )
        )

        async with fetcher:
            result = await fetcher.fetch_search(
                domain="www.vinted.fr",
                search_text="test",
            )

        # Should eventually succeed after retry
        assert result.status_code == 200


class TestEndToEndFlow:
    """End-to-end integration tests."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_fetch_parse_filter_flow(
        self, sample_api_response: str, mock_config: Config
    ):
        """Test complete flow: fetch -> parse -> filter."""
        # Mock API response
        respx.get("https://www.vinted.fr/api/v2/catalog/items").mock(
            return_value=httpx.Response(
                200,
                content=sample_api_response,
                headers={"Content-Type": "application/json"},
            )
        )
        respx.get("https://www.vinted.fr").mock(
            return_value=httpx.Response(200, content="<html></html>")
        )

        # 1. Fetch
        fetcher = VintedFetcher(rate_limit_config=mock_config.app.rate_limit)
        async with fetcher:
            fetch_result = await fetcher.fetch_search(
                domain="www.vinted.fr",
                search_text="sneakers",
            )

        # 2. Parse
        parser = get_parser(fetch_result.content_type)
        parse_result = parser.parse(fetch_result.content, "https://www.vinted.fr")

        assert len(parse_result.items) == 3

        # 3. Filter
        matches = apply_filters(parse_result.items, mock_config.app.filters)

        # Nike and Adidas items should match
        assert len(matches) == 2
        matched_brands = {item.brand for item, _ in matches}
        assert "Nike" in matched_brands
        assert "Adidas" in matched_brands

    @pytest.mark.asyncio
    async def test_parser_handles_both_formats(
        self, sample_api_response: str, sample_html_response: str
    ):
        """Test that parser correctly handles JSON and HTML."""
        # Test JSON
        json_parser = get_parser("application/json")
        json_result = json_parser.parse(sample_api_response, "https://www.vinted.fr")
        assert len(json_result.items) == 3

        # Test HTML
        html_parser = get_parser("text/html")
        html_result = html_parser.parse(sample_html_response, "https://www.vinted.fr")
        assert len(html_result.items) == 3


class TestFixtureDocumentation:
    """Tests that document discovered endpoint shapes."""

    def test_api_response_shape(self, sample_api_data: dict):
        """Document the expected Vinted API response structure.

        Vinted's public catalog API (v2) returns JSON with the following structure:
        - items[]: Array of item objects
        - pagination: Object with current_page, total_pages, total_entries, per_page

        Each item object contains:
        - id: Numeric item ID
        - title: Item title string
        - description: Item description (may be null)
        - price: Price as string (e.g., "45.00")
        - currency: Currency code (EUR, GBP, etc.)
        - brand_title: Brand name
        - size_title: Size label
        - status: Condition code (1=new_with_tags, 2=new_without_tags, 3=very_good, 4=good, 5=satisfactory)
        - url: Relative URL path to item
        - photo: Object with url and thumbnails
        - photos[]: Array of photo objects
        - user: Seller info with id, login, city
        - created_at_ts: Unix timestamp
        - favourite_count: Number of favorites
        - view_count: Number of views
        - location: Object with city, country_title
        """
        # Verify structure matches documentation
        assert "items" in sample_api_data
        assert "pagination" in sample_api_data

        item = sample_api_data["items"][0]
        assert "id" in item
        assert "title" in item
        assert "price" in item
        assert "currency" in item
        assert "photo" in item
        assert "user" in item

        pagination = sample_api_data["pagination"]
        assert "current_page" in pagination
        assert "total_pages" in pagination
        assert "total_entries" in pagination
        assert "per_page" in pagination

    def test_api_endpoint_documentation(self):
        """Document discovered Vinted API endpoints.

        Public Endpoints (no authentication required):
        - GET /api/v2/catalog/items - Search catalog
          Query params:
            - search_text: Search keywords
            - catalog_ids: Category IDs (comma-separated)
            - brand_ids: Brand IDs (comma-separated)
            - size_ids: Size IDs (comma-separated)
            - price_from: Minimum price
            - price_to: Maximum price
            - currency: Currency code
            - order: Sort order (newest_first, price_low_to_high, price_high_to_low)
            - page: Page number
            - per_page: Items per page (max 96)
            - time: Unix timestamp (for cache busting)

        Note: A session cookie may be required. Visit the main page first
        to obtain session cookies before making API requests.
        """
        # This test serves as living documentation
        assert True
