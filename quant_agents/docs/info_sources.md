# 信息源接入说明

本系统将免费信息源统一整理为 `context_evidence`，再交给 Agent 使用，避免策略直接依赖单个网页接口。

## 已接入

- K 线：`--kline-source auto|mootdx|eastmoney`
  - `auto` 会优先尝试 `mootdx`，失败后回退到现有公开 HTTP K 线。
  - `mootdx` 已安装在项目 `.venv`，配置和缓存放在 `data/cache/source/mootdx/`，通达信服务器失败时会自动轮询多条线路。
- 腾讯财经行情：写入 `quote`，用于核对最新价、涨跌幅、成交量、成交额、PE、换手率、市值、PB、量比等盘口/估值指标。
- 同花顺热榜：写入 `theme`，用于情绪面/题材热度加分，并保留热度排名、题材标签、归因文本。
- 新闻：使用 AKShare 三路免费源，`stock_news_em` 个股新闻、`stock_info_global_cls` 财联社快讯、`stock_info_global_em` 东财全球资讯；写入 `news` 并由标题/正文关键词分类。
- 公告：使用巨潮公告，后续可补 mootdx F10/finance 作为公告和基础资料的 TCP 兜底。

## 常用命令

```bash
.venv/bin/python -m quant_agents.cli collect-info-evidence \
  --snapshots data/features/free_snapshots_20260524.json \
  --mode post-close \
  --out data/evidence/context_evidence_20260522.json
```

```bash
.venv/bin/python -m quant_agents.cli plan-from-snapshots \
  --input data/features/free_snapshots_20260524.json \
  --evidence data/evidence/institutional_evidence_20260522.json \
  --context-evidence data/evidence/context_evidence_20260522.json \
  --force-rebalance \
  --out runs/snapshot_plan_with_context_20260522.json
```

## 调度建议

- 开盘前：跑 `collect-info-evidence --mode pre-open`，重点看隔夜公告、财联社快讯、东财全球资讯和个股新闻，负面重大事件直接降权或拦截买入。
- 盘中：跑 `collect-info-evidence --mode intraday --no-news`，重点用腾讯行情和同花顺热点捕捉强势题材，但不追高自动下单。
- 收盘后：跑 `collect-info-evidence --mode post-close`，把盘后公告、新闻、热点和当日盘面反应一起写入下一交易日计划。
