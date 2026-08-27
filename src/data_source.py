#!/usr/bin/env python3
"""数据层：easy-tdx 拉 NONE 原始价 + 自研前复权（QFQ）+ 本地缓存。

为什么不用 easy-tdx 的 QFQ？已实测：它对不同股票用降级策略（茅台深层历史返回
负价自算），且除权日复权方向会算反（浦发 -16% 原始跳变被算成 +14%）。不可靠。

为什么用 NONE + 自研前复权？
- NONE 原始价与现有信号池共同日期收盘价 100% 一致，数据可信。
- 原始K线在分红除权日有 -15%~-20% 假跳变（非真实下跌），回测会误触发。
- 自研前复权：检测单日向下跳空 >10.5%（A股涨跌停10%，利润除权向下跳空），
  把除权日之前的历史价格按因子缩放，消除假跳变，最能真实反映行情。

ponytail: 单一 easy-tdx 源，不做多源抽象。缓存用 pickle。因子用开盘/前收。
"""
from __future__ import annotations

import os
import pickle
import sys

import numpy as np
import pandas as pd

# 除权检测阈值：A股涨跌停 ±10%，单日向下跳空超此值 → 判为除权除息
EX_DIV_CHG = -0.105


def get_market(code: str) -> str:
    p = code[:2].lower()
    return {"sh": "SH", "sz": "SZ", "bj": "BJ"}.get(p, p.upper())


def _fetch_none(code: str, count: int) -> pd.DataFrame:
    """拉 NONE 原始价（与现有信号池 100% 一致）。"""
    from easy_tdx.cli.conn import get_mac_client
    from easy_tdx.cli.parsers import parse_market, parse_period, parse_adjust

    with get_mac_client() as client:
        raw = client.get_stock_kline(
            parse_market(get_market(code)), code[-6:],
            period=parse_period("DAILY"), start=0, count=count,
            adjust=parse_adjust("NONE"),
        )
    return pd.DataFrame({
        "date": [str(x)[:10] for x in raw["datetime"]],
        "open": raw["open"].astype(float).to_numpy(),
        "high": raw["high"].astype(float).to_numpy(),
        "low": raw["low"].astype(float).to_numpy(),
        "close": raw["close"].astype(float).to_numpy(),
        "vol": raw["vol"].astype(float).to_numpy(),
    }).reset_index(drop=True)


def adjust_qfq(df: pd.DataFrame) -> pd.DataFrame:
    """自研前复权：从最旧除权日起，把之前历史价格乘因子，使除权日无跳变。

    因子 = 除权日开盘价 / 前日收盘价（<1，除权后价格整体下调）。
    逐日扫描，找向下跳空>10.5% 的除权日，把 [0, i) 的历史 OHLC 全乘因子。
    """
    df = df.copy()
    for i in range(1, len(df)):
        chg = df["close"].iloc[i] / df["close"].iloc[i - 1] - 1
        if chg < EX_DIV_CHG:
            f = df["open"].iloc[i] / df["close"].iloc[i - 1]
            df.loc[:i - 1, ["open", "high", "low", "close"]] *= f
    return df


def load_kline(code: str, count: int = 2545, cache_dir: str = "./cache") -> pd.DataFrame:
    """拉取单票前复权日线，带本地缓存。返回列 date/open/high/low/close/vol。"""
    os.makedirs(cache_dir, exist_ok=True)
    cf = os.path.join(cache_dir, f"{code}.pkl")
    try:
        with open(cf, "rb") as f:
            df = pickle.load(f)
        if len(df) >= count:
            return df
    except FileNotFoundError:
        pass

    df = _fetch_none(code, count)
    df = adjust_qfq(df)
    # 去掉高低价非正/含 NaN 行（停牌残留/异常），防回测除零
    df = df[(df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)].dropna()
    df = df.reset_index(drop=True)
    with open(cf, "wb") as f:
        pickle.dump(df, f)
    return df


def sanity_check(df: pd.DataFrame, code: str) -> None:
    """数据自检：列完整、日期单调、复权后无 >13% 异常跳变。失败抛 AssertionError。"""
    for col in ("date", "open", "high", "low", "close", "vol"):
        assert col in df.columns, f"{code}: 缺列 {col}"
    assert len(df) > 20, f"{code}: 数据不足 {len(df)} 行"
    assert (pd.to_datetime(df["date"]).diff().dt.days.dropna() > 0).all(), f"{code}: 日期非单调"
    # QFQ 后仅允许真实涨跌停（≤13%），超 13% 说明除权假跳变未消
    chg = (df["close"].pct_change().abs() * 100).dropna()
    if len(chg) > 0:
        assert chg.max() < 13.0, f"{code}: 残留除权假跳变 {chg.max():.1f}%，复权未生效"


if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "sh600000"
    df = load_kline(code)
    sanity_check(df, code)
    print(f"✅ {code}: {len(df)} 行自研前复权, {df['date'].iloc[-1]} 收盘 {df['close'].iloc[-1]:.2f}")
