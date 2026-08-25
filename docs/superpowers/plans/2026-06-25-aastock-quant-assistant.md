# A股量化盯盘助手 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建一个 A股盯盘助手:盘中监控自选股的技术指标/量价异动并提醒(人工下单),并支持盘前选股与历史回测,底层模块共用。

**Architecture:** 三条流水线(盯盘/选股/回测)共用 数据层 / 指标层(纯函数)/ 信号层(注册表模式的纯函数规则)/ 提醒层 / 日志层。信号规则是纯函数,实时盯盘与历史回测复用同一批规则代码。数据源细节封装在数据层,便于换源。

**Tech Stack:** Python 3.14, pandas, akshare(数据源), 标准库 tomllib(配置), pytest(测试), macOS osascript(桌面通知)。

---

## 标准数据结构

- **Bars DataFrame**(分钟/日线统一 schema,按时间升序):列 `datetime, open, high, low, close, volume`。
- **Signal**(dataclass):`code, name, rule, direction('long'|'short'), time, price, detail: dict`。

## File Structure

```
src/quant/
  config.py              # 读取 config/config.toml -> Config dataclass
  datasource/base.py     # DataSource 抽象接口
  datasource/akshare_source.py  # akshare 实现 + 列名归一化
  indicators.py          # add_ma/add_macd/add_rsi/add_volume_features (纯函数)
  signals/types.py       # Signal dataclass
  signals/monitor_rules.py  # MONITOR_RULES 注册表 + 盯盘规则
  signals/screen_rules.py   # SCREEN_RULES 注册表 + 选股规则
  signals/engine.py      # 跑规则 -> [Signal]
  notify/base.py         # Notifier 接口
  notify/terminal.py     # 终端输出
  notify/desktop.py      # macOS 桌面通知
  notify/dedup.py        # 冷却去重
  calendar.py            # 交易日历 / 时段判断
  logstore.py            # 触发日志 (jsonl 追加)
  backtest.py            # 历史回放 -> 绩效统计
scripts/
  run_monitor.py         # 盯盘入口
  run_screen.py          # 选股入口
  run_backtest.py        # 回测入口
tests/                   # 各模块单测 + fixtures/ 录制样本
config/config.example.toml
```

---

## Task 1: 项目骨架 + 指标层

**Files:** Create `src/quant/__init__.py`, `src/quant/indicators.py`, `tests/test_indicators.py`, `pyproject.toml`(pytest 配置)。

- [ ] **Step 1: 写失败测试** `tests/test_indicators.py`

```python
import pandas as pd
from quant.indicators import add_ma, add_rsi, add_macd, add_volume_features

def _bars(closes, vols=None):
    n = len(closes)
    return pd.DataFrame({
        "datetime": pd.date_range("2024-01-01 09:30", periods=n, freq="min"),
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": vols if vols is not None else [100]*n,
    })

def test_add_ma_last_value():
    df = add_ma(_bars([1,2,3,4,5]), windows=(2,))
    assert df["ma2"].iloc[-1] == 4.5

def test_add_rsi_all_up_is_100():
    df = add_rsi(_bars([1,2,3,4,5,6,7,8,9,10,11,12,13,14,15]), period=14)
    assert df["rsi"].iloc[-1] == 100.0

def test_add_macd_columns_exist():
    df = add_macd(_bars(list(range(1,40))))
    for col in ("dif","dea","macd"):
        assert col in df.columns

def test_volume_ratio():
    df = add_volume_features(_bars([1]*6, vols=[100,100,100,100,100,300]), window=5)
    assert df["vol_ratio"].iloc[-1] == 3.0
```

- [ ] **Step 2: 跑测试确认失败** — `.venv/bin/python -m pytest tests/test_indicators.py -v`(ImportError)
- [ ] **Step 3: 实现 `indicators.py`**(MA=rolling mean;RSI=Wilder;MACD=EMA12/26+DEA9,macd=2*(dif-dea);vol_ratio=volume/rolling mean shift)
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: commit**

## Task 2: 信号类型 + 盯盘规则 + 引擎

**Files:** Create `src/quant/signals/{__init__,types,monitor_rules,engine}.py`, `tests/test_monitor_rules.py`, `tests/test_engine.py`。

- [ ] **Step 1: 写失败测试**(均线金叉触发 long;RSI<阈值触发 oversold long;放量>阈值触发;破当日高触发;引擎跑全部规则返回 Signal 列表)
- [ ] **Step 2: 确认失败**
- [ ] **Step 3: 实现**
  - `types.py`: `@dataclass Signal`(code,name,rule,direction,time,price,detail)
  - `monitor_rules.py`: `ma_cross/macd_cross/rsi_extreme/volume_spike/break_intraday_high_low`,每个 `(df, cfg) -> Signal|None`,只看最后一根 bar 是否"刚触发"(需用前一根状态判断穿越,杜绝重复)。`MONITOR_RULES = [...]`
  - `engine.py`: `run_rules(df, rules, code, name, cfg) -> list[Signal]`
- [ ] **Step 4: 确认通过**
- [ ] **Step 5: commit**

## Task 3: 去重 + 提醒层

**Files:** Create `src/quant/notify/{__init__,base,terminal,desktop,dedup}.py`, `tests/test_dedup.py`。

- [ ] **Step 1: 写失败测试**(同 code+rule 在冷却期内第二次 `should_notify` 返回 False;过期后 True)
- [ ] **Step 2: 确认失败**
- [ ] **Step 3: 实现** — `dedup.Deduper(cooldown_seconds)`;`base.Notifier` 接口;`terminal.TerminalNotifier`(格式化打印);`desktop.DesktopNotifier`(osascript,失败静默降级)
- [ ] **Step 4: 确认通过**
- [ ] **Step 5: commit**

## Task 4: 配置 + 交易日历 + 日志

**Files:** Create `src/quant/{config,calendar,logstore}.py`, `config/config.example.toml`, `tests/test_config.py`, `tests/test_calendar.py`。

- [ ] **Step 1: 写失败测试**(config 解析 watchlist/阈值/间隔;calendar `is_in_session` 对 10:00 True、12:00 False;logstore 追加可读回)
- [ ] **Step 2: 确认失败**
- [ ] **Step 3: 实现** — `config.load_config(path)->Config`(tomllib);`calendar.is_in_session(dt)`/`is_trading_day(date, trade_dates)`;`logstore.append(signal)`/`read_all()`(jsonl)
- [ ] **Step 4: 确认通过**
- [ ] **Step 5: commit**

## Task 5: 数据层

**Files:** Create `src/quant/datasource/{__init__,base,akshare_source}.py`, `tests/test_akshare_source.py`(用录制样本,不联网)。

- [ ] **Step 1: 写失败测试** — 构造 akshare 原始列名(中文)的样本 DataFrame,断言归一化函数 `_normalize_bars` 输出标准 schema 且按时间升序
- [ ] **Step 2: 确认失败**
- [ ] **Step 3: 实现** — `base.DataSource` 抽象;`akshare_source.AkshareSource`,方法 `get_minute_bars/get_daily_bars/get_realtime/get_trade_dates`,内部含 `_normalize_bars` 列名映射(中文→英文)。网络方法薄封装,核心逻辑(归一化)可单测
- [ ] **Step 4: 确认通过**
- [ ] **Step 5: commit**

## Task 6: 选股规则

**Files:** Create `src/quant/signals/screen_rules.py`, `tests/test_screen_rules.py`。

- [ ] **Step 1: 写失败测试**(`above_ma(daily,n)` 收盘站上 MA20 为 True;`volume_surge(daily,lookback,mult)` 近期放量为 True;组合规则)
- [ ] **Step 2: 确认失败**
- [ ] **Step 3: 实现** — `SCREEN_RULES` 注册表 + 规则函数 `(daily_df, cfg)->bool`;`screen(codes, source, rules, cfg)->list[code]`
- [ ] **Step 4: 确认通过**
- [ ] **Step 5: commit**

## Task 7: 回测模块(规避未来函数/成本)

**Files:** Create `src/quant/backtest.py`, `tests/test_backtest.py`。

- [ ] **Step 1: 写失败测试** — 构造已知 bars + 一个简单规则,逐 bar 回放(切片 `df.iloc[:i+1]` 保证无未来数据),记录触发后 k 根的前向收益,断言胜率/平均收益/计入成本后数值正确
- [ ] **Step 2: 确认失败**
- [ ] **Step 3: 实现** — `backtest(df, rule, cfg, forward=k, cost=...)->Stats(trades,win_rate,avg_return,...)`;严格用历史切片,禁止使用 i 之后数据
- [ ] **Step 4: 确认通过**
- [ ] **Step 5: commit**

## Task 8: 入口脚本

**Files:** Create `scripts/{run_monitor,run_screen,run_backtest}.py`, `README.md`。

- [ ] **Step 1:** `run_monitor.py` — 读 config,循环:仅交易时段,逐自选股取分钟K→加指标→跑 MONITOR_RULES→去重→提醒+记日志
- [ ] **Step 2:** `run_screen.py` — 读 config,全市场/候选池取日线→跑 SCREEN_RULES→输出名单(写入 watchlist 文件)
- [ ] **Step 3:** `run_backtest.py` — 读 config,取历史→对指定规则回测→打印绩效
- [ ] **Step 4:** `README.md` — 安装、配置、三种用法、数据源说明
- [ ] **Step 5: commit**

---

## Self-Review

- **Spec coverage:** 数据层(T5)/指标层(T1)/信号层盯盘(T2)+选股(T6)/提醒+去重(T3)/日志+配置+日历(T4)/回测(T7)/入口(T8) 均有任务,覆盖 spec 三条流水线与六模块 + 回测正式模块。
- **Placeholder scan:** 无 TBD/TODO。
- **Type consistency:** Bars schema `datetime,open,high,low,close,volume` 全程一致;Signal 字段一致;规则签名 `(df,cfg)->Signal|None`(盯盘)与 `(daily_df,cfg)->bool`(选股)一致。
- **现实约束:** 沙箱无法联网取行情(仅交易日历可用),故数据层用录制样本测试归一化逻辑,实时路径在用户机器联网运行。
