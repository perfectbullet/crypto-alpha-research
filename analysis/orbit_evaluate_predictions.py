import argparse
import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import datetime

import numpy as np
import pandas as pd


DEFAULT_BINS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]


@dataclass
class PointMetrics:
    rows: int
    mae_log_return: float
    rmse_log_return: float
    mae_simple_return: float
    rmse_simple_return: float
    mean_error_log_return: float
    correlation_log_return: float | None
    directional_accuracy: float
    actual_down_rate: float
    predicted_down_rate: float


@dataclass
class ProbabilityMetrics:
    event_name: str
    risk_threshold_simple_return: float
    brier_score: float
    log_loss: float
    average_predicted_probability: float
    actual_event_rate: float
    calibration_mae: float
    top_10pct_event_rate: float | None
    top_20pct_event_rate: float | None
    top_30pct_event_rate: float | None
    top_10pct_lift: float | None
    top_20pct_lift: float | None
    top_30pct_lift: float | None


@dataclass
class SignalUtilityMetrics:
    fee_rate: float
    long_threshold: float
    short_threshold: float
    long_count: int
    short_count: int
    flat_count: int
    trade_count: int
    average_signal_return: float | None
    average_long_return: float | None
    average_short_return: float | None
    hit_rate: float | None
    cumulative_signal_return: float | None
    max_drawdown: float | None
    always_long_average_return: float
    always_long_cumulative_return: float


def read_predictions(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"预测 CSV 不存在: {path}")
    df = pd.read_csv(path)
    required = ["date", "prediction"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"预测 CSV 缺少必要字段: {missing}")

    target_cols = [col for col in df.columns if col.startswith("future_log_return_")]
    if not target_cols:
        raise ValueError("预测 CSV 中找不到 future_log_return_* 目标列")
    if len(target_cols) > 1:
        raise ValueError(f"预测 CSV 中存在多个 future_log_return_* 目标列，无法自动判断: {target_cols}")

    target_col = target_cols[0]
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["actual_log_return"] = pd.to_numeric(df[target_col], errors="coerce")
    df["predicted_log_return"] = pd.to_numeric(df["prediction"], errors="coerce")
    df["actual_simple_return"] = np.expm1(df["actual_log_return"])
    df["predicted_simple_return_eval"] = np.expm1(df["predicted_log_return"])

    if "risk_prob_return_le_threshold" in df.columns:
        df["risk_prob_return_le_threshold"] = pd.to_numeric(df["risk_prob_return_le_threshold"], errors="coerce")

    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    df.dropna(subset=["date", "actual_log_return", "predicted_log_return", "actual_simple_return"], inplace=True)
    df.sort_values("date", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def safe_corr(a: pd.Series, b: pd.Series) -> float | None:
    if len(a) < 3 or a.std() == 0 or b.std() == 0:
        return None
    value = float(a.corr(b))
    return value if math.isfinite(value) else None


def compute_point_metrics(df: pd.DataFrame) -> PointMetrics:
    err_log = df["predicted_log_return"] - df["actual_log_return"]
    err_simple = df["predicted_simple_return_eval"] - df["actual_simple_return"]
    actual_sign = np.sign(df["actual_log_return"])
    pred_sign = np.sign(df["predicted_log_return"])
    return PointMetrics(
        rows=len(df),
        mae_log_return=float(err_log.abs().mean()),
        rmse_log_return=float(np.sqrt(np.square(err_log).mean())),
        mae_simple_return=float(err_simple.abs().mean()),
        rmse_simple_return=float(np.sqrt(np.square(err_simple).mean())),
        mean_error_log_return=float(err_log.mean()),
        correlation_log_return=safe_corr(df["actual_log_return"], df["predicted_log_return"]),
        directional_accuracy=float((actual_sign == pred_sign).mean()),
        actual_down_rate=float((df["actual_simple_return"] < 0).mean()),
        predicted_down_rate=float((df["predicted_simple_return_eval"] < 0).mean()),
    )


def binary_log_loss(y: pd.Series, p: pd.Series) -> float:
    eps = 1e-12
    p_clip = p.clip(eps, 1.0 - eps)
    return float(-(y * np.log(p_clip) + (1.0 - y) * np.log(1.0 - p_clip)).mean())


def build_calibration_table(df: pd.DataFrame, threshold: float, bins: list[float]) -> pd.DataFrame:
    if "risk_prob_return_le_threshold" not in df.columns:
        raise ValueError("预测 CSV 缺少 risk_prob_return_le_threshold，无法评估概率校准")
    out = df.copy()
    out["event"] = (out["actual_simple_return"] <= threshold).astype(int)
    out["prob_bin"] = pd.cut(
        out["risk_prob_return_le_threshold"],
        bins=bins,
        include_lowest=True,
        right=True,
    )
    grouped = out.groupby("prob_bin", observed=False).agg(
        count=("event", "size"),
        mean_predicted_prob=("risk_prob_return_le_threshold", "mean"),
        actual_event_rate=("event", "mean"),
        avg_actual_simple_return=("actual_simple_return", "mean"),
    )
    grouped["calibration_gap"] = grouped["mean_predicted_prob"] - grouped["actual_event_rate"]
    grouped.reset_index(inplace=True)
    grouped["prob_bin"] = grouped["prob_bin"].astype(str)
    return grouped


def top_event_rate(df: pd.DataFrame, threshold: float, top_ratio: float) -> tuple[float | None, float | None]:
    if "risk_prob_return_le_threshold" not in df.columns or len(df) == 0:
        return None, None
    event = df["actual_simple_return"] <= threshold
    base_rate = float(event.mean())
    n = max(1, int(math.ceil(len(df) * top_ratio)))
    top = df.sort_values("risk_prob_return_le_threshold", ascending=False).head(n)
    top_rate = float((top["actual_simple_return"] <= threshold).mean())
    lift = top_rate / base_rate if base_rate > 0 else None
    return top_rate, lift


def compute_probability_metrics(df: pd.DataFrame, threshold: float, calibration: pd.DataFrame) -> ProbabilityMetrics | None:
    if "risk_prob_return_le_threshold" not in df.columns:
        return None
    valid = df.dropna(subset=["risk_prob_return_le_threshold"]).copy()
    if valid.empty:
        return None
    event = (valid["actual_simple_return"] <= threshold).astype(int)
    prob = valid["risk_prob_return_le_threshold"].clip(0, 1)
    top10, lift10 = top_event_rate(valid, threshold, 0.10)
    top20, lift20 = top_event_rate(valid, threshold, 0.20)
    top30, lift30 = top_event_rate(valid, threshold, 0.30)

    cal_valid = calibration.dropna(subset=["mean_predicted_prob", "actual_event_rate"])
    if cal_valid.empty:
        calibration_mae = float("nan")
    else:
        weight = cal_valid["count"] / cal_valid["count"].sum()
        calibration_mae = float((cal_valid["calibration_gap"].abs() * weight).sum())

    return ProbabilityMetrics(
        event_name=f"actual_simple_return <= {threshold}",
        risk_threshold_simple_return=threshold,
        brier_score=float(np.square(prob - event).mean()),
        log_loss=binary_log_loss(event, prob),
        average_predicted_probability=float(prob.mean()),
        actual_event_rate=float(event.mean()),
        calibration_mae=calibration_mae,
        top_10pct_event_rate=top10,
        top_20pct_event_rate=top20,
        top_30pct_event_rate=top30,
        top_10pct_lift=lift10,
        top_20pct_lift=lift20,
        top_30pct_lift=lift30,
    )


def max_drawdown_from_returns(returns: pd.Series) -> float | None:
    if returns.empty:
        return None
    equity = (1.0 + returns).cumprod()
    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    value = float(drawdown.min())
    return value if math.isfinite(value) else None


def compute_signal_utility(
    df: pd.DataFrame,
    long_threshold: float,
    short_threshold: float,
    fee_rate: float,
) -> SignalUtilityMetrics:
    signal = pd.Series(0, index=df.index, dtype=int)
    signal[df["predicted_simple_return_eval"] >= long_threshold] = 1
    signal[df["predicted_simple_return_eval"] <= short_threshold] = -1

    raw_strategy_return = signal * df["actual_simple_return"]
    trade_mask = signal != 0
    strategy_return = raw_strategy_return.copy()
    strategy_return[trade_mask] = strategy_return[trade_mask] - fee_rate

    long_mask = signal == 1
    short_mask = signal == -1
    long_count = int(long_mask.sum())
    short_count = int(short_mask.sum())
    flat_count = int((signal == 0).sum())
    trade_count = long_count + short_count

    if trade_count > 0:
        selected = strategy_return[trade_mask]
        average_signal_return = float(selected.mean())
        hit_rate = float((selected > 0).mean())
        cumulative_signal_return = float((1.0 + strategy_return).prod() - 1.0)
        max_drawdown = max_drawdown_from_returns(strategy_return)
    else:
        average_signal_return = None
        hit_rate = None
        cumulative_signal_return = None
        max_drawdown = None

    return SignalUtilityMetrics(
        fee_rate=fee_rate,
        long_threshold=long_threshold,
        short_threshold=short_threshold,
        long_count=long_count,
        short_count=short_count,
        flat_count=flat_count,
        trade_count=trade_count,
        average_signal_return=average_signal_return,
        average_long_return=float(df.loc[long_mask, "actual_simple_return"].mean()) if long_count > 0 else None,
        average_short_return=float((-df.loc[short_mask, "actual_simple_return"]).mean()) if short_count > 0 else None,
        hit_rate=hit_rate,
        cumulative_signal_return=cumulative_signal_return,
        max_drawdown=max_drawdown,
        always_long_average_return=float(df["actual_simple_return"].mean()),
        always_long_cumulative_return=float((1.0 + df["actual_simple_return"]).prod() - 1.0),
    )


def write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def save_calibration_plot(calibration: pd.DataFrame, output_path: str) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return
    if calibration.empty:
        return
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    labels = calibration["prob_bin"].astype(str)
    x = np.arange(len(labels))
    width = 0.35
    plt.figure(figsize=(10, 5))
    plt.bar(x - width / 2, calibration["mean_predicted_prob"], width, label="mean predicted prob")
    plt.bar(x + width / 2, calibration["actual_event_rate"], width, label="actual event rate")
    plt.xticks(x, labels, rotation=30, ha="right")
    plt.ylabel("probability")
    plt.title("Orbit risk probability calibration")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def parse_bins(raw: str) -> list[float]:
    bins = [float(x.strip()) for x in raw.split(",") if x.strip()]
    if len(bins) < 2:
        raise ValueError("bins 至少需要两个边界")
    if bins != sorted(bins):
        raise ValueError("bins 必须递增")
    return bins


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="评估 Orbit 预测 CSV 的点预测、概率校准和信号效用")
    parser.add_argument("--predictions", required=True, help="orbit_wld_experiment.py 生成的 *_predictions.csv")
    parser.add_argument("--output-dir", default="reports/orbit_eval", help="评估输出目录")
    parser.add_argument("--risk-threshold", type=float, default=None, help="事件阈值，普通收益率；默认从 CSV 的 risk_threshold_simple_return 读取，否则 -0.05")
    parser.add_argument("--bins", default=",".join(str(x) for x in DEFAULT_BINS), help="概率校准分桶边界，默认 0,0.2,0.4,0.6,0.8,1.0")
    parser.add_argument("--long-threshold", type=float, default=0.02, help="预测普通收益率 >= 该值时视为做多信号，默认 2%")
    parser.add_argument("--short-threshold", type=float, default=-0.02, help="预测普通收益率 <= 该值时视为做空信号，默认 -2%")
    parser.add_argument("--fee-rate", type=float, default=0.001, help="每个非空信号扣除的费用/滑点，默认 0.1%")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    df = read_predictions(args.predictions)

    if args.risk_threshold is not None:
        risk_threshold = args.risk_threshold
    elif "risk_threshold_simple_return" in df.columns and df["risk_threshold_simple_return"].notna().any():
        risk_threshold = float(df["risk_threshold_simple_return"].dropna().iloc[0])
    else:
        risk_threshold = -0.05
    if risk_threshold <= -1:
        raise ValueError("risk_threshold 必须大于 -1，因为普通收益率不能低于 -100%")

    bins = parse_bins(args.bins)
    base_name = os.path.splitext(os.path.basename(args.predictions))[0]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_prefix = os.path.join(args.output_dir, f"{base_name}_eval_{timestamp}")

    point = compute_point_metrics(df)
    calibration = build_calibration_table(df, risk_threshold, bins) if "risk_prob_return_le_threshold" in df.columns else pd.DataFrame()
    probability = compute_probability_metrics(df, risk_threshold, calibration) if not calibration.empty else None
    utility = compute_signal_utility(df, args.long_threshold, args.short_threshold, args.fee_rate)

    os.makedirs(args.output_dir, exist_ok=True)
    calibration_path = f"{output_prefix}_calibration.csv"
    summary_path = f"{output_prefix}_summary.json"
    plot_path = f"{output_prefix}_calibration.png"
    if not calibration.empty:
        calibration.to_csv(calibration_path, index=False)
        save_calibration_plot(calibration, plot_path)
    else:
        calibration_path = None
        plot_path = None

    payload = {
        "input_predictions": args.predictions,
        "risk_threshold_simple_return": risk_threshold,
        "point_metrics": asdict(point),
        "probability_metrics": asdict(probability) if probability else None,
        "signal_utility_metrics": asdict(utility),
        "notes": [
            "future return labels overlap when horizon > 1, so signal utility is only a rough diagnostic, not a formal trade backtest.",
            "risk probability is useful only if calibration bins show predicted probability rises with actual event rate.",
            "point prediction error alone is not enough; compare calibration, lift, and utility against simple baselines.",
        ],
    }
    write_json(summary_path, payload)

    print("\nOrbit 预测评估完成")
    print(f"预测文件: {args.predictions}")
    print(f"摘要 JSON: {summary_path}")
    if calibration_path:
        print(f"校准分桶 CSV: {calibration_path}")
    if plot_path:
        print(f"校准图: {plot_path}")
    print("\n核心指标:")
    print(f"  rows: {point.rows}")
    print(f"  MAE(simple return): {point.mae_simple_return:.4%}")
    print(f"  RMSE(simple return): {point.rmse_simple_return:.4%}")
    print(f"  directional accuracy: {point.directional_accuracy:.2%}")
    print(f"  correlation(log return): {point.correlation_log_return}")
    if probability:
        print(f"  event: {probability.event_name}")
        print(f"  brier score: {probability.brier_score:.6f}")
        print(f"  log loss: {probability.log_loss:.6f}")
        print(f"  avg predicted prob: {probability.average_predicted_probability:.2%}")
        print(f"  actual event rate: {probability.actual_event_rate:.2%}")
        print(f"  calibration MAE: {probability.calibration_mae:.2%}")
        print(f"  top 20% event rate/lift: {probability.top_20pct_event_rate} / {probability.top_20pct_lift}")
    print(f"  signal trades: {utility.trade_count} long={utility.long_count} short={utility.short_count} flat={utility.flat_count}")
    print(f"  signal avg return: {utility.average_signal_return}")
    print(f"  signal hit rate: {utility.hit_rate}")
    print(f"  signal cumulative return: {utility.cumulative_signal_return}")
    print("\n注意：horizon>1 时相邻标签高度重叠，signal utility 只能用于筛选参数，不能当正式交易回测。")


if __name__ == "__main__":
    main()
