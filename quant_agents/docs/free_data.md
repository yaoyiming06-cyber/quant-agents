# Free Data Mode

The first real-data integration uses public free A-share market data and local caching.

## Commands

Fetch snapshots:

```bash
python3 -m quant_agents.cli fetch-free-snapshots --limit 100 --lookback-days 120
```

Fetch one recoverable batch and merge it into the daily snapshot file:

```bash
python3 -m quant_agents.cli fetch-free-batch --offset 0 --batch-size 30 --pause 0.5
python3 -m quant_agents.cli fetch-free-batch --offset 30 --batch-size 30 --pause 0.5
python3 -m quant_agents.cli fetch-free-batch --offset 60 --batch-size 30 --pause 0.5
```

Generate an after-close plan:

```bash
python3 -m quant_agents.cli plan-free --limit 100 --lookback-days 120 --cash 1000000
```

Create an institutional evidence template and use it in plan generation:

```bash
python3 -m quant_agents.cli create-evidence-template
python3 -m quant_agents.cli collect-institutional-evidence \
  --snapshots data/features/free_snapshots_20260524.json \
  --max-margin-symbols 80 \
  --merge
python3 -m quant_agents.cli plan-from-snapshots \
  --input data/features/free_snapshots_20260524.json \
  --evidence data/evidence/institutional_evidence_20260524.json \
  --force-rebalance
```

Automatically expand the missing eligible pool and generate a plan:

```bash
python3 -m quant_agents.cli expand-free-pool --batch-size 15 --max-batches 8 --pause 0.8 --force-rebalance
```

When all eligible candidates are covered, create an Apple Reminder:

```bash
python3 -m quant_agents.cli expand-free-pool --batch-size 15 --max-batches 8 --pause 0.8 --apple-reminder
```

Apple Reminders may ask macOS for automation permission the first time `osascript` talks to Reminders.

## Data Flow

1. Fetch all A-share spot rows.
2. Filter to main-board non-GEM codes.
3. Sort by turnover amount and keep the top `--limit` names.
4. Fetch daily K-line history for each candidate.
5. Compute moving averages, momentum, volatility, turnover, limit prices, and relative strength.
6. Convert rows into `StockSnapshot`.
7. Score trend, capital, information, fundamental, sentiment, and institutional/quant-participation traces.
8. Generate a multi-agent plan.

## Cache

Raw data is cached under:

```text
data/cache/free/
```

Feature snapshots are written to:

```text
data/features/
```

This keeps later backtests reproducible and reduces pressure on free endpoints.

## Limits

- Free public endpoints may change fields, rate-limit requests, or be delayed.
- For larger universes, prefer `fetch-free-batch`; it merges successful rows into the daily feature file and survives partial failures.
- For unattended expansion, prefer `expand-free-pool`; it detects missing eligible candidates and keeps adding batches until the configured run limit is reached.
- ST status is inferred from stock names in the free spot data.
- Limit-up and limit-down prices are approximated from previous close for main-board non-ST stocks.
- Fundamental fields are limited; PE ranking is used as a placeholder until a richer free fundamental source is added.
- For a full-universe daily run, use a larger `--limit` gradually and rely on cache.
- The institutional/quant participation score is a public-market footprint heuristic. It is not proof that a named institution or quant fund has bought the stock.
- If `--evidence` is provided, the institutional/quant participation score can use structured evidence from dragon-tiger lists, northbound holdings, margin finance, fund holdings, and manual research notes.
- `collect-institutional-evidence` currently collects Eastmoney public dragon-tiger-board and margin-finance evidence. Northbound and fund-holding fields are supported by the schema and can be filled by later collectors.
