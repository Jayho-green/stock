# A股量化盯盘助手

辅助决策的 A股盯盘工具:**程序提醒,你手动下单**。盘中监控自选股的技术指标与量价异动并提醒;支持盘前选股与历史回测。不做自动下单、不碰你的钱。

设计文档见 `docs/superpowers/specs/`,实施计划见 `docs/superpowers/plans/`。

## 三条流水线,共用底层

```
数据层 / 指标层(纯函数)/ 信号层(注册表)/ 提醒层 / 日志层
   ├─ 盯盘:盘中每 N 秒 → 分钟K → 指标 → MONITOR_RULES → 去重 → 提醒
   ├─ 选股:盘前/盘后 → 日线 → SCREEN_RULES → 入选名单
   └─ 回测:历史回放 → 同一批规则 → 绩效(胜率/平均收益,计成本、无未来函数)
```

## 安装

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install pandas akshare pytest fastapi "uvicorn[standard]" httpx
```

## 配置

```bash
cp config/config.example.toml config/config.toml
# 编辑 config/config.toml:自选股、阈值、轮询间隔、提醒通道
```

## 用法

```bash
# 盯盘(交易时段内循环,触发即提醒 + 写 data/triggers.jsonl)
.venv/bin/python scripts/run_monitor.py

# 选股(科创板+创业板,并发拉日线细筛,输出 config/watchlist.generated.toml)
.venv/bin/python scripts/run_screen.py

# 回测某规则(在自己机器联网运行)
.venv/bin/python scripts/run_backtest.py 000001 ma_cross --forward 5 --days 365

# 网页面板(浏览器打开 http://127.0.0.1:8000)
.venv/bin/python scripts/run_web.py
```

### 市场雷达桌面小窗（macOS）

首次安装桌面依赖并生成可双击应用：

```bash
.venv/bin/pip install 'pywebview>=6,<7'
.venv/bin/python scripts/install_news_window_app.py
open 'dist/市场雷达.app'
```

之后可以直接从 Finder 双击 `dist/市场雷达.app`。小窗会自行启动资讯服务，不要求网页面板预先运行；资讯仍复用 `data/news_cache.json`，关闭实时拉取后继续展示本地缓存。

不生成应用包时也可直接运行：

```bash
.venv/bin/python scripts/run_news_window.py
```

## 龙虎榜 · 机构资金(http://127.0.0.1:8000/lhb)

看每日上榜个股的**机构专用席位**真实买卖(交易所约 17:30 披露),并按东财行业聚合板块净流入。
支持日期切换、板块图/排行、板块机构资金日/周趋势、按板块过滤、排序筛选搜索、行展开看上榜原因与后续表现、一键加自选。

- 数据口径:机构买卖额为交易所披露的真实机构席位成交,置信度高,但**仅覆盖当日上榜股**;
  未上榜个股没有机构数据。同股多榜单口径不同,金额取成交额最大的主榜单。
- 缓存:历史日期榜单落盘 `data/lhb_cache/{日期}.json` 永久复用;当日内存缓存 10 分钟;
  个股行业映射持久化在 `data/lhb_cache/industry_map.json`,只增量补缺
  (首次打开某日榜单需逐只查行业,约几十次请求,稍慢,之后秒开)。
- 接口:`GET /api/lhb`(最新,自动回退到最近披露日)、`GET /api/lhb?date=2026-07-10`、
  `GET /api/lhb/trends?period=daily|weekly&days=30&top=8`。

### 每日自动归档(launchd)

`scripts/run_lhb_archive.py` 收盘披露后拉取当日榜单并落盘,面板此后直接读磁盘不重复请求:

```bash
# 手动归档今天 / 补归档某天
.venv/bin/python scripts/run_lhb_archive.py
.venv/bin/python scripts/run_lhb_archive.py 2026-07-10
.venv/bin/python scripts/run_lhb_archive.py 2026-07-01 2026-07-10  # 回补区间,供趋势图使用

# 注册定时任务(每个工作日 18:30 自动归档)
cp launchd/com.jayho.quant.lhb.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jayho.quant.lhb.plist
launchctl kickstart gui/$(id -u)/com.jayho.quant.lhb   # 立即手动触发一次
tail -f data/lhb_archive.log                            # 看归档日志
```

## 0AMV · 无穷成本均线(http://127.0.0.1:8000/amv0)

指南针成本均线体系(CYC)面板。34 只板块主流 ETF 的 **K 线 × 0AMV 成本线**、板块 CYS0 汇总、
全标的排序与市场宽度。

**0AMV 是什么**:即指南针的**无穷成本均线 CYC∞**。"0" 是通达信公式里"自上市首日起/无穷"的
周期写法,不是数字零。源码为 `DMA(AMOUNT/(100*VOL), VOL/CAPITAL)`,展开即

```
0AMV_t = 换手率_t × 当日均价_t + (1 − 换手率_t) × 0AMV_{t−1}
```

本质是**以每日换手率为衰减因子的持仓成本线**,等价于筹码分布的均值。记忆半衰期约
`ln2/平均换手率` 个交易日(ETF 换手高,实际只有 4~27 天,面板"半衰期"列即此值)。
派生指标 `CYS0 = (收盘价 − 0AMV) / 0AMV × 100`,即价格对全体持仓成本的乖离率:
为正说明持仓者整体浮盈,为负说明整体套牢。

- **数据源**:本机东方财富接口不可达(代理出口为境外 IP),统一走**新浪**(日线+成交额)
  与**腾讯**(前复权价、流通份额)。
- **必须复权**:ETF 存在份额折算(拆分),新浪不复权序列里会出现单日 −50% 的**假跌**。
  用腾讯前复权价反解复权因子修正后才计算指标,否则指标会被彻底污染。
- **缓存**:`data/amv0_cache/{代码}.json` + `_meta.json`。盘后数据稳定,缓存命中直接返回;
  过期时后台线程异步刷新,页面先拿旧数据并显示"缓存待更新",不阻塞。
- **接口**:`GET /api/amv0`(总览+板块+市场宽度)、`GET /api/amv0/series?code=159825&days=250`
  (K 线+成本均线序列)、`POST /api/amv0/refresh`(手动触发后台刷新)。

### 每日收盘自动更新(launchd)

```bash
# 手动刷新(--force 忽略缓存新鲜度)
.venv/bin/python scripts/run_amv0_update.py
.venv/bin/python scripts/run_amv0_update.py --force

# 注册定时任务(每个工作日 15:35 自动刷新,全量 34 只约 15 秒)
cp launchd/com.jayho.quant.amv0.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jayho.quant.amv0.plist
launchctl kickstart gui/$(id -u)/com.jayho.quant.amv0   # 立即手动触发一次
tail -f data/amv0_update.log                             # 看刷新日志

# 停用
launchctl unload ~/Library/LaunchAgents/com.jayho.quant.amv0.plist
```

标的池在 `src/quant/amv0.py` 的 `UNIVERSE` 里维护,增删标的后重跑一次更新脚本即可。

## 每日自动选股(launchd)

`run_screen.py` 从**科创板(688/689)+创业板(300/301)**约 2000 只里,并发拉近 150 天日线、套用 `SCREEN_RULES` 细筛,入选写入 `config/watchlist.generated.toml`。

已配置 macOS launchd 定时任务,**每个工作日 15:30 收盘后自动运行**(Mac 休眠则唤醒后补跑)。

```bash
# 管理定时任务
launchctl list | grep quant.screen                                  # 查看是否注册
launchctl kickstart gui/$(id -u)/com.jayho.quant.screen             # 立即手动触发一次
launchctl unload ~/Library/LaunchAgents/com.jayho.quant.screen.plist  # 停用
tail -f data/screen.log                                             # 看运行日志/入选结果
```

股票池前缀、并发数、回溯天数在 `config/config.toml` 的 `[screen]` 调整。

## 网页面板

本地 dashboard(只读,不下单):左侧自选股实时表格(价格/涨跌/量比/RSI,触发信号的行高亮)+ 信号提醒流;右侧点击某股看分钟K线图(叠 MA5/MA20,下方 RSI 副图)。

- 技术:FastAPI 后端 + 单页静态前端(ECharts CDN),无构建步骤。
- 服务端 **30s TTL 缓存**:前端轮询不会击穿到 akshare,避免东财限流。
- 信号提醒流读 `data/triggers.jsonl`,由 `run_monitor.py` 持续写入(只开面板时表格仍会实时算信号并高亮)。
- 注意:自选股越多冷启动越慢(akshare 顺序拉取,2 只约 16s),之后走缓存即时刷新。

## 怎么加自己的规则

**盯盘信号**:在 `src/quant/signals/monitor_rules.py` 写一个 `(df, cfg) -> Signal | None` 函数,加进 `MONITOR_RULES`。
**选股规则**:在 `src/quant/signals/screen_rules.py` 写一个 `(daily_df, cfg) -> bool` 函数,加进 `SCREEN_RULES`。
其它模块一律不用动。新规则可单独写单测验证。

## 测试

```bash
.venv/bin/python -m pytest
```

## 数据源说明

默认用 akshare(免费)。实时行情/分钟K/日线需要联网,已在真实网络验证可用(平安银行日线/分钟K/回测均跑通)。

注意:东方财富接口**拒绝默认的 python-requests UA、并会对突发请求限流断连**。数据层已内置应对(`akshare_source.py`):全局设置浏览器 User-Agent + 指数退避重试。盯盘轮询别设太密(建议 ≥30s)以免触发限流。

换数据源只需实现 `src/quant/datasource/base.py` 的 `DataSource` 接口。单测全部为纯函数 + 录制样本,不依赖网络。

## 现实提醒

- 这是**盯盘助手**,不是自动交易系统;不做秒级高频(免费数据吃不了)。
- **信号 ≠ 赚钱**:用回测先验证一条规则有没有边际,再决定是否信它。回测已规避未来函数、幸存者偏差需自行扩充股票池、并计入交易成本。
