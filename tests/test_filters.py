# tests/test_filters.py - Unit tests for filtering engine
"""Tests for Vinted filtering engine."""

import pytest

from src.config import FilterConfig
from src.filters import FilterEngine, FilterResult, MatchReason, apply_filters
from src.parser import VintedItem


class TestFilterEngine:
    """Tests for FilterEngine class."""

    def test_keyword_match(self, sample_item: VintedItem, basic_filter_config: FilterConfig):
        """Test that keywords match in title."""
        engine = FilterEngine(basic_filter_config)
        result = engine.match(sample_item)

        assert result.matched is True
        assert "keywords" in result.matched_fields

    def test_keyword_no_match(self, sample_item: VintedItem):
        """Test that non-matching keywords fail."""
        config = FilterConfig(name="test", keywords=["reebok", "new balance"])
        engine = FilterEngine(config)
        result = engine.match(sample_item)

        assert result.matched is False

    def test_keyword_case_insensitive(self, sample_item: VintedItem):
        """Test that keyword matching is case-insensitive."""
        config = FilterConfig(name="test", keywords=["NIKE"])
        engine = FilterEngine(config)
        result = engine.match(sample_item)

        assert result.matched is True

    def test_keyword_in_description(self):
        """Test that keywords match in description."""
        item = VintedItem(
            id="1",
            title="Sneakers",
            description="These are Nike Air Max sneakers",
            price=50.0,
            url="https://example.com",
        )
        config = FilterConfig(name="test", keywords=["nike"])
        engine = FilterEngine(config)
        result = engine.match(item)

        assert result.matched is True

    def test_regex_match(self, sample_item: VintedItem):
        """Test regex pattern matching."""
        config = FilterConfig(name="test", keywords_regex=r"air\s+max\s+\d+")
        engine = FilterEngine(config)
        result = engine.match(sample_item)

        assert result.matched is True
        assert "regex" in result.matched_fields

    def test_regex_no_match(self, sample_item: VintedItem):
        """Test regex that doesn't match."""
        config = FilterConfig(name="test", keywords_regex=r"jordan\s+\d+")
        engine = FilterEngine(config)
        result = engine.match(sample_item)

        assert result.matched is False

    def test_brand_match(self, sample_item: VintedItem, strict_filter_config: FilterConfig):
        """Test brand matching."""
        engine = FilterEngine(strict_filter_config)
        result = engine.match(sample_item)

        assert result.matched is True
        assert "brand" in result.matched_fields

    def test_brand_no_match(self, sample_item: VintedItem):
        """Test brand that doesn't match."""
        config = FilterConfig(name="test", keywords=["shoes"], brands=["Adidas"])
        engine = FilterEngine(config)
        result = engine.match(sample_item)

        assert result.matched is False

    def test_size_match(self, sample_item: VintedItem, strict_filter_config: FilterConfig):
        """Test size matching."""
        engine = FilterEngine(strict_filter_config)
        result = engine.match(sample_item)

        assert result.matched is True
        assert "size" in result.matched_fields

    def test_size_no_match(self, sample_item: VintedItem):
        """Test size that doesn't match."""
        config = FilterConfig(name="test", keywords=["nike"], sizes=["40", "41"])
        engine = FilterEngine(config)
        result = engine.match(sample_item)

        assert result.matched is False

    def test_price_min(self, sample_item: VintedItem):
        """Test minimum price filter."""
        config = FilterConfig(name="test", keywords=["nike"], price_min=50.00)
        engine = FilterEngine(config)
        result = engine.match(sample_item)

        # Item price is 45, min is 50
        assert result.matched is False

    def test_price_max(self, sample_item: VintedItem):
        """Test maximum price filter."""
        config = FilterConfig(name="test", keywords=["nike"], price_max=40.00)
        engine = FilterEngine(config)
        result = engine.match(sample_item)

        # Item price is 45, max is 40
        assert result.matched is False

    def test_price_range(self, sample_item: VintedItem):
        """Test price within range."""
        config = FilterConfig(
            name="test", keywords=["nike"], price_min=40.00, price_max=50.00
        )
        engine = FilterEngine(config)
        result = engine.match(sample_item)

        assert result.matched is True
        assert "price" in result.matched_fields

    def test_location_match(self, sample_item: VintedItem):
        """Test location matching."""
        config = FilterConfig(name="test", keywords=["nike"], locations=["Paris", "Lyon"])
        engine = FilterEngine(config)
        result = engine.match(sample_item)

        assert result.matched is True
        assert "location" in result.matched_fields

    def test_location_no_match(self, sample_item: VintedItem):
        """Test location that doesn't match."""
        config = FilterConfig(name="test", keywords=["nike"], locations=["Berlin", "Munich"])
        engine = FilterEngine(config)
        result = engine.match(sample_item)

        assert result.matched is False

    def test_condition_match(self, sample_item: VintedItem):
        """Test condition matching."""
        config = FilterConfig(
            name="test", keywords=["nike"], conditions=["very_good", "new_with_tags"]
        )
        engine = FilterEngine(config)
        result = engine.match(sample_item)

        assert result.matched is True

    def test_condition_no_match(self, sample_item: VintedItem):
        """Test condition that doesn't match."""
        config = FilterConfig(
            name="test", keywords=["nike"], conditions=["new_with_tags"]
        )
        engine = FilterEngine(config)
        result = engine.match(sample_item)

        # Item condition is "very_good", not "new_with_tags"
        assert result.matched is False


class TestFilterExclusions:
    """Tests for exclusion filtering."""

    def test_keyword_exclusion(self, sample_items: list[VintedItem]):
        """Test keyword exclusion."""
        config = FilterConfig(
            name="test",
            keywords=["sneakers", "shoes"],
            keywords_exclude=["fake", "replica"],
        )
        engine = FilterEngine(config)

        # Item 4 has "Fake" in title and "Replica" in description
        result = engine.match(sample_items[3])
        assert result.matched is False
        assert len(result.excluded_reasons) > 0
        assert "excluded keyword" in result.excluded_reasons[0].reason.lower()

    def test_brand_exclusion(self, sample_items: list[VintedItem]):
        """Test brand exclusion."""
        config = FilterConfig(
            name="test",
            keywords=["sneakers"],
            brands_exclude=["Unknown"],
        )
        engine = FilterEngine(config)

        # Item 4 has brand "Unknown"
        result = engine.match(sample_items[3])
        assert result.matched is False

    def test_size_exclusion(self, sample_items: list[VintedItem]):
        """Test size exclusion."""
        config = FilterConfig(
            name="test",
            keywords=["nike", "adidas", "puma"],
            sizes_exclude=["41"],
        )
        engine = FilterEngine(config)

        # Item 3 (Puma) has size 41
        result = engine.match(sample_items[2])
        assert result.matched is False

    def test_location_exclusion(self, sample_items: list[VintedItem]):
        """Test location exclusion."""
        config = FilterConfig(
            name="test",
            keywords=["nike", "adidas"],
            locations_exclude=["Berlin"],
        )
        engine = FilterEngine(config)

        # Item 2 (Adidas) is from Berlin
        result = engine.match(sample_items[1])
        assert result.matched is False


class TestFilterResult:
    """Tests for FilterResult class."""

    def test_match_summary_matched(self, sample_item: VintedItem, basic_filter_config: FilterConfig):
        """Test match summary for matched items."""
        engine = FilterEngine(basic_filter_config)
        result = engine.match(sample_item)

        assert "Matched" in result.match_summary
        assert "keyword" in result.match_summary.lower()

    def test_match_summary_excluded(self, sample_items: list[VintedItem]):
        """Test match summary for excluded items."""
        config = FilterConfig(
            name="test",
            keywords=["sneakers"],
            keywords_exclude=["fake"],
        )
        engine = FilterEngine(config)
        result = engine.match(sample_items[3])

        assert "Excluded" in result.match_summary

    def test_matched_fields_list(self, sample_item: VintedItem, strict_filter_config: FilterConfig):
        """Test that matched_fields lists all matching fields."""
        engine = FilterEngine(strict_filter_config)
        result = engine.match(sample_item)

        assert result.matched is True
        fields = result.matched_fields
        assert "keywords" in fields
        assert "brand" in fields
        assert "size" in fields
        assert "price" in fields


class TestApplyFilters:
    """Tests for apply_filters function."""

    def test_apply_multiple_filters(self, sample_items: list[VintedItem]):
        """Test applying multiple filters to items."""
        filters = [
            FilterConfig(name="nike_filter", keywords=["nike"]),
            FilterConfig(name="adidas_filter", keywords=["adidas"]),
        ]

        matches = apply_filters(sample_items, filters)

        # Should match item 0 (Nike) and item 1 (Adidas)
        assert len(matches) == 2
        matched_ids = [item.id for item, _ in matches]
        assert "1" in matched_ids
        assert "2" in matched_ids

    def test_apply_disabled_filter(self, sample_items: list[VintedItem]):
        """Test that disabled filters are skipped."""
        filters = [
            FilterConfig(name="enabled", keywords=["nike"], enabled=True),
            FilterConfig(name="disabled", keywords=["adidas"], enabled=False),
        ]

        matches = apply_filters(sample_items, filters)

        # Only Nike should match (Adidas filter disabled)
        assert len(matches) == 1
        assert matches[0][0].brand == "Nike"

    def test_apply_no_filters(self, sample_items: list[VintedItem]):
        """Test applying empty filter list."""
        matches = apply_filters(sample_items, [])
        assert len(matches) == 0

    def test_item_matches_multiple_filters(self, sample_items: list[VintedItem]):
        """Test that an item can match multiple filters."""
        filters = [
            FilterConfig(name="brand_filter", keywords=["nike"]),
            FilterConfig(name="price_filter", keywords=["nike"], price_max=50.00),
        ]

        matches = apply_filters(sample_items, filters)

        # Nike item should match both filters
        assert len(matches) == 2
        for item, result in matches:
            assert item.id == "1"


class TestMatchReason:
    """Tests for MatchReason class."""

    def test_match_reason_string(self):
        """Test MatchReason string representation."""
        reason = MatchReason(
            field="keywords",
            criterion="contains",
            value="nike",
            matched=True,
            reason="Contains keyword 'nike'",
        )
        assert str(reason) == "Contains keyword 'nike'"

    def test_match_reason_fields(self):
        """Test MatchReason field access."""
        reason = MatchReason(
            field="price",
            criterion="range",
            value="45.00",
            matched=True,
            reason="Price within range",
        )
        assert reason.field == "price"
        assert reason.matched is True
