"use strict";

const REFRESH_MS = 30000; // 与服务端缓存 TTL 对齐
const NEWS_REFRESH_MS = 120000;
const DAILY_CHART_CACHE_MS = 10 * 60 * 1000;
const NEWS_ENABLED_KEY = "quant.news.enabled";
const NEWS_CACHE_KEY = "quant.news.cache";
const LEFT_COLLAPSED_KEY = "quant.layout.leftCollapsed";
const NEWS_COLLAPSED_KEY = "quant.layout.newsCollapsed";
const AUX_TAB_KEY = "quant.layout.auxTab";
const SCREEN_HISTORY_LIMIT = 100;
let selected = null;
let selectedName = "";
let chartPeriod = "minute";
let indicatorMode = "rsi";
let chart = null;
let backtestChart = null;
let chartRequestId = 0;
let chartAbort = null;
const chartKlineCache = new Map();
let newsItems = [];
let newsFilter = "全部";
let newsAbort = null;
let newsEnabled = readNewsEnabled();
let quoteRows = [];
let quotesLoading = false;
let backtestBatch = null;
let chartTradeMarkers = [];
let leftCollapsed = readStoredBoolean(LEFT_COLLAPSED_KEY, false);
let newsCollapsed = readStoredBoolean(NEWS_COLLAPSED_KEY, false);
let activeAuxTarget = readStoredText(AUX_TAB_KEY, "signals-pane");

function readStoredBoolean(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    if (value === "1") return true;
    if (value === "0") return false;
  } catch (e) {
    /* 忽略 */
  }
  return fallback;
}

function saveStoredBoolean(key, value) {
  try {
    localStorage.setItem(key, value ? "1" : "0");
  } catch (e) {
    /* 忽略 */
  }
}

function readStoredText(key, fallback) {
  try {
    return localStorage.getItem(key) || fallback;
  } catch (e) {
    return fallback;
  }
}

function saveStoredText(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (e) {
    /* 忽略 */
  }
}

function readNewsEnabled() {
  try {
    return localStorage.getItem(NEWS_ENABLED_KEY) !== "0";
  } catch (e) {
    return true;
  }
}

function saveNewsEnabled(on) {
  try {
    localStorage.setItem(NEWS_ENABLED_KEY, on ? "1" : "0");
  } catch (e) {
    /* 忽略 */
  }
}

function readLocalNewsCache() {
  try {
    const raw = localStorage.getItem(NEWS_CACHE_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw);
    return data && Array.isArray(data.items) ? data : null;
  } catch (e) {
    return null;
  }
}

function saveLocalNewsCache(data) {
  if (!data?.items?.length) return;
  try {
    localStorage.setItem(
      NEWS_CACHE_KEY,
      JSON.stringify({ ...data, cached_at: data.cached_at || new Date().toISOString().slice(0, 19).replace("T", " ") })
    );
  } catch (e) {
    /* 忽略 */
  }
}

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function fmtClass(v) {
  if (v > 0) return "up";
  if (v < 0) return "down";
  return "flat";
}

function pct(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "-";
  const n = Number(v) * 100;
  return `${n > 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function axisPct(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "";
  return `${(n * 100).toFixed(1)}%`;
}

function dateOnly(value) {
  if (!value) return "-";
  return String(value).slice(0, 10);
}

function sameDateAxisPoint(values, time) {
  const raw = String(time || "");
  if (!raw) return null;
  let index = values.indexOf(raw);
  if (index >= 0) return { index, value: raw };
  const day = dateOnly(raw);
  index = values.findIndex((value) => dateOnly(value) === day);
  return index >= 0 ? { index, value: values[index] } : null;
}

function visibleTradeCount(values, trades) {
  return (trades || []).filter(
    (trade) => sameDateAxisPoint(values, trade.entry_time) || sameDateAxisPoint(values, trade.exit_time)
  ).length;
}

function setChartMode(period) {
  chartPeriod = period;
  document.querySelectorAll(".chart-mode button").forEach((el) =>
    el.classList.toggle("active", (el.dataset.period || "minute") === period)
  );
}

function setIndicatorMode(mode) {
  indicatorMode = mode === "kdj" ? "kdj" : "rsi";
  document.querySelectorAll(".indicator-mode button").forEach((el) =>
    el.classList.toggle("active", (el.dataset.indicator || "rsi") === indicatorMode)
  );
}

function syncIndicatorButtons(hasKdj) {
  if (!hasKdj && indicatorMode === "kdj") indicatorMode = "rsi";
  document.querySelectorAll(".indicator-mode button").forEach((el) => {
    const isKdj = el.dataset.indicator === "kdj";
    el.disabled = isKdj && !hasKdj;
    el.classList.toggle("active", (el.dataset.indicator || "rsi") === indicatorMode);
  });
}

function setChartTradeMarkers(trades) {
  chartTradeMarkers = (trades || []).filter((trade) => trade && trade.entry_time && trade.entry_price);
}

async function getJSON(url, options = {}) {
  const r = await fetch(url, options);
  if (!r.ok) throw new Error(url + " " + r.status);
  return r.json();
}

function mergeWatchlistRows(items) {
  const existing = new Map(quoteRows.map((row) => [row.code, row]));
  return (items || []).map((item) => {
    const code = String(item.code || "").padStart(6, "0");
    const prev = existing.get(code);
    if (prev && !prev.pending) return { ...prev, name: item.name || prev.name || code };
    return { code, name: item.name || code, pending: true };
  });
}

function upsertQuotePlaceholder(item, text = "行情刷新中") {
  const code = String(item.code || "").padStart(6, "0");
  const rows = quoteRows.filter((row) => row.code !== code);
  rows.splice(0, 0, { code, name: item.name || code, pending: true, pending_text: text });
  renderQuotes(rows);
}

function renderQuotes(rows) {
  const tbody = document.querySelector("#quotes tbody");
  quoteRows = rows || [];
  tbody.innerHTML = "";
  setText("watch-count", `${quoteRows.length} 自选`);
  setText("watch-count-card", quoteRows.length);
  setText("watch-count-rail", quoteRows.length);
  for (const row of quoteRows) {
    const tr = document.createElement("tr");
    tr.dataset.code = row.code;
    tr.dataset.name = row.name;
    if (row.code === selected) tr.classList.add("active");
    if (row.pending) {
      tr.innerHTML = `<td>${row.name}</td><td colspan="5" class="flat">${row.pending_text || "行情刷新中"}</td>`;
    } else if (row.error) {
      tr.innerHTML = `<td>${row.name}</td><td colspan="5" class="flat">取数失败</td>`;
    } else {
      const sig = (row.signals || []).length
        ? row.signals.map((s) => `<span class="badge">${s}</span>`).join("")
        : "";
      if ((row.signals || []).length) tr.classList.add("has-signal");
      const chg = `${row.change_pct > 0 ? "+" : ""}${row.change_pct}%`;
      tr.innerHTML =
        `<td>${row.name}</td>` +
        `<td>${row.price}</td>` +
        `<td class="${fmtClass(row.change_pct)}">${chg}</td>` +
        `<td>${row.vol_ratio ?? "-"}</td>` +
        `<td>${row.rsi ?? "-"}</td>` +
        `<td>${sig}</td>`;
    }
    tr.addEventListener("click", () => selectStock(row.code, row.name));
    tbody.appendChild(tr);
  }
}

function renderSignals(rows) {
  const ul = document.querySelector("#signals");
  ul.innerHTML = "";
  setText("signal-count", `${rows.length} 信号`);
  setText("signal-count-card", rows.length);
  if (!rows.length) {
    ul.innerHTML = '<li class="empty">暂无触发(运行 run_monitor.py 会持续写入)</li>';
    return;
  }
  const arrow = { long: "↑看多", short: "↓看空" };
  for (const s of rows) {
    const li = document.createElement("li");
    const t = String(s.time).slice(5, 16);
    li.innerHTML = `<span class="t">${t}</span>${s.name} ${s.rule} <b class="${
      s.direction === "long" ? "up" : "down"
    }">${arrow[s.direction] || s.direction}</b>`;
    ul.appendChild(li);
  }
}

function renderScreenHistory(rows) {
  const ul = document.querySelector("#screen-history");
  ul.innerHTML = "";
  setText("history-count-card", rows.length);
  if (!rows.length) {
    ul.innerHTML = '<li class="empty">暂无选股历史</li>';
    return;
  }
  for (const row of rows) {
    const li = document.createElement("li");
    const selected = row.selected || [];
    const state = row.aborted ? "停止" : row.timed_out ? "超时" : row.complete ? "完成" : "未完";
    const failed = row.failed ? ` · 失败 ${row.failed}` : "";
    const done = row.done && row.universe ? `${row.done}/${row.universe}` : row.universe || "-";
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    const main = document.createElement("span");
    const sub = document.createElement("span");
    const stocks = document.createElement("div");
    const actions = document.createElement("div");
    main.className = "hist-main";
    sub.className = "hist-sub";
    stocks.className = "hist-stocks";
    actions.className = "hist-actions";
    main.textContent = `${String(row.time || "").slice(5, 16)} · ${row.count || 0}只`;
    sub.textContent = `${row.strategy || "-"} / ${row.scope || "-"} · ${state} · ${done}${failed}`;
    summary.append(main, sub);
    const backtestBtn = document.createElement("button");
    backtestBtn.type = "button";
    backtestBtn.className = "hist-backtest-btn";
    backtestBtn.textContent = "载入回测";
    backtestBtn.disabled = !selected.length;
    backtestBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      setBacktestBatch(row);
    });
    actions.appendChild(backtestBtn);
    if (selected.length) {
      for (const s of selected) {
        const tag = document.createElement("span");
        const code = document.createElement("em");
        tag.append(s.name || s.code || "");
        code.textContent = s.code || "";
        tag.append(code);
        stocks.appendChild(tag);
      }
    } else {
      const empty = document.createElement("span");
      empty.className = "flat";
      empty.textContent = "无入选";
      stocks.appendChild(empty);
    }
    details.append(summary, actions, stocks);
    li.appendChild(details);
    ul.appendChild(li);
  }
}

function clearBacktestBatch() {
  backtestBatch = null;
}

function setBacktestBatch(row) {
  const items = (row.selected || []).filter((item) => item.code);
  if (!items.length) {
    setText("backtest-msg", "这次选股没有入选股票");
    return;
  }
  backtestBatch = {
    time: row.time || "",
    strategy: row.strategy || "",
    scope: row.scope || "",
    stocks: items.map((item) => ({ code: String(item.code).padStart(6, "0"), name: item.name || item.code })),
  };
  backtestCode.value = `批量 ${backtestBatch.stocks.length} 只`;
  setText(
    "backtest-msg",
    `已载入 ${String(backtestBatch.time).slice(5, 16)} 选股结果 · ${backtestBatch.stocks.length}只`
  );
}

function renderNewsSources(sources) {
  const wrap = document.getElementById("news-sources");
  if (!wrap) return;
  wrap.innerHTML = "";
  for (const src of sources || []) {
    const chip = document.createElement("span");
    chip.className = `source-chip ${src.ok ? "ok" : "failed"}`;
    chip.textContent = `${src.label} ${src.ok ? src.count : "失败"}`;
    wrap.appendChild(chip);
  }
}

function newsMatches(item) {
  if (newsFilter === "全部") return true;
  const tags = item.tags || [];
  const text = `${item.title || ""} ${item.summary || ""}`.toUpperCase();
  return tags.includes(newsFilter) || text.includes(newsFilter.toUpperCase());
}

function renderNewsList() {
  const list = document.getElementById("news-list");
  if (!list) return;
  list.innerHTML = "";
  const visible = newsItems.filter(newsMatches);
  setText("news-count", visible.length);
  setText("news-count-rail", visible.length);
  if (!visible.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = newsItems.length
      ? "当前筛选下暂无资讯"
      : newsEnabled
        ? "暂无今日资讯"
        : "资讯已关闭，暂无本地缓存";
    list.appendChild(li);
    return;
  }
  for (const item of visible) {
    const li = document.createElement("li");
    li.className = "news-item";

    const meta = document.createElement("div");
    meta.className = "news-meta-row";
    const time = document.createElement("span");
    time.className = "news-time";
    time.textContent = item.time || String(item.published_at || "").slice(11, 16) || "--:--";
    const source = document.createElement("span");
    source.className = "news-source";
    source.textContent = item.source || "-";
    const sentiment = document.createElement("span");
    sentiment.className = `news-sentiment ${item.sentiment_direction || "neutral"}`;
    sentiment.textContent = item.sentiment || "中性";
    meta.append(time, source, sentiment);

    const title = document.createElement("div");
    title.className = "news-title";
    title.textContent = item.title || "未命名资讯";

    const summary = document.createElement("p");
    summary.className = "news-summary";
    summary.textContent = item.summary || "";

    const stocks = document.createElement("div");
    stocks.className = "news-stocks";
    for (const stock of item.related_stocks || []) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `news-stock ${stock.sentiment_direction || "neutral"}${stock.in_watchlist ? " watch" : ""}`;
      const conf = stock.confidence === undefined ? "" : ` · 置信度 ${Math.round((stock.confidence || 0) * 100)}%`;
      btn.title = `${stock.reason || "相关股票"}${conf}`;
      btn.innerHTML =
        `<span>${stock.sentiment || "中性"}</span>` +
        `<b>${stock.name || stock.code}</b>` +
        `<em>${stock.code || ""}</em>` +
        `${stock.in_watchlist ? "<i>自选</i>" : ""}`;
      btn.addEventListener("click", () => {
        selectStock(stock.code, stock.name || stock.code);
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
      stocks.appendChild(btn);
    }

    const foot = document.createElement("div");
    foot.className = "news-foot";
    const tags = document.createElement("div");
    tags.className = "news-tags";
    for (const tag of item.tags || []) {
      const tagEl = document.createElement("span");
      tagEl.textContent = tag;
      tags.appendChild(tagEl);
    }
    foot.appendChild(tags);
    if (item.url) {
      const link = document.createElement("a");
      link.className = "news-link";
      link.href = item.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "原文";
      foot.appendChild(link);
    }

    li.append(meta, title);
    if (summary.textContent && summary.textContent !== title.textContent) li.appendChild(summary);
    if (stocks.children.length) li.appendChild(stocks);
    li.appendChild(foot);
    list.appendChild(li);
  }
}

function renderNews(data) {
  newsItems = data.items || [];
  saveLocalNewsCache(data);
  const total = newsItems.length;
  setText("news-count-top", `${total} 资讯`);
  setText("news-count-rail", total);
  const updated = String(data.cached_at || data.updated_at || "").slice(11, 16);
  const label = data.from_disk_cache ? "本地缓存" : data.fallback_latest ? "最新可用" : data.date || "今日";
  const errors = (data.errors || []).length;
  const suffix = `${errors ? ` · ${errors}源失败` : ""}${!newsEnabled ? " · 实时已关闭" : ""}`;
  setText("news-meta", `${label} · ${updated || "-"}${suffix}`);
  renderNewsSources(data.sources || []);
  renderNewsList();
}

function tradeMarkerSeries(k, colors) {
  if (k.period !== "daily" || !chartTradeMarkers.length) return [];
  const entryPoints = [];
  const exitPoints = [];
  for (const trade of chartTradeMarkers) {
    const entryAxis = sameDateAxisPoint(k.datetime || [], trade.entry_time);
    const exitAxis = sameDateAxisPoint(k.datetime || [], trade.exit_time);
    const entryPrice = Number(trade.entry_price);
    const exitPrice = Number(trade.exit_price);
    const isShort = trade.direction === "short";
    if (entryAxis && Number.isFinite(entryPrice)) {
      entryPoints.push({
        name: isShort ? "开空" : "买入",
        value: [entryAxis.index, entryPrice],
        trade,
        axis_time: entryAxis.value,
        itemStyle: {
          color: isShort ? colors.green : colors.red,
          borderColor: colors.text,
          borderWidth: 1,
        },
        label: {
          show: true,
          formatter: isShort ? "空" : "买",
          color: "#08090a",
          fontWeight: 800,
          fontSize: 10,
        },
      });
    }
    if (exitAxis && Number.isFinite(exitPrice)) {
      exitPoints.push({
        name: isShort ? "平空" : "卖出",
        value: [exitAxis.index, exitPrice],
        trade,
        axis_time: exitAxis.value,
        itemStyle: {
          color: isShort ? colors.cyan : colors.green,
          borderColor: colors.text,
          borderWidth: 1,
        },
        label: {
          show: true,
          formatter: isShort ? "平" : "卖",
          color: "#08090a",
          fontWeight: 800,
          fontSize: 10,
        },
      });
    }
  }
  const tooltip = (params) => {
    const t = params.data?.trade || {};
    return [
      `${params.name || "交易点"} ${dateOnly(params.data?.axis_time)}`,
      `入场 ${dateOnly(t.entry_time)} @ ${Number(t.entry_price || 0).toFixed(2)}`,
      `出场 ${dateOnly(t.exit_time)} @ ${Number(t.exit_price || 0).toFixed(2)}`,
      `收益 ${pct(t.return_pct)}`,
    ].join("<br/>");
  };
  const series = [];
  if (entryPoints.length) {
    series.push({
      name: "回测入场",
      type: "scatter",
      data: entryPoints,
      xAxisIndex: 0,
      yAxisIndex: 0,
      symbol: "pin",
      symbolSize: 36,
      z: 12,
      tooltip: { formatter: tooltip },
    });
  }
  if (exitPoints.length) {
    series.push({
      name: "回测出场",
      type: "scatter",
      data: exitPoints,
      xAxisIndex: 0,
      yAxisIndex: 0,
      symbol: "triangle",
      symbolSize: 24,
      z: 13,
      tooltip: { formatter: tooltip },
    });
  }
  return series;
}

function renderChart(k) {
  if (!chart) chart = echarts.init(document.getElementById("chart"), "dark");
  const brass = cssVar("--brass", "#c59b5a");
  const cyan = cssVar("--cyan", "#68d8d6");
  const red = cssVar("--red", "#f04f5f");
  const green = cssVar("--green", "#2fca88");
  const text = cssVar("--text", "#e5e1d8");
  const muted = cssVar("--muted", "#8f9797");
  const line = cssVar("--line", "#263033");
  const extraLines = [];
  if (Array.isArray(k.zx_short)) {
    extraLines.push({
      name: "知行短期",
      type: "line",
      data: k.zx_short,
      smooth: true,
      showSymbol: false,
      lineStyle: { width: 1.7, color: brass },
    });
  }
  if (Array.isArray(k.zx_bull)) {
    extraLines.push({
      name: "知行多空",
      type: "line",
      data: k.zx_bull,
      smooth: true,
      showSymbol: false,
      lineStyle: { width: 1.8, color: cyan },
    });
  }
  const hasKdj =
    k.period === "daily" &&
    Array.isArray(k.kdj_k) &&
    Array.isArray(k.kdj_d) &&
    Array.isArray(k.kdj_j);
  syncIndicatorButtons(hasKdj);
  const activeIndicator = indicatorMode === "kdj" && hasKdj ? "kdj" : "rsi";
  const xLabel = (value) => (k.period === "daily" ? String(value).slice(0, 10) : String(value));
  const xAxisIndexes = [0, 1, 2];
  const grids = [
    { left: 58, right: 22, top: 24, height: "49%" },
    { left: 58, right: 22, top: "57%", height: "12%" },
    { left: 58, right: 22, top: "72%", height: "13%" },
  ];
  const xAxes = [
    { type: "category", data: k.datetime, gridIndex: 0, axisLabel: { show: false }, scale: true, axisLine: { lineStyle: { color: line } } },
    { type: "category", data: k.datetime, gridIndex: 1, axisLabel: { show: false }, scale: true, axisLine: { lineStyle: { color: line } } },
    { type: "category", data: k.datetime, gridIndex: 2, axisLabel: { color: muted, fontSize: 11, formatter: xLabel }, scale: true, axisLine: { lineStyle: { color: line } } },
  ];
  const yAxes = [
    { scale: true, gridIndex: 0, axisLabel: { color: muted }, splitLine: { lineStyle: { color: line } } },
    { scale: true, gridIndex: 1, name: "量", nameTextStyle: { color: muted }, axisLabel: { color: muted }, splitNumber: 2, splitLine: { show: false } },
    {
      scale: true,
      gridIndex: 2,
      name: activeIndicator === "kdj" ? "KDJ" : "RSI",
      nameTextStyle: { color: muted },
      axisLabel: { color: muted },
      max: activeIndicator === "rsi" ? 100 : undefined,
      min: activeIndicator === "rsi" ? 0 : undefined,
      splitNumber: 2,
      splitLine: { lineStyle: { color: line } },
    },
  ];
  const indicatorSeries =
    activeIndicator === "kdj"
      ? [
          { name: "K", type: "line", data: k.kdj_k, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: { width: 1, color: "#f0c36a" } },
          { name: "D", type: "line", data: k.kdj_d, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: { width: 1, color: "#68d8d6" } },
          { name: "J", type: "line", data: k.kdj_j, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: { width: 1, color: "#d39d6a" } },
        ]
      : [
          { name: "RSI", type: "line", data: k.rsi, xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, lineStyle: { width: 1, color: "#d39d6a" } },
        ];
  const markerSeries = tradeMarkerSeries(k, { brass, cyan, green, red, text });
  const tooltipBase = {
    backgroundColor: "rgba(8, 12, 13, 0.94)",
    borderColor: "rgba(85, 214, 210, 0.28)",
    borderWidth: 1,
    textStyle: { color: text, fontSize: 12 },
    extraCssText: "box-shadow:0 14px 34px rgba(0,0,0,.36);border-radius:8px;",
    confine: true,
  };
  const opt = {
    backgroundColor: "transparent",
    animation: false,
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    tooltip: { ...tooltipBase, trigger: "axis", axisPointer: { type: "cross" } },
    legend: {
      top: 0,
      right: 120,
      selected: { MA5: false, MA20: false },
      textStyle: { color: muted, fontSize: 11 },
      itemWidth: 14,
      itemHeight: 8,
    },
    grid: grids,
    xAxis: xAxes,
    yAxis: yAxes,
    dataZoom: [
      { type: "inside", xAxisIndex: xAxisIndexes, start: 0, end: 100 },
      {
        type: "slider",
        xAxisIndex: xAxisIndexes,
        bottom: 8,
        height: 52,
        start: 0,
        end: 100,
        showDetail: true,
        showDataShadow: true,
        brushSelect: true,
        handleSize: "120%",
        textStyle: { color: muted, fontSize: 11 },
        borderColor: line,
        fillerColor: "rgba(85, 214, 210, 0.18)",
        dataBackground: {
          lineStyle: { color: cyan },
          areaStyle: { color: "rgba(85, 214, 210, 0.08)" },
        },
        selectedDataBackground: {
          lineStyle: { color: brass },
          areaStyle: { color: "rgba(210, 165, 82, 0.18)" },
        },
        labelFormatter: (_value, valueStr) => xLabel(valueStr),
      },
    ],
    series: [
      {
        name: "K线", type: "candlestick", data: k.ohlc,
        itemStyle: { color: red, color0: green, borderColor: red, borderColor0: green },
      },
      { name: "MA5", type: "line", data: k.ma5, smooth: true, showSymbol: false, lineStyle: { width: 1, color: "#e0c16b" } },
      { name: "MA20", type: "line", data: k.ma20, smooth: true, showSymbol: false, lineStyle: { width: 1, color: "#83a6a7" } },
      ...extraLines,
      { name: "成交量", type: "bar", data: k.volume, xAxisIndex: 1, yAxisIndex: 1, itemStyle: { color: "rgba(85, 214, 210, 0.58)" }, barMaxWidth: 10 },
      ...indicatorSeries,
      ...markerSeries,
    ],
  };
  chart.setOption(opt, true);
  chart.resize();
}

function setBacktestHeaders(labels) {
  const head = document.querySelector("#backtest-trades thead tr");
  if (!head) return;
  head.innerHTML = labels.map((label) => `<th>${label}</th>`).join("");
}

function renderBacktest(result) {
  const stats = result.stats || {};
  setBacktestHeaders(["信号", "入场", "方向", "出场", "收益"]);
  setText("bt-trades", stats.trades ?? 0);
  setText("bt-win", pct(stats.win_rate));
  setText("bt-avg", pct(stats.avg_return));
  setText("bt-total", pct(stats.total_return));
  setText("bt-dd", pct(stats.max_drawdown));
  const entryText = result.execution?.entry ? ` · 入场:${result.execution.entry}` : "";
  const title = `${result.name || result.code}(${result.code}) · ${result.rule_label || result.rule}${entryText}`;
  setText("backtest-msg", title);
  const msgEl = document.getElementById("backtest-msg");
  if (msgEl) msgEl.title = title;

  const equityEl = document.getElementById("backtest-equity");
  if (equityEl) {
    if (!backtestChart) backtestChart = echarts.init(equityEl, "dark");
    const curve = result.equity_curve || [];
    const muted = cssVar("--muted", "#8f9797");
    const line = cssVar("--line", "#263033");
    const cyan = cssVar("--cyan", "#68d8d6");
    const brass = cssVar("--brass", "#c59b5a");
    const text = cssVar("--text", "#e5e1d8");
    backtestChart.setOption(
      {
        backgroundColor: "transparent",
        animation: false,
        tooltip: {
          trigger: "axis",
          valueFormatter: (v) => axisPct(Number(v) - 1),
          backgroundColor: "rgba(8, 12, 13, 0.94)",
          borderColor: "rgba(85, 214, 210, 0.28)",
          textStyle: { color: text, fontSize: 12 },
          extraCssText: "box-shadow:0 14px 34px rgba(0,0,0,.36);border-radius:8px;",
          confine: true,
        },
        grid: { left: 44, right: 12, top: 16, bottom: 24 },
        xAxis: {
          type: "category",
          data: curve.map((p) => String(p.time).slice(0, 10)),
          axisLabel: { color: muted, fontSize: 10 },
          axisLine: { lineStyle: { color: line } },
        },
        yAxis: {
          type: "value",
          scale: true,
          axisLabel: { color: muted, formatter: (v) => axisPct(Number(v) - 1) },
          splitLine: { lineStyle: { color: line } },
        },
        series: [
          {
            name: "累计收益",
            type: "line",
            data: curve.map((p) => Number(p.equity)),
            showSymbol: false,
            areaStyle: { color: "rgba(85, 214, 210, 0.08)" },
            lineStyle: { width: 2, color: curve.length ? cyan : brass },
          },
        ],
      },
      true
    );
    backtestChart.resize();
  }

  const tbody = document.querySelector("#backtest-trades tbody");
  if (!tbody) return;
  tbody.innerHTML = "";
  const trades = result.trades || [];
  if (!trades.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="flat">无触发交易</td></tr>';
    return;
  }
  for (const t of trades.slice().reverse().slice(0, 10)) {
    const tr = document.createElement("tr");
    const cls = t.return_pct > 0 ? "up" : t.return_pct < 0 ? "down" : "flat";
    tr.innerHTML =
      `<td>${dateOnly(t.signal_time)}</td>` +
      `<td>${dateOnly(t.entry_time)}</td>` +
      `<td>${t.direction === "long" ? "多" : "空"}</td>` +
      `<td>${dateOnly(t.exit_time)}</td>` +
      `<td class="${cls}">${pct(t.return_pct)}</td>`;
    tbody.appendChild(tr);
  }
  showBacktestChart(result, { scroll: false });
}

function renderBacktestBatch(result) {
  const summary = result.summary || {};
  const rows = result.results || [];
  const errors = result.errors || [];
  const entryText = result.execution?.entry ? ` · 入场:${result.execution.entry}` : "";
  const title = `批量回测 ${summary.ok || 0}/${summary.stocks || 0}只 · ${
    result.rule_label || result.rule
  }${entryText}`;
  setText("backtest-msg", title);
  const msgEl = document.getElementById("backtest-msg");
  if (msgEl) msgEl.title = title;
  setText("bt-trades", `${summary.ok || 0}/${summary.stocks || 0}股`);
  setText("bt-win", pct(summary.win_rate));
  setText("bt-avg", pct(summary.avg_return));
  setText("bt-total", pct(summary.avg_total_return));
  setText("bt-dd", errors.length ? `${errors.length}失败` : pct(summary.max_drawdown));

  const equityEl = document.getElementById("backtest-equity");
  if (equityEl) {
    if (!backtestChart) backtestChart = echarts.init(equityEl, "dark");
    const values = rows.map((row) => Number(row.stats?.total_return || 0));
    const minValue = Math.min(0, ...values);
    const maxValue = Math.max(0, ...values);
    const span = Math.max(maxValue - minValue, 0.02);
    const yMin = minValue - span * 0.18;
    const yMax = maxValue + span * 0.18;
    const muted = cssVar("--muted", "#8f9797");
    const line = cssVar("--line", "#263033");
    const red = cssVar("--red", "#f04f5f");
    const green = cssVar("--green", "#2fca88");
    const text = cssVar("--text", "#e5e1d8");
    backtestChart.setOption(
      {
        backgroundColor: "transparent",
        animation: false,
        tooltip: {
          trigger: "axis",
          valueFormatter: (v) => axisPct(v),
          backgroundColor: "rgba(8, 12, 13, 0.94)",
          borderColor: "rgba(85, 214, 210, 0.28)",
          textStyle: { color: text, fontSize: 12 },
          extraCssText: "box-shadow:0 14px 34px rgba(0,0,0,.36);border-radius:8px;",
          confine: true,
        },
        grid: { left: 44, right: 12, top: 16, bottom: 42 },
        xAxis: {
          type: "category",
          data: rows.map((row) => row.name || row.code),
          axisLabel: { color: muted, fontSize: 10, rotate: rows.length > 8 ? 35 : 0 },
          axisLine: { lineStyle: { color: line } },
        },
        yAxis: {
          type: "value",
          min: yMin,
          max: yMax,
          axisLabel: { color: muted, formatter: axisPct },
          splitLine: { lineStyle: { color: line } },
        },
        series: [
          {
            name: "单股累计",
            type: "bar",
            data: values,
            itemStyle: { color: (p) => (Number(p.value) >= 0 ? red : green) },
            barMaxWidth: 18,
          },
        ],
      },
      true
    );
    backtestChart.resize();
  }

  const tbody = document.querySelector("#backtest-trades tbody");
  if (!tbody) return;
  setBacktestHeaders(["股票", "交易", "胜率", "均值", "累计"]);
  tbody.innerHTML = "";
  if (!rows.length && !errors.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="flat">无批量回测结果</td></tr>';
    return;
  }
  for (const row of rows) {
    const stats = row.stats || {};
    const cls = stats.total_return > 0 ? "up" : stats.total_return < 0 ? "down" : "flat";
    const tr = document.createElement("tr");
    tr.className = "bt-stock-row";
    tr.dataset.code = row.code;
    tr.innerHTML =
      `<td>${row.name || row.code}</td>` +
      `<td>${stats.trades || 0}</td>` +
      `<td>${pct(stats.win_rate)}</td>` +
      `<td>${pct(stats.avg_return)}</td>` +
      `<td class="${cls}">${pct(stats.total_return)}</td>`;
    tr.addEventListener("click", () => showBacktestChart(row, { scroll: true }));
    tbody.appendChild(tr);
  }
  for (const err of errors) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${err.name || err.code}</td>` +
      '<td colspan="4" class="flat">回测失败</td>';
    tbody.appendChild(tr);
  }
  if (rows.length) showBacktestChart(rows[0], { scroll: false });
}

function showBacktestChart(row, options = {}) {
  if (!row || !row.code) return;
  selected = row.code;
  selectedName = row.name || row.code;
  setChartTradeMarkers(row.trades || []);
  setChartMode("daily");
  document.querySelectorAll("#quotes tbody tr").forEach((tr) =>
    tr.classList.toggle("active", tr.dataset.code === row.code)
  );
  document.querySelectorAll("#backtest-trades tbody tr").forEach((tr) =>
    tr.classList.toggle("active", tr.dataset.code === row.code)
  );
  loadChart({ background: !options.scroll });
  if (options.scroll) {
    document.querySelector(".chart-panel")?.scrollIntoView({ block: "start", behavior: "smooth" });
  }
}

async function selectStock(code, name) {
  selected = code;
  selectedName = name || code;
  const btCode = document.getElementById("backtest-code");
  clearBacktestBatch();
  setChartTradeMarkers([]);
  if (btCode) btCode.value = code;
  document.querySelectorAll("#quotes tbody tr").forEach((tr) =>
    tr.classList.toggle("active", tr.dataset.code === code)
  );
  loadChart();
  loadBandStock(code, name);
}

// ---- 波段战法 ----

function bandToneClass(tone) {
  return tone === "good" || tone === "ok" || tone === "bad" || tone === "warn" ? `tone-${tone}` : "";
}

async function loadBandMarket() {
  const pill = document.getElementById("band-temp-pill");
  const valueEl = document.getElementById("band-temp-value");
  const levelEl = document.getElementById("band-temp-level");
  try {
    const data = await getJSON("/api/band/market");
    const t = data.temperature;
    if (t) {
      valueEl.textContent = `${t.value}%`;
      levelEl.textContent = t.level;
      levelEl.className = `band-badge ${bandToneClass(t.tone)}`;
      document.getElementById("band-temp-detail").innerHTML =
        `上涨 <b>${t.up}</b> 家 · 下跌 <b>${t.down}</b> 家 · 平盘 <b>${t.flat}</b> 家` +
        ` · 涨停 <b>${t.limit_up}</b> · 跌停 <b>${t.limit_down}</b>`;
      document.getElementById("band-temp-action").textContent = `参考：${t.action}`;
      if (pill) {
        pill.textContent = `温度 ${t.value}% ${t.level}`;
        pill.classList.toggle("hot", t.tone === "good");
        pill.classList.toggle("cold", t.tone === "bad");
        pill.title = `${t.level}：${t.action}`;
      }
      if (t.stat_time) document.getElementById("band-market-time").textContent = `数据 ${t.stat_time}`;
    } else {
      valueEl.textContent = "—";
      levelEl.textContent = "无数据";
      document.getElementById("band-temp-detail").textContent = "涨跌家数暂不可用";
      if (pill) pill.textContent = "温度 —";
    }
    const idx = data.index;
    if (idx && idx.close) {
      document.getElementById("band-index-detail").innerHTML =
        `大盘 上证 <b>${idx.close}</b> · <b>${idx.position}</b>(60日区间 ${idx.pos_pct}%) · ` +
        `连续 <b>${idx.no_new_low_streak}</b> 日未创新低${idx.made_new_low ? "（今日创新低）" : ""} —— ${idx.note}`;
    } else {
      document.getElementById("band-index-detail").textContent = "大盘体检暂不可用";
    }
  } catch (e) {
    if (pill) pill.textContent = "温度 —";
  }
}

async function loadBandStock(code, name) {
  const titleEl = document.getElementById("band-stock-title");
  const verdictEl = document.getElementById("band-stock-verdict");
  const listEl = document.getElementById("band-stock-checks");
  if (!verdictEl || !listEl) return;
  titleEl.textContent = "加载中…";
  verdictEl.innerHTML = "";
  listEl.innerHTML = "";
  try {
    const r = await getJSON(`/api/band/stock?code=${encodeURIComponent(code)}&name=${encodeURIComponent(name || "")}`);
    if (r.code !== String(code).padStart(6, "0")) return;
    titleEl.textContent = `${r.name}(${r.code}) ¥${r.price}`;
    const v = r.verdict || {};
    verdictEl.innerHTML =
      `<span class="band-badge ${bandToneClass(v.tone)}">${v.title || "—"}</span>` +
      (v.note ? `<p>${v.note}</p>` : "");
    listEl.innerHTML = (r.checks || [])
      .map((c) => {
        const cls = c.ok === null || c.ok === undefined ? "na" : c.ok ? "ok" : "ng";
        const mark = cls === "na" ? "·" : cls === "ok" ? "✓" : "✗";
        const sub = [c.detail, c.note].filter(Boolean).join(" · ");
        return (
          `<li class="${cls}"><span class="mark">${mark}</span>` +
          `<span class="label">${c.label}${sub ? `<small>${sub}</small>` : ""}</span>` +
          `<span class="val">${c.value}</span></li>`
        );
      })
      .join("");
  } catch (e) {
    titleEl.textContent = "体检失败";
    verdictEl.innerHTML = `<span class="band-badge tone-bad">加载失败</span><p>${e.message}</p>`;
  }
}

async function loadChart(options = {}) {
  if (!selected) return;
  const code = selected;
  const name = selectedName || code;
  const period = chartPeriod;
  const label = chartPeriod === "daily" ? "日K" : "分钟K";
  const titleEl = document.getElementById("chart-title");
  if (!options.background) titleEl.textContent = `${name}(${code}) ${label} 加载中…`;
  const reqId = ++chartRequestId;
  const cacheKey = `${code}:${period}`;
  const cached = period === "daily" ? chartKlineCache.get(cacheKey) : null;
  if (cached && Date.now() - cached.ts < DAILY_CHART_CACHE_MS) {
    const k = cached.data;
    const visibleTrades = visibleTradeCount(k.datetime || [], chartTradeMarkers);
    const markerText = visibleTrades ? ` · ${visibleTrades} 笔交易` : "";
    titleEl.textContent = `${name}(${code}) ${label}${markerText}`;
    renderChart(k);
    return;
  }
  if (chartAbort) chartAbort.abort();
  chartAbort = new AbortController();
  const signal = chartAbort.signal;
  try {
    const qs = new URLSearchParams({ code, period });
    const k = await getJSON(`/api/kline?${qs.toString()}`, { signal });
    if (reqId !== chartRequestId || code !== selected || period !== chartPeriod) return;
    if (period === "daily") chartKlineCache.set(cacheKey, { ts: Date.now(), data: k });
    const visibleTrades = period === "daily" ? visibleTradeCount(k.datetime || [], chartTradeMarkers) : 0;
    const markerText = visibleTrades ? ` · ${visibleTrades} 笔交易` : "";
    titleEl.textContent = `${name}(${code}) ${label}${markerText}`;
    renderChart(k);
  } catch (e) {
    if (e.name === "AbortError") return;
    if (reqId !== chartRequestId || code !== selected || period !== chartPeriod) return;
    titleEl.textContent = `${name}(${code}) 取K线失败`;
  } finally {
    if (chartAbort && chartAbort.signal === signal) chartAbort = null;
  }
}

async function tick() {
  const jobs = [
    getJSON("/api/watchlist").then((data) => renderQuotes(mergeWatchlistRows(data.watchlist || []))),
    getJSON("/api/signals?limit=50").then(renderSignals),
    getJSON(`/api/screen/history?limit=${SCREEN_HISTORY_LIMIT}`).then((data) =>
      renderScreenHistory(data.history || [])
    ),
  ];
  const results = await Promise.allSettled(jobs);
  const failed = results.filter((r) => r.status === "rejected").length;
  if (selected && !chartAbort) loadChart({ background: true });
  refreshQuotes();
  document.getElementById("status").textContent = failed
    ? `刷新部分失败 ${failed} 项`
    : "更新于 " + new Date().toLocaleTimeString();
}

async function refreshQuotes() {
  if (quotesLoading) return;
  quotesLoading = true;
  try {
    const quotes = await getJSON("/api/quotes");
    renderQuotes(quotes);
  } catch (e) {
    document.getElementById("status").textContent = "行情刷新失败: " + e.message;
  } finally {
    quotesLoading = false;
  }
}

let newsLoading = false;

function syncNewsControls() {
  const btn = document.getElementById("news-refresh");
  const toggle = document.getElementById("news-enabled");
  const panel = document.querySelector(".news-panel");
  if (toggle) toggle.checked = newsEnabled;
  if (panel) panel.classList.toggle("news-disabled", !newsEnabled);
  if (btn) {
    btn.disabled = !newsEnabled || newsLoading;
    btn.textContent = newsLoading ? "刷新中" : "刷新";
  }
}

function syncWorkspaceLayout() {
  const workspace = document.getElementById("workspace");
  const left = document.getElementById("left-sidebar");
  const news = document.getElementById("news-sidebar");
  const leftCollapseBtn = document.getElementById("left-collapse-btn");
  const newsCollapseBtn = document.getElementById("news-collapse-btn");
  const leftExpandBtn = document.getElementById("left-expand-btn");
  const newsExpandBtn = document.getElementById("news-expand-btn");

  workspace?.classList.toggle("left-collapsed", leftCollapsed);
  workspace?.classList.toggle("news-collapsed", newsCollapsed);
  left?.classList.toggle("collapsed", leftCollapsed);
  news?.classList.toggle("collapsed", newsCollapsed);

  if (leftCollapseBtn) leftCollapseBtn.setAttribute("aria-expanded", String(!leftCollapsed));
  if (newsCollapseBtn) newsCollapseBtn.setAttribute("aria-expanded", String(!newsCollapsed));
  if (leftExpandBtn) leftExpandBtn.setAttribute("aria-expanded", String(!leftCollapsed));
  if (newsExpandBtn) newsExpandBtn.setAttribute("aria-expanded", String(!newsCollapsed));

  window.setTimeout(() => {
    if (chart) chart.resize();
    if (backtestChart) backtestChart.resize();
  }, 220);
}

function setLeftCollapsed(collapsed) {
  leftCollapsed = Boolean(collapsed);
  saveStoredBoolean(LEFT_COLLAPSED_KEY, leftCollapsed);
  syncWorkspaceLayout();
}

function setNewsCollapsed(collapsed) {
  newsCollapsed = Boolean(collapsed);
  saveStoredBoolean(NEWS_COLLAPSED_KEY, newsCollapsed);
  syncWorkspaceLayout();
}

function syncAuxTabs() {
  if (!document.getElementById(activeAuxTarget)) activeAuxTarget = "signals-pane";
  document.querySelectorAll(".aux-tab").forEach((el) => {
    const active = el.dataset.auxTarget === activeAuxTarget;
    el.classList.toggle("active", active);
    el.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".aux-pane").forEach((pane) =>
    pane.classList.toggle("active", pane.id === activeAuxTarget)
  );
}

function setAuxTab(target) {
  activeAuxTarget = target || "signals-pane";
  saveStoredText(AUX_TAB_KEY, activeAuxTarget);
  syncAuxTabs();
}

function setNewsEnabled(on) {
  newsEnabled = Boolean(on);
  saveNewsEnabled(newsEnabled);
  if (!newsEnabled) {
    if (newsAbort) newsAbort.abort();
    newsAbort = null;
    newsLoading = false;
    setText("news-count-top", "资讯关闭");
    setText("news-meta", newsItems.length ? "实时已关闭 · 保留已拉取资讯" : "实时已关闭 · 读取本地缓存");
    renderNewsList();
    if (!newsItems.length) loadCachedNews();
  } else {
    setText("news-meta", "等待刷新");
    loadNews();
  }
  syncNewsControls();
}

async function loadCachedNews() {
  const local = readLocalNewsCache();
  if (local?.items?.length) {
    renderNews({ ...local, from_disk_cache: true });
    return;
  }
  try {
    const data = await getJSON("/api/news/cache?limit=80");
    if (newsEnabled) return;
    renderNews(data);
  } catch (e) {
    if (newsEnabled) return;
    setText("news-meta", "实时已关闭 · 本地缓存读取失败");
    renderNewsList();
  } finally {
    syncNewsControls();
  }
}

async function loadNews() {
  const btn = document.getElementById("news-refresh");
  if (!newsEnabled) {
    if (!newsItems.length) loadCachedNews();
    syncNewsControls();
    return;
  }
  if (newsLoading) return;
  newsLoading = true;
  syncNewsControls();
  if (newsAbort) newsAbort.abort();
  newsAbort = new AbortController();
  const signal = newsAbort.signal;
  try {
    const data = await getJSON("/api/news?limit=80&today=1", { signal });
    if (!newsEnabled || signal.aborted) return;
    renderNews(data);
  } catch (e) {
    if (e.name === "AbortError") return;
    if (!newsEnabled) return;
    setText("news-meta", "资讯加载失败");
    if (!newsItems.length) loadCachedNews();
    else renderNewsList();
  } finally {
    if (newsAbort && newsAbort.signal === signal) newsAbort = null;
    newsLoading = false;
    syncNewsControls();
  }
}

// ---- 立即选股 ----
const screenBtn = document.getElementById("screen-btn");
const screenMsg = document.getElementById("screen-msg");
const dd = document.getElementById("strategy");
const ddBtn = document.getElementById("dd-btn");
const ddMenu = document.getElementById("dd-menu");
const ddLabel = document.getElementById("dd-label");
const scopeDd = document.getElementById("scope");
const scopeBtn = document.getElementById("scope-btn");
const scopeMenu = document.getElementById("scope-menu");
const scopeLabel = document.getElementById("scope-label");
let screenPoll = null;

const currentStrategy = () => dd.dataset.value;
const currentScope = () => scopeDd.dataset.value;

function selectOption(root, labelEl, menuEl, id, label) {
  root.dataset.value = id;
  labelEl.textContent = label;
  menuEl.querySelectorAll(".dd-item").forEach((el) =>
    el.classList.toggle("selected", el.dataset.id === id)
  );
}

function bindDropdown(button, menu) {
  button.addEventListener("click", (e) => {
    e.stopPropagation();
    menu.hidden = !menu.hidden;
  });
}

async function loadDropdown(url, key, root, labelEl, menuEl) {
  try {
    const data = await getJSON(url);
    const rows = data[key] || [];
    menuEl.innerHTML = "";
    for (const s of rows) {
      const li = document.createElement("li");
      li.className = "dd-item";
      li.dataset.id = s.id;
      li.textContent = s.label;
      li.addEventListener("click", () => {
        selectOption(root, labelEl, menuEl, s.id, s.label);
        menuEl.hidden = true;
      });
      menuEl.appendChild(li);
    }
    const def = rows.find((s) => s.id === data.default) || rows[0];
    if (def) selectOption(root, labelEl, menuEl, def.id, def.label);
  } catch (e) {
    /* 忽略 */
  }
}

const loadStrategies = () => loadDropdown("/api/strategies", "strategies", dd, ddLabel, ddMenu);
const loadScopes = () => loadDropdown("/api/scopes", "scopes", scopeDd, scopeLabel, scopeMenu);

bindDropdown(ddBtn, ddMenu);
bindDropdown(scopeBtn, scopeMenu);
document.addEventListener("click", () => {
  ddMenu.hidden = true;
  scopeMenu.hidden = true;
});

let screening = false;

function setScreening(on) {
  screening = on;
  screenBtn.disabled = false;
  screenBtn.textContent = on ? "停止选股" : "立即选股";
}

function progressText(progress) {
  if (!progress || !progress.total) return "选股中…";
  const doneText = `${progress.done}/${progress.total}`;
  if (progress.cooling_down) {
    return `限流冷却中(第${progress.cooldowns}次),${progress.retry_delay_seconds || 60}秒后自动继续,已完成 ${doneText}`;
  }
  if (progress.retrying) {
    const err = progress.retry_error ? `(${progress.retry_error})` : "";
    return `限流中断${err},等待重试(第${progress.retry_attempt}/${progress.max_retries}次),已完成 ${doneText},续跑不重选`;
  }
  const failed = progress.failed ? `,本轮失败 ${progress.failed}` : "";
  const resumed = progress.resumed_done ? `(续跑${progress.resumed_done})` : "";
  return `选股中:${doneText}${failed}${resumed}`;
}

function finishText(last) {
  if (!last.ok) {
    if (last.cancelled) return `已停止:${last.error || ""}(可重新点击继续跑完)`;
    return "选股失败:" + last.error;
  }
  if (last.cancelled) {
    return `已手动停止:已处理 ${last.done || 0}/${last.total || 0},入选 ${last.count || 0} 只(结果已保留,再点可续跑)`;
  }
  if (last.retries_exhausted) {
    return `选股未完成:已达最大重试次数,已处理 ${last.done}/${last.total},入选 ${last.count || 0} 只(结果已保留)`;
  }
  if (last.aborted) {
    return `选股停止:${last.abort_reason || "数据源失败过多"}(已处理 ${last.done || 0}/${last.total || 0},结果已保留)`;
  }
  if (last.timed_out) {
    return `选股超时:已处理 ${last.done}/${last.total},入选 ${last.count} 只(结果已保留)`;
  }
  return `选股完成:入选 ${last.count} 只(用时 ${last.elapsed}s)`;
}

async function pollScreen() {
  let st;
  try {
    st = await getJSON("/api/screen/status");
  } catch (e) {
    return;
  }
  if (st.running) {
    setScreening(true);
    screenMsg.textContent = progressText(st.progress);
    if (!screenPoll) screenPoll = setInterval(pollScreen, 3000);
    return;
  }
  // 跑完了
  if (screenPoll) {
    clearInterval(screenPoll);
    screenPoll = null;
  }
  setScreening(false);
  if (st.last) {
    screenMsg.textContent = finishText(st.last);
  }
  tick(); // 刷新名单
}

screenBtn.addEventListener("click", async () => {
  if (screening) {
    // 运行中:点击即请求停止(进度与已入选结果保留)
    screenMsg.textContent = "正在停止选股…";
    try {
      await fetch("/api/screen/cancel", { method: "POST" });
    } catch (e) {
      screenMsg.textContent = "停止请求失败:" + e.message;
    }
    return;
  }
  const strategy = currentStrategy() || "zhixing";
  const scope = currentScope() || "star_chinext";
  setScreening(true);
  screenMsg.textContent = "选股已启动,约需数分钟…";
  try {
    const qs = new URLSearchParams({ strategy, scope });
    const r = await fetch("/api/screen/run?" + qs.toString(), {
      method: "POST",
    }).then((x) => x.json());
    if (r.started === false) screenMsg.textContent = r.error || "已在选股中…";
  } catch (e) {
    screenMsg.textContent = "启动失败:" + e.message;
    setScreening(false);
    return;
  }
  if (!screenPoll) screenPoll = setInterval(pollScreen, 3000);
});

window.addEventListener("resize", () => {
  if (chart) chart.resize();
  if (backtestChart) backtestChart.resize();
});

document.querySelectorAll(".chart-mode button").forEach((btn) => {
  btn.addEventListener("click", () => {
    chartPeriod = btn.dataset.period || "minute";
    document.querySelectorAll(".chart-mode button").forEach((el) =>
      el.classList.toggle("active", el === btn)
    );
    loadChart();
  });
});

document.querySelectorAll(".indicator-mode button").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.disabled) return;
    setIndicatorMode(btn.dataset.indicator || "rsi");
    loadChart({ background: true });
  });
});

document.querySelectorAll(".aux-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    setAuxTab(btn.dataset.auxTarget);
  });
});

const watchSearch = document.getElementById("watch-search");
const watchSearchMenu = document.getElementById("watch-search-menu");
const watchAddBtn = document.getElementById("watch-add-btn");
const watchAddMsg = document.getElementById("watch-add-msg");
const backtestCode = document.getElementById("backtest-code");
const backtestRule = document.getElementById("backtest-rule");
const backtestDays = document.getElementById("backtest-days");
const backtestForward = document.getElementById("backtest-forward");
const backtestCost = document.getElementById("backtest-cost");
const backtestBtn = document.getElementById("backtest-btn");
const backtestMsg = document.getElementById("backtest-msg");
const newsRefresh = document.getElementById("news-refresh");
const newsEnabledToggle = document.getElementById("news-enabled");
const leftCollapseBtn = document.getElementById("left-collapse-btn");
const leftExpandBtn = document.getElementById("left-expand-btn");
const newsCollapseBtn = document.getElementById("news-collapse-btn");
const newsExpandBtn = document.getElementById("news-expand-btn");

async function loadBacktestRules() {
  try {
    const data = await getJSON("/api/backtest/rules");
    backtestRule.innerHTML = "";
    for (const rule of data.rules || []) {
      const opt = document.createElement("option");
      opt.value = rule.id;
      opt.textContent = rule.label;
      if (rule.id === data.default) opt.selected = true;
      backtestRule.appendChild(opt);
    }
  } catch (e) {
    backtestMsg.textContent = "规则加载失败";
  }
}

async function runBatchBacktest() {
  const days = backtestDays.value || "365";
  const forward = backtestForward.value || "5";
  const cost = Number(backtestCost.value || 0) / 100;
  const payload = {
    stocks: backtestBatch.stocks,
    rule: backtestRule.value || "ma_cross",
    days: Number(days),
    forward: Number(forward),
    cost,
  };
  const data = await fetch("/api/backtest/batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then(async (r) => {
    const body = await r.json();
    if (!r.ok) throw new Error(body.detail || "批量回测失败");
    return body;
  });
  renderBacktestBatch(data);
}

backtestBtn.addEventListener("click", async () => {
  if (backtestBatch && backtestBatch.stocks.length) {
    backtestBtn.disabled = true;
    backtestMsg.textContent = `批量回测中…${backtestBatch.stocks.length}只`;
    try {
      await runBatchBacktest();
    } catch (e) {
      backtestMsg.textContent = e.message;
    } finally {
      backtestBtn.disabled = false;
    }
    return;
  }

  const code = (backtestCode.value.trim() || selected || "").trim();
  if (!/^\d{6}$/.test(code)) {
    backtestMsg.textContent = "请输入6位股票代码";
    return;
  }
  backtestCode.value = code;
  const days = backtestDays.value || "365";
  const forward = backtestForward.value || "5";
  const cost = (Number(backtestCost.value || 0) / 100).toString();
  backtestBtn.disabled = true;
  backtestMsg.textContent = "回测中…";
  try {
    const qs = new URLSearchParams({
      code,
      rule: backtestRule.value || "ma_cross",
      days,
      forward,
      cost,
    });
    const data = await fetch("/api/backtest?" + qs.toString()).then(async (r) => {
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || "回测失败");
      return body;
    });
    renderBacktest(data);
  } catch (e) {
    backtestMsg.textContent = e.message;
  } finally {
    backtestBtn.disabled = false;
  }
});

// ---- 自选搜索(名称/代码联想) ----
let watchSearchTimer = null;
let watchSearchResults = [];
let watchSearchIndex = -1;

const watchCodeSet = () => new Set(quoteRows.map((row) => row.code));

function hideWatchSearchMenu() {
  watchSearchMenu.hidden = true;
  watchSearchIndex = -1;
}

function renderWatchSearchMenu(rows) {
  watchSearchResults = rows || [];
  watchSearchIndex = -1;
  if (!watchSearchResults.length) {
    hideWatchSearchMenu();
    return;
  }
  const added = watchCodeSet();
  watchSearchMenu.innerHTML = "";
  for (const item of watchSearchResults) {
    const li = document.createElement("li");
    li.className = "dd-item";
    li.dataset.code = item.code;
    li.dataset.name = item.name;
    li.innerHTML =
      `<span>${item.name}</span>` +
      (added.has(item.code)
        ? `<span class="added">已自选</span>`
        : `<span class="code">${item.code}</span>`);
    li.addEventListener("mousedown", (e) => {
      e.preventDefault();
      addWatchStock(item.code, item.name);
    });
    watchSearchMenu.appendChild(li);
  }
  watchSearchMenu.hidden = false;
}

function highlightWatchSearchItem() {
  const items = watchSearchMenu.querySelectorAll(".dd-item");
  items.forEach((el, i) => el.classList.toggle("selected", i === watchSearchIndex));
  if (watchSearchIndex >= 0 && items[watchSearchIndex]) {
    items[watchSearchIndex].scrollIntoView({ block: "nearest" });
  }
}

async function searchWatchStocks() {
  const q = watchSearch.value.trim();
  if (!q) {
    hideWatchSearchMenu();
    return;
  }
  try {
    const data = await getJSON(`/api/search?q=${encodeURIComponent(q)}&limit=10`);
    if (watchSearch.value.trim() === q) renderWatchSearchMenu(data.results || []);
  } catch (e) {
    /* 搜索失败静默 */
  }
}

async function addWatchStock(code, name) {
  watchAddBtn.disabled = true;
  watchAddMsg.textContent = "加入中…";
  try {
    const qs = new URLSearchParams({ code });
    if (name) qs.set("name", name);
    const r = await fetch("/api/watchlist/add?" + qs.toString(), { method: "POST" });
    const data = await r.json();
    if (!r.ok || !data.ok) throw new Error(data.detail || "加入失败");
    watchSearch.value = "";
    hideWatchSearchMenu();
    watchAddMsg.textContent = data.added ? `已加入 ${data.item.name}` : `${data.item.name} 已在自选`;
    upsertQuotePlaceholder(data.item, data.added ? "已加入，行情刷新中" : "已存在，行情刷新中");
    selectStock(data.item.code, data.item.name);
    tick();
  } catch (e) {
    watchAddMsg.textContent = e.message;
  } finally {
    watchAddBtn.disabled = false;
    watchSearch.focus();
  }
}

watchSearch.addEventListener("input", () => {
  watchAddMsg.textContent = "";
  clearTimeout(watchSearchTimer);
  watchSearchTimer = setTimeout(searchWatchStocks, 250);
});

watchSearch.addEventListener("focus", () => {
  if (watchSearch.value.trim() && !watchSearchMenu.hidden) return;
  if (watchSearch.value.trim()) searchWatchStocks();
});

watchSearch.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown" && !watchSearchMenu.hidden) {
    e.preventDefault();
    watchSearchIndex = Math.min(watchSearchIndex + 1, watchSearchResults.length - 1);
    highlightWatchSearchItem();
  } else if (e.key === "ArrowUp" && !watchSearchMenu.hidden) {
    e.preventDefault();
    watchSearchIndex = Math.max(watchSearchIndex - 1, 0);
    highlightWatchSearchItem();
  } else if (e.key === "Escape") {
    hideWatchSearchMenu();
  } else if (e.key === "Enter") {
    e.preventDefault();
    if (watchSearchIndex >= 0 && watchSearchResults[watchSearchIndex]) {
      const item = watchSearchResults[watchSearchIndex];
      addWatchStock(item.code, item.name);
    } else if (/^\d{6}$/.test(watchSearch.value.trim())) {
      addWatchStock(watchSearch.value.trim(), null);
    } else if (watchSearchResults.length) {
      const item = watchSearchResults[0];
      addWatchStock(item.code, item.name);
    } else {
      watchAddMsg.textContent = "请输入名称或6位代码";
    }
  }
});

document.addEventListener("click", (e) => {
  if (!e.target.closest(".watch-search")) hideWatchSearchMenu();
});

watchAddBtn.addEventListener("click", () => {
  const q = watchSearch.value.trim();
  if (!q) {
    watchAddMsg.textContent = "请输入名称或代码";
    return;
  }
  if (/^\d{6}$/.test(q)) {
    addWatchStock(q, null);
  } else if (watchSearchResults.length) {
    const item = watchSearchResults[0];
    addWatchStock(item.code, item.name);
  } else {
    watchAddMsg.textContent = "未找到匹配的股票";
  }
});

[backtestCode].forEach((el) => {
  el.addEventListener("input", clearBacktestBatch);
});

[backtestCode, backtestDays, backtestForward, backtestCost].forEach((el) => {
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter") backtestBtn.click();
  });
});

document.querySelectorAll(".news-filters button").forEach((btn) => {
  btn.addEventListener("click", () => {
    newsFilter = btn.dataset.tag || "全部";
    document.querySelectorAll(".news-filters button").forEach((el) =>
      el.classList.toggle("active", el === btn)
    );
    renderNewsList();
  });
});

if (newsRefresh) {
  newsRefresh.addEventListener("click", loadNews);
}
if (newsEnabledToggle) {
  newsEnabledToggle.addEventListener("change", () => setNewsEnabled(newsEnabledToggle.checked));
}
if (leftCollapseBtn) {
  leftCollapseBtn.addEventListener("click", () => setLeftCollapsed(true));
}
if (leftExpandBtn) {
  leftExpandBtn.addEventListener("click", () => setLeftCollapsed(false));
}
if (newsCollapseBtn) {
  newsCollapseBtn.addEventListener("click", () => setNewsCollapsed(true));
}
if (newsExpandBtn) {
  newsExpandBtn.addEventListener("click", () => setNewsCollapsed(false));
}

loadStrategies();
loadScopes();
loadBacktestRules();
syncWorkspaceLayout();
syncAuxTabs();
syncNewsControls();
const initialStock = new URLSearchParams(window.location.search).get("stock") || "";
if (/^\d{6}$/.test(initialStock)) selectStock(initialStock, initialStock);
if (newsEnabled) loadNews();
else setNewsEnabled(false);
tick();
pollScreen(); // 页面加载时若已有选股在跑,自动接上进度
loadBandMarket();
setInterval(tick, REFRESH_MS);
setInterval(() => {
  if (newsEnabled) loadNews();
}, NEWS_REFRESH_MS);
setInterval(loadBandMarket, 5 * 60 * 1000);
const bandPill = document.getElementById("band-temp-pill");
if (bandPill) {
  bandPill.addEventListener("click", () => {
    document.querySelector(".band-console")?.scrollIntoView({ block: "start", behavior: "smooth" });
  });
}
