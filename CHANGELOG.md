# Changelog

记录回测系统的版本变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [v1.2.1] - 2026-08-27

### 修复（代码审查）
- **WF 严格样本外（重要）**：`walk_forward` 改为每窗从窗口起点独立重新开仓回测，不再对整个区间跑一次资金曲线再切窗。修复「一笔跨窗持仓收益两个窗口都算 → WF 偏乐观」的方法论硬伤。
- **trailing 止损平仓防重复计**：新增 `test_trailing_stop_no_double_count`，锁定某日触发止损平仓后不重复走 else 持仓收益，防将来改挂。
- **tp/be 参数注明**：`single_daily_rets` / `portfolio_performance` 的 docstring 注明 tp(移动止盈)/be(保本) 仅 `exit_mode="trailing"` 生效，signal / long_only 忽略，防参数名误导。

### 验证
- 回归测试 7/7 通过（含新增 trailing 止损测试）

## [v1.2] - 2026-08-27

### 新增
- **性能评分系统** `score_engine()`：每个引擎自动出 1 个综合评分（满分100，保留3位小数，引擎越强分越高）
  - 相对最强参考引擎做基准（看最强，超哥需求）
  - 权重（赚钱为主）：收益 50% + 夏普 15% + 回撤 10% + 索提诺 5% + 样本外WF 20%
  - 上限 120% 防压扁参考引擎，`evaluate_engine` 每个引擎带 `score`

### 变更
- **参考引擎精简为 2 个**：easy-tdx MyTT MACD + 内置 MACD 金叉死叉（原 3 个：双均线/MACD/RSI，去掉双均线和 RSI）
- **新增参考引擎** `mytt_macd`：easy-tdx MyTT 原生 MACD 实现（无 easy-tdx 时回退内置）

### 修复
- **`src/__init__.py` 缺 `mytt_macd` 导出**：`from src import mytt_macd` 会 ImportError，已补全
- **基准引擎变更同步**：README/benchmark 文档描述 3 个经典→2 个参考引擎

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
