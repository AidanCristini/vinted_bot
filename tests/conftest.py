# tests/conftest.py - Pytest fixtures and configuration
"""Pytest configuration and shared fixtures."""

import json
from pathlib import Path

import pytest

from src.config import FilterConfig, NotificationConfig
from src.parser import VintedItem


@pytest.fixture
def fixtures_dir() -> Path:
    """Return path to fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_api_response(fixtures_dir: Path) -> str:
    """Load sample API JSON response."""
    return (fixtures_dir / "api_response.json").read_text(encoding="utf-8")


@pytest.fixture
def sample_api_data(sample_api_response: str) -> dict:
    """Load sample API response as dict."""
    return json.loads(sample_api_response)


@pytest.fixture
def sample_html_response(fixtures_dir: Path) -> str:
    """Load sample HTML response."""
    return (fixtures_dir / "html_response.html").read_text(encoding="utf-8")


@pytest.fixture
def sample_item() -> VintedItem:
    """Create a sample VintedItem for testing."""
    return VintedItem(
        id="123456789",
        title="Nike Air Max 90 - Size 42",
        description="Excellent condition Nike Air Max 90. Only worn a few times.",
        price=45.00,
        currency="EUR",
        size="42",
        brand="Nike",
        location="Paris, France",
        url="https://www.vinted.fr/items/123456789",
        image_urls=["https://images.vinted.net/items/123456789/photos/1.jpg"],
        condition="very_good",
    )


@pytest.fixture
def sample_items() -> list[VintedItem]:
    """Create multiple sample items for testing."""
    return [
        VintedItem(
            id="1",
            title="Nike Air Max 90",
            description="Great sneakers",
            price=45.00,
            currency="EUR",
            size="42",
            brand="Nike",
            location="Paris",
            url="https://www.vinted.fr/items/1",
            condition="very_good",
        ),
        VintedItem(
            id="2",
            title="Adidas Ultraboost",
            description="New with tags",
            price=89.99,
            currency="EUR",
            size="43",
            brand="Adidas",
            location="Berlin",
            url="https://www.vinted.fr/items/2",
            condition="new_with_tags",
        ),
        VintedItem(
            id="3",
            title="Puma RS-X",
            description="Good condition",
            price=35.00,
            currency="EUR",
            size="41",
            brand="Puma",
            location="Lyon",
            url="https://www.vinted.fr/items/3",
            condition="good",
        ),
        VintedItem(
            id="4",
            title="Cheap Fake Sneakers",
            description="Replica shoes",
            price=15.00,
            currency="EUR",
            size="42",
            brand="Unknown",
            location="Paris",
            url="https://www.vinted.fr/items/4",
            condition="satisfactory",
        ),
    ]


@pytest.fixture
def basic_filter_config() -> FilterConfig:
    """Basic filter configuration for testing."""
    return FilterConfig(
        name="test_filter",
        keywords=["nike", "adidas"],
        price_max=100.00,
    )


@pytest.fixture
def strict_filter_config() -> FilterConfig:
    """Strict filter with multiple criteria."""
    return FilterConfig(
        name="strict_filter",
        keywords=["nike"],
        brands=["Nike"],
        sizes=["42", "43"],
        price_min=20.00,
        price_max=80.00,
        conditions=["very_good", "new_with_tags"],
    )


@pytest.fixture
def exclusion_filter_config() -> FilterConfig:
    """Filter with exclusion criteria."""
    return FilterConfig(
        name="exclusion_filter",
        keywords=["sneakers", "shoes"],
        keywords_exclude=["fake", "replica"],
        brands_exclude=["Unknown"],
    )


@pytest.fixture
def notification_config() -> NotificationConfig:
    """Notification configuration for testing."""
    return NotificationConfig(
        include_image=True,
        include_description=True,
        max_description_length=100,
    )
