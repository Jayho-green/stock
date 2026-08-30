/* 0AMV 无穷成本均线面板 */
(function () {
  "use strict";

  const CSS = getComputedStyle(document.documentElement);
  const C = (name) => CSS.getPropertyValue(name).trim();
  const COLORS = {
    text: C("--text"), muted: C("--muted"), faint: C("--faint"),
    line: C("--line"), lineSoft: C("--line-soft"), panel: C("--panel"),
    up: C("--up"), down: C("--down"), amv0: C("--amv0"), brass: C("--brass"),
    buy: C("--buy"),
  };

  const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "--" : Number(v).toFixed(d));
  const signed = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "--" : (v > 0 ? "+" : "") + Number(v).toFixed(d));

  let overview = null;
  let sortKey = "cys0";
  let sortAsc = false;
  let current = null;
  let chart = null;
  let breadthChart = null;
  let pollTimer = null;

  async function getJSON(url, options) {
    const r = await fetch(url, options);
    if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => "")}`.slice(0, 160));
    return r.json();
  }

  /* ---------- 顶部状态 ---------- */
  function renderStatus(status) {
    const pill = document.getElementById("status-pill");
    pill.classList.remove("stale", "busy");
    if (status.refreshing) {
      pill.classList.add("busy");
      pill.innerHTML = "<i></i>正在刷新数据…";
    } else if (status.stale) {
      pill.classList.add("stale");
      pill.innerHTML = `<i></i>缓存待更新 · ${status.session_date || "无"}`;
    } else {
      pill.innerHTML = `<i></i>数据日期 ${status.session_date} · ${(status.updated_at || "").slice(11) || "已就绪"}`;
    }

    if (status.refreshing && !pollTimer) {
      pollTimer = setInterval(async () => {
        const data = await getJSON("/api/amv0").catch(() => null);
        if (data && !data.status.refreshing) {
          clearInterval(pollTimer);
          pollTimer = null;
          applyOverview(data);
          if (current) loadSeries(current);
        } else if (data) {
          renderStatus(data.status);
        }
      }, 3000);
    }
  }

  function renderTiles(data) {
    const rows = data.rows;
    const above = rows.filter((r) => r.above0).length;
    const breadth = rows.length ? (above / rows.length) * 100 : 0;
    const avgCys = rows.length ? rows.reduce((s, r) => s + (r.cys0 || 0), 0) / rows.length : 0;
    const sorted = [...data.sectors].sort((a, b) => (b.cys0 ?? -99) - (a.cys0 ?? -99));
    const best = sorted[0], worst = sorted[sorted.length - 1];
    const tiles = [
      ["站上 0AMV 占比", `${breadth.toFixed(0)}%`, `${above} / ${rows.length} 只 · ${breadth >= 50 ? "动量市" : "反转市"}`],
      ["全市场平均 CYS0", `${signed(avgCys)}%`, avgCys >= 0 ? "整体浮盈" : "整体套牢"],
      ["最强板块", best ? best.sector : "--", best ? `均 CYS0 ${signed(best.cys0)}%` : ""],
      ["最弱板块", worst ? worst.sector : "--", worst ? `均 CYS0 ${signed(worst.cys0)}%` : ""],
    ];
    document.getElementById("tiles").innerHTML = tiles
      .map((t) => `<div class="tile"><div class="k">${t[0]}</div><div class="v">${t[1]}</div><div class="n">${t[2]}</div></div>`)
      .join("");
  }

  function renderWatchTable(watch) {
    const body = document.querySelector("#watch-table tbody");
    if (!watch || !watch.length) {
      body.innerHTML = `<tr><td colspan="4" class="muted">当前无标的进入折价区（CYS0 均高于 −6%）</td></tr>`;
      return;
    }
    body.innerHTML = watch
      .map((r) => {
        const fired = r.buy_signal === 1;
        const since = r.days_since_signal === null || r.days_since_signal === undefined
          ? "—" : (r.days_since_signal === 0 ? "今日" : `${r.days_since_signal} 日前`);
        return `<tr data-code="${r.code}">
          <td>${r.name}</td>
          <td class="neg">${signed(r.cys0)}</td>
          <td class="${r.zone === "深度折价" ? "zone-deep" : ""}">${r.zone}</td>
          <td class="${fired ? "fire" : ""}">${fired ? "▲ 今日触发" : since}</td></tr>`;
      })
      .join("");
    body.querySelectorAll("tr[data-code]").forEach((tr) => {
      tr.onclick = () => {
        current = tr.dataset.code;
        document.getElementById("picker").value = current;
        renderRankTable(overview.rows);
        loadSeries(current);
      };
    });
  }

  function renderSectorTable(sectors) {
    document.querySelector("#sector-table tbody").innerHTML = sectors
      .map(
        (s) => `<tr><td>${s.sector}</td><td>${s.count}</td>
        <td class="${s.cys0 >= 0 ? "pos" : "neg"}">${signed(s.cys0)}</td>
        <td>${s.above_pct === null ? "--" : s.above_pct.toFixed(0) + "%"}</td></tr>`
      )
      .join("");
  }

  function renderRankTable(rows) {
    const sorted = [...rows].sort((a, b) => {
      const x = a[sortKey], y = b[sortKey];
      if (typeof x === "string") return sortAsc ? x.localeCompare(y) : y.localeCompare(x);
      return sortAsc ? (x ?? 0) - (y ?? 0) : (y ?? 0) - (x ?? 0);
    });
    document.querySelector("#rank-table tbody").innerHTML = sorted
      .map(
        (r) => `<tr data-code="${r.code}" class="${r.code === current ? "active" : ""}">
        <td>${r.name}</td><td><span class="tag">${r.sector}</span></td>
        <td>${fmt(r.close, 3)}</td><td>${fmt(r.amv0, 3)}</td>
        <td class="${r.cys0 >= 0 ? "pos" : "neg"}">${signed(r.cys0)}</td>
        <td>${r.half_life ? r.half_life.toFixed(0) + "d" : "--"}</td></tr>`
      )
      .join("");
    document.querySelectorAll("#rank-table tbody tr").forEach((tr) => {
      tr.onclick = () => {
        current = tr.dataset.code;
        document.getElementById("picker").value = current;
        renderRankTable(overview.rows);
        loadSeries(current);
      };
    });
  }

  function renderPicker(rows) {
    const bySector = {};
    rows.forEach((r) => (bySector[r.sector] = bySector[r.sector] || []).push(r));
    document.getElementById("picker").innerHTML = Object.entries(bySector)
      .map(
        ([sector, items]) =>
          `<optgroup label="${sector}">` +
          items.map((r) => `<option value="${r.code}">${r.name} (${r.code})</option>`).join("") +
          `</optgroup>`
      )
      .join("");
  }

  /* ---------- 市场宽度 ---------- */
  function renderBreadth(points) {
    if (!breadthChart) breadthChart = echarts.init(document.getElementById("breadth-chart"), null, { renderer: "canvas" });
    breadthChart.setOption({
      backgroundColor: "transparent",
      grid: { left: 44, right: 16, top: 14, bottom: 24 },
      tooltip: {
        trigger: "axis", backgroundColor: COLORS.panel, borderColor: COLORS.line,
        textStyle: { color: COLORS.text, fontSize: 12 },
        formatter: (p) => `${p[0].axisValue}<br/>站上 0AMV：<b>${p[0].data}%</b><br/>${p[0].data >= 50 ? "动量市" : "反转市"}`,
      },
      xAxis: {
        type: "category", data: points.map((p) => p.date), boundaryGap: false,
        axisLine: { lineStyle: { color: COLORS.line } }, axisLabel: { color: COLORS.faint, fontSize: 10 },
      },
      yAxis: {
        type: "value", min: 0, max: 100,
        splitLine: { lineStyle: { color: COLORS.lineSoft } },
        axisLabel: { color: COLORS.faint, fontSize: 10, formatter: "{value}%" },
      },
      series: [{
        type: "line", data: points.map((p) => p.value), showSymbol: false,
        lineStyle: { color: COLORS.amv0, width: 2 },
        areaStyle: { color: "rgba(130,100,230,0.14)" },
        markLine: {
          silent: true, symbol: "none",
          data: [{ yAxis: 50 }],
          lineStyle: { color: COLORS.brass, type: "dashed", width: 1.5 },
          label: { color: COLORS.faint, fontSize: 10, formatter: "50%" },
        },
      }],
    });
  }

  /* ---------- K 线 ---------- */
  function renderKline(s) {
    if (!chart) chart = echarts.init(document.getElementById("chart"), null, { renderer: "canvas" });
    const volColors = s.ohlc.map((o) => (o[1] >= o[0] ? COLORS.up : COLORS.down));
    chart.setOption(
      {
        backgroundColor: "transparent",
        animation: false,
        axisPointer: { link: [{ xAxisIndex: "all" }], label: { backgroundColor: COLORS.panel } },
        tooltip: {
          trigger: "axis", axisPointer: { type: "cross" },
          backgroundColor: COLORS.panel, borderColor: COLORS.line,
          textStyle: { color: COLORS.text, fontSize: 12 },
          formatter: (ps) => {
            const i = ps[0].dataIndex;
            const o = s.ohlc[i];
            const pct = i > 0 ? ((o[1] / s.ohlc[i - 1][1] - 1) * 100) : 0;
            const dir = o[1] >= o[0] ? "涨" : "跌";
            const isBuy = (s.buy_marks || []).some((m) => m[0] === i);
            const isAlign = (s.align_marks || []).some((m) => m[0] === i);
            let tail = "";
            if (isBuy) tail += `<br/><span style="color:${COLORS.buy}">▲ 超跌反转信号</span>（深度折价 + 乖离回升）`;
            if (isAlign) tail += `<br/><span style="color:${COLORS.faint}">○ 三线多头排列成立</span>（滞后信号，仅作趋势确认）`;
            return `<b>${s.dates[i]}</b>　${s.name}<br/>
              开 ${fmt(o[0], 3)}　高 ${fmt(o[3], 3)}<br/>
              低 ${fmt(o[2], 3)}　收 <b>${fmt(o[1], 3)}</b>（${dir} ${signed(pct)}%）<br/>
              <span style="color:${COLORS.amv0}">■</span> 0AMV ${fmt(s.amv0[i], 4)}<br/>
              <span style="color:${COLORS.muted}">▫</span> CYC13 ${fmt(s.cyc13[i], 4)}<br/>
              CYS0 乖离 <b>${signed(s.cys0[i])}%</b>${tail}`;
          },
        },
        grid: [
          { left: 62, right: 22, top: 18, height: "52%" },
          { left: 62, right: 22, top: "62%", height: "10%" },
          { left: 62, right: 22, top: "77%", height: "15%" },
        ],
        xAxis: [
          { type: "category", data: s.dates, gridIndex: 0, axisLine: { lineStyle: { color: COLORS.line } }, axisLabel: { show: false }, splitLine: { show: false } },
          { type: "category", data: s.dates, gridIndex: 1, axisLine: { lineStyle: { color: COLORS.line } }, axisLabel: { show: false }, splitLine: { show: false } },
          { type: "category", data: s.dates, gridIndex: 2, axisLine: { lineStyle: { color: COLORS.line } }, axisLabel: { color: COLORS.faint, fontSize: 10 }, splitLine: { show: false } },
        ],
        yAxis: [
          { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: COLORS.lineSoft } }, axisLabel: { color: COLORS.faint, fontSize: 10 } },
          { scale: true, gridIndex: 1, splitNumber: 2, splitLine: { show: false }, axisLabel: { color: COLORS.faint, fontSize: 9, formatter: (v) => (v / 1e8).toFixed(1) + "亿" } },
          { scale: true, gridIndex: 2, splitLine: { lineStyle: { color: COLORS.lineSoft } }, axisLabel: { color: COLORS.faint, fontSize: 10, formatter: "{value}%" } },
        ],
        dataZoom: [
          { type: "inside", xAxisIndex: [0, 1, 2], start: 55, end: 100 },
          { type: "slider", xAxisIndex: [0, 1, 2], bottom: 4, height: 16, borderColor: COLORS.line, fillerColor: "rgba(130,100,230,0.14)", handleStyle: { color: COLORS.amv0 }, textStyle: { color: COLORS.faint, fontSize: 10 } },
        ],
        series: [
          {
            name: "K线", type: "candlestick", data: s.ohlc, xAxisIndex: 0, yAxisIndex: 0,
            itemStyle: { color: COLORS.up, color0: COLORS.down, borderColor: COLORS.up, borderColor0: COLORS.down },
          },
          {
            name: "0AMV", type: "line", data: s.amv0, xAxisIndex: 0, yAxisIndex: 0,
            showSymbol: false, smooth: false, lineStyle: { color: COLORS.amv0, width: 2.5 }, z: 5,
          },
          {
            name: "CYC13", type: "line", data: s.cyc13, xAxisIndex: 0, yAxisIndex: 0,
            showSymbol: false, lineStyle: { color: COLORS.muted, width: 1.4, type: "dashed", opacity: 0.85 },
          },
          {
            name: "成交量", type: "bar", data: s.volume, xAxisIndex: 1, yAxisIndex: 1,
            itemStyle: { color: (p) => volColors[p.dataIndex], opacity: 0.55 },
          },
          {
            name: "超跌反转信号", type: "scatter", data: s.buy_marks || [], xAxisIndex: 0, yAxisIndex: 0,
            symbol: "triangle", symbolSize: 11, symbolOffset: [0, 15], z: 12,
            itemStyle: { color: COLORS.buy, borderColor: COLORS.panel, borderWidth: 1.5 },
            tooltip: { show: false },
          },
          {
            name: "三线多头排列", type: "scatter", data: s.align_marks || [], xAxisIndex: 0, yAxisIndex: 0,
            symbol: "circle", symbolSize: 8, symbolOffset: [0, -15], z: 11,
            itemStyle: { color: "transparent", borderColor: COLORS.faint, borderWidth: 1.6 },
            tooltip: { show: false },
          },
          {
            name: "CYS0", type: "line", data: s.cys0, xAxisIndex: 2, yAxisIndex: 2,
            showSymbol: false, lineStyle: { color: COLORS.brass, width: 1.8 },
            areaStyle: { color: "rgba(210,165,82,0.13)" },
            markLine: { silent: true, symbol: "none", data: [{ yAxis: 0 }], lineStyle: { color: COLORS.line, width: 1.5 } },
          },
        ],
      },
      true
    );
    document.getElementById("chart-note").textContent =
      `${s.name}（${s.code}·${s.sector}）　红涨绿跌；紫线为 0AMV，价在其上＝持仓者整体浮盈；底部 CYS0 为价格对 0AMV 的乖离率。`;
  }

  async function loadSeries(code) {
    const days = document.getElementById("range").value;
    try {
      const s = await getJSON(`/api/amv0/series?code=${code}&days=${days}`);
      renderKline(s);
    } catch (e) {
      document.getElementById("chart-note").innerHTML = `<span class="err">K 线加载失败：${e.message}</span>`;
    }
  }

  function applyOverview(data) {
    overview = data;
    renderStatus(data.status);
    if (!data.rows.length) {
      document.getElementById("tiles").innerHTML =
        `<div class="tile"><div class="k">暂无数据</div><div class="v">--</div><div class="n">点右上「立即刷新」抓取</div></div>`;
      return;
    }
    renderTiles(data);
    renderWatchTable(data.watch);
    renderSectorTable(data.sectors);
    renderPicker(data.rows);
    if (!current || !data.rows.some((r) => r.code === current)) current = data.rows[0].code;
    document.getElementById("picker").value = current;
    renderRankTable(data.rows);
    renderBreadth(data.breadth);
  }

  async function init() {
    document.querySelectorAll("#rank-table th[data-sort]").forEach((th) => {
      th.onclick = () => {
        const key = th.dataset.sort;
        if (key === sortKey) sortAsc = !sortAsc;
        else { sortKey = key; sortAsc = key === "name" || key === "sector"; }
        renderRankTable(overview.rows);
      };
    });
    document.getElementById("picker").onchange = (e) => {
      current = e.target.value;
      renderRankTable(overview.rows);
      loadSeries(current);
    };
    document.getElementById("range").onchange = () => current && loadSeries(current);
    document.getElementById("refresh").onclick = async () => {
      const r = await getJSON("/api/amv0/refresh", { method: "POST" }).catch(() => null);
      if (r) renderStatus({ ...r.status, refreshing: true });
    };
    window.addEventListener("resize", () => { chart && chart.resize(); breadthChart && breadthChart.resize(); });

    try {
      const data = await getJSON("/api/amv0");
      applyOverview(data);
      if (current) await loadSeries(current);
    } catch (e) {
      document.getElementById("tiles").innerHTML =
        `<div class="tile"><div class="k">接口异常</div><div class="v">--</div><div class="n err">${e.message}</div></div>`;
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
