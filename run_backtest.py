#!/usr/bin/env python3
"""回测入口：喂信号 → 跑单标的/多标的组合 + 严格7窗WF → 打印绩效。

信号来历：本项目是回测框架，不内置指标引擎(超哥定: 指标引擎后接)。
信号通过外部提供——内置 demo 信号(收盘>MA20)仅用于验证框架可跑通。
接入真实引擎时，把引擎输出的 sig 数组(与各 df 等长)喂进 run() 即可。

用法:
  python run_backtest.py sh600000,sh601318    # 指定股票池
  python run_backtest.py --signal demo        # 用内置 demo 信号
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from backtest import single_daily_rets, portfolio_metrics
from data_source import load_kline, sanity_check
from walkforward import walk_forward


def _demo_sig(df: pd.DataFrame) -> np.ndarray:
    """内置 demo 信号：收盘 > MA20 → +50，否则 0。仅验证框架，不参与引擎评定。"""
    close = df["close"].to_numpy()
    ma20 = pd.Series(close).rolling(20).mean().to_numpy()
    return np.where(close > ma20, 50, 0)


def run(codes: list[str], sig_fn, th: float = 25, tp: float = 2.5, be: bool = True) -> None:
    data = {}
    sig = {}
    for c in codes:
        df = load_kline(c)
        sanity_check(df, c)
        data[c] = df
        sig[c] = sig_fn(df)

    # 组合绩效
    first = data[codes[0]]
    dates = first["date"].to_numpy()
    rets_map = {}
    for c in codes:
        rs, trades, wins = single_daily_rets(data[c], sig[c], th, tp, be)
        rets_map[c] = rs
    m = portfolio_metrics(rets_map, dates)
    print(f"\n=== 组合绩效 ({len(codes)}只, th{th} tp{tp} BE{be}) ===")
    print(f"总收益 {m['total']:+.2f}% | 年化 {m['annual']:+.2f}% | 最大回撤 {m['mdd']:.2f}% | 夏普 {m['sharpe']:.2f} | {m['days']} 交易日")

    # 严格 7 窗样本外 WF
    wf = walk_forward(data, sig, th, tp, be)
    print(f"\n严格7窗样本外WF: 累计 {wf['wf_total']:+.2f}%")
    print(f"  逐窗收益%: {wf['windows']}")

    # 单标的明细
    print("\n=== 单标的 ===")
    for c in codes:
        rs, trades, wins = single_daily_rets(data[c], sig[c], th, tp, be)
        tot = (np.prod(1 + rs) - 1) * 100
        wr = (wins / trades * 100) if trades else 0
        print(f"  {c}: 净收益 {tot:+.2f}% | 交易 {trades} 次 | 胜率 {wr:.1f}%")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="*", default=["sh600000", "sh601318"], help="股票池,如 sh600000,sz000001")
    ap.add_argument("--signal", default="demo", choices=["demo"], help="信号来源(暂只有demo)")
    ap.add_argument("--th", type=float, default=25)
    ap.add_argument("--tp", type=float, default=2.5)
    ap.add_argument("--be", dest="be", action="store_true", default=True)
    args = ap.parse_args()
    run(args.codes, _demo_sig, args.th, args.tp, args.be)
