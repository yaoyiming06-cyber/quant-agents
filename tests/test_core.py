from __future__ import annotations

import unittest
import json
import os
import subprocess
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

from quant_agents.agents.risk import RiskAgent
from quant_agents.agents.information import InformationAgent
from quant_agents.agents.capital import CapitalAgent
from quant_agents.agents.fundamental import FundamentalAgent
from quant_agents.agents.sentiment import SentimentAgent
from quant_agents.agents.institutional import InstitutionalFlowAgent
from quant_agents.config import AppConfig
from quant_agents.data_provider import load_sample_history, load_sample_snapshots
from quant_agents.free_data import FreeAshareProvider, FreeDataError, _parse_kline, _secid, _yahoo_symbol
from quant_agents.evidence import apply_context_evidence, apply_institutional_evidence
from quant_agents.evidence_collectors import EastmoneyEvidenceCollector, _institution_count, _market_code, assess_dragon_tiger_seat_quality
from quant_agents.env import load_project_env
from quant_agents.llm import DeepSeekClient, LLMConfig, LLMError, _extract_json_object
from quant_agents.market_sources import TencentQuoteClient, _normalise_dataframe_bars, _parse_tencent_quotes
from quant_agents.models import AccountState, Decision, Position, StockSnapshot
from quant_agents.news import InformationEvidenceCollector, NewsEvent, classify_news_text, summarize_news_events
from quant_agents.paper import PaperTradingSession
from quant_agents.pipeline import TradingPipeline
from quant_agents.sector_flow import apply_global_sector_flow, build_global_sector_flow
from quant_agents.simulated_trading import update_simulated_trading
from quant_agents.strategy_compare import build_defensive_strategy_candidates, build_shadow_strategy_candidates, update_strategy_comparison
from quant_agents.universe import filter_universe
from quant_agents import cli as quant_cli
from tools.build_dashboard_data import (
    apply_intraday_health_adjustments,
    apply_latest_history_price,
    build_automation_status,
    build_entry_context,
    build_sector_contexts,
    build_strategy_a2_config,
    build_strategy_simulation_comparison,
    history_membership_update_allowed,
    missing_snapshot_decision_codes,
    output_display_path,
    seed_strategy_a2_state,
    simulation_execution_time,
    update_history,
    update_trade_ledger,
)
from tools import build_dashboard_data as dashboard_builder
from tools.run_intraday_update import due_slot, slot_already_completed, write_state
from tools import run_intraday_update
from tools import collect_overnight_market_context
from tools import run_preopen_check
from tools.run_preopen_check import in_preopen_window
from tools import run_post_close_update
from tools.run_post_close_update import in_post_close_window


def _sim_stock(code: str = "600522", price: float = 10.0, quote_time: str = "20260601130500") -> dict:
    return {
        "code": code,
        "name": "中天科技" if code == "600522" else code,
        "rank": 1,
        "finalScore": 0.82,
        "currentClose": price,
        "riskCheck": "approved",
        "states": {},
        "anomalies": [],
        "latestQuoteTime": quote_time,
    }


def _sector_context(
    theme: str = "光通信",
    avg_pct: float = 0.0,
    up_ratio: float = 0.5,
    hot_score: float = 100.0,
    quote_time: str = "20260603095000",
) -> dict:
    return {
        "primaryTheme": theme,
        "themes": [theme],
        "avgPctChange": avg_pct,
        "upRatio": up_ratio,
        "downRatio": round(1 - up_ratio, 4),
        "hotScore": hot_score,
        "peerCount": 12,
        "latestQuoteTime": quote_time,
    }


def _fresh_quote_quality() -> dict:
    return {
        "freshRatio": 1.0,
        "preferredStaleCount": 0,
        "preferredFreshCount": 1,
        "preferredCount": 1,
    }


def _reversal_stock(
    price: float,
    pct_change: float,
    quote_time: str,
    avg_price: float,
    volume_ratio: float,
    sector_avg: float,
    sector_up_ratio: float,
    flow_status: str = "divergent",
) -> dict:
    return _sim_stock(price=price, quote_time=quote_time) | {
        "finalScore": 0.80,
        "todayPctChange": pct_change,
        "basic": {
            "open": 10.0,
            "latestVolumeRatio": volume_ratio,
            "relativeStrength": 0.80,
        },
        "context": {
            "quote": {
                "avg_price": avg_price,
                "open": 10.0,
                "volume_ratio": volume_ratio,
                "pct_change": pct_change,
            }
        },
        "sectorContext": _sector_context(
            avg_pct=sector_avg,
            up_ratio=sector_up_ratio,
            quote_time=quote_time,
        )
        | {"flowStatus": flow_status},
        "intradayHealth": {"level": "ok", "blockers": [], "reasons": []},
    }


class CoreWorkflowTests(unittest.TestCase):
    def test_global_sector_flow_ranks_hot_sector_and_identifies_leader(self) -> None:
        context = {
            "600487": {
                "quote": {"pct_change": 7.0, "amount_yuan": 3_000_000_000, "volume_ratio": 2.3, "quote_time": "20260605095000"},
                "theme": {"themes": ["光通信"], "hot_rank": 3, "hot_score": 300.0},
            },
            "600522": {
                "quote": {"pct_change": 3.0, "amount_yuan": 2_000_000_000, "volume_ratio": 1.8, "quote_time": "20260605095000"},
                "theme": {"themes": ["光通信"], "hot_rank": 15, "hot_score": 180.0},
            },
            "000021": {
                "quote": {"pct_change": -2.0, "amount_yuan": 2_500_000_000, "volume_ratio": 1.1, "quote_time": "20260605095000"},
                "theme": {"themes": ["存储芯片"], "hot_rank": 35, "hot_score": 80.0},
            },
        }

        result = build_global_sector_flow(context)

        self.assertEqual("光通信", result["hotSectors"][0]["name"])
        self.assertEqual("600487", result["hotSectors"][0]["leader"]["code"])
        self.assertEqual("leader", result["stockSignals"]["600487"]["role"])
        self.assertGreater(result["stockSignals"]["600487"]["scoreBonus"], result["stockSignals"]["600522"]["scoreBonus"])
        self.assertTrue(result["hotSectors"][0]["hotStocks"])

    def test_information_collector_global_news_only_skips_slow_per_code_sources(self) -> None:
        class FakeGlobalNews:
            stats = {}

            @staticmethod
            def available() -> bool:
                return True

            def fetch_cls_alerts(self, trade_date: date, use_cache: bool = True) -> list[dict]:
                return [
                    {
                        "title": "三星海力士扩产 HBM 利好半导体产业链",
                        "content": "半导体和CPO相关公司受益",
                        "source": "fake:cls",
                        "publish_time": "2026-07-01 08:01:00",
                    }
                ]

            def fetch_eastmoney_global_news(self, trade_date: date, use_cache: bool = True) -> list[dict]:
                return []

            def fetch_stock_news(self, *args, **kwargs) -> list[dict]:
                raise AssertionError("per-code stock news should be skipped")

        class SlowPerCodeNews:
            pause = 0

            def fetch_announcements(self, *args, **kwargs) -> list[dict]:
                raise AssertionError("per-code announcements should be skipped")

        snapshot = StockSnapshot(
            code="600522",
            name="中天科技",
            trade_date=date(2026, 7, 1),
            close=10,
            prev_close=9.8,
            ma20=9,
            ma60=8,
            ma60_slope=0.1,
            return_20d=5,
            return_60d=12,
            volatility_20d=3,
            avg_turnover_20d=1_000_000,
            turnover_rate_20d=2,
            relative_strength=0.7,
            listing_days=1000,
            theme_evidence={"themes": ["半导体"]},
        )
        collector = InformationEvidenceCollector(cninfo=SlowPerCodeNews(), akshare_news=FakeGlobalNews())

        evidence = collector.collect(
            [snapshot],
            date(2026, 7, 1),
            include_quotes=False,
            include_theme=False,
            include_news=True,
            max_news_per_code=3,
            global_news_only=True,
            initial_evidence={"600522": {"theme": {"themes": ["半导体"]}}},
        )

        self.assertEqual(1, collector.stats["global_news_events"])
        self.assertEqual(1, collector.stats["news_codes"])
        self.assertEqual(0, collector.stats["cninfo_errors"])
        self.assertEqual(0, collector.stats["stock_news_errors"])
        self.assertEqual("fake:cls", evidence["600522"]["news"]["events"][0]["source"])

    def test_global_sector_flow_enrichment_boosts_leader_and_marks_outflow(self) -> None:
        context = {
            "600487": {"theme": {"themes": ["光通信"]}},
            "000021": {"theme": {"themes": ["存储芯片"]}},
        }
        flow = {
            "stockSignals": {
                "600487": {"primarySector": "光通信", "role": "leader", "scoreBonus": 0.035, "status": "sustained"},
                "000021": {"primarySector": "存储芯片", "role": "member", "scoreBonus": 0.0, "status": "outflow"},
            }
        }

        enriched = apply_global_sector_flow(context, flow)

        self.assertEqual("leader", enriched["600487"]["theme"]["globalSectorFlow"]["role"])
        self.assertEqual(0.035, enriched["600487"]["theme"]["globalSectorFlow"]["scoreBonus"])
        self.assertEqual("outflow", enriched["000021"]["theme"]["globalSectorFlow"]["status"])

    def test_overnight_us_sector_context_enriches_matching_theme(self) -> None:
        context = {
            "600522": {"theme": {"themes": ["半导体", "CPO"]}},
            "000001": {"theme": {"themes": ["银行"]}},
        }
        overnight = {
            "status": "ok",
            "sectors": [
                {
                    "symbol": "SOXX",
                    "name": "半导体",
                    "pctChange": 2.4,
                    "status": "positive",
                    "scoreBonus": 0.04,
                    "keywords": ["半导体", "芯片", "CPO"],
                }
            ],
        }

        enriched = collect_overnight_market_context.apply_overnight_context(context, overnight)

        self.assertEqual("positive", enriched["600522"]["theme"]["overnightUS"]["status"])
        self.assertEqual(0.04, enriched["600522"]["theme"]["overnightUS"]["scoreBonus"])
        self.assertNotIn("overnightUS", enriched["000001"]["theme"])

    def test_dragon_tiger_risky_buyer_seat_gets_small_penalty(self) -> None:
        quality = assess_dragon_tiger_seat_quality(
            {
                "buy_seat_names": ["东方财富证券拉萨团结路第二营业部", "机构专用"],
                "sell_seat_names": ["华泰证券总部"],
            }
        )

        self.assertEqual("risky_hot_money_buy", quality["label"])
        self.assertLess(quality["score_adjustment"], 0)
        self.assertIn("拉萨", quality["risk_seats"][0])

    def test_evidence_collector_zero_margin_symbols_skips_margin_collection(self) -> None:
        class Collector(EastmoneyEvidenceCollector):
            def collect_dragon_tiger(self, trade_date: date) -> dict:
                return {}

            def collect_margin_for_code(self, code: str) -> dict:
                raise AssertionError("margin collection should be skipped")

        collector = Collector(pause=0)
        evidence = collector.collect(date(2026, 7, 15), ["600366", "002821"], max_margin_symbols=0)

        self.assertEqual({}, evidence)
        self.assertEqual(0, collector.stats["margin_attempted"])

    def test_shadow_strategy_boosts_hot_leader_and_penalizes_outflow(self) -> None:
        signals = [
            {"code": "600487", "agent_name": "trend", "score": 0.78, "confidence": 0.8, "risk_flag": False, "reason": "trend"},
            {"code": "600487", "agent_name": "capital", "score": 0.76, "confidence": 0.72, "risk_flag": False, "reason": "capital"},
            {"code": "000021", "agent_name": "trend", "score": 0.78, "confidence": 0.8, "risk_flag": False, "reason": "trend"},
            {"code": "000021", "agent_name": "capital", "score": 0.76, "confidence": 0.72, "risk_flag": False, "reason": "capital"},
        ]
        snapshots = [
            {"code": "600487", "name": "亨通光电", "close": 80.0},
            {"code": "000021", "name": "深科技", "close": 38.0},
        ]
        context = {
            "600487": {
                "quote": {"price": 81.0, "quote_time": "20260605095000", "pct_change": 4.0},
                "theme": {"globalSectorFlow": {"status": "accelerating", "role": "leader", "scoreBonus": 0.035}},
            },
            "000021": {
                "quote": {"price": 38.0, "quote_time": "20260605095000", "pct_change": -1.0},
                "theme": {"globalSectorFlow": {"status": "outflow", "role": "member", "scoreBonus": 0.0}},
            },
        }

        rows = build_shadow_strategy_candidates(signals, snapshots, context, {}, limit=2)

        self.assertEqual("600487", rows[0]["code"])
        self.assertGreater(rows[0]["shadowScore"], rows[1]["shadowScore"])
        self.assertIn("sector_leader", rows[0]["reasons"])
        self.assertIn("sector_outflow_penalty", rows[1]["reasons"])

    def test_defensive_strategy_prefers_resilient_low_risk_candidates(self) -> None:
        preferred = [
            {
                "code": "600522",
                "name": "中天科技",
                "rank": 1,
                "finalScore": 0.80,
                "currentClose": 50.0,
                "latestQuoteTime": "20260622093500",
                "basic": {"latestPctChange": -0.4, "volatility20d": 3.2, "relativeStrength": 0.74, "return20d": 8.0},
                "anomalies": [],
                "states": {"isLimitUp": False, "isSuspended": False},
                "sectorContext": {"flowStatus": "divergent", "upRatio": 0.58, "avgPctChange": -0.2},
            },
            {
                "code": "600584",
                "name": "长电科技",
                "rank": 2,
                "finalScore": 0.82,
                "currentClose": 90.0,
                "latestQuoteTime": "20260622093500",
                "basic": {"latestPctChange": 9.8, "volatility20d": 6.2, "relativeStrength": 0.82, "return20d": 36.0},
                "anomalies": [{"level": "danger", "title": "20日波动率高"}],
                "states": {"isLimitUp": True, "isSuspended": False},
                "sectorContext": {"flowStatus": "overheated", "upRatio": 1.0, "avgPctChange": 7.0},
            },
            {
                "code": "600578",
                "name": "京能电力",
                "rank": 3,
                "finalScore": 0.72,
                "currentClose": 7.5,
                "latestQuoteTime": "20260622093500",
                "basic": {"latestPctChange": -1.2, "volatility20d": 2.1, "relativeStrength": 0.48, "return20d": -2.0},
                "anomalies": [],
                "states": {"isLimitUp": False, "isSuspended": False},
                "sectorContext": {"flowStatus": "outflow", "upRatio": 0.25, "avgPctChange": -2.4},
            },
        ]

        rows = build_defensive_strategy_candidates(preferred, limit=3)

        self.assertEqual("600522", rows[0]["code"])
        self.assertGreater(rows[0]["score"], rows[1]["score"])
        self.assertIn("relative_resilience", rows[0]["reasons"])
        self.assertIn("danger_anomaly_penalty", rows[1]["reasons"])
        self.assertIn("sector_outflow_penalty", rows[2]["reasons"])

    def test_defensive_strategy_can_select_candidates_outside_preferred_pool(self) -> None:
        preferred = [
            {
                "code": "600522",
                "name": "中天科技",
                "rank": 1,
                "finalScore": 0.80,
                "currentClose": 50.0,
                "latestQuoteTime": "20260622093500",
                "basic": {"latestPctChange": 2.8, "volatility20d": 4.8, "relativeStrength": 0.55, "return20d": 18.0},
                "anomalies": [],
                "states": {"isLimitUp": False, "isSuspended": False},
                "sectorContext": {"flowStatus": "divergent", "upRatio": 0.58, "avgPctChange": -0.2},
            }
        ]
        signals = [
            {"code": "600522", "agent_name": "trend", "score": 0.77, "confidence": 0.70, "risk_flag": False},
            {"code": "600522", "agent_name": "capital", "score": 0.76, "confidence": 0.65, "risk_flag": False},
            {"code": "600578", "agent_name": "trend", "score": 0.78, "confidence": 0.72, "risk_flag": False},
            {"code": "600578", "agent_name": "capital", "score": 0.74, "confidence": 0.66, "risk_flag": False},
        ]
        snapshots = [
            {
                "code": "600522",
                "name": "中天科技",
                "trade_date": "2026-06-22",
                "close": 50.0,
                "return_20d": 0.18,
                "volatility_20d": 0.048,
                "relative_strength": 0.55,
                "is_limit_up": False,
                "is_suspended": False,
            },
            {
                "code": "600578",
                "name": "京能电力",
                "trade_date": "2026-06-22",
                "close": 7.5,
                "return_20d": -0.02,
                "volatility_20d": 0.021,
                "relative_strength": 0.74,
                "is_limit_up": False,
                "is_suspended": False,
            },
        ]
        context = {
            "600522": {"quote": {"price": 50.0, "quote_time": "20260622093500", "pct_change": 2.8}},
            "600578": {
                "quote": {"price": 7.5, "quote_time": "20260622093500", "pct_change": -1.2},
                "theme": {"globalSectorFlow": {"status": "divergent", "role": "member"}},
            },
        }

        rows = build_defensive_strategy_candidates(
            preferred,
            signals=signals,
            snapshots=snapshots,
            context_evidence=context,
            latest_price_by_code={},
            limit=1,
        )

        self.assertEqual("600578", rows[0]["code"])
        self.assertIn("relative_resilience", rows[0]["reasons"])
        self.assertIn("low_volatility", rows[0]["reasons"])

    def test_strategy_comparison_history_updates_future_outcomes(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "strategy_comparison.json"
            first = update_strategy_comparison(
                path,
                "2026-06-01",
                "20260601132000",
                [{"code": "600522", "name": "中天科技", "rank": 1, "score": 0.80, "price": 40.0, "quoteTime": "20260601132000"}],
                [{"code": "600487", "name": "亨通光电", "rank": 1, "score": 0.82, "price": 76.0, "quoteTime": "20260601132000"}],
                [{"code": "600578", "name": "京能电力", "rank": 1, "score": 0.78, "price": 7.5, "quoteTime": "20260601132000"}],
                {},
            )
            second = update_strategy_comparison(
                path,
                "2026-06-03",
                "20260603142000",
                [{"code": "000021", "name": "深科技", "rank": 1, "score": 0.79, "price": 38.0, "quoteTime": "20260603142000"}],
                [{"code": "600487", "name": "亨通光电", "rank": 1, "score": 0.83, "price": 92.0, "quoteTime": "20260603142000"}],
                [{"code": "600578", "name": "京能电力", "rank": 1, "score": 0.78, "price": 7.8, "quoteTime": "20260603142000"}],
                {
                    "600522": {"currentClose": 44.0, "latestQuoteTime": "20260603142000"},
                    "600487": {"currentClose": 91.2, "latestQuoteTime": "20260603142000"},
                    "600578": {"currentClose": 7.875, "latestQuoteTime": "20260603142000"},
                },
            )

        self.assertEqual(1, first["latest"]["onlyACount"])
        old_record = next(row for row in second["history"] if row["date"] == "2026-06-01")
        self.assertEqual(10.0, old_record["outcomes"]["A"]["avgGainPct"])
        self.assertEqual(20.0, old_record["outcomes"]["B"]["avgGainPct"])
        self.assertEqual(5.0, old_record["outcomes"]["C"]["avgGainPct"])
        self.assertEqual("B", second["summary"]["leaderByAvgGain"])
        self.assertEqual(5.0, second["summary"]["avgGainPctC"])

    def test_strategy_comparison_preserves_first_same_day_candidates_and_entry_prices(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "strategy_comparison.json"
            update_strategy_comparison(
                path,
                "2026-06-08",
                "20260608093500",
                [{"code": "600522", "name": "中天科技", "rank": 1, "score": 0.80, "price": 46.0, "quoteTime": "20260608093500"}],
                [{"code": "600487", "name": "亨通光电", "rank": 1, "score": 0.82, "price": 76.0, "quoteTime": "20260608093500"}],
                [{"code": "600578", "name": "京能电力", "rank": 1, "score": 0.78, "price": 7.5, "quoteTime": "20260608093500"}],
                {},
            )
            result = update_strategy_comparison(
                path,
                "2026-06-08",
                "20260608112000",
                [{"code": "000021", "name": "深科技", "rank": 1, "score": 0.81, "price": 38.0, "quoteTime": "20260608112000"}],
                [{"code": "002747", "name": "埃斯顿", "rank": 1, "score": 0.84, "price": 34.0, "quoteTime": "20260608112000"}],
                [{"code": "601991", "name": "大唐发电", "rank": 1, "score": 0.79, "price": 8.0, "quoteTime": "20260608112000"}],
                {
                    "600522": {"currentClose": 50.6, "latestQuoteTime": "20260608112000"},
                    "600487": {"currentClose": 83.6, "latestQuoteTime": "20260608112000"},
                    "600578": {"currentClose": 7.875, "latestQuoteTime": "20260608112000"},
                },
            )

        self.assertEqual("600522", result["latest"]["A"][0]["code"])
        self.assertEqual(46.0, result["latest"]["A"][0]["entryPrice"])
        self.assertEqual(10.0, result["latest"]["outcomes"]["A"]["avgGainPct"])
        self.assertEqual(10.0, result["latest"]["onlyA"][0]["gainPct"])
        self.assertEqual("600487", result["latest"]["B"][0]["code"])
        self.assertEqual(10.0, result["latest"]["outcomes"]["B"]["avgGainPct"])
        self.assertEqual(10.0, result["latest"]["onlyB"][0]["gainPct"])
        self.assertEqual("600578", result["latest"]["C"][0]["code"])
        self.assertEqual(5.0, result["latest"]["outcomes"]["C"]["avgGainPct"])
        self.assertEqual(5.0, result["latest"]["onlyC"][0]["gainPct"])

    def test_strategy_comparison_freezes_target_period_returns(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "strategy_comparison.json"
            update_strategy_comparison(
                path,
                "2026-06-01",
                "20260601132000",
                [
                    {
                        "code": "600522",
                        "name": "中天科技",
                        "rank": 1,
                        "score": 0.80,
                        "price": 40.0,
                        "quoteTime": "20260601132000",
                        "selectionDate": "2026-06-01",
                    }
                ],
                [],
                [],
                {},
            )
            update_strategy_comparison(
                path,
                "2026-06-05",
                "20260605150000",
                [],
                [],
                [],
                {
                    "600522": {
                        "dailyCloses": {
                            "2026-06-02": 41.0,
                            "2026-06-03": 42.0,
                            "2026-06-04": 44.0,
                            "2026-06-05": 50.0,
                        },
                        "currentClose": 50.0,
                        "latestQuoteTime": "20260605150000",
                    }
                },
            )
            result = update_strategy_comparison(
                path,
                "2026-06-06",
                "20260606150000",
                [],
                [],
                [],
                {
                    "600522": {
                        "dailyCloses": {
                            "2026-06-02": 41.0,
                            "2026-06-03": 42.0,
                            "2026-06-04": 44.0,
                            "2026-06-05": 50.0,
                            "2026-06-06": 36.0,
                        },
                        "currentClose": 36.0,
                        "latestQuoteTime": "20260606150000",
                    }
                },
            )

        row = next(item for item in result["history"] if item["date"] == "2026-06-01")["A"][0]
        self.assertEqual(2.5, row["return1d"])
        self.assertEqual(10.0, row["return3d"])
        self.assertNotEqual(36.0, row["return3d"])
        self.assertEqual(36.0, row["latestPrice"])

    def test_strategy_comparison_reseeds_same_day_c_when_candidate_universe_changes(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "strategy_comparison.json"
            update_strategy_comparison(
                path,
                "2026-06-08",
                "20260608093500",
                [{"code": "600522", "name": "中天科技", "rank": 1, "score": 0.80, "price": 46.0, "quoteTime": "20260608093500"}],
                [{"code": "600487", "name": "亨通光电", "rank": 1, "score": 0.82, "price": 76.0, "quoteTime": "20260608093500"}],
                [{"code": "600578", "name": "京能电力", "rank": 1, "score": 0.78, "price": 7.5, "quoteTime": "20260608093500"}],
                {},
            )
            result = update_strategy_comparison(
                path,
                "2026-06-08",
                "20260608112000",
                [{"code": "000021", "name": "深科技", "rank": 1, "score": 0.81, "price": 38.0, "quoteTime": "20260608112000"}],
                [{"code": "002747", "name": "埃斯顿", "rank": 1, "score": 0.84, "price": 34.0, "quoteTime": "20260608112000"}],
                [
                    {
                        "code": "601991",
                        "name": "大唐发电",
                        "rank": 1,
                        "score": 0.79,
                        "price": 8.0,
                        "quoteTime": "20260608112000",
                        "candidateUniverse": "market",
                    }
                ],
                {},
            )

        self.assertEqual("600522", result["latest"]["A"][0]["code"])
        self.assertEqual(46.0, result["latest"]["A"][0]["entryPrice"])
        self.assertEqual("600487", result["latest"]["B"][0]["code"])
        self.assertEqual("601991", result["latest"]["C"][0]["code"])
        self.assertEqual("market", result["latest"]["C"][0]["candidateUniverse"])

    def test_strategy_a2_config_disables_mainline_probe(self) -> None:
        config = build_strategy_a2_config("2026-06-16")

        self.assertEqual("A2", config["strategyName"])
        self.assertFalse(config["mainlineProbeEnabled"])
        self.assertEqual("2026-06-16", config["startDate"])

    def test_seed_strategy_a2_state_uses_main_equity_once(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "simulated_trading_a2.json"
            first = seed_strategy_a2_state(path, {"account": {"equity": 1_051_754.3}}, "2026-06-16")
            second = seed_strategy_a2_state(path, {"account": {"equity": 999_999.0}}, "2026-06-17")

        self.assertEqual("A2", first["strategy"])
        self.assertEqual(1_051_754.3, first["account"]["initialCash"])
        self.assertEqual(1_051_754.3, first["account"]["cash"])
        self.assertEqual("2026-06-16", first["startDate"])
        self.assertEqual(1_051_754.3, second["account"]["initialCash"])
        self.assertEqual("2026-06-16", second["startDate"])

    def test_strategy_a2_config_disables_mainline_probe(self) -> None:
        builder = getattr(dashboard_builder, "build_strategy_a2_config", None)
        self.assertIsNotNone(builder)
        config = builder("2026-06-16")

        self.assertEqual("A2", config["strategyName"])
        self.assertFalse(config["mainlineProbeEnabled"])
        self.assertEqual("2026-06-16", config["startDate"])

    def test_seed_strategy_a2_state_uses_main_equity_once(self) -> None:
        builder = getattr(dashboard_builder, "seed_strategy_a2_state", None)
        self.assertIsNotNone(builder)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "simulated_trading_a2.json"
            first = builder(path, {"account": {"equity": 1_051_754.3}}, "2026-06-16")
            second = builder(path, {"account": {"equity": 999_999.0}}, "2026-06-17")

        self.assertEqual("A2", first["strategy"])
        self.assertEqual(1_051_754.3, first["account"]["initialCash"])
        self.assertEqual("2026-06-16", first["startDate"])
        self.assertEqual(1_051_754.3, second["account"]["initialCash"])
        self.assertEqual("2026-06-16", second["startDate"])

    def test_strategy_simulation_comparison_compares_main_and_a2_accounts(self) -> None:
        comparison = build_strategy_simulation_comparison(
            {
                "account": {
                    "equity": 1_050_000,
                    "initialCash": 1_000_000,
                    "totalPnl": 50_000,
                    "totalPnlPct": 5.0,
                    "dayPnl": 1_000,
                    "dayPnlPct": 0.1,
                    "exposurePct": 10.0,
                    "positions": 1,
                },
                "latestFills": [{"status": "filled", "side": "buy", "code": "600522"}],
            },
            {
                "account": {
                    "equity": 1_060_000,
                    "initialCash": 1_050_000,
                    "totalPnl": 10_000,
                    "totalPnlPct": 0.95,
                    "dayPnl": 2_000,
                    "dayPnlPct": 0.19,
                    "exposurePct": 18.0,
                    "positions": 2,
                },
                "latestFills": [{"status": "filled", "side": "buy", "code": "600584"}],
            },
        )

        self.assertEqual("A2", comparison["leaderByEquity"])
        self.assertEqual(10_000, comparison["equityDiff"])
        self.assertEqual(8.0, comparison["exposureDiffPct"])
        self.assertEqual(1, comparison["positionDiff"])
        self.assertEqual("600522", comparison["latestAAction"]["code"])
        self.assertEqual("600584", comparison["latestA2Action"]["code"])
        self.assertNotIn("B", [row["strategy"] for row in comparison["ranked"]])

    def test_capital_and_sentiment_agents_reward_hot_sector_leader_without_overboosting(self) -> None:
        snapshot = load_sample_snapshots()[0]
        plain_capital = CapitalAgent().score(snapshot)
        plain_sentiment = SentimentAgent().score(snapshot)
        leader = replace(
            snapshot,
            theme_evidence={
                "is_hot": True,
                "globalSectorFlow": {
                    "primarySector": "光通信",
                    "role": "leader",
                    "scoreBonus": 0.035,
                    "recognitionScore": 82.0,
                    "status": "sustained",
                },
            },
        )

        boosted_capital = CapitalAgent().score(leader)
        boosted_sentiment = SentimentAgent().score(leader)

        self.assertGreater(boosted_capital.score, plain_capital.score)
        self.assertGreater(boosted_sentiment.score, plain_sentiment.score)
        self.assertLessEqual(boosted_capital.score - plain_capital.score, 0.16)
        self.assertIn("sector_flow", boosted_capital.reason)

    def test_simulated_trading_blocks_new_buy_when_global_sector_flow_reverses(self) -> None:
        stock = _sim_stock(quote_time="20260601132000") | {
            "sectorContext": {
                **_sector_context(avg_pct=-1.6, up_ratio=0.2),
                "flowStatus": "outflow",
                "flowRecognitionScore": 32.0,
            }
        }
        with TemporaryDirectory() as tmp:
            result = update_simulated_trading(
                [stock],
                {"records": []},
                Path(tmp) / "simulated_trading.json",
                "20260601132000",
                "2026-06-01",
                _fresh_quote_quality(),
                {"confirmationsRequired": 1},
            )

        self.assertEqual(0, result["account"]["positions"])
        self.assertIn("sector_flow_outflow", result["pendingSignals"][0]["blockers"])

    def test_simulated_trading_probes_strong_mainline_when_soft_blocked(self) -> None:
        stock = _sim_stock(code="600584", price=30.0, quote_time="20260601130500") | {
            "name": "长电科技",
            "rank": 1,
            "finalScore": 0.78,
            "adjustedScore": 0.78,
            "todayPctChange": 4.2,
            "anomalies": [{"level": "danger", "title": "20日波动率高"}],
            "sectorContext": {
                **_sector_context(theme="半导体", avg_pct=3.2, up_ratio=0.78, hot_score=260.0, quote_time="20260601130500"),
                "flowStatus": "accelerating",
                "flowRole": "leader",
                "flowRecognitionScore": 86.0,
                "netFlowRatio": 5.8,
            },
            "intradayHealth": {"level": "ok", "blockers": [], "reasons": []},
        }
        strong_market = {
            "items": [
                {"symbol": "sh000001", "pctChange": 1.7},
                {"symbol": "sz399006", "pctChange": 1.8},
            ]
        }
        with TemporaryDirectory() as tmp:
            result = update_simulated_trading(
                [stock],
                {"records": []},
                Path(tmp) / "simulated_trading.json",
                "20260601130500",
                "2026-06-01",
                _fresh_quote_quality(),
                {"confirmationsRequired": 2, "maxSingleWeight": 0.1, "maxTotalWeight": 0.5},
                strong_market,
            )

        fill = result["latestFills"][-1]
        position = result["positions"][0]
        signal = result["pendingSignals"][0]
        self.assertEqual("overheated", result["marketRegime"]["regime"])
        self.assertIn("needs_confirmation", signal["blockers"])
        self.assertIn("danger_anomaly", signal["blockers"])
        self.assertIn("score_below_min", signal["blockers"])
        self.assertTrue(signal["mainlineProbe"]["eligible"])
        self.assertEqual("mainline_probe", fill["reason"])
        self.assertEqual("mainline_probe", position["entryChannel"])
        self.assertEqual("mainline_probe", position["positionConviction"])
        self.assertLessEqual(position["targetWeight"], 0.025)

    def test_simulated_trading_keeps_hard_blockers_out_of_mainline_probe(self) -> None:
        stock = _sim_stock(code="600585", price=20.0, quote_time="20260601130500") | {
            "name": "硬风险主线股",
            "finalScore": 0.88,
            "states": {"isLimitUp": True},
            "sectorContext": {
                **_sector_context(theme="CPO", avg_pct=4.0, up_ratio=0.82, hot_score=280.0, quote_time="20260601130500"),
                "flowStatus": "accelerating",
                "flowRole": "leader",
                "flowRecognitionScore": 90.0,
            },
        }
        strong_market = {
            "items": [
                {"symbol": "sh000001", "pctChange": 1.7},
                {"symbol": "sz399006", "pctChange": 1.8},
            ]
        }
        with TemporaryDirectory() as tmp:
            result = update_simulated_trading(
                [stock],
                {"records": []},
                Path(tmp) / "simulated_trading.json",
                "20260601130500",
                "2026-06-01",
                _fresh_quote_quality(),
                {"confirmationsRequired": 2},
                strong_market,
            )

        self.assertEqual(0, result["account"]["positions"])
        self.assertIn("limit_up", result["pendingSignals"][0]["blockers"])
        self.assertFalse(result["pendingSignals"][0]["mainlineProbe"]["eligible"])

    def test_simulated_trading_uses_configured_mainline_recognition_threshold(self) -> None:
        stock = _sim_stock(code="600586", price=20.0, quote_time="20260601130500") | {
            "name": "阈值测试",
            "finalScore": 0.78,
            "sectorContext": {
                **_sector_context(theme="半导体", avg_pct=1.3, up_ratio=0.61, hot_score=160.0, quote_time="20260601130500"),
                "flowStatus": "accelerating",
                "flowRole": "member",
                "flowRecognitionScore": 75.0,
            },
            "intradayHealth": {"level": "ok", "blockers": [], "reasons": []},
        }
        strong_market = {
            "items": [
                {"symbol": "sh000001", "pctChange": 1.7},
                {"symbol": "sz399006", "pctChange": 1.8},
            ]
        }
        with TemporaryDirectory() as tmp:
            result = update_simulated_trading(
                [stock],
                {"records": []},
                Path(tmp) / "simulated_trading.json",
                "20260601130500",
                "2026-06-01",
                _fresh_quote_quality(),
                {"confirmationsRequired": 2, "mainlineProbeMinRecognitionScore": 80.0},
                strong_market,
            )

        self.assertEqual(0, result["account"]["positions"])
        self.assertIn("not_mainline", result["pendingSignals"][0]["mainlineProbe"]["blockers"])

    def test_simulated_trading_records_daily_mark_to_market_returns(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            config = {"confirmationsRequired": 1, "pyramidingEnabled": False}
            update_simulated_trading(
                [_sim_stock(code="002245", price=10.0, quote_time="20260612135000")],
                {"records": []},
                state_path,
                "20260612135000",
                "2026-06-12",
                _fresh_quote_quality(),
                config,
            )
            update_simulated_trading(
                [_sim_stock(code="002245", price=10.5, quote_time="20260615145000")],
                {"records": []},
                state_path,
                "20260615145000",
                "2026-06-15",
                _fresh_quote_quality(),
                config,
            )
            result = update_simulated_trading(
                [_sim_stock(code="002245", price=10.8, quote_time="20260616145000")],
                {"records": []},
                state_path,
                "20260616145000",
                "2026-06-16",
                _fresh_quote_quality(),
                config,
            )

        daily = {row["date"]: row for row in result.get("dailyReturns", [])}
        self.assertIn("2026-06-12", daily)
        self.assertIn("2026-06-15", daily)
        self.assertIn("2026-06-16", daily)
        self.assertGreater(daily["2026-06-15"]["amount"], 0)
        self.assertEqual(result["account"]["dayPnl"], daily["2026-06-16"]["amount"])

    def test_simulated_trading_sells_on_first_confirmed_strong_sector_outflow(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            state_path.write_text(
                json.dumps(
                    {
                        "account": {"initialCash": 1_000_000, "cash": 900_000, "realizedPnl": 0},
                        "positions": [
                            {
                                "code": "600487",
                                "name": "亨通光电",
                                "shares": 1200,
                                "avgCost": 76.677,
                                "costBasis": 92_035.4,
                                "entryTime": "2026-06-01T13:20:08",
                                "entryTradeDate": "2026-06-01",
                            }
                        ],
                        "fills": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = update_simulated_trading(
                [
                    _sim_stock(code="600487", price=74.8, quote_time="20260603095000")
                    | {
                        "sectorContext": {
                            **_sector_context(avg_pct=-1.8, up_ratio=0.18, quote_time="20260603095000"),
                            "flowStatus": "outflow",
                            "netFlowRatio": -3.5,
                        }
                    }
                ],
                {"records": []},
                state_path,
                "20260603095000",
                "2026-06-03",
                _fresh_quote_quality(),
            )

        self.assertEqual(0, result["account"]["positions"])
        self.assertEqual("sector_breakdown", result["latestFills"][-1]["reason"])
        self.assertEqual(1, result["sectorWatch"].get("600487", {}).get("confirmations", 1))

    def test_trading_dashboard_renders_global_sector_flow_leaders_and_hot_stocks(self) -> None:
        app_js = Path("web_dashboard/app.js").read_text(encoding="utf-8")
        trading = app_js.split("function renderTrading()", 1)[1].split("function renderTradeRecords()", 1)[0]
        sector_row = app_js.split("function sectorFlowRow(row, index)", 1)[1].split("function sectorStockChip(stock)", 1)[0]
        missed_tracker = app_js.split("function missedOpportunityTracker", 1)[1].split("function simPositionRow", 1)[0]
        styles = Path("web_dashboard/styles.css").read_text(encoding="utf-8")

        self.assertIn("今日资金主线", trading)
        self.assertIn("sectorFlowRow", trading)
        self.assertIn("hotSectors", trading)
        self.assertIn("trading-workbench", trading)
        self.assertIn("trading-left-stack", trading)
        self.assertIn("trading-review-grid", trading)
        self.assertIn("trading-review-stack", trading)
        self.assertIn("missedOpportunityTracker", trading)
        self.assertIn("missed-opportunity-tracker", missed_tracker)
        self.assertIn("错过标的追踪", missed_tracker)
        self.assertIn("复盘摘要", missed_tracker)
        self.assertIn("未买原因", missed_tracker)
        self.assertIn("入池后涨幅", missed_tracker)
        self.assertIn("相对持仓", missed_tracker)
        self.assertIn("价格轨迹", missed_tracker)
        self.assertLess(trading.index("最近模拟成交"), trading.index("missedOpportunityTracker"))
        self.assertLess(trading.index("missedOpportunityTracker"), trading.index("自动交易规则"))
        self.assertIn("收益记录", trading)
        self.assertIn("returnWindow", trading)
        self.assertIn("strategyComparisonPanel", trading)
        self.assertIn("弱转强", trading)
        self.assertIn("主线试仓", trading)
        self.assertIn("mainlineProbe", app_js)
        self.assertIn("weak_to_strong_confirmed", app_js)
        self.assertIn("策略 A/B/C 跟踪", app_js)
        self.assertIn('pct(summary.avgGainPctA)', app_js)
        self.assertIn('pct(summary.avgGainPctB)', app_js)
        self.assertIn('pct(summary.avgGainPctC)', app_js)
        self.assertNotIn('pct(aOutcome.avgGainPct)', app_js)
        self.assertNotIn('pct(bOutcome.avgGainPct)', app_js)
        self.assertIn("B 独有候选", app_js)
        self.assertIn("C 独有候选", app_js)
        self.assertIn("仅影子策略选中", app_js)
        self.assertIn("仅防守策略选中", app_js)
        self.assertIn("仅主策略选中", app_js)
        self.assertIn('strategyCandidateRow(row, "B")', app_js)
        self.assertIn('strategyCandidateRow(row, "A")', app_js)
        self.assertIn('strategyCandidateRow(row, "C")', app_js)
        self.assertIn("主策略 A", app_js)
        self.assertIn("影子策略 B", app_js)
        self.assertIn("防守策略 C", app_js)
        self.assertNotIn('|| "shadow"', app_js)
        self.assertIn("return-window-body", trading)
        self.assertLess(trading.index("本轮自动指令"), trading.index("收益记录"))
        self.assertIn(".trading-workbench", styles)
        self.assertIn(".missed-opportunity-tracker", styles)
        self.assertIn(".missed-opportunity-summary", styles)
        self.assertIn(".missed-opportunity-row", styles)
        self.assertIn(".missed-opportunity-flow", styles)
        self.assertIn(".return-window", styles)
        self.assertNotIn("板块涨幅", sector_row)
        self.assertNotIn("上涨占比", sector_row)
        self.assertIn("龙头", app_js)
        self.assertIn("function sectorStockHref(stock)", app_js)
        self.assertIn("function xueqiuStockUrl(code)", app_js)
        self.assertIn("target=\"_blank\"", app_js)
        self.assertIn("rel=\"noopener noreferrer\"", app_js)
        self.assertIn("xueqiu.com/S/", app_js)
        self.assertIn("热门股", app_js)
        self.assertIn("function tradingReturnSnapshot()", app_js)
        self.assertIn("function returnViewForPeriod(", app_js)
        self.assertIn("function buildWeeklyReturnCalendar(", app_js)
        self.assertIn("function buildMonthlyReturnCalendar(", app_js)
        self.assertIn("function buildYearlyReturnCalendar(", app_js)
        self.assertIn("function stockReturnRanking(periodKey, currentDate)", app_js)
        self.assertIn("日内排行", app_js)
        self.assertIn("周内排行", app_js)
        self.assertIn("月内排行", app_js)
        self.assertIn("年内排行", app_js)
        self.assertIn("function accountReturnSnapshot(", app_js)
        self.assertIn("function reconcileReturnPeriodsWithAccount(", app_js)
        self.assertIn("periods.year.amount = accountReturn.amount", app_js)
        self.assertIn("periods.month.amount = accountReturn.amount", app_js)
        self.assertIn("function returnReconciliationLine(", app_js)
        self.assertIn("reconciliationAmount", app_js)
        self.assertIn("未分配历史收益", app_js)
        self.assertIn("按账户总权益估算", app_js)
        self.assertIn("function Hyperframes", app_js)
        self.assertIn("收益日历", app_js)
        self.assertIn("股票收益排行", app_js)
        self.assertIn(".return-calendar.is-week", styles)
        self.assertIn(".return-calendar.is-month", styles)
        self.assertIn(".return-calendar.is-year", styles)

    def test_a2_comparison_replaces_b_simulation_page(self) -> None:
        app_js = Path("web_dashboard/app.js").read_text(encoding="utf-8")
        trading = Path("web_dashboard/trading.html").read_text(encoding="utf-8")
        build_script = Path("tools/build_dashboard_data.py").read_text(encoding="utf-8")

        self.assertIn("simulatedTradingA2", build_script)
        self.assertIn("simulatedTradingA2", app_js)
        self.assertIn("策略 A / A2 对照", app_js)
        self.assertIn("手续费", app_js)
        self.assertIn("滑点", app_js)
        self.assertNotIn("simulatedTradingB", build_script)
        self.assertNotIn("simulatedTradingB", app_js)
        self.assertNotIn("./strategy-b.html", trading)
        self.assertFalse(Path("web_dashboard/strategy-b.html").exists())
        self.assertIn("strategySimulationComparison", app_js)
        self.assertIn("策略 A/B/C 跟踪", app_js)

    def test_sidebar_links_close_brief_and_daily_news(self) -> None:
        pages = [
            "index.html",
            "pool.html",
            "history.html",
            "anomalies.html",
            "agents.html",
            "trading.html",
            "information.html",
            "stock.html",
            "trade-records.html",
            "daily-news.html",
            "close-brief.html",
        ]
        for page_name in pages:
            with self.subTest(page=page_name):
                html = Path("web_dashboard", page_name).read_text(encoding="utf-8")
                self.assertIn('href="./close-brief.html"', html)
                self.assertIn(">收盘简报</a>", html)
                self.assertIn('href="./daily-news.html"', html)
                self.assertIn(">每日新闻</a>", html)

    def test_daily_news_page_groups_positive_and_negative_news(self) -> None:
        page = Path("web_dashboard/daily-news.html").read_text(encoding="utf-8")
        app_js = Path("web_dashboard/app.js").read_text(encoding="utf-8")

        self.assertIn('data-page="daily-news"', page)
        self.assertIn("function renderDailyNews()", app_js)
        self.assertIn("function dailyNewsRowsByPolarity(", app_js)
        self.assertIn("每日新闻", app_js)
        self.assertIn("利好", app_js)
        self.assertIn("利空", app_js)
        self.assertIn('page === "daily-news"', app_js)

    def test_close_brief_includes_trade_actions_and_reasons(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            dashboard = root / "web_dashboard"
            dashboard.mkdir()
            (dashboard / "data.js").write_text(
                "window.QA_DATA = "
                + json.dumps(
                    {
                        "automationStatus": {
                            "postClose": {
                                "status": "ok",
                                "label": "盘后收盘定稿",
                                "lastRun": "2026-06-29T15:10:00+08:00",
                            }
                        },
                        "preferred": [
                            {
                                "code": "600522",
                                "name": "中天科技",
                                "rank": 1,
                            }
                        ],
                        "simulatedTrading": {
                            "latestFills": [
                                {
                                    "status": "filled",
                                    "side": "buy",
                                    "code": "600522",
                                    "name": "中天科技",
                                    "reason": "confirmed_signal",
                                    "time": "2026-06-29T10:30:00",
                                }
                            ],
                            "orders": [
                                {
                                    "side": "sell",
                                    "code": "000021",
                                    "name": "深科技",
                                    "reason": "sector_breakdown",
                                    "time": "2026-06-29T14:45:00",
                                }
                            ],
                            "pendingSignals": [
                                {
                                    "code": "600584",
                                    "name": "长电科技",
                                    "blockers": ["score_below_min"],
                                    "adjustedScore": 0.78,
                                }
                            ],
                        },
                    },
                    ensure_ascii=False,
                )
                + ";",
                encoding="utf-8",
            )

            with patch("tools.run_post_close_update.ROOT", root):
                run_post_close_update.write_close_brief(
                    dashboard / "close-brief.html",
                    {"status": "ok", "remaining": 0},
                    {
                        "decisions": [
                            {
                                "code": "600522",
                                "final_score": 0.81,
                                "risk_check": "approved",
                                "reason": "capital:avg_turnover=18422892902, turnover_rate=9.35%; information:no_material_event; trend:trend_quality=1.00, r20=39.46%",
                            }
                        ]
                    },
                    {"snapshots": 10, "eligible_candidates": 10},
                )

            html = (dashboard / "close-brief.html").read_text(encoding="utf-8")

        self.assertIn('data-page="close-brief"', html)
        self.assertIn('class="side-nav"', html)
        self.assertIn('<main id="app" class="content"', html)
        self.assertIn("./data.js", html)
        self.assertIn("./app.js", html)
        self.assertIn("window.CLOSE_BRIEF_DATA", html)
        self.assertIn("今日系统运行", html)
        self.assertIn("今日操作与原因", html)
        self.assertIn("中天科技", html)
        self.assertIn("行情确认", html)
        self.assertIn("板块破位", html)
        self.assertIn("评分未过线", html)
        self.assertIn("量能活跃", html)
        self.assertIn('"decisionRows":[{"rank":1,"code":"600522","name":"中天科技"', html)
        self.assertNotIn('"decisionRows":[{"rank":1,"code":"600522","name":"600522"', html)
        self.assertNotIn("<style>", html)
        self.assertNotIn("<table>", html)
        self.assertNotIn("capital:avg_turnover", html)

    def test_close_brief_reuses_dashboard_automation_status_and_overlays_current_post_close_report(self) -> None:
        dashboard = {
            "automationStatus": {
                "date": "2026-07-01",
                "summaryStatus": "ok",
                "items": [
                    {
                        "key": "midday",
                        "label": "中午收盘",
                        "status": "ok",
                        "statusText": "正常",
                        "note": "15:05",
                    },
                    {
                        "key": "post_close",
                        "label": "下午收盘",
                        "status": "missing",
                        "statusText": "未运行",
                        "note": "未找到运行报告",
                    },
                    {
                        "key": "preopen",
                        "label": "盘前预评",
                        "status": "ok",
                        "statusText": "正常",
                        "note": "新闻延后补抓；盘前报价/主题/机构/隔夜重排已完成",
                    },
                ],
            }
        }
        report = {
            "status": "ok",
            "run_date": "20260701",
            "finished_at": "2026-07-01T15:27:35+08:00",
            "remaining": 0,
        }

        payload = run_post_close_update.close_brief_payload(
            report,
            {"decisions": []},
            {"snapshots": 761, "eligible_candidates": 761},
            dashboard,
        )

        self.assertEqual(payload["summary"]["status"], "正常")
        self.assertEqual(payload["summary"]["automationStatus"], "ok")
        post_close = next(row for row in payload["automationRows"] if row["key"] == "post_close")
        self.assertEqual(post_close["status"], "ok")
        self.assertEqual(post_close["statusText"], "正常")
        self.assertEqual(post_close["finishedAt"], "2026-07-01T15:27:35+08:00")

    def test_daily_news_filters_market_data_and_keeps_message_news(self) -> None:
        app_js = Path("web_dashboard/app.js").read_text(encoding="utf-8")

        self.assertFalse(
            dashboard_builder.is_message_news_event(
                {
                    "title": "今日盘中突破半年线个股",
                    "summary": "当日涨跌较大，20日均换手偏高。",
                }
            )
        )
        self.assertFalse(
            dashboard_builder.is_message_news_event(
                {
                    "title": "中国AI 50概念下跌4.76%，主力资金净流出46股",
                    "summary": "板块资金流出。",
                }
            )
        )
        self.assertTrue(
            dashboard_builder.is_message_news_event(
                {
                    "title": "韩国800万亿韩元押注半导体 三星和SK海力士宣布千万亿级投资计划",
                    "summary": "三星、SK海力士拟加大HBM与存储芯片投资。",
                }
            )
        )
        self.assertTrue(
            dashboard_builder.is_message_news_event(
                {
                    "title": "药明康德：完成10亿元股份回购计划",
                    "summary": "公司公告回购方案实施完毕。",
                }
            )
        )
        self.assertIn("function isMessageNewsEvent(", app_js)
        self.assertIn("MESSAGE_NEWS_KEYWORDS", app_js)
        self.assertIn("MARKET_DATA_NEWS_KEYWORDS", app_js)
        self.assertIn(".filter(isMessageNewsEvent)", app_js)

    def test_daily_news_groups_one_news_with_multiple_related_stocks(self) -> None:
        app_js = Path("web_dashboard/app.js").read_text(encoding="utf-8")
        styles = Path("web_dashboard/styles.css").read_text(encoding="utf-8")

        self.assertIn("function aggregateDailyNewsRows(", app_js)
        self.assertIn("function dailyNewsRelatedStocks(", app_js)
        self.assertIn("function isTrackedNewsStock(", app_js)
        self.assertIn("function dailyNewsEventKey(", app_js)
        self.assertIn("相关标的", app_js)
        self.assertIn("daily-news-related-stock", app_js)
        self.assertIn("is-tracked", app_js)
        self.assertIn(".daily-news-related-stock.is-tracked", styles)

    def test_sim_position_row_shows_pnl_amount_and_pct(self) -> None:
        app_js = Path("web_dashboard/app.js").read_text(encoding="utf-8")

        self.assertIn("收益", app_js)
        self.assertIn("signedMoneyFull(row.unrealizedPnl)", app_js)
        self.assertIn("pct(row.pnlPct)", app_js)

    def test_stock_chart_data_builds_daily_ma_intraday_points_and_markers(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kline_dir = root / "data" / "cache" / "source" / "mootdx"
            archive_dir = root / "web_dashboard" / "archive"
            kline_dir.mkdir(parents=True)
            archive_dir.mkdir(parents=True)
            rows = [
                {
                    "date": f"2026-06-{day:02d}",
                    "open": 10 + day,
                    "close": 10.5 + day,
                    "high": 11 + day,
                    "low": 9 + day,
                    "volume": 1000 * day,
                    "amount": 100000 * day,
                    "pct_change": 1.2,
                    "change": 0.3,
                    "turnover": 0.5 + day / 10,
                }
                for day in range(1, 22)
            ]
            (kline_dir / "600522_20260601_20260621.json").write_text(json.dumps(rows), encoding="utf-8")
            snapshot = {
                "meta": {"assetVersion": "20260621100508"},
                "preferred": [
                    {
                        "code": "600522",
                        "name": "中天科技",
                        "currentClose": 32.8,
                        "latestQuoteTime": "20260621100503",
                        "todayPctChange": 2.5,
                        "todayPriceChange": 0.8,
                        "context": {
                            "quote": {
                                "price": 32.8,
                                "quote_time": "20260621100503",
                                "high": 33.2,
                                "low": 31.6,
                                "open": 31.9,
                                "turnover_rate_pct": 1.2,
                                "volume_ratio": 1.4,
                                "total_market_cap_100m": 320.5,
                                "float_market_cap_100m": 260.4,
                                "pe_dynamic": 24.2,
                                "amount_yuan": 880000000,
                                "pct_change": 2.5,
                                "change": 0.8,
                            }
                        },
                    }
                ],
            }
            (archive_dir / "data_20260621100508.js").write_text(
                "window.QA_DATA = " + json.dumps(snapshot, ensure_ascii=False) + ";\n",
                encoding="utf-8",
            )
            ledger_snapshot = {
                "meta": {"assetVersion": "20260622093508"},
                "preferred": [],
                "tradeLedger": {
                    "records": [
                        {
                            "code": "600522",
                            "name": "中天科技",
                            "currentPrice": 33.6,
                            "currentPriceTime": "2026-06-22T09:35:03+08:00",
                            "currentPctChange": 1.1,
                        }
                    ]
                },
            }
            (archive_dir / "data_20260622093508.js").write_text(
                "window.QA_DATA = " + json.dumps(ledger_snapshot, ensure_ascii=False) + ";\n",
                encoding="utf-8",
            )
            simulated_a = {
                "fills": [
                    {
                        "tradeDate": "2026-06-21",
                        "time": "2026-06-21T10:05:03",
                        "side": "buy",
                        "code": "600522",
                        "price": 32.7,
                        "status": "filled",
                    }
                ]
            }
            simulated_b = {
                "fills": [
                    {
                        "tradeDate": "2026-06-21",
                        "time": "2026-06-21T14:20:03",
                        "side": "sell",
                        "code": "600522",
                        "price": 33.1,
                        "status": "filled",
                    }
                ]
            }

            chart_data = dashboard_builder.build_stock_chart_data(
                [{"code": "600522", "name": "中天科技"}],
                {"records": []},
                simulated_a,
                simulated_b,
                root=root,
            )

        daily = chart_data["dailyByCode"]["600522"]
        latest = daily[-1]
        self.assertEqual("600522", chart_data["codes"][0])
        self.assertEqual(22, len(daily))
        self.assertEqual({"ma5", "ma10", "ma20"}, set(latest["ma"]))
        self.assertNotIn("ma30", latest)
        self.assertNotIn("ma30", latest["ma"])
        self.assertEqual("2026-06-22", latest["date"])
        self.assertEqual(33.6, latest["close"])
        self.assertAlmostEqual(30.98, latest["ma"]["ma5"])
        self.assertAlmostEqual(28.24, latest["ma"]["ma10"])
        self.assertAlmostEqual(23.12, latest["ma"]["ma20"])
        intraday = chart_data["intradayByCode"]["600522"]["2026-06-21"]
        self.assertEqual(1, len(intraday))
        self.assertEqual("10:05", intraday[0]["time"])
        self.assertEqual(32.8, intraday[0]["price"])
        self.assertEqual(33.2, intraday[0]["quote"]["high"])
        self.assertEqual(1.2, intraday[0]["quote"]["turnoverRate"])
        ledger_intraday = chart_data["intradayByCode"]["600522"]["2026-06-22"]
        self.assertEqual("09:35", ledger_intraday[0]["time"])
        self.assertEqual(33.6, ledger_intraday[0]["price"])
        self.assertEqual(1.1, ledger_intraday[0]["pctChange"])
        markers = chart_data["markersByCode"]["600522"]
        self.assertEqual(["A", "A2"], [row["strategy"] for row in markers])
        self.assertEqual(["buy", "sell"], [row["side"] for row in markers])

    def test_stock_chart_data_can_skip_archive_scan_for_intraday_refresh(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_dir = root / "web_dashboard" / "archive"
            archive_dir.mkdir(parents=True)
            (archive_dir / "data_20260621100508.js").write_text(
                "window.QA_DATA = {};",
                encoding="utf-8",
            )
            preferred = [
                {
                    "code": "600522",
                    "name": "中天科技",
                    "currentClose": 10.5,
                    "latestQuoteTime": "20260727134503",
                    "todayPctChange": 1.2,
                    "context": {"quote": {"price": 10.5, "quote_time": "20260727134503", "pct_change": 1.2}},
                }
            ]

            with patch("tools.build_dashboard_data.load_dashboard_archive", side_effect=AssertionError("archive should not be read")):
                with patch("tools.build_dashboard_data.daily_chart_rows", side_effect=AssertionError("daily cache should not be read")):
                    chart_data = dashboard_builder.build_stock_chart_data(
                        preferred,
                        {"records": []},
                        {},
                        {},
                        root=root,
                        include_archive_intraday=False,
                        include_daily_cache=False,
                    )

        intraday = chart_data["intradayByCode"]["600522"]["2026-07-27"]
        self.assertEqual(1, len(intraday))
        self.assertEqual("13:45", intraday[0]["time"])
        self.assertEqual(10.5, chart_data["latestQuoteByCode"]["600522"]["price"])

    def test_stock_chart_uses_current_preferred_quote_for_today_tail(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kline_dir = root / "data" / "cache" / "source" / "mootdx"
            archive_dir = root / "web_dashboard" / "archive"
            kline_dir.mkdir(parents=True)
            archive_dir.mkdir(parents=True)
            rows = [
                {
                    "date": f"2026-06-{day:02d}",
                    "open": 10 + day / 10,
                    "close": 10 + day / 10,
                    "high": 10.2 + day / 10,
                    "low": 9.8 + day / 10,
                    "volume": 1000,
                    "amount": 100000,
                    "pct_change": 0.0,
                    "change": 0.0,
                    "turnover": 1.0,
                }
                for day in range(11, 30)
            ]
            rows[-1]["close"] = 11.83
            rows.append(
                {
                    "date": "2026-06-30",
                    "open": 11.95,
                    "close": 12.5,
                    "high": 12.68,
                    "low": 11.9,
                    "volume": 1200,
                    "amount": 1194245266,
                    "pct_change": 5.66,
                    "change": 0.67,
                    "turnover": 9.1,
                }
            )
            (kline_dir / "002745_20260611_20260630.json").write_text(json.dumps(rows), encoding="utf-8")
            archive_snapshot = {
                "meta": {"assetVersion": "20260630145000"},
                "preferred": [
                    {
                        "code": "002745",
                        "name": "木林森",
                        "currentClose": 12.5,
                        "latestQuoteTime": "20260630145000",
                    }
                ],
            }
            (archive_dir / "data_20260630145000.js").write_text(
                "window.QA_DATA = " + json.dumps(archive_snapshot, ensure_ascii=False) + ";\n",
                encoding="utf-8",
            )
            preferred = [
                {
                    "code": "002745",
                    "name": "木林森",
                    "currentClose": 12.55,
                    "latestQuoteTime": "20260630150415",
                    "todayPctChange": 6.09,
                    "todayPriceChange": 0.72,
                    "context": {
                        "quote": {
                            "price": 12.55,
                            "quote_time": "20260630150415",
                            "high": 12.68,
                            "low": 11.9,
                            "open": 11.95,
                            "turnover_rate_pct": 9.1,
                            "volume_ratio": 0.89,
                            "amount_yuan": 1194245266,
                            "pct_change": 6.09,
                            "change": 0.72,
                        }
                    },
                }
            ]

            chart_data = dashboard_builder.build_stock_chart_data(
                preferred,
                {"records": []},
                {},
                {},
                root=root,
            )

        latest_daily = chart_data["dailyByCode"]["002745"][-1]
        today_points = chart_data["intradayByCode"]["002745"]["2026-06-30"]
        self.assertEqual("2026-06-30", latest_daily["date"])
        self.assertEqual(12.55, latest_daily["close"])
        self.assertEqual(6.09, latest_daily["pctChange"])
        self.assertEqual(0.72, latest_daily["change"])
        self.assertEqual("15:04", today_points[-1]["time"])
        self.assertEqual(12.55, today_points[-1]["price"])
        self.assertEqual(12.55, chart_data["latestQuoteByCode"]["002745"]["price"])

    def test_daily_chart_rows_calculates_historical_daily_pct_change(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            kline_dir = root / "data" / "cache" / "source" / "mootdx"
            kline_dir.mkdir(parents=True)
            rows = [
                {"date": "2026-06-01", "open": 9.8, "close": 10.0, "high": 10.2, "low": 9.7, "pct_change": 0, "change": 0},
                {"date": "2026-06-02", "open": 10.1, "close": 11.0, "high": 11.2, "low": 10.0, "pct_change": 0, "change": 0},
                {"date": "2026-06-03", "open": 10.9, "close": 10.45, "high": 11.0, "low": 10.3, "pct_change": 0, "change": 0},
            ]
            (kline_dir / "600522_20260601_20260603.json").write_text(json.dumps(rows), encoding="utf-8")

            daily = dashboard_builder.daily_chart_rows("600522", root=root)

        self.assertIsNone(daily[0]["pctChange"])
        self.assertIsNone(daily[0]["change"])
        self.assertEqual(10.0, daily[1]["pctChange"])
        self.assertEqual(1.0, daily[1]["change"])
        self.assertEqual(-5.0, daily[2]["pctChange"])
        self.assertEqual(-0.55, daily[2]["change"])

    def test_stock_detail_chart_ui_contract_is_wired(self) -> None:
        app_js = Path("web_dashboard/app.js").read_text(encoding="utf-8")
        styles = Path("web_dashboard/styles.css").read_text(encoding="utf-8")

        self.assertIn("function renderStockCharts(", app_js)
        self.assertIn("function renderStockQuoteStrip(", app_js)
        self.assertIn("function drawDailyKChart(", app_js)
        self.assertIn("function drawIntradaySnapshotChart(", app_js)
        self.assertIn("MA5", app_js)
        self.assertIn("MA10", app_js)
        self.assertIn("MA20", app_js)
        self.assertNotIn("MA30", app_js)
        self.assertIn('class="ma-label ${key}-label"', app_js)
        self.assertIn("data-chart-mode=\"daily\"", app_js)
        self.assertIn("data-chart-mode=\"intraday\"", app_js)
        self.assertIn('toggleAttribute("hidden"', app_js)
        self.assertIn("switchStockChartMode(code, \"intraday\", day.date)", app_js)
        self.assertNotIn("intradaySvg.addEventListener(\"click\"", app_js)
        self.assertIn("data-stock-chart-tooltip", app_js)
        self.assertIn("dailyTooltipHtml(day)", app_js)
        self.assertIn("chart-tooltip-price", app_js)
        self.assertIn("chart-tooltip-date", app_js)
        self.assertIn("当日涨幅", app_js)
        self.assertIn("showChartTooltip(root, tooltip, event, dailyTooltipHtml(day), true)", app_js)
        self.assertIn("tooltip.parentElement", app_js)
        self.assertNotIn("const rect = root.getBoundingClientRect();", app_js)
        self.assertIn("当日高点", app_js)
        self.assertIn("换手率", app_js)
        self.assertIn("量比", app_js)
        self.assertIn("市盈率", app_js)
        self.assertIn("成交额", app_js)
        self.assertIn(".stock-chart-card", styles)
        self.assertIn(".quote-strip-price", styles)
        self.assertIn(".stock-quote-strip.is-compact", styles)
        self.assertIn(".chart-tooltip-date", styles)
        self.assertIn(".ma-line.ma5", styles)
        self.assertIn(".ma5-label", styles)
        self.assertNotIn("fill: #fbbc04", styles)
        self.assertNotIn("fill: #1a73e8", styles)
        self.assertNotIn("fill: #7e57c2", styles)
        self.assertIn(".ratio-hot", styles)
        self.assertIn(".ratio-cool", styles)

    def test_intraday_snapshot_completeness_script_reports_missing_slots(self) -> None:
        from tools import check_intraday_snapshot_completeness as completeness

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_dir = root / "web_dashboard" / "archive"
            archive_dir.mkdir(parents=True)
            for stamp in ("20260621093508", "20260621100508"):
                payload = {
                    "preferred": [
                        {
                            "code": "600522",
                            "name": "中天科技",
                            "currentClose": 32.8,
                            "latestQuoteTime": f"{stamp[:8]}{stamp[8:14]}",
                        }
                    ]
                }
                (archive_dir / f"data_{stamp}.js").write_text(
                    "window.QA_DATA = " + json.dumps(payload, ensure_ascii=False) + ";\n",
                    encoding="utf-8",
                )
            report = completeness.build_completeness_report(
                date_text="20260621",
                root=root,
                expected_slots=["09:35", "09:50", "10:05"],
                codes=["600522"],
            )

        self.assertFalse(report["complete"])
        self.assertEqual(["09:50"], report["missingSlots"])
        self.assertEqual([], report["missingCodesBySlot"]["09:35"])
        self.assertEqual(["600522"], report["missingCodesBySlot"]["09:50"])
        self.assertIn("best_effort", report["repairMode"])

    def test_strategy_c_is_pool_tracking_not_buy_simulation(self) -> None:
        app_js = Path("web_dashboard/app.js").read_text(encoding="utf-8")
        styles = Path("web_dashboard/styles.css").read_text(encoding="utf-8")
        index = Path("web_dashboard/index.html").read_text(encoding="utf-8")
        trading = Path("web_dashboard/trading.html").read_text(encoding="utf-8")
        build_script = Path("tools/build_dashboard_data.py").read_text(encoding="utf-8")

        self.assertNotIn("./strategy-c.html", index)
        self.assertNotIn("./strategy-c.html", trading)
        self.assertNotIn("function renderStrategyC()", app_js)
        self.assertNotIn("DATA.simulatedTradingC", app_js)
        self.assertNotIn("strategy-c-stage", app_js)
        self.assertNotIn(".strategy-c-stage", styles)
        self.assertNotIn("@keyframes defensiveGlow", styles)
        self.assertNotIn("simulatedTradingC", build_script)
        self.assertNotIn("build_strategy_c_config", build_script)
        self.assertNotIn("seed_strategy_c_state", build_script)

    def test_dashboard_server_disables_browser_cache(self) -> None:
        from tools.serve_dashboard import NoCacheHTTPRequestHandler

        headers: list[tuple[str, str]] = []
        handler = object.__new__(NoCacheHTTPRequestHandler)
        handler.send_header = lambda key, value: headers.append((key, value))

        with patch("http.server.SimpleHTTPRequestHandler.end_headers", lambda self: None):
            NoCacheHTTPRequestHandler.end_headers(handler)

        self.assertIn(("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0"), headers)
        self.assertIn(("Pragma", "no-cache"), headers)
        self.assertIn(("Expires", "0"), headers)

    def test_dashboard_server_materializes_file_before_sending(self) -> None:
        from tools.serve_dashboard import NoCacheHTTPRequestHandler

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.js"
            path.write_text("console.log('ok');", encoding="utf-8")
            handler = object.__new__(NoCacheHTTPRequestHandler)
            handler.path = "/app.js"
            handler.translate_path = lambda request_path: str(path)

            with patch("tools.serve_dashboard.materialize_file") as materialize, patch(
                "http.server.SimpleHTTPRequestHandler.send_head",
                return_value="sent",
            ):
                result = NoCacheHTTPRequestHandler.send_head(handler)

        self.assertEqual("sent", result)
        materialize.assert_called_once_with(path)

    def test_runtime_maintenance_materializes_cloud_placeholder(self) -> None:
        from tools import runtime_maintenance

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.js"
            path.write_text("console.log('ok');", encoding="utf-8")
            reads = [OSError(11, "Resource deadlock avoided"), b"console.log('ok');"]
            downloads: list[Path] = []
            writes: list[tuple[Path, bytes]] = []

            def fake_read_file_bytes(target: Path) -> bytes:
                item = reads.pop(0)
                if isinstance(item, OSError):
                    raise item
                return item

            with patch("tools.runtime_maintenance.probe_file", side_effect=OSError(11, "Resource deadlock avoided")), patch(
                "tools.runtime_maintenance.read_file_bytes", side_effect=fake_read_file_bytes
            ), patch(
                "tools.runtime_maintenance.trigger_cloud_download",
                side_effect=lambda target: downloads.append(target),
            ), patch(
                "tools.runtime_maintenance.write_file_bytes",
                side_effect=lambda target, payload: writes.append((target, payload)),
            ):
                result = runtime_maintenance.materialize_file(path)

        self.assertEqual("materialized", result["status"])
        self.assertGreaterEqual(len(downloads), 1)
        self.assertEqual(path, downloads[0])
        self.assertEqual([(path, b"console.log('ok');")], writes)

    def test_runtime_maintenance_checks_top_level_feature_json_files(self) -> None:
        from tools import runtime_maintenance

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            feature_path = root / "data" / "features" / "free_snapshots_20260716.json"
            feature_path.parent.mkdir(parents=True)
            feature_path.write_text("[]", encoding="utf-8")

            targets = [path for path, _rewrite in runtime_maintenance.runtime_targets(root)]

        self.assertIn(feature_path, targets)

    def test_runtime_maintenance_does_not_touch_project_venv(self) -> None:
        from tools import runtime_maintenance

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            venv_cfg = root / ".venv" / "pyvenv.cfg"
            venv_cfg.parent.mkdir()
            venv_cfg.write_text("home = /opt/homebrew/bin\n", encoding="utf-8")

            targets = [path for path, _rewrite in runtime_maintenance.runtime_targets(root)]

        self.assertNotIn(venv_cfg, targets)
        self.assertFalse(any(".venv" in path.parts for path in targets))

    def test_runtime_maintenance_checks_recent_evidence_json_files(self) -> None:
        from tools import runtime_maintenance

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence_path = root / "data" / "evidence" / "institutional_evidence_preopen_20260717.json"
            evidence_path.parent.mkdir(parents=True)
            evidence_path.write_text("{}", encoding="utf-8")

            targets = [path for path, _rewrite in runtime_maintenance.runtime_targets(root)]

        self.assertIn(evidence_path, targets)

    def test_runtime_maintenance_checks_recent_run_json_files(self) -> None:
        from tools import runtime_maintenance

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_path = root / "runs" / "free_pool_expand_20260716.json"
            run_path.parent.mkdir(parents=True)
            run_path.write_text("{}", encoding="utf-8")

            targets = [path for path, _rewrite in runtime_maintenance.runtime_targets(root)]

        self.assertIn(run_path, targets)

    def test_runtime_maintenance_restores_zero_length_dashboard_data_from_archive(self) -> None:
        from tools import runtime_maintenance

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            web_dir = root / "web_dashboard"
            archive_dir = web_dir / "archive"
            archive_dir.mkdir(parents=True)
            archive_payload = 'window.QA_DATA = {"meta":{"assetVersion":"20260717130508"},"preferred":[{"code":"600522"}]};\n'
            archive_path = archive_dir / "data_20260717130508.js"
            archive_path.write_text(archive_payload, encoding="utf-8")
            data_path = web_dir / "data.js"
            data_path.write_text("", encoding="utf-8")

            result = runtime_maintenance.materialize_file(data_path)
            restored = data_path.read_text(encoding="utf-8")

        self.assertEqual("materialized", result["status"])
        self.assertEqual(archive_payload, restored)
        self.assertEqual(str(archive_path), result["restoredFrom"])

    def test_runtime_maintenance_preserves_mtime_when_rewriting_materialized_file(self) -> None:
        from tools import runtime_maintenance

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "institutional_evidence_preopen_20260717.json"
            path.write_text("{}", encoding="utf-8")
            original_ns = 1_788_500_000_123_456_789
            os.utime(path, ns=(original_ns, original_ns))

            with patch("tools.runtime_maintenance.probe_file", side_effect=OSError(11, "Resource deadlock avoided")), patch(
                "tools.runtime_maintenance.trigger_cloud_download",
                return_value={"attempted": True, "returncode": 0, "stderr": ""},
            ):
                result = runtime_maintenance.materialize_file(path)

            restored_ns = path.stat().st_mtime_ns

        self.assertEqual("materialized", result["status"])
        self.assertEqual(original_ns, restored_ns)

    def test_intraday_latest_file_prefers_filename_trade_date_over_mtime(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence_dir = root / "data" / "evidence"
            evidence_dir.mkdir(parents=True)
            older_date = evidence_dir / "institutional_evidence_preopen_20260702.json"
            newer_date = evidence_dir / "institutional_evidence_preopen_20260717.json"
            older_date.write_text("{}", encoding="utf-8")
            newer_date.write_text("{}", encoding="utf-8")
            os.utime(older_date, ns=(1_788_900_000_000_000_000, 1_788_900_000_000_000_000))
            os.utime(newer_date, ns=(1_788_000_000_000_000_000, 1_788_000_000_000_000_000))

            with patch("tools.run_intraday_update.ROOT", root):
                selected = run_intraday_update.latest_file("data/evidence/institutional_evidence_*.json")

        self.assertEqual(newer_date, selected)

    def test_intraday_runtime_file_replaces_stale_state_path_with_newer_dated_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence_dir = root / "data" / "evidence"
            evidence_dir.mkdir(parents=True)
            stale_path = evidence_dir / "institutional_evidence_preopen_20260702.json"
            fresh_path = evidence_dir / "institutional_evidence_preopen_20260717.json"
            stale_path.write_text("{}", encoding="utf-8")
            fresh_path.write_text("{}", encoding="utf-8")
            state = {
                "run_date": "20260721",
                "evidence_path": "data/evidence/institutional_evidence_preopen_20260702.json",
            }

            with patch("tools.run_intraday_update.ROOT", root):
                selected = run_intraday_update.runtime_file(
                    state,
                    "20260721",
                    "evidence_path",
                    "data/evidence/institutional_evidence_*.json",
                )

        self.assertEqual(fresh_path, selected)

    def test_intraday_latest_snapshot_ignores_incomplete_unfinished_snapshot(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_dir = root / "data" / "features"
            runs_dir = root / "runs"
            snapshot_dir.mkdir(parents=True)
            runs_dir.mkdir()
            incomplete = snapshot_dir / "free_snapshots_20260827.json"
            complete = snapshot_dir / "free_snapshots_20260805.json"
            incomplete.write_text(json.dumps([{"code": f"600{i:03d}"} for i in range(500)]), encoding="utf-8")
            complete.write_text(json.dumps([{"code": f"600{i:03d}"} for i in range(1200)]), encoding="utf-8")
            os.utime(complete, ns=(1_788_000_000_000_000_000, 1_788_000_000_000_000_000))
            os.utime(incomplete, ns=(1_788_900_000_000_000_000, 1_788_900_000_000_000_000))
            (runs_dir / "free_pool_expand_20260805.json").write_text(
                json.dumps({"complete": True, "snapshots": 1200, "status": "ok"}),
                encoding="utf-8",
            )

            with patch("tools.run_intraday_update.ROOT", root):
                selected = run_intraday_update.latest_file("data/features/free_snapshots_*.json")

        self.assertEqual(complete, selected)

    def test_preopen_latest_snapshot_ignores_incomplete_unfinished_snapshot(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_dir = root / "data" / "features"
            runs_dir = root / "runs"
            snapshot_dir.mkdir(parents=True)
            runs_dir.mkdir()
            incomplete = snapshot_dir / "free_snapshots_20260827.json"
            complete = snapshot_dir / "free_snapshots_20260805.json"
            incomplete.write_text(json.dumps([{"code": f"600{i:03d}"} for i in range(500)]), encoding="utf-8")
            complete.write_text(json.dumps([{"code": f"600{i:03d}"} for i in range(1200)]), encoding="utf-8")
            os.utime(complete, ns=(1_788_000_000_000_000_000, 1_788_000_000_000_000_000))
            os.utime(incomplete, ns=(1_788_900_000_000_000_000, 1_788_900_000_000_000_000))
            (runs_dir / "free_pool_expand_20260805.json").write_text(
                json.dumps({"complete": True, "snapshots": 1200, "status": "ok"}),
                encoding="utf-8",
            )

            with patch("tools.run_preopen_check.ROOT", root):
                selected = run_preopen_check.latest_file("data/features/free_snapshots_*.json")

        self.assertEqual(complete, selected)

    def test_post_close_latest_existing_snapshot_ignores_incomplete_unfinished_snapshot(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_dir = root / "data" / "features"
            runs_dir = root / "runs"
            snapshot_dir.mkdir(parents=True)
            runs_dir.mkdir()
            incomplete = snapshot_dir / "free_snapshots_20260827.json"
            complete = snapshot_dir / "free_snapshots_20260805.json"
            incomplete.write_text(json.dumps([{"code": f"600{i:03d}"} for i in range(500)]), encoding="utf-8")
            complete.write_text(json.dumps([{"code": f"600{i:03d}"} for i in range(1200)]), encoding="utf-8")
            os.utime(complete, ns=(1_788_000_000_000_000_000, 1_788_000_000_000_000_000))
            os.utime(incomplete, ns=(1_788_900_000_000_000_000, 1_788_900_000_000_000_000))
            (runs_dir / "free_pool_expand_20260805.json").write_text(
                json.dumps({"complete": True, "snapshots": 1200, "status": "ok"}),
                encoding="utf-8",
            )

            with patch("tools.run_post_close_update.ROOT", root):
                selected = run_post_close_update.latest_existing_file("data/features/free_snapshots_*.json")

        self.assertEqual(complete, selected)

    def test_dashboard_launch_agent_keeps_fixed_website_alive(self) -> None:
        plist = Path("tools/com.a0000.quant-agents.dashboard.plist").read_text(encoding="utf-8")

        self.assertIn("com.a0000.quant-agents.dashboard", plist)
        self.assertIn("cd /Users/a0000/.local/share/quant-agents/app", plist)
        self.assertIn("/Users/a0000/.local/share/quant-agents/venv/bin/python tools/serve_dashboard.py --host 127.0.0.1 --port 8788", plist)
        self.assertNotIn("/Users/a0000/Documents/Codex/2026-05-24/new-chat", plist)
        self.assertNotIn("runtime_maintenance.py --quick --quiet", plist)
        self.assertNotIn(".venv/bin/python tools/serve_dashboard.py", plist)
        self.assertNotIn(".venv/bin/python tools/runtime_maintenance.py", plist)
        self.assertNotIn("launchd_preflight.sh", plist)
        self.assertIn("<key>RunAtLoad</key>", plist)
        self.assertIn("<key>KeepAlive</key>", plist)

    def test_post_close_launch_agent_retries_after_close(self) -> None:
        plist = Path("tools/com.a0000.quant-agents.post-close.plist").read_text(encoding="utf-8")

        self.assertIn("com.a0000.quant-agents.post-close", plist)
        self.assertIn("cd /Users/a0000/.local/share/quant-agents/app", plist)
        self.assertIn("/Users/a0000/.local/share/quant-agents/venv/bin/python tools/run_post_close_update.py", plist)
        self.assertNotIn("/Users/a0000/Documents/Codex/2026-05-24/new-chat", plist)
        self.assertNotIn("runtime_maintenance.py --quick --quiet", plist)
        self.assertNotIn(".venv/bin/python tools/run_post_close_update.py", plist)
        self.assertNotIn(".venv/bin/python tools/runtime_maintenance.py", plist)
        self.assertNotIn("launchd_preflight.sh", plist)
        self.assertIn("<key>Hour</key><integer>15</integer><key>Minute</key><integer>10</integer>", plist)
        self.assertIn("<key>Hour</key><integer>16</integer><key>Minute</key><integer>10</integer>", plist)
        self.assertIn("<key>Hour</key><integer>20</integer><key>Minute</key><integer>31</integer>", plist)
        self.assertIn("quant_agents_post_close.out.log", plist)

    def test_preopen_launch_agent_runs_before_market(self) -> None:
        plist = Path("tools/com.a0000.quant-agents.preopen.plist").read_text(encoding="utf-8")

        self.assertIn("com.a0000.quant-agents.preopen", plist)
        self.assertIn("cd /Users/a0000/.local/share/quant-agents/app", plist)
        self.assertIn("/Users/a0000/.local/share/quant-agents/venv/bin/python tools/run_preopen_check.py", plist)
        self.assertNotIn("/Users/a0000/Documents/Codex/2026-05-24/new-chat", plist)
        self.assertNotIn("runtime_maintenance.py --quick --quiet", plist)
        self.assertNotIn(".venv/bin/python tools/run_preopen_check.py", plist)
        self.assertNotIn(".venv/bin/python tools/runtime_maintenance.py", plist)
        self.assertNotIn("launchd_preflight.sh", plist)
        self.assertIn("<key>Hour</key><integer>8</integer><key>Minute</key><integer>30</integer>", plist)
        self.assertIn("quant_agents_preopen.out.log", plist)
        self.assertNotIn("<key>RunAtLoad</key>", plist)

    def test_intraday_launch_agent_runs_preflight_before_market_updates(self) -> None:
        plist = Path("tools/com.a0000.quant-agents.intraday.plist").read_text(encoding="utf-8")

        self.assertIn("com.a0000.quant-agents.intraday", plist)
        self.assertIn("cd /Users/a0000/.local/share/quant-agents/app", plist)
        self.assertIn("/Users/a0000/.local/share/quant-agents/venv/bin/python tools/run_intraday_update.py", plist)
        self.assertNotIn("/Users/a0000/Documents/Codex/2026-05-24/new-chat", plist)
        self.assertNotIn("runtime_maintenance.py --quick --quiet", plist)
        self.assertNotIn(".venv/bin/python tools/run_intraday_update.py", plist)
        self.assertNotIn(".venv/bin/python tools/runtime_maintenance.py", plist)
        self.assertNotIn("launchd_preflight.sh", plist)
        self.assertIn("<key>Hour</key><integer>15</integer><key>Minute</key><integer>5</integer>", plist)

    def test_automation_watchdog_schedules_missing_reports_only_after_due_time(self) -> None:
        from tools import run_automation_watchdog

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runs").mkdir()

            early_actions = run_automation_watchdog.required_actions(
                root,
                datetime(2026, 7, 28, 8, 40, tzinfo=ZoneInfo("Asia/Shanghai")),
            )
            preopen_actions = run_automation_watchdog.required_actions(
                root,
                datetime(2026, 7, 28, 8, 50, tzinfo=ZoneInfo("Asia/Shanghai")),
            )

        self.assertEqual([], early_actions)
        self.assertEqual(["preopen"], [action["key"] for action in preopen_actions])
        self.assertIn("tools/run_preopen_check.py", preopen_actions[0]["command"])
        self.assertIn("--force", preopen_actions[0]["command"])

    def test_automation_watchdog_skips_reports_already_ok(self) -> None:
        from tools import run_automation_watchdog

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir()
            (runs / "preopen_check_report_20260728.json").write_text(
                json.dumps({"status": "ok"}), encoding="utf-8"
            )
            (runs / "intraday_update_report_20260728.json").write_text(
                json.dumps({"status": "ok"}), encoding="utf-8"
            )
            (runs / "post_close_update_report_20260728.json").write_text(
                json.dumps({"status": "ok"}), encoding="utf-8"
            )

            actions = run_automation_watchdog.required_actions(
                root,
                datetime(2026, 7, 28, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
            )

        self.assertEqual([], actions)

    def test_automation_watchdog_retries_degraded_post_close_report(self) -> None:
        from tools import run_automation_watchdog

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir()
            (runs / "post_close_update_report_20260728.json").write_text(
                json.dumps({"status": "degraded", "reason": "snapshot_fallback_used"}), encoding="utf-8"
            )

            actions = run_automation_watchdog.required_actions(
                root,
                datetime(2026, 7, 28, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
            )

        self.assertEqual(["post_close"], [action["key"] for action in actions])
        self.assertIn("tools/run_post_close_update.py", actions[0]["command"])

    def test_automation_watchdog_plist_runs_from_stable_app(self) -> None:
        plist = Path("tools/com.a0000.quant-agents.watchdog.plist").read_text(encoding="utf-8")

        self.assertIn("com.a0000.quant-agents.watchdog", plist)
        self.assertIn("cd /Users/a0000/.local/share/quant-agents/app", plist)
        self.assertIn("/Users/a0000/.local/share/quant-agents/venv/bin/python tools/run_automation_watchdog.py", plist)
        self.assertNotIn("/Users/a0000/Documents/Codex/2026-05-24/new-chat", plist)
        self.assertNotIn("brctl", plist)
        self.assertIn("<key>StartCalendarInterval</key>", plist)

    def test_launchd_preflight_uses_non_venv_python_for_runtime_maintenance(self) -> None:
        script = Path("tools/launchd_preflight.sh").read_text(encoding="utf-8")

        self.assertIn("SYSTEM_PYTHON=", script)
        self.assertIn("/opt/homebrew/opt/python@3.14/bin/python3.14", script)
        self.assertIn('"$SYSTEM_PYTHON" "$ROOT/tools/runtime_maintenance.py" --quick --quiet', script)
        self.assertNotIn('"$ROOT/.venv/bin/python" "$ROOT/tools/runtime_maintenance.py"', script)

    def test_launchd_preflight_resolves_root_from_its_own_location(self) -> None:
        script = Path("tools/launchd_preflight.sh").read_text(encoding="utf-8")

        self.assertIn('ROOT="$(cd "$(dirname "$0")/.." && pwd)"', script)
        self.assertNotIn("/Users/a0000/Documents/Codex/2026-05-24/new-chat", script)
        self.assertNotIn("$ROOT/.venv/", script)

    def test_dashboard_pool_row_displays_realtime_price_not_snapshot_close(self) -> None:
        app_js = Path("web_dashboard/app.js").read_text(encoding="utf-8")
        pool_row = app_js.split("function poolRow(stock)", 1)[1].split("function wirePoolControls()", 1)[0]
        history_row = app_js.split("function historyRow(row)", 1)[1].split("function historyPeriodRow(row)", 1)[0]
        history_period_row = app_js.split("function historyPeriodRow(row)", 1)[1].split("function renderTrading()", 1)[0]

        self.assertNotIn("stock.basic.close", pool_row)
        self.assertIn("preferredDisplayPrice(stock)", pool_row)
        self.assertIn("row.currentClose", history_row)
        self.assertIn("row.currentClose", history_period_row)

    def test_intraday_health_adjustment_pushes_limit_down_stock_down_pool_rank(self) -> None:
        rows = [
            {
                "rank": 1,
                "code": "000582",
                "name": "北部湾港",
                "finalScore": 0.90,
                "currentClose": 13.42,
                "currentLimitDown": 13.42,
                "todayPctChange": -9.99,
                "states": {"isLimitDown": True},
                "sectorContext": {"avgPctChange": -9.99, "downRatio": 1.0},
            },
            {
                "rank": 2,
                "code": "600522",
                "name": "中天科技",
                "finalScore": 0.80,
                "currentClose": 46.06,
                "todayPctChange": 2.4,
                "states": {},
                "sectorContext": {"avgPctChange": 2.7, "downRatio": 0.2},
            },
        ]

        adjusted = apply_intraday_health_adjustments(rows)

        self.assertEqual("600522", adjusted[0]["code"])
        self.assertEqual(2, adjusted[0]["rawRank"])
        self.assertIn("limit_down", adjusted[1]["intradayHealth"]["blockers"])
        self.assertIn("sector_breakdown", adjusted[1]["intradayHealth"]["blockers"])
        self.assertLess(adjusted[1]["adjustedScore"], adjusted[0]["adjustedScore"])

    def test_intraday_health_keeps_base_score_separate_from_adjusted_score(self) -> None:
        rows = [
            {
                "rank": 1,
                "code": "000021",
                "name": "深科技",
                "finalScore": 0.7408,
                "currentClose": 47.26,
                "currentLimitDown": 47.26,
                "todayPctChange": -10.0,
                "states": {"isLimitDown": True},
                "sectorContext": {"avgPctChange": -6.25, "downRatio": 0.75},
            }
        ]

        adjusted = apply_intraday_health_adjustments(rows)

        self.assertEqual(0.7408, adjusted[0]["finalScore"])
        self.assertEqual(0.7408, adjusted[0]["baseScore"])
        self.assertLess(adjusted[0]["adjustedScore"], adjusted[0]["baseScore"])

    def test_free_spot_partial_cache_is_refetched(self) -> None:
        with TemporaryDirectory() as tmp:
            provider = FreeAshareProvider(cache_dir=Path(tmp), request_pause=0)
            cache_path = Path(tmp) / "spot_all" / "20260715.json"
            cache_path.parent.mkdir(parents=True)
            cache_path.write_text(json.dumps([{"f12": f"600{i:03d}", "f14": "样本"} for i in range(300)]), encoding="utf-8")
            fresh_rows = [{"f12": f"600{i:03d}", "f14": "样本"} for i in range(1000)]

            def fake_get_json(url: str, params: dict) -> dict:
                page = int(params["pn"])
                start = (page - 1) * 100
                return {"data": {"diff": fresh_rows[start : start + 100]}}

            with patch("quant_agents.free_data._get_json", side_effect=fake_get_json):
                rows = provider.fetch_spot(date(2026, 7, 15), use_cache=True)

        self.assertEqual(1000, len(rows))

    def test_free_spot_does_not_reuse_tiny_cache_when_refetch_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            provider = FreeAshareProvider(cache_dir=Path(tmp), request_pause=0)
            cache_path = Path(tmp) / "spot_all" / "20260715.json"
            cache_path.parent.mkdir(parents=True)
            cache_path.write_text(json.dumps([{"f12": f"600{i:03d}", "f14": "半截缓存"} for i in range(183)]), encoding="utf-8")

            with patch("quant_agents.free_data._get_json", side_effect=FreeDataError("spot unavailable")):
                with self.assertRaises(FreeDataError):
                    provider.fetch_spot(date(2026, 7, 15), use_cache=True)

    def test_free_spot_does_not_write_partial_rows_after_mid_fetch_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            provider = FreeAshareProvider(cache_dir=Path(tmp), request_pause=0)
            cache_path = Path(tmp) / "spot_all" / "20260715.json"

            def fake_get_json(url: str, params: dict) -> dict:
                if int(params["pn"]) == 1:
                    return {"data": {"diff": [{"f12": f"600{i:03d}", "f14": "半截"} for i in range(100)]}}
                raise FreeDataError("page failed")

            with patch("quant_agents.free_data._get_json", side_effect=fake_get_json):
                with self.assertRaises(FreeDataError):
                    provider.fetch_spot(date(2026, 7, 15), use_cache=False)

        self.assertFalse(cache_path.exists())

    def test_free_kline_extends_cached_range_with_only_missing_tail(self) -> None:
        with TemporaryDirectory() as tmp:
            provider = FreeAshareProvider(cache_dir=Path(tmp), request_pause=0)
            cache_dir = Path(tmp) / "kline" / "qfq"
            cache_dir.mkdir(parents=True)
            code = "600522"
            start = date(2026, 1, 1)
            cached_end = date(2026, 3, 5)
            end = date(2026, 3, 6)
            cached_rows = [
                {
                    "date": (start + timedelta(days=offset)).isoformat(),
                    "open": 10.0,
                    "close": 10.0 + offset * 0.1,
                    "high": 10.2 + offset * 0.1,
                    "low": 9.8 + offset * 0.1,
                    "volume": 1000,
                    "amount": 100000,
                    "pct_change": 0.0,
                    "change": 0.0,
                    "turnover": 1.0,
                }
                for offset in range((cached_end - start).days + 1)
            ]
            (cache_dir / f"{code}_{start:%Y%m%d}_{cached_end:%Y%m%d}.json").write_text(
                json.dumps(cached_rows),
                encoding="utf-8",
            )
            remote_calls: list[dict[str, str]] = []

            def fake_get_json(url: str, params: dict) -> dict:
                remote_calls.append(params)
                self.assertEqual("20260306", params["beg"])
                self.assertEqual("20260306", params["end"])
                return {
                    "data": {
                        "klines": [
                            "2026-03-06,14,15,15.5,13.5,1200,18000,0,2,0.3,1.5",
                        ]
                    }
                }

            with patch("quant_agents.free_data._get_json", side_effect=fake_get_json):
                rows = provider.fetch_kline(code, start, end, use_cache=True)

        self.assertEqual(1, len(remote_calls))
        self.assertEqual(65, len(rows))
        self.assertEqual("2026-03-06", rows[-1]["date"])

    def test_free_spot_falls_back_to_latest_valid_cached_snapshot_when_live_fetch_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            provider = FreeAshareProvider(cache_dir=Path(tmp), request_pause=0)
            cache_path = Path(tmp) / "spot_all" / "20260805.json"
            cache_path.parent.mkdir(parents=True)
            cached_rows = [{"f12": f"600{i:03d}", "f14": "历史缓存"} for i in range(1000)]
            cache_path.write_text(json.dumps(cached_rows), encoding="utf-8")

            with patch("quant_agents.free_data._get_json", side_effect=FreeDataError("spot unavailable")):
                rows = provider.fetch_spot(date(2026, 8, 26), use_cache=True)

        self.assertEqual(1000, len(rows))
        self.assertEqual("600000", rows[0]["f12"])

    def test_post_close_defaults_to_eastmoney_kline_source(self) -> None:
        argv = ["run_post_close_update.py", "--date", "20260715", "--force"]
        with patch("tools.run_post_close_update.sys.argv", argv):
            args = run_post_close_update.parse_args()

        self.assertEqual("eastmoney", args.kline_source)

    def test_universe_excludes_gem(self) -> None:
        config = AppConfig()
        universe = filter_universe(load_sample_snapshots(date(2026, 5, 25)), config.universe)
        self.assertNotIn("300750", {snapshot.code for snapshot in universe})

    def test_t_plus_one_unlocks_previous_positions(self) -> None:
        session = PaperTradingSession(initial_cash=1_000_000)
        history = load_sample_history(days=3, start_date=date(2026, 5, 25))
        session.run_day(history[0])
        session.run_day(history[1])
        self.assertTrue(session.account.positions)
        self.assertTrue(any(not position.can_sell for position in session.account.positions.values()))
        session.run_day(history[2])
        self.assertTrue(all(position.can_sell or position.holding_days == 0 for position in session.account.positions.values()))

    def test_negative_information_blocks_new_buy(self) -> None:
        config = AppConfig()
        risk = RiskAgent(config.risk, config.strategy)
        snapshot = load_sample_snapshots(date(2026, 5, 25))[0]
        snapshot = replace(snapshot, info_risk="investigation")
        decision = Decision(
            code=snapshot.code,
            action="buy",
            final_score=0.95,
            target_weight=0.10,
            reason="test",
            risk_flags=("information:negative_event=investigation",),
        )
        checked = risk.check_decision(decision, snapshot, AccountState(cash=1_000_000))
        self.assertEqual(checked.action, "hold")
        self.assertIn("agent_hard_veto", checked.risk_check)

    def test_drawdown_blocks_buys_but_allows_sells(self) -> None:
        config = AppConfig()
        risk = RiskAgent(config.risk, config.strategy)
        snapshot = load_sample_snapshots(date(2026, 5, 25))[0]
        account = AccountState(
            cash=100_000,
            peak_equity=1_000_000,
            previous_equity=1_000_000,
            positions={"600519": Position("600519", 100, 2000.0, 1500.0, 3, True)},
        )
        buy = Decision(snapshot.code, "buy", 0.9, 0.1, "test")
        sell = Decision(snapshot.code, "sell", 0.0, 0.0, "risk_reduce")
        self.assertIn("account_drawdown_exceeded", risk.check_decision(buy, snapshot, account).risk_check)
        self.assertEqual("approved", risk.check_decision(sell, snapshot, account).risk_check)

    def test_free_data_helpers(self) -> None:
        self.assertEqual("1.600519", _secid("600519"))
        self.assertEqual("0.000858", _secid("000858"))
        self.assertEqual("600519.SS", _yahoo_symbol("600519"))
        self.assertEqual("000858.SZ", _yahoo_symbol("000858"))
        row = _parse_kline("2026-05-22,10,11,12,9,100,100000,3,1,0.1,2")
        self.assertEqual("2026-05-22", row["date"])
        self.assertEqual(11.0, row["close"])

    def test_institutional_agent_is_heuristic(self) -> None:
        snapshot = load_sample_snapshots(date(2026, 5, 25))[0]
        signal = InstitutionalFlowAgent().score(snapshot)
        self.assertEqual("institutional", signal.agent_name)
        self.assertIn("not_named_institution_buy", signal.raw_features)
        self.assertGreaterEqual(signal.score, 0.0)
        self.assertLessEqual(signal.score, 1.0)

    def test_institutional_agent_uses_external_evidence(self) -> None:
        snapshot = load_sample_snapshots(date(2026, 5, 25))[0]
        enriched = apply_institutional_evidence(
            [snapshot],
            {
                snapshot.code: {
                    "dragon_tiger": {"institution_net_buy": 10_000_000, "institution_seat_count": 2},
                    "northbound": {"holding_change_pct": 0.02},
                }
            },
        )[0]
        signal = InstitutionalFlowAgent().score(enriched)
        self.assertTrue(signal.raw_features["has_external_evidence"])
        self.assertIn("dragon_tiger_institution_net_buy", signal.reason)

    def test_evidence_collector_helpers(self) -> None:
        self.assertEqual("600519.SH", _market_code("600519"))
        self.assertEqual("000858.SZ", _market_code("000858"))
        self.assertEqual(3, _institution_count("2家机构买入，1家机构卖出"))

    def test_news_classifier_flags_material_risk(self) -> None:
        polarity, severity, confidence, keywords, reason = classify_news_text("关于收到证监会立案调查通知书的公告")
        self.assertEqual("negative", polarity)
        self.assertEqual("high", severity)
        self.assertGreater(confidence, 0.8)
        self.assertIn("hard_material_risk", reason)
        self.assertTrue(keywords)

    def test_project_env_loader_does_not_override_existing_env(self) -> None:
        with TemporaryDirectory() as tmp, patch.dict("os.environ", {"DEEPSEEK_MODEL": "already-set"}, clear=False):
            env_path = Path(tmp) / ".env"
            env_path.write_text("DEEPSEEK_MODEL=from-file\nLLM_PROVIDER=deepseek\n", encoding="utf-8")
            load_project_env(env_path)
            import os

            self.assertEqual("already-set", os.environ["DEEPSEEK_MODEL"])
            self.assertEqual("deepseek", os.environ["LLM_PROVIDER"])

    def test_deepseek_client_reports_unconfigured(self) -> None:
        client = DeepSeekClient(LLMConfig(provider="deepseek", api_key=None))
        self.assertFalse(client.available)
        with self.assertRaises(LLMError):
            client.chat_json("system", "user")

    def test_extract_json_object_from_markdown_response(self) -> None:
        self.assertEqual('{"ok": true}', _extract_json_object('```json\n{"ok": true}\n```'))
        self.assertEqual('{"ok": true}', _extract_json_object('说明\n{"ok": true}\n'))

    def test_llm_news_summary_falls_back_when_unavailable(self) -> None:
        event = NewsEvent(
            code="600522",
            title="公司签订重大合同",
            source="test",
            publish_time=None,
            url=None,
            polarity="positive",
            severity="medium",
            confidence=0.7,
            keywords=["重大合同"],
            reason="positive_news",
        )
        news = summarize_news_events("600522", [event], None, "manual", DeepSeekClient(LLMConfig(provider="rules")))
        self.assertEqual("rules", news["llm"]["provider"])
        self.assertEqual("positive", news["polarity"])

    def test_information_agent_uses_news_evidence(self) -> None:
        snapshot = load_sample_snapshots(date(2026, 5, 25))[0]
        enriched = apply_context_evidence(
            [snapshot],
            {
                snapshot.code: {
                    "news": {
                        "polarity": "negative",
                        "score": 0.05,
                        "confidence": 0.9,
                        "material_risk": True,
                        "headline": "收到行政处罚事先告知书",
                    }
                }
            },
        )[0]
        signal = InformationAgent().score(enriched)
        self.assertTrue(signal.risk_flag)
        self.assertLess(signal.score, 0.2)

    def test_sentiment_agent_uses_theme_evidence(self) -> None:
        snapshot = load_sample_snapshots(date(2026, 5, 25))[0]
        enriched = apply_context_evidence(
            [snapshot],
            {snapshot.code: {"theme": {"hot_rank": 12, "is_hot": True, "themes": ["算力"]}}},
        )[0]
        baseline = SentimentAgent().score(snapshot)
        themed = SentimentAgent().score(enriched)
        self.assertGreater(themed.score, baseline.score)

    def test_tencent_full_quote_parses_fundamental_metrics(self) -> None:
        text = 'v_sh600522="1~中天科技~600522~43.82~41.90~42.69~2264915~1241858~1023057~43.82~2328~43.81~1371~43.80~2339~43.79~780~43.78~943~43.83~2007~43.84~2546~43.85~5785~43.86~1709~43.87~939~~20260522161420~1.92~4.58~43.88~42.48~43.82/2264915/9819713358~2264915~981971~6.64~46.83~~43.88~42.48~3.34~1495.55~1495.55~3.93~46.09~37.71~0.68~-5225~43.36~40.69~51.53~~~1.75~981971.3358~0.0000~0~ ~GP-A~141.83~6.90~0.68~8.40~5.14~47.40~12.75~7.14~35.08~79.44~3412949652~3412949652~-25.18~184.55~3412949652~~~234.45~0.00~~CNY~0~___D__F__N~43.88~-5754~";'
        quote = _parse_tencent_quotes(text)["600522"]
        self.assertEqual(46.83, quote["pe_dynamic"])
        self.assertEqual(6.64, quote["turnover_rate_pct"])
        self.assertEqual(1495.55, quote["total_market_cap_100m"])

    def test_tencent_quote_cache_fetches_missing_codes(self) -> None:
        with TemporaryDirectory() as tmp:
            client = TencentQuoteClient(cache_dir=tmp, pause=0)
            cache_path = client.cache_dir / "quotes" / "20260525.json"
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text('{"600522": {"code": "600522", "name": "中天科技"}}', encoding="utf-8")
            payload = 'v_sz001309="51~德明利~001309~674.00~656.10~650.00~100~50~50~674.00~1~673.99~1~673.98~1~673.97~1~673.96~1~674.01~1~674.02~1~674.03~1~674.04~1~674.05~1~~20260522161406~17.90~2.73~681.00~640.00~674.00/100/100000~100~10~8.20~37.26~~681.00~640.00~6.25~100.00~100.00~3.10~721.71~590.49~0.82";'
            with patch("quant_agents.market_sources._get_text", return_value=payload):
                quotes = client.fetch_quotes(["600522", "001309"], trade_date=date(2026, 5, 25), use_cache=True)

            self.assertIn("600522", quotes)
            self.assertIn("001309", quotes)

    def test_tencent_market_indices_parse_by_symbol(self) -> None:
        with TemporaryDirectory() as tmp:
            client = TencentQuoteClient(cache_dir=tmp, pause=0)
            payload = 'v_sh000001="1~上证指数~000001~3350.12~3348.37~3344.10~100~50~50~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~~20260525113000~1.75~0.05~3360.00~3330.00~3350.12/100/123456789000~100~12345679~0.00~0.00~~3360.00~3330.00~0.90~0.00~0.00~0.00";'
            with patch("quant_agents.market_sources._get_text", return_value=payload):
                quotes = client.fetch_market_indices(["sh000001"], trade_date=date(2026, 5, 25), use_cache=False)

            self.assertIn("sh000001", quotes)
            self.assertEqual("上证指数", quotes["sh000001"]["label"])
            self.assertEqual(0.05, quotes["sh000001"]["pct_change"])

    def test_mootdx_normaliser_skips_placeholder_bars(self) -> None:
        class FakeFrame:
            def to_dict(self, orient: str) -> list[dict[str, object]]:
                return [
                    {
                        "date": "2026-05-25",
                        "open": 43.82,
                        "close": 43.82,
                        "high": 43.82,
                        "low": 43.82,
                        "volume": 5.8e-39,
                        "amount": 5.8e-39,
                    },
                    {
                        "date": "2026-05-22",
                        "open": 42.69,
                        "close": 43.82,
                        "high": 43.88,
                        "low": 42.48,
                        "volume": 2_264_914,
                        "amount": 9_819_713_536,
                    },
                ]

        rows = _normalise_dataframe_bars(FakeFrame(), date(2026, 5, 1), date(2026, 5, 25))
        self.assertEqual(["2026-05-22"], [row["date"] for row in rows])

    def test_quote_evidence_feeds_capital_and_fundamental_agents(self) -> None:
        snapshot = load_sample_snapshots(date(2026, 5, 25))[0]
        enriched = apply_context_evidence(
            [snapshot],
            {
                snapshot.code: {
                    "quote": {
                        "amount_yuan": 2_000_000_000,
                        "turnover_rate": 0.07,
                        "volume_ratio": 1.8,
                        "pe_dynamic": 24.0,
                        "pb": 3.0,
                        "total_market_cap_100m": 850.0,
                    }
                }
            },
        )[0]
        capital = CapitalAgent().score(enriched)
        fundamental = FundamentalAgent().score(enriched)
        self.assertTrue(capital.raw_features["has_quote_evidence"])
        self.assertTrue(fundamental.raw_features["has_quote_evidence"])
        self.assertIn("quote_pe=24.00", fundamental.reason)

    def test_decision_target_weights_are_conviction_weighted(self) -> None:
        config = AppConfig()
        snapshots = load_sample_snapshots(date(2026, 5, 25))
        report = TradingPipeline(_force_rebalance_config(config)).plan_day(snapshots, AccountState(cash=1_000_000))
        weights = [decision["target_weight"] for decision in report["decisions"] if decision["action"] == "buy"]
        self.assertTrue(weights)
        self.assertLessEqual(sum(weights), config.strategy.base_total_weight + 1e-9)
        self.assertLessEqual(max(weights), config.strategy.max_single_weight)

    def test_intraday_update_slot_gate_allows_launchd_jitter(self) -> None:
        tz = ZoneInfo("Asia/Shanghai")
        self.assertEqual("13:50", due_slot(datetime(2026, 5, 25, 13, 50, 30, tzinfo=tz), 2))
        self.assertEqual("13:50", due_slot(datetime(2026, 5, 25, 13, 52, 0, tzinfo=tz), 2))
        self.assertIsNone(due_slot(datetime(2026, 5, 25, 13, 53, 1, tzinfo=tz), 2))

    def test_intraday_update_state_blocks_duplicate_slot(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            write_state(state_path, {"run_date": "20260525", "last_completed_slot": "13:50"})
            self.assertTrue(slot_already_completed(state_path, "20260525", "13:50"))
            self.assertFalse(slot_already_completed(state_path, "20260525", "14:05"))

    def test_intraday_update_state_read_error_does_not_crash_slot_gate(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text("{}", encoding="utf-8")
            with patch.object(Path, "read_text", side_effect=OSError(11, "Resource deadlock avoided")):
                self.assertFalse(slot_already_completed(state_path, "20260525", "13:50"))

    def test_optional_cli_json_read_error_falls_back_to_empty_state(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text("{}", encoding="utf-8")
            with patch.object(Path, "read_text", side_effect=OSError(11, "Resource deadlock avoided")):
                self.assertEqual({}, quant_cli.load_optional_json(state_path))

    def test_project_env_read_error_does_not_crash_automation(self) -> None:
        with TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("DEEPSEEK_API_KEY=temporary\n", encoding="utf-8")
            with patch.object(Path, "read_text", side_effect=OSError(11, "Resource deadlock avoided")):
                load_project_env(env_path)

    def test_intraday_update_uses_current_expand_report_from_state(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir()
            report_path = runs / "intraday_update_report_20260629.json"
            state_path = runs / "intraday_update_state.json"
            (root / "data" / "features").mkdir(parents=True)
            (root / "data" / "evidence").mkdir(parents=True)
            (root / "web_dashboard").mkdir()
            (root / "data" / "features" / "free_snapshots_20260629.json").write_text("[]", encoding="utf-8")
            (root / "data" / "evidence" / "institutional_evidence_20260629.json").write_text("{}", encoding="utf-8")
            (root / "data" / "evidence" / "context_evidence_preopen_20260629.json").write_text("{}", encoding="utf-8")
            (runs / "free_pool_expand_20260629.json").write_text("{}", encoding="utf-8")
            (runs / "info_evidence_collect_20260629_intraday.json").write_text(
                json.dumps({"stats": {"quotes": 0, "themes": 0}}),
                encoding="utf-8",
            )
            (root / "web_dashboard" / "data.js").write_text(
                'window.QA_DATA = {"meta":{"assetVersion":"20260629150508","latestQuoteTime":"20260629150503","latestIndexQuoteTime":"20260629150500"},"preferred":[]};\n',
                encoding="utf-8",
            )
            write_state(
                state_path,
                {
                    "run_date": "20260629",
                    "snapshot_path": "data/features/free_snapshots_20260629.json",
                    "evidence_path": "data/evidence/institutional_evidence_20260629.json",
                    "expand_report_path": "runs/free_pool_expand_20260629.json",
                },
            )
            argv = [
                "run_intraday_update.py",
                "--date",
                "20260629",
                "--report",
                str(report_path),
                "--state",
                str(state_path),
                "--force",
            ]

            with patch("tools.run_intraday_update.ROOT", root), patch(
                "tools.run_intraday_update.sys.argv", argv
            ), patch("tools.run_intraday_update.datetime") as fake_datetime, patch(
                "tools.run_intraday_update.run_command"
            ) as run_command:
                fake_datetime.now.return_value = datetime(2026, 6, 29, 14, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
                fake_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
                run_intraday_update.main()

            dashboard_command = run_command.call_args_list[-1].args[1]
            expand_report_arg = dashboard_command[dashboard_command.index("--expand-report") + 1]
            state_after = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(str(root / "runs" / "free_pool_expand_20260629.json"), expand_report_arg)
        self.assertIn("--skip-archive-intraday", dashboard_command)
        self.assertNotIn("--skip-stock-chart-cache", dashboard_command)
        self.assertEqual("runs/free_pool_expand_20260629.json", state_after["expand_report_path"])

    def test_entry_context_preserves_legacy_history_entry_price(self) -> None:
        result = build_entry_context(
            {"close": 43.82, "trade_date": "2026-05-22"},
            {
                "currentClose": 42.68,
                "latestDate": "2026-05-25",
                "currentSource": "腾讯行情",
                "latestQuoteTime": "20260525142008",
            },
            {"entryDate": "2026-05-22", "entryClose": 43.82, "active": True},
            "2026-05-25",
        )
        self.assertEqual("2026-05-22", result["entryDate"])
        self.assertEqual(43.82, result["entryClose"])
        self.assertEqual("历史入池价", result["entrySource"])
        self.assertIsNone(result["entryQuoteTime"])
        self.assertIsNone(result["legacyEntryClose"])

    def test_entry_context_uses_decision_moment_quote_for_new_rows(self) -> None:
        result = build_entry_context(
            {"close": 43.82, "trade_date": "2026-05-22"},
            {
                "currentClose": 42.68,
                "latestDate": "2026-05-25",
                "currentSource": "腾讯行情",
                "latestQuoteTime": "20260525142008",
            },
            None,
            "2026-05-25",
        )
        self.assertEqual("2026-05-25", result["entryDate"])
        self.assertEqual(42.68, result["entryClose"])
        self.assertEqual("腾讯行情", result["entrySource"])
        self.assertEqual("20260525142008", result["entryQuoteTime"])

    def test_entry_context_preserves_fixed_active_entry_price(self) -> None:
        result = build_entry_context(
            {"close": 43.82, "trade_date": "2026-05-22"},
            {"currentClose": 44.0, "latestDate": "2026-05-25", "currentSource": "腾讯行情"},
            {
                "entryDate": "2026-05-25",
                "entryClose": 42.68,
                "entrySource": "腾讯行情",
                "entryQuoteTime": "20260525142008",
            },
            "2026-05-25",
        )
        self.assertEqual(42.68, result["entryClose"])
        self.assertEqual("20260525142008", result["entryQuoteTime"])

    def test_history_price_rejects_quote_time_regression(self) -> None:
        row = {
            "code": "603618",
            "entryClose": 43.44,
            "currentClose": 42.09,
            "latestQuoteTime": "20260605142002",
            "latestDate": "2026-06-05",
        }
        apply_latest_history_price(
            row,
            {
                "603618": {
                    "currentClose": 45.77,
                    "latestQuoteTime": "20260605102002",
                    "latestDate": "2026-06-05",
                }
            },
            "2026-06-05",
        )

        self.assertEqual(42.09, row["currentClose"])
        self.assertEqual("20260605142002", row["latestQuoteTime"])
        self.assertEqual(-3.11, row["gainToDate"])

    def test_history_update_without_quotes_preserves_active_membership(self) -> None:
        with TemporaryDirectory() as tmp:
            history_path = Path(tmp) / "history.json"
            history_path.write_text(
                json.dumps(
                    [
                        {
                            "code": "603618",
                            "name": "杭电股份",
                            "entryDate": "2026-06-05",
                            "entryClose": 43.44,
                            "entryQuoteTime": "20260605093502",
                            "currentClose": 42.09,
                            "latestQuoteTime": "20260605142002",
                            "active": True,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            rows = update_history([], "2026-06-05", history_path, {}, allow_membership_changes=False)

        self.assertEqual(1, len(rows))
        self.assertTrue(rows[0]["active"])
        self.assertEqual("20260605093502", rows[0]["entryQuoteTime"])

    def test_history_update_reactivates_same_day_exit_without_resetting_entry_price(self) -> None:
        with TemporaryDirectory() as tmp:
            history_path = Path(tmp) / "history.json"
            history_path.write_text(
                json.dumps(
                    [
                        {
                            "code": "600500",
                            "name": "中化国际",
                            "entryDate": "2026-07-01",
                            "entryClose": 7.86,
                            "entryQuoteTime": "20260701093502",
                            "currentClose": 7.96,
                            "latestQuoteTime": "20260701161408",
                            "exitDate": "2026-07-01",
                            "exitPrice": 7.96,
                            "exitQuoteTime": "20260701161408",
                            "active": False,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            rows = update_history(
                [
                    {
                        "code": "600500",
                        "name": "中化国际",
                        "rank": 1,
                        "finalScore": 0.79,
                        "riskCheck": "approved",
                        "entryDate": "2026-07-01",
                        "entryClose": 7.96,
                        "entryQuoteTime": "20260701161408",
                        "currentClose": 7.96,
                        "latestQuoteTime": "20260701161408",
                        "currentSource": "腾讯行情",
                    }
                ],
                "2026-07-01",
                history_path,
                {
                    "600500": {
                        "currentClose": 7.96,
                        "latestQuoteTime": "20260701161408",
                        "latestDate": "2026-07-01",
                        "currentSource": "腾讯行情",
                    }
                },
            )

        active_rows = [row for row in rows if row.get("code") == "600500" and row.get("active")]
        self.assertEqual(1, len(active_rows))
        self.assertEqual("20260701093502", active_rows[0]["entryQuoteTime"])
        self.assertEqual(7.86, active_rows[0]["entryClose"])
        self.assertEqual(1.27, active_rows[0]["gainToDate"])
        self.assertNotIn("exitDate", active_rows[0])
        self.assertEqual(1, sum(1 for row in rows if row.get("code") == "600500"))

    def test_history_update_repairs_reset_active_duplicate_from_same_moment_reentry(self) -> None:
        with TemporaryDirectory() as tmp:
            history_path = Path(tmp) / "history.json"
            history_path.write_text(
                json.dumps(
                    [
                        {
                            "code": "600500",
                            "name": "中化国际",
                            "entryDate": "2026-07-01",
                            "entryClose": 7.96,
                            "entryQuoteTime": "20260701161408",
                            "currentClose": 7.96,
                            "latestQuoteTime": "20260701161408",
                            "active": True,
                        },
                        {
                            "code": "600500",
                            "name": "中化国际",
                            "entryDate": "2026-07-01",
                            "entryClose": 7.86,
                            "entryQuoteTime": "20260701093502",
                            "currentClose": 7.96,
                            "latestQuoteTime": "20260701161408",
                            "exitDate": "2026-07-01",
                            "exitPrice": 7.96,
                            "exitQuoteTime": "20260701161408",
                            "active": False,
                        },
                    ]
                ),
                encoding="utf-8",
            )

            rows = update_history(
                [
                    {
                        "code": "600500",
                        "name": "中化国际",
                        "rank": 1,
                        "finalScore": 0.79,
                        "riskCheck": "approved",
                        "entryDate": "2026-07-01",
                        "entryClose": 7.96,
                        "entryQuoteTime": "20260701161408",
                        "currentClose": 7.96,
                        "latestQuoteTime": "20260701161408",
                        "currentSource": "腾讯行情",
                    }
                ],
                "2026-07-01",
                history_path,
                {
                    "600500": {
                        "currentClose": 7.96,
                        "latestQuoteTime": "20260701161408",
                        "latestDate": "2026-07-01",
                        "currentSource": "腾讯行情",
                    }
                },
            )

        active_rows = [row for row in rows if row.get("code") == "600500" and row.get("active")]
        self.assertEqual(1, len(active_rows))
        self.assertEqual("20260701093502", active_rows[0]["entryQuoteTime"])
        self.assertEqual(7.86, active_rows[0]["entryClose"])
        self.assertEqual(1.27, active_rows[0]["gainToDate"])
        self.assertEqual(1, sum(1 for row in rows if row.get("code") == "600500"))

    def test_history_entry_by_code_uses_pre_reset_entry_when_active_duplicate_was_reset(self) -> None:
        with TemporaryDirectory() as tmp:
            history_path = Path(tmp) / "history.json"
            history_path.write_text(
                json.dumps(
                    [
                        {
                            "code": "600500",
                            "entryDate": "2026-07-01",
                            "entryClose": 7.96,
                            "entryQuoteTime": "20260701161408",
                            "currentClose": 7.96,
                            "latestQuoteTime": "20260701161408",
                            "active": True,
                        },
                        {
                            "code": "600500",
                            "entryDate": "2026-07-01",
                            "entryClose": 7.86,
                            "entryQuoteTime": "20260701093502",
                            "currentClose": 7.96,
                            "latestQuoteTime": "20260701161408",
                            "exitDate": "2026-07-01",
                            "exitPrice": 7.96,
                            "exitQuoteTime": "20260701161408",
                            "active": False,
                        },
                    ]
                ),
                encoding="utf-8",
            )

            entries = dashboard_builder.history_entry_by_code(history_path)

        self.assertEqual("20260701093502", entries["600500"]["entryQuoteTime"])
        self.assertEqual(7.86, entries["600500"]["entryClose"])

    def test_history_membership_rejects_older_incoming_quote(self) -> None:
        with TemporaryDirectory() as tmp:
            history_path = Path(tmp) / "history.json"
            history_path.write_text(
                json.dumps([{"code": "603618", "active": True, "latestQuoteTime": "20260605142002"}]),
                encoding="utf-8",
            )

            self.assertFalse(history_membership_update_allowed(history_path, "20260605102002"))
            self.assertTrue(history_membership_update_allowed(history_path, "20260605143502"))

    def test_trade_ledger_marks_preopen_as_reference_only(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = update_trade_ledger(
                [
                    {
                        "code": "002297",
                        "name": "博云新材",
                        "active": True,
                        "entryDate": "2026-05-25",
                        "entryClose": 26.58,
                        "entrySource": "盘前基准价",
                        "entryQuoteTime": "20260525092708",
                        "currentClose": 29.24,
                        "currentScore": 0.76,
                        "latestQuoteTime": "20260525161500",
                    }
                ],
                [{"code": "002297", "agents": [{"key": "trend", "score": 1.0, "confidence": 0.8}]}],
                Path(tmp) / "trade_ledger.json",
                "20260525120000",
                "2026-05-25",
            )

        record = ledger["records"][0]
        self.assertEqual("preopen", record["entryMode"])
        self.assertEqual("reference_only", record["fillStatus"])
        self.assertEqual(10.01, record["benchmarkPnlPct"])
        self.assertIsNone(record["simulatedPnlPct"])
        self.assertIn("trend", record["factorScores"])

    def test_trade_ledger_applies_intraday_slippage(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = update_trade_ledger(
                [
                    {
                        "code": "002491",
                        "name": "通鼎互联",
                        "active": True,
                        "entryDate": "2026-05-25",
                        "entryClose": 23.3,
                        "entrySource": "腾讯行情",
                        "entryQuoteTime": "20260525142006",
                        "currentClose": 23.58,
                        "currentScore": 0.74,
                        "latestQuoteTime": "20260525161500",
                    }
                ],
                [{"code": "002491", "agents": []}],
                Path(tmp) / "trade_ledger.json",
                "20260525120000",
                "2026-05-25",
            )

        record = ledger["records"][0]
        self.assertEqual("intraday", record["entryMode"])
        self.assertEqual("simulated", record["fillStatus"])
        self.assertGreater(record["fillPrice"], record["intendedPrice"])
        self.assertLess(record["simulatedPnlPct"], record["benchmarkPnlPct"])

    def test_dashboard_builds_sector_context_from_theme_and_quotes(self) -> None:
        contexts = build_sector_contexts(
            {
                "600487": {
                    "quote": {"pct_change": 10.0},
                    "theme": {"themes": ["2天2板", "光通信"], "hot_score": 300.0},
                },
                "600522": {
                    "quote": {"pct_change": 2.0},
                    "theme": {"themes": ["光通信"], "hot_score": 180.0},
                },
                "000021": {
                    "quote": {"pct_change": -1.0},
                    "theme": {"themes": ["算力"], "hot_score": 80.0},
                },
            }
        )

        hengtong = contexts["600487"]
        self.assertEqual("光通信", hengtong["primaryTheme"])
        self.assertEqual(2, hengtong["peerCount"])
        self.assertEqual(6.0, hengtong["avgPctChange"])
        self.assertEqual(1.0, hengtong["upRatio"])
        self.assertEqual(240.0, hengtong["hotScore"])

    def test_simulated_trading_waits_for_two_confirmations(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            result = update_simulated_trading(
                [_sim_stock()],
                {"records": []},
                state_path,
                "20260601093500",
                "2026-06-01",
                _fresh_quote_quality(),
            )

        self.assertEqual(0, result["account"]["positions"])
        self.assertEqual(1, result["pendingSignals"][0]["confirmations"])
        self.assertIn("needs_confirmation", result["pendingSignals"][0]["blockers"])

    def test_simulated_trading_buys_after_second_confirmation(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            update_simulated_trading(
                [_sim_stock(quote_time="20260601130500")],
                {"records": []},
                state_path,
                "20260601130500",
                "2026-06-01",
                _fresh_quote_quality(),
            )
            result = update_simulated_trading(
                [_sim_stock(quote_time="20260601132000")],
                {"records": []},
                state_path,
                "20260601132000",
                "2026-06-01",
                _fresh_quote_quality(),
            )

        self.assertEqual(1, result["account"]["positions"])
        self.assertEqual("buy", result["latestFills"][-1]["side"])
        self.assertEqual("filled", result["latestFills"][-1]["status"])

    def test_simulated_trading_skips_scores_below_min_buy_score(self) -> None:
        low_score = _sim_stock(quote_time="20260601132000") | {"finalScore": 0.77}
        with TemporaryDirectory() as tmp:
            result = update_simulated_trading(
                [low_score],
                {"records": []},
                Path(tmp) / "simulated_trading.json",
                "20260601132000",
                "2026-06-01",
                _fresh_quote_quality(),
                {"confirmationsRequired": 1, "minBuyScore": 0.78},
            )

        self.assertEqual(0, result["account"]["positions"])
        self.assertIn("score_below_min", result["pendingSignals"][0]["blockers"])

    def test_simulated_trading_blocks_intraday_broken_stock_even_with_high_base_score(self) -> None:
        broken = _sim_stock(code="000582", price=13.42, quote_time="20260601132000") | {
            "name": "北部湾港",
            "finalScore": 0.90,
            "adjustedScore": 0.52,
            "todayPctChange": -9.99,
            "states": {"isLimitDown": True},
            "intradayHealth": {
                "blockers": ["limit_down", "sector_breakdown"],
                "penalty": 0.3,
                "label": "盘中破位",
            },
        }
        with TemporaryDirectory() as tmp:
            result = update_simulated_trading(
                [broken],
                {"records": []},
                Path(tmp) / "simulated_trading.json",
                "20260601132000",
                "2026-06-01",
                _fresh_quote_quality(),
                {"confirmationsRequired": 1, "minBuyScore": 0.78},
            )

        self.assertEqual(0, result["account"]["positions"])
        self.assertIn("limit_down", result["pendingSignals"][0]["blockers"])
        self.assertIn("sector_breakdown", result["pendingSignals"][0]["blockers"])
        self.assertEqual(0.52, result["pendingSignals"][0]["adjustedScore"])

    def test_simulated_trading_raises_score_gate_in_risk_off_market(self) -> None:
        borderline = _sim_stock(quote_time="20260601132000") | {"finalScore": 0.79}
        risk_off_market = {
            "items": [
                {"symbol": "sh000001", "pctChange": -1.1},
                {"symbol": "sz399006", "pctChange": -1.4},
            ]
        }
        with TemporaryDirectory() as tmp:
            result = update_simulated_trading(
                [borderline],
                {"records": []},
                Path(tmp) / "simulated_trading.json",
                "20260601132000",
                "2026-06-01",
                _fresh_quote_quality(),
                {"confirmationsRequired": 1, "minBuyScore": 0.78},
                risk_off_market,
            )

        self.assertEqual("risk_off", result["marketRegime"]["regime"])
        self.assertEqual(0, result["account"]["positions"])
        self.assertIn("score_below_min", result["pendingSignals"][0]["blockers"])

    def test_simulated_trading_ignores_bad_market_index_values(self) -> None:
        bad_market = {
            "items": [
                {"symbol": "sh000001", "pctChange": "-"},
                {"symbol": "sz399006", "pctChange": ""},
            ]
        }
        with TemporaryDirectory() as tmp:
            result = update_simulated_trading(
                [_sim_stock(quote_time="20260601132000")],
                {"records": []},
                Path(tmp) / "simulated_trading.json",
                "20260601132000",
                "2026-06-01",
                _fresh_quote_quality(),
                {"confirmationsRequired": 1},
                bad_market,
            )

        self.assertEqual("neutral", result["marketRegime"]["regime"])
        self.assertEqual("neutral_fallback", result["marketRegime"]["source"])

    def test_simulated_trading_increases_single_position_size_in_risk_on_market(self) -> None:
        strong = _sim_stock(quote_time="20260601132000") | {"finalScore": 0.86}
        risk_on_market = {
            "items": [
                {"symbol": "sh000001", "pctChange": 0.8},
                {"symbol": "sz399006", "pctChange": 1.0},
            ]
        }
        with TemporaryDirectory() as tmp:
            result = update_simulated_trading(
                [strong],
                {"records": []},
                Path(tmp) / "simulated_trading.json",
                "20260601132000",
                "2026-06-01",
                _fresh_quote_quality(),
                {"confirmationsRequired": 1, "maxSingleWeight": 0.1, "maxTotalWeight": 0.5},
                risk_on_market,
            )

        self.assertEqual("risk_on", result["marketRegime"]["regime"])
        self.assertEqual(1, result["account"]["positions"])
        self.assertGreater(result["account"]["positionValue"], 105_000)

    def test_simulated_trading_sizes_elite_stock_above_normal_stock(self) -> None:
        elite = _sim_stock(code="600001", price=10.0, quote_time="20260601132000") | {
            "name": "高置信",
            "rank": 1,
            "finalScore": 0.88,
            "agents": [
                {"key": "trend", "score": 0.96},
                {"key": "capital", "score": 0.86},
                {"key": "sentiment", "score": 0.72},
            ],
        }
        normal = _sim_stock(code="600002", price=10.0, quote_time="20260601132000") | {
            "name": "普通",
            "rank": 2,
            "finalScore": 0.80,
            "agents": [
                {"key": "trend", "score": 0.70},
                {"key": "capital", "score": 0.66},
                {"key": "sentiment", "score": 0.50},
            ],
        }
        with TemporaryDirectory() as tmp:
            result = update_simulated_trading(
                [elite, normal],
                {"records": []},
                Path(tmp) / "simulated_trading.json",
                "20260601132000",
                "2026-06-01",
                _fresh_quote_quality(),
                {"confirmationsRequired": 1, "maxSingleWeight": 0.1, "maxTotalWeight": 0.5},
            )

        by_code = {row["code"]: row for row in result["positions"]}
        signals = {row["code"]: row for row in result["pendingSignals"]}
        self.assertEqual("elite", signals["600001"]["positionConviction"])
        self.assertEqual("normal", signals["600002"]["positionConviction"])
        self.assertGreater(signals["600001"]["targetWeight"], signals["600002"]["targetWeight"])
        self.assertGreater(by_code["600001"]["marketValue"], by_code["600002"]["marketValue"])

    def test_simulated_trading_pyramids_profitable_confirmed_position(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            state_path.write_text(
                json.dumps(
                    {
                        "config": {"confirmationsRequired": 2},
                        "account": {"initialCash": 1_000_000, "cash": 940_000, "realizedPnl": 0},
                        "positions": [
                            {
                                "code": "600522",
                                "name": "中天科技",
                                "shares": 6000,
                                "avgCost": 10.0,
                                "costBasis": 60_000,
                                "entryTime": "2026-06-01T13:20:00",
                                "entryTradeDate": "2026-06-01",
                                "pyramidLevel": 0,
                                "targetWeight": 0.06,
                            }
                        ],
                        "watchlist": {
                            "600522": {
                                "confirmations": 1,
                                "lastQuoteTime": "20260603130500",
                            }
                        },
                        "fills": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            stock = _sim_stock(price=10.6, quote_time="20260603132000") | {
                "finalScore": 0.83,
                "agents": [
                    {"key": "trend", "score": 0.93},
                    {"key": "capital", "score": 0.84},
                    {"key": "sentiment", "score": 0.70},
                ],
                "intradayHealth": {"level": "ok", "blockers": [], "reasons": []},
            }
            result = update_simulated_trading(
                [stock],
                {"records": []},
                state_path,
                "20260603132000",
                "2026-06-03",
                _fresh_quote_quality(),
                {"maxSingleWeight": 0.1, "maxTotalWeight": 0.5},
            )

        fill = result["latestFills"][-1]
        position = result["positions"][0]
        self.assertEqual("buy", fill["side"])
        self.assertEqual("filled", fill["status"])
        self.assertEqual("pyramid_add", fill["reason"])
        self.assertEqual(1, position["pyramidLevel"])
        self.assertGreater(position["shares"], 6000)
        self.assertGreaterEqual(len(position["lots"]), 2)
        self.assertEqual("2026-06-03", position["lots"][-1]["tradeDate"])

    def test_simulated_trading_does_not_average_down_losing_position(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            state_path.write_text(
                json.dumps(
                    {
                        "account": {"initialCash": 1_000_000, "cash": 940_000, "realizedPnl": 0},
                        "positions": [
                            {
                                "code": "600522",
                                "name": "中天科技",
                                "shares": 6000,
                                "avgCost": 10.0,
                                "costBasis": 60_000,
                                "entryTime": "2026-06-01T13:20:00",
                                "entryTradeDate": "2026-06-01",
                                "pyramidLevel": 0,
                            }
                        ],
                        "watchlist": {"600522": {"confirmations": 2, "lastQuoteTime": "20260603130500"}},
                        "fills": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = update_simulated_trading(
                [_sim_stock(price=9.7, quote_time="20260603132000") | {"finalScore": 0.86}],
                {"records": []},
                state_path,
                "20260603132000",
                "2026-06-03",
                _fresh_quote_quality(),
                {"maxSingleWeight": 0.1, "maxTotalWeight": 0.5},
            )

        self.assertEqual([], result["latestFills"])
        self.assertEqual(6000, result["positions"][0]["shares"])
        self.assertEqual(0, result["positions"][0].get("pyramidLevel", 0))

    def test_simulated_trading_tracks_add_on_lot_t_plus_one_separately(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            state_path.write_text(
                json.dumps(
                    {
                        "config": {"confirmationsRequired": 2},
                        "account": {"initialCash": 1_000_000, "cash": 940_000, "realizedPnl": 0},
                        "positions": [
                            {
                                "code": "600522",
                                "name": "中天科技",
                                "shares": 6000,
                                "avgCost": 10.0,
                                "costBasis": 60_000,
                                "entryTime": "2026-06-01T13:20:00",
                                "entryTradeDate": "2026-06-01",
                                "pyramidLevel": 0,
                            }
                        ],
                        "watchlist": {"600522": {"confirmations": 2, "lastQuoteTime": "20260603130500"}},
                        "fills": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            stock = _sim_stock(price=10.7, quote_time="20260603132000") | {
                "finalScore": 0.84,
                "intradayHealth": {"level": "ok", "blockers": [], "reasons": []},
            }
            result = update_simulated_trading(
                [stock],
                {"records": []},
                state_path,
                "20260603132000",
                "2026-06-03",
                _fresh_quote_quality(),
                {"maxSingleWeight": 0.12, "maxTotalWeight": 0.5},
            )

        position = result["positions"][0]
        self.assertGreater(position["shares"], 6000)
        self.assertEqual(6000, position["availableShares"])
        self.assertTrue(position["canSell"])
        self.assertEqual(0, position["lots"][-1]["availableShares"])
        self.assertGreater(position["lots"][-1]["shares"], 0)

    def test_simulated_trading_caps_elite_stock_in_risk_off_market(self) -> None:
        elite = _sim_stock(code="600001", price=10.0, quote_time="20260601132000") | {
            "name": "弱市强票",
            "rank": 1,
            "finalScore": 0.90,
            "agents": [
                {"key": "trend", "score": 0.97},
                {"key": "capital", "score": 0.88},
                {"key": "sentiment", "score": 0.75},
            ],
        }
        risk_off_market = {
            "items": [
                {"symbol": "sh000001", "pctChange": -1.1},
                {"symbol": "sz399006", "pctChange": -1.4},
            ]
        }
        with TemporaryDirectory() as tmp:
            result = update_simulated_trading(
                [elite],
                {"records": []},
                Path(tmp) / "simulated_trading.json",
                "20260601132000",
                "2026-06-01",
                _fresh_quote_quality(),
                {"confirmationsRequired": 1, "maxSingleWeight": 0.1, "maxTotalWeight": 0.5},
                risk_off_market,
            )

        signal = result["pendingSignals"][0]
        self.assertEqual("risk_off", result["marketRegime"]["regime"])
        self.assertEqual("elite", signal["positionConviction"])
        self.assertLessEqual(signal["targetWeight"], 0.10)
        self.assertLessEqual(result["account"]["positionValue"], result["account"]["equity"] * 0.10)

    def test_simulated_trading_tracks_missed_opportunities_weekly(self) -> None:
        low_score = _sim_stock(price=10.0, quote_time="20260601132000") | {"finalScore": 0.77}
        rallied = low_score | {"currentClose": 10.8, "latestQuoteTime": "20260601133500"}
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            update_simulated_trading(
                [low_score],
                {"records": []},
                state_path,
                "20260601132000",
                "2026-06-01",
                _fresh_quote_quality(),
                {"confirmationsRequired": 1, "minBuyScore": 0.78},
            )
            result = update_simulated_trading(
                [rallied],
                {"records": []},
                state_path,
                "20260601133500",
                "2026-06-01",
                _fresh_quote_quality(),
                {"confirmationsRequired": 1, "minBuyScore": 0.78},
            )

        self.assertEqual("weekly", result["missedOpportunitySummary"]["reviewCadence"])
        self.assertEqual(1, result["missedOpportunitySummary"]["strongMissedCount"])
        self.assertEqual("score_below_min", result["missedOpportunities"][0]["primaryReason"])
        self.assertAlmostEqual(8.0, result["missedOpportunities"][0]["gainPct"])

    def test_simulated_trading_uses_rolling_two_confirmations(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            update_simulated_trading(
                [_sim_stock(quote_time="20260601132000")],
                {"records": []},
                state_path,
                "20260601132000",
                "2026-06-01",
                _fresh_quote_quality(),
            )
            result = update_simulated_trading(
                [_sim_stock(quote_time="20260601133500")],
                {"records": []},
                state_path,
                "20260601133500",
                "2026-06-01",
                _fresh_quote_quality(),
            )

        self.assertEqual(1, result["account"]["positions"])
        self.assertEqual("buy", result["latestFills"][-1]["side"])

    def test_simulated_trading_does_not_buy_from_morning_confirmations(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            update_simulated_trading(
                [_sim_stock(quote_time="20260601093500")],
                {"records": []},
                state_path,
                "20260601093500",
                "2026-06-01",
                _fresh_quote_quality(),
            )
            result = update_simulated_trading(
                [_sim_stock(quote_time="20260601095000")],
                {"records": []},
                state_path,
                "20260601095000",
                "2026-06-01",
                _fresh_quote_quality(),
            )

        self.assertEqual(0, result["account"]["positions"])
        self.assertEqual(0, result["pendingSignals"][0]["confirmations"])
        self.assertIn("before_buy_window", result["pendingSignals"][0]["blockers"])

    def test_simulated_trading_buys_small_weak_to_strong_position_after_two_morning_confirmations(self) -> None:
        risk_off_market = {
            "items": [
                {"symbol": "sh000001", "pctChange": -1.2},
                {"symbol": "sz399006", "pctChange": -2.0},
            ]
        }
        rounds = [
            _reversal_stock(9.80, -2.0, "20260601093500", 9.90, 1.00, -1.2, 0.30),
            _reversal_stock(9.96, -0.4, "20260601095000", 9.91, 1.12, -0.3, 0.50),
            _reversal_stock(10.12, 1.2, "20260601100500", 9.98, 1.25, 0.4, 0.65, "accelerating"),
        ]
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            for stock in rounds:
                result = update_simulated_trading(
                    [stock],
                    {"records": []},
                    state_path,
                    stock["latestQuoteTime"],
                    "2026-06-01",
                    _fresh_quote_quality(),
                    market_context=risk_off_market,
                )

        self.assertEqual(1, result["account"]["positions"])
        fill = result["latestFills"][-1]
        self.assertEqual("weak_to_strong_confirmed", fill["reason"])
        self.assertEqual("filled", fill["status"])
        self.assertLessEqual(fill["targetWeight"], 0.025)
        self.assertLessEqual(result["account"]["positionValue"], result["account"]["equity"] * 0.026)
        self.assertFalse(result["positions"][0]["canSell"])
        self.assertEqual("confirmed", result["weakToStrongWatch"]["600522"]["state"])

    def test_simulated_trading_keeps_weak_to_strong_buy_after_replay_repair(self) -> None:
        risk_off_market = {
            "items": [
                {"symbol": "sh000001", "pctChange": -1.2},
                {"symbol": "sz399006", "pctChange": -2.0},
            ]
        }
        rounds = [
            _reversal_stock(9.80, -2.0, "20260601093500", 9.90, 1.00, -1.2, 0.30),
            _reversal_stock(9.96, -0.4, "20260601095000", 9.91, 1.12, -0.3, 0.50),
            _reversal_stock(10.12, 1.2, "20260601100500", 9.98, 1.25, 0.4, 0.65, "accelerating"),
            _reversal_stock(10.18, 1.8, "20260601102000", 10.02, 1.30, 0.6, 0.70, "accelerating"),
        ]
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            for stock in rounds:
                result = update_simulated_trading(
                    [stock],
                    {"records": []},
                    state_path,
                    stock["latestQuoteTime"],
                    "2026-06-01",
                    _fresh_quote_quality(),
                    market_context=risk_off_market,
                )

        self.assertEqual(1, result["account"]["positions"])
        weak_fills = [row for row in result["fills"] if row.get("reason") == "weak_to_strong_confirmed"]
        self.assertEqual(1, len(weak_fills))
        self.assertEqual("filled", weak_fills[0]["status"])
        self.assertEqual("weak_to_strong", result["positions"][0]["entryChannel"])

    def test_simulated_trading_rejects_fake_weak_to_strong_rebound_when_sector_keeps_outflowing(self) -> None:
        rounds = [
            _reversal_stock(9.80, -2.0, "20260601093500", 9.90, 1.00, -2.0, 0.25, "outflow"),
            _reversal_stock(9.96, -0.4, "20260601095000", 9.91, 1.12, -1.8, 0.30, "outflow"),
            _reversal_stock(10.12, 1.2, "20260601100500", 9.98, 1.25, -1.5, 0.35, "outflow"),
        ]
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            for stock in rounds:
                result = update_simulated_trading(
                    [stock],
                    {"records": []},
                    state_path,
                    stock["latestQuoteTime"],
                    "2026-06-01",
                    _fresh_quote_quality(),
                )

        self.assertEqual(0, result["account"]["positions"])
        reversal = result["weakToStrongWatch"]["600522"]
        self.assertEqual("blocked", reversal["state"])
        self.assertIn("sector_flow_outflow", reversal["blockers"])

    def test_simulated_trading_requires_two_afternoon_confirmations(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            update_simulated_trading(
                [_sim_stock(quote_time="20260601095000")],
                {"records": []},
                state_path,
                "20260601095000",
                "2026-06-01",
                _fresh_quote_quality(),
            )
            result = update_simulated_trading(
                [_sim_stock(quote_time="20260601130500")],
                {"records": []},
                state_path,
                "20260601130500",
                "2026-06-01",
                _fresh_quote_quality(),
            )

        self.assertEqual(0, result["account"]["positions"])
        self.assertEqual(1, result["pendingSignals"][0]["confirmations"])
        self.assertIn("before_buy_window", result["pendingSignals"][0]["blockers"])

    def test_simulated_trading_resets_confirmation_after_missed_slot(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            update_simulated_trading(
                [_sim_stock(quote_time="20260601130500")],
                {"records": []},
                state_path,
                "20260601130500",
                "2026-06-01",
                _fresh_quote_quality(),
            )
            result = update_simulated_trading(
                [_sim_stock(quote_time="20260601135000")],
                {"records": []},
                state_path,
                "20260601135000",
                "2026-06-01",
                _fresh_quote_quality(),
            )

        self.assertEqual(0, result["account"]["positions"])
        self.assertEqual(1, result["pendingSignals"][0]["confirmations"])
        self.assertIn("needs_confirmation", result["pendingSignals"][0]["blockers"])

    def test_simulated_trading_resets_confirmation_when_stock_risk_blocks(self) -> None:
        blocked = _sim_stock(quote_time="20260601093500") | {"riskCheck": "blocked"}
        blocked_next = _sim_stock(quote_time="20260601095000") | {"riskCheck": "blocked"}
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            update_simulated_trading(
                [blocked],
                {"records": []},
                state_path,
                "20260601093500",
                "2026-06-01",
                _fresh_quote_quality(),
            )
            risk_result = update_simulated_trading(
                [blocked_next],
                {"records": []},
                state_path,
                "20260601095000",
                "2026-06-01",
                _fresh_quote_quality(),
            )
            first_clean = update_simulated_trading(
                [_sim_stock(quote_time="20260601130500")],
                {"records": []},
                state_path,
                "20260601130500",
                "2026-06-01",
                _fresh_quote_quality(),
            )
            second_clean = update_simulated_trading(
                [_sim_stock(quote_time="20260601132000")],
                {"records": []},
                state_path,
                "20260601132000",
                "2026-06-01",
                _fresh_quote_quality(),
            )

        self.assertEqual(0, risk_result["pendingSignals"][0]["confirmations"])
        self.assertIn("risk_not_approved", risk_result["pendingSignals"][0]["blockers"])
        self.assertEqual(0, first_clean["account"]["positions"])
        self.assertEqual(1, first_clean["pendingSignals"][0]["confirmations"])
        self.assertEqual(1, second_clean["account"]["positions"])

    def test_simulated_trading_blocks_buys_when_quotes_are_stale(self) -> None:
        stale_quality = {"freshRatio": 0.5, "preferredStaleCount": 1}
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            update_simulated_trading(
                [_sim_stock(quote_time="20260601093500")],
                {"records": []},
                state_path,
                "20260601093500",
                "2026-06-01",
                stale_quality,
            )
            result = update_simulated_trading(
                [_sim_stock(quote_time="20260601095000")],
                {"records": []},
                state_path,
                "20260601095000",
                "2026-06-01",
                stale_quality,
            )

        self.assertEqual(0, result["account"]["positions"])
        self.assertEqual(0, result["pendingSignals"][0]["confirmations"])
        self.assertIn("stale_quotes", result["pendingSignals"][0]["blockers"])
        self.assertTrue(result["guards"]["blockNewBuys"])

    def test_simulated_trading_blocks_buys_without_quote_quality(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            update_simulated_trading(
                [_sim_stock(quote_time="20260601093500")],
                {"records": []},
                state_path,
                "20260601093500",
                "2026-06-01",
                None,
            )
            result = update_simulated_trading(
                [_sim_stock(quote_time="20260601095000")],
                {"records": []},
                state_path,
                "20260601095000",
                "2026-06-01",
                None,
            )

        self.assertEqual(0, result["account"]["positions"])
        self.assertTrue(result["guards"]["blockTrading"])

    def test_simulated_trading_holds_same_day_when_stock_leaves_pool(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            update_simulated_trading(
                [_sim_stock(quote_time="20260601130500")],
                {"records": []},
                state_path,
                "20260601130500",
                "2026-06-01",
                _fresh_quote_quality(),
            )
            update_simulated_trading(
                [_sim_stock(quote_time="20260601132000")],
                {"records": []},
                state_path,
                "20260601132000",
                "2026-06-01",
                _fresh_quote_quality(),
            )
            result = update_simulated_trading(
                [],
                {"records": [{"code": "600522", "name": "中天科技", "currentPrice": 9.5}]},
                state_path,
                "20260601133500",
                "2026-06-01",
                _fresh_quote_quality(),
            )

        self.assertEqual(1, result["account"]["positions"])
        self.assertEqual("buy", result["latestFills"][-1]["side"])
        self.assertFalse(result["positions"][0]["canSell"])
        self.assertTrue(any(item["reason"] == "out_of_pool_watch" for item in result["events"]))

    def test_simulated_trading_sells_next_day_when_stock_leaves_pool(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            update_simulated_trading(
                [_sim_stock(quote_time="20260601130500")],
                {"records": []},
                state_path,
                "20260601130500",
                "2026-06-01",
                _fresh_quote_quality(),
            )
            update_simulated_trading(
                [_sim_stock(quote_time="20260601132000")],
                {"records": []},
                state_path,
                "20260601132000",
                "2026-06-01",
                _fresh_quote_quality(),
            )
            first_watch = update_simulated_trading(
                [],
                {"records": [{"code": "600522", "name": "中天科技", "currentPrice": 9.5}]},
                state_path,
                "20260602093500",
                "2026-06-02",
                _fresh_quote_quality(),
            )
            result = update_simulated_trading(
                [],
                {"records": [{"code": "600522", "name": "中天科技", "currentPrice": 9.4}]},
                state_path,
                "20260602095000",
                "2026-06-02",
                _fresh_quote_quality(),
            )

        self.assertEqual(1, first_watch["account"]["positions"])
        self.assertTrue(any(item["reason"] == "out_of_pool_watch" for item in first_watch["events"]))
        self.assertEqual(0, result["account"]["positions"])
        self.assertEqual("sell", result["latestFills"][-1]["side"])
        self.assertEqual("out_of_pool_confirmed", result["latestFills"][-1]["reason"])

    def test_simulated_trading_holds_out_of_pool_stock_when_sector_breaks_out(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            state_path.write_text(
                json.dumps(
                    {
                        "account": {"initialCash": 1_000_000, "cash": 900_000, "realizedPnl": 0},
                        "positions": [
                            {
                                "code": "600487",
                                "name": "亨通光电",
                                "shares": 1200,
                                "avgCost": 76.677,
                                "costBasis": 92_035.4,
                                "entryTime": "2026-06-01T13:20:08",
                                "entryTradeDate": "2026-06-01",
                            }
                        ],
                        "fills": [],
                        "exitWatch": {"600487": {"confirmations": 2}},
                        "sectorWatch": {
                            "600487": {
                                "state": "strong_breakout",
                                "confirmations": 1,
                                "avgPctEma": 0.2,
                                "theme": "光通信",
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = update_simulated_trading(
                [],
                {
                    "records": [
                        {
                            "code": "600487",
                            "name": "亨通光电",
                            "currentPrice": 91.43,
                            "sectorContext": _sector_context(avg_pct=3.2, up_ratio=0.82, hot_score=300.0),
                        }
                    ]
                },
                state_path,
                "20260603095000",
                "2026-06-03",
                _fresh_quote_quality(),
            )

        self.assertEqual(1, result["account"]["positions"])
        self.assertFalse(any(row.get("side") == "sell" and row.get("status") == "filled" for row in result["fills"]))
        self.assertTrue(any(item["reason"] == "sector_strength_hold" for item in result["events"]))

    def test_simulated_trading_sells_out_of_pool_stock_when_sector_breaks_down(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            state_path.write_text(
                json.dumps(
                    {
                        "account": {"initialCash": 1_000_000, "cash": 900_000, "realizedPnl": 0},
                        "positions": [
                            {
                                "code": "600487",
                                "name": "亨通光电",
                                "shares": 1200,
                                "avgCost": 76.677,
                                "costBasis": 92_035.4,
                                "entryTime": "2026-06-01T13:20:08",
                                "entryTradeDate": "2026-06-01",
                            }
                        ],
                        "fills": [],
                        "sectorWatch": {
                            "600487": {
                                "state": "breakdown",
                                "confirmations": 1,
                                "avgPctEma": 2.1,
                                "theme": "光通信",
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = update_simulated_trading(
                [],
                {
                    "records": [
                        {
                            "code": "600487",
                            "name": "亨通光电",
                            "currentPrice": 74.8,
                            "sectorContext": _sector_context(avg_pct=-1.8, up_ratio=0.18, hot_score=40.0),
                        }
                    ]
                },
                state_path,
                "20260603095000",
                "2026-06-03",
                _fresh_quote_quality(),
            )

        self.assertEqual(0, result["account"]["positions"])
        self.assertEqual("sector_breakdown", result["latestFills"][-1]["reason"])

    def test_simulated_trading_trims_profitable_position_on_confirmed_sector_breakdown(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            state_path.write_text(
                json.dumps(
                    {
                        "account": {"initialCash": 1_000_000, "cash": 800_000, "realizedPnl": 0},
                        "positions": [
                            {
                                "code": "600487",
                                "name": "亨通光电",
                                "shares": 1200,
                                "avgCost": 76.677,
                                "costBasis": 92_035.4,
                                "entryTime": "2026-06-01T13:20:08",
                                "entryTradeDate": "2026-06-01",
                            }
                        ],
                        "fills": [],
                        "sectorWatch": {
                            "600487": {
                                "state": "breakdown",
                                "confirmations": 1,
                                "avgPctEma": 2.0,
                                "theme": "光通信",
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = update_simulated_trading(
                [
                    _sim_stock(code="600487", price=88.0, quote_time="20260603095000")
                    | {"sectorContext": _sector_context(avg_pct=-1.6, up_ratio=0.2, hot_score=45.0)}
                ],
                {"records": []},
                state_path,
                "20260603095000",
                "2026-06-03",
                _fresh_quote_quality(),
            )

        self.assertEqual(1, result["account"]["positions"])
        self.assertEqual(600, result["positions"][0]["shares"])
        self.assertEqual("sector_breakdown_trim", result["latestFills"][-1]["reason"])

    def test_sector_watch_does_not_confirm_again_for_same_quote_time(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            state_path.write_text(
                json.dumps(
                    {
                        "account": {"initialCash": 1_000_000, "cash": 900_000, "realizedPnl": 0},
                        "positions": [
                            {
                                "code": "600487",
                                "name": "亨通光电",
                                "shares": 1200,
                                "avgCost": 76.677,
                                "costBasis": 92_035.4,
                                "entryTime": "2026-06-01T13:20:08",
                                "entryTradeDate": "2026-06-01",
                            }
                        ],
                        "fills": [],
                        "sectorWatch": {
                            "600487": {
                                "state": "breakdown",
                                "confirmations": 1,
                                "avgPctEma": 2.0,
                                "lastQuoteTime": "20260603095000",
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = update_simulated_trading(
                [
                    _sim_stock(code="600487", price=74.8, quote_time="20260603095000")
                    | {"sectorContext": _sector_context(avg_pct=-1.8, up_ratio=0.18, quote_time="20260603095000")}
                ],
                {"records": []},
                state_path,
                "20260603200000",
                "2026-06-03",
                _fresh_quote_quality(),
            )

        self.assertEqual(1, result["sectorWatch"]["600487"]["confirmations"])
        self.assertFalse(any(row.get("reason") == "sector_breakdown" for row in result["fills"]))

    def test_simulated_trading_blocks_sell_before_continuous_auction(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            state_path.write_text(
                json.dumps(
                    {
                        "account": {"initialCash": 1_000_000, "cash": 907_964.6, "realizedPnl": 0},
                        "positions": [
                            {
                                "code": "600487",
                                "name": "亨通光电",
                                "shares": 1200,
                                "avgCost": 76.677,
                                "costBasis": 92_035.4,
                                "entryTime": "2026-06-01T13:20:08",
                                "entryTradeDate": "2026-06-01",
                            }
                        ],
                        "fills": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = update_simulated_trading(
                [],
                {"records": [{"code": "600487", "name": "亨通光电", "currentPrice": 83.12}]},
                state_path,
                "20260603090909",
                "2026-06-03",
                _fresh_quote_quality(),
            )

        self.assertEqual(1, result["account"]["positions"])
        self.assertFalse(any(row.get("side") == "sell" and row.get("status") == "filled" for row in result["fills"]))
        self.assertTrue(any(item["reason"] == "outside_trade_execution_window" for item in result["events"]))

    def test_simulated_trading_repairs_prefilled_sell_before_continuous_auction(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            state_path.write_text(
                json.dumps(
                    {
                        "config": {"earliestBuyTime": "00:00", "buyConfirmStartTime": "00:00"},
                        "account": {"initialCash": 1_000_000, "cash": 1_000_000, "realizedPnl": 0},
                        "positions": [],
                        "fills": [
                            {
                                "tradeDate": "2026-06-01",
                                "time": "2026-06-01T13:20:08",
                                "side": "buy",
                                "code": "600487",
                                "name": "亨通光电",
                                "shares": 1200,
                                "price": 76.677,
                                "gross": 92_012.4,
                                "fee": 23.0,
                                "status": "filled",
                                "reason": "confirmed_signal",
                            },
                            {
                                "assetVersion": "20260603090909",
                                "tradeDate": "2026-06-02",
                                "time": "2026-06-03T09:09:09",
                                "side": "sell",
                                "code": "600487",
                                "name": "亨通光电",
                                "shares": 1200,
                                "price": 83.037,
                                "gross": 99_644.4,
                                "fee": 74.73,
                                "stampTax": 49.82,
                                "status": "filled",
                                "reason": "out_of_pool",
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = update_simulated_trading(
                [_sim_stock(code="600487", price=91.43, quote_time="20260603112000")],
                {"records": []},
                state_path,
                "20260603112000",
                "2026-06-03",
                _fresh_quote_quality(),
            )

        repaired_sell = [row for row in result["fills"] if row.get("side") == "sell"][0]
        self.assertEqual(1, result["account"]["positions"])
        self.assertEqual("rejected", repaired_sell["status"])
        self.assertEqual("outside_trade_execution_window_after_repair", repaired_sell["reason"])
        self.assertEqual("2026-06-03", repaired_sell["tradeDate"])

    def test_simulated_trading_holds_limit_up_position_when_it_leaves_pool(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            state_path.write_text(
                json.dumps(
                    {
                        "account": {"initialCash": 1_000_000, "cash": 907_964.6, "realizedPnl": 0},
                        "positions": [
                            {
                                "code": "600487",
                                "name": "亨通光电",
                                "shares": 1200,
                                "avgCost": 76.677,
                                "costBasis": 92_035.4,
                                "entryTime": "2026-06-01T13:20:08",
                                "entryTradeDate": "2026-06-01",
                            }
                        ],
                        "fills": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = update_simulated_trading(
                [],
                {
                    "records": [
                        {
                            "code": "600487",
                            "name": "亨通光电",
                            "currentPrice": 91.43,
                            "currentPctChange": 10.0,
                            "isLimitUp": True,
                        }
                    ]
                },
                state_path,
                "20260603093500",
                "2026-06-03",
                _fresh_quote_quality(),
            )

        self.assertEqual(1, result["account"]["positions"])
        self.assertFalse(any(row.get("side") == "sell" and row.get("status") == "filled" for row in result["fills"]))
        self.assertTrue(any(item["reason"] == "limit_up_hold" for item in result["events"]))

    def test_simulated_trading_does_not_derisk_limit_up_position(self) -> None:
        risk_off_market = {
            "items": [
                {"symbol": "sh000001", "pctChange": -1.1},
                {"symbol": "sz399006", "pctChange": -1.4},
            ]
        }
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            state_path.write_text(
                json.dumps(
                    {
                        "account": {"initialCash": 1_000_000, "cash": 0, "realizedPnl": 0},
                        "positions": [
                            {
                                "code": "600487",
                                "name": "亨通光电",
                                "shares": 12000,
                                "avgCost": 76.677,
                                "costBasis": 920_354.0,
                                "marketValue": 1_097_160.0,
                                "entryTime": "2026-06-01T13:20:08",
                                "entryTradeDate": "2026-06-01",
                            }
                        ],
                        "fills": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = update_simulated_trading(
                [_sim_stock(code="600487", price=91.43, quote_time="20260603093500") | {"states": {"isLimitUp": True}}],
                {
                    "records": [
                        {
                            "code": "600487",
                            "name": "亨通光电",
                            "currentPrice": 91.43,
                            "currentPctChange": 10.0,
                            "isLimitUp": True,
                        }
                    ]
                },
                state_path,
                "20260603093500",
                "2026-06-03",
                _fresh_quote_quality(),
                {"marketRegimeTotalWeights": {"risk_off": 0.01}},
                risk_off_market,
            )

        self.assertEqual(1, result["account"]["positions"])
        self.assertFalse(any(row.get("side") == "sell" and row.get("status") == "filled" for row in result["fills"]))
        self.assertTrue(any(item["reason"] == "limit_up_hold" for item in result["events"]))

    def test_simulated_trading_does_not_duplicate_market_derisk_sell(self) -> None:
        risk_off_market = {
            "items": [
                {"symbol": "sh000001", "pctChange": -1.1},
                {"symbol": "sz399006", "pctChange": -1.4},
            ]
        }
        config = {
            "confirmationsRequired": 1,
            "maxPositions": 1,
            "maxSingleWeight": 0.5,
            "maxTotalWeight": 0.8,
            "marketRegimeTotalWeights": {"neutral": 0.8, "risk_off": 0.1},
            "marketRegimeSingleWeightCaps": {"neutral": 0.5, "risk_off": 0.5},
        }
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            update_simulated_trading(
                [_sim_stock(price=10.0, quote_time="20260601132000")],
                {"records": []},
                state_path,
                "20260601132000",
                "2026-06-01",
                _fresh_quote_quality(),
                config,
            )
            result = update_simulated_trading(
                [],
                {"records": [{"code": "600522", "name": "中天科技", "currentPrice": 10.0}]},
                state_path,
                "20260602093500",
                "2026-06-02",
                _fresh_quote_quality(),
                config,
                risk_off_market,
            )

        sell_fills = [row for row in result["latestFills"] if row.get("side") == "sell"]
        self.assertEqual(["market_de_risk"], [row.get("reason") for row in sell_fills])
        self.assertTrue(all(row.get("status") == "filled" for row in sell_fills))

    def test_simulated_trading_repairs_same_day_sell_violations(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            state_path.write_text(
                json.dumps(
                    {
                        "config": {"maxPositions": 1, "earliestBuyTime": "00:00", "buyConfirmStartTime": "00:00"},
                        "account": {"initialCash": 1_000_000, "cash": 1_000_000, "realizedPnl": 0},
                        "positions": [],
                        "fills": [
                            {
                                "tradeDate": "2026-06-01",
                                "time": "2026-06-01T09:50:00",
                                "side": "buy",
                                "code": "600522",
                                "name": "中天科技",
                                "shares": 1000,
                                "price": 10.0,
                                "gross": 10000.0,
                                "fee": 5.0,
                                "status": "filled",
                                "reason": "confirmed_signal",
                            },
                            {
                                "tradeDate": "2026-06-01",
                                "time": "2026-06-01T10:05:00",
                                "side": "sell",
                                "code": "600522",
                                "name": "中天科技",
                                "shares": 1000,
                                "price": 10.5,
                                "gross": 10500.0,
                                "fee": 10.25,
                                "status": "filled",
                                "reason": "out_of_pool",
                            },
                            {
                                "tradeDate": "2026-06-01",
                                "time": "2026-06-01T10:20:00",
                                "side": "buy",
                                "code": "000001",
                                "name": "平安银行",
                                "shares": 1000,
                                "price": 11.0,
                                "gross": 11000.0,
                                "fee": 5.0,
                                "status": "filled",
                                "reason": "confirmed_signal",
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = update_simulated_trading(
                [],
                {"records": [{"code": "600522", "name": "中天科技", "currentPrice": 10.2}]},
                state_path,
                "20260601103500",
                "2026-06-01",
                _fresh_quote_quality(),
            )

        self.assertEqual(1, result["account"]["positions"])
        self.assertEqual("600522", result["positions"][0]["code"])
        self.assertFalse(result["positions"][0]["canSell"])
        reasons = [row.get("reason") for row in result["fills"]]
        self.assertIn("t_plus_one_locked", reasons)
        self.assertIn("max_positions_after_t_plus_one_repair", reasons)

    def test_simulated_trading_repairs_before_window_buys(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            state_path.write_text(
                json.dumps(
                    {
                        "account": {"initialCash": 1_000_000, "cash": 1_000_000, "realizedPnl": 0},
                        "positions": [],
                        "fills": [
                            {
                                "tradeDate": "2026-06-01",
                                "time": "2026-06-01T09:50:00",
                                "side": "buy",
                                "code": "600522",
                                "name": "中天科技",
                                "shares": 1000,
                                "price": 10.0,
                                "gross": 10000.0,
                                "fee": 5.0,
                                "status": "filled",
                                "reason": "confirmed_signal",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = update_simulated_trading(
                [],
                {"records": []},
                state_path,
                "20260601120000",
                "2026-06-01",
                _fresh_quote_quality(),
            )

        self.assertEqual(0, result["account"]["positions"])
        self.assertEqual("rejected", result["fills"][0]["status"])
        self.assertEqual("before_buy_window_after_repair", result["fills"][0]["reason"])

    def test_simulated_trading_does_not_sell_on_stale_quote_quality(self) -> None:
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "simulated_trading.json"
            update_simulated_trading(
                [_sim_stock(quote_time="20260601130500")],
                {"records": []},
                state_path,
                "20260601130500",
                "2026-06-01",
                _fresh_quote_quality(),
            )
            update_simulated_trading(
                [_sim_stock(quote_time="20260601132000")],
                {"records": []},
                state_path,
                "20260601132000",
                "2026-06-01",
                _fresh_quote_quality(),
            )
            result = update_simulated_trading(
                [],
                {"records": [{"code": "600522", "name": "中天科技", "currentPrice": 9.5}]},
                state_path,
                "20260601133500",
                "2026-06-01",
                {"freshRatio": 0.5, "preferredStaleCount": 1},
            )

        self.assertEqual(1, result["account"]["positions"])
        self.assertEqual("buy", result["latestFills"][-1]["side"])
        self.assertTrue(result["guards"]["blockTrading"])

    def test_simulated_trading_counts_pending_buys_against_total_exposure(self) -> None:
        stocks = [
            _sim_stock(code=f"60052{idx}", price=10.0 + idx, quote_time="20260601132000")
            | {"name": f"测试{idx}", "rank": idx}
            for idx in range(1, 9)
        ]
        with TemporaryDirectory() as tmp:
            result = update_simulated_trading(
                stocks,
                {"records": []},
                Path(tmp) / "simulated_trading.json",
                "20260601132000",
                "2026-06-01",
                {"freshRatio": 1.0, "preferredStaleCount": 0},
                {"confirmationsRequired": 1, "maxPositions": 8, "maxSingleWeight": 0.2, "maxTotalWeight": 0.5},
            )

        self.assertLessEqual(result["account"]["exposurePct"], 50.0)
        self.assertLessEqual(result["account"]["positionValue"], result["account"]["equity"] * 0.5)

    def test_simulated_trading_uses_explicit_execution_time_instead_of_asset_version(self) -> None:
        with TemporaryDirectory() as tmp:
            result = update_simulated_trading(
                [_sim_stock(quote_time="20260601145000")],
                {"records": []},
                Path(tmp) / "simulated_trading.json",
                "20260601150506",
                "2026-06-01",
                _fresh_quote_quality(),
                {"confirmationsRequired": 1},
                execution_time="20260601145000",
            )

        self.assertEqual(1, result["account"]["positions"])
        self.assertEqual("filled", result["latestFills"][-1]["status"])
        self.assertEqual("2026-06-01T14:50:00", result["latestFills"][-1]["time"])

    def test_dashboard_simulation_execution_time_prefers_latest_quote_time(self) -> None:
        self.assertEqual("20260601145000", simulation_execution_time("20260601145000", "20260601150506"))
        self.assertEqual("20260601150506", simulation_execution_time(None, "20260601150506"))

    def test_dashboard_latest_quote_time_uses_stock_chart_tail_when_newer(self) -> None:
        self.assertEqual(
            "20260630150415",
            dashboard_builder.latest_dashboard_quote_time(
                "20260630100512",
                {"latestQuoteByCode": {"002745": {"quoteTime": "20260630150415"}}},
            ),
        )

    def test_dashboard_detects_decisions_missing_from_snapshot_map(self) -> None:
        decisions = [{"code": "600522"}, {"code": "000001"}, {"code": ""}, {}]
        snapshots = [{"code": "600522"}]

        self.assertEqual(["000001"], missing_snapshot_decision_codes(decisions, snapshots))

    def test_dashboard_output_display_path_accepts_external_tmp_output(self) -> None:
        self.assertEqual("/tmp/qa_dashboard_data_verify.js", output_display_path(Path("/tmp/qa_dashboard_data_verify.js")))

    def test_dashboard_automation_status_marks_missing_post_close_report(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir()
            (runs / "intraday_update_report_20260612.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "slot": "11:20",
                        "finished_at": "2026-06-12T11:20:06+08:00",
                        "selected": ["600522"],
                    }
                ),
                encoding="utf-8",
            )

            status = build_automation_status("2026-06-12", root=root)

        self.assertEqual("ok", status["items"][0]["status"])
        self.assertEqual("missing", status["items"][1]["status"])
        self.assertEqual("中午收盘", status["items"][0]["label"])
        self.assertEqual("下午收盘", status["items"][1]["label"])

    def test_dashboard_automation_status_accepts_forced_intraday_repair(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir()
            (runs / "intraday_update_report_20260715.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "forced": True,
                        "slot": None,
                        "finished_at": "2026-07-15T11:42:00+08:00",
                        "selected": ["600584"],
                    }
                ),
                encoding="utf-8",
            )

            status = build_automation_status("2026-07-15", root=root, now=datetime(2026, 7, 15, 11, 45))

        midday = next(item for item in status["items"] if item["key"] == "midday")
        self.assertEqual("ok", midday["status"])
        self.assertEqual("正常", midday["statusText"])

    def test_dashboard_automation_status_keeps_early_post_close_skip_pending(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir()
            (runs / "post_close_update_report_20260715.json").write_text(
                json.dumps(
                    {
                        "status": "skipped",
                        "reason": "outside_post_close_window",
                        "finished_at": "2026-07-15T09:05:00+08:00",
                    }
                ),
                encoding="utf-8",
            )

            status = build_automation_status("2026-07-15", root=root, now=datetime(2026, 7, 15, 10, 30))

        post_close = next(item for item in status["items"] if item["key"] == "post_close")
        self.assertEqual("pending", post_close["status"])
        self.assertEqual("待运行", post_close["statusText"])

    def test_dashboard_automation_status_marks_degraded_post_close_report(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir()
            (runs / "intraday_update_report_20260612.json").write_text(
                json.dumps({"status": "ok", "slot": "11:20", "finished_at": "2026-06-12T11:20:06+08:00"}),
                encoding="utf-8",
            )
            (runs / "post_close_update_report_20260612.json").write_text(
                json.dumps(
                    {
                        "status": "degraded",
                        "reason": "snapshot_fallback_used",
                        "finished_at": "2026-06-12T17:30:06+08:00",
                        "commands": [
                            {"command": ["python", "-m", "quant_agents.cli", "expand-free-pool"], "returncode": 1, "toleratedFailure": True},
                            {"command": ["python", "-m", "quant_agents.cli", "collect-info-evidence"], "returncode": 0},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            status = build_automation_status("2026-06-12", root=root)

        self.assertEqual("degraded", status["items"][1]["status"])
        self.assertEqual("降级完成", status["items"][1]["statusText"])
        self.assertEqual("snapshot_fallback_used", status["items"][1]["note"])

    def test_dashboard_automation_status_keeps_preopen_ok_when_news_deferred(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir()
            (runs / "preopen_check_report_20260701.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "news_status": "deferred",
                        "finished_at": "2026-07-01T08:40:06+08:00",
                        "commands": [
                            {"command": ["python", "-m", "quant_agents.cli", "collect-info-evidence", "--global-news-only"], "returncode": -1, "toleratedFailure": True}
                        ],
                    }
                ),
                encoding="utf-8",
            )

            status = build_automation_status("2026-07-01", root=root)

        preopen = next(item for item in status["items"] if item["key"] == "preopen")
        self.assertEqual("ok", preopen["status"])
        self.assertEqual("正常", preopen["statusText"])
        self.assertIn("新闻延后补抓", preopen["note"])

    def test_dashboard_automation_status_marks_future_same_day_tasks_pending(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir()
            (runs / "preopen_check_report_20260702.json").write_text(
                json.dumps({"status": "ok", "finished_at": "2026-07-02T08:37:19+08:00"}),
                encoding="utf-8",
            )

            status = build_automation_status("2026-07-02", root=root, now=datetime(2026, 7, 2, 8, 46))

        self.assertEqual("ok", status["summaryStatus"])
        by_key = {item["key"]: item for item in status["items"]}
        self.assertEqual("ok", by_key["preopen"]["status"])
        self.assertEqual("pending", by_key["midday"]["status"])
        self.assertEqual("待运行", by_key["midday"]["statusText"])
        self.assertEqual("pending", by_key["post_close"]["status"])

    def test_dashboard_automation_status_degrades_incomplete_candidate_pool(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir()
            (runs / "intraday_update_report_20260629.json").write_text(
                json.dumps({"status": "ok", "slot": "11:20", "finished_at": "2026-06-29T11:20:06+08:00"}),
                encoding="utf-8",
            )
            (runs / "post_close_update_report_20260629.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "finished_at": "2026-06-29T17:01:04+08:00",
                        "remaining": 1639,
                        "pool_complete": False,
                        "commands": [],
                    }
                ),
                encoding="utf-8",
            )

            status = build_automation_status("2026-06-29", root=root)

        self.assertEqual("degraded", status["items"][1]["status"])
        self.assertEqual("候选池未完成：剩余 1639 支未进入评分", status["items"][1]["note"])

    def test_candidate_pool_quality_marks_incomplete_expand_report(self) -> None:
        quality = dashboard_builder.build_candidate_pool_quality(
            {
                "eligible_candidates": 1759,
                "snapshots": 119,
                "skipped": 1,
                "remaining": 1639,
                "complete": False,
            },
            [{"code": str(index)} for index in range(119)],
        )

        self.assertEqual("partial", quality["status"])
        self.assertFalse(quality["poolComplete"])
        self.assertEqual(1759, quality["eligibleCandidates"])
        self.assertEqual(119, quality["deepScoredCandidates"])
        self.assertEqual(1639, quality["remainingCandidates"])
        self.assertAlmostEqual(0.0677, quality["coverageRatio"], places=4)
        self.assertIn("候选池未完成", quality["warning"])

    def test_preopen_window_only_accepts_weekday_preopen_times(self) -> None:
        self.assertTrue(in_preopen_window(datetime(2026, 6, 15, 8, 30, tzinfo=ZoneInfo("Asia/Shanghai"))))
        self.assertTrue(in_preopen_window(datetime(2026, 6, 15, 8, 50, tzinfo=ZoneInfo("Asia/Shanghai"))))
        self.assertTrue(in_preopen_window(datetime(2026, 6, 15, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))))
        self.assertFalse(in_preopen_window(datetime(2026, 6, 15, 8, 29, tzinfo=ZoneInfo("Asia/Shanghai"))))
        self.assertFalse(in_preopen_window(datetime(2026, 6, 15, 9, 26, tzinfo=ZoneInfo("Asia/Shanghai"))))
        self.assertFalse(in_preopen_window(datetime(2026, 6, 14, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))))

    def test_preopen_main_rebuilds_dashboard_after_preview_chain(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runs").mkdir()
            (root / "data" / "features").mkdir(parents=True)
            (root / "data" / "evidence").mkdir(parents=True)
            (root / "web_dashboard").mkdir()
            snapshot_path = root / "data" / "features" / "free_snapshots_20260614.json"
            evidence_path = root / "data" / "evidence" / "institutional_evidence_preopen_20260615.json"
            context_path = root / "data" / "evidence" / "context_evidence_preopen_20260615.json"
            overnight_path = root / "data" / "evidence" / "overnight_market_context_20260615.json"
            baseline_plan_path = root / "runs" / "snapshot_plan_preopen_baseline_20260615.json"
            plan_path = root / "runs" / "snapshot_plan_preopen_context_20260615.json"
            expand_report_path = root / "runs" / "free_pool_expand_20260614.json"
            snapshot_path.write_text(json.dumps([{"code": "600522", "name": "中天科技", "trade_date": "2026-06-14"}]), encoding="utf-8")
            expand_report_path.write_text(json.dumps({"eligible_candidates": 1, "snapshots": 1, "remaining": 0, "complete": True}), encoding="utf-8")
            (root / "web_dashboard" / "data.js").write_text(
                'window.QA_DATA = {"meta":{"assetVersion":"20260615083000","latestQuoteTime":"20260614150500"},"preferred":[{"code":"600522"}]};\n',
                encoding="utf-8",
            )
            report_path = root / "runs" / "preopen_check_report_20260615.json"
            argv = [
                "run_preopen_check.py",
                "--date",
                "20260615",
                "--report",
                str(report_path),
                "--snapshot",
                str(snapshot_path),
            ]
            loaded = {
                "info_evidence_collect_20260615_pre_open.json": {"evidence_codes": ["600522"], "stats": {"quotes": 1, "themes": 1, "news_events": 2}},
                "institutional_evidence_collect_20260614.json": {"evidence_codes": ["600522"], "stats": {"evidence_codes": 1}},
                "snapshot_plan_preopen_baseline_20260615.json": {"universe": ["600522"], "decisions": [{"code": "600522"}]},
                "snapshot_plan_preopen_context_20260615.json": {"universe": ["600522"], "decisions": [{"code": "600522"}]},
                "context_evidence_preopen_20260615.json": {
                    "600522": {
                        "quote": {"price": 10},
                        "theme": {"hot_rank": 1},
                        "news": {"event_count": 2},
                    }
                },
                "overnight_market_context_20260615.json": {"status": "ok", "sectors": [{"name": "半导体"}]},
            }

            def fake_run_command(
                report: dict,
                command: list[str],
                allow_failure: bool = False,
                timeout_seconds: int | None = None,
            ) -> dict:
                item = {"command": command, "returncode": 0, "stdout": "", "stderr": ""}
                report.setdefault("commands", []).append(item)
                if "collect-institutional-evidence" in command:
                    evidence_path.write_text("{}", encoding="utf-8")
                if "collect-info-evidence" in command:
                    context_path.write_text("{}", encoding="utf-8")
                if any(str(part).endswith("collect_overnight_market_context.py") for part in command):
                    overnight_path.write_text(json.dumps(loaded["overnight_market_context_20260615.json"]), encoding="utf-8")
                if "plan-from-snapshots" in command:
                    out_path = Path(command[command.index("--out") + 1])
                    out_path.write_text(json.dumps(loaded[out_path.name]), encoding="utf-8")
                return item

            def fake_load_json(path: Path) -> dict:
                return loaded[path.name]

            with patch("tools.run_preopen_check.ROOT", root), patch(
                "tools.run_preopen_check.sys.argv", argv
            ), patch("tools.run_preopen_check.datetime") as fake_datetime, patch(
                "tools.run_preopen_check.run_command", side_effect=fake_run_command
            ), patch(
                "tools.run_preopen_check.load_json", side_effect=fake_load_json
            ):
                fake_datetime.now.return_value = datetime(2026, 6, 15, 8, 50, tzinfo=ZoneInfo("Asia/Shanghai"))
                fake_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
                run_preopen_check.main()

            report = json.loads(report_path.read_text(encoding="utf-8"))
            commands = [item["command"] for item in report["commands"]]
            command_labels = [
                "collect_overnight_market_context.py"
                if any(str(part).endswith("collect_overnight_market_context.py") for part in cmd)
                else "build_dashboard_data.py"
                if any(str(part).endswith("build_dashboard_data.py") for part in cmd)
                else cmd[3]
                for cmd in commands
            ]

        self.assertEqual("ok", report["status"])
        self.assertEqual(
            [
                "collect-institutional-evidence",
                "collect-info-evidence",
                "collect-info-evidence",
                "collect_overnight_market_context.py",
                "plan-from-snapshots",
                "plan-from-snapshots",
                "build_dashboard_data.py",
            ],
            command_labels,
        )
        self.assertIn("--no-news", commands[1])
        self.assertIn("--codes", commands[2])
        self.assertIn("--no-quotes", commands[2])
        self.assertIn("--no-theme", commands[2])
        self.assertIn("--global-news-only", commands[2])
        self.assertIn("--no-llm", commands[2])
        self.assertLessEqual(len(commands[2][commands[2].index("--codes") + 1].split(",")), 12)
        self.assertEqual(["600522"], report["selected"])
        self.assertEqual(1, report["context_evidence_codes"])
        self.assertEqual(1, report["quote_coverage"])
        self.assertEqual(1, report["theme_coverage"])
        self.assertEqual(2, report["news_events"])
        self.assertEqual("runs/snapshot_plan_preopen_baseline_20260615.json", report["baseline_plan_path"])
        self.assertEqual("runs/snapshot_plan_preopen_context_20260615.json", report["context_plan_path"])
        self.assertEqual("data/evidence/institutional_evidence_preopen_20260615.json", report["evidence_path"])
        self.assertEqual("data/evidence/overnight_market_context_20260615.json", report["overnight_context_path"])
        dashboard_command = commands[-1]
        self.assertIn("tools/build_dashboard_data.py", dashboard_command)
        self.assertIn("--automation-date", dashboard_command)
        self.assertIn("2026-06-15", dashboard_command)
        self.assertIn("--skip-archive-intraday", dashboard_command)
        self.assertNotIn("--skip-stock-chart-cache", dashboard_command)
        self.assertEqual("http://127.0.0.1:8788/index.html", report["website_url"])
        self.assertFalse((root / "runs" / "intraday_update_state.json").exists())

    def test_preopen_defaults_keep_focused_news_bounded_for_morning_window(self) -> None:
        argv = ["run_preopen_check.py", "--date", "20260615", "--force"]
        with patch("tools.run_preopen_check.sys.argv", argv):
            args = run_preopen_check.parse_args()

        self.assertLessEqual(args.news_focus_limit, 12)
        self.assertLessEqual(args.info_timeout_seconds, 180)

    def test_preopen_main_skips_late_trigger_without_running_commands(self) -> None:
        with TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "preopen_report.json"
            argv = ["run_preopen_check.py", "--date", "20260615", "--report", str(report_path)]
            late = datetime(2026, 6, 15, 9, 26, tzinfo=ZoneInfo("Asia/Shanghai"))
            with patch("tools.run_preopen_check.sys.argv", argv), patch(
                "tools.run_preopen_check.datetime"
            ) as fake_datetime, patch("tools.run_preopen_check.run_command") as run_command:
                fake_datetime.now.return_value = late
                fake_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
                run_preopen_check.main()

            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual("skipped", report["status"])
        self.assertEqual("skipped_due_late_trigger", report["reason"])
        run_command.assert_not_called()

    def test_preopen_window_starts_at_0830_shanghai(self) -> None:
        self.assertFalse(in_preopen_window(datetime(2026, 6, 15, 8, 29, tzinfo=ZoneInfo("Asia/Shanghai"))))
        self.assertTrue(in_preopen_window(datetime(2026, 6, 15, 8, 30, tzinfo=ZoneInfo("Asia/Shanghai"))))

    def test_preopen_run_command_records_tolerated_timeout_failure(self) -> None:
        report: dict = {}
        with patch(
            "tools.run_preopen_check.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["python", "-m", "quant_agents.cli", "collect-info-evidence"], 5),
        ):
            item = run_preopen_check.run_command(
                report,
                ["python", "-m", "quant_agents.cli", "collect-info-evidence"],
                allow_failure=True,
                timeout_seconds=5,
            )

        self.assertTrue(item["timedOut"])
        self.assertTrue(item["toleratedFailure"])
        self.assertEqual(-1, item["returncode"])
        self.assertIn("command timed out after 5s", item["stderr"])

    def test_preopen_global_news_timeout_is_deferred_not_blocking(self) -> None:
        item = {
            "command": [
                "python",
                "-m",
                "quant_agents.cli",
                "collect-info-evidence",
                "--global-news-only",
            ],
            "returncode": -1,
            "toleratedFailure": True,
        }

        self.assertTrue(run_preopen_check.is_deferred_news_failure(item))

    def test_preopen_lock_prevents_duplicate_instances(self) -> None:
        with TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "preopen.lock"
            first = run_preopen_check.acquire_run_lock(lock_path)
            try:
                second = run_preopen_check.acquire_run_lock(lock_path)
            finally:
                first.close()

        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_post_close_window_accepts_weekday_after_close_only(self) -> None:
        self.assertTrue(in_post_close_window(datetime(2026, 6, 15, 15, 10, tzinfo=ZoneInfo("Asia/Shanghai"))))
        self.assertTrue(in_post_close_window(datetime(2026, 6, 15, 16, 30, tzinfo=ZoneInfo("Asia/Shanghai"))))
        self.assertFalse(in_post_close_window(datetime(2026, 6, 15, 15, 9, tzinfo=ZoneInfo("Asia/Shanghai"))))
        self.assertFalse(in_post_close_window(datetime(2026, 6, 14, 16, 30, tzinfo=ZoneInfo("Asia/Shanghai"))))

    def test_post_close_main_skips_outside_window_without_running_commands(self) -> None:
        with TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "post_close_report.json"
            argv = ["run_post_close_update.py", "--date", "20260615", "--report", str(report_path)]
            early = datetime(2026, 6, 15, 15, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
            with patch("tools.run_post_close_update.sys.argv", argv), patch(
                "tools.run_post_close_update.datetime"
            ) as fake_datetime, patch("tools.run_post_close_update.run_command") as run_command:
                fake_datetime.now.return_value = early
                fake_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
                run_post_close_update.main()

            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual("skipped", report["status"])
        self.assertEqual("outside_post_close_window", report["reason"])
        run_command.assert_not_called()

    def test_post_close_main_skips_recheck_after_completed_ok_report(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "runs" / "post_close_update_report_20260615.json"
            (root / "runs").mkdir()
            (root / "data" / "features").mkdir(parents=True)
            (root / "data" / "evidence").mkdir(parents=True)
            (root / "data" / "features" / "free_snapshots_20260615.json").write_text("[]", encoding="utf-8")
            (root / "runs" / "snapshot_plan_close_20260615.json").write_text("{}", encoding="utf-8")
            (root / "web_dashboard").mkdir()
            (root / "web_dashboard" / "close-brief.html").write_text("<html></html>", encoding="utf-8")
            report_path.write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "finished_at": "2026-06-15T16:35:00+08:00",
                        "selected": ["600522"],
                        "commands": [{"command": ["python", "-m", "quant_agents.cli", "expand-free-pool"], "returncode": 0}],
                    }
                ),
                encoding="utf-8",
            )
            argv = ["run_post_close_update.py", "--date", "20260615", "--report", str(report_path)]

            with patch("tools.run_post_close_update.ROOT", root), patch(
                "tools.run_post_close_update.sys.argv", argv
            ), patch("tools.run_post_close_update.datetime") as fake_datetime, patch(
                "tools.run_post_close_update.run_command"
            ) as run_command:
                fake_datetime.now.return_value = datetime(2026, 6, 15, 17, 31, tzinfo=ZoneInfo("Asia/Shanghai"))
                fake_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
                run_post_close_update.main()

            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual("ok", report["status"])
        self.assertEqual("post_close_already_completed", report["last_skip_reason"])
        self.assertEqual("2026-06-15T17:31:00+08:00", report["last_skipped_at"])
        self.assertEqual(["600522"], report["selected"])
        run_command.assert_not_called()

    def test_post_close_main_runs_expected_command_chain_when_forced(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "post_close_report.json"
            argv = ["run_post_close_update.py", "--date", "20260615", "--report", str(report_path), "--force"]
            loaded = {
                "free_pool_expand_20260615.json": {"eligible_candidates": 10, "snapshots": 8, "skipped": 1, "remaining": 1, "complete": False},
                "snapshot_plan_close_20260615.json": {"universe": ["600522"], "decisions": [{"code": "600522"}]},
            }

            def fake_load_json(path: Path) -> dict:
                return loaded[path.name]

            with patch("tools.run_post_close_update.ROOT", root), patch(
                "tools.run_post_close_update.sys.argv", argv
            ), patch(
                "tools.run_post_close_update.datetime"
            ) as fake_datetime, patch("tools.run_post_close_update.run_command") as run_command, patch(
                "tools.run_post_close_update.load_json", side_effect=fake_load_json
            ):
                fake_datetime.now.return_value = datetime(2026, 6, 15, 15, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
                fake_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
                run_post_close_update.main()

            report = json.loads(report_path.read_text(encoding="utf-8"))
            commands = [call.args[1] for call in run_command.call_args_list]

        self.assertEqual("degraded", report["status"])
        self.assertEqual(["expand-free-pool"], [cmd[3] for cmd in commands])
        self.assertEqual(["600522"], report["selected"])
        self.assertEqual("web_dashboard/close-brief.html", report["close_brief_path"])
        self.assertEqual(1, report["remaining"])
        self.assertFalse(report["pool_complete"])

    def test_post_close_defaults_expand_more_than_legacy_120(self) -> None:
        argv = ["run_post_close_update.py", "--date", "20260615", "--force"]
        with patch("tools.run_post_close_update.sys.argv", argv):
            args = run_post_close_update.parse_args()

        self.assertGreaterEqual(args.batch_size * args.max_batches, 2000)

    def test_post_close_main_continues_with_latest_snapshot_when_expansion_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_dir = root / "data" / "features"
            snapshot_dir.mkdir(parents=True)
            (snapshot_dir / "free_snapshots_20260610.json").write_text(
                json.dumps([{"code": "600522", "name": "中天科技"}]),
                encoding="utf-8",
            )
            report_path = root / "runs" / "post_close_report.json"
            argv = ["run_post_close_update.py", "--date", "20260615", "--report", str(report_path), "--force"]
            loaded = {
                "free_snapshots_20260610.json": [{"code": "600522", "name": "中天科技"}],
                "snapshot_plan_close_20260615.json": {"universe": ["600522"], "decisions": [{"code": "600522"}]},
            }

            def fake_run_command(
                report: dict,
                command: list[str],
                allow_failure: bool = False,
                timeout_seconds: int | None = None,
            ) -> dict:
                item = {
                    "command": command,
                    "returncode": 1 if command[3] == "expand-free-pool" else 0,
                    "stdout": "",
                    "stderr": "eastmoney unavailable" if command[3] == "expand-free-pool" else "",
                }
                if item["returncode"] != 0 and allow_failure:
                    item["toleratedFailure"] = True
                report.setdefault("commands", []).append(item)
                if item["returncode"] != 0 and not allow_failure:
                    raise run_post_close_update.CommandFailed("boom")
                return item

            def fake_load_json(path: Path) -> dict | list:
                return loaded[path.name]

            with patch("tools.run_post_close_update.ROOT", root), patch(
                "tools.run_post_close_update.sys.argv", argv
            ), patch("tools.run_post_close_update.datetime") as fake_datetime, patch(
                "tools.run_post_close_update.run_command", side_effect=fake_run_command
            ), patch(
                "tools.run_post_close_update.load_json", side_effect=fake_load_json
            ):
                fake_datetime.now.return_value = datetime(2026, 6, 15, 17, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
                fake_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
                run_post_close_update.main()

            report = json.loads(report_path.read_text(encoding="utf-8"))
            commands = report["commands"]

        self.assertEqual("degraded", report["status"])
        self.assertEqual("snapshot_fallback_used", report["reason"])
        self.assertTrue(report["snapshotFallback"]["used"])
        self.assertEqual("data/features/free_snapshots_20260610.json", report["snapshotFallback"]["path"])
        self.assertEqual(["expand-free-pool"], [cmd["command"][3] for cmd in commands])
        self.assertTrue(commands[0]["toleratedFailure"])
        self.assertEqual(["600522"], report["selected"])

    def test_post_close_main_builds_fallback_plan_when_expansion_fails_before_plan(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_dir = root / "data" / "features"
            snapshot_dir.mkdir(parents=True)
            fallback_snapshot = snapshot_dir / "free_snapshots_20260610.json"
            fallback_snapshot.write_text(
                json.dumps([{"code": "600522", "name": "中天科技"}]),
                encoding="utf-8",
            )
            report_path = root / "runs" / "post_close_report.json"
            plan_path = root / "runs" / "snapshot_plan_close_20260615.json"
            argv = ["run_post_close_update.py", "--date", "20260615", "--report", str(report_path), "--force"]

            def fake_run_command(
                report: dict,
                command: list[str],
                allow_failure: bool = False,
                timeout_seconds: int | None = None,
            ) -> dict:
                returncode = 1 if command[3] == "expand-free-pool" else 0
                item = {"command": command, "returncode": returncode, "stdout": "", "stderr": ""}
                if returncode != 0 and allow_failure:
                    item["toleratedFailure"] = True
                report.setdefault("commands", []).append(item)
                if command[3] == "plan-from-snapshots":
                    out_path = Path(command[command.index("--out") + 1])
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_text(
                        json.dumps({"universe": ["600522"], "decisions": [{"code": "600522"}]}),
                        encoding="utf-8",
                    )
                if returncode != 0 and not allow_failure:
                    raise run_post_close_update.CommandFailed("boom")
                return item

            with patch("tools.run_post_close_update.ROOT", root), patch(
                "tools.run_post_close_update.sys.argv", argv
            ), patch("tools.run_post_close_update.datetime") as fake_datetime, patch(
                "tools.run_post_close_update.run_command", side_effect=fake_run_command
            ):
                fake_datetime.now.return_value = datetime(2026, 6, 15, 17, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
                fake_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
                run_post_close_update.main()

            report = json.loads(report_path.read_text(encoding="utf-8"))
            commands = [item["command"][3] for item in report["commands"]]
            plan_exists = plan_path.exists()

        self.assertEqual("degraded", report["status"])
        self.assertTrue(plan_exists)
        self.assertEqual(["expand-free-pool", "plan-from-snapshots"], commands)
        self.assertEqual(["600522"], report["selected"])
        self.assertEqual("data/features/free_snapshots_20260610.json", report["snapshotFallback"]["path"])

    def test_post_close_main_writes_error_report_when_fallback_plan_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_dir = root / "data" / "features"
            snapshot_dir.mkdir(parents=True)
            (snapshot_dir / "free_snapshots_20260610.json").write_text(
                json.dumps([{"code": "600522", "name": "中天科技"}]),
                encoding="utf-8",
            )
            report_path = root / "runs" / "post_close_report.json"
            argv = ["run_post_close_update.py", "--date", "20260615", "--report", str(report_path), "--force"]

            def fake_run_command(
                report: dict,
                command: list[str],
                allow_failure: bool = False,
                timeout_seconds: int | None = None,
            ) -> dict:
                returncode = 1 if command[3] == "expand-free-pool" else 2
                item = {"command": command, "returncode": returncode, "stdout": "", "stderr": "failed"}
                if returncode != 0 and allow_failure:
                    item["toleratedFailure"] = True
                report.setdefault("commands", []).append(item)
                if returncode != 0 and not allow_failure:
                    report["status"] = "error"
                    report["failed_command"] = command
                    raise run_post_close_update.CommandFailed("boom")
                return item

            with patch("tools.run_post_close_update.ROOT", root), patch(
                "tools.run_post_close_update.sys.argv", argv
            ), patch("tools.run_post_close_update.datetime") as fake_datetime, patch(
                "tools.run_post_close_update.run_command", side_effect=fake_run_command
            ):
                fake_datetime.now.return_value = datetime(2026, 6, 15, 17, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
                fake_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
                with self.assertRaises(SystemExit):
                    run_post_close_update.main()

            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual("error", report["status"])
        self.assertEqual("plan-from-snapshots", report["failed_command"][3])
        self.assertEqual(["expand-free-pool", "plan-from-snapshots"], [item["command"][3] for item in report["commands"]])

    def test_post_close_run_command_records_tolerated_timeout_failure(self) -> None:
        report: dict[str, object] = {}
        with patch(
            "tools.run_post_close_update.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["python", "-m", "quant_agents.cli", "expand-free-pool"], 5),
        ):
            item = run_post_close_update.run_command(
                report,
                ["python", "-m", "quant_agents.cli", "expand-free-pool"],
                allow_failure=True,
                timeout_seconds=5,
            )

        self.assertEqual(-1, item["returncode"])
        self.assertTrue(item["timedOut"])
        self.assertTrue(item["toleratedFailure"])
        self.assertIn("timed out", item["stderr"])

def _force_rebalance_config(config: AppConfig) -> AppConfig:
    return replace(config, strategy=replace(config.strategy, rebalance_weekdays=(0, 1, 2, 3, 4, 5, 6)))


if __name__ == "__main__":
    unittest.main()
