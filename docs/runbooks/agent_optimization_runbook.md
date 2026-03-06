# Agent Optimization Runbook

## Baseline Capture

1. Start API:
   - `uv run uvicorn app.main:app --host 0.0.0.0 --port 8000`
2. Run challenge stream:
   - `uv run python test_stream.py --file data/menu_spec.json`
3. Record:
   - wall time
   - `estimation_metrics.total_tokens`
   - `llm_calls`, `tool_calls`
   - `rate_limit_retries`, `validation_retries`

## Throughput Tuning

Set in environment:

- `WORKER_CONCURRENCY=2`
- `ITEM_MAX_ITERATIONS=12`
- `ITEM_MAX_RETRIES=2`

Re-run baseline and compare metrics.

## Token/Cost Tuning

Set in environment:

- `TOOL_RESULT_MAX_MATCHES=3`
- `TOKEN_BUDGET_PER_ITEM=6000`
- `OPENAI_REPAIR_MODEL=gpt-5-nano`

Watch emitted progress event `token_budget_warning` for outliers.

## Live Validation Lane

Run:

```bash
RUN_LIVE_LLM_TESTS=1 OPENAI_API_KEY=your_key uv run pytest tests/test_live_llm_flow.py -q
```

Pass criteria:

- includes `quote_complete`
- includes `estimation_complete`
- includes `estimation_metrics`
- quote passes schema validation
