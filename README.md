# Yes Chef

AI agent backend that estimates catering ingredient costs from menu specs using real tool calls against a Sysco catalog.

## Run API

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Run Smoke Stream

```bash
uv run python test_stream.py --file artifacts/live-smoke/small_menu.json
```

## Live LLM Flow Test (No Mocks)

This test exercises the real `/estimate` flow end-to-end using a real model provider.

```bash
RUN_LIVE_LLM_TESTS=1 OPENAI_API_KEY=your_key uv run pytest tests/test_live_llm_flow.py -q
```

## Performance and Cost Controls

Key runtime settings (all read by the API settings layer):

- `WORKER_CONCURRENCY` (default `1`, recommend `2` for throughput)
- `ITEM_MAX_RETRIES` (default `2`)
- `ITEM_MAX_ITERATIONS` (default `12`)
- `TOOL_RESULT_MAX_MATCHES` (default `3`)
- `TOKEN_BUDGET_PER_ITEM` (default `6000`)
- `OPENAI_MODEL` + `OPENAI_REPAIR_MODEL`

## Environment Ownership

The API owns configuration parsing and defaults via `app/infrastructure/settings.py`.
`docker-compose.yml` only passes environment through (`env_file`) and does not hardcode business defaults.

## Benchmarking

Use the stream runner and compare these summary fields before/after changes:

- elapsed time
- total tokens from `estimation_metrics`
- retries and tool call counts
- schema validity and quote completion
