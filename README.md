# 回测系统 (Backtest System)

一个**聚焦「最真实反映引擎能力」的 A股回测框架**。

用 easy-tdx 拉取行情数据（毫秒级、免注册、覆盖 A股/港美股/期货），
自研前复权消除分红除权假跳变，复用验证过的高频交易引擎逻辑，
跑**严格 7 窗样本外 Walk-Forward**，把成本算到 A股真实费率。

> ⚠️ 本项目是 **回测框架**，内置 demo 信号(收盘>MA20)仅用于验证链路可跑通，
> **不构成任何投资建议**。真实信号由指标引擎提供（本项目当前不含指标引擎）。

## 为什么这样设计（真实性优先）

回测要骗人很容易，这个框架针对 6 个常见失真点逐一处理：

| 失真点 | 常见偷懒做法 | 本项目做法 |
|--------|-------------|-----------|
| 未来函数 | 当根信号当根成交 | `next_open` 风格，信号次根起算收益 |
| 高估收益 | 忽略交易成本 | 佣金双边 + 印花税卖出 + 滑点 |
| 除权假跳变 | 用不复权原始K线 | **自研前复权**，消除 -15%~-20% 除权跳空 |
| 过拟合自欺 | 只看样本内收益 | **严格 7 窗样本外 WF** |
| 单只运气 | 单标的回测 | **多标的等权组合**（资金池流动） |
| 止损失真 | 用收盘价判断 | 用**真实 high/low** 触发吊灯ATR止损 |

## 目录结构（ponytail 精简）

```
backtest-system/
├── data_source.py     # easy-tdx 拉取 + 自研前复权 + 本地缓存 + 自检
├── backtest.py        # 回测核心：单标的逐日收益 + 多标的组合绩效
├── walkforward.py     # 严格 7 窗样本外 WF
├── run_backtest.py    # 入口：喂信号 → 组合绩效 + 7窗WF
├── requirements.txt   # easy-tdx, numpy, pandas
└── cache/             # 本地K线缓存(运行时生成)
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 跑回测(默认 demo 信号 + 3只股票)
python run_backtest.py sh600000 sh601318 sz000001

# 3. 指定信号阈值/止盈/保本(后续接真实引擎时用)
python run_backtest.py sh600000 --th 25 --tp 2.5 --be
```

## 核心概念

- **数据层** `data_source.load_kline(code)` → QFQ 前复权日线(open/high/low/close/vol)
  - 用 `NONE` 原始价 + 自研向下跳空检测(>10.5%)前复权
  - 因为 easy-tdx 的 QFQ 对不同股票降级(茅台负价)、除权方向算反(浦发)，不可靠
- **回测核心** `backtest.single_daily_rets(df, sig, th, tp, be)` → 逐日收益
  - 出场=吊灯ATR14(×3) + 保本BE + 移动止盈TP，用真实高低点触发
  - 成本=佣金0.03%双边 + 印花税0.05%卖出 + 滑点
- **样本外** `walkforward.walk_forward(...)` → 严格 7 窗(后70%切7段)累计收益
  - 看引擎在没见过的行情上的真实泛化，是最该看重的指标

## 自定义信号（接入真实引擎）

```python
import numpy as np, pandas as pd
from data_source import load_kline, sanity_check
from run_backtest import run

# 信号函数：输入 DataFrame，输出与 df 等长的 sig 分数数组
def my_signal(df):
    close = df["close"].to_numpy()
    ma10 = pd.Series(close).rolling(10).mean().to_numpy()
    return np.where(close > ma10, 50, 0)  # 例：收盘>MA10 触发

run(["sh600000", "sh601318"], my_signal, th=25, tp=2.5, be=True)
```

## License

MIT
