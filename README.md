# Vinted Notifier

Monitor Vinted product listings and get notified when new items match your filters.

## Features

- **Config-driven filters**: Define multiple search filters with keywords, brands, sizes, price ranges, locations, and conditions
- **Multiple notification backends**: Discord webhooks (primary)
- **Smart deduplication**: SQLite-based tracking to avoid duplicate notifications with configurable cooldowns
- **Rate limiting**: Respectful request rates with exponential backoff and jitter
- **Dual parsing**: Handles both JSON API responses and HTML fallback pages
- **Flexible matching**: Boolean combinations, regex patterns, and negative matches with human-readable match reasons
- **Containerized**: Docker and docker-compose support for easy deployment

## Quick Start

### 1. Clone and Configure

```bash
git clone <repository-url>
cd vinted_bot

# Copy example files
cp config.example.yaml config.yaml
cp .env.example .env

# Edit configuration
# - config.yaml: Define your search filters
# - .env: Add your notification credentials (Discord webhook, etc.)
```

### 2. Run with Docker (Recommended)

```bash
# Build and start
docker-compose up --build

# Run in background
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

### 3. Run Locally (Development)

```bash
# Install dependencies
pip install -e ".[dev]"

# Run once (dry-run mode, console output)
python -m src.main --once --dry-run

# Run continuously
python -m src.main

# Run with custom config
python -m src.main -c myconfig.yaml
```

## Configuration

### Filter Configuration (config.yaml)

```yaml
filters:
  - name: "sneaker_hunt"
    enabled: true

    # Keywords (OR logic - any match works)
    keywords:
      - "nike air max"
      - "adidas ultraboost"

    # Exclude items containing these (any match = skip)
    keywords_exclude:
      - "fake"
      - "replica"

    # Optional regex for advanced patterns
    keywords_regex: "air\\s+max\\s+\\d+"

    # Filter by brand
    brands:
      - "Nike"
      - "Adidas"
    brands_exclude:
      - "Unknown"

    # Filter by size
    sizes:
      - "42"
      - "43"

    # Price range
    price_min: 20.00
    price_max: 100.00
    currency: "EUR"

    # Location filter
    locations:
      - "France"
      - "Germany"

    # Condition filter
    # Options: new_with_tags, new_without_tags, very_good, good, satisfactory
    conditions:
      - "very_good"
      - "new_with_tags"

    # Notification targets
    notify_discord: true
```

### Environment Variables (.env)

```bash
# Discord (Primary)
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/xxx/yyy

# Application
LOG_LEVEL=INFO
DRY_RUN=false
```

## Switching Notification Backends

### Discord Webhook (Default)

1. Go to your Discord server settings
2. Navigate to Integrations > Webhooks
3. Create a new webhook and copy the URL
4. Set `DISCORD_WEBHOOK_URL` in your `.env` file

## Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Run specific test file
pytest tests/test_filters.py -v
```

## Project Structure

```
vinted_bot/
├── src/
│   ├── __init__.py      # Package initialization
│   ├── config.py        # Configuration loading and validation
│   ├── fetcher.py       # HTTP client with rate limiting
│   ├── parser.py        # JSON/HTML parsers for Vinted responses
│   ├── filters.py       # Filtering engine with boolean/regex support
│   ├── storage.py       # SQLite storage for deduplication
│   ├── notifier.py      # Notification backends (Discord)
│   ├── scheduler.py     # APScheduler-based job scheduling
│   └── main.py          # Application entry point and CLI
├── tests/
│   ├── fixtures/        # Test data (sample API/HTML responses)
│   ├── conftest.py      # Pytest fixtures
│   ├── test_parser.py   # Parser unit tests
│   ├── test_filters.py  # Filter engine unit tests
│   └── test_integration.py  # Integration tests
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── config.example.yaml
├── .env.example
└── README.md
```

## Rate Limit Tuning

The default settings are conservative to be respectful to Vinted's servers:

```yaml
rate_limit:
  requests_per_minute: 10   # Max 10 requests/minute
  requests_per_hour: 100    # Max 100 requests/hour
  backoff_base: 2.0         # Start with 2s backoff on errors
  backoff_max: 300.0        # Max 5 minute backoff
```

**Tuning recommendations:**

- **More aggressive** (higher risk of blocking): Increase `requests_per_minute` to 20-30
- **More conservative** (for multiple filters): Decrease to 5 requests/minute
- **Longer intervals**: Increase `scheduler.interval_seconds` to 600+ (10+ minutes)

**Warning**: Setting rates too high may result in:
- IP blocking
- Account suspension (if logged in)
- Violating Terms of Service

## Adding More Sources

To add support for other marketplace sources:

1. Create a new fetcher in `src/fetcher.py` or a new file
2. Implement the same `fetch()` interface returning `FetchResult`
3. Create a corresponding parser in `src/parser.py`
4. Ensure the parser outputs `VintedItem` objects (rename to `MarketplaceItem` if needed)
5. Update configuration to support source selection

## Legal and Ethical Considerations

### Terms of Service

**IMPORTANT**: This tool is intended for personal use to monitor public listings. Before using:

1. **Read Vinted's Terms of Service** for your region
2. **Respect robots.txt**: This tool only accesses public pages
3. **Do not use for commercial purposes** without explicit permission
4. **Do not attempt to log in** or access authenticated endpoints unless you've confirmed it's permitted

### Rate Limiting

This tool implements rate limiting to minimize impact on Vinted's servers:
- Default: 10 requests/minute, 100 requests/hour
- Exponential backoff on errors
- Respects `Retry-After` headers

### Data Collection

- Only collects publicly visible listing data
- Data is stored locally in SQLite for deduplication
- No data is shared with third parties

### Disclaimer

This software is provided "as is" without warranty. The authors are not responsible for:
- Any violations of Vinted's Terms of Service
- Account suspensions or IP blocks
- Any legal issues arising from use of this software

**Use at your own risk and responsibility.**

## API Documentation

### Discovered Endpoints

Vinted's public catalog API (discovered from browser inspection):

```
GET /api/v2/catalog/items
```

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| search_text | string | Search keywords |
| catalog_ids | string | Category IDs (comma-separated) |
| brand_ids | string | Brand IDs (comma-separated) |
| size_ids | string | Size IDs (comma-separated) |
| price_from | number | Minimum price |
| price_to | number | Maximum price |
| currency | string | Currency code (EUR, GBP, etc.) |
| order | string | Sort: newest_first, price_low_to_high, price_high_to_low |
| page | number | Page number |
| per_page | number | Items per page (max 96) |

**Note**: Session cookies may be required. The fetcher automatically handles this by visiting the main page first.

See `tests/fixtures/api_response.json` for example response structure.

## Troubleshooting

### No notifications received

1. Check `LOG_LEVEL=DEBUG` for detailed logs
2. Verify webhook/token credentials in `.env`
3. Run with `--dry-run` to test without sending
4. Check filter criteria aren't too restrictive

### Rate limited

1. Increase `interval_seconds` (e.g., 600 for 10 minutes)
2. Decrease `requests_per_minute`
3. Check logs for `429` responses

### Database errors

1. Ensure `data/` directory exists and is writable
2. Check `DATABASE_URL` path is correct
3. Delete `data/vinted.db` to reset (loses history)

## Contributing

1. Fork the repository
2. Create a feature branch
3. Run tests: `pytest`
4. Submit a pull request

## License

MIT License - see LICENSE file for details.
