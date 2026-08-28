# 回测系统 (Backtest System)

一个**聚焦「最真实反映引擎能力」的 A股回测框架**，专门用来**测试你开发的指标引擎（波段策略）**。

用 easy-tdx 拉取行情数据（毫秒级、免注册、覆盖 A股/港股/美股/期货），
自研前复权消除分红除权假跳变，用验证过的高频引擎逻辑做撮合与出场，
跑**严格 7 窗样本外 Walk-Forward**，把成本算到 A股真实费率。

> ⚠️ 本项目是 **回测框架**。内置 2 个**参考指标引擎**（`src/engines.py`：easy-tdx MyTT MACD / 内置 MACD 金叉死叉，用于对比基准）+ 1 个 demo 信号（收盘>MA20，仅验证链路）。**不构成任何投资建议**。你的真实引擎输出「每日信号分数数组」喂进来即可。

## 为什么这样设计（真实性优先）

回测要骗人很容易，这个框架针对 6 个常见失真点逐一处理：

| 失真点 | 常见偷懒做法 | 本项目做法 |
|--------|-------------|-----------|
| 未来函数 | 当根信号当根成交 / ATR 偷看未来 | **next_open** 次日成交 + **ATR 纯历史滚动**（cumsum） |
| 高估收益 | 忽略交易成本 | 佣金双边 + 印花税卖出 + 滑点 |
| 除权假跳变 | 用不复权原始K线 | **自研前复权**，消除 -15%~-20% 除权跳空 |
| 过拟合自欺 | 只看样本内收益 | **严格 7 窗样本外 WF** |
| 单只运气 | 单标的回测 | **多标的等权组合**（资金池流动） |
| 止损失真 | 用收盘价判断 | 用**真实 high/low** 触发吊灯ATR止损，跳空按更差开盘价成交 |

## 目录结构（src 包 + 相对导入）

```
backtest-system/
├── src/
│   ├── __init__.py        # 包导出
│   ├── data_source.py     # easy-tdx 拉取 + 自研前复权 + 本地缓存(7天自动更新) + 自检
│   ├── backtest.py        # 回测核心：单标的逐日收益(双向信号/exit_mode) + 多标的组合绩效
│   ├── engines.py         # 内置参考引擎：easy-tdx MyTT MACD / 内置 MACD（对比基准）
│   ├── benchmark.py       # 标准引擎评估接口 evaluate_engine()
│   ├── walkforward.py     # 严格 7 窗样本外 WF
│   └── run_backtest.py    # 入口：喂信号 → 组合绩效 + 7窗WF
├── tests/
│   └── test_backtest.py   # 回归测试（性质验证）
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
# 预期输出全部通过
```

### 2. 快速回测（demo 信号，验证链路）

```bash
# 默认 demo 信号(收盘>MA20) + 3 只股票
python -m src.run_backtest sh600000 sh601318 sz000001
```

### 3. CLI 参数说明

```bash
python -m src.run_backtest <codes...> [--signal demo] [--th 25] [--tp 2.5] [--no-be] [--exit-mode signal]

# codes:       股票代码，格式 sh600000 / sz000001 / bj920821（可多只）
# --signal:    信号来源，目前只有 demo（默认）
# --th:        信号阈值，sig>=th 才触发买入（默认 25）
# --tp:        移动止盈，距高点 tp × ATR 回落出场（默认 2.5，设 -1 禁用）
# --no-be:      禁用保本开关，盈利超 1 ATR 后止损上移到成本价（默认开，加此参数关闭）
# --exit-mode: 出场模式 signal=有买有卖(默认) / long_only=只买不卖 / trailing=趋势止损止盈
```

### 4. 使用你自己的指标引擎（核心用法）

回测系统**不内置引擎，只吃信号**。你的指标引擎只需输出「每日信号分数数组」`sig`（与 K 线等长），喂进来即可：

```python
import numpy as np, pandas as pd
from src.data_source import load_kline, sanity_check
from src.backtest import single_daily_rets, portfolio_performance
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
print(f"索提诺 {m.get('sortino',0):.2f} | 卡玛 {m.get('calmar',0):.2f} | 胜率 {m['win_rate']:.1f}%")

# ④ 严格7窗样本外WF（看真实泛化）
wf = walk_forward(data, sig, th=25, tp=2.5, be=True)
print(f"7窗样本外WF {wf['wf_total']:+.2f}% 窗:{wf['windows']}")
```

### 5. 怎么读结果（判断引擎好坏）

回测系统输出核心绩效 + 严格 7 窗样本外 WF，从 3 个维度评估你的波段引擎：

| 维度 | 看什么 | 说明 |
|------|--------|------|
| ① 收益质量 | 总收益/年化 | 引擎在这个股票池上的整体表现 |
| ② **风险控制** | 回撤/索提诺/卡玛 | 波段引擎核心卖点——**用更小回撤换收益**（卡玛=年化/回撤） |
| ③ **样本外泛化** | 7窗WF 逐窗收益 | 没见过的行情上是否仍有效（最该看重） |

**判断波段引擎好坏**：
- 7窗WF > 0 → ✅ 样本外有效，值得用
- 7窗WF < 0 但样本内正 → ⚠️ 样本内拟合，样本外失效（过拟合）
- 回撤显著小于指数 + 收益可观 → ✅ 波段引擎"避开下跌段"的价值体现（卡玛高）
- 收益和回撤都不占优 → ❌ 引擎没价值

> 波段引擎的价值不在「总收益超过死拿」，而在 **用更小的回撤赚到接近的收益**（卡玛比率高 + 样本外泛化稳）。

### 6. 批量拉样本测引擎（可复现）

```python
python -m src.run_backtest sh600000 sh601318 sh600519 sz000001 sz300750
# 想测全市场随机大样本，用 data_source 逐只拉 + 等权组合即可
```

### 7. 用标准接口评估引擎（推荐流程，一条龙）

`evaluate_engine()` 是**测引擎的标准入口**——自动拉数据/归一化列序/对齐信号长度/选出场模式/对比 3 个经典引擎，一次跑出完整对比报告，避免手拼数据出错。

```python
from src import evaluate_engine

# 你的引擎：输入 DataFrame，输出与 df 等长的 sig 数组（+50买/-50卖/0无）
def my_engine(df):
    close = df["close"].to_numpy()
    ma20 = pd.Series(close).rolling(20).mean().to_numpy()
    return np.where(close > ma20, 50, 0)

# ① 默认：有买有卖（被测引擎 + 参考引擎都用 signal）
result = evaluate_engine(my_engine, n_sample=300)

# ② 只买不卖：被测引擎 + 参考引擎都用 long_only（同规则对比）
result = evaluate_engine(my_engine, n_sample=300, exit_mode="long_only", reference_exit_mode="long_only")

# ③ 看结果：你的引擎 vs 参考引擎
for r in result["reference"]:
    print(f"{r['name']}: 收益{r['total']:+.2f}% 夏普{r['sharpe']:.2f} 7窗WF {r['wf_total']:+.2f}%")
print(f"你的引擎: 收益{result['your']['total']:+.2f}% 夏普{result['your']['sharpe']:.2f} 7窗WF {result['your']['wf_total']:+.2f}%")
```

**evaluate_engine 关键参数：**

| 参数 | 说明 | 默认 |
|------|------|------|
| `engine_fn` | 你的引擎函数 | 必填 |
| `n_sample` | 随机抽取股票数（None 则用 codes） | 100 |
| `codes` | 固定股票池（给则用，不随机抽） | None |
| `exit_mode` | 被测引擎出场模式：signal/trailing/long_only/auto | auto |
| `reference_exit_mode` | 参考对照引擎的出场模式 | signal |
| `th` | 信号阈值 | 25 |

> `exit_mode="auto"` 会按信号特征自动判：有卖出信号→signal，只买不卖→trailing/long_only。

## 核心概念

- **数据层** `src.data_source.load_kline(code)` → 自研前复权日线(open/high/low/close/vol)
  - 用 `NONE` 原始价 + 自研向下跳空检测前复权（阈值按板块：主板10%/双创20%/北交所30%）
  - 因为 easy-tdx 的 QFQ 对不同股票降级(茅台负价)、除权方向算反(浦发)，不可靠
- **回测核心** `src.backtest.single_daily_rets(df, sig, th, tp, be, exit_mode)` → 资金曲线 + 交易明细
  - 双向信号：买入 `sig>=th`，卖出 `sig<=-th`（金叉买/死叉卖，波段引擎能主动平仓）
  - `exit_mode`: `signal`（默认，有买有卖）/ `long_only`（可选，只买不卖）/ `trailing`（趋势引擎，吊灯ATR+BE+TP止损止盈）
  - 成交=**next_open**（信号次日开盘成交，无未来函数）；止损=吊灯ATR14(×3)+保本BE+移动止盈TP，真实 high/low 触发，跳空低开按更差开盘价成交
  - 成本=佣金0.03%双边 + 印花税0.05%卖出 + 滑点
  - 绩效用 **easy-tdx PerformanceAnalyzer** 出 19 项
- **参考引擎** `src.engines`: `mytt_macd`（easy-tdx MyTT MACD）/ `macd_cross`（内置 MACD），2 个参考引擎，作为你开发新引擎的对比基准（评分也以它们为标杆）
- **样本外** `src.walkforward.walk_forward(...)` → 严格 7 窗(后70%切7段)累计收益
  - 看引擎在没见过的行情上的真实泛化，是最该看重的指标

## 版本历史

### v1.2.1（当前）

代码审查的修复：
- **WF 严格样本外**：`walk_forward` 每窗从窗口起点独立重新开仓回测，修复「跨窗持仓收益重复算 → WF 偏乐观」
- **trailing 止损防重复计**：新增 `test_trailing_stop_no_double_count` 锁定
- **tp/be 参数注明**：docstring 说明 tp/be 仅 trailing 生效，signal/long_only 忽略
- **compute_atr 短数据崩溃**：修复窗口只有几根K线时 ATR 广播崩溃
- **CLI `--be` 参数失效**：改为 `--no-be`，可真正关闭保本开关（原 `--be` 恒为 True 关不掉）
- **`evaluate_engine` 默认抽样硬编码路径**：改用 `shutil.which` 探测 easy-tdx，修后默认 500 只可正常跑
- **`compute_atr` 短数据越界**：加 n<2 guard，防空/单根K线崩

### v1.2

新增**性能评分系统**，并精简参考引擎：
- **性能评分** `score_engine()`：每个引擎自动出 1 个综合评分（满分100，保留3位小数），引擎越强分越高
  - 相对最强参考引擎做基准（看最强）
  - 权重（赚钱为主）：收益 50% + 夏普 15% + 回撤 10% + 索提诺 5% + 样本外WF 20%
- **参考引擎精简为 2 个**：easy-tdx MyTT MACD + 内置 MACD 金叉死叉（去掉双均线/RSI）
- **新增参考引擎** `mytt_macd`（easy-tdx MyTT 原生 MACD）

### v1.1

面向「测引擎」的完整能力：
- **标准引擎评估接口** `evaluate_engine()`：自动拉数据/归一化列序/对齐信号/选出场模式/对比参考引擎
- **内置参考引擎** `src/engines.py`：双均线 / MACD / RSI（v1.2 起精简为 MyTT MACD + MACD）
- **双向信号**：金叉买/死叉卖
- **`exit_mode` 出场模式**：`signal`(默认有买有卖) / `long_only`(只买不卖) / `trailing`(趋势止损止盈)
- **`reference_exit_mode`**：参考对照引擎可跟被测引擎同模式
- **默认 500 只随机样本**（seed=42 可复现）
- **缓存 7 天自动更新**（数据不过期）
- **`long_only` 进 CLI**

### v1.0（核心引擎）

回测系统骨架：
- **无未来函数**：ATR 用 cumsum 纯历史滚动；成交 next_open 次日开盘
- **真实费率**：佣金双边 + 印花税卖出 + 滑点
- **自研前复权数据层**：板块感知阈值（主板10%/双创20%/北交所30%），消除除权假跳变
- **严格 7 窗样本外 WF**：看引擎真实泛化
- **多标的等权组合**：easy-tdx PerformanceAnalyzer 出 19 项绩效

> 完整变更历史见 [CHANGELOG.md](CHANGELOG.md)

## License

MIT
