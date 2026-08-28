#!/usr/bin/env python3
"""引擎协议层 v1.3 —— 全兼容接口核心。

统一引擎接入约定，彻底消除 use_kl_array 全局开关和列序错位问题。

## 引擎函数唯一约定
    fn(x) -> np.ndarray      # 输出与 K线等长的 sig 数组（+50买/-50卖/0无）

其中输入 x 支持三种，协议层自动适配，引擎无需关心：
    - DataFrame 派：fn(df)，内部 df["close"] 取列
    - numpy 数组派：fn(kl)，kl 形状 (n,5)，列序 = KL_COLUMNS
    - 带参派：fn(df, p=...)，协议层用默认参数调用

## 列序单一真源
    KL_COLUMNS = ["open","high","low","close","vol"]   # close 在最后
    df 与数组统一用此列序，杜绝错位。

依赖：仅 stdlib + numpy。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 单一真源列序（close 在最后，与 data_source / 通达信天然顺序一致）
KL_COLUMNS = ["open", "high", "low", "close", "vol"]


def normalize_kl(df: pd.DataFrame) -> np.ndarray:
    """把 DataFrame 转成 (n,5) 数组，列序 = KL_COLUMNS（open,high,low,close,vol）。

    喂给「numpy 数组派」引擎（BIG-A-POWER、TA-Lib 式）。
    缺列时抛 ValueError，杜绝列序错位。
    """
    missing = [c for c in KL_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"K线缺列 {missing}，需要 {KL_COLUMNS}")
    return df[KL_COLUMNS].to_numpy()


def detect_input(engine_fn) -> str:
    """判断引擎吃 DataFrame 还是 numpy 数组：返回 "array" 或 "df"。

    策略：先试 ndarray（数组派特征）。若引擎能处理 ndarray → array；
    否则试 DataFrame。个别 DataFrame 派引擎误接受 ndarray 会判 array，
    但 ndarray 派引擎必然抓取正确，且归一化后结果一致，故可接受。
    """
    # probe 用 120 根：足够覆盖长窗口指标(MA60/MACD等)，避免数据不足误判
    n = 120
    base = np.linspace(10, 20, n)
    probe = pd.DataFrame({
        "open": base, "high": base + 0.5,
        "low": base - 0.5, "close": base, "vol": np.full(n, 1e6),
    })
    try:
        engine_fn(probe[KL_COLUMNS].to_numpy())
        return "array"
    except Exception:
        # ndarray 不行 → 试 DataFrame
        try:
            engine_fn(probe)
            return "df"
        except Exception:
            # 都失败 → 默认按 DataFrame 处理（最通用），真实调用时抛清晰错误
            return "df"


def call_engine(engine_fn, df: pd.DataFrame) -> np.ndarray:
    """按引擎输入偏好调用，返回对齐到 df 长度的 sig 数组。

    数组派：喂 df[KL_COLUMNS].to_numpy()（列序单一真源）。
    DataFrame派：直接喂 df。
    """
    kind = detect_input(engine_fn)
    if kind == "array":
        return np.asarray(engine_fn(normalize_kl(df)), dtype=float)
    return np.asarray(engine_fn(df), dtype=float)


# 与 backtest._align_sig 等价的对齐工具（避免跨模块私有依赖）
def align_sig(sig: np.ndarray, n: int) -> np.ndarray:
    """sig 归一化到长度 n：短→补0(无信号)，长→截断。防越界。"""
    sig = np.asarray(sig, dtype=float)
    if len(sig) < n:
        return np.pad(sig, (0, n - len(sig)), constant_values=0.0)
    if len(sig) > n:
        return sig[:n]
    return sig
