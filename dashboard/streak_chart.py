import argparse
import os
from datetime import timedelta

import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.font_manager as fm
import pandas as pd

FONT_PATH = os.path.expanduser("~/.fonts/SimHei.ttf")
if os.path.exists(FONT_PATH):
    fm.fontManager.addfont(FONT_PATH)
    mpl.rcParams["font.sans-serif"] = ["SimHei"] + mpl.rcParams["font.sans-serif"]
mpl.rcParams["axes.unicode_minus"] = False

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")


TIME_RANGES = {
    "1m": timedelta(days=30),
    "3m": timedelta(days=90),
    "6m": timedelta(days=180),
    "1y": timedelta(days=365),
    "all": None,
}


def load_data(symbol: str, interval: str, period: str = "all") -> pd.DataFrame:
    path = os.path.join(DATA_DIR, f"{symbol}_{interval}.csv")
    if not os.path.exists(path):
        print(f"数据文件不存在: {path}\n请先运行: python collectors/get_wld_data.py -s {symbol} -i {interval}")
        return pd.DataFrame()
    df = pd.read_csv(path, parse_dates=["date"])
    delta = TIME_RANGES.get(period)
    if delta is not None:
        cutoff = df["date"].max() - delta
        df = df.loc[df["date"] >= cutoff].reset_index(drop=True)
    return df


def compute_streaks(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["change"] = df["close"].pct_change()
    df["direction"] = df["change"].apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))

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


def get_streak_spans(df: pd.DataFrame):
    """提取每段连续涨跌的起止索引。"""
    spans = []
    i = 0
    while i < len(df):
        d = df.iloc[i]["direction"]
        if d == 0:
            i += 1
            continue
        j = i + 1
        while j < len(df) and df.iloc[j]["direction"] == d:
            j += 1
        spans.append((i, j, int(d)))
        i = j
    return spans


PERIOD_LABELS = {"1m": "最近1个月", "3m": "最近3个月", "6m": "最近6个月", "1y": "最近1年", "all": "全部"}


def plot_streak(df: pd.DataFrame, symbol: str, interval: str, period: str = "all"):
    period_label = PERIOD_LABELS.get(period, period)
    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True,
                             gridspec_kw={"height_ratios": [3, 2, 1]})
    fig.suptitle(f"{symbol} ({interval}) 连续涨跌分析 — {period_label}", fontsize=16, fontweight="bold")

    spans = get_streak_spans(df)
    colors = {1: "#e74c3c", -1: "#2ecc71"}  # 红=涨 绿=跌

    # ---- 1. 收盘价 + 涨跌着色 ----
    ax1 = axes[0]
    ax1.plot(df["date"], df["close"], color="#333", linewidth=0.8, zorder=2)
    for start, end, d in spans:
        ax1.axvspan(df["date"].iloc[start], df["date"].iloc[end - 1],
                    alpha=0.12, color=colors[d])
    ax1.set_ylabel("收盘价")
    ax1.grid(alpha=0.3)

    # ---- 2. 每日涨跌幅柱状图 ----
    ax2 = axes[1]
    bar_colors = df["direction"].map({1: "#e74c3c", -1: "#2ecc71", 0: "#bbb"})
    ax2.bar(df["date"], df["change"] * 100, color=bar_colors, width=0.8)
    ax2.axhline(0, color="#333", linewidth=0.5)
    ax2.set_ylabel("涨跌幅 (%)")
    ax2.grid(alpha=0.3)

    # ---- 3. 连续天数 ----
    ax3 = axes[2]
    streak_colors = df["direction"].map({1: "#e74c3c", -1: "#2ecc71", 0: "#bbb"})
    ax3.bar(df["date"], df["streak"], color=streak_colors, width=0.8)
    # 标注最大连续涨/跌
    for d, label in [(1, "最大连续上涨"), (-1, "最大连续下跌")]:
        sub = df[df["direction"] == d]
        if sub.empty:
            continue
        best_idx = sub["streak"].idxmax()
        best_val = sub.loc[best_idx, "streak"]
        ax3.annotate(f"{label}: {best_val}天",
                     xy=(df.loc[best_idx, "date"], best_val),
                     xytext=(0, 10), textcoords="offset points",
                     fontsize=9, fontweight="bold", ha="center",
                     color=colors[d],
                     arrowprops=dict(arrowstyle="->", color=colors[d]))
    ax3.set_ylabel("连续天数")
    ax3.grid(alpha=0.3)

    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax3.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    plt.xticks(rotation=45)

    plt.tight_layout()
    out_dir = os.path.join(BASE_DIR, "reports")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{symbol}_{interval}_{period}_streak.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"图表已保存: {out_path}")
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="连续涨跌可视化")
    parser.add_argument("-s", "--symbol", default="WLDUSDT")
    parser.add_argument("-i", "--interval", default="1d")
    parser.add_argument("-p", "--period", default="all",
                        choices=["1m", "3m", "6m", "1y", "all"],
                        help="时间范围: 1m=1个月, 3m=3个月, 6m=6个月, 1y=1年, all=全部")
    args = parser.parse_args()

    df = load_data(args.symbol, args.interval, args.period)
    if not df.empty:
        df = compute_streaks(df)
        plot_streak(df, args.symbol, args.interval, args.period)
