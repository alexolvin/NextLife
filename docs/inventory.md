| Файл | Строк | Назначение | Нарушения |
|---|---|---|---|
| `src/nextlife/__init__.py` | 7 | NextLife OpenClaw Skill Package. | Нет |
| `src/nextlife/android/__init__.py` | 1 | Android package for NextLife. | Нет |
| `src/nextlife/android/bridge.py` | 48 | Bridge to OpenClaw Android agent on phone (sections 6.4, 19.2 Э5). | Нет |
| `src/nextlife/api/__init__.py` | 1 | API package for NextLife. | Нет |
| `src/nextlife/api/main.py` | 74 | FastAPI application for NextLife. | Нет |
| `src/nextlife/api/routes.py` | 1202 | Описание отсутствует | VI.1.1 (превышение 400 строк), V.1 (HTML-строки в Python) |
| `src/nextlife/cli/__init__.py` | 1 | CLI package for NextLife. | Нет |
| `src/nextlife/cli/main.py` | 486 | CLI entrypoint for NextLife. Single entry point for OpenClaw. | VI.1.1 (превышение 400 строк) |
| `src/nextlife/comms/__init__.py` | 1 | Communications package for NextLife. | Нет |
| `src/nextlife/comms/classifier.py` | 191 | Incoming message classifier, FAQ matcher, escalation logic, and thread tracking (sections 9.2 Ш6, 11, 13). | Нет |
| `src/nextlife/comms/inbox.py` | 173 | Inbox service for receiving external messages via OpenClaw (Section 4.4, Section 17). | Нет |
| `src/nextlife/comms/outbox.py` | 115 | Outbox service for OpenClaw communication decoupling (TZ Section 4.4, Section 5). | Нет |
| `src/nextlife/comms/translation.py` | 38 | Language detection and translation service (section 11.3). | Нет |
| `src/nextlife/connectors/__init__.py` | 1 | Marketplace connectors package for NextLife. | Нет |
| `src/nextlife/connectors/aukro.py` | 11 | Aukro connector implementation. | Нет |
| `src/nextlife/connectors/base.py` | 130 | Base connector abstractions and data contracts (section 6.2). | Нет |
| `src/nextlife/connectors/bazos.py` | 11 | Bazoš connector implementation. | Нет |
| `src/nextlife/connectors/registry.py` | 65 | Marketplace connector registry. | Нет |
| `src/nextlife/connectors/sbazar.py` | 11 | Sbazar connector implementation. | Нет |
| `src/nextlife/core/__init__.py` | 1 | Core package for NextLife. | Нет |
| `src/nextlife/core/approval.py` | 56 | Draft approval and canonical payload hashing. | Нет |
| `src/nextlife/core/codes.py` | 59 | Item code generation and pool management. | Нет |
| `src/nextlife/core/config.py` | 201 | Configuration schemas and loaders for NextLife. | Нет |
| `src/nextlife/core/exceptions.py` | 25 | Exceptions hierarchy for NextLife. | Нет |
| `src/nextlife/core/secrets.py` | 34 | SecretRef resolver for NextLife. | Нет |
| `src/nextlife/core/status.py` | 123 | Status lifecycle and single state transition table for NextLife items. | Нет |
| `src/nextlife/jobs/__init__.py` | 1 | Jobs package for NextLife. | Нет |
| `src/nextlife/jobs/worker.py` | 46 | Background job worker for NextLife. | Нет |
| `src/nextlife/media/__init__.py` | 1 | Media package for NextLife. | Нет |
| `src/nextlife/media/pipeline.py` | 240 | Media pipeline for photos: ingest, sanitize, process, VLM verify, and assemble kits (section 8). | Нет |
| `src/nextlife/money/__init__.py` | 1 | Money package for NextLife. | Нет |
| `src/nextlife/money/money.py` | 186 | Money and currency conversion module for NextLife (section 7). | П-1.2/внешние вызовы вне слоя коннекторов |
| `src/nextlife/obs/__init__.py` | 1 | Observability package for NextLife. | Нет |
| `src/nextlife/obs/audit.py` | 28 | Audit log recorder. | Нет |
| `src/nextlife/obs/logger.py` | 34 | Structured JSON logging for NextLife. | Нет |
| `src/nextlife/obs/truth.py` | 28 | Truth assertion logger with strict evidence validation (Section 13, Ш4). | Нет |
| `src/nextlife/obs/truth_log.py` | 36 | Truth assertion log recorder with strict evidence enforcement. | Нет |
| `src/nextlife/services/__init__.py` | 1 | Services package for NextLife. | Нет |
| `src/nextlife/services/buy.py` | 133 | Buying scenario service (section 10, 13, 19.2 Э8). | Нет |
| `src/nextlife/services/deals.py` | 140 | Deal management and safe archival service (section 9.2 Ш7-Ш8, 13, 19.2 Э8). | Нет |
| `src/nextlife/services/research.py` | 205 | Market research and comparable items analysis service (section 9.2, Ш2). | Нет |
| `src/nextlife/services/sell.py` | 269 | Selling scenario service: draft building, approval validation, publication, and queueing (sections 9, 13, 17). | Нет |
| `src/nextlife/storage/__init__.py` | 1 | Storage package for NextLife. | Нет |
| `src/nextlife/storage/db.py` | 52 | Database engine and session management for NextLife. | Нет |
| `src/nextlife/storage/models.py` | 336 | SQLAlchemy ORM models for NextLife according to section 5. | Нет |
