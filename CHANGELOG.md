# Changelog

记录回测系统的版本变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [v1.4] - 2026-08-29

### 新增：引擎自我迭代系统（src/engine_iter.py）
借鉴 easy-tdx 的架构（register_strategy 注册表模式 + ParamGridOptimizer 思路），
打造规范化的「引擎参数化 + 多引擎对比 + 自适应搜索」系统，回测用本项目的
portfolio_performance/walk_forward（保留严格样本外WF、自研前复权优势）。

- **`register_engine` 引擎注册表**：多引擎统一登记，参数自描述（list[Param]），
  供优化器/对比引擎发现。复用 easy-tdx 的 `Param` 数据结构（未装则回退内置简化版）。
- **`build_engine(name, **params)` 参数化构建**：任意参数组合 → 引擎函数闭包。
- **`evaluate(name, params, data)` 单引擎评估**：对接回测系统，输出收益/夏普/回撤/
  泛化7窗WF + 综合评分（score = 收益 - 0.3×回撤 + 0.1×7窗WF）。
- **`optimize_engine(name, param_grid, data)` 参数网格优化**：笛卡尔积遍历，
  每组合跑回测系统评估，按目标排序（total/score/wf），`min_trades` 过滤假象（信号过稀）。
- **`multi_seed_validate(name, params, data_by_seed)` 多seed验证**：防过拟合，取平均。
- **`compare_engines(names, params_list, data)` 多引擎横向对比**：多引擎同池竞争，按评分排序。

### 验证
- 新增 `tests/test_engine_iter.py`：6 个集成测试全过（注册/构建/评估/优化/多seed/对比）。
- 回归测试 7/7、兼容测试（v1.3）5/5 通过。
- 注册 BIG-A + 内置MACD 验证：注册表、参数化、网格搜索全链路可用。
- 复用 easy-tdx Param（未装 fallback 内置简化版），不依赖其回测引擎。

### 说明
- 为下一个项目「引擎自我迭代系统」打基础：注册任意引擎 → 参数化 → 自动搜参 →
  多seed验证 → 多引擎对比，全部跑在本项目严格回测引擎上。

## [v1.3] - 2026-08-28

### 新增：全兼容引擎接口
- **引擎协议层 `src/engine_api.py`**：统一引擎接入约定，自动适配三种输入风格——DataFrame 派（`fn(df)`）、numpy 数组派（`fn(kl)`，列序=KL_COLUMNS）、带参派。`evaluate_engine` 不再需要 `use_kl_array` 开关。
- **`KL_COLUMNS` 单一真源常量**：`["open","high","low","close","vol"]`，DataFrame 与数组统一此列序，杜绝列序错位（曾把 K线列序喂错导致 A股引擎误测全亏）。
- **`detect_input` 自动探测引擎输入类型**：先试 ndarray 再试 DataFrame，数组派/DataFrame派引擎无需声明即可接入。
- **`call_engine` / `normalize_kl` / `align_sig`**：统一引擎调用与信号对齐，供评估器使用。
- **参考引擎数组/DataFrame 通吃**：内置 `mytt_macd` / `macd_cross` 等加 `_close` helper，输入自动兼容，`use_kl_array` 下不再崩。

### 修复
- **移除 `use_kl_array` 全局开关**：原接口靠一个全局开关生硬切换引擎输入类型，且 `use_kl_array=True` 时参考引擎（吃 DataFrame）崩溃。v1.3 改为协议层自动适配，彻底移除该开关。
- **`evaluate_engine` 默认抽样硬编码路径**：股票池拉取写死 `/home/node/.openclaw/...` 改 `shutil.which("easy-tdx")` 探测（v1.2.1 已修，v1.3 保留）。
- **`compute_atr` 短数据越界**：加 `n<2` guard（v1.2.1 已修，v1.3 保留）。
- **CLI `--be` 参数失效**：改 `--no-be`（v1.2.1 已修，v1.3 保留）。

### 验证
- 新增 `tests/test_engine_compat.py`：4 个兼容测试全过（detect_input 类型识别、数组/DataFrame等价、evaluate_engine 双类型跑通、第三方库引擎）。
- 回归测试 7/7 通过。
- **5 引擎默认 500 只全兼容验证**（seed=42，通过 487~488 只）：
  - BIG-A-POWER（数组派）：+540.88%，夏普 0.92，回撤 31.08%，7窗WF +89.43%，评分 118.09
  - 内置MACD金叉死叉（DataFrame派）：+288.75%，夏普 0.63，回撤 31.35%，7窗WF +36.14%，评分 100.00
  - TA-Lib MACD（数组派）：+105.15%，夏普 0.31，回撤 26.76%，7窗WF +31.31%，评分 57.27
  - pandas_ta RSI（DataFrame派）：+103.96%，夏普 0.31，回撤 32.95%，7窗WF +64.74%，评分 61.21
  - ta 双均线（DataFrame派）：+63.31%，夏普 0.16，回撤 37.35%，7窗WF +25.53%，评分 38.90
- BIG-A-POWER 结果与 README 报告一致（+538.2%/118.99 声称 vs +540.88%/118.09 实测），证明接口重构未改坏引擎成绩。

## [v1.2.1] - 2026-08-27

### 修复（代码审查）
- **WF 严格样本外（重要）**：`walk_forward` 改为每窗从窗口起点独立重新开仓回测，不再对整个区间跑一次资金曲线再切窗。修复「一笔跨窗持仓收益两个窗口都算 → WF 偏乐观」的方法论硬伤。
- **trailing 止损平仓防重复计**：新增 `test_trailing_stop_no_double_count`，锁定某日触发止损平仓后不重复走 else 持仓收益，防将来改挂。
- **tp/be 参数注明**：`single_daily_rets` / `portfolio_performance` 的 docstring 注明 tp(移动止盈)/be(保本) 仅 `exit_mode="trailing"` 生效，signal / long_only 忽略，防参数名误导。
- **compute_atr 短数据崩溃**：WF 每窗独立回测时窗口只有几根K线（n < period），ATR 冷启动段广播形状不匹配崩溃，改为边界安全填充。
- **CLI `--be` 参数失效**：`--be` 用 `action='store_true'` + `default=True`，无论是否传参 `be` 恒为 True，用户无法关闭保本开关。改为 `--no-be`（`action='store_false'` + `default=True`），缺省保持默认开，加参数则关闭。
- **`evaluate_engine` 默认抽样硬编码路径失效**：`codes=None`（默认）时股票池拉取写死 `/home/node/.openclaw/workspace/tools/easy_tdx_test/.venv/bin/easy-tdx`，该路径仅存在原作者 openclaw 容器，他处不存在导致默认 500 只直接崩。改用 `shutil.which("easy-tdx")` 探测 PATH，找不到时报清晰错误（提示安装或显式传 `codes`）。修后默认 500 只可正常跑。
- **`compute_atr` 短数据越界**：n<2（空/单根K线）时 `tr[1]` 越界 `IndexError`，v1.2.1 只修了短数据广播崩溃，n=0/1 极端边界仍在。加 `n<2` guard 返回全 1 安全值。

### 验证
- 回归测试 7/7 通过（含新增 trailing 止损测试）
- `compute_atr` n=0/1/2 不再崩
- `evaluate_engine`（不传 `codes`）默认 500 只跑通，通过 488 只，结果与手造股票池一致（MACD 金叉死叉 +288.75%）

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
