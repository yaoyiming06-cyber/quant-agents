from __future__ import annotations

import html
import importlib.util
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .llm import DeepSeekClient, LLMError
from .market_sources import TencentQuoteClient, TonghuashunHotClient
from .models import StockSnapshot
from .utils import clamp


HIGH_RISK_KEYWORDS = (
    "立案调查",
    "行政处罚",
    "证监会调查",
    "涉嫌违法",
    "退市",
    "终止上市",
    "暂停上市",
    "被实施st",
    "被实施 st",
    "债务逾期",
    "违规担保",
    "重大诉讼",
    "重大风险",
    "investigation",
    "penalty",
    "delist",
    "fraud",
)
NEGATIVE_KEYWORDS = (
    "减持",
    "被动减持",
    "问询函",
    "监管函",
    "警示函",
    "业绩预亏",
    "业绩下降",
    "亏损扩大",
    "商誉减值",
    "质押",
    "解除合作",
    "合同终止",
    "negative",
    "investigation",
    "penalty",
    "delist",
    "fraud",
    "major_reduction",
)
POSITIVE_KEYWORDS = (
    "回购",
    "增持",
    "中标",
    "签订合同",
    "重大合同",
    "战略合作",
    "业绩预增",
    "扭亏",
    "利润增长",
    "分红",
    "股权激励",
    "重组",
    "并购",
    "定增获批",
    "获得补助",
    "positive",
    "buyback",
    "earnings_beat",
    "contract",
    "policy_support",
)


@dataclass(frozen=True)
class NewsEvent:
    code: str
    title: str
    source: str
    publish_time: str | None
    url: str | None
    polarity: str
    severity: str
    confidence: float
    keywords: list[str]
    reason: str


class NewsCollectionError(RuntimeError):
    pass


class CninfoAnnouncementClient:
    query_url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"

    def __init__(self, cache_dir: Path | str = Path("data/cache/source/cninfo"), pause: float = 0.25) -> None:
        self.cache_dir = Path(cache_dir)
        self.pause = pause

    def fetch_announcements(self, code: str, start: date, end: date, limit: int = 20, use_cache: bool = True) -> list[dict[str, Any]]:
        cache_path = self.cache_dir / f"{code}_{start:%Y%m%d}_{end:%Y%m%d}.json"
        if use_cache and cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))
        data = urllib.parse.urlencode(
            {
                "pageNum": "1",
                "pageSize": str(limit),
                "column": _cninfo_column(code),
                "tabName": "fulltext",
                "stock": code,
                "searchkey": "",
                "secid": "",
                "category": "",
                "trade": "",
                "seDate": f"{start:%Y-%m-%d}~{end:%Y-%m-%d}",
                "sortName": "time",
                "sortType": "desc",
                "isHLtitle": "true",
            }
        ).encode()
        request = urllib.request.Request(
            self.query_url,
            data=data,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.cninfo.com.cn/",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise NewsCollectionError(f"cninfo_failed:{code}:{error}") from error
        rows = payload.get("announcements") or []
        events = [_normalise_cninfo_announcement(code, row) for row in rows]
        _write_json(cache_path, events)
        return events


class EastmoneyNewsClient:
    search_url = "https://so.eastmoney.com/news/s"

    def __init__(self, cache_dir: Path | str = Path("data/cache/source/eastmoney_news"), pause: float = 0.25) -> None:
        self.cache_dir = Path(cache_dir)
        self.pause = pause

    def fetch_stock_news(
        self,
        code: str,
        name: str,
        start: date,
        end: date,
        limit: int = 20,
        use_cache: bool = True,
    ) -> list[dict[str, Any]]:
        cache_path = self.cache_dir / f"{code}_{start:%Y%m%d}_{end:%Y%m%d}.json"
        if use_cache and cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))
        keyword = f"{code} {name}".strip()
        url = f"{self.search_url}?{urllib.parse.urlencode({'keyword': keyword})}"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.eastmoney.com/"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read().decode(response.headers.get_content_charset() or "utf-8", errors="ignore")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise NewsCollectionError(f"eastmoney_news_failed:{code}:{error}") from error
        events = _parse_news_html(code, raw, start, end, limit)
        _write_json(cache_path, events)
        return events


class AkshareNewsClient:
    """AKShare news adapter for the three free news feeds in the data plan."""

    def __init__(self, cache_dir: Path | str = Path("data/cache/source/akshare_news"), pause: float = 0.25) -> None:
        self.cache_dir = Path(cache_dir)
        self.pause = pause
        self.stats: dict[str, Any] = {}

    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("akshare") is not None

    def fetch_stock_news(
        self,
        code: str,
        name: str,
        start: date,
        end: date,
        limit: int = 20,
        use_cache: bool = True,
    ) -> list[dict[str, Any]]:
        cache_path = self.cache_dir / "stock_news_em" / f"{code}_{start:%Y%m%d}_{end:%Y%m%d}.json"
        if use_cache and cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))
        ak = self._akshare()
        records = self._call_stock_news_em(ak, code)
        events = [
            _normalise_akshare_record(code, record, "akshare:stock_news_em")
            for record in records
        ]
        events = _filter_news_window([event for event in events if _mentions_stock(event, code, name)], start, end, limit)
        _write_json(cache_path, events)
        return events

    def fetch_cls_alerts(self, trade_date: date, use_cache: bool = True) -> list[dict[str, Any]]:
        cache_path = self.cache_dir / "stock_info_global_cls" / f"{trade_date:%Y%m%d}.json"
        if use_cache and cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))
        ak = self._akshare()
        records = self._call_first_available(ak, ("stock_info_global_cls", "stock_telegraph_cls", "stock_zh_a_alerts_cls"))
        events = [_normalise_akshare_record("*", record, "akshare:stock_info_global_cls") for record in records]
        _write_json(cache_path, events)
        return events

    def fetch_eastmoney_global_news(self, trade_date: date, use_cache: bool = True) -> list[dict[str, Any]]:
        cache_path = self.cache_dir / "stock_info_global_em" / f"{trade_date:%Y%m%d}.json"
        if use_cache and cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))
        ak = self._akshare()
        records = self._call_first_available(ak, ("stock_info_global_em", "stock_news_main_em", "stock_info_global_ths"))
        events = [_normalise_akshare_record("*", record, "akshare:stock_info_global_em") for record in records]
        _write_json(cache_path, events)
        return events

    def _akshare(self) -> Any:
        if not self.available():
            raise NewsCollectionError("akshare_not_installed")
        try:
            import akshare as ak
        except Exception as error:  # pragma: no cover - optional package
            raise NewsCollectionError(f"akshare_import_failed:{error}") from error
        return ak

    def _call_stock_news_em(self, ak: Any, code: str) -> list[dict[str, Any]]:
        func = getattr(ak, "stock_news_em", None)
        if func is None:
            raise NewsCollectionError("akshare_missing_function:stock_news_em")
        errors: list[str] = []
        for kwargs in ({"symbol": code}, {"stock": code}, {"code": code}):
            try:
                return _records_from_frame(func(**kwargs))
            except TypeError as error:
                errors.append(str(error))
            except Exception as error:
                raise NewsCollectionError(f"akshare_stock_news_em_failed:{code}:{error}") from error
        try:
            return _records_from_frame(func(code))
        except Exception as error:
            raise NewsCollectionError(f"akshare_stock_news_em_failed:{code}:{error}; tried={errors}") from error

    def _call_first_available(self, ak: Any, names: tuple[str, ...]) -> list[dict[str, Any]]:
        missing: list[str] = []
        for name in names:
            func = getattr(ak, name, None)
            if func is None:
                missing.append(name)
                continue
            try:
                return _records_from_frame(func())
            except Exception as error:
                raise NewsCollectionError(f"akshare_{name}_failed:{error}") from error
        raise NewsCollectionError(f"akshare_missing_function:{','.join(missing)}")


class InformationEvidenceCollector:
    def __init__(
        self,
        cninfo: CninfoAnnouncementClient | None = None,
        eastmoney_news: EastmoneyNewsClient | None = None,
        akshare_news: AkshareNewsClient | None = None,
        tencent: TencentQuoteClient | None = None,
        tonghuashun: TonghuashunHotClient | None = None,
        llm: DeepSeekClient | None = None,
    ) -> None:
        self.cninfo = cninfo or CninfoAnnouncementClient()
        self.eastmoney_news = eastmoney_news or EastmoneyNewsClient()
        self.akshare_news = akshare_news or AkshareNewsClient()
        self.tencent = tencent or TencentQuoteClient()
        self.tonghuashun = tonghuashun or TonghuashunHotClient()
        self.llm = llm or DeepSeekClient()
        self.stats: dict[str, Any] = {}

    def collect(
        self,
        snapshots: list[StockSnapshot],
        trade_date: date,
        mode: str = "post-close",
        lookback_days: int = 3,
        max_news_per_code: int = 12,
        include_quotes: bool = True,
        include_theme: bool = True,
        include_news: bool = True,
        use_cache: bool = True,
        global_news_only: bool = False,
        initial_evidence: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        codes = [snapshot.code for snapshot in snapshots]
        by_code = {snapshot.code: snapshot for snapshot in snapshots}
        initial_evidence = initial_evidence or {}
        evidence: dict[str, dict[str, Any]] = {
            code: dict(initial_evidence.get(code, {})) if isinstance(initial_evidence.get(code), dict) else {}
            for code in codes
        }
        errors: list[str] = []
        start = trade_date - timedelta(days=max(1, lookback_days))

        if include_quotes and codes:
            try:
                quotes = self.tencent.fetch_quotes(codes, trade_date=trade_date, use_cache=use_cache)
                for code, quote in quotes.items():
                    evidence.setdefault(code, {})["quote"] = quote
            except Exception as error:
                errors.append(f"tencent_quotes:{error}")

        if include_theme and codes:
            try:
                themes = self.tonghuashun.collect_theme_evidence(codes, trade_date=trade_date, use_cache=use_cache)
                for code, theme in themes.items():
                    evidence.setdefault(code, {})["theme"] = theme
            except Exception as error:
                errors.append(f"tonghuashun_hot:{error}")

        news_codes = 0
        news_events = 0
        global_events: list[dict[str, Any]] = []
        cninfo_errors = 0
        cninfo_disabled = False
        stock_news_errors = 0
        stock_news_disabled = False
        if include_news:
            try:
                global_events.extend(self.akshare_news.fetch_cls_alerts(trade_date, use_cache=use_cache))
            except NewsCollectionError as error:
                errors.append(str(error))
            try:
                global_events.extend(self.akshare_news.fetch_eastmoney_global_news(trade_date, use_cache=use_cache))
            except NewsCollectionError as error:
                errors.append(str(error))
            for snapshot in snapshots:
                raw_events: list[dict[str, Any]] = []
                attempted_per_code_fetch = False
                if not global_news_only and not cninfo_disabled:
                    attempted_per_code_fetch = True
                    try:
                        raw_events.extend(self.cninfo.fetch_announcements(snapshot.code, start, trade_date, max_news_per_code, use_cache))
                    except NewsCollectionError as error:
                        cninfo_errors += 1
                        errors.append(str(error))
                        if cninfo_errors >= 3:
                            cninfo_disabled = True
                            errors.append("cninfo_announcements_disabled_after_3_errors")
                if not global_news_only and not stock_news_disabled:
                    attempted_per_code_fetch = True
                    try:
                        raw_events.extend(
                            self.akshare_news.fetch_stock_news(
                                snapshot.code,
                                snapshot.name,
                                start,
                                trade_date,
                                max_news_per_code,
                                use_cache,
                            )
                        )
                    except NewsCollectionError as error:
                        stock_news_errors += 1
                        errors.append(str(error))
                        if stock_news_errors >= 3:
                            stock_news_disabled = True
                            errors.append("akshare_stock_news_em_disabled_after_3_errors")
                raw_events.extend(_match_global_events(snapshot, global_events, evidence.get(snapshot.code, {}).get("theme"), max_news_per_code))
                events = [_event_from_raw(snapshot.code, item) for item in raw_events]
                if events:
                    news_codes += 1
                    news_events += len(events)
                evidence.setdefault(snapshot.code, {})["news"] = summarize_news_events(
                    snapshot.code,
                    events,
                    by_code.get(snapshot.code),
                    mode,
                    self.llm,
                )
                if attempted_per_code_fetch:
                    time.sleep(max(self.cninfo.pause, self.eastmoney_news.pause))

        self.stats = {
            "mode": mode,
            "trade_date": f"{trade_date:%Y-%m-%d}",
            "codes": len(codes),
            "quotes": sum(1 for item in evidence.values() if item.get("quote")),
            "themes": sum(1 for item in evidence.values() if item.get("theme")),
            "news_codes": news_codes,
            "news_events": news_events,
            "global_news_events": len(global_events),
            "cninfo_errors": cninfo_errors,
            "cninfo_disabled": cninfo_disabled,
            "stock_news_errors": stock_news_errors,
            "stock_news_disabled": stock_news_disabled,
            "errors": errors[:30],
            "akshare": {
                "available": self.akshare_news.available(),
                "source": "stock_news_em + stock_info_global_cls + stock_info_global_em",
            },
            "global_news_only": global_news_only,
            "tonghuashun": self.tonghuashun.stats,
            "llm": {
                "provider": "deepseek" if self.llm.available else "rules",
                "available": self.llm.available,
            },
        }
        return {code: item for code, item in evidence.items() if item}


def classify_news_text(title: str, content: str = "") -> tuple[str, str, float, list[str], str]:
    text = f"{title} {content}".lower()
    high_hits = [keyword for keyword in HIGH_RISK_KEYWORDS if keyword.lower() in text]
    if high_hits:
        return "negative", "high", 0.90, high_hits, "hard_material_risk"
    negative_hits = [keyword for keyword in NEGATIVE_KEYWORDS if keyword.lower() in text]
    positive_hits = [keyword for keyword in POSITIVE_KEYWORDS if keyword.lower() in text]
    if negative_hits and not positive_hits:
        severity = "medium" if any(keyword in text for keyword in ("减持", "问询函", "监管函", "预亏")) else "low"
        return "negative", severity, 0.72, negative_hits, "negative_news"
    if positive_hits and not negative_hits:
        severity = "medium" if any(keyword in text for keyword in ("重大", "中标", "重组", "扭亏", "预增")) else "low"
        return "positive", severity, 0.68, positive_hits, "positive_news"
    if positive_hits and negative_hits:
        return "mixed", "medium", 0.58, positive_hits + negative_hits, "mixed_news"
    return "neutral", "low", 0.45, [], "no_keyword_hit"


def summarize_news_events(
    code: str,
    events: list[NewsEvent],
    snapshot: StockSnapshot | None,
    mode: str,
    llm: DeepSeekClient | None = None,
) -> dict[str, Any]:
    if not events:
        return {
            "source": "news_announcement",
            "mode": mode,
            "polarity": "neutral",
            "severity": "low",
            "score": 0.55,
            "confidence": 0.40,
            "material_risk": False,
            "headline": "",
            "summary": "近窗口未抓到重大新闻或公告",
            "events": [],
        }

    impact = 0.0
    confidence = 0.45
    severity_order = {"low": 0, "medium": 1, "high": 2}
    highest_severity = "low"
    material_risk = False
    for event in events:
        confidence = max(confidence, event.confidence)
        if severity_order[event.severity] > severity_order[highest_severity]:
            highest_severity = event.severity
        weight = {"low": 0.05, "medium": 0.12, "high": 0.35}[event.severity]
        if event.polarity == "positive":
            impact += weight
        elif event.polarity == "negative":
            impact -= weight
        elif event.polarity == "mixed":
            impact -= weight * 0.25
        if event.polarity == "negative" and event.severity == "high":
            material_risk = True

    score = clamp(0.55 + impact)
    headline_event = next((event for event in events if event.polarity != "neutral"), events[0])
    polarity = _aggregate_polarity(impact, material_risk)
    reaction = _market_reaction(snapshot)
    summary = _news_summary(polarity, highest_severity, headline_event, reaction)
    llm_result = _llm_news_summary(code, events, snapshot, mode, llm)
    if llm_result:
        polarity = str(llm_result.get("polarity") or polarity)
        highest_severity = str(llm_result.get("severity") or highest_severity)
        material_risk = bool(llm_result.get("material_risk", material_risk))
        score = clamp(float(llm_result.get("score", score)))
        confidence = max(confidence, clamp(float(llm_result.get("confidence", 0.0))))
        summary = str(llm_result.get("summary") or summary)
    return {
        "source": "news_announcement",
        "mode": mode,
        "polarity": polarity,
        "severity": highest_severity,
        "score": score,
        "confidence": confidence,
        "material_risk": material_risk,
        "headline": headline_event.title,
        "summary": summary,
        "llm": llm_result or {"provider": "rules", "status": "fallback"},
        "market_reaction": reaction,
        "events": [asdict(event) for event in events[:20]],
    }


def write_context_evidence(path: Path | str, evidence: dict[str, dict[str, Any]], merge: bool = False) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if merge and output.exists():
        existing = json.loads(output.read_text(encoding="utf-8"))
        for code, item in evidence.items():
            existing.setdefault(code, {})
            _deep_update(existing[code], item)
        evidence = existing
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _records_from_frame(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if isinstance(frame, list):
        return [dict(item) for item in frame if isinstance(item, dict)]
    if isinstance(frame, dict):
        return [frame]
    if hasattr(frame, "to_dict"):
        try:
            return [dict(item) for item in frame.to_dict("records")]
        except TypeError:
            return [dict(frame.to_dict())]
    return []


def _normalise_akshare_record(code: str, record: dict[str, Any], source: str) -> dict[str, Any]:
    title = str(_first_value(record, ("新闻标题", "标题", "title", "Title", "资讯标题", "内容", "摘要")) or "")
    content = str(_first_value(record, ("新闻内容", "内容", "摘要", "简介", "content", "summary")) or "")
    publish_time = _first_value(record, ("发布时间", "时间", "日期", "datetime", "time", "date"))
    source_name = str(_first_value(record, ("文章来源", "来源", "source", "媒体")) or source)
    url = _first_value(record, ("新闻链接", "链接", "url", "URL", "Link"))
    if not title and content:
        title = content[:80]
    return {
        "code": code,
        "title": _strip_html(title),
        "content": _strip_html(content),
        "source": source,
        "source_name": source_name,
        "publish_time": str(publish_time) if publish_time is not None else None,
        "url": str(url) if url else None,
    }


def _filter_news_window(events: list[dict[str, Any]], start: date, end: date, limit: int) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        title = str(event.get("title") or "")
        if not title or title in seen:
            continue
        event_date = _parse_event_date(event.get("publish_time"))
        if event_date and (event_date < start or event_date > end):
            continue
        seen.add(title)
        filtered.append(event)
        if len(filtered) >= limit:
            break
    return filtered


def _match_global_events(
    snapshot: StockSnapshot,
    events: list[dict[str, Any]],
    theme: object,
    limit: int,
) -> list[dict[str, Any]]:
    keywords = {snapshot.code, snapshot.name}
    if isinstance(theme, dict):
        keywords.update(str(item) for item in theme.get("themes") or [] if item)
    matched: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        text = f"{event.get('title', '')} {event.get('content', '')}"
        if not any(keyword and keyword in text for keyword in keywords):
            continue
        title = str(event.get("title") or "")
        if not title or title in seen:
            continue
        item = dict(event)
        item["matched_by"] = "stock_or_theme_keyword"
        matched.append(item)
        seen.add(title)
        if len(matched) >= limit:
            break
    return matched


def _mentions_stock(event: dict[str, Any], code: str, name: str) -> bool:
    text = f"{event.get('title', '')} {event.get('content', '')}"
    return code in text or bool(name and name in text) or event.get("source") == "akshare:stock_news_em"


def _event_from_raw(code: str, item: dict[str, Any]) -> NewsEvent:
    title = str(item.get("title") or item.get("announcementTitle") or "")
    polarity, severity, confidence, keywords, reason = classify_news_text(title, str(item.get("content") or ""))
    return NewsEvent(
        code=code,
        title=title,
        source=str(item.get("source") or "unknown"),
        publish_time=item.get("publish_time") or item.get("publishTime"),
        url=item.get("url") or item.get("announcement_url"),
        polarity=polarity,
        severity=severity,
        confidence=confidence,
        keywords=keywords,
        reason=reason,
    )


def _normalise_cninfo_announcement(code: str, row: dict[str, Any]) -> dict[str, Any]:
    title = _strip_html(str(row.get("announcementTitle") or ""))
    adjunct_url = str(row.get("adjunctUrl") or "")
    timestamp = row.get("announcementTime")
    publish_time = None
    if timestamp:
        try:
            publish_time = datetime.fromtimestamp(float(timestamp) / 1000).isoformat(timespec="seconds")
        except (TypeError, ValueError, OSError):
            publish_time = str(timestamp)
    url = f"https://static.cninfo.com.cn/{adjunct_url}" if adjunct_url else None
    return {
        "code": code,
        "title": title,
        "source": "cninfo_announcement",
        "publish_time": publish_time,
        "url": url,
    }


def _parse_news_html(code: str, raw: str, start: date, end: date, limit: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in re.finditer(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", raw, re.I | re.S):
        url, title_html = match.groups()
        title = _strip_html(title_html)
        if not title or title in seen or code not in raw[max(0, match.start() - 300) : match.end() + 300]:
            continue
        date_match = re.search(r"(20\d{2}-\d{2}-\d{2})", raw[match.end() : match.end() + 300])
        publish_time = date_match.group(1) if date_match else None
        if publish_time:
            try:
                day = date.fromisoformat(publish_time)
                if day < start or day > end:
                    continue
            except ValueError:
                pass
        seen.add(title)
        events.append({"code": code, "title": title, "source": "eastmoney_news_search", "publish_time": publish_time, "url": url})
        if len(events) >= limit:
            break
    return events


def _news_summary(polarity: str, severity: str, event: NewsEvent, reaction: dict[str, Any]) -> str:
    prefix = {
        "negative": "负面信息需降权",
        "positive": "正面信息可加分",
        "mixed": "多空信息混合",
        "neutral": "未发现明确方向性信息",
    }.get(polarity, "信息中性")
    reaction_text = reaction.get("summary")
    if reaction_text:
        return f"{prefix}，强度{severity}；{event.title}；{reaction_text}"
    return f"{prefix}，强度{severity}；{event.title}"


def _llm_news_summary(
    code: str,
    events: list[NewsEvent],
    snapshot: StockSnapshot | None,
    mode: str,
    llm: DeepSeekClient | None,
) -> dict[str, Any] | None:
    if not llm or not llm.available or not events:
        return None
    compact_events = [
        {
            "title": event.title[:160],
            "source": event.source,
            "publish_time": event.publish_time,
            "rule_polarity": event.polarity,
            "rule_severity": event.severity,
            "keywords": event.keywords[:8],
        }
        for event in events[:8]
    ]
    user_payload = {
        "code": code,
        "name": snapshot.name if snapshot else "",
        "mode": mode,
        "events": compact_events,
    }
    system = (
        "你是A股信息面风控副手。只基于给定新闻/公告标题做分类，不编造事实。"
        "输出严格JSON，字段为 polarity(positive/negative/mixed/neutral), severity(low/medium/high), "
        "material_risk(boolean), score(0到1), confidence(0到1), summary(不超过80个中文字符), catalysts(array)。"
        "立案调查、行政处罚、退市、重大减持、债务逾期等必须标 material_risk=true。"
    )
    try:
        result = llm.chat_json(system, json.dumps(user_payload, ensure_ascii=False), max_tokens=600)
    except (LLMError, ValueError, TypeError):
        return None
    polarity = str(result.get("polarity") or "neutral").lower()
    severity = str(result.get("severity") or "low").lower()
    if polarity not in {"positive", "negative", "mixed", "neutral"}:
        polarity = "neutral"
    if severity not in {"low", "medium", "high"}:
        severity = "low"
    catalysts = result.get("catalysts")
    if not isinstance(catalysts, list):
        catalysts = []
    return {
        "provider": "deepseek",
        "status": "ok",
        "polarity": polarity,
        "severity": severity,
        "material_risk": bool(result.get("material_risk")),
        "score": clamp(_safe_float(result.get("score"), 0.55)),
        "confidence": clamp(_safe_float(result.get("confidence"), 0.55)),
        "summary": str(result.get("summary") or "")[:120],
        "catalysts": [str(item)[:40] for item in catalysts[:5]],
    }


def _aggregate_polarity(impact: float, material_risk: bool) -> str:
    if material_risk or impact <= -0.10:
        return "negative"
    if impact >= 0.08:
        return "positive"
    if abs(impact) >= 0.04:
        return "mixed"
    return "neutral"


def _safe_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _market_reaction(snapshot: StockSnapshot | None) -> dict[str, Any]:
    if not snapshot or snapshot.prev_close <= 0:
        return {}
    daily_return = snapshot.close / snapshot.prev_close - 1
    summary = f"当日涨跌{daily_return:.2%}，20日均换手{snapshot.turnover_rate_20d:.2%}"
    if abs(daily_return) >= 0.07 or snapshot.turnover_rate_20d >= 0.08:
        summary += "，盘面反应偏强或波动偏高"
    return {
        "daily_return": daily_return,
        "turnover_rate_20d": snapshot.turnover_rate_20d,
        "summary": summary,
    }


def _cninfo_column(code: str) -> str:
    return "sse" if code.startswith(("600", "601", "603", "605", "688", "689")) else "szse"


def _first_value(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in record:
            return record[key]
    lower = {str(key).lower(): value for key, value in record.items()}
    for key in keys:
        if key.lower() in lower:
            return lower[key.lower()]
    return None


def _parse_event_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    text = str(value)
    match = re.search(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})", text)
    if not match:
        return None
    year, month, day = match.groups()
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _deep_update(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
