import argparse
import os
import sys
from collections import Counter

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from analysis.streak_analysis import compute_streaks, format_time, load_data, streak_unit


DIRECTION_LABELS = {
    1: "上涨",
    -1: "下跌",
    0: "持平",
}
DIRECTION_ORDER = [1, -1, 0]


def direction_of_return(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def format_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def format_signed_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def posterior_probs(counts: Counter, prior_counts: Counter, prior_strength: float) -> dict[int, float]:
    """Use a Dirichlet-Multinomial update for up/down/flat probabilities."""
    prior_total = sum(prior_counts.values())
    if prior_total == 0:
        prior_probs = {direction: 1 / len(DIRECTION_ORDER) for direction in DIRECTION_ORDER}
    else:
        smoothed_total = prior_total + len(DIRECTION_ORDER)
        prior_probs = {
            direction: (prior_counts.get(direction, 0) + 1) / smoothed_total
            for direction in DIRECTION_ORDER
        }

    sample_total = sum(counts.values())
    denominator = sample_total + prior_strength
    return {
        direction: (counts.get(direction, 0) + prior_probs[direction] * prior_strength) / denominator
        for direction in DIRECTION_ORDER
    }


def raw_probs(counts: Counter) -> dict[int, float]:
    total = sum(counts.values())
    if total == 0:
        return {direction: 0.0 for direction in DIRECTION_ORDER}
    return {direction: counts.get(direction, 0) / total for direction in DIRECTION_ORDER}


def matching_indices(df: pd.DataFrame, direction: int, streak: int, match: str) -> list[int]:
    if match == "at-least":
        mask = (df["direction"] == direction) & (df["streak"] >= streak)
    else:
        mask = (df["direction"] == direction) & (df["streak"] == streak)
    return [int(idx) for idx in df.index[mask]]


def count_next_directions(df: pd.DataFrame, indices: list[int]) -> Counter:
    counts: Counter = Counter()
    last_idx = len(df) - 1
    for idx in indices:
        if idx < last_idx:
            counts[int(df.loc[idx + 1, "direction"])] += 1
    return counts


def count_all_next_directions(df: pd.DataFrame) -> Counter:
    counts: Counter = Counter()
    for idx in range(len(df) - 1):
        counts[int(df.loc[idx + 1, "direction"])] += 1
    return counts


def future_returns(df: pd.DataFrame, indices: list[int], horizon: int) -> list[float]:
    returns = []
    last_idx = len(df) - 1
    for idx in indices:
        future_idx = idx + horizon
        if future_idx <= last_idx:
            current_close = df.loc[idx, "close"]
            future_close = df.loc[future_idx, "close"]
            returns.append(future_close / current_close - 1)
    return returns


def future_direction_counts(returns: list[float]) -> Counter:
    return Counter(direction_of_return(value) for value in returns)


def all_future_returns(df: pd.DataFrame, horizon: int) -> list[float]:
    returns = []
    for idx in range(len(df) - horizon):
        current_close = df.loc[idx, "close"]
        future_close = df.loc[idx + horizon, "close"]
        returns.append(future_close / current_close - 1)
    return returns


def print_probability_block(title: str, counts: Counter, priors: Counter, prior_strength: float):
    sample_count = sum(counts.values())
    raw = raw_probs(counts)
    posterior = posterior_probs(counts, priors, prior_strength)

    print(f"\n{title}")
    print(f"样本数: {sample_count}")
    for direction in DIRECTION_ORDER:
        label = DIRECTION_LABELS[direction]
        print(
            f"  {label}: {counts.get(direction, 0):>4} 次 | "
            f"原始频率 {format_pct(raw[direction]):>7} | "
            f"贝叶斯后验 {format_pct(posterior[direction]):>7}"
        )


def describe_future_horizon(
    df: pd.DataFrame,
    indices: list[int],
    horizon: int,
    prior_strength: float,
):
    returns = future_returns(df, indices, horizon)
    counts = future_direction_counts(returns)
    priors = future_direction_counts(all_future_returns(df, horizon))
    print_probability_block(f"未来 {horizon} 根K线收益方向", counts, priors, prior_strength)

    if returns:
        series = pd.Series(returns)
        print(
            f"  平均收益: {format_signed_pct(series.mean())} | "
            f"中位数收益: {format_signed_pct(series.median())}"
        )


def run(
    symbol: str,
    interval: str,
    horizons: list[int],
    match: str,
    prior_strength: float,
    min_samples: int,
):
    df = load_data(symbol, interval)
    if df.empty:
        return

    df = compute_streaks(df)
    current = df.iloc[-1]
    current_direction = int(current["direction"])
    current_streak = int(current["streak"])
    unit = streak_unit(interval)

    indices = matching_indices(df, current_direction, current_streak, match)
    next_counts = count_next_directions(df, indices)
    next_priors = count_all_next_directions(df)
    next_sample_count = sum(next_counts.values())

    match_label = "至少达到" if match == "at-least" else "精确等于"
    direction_label = DIRECTION_LABELS[current_direction]

    print(f"\n{'=' * 60}")
    print(f"  {symbol} ({interval}) 当前状态贝叶斯概率分析")
    print(f"  数据范围: {format_time(df['date'].iloc[0], interval)} ~ {format_time(df['date'].iloc[-1], interval)}")
    print(f"{'=' * 60}")
    print(f"\n当前状态: 连续{direction_label} {current_streak} {unit}")
    print(f"匹配口径: 历史状态 {match_label} 连续{direction_label} {current_streak} {unit}")
    print(f"先验强度: {prior_strength:g}")

    print_probability_block("下一根K线方向", next_counts, next_priors, prior_strength)

    if next_sample_count < min_samples:
        print(f"\n提示: 当前状态样本数 {next_sample_count} < {min_samples}，后验概率已向整体基准概率收缩。")

    for horizon in horizons:
        describe_future_horizon(df, indices, horizon, prior_strength)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="基于当前连续涨跌状态的贝叶斯概率分析")
    parser.add_argument("-s", "--symbol", default="WLDUSDT", help="交易对")
    parser.add_argument("-i", "--interval", default="4h", help="K线周期")
    parser.add_argument("--horizons", type=int, nargs="+", default=[1, 3, 6, 12],
                        help="统计未来 N 根K线后的收益方向")
    parser.add_argument("--match", choices=["exact", "at-least"], default="exact",
                        help="历史状态匹配口径: exact=精确连续数量, at-least=至少达到该连续数量")
    parser.add_argument("--prior-strength", type=float, default=20,
                        help="贝叶斯先验强度，数值越大越向整体基准概率收缩")
    parser.add_argument("--min-samples", type=int, default=30,
                        help="样本数低于该值时输出提示")
    args = parser.parse_args()

    run(args.symbol, args.interval, args.horizons, args.match, args.prior_strength, args.min_samples)
