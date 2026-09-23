"""Restore one strategy-comparison day from an archived dashboard bundle."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant_agents.strategy_compare import comparison_summary, update_record_outcomes
from tools.build_dashboard_data import build_latest_price_map


def load_dashboard_bundle(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"Cannot find dashboard JSON in {path}")
    return json.loads(text[start : end + 1])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--baseline-data", type=Path, required=True)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--context-evidence", type=Path, required=True)
    args = parser.parse_args()

    bundle = load_dashboard_bundle(args.baseline_data)
    baseline = bundle.get("strategyComparison", {}).get("latest", {})
    if baseline.get("date") != args.date:
        raise ValueError(f"Baseline date is {baseline.get('date')}, expected {args.date}")

    store = json.loads(args.comparison.read_text(encoding="utf-8"))
    snapshots = json.loads(args.snapshot.read_text(encoding="utf-8"))
    context_evidence = json.loads(args.context_evidence.read_text(encoding="utf-8"))
    update_record_outcomes(baseline, build_latest_price_map(snapshots, context_evidence))

    now = datetime.now().isoformat(timespec="seconds")
    baseline["updatedAt"] = now
    history = [row for row in store.get("history", []) if row.get("date") != args.date]
    history.append(baseline)
    history.sort(key=lambda row: str(row.get("date") or ""))

    store["history"] = history
    store["latest"] = baseline
    store["summary"] = comparison_summary(history)
    store["updatedAt"] = now
    store["latestDataDate"] = args.date
    args.comparison.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "date": args.date,
                "A": baseline["outcomes"]["A"],
                "B": baseline["outcomes"]["B"],
                "summary": store["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
