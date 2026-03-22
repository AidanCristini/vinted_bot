# tests/test_parser.py - Unit tests for JSON and HTML parsers
"""Tests for Vinted response parsers."""

import pytest

from src.parser import HTMLParser, JSONParser, ParseResult, VintedItem, get_parser


class TestJSONParser:
    """Tests for JSONParser."""

    def test_parse_valid_response(self, sample_api_response: str):
        """Test parsing a valid API response."""
        parser = JSONParser()
        result = parser.parse(sample_api_response, "https://www.vinted.fr")

        assert isinstance(result, ParseResult)
        assert len(result.items) == 3
        assert result.total_count == 112
        assert result.page == 1
        assert result.per_page == 24
        assert result.has_more is True
        assert len(result.parse_errors) == 0

    def test_parse_item_fields(self, sample_api_response: str):
        """Test that item fields are correctly parsed."""
        parser = JSONParser()
        result = parser.parse(sample_api_response, "https://www.vinted.fr")

        item = result.items[0]
        assert item.id == "123456789"
        assert item.title == "Nike Air Max 90 - Size 42"
        assert item.price == 45.00
        assert item.currency == "EUR"
        assert item.brand == "Nike"
        assert item.size == "42"
        assert item.location == "Paris, France"
        assert item.condition == "very_good"
        assert len(item.image_urls) > 0
        assert item.seller_name == "sneaker_seller"

    def test_parse_url_construction(self, sample_api_response: str):
        """Test that URLs are correctly constructed."""
        parser = JSONParser()
        result = parser.parse(sample_api_response, "https://www.vinted.fr")

        item = result.items[0]
        assert item.url.startswith("https://www.vinted.fr")
        assert "123456789" in item.url

    def test_parse_empty_response(self):
        """Test parsing an empty response."""
        parser = JSONParser()
        result = parser.parse('{"items": []}', "https://www.vinted.fr")

        assert len(result.items) == 0
        assert result.has_more is False

    def test_parse_invalid_json(self):
        """Test parsing invalid JSON."""
        parser = JSONParser()
        result = parser.parse("not valid json", "https://www.vinted.fr")

        assert len(result.items) == 0
        assert len(result.parse_errors) > 0
        assert "JSON decode error" in result.parse_errors[0]

    def test_parse_missing_fields(self):
        """Test parsing items with missing optional fields."""
        json_content = '{"items": [{"id": 1, "title": "Test", "price": "10.00"}]}'
        parser = JSONParser()
        result = parser.parse(json_content, "https://www.vinted.fr")

        assert len(result.items) == 1
        item = result.items[0]
        assert item.id == "1"
        assert item.title == "Test"
        assert item.price == 10.00
        assert item.brand is None
        assert item.size is None

    def test_parse_price_string(self, sample_api_response: str):
        """Test that string prices are correctly parsed."""
        parser = JSONParser()
        result = parser.parse(sample_api_response, "https://www.vinted.fr")

        # Second item has price "89.99"
        item = result.items[1]
        assert item.price == 89.99

    def test_condition_mapping(self, sample_api_response: str):
        """Test that conditions are correctly mapped."""
        parser = JSONParser()
        result = parser.parse(sample_api_response, "https://www.vinted.fr")

        # status "3" should map to "very_good"
        assert result.items[0].condition == "very_good"
        # status "1" should map to "new_with_tags"
        assert result.items[1].condition == "new_with_tags"
        # status "4" should map to "good"
        assert result.items[2].condition == "good"


class TestHTMLParser:
    """Tests for HTMLParser."""

    def test_parse_valid_html(self, sample_html_response: str):
        """Test parsing a valid HTML response."""
        parser = HTMLParser()
        result = parser.parse(sample_html_response, "https://www.vinted.fr")

        assert isinstance(result, ParseResult)
        assert len(result.items) == 3

    def test_parse_item_from_html(self, sample_html_response: str):
        """Test that item fields are extracted from HTML."""
        parser = HTMLParser()
        result = parser.parse(sample_html_response, "https://www.vinted.fr")

        item = result.items[0]
        assert item.id == "555555555"
        assert "Levis" in item.title or "Vintage" in item.title
        assert item.price == 55.00
        assert item.currency == "EUR"

    def test_parse_gbp_price(self, sample_html_response: str):
        """Test parsing GBP prices."""
        parser = HTMLParser()
        result = parser.parse(sample_html_response, "https://www.vinted.fr")

        # Third item has GBP price
        item = result.items[2]
        assert item.price == 30.00
        assert item.currency == "GBP"

    def test_parse_empty_html(self):
        """Test parsing HTML with no items."""
        parser = HTMLParser()
        result = parser.parse("<html><body></body></html>", "https://www.vinted.fr")

        assert len(result.items) == 0

    def test_extract_url_from_href(self, sample_html_response: str):
        """Test URL extraction from href attributes."""
        parser = HTMLParser()
        result = parser.parse(sample_html_response, "https://www.vinted.fr")

        for item in result.items:
            assert item.url.startswith("https://www.vinted.fr")
            assert "/items/" in item.url


class TestGetParser:
    """Tests for get_parser factory function."""

    def test_get_json_parser(self):
        """Test getting JSON parser for JSON content type."""
        parser = get_parser("application/json")
        assert isinstance(parser, JSONParser)

    def test_get_json_parser_with_charset(self):
        """Test getting JSON parser with charset in content type."""
        parser = get_parser("application/json; charset=utf-8")
        assert isinstance(parser, JSONParser)

    def test_get_html_parser(self):
        """Test getting HTML parser for HTML content type."""
        parser = get_parser("text/html")
        assert isinstance(parser, HTMLParser)

    def test_get_html_parser_default(self):
        """Test that unknown content types default to HTML parser."""
        parser = get_parser("unknown/type")
        assert isinstance(parser, HTMLParser)


class TestVintedItem:
    """Tests for VintedItem model."""

    def test_id_coercion(self):
        """Test that numeric IDs are coerced to strings."""
        item = VintedItem(
            id=123456,  # type: ignore - intentionally passing int
            title="Test",
            price=10.0,
            url="https://example.com",
        )
        assert item.id == "123456"
        assert isinstance(item.id, str)

    def test_price_parsing(self):
        """Test price parsing from string."""
        item = VintedItem(
            id="1",
            title="Test",
            price="€ 45,99",  # type: ignore - testing string parsing
            url="https://example.com",
        )
        assert item.price == 45.99

    def test_scraped_at_default(self):
        """Test that scraped_at is set by default."""
        item = VintedItem(
            id="1",
            title="Test",
            price=10.0,
            url="https://example.com",
        )
        assert item.scraped_at is not None

    def test_optional_fields(self):
        """Test that optional fields can be None."""
        item = VintedItem(
            id="1",
            title="Test",
            price=10.0,
            url="https://example.com",
        )
        assert item.description is None
        assert item.brand is None
        assert item.size is None
        assert item.location is None
        assert item.condition is None
