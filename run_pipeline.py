import argparse
import os

from analysis.streak_analysis import run as run_analysis
from analysis.state_probability import run as run_state_probability
from collectors.get_wld_data import DATA_DIR, get_binance_klines
from dashboard.streak_chart import compute_streaks, load_data, plot_streak


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def save_data(symbol: str, interval: str, total: int) -> str:
    df = get_binance_klines(symbol, interval, total)
    if df.empty:
        raise RuntimeError("未获取到数据，流水线终止")

    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, f"{symbol}_{interval}.csv")
    df.to_csv(out_path, index=False)
    print(f"[1/4] 数据已保存: {out_path}")
    print(f"      数据范围(UTC): {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}，共 {len(df)} 条")
    return out_path


def run_pipeline(
    symbol: str,
    interval: str,
    total: int,
    period: str,
    top: int,
    horizons: list[int],
    match: str,
    prior_strength: float,
    min_samples: int,
):
    print(f"开始流水线: symbol={symbol}, interval={interval}, total={total}, period={period}")

    save_data(symbol, interval, total)

    print("\n[2/4] 连续涨跌分析")
    run_analysis(symbol, interval, top)

    print("\n[3/4] 当前状态贝叶斯概率推演")
    run_state_probability(symbol, interval, horizons, match, prior_strength, min_samples)

    print("\n[4/4] 生成图表报告")
    df = load_data(symbol, interval, period)
    if df.empty:
        raise RuntimeError("图表数据为空，流水线终止")
    df = compute_streaks(df)
    plot_streak(df, symbol, interval, period)

    report_path = os.path.join(BASE_DIR, "reports", f"{symbol}_{interval}_{period}_streak.png")
    print(f"\n流水线完成: {report_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="运行数据采集、连续涨跌分析、贝叶斯概率推演和图表生成流水线")
    parser.add_argument("-s", "--symbol", default="WLDUSDT", help="交易对")
    parser.add_argument("-i", "--interval", default="1d", help="K线周期")
    parser.add_argument("-n", "--total", type=int, default=1000, help="采集总条数")
    parser.add_argument("-p", "--period", default="all",
                        choices=["1m", "3m", "6m", "1y", "all"],
                        help="图表时间范围")
    parser.add_argument("-t", "--top", type=int, default=10, help="分析输出 Top N 连续记录")
    parser.add_argument("--horizons", type=int, nargs="+", default=[1, 3, 6, 12],
                        help="贝叶斯概率推演统计未来 N 根K线后的收益方向")
    parser.add_argument("--match", choices=["exact", "at-least"], default="exact",
                        help="历史状态匹配口径")
    parser.add_argument("--prior-strength", type=float, default=20,
                        help="贝叶斯先验强度")
    parser.add_argument("--min-samples", type=int, default=30,
                        help="样本数低于该值时输出提示")
    args = parser.parse_args()

    run_pipeline(
        args.symbol,
        args.interval,
        args.total,
        args.period,
        args.top,
        args.horizons,
        args.match,
        args.prior_strength,
        args.min_samples,
    )
