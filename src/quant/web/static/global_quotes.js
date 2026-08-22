"use strict";

(function () {
  const REFRESH_MS = 8000;
  const root = document.getElementById("global-quotes");
  const timeEl = document.getElementById("global-quotes-time");
  if (!root) return;

  let loading = false;

  function clsOf(v) {
    if (v === null || v === undefined || Number.isNaN(Number(v)) || Number(v) === 0) return "flat-val";
    return Number(v) > 0 ? "up" : "down";
  }

  function fmtPrice(v) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "--";
    return Number(v).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  }

  function fmtPct(v) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "--";
    return `${Number(v) > 0 ? "+" : ""}${Number(v).toFixed(2)}%`;
  }

  function fmtChange(v) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "";
    return `${Number(v) > 0 ? "+" : ""}${Number(v).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;
  }

  function render(quotes) {
    root.innerHTML = (quotes || []).map((q) => {
      const cls = q.error ? "flat-val" : clsOf(q.change_pct);
      const change = fmtChange(q.change);
      return (
        `<div class="global-quote-card ${cls}${q.error ? " error" : ""}">` +
          `<span>${escapeHtml(q.name || q.code || "--")}</span>` +
          `<b>${fmtPrice(q.price)}</b>` +
          `<em>${fmtPct(q.change_pct)}${change ? ` · ${change}` : ""}</em>` +
        `</div>`
      );
    }).join("");
  }

  function setTime(text) {
    if (timeEl) timeEl.textContent = text;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  async function loadGlobalQuotes() {
    if (loading) return;
    loading = true;
    try {
      const resp = await fetch("/api/global-quotes", { cache: "no-store" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      render(data.quotes || []);
      const stamp = data.updated_at ? data.updated_at.slice(11, 19) : new Date().toLocaleTimeString();
      setTime(`${stamp} · 8秒刷新`);
    } catch (e) {
      setTime("行情获取失败");
      root.querySelectorAll(".global-quote-card").forEach((el) => el.classList.add("error"));
    } finally {
      loading = false;
    }
  }

  loadGlobalQuotes();
  window.setInterval(loadGlobalQuotes, REFRESH_MS);
})();
