import argparse
import os

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")


def load_data(symbol: str, interval: str) -> pd.DataFrame:
    path = os.path.join(DATA_DIR, f"{symbol}_{interval}.csv")
    if not os.path.exists(path):
        print(f"数据文件不存在: {path}\n请先运行: python collectors/get_wld_data.py -s {symbol} -i {interval}")
        return pd.DataFrame()
    return pd.read_csv(path, parse_dates=["date"])


def compute_streaks(df: pd.DataFrame) -> pd.DataFrame:
    """计算每日涨跌方向及连续天数。"""
    df = df.copy()
    df["change"] = df["close"].pct_change()
    df["direction"] = df["change"].apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))

    # 当前连续天数
    streak = []
    cur = 0
    prev_dir = 0
    for d in df["direction"]:
        if d == prev_dir and d != 0:
            cur += 1
        else:
            cur = 1 if d != 0 else 0
        streak.append(cur)
        prev_dir = d
    df["streak"] = streak
    return df


def max_streak(df: pd.DataFrame, direction_value: int) -> tuple[int, pd.Timestamp]:
    """返回最大连续涨/跌天数及起止日期。"""
    sub = df[df["direction"] == direction_value]
    if sub.empty:
        return 0, pd.NaT
    best_idx = sub["streak"].idxmax()
    best_len = sub.loc[best_idx, "streak"]
    end_date = sub.loc[best_idx, "date"]
    start_date = sub.loc[best_idx - best_len + 1, "date"] if best_len > 0 else end_date
    return best_len, start_date, end_date


def current_streak(df: pd.DataFrame) -> tuple[int, int]:
    """返回当前连续涨/跌天数及方向 (1=涨, -1=跌, 0=平)。"""
    last_dir = df.iloc[-1]["direction"]
    last_streak = df.iloc[-1]["streak"]
    return last_streak, last_dir


def run(symbol: str, interval: str, top_n: int = 5):
    df = load_data(symbol, interval)
    if df.empty:
        return
    df = compute_streaks(df)

    print(f"\n{'='*50}")
    print(f"  {symbol} ({interval}) 连续涨跌分析")
    print(f"  数据范围: {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()}  共 {len(df)} 条")
    print(f"{'='*50}")

    # 最大连续涨跌
    for direction_value, label in [(1, "上涨"), (-1, "下跌")]:
        length, start, end = max_streak(df, direction_value)
        print(f"\n最大连续{label}: {length} 天  ({start.date()} ~ {end.date()})")

    # 当前连续状态
    cur_len, cur_dir = current_streak(df)
    dir_label = {1: "上涨", -1: "下跌", 0: "持平"}.get(cur_dir, "未知")
    print(f"\n当前状态: 连续{dir_label} {cur_len} 天")

    # Top N 连续涨跌记录
    for direction_value, label in [(1, "上涨"), (-1, "下跌")]:
        sub = df[df["direction"] == direction_value].nlargest(top_n, "streak")
        print(f"\nTop {top_n} 连续{label}:")
        for _, row in sub.iterrows():
            s = int(row["streak"])
            end_date = row["date"]
            start_idx = row.name - s + 1
            start_date = df.loc[start_idx, "date"]
            change_pct = (df.loc[row.name, "close"] / df.loc[start_idx, "close"] - 1) * 100
            print(f"  {s:>3} 天  {start_date.date()} ~ {end_date.date()}  累计涨跌幅: {change_pct:+.2f}%")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="连续涨跌分析")
    parser.add_argument("-s", "--symbol", default="WLDUSDT", help="交易对")
    parser.add_argument("-i", "--interval", default="1d", help="K线周期")
    parser.add_argument("-n", "--top", type=int, default=5, help="显示 Top N 记录")
    args = parser.parse_args()
    run(args.symbol, args.interval, args.top)
