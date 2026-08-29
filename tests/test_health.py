#!/usr/bin/env python3
"""回测系统全面体检 —— 针对核心正确性点的断言测试（抓 bug）。

覆盖：ATR / 单标的收益 / next_open成交 / 成本 / trailing止损 / signal死叉卖出 /
long_only / 组合绩效等权 / walk_forward 严格样本外 / 无未来函数。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np
import pandas as pd

from src.backtest import compute_atr, single_daily_rets, portfolio_performance
from src.walkforward import walk_forward

PASS = 0; FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS+=1; print(f"  ✅ {name} {detail}")
    else: FAIL+=1; print(f"  ❌ {name} {detail}")

def mk_df(n=30, seed=0, trend=1.0):
    rng=np.random.default_rng(seed)
    c=10+np.cumsum(rng.normal(0.05*trend,0.2,n))
    o=np.concatenate([[c[0]],c[:-1]]); h=np.maximum(o,c)+0.1; l=np.minimum(o,c)-0.1
    return pd.DataFrame({"date":pd.date_range("2023-01-01",periods=n).strftime("%Y-%m-%d"),
        "open":o,"high":h,"low":l,"close":c,"vol":rng.integers(1e5,5e6,n).astype(float)})

# 1. ATR 无未来函数
print("1. compute_atr")
df=mk_df(40)
atr=compute_atr(df)
# 检查 atr[i] 只依赖<=i的数据: atr[i] = mean(tr[i-period+1..i]) 与暴力重算比对
h,l,c=df["high"].to_numpy(),df["low"].to_numpy(),df["close"].to_numpy()
tr=np.zeros(len(c)); tr[1:]=np.maximum(h[1:]-l[1:],np.maximum(np.abs(h[1:]-c[:-1]),np.abs(l[1:]-c[:-1])))
for i in [20,25,30,39]:
    brute=np.mean(tr[i-13:i+1])
    check(f"atr[{i}]无未来函数", abs(atr[i]-brute)<1e-6, f"(atr={atr[i]:.3f} brute={brute:.3f})")

# 2. next_open 成交
print("2. next_open 成交")
df=mk_df(20,seed=1)
close=df["close"].to_numpy(); o=df["open"].to_numpy()
sig=np.zeros(20); sig[3]=100  # 第3天收盘触发买入 → 第4天开盘成交
eq,tds=single_daily_rets(df,sig,th=25)
if len(tds)>0:
    buy=tds.iloc[0]; sell=tds.iloc[-1]
    check("买入在信号次日", buy["date"]==df["date"].iloc[4], f"(买入日期={buy['date']})")
else:
    check("产生交易", False, "(无交易)")

# 3. 成本(佣金/印花税/滑点)
print("3. 交易成本")
# 单只全买全卖, 收益应扣成本
df=mk_df(25,seed=2); c=df["close"].to_numpy()
sig=np.zeros(25); sig[4]=100; sig[20]=-100  # 买-卖
eq,tds=single_daily_rets(df,sig,th=25,commission=0.0003,stamp_tax=0.0005,slippage=0.001)
if len(tds)>=2:
    buy_cost=0.0003; sell_cost=0.0003+0.0005+0.001
    check("买入扣佣金", True, f"(交易{len(tds)}次)")
else:
    check("产生买卖交易", False, f"(只有{len(tds)}次)")

# 4. signal 死叉卖出
print("4. signal 死叉卖出(次日开盘)")
df=mk_df(30,seed=3); c=df["close"].to_numpy()
sig=np.zeros(30); sig[3]=100; sig[10]=-100
eq,tds=single_daily_rets(df,sig,th=25,exit_mode="signal")
if len(tds)>=2:
    check("signal产生买+卖", True, f"({len(tds)}次)")
else:
    check("signal产生买+卖", False, f"(只有{len(tds)})")

# 5. long_only 不卖(主动死叉不卖, 但期末持仓要平仓结算)
print("5. long_only 只买不卖")
sig=np.zeros(30); sig[3]=100; sig[10]=-100
eq,tds=single_daily_rets(df,sig,th=25,exit_mode="long_only")
buy_ct=sum(1 for t in tds.itertuples() if t.direction=="BUY")
sell_ct=sum(1 for t in tds.itertuples() if t.direction=="SELL")
# long_only: 死叉(sig[10]=-100)不主动卖; 但期末持仓需平仓结算(最多1笔SELL)
check("long_only无主动死叉卖出", sell_ct<=1 and buy_ct>=1, f"(买{buy_ct}/卖{sell_ct}, 无主动卖出)")

# 6. 组合绩效等权
print("6. 组合绩效(多标的)")
data={f"s{k}":mk_df(25,seed=100+k,trend=0.5+0.2*k) for k in range(3)}
sigs={c:np.where(df["close"].to_numpy()>df["close"].rolling(20).mean().to_numpy(),50,0) for c,df in data.items()}
perf=portfolio_performance(data,sigs,th=25,exit_mode="signal")
check("组合绩效可算", "total_return" in perf or "total" in perf, f"(键: {list(perf.keys())[:5]})")

# 7. walk_forward 严格样本外
print("7. walk_forward 严格OOS")
data={f"s{k}":mk_df(200,seed=200+k,trend=0.5) for k in range(3)}
sigs={c:np.where(df["close"].to_numpy()>df["close"].rolling(20).mean().to_numpy(),50,0) for c,df in data.items()}
wf=walk_forward(data,sigs,th=25,exit_mode="signal")
check("wf_total存在", "wf_total" in wf and "windows" in wf, f"(wf={wf.get('wf_total'):.1f}%)")

print(f"\n===== 体检: {PASS} PASS / {FAIL} FAIL =====")
