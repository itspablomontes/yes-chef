# Yes Chef

AI agent backend that estimates catering ingredient costs from menu specs using real tool calls against a Sysco catalog.

**Live API:** [https://yes-chef-production.up.railway.app](https://yes-chef-production.up.railway.app)

---

## Interviewer Quick Start

Clone, configure, run, and verify in under 10 minutes:

```bash
git clone git@github.com:itspablomontes/yes-chef.git && cd yes-chef
cp .env.example .env
# Edit .env and set OPENAI_API_KEY=sk-...
docker compose up --build -d
# Wait ~30s for health, then:
uv run python test_stream.py --file data/menu_spec.json
```

Or run locally without Docker: `uv run uvicorn app.main:app --host 0.0.0.0 --port 8000` (requires `uv` and Python 3.12).

---

## Quick start

**Run locally:**

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Or with Docker: `docker compose up`

**Test against the deployed API:**

```bash
uv run python test_stream.py --file data/menu_spec.json --base-url https://yes-chef-production.up.railway.app
```

---

## Testing

`test_stream.py` is the primary way to exercise the API: health check, live TUI, final summary (tokens, schema validity).


| Target   | Command                                                                                                         |
| -------- | --------------------------------------------------------------------------------------------------------------- |
| Deployed | `uv run python test_stream.py --file data/menu_spec.json --base-url https://yes-chef-production.up.railway.app` |
| Local    | `uv run python test_stream.py --file data/menu_spec.json`                                                       |


**Stress testing** ([artifacts/stress-test/](artifacts/stress-test/)):


| Items | Command                                                                                                                               |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------- |
| 100   | `uv run python test_stream.py --file artifacts/stress-test/menu_100_items.json --base-url https://yes-chef-production.up.railway.app` |
| 250   | `uv run python test_stream.py --file artifacts/stress-test/menu_250_items.json --base-url https://yes-chef-production.up.railway.app` |
| 500   | `uv run python test_stream.py --file artifacts/stress-test/menu_500_items.json --base-url https://yes-chef-production.up.railway.app` |


For local runs, omit `--base-url`. Regenerate menus: `uv run python scripts/generate_stress_menus.py`.

**Resumability testing:** Use `--test-resume` to simulate an interrupt and verify resume works. Interrupts after N items (default 3, override with `--resume-after`), then resumes via `POST /estimate/{id}/resume`. Works with all existing flags.


| Example                     | Command                                                                                                                                        |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Local, interrupt after 3    | `uv run python test_stream.py --file data/menu_spec.json --test-resume`                                                                        |
| Deployed, interrupt after 2 | `uv run python test_stream.py --file data/menu_spec.json --base-url https://yes-chef-production.up.railway.app --test-resume --resume-after 2` |


**Benchmarking:** Use `test_stream.py` and compare before/after changes: elapsed time, total tokens (`estimation_metrics`), retries and tool call counts, schema validity, quote completion.

---

## API usage

**Base URL:** `https://yes-chef-production.up.railway.app` (or `http://localhost:8000` for local)


| Method                    | URL / Command                                                                                                                     |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Swagger UI                | [https://yes-chef-production.up.railway.app/docs](https://yes-chef-production.up.railway.app/docs)                                |
| test_stream.py            | `uv run python test_stream.py --file data/menu_spec.json --base-url https://yes-chef-production.up.railway.app`                   |
| test_stream (resume test) | `uv run python test_stream.py --file data/menu_spec.json --base-url https://yes-chef-production.up.railway.app --test-resume`     |
| curl (start)              | `curl -N -X POST https://yes-chef-production.up.railway.app/estimate -H "Content-Type: application/json" -d @data/menu_spec.json` |
| curl (resume)             | `curl -N -X POST https://yes-chef-production.up.railway.app/estimate/{id}/resume`                                                 |


**Request format:** Menu spec JSON with `event`, `date`, `venue`, `guest_count_estimate`, `notes`, `categories` (see [data/menu_spec.json](data/menu_spec.json)).

**Output format:** Output conforms to [data/quote_schema.json](data/quote_schema.json) (line_items, ingredients with source/sysco_item_number, ingredient_cost_per_unit).

### Stats stream (interrupt and resume)

Stats-only SSE endpoints for progress without parsing raw events:


| Endpoint                            | Purpose                                |
| ----------------------------------- | -------------------------------------- |
| `POST /estimate/stream`             | Start estimation, receive stats events |
| `POST /estimate/{id}/resume/stream` | Resume interrupted estimation          |


**Interrupt:** Close connection (Ctrl+C). Capture `estimation_id` from the first event before interrupting.

**Automated test:** `test_stream.py --test-resume` simulates this flow without manual intervention.

**Resume:** `POST /estimate/{id}/resume/stream` with the captured ID.

```bash
# Start stats stream
curl -N -X POST https://yes-chef-production.up.railway.app/estimate/stream \
  -H "Content-Type: application/json" -d @data/menu_spec.json

# Resume (replace {id} with estimation_id from stream)
curl -N -X POST https://yes-chef-production.up.railway.app/estimate/{id}/resume/stream
```

**Polling:** `GET /estimate/{id}` to poll status and retrieve the final quote.

---

## Deployment

Step-by-step runbook for common cloud providers. All paths use the same Dockerfile; differences are in how you provision and configure.


| Provider                 | Steps                                                                                                                                                                                                        |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Railway**              | 1. New project → Deploy from GitHub. 2. Add variable `OPENAI_API_KEY`. 3. Expose port 8000. 4. (Optional) Add volume at `/app/data` for SQLite persistence.                                                  |
| **Render**               | 1. New Web Service → Connect repo. 2. Build: Dockerfile (auto-detected). 3. Add env `OPENAI_API_KEY`. 4. Expose port 8000. 5. Add disk at `/app/data` for SQLite persistence.                                |
| **Fly.io**               | 1. `fly launch` from repo. 2. `fly secrets set OPENAI_API_KEY=sk-...` 3. Ensure `fly.toml` exposes internal port 8000. 4. `fly volumes create data` and mount at `/app/data` for SQLite.                     |
| **AWS EC2**              | 1. Launch Ubuntu AMI, install Docker. 2. Clone repo, run `./scripts/deploy-vm.sh` (prompts for `OPENAI_API_KEY`). 3. Open security group port 8000. 4. Optional: `--systemd` for persistence across reboots. |
| **GCP Compute Engine**   | Same as EC2: Ubuntu VM, install Docker, `./scripts/deploy-vm.sh`, open firewall for port 8000.                                                                                                               |
| **DigitalOcean Droplet** | Same as EC2: Ubuntu, Docker, `./scripts/deploy-vm.sh`, open port 8000 in firewall.                                                                                                                           |


**Common requirements:** `OPENAI_API_KEY` (required). `DATABASE_URL` optional (default SQLite). For SQLite persistence on PaaS, ensure a volume is mounted at `/app/data`. For Postgres, set `DATABASE_URL=postgresql://...`.

### VM script (EC2, GCP, DigitalOcean, etc.)

```bash
./scripts/deploy-vm.sh              # Prompts for OPENAI_API_KEY if .env missing
./scripts/deploy-vm.sh --skip-env    # Use existing .env
./scripts/deploy-vm.sh --systemd    # Start on boot
./scripts/deploy-vm.sh --dry-run    # Preview without building
```

Requires Docker. Script creates `.env`, builds image, runs container with data volume.

---

## Configuration

**Data files:** Menu spec: [data/menu_spec.json](data/menu_spec.json). Catalog: [data/sysco_catalog.csv](data/sysco_catalog.csv).

| Variable                  | Default                                        | Purpose                        |
| ------------------------- | ---------------------------------------------- | ------------------------------ |
| `OPENAI_API_KEY`          | —                                              | Required                       |
| `DATABASE_URL`            | yeschef.db (Docker) / yeschef_local.db (local) | Optional; auto-selected by env |
| `OPENAI_MODEL`            | gpt-4o-mini                                    | Primary model                  |
| `BATCH_SIZE`              | 5                                              | Items per batch                |
| `PLANNING_POOL_SIZE`      | 6                                              | Parallel planning              |
| `TOOL_RESULT_MAX_MATCHES` | 3                                              | Catalog matches per ingredient |


---

## Architecture

The system uses a **durable single-item workflow**: one menu item is the unit of work. Each item gets a fresh LLM prompt and bounded knowledge carry-forward. Interrupting loses at most the current batch; resume reconstructs state from persisted completed items.

The design addresses the challenge's architectural concerns: per-item prompts avoid context degradation; per-item checkpointing enables recoverability; SSE events provide observability.

```mermaid
flowchart TB
    subgraph API [API Layer]
        FastAPI[FastAPI Routes]
        EstService[EstimationService]
        Orchestrator[EstimationOrchestrator]
    end

    subgraph Graph [LangGraph]
        START([START])
        Router{route_work_item}
        ItemWorker[item_worker]
        Reduce[reduce]
        END([END])
    end

    subgraph ItemWorkerInternals [item_worker pipeline]
        Plan[IngredientPlannerNode]
        Resolve[CatalogResolverNode]
        Price[PriceComputerNode]
    end

    FastAPI --> EstService --> Orchestrator
    Orchestrator -->|invokes| Graph
    START --> Router
    Router -->|"items remain"| ItemWorker
    Router -->|"all done"| Reduce
    ItemWorker --> Router
    Reduce --> END

    ItemWorker --> Plan --> Resolve --> Price
```



**Request flow:** FastAPI receives the menu spec, EstimationService creates an estimation and invokes the Orchestrator. The Orchestrator runs the compiled LangGraph and streams SSE events (item_complete, quote_complete, estimation_metrics) back to the client.

**Graph flow:** `route_work_item` checks for unprocessed items. If any remain, control goes to `item_worker`; otherwise to `reduce`. The item_worker processes a batch of items: **plan** (LLM extracts ingredients per item, parallel via PlanningPool), **resolve** (CatalogResolverNode matches ingredients to Sysco catalog with global cache), **price** (PriceComputerNode computes unit costs). Completed items are persisted by the ProgressObserver. When all items are done, `reduce` aggregates them into the final quote.

**Persistence:** Per-item checkpointing on `item_complete`. KnowledgeStore records catalog hits and misses; on resume it is rebuilt from persisted results so the agent does not re-discover the same failures. No growing chat transcript—each item gets a new prompt.

**Summarization:** Not needed—each item gets a fresh prompt and bounded carry-forward (KnowledgeStore), so there is no long transcript to summarize.

**Resumability:** The system is designed for interrupt-and-resume. If the connection drops or the process is interrupted, capture `estimation_id` from the first event, then call `POST /estimate/{id}/resume` (or `POST /estimate/{id}/resume/stream` for stats-only) to continue. Automated testing: `test_stream.py --test-resume` simulates interrupt-after-N-items and verifies resume works.

---

## Future improvements

**Infrastructure**

- Evaluate the possibility of external workflow engine (like Temporal) if concurrent long-running jobs become a constraint
- Structured logging and tracing with Langfuse for observability

**Retrieval and caching**

- Implement hybrid search: incorporate vector-based catalog lookups to enhance fuzzy and semantic matching accuracy
- Ingredient-level caching across estimations (same ingredient + quantity → reuse cost)
- Catalog pre-indexing or embedding for faster resolution

**Quality and evaluation**

- Explicit evaluation runs against known menus with golden outputs
- A/B testing for prompt or model changes
- Validation rules and repair strategies for edge cases (units, allergens, dietary)

**Cost and performance**

- Token budget enforcement
- Model routing (cheaper model for simple items, stronger for complex)

