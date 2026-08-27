# 回测系统 (Backtest System)

一个**聚焦「最真实反映引擎能力」的 A股回测框架**，专门用来**测试你开发的指标引擎**。

用 easy-tdx 拉取行情数据（毫秒级、免注册、覆盖 A股/港股/美股/期货），
自研前复权消除分红除权假跳变，用验证过的高频引擎逻辑做撮合与出场，
跑**严格 7 窗样本外 Walk-Forward**，把成本算到 A股真实费率，并提供**买入持有基准**对照。

> ⚠️ 本项目是 **回测框架**，不含内置指标引擎。内置 demo 信号（收盘>MA20）仅用于验证链路可跑通，**不构成任何投资建议**。真实信号由你的指标引擎提供（输出每日信号分数数组喂进来即可）。

## 为什么这样设计（真实性优先）

回测要骗人很容易，这个框架针对 7 个常见失真点逐一处理：

| 失真点 | 常见偷懒做法 | 本项目做法 |
|--------|-------------|-----------|
| 未来函数 | 当根信号当根成交 / ATR 偷看未来 | **next_open** 次日成交 + **ATR 纯历史滚动**（cumsum） |
| 高估收益 | 忽略交易成本 | 佣金双边 + 印花税卖出 + 滑点 |
| 除权假跳变 | 用不复权原始K线 | **自研前复权**，消除 -15%~-20% 除权跳空 |
| 过拟合自欺 | 只看样本内收益 | **严格 7 窗样本外 WF** |
| 单只运气 | 单标的回测 | **多标的等权组合**（资金池流动） |
| 止损失真 | 用收盘价判断 | 用**真实 high/low** 触发吊灯ATR止损，跳空按更差开盘价成交 |
| 策略无用 | 只报收益不看参照 | **买入持有基准对照**（跑不赢死拿就没价值）|

## 功能特性（v1.0）

- ✅ **无未来函数**：ATR 用 cumsum 纯历史滚动（修复了 `convolve(mode=same)` 偷看未来 6 天的致命 bug）
- ✅ **买入持有基准**：`buy_and_hold_benchmark()` 判断策略是否真的跑赢死拿（alpha 金标准）
- ✅ **19 项绩效**：收益/年化/回撤/夏普/索提诺/卡玛/利润因子/波动率/胜率等（easy-tdx PerformanceAnalyzer）
- ✅ **严格 7 窗样本外 WF**：看引擎在没见过的行情上的真实泛化
- ✅ **真实交易模拟**：next_open 成交、吊灯ATR14(×3)+保本BE+移动止盈TP、跳空异常价
- ✅ **真实 A股费率**：佣金 0.03% 双边 + 印花税 0.05% 卖出 + 滑点
- ✅ **自研前复权数据层**：easy-tdx 拉取 + 板块感知涨停/除权阈值（主板10%/双创20%/北交所30%）+ 缓存复用
- ✅ **回归测试**：`tests/test_backtest.py` 7 项性质验证，防再次引入未来函数/重复计收益 bug

## 目录结构（src 包 + 相对导入）

```
backtest-system/
├── src/
│   ├── __init__.py        # 包导出
│   ├── data_source.py     # easy-tdx 拉取 + 自研前复权 + 本地缓存 + 自检
│   ├── backtest.py        # 回测核心：单标的逐日收益 + 多标的组合绩效 + 买入持有基准
│   ├── walkforward.py     # 严格 7 窗样本外 WF
│   └── run_backtest.py    # 入口：喂信号 → 组合绩效 + 基准对照 + 7窗WF
├── tests/
│   └── test_backtest.py   # 回归测试（7项性质验证）
├── requirements.txt       # easy-tdx, numpy, pandas
└── cache/                 # 本地K线缓存(运行时生成)
```

> 用 **src 包 + 相对导入**（`from .backtest import`），避免被同名 `backtest.py` 模块劫持。

---

## 📖 使用指南

### 0. 安装

```bash
pip install -r requirements.txt
```

### 1. 跑回归测试（确认框架基线正常）

```bash
python -m tests.test_backtest
# 预期输出 7/7 通过
```

### 2. 快速回测（demo 信号，验证链路）

```bash
# 默认 demo 信号(收盘>MA20) + 3 只股票
python -m src.run_backtest sh600000 sh601318 sz000001
```

### 3. CLI 参数说明

```bash
python -m src.run_backtest <codes...> [--signal demo] [--th 25] [--tp 2.5] [--be]

# codes:      股票代码，格式 sh600000 / sz000001 / bj920821（可多只）
# --signal:   信号来源，目前只有 demo（默认）
# --th:       信号阈值，sig>=th 才触发买入（默认 25）
# --tp:       移动止盈，距高点 tp × ATR 回落出场（默认 2.5，设 -1 禁用）
# --be:       保本开关，盈利超 1 ATR 后止损上移到成本价（默认开）
```

### 4. 使用你自己的指标引擎（核心用法）

回测系统**不内置引擎，只吃信号**。你的指标引擎只需输出「每日信号分数数组」`sig`（与 K 线等长），喂进来即可：

```python
import numpy as np, pandas as pd
from src.data_source import load_kline, sanity_check
from src.backtest import single_daily_rets, portfolio_performance, buy_and_hold_benchmark
from src.walkforward import walk_forward

# ① 拉数据（自研前复权）
codes = ["sh600000", "sh601318", "sz000001"]
data = {}
for c in codes:
    df = load_kline(c)
    sanity_check(df, c)
    data[c] = df

# ② 你的指标引擎算信号（示例：收盘>MA20 触发 +50）
def my_engine(df):
    close = df["close"].to_numpy()
    ma20 = pd.Series(close).rolling(20).mean().to_numpy()
    return np.where(close > ma20, 50, 0)

sig = {c: my_engine(data[c]) for c in codes}

# ③ 跑组合绩效（19项指标）
m = portfolio_performance(data, sig, th=25, tp=2.5, be=True)
print(f"总收益 {m['total_return']*100:+.2f}% | 夏普 {m['sharpe']:.2f} | 回撤 {m['max_drawdown']*100:.2f}%")

# ④ 买入持有基准（金标准：跑不赢死拿就没价值）
bh = buy_and_hold_benchmark(data)
alpha = (m['total_return'] - bh['total_return']) * 100
print(f"买入持有基准总收益 {bh['total_return']*100:+.2f}%")
print(f"策略-基准 = {alpha:+.2f}%  {'🔴 跑赢' if alpha>0 else '🟢 跑输'}")

# ⑤ 严格7窗样本外WF（看真实泛化）
wf = walk_forward(data, sig, th=25, tp=2.5, be=True)
print(f"7窗样本外WF {wf['wf_total']:+.2f}% 窗:{wf['windows']}")
```

### 5. 怎么读结果（判断引擎好坏）

回测系统输出 3 层判断，从下到上越来越严格：

| 输出 | 看什么 | 说明 |
|------|--------|------|
| ① 组合绩效 | 收益/回撤/夏普 | 引擎在这个股票池上的整体表现 |
| ② **买入持有基准** | 策略-基准的 `alpha` | **跑不赢死拿 → 引擎没价值**（金标准）|
| ③ **7窗样本外WF** | 逐窗收益 | 没见过的行情上的真实泛化，最该看重的指标 |

**判断引擎好坏**：
- alpha > 0 + 7窗WF > 0 → ✅ 引擎有真实 alpha，值得用
- alpha > 0 但 7窗WF < 0 → ⚠️ 样本内拟合，样本外失效（过拟合）
- alpha <= 0 → ❌ 引擎还不如死拿，没价值

### 6. 批量拉样本测引擎（可复现）

```python
python -m src.run_backtest sh600000 sh601318 sh600519 sz000001 sz300750
# 想测全市场随机大样本，用 data_source 逐只拉 + 等权组合即可
```

## 核心概念

- **数据层** `src.data_source.load_kline(code)` → 自研前复权日线(open/high/low/close/vol)
  - 用 `NONE` 原始价 + 自研向下跳空检测前复权（阈值按板块：主板10%/双创20%/北交所30%）
  - 因为 easy-tdx 的 QFQ 对不同股票降级(茅台负价)、除权方向算反(浦发)，不可靠
- **回测核心** `src.backtest.single_daily_rets(df, sig, th, tp, be)` → 资金曲线 + 交易明细
  - 成交=**next_open**（信号次日开盘成交，无未来函数）；止损=吊灯ATR14(×3)+保本BE+移动止盈TP，真实 high/low 触发，跳空低开按更差开盘价成交
  - 成本=佣金0.03%双边 + 印花税0.05%卖出 + 滑点
  - 绩效用 **easy-tdx PerformanceAnalyzer** 出 19 项
- **买入持有基准** `src.backtest.buy_and_hold_benchmark(data)` → 各标的死拿不动的等权组合收益
  - **金标准**：策略连死拿都跑不赢，就没有 alpha 价值
- **样本外** `src.walkforward.walk_forward(...)` → 严格 7 窗(后70%切7段)累计收益
  - 看引擎在没见过的行情上的真实泛化，是最该看重的指标

## License

MIT
