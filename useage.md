# 使用说明：加密货币连续涨跌分析流水线

本项目当前流水线分为五步：**收集数据 -> 分析数据 -> 当前状态概率推演 -> 生成图表报告 -> 复盘与迭代**。

## 0. 环境准备

安装脚本运行所需依赖：

```bash
pip install pandas requests matplotlib
```

如果图表中的中文显示异常，可安装或配置中文字体。`dashboard/streak_chart.py` 会优先尝试使用 `~/.fonts/SimHei.ttf`。

## 1. 收集数据

数据采集脚本会从 Binance 拉取已收盘 K 线数据，并保存到 `data/{SYMBOL}_{INTERVAL}.csv`。CSV 中的 `date` 使用 Binance K 线开盘时间，时间口径为 UTC。

默认采集 `WLDUSDT` 日线 1000 条：

```bash
python collectors/get_wld_data.py
```

指定交易对、周期和条数：

```bash
python collectors/get_wld_data.py -s WLDUSDT -i 4h -n 3000
```

例如 `-i 4h -n 30` 表示获取最近 30 根已经收盘的 4 小时 K 线，不包含当前仍在形成中的 4 小时 K 线。

常用参数：

- `-s, --symbol`：交易对，例如 `WLDUSDT`、`WLDUSDT`
- `-i, --interval`：K 线周期，例如 `1d`、`4h`、`1h`、`15m`
- `-n, --total`：采集总条数

## 2. 分析数据

分析脚本会读取 `data/{SYMBOL}_{INTERVAL}.csv`，计算最大连续上涨、最大连续下跌、当前连续状态，以及 Top N 连续涨跌记录。

默认分析 `WLDUSDT` 日线：

```bash
python analysis/streak_analysis.py
```

指定交易对、周期和 Top 数量：

```bash
python analysis/streak_analysis.py -s WLDUSDT -i 4h -n 10
```

如果提示数据文件不存在，先回到第 1 步采集对应的 `symbol` 和 `interval`。

## 3. 当前状态概率推演

概率脚本会根据当前连续上涨/下跌状态，在历史记录中寻找同类状态，并使用贝叶斯平滑估计下一根 K 线和未来多根 K 线的方向概率。

默认示例参数为 `WLDUSDT` 与 `4h`：

```bash
python analysis/state_probability.py
```

指定交易对和周期：

```bash
python analysis/state_probability.py -s WLDUSDT -i 4h
```

常用参数：

- `--horizons 1 3 6 12`：统计未来 N 根 K 线后的收益方向
- `--match exact`：只匹配历史上精确相同的连续数量
- `--match at-least`：匹配历史上至少达到当前连续数量的状态
- `--prior-strength 20`：贝叶斯先验强度，越大越向整体基准概率收缩

## 4. 生成图表报告

可视化脚本会生成包含收盘价、每日涨跌幅、连续天数的 PNG 图表，并保存到 `reports/{SYMBOL}_{INTERVAL}_{PERIOD}_streak.png`。

默认生成 `WLDUSDT` 全部数据图表：

```bash
python dashboard/streak_chart.py -s WLDUSDT
```

按时间范围生成报告：

```bash
python dashboard/streak_chart.py -s WLDUSDT -p 1m
python dashboard/streak_chart.py -s WLDUSDT -p 3m
python dashboard/streak_chart.py -s WLDUSDT -p 6m
python dashboard/streak_chart.py -s WLDUSDT -p 1y
python dashboard/streak_chart.py -s WLDUSDT -p all
```

可选时间范围：

- `1m`：最近 1 个月
- `3m`：最近 3 个月
- `6m`：最近 6 个月
- `1y`：最近 1 年
- `all`：全部数据，默认值

## 5. 复盘与迭代

建议每次跑完流水线后记录三类结论：

- 当前状态：现在是连续上涨、连续下跌还是持平，已持续多少根 K 线。
- 历史对比：当前连续天数是否接近历史 Top N 记录。
- 后续动作：是否需要扩大样本、切换周期、加入新指标或更新图表。

可以使用 `run_pipeline.py` 一次性执行采集、连续涨跌分析、当前状态概率推演和制图，并统一输出本次运行摘要。

示例：

```bash
python run_pipeline.py -s WLDUSDT -i 4h -n 3000 -p 6m
```

该命令会获取 `WLDUSDT` 最近 3000 根已收盘 4 小时 K 线，保存到 `data/WLDUSDT_4h.csv`，输出连续涨跌分析，并生成 `reports/WLDUSDT_4h_6m_streak.png`。

流水线会默认输出贝叶斯概率推演；如需调整口径，可以追加参数：

```bash
python run_pipeline.py -s WLDUSDT -i 4h -n 3000 -p 6m --horizons 1 3 6 12 --match exact --prior-strength 20
```
