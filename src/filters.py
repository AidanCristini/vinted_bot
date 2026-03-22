# src/filters.py - Filtering engine with boolean combinations, regex, and negative matches
"""Filtering engine for matching Vinted items against user-defined criteria."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import FilterConfig
    from src.parser import VintedItem

logger = logging.getLogger(__name__)


@dataclass
class MatchReason:
    """Describes why an item matched a filter criterion."""

    field: str
    criterion: str
    value: str
    matched: bool
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass
class FilterResult:
    """Result of filtering an item against criteria."""

    matched: bool
    filter_name: str
    reasons: list[MatchReason] = field(default_factory=list)
    excluded_reasons: list[MatchReason] = field(default_factory=list)

    @property
    def match_summary(self) -> str:
        """Human-readable summary of why the item matched."""
        if not self.matched:
            if self.excluded_reasons:
                return f"Excluded: {'; '.join(str(r) for r in self.excluded_reasons)}"
            return "No criteria matched"

        matched_reasons = [r for r in self.reasons if r.matched]
        if matched_reasons:
            return f"Matched: {'; '.join(str(r) for r in matched_reasons)}"
        return "Matched (default pass)"

    @property
    def matched_fields(self) -> list[str]:
        """List of field names that matched."""
        return [r.field for r in self.reasons if r.matched]


class FilterEngine:
    """Engine for matching items against filter configurations.

    Supports:
    - Keyword matching (case-insensitive substring)
    - Regex patterns
    - Negative matches (exclusions)
    - Price range filtering
    - Brand, size, location, condition filtering
    - Boolean AND logic for multiple criteria
    """

    def __init__(self, filter_config: FilterConfig):
        """Initialize filter engine with configuration.

        Args:
            filter_config: Filter configuration to match against
        """
        self.config = filter_config
        self._compiled_regex: re.Pattern | None = None

        if filter_config.keywords_regex:
            try:
                self._compiled_regex = re.compile(
                    filter_config.keywords_regex, re.IGNORECASE
                )
            except re.error as e:
                logger.error(f"Invalid regex pattern: {e}")

    def match(self, item: VintedItem) -> FilterResult:
        """Check if an item matches the filter criteria.

        All specified criteria must match (AND logic).
        Any exclusion match results in the item being rejected.

        Args:
            item: VintedItem to check

        Returns:
            FilterResult with match status and reasons
        """
        reasons: list[MatchReason] = []
        excluded_reasons: list[MatchReason] = []

        # Check exclusions first (any exclusion = reject)
        if self._check_exclusions(item, excluded_reasons):
            return FilterResult(
                matched=False,
                filter_name=self.config.name,
                reasons=reasons,
                excluded_reasons=excluded_reasons,
            )

        # Track if any positive criteria are specified
        has_criteria = False

        # Check keywords
        if self.config.keywords:
            has_criteria = True
            keyword_match = self._match_keywords(item, reasons)
            if not keyword_match:
                return FilterResult(
                    matched=False,
                    filter_name=self.config.name,
                    reasons=reasons,
                )

        # Check regex pattern
        if self._compiled_regex:
            has_criteria = True
            regex_match = self._match_regex(item, reasons)
            if not regex_match:
                return FilterResult(
                    matched=False,
                    filter_name=self.config.name,
                    reasons=reasons,
                )

        # Check brands
        if self.config.brands:
            has_criteria = True
            brand_match = self._match_brands(item, reasons)
            if not brand_match:
                return FilterResult(
                    matched=False,
                    filter_name=self.config.name,
                    reasons=reasons,
                )

        # Check sizes
        if self.config.sizes:
            has_criteria = True
            size_match = self._match_sizes(item, reasons)
            if not size_match:
                return FilterResult(
                    matched=False,
                    filter_name=self.config.name,
                    reasons=reasons,
                )

        # Check price range
        if self.config.price_min is not None or self.config.price_max is not None:
            has_criteria = True
            price_match = self._match_price(item, reasons)
            if not price_match:
                return FilterResult(
                    matched=False,
                    filter_name=self.config.name,
                    reasons=reasons,
                )

        # Check locations
        if self.config.locations:
            has_criteria = True
            location_match = self._match_locations(item, reasons)
            if not location_match:
                return FilterResult(
                    matched=False,
                    filter_name=self.config.name,
                    reasons=reasons,
                )

        # Check conditions
        if self.config.conditions:
            has_criteria = True
            condition_match = self._match_conditions(item, reasons)
            if not condition_match:
                return FilterResult(
                    matched=False,
                    filter_name=self.config.name,
                    reasons=reasons,
                )

        # If no criteria specified, don't match everything
        if not has_criteria:
            logger.debug(f"Filter '{self.config.name}' has no criteria, skipping")
            return FilterResult(
                matched=False,
                filter_name=self.config.name,
                reasons=[
                    MatchReason(
                        field="none",
                        criterion="no_criteria",
                        value="",
                        matched=False,
                        reason="No filter criteria specified",
                    )
                ],
            )

        return FilterResult(
            matched=True,
            filter_name=self.config.name,
            reasons=reasons,
        )

    def _check_exclusions(
        self, item: VintedItem, excluded_reasons: list[MatchReason]
    ) -> bool:
        """Check if item matches any exclusion criteria.

        Returns:
            True if item should be excluded, False otherwise
        """
        # Check keyword exclusions
        if self.config.keywords_exclude:
            searchable = self._get_searchable_text(item)
            for keyword in self.config.keywords_exclude:
                if keyword.lower() in searchable:
                    excluded_reasons.append(
                        MatchReason(
                            field="keywords",
                            criterion="exclude",
                            value=keyword,
                            matched=True,
                            reason=f"Contains excluded keyword '{keyword}'",
                        )
                    )
                    return True

        # Check brand exclusions
        if self.config.brands_exclude and item.brand:
            brand_lower = item.brand.lower()
            for brand in self.config.brands_exclude:
                if brand.lower() in brand_lower:
                    excluded_reasons.append(
                        MatchReason(
                            field="brand",
                            criterion="exclude",
                            value=brand,
                            matched=True,
                            reason=f"Brand '{item.brand}' is excluded",
                        )
                    )
                    return True

        # Check size exclusions
        if self.config.sizes_exclude and item.size:
            size_lower = item.size.lower()
            for size in self.config.sizes_exclude:
                if size.lower() == size_lower:
                    excluded_reasons.append(
                        MatchReason(
                            field="size",
                            criterion="exclude",
                            value=size,
                            matched=True,
                            reason=f"Size '{item.size}' is excluded",
                        )
                    )
                    return True

        # Check location exclusions
        if self.config.locations_exclude and item.location:
            location_lower = item.location.lower()
            for location in self.config.locations_exclude:
                if location.lower() in location_lower:
                    excluded_reasons.append(
                        MatchReason(
                            field="location",
                            criterion="exclude",
                            value=location,
                            matched=True,
                            reason=f"Location '{item.location}' is excluded",
                        )
                    )
                    return True

        return False

    def _get_searchable_text(self, item: VintedItem) -> str:
        """Combine item fields for keyword searching."""
        parts = [item.title]
        if item.description:
            parts.append(item.description)
        if item.brand:
            parts.append(item.brand)
        return " ".join(parts).lower()

    def _match_keywords(self, item: VintedItem, reasons: list[MatchReason]) -> bool:
        """Match item against keyword list (OR logic for keywords)."""
        searchable = self._get_searchable_text(item)

        for keyword in self.config.keywords:
            if keyword.lower() in searchable:
                reasons.append(
                    MatchReason(
                        field="keywords",
                        criterion="contains",
                        value=keyword,
                        matched=True,
                        reason=f"Contains keyword '{keyword}'",
                    )
                )
                return True

        reasons.append(
            MatchReason(
                field="keywords",
                criterion="contains",
                value=", ".join(self.config.keywords),
                matched=False,
                reason=f"No keywords matched: {self.config.keywords}",
            )
        )
        return False

    def _match_regex(self, item: VintedItem, reasons: list[MatchReason]) -> bool:
        """Match item against regex pattern."""
        if not self._compiled_regex:
            return True

        searchable = self._get_searchable_text(item)
        match = self._compiled_regex.search(searchable)

        if match:
            reasons.append(
                MatchReason(
                    field="regex",
                    criterion="pattern",
                    value=self.config.keywords_regex or "",
                    matched=True,
                    reason=f"Matched regex pattern '{self.config.keywords_regex}'",
                )
            )
            return True

        reasons.append(
            MatchReason(
                field="regex",
                criterion="pattern",
                value=self.config.keywords_regex or "",
                matched=False,
                reason=f"Regex pattern not matched: {self.config.keywords_regex}",
            )
        )
        return False

    def _match_brands(self, item: VintedItem, reasons: list[MatchReason]) -> bool:
        """Match item brand against allowed brands (OR logic)."""
        if not item.brand:
            reasons.append(
                MatchReason(
                    field="brand",
                    criterion="in_list",
                    value="None",
                    matched=False,
                    reason="Item has no brand specified",
                )
            )
            return False

        brand_lower = item.brand.lower()
        for brand in self.config.brands:
            if brand.lower() in brand_lower or brand_lower in brand.lower():
                reasons.append(
                    MatchReason(
                        field="brand",
                        criterion="in_list",
                        value=brand,
                        matched=True,
                        reason=f"Brand '{item.brand}' matches '{brand}'",
                    )
                )
                return True

        reasons.append(
            MatchReason(
                field="brand",
                criterion="in_list",
                value=item.brand,
                matched=False,
                reason=f"Brand '{item.brand}' not in allowed list",
            )
        )
        return False

    def _match_sizes(self, item: VintedItem, reasons: list[MatchReason]) -> bool:
        """Match item size against allowed sizes (OR logic)."""
        if not item.size:
            reasons.append(
                MatchReason(
                    field="size",
                    criterion="in_list",
                    value="None",
                    matched=False,
                    reason="Item has no size specified",
                )
            )
            return False

        size_lower = item.size.lower().strip()
        for size in self.config.sizes:
            if size.lower().strip() == size_lower:
                reasons.append(
                    MatchReason(
                        field="size",
                        criterion="in_list",
                        value=size,
                        matched=True,
                        reason=f"Size '{item.size}' matches",
                    )
                )
                return True

        reasons.append(
            MatchReason(
                field="size",
                criterion="in_list",
                value=item.size,
                matched=False,
                reason=f"Size '{item.size}' not in allowed list",
            )
        )
        return False

    def _match_price(self, item: VintedItem, reasons: list[MatchReason]) -> bool:
        """Match item price against price range."""
        price = item.price

        if self.config.price_min is not None and price < self.config.price_min:
            reasons.append(
                MatchReason(
                    field="price",
                    criterion="min",
                    value=str(price),
                    matched=False,
                    reason=f"Price {price} below minimum {self.config.price_min}",
                )
            )
            return False

        if self.config.price_max is not None and price > self.config.price_max:
            reasons.append(
                MatchReason(
                    field="price",
                    criterion="max",
                    value=str(price),
                    matched=False,
                    reason=f"Price {price} above maximum {self.config.price_max}",
                )
            )
            return False

        price_range = []
        if self.config.price_min is not None:
            price_range.append(f">={self.config.price_min}")
        if self.config.price_max is not None:
            price_range.append(f"<={self.config.price_max}")

        reasons.append(
            MatchReason(
                field="price",
                criterion="range",
                value=str(price),
                matched=True,
                reason=f"Price {price} within range ({', '.join(price_range)})",
            )
        )
        return True

    def _match_locations(self, item: VintedItem, reasons: list[MatchReason]) -> bool:
        """Match item location against allowed locations (OR logic)."""
        if not item.location:
            # If no location specified on item, allow it
            reasons.append(
                MatchReason(
                    field="location",
                    criterion="in_list",
                    value="Unknown",
                    matched=True,
                    reason="Item location unknown, allowing",
                )
            )
            return True

        location_lower = item.location.lower()
        for location in self.config.locations:
            if location.lower() in location_lower:
                reasons.append(
                    MatchReason(
                        field="location",
                        criterion="in_list",
                        value=location,
                        matched=True,
                        reason=f"Location '{item.location}' matches '{location}'",
                    )
                )
                return True

        reasons.append(
            MatchReason(
                field="location",
                criterion="in_list",
                value=item.location,
                matched=False,
                reason=f"Location '{item.location}' not in allowed list",
            )
        )
        return False

    def _match_conditions(self, item: VintedItem, reasons: list[MatchReason]) -> bool:
        """Match item condition against allowed conditions (OR logic)."""
        if not item.condition:
            # If no condition specified, allow it
            reasons.append(
                MatchReason(
                    field="condition",
                    criterion="in_list",
                    value="Unknown",
                    matched=True,
                    reason="Item condition unknown, allowing",
                )
            )
            return True

        condition_lower = item.condition.lower()
        for condition in self.config.conditions:
            if condition.lower() == condition_lower:
                reasons.append(
                    MatchReason(
                        field="condition",
                        criterion="in_list",
                        value=condition,
                        matched=True,
                        reason=f"Condition '{item.condition}' matches",
                    )
                )
                return True

        reasons.append(
            MatchReason(
                field="condition",
                criterion="in_list",
                value=item.condition,
                matched=False,
                reason=f"Condition '{item.condition}' not in allowed list",
            )
        )
        return False


def apply_filters(
    items: list[VintedItem], filters: list[FilterConfig]
) -> list[tuple[VintedItem, FilterResult]]:
    """Apply multiple filters to a list of items.

    Each item can match multiple filters. Returns all matches.

    Args:
        items: List of items to filter
        filters: List of filter configurations

    Returns:
        List of (item, filter_result) tuples for matched items
    """
    matches: list[tuple[VintedItem, FilterResult]] = []

    for item in items:
        for filter_config in filters:
            if not filter_config.enabled:
                continue

            engine = FilterEngine(filter_config)
            result = engine.match(item)

            if result.matched:
                logger.debug(f"Item {item.id} matched filter '{filter_config.name}'")
                matches.append((item, result))

    return matches
