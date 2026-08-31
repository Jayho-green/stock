/* 0AMV 板块轮动策略回测 */
(function () {
  "use strict";

  const CSS = getComputedStyle(document.documentElement);
  const C = (n) => CSS.getPropertyValue(n).trim();
  const COL = {
    text: C("--text"), muted: C("--muted"), faint: C("--faint"),
    line: C("--line"), lineSoft: C("--line-soft"), panel: C("--panel"),
    up: C("--up"), down: C("--down"), brass: C("--brass"),
    S1: C("--s1"), S2: C("--s2"), S3: C("--s3"),
  };
  const NAMES = { S1: "S1 分档止盈", S2: "S2 首阴清仓", S3: "S3 首阴减半" };
  const KEYS = ["S1", "S2", "S3"];

  const f2 = (v) => (v == null || Number.isNaN(v) ? "--" : Number(v).toFixed(2));
  const sg = (v, d = 2) => (v == null || Number.isNaN(v) ? "--" : (v > 0 ? "+" : "") + Number(v).toFixed(d));
  const cls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");

  const tip = document.getElementById("tip");
  function showTip(e, html) {
    tip.innerHTML = html; tip.style.opacity = 1;
    const r = tip.getBoundingClientRect();
    let x = e.clientX + 14, y = e.clientY + 14;
    if (x + r.width > innerWidth - 8) x = e.clientX - r.width - 14;
    if (y + r.height > innerHeight - 8) y = e.clientY - r.height - 14;
    tip.style.left = x + "px"; tip.style.top = y + "px";
  }
  const hideTip = () => (tip.style.opacity = 0);

  let DATA = null, current = "S1";
  const charts = {};
  const mk = (id) => (charts[id] = charts[id] || echarts.init(document.getElementById(id), null, { renderer: "canvas" }));
  const axis = { axisLine: { lineStyle: { color: COL.line } }, axisLabel: { color: COL.faint, fontSize: 10 } };
  const tipBase = {
    backgroundColor: COL.panel, borderColor: COL.line,
    textStyle: { color: COL.text, fontSize: 12 },
  };

  /* ---------- 概览 tiles ---------- */
  function renderTiles() {
    const s = DATA.strategies[current].summary;
    const t = [
      ["累计收益", sg(s.cumulative, 1) + "%", `年化 ${sg(s.annualized, 1)}% · ${s.rounds} 轮`, s.cumulative],
      ["每轮均值", sg(s.mean) + "%", `中位 ${sg(s.median)}%`, s.mean],
      ["胜率", s.win_rate.toFixed(1) + "%", `最好 ${sg(s.best, 1)}% / 最差 ${sg(s.worst, 1)}%`, s.win_rate - 50],
      ["最大回撤", sg(s.max_drawdown, 1) + "%", `平均持有 ${s.avg_hold.toFixed(1)} 天 · 在场 ${s.days_in_market} 天`, s.max_drawdown],
    ];
    document.getElementById("tiles").innerHTML = t
      .map((x) => `<div class="tile"><div class="k">${x[0]}</div>
        <div class="v ${cls(x[3])}">${x[1]}</div><div class="n">${x[2]}</div></div>`)
      .join("");
  }

  /* ---------- 资金曲线(3策略同图) ---------- */
  function renderEquity() {
    const all = new Set();
    KEYS.forEach((k) => DATA.strategies[k].rounds.forEach((r) => all.add(r.entry)));
    const dates = [...all].sort();
    const series = KEYS.map((k) => {
      const m = new Map(DATA.strategies[k].rounds.map((r) => [r.entry, (r.equity - 1) * 100]));
      let last = 0;
      const data = dates.map((d) => (m.has(d) ? (last = m.get(d)) : last));
      return {
        name: NAMES[k], type: "line", data, showSymbol: false, smooth: false,
        lineStyle: { color: COL[k], width: k === current ? 2.8 : 1.6, opacity: k === current ? 1 : 0.55 },
        z: k === current ? 5 : 2,
      };
    });
    mk("equity-chart").setOption({
      backgroundColor: "transparent", animation: false,
      grid: { left: 54, right: 20, top: 16, bottom: 34 },
      tooltip: {
        trigger: "axis", ...tipBase,
        formatter: (ps) => `<b>${ps[0].axisValue}</b>` +
          ps.map((p) => `<div style="display:flex;justify-content:space-between;gap:16px">
            <span><span style="color:${p.color}">■</span> ${p.seriesName}</span><b>${sg(p.data, 1)}%</b></div>`).join(""),
      },
      xAxis: { type: "category", data: dates, boundaryGap: false, ...axis, splitLine: { show: false } },
      yAxis: {
        type: "value", ...axis, splitLine: { lineStyle: { color: COL.lineSoft } },
        axisLabel: { color: COL.faint, fontSize: 10, formatter: "{value}%" },
      },
      series,
    }, true);
    document.getElementById("eq-legend").innerHTML = KEYS.map((k) =>
      `<span class="lg"><i class="sw" style="background:${COL[k]};height:${k === current ? 4 : 2}px"></i>${NAMES[k]}</span>`
    ).join("");
  }

  /* ---------- 分年度 ---------- */
  function renderYearly() {
    const years = [...new Set(KEYS.flatMap((k) => DATA.strategies[k].yearly.map((y) => y.yr)))].sort();
    mk("yearly-chart").setOption({
      backgroundColor: "transparent", animation: false,
      grid: { left: 50, right: 16, top: 30, bottom: 28 },
      legend: {
        data: KEYS.map((k) => NAMES[k]), top: 0, textStyle: { color: COL.muted, fontSize: 11 },
        itemWidth: 12, itemHeight: 8,
      },
      tooltip: { trigger: "axis", ...tipBase, valueFormatter: (v) => sg(v, 1) + "%" },
      xAxis: { type: "category", data: years, ...axis },
      yAxis: {
        type: "value", ...axis, splitLine: { lineStyle: { color: COL.lineSoft } },
        axisLabel: { color: COL.faint, fontSize: 10, formatter: "{value}%" },
      },
      series: KEYS.map((k) => {
        const m = new Map(DATA.strategies[k].yearly.map((y) => [y.yr, y.cum]));
        return {
          name: NAMES[k], type: "bar", data: years.map((y) => m.get(y) ?? 0),
          itemStyle: { color: COL[k], borderRadius: [3, 3, 0, 0] }, barGap: "12%",
        };
      }),
    }, true);
  }

  /* ---------- 板块贡献(发散条形) ---------- */
  function renderSector() {
    const legs = DATA.strategies[current].legs;
    mk("sector-chart").setOption({
      backgroundColor: "transparent", animation: false,
      grid: { left: 92, right: 46, top: 12, bottom: 26 },
      tooltip: {
        trigger: "item", ...tipBase,
        formatter: (p) => {
          const r = legs[p.dataIndex];
          return `<b>${r.name}</b><br/>入选 ${r.n} 次　胜率 ${r.win.toFixed(0)}%<br/>
            平均单腿 ${sg(r.mean)}%<br/>累计贡献 <b>${sg(r.contrib)}%</b>`;
        },
      },
      xAxis: {
        type: "value", ...axis, splitLine: { lineStyle: { color: COL.lineSoft } },
        axisLabel: { color: COL.faint, fontSize: 10, formatter: "{value}%" },
      },
      yAxis: { type: "category", data: legs.map((l) => l.name), ...axis, splitLine: { show: false } },
      series: [{
        type: "bar", data: legs.map((l) => l.contrib),
        itemStyle: { color: (p) => (p.data >= 0 ? COL.up : COL.down), borderRadius: 3 },
        label: {
          show: true, position: "right", fontSize: 10, color: COL.muted,
          formatter: (p) => sg(p.data, 1),
        },
      }],
    }, true);
  }

  /* ---------- 逐轮盈亏 ---------- */
  function renderRounds() {
    const r = DATA.strategies[current].rounds;
    mk("rounds-chart").setOption({
      backgroundColor: "transparent", animation: false,
      grid: { left: 50, right: 16, top: 14, bottom: 46 },
      tooltip: {
        trigger: "item", ...tipBase,
        formatter: (p) => {
          const x = r[p.dataIndex];
          return `<b>${x.entry} → ${x.exit}</b>　持有 ${x.hold} 天
            <div style="margin-top:4px">40%　${x.hi_name}　<b>${sg(x.hi_pnl)}%</b></div>
            <div>60%　${x.lo_name}　<b>${sg(x.lo_pnl)}%</b></div>
            <div style="margin-top:4px">轮收益 <b>${sg(x.pnl)}%</b></div>`;
        },
      },
      xAxis: { type: "category", data: r.map((x) => x.entry), ...axis, axisLabel: { color: COL.faint, fontSize: 9, rotate: 45 } },
      yAxis: {
        type: "value", ...axis, splitLine: { lineStyle: { color: COL.lineSoft } },
        axisLabel: { color: COL.faint, fontSize: 10, formatter: "{value}%" },
      },
      series: [{
        type: "bar", data: r.map((x) => x.pnl),
        itemStyle: { color: (p) => (p.data >= 0 ? COL.up : COL.down), borderRadius: [2, 2, 0, 0] },
      }],
    }, true);
  }

  /* ---------- 明细表 ---------- */
  function row(x) {
    return `<tr class="${Math.abs(x.pnl) >= 8 ? "big" : ""}">
      <td>${x.entry}</td><td>${x.exit}</td><td>${x.hold}</td>
      <td>${x.hi_name}</td><td class="${cls(x.hi_pnl)}">${sg(x.hi_pnl)}</td>
      <td>${x.lo_name}</td><td class="${cls(x.lo_pnl)}">${sg(x.lo_pnl)}</td>
      <td class="${cls(x.pnl)}"><b>${sg(x.pnl)}</b></td></tr>`;
  }
  function renderTables() {
    const r = DATA.strategies[current].rounds;
    const loss = r.filter((x) => x.pnl < 0), win = r.filter((x) => x.pnl >= 0);
    const sum = (a) => a.reduce((s, x) => s + x.pnl, 0);
    document.querySelector("#loss-table tbody").innerHTML = loss.map(row).join("");
    document.querySelector("#win-table tbody").innerHTML = win.map(row).join("");
    document.getElementById("loss-title").innerHTML =
      `亏损轮次 <span class="muted" style="font-weight:400;font-size:13px">${loss.length} 轮 · 合计 ${sg(sum(loss), 1)}%</span>`;
    document.getElementById("win-title").innerHTML =
      `盈利轮次 <span class="muted" style="font-weight:400;font-size:13px">${win.length} 轮 · 合计 ${sg(sum(win), 1)}%</span>`;
  }

  /* ---------- 敏感性 ---------- */
  function renderSensitivity() {
    const rows = DATA.sensitivity.map((s) => `<tr class="${s.flow_key === "mf2" ? "big" : ""}">
      <td>${s.flow}${s.flow_key === "mf2" ? " <span class='tag'>主口径</span>" : ""}</td>
      <td>${NAMES[s.strategy]}</td><td>${s.rounds}</td>
      <td class="${cls(s.mean)}">${sg(s.mean)}</td><td class="${cls(s.median)}">${sg(s.median)}</td>
      <td>${s.win_rate.toFixed(1)}%</td>
      <td class="${cls(s.cumulative)}"><b>${sg(s.cumulative, 1)}%</b></td>
      <td class="${cls(s.annualized)}">${sg(s.annualized, 1)}%</td>
      <td class="neg">${sg(s.max_drawdown, 1)}%</td></tr>`).join("");
    document.querySelector("#sens-table tbody").innerHTML = rows;
  }

  function renderAll() {
    renderTiles(); renderEquity(); renderYearly(); renderSector(); renderRounds(); renderTables();
  }

  async function init() {
    const pill = document.getElementById("status-pill");
    try {
      const r = await fetch("/api/amv-strategy");
      if (!r.ok) throw new Error((await r.json()).detail || r.status);
      DATA = await r.json();
    } catch (e) {
      pill.classList.add("stale");
      pill.innerHTML = `<i></i>回测结果未生成`;
      document.getElementById("tiles").innerHTML =
        `<div class="tile"><div class="k">无数据</div><div class="v">--</div>
         <div class="n err">${e.message}<br/>请先运行 scripts/run_amv_strategy.py</div></div>`;
      return;
    }
    pill.innerHTML = `<i></i>回测于 ${DATA.generated_at}`;

    // 策略切换 chips 挂到标题右侧
    const chips = document.createElement("div");
    chips.className = "chips";
    chips.innerHTML = KEYS.map((k) =>
      `<button class="chip ${k === current ? "on" : ""}" data-s="${k}">${NAMES[k]}</button>`).join("");
    document.querySelector(".top-actions").prepend(chips);
    chips.addEventListener("click", (e) => {
      const b = e.target.closest(".chip");
      if (!b) return;
      current = b.dataset.s;
      chips.querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c.dataset.s === current));
      renderAll();
    });

    renderAll();
    renderSensitivity();
    addEventListener("resize", () => Object.values(charts).forEach((c) => c.resize()));
  }

  document.addEventListener("DOMContentLoaded", init);
})();
