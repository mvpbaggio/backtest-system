#!/usr/bin/env python3
"""严格 7 窗样本外 Walk-Forward 验证。

为什么要样本外：只看样本内总收益会过拟合自欺(挑同期最顺的参数)。严格 WF
把后 70% 区间切成 7 段，每段独立验证，看引擎在"没见过的行情"上的真实泛化——
这是实盘最看重的指标。

流程：
  - 锚定第一只股票的日期序列，取后 70%(前30%留作样本内，不做对比)
  - 切成 7 个样本外窗口，每窗内各只股票只统计落在该窗口的日收益
  - 逐窗等权组合 → 每窗收益，连乘得累计 WF 收益
"""
from __future__ import annotations

import numpy as np

from .backtest import single_daily_rets


def walk_forward(
    data: dict[str, object],
    sig: dict[str, np.ndarray],
    th: float = 25,
    tp: float = 2.5,
    be: bool = True,
    n_windows: int = 7,
    commission: float = 0.0003,
    stamp_tax: float = 0.0005,
    slippage: float = 0.0,
    exit_mode: str = "signal",
) -> dict:
    """严格 n 窗样本外 WF。

    data: {code: DataFrame(df带date/open/high/low/close/vol)}，第一只为日期锚。
    sig:  {code: 每日信号分数数组}，与对应 df 等长。
    exit_mode: "signal"(波段纯信号) / "trailing"(趋势止损止盈)。
    返回: {wf_total, windows(每窗收益%列表)}
    """
    anchor = data[list(data.keys())[0]]
    dates = anchor["date"].to_numpy()
    n = len(dates)
    win = int(n * 0.10)               # 后70%切7窗，每窗10%
    if win < 1:
        raise ValueError("数据太短，无法切窗")

    window_rets = []
    for w in range(n_windows):
        i0 = n - (n_windows - w) * win
        i1 = i0 + win
        if i1 > n:
            break
        d_start, d_end = dates[i0], dates[i1 - 1]

        # 每窗独立回测：只取该窗 [d_start, d_end] 的 K线段，从窗口起点重新开仓。
        # 避免「全历史入场持仓跨窗」导致两个窗口重复算同一笔钱(严格OOS)。
        daily: dict[str, list[float]] = {}
        for code, df in data.items():
            dcol = df["date"].to_numpy()
            # 截取落在窗口内的行索引
            idx = np.where((dcol >= d_start) & (dcol <= d_end))[0]
            if len(idx) < 2:
                continue
            df_win = df.iloc[idx].reset_index(drop=True)
            sig_win = np.asarray(sig[code])[idx]
            eq, _ = single_daily_rets(df_win, sig_win, th, tp, be, commission, stamp_tax, slippage, exit_mode)
            tot = eq["total"].to_numpy()
            dayrets = np.diff(tot) / tot[:-1]
            wd = df_win["date"].to_numpy()
            for d, r in zip(wd[1:], dayrets):
                daily.setdefault(d, []).append(r)
        ds = sorted(daily.keys())
        if not ds:
            window_rets.append(0.0)
            continue
        nav = 1.0
        for d in ds:
            nav *= (1 + np.mean(daily[d]))
        window_rets.append((nav - 1) * 100)

    tot = 1.0
    for r in window_rets:
        tot *= (1 + r / 100)
    return {"wf_total": (tot - 1) * 100, "windows": [round(r, 1) for r in window_rets]}


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    import pandas as pd
    from .data_source import load_kline, sanity_check

    codes = ["sh600000", "sh601318"]
    data, sig = {}, {}
    for c in codes:
        df = load_kline(c)
        sanity_check(df, c)
        ma20 = pd.Series(df["close"]).rolling(20).mean().to_numpy()
        data[c] = df
        sig[c] = np.where(df["close"].to_numpy() > ma20, 50, 0)

    res = walk_forward(data, sig, th=25, tp=2.5, be=True)
    print(f"✅ 7窗WF 自检: 累计 {res['wf_total']:+.1f}% 窗:{res['windows']}")
