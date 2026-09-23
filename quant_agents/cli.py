from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from .backtest import optimize_cycle, optimize_parameters, rolling_optimize, run_backtest
from .config import AppConfig
from .data_provider import load_sample_history, load_sample_snapshots
from .evidence import apply_context_evidence, apply_institutional_evidence, load_evidence, load_institutional_evidence, write_evidence_template
from .evidence_collectors import EastmoneyEvidenceCollector, write_evidence
from .free_data import FreeAshareProvider
from .models import AccountState, StockSnapshot
from .news import InformationEvidenceCollector, write_context_evidence
from .paper import PaperTradingSession
from .pipeline import TradingPipeline
from .reminders import add_apple_reminder
from .sector_flow import apply_global_sector_flow, build_global_sector_flow


def main() -> None:
    parser = argparse.ArgumentParser(prog="quant-agents")
    subparsers = parser.add_subparsers(dest="command", required=True)
    sample = subparsers.add_parser("run-sample", help="Run the built-in paper trading sample.")
    sample.add_argument("--cash", type=float, default=1_000_000, help="Initial cash.")
    sample.add_argument("--out", type=Path, default=Path("runs/sample_report.json"), help="Report path.")
    paper = subparsers.add_parser("run-paper-sample", help="Run multi-day paper trading with sample data.")
    paper.add_argument("--cash", type=float, default=1_000_000, help="Initial cash.")
    paper.add_argument("--days", type=int, default=8, help="Sample trading days.")
    paper.add_argument("--out", type=Path, default=Path("runs/paper_sample_report.json"), help="Report path.")
    backtest = subparsers.add_parser("backtest-sample", help="Run sample backtest.")
    backtest.add_argument("--cash", type=float, default=1_000_000, help="Initial cash.")
    backtest.add_argument("--days", type=int, default=8, help="Sample trading days.")
    backtest.add_argument("--out", type=Path, default=Path("runs/backtest_sample_report.json"), help="Report path.")
    optimize = subparsers.add_parser("optimize-sample", help="Run sample parameter search and rolling validation.")
    optimize.add_argument("--days", type=int, default=8, help="Sample trading days.")
    optimize.add_argument("--out", type=Path, default=Path("runs/optimize_sample_report.json"), help="Report path.")
    free_fetch = subparsers.add_parser("fetch-free-snapshots", help="Fetch free A-share data and build StockSnapshot records.")
    free_fetch.add_argument("--date", type=str, default=None, help="Trade date as YYYYMMDD. Defaults to today.")
    free_fetch.add_argument("--limit", type=int, default=100, help="Max liquidity-ranked stocks to fetch.")
    free_fetch.add_argument("--offset", type=int, default=0, help="Candidate offset after liquidity ranking.")
    free_fetch.add_argument("--lookback-days", type=int, default=120, help="Historical lookback days.")
    free_fetch.add_argument("--pause", type=float, default=0.20, help="Pause seconds between free endpoint requests.")
    free_fetch.add_argument("--kline-source", choices=("eastmoney", "mootdx", "auto"), default="eastmoney", help="Daily K-line source. auto tries mootdx first, then public HTTP fallback.")
    free_fetch.add_argument("--merge", action="store_true", help="Merge fetched snapshots into the output file by code.")
    free_fetch.add_argument("--out", type=Path, default=None, help="Snapshot output path.")
    free_batch = subparsers.add_parser("fetch-free-batch", help="Fetch one free-data batch and merge it into the daily snapshot file.")
    free_batch.add_argument("--date", type=str, default=None, help="Trade date as YYYYMMDD. Defaults to today.")
    free_batch.add_argument("--offset", type=int, default=0, help="Candidate offset after liquidity ranking.")
    free_batch.add_argument("--batch-size", type=int, default=30, help="Number of candidates to attempt.")
    free_batch.add_argument("--lookback-days", type=int, default=120, help="Historical lookback days.")
    free_batch.add_argument("--pause", type=float, default=0.50, help="Pause seconds between free endpoint requests.")
    free_batch.add_argument("--kline-source", choices=("eastmoney", "mootdx", "auto"), default="eastmoney", help="Daily K-line source.")
    free_batch.add_argument("--out", type=Path, default=None, help="Merged snapshot output path.")
    free_plan = subparsers.add_parser("plan-free", help="Fetch free data and generate an after-close plan.")
    free_plan.add_argument("--date", type=str, default=None, help="Trade date as YYYYMMDD. Defaults to today.")
    free_plan.add_argument("--limit", type=int, default=100, help="Max liquidity-ranked stocks to fetch.")
    free_plan.add_argument("--lookback-days", type=int, default=120, help="Historical lookback days.")
    free_plan.add_argument("--pause", type=float, default=0.20, help="Pause seconds between free endpoint requests.")
    free_plan.add_argument("--kline-source", choices=("eastmoney", "mootdx", "auto"), default="eastmoney", help="Daily K-line source.")
    free_plan.add_argument("--cash", type=float, default=1_000_000, help="Paper account cash.")
    free_plan.add_argument("--out", type=Path, default=Path("runs/free_plan_report.json"), help="Plan output path.")
    free_plan.add_argument("--force-rebalance", action="store_true", help="Preview selection even if today is not a rebalance weekday.")
    snapshot_plan = subparsers.add_parser("plan-from-snapshots", help="Generate a plan from an existing StockSnapshot JSON file.")
    snapshot_plan.add_argument("--input", type=Path, required=True, help="Snapshot JSON path.")
    snapshot_plan.add_argument("--cash", type=float, default=1_000_000, help="Paper account cash.")
    snapshot_plan.add_argument("--out", type=Path, default=Path("runs/snapshot_plan_report.json"), help="Plan output path.")
    snapshot_plan.add_argument("--force-rebalance", action="store_true", help="Preview selection even if today is not a rebalance weekday.")
    snapshot_plan.add_argument("--evidence", type=Path, default=None, help="Optional institutional evidence JSON.")
    snapshot_plan.add_argument("--context-evidence", type=Path, default=None, help="Optional Tencent/Tonghuashun/news context evidence JSON.")
    evidence_template = subparsers.add_parser("create-evidence-template", help="Create an institutional evidence JSON template.")
    evidence_template.add_argument("--out", type=Path, default=Path("data/evidence/institutional_evidence_template.json"))
    collect_evidence = subparsers.add_parser("collect-institutional-evidence", help="Collect public institutional evidence and write evidence JSON.")
    collect_evidence.add_argument("--date", type=str, default=None, help="Trade date as YYYYMMDD. Defaults to today.")
    collect_evidence.add_argument("--snapshots", type=Path, default=None, help="Snapshot JSON path used for stock codes.")
    collect_evidence.add_argument("--out", type=Path, default=None, help="Evidence output path.")
    collect_evidence.add_argument("--max-margin-symbols", type=int, default=80, help="Max symbols for margin evidence this run; 0 skips margin, negative means all.")
    collect_evidence.add_argument("--pause", type=float, default=0.25, help="Pause seconds between public endpoint requests.")
    collect_evidence.add_argument("--merge", action="store_true", help="Merge into existing evidence file.")
    collect_info = subparsers.add_parser("collect-info-evidence", help="Collect Tencent quote, Tonghuashun hot theme, and news/announcement evidence.")
    collect_info.add_argument("--date", type=str, default=None, help="Trade date as YYYYMMDD. Defaults to snapshot trade date or today.")
    collect_info.add_argument("--snapshots", type=Path, required=True, help="Snapshot JSON path used for stock codes.")
    collect_info.add_argument("--codes", type=str, default=None, help="Comma-separated stock codes to collect. Defaults to all snapshots.")
    collect_info.add_argument("--limit", type=int, default=0, help="Limit snapshot count for this run; 0 means all.")
    collect_info.add_argument("--out", type=Path, default=None, help="Context evidence output path.")
    collect_info.add_argument("--mode", choices=("pre-open", "post-close", "intraday", "manual"), default="post-close", help="Collection mode for report context.")
    collect_info.add_argument("--lookback-days", type=int, default=3, help="News/announcement lookback window.")
    collect_info.add_argument("--max-news-per-code", type=int, default=12, help="Max news/announcement rows per code per source.")
    collect_info.add_argument("--no-quotes", action="store_true", help="Skip Tencent quote collection.")
    collect_info.add_argument("--no-theme", action="store_true", help="Skip Tonghuashun hot theme collection.")
    collect_info.add_argument("--no-news", action="store_true", help="Skip news/announcement collection.")
    collect_info.add_argument("--global-news-only", action="store_true", help="Use global market news feeds only; skip slower per-code news/announcement calls.")
    collect_info.add_argument("--no-llm", action="store_true", help="Use rule-based news summaries only.")
    collect_info.add_argument("--merge", action="store_true", help="Merge into existing context evidence file.")
    expand = subparsers.add_parser("expand-free-pool", help="Automatically expand the free-data snapshot pool from missing eligible candidates.")
    expand.add_argument("--date", type=str, default=None, help="Trade date as YYYYMMDD. Defaults to today.")
    expand.add_argument("--batch-size", type=int, default=15, help="Number of missing candidates per batch.")
    expand.add_argument("--max-batches", type=int, default=4, help="Max batches to process this run.")
    expand.add_argument("--lookback-days", type=int, default=120, help="Historical lookback days.")
    expand.add_argument("--pause", type=float, default=0.8, help="Pause seconds between free endpoint requests.")
    expand.add_argument("--kline-source", choices=("eastmoney", "mootdx", "auto"), default="eastmoney", help="Daily K-line source.")
    expand.add_argument("--out", type=Path, default=None, help="Merged snapshot output path.")
    expand.add_argument("--plan-out", type=Path, default=None, help="Generated plan output path.")
    expand.add_argument("--cash", type=float, default=1_000_000, help="Paper account cash.")
    expand.add_argument("--force-rebalance", action="store_true", help="Preview selection even if today is not a rebalance weekday.")
    expand.add_argument("--apple-reminder", action="store_true", help="Create an Apple Reminder when all eligible candidates are covered.")

    args = parser.parse_args()
    if args.command == "run-sample":
        run_sample(args.cash, args.out)
    elif args.command == "run-paper-sample":
        run_paper_sample(args.cash, args.days, args.out)
    elif args.command == "backtest-sample":
        run_backtest_sample(args.cash, args.days, args.out)
    elif args.command == "optimize-sample":
        run_optimize_sample(args.days, args.out)
    elif args.command == "fetch-free-snapshots":
        fetch_free_snapshots(args.date, args.limit, args.offset, args.lookback_days, args.pause, args.kline_source, args.merge, args.out)
    elif args.command == "fetch-free-batch":
        fetch_free_batch(args.date, args.offset, args.batch_size, args.lookback_days, args.pause, args.kline_source, args.out)
    elif args.command == "plan-free":
        plan_free(args.date, args.limit, args.lookback_days, args.pause, args.kline_source, args.cash, args.out, args.force_rebalance)
    elif args.command == "plan-from-snapshots":
        plan_from_snapshots(args.input, args.cash, args.out, args.force_rebalance, args.evidence, args.context_evidence)
    elif args.command == "expand-free-pool":
        expand_free_pool(
            args.date,
            args.batch_size,
            args.max_batches,
            args.lookback_days,
            args.pause,
            args.kline_source,
            args.out,
            args.plan_out,
            args.cash,
            args.force_rebalance,
            args.apple_reminder,
        )
    elif args.command == "create-evidence-template":
        write_evidence_template(args.out)
        print(f"template={args.out}")
    elif args.command == "collect-institutional-evidence":
        collect_institutional_evidence(
            args.date,
            args.snapshots,
            args.out,
            args.max_margin_symbols,
            args.pause,
            args.merge,
        )
    elif args.command == "collect-info-evidence":
        collect_info_evidence(
            args.date,
            args.snapshots,
            args.codes,
            args.limit,
            args.out,
            args.mode,
            args.lookback_days,
            args.max_news_per_code,
            not args.no_quotes,
            not args.no_theme,
            not args.no_news,
            args.global_news_only,
            not args.no_llm,
            args.merge,
        )


def run_sample(cash: float, out: Path) -> None:
    account = AccountState(cash=cash)
    account.peak_equity = cash
    account.previous_equity = cash
    snapshots = load_sample_snapshots(_next_rebalance_day(date.today()))
    report = TradingPipeline().run_once(snapshots, account)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"report={out}")
    print(f"universe={','.join(report['universe'])}")
    print(f"equity={report['account']['equity']:.2f}")
    print(f"positions={','.join(report['account']['positions'].keys())}")


def run_paper_sample(cash: float, days: int, out: Path) -> None:
    session = PaperTradingSession(initial_cash=cash)
    reports = [session.run_day(snapshots) for snapshots in load_sample_history(days)]
    _write_report(out, {"reports": reports})
    last_account = reports[-1]["new_plan"]["account"] if reports else {"equity": cash, "positions": {}}
    print(f"report={out}")
    print(f"days={len(reports)}")
    print(f"equity={last_account['equity']:.2f}")
    print(f"positions={','.join(last_account['positions'].keys())}")


def run_backtest_sample(cash: float, days: int, out: Path) -> None:
    report = run_backtest(load_sample_history(days), initial_cash=cash)
    _write_report(out, report)
    print(f"report={out}")
    print(f"total_return={report['metrics']['total_return']:.2%}")
    print(f"max_drawdown={report['metrics']['max_drawdown']:.2%}")
    print(f"calmar={report['metrics']['calmar']:.3f}")


def run_optimize_sample(days: int, out: Path) -> None:
    history = load_sample_history(days)
    report = {
        "top_candidates": optimize_parameters(history)[:10],
        "rolling_windows": rolling_optimize(history),
        "cycle_windows": optimize_cycle(
            history,
            train_days=max(4, days // 2),
            validation_days=max(2, days // 4),
            test_days=max(1, days // 8),
            step=1,
        ),
    }
    _write_report(out, report)
    best = report["top_candidates"][0]
    print(f"report={out}")
    print(f"best_score={best['final_score']:.4f}")
    print(f"best_strategy={best['strategy']}")


def _write_report(out: Path, report: dict[str, object]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def fetch_free_snapshots(
    day_text: str | None,
    limit: int,
    offset: int,
    lookback_days: int,
    pause: float,
    kline_source: str,
    merge: bool,
    out: Path | None,
) -> None:
    trade_date = _parse_cli_date(day_text)
    provider = FreeAshareProvider(request_pause=pause, kline_source=kline_source)
    snapshots = provider.load_snapshots(trade_date=trade_date, lookback_days=lookback_days, limit=limit, offset=offset)
    output = out or Path(f"data/features/free_snapshots_{trade_date:%Y%m%d}.json")
    provider.write_snapshots(snapshots, output, merge=merge)
    print(f"report={output}")
    print(f"snapshots={len(snapshots)}")
    print(f"stats={provider.last_stats}")
    print(f"kline_source={kline_source}")
    print(f"date={trade_date:%Y-%m-%d}")


def fetch_free_batch(
    day_text: str | None,
    offset: int,
    batch_size: int,
    lookback_days: int,
    pause: float,
    kline_source: str,
    out: Path | None,
) -> None:
    trade_date = _parse_cli_date(day_text)
    output = out or Path(f"data/features/free_snapshots_{trade_date:%Y%m%d}.json")
    fetch_free_snapshots(
        day_text=day_text,
        limit=batch_size,
        offset=offset,
        lookback_days=lookback_days,
        pause=pause,
        kline_source=kline_source,
        merge=True,
        out=output,
    )


def plan_free(
    day_text: str | None,
    limit: int,
    lookback_days: int,
    pause: float,
    kline_source: str,
    cash: float,
    out: Path,
    force_rebalance: bool = False,
) -> None:
    trade_date = _parse_cli_date(day_text)
    provider = FreeAshareProvider(request_pause=pause, kline_source=kline_source)
    snapshots = provider.load_snapshots(trade_date=trade_date, lookback_days=lookback_days, limit=limit)
    account = AccountState(cash=cash, peak_equity=cash, previous_equity=cash)
    report = TradingPipeline(_force_rebalance_config() if force_rebalance else None).plan_day(snapshots, account)
    _write_report(out, report)
    print(f"report={out}")
    print(f"snapshots={len(snapshots)}")
    print(f"stats={provider.last_stats}")
    print(f"universe={len(report['universe'])}")
    print(f"decisions={len(report['decisions'])}")


def plan_from_snapshots(
    input_path: Path,
    cash: float,
    out: Path,
    force_rebalance: bool = False,
    evidence: Path | None = None,
    context_evidence: Path | None = None,
) -> None:
    snapshots = _read_snapshot_file(input_path)
    snapshots = apply_institutional_evidence(snapshots, load_institutional_evidence(evidence))
    context = load_evidence(context_evidence)
    sector_flow_state_path = Path("data/features/global_sector_flow_state.json")
    previous_sector_flow = load_optional_json(sector_flow_state_path)
    global_sector_flow = build_global_sector_flow(context, previous_sector_flow)
    context = apply_global_sector_flow(context, global_sector_flow)
    snapshots = apply_context_evidence(snapshots, context)
    account = AccountState(cash=cash, peak_equity=cash, previous_equity=cash)
    report = TradingPipeline(_force_rebalance_config() if force_rebalance else None).plan_day(snapshots, account)
    report["global_sector_flow"] = global_sector_flow
    if global_sector_flow.get("sectors"):
        sector_flow_state_path.parent.mkdir(parents=True, exist_ok=True)
        sector_flow_state_path.write_text(json.dumps(global_sector_flow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_report(out, report)
    print(f"report={out}")
    print(f"snapshots={len(snapshots)}")
    print(f"universe={len(report['universe'])}")
    print(f"decisions={len(report['decisions'])}")


def load_optional_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def expand_free_pool(
    day_text: str | None,
    batch_size: int,
    max_batches: int,
    lookback_days: int,
    pause: float,
    kline_source: str,
    out: Path | None,
    plan_out: Path | None,
    cash: float,
    force_rebalance: bool,
    apple_reminder: bool,
) -> None:
    trade_date = _parse_cli_date(day_text)
    snapshot_path = out or Path(f"data/features/free_snapshots_{trade_date:%Y%m%d}.json")
    skip_path = Path(f"data/features/free_snapshot_skips_{trade_date:%Y%m%d}.json")
    plan_path = plan_out or Path(f"runs/snapshot_plan_{trade_date:%Y%m%d}.json")
    provider = FreeAshareProvider(request_pause=pause, kline_source=kline_source)
    candidates = provider.eligible_candidates(trade_date=trade_date)
    existing_codes = _snapshot_codes(snapshot_path)
    skipped = _read_skip_file(skip_path)
    skipped_codes = set(skipped)
    missing = [
        row for row in candidates if str(row.get("f12", "")) not in existing_codes and str(row.get("f12", "")) not in skipped_codes
    ]

    total_added = 0
    batches: list[dict[str, object]] = []
    for batch_index in range(max(0, max_batches)):
        batch_rows = missing[batch_index * batch_size : (batch_index + 1) * batch_size]
        if not batch_rows:
            break
        snapshots = provider.load_snapshots_from_rows(
            batch_rows,
            trade_date,
            lookback_days=lookback_days,
            use_cache=True,
        )
        provider.write_snapshots(snapshots, snapshot_path, merge=True)
        total_added += len(snapshots)
        batches.append(
            {
                "batch": batch_index + 1,
                "attempted": len(batch_rows),
                "added": len(snapshots),
                "stats": provider.last_stats,
                "failed_codes": provider.last_failed_codes,
                "insufficient_codes": provider.last_insufficient_codes,
                "codes": [snapshot.code for snapshot in snapshots],
            }
        )
        for code in provider.last_insufficient_codes:
            skipped[code] = "insufficient_history"
        if provider.last_insufficient_codes:
            _write_report(skip_path, skipped)
        existing_codes.update(snapshot.code for snapshot in snapshots)
        time_note = f"batch={batch_index + 1} attempted={len(batch_rows)} added={len(snapshots)}"
        print(time_note, flush=True)

    snapshots = _read_snapshot_file(snapshot_path) if snapshot_path.exists() else []
    account = AccountState(cash=cash, peak_equity=cash, previous_equity=cash)
    plan = TradingPipeline(_force_rebalance_config() if force_rebalance else None).plan_day(snapshots, account)
    _write_report(plan_path, plan)

    candidate_codes = {str(row.get("f12", "")) for row in candidates}
    covered_codes = _snapshot_codes(snapshot_path)
    skipped = _read_skip_file(skip_path)
    skipped_codes = set(skipped)
    remaining_codes = sorted(candidate_codes - covered_codes - skipped_codes)
    complete = not remaining_codes
    reminder_result: tuple[bool, str] | None = None
    if complete and apple_reminder:
        marker = Path(f"runs/apple_reminder_full_pool_{trade_date:%Y%m%d}.done")
        if marker.exists():
            reminder_result = (True, "already_created")
        else:
            selected = ", ".join(decision["code"] for decision in plan["decisions"]) or "none"
            reminder_result = add_apple_reminder(
                "A股合规股票池已全部进入快照池",
                f"日期 {trade_date:%Y-%m-%d}，快照 {len(snapshots)} 只，候选 {len(plan['decisions'])} 个：{selected}",
            )
            if reminder_result[0]:
                marker.write_text("created\n", encoding="utf-8")

    summary = {
        "trade_date": f"{trade_date:%Y-%m-%d}",
        "snapshot_path": str(snapshot_path),
        "skip_path": str(skip_path),
        "plan_path": str(plan_path),
        "eligible_candidates": len(candidates),
        "snapshots": len(snapshots),
        "skipped": len(skipped_codes),
        "added_this_run": total_added,
        "remaining": len(remaining_codes),
        "complete": complete,
        "batches": batches,
        "selected": [decision["code"] for decision in plan["decisions"]],
        "apple_reminder": reminder_result,
    }
    summary_path = Path(f"runs/free_pool_expand_{trade_date:%Y%m%d}.json")
    _write_report(summary_path, summary)
    print(f"summary={summary_path}")
    print(f"snapshot_path={snapshot_path}")
    print(f"plan_path={plan_path}")
    print(f"eligible_candidates={len(candidates)}")
    print(f"snapshots={len(snapshots)}")
    print(f"skipped={len(skipped_codes)}")
    print(f"added_this_run={total_added}")
    print(f"remaining={len(remaining_codes)}")
    print(f"complete={complete}")
    print(f"selected={','.join(summary['selected'])}")
    if reminder_result is not None:
        print(f"apple_reminder={reminder_result}")


def collect_institutional_evidence(
    day_text: str | None,
    snapshots_path: Path | None,
    out: Path | None,
    max_margin_symbols: int,
    pause: float,
    merge: bool,
) -> None:
    initial_date = _parse_cli_date(day_text)
    snapshots_path = snapshots_path or Path(f"data/features/free_snapshots_{initial_date:%Y%m%d}.json")
    snapshots = _read_snapshot_file(snapshots_path) if snapshots_path.exists() else []
    trade_date = _snapshot_trade_date(snapshots) if day_text is None and snapshots else initial_date
    evidence_path = out or Path(f"data/evidence/institutional_evidence_{trade_date:%Y%m%d}.json")
    codes = [snapshot.code for snapshot in snapshots]
    collector = EastmoneyEvidenceCollector(pause=pause)
    max_symbols = None if max_margin_symbols < 0 else max(0, max_margin_symbols)
    evidence = collector.collect(trade_date, codes, max_margin_symbols=max_symbols)
    write_evidence(evidence_path, evidence, merge=merge)
    summary = {
        "trade_date": f"{trade_date:%Y-%m-%d}",
        "snapshots_path": str(snapshots_path),
        "evidence_path": str(evidence_path),
        "stats": collector.stats,
        "evidence_codes": sorted(evidence),
    }
    summary_path = Path(f"runs/institutional_evidence_collect_{trade_date:%Y%m%d}.json")
    _write_report(summary_path, summary)
    print(f"summary={summary_path}")
    print(f"evidence={evidence_path}")
    print(f"evidence_codes={len(evidence)}")
    print(f"stats={collector.stats}")


def collect_info_evidence(
    day_text: str | None,
    snapshots_path: Path,
    codes_text: str | None,
    limit: int,
    out: Path | None,
    mode: str,
    lookback_days: int,
    max_news_per_code: int,
    include_quotes: bool,
    include_theme: bool,
    include_news: bool,
    global_news_only: bool,
    include_llm: bool,
    merge: bool,
) -> None:
    snapshots = _read_snapshot_file(snapshots_path)
    if codes_text:
        wanted = {code.strip() for code in codes_text.split(",") if code.strip()}
        snapshots = [snapshot for snapshot in snapshots if snapshot.code in wanted]
    elif limit > 0:
        snapshots = snapshots[:limit]
    initial_date = _parse_cli_date(day_text)
    trade_date = _snapshot_trade_date(snapshots) if day_text is None and snapshots else initial_date
    evidence_path = out or Path(f"data/evidence/context_evidence_{trade_date:%Y%m%d}.json")
    collector = InformationEvidenceCollector(llm=None if include_llm else _DisabledLLM())
    use_cache = mode not in {"intraday", "post-close", "pre-open"}
    initial_evidence = load_evidence(evidence_path) if merge and evidence_path.exists() else {}
    evidence = collector.collect(
        snapshots,
        trade_date,
        mode=mode,
        lookback_days=lookback_days,
        max_news_per_code=max_news_per_code,
        include_quotes=include_quotes,
        include_theme=include_theme,
        include_news=include_news,
        use_cache=use_cache,
        global_news_only=global_news_only,
        initial_evidence=initial_evidence,
    )
    write_context_evidence(evidence_path, evidence, merge=merge)
    summary = {
        "trade_date": f"{trade_date:%Y-%m-%d}",
        "snapshots_path": str(snapshots_path),
        "evidence_path": str(evidence_path),
        "mode": mode,
        "lookback_days": lookback_days,
        "include_quotes": include_quotes,
        "include_theme": include_theme,
        "include_news": include_news,
        "global_news_only": global_news_only,
        "include_llm": include_llm,
        "use_cache": use_cache,
        "stats": collector.stats,
        "evidence_codes": sorted(evidence),
    }
    summary_path = Path(f"runs/info_evidence_collect_{trade_date:%Y%m%d}_{mode.replace('-', '_')}.json")
    _write_report(summary_path, summary)
    print(f"summary={summary_path}")
    print(f"evidence={evidence_path}")
    print(f"evidence_codes={len(evidence)}")
    print(f"stats={collector.stats}")


class _DisabledLLM:
    available = False


def _parse_cli_date(day_text: str | None) -> date:
    if not day_text:
        return date.today()
    return date(int(day_text[:4]), int(day_text[4:6]), int(day_text[6:8]))


def _read_snapshot_file(path: Path) -> list[StockSnapshot]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    snapshots = []
    for row in rows:
        if isinstance(row.get("trade_date"), str):
            row["trade_date"] = date.fromisoformat(row["trade_date"])
        snapshots.append(StockSnapshot(**row))
    return snapshots


def _snapshot_codes(path: Path) -> set[str]:
    if not path.exists():
        return set()
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {str(row["code"]) for row in rows}


def _snapshot_trade_date(snapshots: list[StockSnapshot]) -> date:
    return max(snapshot.trade_date for snapshot in snapshots)


def _read_skip_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return {str(code): str(reason) for code, reason in json.loads(path.read_text(encoding="utf-8")).items()}


def _force_rebalance_config() -> AppConfig:
    config = AppConfig()
    return replace(config, strategy=replace(config.strategy, rebalance_weekdays=(0, 1, 2, 3, 4, 5, 6)))


def _next_rebalance_day(day: date) -> date:
    while day.weekday() not in (0, 3):
        day += timedelta(days=1)
    return day


if __name__ == "__main__":
    main()
