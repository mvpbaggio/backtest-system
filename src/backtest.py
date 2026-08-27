#!/usr/bin/env python3
"""回测核心：单标的逐日收益 + 多标的等权组合绩效。

【实测选型】经"测试出最好结果"对比：
- 数据层：自研前复权（easy-tdx 的 QFQ 实测不可靠，逐步被弃——茅台负价、浦发方向反）
- 出场逻辑：吊灯ATR+BE+TP（已验证，真实 high/low 触发，无未来函数）
- 绩效报表：easy-tdx 的 PerformanceAnalyzer（19 项指标，纯计算零链路风险，实测可用）
- easy-tdx 的 OrderSimulator/PortfolioBacktestEngine：实测 0 交易，弃用

成本(真实A股)：佣金双边 + 印花税卖出 + 滑点，费率可在 config 调。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 默认费率
DEF_COMMISSION = 0.0003   # 佣金 0.03% 双边
DEF_STAMP_TAX = 0.0005    # 印花税 0.05% 卖出
DEF_SLIPPAGE = 0.0        # 滑点（默认0，实测真实盘口再调）
ATR_PERIOD = 14
ATR_STOP_MULT = 3.0       # 吊灯止损 ATR×3


def compute_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> np.ndarray:
    """ATR(真实波幅均值)，严格用截至当日的历史数据，无未来函数。

    原实现 np.convolve(mode="same") 是中心对齐——第 i 天会用 到 i+period/2 天
    之后的 TR，属未来函数(决策时未来还没发生)，会导致止损/止盈点失真。
    这里改用 cumsum 前缀滚动均值，atr[i] 只依赖 tr[0..i]，彻底消除未来暴露。
    """
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    n = len(c)
    tr = np.zeros(n)
    tr[1:] = np.maximum(
        h[1:] - l[1:],
        np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])),
    )
    # 向量化前缀滚动均值（替代逐日 for，477只×2500根下提速明显）
    # atr[i] = mean(tr[i-period+1..i])，cumsum 前缀差
    csum = np.concatenate([[0.0], np.cumsum(tr)])
    atr = np.full(n, np.nan)
    denom = np.arange(1, n + 1)
    # 冷启动段(1..period-1)：均值 = csum[i+1]/i （不足一整窗，用已有历史）
    cold = csum[1:period] / denom[:period - 1]
    atr[1:period] = cold if period - 1 <= n else cold[:n]
    # 正式段(period..n)：窗口 period
    if n >= period:
        hot = (csum[period:] - csum[:n - period + 1]) / period
        atr[period - 1:] = hot   # 注意: atr[period-1] 才对应满窗首日
    # 填充仍为 NaN 的首根
    atr[0] = tr[1] if n > 1 and np.isfinite(tr[1]) else 1.0
    # 兜底：非法值/非正
    atr[~np.isfinite(atr)] = np.nanmean(tr[1:]) if np.isfinite(tr[1:]).any() else 1.0
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
    exit_mode: str = "signal",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """单标的逐日收益 → 资金曲线 + 交易明细。

    返回 (equity_df, trades_df)：
    - equity_df: total(净值) / drawdown / drawdown_pct，供 PerformanceAnalyzer
    - trades_df: direction / pnl / rejected，供 PerformanceAnalyzer

    exit_mode 区分三类引擎的出场方式：
    - "signal"   (波段引擎)：买入靠 sig>=th，卖出靠死叉信号 sig<=-th，不叠止损止盈
    - "trailing" (趋势引擎)：买入靠 sig>=th，出场靠吊灯ATR(×3)+保本BE+移动止盈TP，
      死叉信号也触发平仓(双保险)
    - "long_only"(只买不卖)：买入靠 sig>=th，**无任何主动卖出/止损**，只持有到期末平仓
      (专门用于对比'只买不卖'引擎的性能)
    三者都 next_open 成交、真实成本、无未来函数。
    """
    c = df["close"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    o = df["open"].to_numpy()
    n = len(c)
    atr = compute_atr(df)
    dates = df["date"].to_numpy()
    buy_cost = commission
    sell_cost = commission + stamp_tax + slippage

    # 健壮性：归一化 sig 到与 df 等长（短→补0无信号，长→截断），防越界崩溃
    if len(sig) < n:
        sig = np.pad(sig, (0, n - len(sig)), constant_values=0.0)
    elif len(sig) > n:
        sig = sig[:n]

    nav = 1.0
    equity = [1.0]
    trades = []
    holding = False          # 是否已持仓(进入 bar i 前)
    pending_buy = False      # sig>=th 触发买入，等次日开盘买入 (next_open)
    pending_sell = False     # 持仓时 sig<=-th 触发卖出，等次日开盘卖出 (next_open)
    entry = 0.0
    hi = 0.0
    entry_date = None

    for i in range(1, n):
        # 1. 买入处理(信号于昨日收盘触发 → 今日开盘价成交 next_open)
        if pending_buy and not holding:
            entry = float(o[i])
            nav *= (1 - buy_cost) * (c[i] / entry)      # 开盘买入(扣佣金)持有到今收
            trades.append({"date": dates[i], "direction": "BUY",
                           "pnl": 0.0, "rejected": False, "entry_date": dates[i]})
            holding = True
            hi = float(h[i])
            entry_date = dates[i]
            pending_buy = False
            equity.append(nav)
            continue

        # 2. 卖出处理(持仓时昨日产生死叉信号 → 今日开盘价卖出 next_open)
        if pending_sell and holding:
            exit_px = float(o[i])
            # 净值乘数 = exit_px/c[i-1] * (1-sell_cost)，含成本；pnl 记录收益率
            nav *= (exit_px / c[i - 1]) * (1 - sell_cost)
            trades.append({"date": dates[i], "direction": "SELL",
                           "pnl": exit_px / c[i - 1] - 1 - sell_cost, "rejected": False,
                           "entry_date": entry_date})
            holding = False; entry = 0.0; hi = 0.0
            pending_sell = False
            equity.append(nav)
            continue

        # 3. 持仓 bar：昨收→今收 mark-to-market，或触发止损/止盈(仅 trailing 模式)
        if holding:
            hi = max(hi, h[i])
            stop = entry - ATR_STOP_MULT * atr[i]
            if exit_mode == "trailing":
                if be and hi - entry > atr[i]:
                    stop = max(stop, entry)           # 保本：盈利超1ATR后止损上移成本
                if tp > 0:
                    stop = max(stop, hi - tp * atr[i])   # 移动止盈：距高点 tp×ATR 回落出场
            if exit_mode == "trailing" and l[i] <= stop:
                exit_px = o[i] if o[i] < stop else stop   # 跳空低开按更差开盘价
                ret = exit_px / c[i - 1] - 1 - sell_cost
                nav *= (1 + ret)
                trades.append({"date": dates[i], "direction": "SELL",
                               "pnl": ret, "rejected": False, "entry_date": entry_date})
                holding = False; entry = 0.0; hi = 0.0
            else:
                nav *= (1 + (c[i] / c[i - 1] - 1))
                if exit_mode != "long_only" and sig[i] <= -th:   # 死叉信号 → 次日开盘卖出(非long_only才认)
                    pending_sell = True
        else:
            # 4. 空仓：记录今日盘后信号 → 次日开盘成交
            if sig[i] >= th:
                pending_buy = True
        equity.append(nav)

    # 期末仍持仓 → 按末日收盘平仓(持仓收益已逐日计入，只扣卖出成本)
    if holding:
        nav *= (1 - sell_cost)
        final_ret = c[-1] / entry - 1 - sell_cost
        trades.append({"date": dates[-1], "direction": "SELL",
                       "pnl": final_ret, "rejected": False, "entry_date": entry_date})
        equity[-1] = nav

    equity_arr = np.array(equity)
    peak = np.maximum.accumulate(equity_arr)
    dd = equity_arr / peak - 1
    equity_df = pd.DataFrame({
        "total": equity_arr, "drawdown": dd, "drawdown_pct": -dd,
    })
    trades_df = pd.DataFrame(trades) if trades else pd.DataFrame(
        columns=["date", "direction", "pnl", "rejected", "entry_date"])
    return equity_df, trades_df


def portfolio_performance(
    data: dict[str, pd.DataFrame],
    sig: dict[str, np.ndarray],
    th: float = 25,
    tp: float = 2.5,
    be: bool = True,
    commission: float = DEF_COMMISSION,
    stamp_tax: float = DEF_STAMP_TAX,
    slippage: float = DEF_SLIPPAGE,
    exit_mode: str = "signal",
) -> dict:
    """多标的等权组合绩效（用 easy-tdx PerformanceAnalyzer 出 19 项指标）。

    组合日收益 = 各标的日子收益等权平均（资金池流动），再累计成组合资金曲线；
    组合交易 = 各标的交易合并。喂给 PerformanceAnalyzer。
    exit_mode: "signal"(波段纯信号) / "trailing"(趋势止损止盈)
    """
    from easy_tdx.backtest.performance import PerformanceAnalyzer

    anchor_dates = data[list(data.keys())[0]]["date"].to_numpy()
    daily_map: dict[str, list[float]] = {d: [] for d in anchor_dates}
    all_trades = []

    for code in data:
        eq, tds = single_daily_rets(data[code], sig[code], th, tp, be,
                                    commission, stamp_tax, slippage, exit_mode)
        # 该标的日子收益（用资金曲线差分还原），按各标的自己的日期索引
        tot = eq["total"].to_numpy()
        dayrets = np.diff(tot) / tot[:-1]
        own_dates = data[code]["date"].to_numpy()
        for d, r in zip(own_dates[1:], dayrets):
            daily_map.setdefault(d, []).append(r)
        if len(tds):
            all_trades.append(tds)

    # 只保留当天至少有一只股票数据的日期（避免空列表均值 NaN 污染净值）
    ds = sorted(set(daily_map) - set(d for d in daily_map if len(daily_map[d]) == 0))
    if not ds:
        # 完全无数据——返回全 0 绩效
        empty = pd.DataFrame({"total": [1.0, 1.0], "drawdown": [0.0, 0.0], "drawdown_pct": [0.0, 0.0]})
        tmp = pd.DataFrame(columns=["direction", "pnl", "rejected"])
        return PerformanceAnalyzer(empty, tmp, risk_free_rate=0.03).compute()
    comb = np.array([np.mean(daily_map[d]) for d in ds])
    nav = np.concatenate([[1.0], np.cumprod(1 + comb)])   # 组合净值 + 1 根
    peak = np.maximum.accumulate(nav)
    dd = nav / peak - 1
    equity_df = pd.DataFrame({"total": nav, "drawdown": dd, "drawdown_pct": -dd})

    trades_df = pd.concat(all_trades, ignore_index=True) if all_trades else \
        pd.DataFrame(columns=["date", "direction", "pnl", "rejected"])
    # PerformanceAnalyzer 要求 direction/pnl/rejected
    trades_df = trades_df[["direction", "pnl", "rejected"]]

    analyzer = PerformanceAnalyzer(equity_df, trades_df, risk_free_rate=0.03)
    perf = analyzer.compute()

    # 附上标准字段别名，方便下游读取
    perf["win_rate"] = perf.get("win_rate", 0) * 100
    perf["total_trades"] = len(trades_df)
    perf["days"] = len(ds)
    return perf


if __name__ == "__main__":
    # 自检：收盘>MA20 信号，验证框架能产生真实交易 + 绩效可算
    import pandas as pd
    from .data_source import load_kline, sanity_check

    df = load_kline("sh600000")
    sanity_check(df, "sh600000")
    close = df["close"].to_numpy()
    ma20 = pd.Series(close).rolling(20).mean().to_numpy()
    sig = np.where(close > ma20, 50, 0)
    eq, tds = single_daily_rets(df, sig, th=25, tp=2.5, be=True)
    print(f"✅ sh600000 自检: {len(eq)} 根净值, 交易 {len(tds)} 次")
    print(f"  自研前复权后最大单日涨跌 {abs(pd.Series(close).pct_change()).max()*100:.1f}%")
