#!/usr/bin/env python3
"""回测入口：喂信号 → 跑多标的组合绩效(19项指标) + 严格7窗WF → 打印。

本项目是回测框架，不内置指标引擎。信号由外部提供——内置 demo(收盘>MA20)
仅用于验证链路。接入真实引擎时，把引擎输出的 sig 数组喂进 run() 即可。

用法:
  python run_backtest.py sh600000,sh601318    # 指定股票池
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
    """demo 信号：收盘 > MA20 → +50。仅验证框架，不参与引擎评定。"""
    close = df["close"].to_numpy()
    ma20 = pd.Series(close).rolling(20).mean().to_numpy()
    return np.where(close > ma20, 50, 0)


def run(codes, sig_fn, th=25, tp=2.5, be=True):
    data, sig = {}, {}
    for c in codes:
        df = load_kline(c)
        sanity_check(df, c)
        data[c] = df
        sig[c] = sig_fn(df)

    # 组合绩效（PerformanceAnalyzer 19项）
    m = portfolio_performance(data, sig, th, tp, be)
    print(f"\n=== 组合绩效 ({len(codes)}只, th{th} tp{tp} BE{be}) ===")
    print(f"总收益 {m['total_return']*100:+.2f}% | 年化 {m['annual_return']*100:+.2f}% | "
          f"最大回撤 {m['max_drawdown']*100:.2f}% | 夏普 {m['sharpe']:.2f} | "
          f"索提诺 {m.get('sortino',0):.2f} | 胜率 {m['win_rate']:.1f}% | {m['days']} 交易日")
    print(f"  利润因子 {m.get('profit_factor',0):.2f} | 波动率 {m.get('volatility',0)*100:.1f}% | 卡玛 {m.get('calmar',0):.2f}")

    # 严格 7 窗样本外 WF
    wf = walk_forward(data, sig, th, tp, be)
    print(f"\n严格7窗样本外WF: 累计 {wf['wf_total']:+.2f}%")
    print(f"  逐窗收益%: {wf['windows']}")

    # 单标的明细
    print("\n=== 单标的 ===")
    for c in codes:
        eq, tds = single_daily_rets(data[c], sig[c], th, tp, be)
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
    args = ap.parse_args()
    run(args.codes, _demo_sig, args.th, args.tp, args.be)
