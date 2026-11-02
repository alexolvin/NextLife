# NextLife

**Languages:** English | [Русский](README.ru.md)

**Version:** 2026.11.23

<p align="left">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-blue.svg">
  <img alt="Python" src="https://img.shields.io/badge/python-3.12%2B-blue">
</p>

NextLife is an open recommerce management platform and OpenClaw skill designed for multi-marketplace inventory selling, listing management, customer communications, and deal tracking.

---

## Features

- **Stock & Inventory:** Manage physical items, pricing research, descriptions, defects, and shipping constructor in a single non-linear workflow.
- **Multi-Marketplace Listings:** Automated listing preparation, cross-platform category and translation localization, approval gates, and background publishing.
- **Unified Customer Messages:** Grouped buyer dialogs, LLM-powered reply suggestions, and calendar scheduling integration (Notion API).
- **Buy & Kit Analysis:** Automated candidate search and component kit evaluation to discover profitable resale opportunities.
- **Action Dashboard ("Attention"):** Prioritized task queue surfacing unread messages, pending dispatches, payment confirmations, and expired listings.
- **Background Worker & Resilience:** Asynchronous background job processing with an error recovery catalog and structured batch reports.
- **REST & OpenClaw API:** Full JSON API and OpenClaw gateway (`/api/oc/*`) for autonomous agent operation.

---

## Requirements

- **Python:** 3.12 or newer
- **Database:** PostgreSQL 14+ (or compatible)
- **OS:** Linux (systemd-compatible for background services) / macOS

---

## Hardware Requirements

- **CPU:** 2 cores minimum (4 cores recommended)
- **RAM:** 4 GB minimum (8 GB recommended for browser automation)
- **Storage:** 10 GB free space for database, variant photo caches, and logs
- **Accelerators / GPU:** Not strictly required (cloud AI endpoints supported via OpenAI-compatible API)

---

## Quick Start

```bash
# 1. Clone repository
git clone https://github.com/alexolvin/NextLife.git
cd NextLife

# 2. Configure environment
cp .env.example .env

# 3. Create virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 4. Apply database migrations
alembic upgrade head

# 5. Start the web application
uvicorn nextlife.api.main:app --host 127.0.0.1 --port 8081
```

The web dashboard is accessible at `http://127.0.0.1:8081/ui/stock`.

---

## Configuration

Environment variables can be specified in `.env`:

| Variable | Description | Example / Default | Required |
|---|---|---|---|
| `NEXTLIFE_PORT` | HTTP port for the web service | `8081` | No |
| `NEXTLIFE_DSN` | PostgreSQL connection string | `postgresql+psycopg2:///nextlife` | Yes |
| `NEXTLIFE_WEB_TOKEN` | Secret token for UI session authentication | `your_secret_token` | Yes |
| `NEXTLIFE_OPENCLAW_TOKEN` | Bearer token for OpenClaw skill API | `your_oc_token` | Yes |
| `NEXTLIFE_DATA_DIR` | Path to persistent storage directory | `/var/lib/nextlife/data` | Yes |
| `NEXTLIFE_MEDIA_DIR` | Path to media assets and photo store | `/var/lib/nextlife/media` | Yes |

---

## Usage

- **Web Interface:** Navigate to `http://127.0.0.1:8081/` to access:
  - `/ui/stock` — Inventory warehouse and item cards.
  - `/ui/listings` — Marketplace listings and batch publish queues.
  - `/ui/messages` — Multi-channel customer conversation threads.
  - `/ui/buy` — Purchase search and kit profitability calculator.
  - `/ui/attention` — Action center with prioritized tasks.
  - `/ui/settings` — Profile, marketplace capabilities, and AI endpoints.
- **Background Worker:**
  ```bash
  python -m nextlife.jobs.worker
  ```

---

## Project Structure

```text
NextLife/
├── alembic/              # Database schema migrations
├── config/               # Marketplace profiles and configuration schemas
├── docs/                 # Documentation and architecture reports
├── scripts/              # Operational, deployment and installation scripts
├── src/nextlife/
│   ├── api/              # FastAPI application, routers and templates
│   ├── comms/            # Message inbox/outbox synchronization
│   ├── connectors/       # Marketplace adapters and parsers
│   ├── core/             # Configuration, statuses, domain models
│   ├── jobs/             # Background queue worker, catalog and reporting
│   ├── media/            # Photo pipeline, image editing, AI enhancements
│   ├── services/         # Business logic (stock, listings, buy, Notion)
│   └── storage/          # SQLAlchemy models and database sessions
├── templates/web/        # Jinja2 mobile-first HTML templates
└── tests/                # Automated pytest suite
```

---

## Testing

Run the test suite with:

```bash
NEXTLIFE_SKIP_DB_ON_ERROR=1 pytest
```

---

## Security

Please do not commit credentials, real tokens, or private network identifiers. Refer to [SECURITY.md](SECURITY.md) to report vulnerabilities privately.

---

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for details on code style, branch naming, and pull request guidelines.

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
