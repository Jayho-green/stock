const STORAGE = {
  cache: "quant.news.window.cache",
  seen: "quant.news.window.seen",
  notified: "quant.news.window.notified",
  settings: "quant.news.window.settings",
};

const DEFAULT_SETTINGS = {
  live: true,
  notifications: true,
  notificationScope: "watchlist",
  refreshSeconds: 60,
  pinned: true,
  compact: false,
};

let settings = readJSON(STORAGE.settings, DEFAULT_SETTINGS);
let newsItems = [];
let currentData = null;
let activeFilter = "all";
let expandedId = null;
let loading = false;
let refreshTimer = null;
let abortController = null;
let toastTimer = null;
let firstPayload = true;
let nativeReady = false;
let backtestPollTimer = null;
let seenIds = new Set(readJSON(STORAGE.seen, []));
let notifiedIds = new Set(readJSON(STORAGE.notified, []));

const list = document.getElementById("news-list");
const scrollPanel = document.getElementById("news-scroll");
const newItemsButton = document.getElementById("new-items-button");

function readJSON(key, fallback) {
  try {
    const value = JSON.parse(localStorage.getItem(key));
    return value === null || value === undefined ? fallback : value;
  } catch (_) {
    return fallback;
  }
}

function writeJSON(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (_) {
    // A full browser storage quota must not break the live feed.
  }
}

function itemId(item) {
  return String(item.id || `${item.published_at || ""}-${item.title || ""}`);
}

function directionOf(item) {
  const value = String(item.sentiment_direction || "neutral");
  return ["positive", "negative"].includes(value) ? value : "neutral";
}

function isWatchlistRelated(item) {
  return (item.related_stocks || []).some((stock) => Boolean(stock.in_watchlist));
}

function filteredItems() {
  if (activeFilter === "all") return newsItems;
  if (activeFilter === "watchlist") return newsItems.filter(isWatchlistRelated);
  return newsItems.filter((item) => directionOf(item) === activeFilter);
}

function formatTime(item) {
  return item.time || String(item.published_at || "").slice(11, 16) || "--:--";
}

function shortSource(item) {
  return String(item.source || "资讯源").split("/")[0].trim();
}

function invokeNative(method, ...args) {
  const api = window.pywebview?.api;
  if (!api || typeof api[method] !== "function") return Promise.resolve(null);
  return Promise.resolve(api[method](...args)).catch(() => null);
}

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toast.classList.remove("show"), 2200);
}

function openExternal(url) {
  if (!url) return;
  if (nativeReady) invokeNative("open_external", url);
  else window.open(url, "_blank", "noopener,noreferrer");
}

function openDashboard(code = "") {
  if (nativeReady) {
    invokeNative("open_dashboard", code);
    return;
  }
  const query = code ? `?stock=${encodeURIComponent(code)}` : "";
  window.open(`/${query}`, "_blank", "noopener,noreferrer");
}

function renderCounts() {
  const counts = {
    all: newsItems.length,
    positive: newsItems.filter((item) => directionOf(item) === "positive").length,
    negative: newsItems.filter((item) => directionOf(item) === "negative").length,
    watchlist: newsItems.filter(isWatchlistRelated).length,
  };
  for (const [name, count] of Object.entries(counts)) {
    const target = document.getElementById(`count-${name}`);
    if (target) target.textContent = String(count);
  }
}

function renderUnread() {
  const unread = newsItems.filter((item) => !seenIds.has(itemId(item))).length;
  document.getElementById("unread-count").textContent = String(unread);
  document.querySelector(".unread-summary")?.classList.toggle("none", unread === 0);
  invokeNative("set_badge", unread);
}

function markRead(id) {
  if (!id || seenIds.has(id)) return;
  seenIds.add(id);
  trimAndStoreSet(STORAGE.seen, seenIds);
  renderUnread();
}

function markAllRead() {
  for (const item of newsItems) seenIds.add(itemId(item));
  trimAndStoreSet(STORAGE.seen, seenIds);
  renderNewsList();
  showToast("当前资讯已全部标记为已读");
}

function trimAndStoreSet(key, values, limit = 1200) {
  const rows = [...values];
  const trimmed = rows.length > limit ? rows.slice(rows.length - limit) : rows;
  if (key === STORAGE.seen) seenIds = new Set(trimmed);
  if (key === STORAGE.notified) notifiedIds = new Set(trimmed);
  writeJSON(key, trimmed);
}

function createStockChip(stock) {
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = `stock-chip${stock.in_watchlist ? " watch" : ""}`;
  chip.title = `${stock.reason || "关联标的"}${stock.in_watchlist ? " · 已在自选池" : ""}`;

  const name = document.createElement("span");
  name.textContent = stock.name || stock.code || "未知";
  const code = document.createElement("em");
  code.textContent = stock.code || "";
  chip.append(name, code);
  chip.addEventListener("click", (event) => {
    event.stopPropagation();
    openDashboard(stock.code || "");
  });
  return chip;
}

function createNewsItem(item) {
  const id = itemId(item);
  const direction = directionOf(item);
  const watchRelated = isWatchlistRelated(item);
  const article = document.createElement("article");
  article.className = `news-item ${direction}${seenIds.has(id) ? "" : " unread"}${expandedId === id ? " expanded" : ""}`;
  article.tabIndex = 0;
  article.setAttribute("role", "button");
  article.setAttribute("aria-expanded", String(expandedId === id));

  const timeBlock = document.createElement("div");
  timeBlock.className = "news-time-block";
  const time = document.createElement("time");
  time.dateTime = item.published_at || "";
  time.textContent = formatTime(item);
  const source = document.createElement("span");
  source.className = "news-source";
  source.textContent = shortSource(item);
  source.title = item.source || "";
  timeBlock.append(time, source);

  const content = document.createElement("div");
  content.className = "news-content";
  const meta = document.createElement("div");
  meta.className = "news-meta";
  const sentiment = document.createElement("span");
  sentiment.className = `sentiment ${direction}`;
  sentiment.textContent = item.sentiment || "中性";
  meta.appendChild(sentiment);

  const scoreValue = Number(item.sentiment_score || 0);
  if (scoreValue !== 0) {
    const score = document.createElement("span");
    score.className = "impact-score";
    score.textContent = `影响 ${Math.abs(scoreValue)}`;
    meta.appendChild(score);
  }
  if (item.impact_level) {
    const grade = document.createElement("span");
    grade.className = `impact-grade grade-${String(item.impact_level).toLowerCase()}`;
    grade.textContent = `${item.impact_level}·${item.impact_label || "影响"}`;
    grade.title = `${item.impact_basis || "模型预估"} · 强度 ${Number(item.impact_score_adjusted || 0).toFixed(1)}`;
    meta.appendChild(grade);
  }
  if (watchRelated) {
    const flag = document.createElement("span");
    flag.className = "watch-flag";
    flag.textContent = "自选相关";
    meta.appendChild(flag);
  }

  const title = document.createElement("h2");
  title.className = "news-title";
  title.textContent = item.title || "未命名资讯";
  const summary = document.createElement("p");
  summary.className = "news-summary";
  summary.textContent = item.summary && item.summary !== item.title ? item.summary : "暂无更多摘要";
  content.append(meta, title, summary);

  const stocks = document.createElement("div");
  stocks.className = "stock-list";
  for (const stock of item.related_stocks || []) stocks.appendChild(createStockChip(stock));
  if (stocks.children.length) content.appendChild(stocks);

  const details = document.createElement("div");
  details.className = "news-details";
  const analysis = document.createElement("span");
  const analysisLabel = item.analysis_source === "glm" || String(item.analysis_source || "").startsWith("glm")
    ? "GLM 深度判断"
    : "规则快速判断";
  const impactLabel = item.impact_basis
    ? ` · ${item.impact_basis} ${Number(item.impact_score_adjusted || 0).toFixed(0)}`
    : "";
  analysis.textContent = analysisLabel + impactLabel;
  details.appendChild(analysis);
  if (item.url) {
    const original = document.createElement("a");
    original.href = item.url;
    original.textContent = "查看原文 ↗";
    original.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      openExternal(item.url);
    });
    details.appendChild(original);
  }
  content.appendChild(details);
  article.append(timeBlock, content);

  const toggle = () => {
    markRead(id);
    expandedId = expandedId === id ? null : id;
    renderNewsList({ preserveScroll: true });
  };
  article.addEventListener("click", toggle);
  article.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggle();
    }
  });
  return article;
}

function renderNewsList({ preserveScroll = false } = {}) {
  const scrollTop = list.scrollTop;
  list.innerHTML = "";
  const items = filteredItems();
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    const radar = document.createElement("div");
    radar.className = "radar-mark";
    radar.setAttribute("aria-hidden", "true");
    radar.appendChild(document.createElement("i"));
    const title = document.createElement("strong");
    title.textContent = newsItems.length ? "当前筛选暂无消息" : "尚未获取到资讯";
    const note = document.createElement("span");
    note.textContent = settings.live ? "系统会在下一轮刷新后自动更新" : "实时拉取已关闭，当前只显示本地缓存";
    empty.append(radar, title, note);
    list.appendChild(empty);
  } else {
    const fragment = document.createDocumentFragment();
    for (const item of items) fragment.appendChild(createNewsItem(item));
    list.appendChild(fragment);
  }
  list.setAttribute("aria-busy", "false");
  if (preserveScroll) list.scrollTop = scrollTop;
  renderCounts();
  renderUnread();
}

function parseDate(value) {
  if (!value) return null;
  const date = new Date(String(value).replace(" ", "T"));
  return Number.isNaN(date.getTime()) ? null : date;
}

function renderStatus(data, mode = "live") {
  const timestamp = data.cached_at || data.updated_at;
  const parsed = parseDate(timestamp);
  const ageMinutes = parsed ? (Date.now() - parsed.getTime()) / 60000 : Infinity;
  const cached = Boolean(data.from_disk_cache) || mode === "cache";
  const stale = ageMinutes > 10;
  const timeText = parsed
    ? parsed.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })
    : "--:--";
  document.getElementById("updated-time").textContent = timeText;
  document.getElementById("live-dot").className = `live-dot${cached || !settings.live ? " offline" : stale ? " stale" : ""}`;
  document.getElementById("live-label").textContent = !settings.live
    ? "实时已关闭"
    : cached
      ? "本地缓存"
      : stale
        ? "等待新消息"
        : "实时监控";
  const sourceCount = (data.sources || []).filter((source) => source.ok).length;
  const connection = document.getElementById("connection-label");
  connection.classList.remove("error");
  connection.textContent = `${sourceCount || 0} 个资讯源 · ${newsItems.length} 条消息`;
}

function storeCache(data) {
  const payload = { ...data, cached_at: data.cached_at || data.updated_at || new Date().toISOString() };
  writeJSON(STORAGE.cache, payload);
}

function payloadNewItems(items) {
  return items.filter((item) => !newsItems.some((existing) => itemId(existing) === itemId(item)));
}

function shouldNotify(item) {
  if (settings.notificationScope === "all") return true;
  if (settings.notificationScope === "impactful") return directionOf(item) !== "neutral";
  return isWatchlistRelated(item) && directionOf(item) !== "neutral";
}

function notifyNewItems(items) {
  if (!settings.notifications || !items.length) return;
  const important = items.filter((item) => !notifiedIds.has(itemId(item)) && shouldNotify(item));
  for (const item of items) notifiedIds.add(itemId(item));
  trimAndStoreSet(STORAGE.notified, notifiedIds);
  if (!important.length) return;

  const first = important[0];
  const grade = first.impact_level ? `${first.impact_level}·${first.impact_label || "影响"} ` : "";
  const title = important.length === 1
    ? `${grade}${first.sentiment || "新消息"}${isWatchlistRelated(first) ? " · 自选相关" : ""}`
    : `市场雷达 · 新增 ${important.length} 条重要资讯`;
  const body = important.length === 1 ? first.title : `${first.title} 等 ${important.length} 条`;
  if (nativeReady) {
    invokeNative("notify", title, body);
  } else if ("Notification" in window && Notification.permission === "granted") {
    new Notification(title, { body });
  }
}

function applyPayload(data, mode = "live") {
  const incoming = Array.isArray(data.items) ? data.items : [];
  const additions = firstPayload ? [] : payloadNewItems(incoming);

  if (firstPayload && seenIds.size === 0) {
    for (const item of incoming) seenIds.add(itemId(item));
    trimAndStoreSet(STORAGE.seen, seenIds);
  }
  if (firstPayload && notifiedIds.size === 0) {
    for (const item of incoming) notifiedIds.add(itemId(item));
    trimAndStoreSet(STORAGE.notified, notifiedIds);
  }

  newsItems = incoming;
  currentData = data;
  firstPayload = false;
  storeCache(data);
  renderStatus(data, mode);
  renderNewsList();

  if (additions.length) {
    notifyNewItems(additions);
    if (list.scrollTop > 80) {
      newItemsButton.textContent = `${additions.length} 条新资讯`;
      newItemsButton.hidden = false;
    }
  }
}

async function getJSON(url, timeoutMs = 60000) {
  if (abortController) abortController.abort();
  abortController = new AbortController();
  const active = abortController;
  const timeout = window.setTimeout(() => active.abort(), timeoutMs);
  try {
    const response = await fetch(url, { cache: "no-store", signal: active.signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json();
  } finally {
    window.clearTimeout(timeout);
    if (abortController === active) abortController = null;
  }
}

function setLoading(value) {
  loading = Boolean(value);
  const refresh = document.getElementById("refresh-button");
  refresh.disabled = loading;
  refresh.classList.toggle("loading", loading);
  const dot = document.getElementById("live-dot");
  if (settings.live) dot.classList.toggle("loading", loading);
}

async function loadDiskCache() {
  const browserCache = readJSON(STORAGE.cache, null);
  if (browserCache?.items?.length) applyPayload({ ...browserCache, from_disk_cache: true }, "cache");
  try {
    const disk = await getJSON("/api/news/cache?limit=80", 10000);
    if (disk.items?.length) applyPayload(disk, "cache");
  } catch (_) {
    if (!newsItems.length) showConnectionError("本地缓存读取失败");
  }
}

async function refreshNews({ manual = false } = {}) {
  if (!settings.live) {
    if (manual) showToast("实时拉取已关闭，可在提醒设置中开启");
    return;
  }
  if (loading) return;
  setLoading(true);
  try {
    const data = await getJSON("/api/news?limit=80&today=1");
    applyPayload(data, data.from_disk_cache ? "cache" : "live");
    if (manual) showToast(`已更新至 ${document.getElementById("updated-time").textContent}`);
  } catch (error) {
    if (error.name !== "AbortError") {
      showConnectionError(newsItems.length ? "刷新失败，继续显示本地资讯" : "资讯连接失败");
      if (manual) showToast("刷新失败，已保留本地资讯");
    }
  } finally {
    setLoading(false);
  }
}

function showConnectionError(message) {
  const connection = document.getElementById("connection-label");
  connection.textContent = message;
  connection.classList.add("error");
  document.getElementById("live-dot").className = "live-dot offline";
  document.getElementById("live-label").textContent = "连接异常";
}

function restartTimer() {
  window.clearInterval(refreshTimer);
  refreshTimer = null;
  if (!settings.live) return;
  const seconds = Math.max(30, Number(settings.refreshSeconds) || 60);
  refreshTimer = window.setInterval(() => refreshNews(), seconds * 1000);
}

function saveSettings() {
  writeJSON(STORAGE.settings, settings);
  syncSettings();
  restartTimer();
}

function syncSettings() {
  document.getElementById("live-toggle").checked = settings.live;
  document.getElementById("notification-toggle").checked = settings.notifications;
  document.getElementById("notification-scope").value = settings.notificationScope;
  document.getElementById("refresh-interval").value = String(settings.refreshSeconds);
  document.getElementById("pin-button").classList.toggle("active", settings.pinned);
  document.getElementById("pin-button").setAttribute("aria-label", settings.pinned ? "取消窗口置顶" : "窗口置顶");
  document.getElementById("compact-button").classList.toggle("active", settings.compact);
  document.body.classList.toggle("compact", settings.compact);
  if (currentData) renderStatus(currentData, currentData.from_disk_cache ? "cache" : "live");
}

function toggleSettings(open) {
  const panel = document.getElementById("settings-panel");
  const next = open === undefined ? !panel.classList.contains("open") : Boolean(open);
  if (next) toggleBacktest(false);
  panel.classList.toggle("open", next);
  panel.setAttribute("aria-hidden", String(!next));
  if (next) document.getElementById("settings-close").focus();
}

function formatPercent(value, digits = 2) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return `${number > 0 ? "+" : ""}${number.toFixed(digits)}%`;
}

function renderBacktestReport(report, state = null) {
  const overview = report?.overview || {};
  document.getElementById("metric-samples").textContent = String(overview.evaluated_samples || 0);
  document.getElementById("metric-hit-rate").textContent = overview.hit_rate == null
    ? "--"
    : `${Number(overview.hit_rate).toFixed(1)}%`;
  document.getElementById("metric-signed-return").textContent = formatPercent(overview.avg_signed_return_10m);
  document.getElementById("metric-pending").textContent = String(overview.pending_samples || 0);

  const trajectories = document.getElementById("trajectory-bars");
  trajectories.innerHTML = "";
  const horizons = report?.horizons || [];
  const scale = Math.max(0.1, ...horizons.map((row) => Math.abs(Number(row.avg_signed_return || 0))));
  for (const row of horizons) {
    const value = Number(row.avg_signed_return || 0);
    const line = document.createElement("div");
    line.className = "trajectory-row";
    const label = document.createElement("label");
    label.textContent = `${row.minutes}m`;
    const track = document.createElement("div");
    track.className = "trajectory-track";
    const bar = document.createElement("i");
    const direction = value >= 0 ? "positive" : "negative";
    bar.className = direction;
    bar.style.setProperty("--bar-width", `${Math.min(48, Math.abs(value) / scale * 48)}%`);
    track.appendChild(bar);
    const result = document.createElement("b");
    result.className = direction;
    result.textContent = formatPercent(value, 3);
    result.title = `方向命中率 ${row.hit_rate == null ? "--" : `${Number(row.hit_rate).toFixed(1)}%`}`;
    line.append(label, track, result);
    trajectories.appendChild(line);
  }

  const directions = document.getElementById("direction-results");
  directions.innerHTML = "";
  for (const row of report?.directions || []) {
    const line = document.createElement("div");
    line.className = "direction-row";
    const label = document.createElement("strong");
    label.textContent = `${row.label} · ${row.samples} 样本`;
    const hit = document.createElement("span");
    hit.textContent = row.hit_rate == null ? "--" : `${Number(row.hit_rate).toFixed(1)}%`;
    const result = document.createElement("b");
    result.textContent = formatPercent(row.avg_signed_return_10m, 3);
    line.append(label, hit, result);
    directions.appendChild(line);
  }

  const levels = document.getElementById("level-results");
  levels.innerHTML = "";
  for (const row of report?.levels || []) {
    const line = document.createElement("div");
    line.className = "level-row";
    const grade = document.createElement("strong");
    grade.className = `grade-${String(row.level || "d").toLowerCase()}`;
    grade.textContent = `${row.level}级`;
    const samples = document.createElement("span");
    samples.textContent = `${row.samples} 样本`;
    const hit = document.createElement("b");
    hit.textContent = row.hit_rate == null ? "--" : `${Number(row.hit_rate).toFixed(1)}%`;
    const result = document.createElement("em");
    result.textContent = formatPercent(row.avg_signed_return_10m, 3);
    line.append(grade, samples, hit, result);
    levels.appendChild(line);
  }

  const weights = document.getElementById("weight-results");
  weights.innerHTML = "";
  const visibleWeights = (report?.weights || [])
    .filter((row) => ["内容事件", "内容标签", "影响链路", "判断方向"].includes(row.dimension))
    .slice(0, 8);
  for (const row of visibleWeights) {
    const line = document.createElement("div");
    line.className = "weight-row";
    const dimension = document.createElement("em");
    dimension.textContent = row.dimension;
    const key = document.createElement("strong");
    key.textContent = row.key;
    key.title = `${row.status} · 命中率 ${Number(row.hit_rate || 0).toFixed(1)}%`;
    const samples = document.createElement("span");
    samples.textContent = `${row.samples}例`;
    const factor = document.createElement("b");
    factor.className = Number(row.factor) >= 1 ? "up" : "down";
    factor.textContent = `×${Number(row.factor || 1).toFixed(2)}`;
    line.append(dimension, key, samples, factor);
    weights.appendChild(line);
  }

  const empty = Number(overview.evaluated_samples || 0) === 0;
  document.getElementById("backtest-empty").hidden = !empty;
  document.querySelector(".trajectory-section").hidden = empty;
  document.querySelector(".direction-section").hidden = empty;
  document.querySelector(".level-section").hidden = empty;
  document.querySelector(".weight-section").hidden = !visibleWeights.length;

  if (state) {
    const total = Number(state.total || 0);
    const processed = Number(state.processed || 0);
    const ratio = total ? Math.min(100, processed / total * 100) : state.running ? 4 : 100;
    document.getElementById("backtest-status").textContent = state.message || "回测状态已更新";
    document.getElementById("backtest-progress").textContent = state.running ? `${processed}/${total || "--"}` : "完成";
    document.getElementById("backtest-progress-bar").style.width = `${ratio}%`;
    document.getElementById("backtest-run").disabled = Boolean(state.running);
    document.getElementById("backtest-run").textContent = state.running ? "验证中" : "更新回测";
  } else {
    const duplicateCount = Number(overview.duplicate_publications || 0);
    document.getElementById("backtest-status").textContent = duplicateCount
      ? `${overview.archived_events || 0} 个事件 · 合并 ${duplicateCount} 次重复传播`
      : `归档 ${overview.archived_events || 0} 个事件`;
    document.getElementById("backtest-progress").textContent = report?.generated_at?.slice(11, 16) || "--";
    document.getElementById("backtest-progress-bar").style.width = empty ? "0" : "100%";
  }
}

async function fetchBacktestJSON(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

async function loadBacktestReport() {
  const days = Number(document.getElementById("backtest-days").value || 30);
  try {
    const report = await fetchBacktestJSON(`/api/news/backtest?days=${days}`);
    renderBacktestReport(report);
  } catch (error) {
    document.getElementById("backtest-status").textContent = `报告读取失败：${error.message}`;
  }
}

function stopBacktestPolling() {
  window.clearTimeout(backtestPollTimer);
  backtestPollTimer = null;
}

async function pollBacktest() {
  try {
    const state = await fetchBacktestJSON("/api/news/backtest/status");
    renderBacktestReport(state.report || {}, state);
    if (state.running) {
      backtestPollTimer = window.setTimeout(pollBacktest, 1200);
    } else {
      stopBacktestPolling();
      if (Number(state.evaluated || 0) > 0) {
        showToast(`新增 ${state.evaluated} 个有效验证样本`);
        if (settings.live) refreshNews();
      }
    }
  } catch (error) {
    document.getElementById("backtest-status").textContent = `回测状态读取失败：${error.message}`;
    stopBacktestPolling();
    document.getElementById("backtest-run").disabled = false;
  }
}

async function runBacktest() {
  const days = Number(document.getElementById("backtest-days").value || 30);
  const button = document.getElementById("backtest-run");
  button.disabled = true;
  button.textContent = "准备中";
  try {
    const state = await fetchBacktestJSON("/api/news/backtest/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ days, limit: 800 }),
    });
    renderBacktestReport(state.report || {}, state);
    stopBacktestPolling();
    backtestPollTimer = window.setTimeout(pollBacktest, 500);
  } catch (error) {
    document.getElementById("backtest-status").textContent = `启动失败：${error.message}`;
    button.disabled = false;
    button.textContent = "更新回测";
  }
}

function toggleBacktest(open) {
  const panel = document.getElementById("backtest-panel");
  const next = open === undefined ? !panel.classList.contains("open") : Boolean(open);
  if (next) toggleSettings(false);
  panel.classList.toggle("open", next);
  panel.setAttribute("aria-hidden", String(!next));
  document.getElementById("backtest-button").classList.toggle("active", next);
  if (next) {
    loadBacktestReport();
    document.getElementById("backtest-close").focus();
  }
}

document.querySelectorAll(".filter").forEach((button) => {
  button.addEventListener("click", () => {
    activeFilter = button.dataset.filter || "all";
    expandedId = null;
    document.querySelectorAll(".filter").forEach((item) => item.classList.toggle("active", item === button));
    renderNewsList();
    list.scrollTop = 0;
  });
});

document.getElementById("refresh-button").addEventListener("click", () => refreshNews({ manual: true }));
document.getElementById("mark-read-button").addEventListener("click", markAllRead);
document.getElementById("dashboard-button").addEventListener("click", () => openDashboard());
document.getElementById("backtest-button").addEventListener("click", () => toggleBacktest());
document.getElementById("backtest-close").addEventListener("click", () => toggleBacktest(false));
document.getElementById("backtest-backdrop").addEventListener("click", () => toggleBacktest(false));
document.getElementById("backtest-run").addEventListener("click", runBacktest);
document.getElementById("backtest-days").addEventListener("change", loadBacktestReport);
document.getElementById("settings-button").addEventListener("click", () => toggleSettings());
document.getElementById("settings-close").addEventListener("click", () => toggleSettings(false));
document.getElementById("settings-backdrop").addEventListener("click", () => toggleSettings(false));
document.getElementById("pin-button").addEventListener("click", () => {
  settings.pinned = !settings.pinned;
  saveSettings();
  invokeNative("set_always_on_top", settings.pinned);
  showToast(settings.pinned ? "窗口已置顶" : "已取消窗口置顶");
});
document.getElementById("compact-button").addEventListener("click", () => {
  settings.compact = !settings.compact;
  saveSettings();
  invokeNative("resize_window", settings.compact);
});
document.getElementById("live-toggle").addEventListener("change", (event) => {
  settings.live = event.target.checked;
  saveSettings();
  if (settings.live) refreshNews({ manual: true });
  else {
    if (abortController) abortController.abort();
    setLoading(false);
    if (currentData) renderStatus(currentData, "cache");
  }
});
document.getElementById("notification-toggle").addEventListener("change", (event) => {
  settings.notifications = event.target.checked;
  saveSettings();
});
document.getElementById("notification-scope").addEventListener("change", (event) => {
  settings.notificationScope = event.target.value;
  saveSettings();
});
document.getElementById("refresh-interval").addEventListener("change", (event) => {
  settings.refreshSeconds = Number(event.target.value) || 60;
  saveSettings();
});
newItemsButton.addEventListener("click", () => {
  list.scrollTo({ top: 0, behavior: "smooth" });
  newItemsButton.hidden = true;
});
list.addEventListener("scroll", () => {
  if (list.scrollTop < 40) newItemsButton.hidden = true;
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    if (document.getElementById("backtest-panel").classList.contains("open")) toggleBacktest(false);
    else if (document.getElementById("settings-panel").classList.contains("open")) toggleSettings(false);
    else if (expandedId) {
      expandedId = null;
      renderNewsList({ preserveScroll: true });
    }
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "r") {
    event.preventDefault();
    refreshNews({ manual: true });
  }
});

window.addEventListener("pywebviewready", () => {
  nativeReady = true;
  invokeNative("set_always_on_top", settings.pinned);
  invokeNative("resize_window", settings.compact);
  renderUnread();
});

async function boot() {
  settings = { ...DEFAULT_SETTINGS, ...settings };
  syncSettings();
  await loadDiskCache();
  if (settings.live) await refreshNews();
  restartTimer();
}

boot();
