# WLDUSDT 手工交易记录 JSON 规范

> 适用对象：WLDUSDT Orbit 实盘实验。  
> 目的：用本地 JSON 文件记录每一次真实交易、对应模型信号、仓位变化和复盘备注，方便后续做页面维护、统计收益、检查执行纪律。

---

## 1. 文件位置

真实交易记录建议放在本地：

```text
data/manual/wldusdt_manual_trades.json
```

注意：仓库 `.gitignore` 已忽略 `data/` 目录，所以真实交易流水默认不会提交到 GitHub。

示例模板放在：

```text
examples/wldusdt_manual_trades.example.json
```

---

## 2. 总体结构

建议使用一个对象维护完整实验状态：

```json
{
  "meta": {},
  "current_state": {},
  "trades": [],
  "snapshots": []
}
```

含义：

| 字段 | 含义 |
|---|---|
| `meta` | 实验基础信息，例如标的、实验资金、时区、模型参数 |
| `current_state` | 当前持仓状态，方便页面直接读取展示 |
| `trades` | 每一笔真实买卖记录 |
| `snapshots` | 每次跑模型信号后的状态快照，可选但建议记录 |

---

## 3. `meta` 字段

```json
{
  "symbol": "WLDUSDT",
  "timezone": "Asia/Shanghai",
  "experiment_capital_usdt": 150.0,
  "model": {
    "interval": "1h",
    "horizon_hours": 72,
    "max_rows": 6000,
    "seasonality": 168,
    "estimator": "stan-map",
    "risk_threshold": -0.06
  },
  "rules": {
    "base_position": 0.60,
    "max_position": 1.00,
    "reduced_position": 0.30,
    "long_threshold": 0.03,
    "buy_risk_max": 0.35,
    "strong_long_threshold": 0.06,
    "strong_risk_max": 0.30,
    "reduce_pred_threshold": 0.01,
    "reduce_risk_threshold": 0.60,
    "clear_pred_threshold": -0.01,
    "clear_risk_threshold": 0.80
  }
}
```

---

## 4. `current_state` 字段

页面维护时，最常读取这一块。

```json
{
  "as_of_bj": "2026-06-22 09:40:00",
  "base_qty_wld": 141.35,
  "cash_usdt": 60.11,
  "avg_cost_usdt": null,
  "last_price_usdt": 0.6387,
  "position_pct": 0.60,
  "notes": "从满仓降回 60% 基础仓位"
}
```

说明：

| 字段 | 含义 |
|---|---|
| `base_qty_wld` | 当前剩余 WLD 数量 |
| `cash_usdt` | 已回收或可用的 USDT |
| `avg_cost_usdt` | 当前剩余仓位的平均成本，后续可由脚本自动计算 |
| `last_price_usdt` | 最近一次记录时的价格 |
| `position_pct` | 当前实验仓位，例如 0.60 表示 60% |
| `notes` | 人工备注 |

---

## 5. `trades` 字段

每次真实交易追加一条，不覆盖历史。

```json
{
  "id": "20260622-sell-001",
  "trade_time_bj": "2026-06-22 09:32:00",
  "symbol": "WLDUSDT",
  "side": "SELL",
  "base_qty": 94.0,
  "price": 0.6387,
  "quote_amount": 60.17,
  "fee_amount": 0.06,
  "fee_asset": "USDT",
  "net_quote_amount": 60.11,
  "position_before_pct": 1.00,
  "position_after_pct": 0.60,
  "remaining_base_qty": 141.35,
  "reason_type": "manual_reduce",
  "reason": "手动卖出约 40%，从满仓降回 60% 基础仓位",
  "linked_signal_id": "20260622-000000-orbit-live",
  "order_id": null,
  "notes": "成交额和手续费以交易所订单详情为准"
}
```

关键字段：

| 字段 | 必填 | 含义 |
|---|---:|---|
| `id` | 是 | 自定义交易 ID，建议时间 + buy/sell + 编号 |
| `trade_time_bj` | 是 | 北京时间 |
| `side` | 是 | `BUY` 或 `SELL` |
| `base_qty` | 是 | WLD 数量 |
| `price` | 是 | 成交均价 |
| `quote_amount` | 是 | 成交额，单位 USDT |
| `fee_amount` | 是 | 手续费数量 |
| `fee_asset` | 是 | 手续费币种 |
| `net_quote_amount` | 建议 | 卖出时为扣费后回收 USDT；买入时可为空或记录总支出 |
| `position_before_pct` | 建议 | 交易前仓位 |
| `position_after_pct` | 建议 | 交易后仓位 |
| `remaining_base_qty` | 建议 | 交易后剩余 WLD 数量 |
| `reason_type` | 建议 | `model_signal`、`manual_reduce`、`stop_loss`、`take_profit` 等 |
| `linked_signal_id` | 建议 | 对应模型快照 ID |
| `order_id` | 建议 | 交易所订单 ID |

---

## 6. `snapshots` 字段

每次跑 `orbit_live_signal.py` 后建议记录一条。

```json
{
  "id": "20260622-000000-orbit-live",
  "run_time_bj": "2026-06-22 09:32:03",
  "signal_date_utc": "2026-06-22 00:00:00",
  "predicted_simple_return_72h": 0.0651,
  "risk_prob_return_le_minus_6pct": 0.1954,
  "signal": "strong_long",
  "action": "increase",
  "current_position": 0.60,
  "target_position": 1.00,
  "trade_delta": 0.40,
  "signal_file": "reports/orbit_live/WLDUSDT_1h_orbit_live_h72_20260622_093203_signal.csv",
  "summary_file": "reports/orbit_live/WLDUSDT_1h_orbit_live_h72_20260622_093203_summary.json",
  "decision": "按 strong_long 加到 100%，随后手动降回 60%",
  "notes": "3% 点预测不作为强入场依据，6% 以上才视为强信号"
}
```

---

## 7. 当前这笔卖出如何追加

先创建目录：

```bash
mkdir -p data/manual
```

若还没有文件，可以先复制模板：

```bash
cp examples/wldusdt_manual_trades.example.json data/manual/wldusdt_manual_trades.json
```

然后在 `trades` 数组里追加：

```json
{
  "id": "20260622-sell-001",
  "trade_time_bj": "2026-06-22 09:32:00",
  "symbol": "WLDUSDT",
  "side": "SELL",
  "base_qty": 94.0,
  "price": 0.6387,
  "quote_amount": 60.17,
  "fee_amount": 0.06,
  "fee_asset": "USDT",
  "net_quote_amount": 60.11,
  "position_before_pct": 1.00,
  "position_after_pct": 0.60,
  "remaining_base_qty": 141.35,
  "reason_type": "manual_reduce",
  "reason": "手动卖出约 40%，从满仓降回 60% 基础仓位",
  "linked_signal_id": "20260622-000000-orbit-live",
  "order_id": null,
  "notes": "成交额和手续费以交易所订单详情为准"
}
```

同时把 `current_state` 更新为：

```json
{
  "as_of_bj": "2026-06-22 09:32:00",
  "base_qty_wld": 141.35,
  "cash_usdt": 60.11,
  "avg_cost_usdt": null,
  "last_price_usdt": 0.6387,
  "position_pct": 0.60,
  "notes": "已从 100% 满仓降回 60% 基础仓位；后续 live_signal 使用 --current-position 0.60"
}
```

---

## 8. 页面维护预留设计

后续可以写一个本地页面读取这个 JSON，做四件事：

1. 展示当前仓位、剩余 WLD、现金 USDT、浮盈浮亏。
2. 新增一笔交易记录。
3. 新增一次模型信号快照。
4. 自动计算平均成本、净投入、已实现盈亏、未实现盈亏、总收益率。

页面可以先做成本地单页应用：

```text
web/manual_trade_journal.html
```

读取/写入则可以配一个很小的 Python FastAPI 服务：

```text
tools/manual_trade_journal_api.py
```

先用 JSON 维护是合适的，后面页面只是把 JSON 的增删改查可视化。
