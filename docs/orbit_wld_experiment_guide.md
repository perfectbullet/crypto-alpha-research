# Orbit WLD 小时数据实验手册

本文档用于在当前项目中快速验证 `Orbit` 是否适合加入“当前反转状态概率推演”系统。

目标不是直接得到可交易信号，而是先回答三个问题：

1. Orbit 能否基于 WLD 小时数据跑出稳定的预测结果？
2. Orbit 的预测分布/区间能否转成下行风险概率？
3. 这些风险概率是否比简单基线更有信息量？

---

## 1. 为什么第一版预测 future return

当前已有数据文件：

```text
data/WLDUSDT_1h.csv
```

字段来自 Binance K 线采集器，至少包含：

```text
date, open, high, low, close, volume, quote_volume, trades
```

第一版不直接预测未来价格，而是预测未来 `H` 小时收益率：

```text
future_log_return_H = log(close[t + H] / close[t])
```

例如 `--horizon 24` 表示：

```text
在 t 时刻，预测未来 24 小时后的收益率分布。
```

这样做的原因：

| 目标 | 评价 |
|---|---|
| 预测具体价格 | 噪声大，不适合第一版 |
| 预测未来收益率 | 更稳定，方便转风险概率 |
| 预测未来回撤 | 更贴近风控，但第一版实现复杂度略高 |
| 预测是否反转 | 需要先定义标签，不适合第一步 |

因此第一版先预测 `future_log_return_24h`。

---

## 2. Orbit 在当前系统中的定位

Orbit 不是完整的反转判断系统。

它在当前系统中的合理定位是：

```text
OHLCV 小时数据
  ↓
构造市场状态特征
  ↓
Orbit 预测未来收益率分布
  ↓
转换成下行风险概率
  ↓
作为反转概率推演中的一类证据
```

也就是说，Orbit 先作为：

```text
下行风险概率预测器
```

而不是：

```text
自动交易信号生成器
```

---

## 3. 安装依赖

优先推荐 conda，尤其是在 WSL2 / Linux 环境里：

```bash
conda create -n orbit-test python=3.10 -y
conda activate orbit-test
conda install -c conda-forge orbit-ml pandas numpy matplotlib -y
```

也可以使用 pip：

```bash
pip install -r requirements-orbit.txt
```

如果 pip 安装 Orbit 或 Stan 相关依赖失败，不要在 pip 方向硬耗时间，直接换 conda。

---

## 4. 最小运行命令

在项目根目录运行：

```bash
python analysis/orbit_wld_experiment.py \
  --input data/WLDUSDT_1h.csv \
  --horizon 24 \
  --test-size 240 \
  --max-rows 6000 \
  --seasonality 24 \
  --estimator stan-map \
  --risk-threshold -0.05
```

参数含义：

| 参数 | 默认值 | 含义 |
|---|---:|---|
| `--horizon` | 24 | 预测未来多少小时后的收益率 |
| `--test-size` | 240 | 最近多少条样本作为测试集 |
| `--max-rows` | 6000 | 最多使用最近多少条有效样本，控制耗时 |
| `--seasonality` | 24 | 小时数据的日内周期 |
| `--estimator` | `stan-map` | 速度优先的估计方式 |
| `--risk-threshold` | -0.05 | 计算未来收益率小于等于 -5% 的风险概率 |

---

## 5. 输出文件

脚本会输出到：

```text
reports/orbit/
```

包括三类文件：

```text
*_predictions.csv
*_metrics.json
*_plot.png
```

由于 `reports/` 已经在 `.gitignore` 中，实验结果默认不提交到仓库。

---

## 6. predictions.csv 怎么看

核心字段通常包括：

| 字段 | 含义 |
|---|---|
| `date` | 当前时间点 |
| `future_log_return_24h` | 实际未来 24 小时 log 收益率 |
| `prediction` | Orbit 对未来 24 小时 log 收益率的预测 |
| `predicted_simple_return` | 把 log 收益率预测转成普通收益率 |
| `risk_prob_return_le_threshold` | 未来收益率低于阈值的风险概率 |
| `risk_threshold_simple_return` | 风险阈值，例如 -0.05 |

注意：如果 Orbit 版本返回了预测分位数列，例如 `prediction_5`、`prediction_95`，脚本会优先基于预测区间近似计算风险概率。否则会退回到基于训练集目标波动率的正态近似。

---

## 7. metrics.json 怎么看

重点看这些指标：

| 指标 | 解释 |
|---|---|
| `mae_log_return` | log 收益率平均绝对误差 |
| `rmse_log_return` | log 收益率均方根误差 |
| `directional_accuracy` | 方向判断准确率 |
| `actual_down_rate` | 测试集中真实下跌比例 |
| `predicted_down_rate` | 模型预测为下跌的比例 |
| `average_predicted_risk_prob` | 平均下行风险概率 |
| `high_risk_count` | 风险概率 >= 60% 的样本数 |
| `high_risk_actual_down_rate` | 高风险样本中真实下跌比例 |
| `interval_coverage` | 若存在预测区间，实际值落入区间的比例 |
| `risk_method` | 风险概率计算方法 |

第一版最重要的不是 MAE，而是：

```text
high_risk_actual_down_rate 是否明显高于 actual_down_rate
```

例如：

```text
actual_down_rate = 52%
high_risk_actual_down_rate = 70%
```

这说明 Orbit 的高风险区间有一定区分能力。

如果：

```text
actual_down_rate = 52%
high_risk_actual_down_rate = 51%
```

那说明 Orbit 的风险概率暂时没提供有效增量信息。

---

## 8. 建议的实验顺序

不要一次性跑所有组合。按下面顺序来。

### 8.1 第一组：只验证默认流程

```bash
python analysis/orbit_wld_experiment.py --input data/WLDUSDT_1h.csv
```

确认能输出 CSV、JSON 和 PNG。

### 8.2 第二组：比较不同预测窗口

```bash
python analysis/orbit_wld_experiment.py --input data/WLDUSDT_1h.csv --horizon 6
python analysis/orbit_wld_experiment.py --input data/WLDUSDT_1h.csv --horizon 12
python analysis/orbit_wld_experiment.py --input data/WLDUSDT_1h.csv --horizon 24
python analysis/orbit_wld_experiment.py --input data/WLDUSDT_1h.csv --horizon 72
```

一般来说：

| horizon | 用途 |
|---:|---|
| 6 | 短线噪声较大，但反应快 |
| 12 | 半日级风险 |
| 24 | 第一版主力窗口 |
| 72 | 更接近趋势/反转判断 |

### 8.3 第三组：比较样本长度

```bash
python analysis/orbit_wld_experiment.py --input data/WLDUSDT_1h.csv --max-rows 3000
python analysis/orbit_wld_experiment.py --input data/WLDUSDT_1h.csv --max-rows 6000
python analysis/orbit_wld_experiment.py --input data/WLDUSDT_1h.csv --max-rows 12000
```

不要默认认为数据越多越好。加密市场有明显非平稳性，太久以前的数据可能反而干扰当前状态。

### 8.4 第四组：比较是否使用外生变量

使用 OHLCV 派生特征：

```bash
python analysis/orbit_wld_experiment.py --input data/WLDUSDT_1h.csv --horizon 24
```

只用目标序列：

```bash
python analysis/orbit_wld_experiment.py --input data/WLDUSDT_1h.csv --horizon 24 --no-regressors
```

如果使用外生变量没有明显改善，说明当前 OHLCV 派生特征质量不够，后续需要加入 Funding、OI、资金流、链上、情绪等指标。

---

## 9. 不建议第一版做什么

第一版不要做这些：

```text
不要直接预测精确价格。
不要直接把 Orbit 结果当交易信号。
不要一上来用 stan-mcmc 跑全量 2 万条。
不要把 Orbit 源码复制到当前仓库。
不要只看图好不好看。
不要只看训练集表现。
```

第一版只做：

```text
能不能跑通；
预测分布有没有稳定性；
高风险分组是否真的更容易下跌；
是否值得纳入反转概率推演。
```

---

## 10. 关于是否把 Orbit 源码放进当前仓库

暂时不建议。

更推荐：

```text
当前仓库只保存实验脚本、文档和依赖说明。
Orbit 作为外部依赖安装。
```

只有在下面情况出现时，才考虑 vendoring 或 fork：

| 情况 | 是否考虑放源码 |
|---|---|
| 官方包安装失败且无法解决 | 可以考虑 fork |
| 需要修改 Orbit 内部模型 | 可以考虑 fork |
| 需要固定某个私有补丁版本 | 可以考虑 fork |
| 只是正常调用 DLT | 不需要 |

---

## 11. 如何判断 Orbit 是否值得继续用

用下面标准判断：

| 结果 | 结论 |
|---|---|
| 高风险样本真实下跌率明显高于总体下跌率 | 值得继续 |
| interval coverage 接近预期区间 | 预测区间有参考价值 |
| 不同 horizon 下都有稳定区分能力 | 值得纳入系统 |
| 换样本长度后结果大幅漂移 | 暂时不稳定 |
| 与 `--no-regressors` 相比没有改善 | 当前外生特征不足 |
| 长期跑不赢简单基线 | 不应作为主模块 |

---

## 12. 后续增强方向

第一版跑通后，再考虑这些增强：

1. 加入 Funding Rate。
2. 加入 Open Interest。
3. 加入多空爆仓数据。
4. 加入 BTC/ETH/Total Crypto Market Cap 跨资产特征。
5. 加入趋势状态标签，例如连续涨跌、破位、反弹失败。
6. 把目标从 `future_return` 扩展到 `future_max_drawdown`。
7. 做 walk-forward 多折回测，而不是只用最后一段 holdout。
8. 把 Orbit 输出的风险概率接入 `analysis/state_probability.py` 的证据系统。

---

## 13. 当前推荐结论

当前项目阶段，Orbit 最合适的试验定位是：

```text
用 WLD 小时 OHLCV 数据预测未来 24/72 小时收益率分布，
并把下行尾部概率转换成反转风险证据。
```

如果第一版实验显示高风险分组确实更容易下跌，再继续把 Orbit 接入完整的反转概率推演系统。
