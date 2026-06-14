# Repository Guidelines

## 项目结构与模块组织

本仓库是一个用于加密货币价格连续涨跌分析的 Python 研究工作流。

- `collectors/` 存放数据采集脚本。`get_wld_data.py` 从 Binance 下载 K 线数据，并写入 `data/{SYMBOL}_{INTERVAL}.csv`。
- `analysis/` 存放命令行分析脚本。`streak_analysis.py` 读取 CSV 数据并输出连续上涨/下跌统计。
- `dashboard/` 存放可视化脚本。`streak_chart.py` 读取已准备的数据，并将图表保存到 `reports/`。
- `data/` 存放生成的 CSV 数据集。除非任务明确要求更新样例数据，否则将其视为本地产物。
- `reports/` 存放生成的图表图片。不要手动编辑生成图片。
- `indicators/` 与 `notebooks/` 用于探索性工作；可复用逻辑应尽量沉淀到脚本中。

## 构建、测试与开发命令

在当前 Python 环境中安装运行依赖：

```bash
pip install pandas requests matplotlib
```

常用工作流命令：

```bash
python collectors/get_wld_data.py
python collectors/get_wld_data.py -s BTCUSDT -i 4h -n 3000
python analysis/streak_analysis.py -s WLDUSDT -i 1d -n 10
python dashboard/streak_chart.py -s WLDUSDT -i 1d -p 3m
```

采集脚本负责拉取 Binance K 线，分析脚本输出连续涨跌摘要，可视化脚本生成 PNG 报告。

## 代码风格与命名约定

使用 Python 3，缩进为 4 个空格；导入顺序优先标准库，再导入第三方库。函数名保持清晰，例如 `load_data`、`compute_streaks`、`plot_streak`。命令行参数应保持一致：`--symbol`、`--interval`、`--total`、`--top`、`--period`。生成数据文件命名为 `{SYMBOL}_{INTERVAL}.csv`，报告命名为 `{SYMBOL}_{INTERVAL}_{PERIOD}_streak.png`。

## 测试指南

当前没有专门的测试套件。修改脚本后，先运行语法检查：

```bash
python -m py_compile collectors/get_wld_data.py analysis/streak_analysis.py dashboard/streak_chart.py
```

修改分析逻辑时，使用小数据集验证连续涨跌日期、长度和百分比是否符合预期。后续新增测试建议使用 `pytest`，放在 `tests/` 下，文件名如 `test_streak_analysis.py`。

## 提交与 Pull Request 指南

为避免读取 `.git`，这里未检查提交历史。提交信息建议使用简短的祈使句，例如 `Add streak chart period filter` 或 `Fix Binance pagination`。Pull Request 应说明目的、执行过的命令、影响的脚本；如果可视化输出变化，也应列出生成的报告路径。尽量关联相关 issue，除非评审需要，否则不要提交本地环境文件、大型数据集或重新生成的报告。

## 安全与配置建议

不要提交 API key、私有凭据或虚拟环境。涉及网络请求的脚本应显式保留超时与重试逻辑。CSV 与 PNG 生成文件可能较大，加入版本控制前先检查文件大小。
