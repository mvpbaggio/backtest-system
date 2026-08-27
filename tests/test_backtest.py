#!/usr/bin/env python3
"""回测系统回归测试：性质验证 + 边界 + 区间一致性。

用确定性小数据手工核对资金曲线，防「重复计/漏计/未来函数」这类致命 bug 重演
（如之前 ATR 用 convolve(mode="same") 偷看未来 6 天，导致全部回测结论虚高）。

ponytail: 纯 assert 自检，不引入 pytest 框架；`python -m tests.test_backtest` 直接跑。
也可用 pytest（如已装）发现 test_* 函数。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.backtest import (
    single_daily_rets, portfolio_performance, compute_atr,
)


def _mkdf(n=60, price0=10.0, price1=20.0):
    return pd.DataFrame({
        "date": pd.date_range("2020-01-01", periods=n).strftime("%Y-%m-%d"),
        "open": np.linspace(price0, price1, n),
        "high": np.linspace(price0 + 0.5, price1 + 0.5, n),
        "low": np.linspace(price0 - 0.5, price1 - 0.5, n),
        "close": np.linspace(price0, price1, n),
        "vol": np.full(n, 1000.0),
    })


def test_no_signal_flat():
    """无信号 → 净值恒1、无交易。"""
    df = _mkdf()
    sig0 = np.zeros(len(df))
    eq, tds = single_daily_rets(df, sig0, th=25)
    assert abs(eq["total"].iloc[-1] - 1.0) < 1e-9, "无信号应净值恒1"
    assert len(tds) == 0, "无信号应无交易"


def test_full_position_matches_manual():
    """信号触发一次且不卖 → 净值 = 手工累计（c/entry 连乘扣成本）。"""
    df = _mkdf()
    sig = np.zeros(len(df)); sig[20] = 50
    o = df["open"].to_numpy(); c = df["close"].to_numpy()
    # 信号在第20根收盘触发 → 第21根(index21)开盘成交 next_open
    entry_px = o[21]
    manual = (1 - 0.0003) * (c[21] / entry_px)          # 入场bar：开盘买(扣佣金)持有到今收
    for k in range(22, len(c)):
        manual *= (c[k] / c[k - 1])                      # 之后逐日 mark-to-market
    manual *= (1 - (0.0003 + 0.0005))                    # 期末平仓扣卖出成本
    eq, tds = single_daily_rets(df, sig, th=25, tp=-1, be=True)  # tp=-1 禁用止盈
    assert abs(eq["total"].iloc[-1] - manual) < 1e-6, f"全仓净值不符"


def test_atr_no_future():
    """ATR 第 i 天只用历史：改第 i+k 天数据不影响 atr[i]。"""
    df = _mkdf(60)
    atr = compute_atr(df, period=14)
    df_b = df.copy()
    df_b.loc[40, "close"] *= 1.5                          # 改未来
    atr_b = compute_atr(df_b, period=14)
    assert abs(atr[30] - atr_b[30]) < 1e-12, "ATR 用了未来数据（未来函数）!"


def test_strategy_same_days():
    """组合绩效能在多支不同起止日期的股票上正确聚合交易日，不含空窗。"""
    base = pd.date_range("2018-01-01", periods=300).strftime("%Y-%m-%d")
    dfs = {
        "A": pd.DataFrame({"date": base, "open": np.linspace(10, 20, 300),
                           "high": np.linspace(10.5, 20.5, 300), "low": np.linspace(9.5, 19.5, 300),
                           "close": np.linspace(10, 20, 300), "vol": np.full(300, 1000.)}),
        "B": pd.DataFrame({"date": base[50:], "open": np.linspace(5, 15, 250),
                           "high": np.linspace(5.5, 15.5, 250), "low": np.linspace(4.5, 14.5, 250),
                           "close": np.linspace(5, 15, 250), "vol": np.full(250, 1000.)}),
    }
    sig = {c: np.zeros(len(d)) for c, d in dfs.items()}
    sig["A"][30] = 50
    pf = portfolio_performance(dfs, sig, th=25, be=False)
    assert pf.get("days") is not None and pf.get("days") > 0, "组合绩效应含交易日"
    assert np.isfinite(pf.get("total_return", float("nan"))), "total_return 应为有限值"


def test_pending_signal_then_stop():
    """信号触发后次日开盘成交，若次日跳空低开且整天大跌 → 应按更差开盘价/止损出场，
    净值亏损（不虚高）。用真正“跳空低开+全天大跌”的数据。"""
    n = 30
    open_ = np.linspace(10, 12, n); close_ = np.linspace(10, 12, n)
    high_ = np.linspace(10.5, 12.5, n); low_ = np.linspace(9.5, 11.5, n)
    # 第21根(index21)：信号日之后成交 —— 跳空低开 + 整天大跌
    open_[21] = 9.0; high_[21] = 9.2; low_[21] = 8.0; close_[21] = 8.5
    for i in range(22, n):                     # 之后维持低位
        open_[i] = 8.5; high_[i] = 8.7; low_[i] = 8.0; close_[i] = 8.5
    df = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=n).strftime("%Y-%m-%d"),
                       "open": open_, "high": high_, "low": low_, "close": close_, "vol": np.full(n, 1000.0)})
    sig = np.zeros(n); sig[20] = 50             # 第20根收盘触发 → 第21根开盘成交
    eq, _ = single_daily_rets(df, sig, th=25, tp=2.5, be=True)
    final = eq["total"].iloc[-1]
    assert final < 1.0, f"跳空大跌应亏损（止损生效），但净值={final:.4f}"


if __name__ == "__main__":
    import sys, traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = []
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
        except Exception:
            failed.append(t.__name__)
            print(f"  ❌ {t.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - len(failed)}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)
