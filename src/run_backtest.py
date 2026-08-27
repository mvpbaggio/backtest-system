#!/usr/bin/env python3
"""回测入口：喂信号 → 跑多标的组合绩效(19项指标) + 严格7窗WF → 打印。

回测系统只吃信号。信号由外部提供——内置 demo(收盘>MA20) + 3个参考引擎
(engines.py) 仅用于验证链路/对比。接入真实引擎时，把引擎输出的 sig 数组喂
进 run() 即可。

用法:
  python -m src.run_backtest sh600000,sh601318    # 指定股票池
  python -m src.run_backtest sh600000 --exit-mode signal
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from .backtest import single_daily_rets, portfolio_performance
from .data_source import load_kline, sanity_check
from .walkforward import walk_forward


def _demo_sig(df: pd.DataFrame) -> np.ndarray:
    """demo 信号：收盘上穿 MA20 买(+50)，下穿 MA20 卖(-50)。
    双向信号，让 signal 模式下也能正确跑波段（仅验证链路，不参与引擎评定）。
    """
    close = df["close"].to_numpy()
    ma20 = pd.Series(close).rolling(20).mean().to_numpy()
    sig = np.zeros(len(close))
    for i in range(1, len(close)):
        if close[i] > ma20[i] and close[i-1] <= ma20[i-1]:
            sig[i] = 50      # 上穿买
        elif close[i] < ma20[i] and close[i-1] >= ma20[i-1]:
            sig[i] = -50     # 下穿卖
    return sig


def run(codes, sig_fn, th=25, tp=2.5, be=True, exit_mode="signal"):
    data, sig = {}, {}
    for c in codes:
        df = load_kline(c)
        sanity_check(df, c)
        data[c] = df
        sig[c] = sig_fn(df)

    # 组合绩效（PerformanceAnalyzer 19项）
    m = portfolio_performance(data, sig, th, tp, be, exit_mode=exit_mode)
    print(f"\n=== 组合绩效 ({len(codes)}只, th{th} tp{tp} BE{be} exit={exit_mode}) ===")
    print(f"总收益 {m['total_return']*100:+.2f}% | 年化 {m['annual_return']*100:+.2f}% | "
          f"最大回撤 {m['max_drawdown']*100:.2f}% | 夏普 {m['sharpe']:.2f} | "
          f"索提诺 {m.get('sortino',0):.2f} | 胜率 {m['win_rate']:.1f}% | {m['days']} 交易日")
    print(f"  利润因子 {m.get('profit_factor',0):.2f} | 波动率 {m.get('volatility',0)*100:.1f}% | 卡玛 {m.get('calmar',0):.2f}")

    # 严格 7 窗样本外 WF
    wf = walk_forward(data, sig, th, tp, be, exit_mode=exit_mode)
    print(f"\n严格7窗样本外WF: 累计 {wf['wf_total']:+.2f}%")
    print(f"  逐窗收益%: {wf['windows']}")

    # 单标的明细
    print("\n=== 单标的 ===")
    for c in codes:
        eq, tds = single_daily_rets(data[c], sig[c], th, tp, be, exit_mode=exit_mode)
        tot = (eq["total"].iloc[-1] - 1) * 100
        tr = len(tds)
        wins = int((tds["pnl"] > 0).sum()) if tr else 0
        wr = wins / tr * 100 if tr else 0
        print(f"  {c}: 净收益 {tot:+.2f}% | 交易 {tr} 次 | 胜率 {wr:.1f}%")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="*", default=["sh600000", "sh601318", "sz000001"])
    ap.add_argument("--signal", default="demo", choices=["demo"])
    ap.add_argument("--th", type=float, default=25)
    ap.add_argument("--tp", type=float, default=2.5)
    ap.add_argument("--be", dest="be", action="store_true", default=True)
    ap.add_argument("--exit-mode", dest="exit_mode", default="signal",
                    choices=["signal", "trailing", "long_only"],
                    help="出场模式: signal=有买有卖(默认) / trailing=趋势止损止盈 / long_only=只买不卖")
    args = ap.parse_args()
    run(args.codes, _demo_sig, args.th, args.tp, args.be, args.exit_mode)
