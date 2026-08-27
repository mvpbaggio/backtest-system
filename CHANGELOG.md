# Changelog

记录回测系统的版本变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [v1.1] - 2026-08-27

### 新增
- **标准引擎评估接口** `src/benchmark.py:evaluate_engine()`：测引擎的标准入口——自动拉数据/归一化列序/对齐信号长度/选出场模式/对比3个经典引擎一次跑完
- **内置参考引擎** `src/engines.py`：双均线 `ma_cross` / MACD `macd_cross` / RSI 超买超卖 `rsi_reversal`，作为新引擎对比基准
- **双向信号支持**：买入 `sig>=th`、卖出 `sig<=-th`（金叉买/死叉卖），波段引擎可主动平仓
- **`exit_mode` 出场模式**：`signal`（默认，有买有卖）/ `long_only`（可选，只买不卖）/ `trailing`（趋势引擎吊灯ATR+BE+TP止损止盈）
- **`reference_exit_mode`**：`evaluate_engine` 里 3 个经典对照引擎的出场模式（默认 signal），可与被测引擎同模式对比
- **默认 500 只随机样本**：`evaluate_engine` 的 `n_sample` 默认 500
- **缓存 7 天自动更新**：`load_kline` 加 `max_age_days=7`，缓存超 7 天自动重拉
- **`long_only` 模式进 CLI**：`run_backtest --exit-mode` 增加 `long_only`

### 修复
- **sig 数组越界崩溃**：`single_daily_rets` 直接 `sig[i]` 访问，若信号比 df 短会 IndexError。改为函数开头归一化 sig 到与 df 等长
- **CLI `--exit-mode` 缺 `long_only`**：底层已支持，CLI choices 补全
- **evaluate_engine 去硬编码**：删除 `_th = 15 if name == "你的A股引擎"` 特殊分支，阈值统一由参数控制

### 优化
- **compute_atr 向量化**：逐日 Python 循环 → cumsum 前缀向量化
- **walkforward 预计算日收益**：避免 7 窗重复跑完整资金曲线

## [v1.0] - 2026-08-27

### 核心回测引擎
- **无未来函数**：ATR 用 cumsum 纯历史滚动（修复 `np.convolve(mode="same")` 中心对齐偷看未来 6 天的致命 bug）；成交 next_open 次日开盘
- **真实费率**：佣金双边 + 印花税卖出 + 滑点；期末平仓只扣卖出成本；止损跳空按更差开盘价成交
- **自研前复权数据层**：easy-tdx 拉 NONE 原始价 + 向下跳空检测前复权（板块感知阈值：主板10%/双创20%/北交所30%），消除除权假跳变；本地缓存
- **严格 7 窗样本外 WF**：后70%切7段，看引擎真实泛化
- **多标的等权组合**：资金池流动，绩效用 easy-tdx PerformanceAnalyzer 出 19 项

### 修复
- **期末平仓双重计收益**：持仓收益已逐日计入，期末又乘一遍导致虚高 → 只扣卖出成本
- **止损跳空保守成交**：跳空低开按更差开盘价
- **数据层板块感知阈值**：修复固定13%误拦双创/北交所导致样本偏向主板
- **walkforward `__main__` 导入**：非相对导入失败，改 `.data_source`

### 已知
- A股引擎（gate_trmo）经 ATR 修复后实际泛化能力有限（7窗WF 约 +1%），原策略结论受未来函数污染
- 原脚本 `/tmp/bt` 的矩阵引擎同样带 ATR 未来函数，其历史结论（如 +411%/+1553%）应打折扣
