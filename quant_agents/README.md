# A-share Multi-Agent Trading MVP

Lightweight simulation-first framework for A-share main-board strategies.

Scope:

- A-share non-GEM universe by default.
- Multi-agent scoring for trend, capital, information, risk, and decision.
- Paper trading workflow before any real broker integration.
- No external Python dependency in the MVP.

Run:

```bash
python3 -m quant_agents.cli run-sample
python3 -m quant_agents.cli run-paper-sample
python3 -m quant_agents.cli backtest-sample
python3 -m quant_agents.cli optimize-sample
python3 -m quant_agents.cli fetch-free-snapshots --limit 100
python3 -m quant_agents.cli plan-free --limit 100
```

Outputs are written to `runs/`.

Important workflow:

1. After the close, agents generate next-session decisions.
2. On the next trading day, the paper session rolls positions forward for T+1.
3. Previous decisions are rechecked by risk control and executed using the next day's open price when available.
4. The account is marked to market at the close and a new plan is produced.
