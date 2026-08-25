"use strict";

const KR_REFRESH_MS = 8000;
let krChart = null;
let krLoading = false;

const $ = (id) => document.getElementById(id);

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function num(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function fmtPrice(v) {
  const n = num(v);
  if (n === null) return "--";
  return n.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

function fmtPct(v) {
  const n = num(v);
  if (n === null) return "--";
  return `${n > 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function fmtSigned(v) {
  const n = num(v);
  if (n === null) return "--";
  return `${n > 0 ? "+" : ""}${n.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`;
}

function fmtVolume(v) {
  const n = num(v);
  if (n === null) return "--";
  if (n >= 1e8) return `${(n / 1e8).toFixed(2)}亿`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e4) return `${(n / 1e4).toFixed(2)}万`;
  return n.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

function clsChange(v) {
  const n = num(v);
  if (n === null || n === 0) return "";
  return n < 0 ? "down" : "";
}

function rangeMetric(q) {
  const low = num(q.day_low);
  const high = num(q.day_high);
  const price = num(q.price);
  if (low === null || high === null || price === null || high <= low) {
    const points = q.trend?.points || [];
    const prices = points.map((p) => num(p.price)).filter((v) => v !== null);
    if (!prices.length || price === null) return { low: null, high: null, pct: 50 };
    const fallbackLow = Math.min(...prices, price);
    const fallbackHigh = Math.max(...prices, price);
    const pct = fallbackHigh > fallbackLow ? ((price - fallbackLow) / (fallbackHigh - fallbackLow)) * 100 : 50;
    return { low: fallbackLow, high: fallbackHigh, pct: Math.max(0, Math.min(100, pct)) };
  }
  return { low, high, pct: Math.max(0, Math.min(100, ((price - low) / (high - low)) * 100)) };
}

function renderCards(quotes, market) {
  $("kr-cards").innerHTML = (quotes || []).map((q) => {
    const accent = q.accent === "violet" ? "accent-violet" : "accent-teal";
    const range = rangeMetric(q);
    const status = q.error ? "取数失败" : market?.is_open ? "交易中" : "已收盘";
    const code = q.code || String(q.secid || "").split(".").pop() || "--";
    return (
      `<article class="kr-stock-card ${accent}">` +
        `<div class="kr-card-head">` +
          `<div><h2>${escapeHtml(q.name)}</h2>` +
          `<p>${escapeHtml(q.company || q.name)} · ${escapeHtml(q.market || "KRX")}:${escapeHtml(code)}</p></div>` +
          `<span>${status}</span>` +
        `</div>` +
        `<div class="kr-price">${fmtPrice(q.price)} <small>${escapeHtml(q.currency || "KRW")}</small></div>` +
        `<div class="kr-change-chip ${clsChange(q.change_pct)}">${num(q.change_pct) >= 0 ? "▲" : "▼"} ${fmtSigned(q.change)} KRW（${fmtPct(q.change_pct)}）</div>` +
        `<div class="kr-range">` +
          `<div class="kr-range-row"><span>日内低 <b>${fmtPrice(range.low)}</b></span><span>日内高 <b>${fmtPrice(range.high)}</b></span></div>` +
          `<div class="kr-range-track"><i class="kr-range-fill"></i><i class="kr-range-knob" style="left:${range.pct.toFixed(2)}%"></i></div>` +
        `</div>` +
        `<div class="kr-stat-row">` +
          `<div><span>今开</span><b>${fmtPrice(q.open)} KRW</b></div>` +
          `<div><span>成交量</span><b>${fmtVolume(q.volume)}</b></div>` +
          `<div><span>昨收</span><b>${fmtPrice(q.prev_close)} KRW</b></div>` +
        `</div>` +
      `</article>`
    );
  }).join("");
}

function renderMarket(market) {
  const el = $("kr-market-state");
  el.classList.toggle("open", Boolean(market?.is_open));
  el.innerHTML = `<i></i>${escapeHtml(market?.label || "市场状态未知")}`;
}

function renderArchive(rows) {
  const list = $("kr-archive-list");
  const data = (rows || []).slice(0, 4);
  if (!data.length) {
    list.innerHTML = `<div class="kr-archive-row muted">暂无收盘归档</div>`;
    return;
  }
  list.innerHTML = data.map((row) => (
    `<div class="kr-archive-row">` +
      `<div><span>${escapeHtml(row.name)} · ${row.archived === false ? "待归档" : "已归档"}</span>` +
      `<b>${fmtPrice(row.price)} ${escapeHtml(row.currency || "KRW")}</b></div>` +
      `<time>${escapeHtml(row.date || "--")}</time>` +
    `</div>`
  )).join("");
}

function chartSeries(quotes) {
  const labels = [];
  const labelSet = new Set();
  for (const q of quotes || []) {
    for (const p of q.trend?.points || []) {
      const label = String(p.time || "").slice(11, 16) || String(p.time || "");
      if (label && !labelSet.has(label)) {
        labelSet.add(label);
        labels.push(label);
      }
    }
  }
  const series = (quotes || []).map((q) => {
    const map = new Map((q.trend?.points || []).map((p) => {
      const label = String(p.time || "").slice(11, 16) || String(p.time || "");
      return [label, num(p.pct)];
    }));
    const color = q.accent === "violet" ? "#a78bfa" : "#66e5d7";
    return {
      name: q.name,
      type: "line",
      smooth: true,
      symbol: "none",
      lineStyle: {
        width: 3,
        color,
        shadowColor: color,
        shadowBlur: 8,
      },
      emphasis: { focus: "series" },
      data: labels.map((label) => map.get(label)),
    };
  });
  return { labels, series };
}

function renderChart(quotes) {
  if (!krChart) {
    krChart = echarts.init($("kr-chart"));
    window.addEventListener("resize", () => krChart && krChart.resize());
  }
  const { labels, series } = chartSeries(quotes);
  const synthetic = (quotes || []).some((q) => q.trend?.synthetic);
  $("kr-trend-state").textContent = synthetic ? "分时源失败，显示参考线" : "真实分时";
  $("kr-chart-note").textContent = synthetic
    ? "分时接口暂不可用，当前图只用昨收与最新价生成参考线；价格卡片仍来自实时快照。"
    : "以各自昨收为基准归一化，便于比较从 KRX 09:00 开盘至当前/15:30 收盘的表现。";
  if (!labels.length) {
    krChart.clear();
    return;
  }
  krChart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      backgroundColor: "rgba(6,10,22,0.94)",
      borderColor: "rgba(136,160,210,0.22)",
      textStyle: { color: "#edf5ff" },
      valueFormatter: (v) => (num(v) === null ? "--" : `${Number(v).toFixed(2)}%`),
    },
    grid: { left: 8, right: 18, top: 14, bottom: 26, containLabel: true },
    xAxis: {
      type: "category",
      data: labels,
      boundaryGap: false,
      axisLabel: { color: "#677489", fontSize: 11 },
      axisLine: { lineStyle: { color: "rgba(136,160,210,0.18)" } },
      axisTick: { show: false },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: "#8d99ad", formatter: (v) => `${v > 0 ? "+" : ""}${Number(v).toFixed(2)}%` },
      splitLine: { lineStyle: { color: "rgba(136,160,210,0.11)" } },
    },
    series,
  }, true);
}

function renderStatus(data) {
  $("kr-updated-at").textContent = `最近更新 ${escapeHtml(data.updated_at || "--")}`;
}

async function loadKoreaWatch() {
  if (krLoading) return;
  krLoading = true;
  try {
    const resp = await fetch("/api/global-semiconductors", { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    renderMarket(data.market || {});
    renderCards(data.quotes || [], data.market || {});
    renderChart(data.quotes || []);
    renderArchive(data.archive || []);
    renderStatus(data);
  } catch (e) {
    $("kr-market-state").innerHTML = `<i></i>数据获取失败`;
    $("kr-trend-state").textContent = "失败";
    $("kr-updated-at").textContent = String(e.message || e);
  } finally {
    krLoading = false;
  }
}

loadKoreaWatch();
window.setInterval(loadKoreaWatch, KR_REFRESH_MS);
