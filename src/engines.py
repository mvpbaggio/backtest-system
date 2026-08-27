"""内置参考指标引擎（双均线 / MACD / RSI）。

这些是经典"金叉买/死叉卖"波段引擎，作为**基准对照组**——你开发新指标引擎时，
用它们对比，看你的引擎能不能跑赢这些教科书款。

统一接口: `fn(df) -> np.ndarray`，输出与 df 等长的 sig 数组：
  +50 = 金叉/买入信号, -50 = 死叉/卖出信号, 0 = 无信号

配套回测: 波段引擎建议用 `exit_mode="signal"`（纯信号死叉卖出），
不要叠趋势引擎的吊灯止损/移动止盈，否则会过度敏感平仓、吃不到趋势。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(x, p):
    return pd.Series(x).rolling(p).mean().to_numpy()


def ema(x, p):
    return pd.Series(x).ewm(span=p, adjust=False).mean().to_numpy()


def ma_cross(df, fast: int = 5, slow: int = 20) -> np.ndarray:
    """双均线金叉死叉：短均线上穿长均线买(金叉)，下穿卖(死叉)。默认 MA5/MA20。"""
    close = df["close"].to_numpy()
    maf, mas = sma(close, fast), sma(close, slow)
    sig = np.zeros(len(close))
    for i in range(1, len(close)):
        if maf[i] > mas[i] and maf[i - 1] <= mas[i - 1]:
            sig[i] = 50
        elif maf[i] < mas[i] and maf[i - 1] >= mas[i - 1]:
            sig[i] = -50
    return sig


def macd_cross(df, short: int = 12, long: int = 26, signal: int = 9) -> np.ndarray:
    """MACD 金叉死叉：DIF 上穿 DEA 买(金叉)，下穿卖(死叉)。"""
    close = df["close"].to_numpy()
    if len(close) < long + signal:
        return np.zeros(len(close))
    efast, eslow = ema(close, short), ema(close, long)
    dif = efast - eslow
    dea = ema(dif, signal)
    sig = np.zeros(len(close))
    for i in range(1, len(close)):
        if dif[i] > dea[i] and dif[i - 1] <= dea[i - 1]:
            sig[i] = 50
        elif dif[i] < dea[i] and dif[i - 1] >= dea[i - 1]:
            sig[i] = -50
    return sig


def rsi_reversal(df, period: int = 14, oversold: float = 30, overbought: float = 70) -> np.ndarray:
    """RSI 超买超卖：超卖回升买(<30 后拐头)，超买回落卖(>70 后拐头)。"""
    close = df["close"].to_numpy()
    n = len(close)
    if n < period + 2:
        return np.zeros(n)
    delta = np.zeros(n)
    delta[1:] = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    rsi = np.full(n, np.nan)
    for i in range(period, n):
        ag = gain[i - period + 1:i + 1].mean()
        al = loss[i - period + 1:i + 1].mean()
        rsi[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    sig = np.zeros(n)
    for i in range(period + 1, n):
        if rsi[i] < oversold and rsi[i - 1] >= oversold:
            sig[i] = 50
        elif rsi[i] > overbought and rsi[i - 1] <= overbought:
            sig[i] = -50
    return sig


def mytt_macd(df, short: int = 12, long: int = 26, signal: int = 9) -> np.ndarray:
    """easy-tdx MyTT MACD 金叉死叉：DIF 上穿 DEA 买，下穿卖。
    (用 easy_tdx.MyTT 原生 MACD 实现)
    """
    try:
        from easy_tdx.MyTT import MACD as _macd
    except ImportError:
        # 无 easy-tdx 时回退到内置 ema 实现
        return macd_cross(df, short, long, signal)
    close = df["close"].to_numpy()
    if len(close) < long + signal:
        return np.zeros(len(close))
    dif, dea, _hist = _macd(close, short, long, signal)
    dif, dea = np.array(dif, dtype=float), np.array(dea, dtype=float)
    sig = np.zeros(len(close))
    for i in range(1, len(close)):
        if dif[i] > dea[i] and dif[i - 1] <= dea[i - 1]:
            sig[i] = 50
        elif dif[i] < dea[i] and dif[i - 1] >= dea[i - 1]:
            sig[i] = -50
    return sig


# 默认对比引擎：easy-tdx MyTT MACD + 内置 MACD 金叉死叉
REFERENCE_ENGINES = {
    "easy-tdx MyTT MACD": mytt_macd,
    "MACD金叉死叉": macd_cross,
}
