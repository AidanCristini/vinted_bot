# src/config.py - Configuration management with YAML/env support
"""Configuration loading and validation for Vinted Notifier."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class EnvSettings(BaseSettings):
    """Environment-based settings (secrets and overrides)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Discord
    discord_webhook_url: str | None = None

    # Database
    database_url: str = "sqlite+aiosqlite:///data/vinted.db"

    # Runtime
    config_path: str = "config.yaml"
    log_level: str = "INFO"
    dry_run: bool = False


class FilterConfig(BaseModel):
    """Single filter configuration."""

    name: str = "default"
    enabled: bool = True

    # Search criteria
    keywords: list[str] = Field(default_factory=list)
    keywords_exclude: list[str] = Field(default_factory=list)
    keywords_regex: str | None = None

    brands: list[str] = Field(default_factory=list)
    brands_exclude: list[str] = Field(default_factory=list)

    sizes: list[str] = Field(default_factory=list)
    sizes_exclude: list[str] = Field(default_factory=list)

    price_min: float | None = None
    price_max: float | None = None
    currency: str = "EUR"

    locations: list[str] = Field(default_factory=list)
    locations_exclude: list[str] = Field(default_factory=list)

    conditions: list[str] = Field(default_factory=list)
    # Valid conditions: new_with_tags, new_without_tags, very_good, good, satisfactory

    # Notification targets for this filter
    notify_discord: bool = True

    # Vinted search URL (optional, for direct URL monitoring)
    search_url: str | None = None

    # Catalog ID for API queries
    catalog_id: int | None = None

    @field_validator("conditions", mode="before")
    @classmethod
    def validate_conditions(cls, v: list[str]) -> list[str]:
        valid = {"new_with_tags", "new_without_tags", "very_good", "good", "satisfactory"}
        if v:
            for cond in v:
                if cond.lower() not in valid:
                    logger.warning(f"Unknown condition: {cond}")
        return [c.lower() for c in v] if v else []


class RateLimitConfig(BaseModel):
    """Rate limiting configuration."""

    requests_per_minute: int = 10
    requests_per_hour: int = 100
    backoff_base: float = 2.0
    backoff_max: float = 300.0
    jitter_factor: float = 0.5
    respect_retry_after: bool = True


class SchedulerConfig(BaseModel):
    """Scheduler configuration."""

    interval_seconds: int = 300  # 5 minutes default
    startup_delay_seconds: int = 5
    max_concurrent_fetches: int = 3


class StorageConfig(BaseModel):
    """Storage/dedup configuration."""

    cooldown_hours: int = 24
    cleanup_days: int = 30
    max_items_per_fetch: int = 100


class NotificationConfig(BaseModel):
    """Notification display configuration."""

    include_image: bool = True
    include_description: bool = True
    max_description_length: int = 200
    batch_notifications: bool = False
    batch_max_items: int = 5
    batch_delay_seconds: int = 10


class AppConfig(BaseModel):
    """Main application configuration."""

    filters: list[FilterConfig] = Field(default_factory=lambda: [FilterConfig()])
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    notification: NotificationConfig = Field(default_factory=NotificationConfig)

    # Vinted domains to monitor
    vinted_domains: list[str] = Field(
        default_factory=lambda: [
            "www.vinted.fr",
            "www.vinted.co.uk",
            "www.vinted.de",
            "www.vinted.es",
            "www.vinted.it",
            "www.vinted.pl",
            "www.vinted.nl",
            "www.vinted.be",
            "www.vinted.pt",
        ]
    )
    default_domain: str = "www.vinted.fr"


@dataclass
class Config:
    """Combined configuration from environment and YAML."""

    env: EnvSettings
    app: AppConfig

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> Config:
        """Load configuration from environment and YAML file."""
        env = EnvSettings()

        # Determine config path
        if config_path is None:
            config_path = Path(env.config_path)
        else:
            config_path = Path(config_path)

        # Load YAML config if exists
        app_config: dict[str, Any] = {}
        if config_path.exists():
            logger.info(f"Loading configuration from {config_path}")
            with open(config_path, "r", encoding="utf-8") as f:
                app_config = yaml.safe_load(f) or {}
        else:
            logger.warning(f"Config file not found: {config_path}, using defaults")

        app = AppConfig(**app_config)

        return cls(env=env, app=app)


def setup_logging(level: str = "INFO") -> None:
    """Configure logging for the application."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Reduce noise from httpx
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
