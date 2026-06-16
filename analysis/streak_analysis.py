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
    """计算每根 K 线涨跌方向及连续数量。"""
    df = df.copy()
    df["change"] = df["close"].pct_change()
    df["direction"] = df["change"].apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))

    # 当前连续 K 线数量
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


def streak_unit(interval: str) -> str:
    """返回当前周期下连续数量的展示单位。"""
    return "天" if interval == "1d" else f"根K线({interval})"


def format_time(value: pd.Timestamp, interval: str) -> str:
    """日线只显示日期，日内周期显示到分钟。"""
    if pd.isna(value):
        return "N/A"
    if interval == "1d":
        return value.strftime("%Y-%m-%d")
    return value.strftime("%Y-%m-%d %H:%M")


def streak_segments(df: pd.DataFrame, direction_value: int) -> list[dict]:
    """提取完整连续涨/跌区间，避免 Top 记录重复列出同一区间的中间状态。"""
    segments = []
    i = 0
    while i < len(df):
        if df.iloc[i]["direction"] != direction_value:
            i += 1
            continue

        start_idx = i
        while i + 1 < len(df) and df.iloc[i + 1]["direction"] == direction_value:
            i += 1
        end_idx = i

        start_close = df.iloc[start_idx]["close"]
        end_close = df.iloc[end_idx]["close"]
        change_pct = (end_close / start_close - 1) * 100
        segments.append({
            "length": end_idx - start_idx + 1,
            "start": df.iloc[start_idx]["date"],
            "end": df.iloc[end_idx]["date"],
            "change_pct": change_pct,
        })
        i += 1
    return segments


def max_streak(df: pd.DataFrame, direction_value: int) -> dict | None:
    """返回最大连续涨/跌区间。"""
    segments = streak_segments(df, direction_value)
    if not segments:
        return None
    return max(segments, key=lambda item: item["length"])


def current_streak(df: pd.DataFrame) -> tuple[int, int]:
    """返回当前连续涨/跌数量及方向 (1=涨, -1=跌, 0=平)。"""
    last_dir = df.iloc[-1]["direction"]
    last_streak = df.iloc[-1]["streak"]
    return int(last_streak), int(last_dir)


def run(symbol: str, interval: str, top_n: int = 5):
    df = load_data(symbol, interval)
    if df.empty:
        return
    df = compute_streaks(df)
    unit = streak_unit(interval)

    print(f"\n{'='*50}")
    print(f"  {symbol} ({interval}) 连续涨跌分析")
    print(
        f"  数据范围: {format_time(df['date'].iloc[0], interval)} ~ "
        f"{format_time(df['date'].iloc[-1], interval)}  共 {len(df)} 条"
    )
    print(f"{'='*50}")

    # 最大连续涨跌
    for direction_value, label in [(1, "上涨"), (-1, "下跌")]:
        best = max_streak(df, direction_value)
        if best is None:
            print(f"\n最大连续{label}: 0 {unit}")
            continue
        print(
            f"\n最大连续{label}: {best['length']} {unit}  "
            f"({format_time(best['start'], interval)} ~ {format_time(best['end'], interval)})"
        )

    # 当前连续状态
    cur_len, cur_dir = current_streak(df)
    dir_label = {1: "上涨", -1: "下跌", 0: "持平"}.get(cur_dir, "未知")
    print(f"\n当前状态: 连续{dir_label} {cur_len} {unit}")

    # Top N 连续涨跌记录
    for direction_value, label in [(1, "上涨"), (-1, "下跌")]:
        segments = sorted(
            streak_segments(df, direction_value),
            key=lambda item: item["length"],
            reverse=True,
        )[:top_n]
        print(f"\nTop {top_n} 连续{label}:")
        for item in segments:
            print(
                f"  {item['length']:>3} {unit}  "
                f"{format_time(item['start'], interval)} ~ {format_time(item['end'], interval)}  "
                f"累计涨跌幅: {item['change_pct']:+.2f}%"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="连续涨跌分析")
    parser.add_argument("-s", "--symbol", default="WLDUSDT", help="交易对")
    parser.add_argument("-i", "--interval", default="1d", help="K线周期")
    parser.add_argument("-n", "--top", type=int, default=5, help="显示 Top N 记录")
    args = parser.parse_args()
    run(args.symbol, args.interval, args.top)
