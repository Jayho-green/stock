"use strict";

/* 龙虎榜 · 机构资金面板
 * 数据来自 /api/lhb;交互:日期切换、板块图/排行切换、板块过滤、
 * 表格排序/筛选/搜索、行展开详情、一键加自选。
 */

const LHB_REFRESH_MS = 5 * 60 * 1000; // 查看"最新"时的自动刷新间隔
const LHB_PENDING_POLL_MS = 20000;
const LHB_PENDING_MAX_POLLS = 3;
const FILTER_KEY = "quant.lhb.filter";
const CHART_MODE_KEY = "quant.lhb.chartMode";
const SORT_KEY = "quant.lhb.sort";
const TREND_PERIOD_KEY = "quant.lhb.trendPeriod";

let data = null;          // /api/lhb payload
let viewingLatest = true; // 未指定日期(跟随最新披露)
let chartMode = readText(CHART_MODE_KEY, "treemap");
let trendPeriod = readText(TREND_PERIOD_KEY, "daily");
if (!["daily", "weekly"].includes(trendPeriod)) trendPeriod = "daily";
let rowFilter = readText(FILTER_KEY, "all");
let sectorFilter = null;
let searchText = "";
let sort = readSort();
let expanded = new Set();
let chart = null;
let trendChart = null;
let fetchAbort = null;
let trendAbort = null;
let refreshTimer = null;
let pendingTimer = null;
let lastDate = null;
let pendingPolls = 0;
let lastPendingCount = null;

const $ = (id) => document.getElementById(id);

function readText(key, fallback) {
  try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; }
}
function saveText(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* 忽略 */ }
}
function readSort() {
  const raw = readText(SORT_KEY, "org_net:desc");
  const [key, dir] = raw.split(":");
  return { key: key || "org_net", dir: dir === "asc" ? "asc" : "desc" };
}

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/* ---------- 格式化 ---------- */

function fmtMoney(v) {
  if (v === null || v === undefined || isNaN(v)) return "–";
  const abs = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (abs >= 1e8) return `${sign}${(abs / 1e8).toFixed(2)}亿`;
  if (abs >= 1e4) return `${sign}${(abs / 1e4).toFixed(0)}万`;
  return `${sign}${abs.toFixed(0)}`;
}

function fmtSigned(v) {
  if (v === null || v === undefined || isNaN(v)) return "–";
  return (v > 0 ? "+" : "") + fmtMoney(v);
}

function fmtPct(v, digits = 2) {
  if (v === null || v === undefined || isNaN(v)) return "–";
  return `${v > 0 ? "+" : ""}${Number(v).toFixed(digits)}%`;
}

function clsOf(v) {
  if (v === null || v === undefined || isNaN(v) || v === 0) return "flat-val";
  return v > 0 ? "up" : "down";
}

/* ---------- 取数 ---------- */

async function load(dateStr) {
  if (fetchAbort) fetchAbort.abort();
  if (pendingTimer) {
    clearTimeout(pendingTimer);
    pendingTimer = null;
  }
  fetchAbort = new AbortController();
  viewingLatest = !dateStr;
  setStatus("加载中…");
  setMsg("");
  try {
    const url = dateStr ? `/api/lhb?date=${encodeURIComponent(dateStr)}` : "/api/lhb";
    const resp = await fetch(url, { signal: fetchAbort.signal });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${resp.status}`);
    }
    data = await resp.json();
    if (data.date !== lastDate) {
      // 换了日期才重置交互状态;行业补全的静默刷新保留展开/过滤
      expanded = new Set();
      sectorFilter = null;
      pendingPolls = 0;
      lastPendingCount = null;
      lastDate = data.date;
    }
    $("date-input").value = data.date || "";
    renderAll();
    loadTrend(data.date);
    const n = data.summary.stocks;
    setStatus(`${data.date} · ${n} 只上榜`);
    if (!n) setMsg(dateStr ? "该日无龙虎榜数据(非交易日或未披露)" : "暂无数据");
    if (data.industry_pending > 0) {
      const pending = Number(data.industry_pending) || 0;
      if (lastPendingCount === null || pending < lastPendingCount) pendingPolls = 0;
      lastPendingCount = pending;
      if (pendingPolls < LHB_PENDING_MAX_POLLS) {
        pendingPolls += 1;
        setMsg(`板块归类中(剩 ${pending} 只)…`);
        pendingTimer = setTimeout(() => load(viewingLatest ? null : data.date), LHB_PENDING_POLL_MS);
      } else {
        setMsg(`板块归类剩 ${pending} 只，行业接口暂未返回；可稍后刷新或运行归档补齐`);
      }
    } else {
      pendingPolls = 0;
      lastPendingCount = null;
    }
  } catch (e) {
    if (e.name === "AbortError") return;
    setStatus("加载失败");
    $("lhb-tbody").innerHTML =
      `<tr><td colspan="10" class="lhb-error">数据获取失败:${escapeHtml(String(e.message || e))}` +
      `<button type="button" onclick="location.reload()">重试</button></td></tr>`;
  }
  scheduleRefresh();
}

function scheduleRefresh() {
  if (refreshTimer) clearTimeout(refreshTimer);
  if (!viewingLatest) return; // 历史日期不自动刷新
  refreshTimer = setTimeout(() => load(null), LHB_REFRESH_MS);
}

function setStatus(text) { $("lhb-status").textContent = text; }
function setMsg(text) { $("lhb-msg").textContent = text; }

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

/* ---------- KPI ---------- */

function renderKpis() {
  const s = data.summary;
  $("kpi-stocks").textContent = s.stocks;
  $("kpi-org").textContent = s.org_stocks;
  $("kpi-buy").textContent = fmtMoney(s.org_buy);
  $("kpi-sell").textContent = fmtMoney(s.org_sell);
  const net = $("kpi-net");
  net.textContent = fmtSigned(s.org_net);
  net.className = s.org_net > 0 ? "up" : s.org_net < 0 ? "down" : "";
  $("kpi-net-sub").textContent = `净买 ${s.net_buy_count} 只 / 净卖 ${s.net_sell_count} 只`;
}

/* ---------- 板块资金趋势 ---------- */

async function loadTrend(endDate) {
  if (trendAbort) trendAbort.abort();
  trendAbort = new AbortController();
  const params = new URLSearchParams({
    period: trendPeriod,
    days: trendPeriod === "weekly" ? "90" : "30",
    top: "8",
  });
  if (endDate) params.set("end", endDate);
  $("trend-note").textContent = "趋势加载中…";
  try {
    const resp = await fetch(`/api/lhb/trends?${params.toString()}`, { signal: trendAbort.signal });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${resp.status}`);
    }
    const trend = await resp.json();
    renderTrend(trend);
  } catch (e) {
    if (e.name === "AbortError") return;
    $("trend-note").textContent = `趋势数据获取失败:${String(e.message || e)}`;
    if (trendChart) trendChart.clear();
  }
}

function renderTrend(trend) {
  if (!trendChart) {
    trendChart = echarts.init($("trend-chart"));
    window.addEventListener("resize", () => trendChart && trendChart.resize());
  }
  setTrendModeButtons();
  if (!trend.points.length || !trend.industries.length) {
    $("trend-note").textContent = "暂无归档历史。先在盘后运行龙虎榜归档,之后这里会显示日/周板块资金走势。";
    trendChart.setOption({
      title: {
        text: "暂无趋势数据",
        left: "center",
        top: "middle",
        textStyle: { color: cssVar("--muted", "#9aa"), fontSize: 14, fontWeight: 500 },
      },
      xAxis: { show: false },
      yAxis: { show: false },
      series: [],
    }, true);
    return;
  }

  const labels = trend.points.map((p) => p.label || p.date);
  const palette = [
    cssVar("--brass", "#d2a552"),
    cssVar("--cyan", "#55d6d2"),
    cssVar("--red", "#f05b68"),
    cssVar("--green", "#30c889"),
    "#e2c58f",
    "#7fd8b8",
    "#d87b7b",
    "#8fa6ff",
  ];
  const series = trend.industries.map((industry, idx) => ({
    name: industry,
    type: "line",
    smooth: true,
    symbol: "circle",
    symbolSize: 5,
    lineStyle: { width: 2 },
    itemStyle: { color: palette[idx % palette.length] },
    data: trend.points.map((p) => {
      const sec = p.sectors && p.sectors[industry];
      return sec ? sec.org_net : 0;
    }),
  }));
  series.unshift({
    name: "全部板块",
    type: "bar",
    barMaxWidth: 16,
    itemStyle: { color: "rgba(210,165,82,0.22)" },
    data: trend.points.map((p) => p.org_net || 0),
  });

  $("trend-note").textContent =
    `${trend.period === "weekly" ? "周线" : "日线"} · 已归档 ${trend.cached_days} 日` +
    (trend.latest_date ? ` · 最新 ${trend.latest_date}` : "") +
    " · 仅统计龙虎榜机构专用席位";

  trendChart.setOption({
    color: palette,
    tooltip: {
      trigger: "axis",
      backgroundColor: "rgba(8,11,12,0.94)",
      borderColor: cssVar("--line", "#273337"),
      textStyle: { color: cssVar("--text", "#eee") },
      formatter: (items) => {
        const idx = items[0]?.dataIndex || 0;
        const point = trend.points[idx] || {};
        const rows = items
          .filter((it) => it.seriesName !== "全部板块")
          .sort((a, b) => Math.abs(b.value || 0) - Math.abs(a.value || 0))
          .slice(0, 8)
          .map((it) => `${it.marker || ""}${escapeHtml(it.seriesName)}: ${fmtSigned(it.value)}`)
          .join("<br/>");
        return `<b>${escapeHtml(point.label || point.date)}</b><br/>全部:${fmtSigned(point.org_net)}<br/>${rows}`;
      },
    },
    legend: {
      top: 4,
      right: 8,
      type: "scroll",
      textStyle: { color: cssVar("--muted", "#9aa"), fontSize: 11 },
    },
    grid: { left: 8, right: 18, top: 42, bottom: 28, containLabel: true },
    xAxis: {
      type: "category",
      data: labels,
      axisLabel: { color: cssVar("--muted", "#9aa"), fontSize: 11 },
      axisLine: { lineStyle: { color: cssVar("--line", "#273337") } },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: cssVar("--muted", "#9aa"), formatter: (v) => fmtMoney(v) },
      splitLine: { lineStyle: { color: cssVar("--line-soft", "#1b2629") } },
    },
    dataZoom: trend.points.length > 14 ? [
      { type: "inside", start: 40, end: 100 },
      { type: "slider", height: 16, bottom: 4, borderColor: "transparent", textStyle: { color: cssVar("--muted", "#9aa") } },
    ] : [],
    series,
  }, true);
}

/* ---------- 板块图 ---------- */

function sectorColor(net, maxAbs) {
  const red = cssVar("--red", "#f05b68");
  const green = cssVar("--green", "#30c889");
  const base = net >= 0 ? red : green;
  const ratio = maxAbs > 0 ? Math.min(1, Math.abs(net) / maxAbs) : 0;
  const alpha = 0.25 + ratio * 0.65;
  const [r, g, b] = hexToRgb(base);
  return `rgba(${r},${g},${b},${alpha.toFixed(2)})`;
}

function hexToRgb(hex) {
  const m = hex.replace("#", "");
  const n = parseInt(m.length === 3 ? m.split("").map((c) => c + c).join("") : m, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function renderChart() {
  if (!chart) {
    chart = echarts.init($("sector-chart"));
    chart.on("click", (params) => {
      const name = params.data && params.data.industry;
      if (!name) return;
      sectorFilter = sectorFilter === name ? null : name;
      renderSectorChip();
      renderTable();
    });
    window.addEventListener("resize", () => chart && chart.resize());
  }
  const sectors = data.sectors.filter((x) => x.count > 0);
  const maxAbs = Math.max(1, ...sectors.map((x) => Math.abs(x.org_net)));
  const text = cssVar("--text", "#eee");
  const muted = cssVar("--muted", "#9aa");

  if (chartMode === "treemap") {
    chart.setOption({
      tooltip: { formatter: (p) => sectorTip(p.data) },
      series: [{
        type: "treemap",
        roam: false,
        nodeClick: false,
        breadcrumb: { show: false },
        width: "100%",
        height: "100%",
        label: {
          show: true,
          formatter: (p) => `${p.data.industry}\n${fmtSigned(p.data.org_net)}`,
          fontSize: 12,
          color: text,
          textShadowColor: "rgba(0,0,0,0.6)",
          textShadowBlur: 4,
        },
        itemStyle: { borderColor: cssVar("--bg", "#07090a"), borderWidth: 2, gapWidth: 2 },
        data: sectors.map((x) => ({
          name: x.industry,
          industry: x.industry,
          value: Math.max(Math.abs(x.org_net), 1) + x.count * 1e5, // 无机构板块也占位
          org_net: x.org_net,
          org_buy: x.org_buy,
          org_sell: x.org_sell,
          count: x.count,
          org_count: x.org_count,
          itemStyle: { color: sectorColor(x.org_net, maxAbs) },
        })),
      }],
    }, true);
    return;
  }

  const rows = sectors
    .slice()
    .sort((a, b) => a.org_net - b.org_net)
    .slice(-18);
  chart.setOption({
    grid: { left: 8, right: 60, top: 8, bottom: 8, containLabel: true },
    tooltip: { formatter: (p) => sectorTip(p.data) },
    xAxis: {
      type: "value",
      axisLabel: { color: muted, formatter: (v) => fmtMoney(v) },
      splitLine: { lineStyle: { color: cssVar("--line-soft", "#1b2629") } },
    },
    yAxis: {
      type: "category",
      data: rows.map((x) => x.industry),
      axisLabel: { color: text, fontSize: 12 },
      axisLine: { lineStyle: { color: muted } },
    },
    series: [{
      type: "bar",
      barMaxWidth: 16,
      data: rows.map((x) => ({
        value: x.org_net,
        industry: x.industry,
        org_net: x.org_net,
        org_buy: x.org_buy,
        org_sell: x.org_sell,
        count: x.count,
        org_count: x.org_count,
        itemStyle: { color: sectorColor(x.org_net, Math.max(1, ...rows.map((r) => Math.abs(r.org_net)))) },
      })),
      label: {
        show: true,
        position: "right",
        color: muted,
        fontSize: 11,
        formatter: (p) => fmtSigned(p.data.org_net),
      },
    }],
  }, true);
}

function sectorTip(d) {
  if (!d) return "";
  return (
    `<b>${escapeHtml(d.industry)}</b><br/>` +
    `机构净额:${fmtSigned(d.org_net)}<br/>` +
    `买入 ${fmtMoney(d.org_buy)} / 卖出 ${fmtMoney(d.org_sell)}<br/>` +
    `上榜 ${d.count} 只(机构参与 ${d.org_count} 只)<br/>` +
    `<i style="opacity:.7">点击板块过滤个股</i>`
  );
}

function renderSectorChip() {
  const chip = $("sector-chip");
  if (sectorFilter) {
    $("sector-chip-name").textContent = sectorFilter;
    chip.hidden = false;
  } else {
    chip.hidden = true;
  }
}

/* ---------- 表格 ---------- */

function sortValue(s, key) {
  if (key === "org_seats") return (s.org_buy_count || 0) + (s.org_sell_count || 0);
  const v = s[key];
  if (v === null || v === undefined) return sort.dir === "desc" ? -Infinity : Infinity;
  return typeof v === "string" ? v : Number(v);
}

function visibleStocks() {
  let rows = data.stocks;
  if (rowFilter === "org") rows = rows.filter((s) => s.has_org);
  else if (rowFilter === "net_buy") rows = rows.filter((s) => s.has_org && s.org_net > 0);
  else if (rowFilter === "net_sell") rows = rows.filter((s) => s.has_org && s.org_net < 0);
  if (sectorFilter) rows = rows.filter((s) => s.industry === sectorFilter);
  const q = searchText.trim().toLowerCase();
  if (q) {
    rows = rows.filter(
      (s) =>
        s.code.includes(q) ||
        (s.name || "").toLowerCase().includes(q) ||
        (s.industry || "").toLowerCase().includes(q)
    );
  }
  const dir = sort.dir === "asc" ? 1 : -1;
  return rows.slice().sort((a, b) => {
    const va = sortValue(a, sort.key);
    const vb = sortValue(b, sort.key);
    if (typeof va === "string" || typeof vb === "string") {
      return String(va).localeCompare(String(vb), "zh") * dir;
    }
    return (va - vb) * dir;
  });
}

function renderTable() {
  const rows = visibleStocks();
  const tbody = $("lhb-tbody");
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="10" class="flat">没有符合条件的股票</td></tr>`;
    return;
  }
  const maxNet = Math.max(1, ...rows.map((s) => Math.abs(s.org_net || 0)));
  const html = [];
  for (const s of rows) {
    const seat = s.has_org ? `${s.org_buy_count || 0} / ${s.org_sell_count || 0}` : "–";
    const netW = Math.max(4, Math.round((Math.abs(s.org_net || 0) / maxNet) * 100));
    const orgCls = s.has_org ? "" : " no-org";
    html.push(
      `<tr class="lhb-row${expanded.has(s.code) ? " expanded" : ""}" data-code="${s.code}">` +
        `<td class="stock-cell"><b>${escapeHtml(s.name)}</b><small>${s.code}</small></td>` +
        `<td><span class="tag-industry">${escapeHtml(s.industry || "未分类")}</span></td>` +
        `<td class="num">${s.close ?? "–"}</td>` +
        `<td class="num ${clsOf(s.change_pct)}">${fmtPct(s.change_pct)}</td>` +
        `<td class="num${orgCls}">${s.has_org ? fmtMoney(s.org_buy) : "–"}</td>` +
        `<td class="num${orgCls}">${s.has_org ? fmtMoney(s.org_sell) : "–"}</td>` +
        `<td class="num ${s.has_org ? clsOf(s.org_net) : "no-org"}">` +
          (s.has_org
            ? `<span class="net-bar">${fmtSigned(s.org_net)}<i style="transform:scaleX(${netW / 100})"></i></span>`
            : "无机构") +
        `</td>` +
        `<td class="num${orgCls}">${seat}</td>` +
        `<td class="num ${clsOf(s.lhb_net_buy)}">${fmtSigned(s.lhb_net_buy)}</td>` +
        `<td class="num">${s.turnover === null || s.turnover === undefined ? "–" : Number(s.turnover).toFixed(2) + "%"}</td>` +
      `</tr>`
    );
    if (expanded.has(s.code)) html.push(detailRow(s));
  }
  tbody.innerHTML = html.join("");
}

function detailRow(s) {
  const reasons = (s.reasons || []).map((r) => `<li>${escapeHtml(r)}</li>`).join("") || "<li>–</li>";
  const ratio = s.org_net_ratio === null || s.org_net_ratio === undefined
    ? "–" : `${Number(s.org_net_ratio).toFixed(2)}%`;
  return (
    `<tr class="lhb-detail"><td colspan="10"><div class="detail-grid">` +
      `<dl><dt>机构净额占总成交</dt><dd>${ratio}</dd>` +
      `<dt>龙虎榜买入 / 卖出</dt><dd>${fmtMoney(s.lhb_buy)} / ${fmtMoney(s.lhb_sell)}</dd></dl>` +
      `<dl><dt>流通市值</dt><dd>${fmtMoney(s.float_mv)}</dd>` +
      `<dt>上榜后 1日 / 5日</dt>` +
      `<dd><span class="${clsOf(s.after_1d)}">${fmtPct(s.after_1d)}</span> / ` +
      `<span class="${clsOf(s.after_5d)}">${fmtPct(s.after_5d)}</span></dd></dl>` +
      `<div class="detail-reasons"><dl><dt>上榜原因${s.interpretation ? " · " + escapeHtml(s.interpretation) : ""}</dt></dl>` +
      `<ul>${reasons}</ul></div>` +
      `<div class="detail-actions"><button type="button" data-add="${s.code}" data-name="${escapeHtml(s.name)}">＋ 加入自选</button></div>` +
    `</div></td></tr>`
  );
}

async function addWatchlist(code, name, btn) {
  btn.disabled = true;
  btn.textContent = "加入中…";
  try {
    const resp = await fetch(
      `/api/watchlist/add?code=${encodeURIComponent(code)}&name=${encodeURIComponent(name)}`,
      { method: "POST" }
    );
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(body.detail || "失败");
    btn.textContent = body.added ? "✓ 已加入" : "已在自选";
  } catch (e) {
    btn.textContent = `失败:${String(e.message || e).slice(0, 12)}`;
    btn.disabled = false;
  }
}

function renderAll() {
  renderKpis();
  renderChart();
  renderSectorChip();
  renderTable();
}

/* ---------- 事件绑定 ---------- */

function shiftDate(days) {
  const cur = $("date-input").value || (data && data.date);
  if (!cur) return;
  const d = new Date(cur + "T00:00:00");
  d.setDate(d.getDate() + days);
  const iso = d.toISOString().slice(0, 10);
  if (iso > new Date().toISOString().slice(0, 10)) return;
  $("date-input").value = iso;
  load(iso);
}

function bind() {
  $("date-prev").addEventListener("click", () => shiftDate(-1));
  $("date-next").addEventListener("click", () => shiftDate(1));
  $("date-latest").addEventListener("click", () => load(null));
  $("date-input").addEventListener("change", (e) => e.target.value && load(e.target.value));

  $("mode-treemap").addEventListener("click", () => setChartMode("treemap"));
  $("mode-bar").addEventListener("click", () => setChartMode("bar"));
  document.querySelectorAll("[data-trend-period]").forEach((btn) => {
    btn.addEventListener("click", () => {
      trendPeriod = btn.dataset.trendPeriod;
      saveText(TREND_PERIOD_KEY, trendPeriod);
      setTrendModeButtons();
      loadTrend(data && data.date);
    });
  });

  document.querySelectorAll(".lhb-filters button[data-filter]").forEach((btn) => {
    if (btn.dataset.filter === rowFilter) {
      document.querySelectorAll(".lhb-filters button[data-filter]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
    }
    btn.addEventListener("click", () => {
      rowFilter = btn.dataset.filter;
      saveText(FILTER_KEY, rowFilter);
      document.querySelectorAll(".lhb-filters button[data-filter]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      renderTable();
    });
  });

  $("sector-chip-x").addEventListener("click", () => {
    sectorFilter = null;
    renderSectorChip();
    renderTable();
  });

  let searchTimer = null;
  $("lhb-search").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      searchText = e.target.value;
      renderTable();
    }, 180);
  });

  document.querySelectorAll("#lhb-table thead th.sortable").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      if (sort.key === key) sort.dir = sort.dir === "desc" ? "asc" : "desc";
      else sort = { key, dir: "desc" };
      saveText(SORT_KEY, `${sort.key}:${sort.dir}`);
      updateSortHeaders();
      renderTable();
    });
  });
  updateSortHeaders();

  $("lhb-tbody").addEventListener("click", (e) => {
    const addBtn = e.target.closest("button[data-add]");
    if (addBtn) {
      addWatchlist(addBtn.dataset.add, addBtn.dataset.name, addBtn);
      return;
    }
    const row = e.target.closest("tr.lhb-row");
    if (!row) return;
    const code = row.dataset.code;
    if (expanded.has(code)) expanded.delete(code);
    else expanded.add(code);
    renderTable();
  });

  if (chartMode !== "treemap") setChartModeButtons();
  setTrendModeButtons();
}

function updateSortHeaders() {
  document.querySelectorAll("#lhb-table thead th.sortable").forEach((h) => {
    h.classList.toggle("active-sort", h.dataset.sort === sort.key);
    h.textContent = h.textContent.replace(/ [▾▴]$/, "");
    if (h.dataset.sort === sort.key) h.textContent += sort.dir === "desc" ? " ▾" : " ▴";
  });
}

function setChartMode(mode) {
  chartMode = mode;
  saveText(CHART_MODE_KEY, mode);
  setChartModeButtons();
  if (data) renderChart();
}

function setChartModeButtons() {
  $("mode-treemap").classList.toggle("active", chartMode === "treemap");
  $("mode-bar").classList.toggle("active", chartMode === "bar");
}

function setTrendModeButtons() {
  $("trend-daily").classList.toggle("active", trendPeriod === "daily");
  $("trend-weekly").classList.toggle("active", trendPeriod === "weekly");
}

bind();
load(null);
