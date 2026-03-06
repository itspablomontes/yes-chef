# Stress Test Menus

Menus for load testing the estimation pipeline at scale.

| File | Items | Use |
|------|-------|-----|
| `menu_100_items.json` | 100 | Medium stress |
| `menu_250_items.json` | 250 | Heavy stress |
| `menu_500_items.json` | 500 | Max stress |

## Regenerate

```bash
uv run python scripts/generate_stress_menus.py
```

## Run

With Docker API running:

```bash
# 100 items
uv run python test_stream.py --file artifacts/stress-test/menu_100_items.json

# 250 items
uv run python test_stream.py --file artifacts/stress-test/menu_250_items.json

# 500 items
uv run python test_stream.py --file artifacts/stress-test/menu_500_items.json
```

Or via API:

```bash
curl -X POST http://localhost:8000/estimate \
  -H "Content-Type: application/json" \
  -d @artifacts/stress-test/menu_100_items.json
```
