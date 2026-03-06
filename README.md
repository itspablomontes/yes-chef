# Yes Chef

AI agent that estimates catering ingredient costs from menu specifications. Given a menu JSON and a Sysco catalog, it decomposes each dish into ingredients, looks up costs, and produces a per-unit quote.

---

## Quick Start (Local)

```bash
# 1. Clone and configure
git clone <repo-url>
cd yes-chef
cp .env.example .env
# Edit .env: set OPENAI_API_KEY

# 2. Run via dev script (starts Docker, waits for health)
./scripts/dev.sh

# 3. In another terminal, run the challenge verification
uv run python test_stream.py --file data/menu_spec.json
```

Or run Docker directly:

```bash
docker compose up --build
# API at http://localhost:8000
```

---

## API Overview

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/estimate` | POST | Create new estimation (SSE stream) |
| `/estimate/{id}` | GET | Get estimation status |
| `/estimate/{id}/resume` | POST | Resume interrupted estimation (SSE stream) |
| `/health` | GET | Health check |

**Example: Create estimation**

```bash
curl -X POST http://localhost:8000/estimate \
  -H "Content-Type: application/json" \
  -d @data/menu_spec.json
```

---

## Deployment (Cloud)

The solution is a single Docker image. Deploy to any platform that runs containers (Railway, Render, Fly.io, AWS ECS, GCP Cloud Run, etc.).

### Step 1: Build the image

```bash
docker build -t yes-chef:latest .
```

### Step 2: Run locally (verify)

```bash
docker run -p 8000:8000 \
  -e OPENAI_API_KEY=sk-your-key \
  -e DATABASE_URL=sqlite+aiosqlite:////app/data/yeschef.db \
  yes-chef:latest
```

### Step 3: Push to a registry

```bash
# Docker Hub
docker tag yes-chef:latest <username>/yes-chef:latest
docker push <username>/yes-chef:latest

# Or: GitHub Container Registry, AWS ECR, etc.
```

### Step 4: Deploy on your platform

- Use the image URL from your registry
- Set environment variables: `OPENAI_API_KEY`, `DATABASE_URL`
- Expose port 8000
- For persistence across restarts, mount a volume at `/app/data` (when using SQLite)

**Platform notes:**

- **Railway / Render / Fly.io**: Connect repo or use image URL; add `OPENAI_API_KEY` and `DATABASE_URL` in the dashboard
- **AWS ECS / GCP Cloud Run**: Use image URL, configure env vars and port 8000

---

## Orchestration Architecture

The system uses a LangGraph state machine with per-item persistence and an observer pattern for resumability.

```mermaid
flowchart TB
    subgraph api [API Layer]
        POST["POST /estimate"]
        RESUME["POST /estimate/:id/resume"]
    end
    subgraph app [Application]
        ES[EstimationService]
        ORCH[EstimationOrchestrator]
        OBS[ProgressObserver]
    end
    subgraph graph [LangGraph]
        START([START]) --> ROUTE{route_work_item}
        ROUTE --> |more items| IW[item_worker]
        IW --> ROUTE
        ROUTE --> |all done| REDUCE[reduce]
        REDUCE --> END([END])
    end
    POST --> ES
    RESUME --> ES
    ES --> ORCH
    ORCH --> graph
    ORCH --> OBS
    OBS --> DB[(SQLite/Postgres)]
```

### Design rationale

**1. Resumability first**

Production menus can have 50–100+ items. If the process fails or is interrupted, all work is lost unless we persist incrementally. Each completed item is written to the database via the `ProgressObserver`. On resume, we load the job and completed items, reconstruct the knowledge store, and continue the graph from the next unprocessed item. The challenge explicitly tests this: interrupt mid-run, restart, and resume.

**2. Observer pattern**

The orchestrator emits events (`item_complete`, `estimation_complete`, `error`). Observers react to them. The `ProgressObserver` handles persistence; the graph and orchestrator stay free of DB logic. This decouples streaming from side effects and makes the system testable and extensible.

**3. Context isolation**

Each menu item is processed in a focused ReAct loop. The LLM sees one item at a time, not the full conversation history. This avoids context degradation on long menus.

**4. Carry-forward knowledge**

A shared `KnowledgeStore` accumulates findings (e.g., "wagyu not in Sysco", "bacon matched as #4842788"). Each worker receives hints so we don't re-discover the same catalog misses across dishes.

---

## Performance Design

- **Per-item persistence**: Completed items are saved immediately. No batch-at-end; progress is durable.
- **Knowledge store reconstruction**: On resume, we rebuild the knowledge store from persisted item results so carry-forward state is restored.
- **Catalog index**: Hybrid search (lexical + optional semantic) for ingredient lookups. ChromaDB for embeddings when enabled.
- **Single-item workers**: One LLM call per item keeps context small and failures localized.

---

## What I Would Improve (Tradeoffs)

| Chose | Over | Why |
|-------|------|-----|
| SQLite for Docker default | Postgres | Simpler for single-instance deploy; no extra service. Works for the challenge scope. |
| Observer for persistence | Inline DB calls in graph | Keeps graph pure; observers are swappable and testable. |
| Per-item streaming | Batch-at-end | Enables resumability and real-time progress. Slightly more DB writes. |
| Single container | Multi-service (API + DB + queue) | Minimal footprint for interviewer setup. |

| Deferred | Reason |
|----------|--------|
| Postgres for production | SQLite is sufficient for single-instance. Postgres would matter for horizontal scaling. |
| Rate limiting on `/estimate` | Out of scope; would add middleware for production. |
| Structured tracing (OpenTelemetry) | Would help debug LLM latency and token usage; deferred for time. |
| Catalog pre-warming at build time | Cold-start loads ChromaDB at runtime; could bake index into image. |

---

## Verification

**Health check**

```bash
curl http://localhost:8000/health
```

**Full challenge run** (32 items, ~2–4 min)

```bash
uv run python test_stream.py --file data/menu_spec.json
```

**Resumability test**

Start an estimation, interrupt it (Ctrl+C), note the `estimation_id` from the stream, then resume:

```bash
# Terminal 1: start estimation, interrupt when desired
uv run python test_stream.py --file data/menu_spec.json

# Terminal 2: resume with the estimation_id from the stream
curl -X POST http://localhost:8000/estimate/<estimation_id>/resume
```

---

## Data

- `data/menu_spec.json` — Challenge menu (32 items)
- `data/sysco_catalog.csv` — Sysco price list (~565 items)
- Output conforms to `quote_schema.json`

---
