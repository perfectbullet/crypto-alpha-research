# Orbit 参数试验与评估手册

本文档说明如何把 Orbit 当成一个“未来走势概率分布预测器”来评估，而不是把它强行绑定到反转系统。

核心目标：

```text
判断 Orbit 是否能稳定预测未来收益率分布、下行风险概率，以及这些概率是否有盈利利用价值。
```

---

## 1. 不要只看单次 metrics

`orbit_wld_experiment.py` 生成的 `*_metrics.json` 适合快速查看单次实验结果，但不足以决定模型是否可用。

原因：

1. 单次测试窗口可能偶然有效。
2. 只看 MAE / RMSE 不能说明概率是否可交易。
3. 方向准确率高，不代表收益更好。
4. 高风险概率如果没有校准，可能只是一个分数，不是真概率。
5. `horizon > 1` 时，未来收益率标签高度重叠，不能把每一行当成独立交易。

因此需要分三层评估：

| 层级 | 核心问题 | 代表指标 |
|---|---|---|
| 点预测 | 预测值和真实值接近吗 | MAE、RMSE、相关性 |
| 概率预测 | 风险概率可信吗 | Brier Score、Log Loss、Calibration |
| 利用价值 | 信号是否有收益差异 | 分桶收益、Top 风险命中率、信号收益 |

---

## 2. 单次预测评估工具

新增脚本：

```bash
analysis/orbit_evaluate_predictions.py
```

用途：读取 `orbit_wld_experiment.py` 生成的 `*_predictions.csv`，输出更完整的评估结果。

示例：

```bash
python analysis/orbit_evaluate_predictions.py \
  --predictions reports/orbit/WLDUSDT_1h_orbit_h24_xxx_predictions.csv \
  --risk-threshold -0.05 \
  --long-threshold 0.02 \
  --short-threshold -0.02 \
  --fee-rate 0.001
```

输出目录默认：

```text
reports/orbit_eval/
```

输出文件：

| 文件 | 含义 |
|---|---|
| `*_summary.json` | 点预测、概率预测、信号效用汇总 |
| `*_calibration.csv` | 风险概率分桶校准表 |
| `*_calibration.png` | 风险概率校准图 |

---

## 3. summary.json 怎么看

### 3.1 point_metrics

点预测指标回答：

```text
Orbit 预测的未来收益率，和真实未来收益率接近吗？
```

重点字段：

| 字段 | 含义 | 越好方向 |
|---|---|---|
| `mae_log_return` | 对数收益率平均绝对误差 | 越低越好 |
| `rmse_log_return` | 对数收益率均方根误差 | 越低越好 |
| `mae_simple_return` | 普通收益率平均绝对误差 | 越低越好 |
| `rmse_simple_return` | 普通收益率均方根误差 | 越低越好 |
| `correlation_log_return` | 预测收益率和真实收益率相关性 | 越高越好 |
| `directional_accuracy` | 涨跌方向预测准确率 | 越高越好 |
| `actual_down_rate` | 测试集中实际下跌比例 | 参考值 |
| `predicted_down_rate` | 模型预测下跌比例 | 与实际下跌比例对比 |

注意：

```text
方向准确率不能单独决定模型好坏。
```

例如一个模型方向准确率 55%，但在大行情时全部错过，仍然没有盈利价值。

---

### 3.2 probability_metrics

概率指标回答：

```text
Orbit 给出的风险概率是否可信？
```

如果设置：

```bash
--risk-threshold -0.05
```

那么事件就是：

```text
未来 H 小时普通收益率 <= -5%
```

重点字段：

| 字段 | 含义 | 越好方向 |
|---|---|---|
| `brier_score` | 概率预测误差 | 越低越好 |
| `log_loss` | 概率预测惩罚 | 越低越好 |
| `average_predicted_probability` | 平均预测风险概率 | 应接近实际事件率 |
| `actual_event_rate` | 真实发生率 | 参考值 |
| `calibration_mae` | 分桶校准误差 | 越低越好 |
| `top_10pct_event_rate` | 风险概率最高 10% 样本的真实事件率 | 越高越好 |
| `top_20pct_lift` | Top 20% 事件率 / 全样本事件率 | 越高越好 |

最重要的不是平均概率，而是：

```text
风险概率越高的样本，真实下跌事件是否真的越多。
```

---

### 3.3 calibration.csv

校准表是评估概率质量的关键。

默认分桶：

```text
0-20%, 20-40%, 40-60%, 60-80%, 80-100%
```

每个桶里有：

| 字段 | 含义 |
|---|---|
| `count` | 该概率区间内样本数 |
| `mean_predicted_prob` | 平均预测风险概率 |
| `actual_event_rate` | 实际跌破阈值比例 |
| `avg_actual_simple_return` | 该桶实际平均收益率 |
| `calibration_gap` | 预测概率 - 实际发生率 |

判断方法：

| 情况 | 说明 |
|---|---|
| 高概率桶的实际事件率也高 | 模型有排序价值 |
| 预测 70%，实际也接近 70% | 概率校准较好 |
| 每个桶实际事件率差不多 | 风险概率没什么区分度 |
| 高概率桶实际事件率反而低 | 模型方向可能反了 |

---

### 3.4 signal_utility_metrics

信号效用指标回答：

```text
如果根据 Orbit 的预测收益率做一个粗略多空信号，是否有收益差异？
```

默认逻辑：

```text
predicted_simple_return >= 2%  -> 做多
predicted_simple_return <= -2% -> 做空
否则空仓
```

重点字段：

| 字段 | 含义 |
|---|---|
| `long_count` | 做多信号数量 |
| `short_count` | 做空信号数量 |
| `flat_count` | 空仓信号数量 |
| `average_signal_return` | 非空信号平均收益 |
| `average_long_return` | 做多信号平均真实收益 |
| `average_short_return` | 做空信号平均真实收益，已按做空方向取正 |
| `hit_rate` | 信号收益为正的比例 |
| `cumulative_signal_return` | 粗略复利结果 |
| `max_drawdown` | 粗略信号权益最大回撤 |
| `always_long_average_return` | 始终做多的平均收益 |
| `always_long_cumulative_return` | 始终做多的粗略复利结果 |

重要限制：

```text
horizon > 1 时，相邻样本的未来收益率窗口重叠，signal_utility_metrics 不是正式交易回测。
```

它只能用于筛选参数组合，不能直接作为真实策略收益。

---

## 4. 参数批量试验工具

新增脚本：

```bash
analysis/orbit_sweep.py
```

用途：批量运行不同参数组合，汇总每组 `metrics.json`。

示例：

```bash
python analysis/orbit_sweep.py \
  --input data/WLDUSDT_1h.csv \
  --horizons 6,12,24,72 \
  --test-sizes 240,480 \
  --max-rows-list 3000,6000,12000 \
  --seasonalities 24,168 \
  --estimators stan-map \
  --risk-thresholds -0.03,-0.05,-0.08 \
  --regressor-modes with,without \
  --continue-on-error
```

输出目录默认：

```text
reports/orbit_sweep/
```

输出文件：

| 文件 | 含义 |
|---|---|
| `orbit_sweep_*.csv` | 所有参数组合的扁平化指标汇总 |
| `orbit_sweep_*.json` | 所有参数组合完整结果 |

---

## 5. sweep 结果怎么排序

`sweep` 脚本会给出一个临时筛选分数：

```text
screening_score
```

它综合考虑：

1. `directional_accuracy`
2. `high_risk_actual_down_rate / actual_down_rate` 的 lift
3. `mae_simple_return`
4. 高风险样本数量是否过少

这个分数只适合快速筛选，不是最终目标函数。

更严谨的筛选顺序应该是：

1. 先排除 MAE / RMSE 明显很差的组合。
2. 再看方向准确率是否高于简单基线。
3. 再看风险概率分桶是否有单调性。
4. 再看 Top 20% 风险桶是否显著更容易下跌。
5. 再看 signal utility 是否优于 always long 或简单趋势策略。
6. 最后做 walk-forward 多窗口验证。

---

## 6. 推荐第一批参数组合

为了平衡时间和学习效率，先不要跑太大的网格。

第一批：快速筛选。

```bash
python analysis/orbit_sweep.py \
  --input data/WLDUSDT_1h.csv \
  --horizons 6,12,24,72 \
  --test-sizes 240 \
  --max-rows-list 3000,6000 \
  --seasonalities 24,168 \
  --estimators stan-map \
  --risk-thresholds -0.05 \
  --regressor-modes with,without \
  --continue-on-error
```

一共：

```text
4 × 1 × 2 × 2 × 1 × 1 × 2 = 32 组
```

第二批：扩大训练窗口。

```bash
python analysis/orbit_sweep.py \
  --input data/WLDUSDT_1h.csv \
  --horizons 12,24 \
  --test-sizes 240,480 \
  --max-rows-list 6000,12000,0 \
  --seasonalities 24,168 \
  --estimators stan-map \
  --risk-thresholds -0.03,-0.05,-0.08 \
  --regressor-modes with \
  --continue-on-error
```

第三批：只对少数优秀组合试 `stan-mcmc`。

不要一开始全量 MCMC，因为慢。

---

## 7. 什么样的结果才值得继续

Orbit 值得继续研究，至少要看到这些现象：

| 判断项 | 最低要求 |
|---|---|
| 方向准确率 | 明显高于 50%，且多个窗口稳定 |
| 相关性 | 大于 0，最好稳定为正 |
| Top 风险桶 | 事件率明显高于全样本事件率 |
| 风险分桶 | 高概率桶的真实下跌率更高 |
| Signal Utility | 优于 always long 或简单基线 |
| 多窗口稳定性 | 不只在一个测试区间有效 |

如果只是某一次图看起来不错，不算有效。

---

## 8. 后续必须补的基线

当前还缺几个基线模型：

1. `zero baseline`：预测未来收益率为 0。
2. `momentum baseline`：预测未来收益率等于过去 N 小时收益率。
3. `rolling mean baseline`：预测未来收益率等于过去 N 小时未来收益率均值。
4. `LightGBM/XGBoost baseline`：表格特征监督学习。

只有 Orbit 稳定打败这些基线，才说明它有增量价值。

---

## 9. 当前阶段建议

当前优先级：

```text
先用 stan-map 跑大量参数组合
再用 orbit_evaluate_predictions.py 深挖 Top 组合
最后只对少数 Top 组合跑 stan-mcmc 看预测区间
```

不要一开始追求完整贝叶斯采样。先证明 Orbit 的预测信号有排序价值和利用价值。
