# 先获取数据
python collectors/get_wld_data.py

# 分析
python analysis/streak_analysis.py

# 指定交易对 / Top 数量
python analysis/streak_analysis.py -s BTCUSDT -n 10




# 默认：WLDUSDT 日线 1000 条
python collectors/get_wld_data.py

# 获取 BTC 4小时线 3000 条
python collectors/get_wld_data.py -s BTCUSDT -i 4h -n 3000






pip install matplotlib
python dashboard/streak_chart.py -s WLDUSDT


python dashboard/streak_chart.py -p 1m    # 最近1个月
python dashboard/streak_chart.py -p 3m    # 最近3个月
python dashboard/streak_chart.py -s WLDUSDT -p 6m    # 最近6个月
python dashboard/streak_chart.py -p 1y    # 最近1年
python dashboard/streak_chart.py -p all   # 全部（默认）
