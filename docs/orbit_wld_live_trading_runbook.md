# Orbit WLD 实盘实验操作手册

> 适用对象：WLDUSDT 1 小时数据、Orbit 72 小时预测模型、1000 RMB 左右小资金实验账户。
>
> 本文档用于把当前实验流程固定下来，方便后续按同一套规则重复执行、记录和复盘。

---

## 1. 当前实验定位

本实验不是全账户交易策略，而是一个独立的小资金验证流程。

- 总账户：约 3 万 RMB 以上。
- 实验资金：约 1000 RMB，约 150 USDT。
- 当前初始实盘动作：已买入约 89 USDT WLD，对应实验账户约 60% 仓位。
- 策略目标：验证 Orbit 72 小时预测信号是否能指导买入、加仓、减仓、清仓。
- 当前策略风格：进攻型小资金实验，允许较大波动，但不扩大到主账户资金。

实验仓位统一按“实验资金”计算，不按总账户计算。

```text
实验仓位 = 当前 WLD 市值 / 实验资金总额
```

例如实验资金约 150 USDT：

```text
89 USDT / 150 USDT ≈ 0.60 = 60% 仓位
```

---

## 2. 固定模型参数

当前主线模型参数如下：

```text
symbol        = WLDUSDT
interval      = 1h
horizon       = 72
max_rows      = 6000
seasonality   = 168
estimator     = stan-map
risk_threshold = -0.06
```

含义：

```text
模型预测未来 72 小时收益率；
risk_prob_return_le_threshold 表示未来 72 小时跌超 6% 的概率。
```

---

## 3. 当前实盘仓位规则

当前采用“进攻版 1000 RMB 实验规则”。

### 3.1 买入 / 加仓规则

| 条件 | 目标仓位 | 说明 |
|---|---:|---|
| `predicted_simple_return >= 0.06` 且 `risk_prob <= 0.30` | 100% | 强做多，买满实验资金 |
| `predicted_simple_return >= 0.03` 且 `risk_prob <= 0.35` | 60% | 普通做多，持有基础仓位 |

### 3.2 减仓 / 清仓规则

| 条件 | 目标仓位 | 说明 |
|---|---:|---|
| `predicted_simple_return < 0.01` 或 `risk_prob >= 0.60` | 30% | 信号转弱，降仓观察 |
| `predicted_simple_return <= -0.01` 或 `risk_prob >= 0.80` | 0% | 清仓 |

### 3.3 当前操作状态

当前已买入约 89 USDT WLD，按 150 USDT 实验资金计算为 60% 仓位。

因此下一次信号处理规则是：

| 最新信号 | 操作 |
|---|---|
| `strong_long` / `increase` | 加仓到 100%，再买约 60 USDT WLD |
| `long` / `hold` | 继续持有约 89 USDT WLD |
| `reduced` / `reduce` | 减到 30%，卖出约一半，保留约 45 USDT WLD |
| `flat` / `clear_by_risk` / `clear_by_prediction` | 清仓 |

---

## 4. 每次检查信号的标准流程

每次检查信号时，先更新数据，再确认最新时间，最后跑实时信号。

### 4.1 更新 WLD 1 小时 K 线

```bash
python collectors/get_wld_data.py \
  -s WLDUSDT \
  -i 1h \
  -n 20000 \
  -o data/WLDUSDT_1h.csv \
  --no-trim
```

### 4.2 确认数据最新时间

```bash
python - <<'PY'
import pandas as pd

df = pd.read_csv("data/WLDUSDT_1h.csv")
df["date"] = pd.to_datetime(df["date"])
print("rows:", len(df))
print("latest:", df["date"].max())
print(df.tail(5)[["date", "open", "high", "low", "close"]])
PY
```

注意：`date` 是 UTC 时间。正常情况下，最新 K 线应只比当前时间落后 1 到 2 小时左右。若数据明显滞后，不要按信号操作。

### 4.3 计算当前实验仓位

如果实验资金按 150 USDT 计算：

```text
current_position = 当前 WLD 市值 / 150
```

示例：

```text
当前 WLD 市值约 89 USDT
current_position = 89 / 150 ≈ 0.60
```

### 4.4 跑实时信号

当前已持有 60% 仓位时，用下面命令：

```bash
python analysis/orbit_live_signal.py \
  --input data/WLDUSDT_1h.csv \
  --horizon 72 \
  --max-rows 6000 \
  --seasonality 168 \
  --estimator stan-map \
  --risk-threshold -0.06 \
  --prediction-rows 1 \
  --current-position 0.60 \
  --long-threshold 0.03 \
  --buy-risk-max 0.35 \
  --strong-long-threshold 0.06 \
  --strong-risk-max 0.30 \
  --reduce-pred-threshold 0.01 \
  --reduce-risk-threshold 0.60 \
  --clear-pred-threshold -0.01 \
  --clear-risk-threshold 0.80 \
  --reduced-position 0.30 \
  --base-position 0.60 \
  --max-position 1.00
```

如果已清仓，则改为：

```bash
--current-position 0.0
```

如果已经满仓实验资金，则改为：

```bash
--current-position 1.0
```

---

## 5. 实时信号输出怎么看

重点看输出中的这几项：

```text
predicted_simple_return_72h
risk_prob_return_le_-6.00%
signal
action
current_position
target_position
trade_delta
```

解释：

| 字段 | 含义 |
|---|---|
| `predicted_simple_return_72h` | 模型预测未来 72 小时普通收益率 |
| `risk_prob_return_le_-6.00%` | 模型估计未来 72 小时跌超 6% 的概率 |
| `signal` | 模型仓位信号 |
| `action` | 根据当前仓位和目标仓位得出的操作 |
| `target_position` | 目标仓位 |
| `trade_delta` | 需要买入或卖出的仓位差 |

操作解释：

| 输出 | 操作 |
|---|---|
| `signal: strong_long`, `action: increase` | 加仓到 100% 实验仓位 |
| `signal: long`, `action: hold` | 继续持有当前 60% 仓位 |
| `signal: reduced`, `action: reduce` | 减仓到 30% |
| `signal: flat` | 空仓或保持空仓 |
| `signal: clear_by_risk` | 风险过高，清仓 |
| `signal: clear_by_prediction` | 预测转弱，清仓 |

---

## 6. 硬止损与止盈规则

模型信号是主规则，但实盘实验要叠加硬止损和止盈。

### 6.1 硬止损

以买入均价为基准：

```text
浮亏 -8%：卖出一半。
浮亏 -12%：全部卖出。
```

如果当前仓位是 89 USDT：

```text
-8% 约亏 7.1 USDT。
-12% 约亏 10.7 USDT。
```

硬止损优先级高于模型信号。也就是说，即使模型没有清仓，只要硬止损触发，也执行止损。

### 6.2 止盈

以买入均价为基准：

```text
盈利 +15%：卖出约 30 USDT。
盈利 +25%：再卖出约 30 USDT。
剩余仓位继续按模型信号处理。
```

---

## 7. 检查频率

建议频率：

```text
正常行情：每天 2 次，上午 / 晚上。
剧烈波动：每 4 小时检查一次。
不要按 5 分钟级别频繁刷新。
```

原因：当前模型是 72 小时预测模型，不是分钟级交易模型。

---

## 8. 每次交易必须记录

每次买入、加仓、减仓、清仓，都记录以下字段：

```text
操作时间：
操作类型：买入 / 加仓 / 减仓 / 清仓
WLD 成交价格：
WLD 成交数量：
成交金额 USDT：
手续费：
操作后 WLD 市值：
操作后实验仓位：
模型预测收益 predicted_simple_return_72h：
模型风险概率 risk_prob：
signal：
action：
备注：
```

当前第一笔记录模板：

```text
操作时间：待补充
操作类型：买入
成交金额：89 USDT
实验资金：约 150 USDT
操作后仓位：约 60%
买入价格：待补充
WLD 数量：待补充
```

---

## 9. 回测复核命令

如果要复核当前进攻版规则的历史表现，用下面命令：

```bash
python analysis/orbit_position_backtest.py \
  --predictions reports/orbit/WLDUSDT_1h_orbit_h72_20260621_220610_predictions.csv \
  --klines data/WLDUSDT_1h.csv \
  --fee-rate 0.001 \
  --slippage-rate 0.0005 \
  --long-threshold 0.03 \
  --buy-risk-max 0.35 \
  --strong-long-threshold 0.06 \
  --strong-risk-max 0.30 \
  --reduce-pred-threshold 0.01 \
  --reduce-risk-threshold 0.60 \
  --clear-pred-threshold -0.01 \
  --clear-risk-threshold 0.80 \
  --reduced-position 0.30 \
  --base-position 0.60 \
  --max-position 1.00 \
  --min-trade-delta 0.10
```

当前已知该进攻版回测结果：

```text
测试区间：2026-05-26 17:00:00 -> 2026-06-15 16:00:00
策略收益：65.30%
买入持有收益：43.76%
最大回撤：-18.51%
交易次数：41
总换手：17.70
总成本：2.66%
在场时间：68.75%
平均仓位：64.77%
```

解释：

```text
该规则在回测区间内跑赢 buy-and-hold，但最大回撤接近 20%。
因此适合作为 1000 RMB 小资金实验规则，不适合直接扩大到主账户。
```

---

## 10. 当前下一步

当前状态：

```text
已持仓约 89 USDT WLD，约 60% 实验仓位。
最新模型信号：long / hold。
目标仓位：60%。
```

当前动作：

```text
继续持有，不加仓，不卖出。
```

下一次检查：

```text
先更新 K 线，再用 current_position=0.60 跑实时信号。
如果 signal 变成 strong_long，再考虑加仓到 100%。
如果 signal 变成 reduced，则减仓到 30%。
如果 clear_by_risk 或 clear_by_prediction，则清仓。
```
