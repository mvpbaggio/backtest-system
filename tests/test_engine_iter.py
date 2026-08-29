#!/usr/bin/env python3
"""引擎自我迭代系统（v1.4）集成测试。

验证 register_engine / evaluate / optimize_engine / multi_seed_validate /
compare_engines 全链路。用合成K线，快速可复现。
ponytail: 纯 assert 自检，不引入 pytest；python -m tests.test_engine_iter 直接跑。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.engine_iter import (
    register_engine, get_registry, build_engine, evaluate,
    optimize_engine, multi_seed_validate, compare_engines,
)
# 复用 easy-tdx Param（未装则 engine_iter 回退），这里直接用
try:
    from easy_tdx.backtest.strategies.registry import Param
except ImportError:
    from src.engine_iter import Param


def _mk_data(seed=0, n=200, n_symbols=3):
    """合成多标的K线数据（含 date 列，供 walk_forward/portfolio 用）。"""
    rng = np.random.default_rng(seed)
    data = {}
    for i in range(n_symbols):
        c = 10 + np.cumsum(rng.normal(0, 0.2, n))
        df = pd.DataFrame({
            "date": pd.date_range("2020-01-01", periods=n).strftime("%Y-%m-%d"),
            "open": c + rng.normal(0, 0.1, n),
            "high": c + np.abs(rng.normal(0, 0.2, n)),
            "low": c - np.abs(rng.normal(0, 0.2, n)),
            "close": c,
            "vol": rng.integers(1e5, 5e6, n).astype(float),
        })
        data[f"sym{i}"] = df
    return data


# 注册测试引擎
def _ma_cross(df, fast=5, slow=20):
    c = df["close"].to_numpy()
    maf = pd.Series(c).rolling(fast).mean().to_numpy()
    mas = pd.Series(c).rolling(slow).mean().to_numpy()
    sig = np.zeros(len(c))
    for i in range(1, len(c)):
        if maf[i] > mas[i] and maf[i-1] <= mas[i-1]:
            sig[i] = 50
        elif maf[i] < mas[i] and maf[i-1] >= mas[i-1]:
            sig[i] = -50
    return sig


register_engine("test_ma", "测试双均线", _ma_cross, params=[
    Param("fast", int, default=5, min_value=2, max_value=50),
    Param("slow", int, default=20, min_value=5, max_value=250),
])


# 装饰器用法（README 示例方式，回归 Bug：装饰器缺 fn 参数会崩）
@register_engine("test_deco", "装饰器测试", params=[Param("th", int, default=25)])
def _test_deco(df, th=25):
    c = df["close"].to_numpy()
    return np.where(c > 0, 50, 0)


def test_registry_has_engine():
    assert "test_ma" in get_registry(), "引擎未注册"
    assert "test_deco" in get_registry(), "装饰器注册失败"


def test_deco_engine_fn_is_original():
    """装饰器注册的 fn 应是原函数（不是 None/包装器），且能用默认参数构建。"""
    assert get_registry()["test_deco"].fn is _test_deco, "装饰器注册的 fn 不是原函数"
    eng = build_engine("test_deco")
    df = _mk_data()["sym0"]
    sig = eng(df)
    assert len(sig) == len(df), "装饰器构建的引擎输出长度不对"


def test_build_engine():
    eng = build_engine("test_ma", fast=5, slow=20)
    df = _mk_data()["sym0"]
    sig = eng(df)
    assert len(sig) == len(df), "信号长度应等于K线长度"
    assert (sig != 0).any(), "应有信号产生"


def test_evaluate():
    data = _mk_data()
    r = evaluate("test_ma", {"fast": 5, "slow": 20}, data)
    assert "total" in r and "score" in r, "evaluate 应返回 total/score"
    assert np.isfinite(r["total"]), "收益应为有限值"


def test_optimize_engine():
    data = _mk_data()
    res = optimize_engine("test_ma", {"fast": [3, 5, 10], "slow": [20, 30]}, data,
                          objective="score", min_trades=0)
    assert "best" in res
    assert res["best"] is not None, "应找到最优参数"
    assert "fast" in res["best"]["params"], "最优应含参数"

def test_compare_engines():
    data = _mk_data()
    rows = compare_engines(["test_ma"], [{"fast": 5, "slow": 20}], data, min_trades=0)
    assert len(rows) == 1, "应返回1个引擎结果"
    assert "score" in rows[0], "结果应含评分"


def test_multi_seed_validate():
    data_by_seed = {42: _mk_data(42), 7: _mk_data(7)}
    res = multi_seed_validate("test_ma", {"fast": 5, "slow": 20}, data_by_seed, min_trades=0)
    assert "avg" in res and res["avg"] is not None, "应返回平均绩效"
    assert "total" in res["avg"], "平均应含收益"


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
