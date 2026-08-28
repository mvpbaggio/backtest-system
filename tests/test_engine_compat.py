#!/usr/bin/env python3
"""引擎接口全兼容测试（v1.3）。

验证 engine_api 的 detect_input / call_engine 能准确适配不同类型引擎，
evaluate_engine 对数组派、DataFrame 派、带参派引擎都能跑通。

用合成K线（不依赖 easy-tdx 网络），快速、可复现。

ponytail: 纯 assert 自检，不引入 pytest；python -m tests.test_engine_compat 直接跑。
"""
from __future__ import annotations

import sys
import os

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.engine_api import KL_COLUMNS, detect_input, call_engine
from src.benchmark import evaluate_engine
from src.engines import mytt_macd, macd_cross, ma_cross, rsi_reversal


def _mk_kl(n=200, seed=0):
    """合成K线 DataFrame（列序 = data_source 实际输出）。"""
    rng = np.random.default_rng(seed)
    c = 10 + np.cumsum(rng.normal(0, 0.2, n))
    o = c + rng.normal(0, 0.1, n)
    h = np.maximum(o, c) + np.abs(rng.normal(0, 0.1, n))
    l = np.minimum(o, c) - np.abs(rng.normal(0, 0.1, n))
    v = rng.integers(1e5, 5e6, n).astype(float)
    return pd.DataFrame({
        "date": [str(i) for i in range(n)],
        "open": o, "high": h, "low": l, "close": c, "vol": v,
    })


# ---- 各种派的引擎（保持原生输入偏好，验证 detect_input 准确识别） ----

def array_engine(kl):
    """数组派：吃 (n,5) K线数组，close = kl[:, KL_COLUMNS.index('close')]。"""
    close = kl[:, KL_COLUMNS.index("close")]
    # 纯历史 MA20（cumsum 前缀），与 DataFrame 派 rolling 一致，避免 convolve 未来函数
    cs = np.concatenate([[0.0], np.cumsum(close)])
    ma20 = np.full(len(close), np.nan)
    ma20[19:] = (cs[20:] - cs[:len(close) - 19]) / 20
    return np.where(close > ma20, 50, 0)


def df_engine(df):
    """DataFrame派：吃 df，按列名取 close。"""
    c = df["close"].to_numpy()
    ma20 = pd.Series(c).rolling(20).mean().to_numpy()
    return np.where(c > ma20, 50, 0)


# ---- 第三方库兼容性（可选）：本地装了 ta/pandas-ta/TA-Lib 才测，没装跳过 ----
def _libs_available() -> bool:
    """检测 ta / pandas_ta / talib 是否本地可用（可选依赖，非交付必需）。"""
    try:
        import ta, pandas_ta, talib  # noqa
        return True
    except ImportError:
        return False


_HAS_LIBS = _libs_available()


def _make_lib_engines():
    """构造引用第三方库的引擎（仅当一个都不缺时可用）。"""
    def talib_engine(kl):
        """数组派：talib.MACD，吃 numpy 数组。"""
        import talib
        close = kl[:, KL_COLUMNS.index("close")]
        dif, dea, _ = talib.MACD(close)
        sig = np.zeros(len(close))
        for i in range(1, len(close)):
            if dif[i] > dea[i] and dif[i - 1] <= dea[i - 1]:
                sig[i] = 50
            elif dif[i] < dea[i] and dif[i - 1] >= dea[i - 1]:
                sig[i] = -50
        return sig

    def ta_engine(df):
        """DataFrame派：ta 库双均线，吃 pandas Series/DataFrame。"""
        import ta
        c = df["close"]
        fast = ta.trend.sma_indicator(c, 5)
        slow = ta.trend.sma_indicator(c, 20)
        sig = np.zeros(len(c))
        for i in range(1, len(c)):
            if fast[i] > slow[i] and fast[i - 1] <= slow[i - 1]:
                sig[i] = 50
            elif fast[i] < slow[i] and fast[i - 1] >= slow[i - 1]:
                sig[i] = -50
        return sig

    def pandas_ta_engine(df):
        """DataFrame派：pandas_ta RSI 超买超卖，吃 pandas Series。"""
        import pandas_ta as pta
        c = df["close"]
        r = pta.rsi(c, length=14)
        sig = np.zeros(len(c))
        for i in range(1, len(c)):
            if r[i] < 30 and r[i - 1] >= 30:
                sig[i] = 50
            elif r[i] > 70 and r[i - 1] <= 70:
                sig[i] = -50
        return sig

    return talib_engine, ta_engine, pandas_ta_engine


# ---- 测试1：detect_input 准确识别输入类型 ----
def test_detect_input_types():
    cases = {
        "array_engine": (array_engine, "array"),
        "df_engine": (df_engine, "df"),
        # 参考引擎已兼容数组/DF（_close helper），detect 先试数组判 array 合理
        "内置mytt_macd": (mytt_macd, "array"),
        "内置macd_cross": (macd_cross, "array"),
    }
    if _HAS_LIBS:
        # 第三方库引擎已装：验证 detect_input 对 talib(数组派)/ta,pandas_ta(DataFrame派)的识别
        t_eng, ta_eng, pta_eng = _make_lib_engines()
        cases.update({
            "talib_engine": (t_eng, "array"),
            "ta_engine": (ta_eng, "df"),
            "pandas_ta_engine": (pta_eng, "df"),
        })
    for name, (fn, expect) in cases.items():
        got = detect_input(fn)
        assert got == expect, f"{name}: detect_input={got}，期望 {expect}"


# ---- 测试2：数组派与DataFrame派引擎产出等价信号（同一逻辑两实现） ----
def test_array_vs_df_equivalent():
    """同一 MA20 逻辑，数组派和 DataFrame 派应产出等价买卖信号。"""
    df = _mk_kl()
    s_arr = call_engine(array_engine, df)      # call_engine 自动判 array
    s_df = call_engine(df_engine, df)          # 判 df
    # 两者都是"收盘>MA20"信号，方向应一致（允许边界 minor 差）
    assert (np.sign(np.nan_to_num(s_arr)) == np.sign(np.nan_to_num(s_df))).all(), \
        "数组派与DataFrame派同逻辑引擎信号应方向一致"


# ---- 测试3：evaluate_engine 对数组派/DataFrame派都能跑通 ----
def test_evaluate_engine_both_types():
    codes = ["sh600000", "sh601318", "sz000001"]
    for name, eng in [("数组派", array_engine), ("DataFrame派", df_engine)]:
        r = evaluate_engine(eng, codes=codes, n_sample=None, exit_mode="auto")
        assert r["n"] > 0, f"{name}: 无数据通过自检"
        assert np.isfinite(r["your"]["total"]), f"{name}: 总收益非有限值"
        assert np.isfinite(r["your"]["score"]), f"{name}: 评分非有限值"


# ---- 测试4：第三方库引擎（talib 数组派 / ta / pandas_ta DataFrame派）全可跑（可选） ----
def test_third_party_engines_run():
    if not _HAS_LIBS:
        print("  ⚠️ 第三方库(ta/pandas-ta/TA-Lib)未装，跳过第三方引擎测试")
        return
    kl = _mk_kl()
    t_eng, ta_eng, pta_eng = _make_lib_engines()
    for name, fn in [("talib", t_eng), ("ta", ta_eng), ("pandas_ta", pta_eng)]:
        sig = call_engine(fn, kl)
        assert len(sig) == len(kl), f"{name}: 信号长度 {len(sig)} != {len(kl)}"
        print(f"  ✅ {name} 引擎跑通")


# ---- 测试5：exit_mode="auto" 时参考引擎同规则对比（评分公平） ----
def test_auto_mode_reference_same_rule():
    """被测引擎 auto 判出模式后，参考引擎应同步该模式，保证同规则对比。"""
    codes = ["sh600000", "sh601318", "sz000001"]

    # 只买不卖引擎（无卖出信号）→ auto 判 trailing
    def long_only_engine(df):
        c = df["close"].to_numpy()
        ma20 = pd.Series(c).rolling(20).mean().to_numpy()
        return np.where(c > ma20, 50, 0)

    r = evaluate_engine(long_only_engine, codes=codes, n_sample=None, exit_mode="auto")
    assert r["your"]["exit_mode"] == r["reference"][0]["exit_mode"], \
        "auto 模式下被测与参考引擎出场模式应一致（同规则对比）"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {fn.__name__}: {e}")
        except Exception as e:
            print(f"  ❌ {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} 通过")
    sys.exit(0 if passed == len(fns) else 1)
