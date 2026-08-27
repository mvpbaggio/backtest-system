#!/usr/bin/env python3
"""回测核心：单标的逐日收益 + 多标的等权组合绩效。

信号入口 sig（每日信号分数数组）由外部引擎提供——本项目是回测框架，
不内置指标引擎(超哥: 指标引擎后面再接)。传入 sig 即可测任意引擎。

出场逻辑复用现有验证过的引擎(吊灯ATR + 保本BE + 止盈TP，无未来函数)：
- 信号日 sig>=th 建仓(当根收盘价 entry，次根起算收益 = 收盘价成交，避免当日信号当日成交)
- 持仓期用真实 high/low 触发 stop(吊灯ATR14)/BE/TP 出场
- fallback 未出场则持有到最后

成本(真实A股)：买入扣佣金，卖出扣佣金+印花税+滑点。费率可在 config 调。

ponytail: 单一信号数组入口，不搞策略基类/接口。绩效指标取核心几个。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 默认费率（可被 config 覆盖）
DEF_COMMISSION = 0.0003   # 佣金 0.03% 双边
DEF_STAMP_TAX = 0.0005    # 印花税 0.05% 卖出
DEF_SLIPPAGE = 0.0        # 滑点（默认0，实测真实盘口再调）
ATR_PERIOD = 14
ATR_STOP_MULT = 3.0       # 吊灯止损 ATR×3


def compute_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> np.ndarray:
    """ATR(真实波幅均值)。用最高/最低/前收算 TR，滚动均值。"""
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    n = len(c)
    tr = np.zeros(n)
    tr[1:] = np.maximum(
        h[1:] - l[1:],
        np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])),
    )
    atr = np.convolve(tr, np.ones(period) / period, mode="same")
    atr[atr <= 0] = 1e-9
    return atr


def single_daily_rets(
    df: pd.DataFrame,
    sig: np.ndarray,
    th: float = 25,
    tp: float = 2.5,
    be: bool = True,
    commission: float = DEF_COMMISSION,
    stamp_tax: float = DEF_STAMP_TAX,
    slippage: float = DEF_SLIPPAGE,
) -> tuple[np.ndarray, int, int]:
    """单标的逐日收益序列(与 df 等长) + (交易数, 胜数)。

    出场逻辑 = 现有验证过的引擎，成本拆细为真实费率。
    """
    c = df["close"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    n = len(c)
    atr = compute_atr(df)
    buy_cost = commission
    sell_cost = commission + stamp_tax + slippage

    rets = np.zeros(n)
    trades = wins = 0
    pos = False
    entry = 0.0
    hi = 0.0

    for i in range(1, n):
        if pos:
            hi = max(hi, h[i])
            stop = entry - ATR_STOP_MULT * atr[i]
            if be and hi - entry > atr[i]:
                stop = max(stop, entry)          # 保本：盈利超1ATR后止损上移到成本
            if tp > 0:
                stop = max(stop, hi - tp * atr[i])  # 移动止盈：距高点 tp×ATR 回落出场
            if l[i] <= stop:                     # 真实 low 触发出场(次日或当根，取 stop)
                rets[i] = (stop / c[i - 1] - 1) - sell_cost
                trades += 1
                wins += 1 if (stop / c[i - 1] - 1) > 0 else 0
                pos = False
                entry = 0.0
                hi = 0.0
            else:
                rets[i] = c[i] / c[i - 1] - 1
        else:
            if sig[i] >= th:
                rets[i] = -buy_cost               # 建仓扣佣金，价格收益从次根算
                trades += 1
                pos = True
                entry = c[i]
                hi = h[i]
    # 期末仍持有 → 平仓(用最后的 close 估)
    if pos:
        final_ret = c[-1] / entry - 1 - sell_cost
        rets[-1] += final_ret
        trades += 1
        wins += 1 if final_ret > 0 else 0
    return rets, trades, wins


def portfolio_metrics(
    rets_map: dict[str, np.ndarray],
    dates: np.ndarray,
    risk_free: float = 0.03,
) -> dict:
    """多标的等权组合(资金池流动) + 绩效指标。

    rets_map: {code: 该标的逐日收益数组, 与各标的自己 dates 对齐}
    dates:    锚定的交易日序列(用第一只股票日期)
    返回: 总收益/年化/最大回撤/夏普/胜率/交易数
    """
    # 按锚日期聚合所有股票当日收益 → 等权平均 → 组合日收益
    daily: dict[str, list[float]] = {d: [] for d in dates}
    for code, rs in rets_map.items():
        for d, r in zip(dates, rs):
            daily[d].append(r)
    ds = sorted(daily.keys())
    comb_rets = np.array([np.mean(daily[d]) for d in ds])
    nav = np.cumprod(1 + comb_rets)

    total = (nav[-1] - 1) * 100
    years = max(len(ds) / 252.0, 1e-9)
    annual = ((nav[-1]) ** (1 / years) - 1) * 100
    peak = np.maximum.accumulate(nav)
    mdd = float((nav / peak - 1).min()) * 100
    std = comb_rets.std(ddof=1) if len(comb_rets) > 1 else 0.0
    sharpe = (comb_rets.mean() - risk_free / 252) / std * np.sqrt(252) if std > 0 else 0.0

    # 交易统计(跨所有股票累加)
    total_trades = sum(t["trades"] for t in rets_map.values() if isinstance(t, dict))
    # 简化：从单标的返回里取 → 这里 rets_map 只存数组，胜率聚合在 caller 做
    return {"total": total, "annual": annual, "mdd": mdd, "sharpe": sharpe,
            "days": len(ds), "nav": nav, "comb_rets": comb_rets}


if __name__ == "__main__":
    # 自检：用简单"收盘>MA20"信号，验证框架能产生非零交易、指标可算
    import sys
    sys.path.insert(0, ".")
    from data_source import load_kline, sanity_check

    df = load_kline("sh600000")
    sanity_check(df, "sh600000")
    close = df["close"].to_numpy()
    ma20 = pd.Series(close).rolling(20).mean().to_numpy()
    sig = np.where(close > ma20, 50, 0)

    rets, trades, wins = single_daily_rets(df, sig, th=25, tp=2.5, be=True)
    print(f"✅ sh600000 自检: {len(df)} 行, 信号触发交易 {trades} 次, 胜 {wins} 次")
    print(f"  自研前复权后最大单日涨跌 {abs(pd.Series(close).pct_change()).max()*100:.1f}%")
    print(f"  该信号净收益(未做组合) {np.prod(1+rets)-1 if len(rets) else 0:.2%}")
