"""标准引擎评估工具 —— 用统一流程测任意指标引擎，避免手工作坊式出错。

回测系统缺一个标准接入接口，之前每次临时手动拼数据导致过列序/长度/出场方式
出错（曾把 K线列序喂错，把 A股引擎误测成全亏）。这个模块把易错点全部内置处理：

1. **数据归一化**：自动按 `[open, close, high, low, vol]` 列序构造 K线数组，
   传给 `compute_indicators` 类引擎用；按列名取 `df["close"]` 的引擎直接用 DataFrame。
2. **信号归一化**：自动对齐 sig 与 df 等长（短补0/长截断）。
3. **出场模式自动选择**：`signal`=波段纯信号 / `trailing`=趋势止损止盈。
4. **集成参考引擎**做对照（easy-tdx MyTT MACD + 内置 MACD），一次评估引擎 vs 参考的强弱。

典型用法：
```python
from src.benchmark import evaluate_engine
from src.benchmark import REFERENCE_ENGINES  # 2个: easy-tdx MyTT MACD + 内置MACD
result = evaluate_engine(my_engine, codes=["sh600000","sh601318"], n_sample=None,
                         exit_mode="auto")  # exit_mode=auto: 按引擎信号有无卖出信号自动判
```
"""
from __future__ import annotations

import json
import random
import time

import numpy as np
import pandas as pd

from .backtest import portfolio_performance
from .data_source import load_kline, sanity_check
from .walkforward import walk_forward
from .engines import ma_cross, macd_cross, rsi_reversal, mytt_macd, REFERENCE_ENGINES

# A股引擎(compute_indicators 系)期望的 K线列序
KL_COLUMNS = ["open", "close", "high", "low", "vol"]


def _align_sig(sig: np.ndarray, n: int) -> np.ndarray:
    """把 sig 归一化到长度 n：短→补0(无信号)，长→截断。防越界。"""
    sig = np.asarray(sig, dtype=float)
    if len(sig) < n:
        return np.pad(sig, (0, n - len(sig)), constant_values=0.0)
    if len(sig) > n:
        return sig[:n]
    return sig


def _infer_exit_mode(sig: np.ndarray, th: float) -> str:
    """按信号特征自动选出场模式：有卖出信号(<=-th)→波段signal；否则→趋势trailing。"""
    if (sig <= -th).any():
        return "signal"
    return "trailing"


def score_engine(e: dict, best: dict) -> float:
    """相对最强参考引擎打分（方案A：参考引擎固定成标杆，被测引擎可超 100 分）。

    e: 单引擎绩效 dict(total/annual/mdd/sharpe/sortino/calmar/win_rate/wf_total)
    best: 参考引擎最佳值(total/sharpe/sortino/wf/mdd 取最强者) —— 固定标杆
    权重(赚钱为主): 收益50 / 夏普15 / 回撤10 / 索提诺5 / 样本外WF20。
    上限120%防压扁参考，下限0。被测引擎某指标超参考标杆 → 该子项可 >满分，
    总分可超 100（直接体现“比参考强多少”）。返回3位小数。
    """
    def _cap(x: float) -> float:
        return max(0.0, min(1.2, x))

    total_ = 50 * _cap(e["total"] / best["total"]) if best["total"] else 0
    sharpe_ = 15 * _cap(e["sharpe"] / best["sharpe"]) if best["sharpe"] else 0
    mdd_ = 10 * _cap(best["mdd"] / e["mdd"]) if (e["mdd"] and best["mdd"]) else 0
    sortino_ = 5 * _cap(e["sortino"] / best["sortino"]) if best["sortino"] else 0
    wf_ = 20 * _cap(max(0.0, e["wf_total"]) / best["wf"]) if best["wf"] else 0
    return round(total_ + sharpe_ + mdd_ + sortino_ + wf_, 3)


def evaluate_engine(
    engine_fn,
    codes: list[str] | None = None,
    n_sample: int | None = 500,
    th: float = 25,
    tp: float = 2.5,
    be: bool = True,
    exit_mode: str = "auto",
    seed: int = 42,
    use_kl_array: bool = False,
    reference_exit_mode: str = "signal",
) -> dict:
    """用统一流程评估一个指标引擎（对比 3 个经典款）。

    engine_fn: 输入 DataFrame（或 K线数组，若 use_kl_array=True），输出 sig 数组
    codes: 指定股票池（None 则从 easy-tdx 随机抽 n_sample 只）
    th: 阈值，sig>=th 买入 / sig<=-th 卖出
    exit_mode: signal / trailing / auto（auto 自动按有无卖出信号判断）
    use_kl_array: True 表示 engine_fn 吃 `[open,close,high,low,vol]` K线数组
                  （给 compute_indicators 系引擎用），False 表示吃 DataFrame
    reference_exit_mode: 3 个经典对照引擎的出场模式（signal / trailing / long_only）。
                         默认 signal；想跟被测引擎同模式对比（如都只买不卖）可改。
    """
    # 1. 确定股票池
    if codes is None:
        import subprocess
        out = subprocess.run(
            ["/home/node/.openclaw/workspace/tools/easy_tdx_test/.venv/bin/easy-tdx",
             "quote-list", "A", "--count", "3000", "--output", "json"],
            capture_output=True, text=True, timeout=60)
        pool = []
        for item in json.loads(out.stdout):
            m, num = item["market"], item["code"]
            pool.append(("sz" if m == 0 else "sh" if m == 1 else "bj") + num)
        random.seed(seed)
        codes = random.sample(pool, n_sample or min(len(pool), 100))

    # 2. 拉数据（自研前复权 + 自检）
    data = {}
    for c in codes:
        try:
            df = load_kline(c)
            sanity_check(df, c)
            data[c] = df
        except Exception:
            continue
    if not data:
        raise ValueError("没有股票通过数据自检")

    # 3. 生成候选引擎信号
    def run_one(name, fn, em):
        sigmap = {}
        for c, df in data.items():
            if use_kl_array:
                kl = df[KL_COLUMNS].to_numpy()
                sig = _align_sig(fn(kl), len(df))
            else:
                sig = _align_sig(fn(df), len(df))
            sigmap[c] = sig
        if em == "auto":
            em = _infer_exit_mode(next(iter(sigmap.values())), th)
        m = portfolio_performance(data, sigmap, th=th, tp=tp, be=(em == "trailing"), exit_mode=em)
        wf = walk_forward(data, sigmap, th=th, tp=tp, be=(em == "trailing"), exit_mode=em)
        buy = int(sum((v >= th).sum() for v in sigmap.values()))
        sell = int(sum((v <= -th).sum() for v in sigmap.values()))
        return {
            "name": name, "exit_mode": em, "buy": buy, "sell": sell,
            "total": m.get("total_return", 0) * 100, "annual": m.get("annual_return", 0) * 100,
            "mdd": m.get("max_drawdown", 0) * 100, "sharpe": m.get("sharpe", 0),
            "sortino": m.get("sortino", 0), "calmar": m.get("calmar", 0),
            "win_rate": m.get("win_rate", 0), "trades": m.get("total_trades", 0),
            "wf_total": wf["wf_total"], "windows": wf["windows"],
        }

    t0 = time.time()
    # 被测引擎
    your = run_one("被测引擎", engine_fn, exit_mode)
    # 参考引擎对照（用 reference_exit_mode，可与被测引擎同模式）
    refs = []
    for rn, rf in REFERENCE_ENGINES.items():
        refs.append(run_one(rn, rf, reference_exit_mode))

    # 对被测引擎 + 所有参考引擎算相对基准评分(看最强)
    all_eng = [your] + refs
    # 参考引擎里的最佳值(做满分基准)
    best = {"total": max(e["total"] for e in refs), "sharpe": max(e["sharpe"] for e in refs),
            "sortino": max(e["sortino"] for e in refs), "wf": max(e["wf_total"] for e in refs),
            "mdd": min(e["mdd"] for e in refs)}
    for e in all_eng:
        e["score"] = score_engine(e, best)

    return {"n": len(data), "secs": time.time() - t0, "your": your, "reference": refs}
