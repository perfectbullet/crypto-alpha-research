# Orbit WLD 15m 短线实验手册

本手册用于在现有 1h Orbit 实验基础上，跑一套适合 **WLDUSDT 15 分钟 K 线**的短线实验。
所有 15m 脚本只新增、不改 1h 脚本，1h 行为完全保留。

核心区别：**1h 脚本里 horizon 字段其实是「向后多少根 K 线」，名字写成 `future_log_return_{horizon}h`。
这在 1h 数据里成立（1 根 = 1 小时），但在 15m 数据里会把 horizon=8 误解成「8 小时」。**
所以 15m 脚本对外只暴露 `--horizon-minutes`（真实预测时长），内部再换算成 K 线根数喂给 Orbit，
输出里同时给出 `horizon_minutes` / `horizon_bars` / `horizon_label`，杜绝歧义。

---

## 0. 文件总览

| 文件 | 作用 |
|---|---|
| `analysis/orbit_timeframe_utils.py` | 通用时间粒度工具：分钟↔根数换算、人类可读标签、horizon 校验 |
| `analysis/orbit_wld_15m_experiment.py` | 15m 单次实验，复用旧 `run_experiment` |
| `analysis/orbit_sweep_15m.py` | 15m 参数 sweep，调 15m 实验，可选导出 GPT packet |
| `analysis/orbit_export_gpt_packet.py` | 把 Top 候选参数和关键指标打包成一次性上传给 GPT 的 JSON |
| `analysis/orbit_live_signal_15m.py` | 15m 最新仓位信号 |
| `data/WLDUSDT_15m.csv` | 15m K 线数据（采集器产出） |

---

## 1. 如何拉 15m 数据

Binance Spot `/api/v3/klines` 支持 15m K 线，采集器 `collectors/get_wld_data.py` 已支持 `15m` 周期。

```bash
python collectors/get_wld_data.py \
  -s WLDUSDT \
  -i 15m \
  -n 50000 \
  -o data/WLDUSDT_15m.csv \
  --no-trim
```

- `-n 50000`：拉取最近 50000 根 15m K 线（约 520 天）。
- `--no-trim`：不裁剪到 `-n` 条，保留本地全部历史，方便后续增量更新。
- 字段与 1h 一致：`date, open, high, low, close, volume, quote_volume, trades`。
- 采集器只落盘**已收盘** K 线，CSV 里不会混入正在走的那半根。

之后增量更新只需重跑同一条命令，采集器会从本地最后一根之后只拉缺失的新数据。

---

## 2. 如何跑 15m 单次实验

```bash
python analysis/orbit_wld_15m_experiment.py \
  --input data/WLDUSDT_15m.csv \
  --interval-minutes 15 \
  --horizon-minutes 120 \
  --test-size 960 \
  --max-rows 16000 \
  --seasonality-minutes 1440 \
  --estimator stan-map \
  --risk-threshold -0.02
```

参数说明：

| 参数 | 默认 | 含义 |
|---|---|---|
| `--interval-minutes` | `15` | K 线周期（分钟） |
| `--horizon-minutes` | `120` | **真实预测时长**（分钟），必须能被 interval 整除，上限 1440 |
| `--test-size` | `960` | 最近 N 根 K 线做测试集 |
| `--max-rows` | `16000` | 最多用最近 N 条样本训练；`<=0` 全量 |
| `--seasonality-minutes` | `1440` | 季节性周期（分钟），1440=1 天，10080=1 周 |
| `--estimator` | `stan-map` | Orbit 估计器 |
| `--risk-threshold` | `-0.02` | 风险事件阈值（普通收益率） |
| `--no-regressors` | 关 | 不用 OHLCV 派生外生变量 |

输出落在 `reports/orbit_15m/`，文件名带 interval 和 horizon label，例如：

```text
WLDUSDT_15m_orbit_h2h_YYYYMMDD_HHMMSS_predictions.csv
WLDUSDT_15m_orbit_h2h_YYYYMMDD_HHMMSS_metrics.json
WLDUSDT_15m_orbit_h2h_YYYYMMDD_HHMMSS_plot.png
```

`predictions.csv` 末尾追加 4 列：`interval_minutes, horizon_minutes, horizon_bars, horizon_label`。
`metrics.json` 里同样补充 `interval_minutes / horizon_minutes / horizon_bars / horizon_label /
seasonality_minutes / seasonality_bars / seasonality_label`。

---

## 3. 如何跑 15m sweep

```bash
python analysis/orbit_sweep_15m.py \
  --input data/WLDUSDT_15m.csv \
  --interval-minutes 15 \
  --horizon-minutes 15,30,45,60,90,120,180,240 \
  --test-sizes 960 \
  --max-rows-list 8000,16000 \
  --seasonality-minutes 1440,10080 \
  --estimators stan-map \
  --risk-thresholds=-0.01,-0.02 \
  --regressor-modes with \
  --auto-evaluate-top-n 5 \
  --auto-select-min-high-risk-count 20 \
  --eval-long-threshold 0.006 \
  --eval-short-threshold -0.006 \
  --eval-fee-rate 0.001 \
  --export-gpt-json \
  --continue-on-error
```

流程：

1. 把每个 `horizon-minutes` 换算成 `horizon_bars`（如 120/15=8），每个 `seasonality-minutes` 换算成 `seasonality_bars`。
2. 对参数网格里每一组，调用 **15m 实验**（不是旧 1h 实验），落到 `reports/orbit_15m/`。
3. 全部组合写进 `reports/orbit_sweep_15m/orbit_sweep_15m_*.csv`（含 `screening_score` 粗筛打分）。
4. 自动选 Top-N 跑深度评估，复用 `analysis/orbit_evaluate_predictions.py`，产出：
   - `orbit_sweep_15m_*_selected_top.csv`：入选的 Top-N。
   - `orbit_sweep_15m_*_evaluated_top.csv`：含评估结果。
   - 评估明细在 `reports/orbit_eval_15m/`。
5. `--export-gpt-json` 开启时，额外产出 `orbit_sweep_15m_*_gpt_packet.json`。

汇总 CSV 关键列：`interval_minutes, horizon_minutes, horizon_bars, horizon_label,
test_size_bars, test_size_days, max_rows, seasonality_minutes, seasonality_bars,
seasonality_label, risk_threshold` + 全部旧 metrics。

`--auto-select-min-high-risk-count` 用 20~40 之间，避免选到「看起来准但风险样本太稀疏」的组合。

---

## 4. 如何上传 gpt_packet.json 给 GPT

```text
reports/orbit_sweep_15m/orbit_sweep_15m_YYYYMMDD_HHMMSS_gpt_packet.json
```

这个 JSON 是**一次性**设计好的：把整个文件内容直接粘贴/上传给 GPT，不用再补充别的东西。
里面包含：

- `experiment`：资产、周期、输入文件、生成时间。
- `data`：数据行数、最新 K 线 UTC + 北京时间。
- `sweep_config`：本次 sweep 的完整参数网格。
- `top_candidates`：最多 20~30 个候选，每个含：
  - 参数：`horizon_minutes / horizon_bars / horizon_label / test_size_bars / test_size_days /
    max_rows / seasonality_* / risk_threshold`。
  - `point_metrics`：点预测误差与方向准确率。
  - `probability_metrics`：下行风险概率的 brier / log_loss / 校准 / lift。
  - `signal_utility_metrics`：按阈值转多空信号的粗略诊断。
  - `paths`：predictions / metrics / summary / calibration 四个文件路径。
- `notes`：解释字段含义和注意事项。

给 GPT 时可以这样说：「这是 WLDUSDT 15m 的参数 sweep 结果，请帮我挑出最稳健的 1~2 组参数，
重点看校准和 lift，不要只看点预测误差，并说明理由。」

---

## 5. 如何解读 horizon_minutes

这是 15m 实验和 1h 实验最关键的认知差异：

```text
horizon_minutes = 真实预测时长（分钟）   ← 给人看、给交易决策用
horizon_bars    = 内部 K 线根数          ← 给 Orbit 用 = horizon_minutes / interval_minutes
horizon_label   = 人类可读标签            ← 例如 120m -> "2h"，1440m -> "1d"
```

举例（`interval_minutes=15`）：

| horizon_minutes | horizon_bars | horizon_label | 含义 |
|---:|---:|---|---|
| 15 | 1 | 15m | 预测下一根 15m 收盘的收益 |
| 60 | 4 | 1h | 预测未来 1 小时收益 |
| 120 | 8 | 2h | 预测未来 2 小时收益 |
| 240 | 16 | 4h | 预测未来 4 小时收益 |
| 1440 | 96 | 1d | 预测未来 1 天收益 |

- **horizon_minutes 才是你等的时间**。用户最多只愿意等 1 天，所以脚本硬限制 `horizon_minutes <= 1440`。
- `horizon_bars` 是内部细节，文件名里的 `h2h`、`h1d` 用的是 `horizon_label`，不再是 `h8` 这种会让人误解成 8 小时的写法。
- `seasonality` 同理：`seasonality_minutes=1440`（1 天）→ `seasonality_bars=96`；`10080`（1 周）→ `672`。

---

## 6. 如何跑 live_signal_15m

```bash
python analysis/orbit_live_signal_15m.py \
  --input data/WLDUSDT_15m.csv \
  --interval-minutes 15 \
  --horizon-minutes 120 \
  --max-rows 16000 \
  --seasonality-minutes 1440 \
  --estimator stan-map \
  --risk-threshold -0.02 \
  --prediction-rows 1 \
  --current-position 0.00
```

终端会直接打印人类可读结果：

```text
interval: 15m
horizon: 120m / 2h（8 根 K 线）
latest_signal_date UTC: ...
latest_signal_date Beijing: ...
predicted_simple_return_2h: -0.45%
risk_prob_return_le_-2.00%: 38.20%
signal: reduced
action: hold
current_position: 0.00%
target_position: 30.00%
trade_delta: 30.00%
```

信号 CSV 和摘要 JSON 落在 `reports/orbit_live_15m/`，文件名同样带 `15m` 和 `horizon_label`。
`--current-position` 填当前真实仓位（0=空仓，0.6=60%），脚本据此算 `action` 和 `trade_delta`。

---

## 7. 500 RMB 短线实验仓位规则

短线波动幅度远小于 1h，所以阈值整体比 1h（3%/6%）小一个量级。
**实验仓位按「实验资金」算，不按总账户算**：

```text
实验仓位 = 当前 WLD 市值 / 实验资金总额
```

500 RMB 实验资金下，仓位规则（对应 `orbit_live_signal_15m.py` 默认参数）：

| 条件 | 目标仓位 | 说明 |
|---|---:|---|
| `predicted_simple_return >= 0.015` 且 `risk_prob <= 0.25` | 100% | 强做多，买满实验资金 |
| `predicted_simple_return >= 0.008` 且 `risk_prob <= 0.35` | 60% | 普通做多，持有基础仓位 |
| `predicted_simple_return < 0.003` 或 `risk_prob >= 0.60` | 30% | 信号转弱，降仓观察 |
| `predicted_simple_return <= -0.005` 或 `risk_prob >= 0.80` | 0% | 清仓 |

对应 CLI 参数：

| 参数 | 默认 | 含义 |
|---|---|---|
| `--long-threshold` | `0.008` | 预测涨幅 ≥0.8% 才算普通做多 |
| `--buy-risk-max` | `0.35` | 普通做多允许的最大风险概率 |
| `--strong-long-threshold` | `0.015` | 预测涨幅 ≥1.5% 才算强做多 |
| `--strong-risk-max` | `0.25` | 强做多允许的最大风险概率 |
| `--reduce-pred-threshold` | `0.003` | 预测涨幅 <0.3% 说明信号转弱 |
| `--reduce-risk-threshold` | `0.60` | 风险概率 ≥60% 降仓 |
| `--clear-pred-threshold` | `-0.005` | 预测跌幅 ≤-0.5% 清仓 |
| `--clear-risk-threshold` | `0.80` | 风险概率 ≥80% 清仓 |
| `--reduced-position` | `0.30` | 弱信号目标仓位 |
| `--base-position` | `0.60` | 普通做多目标仓位 |
| `--max-position` | `1.00` | 强做多目标仓位 |

举例：500 RMB 实验资金，当前空仓，预测 `2h` 收益 +1.0%、风险概率 30% → `signal=long`、
`target_position=0.60`、`action=buy`、`trade_delta=+0.60`，即买入约 300 RMB 的 WLD。

---

## 8. 短线交易注意事项

- **手续费和滑点不可忽略**。现货 maker/taker 约 0.1%，叠加滑点后单次来回轻松吃掉 0.2%~0.3%。
  15m horizon 的典型预测收益也就 ±0.5%~1%，所以 `--eval-fee-rate 0.001` 必须开着，
  信号阈值（0.8%/1.5%）也要明显高于来回手续费，否则净收益会被手续费吃光。
- **信号噪声高于 1h 模型**。15m 标签受微观结构和单根大单影响更大，方向准确率天然偏低，
  不要因为某次 sweep 的 directional_accuracy 只有 50% 多就否定参数，重点看**校准**和**lift**。
- **相邻 horizon 标签高度重叠**。`horizon_minutes > interval_minutes` 时，相邻两根 K 线的
  未来收益窗口大量重合，所以 `signal_utility_metrics` 只是粗略诊断，**不是正式回测**，
  不能直接当作可交易收益。
- **horizon 别选太短**。15m/30m 的预测窗口太接近一根 K 线，噪声最大、可操作性最低；
  实盘建议优先看 `horizon_minutes` 在 60~240（1h~4h）的组合。
- **先 sweep 再 live**。先用 sweep + GPT packet 选出稳健参数，再拿参数去跑 `live_signal_15m`，
  不要拿未经验证的默认参数直接下单。
