#!/usr/bin/env python3
"""引擎自我迭代系统（v1.3+）—— 多引擎注册 + 参数化 + 自适应搜索。

借鉴 easy-tdx 架构（register_strategy 注册表模式 + ParamGridOptimizer 思路），
但回测跑在本项目的 portfolio_performance/walk_forward 上（保留严格样本外WF、
自研前复权优势）。

设计：
- 复用 easy_tdx.backtest.strategies.registry.Param / optimizer.GridPointResult /
  OptimizeResult（纯数据结构，零依赖 easy-tdx 回测引擎）
- ENGINE_REGISTRY：全局引擎注册表，`register_engine(name, label, fn, params)` 登记
  fn(df)->sig 的引擎 + 参数 schema（list[Param]）
- @register_engine 装饰器：像 easy-tdx 一样声明参数自描述
- optimize_engine(name, param_grid, ...)：网格搜索，每组合跑回测系统评估，
  按 total_return 排序，返回 OptimizeResult
- multi_seed_validate(name, params, seeds)：多 seed 验证防过拟合

接口约定：引擎函数 `fn(df: DataFrame) -> np.ndarray(sig)`（DataFrame派），
供 evaluate_engine / portfolio_performance 直接调用。

ponytail: 复用 easy-tdx 数据结构，不重写；只写对接本项目回测引擎的部分。
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from .backtest import portfolio_performance
from .walkforward import walk_forward
from .data_source import sanity_check
from .engine_api import align_sig

# 复用 easy-tdx 数据结构（纯数据，零依赖其回测引擎）
try:
    from easy_tdx.backtest.strategies.registry import Param  # noqa: F401
    from easy_tdx.backtest.optimizer import OptimizeResult, GridPointResult  # noqa: F401
    _HAS_EASY = True
except ImportError:
    # easy-tdx 未装时回退为本项目最小 Param（保持接口一致）
    from dataclasses import dataclass as _dc
    @_dc(frozen=True)
    class Param:
        name: str
        type: type
        default: Any = None
        min_value: float | None = None
        max_value: float | None = None
        choices: tuple[str, ...] | None = None
        label: str = ""
        description: str = ""
    GridPointResult = None
    OptimizeResult = None
    _HAS_EASY = False


# ── 引擎注册表 ──────────────────────────────────────────────────────────────
@dataclass
class RegisteredEngine:
    name: str
    label: str
    fn: Callable[[pd.DataFrame], np.ndarray]
    params: list[Param]
    description: str = ""
    _two_stage: tuple | None = None  # 两段式引擎: (compute_fn, signal_fn)


_ENGINE_REGISTRY: dict[str, RegisteredEngine] = {}


def register_engine(
    name: str,
    label: str,
    fn: Callable[[pd.DataFrame], np.ndarray] | None = None,
    params: list[Param] | None = None,
    description: str = "",
) -> Callable:
    """登记一个引擎进全局注册表。支持两种用法：
    1. register_engine(name, label, fn, params=[...])  直接注册
    2. @register_engine(name, label, params=[...])     装饰器（装饰 def fn）
    """
    def _do_register(_fn):
        _ENGINE_REGISTRY[name] = RegisteredEngine(
            name=name, label=label, fn=_fn,
            params=params or [], description=description,
        )
        return _fn

    # 直接传入 fn → 立即注册并返回 fn；否则返回装饰器(等 fn 传入)
    if fn is not None:
        return _do_register(fn)
    return _do_register


def get_registry() -> dict[str, RegisteredEngine]:
    return _ENGINE_REGISTRY


# ── 两段式引擎注册（指标缓存，治迭代慢） ───────────────────────────────────
def register_two_stage(name: str, label: str, compute_fn, signal_fn,
                       params: list[Param] | None = None,
                       description: str = "") -> None:
    """注册一个「两段式」引擎（借鉴 tick-stock-panel 指标解耦）。

    compute_fn(kl) -> R   纯指标（不依赖搜索参数），算一次缓存
    signal_fn(R, **params) -> sig   依赖参数（如 min_abs/sell_th），毫秒级

    两段式让 optimize_engine 反复迭代时**指标只算一次**，只重算毫秒级的
    信号组合层 → 迭代从『每次重算全套指标』降到『毫秒级』。
    """
    _ENGINE_REGISTRY[name] = RegisteredEngine(
        name=name, label=label, fn=None, params=params or [], description=description,
    )
    # 用特殊标记存两段式组件（fn 存一个包装标记，供 build_engine 识别）
    _ENGINE_REGISTRY[name]._two_stage = (compute_fn, signal_fn)


def _is_two_stage(entry: "RegisteredEngine") -> bool:
    return getattr(entry, "_two_stage", None) is not None


# ── 参数化 + 引擎构建 ──────────────────────────────────────────────────────
def build_engine(name: str, skip_bounds: bool = False, **params) -> Callable[[pd.DataFrame], np.ndarray]:
    """按参数构建一个引擎函数（闭包捕获参数）。

    skip_bounds: True 时跳过单参数范围检查（供优化器探索超范围值，学 easy-tdx）。
    两段式引擎 → 返回带 .compute_indicators/.signal_from_R 的加速引擎。
    """
    entry = _ENGINE_REGISTRY[name]
    resolved = {}
    for p in entry.params:
        raw = params.get(p.name, p.default)
        if _HAS_EASY:
            resolved[p.name] = p.validate(raw, skip_bounds=skip_bounds)
        else:
            resolved[p.name] = raw
    # 两段式：指标缓存 + 信号毫秒级
    if _is_two_stage(entry):
        compute_fn, signal_fn = entry._two_stage

        def _engine2(df: pd.DataFrame) -> np.ndarray:
            kl = df[["open", "high", "low", "close", "vol"]].to_numpy()
            R = _cached(compute_fn, kl)
            return np.asarray(signal_fn(R, **resolved), dtype=float)

        _engine2.compute_indicators = staticmethod(lambda kl: _cached(compute_fn, kl))
        _engine2.signal_from_R = staticmethod(lambda R, **p: signal_fn(R, **{**resolved, **p}))
        return _engine2
    fn = entry.fn

    def _engine(df: pd.DataFrame) -> np.ndarray:
        return fn(df, **resolved)

    return _engine


# 指标缓存（两段式引擎用，内存）
_ICACHE: dict = {}


def _cached(compute_fn, kl, max_entries=2000):
    import hashlib
    # 缓存键 = compute_fn标识(id区分不同引擎, 防同名不同算法冲突) + K线哈希
    key = (id(compute_fn), hashlib.sha1(np.ascontiguousarray(kl, dtype=np.float64).tobytes()).hexdigest())
    if key in _ICACHE:
        return _ICACHE[key]
    if len(_ICACHE) > max_entries:
        _ICACHE.clear()
    R = compute_fn(kl)
    _ICACHE[key] = R
    return R


def clear_iter_cache():
    _ICACHE.clear()


# ── 单次评估（对接本项目回测系统） ────────────────────────────────────────
def evaluate(name: str, params: dict[str, Any], data: dict[str, pd.DataFrame],
             th: float = 25, tp: float = 2.5, be: bool = True,
             exit_mode: str = "signal", skip_bounds: bool = False,
             min_trades: int = 0) -> dict[str, Any] | None:
    """用回测系统评估一个引擎参数的绩效。返回 dict（含评分用字段）。

    score = 综合评分（收益为主 + 泛化/夏普加成），供多目标优化。
    skip_bounds: 构建引擎时跳过参数范围检查（供优化器探索超范围值）。
    min_trades: 交易次数低于此值视为假象(信号过稀)，返回 None（供上层过滤）。
    """
    engine = build_engine(name, skip_bounds=skip_bounds, **params)
    sig = {}
    for c, df in list(data.items()):
        try:
            sanity_check(df, c)  # 数据自检：残留除权假跳变等脏数据直接剔除
            sig[c] = align_sig(engine(df), len(df))  # 对齐信号长度(防越界)
        except AssertionError:
            continue  # 脏数据(残留除权跳变)剔除，不污染组合
    if not sig:
        return None
    m = portfolio_performance(data, sig, th=th, tp=tp, be=be, exit_mode=exit_mode)
    wf = walk_forward(data, sig, th=th, tp=tp, be=be, exit_mode=exit_mode)
    total = m.get("total_return", 0) * 100
    sharpe = m.get("sharpe", 0)
    mdd = m.get("max_drawdown", 0) * 100
    wf_total = wf["wf_total"]
    trades = m.get("total_trades", 0)
    if trades < min_trades:
        return None  # 假象（信号过稀），上层过滤
    # 综合评分：收益为主，惩罚回撤，奖励泛化（量纲统一：都是百分数）
    # score = 收益 - 0.3×回撤 + 0.1×7窗WF   （收益越高分越高，回撤越小分越高，泛化强加分）
    score = total - 0.3 * mdd + 0.1 * max(0.0, wf_total)
    return {
        "total": total, "sharpe": sharpe, "mdd": mdd,
        "sortino": m.get("sortino", 0), "win_rate": m.get("win_rate", 0),
        "trades": trades, "wf_total": wf_total,
        "score": score, "params": params,
    }


# ── 参数网格优化器（对接本项目回测引擎） ──────────────────────────────────
MAX_GRID_POINTS = 500


def optimize_engine(name: str, param_grid: dict[str, list[Any]],
                    data: dict[str, pd.DataFrame],
                    objective: str = "total", th: float = 25,
                    exit_mode: str = "signal", min_trades: int = 5000,
                    skip_bounds: bool = True, search_mode: str = "grid",
                    n_rand: int = 100, seed: int = 42,
                    ) -> dict[str, Any]:
    """搜索引擎参数，按 objective 排序。

    objective: "total"(收益) / "score"(综合评分, evaluate里的score) / "wf"(泛化)
    min_trades: 交易次数低于此值的组合视为假象(信号过稀)跳过。
    skip_bounds: 寻优时是否跳过参数范围检查(默认True，探索超范围值，学easy-tdx)。
    search_mode: "grid"(默认,笛卡尔积全遍历) / "random"(随机采样n_rand个,防大空间爆炸)
    n_rand/seed: random 模式的采样数/随机种子。
    返回 {best, results, secs}。
    """
    names = list(param_grid.keys())
    value_lists = [param_grid[n] for n in names]
    size = 1
    for vals in value_lists:
        size *= len(vals)
    if size > MAX_GRID_POINTS and search_mode == "grid":
        raise ValueError(f"网格大小 {size} 超过上限 {MAX_GRID_POINTS}，"
                         f"请改用 search_mode='random' 或减少参数取值")

    # 生成要搜索的组合列表
    if search_mode == "random":
        rng = np.random.default_rng(seed)
        combos = []
        total = min(n_rand, size)  # 随机采样，不重复
        seen = set()
        attempts = 0
        while len(combos) < total and attempts < total * 50:
            idx = tuple(rng.integers(0, len(vl)) for vl in value_lists)
            attempts += 1
            if idx in seen:
                continue
            seen.add(idx)
            combos.append(idx)
    else:
        combos = list(itertools.product(*[range(len(vl)) for vl in value_lists]))

    results = []
    t0 = time.time()
    for idx in combos:
        params = {name: value_lists[i][idx[i]] for i, name in enumerate(names)}
        try:
            r = evaluate(name, params, data, th=th, exit_mode=exit_mode,
                         skip_bounds=skip_bounds)
        except Exception:
            continue
        if r is None:   # evaluate 返回 None(全部股票被自检剔除/交易过稀)
            continue
        if r["trades"] < min_trades:
            continue  # 假象（信号过稀）跳过
        if objective == "score":
            score = r["score"]   # 用 evaluate 里的综合评分
        elif objective == "wf":
            score = r["wf_total"]
        else:
            score = r["total"]
        results.append({"params": params, **r, "__score": score})

    results.sort(key=lambda r: r["__score"], reverse=True)
    best = results[0] if results else None
    return {"best": best, "results": results, "secs": time.time() - t0, "searched": len(combos)}


# ── 多 seed 验证 ──────────────────────────────────────────────────────────
def multi_seed_validate(name: str, params: dict[str, Any],
                        data_by_seed: dict[int, dict[str, pd.DataFrame]],
                        th: float = 25, exit_mode: str = "signal",
                        min_trades: int = 5000) -> dict[str, Any]:
    """多 seed 验证参数稳健性。返回各 seed 绩效 + 平均。"""
    per_seed = {}
    for seed, data in data_by_seed.items():
        try:
            r = evaluate(name, params, data, th=th, exit_mode=exit_mode)
            per_seed[seed] = r
        except Exception:
            per_seed[seed] = None
    valid = {s: r for s, r in per_seed.items() if r and r["trades"] >= min_trades}
    if not valid:
        return {"per_seed": per_seed, "avg": None}
    keys = ["total", "sharpe", "mdd", "wf_total", "score"]
    avg = {k: float(np.mean([v[k] for v in valid.values()])) for k in keys}
    avg["trades"] = int(np.mean([v["trades"] for v in valid.values()]))
    avg["seeds"] = sorted(valid.keys())
    return {"per_seed": per_seed, "avg": avg}


# ── 多引擎横向对比 ────────────────────────────────────────────────────────
def promotion_ok(name: str, params: dict[str, Any], data_by_seed: dict[int, dict[str, pd.DataFrame]],
                 th: float = 25, exit_mode: str = "signal", min_trades: int = 5000,
                 min_win_ratio: float = 2 / 3, min_sharpe: float = 0.5,
                 min_wf: float = 0.0) -> dict[str, Any]:
    """晋级门槛校验（借鉴 tick-stock-panel mining 的 publish 门槛，防过拟合）。

    只有同时满足以下才标「达标」(可固化)，防止只选最高分而过拟合：
      - 正收益 seed 比例 ≥ min_win_ratio（跨 seed 都要稳）
      - 平均 Sharpe ≥ min_sharpe
      - 7窗WF > min_wf（泛化为正）
      - 每个 seed 交易数 ≥ min_trades（非假象）
    返回 {ok, reasons(未达标原因), avg}
    """
    v = multi_seed_validate(name, params, data_by_seed, th=th, exit_mode=exit_mode, min_trades=min_trades)
    avg = v["avg"]
    reasons = []
    if avg is None:
        return {"ok": False, "reasons": ["所有seed无效/假象"], "avg": None}
    seeds = avg["seeds"]
    # 正收益 seed 比例
    pos_seeds = [s for s in seeds if v["per_seed"].get(s) and v["per_seed"][s].get("total", 0) > 0]
    if len(pos_seeds) / len(seeds) < min_win_ratio:
        reasons.append(f"正收益seed比例{len(pos_seeds)}/{len(seeds)} < {min_win_ratio:.2f}")
    if avg["sharpe"] < min_sharpe:
        reasons.append(f"平均夏普{avg['sharpe']:.2f} < {min_sharpe}")
    if avg["wf_total"] <= min_wf:
        reasons.append(f"7窗WF{avg['wf_total']:+.1f}% <= {min_wf}")
    ok = not reasons
    return {"ok": ok, "reasons": reasons, "avg": avg}


# ── 多引擎横向对比 ────────────────────────────────────────────────────────
def _auto_register_builtin(name: str) -> None:
    """若 name 是内置参考引擎（REFERENCE_ENGINES），自动注册进本系统注册表。"""
    if name in _ENGINE_REGISTRY:
        return
    try:
        from .engines import REFERENCE_ENGINES
        if name in REFERENCE_ENGINES:
            fn = REFERENCE_ENGINES[name]
            _ENGINE_REGISTRY[name] = RegisteredEngine(
                name=name, label=name, fn=fn, params=[], description="内置参考引擎",
            )
    except Exception:
        pass


def compare_engines(names: list[str], params_list: list[dict[str, Any]],
                    data: dict[str, pd.DataFrame], th: float = 25,
                    exit_mode: str = "signal", min_trades: int = 5000,
                    ) -> list[dict[str, Any]]:
    """多引擎横向对比：各自评估，按 score 排序。

    names: 引擎名列表（须已在注册表，或为内置参考引擎名[自动注册]）。
    params_list: 与 names 对应的参数 dict 列表。
    返回按 score 降序的列表，每项含引擎名/参数/收益/回撤/泛化/评分。
    """
    rows = []
    for name, params in zip(names, params_list):
        try:
            _auto_register_builtin(name)  # 内置引擎自动纳入
            r = evaluate(name, params, data, th=th, exit_mode=exit_mode)
            if r is None:
                continue  # evaluate 返回 None(全部股票被剔)
            if r["trades"] < min_trades:
                continue  # 假象跳过
            r["engine"] = name
            rows.append(r)
        except Exception:
            continue
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows
