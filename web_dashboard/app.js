const DATA = window.QA_DATA || { meta: {}, summary: {}, preferred: [] };
const CLOSE_BRIEF = window.CLOSE_BRIEF_DATA || null;
const APP_VERSION = DATA.meta.assetVersion || DATA.meta.generatedAt || "local";
const state = {
  query: new URLSearchParams(window.location.search).get("q") || "",
  filter: new URLSearchParams(window.location.search).get("level") || "all",
  historyDate: new URLSearchParams(window.location.search).get("date") || "all",
  tradeDate: new URLSearchParams(window.location.search).get("date") || "",
  returnPeriod: new URLSearchParams(window.location.search).get("period") || "month",
  infoView: new URLSearchParams(window.location.search).get("view") || "events",
  sort: "rank",
};
const stockChartState = {};

const app = document.getElementById("app");
const page = document.body.dataset.page || "dashboard";

function normalizeAddressBar() {
  const params = new URLSearchParams(window.location.search);
  if (!params.has("v")) return;
  params.delete("v");
  const query = params.toString();
  const nextUrl = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
  window.history.replaceState(null, "", nextUrl);
}

function appUrl(path) {
  return path;
}

function versionInternalLinks(root = document) {
  root.querySelectorAll('a[href^="./"]').forEach((link) => link.setAttribute("href", appUrl(link.getAttribute("href"))));
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function pct(value) {
  if (value === null || value === undefined) return "-";
  return `${Number(value).toFixed(2)}%`;
}

function num(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function quoteClock(value) {
  if (!value) return "";
  const text = String(value);
  if (text.length >= 14) return `${text.slice(8, 10)}:${text.slice(10, 12)}`;
  return text;
}

function quoteDateTime(value) {
  if (!value) return "";
  const text = String(value);
  if (text.length >= 14) return `${text.slice(4, 6)}-${text.slice(6, 8)} ${text.slice(8, 10)}:${text.slice(10, 12)}`;
  return text;
}

function quoteIsoDate(value) {
  if (!value) return "";
  const digits = String(value).replace(/\D/g, "");
  if (digits.length >= 8) return `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6, 8)}`;
  return "";
}

function entryMoment(row) {
  if (row.entryQuoteTime) return quoteDateTime(row.entryQuoteTime);
  if (row.entryDate) return `${String(row.entryDate).slice(5)} 09:35`;
  return "-";
}

function entrySourceLabel(row) {
  return `入池 ${entryMoment(row)}`;
}

function latestPriceTime(row) {
  if (row.latestQuoteTime) return quoteDateTime(row.latestQuoteTime);
  if (row.latestDate) return String(row.latestDate);
  return "-";
}

function firstNumericValue(...values) {
  for (const value of values) {
    if (value !== null && value !== undefined && !Number.isNaN(Number(value))) return Number(value);
  }
  return undefined;
}

function preferredDisplayPrice(row) {
  return firstNumericValue(row.currentClose, row.currentPrice, row.latestPrice, row.basic?.latestPrice, row.basic?.close);
}

function preferredDisplayPriceLabel(row) {
  const realtime = firstNumericValue(row.currentClose, row.currentPrice, row.latestPrice, row.basic?.latestPrice);
  return realtime === undefined ? "快照价" : "最新价";
}

function moneyYi(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `${num(Number(value) / 100000000, 2)}亿`;
}

function moneyWan(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `${num(Number(value) / 10000, 2)}万`;
}

function moneyFull(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return num(Number(value), 2);
}

function signedMoneyWan(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const amount = Number(value);
  return `${amount > 0 ? "+" : ""}${moneyWan(amount)}`;
}

function signedMoneyFull(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const amount = Number(value);
  return `${amount > 0 ? "+" : ""}${moneyFull(amount)}`;
}

function weightPct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return pct(Number(value) * 100);
}

function score(value) {
  return Number(value || 0).toFixed(4);
}

function gainClass(value) {
  return Number(value || 0) >= 0 ? "gain-up" : "gain-down";
}

function levelText(level) {
  return {
    ok: "正常",
    info: "关注",
    warn: "预警",
    danger: "风险",
  }[level] || "关注";
}

function polarityText(value) {
  return {
    positive: "偏正面",
    negative: "偏负面",
    neutral: "中性",
    mixed: "多空混合",
  }[value] || "中性";
}

function severityText(value) {
  return {
    high: "高",
    medium: "中",
    low: "低",
  }[value] || "低";
}

function pageTitle(title, subtitle, actions = "") {
  return `
    <section class="page-head">
      <div>
        <p class="eyebrow">A's decision</p>
        <h1>${esc(title)}</h1>
        <p class="subtle">${esc(subtitle)}</p>
      </div>
      <div class="toolbar">${actions}</div>
    </section>
  `;
}

function metric(label, value, note, attrs = "") {
  return `
    <article class="metric-card fade-up" ${attrs}>
      <div class="metric-label">${esc(label)}</div>
      <div class="metric-value">${esc(value)}</div>
      <div class="metric-note">${esc(note)}</div>
    </article>
  `;
}

function scoreBar(value, label = "综合评分", variant = "blue") {
  const safe = Math.max(0, Math.min(1, Number(value || 0)));
  return `
    <div class="score-block score-${esc(variant)}">
      <div class="score-line"><span>${esc(label)}</span><span>${score(safe)}</span></div>
      <div class="bar-track"><div class="bar-fill" data-width="${safe * 100}"></div></div>
    </div>
  `;
}

function chip(text, level = "info") {
  return `<span class="pill ${esc(level)}">${esc(text)}</span>`;
}

function anomalyChips(stock, limit = 3) {
  return stock.anomalies
    .slice(0, limit)
    .map((item) => chip(item.title, item.level))
    .join("");
}

function stockTitle(stock) {
  return `
    <div class="stock-title">
      <strong>${esc(stock.name)}</strong>
      <span>${esc(stock.code)}</span>
    </div>
  `;
}

function matchStock(stock, rawQuery) {
  const query = rawQuery.trim().toLowerCase();
  if (!query) return true;
  const haystack = [
    stock.code,
    stock.name,
    stock.reason,
    stock.anomalies.map((item) => `${item.type} ${item.title} ${item.reason}`).join(" "),
    stock.agents.map((agent) => `${agent.label} ${agent.reason}`).join(" "),
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(query);
}

function sortedStocks(stocks) {
  const rows = [...stocks];
  if (state.sort === "score") rows.sort((a, b) => (b.adjustedScore ?? b.finalScore) - (a.adjustedScore ?? a.finalScore));
  if (state.sort === "gain") rows.sort((a, b) => (b.gainAfterEntry || 0) - (a.gainAfterEntry || 0));
  if (state.sort === "risk") {
    const weight = { danger: 4, warn: 3, info: 2, ok: 1 };
    rows.sort((a, b) => {
      const left = Math.max(...a.anomalies.map((item) => weight[item.level] || 0));
      const right = Math.max(...b.anomalies.map((item) => weight[item.level] || 0));
      return right - left;
    });
  }
  return rows;
}

function animateBars() {
  requestAnimationFrame(() => {
    document.querySelectorAll("[data-width]").forEach((bar) => {
      bar.style.width = `${bar.dataset.width}%`;
    });
  });
}

function renderDashboard() {
  const meta = DATA.meta;
  const poolQuality = meta.candidatePoolQuality || {};
  const poolTotal = poolQuality.eligibleCandidates || meta.eligibleCandidates || meta.poolSize || 0;
  const poolScored = poolQuality.deepScoredCandidates || meta.deepScoredCandidates || meta.poolSize || 0;
  const poolRemaining = poolQuality.remainingCandidates ?? meta.remainingCandidates;
  const poolNote = poolQuality.status === "complete"
    ? `深度评分 ${poolScored}/${poolTotal}，候选池已完成`
    : poolQuality.status === "partial"
      ? `深度评分 ${poolScored}/${poolTotal}，剩余 ${poolRemaining || 0} 支未评`
      : "候选池覆盖状态未知";
  const summary = DATA.summary;
  const issueTotals = summary.anomalyTotals || {};
  const anomalyStocks = summary.anomalyStocks || {};
  const evidence = DATA.evidenceSummary || {};
  const information = DATA.informationSummary || {};
  app.innerHTML = `
    ${pageTitle(
      "优选池监管总览",
      `优选池 ${meta.preferredSize} 支，股票池 ${meta.poolSize} 支，最新交易日 ${meta.latestSnapshotDate}。`
    )}

    <section class="grid kpi-grid">
      ${metric("优选池", `${meta.preferredSize || 0} 支`, "当前排序得分最高的标的")}
      ${metric("快照股票池", `${poolScored}/${poolTotal} 支`, poolNote)}
      ${metric(
        "公开证据覆盖",
        `${meta.evidenceCodes || 0} 支`,
        `龙虎榜 ${evidence.dragonTigerCodes || 0} / 融资 ${evidence.marginCodes || 0}`,
        'role="button" tabindex="0" data-evidence-drawer'
      )}
      ${metric("平均得分", score(summary.avgScore), `最高 ${score(summary.maxScore)} / 最低 ${score(summary.minScore)}`)}
    </section>

    ${automationStatusCard(DATA.automationStatus)}

    <section class="panel" style="margin-bottom:16px">
      <h2>信息面监管</h2>
      <div class="grid three-col compact-metrics">
        <a href="./information.html?view=events" class="metric-card mini-metric">
          <div class="metric-label">新闻覆盖</div>
          <div class="metric-value">${information.totals?.codes || 0} 支</div>
          <div class="metric-note">${information.totals?.events || 0} 条事件，点开查看明细</div>
        </a>
        <a href="./information.html?view=material" class="metric-card mini-metric">
          <div class="metric-label">重大风险</div>
          <div class="metric-value">${information.totals?.materialRisks || 0} 支</div>
          <div class="metric-note">高风险公告或新闻</div>
        </a>
        <a href="./information.html?view=risk" class="metric-card mini-metric">
          <div class="metric-label">负面/混合</div>
          <div class="metric-value">${(information.totals?.negative || 0) + (information.totals?.mixed || 0)} 支</div>
          <div class="metric-note">正面 ${information.totals?.positive || 0} 支</div>
        </a>
      </div>
    </section>

    <section class="grid two-col">
      <article class="panel">
        <h2>前八排序</h2>
        <div id="dashboardLeaders" class="leader-list">
          ${DATA.preferred
            .map(
              (stock) => `
                <a class="leader-row" href="./stock.html?code=${esc(stock.code)}" data-stock-row data-search="${esc(stock.code)} ${esc(stock.name)}">
                  <div>
                    ${stockTitle(stock)}
                    <div class="stock-meta">
                      ${chip(`入池价 ${num(stock.entryClose)}`, "info")}
                      ${chip(entrySourceLabel(stock), "info")}
                      ${chip(`入池至今 ${pct(stock.gainAfterEntry)}`, stock.gainAfterEntry >= 0 ? "danger" : "ok")}
                    </div>
                  </div>
                  ${scoreBar(stock.finalScore, "综合评分", "rank")}
                  <div class="score-number">${score(stock.finalScore)}</div>
                </a>
              `
            )
            .join("")}
        </div>
      </article>

      <article class="panel">
        <h2>异常概览</h2>
        <div class="grid three-col compact-metrics">
          <a href="./anomalies.html?level=warn" class="metric-card mini-metric">
            <div class="metric-label">预警股票</div>
            <div class="metric-value">${anomalyStocks.warn || 0}</div>
            <div class="metric-note">${issueTotals.warn || 0} 条预警项，点开看分点</div>
          </a>
          <a href="./anomalies.html?level=danger" class="metric-card mini-metric">
            <div class="metric-label">风险股票</div>
            <div class="metric-value">${anomalyStocks.danger || 0}</div>
            <div class="metric-note">${issueTotals.danger || 0} 条高风险项</div>
          </a>
          <a href="./anomalies.html?level=info" class="metric-card mini-metric">
            <div class="metric-label">关注股票</div>
            <div class="metric-value">${anomalyStocks.info || 0}</div>
            <div class="metric-note">${issueTotals.info || 0} 条资金/证据线索</div>
          </a>
        </div>
        <div class="notice-band" style="margin-top:16px">
          <p class="subtle">${esc(meta.dataNote)}</p>
        </div>
      </article>
    </section>

    <section class="panel" style="margin-top:16px">
      <h2>近60日涨幅参考</h2>
      <p class="subtle chart-note">这是最新快照向前60个交易日的涨幅，用来判断热度；它不是入池后的收益。</p>
      <div class="chart-bars">
        ${DATA.preferred
          .map((stock) => {
            const value = Math.min(100, Math.max(4, stock.basic.return60d / 2));
            return `
              <div class="chart-row">
                <span>${esc(stock.name)}</span>
                <div class="bar-track return-track"><div class="bar-fill" data-width="${value}"></div></div>
                <span>${pct(stock.basic.return60d)}</span>
              </div>
            `;
          })
          .join("")}
      </div>
    </section>
  `;
  animateBars();
}

function automationStatusCard(status) {
  const items = status?.items || [];
  if (!items.length) return "";
  const summaryClass = status.summaryStatus || "missing";
  return `
    <section class="automation-card automation-${esc(summaryClass)}" data-hf>
      <div class="automation-card-head">
        <div>
          <span class="eyebrow">自动化运行</span>
          <strong>${esc((status.date || DATA.meta.latestDataDate || "-").slice(5) || "-")}</strong>
        </div>
        <span class="automation-glow-dot"></span>
      </div>
      <div class="automation-items">
        ${items.map(automationStatusRow).join("")}
      </div>
    </section>
  `;
}

function automationStatusRow(item) {
  const status = item.status || "missing";
  return `
    <div class="automation-row automation-row-${esc(status)}">
      <span class="automation-dot"></span>
      <strong>${esc(item.label || "-")}</strong>
      <span>${esc(item.statusText || status)}</span>
      <em>${esc(automationStatusNote(item))}</em>
    </div>
  `;
}

function automationStatusNote(item) {
  if (item.finishedAt) return quoteDateTime(item.finishedAt) || item.finishedAt;
  return item.note || item.reportPath || "-";
}

function marketIndexCard(item) {
  const up = Number(item.pctChange || 0) >= 0;
  return `
    <article class="index-card">
      <div>
        <strong>${esc(item.name)}</strong>
        <span>${esc(item.symbol)}</span>
      </div>
      <div class="index-price">${num(item.price)}</div>
      <div class="${up ? "gain-up" : "gain-down"}">${pct(item.pctChange)} / ${num(item.change)}</div>
      <div class="subtle">成交 ${moneyYi(item.amountYuan)} / ${esc(item.quoteTime || "-")}</div>
    </article>
  `;
}

function infoWatchRow(row) {
  const level = row.riskLevel || "info";
  return `
    <article class="info-watch-row">
      <div>
        ${stockTitle(row)}
        <div class="stock-meta">
          ${chip(`排名 ${row.rank || "-"}`, "info")}
          ${chip(polarityText(row.polarity), level)}
          ${chip(`严重度${severityText(row.severity)}`, level)}
        </div>
      </div>
      <p>${esc(row.summary || row.headline || "近窗口未抓到重大新闻或公告")}</p>
    </article>
  `;
}

function renderPool() {
  const rows = sortedStocks(DATA.preferred).filter((stock) => matchStock(stock, state.query));
  const summary = DATA.summary || {};
  app.innerHTML = `
    ${pageTitle(
      "优选池",
      "前八支股票的基础行情、入池后涨幅、异常标签和复核入口。"
    )}

    <section class="grid kpi-grid pool-summary-grid">
      <article class="metric-card fade-up pool-gain-card ${gainClass(summary.poolGain)}">
        <div class="metric-label">整池涨幅</div>
        <div class="metric-value">${pct(summary.poolGain)}</div>
        <div class="metric-note">等权入池收益，上涨 ${summary.poolGainUp || 0} 支 / 下跌 ${summary.poolGainDown || 0} 支</div>
      </article>
      ${metric("当前优选", `${DATA.preferred.length} 支`, "前八支模拟观察标的")}
      ${metric("平均得分", score(summary.avgScore), `最高 ${score(summary.maxScore)} / 最低 ${score(summary.minScore)}`)}
      ${metric("异常股票", `${(summary.anomalyStocks?.warn || 0) + (summary.anomalyStocks?.danger || 0)} 支`, "预警与风险合计")}
    </section>

    <section class="panel">
      <div class="pool-tools">
        <label class="input-shell">
          <input id="poolSearch" type="search" value="${esc(state.query)}" placeholder="筛选代码、名称、原因">
        </label>
      </div>
      <div class="pool-list">
        ${
          rows.length
            ? rows.map(poolRow).join("")
            : `<div class="empty-state">没有匹配的优选池标的</div>`
        }
      </div>
    </section>
  `;
  wirePoolControls();
  animateBars();
}

function poolRow(stock) {
  return `
    <article class="pool-row" data-stock-row data-search="${esc(stock.code)} ${esc(stock.name)}">
      <div>
        ${stockTitle(stock)}
        <div class="stock-meta">
          ${chip(`${preferredDisplayPriceLabel(stock)} ${num(preferredDisplayPrice(stock))}`, "info")}
          ${chip(stock.riskCheck === "approved" ? "风控通过" : stock.riskCheck, stock.riskCheck === "approved" ? "ok" : "warn")}
        </div>
      </div>
      <div class="gain-cell">
        <div class="gain-value ${gainClass(stock.gainAfterEntry)}">${pct(stock.gainAfterEntry)}</div>
        <div class="gain-note">${esc(stock.gainStatus)}，入池价 ${num(stock.entryClose)}，${esc(entrySourceLabel(stock))}</div>
      </div>
      <div class="anomaly-chips">${anomalyChips(stock, 4)}</div>
      <div class="row-actions">
        <button class="btn" data-drawer="${esc(stock.code)}">异常原因</button>
        <a class="btn primary" href="./stock.html?code=${esc(stock.code)}">详情</a>
      </div>
    </article>
  `;
}

function wirePoolControls() {
  const input = document.getElementById("poolSearch");
  if (input) {
    input.addEventListener("input", (event) => {
      state.query = event.target.value;
      syncSearchValue();
      renderPool();
    });
  }
  document.querySelectorAll("[data-sort]").forEach((button) => {
    button.addEventListener("click", () => {
      state.sort = button.dataset.sort;
      renderPool();
    });
  });
}

function localStockDetailCodes() {
  if (localStockDetailCodes.cacheVersion === APP_VERSION && localStockDetailCodes.cache) {
    return localStockDetailCodes.cache;
  }
  const codes = new Set();
  (DATA.preferred || []).forEach((row) => row.code && codes.add(String(row.code)));
  (DATA.history || []).forEach((row) => row.code && codes.add(String(row.code)));
  ((DATA.tradeLedger || {}).records || []).forEach((row) => row.code && codes.add(String(row.code)));
  localStockDetailCodes.cacheVersion = APP_VERSION;
  localStockDetailCodes.cache = codes;
  return codes;
}

function xueqiuStockUrl(code) {
  const normalized = String(code || "").replace(/\D/g, "").padStart(6, "0").slice(-6);
  if (/^6/.test(normalized)) return `https://xueqiu.com/S/SH${normalized}`;
  if (/^[0123]/.test(normalized)) return `https://xueqiu.com/S/SZ${normalized}`;
  return `https://xueqiu.com/S/BJ${normalized}`;
}

function stockExternalUrl(code) {
  return xueqiuStockUrl(code);
}

function recordsForStock(code) {
  return ((DATA.tradeLedger || {}).records || [])
    .filter((row) => row.code === code)
    .sort((a, b) => String(b.signalTime || b.entryDate || "").localeCompare(String(a.signalTime || a.entryDate || "")));
}

function historicalStockForCode(code) {
  return (DATA.history || []).find((row) => row.code === code) || {};
}

function ledgerMoment(value, fallback = "-") {
  if (!value) return fallback;
  const text = String(value);
  if (text.includes("T")) {
    const [day, time = ""] = text.split("T");
    return `${day.slice(5)} ${time.slice(0, 5)}`;
  }
  if (text.length >= 10) return text.slice(5, 16);
  return text;
}

function gainFrom(entry, end) {
  const entryPrice = Number(entry || 0);
  const endPrice = Number(end || 0);
  if (!entryPrice || !endPrice) return null;
  return ((endPrice / entryPrice) - 1) * 100;
}

function cycleGain(row) {
  const entry = Number(row.intendedPrice || row.fillPrice || 0);
  const end = Number(row.currentPrice || row.exitPrice || 0);
  if (!entry || !end) return row.benchmarkPnlPct;
  return ((end / entry) - 1) * 100;
}

function cycleExitGain(row) {
  return gainFrom(row.intendedPrice || row.fillPrice, row.exitPrice);
}

function exitReasonText(reason) {
  return {
    not_selected_or_rebalanced: "调仓/未入选",
    marked_exit: "已标记退出",
  }[reason] || reason || "调仓/未入选";
}

function stockCycleSection(code) {
  const records = recordsForStock(code);
  if (!records.length) {
    return `
      <section class="panel stock-cycles">
        <h2>入池/出池记录</h2>
        <div class="empty-state">暂无可追溯的入池出池账本记录</div>
      </section>
    `;
  }
  const active = records.filter((row) => row.active).length;
  const closed = records.length - active;
  return `
    <section class="panel stock-cycles">
      <div class="section-title-row">
        <h2>入池/出池记录</h2>
        <span>${records.length} 次入池 / ${active} 次在池 / ${closed} 次退出</span>
      </div>
      <div class="cycle-list">
        ${records.map(stockCycleRow).join("")}
      </div>
    </section>
  `;
}

function stockCycleRow(row) {
  const gain = cycleGain(row);
  const exitGain = cycleExitGain(row);
  const exitTime = row.active ? "当前仍在池" : ledgerMoment(row.exitTime || row.exitDate);
  return `
    <article class="cycle-row ${row.active ? "is-active" : ""}">
      <div class="cycle-status">
        <span class="pill ${row.active ? "ok" : "info"}">${row.active ? "当前在池" : "已退出"}</span>
        <strong class="${gainClass(gain)}">${pct(gain)}</strong>
      </div>
      <div class="cycle-line">
        <div>
          <span>入池时间</span>
          <strong>${esc(ledgerMoment(row.signalTime, row.entryDate || "-"))}</strong>
        </div>
        <div>
          <span>入池股价</span>
          <strong>${num(row.intendedPrice)}</strong>
        </div>
        <div>
          <span>出池时间</span>
          <strong>${esc(exitTime)}</strong>
        </div>
        <div>
          <span>出池股价</span>
          <strong>${row.active ? "-" : num(row.exitPrice)}</strong>
        </div>
        <div>
          <span>当前股价</span>
          <strong>${num(row.currentPrice)}</strong>
        </div>
      </div>
      <p class="subtle">模式 ${esc(modeText(row.entryMode))}；入池至今 ${pct(gain)}；${row.active ? `最新报价 ${esc(ledgerMoment(row.currentPriceTime))}` : `出池时涨幅 ${pct(exitGain)}，退出原因 ${esc(exitReasonText(row.exitReason))}，最新报价 ${esc(ledgerMoment(row.currentPriceTime))}`}</p>
    </article>
  `;
}

function formatAgentReason(reason) {
  if (!reason) return "暂无补充说明";
  if (reason === "no_material_event") return "未发现重大公开事件。";

  const labels = {
    trend_quality: "趋势质量",
    avg_turnover: "成交额",
    turnover_rate: "换手率",
    roe_rank: "ROE分位",
    cashflow_rank: "现金流分位",
    pe_rank: "PE分位",
    market_heat: "市场热度",
    relative_strength: "相对强度",
    factor_trend: "趋势因子",
    r20: "20日涨幅",
    r60: "60日涨幅",
    dragon_tiger_institution_net_buy: "龙虎榜机构席位净额",
    institution_seats: "机构席位数",
  };

  return reason
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const [rawKey, rawValue = ""] = part.split("=");
      const key = rawKey.trim();
      const value = rawValue.trim();
      if (key === "avg_turnover") return `${labels[key]} ${num(Number(value) / 100000000, 2)}亿`;
      if (key === "dragon_tiger_institution_net_buy") return `${labels[key]} ${num(Number(value) / 10000, 2)}万`;
      return `${labels[key] || key} ${value}`;
    })
    .join(" / ");
}

function renderStockCharts(code, stock = {}) {
  const chart = stockChartForCode(code);
  const daily = chart.daily;
  const intradayMap = chart.intraday;
  const dates = Object.keys(intradayMap).sort();
  const defaultDate = dates.at(-1) || daily.at(-1)?.date || "";
  const selectedDate = stockChartState[code]?.date || defaultDate;
  const mode = stockChartState[code]?.mode || "daily";
  const initialQuote = quoteForStockChart(code, stock, selectedDate);
  if (!daily.length && !dates.length) {
    return `
      <section class="panel stock-chart-card" data-stock-chart="${esc(code)}">
        ${renderStockQuoteStrip(initialQuote)}
        <div class="empty-state">数据缺失：当前没有可用日K或系统分时快照。</div>
      </section>
    `;
  }
  requestAnimationFrame(() => initStockChart(code));
  return `
    <section class="panel stock-chart-card" data-stock-chart="${esc(code)}">
      <div class="stock-chart-topline">
        <div>
          <p class="eyebrow">Stock Lens</p>
          <h2>日K / 系统分时</h2>
        </div>
        <div class="segmented stock-chart-tabs" aria-label="图表类型">
          <button data-chart-mode="daily" class="${mode === "daily" ? "is-selected" : ""}">日K</button>
          <button data-chart-mode="intraday" class="${mode === "intraday" ? "is-selected" : ""}">分时</button>
        </div>
      </div>
      <div data-stock-quote-strip>${renderStockQuoteStrip(initialQuote)}</div>
      <div class="stock-chart-canvas-wrap">
        <svg class="stock-chart-svg" data-daily-chart role="img" aria-label="日K图"></svg>
        <svg class="stock-chart-svg" data-intraday-chart role="img" aria-label="分时图"></svg>
        <div class="stock-chart-tooltip" data-stock-chart-tooltip hidden></div>
      </div>
      <div class="stock-chart-foot">
        <span data-chart-status>${esc(stockChartStatus(chart, selectedDate))}</span>
        <span>日K点击任一交易日自动切换当日分时；分时仅支持悬停查看。</span>
      </div>
    </section>
  `;
}

function stockChartForCode(code) {
  const charts = DATA.stockCharts || {};
  return {
    daily: charts.dailyByCode?.[code] || [],
    intraday: charts.intradayByCode?.[code] || {},
    markers: charts.markersByCode?.[code] || [],
    latestQuote: charts.latestQuoteByCode?.[code] || {},
  };
}

function stockChartStatus(chart, selectedDate) {
  if (selectedDate && chart.intraday[selectedDate]?.length) return `分时快照 ${selectedDate} ${chart.intraday[selectedDate].length} 点`;
  if (selectedDate) return `分时快照 ${selectedDate} 数据缺失`;
  return "分时快照数据缺失";
}

function renderStockQuoteStrip(quote) {
  const tone = Number(quote.pctChange || 0) >= 0 ? "gain-up" : "gain-down";
  const turnoverTone = ratioTone(quote.turnoverRate);
  const volumeTone = ratioTone(quote.volumeRatio);
  return `
    <div class="stock-quote-strip is-compact ${tone}">
      <div class="quote-strip-left">
        <div class="quote-strip-price ${tone}">${num(quote.price)}</div>
        <div class="quote-strip-change ${tone}">
          <span>${signedNumber(quote.change)}</span>
          <span>${pct(quote.pctChange)}</span>
        </div>
      </div>
      <div class="quote-strip-grid">
        ${quoteMetric("当日高点", num(quote.high), "gain-up")}
        ${quoteMetric("低点", num(quote.low), "gain-down")}
        ${quoteMetric("开盘", num(quote.open), quoteTone(quote.open, quote.price))}
        ${quoteMetric("市值", quoteCap(quote.totalMarketCap100m))}
        ${quoteMetric("流通", quoteCap(quote.floatMarketCap100m))}
        ${quoteMetric("市盈率", num(quote.peDynamic))}
        ${quoteMetric("量比", num(quote.volumeRatio), volumeTone)}
        ${quoteMetric("换手率", pct(quote.turnoverRate), turnoverTone)}
        ${quoteMetric("成交额", moneyYi(quote.amountYuan))}
      </div>
    </div>
  `;
}

function quoteMetric(label, value, className = "") {
  return `<div class="quote-strip-metric"><span>${esc(label)}</span><strong class="${esc(className)}">${esc(value)}</strong></div>`;
}

function quoteForStockChart(code, stock = {}, selectedDate = "") {
  const chart = stockChartForCode(code);
  const intradayRows = selectedDate ? chart.intraday[selectedDate] || [] : [];
  const source = intradayRows.at(-1)?.quote || chart.latestQuote || {};
  const daily = chart.daily.at(-1) || {};
  const basic = stock.basic || {};
  const contextQuote = stock.context?.quote || {};
  const price = firstNumericValue(source.price, stock.currentClose, stock.currentPrice, daily.close, basic.latestPrice);
  const pctChange = firstNumericValue(source.pctChange, stock.todayPctChange, daily.pctChange, basic.latestPctChange, contextQuote.pct_change);
  const change = firstNumericValue(source.change, stock.todayPriceChange, daily.change, contextQuote.change);
  return {
    price,
    high: firstNumericValue(source.high, contextQuote.high, daily.high),
    low: firstNumericValue(source.low, contextQuote.low, daily.low),
    open: firstNumericValue(source.open, contextQuote.open, daily.open, basic.open),
    turnoverRate: firstNumericValue(source.turnoverRate, contextQuote.turnover_rate_pct, basic.latestTurnoverRate, daily.turnover),
    volumeRatio: firstNumericValue(source.volumeRatio, contextQuote.volume_ratio, basic.latestVolumeRatio),
    totalMarketCap100m: firstNumericValue(source.totalMarketCap100m, contextQuote.total_market_cap_100m),
    floatMarketCap100m: firstNumericValue(source.floatMarketCap100m, contextQuote.float_market_cap_100m),
    peDynamic: firstNumericValue(source.peDynamic, contextQuote.pe_dynamic),
    amountYuan: firstNumericValue(source.amountYuan, contextQuote.amount_yuan, daily.amount),
    pctChange,
    change,
  };
}

function signedNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${num(number)}`;
}

function quoteCap(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `${num(value, 2)}亿`;
}

function ratioTone(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "";
  return Number(value) >= 1 ? "ratio-hot" : "ratio-cool";
}

function quoteTone(value, reference) {
  if (value === null || value === undefined || reference === null || reference === undefined) return "";
  return Number(value) >= Number(reference) ? "gain-up" : "gain-down";
}

function initStockChart(code) {
  const root = document.querySelector(`[data-stock-chart="${cssEscape(code)}"]`);
  if (!root) return;
  wireStockChartTabs(root, code);
  renderStockChartMode(root, code);
}

function wireStockChartTabs(root, code) {
  root.querySelectorAll("[data-chart-mode]").forEach((button) => {
    if (button.dataset.wired === "1") return;
    button.dataset.wired = "1";
    button.addEventListener("click", () => switchStockChartMode(code, button.dataset.chartMode));
  });
}

function switchStockChartMode(code, mode, date) {
  stockChartState[code] = {
    ...(stockChartState[code] || {}),
    mode,
    date: date || stockChartState[code]?.date || defaultIntradayDate(code),
  };
  const root = document.querySelector(`[data-stock-chart="${cssEscape(code)}"]`);
  if (!root) return;
  root.querySelectorAll("[data-chart-mode]").forEach((button) => {
    button.classList.toggle("is-selected", button.dataset.chartMode === mode);
  });
  renderStockChartMode(root, code);
}

function defaultIntradayDate(code) {
  const dates = Object.keys(stockChartForCode(code).intraday).sort();
  return dates.at(-1) || "";
}

function renderStockChartMode(root, code) {
  const mode = stockChartState[code]?.mode || "daily";
  const selectedDate = stockChartState[code]?.date || defaultIntradayDate(code);
  const dailySvg = root.querySelector("[data-daily-chart]");
  const intradaySvg = root.querySelector("[data-intraday-chart]");
  if (dailySvg) dailySvg.toggleAttribute("hidden", mode !== "daily");
  if (intradaySvg) intradaySvg.toggleAttribute("hidden", mode !== "intraday");
  if (mode === "daily") drawDailyKChart(root, code);
  if (mode === "intraday") drawIntradaySnapshotChart(root, code, selectedDate);
  const chart = stockChartForCode(code);
  const status = root.querySelector("[data-chart-status]");
  if (status) status.textContent = stockChartStatus(chart, selectedDate);
  const strip = root.querySelector("[data-stock-quote-strip]");
  const stock = DATA.preferred.find((row) => row.code === code) || historicalStockForCode(code);
  if (strip) strip.innerHTML = renderStockQuoteStrip(quoteForStockChart(code, stock, selectedDate));
}

function drawDailyKChart(root, code) {
  const svg = root.querySelector("[data-daily-chart]");
  const chart = stockChartForCode(code);
  const rows = chart.daily;
  if (!svg) return;
  if (!rows.length) {
    drawChartEmpty(svg, "数据缺失：没有日K缓存");
    return;
  }
  const size = chartSize(svg);
  const pad = { left: 46, right: 18, top: 18, bottom: 28 };
  const plot = chartPlot(size, pad);
  const values = rows.flatMap((row) => [row.high, row.low, row.ma?.ma5, row.ma?.ma10, row.ma?.ma20]).filter(isFiniteNumber);
  const scaleY = makeLinearScale(Math.min(...values), Math.max(...values), plot.bottom, plot.top);
  const step = plot.width / Math.max(rows.length, 1);
  const candleWidth = Math.max(3, Math.min(12, step * 0.56));
  const markers = chart.markers || [];
  let body = chartGrid(plot, scaleY, values);
  rows.forEach((day, index) => {
    const x = plot.left + step * index + step / 2;
    const up = Number(day.close) >= Number(day.open);
    const cls = up ? "chart-up" : "chart-down";
    const yHigh = scaleY(day.high);
    const yLow = scaleY(day.low);
    const yOpen = scaleY(day.open);
    const yClose = scaleY(day.close);
    const top = Math.min(yOpen, yClose);
    const height = Math.max(2, Math.abs(yClose - yOpen));
    body += `<line class="${cls}" x1="${x}" y1="${yHigh}" x2="${x}" y2="${yLow}"></line>`;
    body += `<rect class="${cls}" x="${x - candleWidth / 2}" y="${top}" width="${candleWidth}" height="${height}" rx="1.5"></rect>`;
    body += `<rect class="chart-hit-zone" data-daily-index="${index}" x="${x - step / 2}" y="${plot.top}" width="${step}" height="${plot.height}"></rect>`;
  });
  body += maLine(rows, "ma5", plot, step, scaleY, "ma-line ma5", "MA5");
  body += maLine(rows, "ma10", plot, step, scaleY, "ma-line ma10", "MA10");
  body += maLine(rows, "ma20", plot, step, scaleY, "ma-line ma20", "MA20");
  body += markerLayer(markers, rows, plot, step, scaleY);
  body += chartAxisLabels(rows, plot, scaleY, values);
  svg.setAttribute("viewBox", `0 0 ${size.width} ${size.height}`);
  svg.innerHTML = body;
  const tooltip = root.querySelector("[data-stock-chart-tooltip]");
  svg.querySelectorAll("[data-daily-index]").forEach((hit) => {
    hit.addEventListener("mousemove", (event) => {
      const day = rows[Number(hit.dataset.dailyIndex)];
      showChartTooltip(root, tooltip, event, dailyTooltipHtml(day), true);
    });
    hit.addEventListener("mouseleave", () => hideChartTooltip(tooltip));
    hit.addEventListener("click", () => {
      const day = rows[Number(hit.dataset.dailyIndex)];
      switchStockChartMode(code, "intraday", day.date);
    });
  });
}

function dailyTooltipHtml(day) {
  const tone = Number(day.pctChange || 0) >= 0 ? "gain-up" : "gain-down";
  return `
    <div class="chart-tooltip-price ${tone}">
      <span>${num(day.close)}</span>
      <span>当日涨幅 ${pct(day.pctChange)}</span>
    </div>
    <div class="chart-tooltip-date ${tone}">涨跌额 ${signedNumber(day.change)}</div>
    <div class="chart-tooltip-date">${esc(day.date)}</div>
  `;
}

function drawIntradaySnapshotChart(root, code, selectedDate) {
  const svg = root.querySelector("[data-intraday-chart]");
  const chart = stockChartForCode(code);
  const rows = chart.intraday[selectedDate] || [];
  if (!svg) return;
  if (!selectedDate || !rows.length) {
    drawChartEmpty(svg, selectedDate ? `${selectedDate} 数据缺失` : "数据缺失：没有系统分时快照");
    return;
  }
  const size = chartSize(svg);
  const pad = { left: 46, right: 18, top: 18, bottom: 28 };
  const plot = chartPlot(size, pad);
  const prices = rows.map((row) => row.price).filter(isFiniteNumber);
  const scaleY = makeLinearScale(Math.min(...prices), Math.max(...prices), plot.bottom, plot.top);
  const step = plot.width / Math.max(rows.length - 1, 1);
  const points = rows.map((row, index) => [plot.left + step * index, scaleY(row.price)]);
  const trendClass = Number(rows.at(-1)?.price || 0) >= Number(rows[0]?.price || 0) ? "chart-up" : "chart-down";
  let body = chartGrid(plot, scaleY, prices);
  body += `<polyline class="intraday-line ${trendClass}" points="${points.map(([x, y]) => `${x},${y}`).join(" ")}"></polyline>`;
  points.forEach(([x, y], index) => {
    body += `<circle class="intraday-dot ${trendClass}" data-intraday-index="${index}" cx="${x}" cy="${y}" r="4"></circle>`;
    body += `<rect class="chart-hit-zone" data-intraday-index="${index}" x="${x - Math.max(10, step / 2)}" y="${plot.top}" width="${Math.max(20, step)}" height="${plot.height}"></rect>`;
  });
  body += markerLayer(chart.markers.filter((marker) => marker.date === selectedDate), rows, plot, step, scaleY, "time");
  body += chartAxisLabels(rows, plot, scaleY, prices, "time");
  svg.setAttribute("viewBox", `0 0 ${size.width} ${size.height}`);
  svg.innerHTML = body;
  const tooltip = root.querySelector("[data-stock-chart-tooltip]");
  svg.querySelectorAll("[data-intraday-index]").forEach((hit) => {
    hit.addEventListener("mousemove", (event) => {
      const point = rows[Number(hit.dataset.intradayIndex)];
      showChartTooltip(root, tooltip, event, `${point.time} ${num(point.price)}`);
    });
    hit.addEventListener("mouseleave", () => hideChartTooltip(tooltip));
  });
}

function chartSize(svg) {
  const width = Math.max(320, Math.round(svg.getBoundingClientRect().width || svg.parentElement?.clientWidth || 880));
  return { width, height: 360 };
}

function chartPlot(size, pad) {
  return {
    left: pad.left,
    right: size.width - pad.right,
    top: pad.top,
    bottom: size.height - pad.bottom,
    width: size.width - pad.left - pad.right,
    height: size.height - pad.top - pad.bottom,
  };
}

function makeLinearScale(min, max, outMin, outMax) {
  const spread = Math.max(0.01, max - min);
  const low = min - spread * 0.08;
  const high = max + spread * 0.08;
  return (value) => outMin + ((Number(value) - low) / (high - low)) * (outMax - outMin);
}

function chartGrid(plot, scaleY, values) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const rows = [0, 0.25, 0.5, 0.75, 1];
  return rows
    .map((ratio) => {
      const y = plot.bottom - plot.height * ratio;
      const value = min + (max - min) * ratio;
      return `<line class="chart-grid-line" x1="${plot.left}" y1="${y}" x2="${plot.right}" y2="${y}"></line><text class="chart-axis-label" x="4" y="${y + 4}">${num(value)}</text>`;
    })
    .join("");
}

function chartAxisLabels(rows, plot, scaleY, values, key = "date") {
  if (!rows.length) return "";
  const first = rows[0];
  const last = rows.at(-1);
  const max = Math.max(...values);
  const min = Math.min(...values);
  return `
    <text class="chart-axis-label" x="${plot.left}" y="${plot.bottom + 20}">${esc(first[key] || first.date)}</text>
    <text class="chart-axis-label" x="${plot.right - 62}" y="${plot.bottom + 20}">${esc(last[key] || last.date)}</text>
    <text class="chart-axis-label chart-axis-strong" x="${plot.right - 72}" y="${scaleY(max) - 6}">高 ${num(max)}</text>
    <text class="chart-axis-label chart-axis-strong" x="${plot.right - 72}" y="${scaleY(min) + 16}">低 ${num(min)}</text>
  `;
}

function maLine(rows, key, plot, step, scaleY, cls, label) {
  const points = rows
    .map((row, index) => {
      const value = row.ma?.[key];
      return isFiniteNumber(value) ? `${plot.left + step * index + step / 2},${scaleY(value)}` : null;
    })
    .filter(Boolean)
    .join(" ");
  if (!points) return "";
  return `<polyline class="${cls}" points="${points}"></polyline><text class="ma-label ${key}-label" x="${plot.left + 8}" y="${plot.top + 16 + maLabelOffset(key)}">${label}</text>`;
}

function maLabelOffset(key) {
  return { ma5: 0, ma10: 16, ma20: 32 }[key] || 0;
}

function markerLayer(markers, rows, plot, step, scaleY, mode = "date") {
  if (!markers.length || !rows.length) return "";
  return markers
    .map((marker) => {
      const index = mode === "time"
        ? rows.findIndex((row) => row.time >= marker.time)
        : rows.findIndex((row) => row.date === marker.date);
      if (index < 0) return "";
      const x = plot.left + step * index + (mode === "date" ? step / 2 : 0);
      const y = scaleY(marker.price);
      const cls = marker.side === "buy" ? "marker-buy" : "marker-sell";
      const text = `${marker.strategy}${marker.side === "buy" ? "B" : "S"}`;
      return `<g class="bs-marker ${cls}"><circle cx="${x}" cy="${y}" r="8"></circle><text x="${x}" y="${y + 4}" text-anchor="middle">${esc(text)}</text></g>`;
    })
    .join("");
}

function isFiniteNumber(value) {
  return value !== null && value !== undefined && Number.isFinite(Number(value));
}

function drawChartEmpty(svg, message) {
  const size = chartSize(svg);
  svg.setAttribute("viewBox", `0 0 ${size.width} ${size.height}`);
  svg.innerHTML = `<rect class="chart-empty-bg" x="0" y="0" width="${size.width}" height="${size.height}" rx="12"></rect><text class="chart-empty-text" x="${size.width / 2}" y="${size.height / 2}" text-anchor="middle">${esc(message)}</text>`;
}

function showChartTooltip(root, tooltip, event, text, html = false) {
  if (!tooltip) return;
  const container = tooltip.parentElement || root;
  const rect = container.getBoundingClientRect();
  tooltip.hidden = false;
  if (html) tooltip.innerHTML = text;
  else tooltip.textContent = text;
  const tooltipWidth = tooltip.offsetWidth || 128;
  const tooltipHeight = tooltip.offsetHeight || 58;
  const rawLeft = event.clientX - rect.left + 14;
  const rawTop = event.clientY - rect.top - tooltipHeight - 12;
  const left = Math.min(rect.width - tooltipWidth - 8, Math.max(8, rawLeft));
  const top = Math.min(rect.height - tooltipHeight - 8, Math.max(8, rawTop));
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

function hideChartTooltip(tooltip) {
  if (tooltip) tooltip.hidden = true;
}

function cssEscape(value) {
  if (window.CSS?.escape) return window.CSS.escape(String(value));
  return String(value).replace(/["\\]/g, "\\$&");
}

function renderStock(code) {
  const requestedCode = code || DATA.preferred[0]?.code;
  const stock = DATA.preferred.find((item) => item.code === requestedCode);
  const historyStock = historicalStockForCode(requestedCode);
  const ledgerStock = recordsForStock(requestedCode)[0] || {};
  if (!stock) {
    if (!requestedCode || (!historyStock.code && !ledgerStock.code)) {
      app.innerHTML = pageTitle("没有可显示的股票", "当前数据包为空。");
      return;
    }
    const name = historyStock.name || ledgerStock.name || requestedCode;
    const currentPrice = historyStock.currentClose ?? ledgerStock.currentPrice;
    const currentTime = historyStock.latestQuoteTime ? latestPriceTime(historyStock) : ledgerMoment(ledgerStock.currentPriceTime || ledgerStock.latestDate);
    const entryPrice = ledgerStock.intendedPrice || ledgerStock.fillPrice || historyStock.entryClose;
    const currentGain = gainFrom(entryPrice, currentPrice) ?? historyStock.gainToDate ?? ledgerStock.benchmarkPnlPct;
    app.innerHTML = `
      ${pageTitle(
        `${name} ${requestedCode}`,
        "该标的当前不在优选池；事件价保持历史记录，当前价随行情刷新。",
        `<a class="btn" href="./history.html">返回历史</a><a class="btn" href="${esc(stockExternalUrl(requestedCode))}" target="_blank" rel="noopener noreferrer">雪球</a>`
      )}
      ${renderStockCharts(requestedCode, { name, currentClose: currentPrice })}
      <section class="stock-hero">
        <article class="panel">
          <h2>历史状态</h2>
          <div class="stock-meta" style="margin-bottom:16px">
            ${chip("当前未入池", "info")}
            ${chip(`${recordsForStock(requestedCode).length} 次记录`, "info")}
          </div>
          <div class="fact-grid">
            ${fact("当前实时价", num(currentPrice))}
            ${fact("报价时间", currentTime)}
            ${fact("入池至今", pct(currentGain))}
            ${fact("最近入池价", num(ledgerStock.intendedPrice || historyStock.entryClose))}
            ${fact("最近入池时间", ledgerMoment(ledgerStock.signalTime, historyStock.entryDate || "-"))}
            ${fact("最近退出价", num(ledgerStock.exitPrice))}
            ${fact("最近区间涨幅", pct(cycleGain(ledgerStock)))}
          </div>
        </article>
      </section>
      ${stockCycleSection(requestedCode)}
    `;
    return;
  }
  app.innerHTML = `
    ${pageTitle(
        `${stock.name} ${stock.code}`,
        `${entrySourceLabel(stock)}，最新快照交易日 ${stock.tradeDate}。`,
        `<a class="btn" href="./pool.html">返回优选池</a><a class="btn" href="${esc(stockExternalUrl(stock.code))}" target="_blank" rel="noopener noreferrer">雪球</a><button class="btn primary" data-drawer="${esc(stock.code)}">异常原因</button>`
      )}
    ${renderStockCharts(stock.code, stock)}

    <section class="stock-hero">
      <article class="panel">
        <h2>交易状态</h2>
        <div class="stock-meta" style="margin-bottom:16px">
          ${chip(stock.action === "buy" ? "推荐买入观察" : stock.action, "info")}
          ${chip(stock.riskCheck === "approved" ? "风控通过" : stock.riskCheck, stock.riskCheck === "approved" ? "ok" : "warn")}
          ${chip(`今日 ${pct(stock.todayPctChange)}`, stock.todayPctChange >= 0 ? "danger" : "ok")}
          ${chip(`入池至今 ${pct(stock.gainAfterEntry)}`, stock.gainAfterEntry >= 0 ? "danger" : "ok")}
        </div>
        <div class="fact-grid">
          ${fact("当前价", num(stock.currentClose))}
          ${fact("报价时间", latestPriceTime(stock))}
          ${fact("今日涨幅", `${pct(stock.todayPctChange)} / ${num(stock.todayPriceChange)}`)}
          ${fact("入池价", num(stock.entryClose))}
          ${fact("入池时间", entryMoment(stock))}
          ${fact("快照收盘价", num(stock.basic.close))}
          ${fact("20日涨幅", pct(stock.basic.return20d))}
          ${fact("60日涨幅", pct(stock.basic.return60d))}
          ${fact("20日波动率", pct(stock.basic.volatility20d))}
          ${fact("20日成交额", `${num(stock.basic.avgTurnover20dWan)} 万`)}
          ${fact("相对强度", score(stock.basic.relativeStrength))}
          ${fact("入池至今", pct(stock.gainAfterEntry))}
        </div>
      </article>
      <aside class="score-ring" style="--score:${stock.finalScore}">
        <span>${Math.round(stock.finalScore * 100)}</span>
      </aside>
    </section>

    ${stockCycleSection(stock.code)}

    <section class="grid two-col">
      <article class="panel">
        <h2>Agent评分</h2>
        <div class="agent-grid">
          ${stock.agents.map(agentCard).join("")}
        </div>
      </article>
      <article class="panel">
        <h2>异常波动原因</h2>
        <div class="reason-list">
          ${stock.anomalies.map(reasonItem).join("")}
        </div>
      </article>
    </section>

    <section class="panel" style="margin-top:16px">
      <h2>公开证据</h2>
      <div class="evidence-list">${formatEvidence(stock)}</div>
    </section>
  `;
  animateBars();
}

function fact(label, value) {
  return `
    <div class="fact">
      <div class="label">${esc(label)}</div>
      <div class="value">${esc(value)}</div>
    </div>
  `;
}

function agentCard(agent) {
  return `
    <article class="agent-card">
      <header>
        <strong>${esc(agent.label)}</strong>
        <span>${score(agent.score)}</span>
      </header>
      ${scoreBar(agent.score, "评分", agent.key)}
      <p class="agent-reason">${esc(formatAgentReason(agent.reason))}</p>
    </article>
  `;
}

function reasonItem(item) {
  return `
    <article class="reason-item">
      <span class="pill ${esc(item.level)}">${esc(levelText(item.level))} / ${esc(item.type)}</span>
      <strong>${esc(item.title)}</strong>
      <p>${esc(item.reason)}</p>
    </article>
  `;
}

function formatEvidence(stock) {
  const evidence = stock.evidence || {};
  const context = stock.context || {};
  const items = [];
  if (context.news) {
    const n = context.news;
    items.push(`
      <article class="evidence-item">
        <strong>新闻/公告信息面</strong>
        <p>${esc(polarityText(n.polarity))}，严重度${esc(severityText(n.severity))}，置信度 ${score(n.confidence)}，事件数 ${(n.events || []).length}。</p>
        <p>${esc(n.summary || n.headline || "近窗口未抓到重大新闻或公告")}</p>
      </article>
    `);
  }
  if (context.theme) {
    const t = context.theme;
    items.push(`
      <article class="evidence-item">
        <strong>题材/热度线索</strong>
        <p>${esc(t.summary || "同花顺热榜线索")}。</p>
        <p>题材：${esc((t.themes || []).join("、") || "-")}</p>
      </article>
    `);
  }
  if (evidence.dragon_tiger) {
    const d = evidence.dragon_tiger;
    items.push(`
      <article class="evidence-item">
        <strong>龙虎榜公开线索</strong>
        <p>机构席位净额 ${num((d.institution_net_buy || 0) / 10000)} 万元，席位数 ${esc(d.institution_seat_count ?? "-")}，交易日 ${esc(d.trade_date || "-")}。</p>
        <p>${esc(d.source_note || "")}</p>
      </article>
    `);
  }
  if (evidence.margin) {
    const m = evidence.margin;
    items.push(`
      <article class="evidence-item">
        <strong>融资融券线索</strong>
        <p>融资余额变化 ${num((m.financing_balance_change || 0) / 10000)} 万元，最新余额 ${num((m.latest_margin_balance || 0) / 10000)} 万元，数据日 ${esc(m.latest_date || "-")}。</p>
        <p>${esc(m.source_note || "")}</p>
      </article>
    `);
  }
  if (!items.length) {
    items.push(`
      <article class="evidence-item">
        <strong>暂无外部证据</strong>
        <p>当前未抓到龙虎榜或融资融券异常证据，机构/量化痕迹评分主要来自成交额、换手、趋势强度等公开行情因子。</p>
      </article>
    `);
  }
  return items.join("");
}

function renderAnomalies() {
  const rows = DATA.preferred
    .map((stock) => ({
      stock,
      items: stock.anomalies.filter((item) => state.filter === "all" || item.level === state.filter),
    }))
    .filter((row) => row.items.length);
  const totals = DATA.summary.anomalyTotals || {};
  const stocks = DATA.summary.anomalyStocks || {};
  app.innerHTML = `
    ${pageTitle(
      "异常监控",
      "一支股票一行；鼠标移入或键盘聚焦后展开分点说明。"
    )}

    <section class="anomaly-layout">
      <aside class="panel">
        <h2>筛选与解释</h2>
        <div class="segmented" style="margin-bottom:14px" aria-label="异常级别">
          ${["all", "danger", "warn", "info", "ok"]
            .map(
              (item) =>
                `<button data-level-filter="${item}" class="${state.filter === item ? "is-selected" : ""}">${esc(item === "all" ? "全部" : levelText(item))}</button>`
            )
            .join("")}
        </div>
        <div class="level-card">
          ${levelCount("风险股票", stocks.danger || 0, "danger", `${totals.danger || 0} 条高风险项`)}
          ${levelCount("预警股票", stocks.warn || 0, "warn", `${totals.warn || 0} 条预警项`)}
          ${levelCount("关注股票", stocks.info || 0, "info", `${totals.info || 0} 条资金/证据线索`)}
          ${levelCount("正常股票", stocks.ok || 0, "ok", `${totals.ok || 0} 条正常项`)}
        </div>
      </aside>
      <section class="panel">
        <h2>异常列表</h2>
        <div class="leader-list">
          ${
            rows.length
              ? rows.map(anomalyStockRow).join("")
              : `<div class="empty-state">当前筛选没有异常项</div>`
          }
        </div>
      </section>
    </section>
  `;
  document.querySelectorAll("[data-level-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      state.filter = button.dataset.levelFilter;
      renderAnomalies();
    });
  });
}

function levelCount(label, value, level, note = "") {
  return `
    <button class="level-count" data-level-filter="${esc(level)}">
      <span>
        <span class="pill ${esc(level)}">${esc(label)}</span>
        <small>${esc(note)}</small>
      </span>
      <strong>${esc(value)}</strong>
    </button>
  `;
}

function anomalyStockRow(row) {
  const stock = row.stock;
  const top = row.items[0];
  const severity = row.items.some((item) => item.level === "danger")
    ? "danger"
    : row.items.some((item) => item.level === "warn")
      ? "warn"
      : row.items.some((item) => item.level === "info")
        ? "info"
        : "ok";
  return `
    <article class="anomaly-stock-row" tabindex="0">
      <div>
        <span class="pill ${esc(severity)}">${esc(levelText(severity))}</span>
        ${chip(`${row.items.length} 条`, "info")}
      </div>
      <div>
        ${stockTitle(stock)}
        <p class="subtle">入池至今 ${pct(stock.gainAfterEntry)} / 综合 ${score(stock.finalScore)}</p>
      </div>
      <div>
        <strong>${esc(top.type)} / ${esc(top.title)}</strong>
        <p class="subtle">${esc(top.reason)}</p>
        <div class="anomaly-details">
          ${row.items.map((item) => reasonItem(item)).join("")}
        </div>
      </div>
    </article>
  `;
}

function renderAgents() {
  const agentKeys = Object.keys(DATA.agentLabels || {});
  app.innerHTML = `
    ${pageTitle(
      "Agent评分矩阵",
      "对比情绪面、资金面、信息面、基本面、趋势面和量化/机构痕迹评分。",
      `<a class="btn" href="./pool.html">查看股票列表</a>`
    )}

    <section class="agent-table-wrap">
      <table class="agent-table">
        <thead>
          <tr>
            <th>标的</th>
            <th>综合</th>
            ${agentKeys.map((key) => `<th>${esc(DATA.agentLabels[key])}</th>`).join("")}
            <th>异常</th>
          </tr>
        </thead>
        <tbody>
          ${DATA.preferred
            .map(
              (stock) => `
                <tr>
                  <td><a href="./stock.html?code=${esc(stock.code)}">${stockTitle(stock)}</a></td>
                  <td>${score(stock.finalScore)}</td>
                  ${agentKeys.map((key) => agentHeat(stock, key)).join("")}
                  <td>${anomalyChips(stock, 2)}</td>
                </tr>
              `
            )
            .join("")}
        </tbody>
      </table>
    </section>
  `;
}

function renderHistory() {
  const periods = DATA.historyPeriods || [];
  if (state.historyDate !== "all" && !periods.some((period) => period.date === state.historyDate)) {
    state.historyDate = periods[0]?.date || "all";
  }
  const selectedPeriod = periods.find((period) => period.date === state.historyDate);
  const rows = (DATA.history || []).filter((row) => {
    const query = state.query.trim().toLowerCase();
    return !query || `${row.code} ${row.name}`.toLowerCase().includes(query);
  });
  const periodStocks = (selectedPeriod?.stocks || []).filter((row) => {
    const query = state.query.trim().toLowerCase();
    return !query || `${row.code} ${row.name}`.toLowerCase().includes(query);
  });
  app.innerHTML = `
    ${pageTitle(
      "历史优选池记录",
      "记录每次进入优选池的入池价、当前价和入池至今涨幅，也可切换查看每期当日优选池。"
    )}

    <section class="panel">
      <div class="pool-tools">
        <label class="input-shell">
          <input id="poolSearch" type="search" value="${esc(state.query)}" placeholder="筛选代码或名称">
        </label>
        <div class="date-strip">
          <div class="segmented" aria-label="历史优选池期次">
            <button data-history-date="all" class="${state.historyDate === "all" ? "is-selected" : ""}">全部记录</button>
            ${periods
              .map(
                (period) =>
                  `<button data-history-date="${esc(period.date)}" class="${state.historyDate === period.date ? "is-selected" : ""}">${esc(period.date)}</button>`
              )
              .join("")}
          </div>
          ${historyDateExpander(periods)}
        </div>
      </div>
      ${
        selectedPeriod
          ? `<div class="notice-band history-period-summary">
              <p>当前期次 ${esc(selectedPeriod.date)}，优选池 ${esc(selectedPeriod.size || 0)} 支，平均分 ${score(selectedPeriod.avgScore)}。</p>
            </div>`
          : ""
      }
      <div class="history-list">
        ${
          state.historyDate === "all"
            ? rows.length
              ? rows.map(historyRow).join("")
              : `<div class="empty-state">暂无历史优选池记录</div>`
            : periodStocks.length
              ? periodStocks.map(historyPeriodRow).join("")
              : `<div class="empty-state">当前期次没有匹配的优选池标的</div>`
        }
      </div>
    </section>
  `;
  const input = document.getElementById("poolSearch");
  if (input) {
    input.addEventListener("input", (event) => {
      state.query = event.target.value;
      syncSearchValue();
      renderHistory();
    });
  }
  document.querySelectorAll("[data-history-date]").forEach((button) => {
    button.addEventListener("click", () => {
      state.historyDate = button.dataset.historyDate;
      renderHistory();
    });
  });
}

function historyDateExpander(periods) {
  const dates = periods.map((period) => period.date).filter(Boolean);
  return calendarDateExpander(dates, state.historyDate === "all" ? "" : state.historyDate, "history-date");
}

function calendarDateExpander(dates, selectedDate, dataName) {
  const months = calendarMonths(dates);
  return `
    <div class="date-expander" tabindex="0">
      <button type="button" class="date-trigger">${esc(selectedDate || "日期检索")}</button>
      <div class="date-popover calendar-popover">
        ${
          months.length
            ? months.map((month) => calendarMonth(month, selectedDate, dataName)).join("")
            : `<p class="subtle">暂无日期记录</p>`
        }
      </div>
    </div>
  `;
}

function calendarMonths(dates) {
  const byMonth = {};
  dates.forEach((date) => {
    const [year, month, day] = String(date).split("-");
    if (!year || !month || !day) return;
    const key = `${year}-${month}`;
    byMonth[key] ||= new Set();
    byMonth[key].add(Number(day));
  });
  return Object.entries(byMonth)
    .sort(([left], [right]) => right.localeCompare(left))
    .map(([key, days]) => {
      const [year, month] = key.split("-").map(Number);
      return { key, year, month, days };
    });
}

function calendarMonth(month, selectedDate, dataName) {
  const monthStart = new Date(month.year, month.month - 1, 1);
  const leading = (monthStart.getDay() + 6) % 7;
  const daysInMonth = new Date(month.year, month.month, 0).getDate();
  const cells = [
    ...Array.from({ length: leading }, () => null),
    ...Array.from({ length: daysInMonth }, (_, index) => index + 1),
  ];
  return `
    <div class="calendar-month">
      <div class="calendar-head">
        <strong>${month.year}年 ${month.month}月</strong>
        <span aria-hidden="true">‹ ›</span>
      </div>
      <div class="calendar-weekdays">
        ${["周一", "周二", "周三", "周四", "周五", "周六", "周日"].map((day) => `<span>${day}</span>`).join("")}
      </div>
      <div class="calendar-grid">
        ${cells
          .map((day) => {
            if (!day) return `<span class="calendar-blank"></span>`;
            const date = `${month.year}-${String(month.month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
            const available = month.days.has(day);
            return available
              ? `<button data-${dataName}="${esc(date)}" class="calendar-day ${selectedDate === date ? "is-selected" : ""}">${day}</button>`
              : `<span class="calendar-day is-disabled">${day}</span>`;
          })
          .join("")}
      </div>
    </div>
  `;
}

function historyRow(row) {
  const gain = row.gainToDate ?? 0;
  const displayScore = row.active && row.currentScore !== undefined ? row.currentScore : row.entryScore;
  return `
    <article class="history-row ${row.active ? "" : "is-inactive"}">
      <div>
        <div class="stock-title"><strong>${esc(row.name)}</strong><span>${esc(row.code)}</span></div>
        <div class="stock-meta">
          ${chip(entrySourceLabel(row), "info")}
          ${chip(row.active ? "当前优选" : "已退出", row.active ? "ok" : "info")}
          ${chip(`报价 ${latestPriceTime(row)}`, "info")}
        </div>
      </div>
      <div class="history-metrics">
        <span>入池价 <strong>${num(row.entryClose)}</strong></span>
        <span>当前价 <strong>${num(row.currentClose)}</strong></span>
        <span>综合分 <strong>${score(displayScore)}</strong></span>
      </div>
      <div class="gain-value ${gainClass(gain)}">${pct(gain)}</div>
      <div class="row-actions history-actions">
        <a class="btn primary" href="./stock.html?code=${esc(row.code)}">详情</a>
      </div>
    </article>
  `;
}

function historyPeriodRow(row) {
  const gain = row.gainAfterEntry ?? 0;
  const maxLevel = row.anomalyLevels?.includes("danger")
    ? "danger"
    : row.anomalyLevels?.includes("warn")
      ? "warn"
      : row.anomalyLevels?.includes("info")
        ? "info"
        : "ok";
  return `
    <article class="history-row">
      <div>
        <div class="stock-title"><strong>${esc(row.name)}</strong><span>${esc(row.code)}</span></div>
        <div class="stock-meta">
          ${chip(`排名 ${row.rank}`, "info")}
          ${chip(entrySourceLabel(row), "info")}
          ${chip(row.riskCheck === "approved" ? "风控通过" : row.riskCheck, row.riskCheck === "approved" ? "ok" : "warn")}
          ${chip(levelText(maxLevel), maxLevel)}
        </div>
      </div>
      <div class="history-metrics">
        <span>入池价 <strong>${num(row.entryClose)}</strong></span>
        <span>当前价 <strong>${num(row.currentClose)}</strong></span>
        <span>综合分 <strong>${score(row.finalScore)}</strong></span>
      </div>
      <div>
        <div class="gain-value ${gainClass(gain)}">${pct(gain)}</div>
        <p class="subtle">报价 ${esc(latestPriceTime(row))}</p>
      </div>
      <div class="row-actions history-actions">
        <a class="btn primary" href="./stock.html?code=${esc(row.code)}">详情</a>
      </div>
    </article>
  `;
}

function renderTrading() {
  const sim = DATA.simulatedTrading || { account: {}, positions: [], orders: [], latestFills: [], pendingSignals: [], guards: {}, config: {} };
  const simA2 = DATA.simulatedTradingA2 || { account: {}, positions: [], latestFills: [], orders: [], config: {} };
  const account = sim.account || {};
  const positions = sim.positions || [];
  const latestFills = sim.latestFills || [];
  const filledFills = latestFills.filter((row) => row.status === "filled");
  const rejectedFills = latestFills.filter((row) => row.status && row.status !== "filled");
  const orders = sim.orders || [];
  const missedSummary = sim.missedOpportunitySummary || {};
  const missedRows = (missedSummary.topStrongMissed || []).length
    ? missedSummary.topStrongMissed
    : sim.missedOpportunities || [];
  const marketRegime = sim.marketRegime || {};
  const cfg = sim.config || {};
  const dayPnl = account.dayPnl ?? account.dailyPnl ?? account.totalPnl;
  const dayPnlPct = account.dayPnlPct ?? account.dailyPnlPct ?? account.totalPnlPct;
  const sectorFlow = DATA.globalSectorFlow || {};
  const hotSectors = Array.isArray(sectorFlow.hotSectors) ? sectorFlow.hotSectors : [];
  const strategyComparison = DATA.strategyComparison || {};
  const strategySimulationComparison = DATA.strategySimulationComparison || buildClientStrategyComparison(sim, simA2);
  app.innerHTML = `
    ${pageTitle(
      "自动模拟交易",
      "使用优选池信号自动生成模拟买卖，接券商接口前不发送真实订单。"
    )}

    <section class="grid kpi-grid">
      ${metric("模拟权益", moneyFull(account.equity), `总盈亏 ${pct(account.totalPnlPct)}`)}
      ${metric("当日盈亏", signedMoneyFull(dayPnl), pct(dayPnlPct))}
      ${metric("可用现金", moneyFull(account.cash), `初始 ${moneyFull(account.initialCash)}`)}
      ${metric("持仓市值", moneyFull(account.positionValue), `仓位暴露 ${pct(account.exposurePct)}`)}
    </section>

    <section class="grid trading-workbench">
      <div class="trading-left-stack">
        <article class="panel">
          <h2>当前模拟持仓</h2>
          <div class="sim-list">
            ${positions.length ? positions.map((row) => simPositionRow(row, account)).join("") : `<div class="empty-state">暂无模拟持仓；等待行情确认后自动执行</div>`}
          </div>
        </article>
        <article class="panel">
          <h2>本轮自动指令</h2>
          <div class="sim-list">
            ${orders.length ? orders.map(simOrderRow).join("") : `<div class="empty-state">本轮没有新增模拟委托</div>`}
          </div>
        </article>
        ${returnWindow(tradingReturnSnapshot())}
        ${strategySimulationComparisonCard(strategySimulationComparison)}
        ${strategyComparisonPanel(strategyComparison)}
      </div>
      <article class="panel sector-flow-panel">
        <div class="section-title-row">
          <h2>热门板块</h2>
          <span>${sectorFlow.monitoringActive ? `识别 ${quoteDateTime(sectorFlow.identifiedAt) || "-"}` : "09:50 后识别"}</span>
        </div>
        <div class="sector-flow-note">
          <span>今日资金主线 · ${flowSourceText(sectorFlow.method || (hotSectors[0] || {}).flowSource)}</span>
          <span>${hotSectors.length || 0} 个</span>
        </div>
        <div class="sector-flow-list">
          ${
            hotSectors.length
              ? hotSectors.slice(0, 5).map((row, index) => sectorFlowRow(row, index)).join("")
              : `<div class="empty-state">${sectorFlow.monitoringActive ? "当前没有达到资金认可阈值的板块" : "等待 09:50 第一轮资金主线识别"}</div>`
          }
        </div>
      </article>
    </section>

    <section class="grid trading-review-grid" style="margin-top:16px">
      <article class="panel">
        <div class="section-title-row">
          <h2>最近模拟成交</h2>
          <a class="btn" href="./trade-records.html">交易记录</a>
        </div>
        <div class="sim-list">
          ${filledFills.length ? filledFills.slice().reverse().map(simFillRow).join("") : `<div class="empty-state">还没有模拟成交</div>`}
          ${
            rejectedFills.length
              ? `<div class="section-subtitle">最近拦截</div>${rejectedFills.slice(-4).reverse().map(simFillRow).join("")}`
              : ""
          }
        </div>
      </article>
      <div class="trading-review-stack">
        ${missedOpportunityTracker(missedRows, missedSummary)}
        <article class="panel">
          <h2>自动交易规则</h2>
          <div class="reason-list">
            <article class="reason-item">
              <span class="pill info">仓位</span>
              <strong>基础 ${pct((marketRegime.maxSingleWeight || cfg.maxSingleWeight || 0.1) * 100)} / 动态上限 ${pct((marketRegime.maxSingleWeightCap || marketRegime.maxSingleWeight || cfg.maxSingleWeight || 0.1) * 100)} / 总仓 ${pct((marketRegime.maxTotalWeight || cfg.maxTotalWeight || 0.5) * 100)}</strong>
              <p>最多 ${cfg.maxPositions || 5} 支但不强制买满；个股按置信档、风险折扣和大盘状态动态分配仓位。</p>
            </article>
            <article class="reason-item">
              <span class="pill info">大盘</span>
              <strong>${marketRegimeText(marketRegime.regime)} / 买入门槛 ${score(marketRegime.minBuyScore || cfg.minBuyScore)}</strong>
              <p>弱市或指数分化时提高门槛并缩仓；只有主要指数同步走强才放宽，过热时不追高并降低新开仓。</p>
            </article>
            <article class="reason-item">
              <span class="pill warn">弱转强</span>
              <strong>最早 ${esc(cfg.weakToStrongEarliestBuyTime || "10:05")} / 单票 ${weightPct((cfg.weakToStrongSingleWeights || {}).risk_off || 0.025)}</strong>
              <p>早盘只在先弱后强、量价回升、板块止跌且相对大盘走强时小仓试买；弱市总试仓不超过 ${weightPct((cfg.weakToStrongTotalWeights || {}).risk_off || 0.10)}。</p>
            </article>
            <article class="reason-item">
              <span class="pill ok">主线</span>
              <strong>主线试仓 ${weightPct(cfg.mainlineProbeWeight || 0.025)} / 总试仓 ${weightPct(cfg.mainlineProbeTotalWeight || 0.15)}</strong>
              <p>强市主线股若只差确认、略低于过热门槛或高波动异常，会用小仓试探；涨停、跌停、停牌、风控未过仍为硬拦截。</p>
            </article>
            <article class="reason-item">
              <span class="pill danger">风控</span>
              <strong>止损 ${pct(cfg.stopLossPct || -8)} / 止盈减半 ${pct(cfg.takeProfitTrimPct || 10)}</strong>
              <p>报价过期时暂停自动委托；涨停、停牌、重大异常时不新开仓。</p>
            </article>
          </div>
        </article>
      </div>
    </section>
  `;
  wireReturnWindow();
  Hyperframes().mount(app);
}

function sectorFlowRow(row, index) {
  const leader = row.leader || (row.hotStocks || [])[0] || {};
  const hotStocks = (row.hotStocks || []).filter((stock) => stock.code !== leader.code).slice(0, 2);
  const status = sectorFlowStatusText(row.status);
  return `
    <article class="sector-flow-row">
      <div class="sector-flow-rank">${index + 1}</div>
      <div class="sector-flow-main">
        <div class="stock-title"><strong>${esc(row.name)}</strong><span>${num(row.recognitionScore, 1)}</span></div>
        <div class="stock-meta">
          ${chip(status, sectorFlowStatusLevel(row.status))}
          ${chip(`净流 ${pct(row.netFlowRatio)}`, Number(row.netFlowRatio || 0) >= 0 ? "ok" : "danger")}
          ${chip(`成交 ${moneyYi(row.amountYuan)}`, "info")}
        </div>
      </div>
      <div class="sector-flow-stocks">
        <div class="sector-stock-group">
          <span>龙头</span>
          <div class="sector-stock-links">${leader.code ? sectorStockChip(leader) : chip("-", "info")}</div>
        </div>
        <div class="sector-stock-group">
          <span>热门股</span>
          <div class="sector-stock-links">${hotStocks.length ? hotStocks.map((stock) => sectorStockChip(stock)).join("") : chip("-", "info")}</div>
        </div>
      </div>
    </article>
  `;
}

function sectorStockChip(stock) {
  const label = `${stock.name || stock.code} ${pct(stock.pctChange)}`;
  const target = sectorStockHref(stock);
  const extraAttrs = target.external
    ? ` target="_blank" rel="noopener noreferrer" title="本地无详情，打开雪球"`
    : ` title="打开本地详情"`;
  return `<a class="stock-link-chip ${esc(gainClass(stock.pctChange))}" href="${esc(target.href)}"${extraAttrs}>${esc(label)}</a>`;
}

function sectorStockHref(stock) {
  const code = String(stock.code || "");
  if (localStockDetailCodes().has(code)) {
    return { href: `./stock.html?code=${encodeURIComponent(code)}`, external: false };
  }
  return { href: xueqiuStockUrl(code), external: true };
}

function sectorFlowStatusText(status) {
  return {
    accelerating: "加速流入",
    sustained: "持续流入",
    overheated: "过热观察",
    cooling: "资金降温",
    outflow: "资金流出",
    divergent: "分化",
    neutral: "中性",
  }[status] || "中性";
}

function sectorFlowStatusLevel(status) {
  return {
    accelerating: "ok",
    sustained: "ok",
    overheated: "warn",
    cooling: "warn",
    outflow: "danger",
    divergent: "warn",
    neutral: "info",
  }[status] || "info";
}

function flowSourceText(value) {
  const text = String(value || "");
  if (text.includes("reported")) return "资金流：接口净流入优先";
  if (text.includes("inferred") || text.includes("proxy")) return "资金流：成交额方向推断";
  if (text.includes("mixed")) return "资金流：接口与推断混合";
  return "资金流：等待识别";
}

function returnWindow(snapshot) {
  const periodKey = snapshot.views[state.returnPeriod] ? state.returnPeriod : "month";
  const active = snapshot.periods[periodKey] || snapshot.periods.month;
  const view = snapshot.views[periodKey];
  return `
    <article class="panel return-window" data-hf>
      <div class="section-title-row">
        <h2>收益记录</h2>
        <span>${esc(view.scopeLabel)}</span>
      </div>
      <div class="return-period-tabs" aria-label="收益周期">
        ${[
          ["day", "日"],
          ["week", "周"],
          ["month", "月"],
          ["year", "年"],
        ]
          .map(
            ([key, label]) =>
              `<button data-return-period="${key}" class="${periodKey === key ? "is-selected" : ""}">${label}</button>`
          )
          .join("")}
      </div>
      <div class="return-summary-strip">
        <div class="return-summary-main" data-hf>
          <span>${esc(active.label)}收益</span>
          <strong class="${gainClass(active.amount)}">${signedMoneyFull(active.amount)}</strong>
          <em class="${gainClass(active.pct)}">${pct(active.pct)}</em>
        </div>
        <div class="return-summary-mini">
          ${Object.entries(snapshot.periods)
            .map(
              ([key, row]) => `
                <button data-return-period="${key}" class="${periodKey === key ? "is-selected" : ""}" data-hf>
                  <span>${esc(row.shortLabel)}</span>
                  <strong class="${gainClass(row.pct)}">${pct(row.pct)}</strong>
                </button>
              `
            )
            .join("")}
        </div>
      </div>
      <div class="return-window-body">
        <div class="return-calendar-block">
          <div class="return-calendar-head">
            <h3>收益日历</h3>
            <span>${esc(view.scopeLabel)}</span>
          </div>
          <div class="return-calendar is-${periodKey}" data-hf>
            ${(view.headers || []).map((label) => `<span class="return-weekday">${esc(label)}</span>`).join("")}
            ${view.cells.map(returnCalendarCell).join("")}
          </div>
          <div class="return-month-line">
            <span>${esc(view.totalLabel)}</span>
            <strong class="${gainClass(view.totalPct)}">${pct(view.totalPct)}</strong>
            <em>${esc(view.totalNote)}</em>
          </div>
          ${returnReconciliationLine(view)}
        </div>
        <div class="return-ranking-block">
          <div class="return-ranking-head">
            <h3>股票收益排行</h3>
            <span>${esc(view.rankingLabel)}</span>
          </div>
          <div class="return-ranking">
            ${
              view.ranking.length
                ? view.ranking.slice(0, 5).map(returnRankRow).join("")
                : `<div class="empty-state">本周期暂无股票收益记录</div>`
            }
          </div>
        </div>
      </div>
    </article>
  `;
}

function strategyComparisonPanel(comparison) {
  const latest = comparison.latest || {};
  const summary = comparison.summary || {};
  const onlyB = latest.onlyB || [];
  const onlyC = latest.onlyC || [];
  const onlyA = latest.onlyA || [];
  const leader = summary.leaderByAvgGain === "C" ? "C 暂优" : summary.leaderByAvgGain === "B" ? "B 暂优" : summary.leaderByAvgGain === "A" ? "A 暂优" : "继续观察";
  return `
    <article class="panel strategy-ab-panel" data-hf>
      <div class="section-title-row">
        <h2>策略 A/B/C 跟踪</h2>
        <span>${esc(latest.date || comparison.latestDataDate || "-")}</span>
      </div>
      <div class="strategy-ab-note">
        <span>A 为当前主优选池，B 为板块资金流增强池，C 为弱市防守优选池。</span>
        <strong>${esc(leader)}</strong>
      </div>
      <div class="strategy-ab-stats">
        ${strategyStat("记录", `${summary.recordCount || 0} 天`, "累计样本")}
        ${strategyStat("重合", `${latest.overlapAllCount ?? latest.overlapCount ?? 0} 支`, `A独有 ${latest.onlyACount || 0} / B独有 ${latest.onlyBCount || 0} / C独有 ${latest.onlyCCount || 0}`)}
        ${strategyStat("A累计均值", pct(summary.avgGainPctA), `${summary.trackedRecordCount || 0} 天样本`)}
        ${strategyStat("B累计均值", pct(summary.avgGainPctB), `${summary.trackedRecordCount || 0} 天样本`)}
        ${strategyStat("C累计均值", pct(summary.avgGainPctC), `${summary.trackedRecordCount || 0} 天样本`)}
      </div>
      <div class="strategy-ab-columns">
        <div>
          <h3>B 独有候选 <span>仅影子策略选中</span></h3>
          <div class="strategy-mini-list">
            ${onlyB.length ? onlyB.slice(0, 4).map((row) => strategyCandidateRow(row, "B")).join("") : `<div class="empty-state compact">B 与 A 暂无独有差异</div>`}
          </div>
        </div>
        <div>
          <h3>C 独有候选 <span>仅防守策略选中</span></h3>
          <div class="strategy-mini-list">
            ${onlyC.length ? onlyC.slice(0, 4).map((row) => strategyCandidateRow(row, "C")).join("") : `<div class="empty-state compact">C 与 A/B 暂无独有差异</div>`}
          </div>
        </div>
        <div>
          <h3>A 独有候选 <span>仅主策略选中</span></h3>
          <div class="strategy-mini-list">
            ${onlyA.length ? onlyA.slice(0, 4).map((row) => strategyCandidateRow(row, "A")).join("") : `<div class="empty-state compact">A 与 B/C 暂无独有差异</div>`}
          </div>
        </div>
      </div>
    </article>
  `;
}

function strategySimulationComparisonCard(comparison) {
  const ranked = comparison.ranked?.length
    ? comparison.ranked
    : [
        { strategy: "A", ...(comparison.A || {}) },
        { strategy: "A2", ...(comparison.A2 || {}) },
      ];
  const leader = comparison.leaderByEquity === "A2"
    ? "A2领先"
    : comparison.leaderByEquity === "A"
      ? "A领先"
      : "持平";
  return `
    <article class="panel strategy-simulation-comparison" data-hf>
      <div class="section-title-row">
        <h2>策略 A / A2 对照</h2>
        <span>${esc(leader)}</span>
      </div>
      <p class="subtle">A2 关闭主线试仓，其他卖出、T+1、仓位、手续费和滑点规则保持一致，用于隔离主线试仓的实际贡献。</p>
      <div class="strategy-simulation-grid">
        ${ranked.map(strategySimulationMetric).join("") || `<div class="empty-state">暂无对照数据</div>`}
      </div>
      <div class="reason-list">
        <article class="reason-item">
          <span class="pill info">收益差</span>
          <strong>权益差 ${signedMoneyFull(comparison.equityDiff)}，收益率差 ${pct(comparison.totalPnlPctDiff)}</strong>
          <p>A 最近：${latestActionText(comparison.latestAAction)}；A2 最近：${latestActionText(comparison.latestA2Action)}。</p>
        </article>
        <article class="reason-item">
          <span class="pill ${gainClass(comparison.exposureDiffPct) === "gain-up" ? "ok" : "warn"}">仓位差</span>
          <strong>${pct(comparison.exposureDiffPct)} · 持仓差 ${comparison.positionDiff || 0} 支</strong>
          <p>用于判断关闭主线试仓后，参与度、交易成本和风险暴露如何变化。</p>
        </article>
      </div>
    </article>
  `;
}

function strategySimulationMetric(row) {
  const isA2 = row.strategy === "A2";
  return `
    <article class="strategy-simulation-card ${isA2 ? "is-control" : "is-main"}">
      <div class="strategy-simulation-card-head">
        <div>
          <strong>${isA2 ? "策略 A2" : "主策略 A"}</strong>
          <span>${isA2 ? "关闭主线试仓" : "当前生产策略"}</span>
        </div>
        <div class="strategy-simulation-equity">
          <strong>${moneyFull(row.equity)}</strong>
          <em class="${gainClass(row.totalPnlPct)}">${pct(row.totalPnlPct)}</em>
        </div>
      </div>
      <div class="strategy-simulation-metrics">
        ${strategySimulationStat("总收益", signedMoneyFull(row.totalPnl))}
        ${strategySimulationStat("手续费", moneyFull(row.commission))}
        ${strategySimulationStat("税费", moneyFull(row.stampTax))}
        ${strategySimulationStat("滑点", moneyFull(row.slippageCost))}
        ${strategySimulationStat("成交次数", `${row.tradeCount ?? 0} 次`)}
        ${strategySimulationStat("胜率", pct(row.winRatePct))}
        ${strategySimulationStat("Profit Factor", num(row.profitFactor, 2))}
        ${strategySimulationStat("最大回撤", `${moneyFull(row.maxDrawdown)} / ${pct(row.maxDrawdownPct)}`)}
        ${strategySimulationStat("仓位", `${pct(row.exposurePct)} · ${row.positions ?? 0} 支`)}
      </div>
    </article>
  `;
}

function strategySimulationStat(label, value) {
  return `
    <div class="strategy-simulation-stat">
      <span>${esc(label)}</span>
      <strong>${esc(value)}</strong>
    </div>
  `;
}

function latestActionText(row) {
  if (!row) return "暂无成交";
  return `${sideText(row.side)} ${row.name || row.code} ${shortTime(row.time)}`;
}

function buildClientStrategyComparison(simA, simA2) {
  const a = simA.account || {};
  const a2 = simA2.account || {};
  const equityA = Number(a.equity || 0);
  const equityA2 = Number(a2.equity || 0);
  return {
    leaderByEquity: equityA2 > equityA ? "A2" : equityA > equityA2 ? "A" : "tie",
    equityDiff: round2(equityA2 - equityA),
    totalPnlPctDiff: round2(Number(a2.totalPnlPct || 0) - Number(a.totalPnlPct || 0)),
    exposureDiffPct: round2(Number(a2.exposurePct || 0) - Number(a.exposurePct || 0)),
    A: a,
    A2: a2,
    ranked: [
      { strategy: "A", ...a },
      { strategy: "A2", ...a2 },
    ],
    latestAAction: (simA.latestFills || []).filter((row) => row.status === "filled").slice(-1)[0],
    latestA2Action: (simA2.latestFills || []).filter((row) => row.status === "filled").slice(-1)[0],
  };
}

function strategyStat(label, value, note) {
  return `
    <div class="strategy-stat">
      <span>${esc(label)}</span>
      <strong>${esc(value)}</strong>
      <em>${esc(note)}</em>
    </div>
  `;
}

function strategyCandidateRow(row, strategyKey) {
  const sector = row.sector || {};
  const gain = row.gainPct;
  const target = sectorStockHref(row);
  const attrs = target.external ? ` target="_blank" rel="noopener noreferrer"` : "";
  const strategyLabel = strategyCandidateLabel(strategyKey, sector.status || (row.reasons || [])[0]);
  return `
    <a class="strategy-candidate" href="${esc(target.href)}"${attrs}>
      <div>
        <strong>${esc(row.name || row.code)}</strong>
        <span>${esc(row.code)} · 排名 ${esc(row.rank || "-")}</span>
      </div>
      <div>
        <b>${score(row.score)}</b>
        <em class="${gainClass(gain)}">${gain === null || gain === undefined ? "待跟踪" : pct(gain)}</em>
      </div>
      <small>${esc(strategyLabel)}</small>
    </a>
  `;
}

function strategyCandidateLabel(strategyKey, rawStatus) {
  const owner = strategyKey === "A" ? "主策略 A" : strategyKey === "C" ? "防守策略 C" : "影子策略 B";
  if (strategyKey === "A") return owner;
  const status = strategyCandidateStatusText(rawStatus);
  return status ? `${owner} · ${status}` : owner;
}

function strategyCandidateStatusText(value) {
  return {
    accelerating: "资金加速流入",
    sustained: "资金持续流入",
    overheated: "过热观察",
    cooling: "资金降温",
    outflow: "资金流出",
    sector_leader: "板块龙头",
    sector_hot: "热门股",
    sector_member: "板块成员",
    sector_accelerating: "资金加速流入",
    sector_sustained: "资金持续流入",
    sector_overheated: "过热观察",
    sector_cooling_penalty: "资金降温",
    sector_outflow_penalty: "资金流出",
    sector_overheated_penalty: "板块过热",
    relative_resilience: "相对抗跌",
    moderate_resilience: "韧性一般",
    low_intraday_drawdown: "日内低回撤",
    low_volatility: "低波动",
    unextended_return: "涨幅未透支",
    danger_anomaly_penalty: "危险异常",
    limit_up_penalty: "涨停不追",
    high_volatility_penalty: "波动偏高",
    short_term_overextension_penalty: "短期过热",
    intraday_overheat_penalty: "盘中过热",
    weak_relative_strength_penalty: "相对偏弱",
    intraday_breakdown_penalty: "盘中破位",
    base_shadow_score: "基础影子评分",
    base_defensive_score: "防守评分",
  }[value] || "";
}

function returnCalendarCell(cell) {
  const level = cell.amount > 0 ? "is-gain" : cell.amount < 0 ? "is-loss" : "";
  const today = cell.isToday ? "is-today" : "";
  const muted = cell.isMuted ? "is-muted" : "";
  const period = cell.subLabel ? "is-period" : "";
  return `
    <div class="return-day ${level} ${today} ${muted} ${period}" data-hf>
      <span>${esc(cell.label)}</span>
      ${cell.subLabel ? `<small>${esc(cell.subLabel)}</small>` : ""}
      ${cell.hasReturn ? `<strong>${pct(cell.pct)}</strong>` : cell.note ? `<em>${esc(cell.note)}</em>` : ""}
    </div>
  `;
}

function returnRankRow(row, index) {
  const width = Math.max(8, Math.min(100, Math.abs(row.barPct || 0)));
  return `
    <article class="return-rank-row" data-hf>
      <div class="return-rank-medal">${index + 1}</div>
      <div class="return-rank-stock">
        <strong>${esc(row.name)}</strong>
        <span>${esc(row.code)}</span>
      </div>
      <div class="return-rank-bar">
        <i class="${gainClass(row.amount)}" data-hf-width="${width}"></i>
      </div>
      <div class="return-rank-value">
        <strong class="${gainClass(row.amount)}">${signedMoneyFull(row.amount)}</strong>
        <span class="${gainClass(row.pct)}">${pct(row.pct)}</span>
      </div>
    </article>
  `;
}

function tradingReturnSnapshot() {
  const sim = DATA.simulatedTrading || {};
  const account = sim.account || {};
  const initialCash = Number(account.initialCash || 1_000_000);
  const currentDate = account.dayPnlDate || sim.latestDataDate || DATA.meta.latestDataDate || DATA.meta.planDate || dateToIso(new Date());
  const currentMonth = currentDate ? currentDate.slice(0, 7) : new Date().toISOString().slice(0, 7);
  const realizedByFill = realizedPnlByFillKey();
  const daily = {};

  (sim.dailyReturns || []).forEach((row) => {
    const date = row.date || quoteIsoDate(row.assetVersion);
    if (!date) return;
    daily[date] = {
      date,
      amount: round2(Number(row.amount || 0)),
      pct: row.pct !== undefined ? Number(row.pct) : undefined,
      source: row.source || "account_mark_to_market",
    };
  });

  (sim.fills || [])
    .filter((row) => row.status === "filled" && row.side === "sell")
    .forEach((fill) => {
      const date = fillTradeDate(fill);
      if (!date || daily[date]) return;
      const realized = realizedByFill[fillKey(fill)] || {};
      const amount = Number(realized.amount || 0);
      daily[date] ||= { date, amount: 0 };
      daily[date].amount = round2(daily[date].amount + amount);
    });

  const dayPnl = Number(account.dayPnl || 0);
  if (currentDate && Math.abs(dayPnl) > 0.001 && !daily[currentDate]) {
    daily[currentDate] = { date: currentDate, amount: round2(dayPnl), source: "account_day_pnl" };
  }

  Object.values(daily).forEach((row) => {
    row.pct = initialCash ? round2((row.amount / initialCash) * 100) : 0;
  });

  const accountReturn = accountReturnSnapshot(account, initialCash);
  const activityDates = returnActivityDates(daily, sim);
  const periods = buildReturnPeriods(daily, currentDate, initialCash);
  reconcileReturnPeriodsWithAccount(periods, accountReturn, currentDate, activityDates);
  const views = Object.fromEntries(
    ["day", "week", "month", "year"].map((periodKey) => [
      periodKey,
      returnViewForPeriod(periodKey, daily, currentDate, initialCash, periods),
    ])
  );
  return {
    currentDate,
    currentMonth,
    currentMonthLabel: returnMonthLabel(currentMonth),
    currentMonthShort: `${Number(currentMonth.slice(5, 7)) || "-"}月`,
    daily,
    periods,
    views,
    accountReturn,
  };
}

function returnViewForPeriod(periodKey, daily, currentDate, initialCash, periods) {
  const currentMonth = currentDate.slice(0, 7);
  const currentYear = currentDate.slice(0, 4);
  const monthLabel = returnMonthLabel(currentMonth);
  const monthShort = `${Number(currentMonth.slice(5, 7))}月`;
  const shared = {
    ranking: stockReturnRanking(periodKey, currentDate),
  };
  if (periodKey === "day") {
    return {
      ...shared,
      scopeLabel: monthLabel,
      headers: ["日", "一", "二", "三", "四", "五", "六"],
      cells: buildReturnCalendar(currentMonth, daily, currentDate, initialCash),
      totalLabel: `${monthShort}累计收益率`,
      totalPct: periods.month.pct,
      totalNote: returnPeriodNote(periods.month),
      reconciliation: returnReconciliationForPeriod(periods.month),
      rankingLabel: "日内排行 · 已实现 + 持仓浮盈",
    };
  }
  if (periodKey === "week") {
    return {
      ...shared,
      scopeLabel: `${monthLabel} · 当月四周`,
      headers: [],
      cells: buildWeeklyReturnCalendar(currentMonth, daily, currentDate, initialCash),
      totalLabel: `${monthShort}累计收益率`,
      totalPct: periods.month.pct,
      totalNote: returnPeriodNote(periods.month),
      reconciliation: returnReconciliationForPeriod(periods.month),
      rankingLabel: "周内排行 · 已实现 + 持仓浮盈",
    };
  }
  if (periodKey === "year") {
    return {
      ...shared,
      scopeLabel: "全部年份",
      headers: [],
      cells: buildYearlyReturnCalendar(daily, currentYear, initialCash, periods),
      totalLabel: "全部年份累计收益率",
      totalPct: periods.year.pct,
      totalNote: returnPeriodNote(periods.year),
      reconciliation: returnReconciliationForPeriod(periods.year),
      rankingLabel: "年内排行 · 已实现 + 持仓浮盈",
    };
  }
  return {
    ...shared,
    scopeLabel: `${currentYear}年`,
    headers: [],
    cells: buildMonthlyReturnCalendar(currentYear, daily, currentDate, initialCash, periods),
    totalLabel: `${currentYear}年累计收益率`,
    totalPct: periods.year.pct,
    totalNote: returnPeriodNote(periods.year),
    reconciliation: returnReconciliationForPeriod(periods.year),
    rankingLabel: "月内排行 · 已实现 + 持仓浮盈",
  };
}

function returnReconciliationLine(view) {
  const row = view?.reconciliation;
  if (!row) return "";
  return `
    <div class="return-reconciliation-line" data-hf>
      <span>未分配历史收益</span>
      <strong class="${gainClass(row.amount)}">${signedMoneyFull(row.amount)} / ${pct(row.pct)}</strong>
      <em>${esc(row.note)}</em>
    </div>
  `;
}

function accountReturnSnapshot(account, initialCash) {
  const explicitPnl = Number(account.totalPnl);
  const equity = Number(account.equity);
  const amount = Number.isFinite(explicitPnl)
    ? explicitPnl
    : Number.isFinite(equity)
      ? equity - initialCash
      : null;
  if (!Number.isFinite(amount)) {
    return { available: false, amount: 0, pct: 0 };
  }
  const explicitPct = Number(account.totalPnlPct);
  return {
    available: true,
    amount: round2(amount),
    pct: Number.isFinite(explicitPct) ? round2(explicitPct) : initialCash ? round2((amount / initialCash) * 100) : 0,
    initialCash,
    source: "account_total_equity",
    note: "按账户总权益估算",
  };
}

function returnActivityDates(daily, sim) {
  const dates = new Set(Object.keys(daily || {}));
  (sim.fills || [])
    .filter((fill) => fill.status === "filled")
    .forEach((fill) => {
      const date = fillTradeDate(fill);
      if (date) dates.add(date);
    });
  return [...dates].sort();
}

function reconcileReturnPeriodsWithAccount(periods, accountReturn, currentDate, activityDates) {
  if (!accountReturn.available || !currentDate) return periods;
  const currentMonth = currentDate.slice(0, 7);
  const currentYear = currentDate.slice(0, 4);
  const dates = activityDates.length ? activityDates : [currentDate];
  if (dates.every((date) => date.startsWith(currentYear))) {
    attachAccountReturnReconciliation(periods.year, accountReturn);
    periods.year.amount = accountReturn.amount;
    periods.year.pct = accountReturn.pct;
    periods.year.source = accountReturn.source;
    periods.year.note = accountReturn.note;
  }
  if (dates.every((date) => date.startsWith(currentMonth))) {
    attachAccountReturnReconciliation(periods.month, accountReturn);
    periods.month.amount = accountReturn.amount;
    periods.month.pct = accountReturn.pct;
    periods.month.source = accountReturn.source;
    periods.month.note = accountReturn.note;
  }
  return periods;
}

function attachAccountReturnReconciliation(period, accountReturn) {
  period.ledgerAmount = round2(period.amount || 0);
  period.ledgerPct = round2(period.pct || 0);
  const reconciliationAmount = round2(accountReturn.amount - period.ledgerAmount);
  if (Math.abs(reconciliationAmount || 0) < 0.01) return;
  period.reconciliationAmount = reconciliationAmount;
  period.reconciliationPct = accountReturn.initialCash ? round2((reconciliationAmount / accountReturn.initialCash) * 100) : round2(accountReturn.pct - period.ledgerPct);
  period.reconciliationNote = "6月12日前缺少完整逐日权益快照，已计入账户总权益";
}

function returnReconciliationForPeriod(period) {
  if (!period || Math.abs(period.reconciliationAmount || 0) < 0.01) return null;
  return {
    amount: period.reconciliationAmount,
    pct: period.reconciliationPct,
    note: period.reconciliationNote || "账户总权益与逐日流水的历史补差",
  };
}

function returnPeriodNote(period) {
  return period?.note || "按模拟成交估算";
}

function buildReturnPeriods(daily, currentDate, initialCash) {
  const dates = Object.keys(daily).sort();
  const current = currentDate || dates[dates.length - 1] || "";
  const currentMonth = current ? current.slice(0, 7) : "";
  const currentYear = current ? current.slice(0, 4) : "";
  const weekStart = current ? startOfWeek(current) : "";
  const rows = {
    day: { label: "日", shortLabel: "日", amount: sumDaily(daily, (date) => date === current) },
    week: { label: "周", shortLabel: "周", amount: sumDaily(daily, (date) => weekStart && date >= weekStart && date <= current) },
    month: { label: "月", shortLabel: "月", amount: sumDaily(daily, (date) => date.startsWith(currentMonth)) },
    year: { label: "年", shortLabel: "年", amount: sumDaily(daily, (date) => date.startsWith(currentYear)) },
  };
  Object.values(rows).forEach((row) => {
    row.amount = round2(row.amount || 0);
    row.pct = initialCash ? round2((row.amount / initialCash) * 100) : 0;
  });
  return rows;
}

function sumDaily(daily, predicate) {
  return Object.entries(daily).reduce((sum, [date, row]) => sum + (predicate(date) ? Number(row.amount || 0) : 0), 0);
}

function startOfWeek(dateText) {
  const date = parseLocalDate(dateText);
  if (!date) return "";
  const daysSinceMonday = (date.getDay() + 6) % 7;
  date.setDate(date.getDate() - daysSinceMonday);
  return dateToIso(date);
}

function buildReturnCalendar(monthText, daily, currentDate, initialCash) {
  const [year, month] = monthText.split("-").map(Number);
  const first = new Date(year, month - 1, 1);
  const start = new Date(first);
  start.setDate(first.getDate() - first.getDay());
  const daysInMonth = new Date(year, month, 0).getDate();
  const cellCount = Math.ceil((first.getDay() + daysInMonth) / 7) * 7;
  const cells = [];
  for (let index = 0; index < Math.max(35, cellCount); index += 1) {
    const date = new Date(start);
    date.setDate(start.getDate() + index);
    const iso = dateToIso(date);
    const row = daily[iso] || {};
    const inMonth = date.getMonth() === month - 1;
    const isWeekend = date.getDay() === 0 || date.getDay() === 6;
    const amount = Number(row.amount || 0);
    cells.push({
      date: iso,
      label: String(date.getDate()).padStart(2, "0"),
      isToday: iso === currentDate,
      isMuted: !inMonth,
      hasReturn: !!row.date,
      amount,
      pct: row.pct !== undefined ? row.pct : initialCash ? round2((amount / initialCash) * 100) : 0,
      note: inMonth && isWeekend ? "休" : "",
    });
  }
  return cells;
}

function buildWeeklyReturnCalendar(monthText, daily, currentDate, initialCash) {
  const [year, month] = monthText.split("-").map(Number);
  const lastDay = new Date(year, month, 0).getDate();
  return [
    [1, 7],
    [8, 14],
    [15, 21],
    [22, lastDay],
  ].map(([startDay, endDay], index) => {
    const start = `${monthText}-${String(startDay).padStart(2, "0")}`;
    const end = `${monthText}-${String(endDay).padStart(2, "0")}`;
    const amount = sumDaily(daily, (date) => date >= start && date <= end);
    const hasReturn = Object.keys(daily).some((date) => date >= start && date <= end);
    return {
      label: `第${index + 1}周`,
      subLabel: `${String(month).padStart(2, "0")}.${String(startDay).padStart(2, "0")}-${String(month).padStart(2, "0")}.${String(endDay).padStart(2, "0")}`,
      isToday: currentDate >= start && currentDate <= end,
      isMuted: false,
      hasReturn,
      amount,
      pct: initialCash ? round2((amount / initialCash) * 100) : 0,
      note: hasReturn ? "" : "暂无",
    };
  });
}

function buildMonthlyReturnCalendar(yearText, daily, currentDate, initialCash, periods = {}) {
  return Array.from({ length: 12 }, (_, index) => {
    const month = index + 1;
    const prefix = `${yearText}-${String(month).padStart(2, "0")}`;
    const periodOverride = currentDate.startsWith(prefix) && periods.month?.source === "account_total_equity" ? periods.month : null;
    const amount = periodOverride ? periodOverride.amount : sumDaily(daily, (date) => date.startsWith(prefix));
    const hasReturn = Object.keys(daily).some((date) => date.startsWith(prefix));
    return {
      label: `${month}月`,
      subLabel: yearText,
      isToday: currentDate.startsWith(prefix),
      isMuted: false,
      hasReturn: hasReturn || !!periodOverride,
      amount,
      pct: periodOverride ? periodOverride.pct : initialCash ? round2((amount / initialCash) * 100) : 0,
      note: hasReturn || periodOverride ? "" : "暂无",
    };
  });
}

function buildYearlyReturnCalendar(daily, currentYear, initialCash, periods = {}) {
  const years = [...new Set([...Object.keys(daily).map((date) => date.slice(0, 4)), currentYear].filter(Boolean))].sort();
  return years.map((year) => {
    const periodOverride = year === currentYear && periods.year?.source === "account_total_equity" ? periods.year : null;
    const amount = periodOverride ? periodOverride.amount : sumDaily(daily, (date) => date.startsWith(year));
    const hasReturn = Object.keys(daily).some((date) => date.startsWith(year));
    return {
      label: `${year}年`,
      subLabel: "年度收益",
      isToday: year === currentYear,
      isMuted: false,
      hasReturn: hasReturn || !!periodOverride,
      amount,
      pct: periodOverride ? periodOverride.pct : initialCash ? round2((amount / initialCash) * 100) : 0,
      note: hasReturn || periodOverride ? "" : "暂无",
    };
  });
}

function stockReturnRanking(periodKey, currentDate) {
  const byCode = {};
  const realizedByFill = realizedPnlByFillKey();
  (DATA.simulatedTrading?.fills || [])
    .filter((fill) => fill.status === "filled" && fill.side === "sell" && returnPeriodMatchesDate(fillTradeDate(fill), periodKey, currentDate))
    .forEach((fill) => {
      const realized = realizedByFill[fillKey(fill)] || {};
      const amount = Number(realized.amount || 0);
      const cost = Math.max(0, Number(fill.price || 0) * Number(fill.shares || 0) - amount);
      addReturnRankingAmount(byCode, fill, amount, cost);
    });

  const latestPrices = latestTradePriceByCode();
  simulatedTradeRecords().forEach((lot) => {
    if (!lot.code || !lot.remainingShares) return;
    const openCost = Number(lot.buyCost || 0) * (Number(lot.remainingShares || 0) / Number(lot.shares || 1));
    const openValue = Number(lot.remainingShares || 0) * Number(latestPrices[lot.code] || lot.buyPrice || 0);
    addReturnRankingAmount(byCode, lot, openValue - openCost, openCost);
  });

  const rows = Object.values(byCode)
    .map((row) => ({
      ...row,
      amount: round2(row.amount),
      pct: row.cost ? round2((row.amount / row.cost) * 100) : 0,
    }))
    .sort((a, b) => b.amount - a.amount);
  const maxAbs = Math.max(...rows.map((row) => Math.abs(row.amount)), 1);
  rows.forEach((row) => {
    row.barPct = Math.abs(row.amount) / maxAbs * 100;
  });
  return rows;
}

function addReturnRankingAmount(byCode, row, amount, cost) {
  if (!row.code) return;
  byCode[row.code] ||= { code: row.code, name: row.name || row.code, amount: 0, cost: 0 };
  byCode[row.code].amount += Number(amount || 0);
  byCode[row.code].cost += Number(cost || 0);
}

function returnPeriodMatchesDate(date, periodKey, currentDate) {
  if (!date || !currentDate) return false;
  if (periodKey === "day") return date === currentDate;
  if (periodKey === "week") return date >= startOfWeek(currentDate) && date <= currentDate;
  if (periodKey === "month") return date.startsWith(currentDate.slice(0, 7));
  return date.startsWith(currentDate.slice(0, 4));
}

function returnMonthLabel(monthText) {
  const [year, month] = String(monthText || "").split("-");
  return year && month ? `${year}年 ${Number(month)}月` : "-";
}

function parseLocalDate(value) {
  const parts = String(value || "").split("-").map(Number);
  if (parts.length < 3 || parts.some((part) => Number.isNaN(part))) return null;
  return new Date(parts[0], parts[1] - 1, parts[2]);
}

function dateToIso(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function wireReturnWindow() {
  document.querySelectorAll("[data-return-period]").forEach((button) => {
    button.addEventListener("click", () => {
      state.returnPeriod = button.dataset.returnPeriod || "month";
      renderTrading();
    });
  });
}

function Hyperframes() {
  return {
    mount(root = document) {
      requestAnimationFrame(() => {
        root.querySelectorAll("[data-hf]").forEach((node, index) => {
          node.style.setProperty("--hf-delay", `${Math.min(index * 28, 420)}ms`);
          node.classList.add("hf-in");
        });
        root.querySelectorAll("[data-hf-width]").forEach((node) => {
          node.style.width = `${node.dataset.hfWidth}%`;
        });
      });
    },
  };
}

function renderTradeRecords() {
  const rows = simulatedTradeRecords();
  const dates = [...new Set(rows.map((row) => row.buyDate).filter(Boolean))].sort().reverse();
  if (!state.tradeDate || !dates.includes(state.tradeDate)) state.tradeDate = dates[0] || "";
  const query = state.query.trim().toLowerCase();
  const filtered = rows.filter((row) => {
    const dateMatch = !state.tradeDate || row.buyDate === state.tradeDate;
    const queryMatch = !query || `${row.code} ${row.name}`.toLowerCase().includes(query);
    return dateMatch && queryMatch;
  });
  const pnlTotal = filtered.reduce((sum, row) => sum + (Number(row.pnlAmount) || 0), 0);
  const costTotal = filtered.reduce((sum, row) => sum + (Number(row.buyCost) || 0), 0);
  const pnlTotalPct = costTotal > 0 ? (pnlTotal / costTotal) * 100 : null;
  app.innerHTML = `
    ${pageTitle(
      "模拟交易记录",
      "按买入日期检索当天买入记录；后续卖出后会自动回填卖出价与盈亏。",
      `<a class="btn" href="./trading.html">返回实盘</a>`
    )}

    <section class="panel">
      <div class="pool-tools">
        <label class="input-shell">
          <input id="tradeSearch" type="search" value="${esc(state.query)}" placeholder="筛选代码或名称">
        </label>
        <div class="date-strip">
          <div class="segmented" aria-label="交易日期">
            ${dates.map((date) => `<button data-trade-date="${esc(date)}" class="${state.tradeDate === date ? "is-selected" : ""}">${esc(date)}</button>`).join("")}
          </div>
          ${calendarDateExpander(dates, state.tradeDate, "trade-date")}
        </div>
      </div>
      <div class="trade-summary-card">
        <div>
          <span>今日盈亏</span>
          <strong class="${gainClass(pnlTotal)}">${signedMoneyFull(pnlTotal)} <em>${pct(pnlTotalPct)}</em></strong>
        </div>
        <p>${esc(state.tradeDate || "-")} 买入 ${filtered.length} 笔，未卖出显示卖出价为 -。</p>
      </div>
      <div class="trade-record-list">
        ${filtered.length ? filtered.map(tradeRecordRow).join("") : `<div class="empty-state">当前日期没有匹配的买入记录</div>`}
      </div>
    </section>
  `;
  const input = document.getElementById("tradeSearch");
  if (input) {
    input.addEventListener("input", (event) => {
      state.query = event.target.value;
      syncSearchValue();
      renderTradeRecords();
    });
  }
  document.querySelectorAll("[data-trade-date]").forEach((button) => {
    button.addEventListener("click", () => {
      state.tradeDate = button.dataset.tradeDate;
      renderTradeRecords();
    });
  });
}

function tradeRecordRow(row) {
  return `
    <article class="trade-record-row">
      <div>
        <div class="stock-title"><strong>${esc(row.name)}</strong><span>${esc(row.code)}</span></div>
        <div class="stock-meta">
          ${chip(`买入 ${shortTime(row.buyTime)}`, "ok")}
          ${chip(`${row.shares} 股`, "info")}
          ${chip(row.remainingShares > 0 ? "未卖出" : "已卖出", row.remainingShares > 0 ? "warn" : "ok")}
        </div>
      </div>
      <div class="trade-record-prices">
        <span>买入价 <strong>${num(row.buyPrice, 3)}</strong></span>
        <span>卖出价 <strong>${row.sellPrice === null || row.sellPrice === undefined ? "-" : num(row.sellPrice, 3)}</strong></span>
      </div>
      <div class="trade-record-pnl">
        <span>盈亏</span>
        <strong class="${gainClass(row.pnlAmount)}">${signedMoneyFull(row.pnlAmount)} <em>${pct(row.pnlPct)}</em></strong>
      </div>
      <div class="row-actions">
        <a class="btn primary" href="./stock.html?code=${esc(row.code)}">详情</a>
      </div>
    </article>
  `;
}

function simMissedRow(row) {
  const reasons = row.reasons || [row.primaryReason || "not_selected"];
  return `
    <article class="sim-signal-row">
      <div>
        ${stockTitle(row)}
        <div class="stock-meta">
          ${chip(`排名 ${row.rank || "-"}`, "info")}
          ${chip(`评分 ${score(row.score)}`, "info")}
          ${chip(`门槛 ${score(row.minBuyScore)}`, "warn")}
        </div>
      </div>
      <span>未买后 ${pct(row.gainPct)}</span>
      <div class="sim-blockers">
        ${reasons.slice(0, 3).map((item) => chip(blockerText(item), blockerLevel(item))).join("")}
      </div>
    </article>
  `;
}

function missedOpportunityTracker(rows, summary = {}) {
  const visibleRows = (rows || []).slice(0, 8);
  const strongCount = Number(summary.strongMissedCount || 0);
  const trackedCount = Number(summary.trackedCount || visibleRows.length || 0);
  const reviewText = `${summary.reviewCadence === "weekly" ? "每周" : "滚动"}复盘`;
  return `
    <article class="panel missed-opportunity-tracker" data-hf>
      <div class="section-title-row">
        <div>
          <p class="eyebrow">Missed Opportunity Desk</p>
          <h2>错过标的追踪</h2>
        </div>
        <span>${reviewText}</span>
      </div>
      <div class="missed-opportunity-summary">
        <div>
          <span>复盘摘要</span>
          <strong>${trackedCount}</strong>
          <em>已跟踪标的</em>
        </div>
        <div>
          <span>强错过</span>
          <strong class="${strongCount ? "gain-up" : ""}">${strongCount}</strong>
          <em>涨幅过阈值</em>
        </div>
        <div>
          <span>入池后涨幅阈值</span>
          <strong>${pct(summary.gainThresholdPct || 0)}</strong>
          <em>跑赢持仓 ${pct(summary.outperformThresholdPct || 0)}</em>
        </div>
        <div>
          <span>持仓均值</span>
          <strong class="${gainClass(summary.heldAveragePnlPct)}">${pct(summary.heldAveragePnlPct)}</strong>
          <em>对照基准</em>
        </div>
      </div>
      <div class="missed-opportunity-list">
        ${
          visibleRows.length
            ? visibleRows.map(missedOpportunityRow).join("")
            : `<div class="empty-state">暂无错过标的记录；进入优选池但未买的票会在这里沉淀未买原因和后续涨幅</div>`
        }
      </div>
    </article>
  `;
}

function missedOpportunityRow(row) {
  const reasons = row.reasons || [row.primaryReason || "not_selected"];
  const outperformance = row.outperformancePct;
  const outperformanceText = outperformance === null || outperformance === undefined ? "待对照" : pct(outperformance);
  const reviewState = Number(row.gainPct || 0) >= 0 ? "机会复盘" : "风险过滤";
  return `
    <article class="missed-opportunity-row" data-hf>
      <div class="missed-opportunity-head">
        ${stockTitle(row)}
        <div class="stock-meta">
          ${chip(`日期 ${row.date || "-"}`, "info")}
          ${chip(`排名 ${row.rank || "-"}`, "info")}
          ${chip(`市场 ${marketRegimeText(row.marketRegime)}`, "info")}
        </div>
      </div>
      <div class="missed-opportunity-metrics">
        <div>
          <span>入池后涨幅</span>
          <strong class="${gainClass(row.gainPct)}">${pct(row.gainPct)}</strong>
        </div>
        <div>
          <span>相对持仓</span>
          <strong class="${gainClass(outperformance)}">${outperformanceText}</strong>
        </div>
        <div>
          <span>评分/门槛</span>
          <strong>${score(row.score)} / ${score(row.minBuyScore)}</strong>
        </div>
      </div>
      <div class="missed-opportunity-flow">
        <div>
          <span>价格轨迹</span>
          <strong>${num(row.firstPrice, 3)} -> ${num(row.latestPrice, 3)}</strong>
          <em>${quoteDateTime(row.firstSeenAt)} 到 ${quoteDateTime(row.lastSeenAt)}</em>
        </div>
        <div>
          <span>未买原因</span>
          <div class="sim-blockers">${reasons.slice(0, 4).map((item) => chip(blockerText(item), blockerLevel(item))).join("")}</div>
        </div>
        <div>
          <span>复盘标签</span>
          <strong>${reviewState}</strong>
          <em>${row.reviewCadence === "weekly" ? "周复盘样本" : "滚动样本"}</em>
        </div>
      </div>
    </article>
  `;
}

function simPositionRow(row, account = {}) {
  const equity = Number(account.equity || 0);
  const positionWeightPct = equity > 0 ? (Number(row.marketValue || 0) / equity) * 100 : null;
  const sector = row.sectorContext && typeof row.sectorContext === "object" ? row.sectorContext : {};
  const sectorLabel = sector.primaryTheme ? `${sector.primaryTheme}/${sectorFlowStatusText(sector.flowStatus)}` : "";
  return `
    <article class="sim-position-row">
      <div>
        ${stockTitle(row)}
        <div class="stock-meta">
          ${chip(`${row.shares || 0} 股`, "info")}
          ${chip(`入场 ${shortTime(row.entryTime)}`, "ok")}
          ${chip(convictionText(row.positionConviction), convictionLevel(row.positionConviction))}
          ${chip(`目标 ${weightPct(row.targetWeight)}`, "info")}
          ${sectorLabel ? chip(sectorLabel, sectorFlowStatusLevel(sector.flowStatus)) : ""}
          ${chip(row.canSell === false ? "T+1锁定" : "可卖", row.canSell === false ? "warn" : "ok")}
        </div>
      </div>
      <div class="history-metrics sim-position-main-metrics">
        <span>成本 <strong>${num(row.avgCost, 3)}</strong></span>
        <span>现价 <strong>${num(row.marketPrice, 3)}</strong></span>
      </div>
      <div class="sim-position-pnl">
        <div class="gain-value ${gainClass(row.pnlPct)}">${pct(row.pnlPct)}</div>
        <p class="subtle">收益 <strong class="${gainClass(row.unrealizedPnl)}">${signedMoneyFull(row.unrealizedPnl)}</strong></p>
      </div>
      <div class="row-actions">
        <a class="btn primary" href="./stock.html?code=${esc(row.code)}">详情</a>
      </div>
      <div class="history-metrics sim-position-extra-metrics">
        <span>市值 <strong>${moneyFull(row.marketValue)}</strong></span>
        <span>仓位占比 <strong>${pct(positionWeightPct)}</strong></span>
      </div>
    </article>
  `;
}

function simOrderRow(row) {
  return `
    <article class="sim-order-row">
      <div>
        <div class="stock-title"><strong>${sideText(row.side)}</strong><span>${esc(row.code)}</span></div>
        <div class="stock-meta">
          ${chip(orderReasonText(row.reason), row.side === "sell" ? "warn" : "ok")}
          ${chip(`${row.shares || 0} 股`, "info")}
        </div>
      </div>
      <span>${esc(row.name || row.code)}</span>
      <strong>${num(row.referencePrice, 3)}</strong>
    </article>
  `;
}

function simFillRow(row) {
  const level = row.status === "filled" ? (row.side === "sell" ? "warn" : "ok") : "danger";
  const realized = row.side === "sell" && row.status === "filled" ? realizedPnlByFillKey()[fillKey(row)] : null;
  return `
    <article class="sim-fill-row">
      <div>
        <div class="stock-title"><strong>${esc(row.name || row.code)}</strong><span>${esc(row.code)}</span></div>
        <div class="stock-meta">
          ${chip(sideText(row.side), level)}
          ${chip(fillOutcomeText(row.status), level)}
          ${chip(orderReasonText(row.reason), "info")}
        </div>
      </div>
      <span>${shortTime(row.time)}</span>
      <span>${row.shares || 0} 股</span>
      <strong>${num(row.price, 3)}</strong>
      <span class="${realized ? gainClass(realized.amount) : ""}">${realized ? `${signedMoneyFull(realized.amount)} / ${pct(realized.pct)}` : "-"}</span>
    </article>
  `;
}

function fillKey(row) {
  return [row.time || row.assetVersion || row.tradeDate || "", row.side || "", row.code || "", row.shares || "", row.price || "", row.status || "", row.reason || ""].join("|");
}

function realizedPnlByFillKey() {
  if (realizedPnlByFillKey.cacheVersion === DATA.simulatedTrading?.assetVersion && realizedPnlByFillKey.cache) {
    return realizedPnlByFillKey.cache;
  }
  const result = {};
  const openByCode = {};
  const fills = (DATA.simulatedTrading?.fills || [])
    .filter((row) => row.status === "filled" && (row.side === "buy" || row.side === "sell"))
    .slice()
    .sort((a, b) => tradeFillSortKey(a).localeCompare(tradeFillSortKey(b)));
  fills.forEach((fill) => {
    const code = fill.code;
    const shares = Number(fill.shares || 0);
    const price = Number(fill.price || 0);
    if (!code || !shares || !price) return;
    openByCode[code] ||= [];
    if (fill.side === "buy") {
      openByCode[code].push({ remainingShares: shares, price });
      return;
    }
    let remainingSell = shares;
    let matchedCost = 0;
    let matchedShares = 0;
    for (const lot of openByCode[code]) {
      if (remainingSell <= 0) break;
      if (lot.remainingShares <= 0) continue;
      const matched = Math.min(lot.remainingShares, remainingSell);
      lot.remainingShares -= matched;
      remainingSell -= matched;
      matchedShares += matched;
      matchedCost += matched * lot.price;
    }
    const amount = price * matchedShares - matchedCost;
    result[fillKey(fill)] = {
      amount: round2(amount),
      pct: matchedCost ? round2((amount / matchedCost) * 100) : null,
    };
  });
  realizedPnlByFillKey.cacheVersion = DATA.simulatedTrading?.assetVersion;
  realizedPnlByFillKey.cache = result;
  return result;
}

function simSignalRow(row) {
  const eligible = !!row.eligible;
  const reversal = row.weakToStrong || {};
  const reversalEligible = !!reversal.eligible;
  const mainline = row.mainlineProbe || {};
  const mainlineEligible = !!mainline.eligible;
  return `
    <article class="sim-signal-row">
      <div>
        ${stockTitle(row)}
        <div class="stock-meta">
          ${chip(`行情 ${row.confirmations || 0}/${row.requiredConfirmations || 2}`, eligible ? "ok" : "warn")}
          ${chip(`弱转强 ${reversal.confirmations || 0}/${reversal.requiredConfirmations || 2}`, reversalEligible ? "ok" : "warn")}
          ${chip(mainlineEligible ? "主线可试仓" : "主线观察", mainlineEligible ? "ok" : "info")}
          ${chip(`排名 ${row.rank || "-"}`, "info")}
          ${chip(`评分 ${score(row.score)}`, "info")}
          ${chip(convictionText(row.positionConviction), convictionLevel(row.positionConviction))}
          ${chip(`目标 ${weightPct(row.targetWeight)}`, "info")}
        </div>
      </div>
      <span>现价 ${num(row.price, 3)}</span>
      <div class="sim-blockers">
        ${
          eligible
            ? chip("可生成买入", "ok")
            : reversalEligible
              ? chip("弱转强可试仓", "ok")
              : mainlineEligible
                ? chip("主线小仓试探", "ok")
            : (row.blockers || []).map((item) => chip(blockerText(item), blockerLevel(item))).join("")
        }
        ${!eligible && !reversalEligible && !mainlineEligible ? (reversal.blockers || []).slice(0, 3).map((item) => chip(blockerText(item), blockerLevel(item))).join("") : ""}
        ${!eligible && !reversalEligible && !mainlineEligible ? (mainline.blockers || []).slice(0, 3).map((item) => chip(blockerText(item), blockerLevel(item))).join("") : ""}
      </div>
    </article>
  `;
}

function sideText(side) {
  return { buy: "买入", sell: "卖出" }[side] || "委托";
}

function fillOutcomeText(status) {
  return { filled: "已成交", rejected: "已拒绝" }[status] || "待处理";
}

function orderReasonText(reason) {
  return {
    confirmed_signal: "行情确认",
    weak_to_strong_confirmed: "弱转强确认",
    mainline_probe: "主线试仓",
    market_de_risk: "大盘降仓",
    out_of_pool: "退出优选池",
    out_of_pool_confirmed: "出池确认",
    sector_breakdown: "板块破位",
    sector_breakdown_trim: "板块破位减仓",
    sector_cooling: "板块降温",
    sector_cooling_trim: "板块降温减仓",
    stop_loss: "止损",
    take_profit_trim: "止盈减半",
    outside_trade_execution_window: "非交易时段",
    outside_trade_execution_window_after_repair: "非交易时段修正",
    before_buy_window_after_repair: "早盘买入修正",
    max_positions_after_t_plus_one_repair: "仓位上限修正",
    no_position_after_t_plus_one_repair: "无持仓修正",
    t_plus_one_locked: "T+1 锁定",
    cash_not_enough: "现金不足",
    no_position: "无持仓",
  }[reason] || reason || "自动规则";
}

function blockerText(reason) {
  return {
    stale_quotes: "报价过期",
    outside_trade_execution_window: "非交易时段",
    needs_confirmation: "等待行情",
    before_buy_window: "下午开仓",
    weak_to_strong_needs_confirmation: "弱转强等待",
    weak_to_strong_before_window: "弱转强未到点",
    weak_to_strong_window_closed: "弱转强窗口结束",
    weak_to_strong_score_below_min: "弱转强评分不足",
    weak_to_strong_disabled: "弱转强关闭",
    mainline_probe_disabled: "主线试仓关闭",
    mainline_probe_before_window: "主线试仓未到点",
    mainline_market_not_strong: "非强市主线",
    mainline_score_too_low: "主线评分不足",
    not_mainline: "非资金主线",
    score_below_min: "评分未过线",
    missing_score: "缺少评分",
    exposure_limit: "总仓已满",
    max_positions: "数量上限",
    lot_size_or_cash: "现金/整手不足",
    risk_not_approved: "风控未通过",
    suspended: "停牌",
    limit_up: "涨停不追",
    limit_down: "跌停破位",
    severe_intraday_drop: "盘中大跌",
    sector_breakdown: "板块破位",
    sector_flow_outflow: "主线资金流出",
    danger_anomaly: "重大异常",
  }[reason] || reason || "待复核";
}

function blockerLevel(reason) {
  return {
    stale_quotes: "danger",
    outside_trade_execution_window: "warn",
    needs_confirmation: "warn",
    before_buy_window: "warn",
    weak_to_strong_needs_confirmation: "warn",
    weak_to_strong_before_window: "warn",
    weak_to_strong_window_closed: "warn",
    weak_to_strong_score_below_min: "warn",
    weak_to_strong_disabled: "warn",
    mainline_probe_disabled: "warn",
    mainline_probe_before_window: "warn",
    mainline_market_not_strong: "warn",
    mainline_score_too_low: "warn",
    not_mainline: "warn",
    score_below_min: "warn",
    missing_score: "warn",
    exposure_limit: "warn",
    max_positions: "warn",
    lot_size_or_cash: "warn",
    risk_not_approved: "danger",
    suspended: "danger",
    limit_up: "warn",
    limit_down: "danger",
    severe_intraday_drop: "danger",
    sector_breakdown: "danger",
    sector_flow_outflow: "danger",
    danger_anomaly: "danger",
  }[reason] || "warn";
}

function convictionText(value) {
  return {
    elite: "高置信",
    strong: "增强仓",
    weak_to_strong: "弱转强",
    mainline_probe: "主线试仓",
    normal: "普通仓",
  }[value] || "普通仓";
}

function convictionLevel(value) {
  return {
    elite: "ok",
    strong: "info",
    weak_to_strong: "ok",
    mainline_probe: "ok",
    normal: "warn",
  }[value] || "warn";
}

function marketRegimeText(regime) {
  return {
    risk_off: "弱市防守",
    divergent: "指数分化",
    neutral: "中性",
    risk_on: "强势进攻",
    overheated: "过热降温",
  }[regime] || "中性";
}

function shortTime(value) {
  if (!value) return "-";
  const text = String(value);
  if (text.includes("T")) return text.slice(5, 16).replace("T", " ");
  return text.slice(0, 16);
}

function simulatedTradeRecords() {
  const fills = (DATA.simulatedTrading?.fills || [])
    .filter((row) => row.status === "filled" && (row.side === "buy" || row.side === "sell"))
    .slice()
    .sort((a, b) => tradeFillSortKey(a).localeCompare(tradeFillSortKey(b)));
  const latestPriceByCode = latestTradePriceByCode();
  const lots = [];
  const openByCode = {};

  fills.forEach((fill) => {
    const code = fill.code;
    if (!code) return;
    const shares = Number(fill.shares || 0);
    const price = Number(fill.price || 0);
    if (!shares || !price) return;

    if (fill.side === "buy") {
      const lot = {
        code,
        name: fill.name || code,
        buyDate: fillTradeDate(fill),
        buyTime: fill.time || fill.assetVersion || fill.tradeDate,
        shares,
        buyPrice: price,
        buyCost: Number(fill.gross || price * shares) + Number(fill.fee || 0),
        remainingShares: shares,
        soldShares: 0,
        sellGross: 0,
        sellFee: 0,
        lastSellTime: null,
      };
      lots.push(lot);
      openByCode[code] ||= [];
      openByCode[code].push(lot);
      return;
    }

    let remainingSell = shares;
    for (const lot of openByCode[code] || []) {
      if (remainingSell <= 0) break;
      if (lot.remainingShares <= 0) continue;
      const matched = Math.min(lot.remainingShares, remainingSell);
      const feeShare = Number(fill.fee || 0) * (matched / shares);
      lot.remainingShares -= matched;
      lot.soldShares += matched;
      lot.sellGross += price * matched;
      lot.sellFee += feeShare;
      lot.lastSellTime = fill.time || fill.assetVersion || fill.tradeDate;
      remainingSell -= matched;
    }
  });

  return lots
    .map((lot) => {
      const latestPrice = Number(latestPriceByCode[lot.code] || lot.buyPrice);
      const sellNet = lot.sellGross - lot.sellFee;
      const openValue = lot.remainingShares * latestPrice;
      const pnlAmount = sellNet + openValue - lot.buyCost;
      return {
        ...lot,
        sellPrice: lot.soldShares > 0 ? lot.sellGross / lot.soldShares : null,
        pnlAmount: round2(pnlAmount),
        pnlPct: lot.buyCost ? round2((pnlAmount / lot.buyCost) * 100) : null,
      };
    })
    .sort((a, b) => `${b.buyDate || ""} ${b.buyTime || ""}`.localeCompare(`${a.buyDate || ""} ${a.buyTime || ""}`));
}

function latestTradePriceByCode() {
  const prices = {};
  (DATA.history || []).forEach((row) => {
    if (row.code && row.currentClose !== undefined && prices[row.code] === undefined) prices[row.code] = row.currentClose;
  });
  (DATA.tradeLedger?.records || []).forEach((row) => {
    if (row.code && row.currentPrice !== undefined && prices[row.code] === undefined) prices[row.code] = row.currentPrice;
  });
  (DATA.preferred || []).forEach((row) => {
    if (row.code && row.currentClose !== undefined) prices[row.code] = row.currentClose;
  });
  (DATA.simulatedTrading?.positions || []).forEach((row) => {
    if (row.code && row.marketPrice !== undefined) prices[row.code] = row.marketPrice;
  });
  return prices;
}

function fillTradeDate(fill) {
  return fill.tradeDate || quoteIsoDate(fill.assetVersion) || quoteIsoDate(fill.time) || "";
}

function tradeFillSortKey(fill) {
  return `${fillTradeDate(fill)} ${fill.assetVersion || ""} ${fill.time || ""}`;
}

function round2(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return null;
  return Math.round(Number(value) * 100) / 100;
}

function renderInformation() {
  const information = DATA.informationSummary || {};
  const rows = informationRowsForView(state.infoView).filter((row) => {
    const query = state.query.trim().toLowerCase();
    return !query || `${row.code} ${row.name} ${row.summary} ${row.headline}`.toLowerCase().includes(query);
  });
  app.innerHTML = `
    ${pageTitle(
      "信息面详情",
      "只展示抓到新闻、公告事件或触发负面/重大风险的标的。",
      `<a class="btn" href="./index.html">返回总览</a>`
    )}

    <section class="panel">
      <div class="pool-tools">
        <label class="input-shell">
          <input id="poolSearch" type="search" value="${esc(state.query)}" placeholder="筛选代码、名称、标题">
        </label>
        <div class="segmented" aria-label="信息面筛选">
          ${[
            ["events", "新闻覆盖"],
            ["material", "重大风险"],
            ["risk", "负面/混合"],
          ]
            .map(
              ([key, label]) =>
                `<button data-info-view="${key}" class="${state.infoView === key ? "is-selected" : ""}">${esc(label)}</button>`
            )
            .join("")}
        </div>
      </div>
      <div class="grid three-col compact-metrics info-metrics">
        <button data-info-view="events" class="metric-card mini-metric ${state.infoView === "events" ? "is-selected" : ""}">
          <div class="metric-label">新闻覆盖</div>
          <div class="metric-value">${information.totals?.codes || 0} 支</div>
          <div class="metric-note">${information.totals?.events || 0} 条事件</div>
        </button>
        <button data-info-view="material" class="metric-card mini-metric ${state.infoView === "material" ? "is-selected" : ""}">
          <div class="metric-label">重大风险</div>
          <div class="metric-value">${information.totals?.materialRisks || 0} 支</div>
          <div class="metric-note">高风险公告或新闻</div>
        </button>
        <button data-info-view="risk" class="metric-card mini-metric ${state.infoView === "risk" ? "is-selected" : ""}">
          <div class="metric-label">负面/混合</div>
          <div class="metric-value">${(information.totals?.negative || 0) + (information.totals?.mixed || 0)} 支</div>
          <div class="metric-note">负面 ${information.totals?.negative || 0} / 混合 ${information.totals?.mixed || 0}</div>
        </button>
      </div>
      <div class="info-detail-list">
        ${
          rows.length
            ? rows.map(infoDetailRow).join("")
            : `<div class="empty-state">当前筛选没有新闻、公告或信息面风险事件</div>`
        }
      </div>
    </section>
  `;
  const input = document.getElementById("poolSearch");
  if (input) {
    input.addEventListener("input", (event) => {
      state.query = event.target.value;
      syncSearchValue();
      renderInformation();
    });
  }
  document.querySelectorAll("[data-info-view]").forEach((button) => {
    button.addEventListener("click", () => {
      state.infoView = button.dataset.infoView;
      renderInformation();
    });
  });
}

function informationRowsForView(view) {
  const rows = DATA.informationSummary?.stocks || [];
  if (view === "material") return rows.filter((row) => row.materialRisk || row.severity === "high");
  if (view === "risk") return rows.filter((row) => row.polarity === "negative" || row.polarity === "mixed");
  return rows.filter((row) => row.eventCount > 0);
}

function infoDetailRow(row) {
  const level = row.riskLevel || "info";
  const events = row.events || [];
  return `
    <article class="info-detail-row">
      <div>
        ${stockTitle(row)}
        <div class="stock-meta">
          ${chip(polarityText(row.polarity), level)}
          ${chip(`严重度${severityText(row.severity)}`, level)}
          ${chip(`${row.eventCount || 0} 条事件`, "info")}
          ${row.materialRisk ? chip("重大风险", "danger") : ""}
        </div>
      </div>
      <p class="subtle">${esc(row.summary || row.headline || "")}</p>
      <div class="info-event-list">
        ${
          events.length
            ? events
                .map(
                  (event) => `
                    <div class="info-event">
                      <strong>${esc(event.title || row.headline || "-")}</strong>
                      <span>${esc(event.source || "-")} / ${esc(event.publish_time || event.publishTime || "-")}</span>
                    </div>
                  `
                )
                .join("")
            : `<div class="info-event"><strong>${esc(row.headline || "信息面风险")}</strong><span>${esc(row.summary || "-")}</span></div>`
        }
      </div>
    </article>
  `;
}

function renderDailyNews() {
  const information = DATA.informationSummary || {};
  const positiveRows = dailyNewsRowsByPolarity(["positive"]);
  const negativeRows = dailyNewsRowsByPolarity(["negative"]);
  const neutralRows = dailyNewsRowsByPolarity(["mixed", "neutral"]);
  app.innerHTML = `
    ${pageTitle(
      "每日新闻",
      "按利好、利空和中性/混合信息拆分，盘前会继续用于优选池再评分。",
      `<a class="btn" href="./information.html">信息面详情</a>`
    )}

    <section class="grid three-col compact-metrics info-metrics">
      ${metric("利好", `${positiveRows.length} 条`, `覆盖 ${information.totals?.positive || 0} 支标的`)}
      ${metric("利空", `${negativeRows.length} 条`, `负面 ${information.totals?.negative || 0} / 重大风险 ${information.totals?.materialRisks || 0}`)}
      ${metric("中性/混合", `${neutralRows.length} 条`, `混合 ${information.totals?.mixed || 0} / 中性 ${information.totals?.neutral || 0}`)}
    </section>

    <section class="grid two-col" style="margin-top:16px">
      ${dailyNewsSection("利好", "偏正面信息，观察能否转化为资金和板块共振。", positiveRows, "ok")}
      ${dailyNewsSection("利空", "负面新闻、重大风险或高严重度事件，盘前评分会优先扣分。", negativeRows, "danger")}
    </section>
    <section class="panel" style="margin-top:16px">
      <div class="section-title-row">
        <h2>中性/多空混合</h2>
        <span>${neutralRows.length} 条</span>
      </div>
      <div class="info-detail-list">
        ${neutralRows.length ? neutralRows.map(dailyNewsEventRow).join("") : `<div class="empty-state">暂无中性或多空混合新闻</div>`}
      </div>
    </section>
  `;
}

function dailyNewsRowsByPolarity(polarities) {
  return aggregateDailyNewsRows(polarities);
}

function aggregateDailyNewsRows(polarities) {
  const wanted = new Set(polarities);
  const grouped = new Map();
  const trackedCodes = dailyNewsTrackedCodes();
  (DATA.informationSummary?.stocks || []).forEach((stock) => {
    const sourceEvents = (stock.messageEvents || []).length
      ? stock.messageEvents
      : (stock.events || []).filter(isMessageNewsEvent);
    const events = sourceEvents.length
      ? sourceEvents
      : [
          {
            title: stock.headline || stock.summary || "信息面事件",
            source: "",
            publish_time: "",
            polarity: stock.polarity,
            severity: stock.severity,
          },
    ];
    events.forEach((event) => {
      if (!isMessageNewsEvent(event)) return;
      const polarity = event.polarity || stock.polarity || "neutral";
      const materialRisk = Boolean(stock.materialRisk || event.material_risk);
      const bucket = materialRisk && polarity !== "positive" ? "negative" : polarity;
      if (!wanted.has(bucket)) return;

      const key = dailyNewsEventKey(event, stock);
      if (!grouped.has(key)) {
        grouped.set(key, {
          eventKey: key,
          title: event.title || stock.headline || stock.summary || "-",
          summary: event.summary || stock.summary || stock.headline || "",
          source: event.source || "-",
          publishTime: event.publish_time || event.publishTime || "-",
          url: event.url || "",
          polarity: bucket,
          severity: event.severity || stock.severity || "low",
          materialRisk,
          relatedStocks: [],
          relatedStockCodes: new Set(),
          trackedRelatedCount: 0,
        });
      }

      const row = grouped.get(key);
      row.materialRisk = row.materialRisk || materialRisk;
      row.severity = higherSeverity(row.severity, event.severity || stock.severity || "low");
      row.trackedRelatedCount += addDailyNewsRelatedStock(row, stock, trackedCodes) ? 1 : 0;
    });
  });
  const rows = Array.from(grouped.values()).map((row) => {
    row.relatedStocks.sort((left, right) => {
      if (left.tracked !== right.tracked) return left.tracked ? -1 : 1;
      return String(left.code || "").localeCompare(String(right.code || ""));
    });
    return row;
  });
  const query = state.query.trim().toLowerCase();
  return rows
    .filter((row) => !query || dailyNewsSearchText(row).includes(query))
    .sort((left, right) => {
      if (right.trackedRelatedCount !== left.trackedRelatedCount) return right.trackedRelatedCount - left.trackedRelatedCount;
      return String(right.publishTime || "").localeCompare(String(left.publishTime || ""));
    });
}

function dailyNewsEventKey(event, stock) {
  const title = event.title || stock.headline || stock.summary || "信息面事件";
  const url = String(event.url || "").replace(/\s+/g, "").trim();
  if (url) return `url:${url}`;
  return [
    title,
    event.source || "",
    event.publish_time || event.publishTime || "",
  ]
    .map((part) => String(part || "").replace(/\s+/g, " ").trim())
    .join("|");
}

function dailyNewsTrackedCodes() {
  const codes = [
    ...(DATA.preferred || []).map((row) => row.code),
    ...(DATA.simulatedTrading?.positions || []).map((row) => row.code),
    ...(DATA.simulatedTradingA2?.positions || []).map((row) => row.code),
  ];
  return new Set(codes.filter(Boolean).map(String));
}

function isTrackedNewsStock(code, trackedCodes = dailyNewsTrackedCodes()) {
  return trackedCodes.has(String(code || ""));
}

function addDailyNewsRelatedStock(row, stock, trackedCodes) {
  const code = String(stock.code || "");
  if (!code || row.relatedStockCodes.has(code)) return false;
  const tracked = isTrackedNewsStock(code, trackedCodes);
  row.relatedStockCodes.add(code);
  row.relatedStocks.push({
    code,
    name: stock.name || code,
    tracked,
  });
  return tracked;
}

function higherSeverity(left, right) {
  const rank = { high: 3, medium: 2, low: 1 };
  return (rank[right] || 1) > (rank[left] || 1) ? right : left;
}

function dailyNewsSearchText(row) {
  return [
    row.title,
    row.summary,
    row.source,
    ...(row.relatedStocks || []).flatMap((stock) => [stock.code, stock.name]),
  ]
    .join(" ")
    .toLowerCase();
}

const MESSAGE_NEWS_KEYWORDS = [
  "公告",
  "拟",
  "计划",
  "投资",
  "投建",
  "建设",
  "扩产",
  "产能",
  "加产能",
  "回购",
  "减持",
  "增持",
  "股权",
  "冻结",
  "辞职",
  "澄清",
  "传闻",
  "合作",
  "签约",
  "订单",
  "中标",
  "并购",
  "收购",
  "重组",
  "项目",
  "涨价",
  "价格上调",
  "政策",
  "政府",
  "监管",
  "处罚",
  "立案",
  "诉讼",
  "纠纷",
  "破产",
  "产线",
  "发布",
  "财报",
  "业绩",
  "营收",
  "利润",
  "亏损",
  "报案",
  "合同",
  "获批",
  "问询函",
  "需求",
  "HBM",
  "DRAM",
  "三星",
  "海力士",
  "韩国",
  "美光",
  "台积电",
  "英伟达",
];

const MARKET_DATA_NEWS_KEYWORDS = [
  "半年线",
  "涨幅",
  "涨超",
  "上涨",
  "下跌",
  "跌停",
  "涨停",
  "走势",
  "主力资金",
  "净流出",
  "净流入",
  "融资客",
  "龙虎榜数据",
  "上榜",
  "收盘价",
  "历史新高",
  "盘中",
  "ETF",
  "个股",
  "成交",
  "换手",
];

function isMessageNewsEvent(event) {
  const text = `${event?.title || ""} ${event?.summary || ""}`;
  if (!text.trim()) return false;
  if (MESSAGE_NEWS_KEYWORDS.some((keyword) => text.includes(keyword))) return true;
  if (MARKET_DATA_NEWS_KEYWORDS.some((keyword) => text.includes(keyword))) return false;
  return false;
}

function dailyNewsSection(title, subtitle, rows, level) {
  return `
    <article class="panel">
      <div class="section-title-row">
        <div>
          <h2>${esc(title)}</h2>
          <p class="subtle">${esc(subtitle)}</p>
        </div>
        <span class="pill ${esc(level)}">${rows.length} 条</span>
      </div>
      <div class="info-detail-list">
        ${rows.length ? rows.map(dailyNewsEventRow).join("") : `<div class="empty-state">暂无${esc(title)}新闻</div>`}
      </div>
    </article>
  `;
}

function dailyNewsEventRow(row) {
  const level = row.materialRisk || row.severity === "high"
    ? "danger"
    : row.polarity === "positive"
      ? "ok"
      : row.polarity === "negative"
        ? "warn"
        : "info";
  return `
    <article class="info-detail-row">
      <div>
        <div class="stock-title daily-news-title"><strong>${esc(row.title || "-")}</strong><span>${esc(row.source || "-")}</span></div>
        <div class="stock-meta">
          ${chip(polarityText(row.polarity), level)}
          ${chip(`严重度${severityText(row.severity)}`, level)}
          ${chip(`${(row.relatedStocks || []).length} 支相关标的`, "info")}
          ${row.materialRisk ? chip("重大风险", "danger") : ""}
        </div>
      </div>
      <div class="info-event-list">
        <div class="info-event">
          <strong>${row.url ? `<a href="${esc(row.url)}" target="_blank" rel="noopener">${esc(row.title || "-")}</a>` : esc(row.title || "-")}</strong>
          <span>${esc(row.source || "-")} / ${esc(row.publishTime || "-")}</span>
        </div>
      </div>
      ${dailyNewsRelatedStocks(row)}
      ${row.summary ? `<p class="subtle">${esc(row.summary)}</p>` : ""}
    </article>
  `;
}

function dailyNewsRelatedStocks(row) {
  const stocks = row.relatedStocks || [];
  if (!stocks.length) return "";
  return `
    <div class="daily-news-related">
      <span>相关标的</span>
      <div>
        ${stocks
          .map(
            (stock) => `
              <a class="daily-news-related-stock${stock.tracked ? " is-tracked" : ""}" href="./stock.html?code=${esc(stock.code)}" title="${stock.tracked ? "优选池/持仓重点跟踪" : "新闻相关标的"}">
                ${esc(stock.name || stock.code)} ${esc(stock.code || "")}
              </a>
            `
          )
          .join("")}
      </div>
    </div>
  `;
}

function renderCloseBrief() {
  const brief = CLOSE_BRIEF || {};
  const summary = brief.summary || {};
  const automationRows = closeBriefAutomationRows(brief);
  const automationStatus = closeBriefAutomationStatus(brief, automationRows);
  const query = state.query.trim().toLowerCase();
  const actions = (brief.actionRows || []).filter((row) => closeBriefMatch(row, query));
  const decisions = (brief.decisionRows || []).filter((row) => closeBriefMatch(row, query));
  app.innerHTML = `
    ${pageTitle(
      brief.title || "今日收盘交易简报",
      brief.subtitle || "收盘后固化全市场快照评分、优选池排序和当天模拟交易结果。",
      `<a class="btn" href="./trading.html">查看实盘</a><a class="btn" href="./daily-news.html">每日新闻</a>`
    )}

    <section class="grid kpi-grid">
      ${metric("任务状态", closeBriefAutomationStatusText(automationStatus, summary), "自动化总览同口径")}
      ${metric("候选覆盖", summary.coverage || "-", "已抓取/应覆盖股票池")}
      ${metric("剩余未评分", `${summary.remaining || 0}`, "越接近 0 越完整")}
      ${metric("优选池", `${summary.decisionCount || 0} 支`, "收盘后排序结果")}
    </section>

    <section class="grid two-col" style="margin-top:16px">
      <article class="panel">
        <div class="section-title-row">
          <h2>今日系统运行</h2>
          <span>${automationRows.length} 项</span>
        </div>
        <div class="reason-list">
          ${
            automationRows.length
              ? automationRows.map(closeBriefAutomationRow).join("")
              : `<div class="empty-state">暂无自动化状态记录</div>`
          }
        </div>
      </article>
      <article class="panel">
        <div class="section-title-row">
          <h2>今日操作与原因</h2>
          <span>${actions.length} 条</span>
        </div>
        <div class="sim-list">
          ${actions.length ? actions.map(closeBriefActionRow).join("") : `<div class="empty-state">今日没有成交、委托或候选拦截记录</div>`}
        </div>
      </article>
    </section>

    <section class="panel" style="margin-top:16px">
      <div class="section-title-row">
        <h2>收盘优选池排序</h2>
        <span>${decisions.length} 支</span>
      </div>
      <div class="pool-list">
        ${decisions.length ? decisions.map(closeBriefDecisionRow).join("") : `<div class="empty-state">暂无收盘优选池记录</div>`}
      </div>
    </section>
  `;
}

function closeBriefAutomationRows(brief) {
  const liveRows = DATA.automationStatus?.items || [];
  if (liveRows.length) return liveRows;
  return brief.automationRows || [];
}

function closeBriefAutomationStatus(brief, rows) {
  if (DATA.automationStatus?.summaryStatus) return DATA.automationStatus.summaryStatus;
  if (brief.summary?.automationStatus) return brief.summary.automationStatus;
  const severity = { error: 4, missing: 3, degraded: 2, skipped: 1, ok: 0 };
  if (!rows.length) return "missing";
  return rows.reduce((worst, row) => (severity[row.status] || 0) > (severity[worst] || 0) ? row.status : worst, "ok");
}

function closeBriefAutomationStatusText(status, summary) {
  return {
    ok: "正常",
    degraded: "降级完成",
    skipped: "跳过",
    error: "失败",
    missing: "未运行",
  }[status] || summary.status || "-";
}

function closeBriefMatch(row, query) {
  if (!query) return true;
  return `${row.code || ""} ${row.name || ""} ${row.reason || ""} ${row.type || ""}`.toLowerCase().includes(query);
}

function closeBriefAutomationRow(row) {
  const status = row.statusText || row.status || "-";
  return `
    <article class="reason-item">
      <span class="pill ${status === "ok" || status === "正常" ? "ok" : status === "degraded" || status === "降级完成" ? "warn" : "info"}">${esc(status)}</span>
      <strong>${esc(row.label || row.key || "自动化任务")}</strong>
      <p>${esc(row.note || row.reason || "完成时间")} ${esc(row.finishedAt || row.lastRun || "")}</p>
    </article>
  `;
}

function closeBriefActionRow(row) {
  const level = row.type === "成交" && row.side === "sell"
    ? "warn"
    : row.type === "成交"
      ? "ok"
      : row.type === "未买入"
        ? "warn"
        : "info";
  return `
    <article class="sim-fill-row">
      <div>
        <div class="stock-title"><strong>${esc(row.name || row.code || "-")}</strong><span>${esc(row.code || "")}</span></div>
        <div class="stock-meta">
          ${chip(row.type || "-", level)}
          ${chip(row.side || "观察", level)}
          ${chip(row.reason || "自动规则", "info")}
        </div>
      </div>
      <span>${esc(row.status || "-")}</span>
      <span>${esc(row.time || "-")}</span>
    </article>
  `;
}

function closeBriefDecisionRow(row) {
  return `
    <article class="pool-row">
      <div>
        <div class="stock-title"><strong>${esc(row.name || row.code || "-")}</strong><span>${esc(row.code || "")}</span></div>
        <div class="stock-meta">
          ${chip(`排名 ${row.rank || "-"}`, "info")}
          ${chip(row.risk || "待复核", String(row.risk || "").includes("通过") ? "ok" : "warn")}
        </div>
      </div>
      ${scoreBar(row.score || 0, "收盘评分", "rank")}
      <p class="subtle">${esc(row.reason || "综合评分靠前，等待盘前复核。")}</p>
    </article>
  `;
}

function orderRow(stock) {
  return `
    <article class="order-row">
      <div>${stockTitle(stock)}</div>
      <span class="pill info">买入观察</span>
      <span>参考价 ${num(stock.currentClose)}</span>
      <span>入池至今 ${pct(stock.gainAfterEntry)}</span>
      <span class="pill ${stock.anomalies.some((item) => item.level === "danger") ? "danger" : "warn"}">
        ${stock.anomalies.length} 条异常
      </span>
    </article>
  `;
}

function ledgerRow(row) {
  const simulated = row.simulatedPnlPct !== undefined && row.simulatedPnlPct !== null;
  return `
    <article class="ledger-row">
      <div>
        <div class="stock-title"><strong>${esc(row.name)}</strong><span>${esc(row.code)}</span></div>
        <div class="stock-meta">
          ${chip(modeText(row.entryMode), "info")}
          ${chip(fillStatusText(row.fillStatus), row.fillStatus === "simulated" ? "ok" : "warn")}
          ${chip(`滑点 ${pct(row.slippagePct || 0)}`, "info")}
        </div>
      </div>
      <div class="history-metrics">
        <span>基准价 <strong>${num(row.intendedPrice)}</strong></span>
        <span>模拟成交 <strong>${num(row.fillPrice)}</strong></span>
        <span>最新价 <strong>${num(row.currentPrice)}</strong></span>
      </div>
      <div>
        <div class="gain-value ${gainClass(row.benchmarkPnlPct)}">${pct(row.benchmarkPnlPct)}</div>
        <p class="subtle">${simulated ? `模拟 ${pct(row.simulatedPnlPct)}` : "待确认成交价"}</p>
      </div>
      <div class="row-actions">
        <a class="btn primary" href="./stock.html?code=${esc(row.code)}">详情</a>
      </div>
    </article>
  `;
}

function modeText(mode) {
  return {
    preopen: "盘前信号",
    intraday: "盘中信号",
    legacy_reference: "历史基准",
    historical: "历史记录",
  }[mode] || "观察记录";
}

function fillStatusText(status) {
  return {
    simulated: "已模拟成交",
    reference_only: "参考价待确认",
  }[status] || "待确认";
}

function agentHeat(stock, key) {
  const agent = stock.agents.find((item) => item.key === key);
  const value = agent ? agent.score : 0;
  return `
    <td>
      <div class="heat" style="--score:${value}">
        <span>${score(value)}</span>
      </div>
    </td>
  `;
}

function openEvidenceDrawer() {
  const drawer = document.getElementById("detailDrawer");
  const backdrop = document.getElementById("drawerBackdrop");
  const evidence = DATA.evidenceSummary || {};
  drawer.innerHTML = `
    <div class="drawer-head">
      <div>
        <p class="eyebrow">公开证据覆盖</p>
        <h2>${esc(evidence.totalCodes || 0)} 支标的</h2>
        <p class="subtle">${esc(evidence.note || "")}</p>
      </div>
      <button class="close-btn" data-close-drawer aria-label="关闭">×</button>
    </div>
    <div class="fact-grid evidence-facts">
      ${fact("龙虎榜", `${evidence.dragonTigerCodes || 0} 支`)}
      ${fact("融资融券", `${evidence.marginCodes || 0} 支`)}
      ${fact("优选池命中", `${evidence.preferredCodes || 0} 支`)}
      ${fact("数据来源", "东方财富公开页")}
    </div>
    <div class="drawer-section">
      <h3>优选池线索</h3>
      <div class="evidence-list">
        ${
          (evidence.preferred || []).length
            ? evidence.preferred.map(evidenceSummaryItem).join("")
            : `<article class="evidence-item"><strong>暂无命中</strong><p>当前优选池没有龙虎榜或融资融券外部证据命中。</p></article>`
        }
      </div>
    </div>
    <div class="drawer-section">
      <h3>样例</h3>
      <div class="evidence-list">
        ${(evidence.preview || []).map(evidenceSummaryItem).join("")}
      </div>
    </div>
  `;
  backdrop.hidden = false;
  requestAnimationFrame(() => {
    drawer.classList.add("is-open");
    backdrop.classList.add("is-open");
  });
}

function evidenceSummaryItem(item) {
  return `
    <article class="evidence-item compact">
      <strong>${esc(item.code)} ${esc((item.tags || []).join(" / "))}</strong>
      <p>${esc(item.brief)}</p>
    </article>
  `;
}

function openDrawer(code) {
  const stock = DATA.preferred.find((item) => item.code === code);
  if (!stock) return;
  const drawer = document.getElementById("detailDrawer");
  const backdrop = document.getElementById("drawerBackdrop");
  drawer.innerHTML = `
    <div class="drawer-head">
      <div>
        <p class="eyebrow">异常波动原因</p>
        <h2>${esc(stock.name)} ${esc(stock.code)}</h2>
        <p class="subtle">${esc(entrySourceLabel(stock))}，入池后涨幅 ${pct(stock.gainAfterEntry)}。</p>
      </div>
      <button class="close-btn" data-close-drawer aria-label="关闭">×</button>
    </div>
    <div class="reason-list">${stock.anomalies.map(reasonItem).join("")}</div>
    <div class="drawer-section">
      <h3>模型决策摘要</h3>
      <p class="subtle">${esc(stock.reason)}</p>
    </div>
    <div class="drawer-section">
      <h3>公开证据</h3>
      <div class="evidence-list">${formatEvidence(stock)}</div>
    </div>
  `;
  backdrop.hidden = false;
  requestAnimationFrame(() => {
    drawer.classList.add("is-open");
    backdrop.classList.add("is-open");
  });
}

function closeDrawer() {
  const drawer = document.getElementById("detailDrawer");
  const backdrop = document.getElementById("drawerBackdrop");
  drawer.classList.remove("is-open");
  backdrop.classList.remove("is-open");
  window.setTimeout(() => {
    backdrop.hidden = true;
  }, 180);
}

function syncSearchValue() {
  const global = document.getElementById("globalSearch");
  if (global && global.value !== state.query) global.value = state.query;
}

function initSearch() {
  const global = document.getElementById("globalSearch");
  if (!global) return;
  global.value = state.query;
  global.addEventListener("input", (event) => {
    state.query = event.target.value;
    if (page === "pool") renderPool();
    if (page === "information") renderInformation();
    if (page === "daily-news") renderDailyNews();
    if (page === "close-brief") renderCloseBrief();
    if (page === "history") renderHistory();
    if (page === "trade-records") renderTradeRecords();
    if (page === "dashboard") {
      document.querySelectorAll("[data-stock-row]").forEach((row) => {
        row.hidden = !row.dataset.search.toLowerCase().includes(state.query.toLowerCase());
      });
    }
  });
  global.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      const query = encodeURIComponent(global.value.trim());
      window.location.href = appUrl(`./pool.html?q=${query}`);
    }
  });
}

function initNavigation() {
  document.querySelectorAll("[data-nav]").forEach((link) => {
    const navPage = page === "trade-records" ? "trading" : page;
    link.classList.toggle("is-active", link.dataset.nav === navPage);
  });
  const status = document.getElementById("dataStatus");
  if (status) {
    status.outerHTML = renderDataStatus();
  }
  renderSidebarMarket();
}

function renderDataStatus() {
  const quality = DATA.meta.quoteQuality || {};
  const expected = quality.expectedDate || DATA.meta.planDate || DATA.meta.latestDataDate || DATA.meta.latestSnapshotDate || "";
  const staleFromPreferred = DATA.preferred.filter((row) => quoteIsoDate(row.latestQuoteTime) && quoteIsoDate(row.latestQuoteTime) !== expected).length;
  const staleCount = Number(quality.preferredStaleCount ?? staleFromPreferred);
  const freshRatio = Number(quality.freshRatio ?? (staleCount > 0 ? 0 : 1));
  const isStale = staleCount > 0 || freshRatio < 0.95;
  const label = isStale ? `STALE ${staleCount || ""}`.trim() : "OK";
  const quote = DATA.meta.latestQuoteTime ? quoteClock(DATA.meta.latestQuoteTime) : "--:--";
  const day = expected ? expected.slice(5) : DATA.meta.latestSnapshotDate || "-";
  const generated = DATA.meta.generatedAt ? DATA.meta.generatedAt.slice(5) : "-";
  return `
    <span id="dataStatus" class="status-pill ${isStale ? "is-stale" : "is-ok"}" title="快照 ${esc(DATA.meta.latestSnapshotDate || "-")} / 报价 ${esc(DATA.meta.latestQuoteTime || "-")} / 生成 ${esc(DATA.meta.generatedAt || "-")}">
      <span class="status-light" aria-hidden="true"></span>
      <strong>${esc(label)}</strong>
      <b>${esc(day)}</b>
      <b>${esc(quote)}</b>
      <em>${esc(generated)}</em>
    </span>
  `;
}

function renderSidebarMarket() {
  const sideNav = document.querySelector(".side-nav");
  if (!sideNav) return;
  sideNav.querySelector(".sidebar-market")?.remove();
  const market = DATA.marketIndices || {};
  const items = market.items || [];
  const block = document.createElement("div");
  block.className = "sidebar-market";
  block.innerHTML = `
    <div class="sidebar-market-title">今日市场</div>
    <div class="sidebar-market-amount">
      <span>实时成交额</span>
      <strong>${moneyYi(market.marketAmountYuan)}</strong>
    </div>
    ${items
      .map(
        (item) => `
          <div class="sidebar-index-row">
            <span>${esc(item.name)}</span>
            <strong>${num(item.price)}</strong>
            <em class="${gainClass(item.pctChange)}">${pct(item.pctChange)}</em>
          </div>
        `
      )
      .join("")}
  `;
  sideNav.appendChild(block);
}

function wireGlobalEvents() {
  document.addEventListener("click", (event) => {
    const internalLink = event.target.closest('a[href^="./"]');
    if (internalLink) {
      internalLink.setAttribute("href", appUrl(internalLink.getAttribute("href")));
    }

    const drawerButton = event.target.closest("[data-drawer]");
    if (drawerButton) openDrawer(drawerButton.dataset.drawer);

    if (event.target.closest("[data-evidence-drawer]")) {
      openEvidenceDrawer();
    }

    if (event.target.closest("[data-close-drawer]") || event.target.id === "drawerBackdrop") {
      closeDrawer();
    }
  });

  document.addEventListener("keydown", (event) => {
    if ((event.key === "Enter" || event.key === " ") && event.target.closest("[data-evidence-drawer]")) {
      event.preventDefault();
      openEvidenceDrawer();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDrawer();
  });
}

function boot() {
  normalizeAddressBar();
  initNavigation();
  wireGlobalEvents();

  if (page === "dashboard") renderDashboard();
  if (page === "pool") renderPool();
  if (page === "stock") renderStock(new URLSearchParams(window.location.search).get("code"));
  if (page === "anomalies") renderAnomalies();
  if (page === "agents") renderAgents();
  if (page === "information") renderInformation();
  if (page === "daily-news") renderDailyNews();
  if (page === "close-brief") renderCloseBrief();
  if (page === "history") renderHistory();
  if (page === "trading") renderTrading();
  if (page === "trade-records") renderTradeRecords();

  initSearch();
  versionInternalLinks();
}

boot();
