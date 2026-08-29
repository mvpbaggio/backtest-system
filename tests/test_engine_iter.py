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


def test_compare_engines_builtin_auto():
    """内置参考引擎名（MACD金叉死叉）应自动注册纳入对比，无需手动注册。"""
    data = _mk_data()
    rows = compare_engines(["MACD金叉死叉"], [{}], data, min_trades=0)
    assert len(rows) == 1, "内置引擎应自动注册并纳入对比"
    assert rows[0]["engine"] == "MACD金叉死叉", "应识别内置引擎名"


def test_optimize_random_mode():
    """random 搜索模式应能找到最优，且不因网格爆炸报错。"""
    data = _mk_data()
    res = optimize_engine("test_ma", {"fast": [2,3,4,5,6,7,8,9,10], "slow": [10,20,30,40,50,60]},
                          data, objective="total", min_trades=0, search_mode="random", n_rand=10, seed=42)
    assert "best" in res and res["best"] is not None, "random 模式应找到最优"
    assert res["searched"] <= 10, "random 模式采样数应受 n_rand 限制"
    assert "fast" in res["best"]["params"], "最优应含参数"


def test_register_two_stage():
    """两段式引擎注册 + 构建，指标缓存生效。"""
    from src.engine_iter import register_two_stage, clear_iter_cache

    def compute_fn(kl):
        c = kl[:, 2]  # close 在 KL_COLUMNS 第3位
        return {"close": c, "ma": pd.Series(c).rolling(5).mean().to_numpy()}

    def signal_fn(R, th=25):
        return np.where(R["close"] > R["ma"], 50, 0)

    register_two_stage("test_2s", "两段式测试", compute_fn, signal_fn,
                       params=[Param("th", int, default=25, min_value=1, max_value=50)])
    clear_iter_cache()
    eng = build_engine("test_2s", th=25)
    df = _mk_data()["sym0"]
    sig = eng(df)
    assert len(sig) == len(df), "两段式引擎输出长度不对"
    assert hasattr(eng, "compute_indicators") and hasattr(eng, "signal_from_R"), "应暴露两段式接口"


def test_promotion_ok():
    """晋级门槛校验：正收益比例+夏普+WF+交易数。"""
    from src.engine_iter import promotion_ok
    data_by_seed = {42: _mk_data(42), 7: _mk_data(7), 123: _mk_data(123)}
    r = promotion_ok("test_ma", {"fast": 5, "slow": 20}, data_by_seed, min_trades=0,
                     min_win_ratio=0, min_sharpe=-1, min_wf=-1e9)
    assert "ok" in r and "reasons" in r, "应返回 ok/reasons"
    # 放宽门槛应全部通过（test_ma 在合成数据上大概率正收益）
    assert r["avg"] is not None, "放宽门槛应有平均绩效"


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
