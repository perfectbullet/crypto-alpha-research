import argparse
import json
import math
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Iterable

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


REQUIRED_COLS = ["date", "open", "high", "low", "close", "volume", "quote_volume", "trades"]
DEFAULT_REGRESSORS = [
    "log_return_1h",
    "return_4h",
    "return_24h",
    "volatility_24h",
    "volume_ratio_24h",
    "quote_volume_ratio_24h",
    "trades_ratio_24h",
    "range_position_168h",
    "drawdown_from_168h_high",
    "ma_gap_24h",
    "ma_gap_168h",
    "candle_body_pct",
    "high_low_range_pct",
]


@dataclass
class ExperimentConfig:
    input: str
    output_dir: str
    horizon: int
    test_size: int
    max_rows: int
    seasonality: int
    estimator: str
    seed: int
    risk_threshold: float
    use_regressors: bool


@dataclass
class ExperimentMetrics:
    rows_used: int
    train_rows: int
    test_rows: int
    horizon: int
    target_col: str
    estimator: str
    seasonality: int
    mae_log_return: float
    rmse_log_return: float
    mae_simple_return: float
    directional_accuracy: float
    actual_down_rate: float
    predicted_down_rate: float
    average_predicted_risk_prob: float
    high_risk_actual_down_rate: float | None
    high_risk_count: int
    interval_coverage: float | None
    risk_method: str
    prediction_col: str
    lower_col: str | None
    upper_col: str | None


def load_klines(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"数据文件不存在: {path}")

    df = pd.read_csv(path)
    missing = [col for col in REQUIRED_COLS if col not in df.columns]
    if missing:
        raise ValueError(f"CSV 缺少必要字段: {missing}")

    df = df[REQUIRED_COLS].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df.dropna(subset=["date"], inplace=True)
    for col in REQUIRED_COLS:
        if col == "date":
            continue
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.dropna(subset=[col for col in REQUIRED_COLS if col != "date"], inplace=True)
    df.sort_values("date", inplace=True)
    df.drop_duplicates(subset=["date"], keep="last", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def safe_ratio(value: pd.Series, base: pd.Series) -> pd.Series:
    return value / base.replace(0, np.nan)


def add_features(df: pd.DataFrame, horizon: int) -> tuple[pd.DataFrame, str]:
    """Build OHLCV-derived state features and a future-return target.

    Target design:
    - At timestamp t, target is log(close[t + horizon] / close[t]).
    - This turns the problem into predicting future H-hour return distribution.
    """
    out = df.copy()
    out["log_close"] = np.log(out["close"])
    out["log_return_1h"] = out["log_close"].diff()
    out["return_4h"] = out["close"].pct_change(4)
    out["return_24h"] = out["close"].pct_change(24)
    out["volatility_24h"] = out["log_return_1h"].rolling(24).std()

    volume_ma24 = out["volume"].rolling(24).mean()
    quote_volume_ma24 = out["quote_volume"].rolling(24).mean()
    trades_ma24 = out["trades"].rolling(24).mean()
    out["volume_ratio_24h"] = safe_ratio(out["volume"], volume_ma24)
    out["quote_volume_ratio_24h"] = safe_ratio(out["quote_volume"], quote_volume_ma24)
    out["trades_ratio_24h"] = safe_ratio(out["trades"], trades_ma24)

    rolling_high_168 = out["high"].rolling(168).max()
    rolling_low_168 = out["low"].rolling(168).min()
    out["range_position_168h"] = (out["close"] - rolling_low_168) / (rolling_high_168 - rolling_low_168).replace(0, np.nan)
    out["drawdown_from_168h_high"] = safe_ratio(out["close"], rolling_high_168) - 1

    ma24 = out["close"].rolling(24).mean()
    ma168 = out["close"].rolling(168).mean()
    out["ma_gap_24h"] = safe_ratio(out["close"], ma24) - 1
    out["ma_gap_168h"] = safe_ratio(out["close"], ma168) - 1

    out["candle_body_pct"] = (out["close"] - out["open"]) / out["open"].replace(0, np.nan)
    out["high_low_range_pct"] = (out["high"] - out["low"]) / out["open"].replace(0, np.nan)

    target_col = f"future_log_return_{horizon}h"
    out[target_col] = np.log(out["close"].shift(-horizon) / out["close"])

    return out, target_col


def prepare_model_frame(
    df: pd.DataFrame,
    horizon: int,
    max_rows: int,
    use_regressors: bool,
) -> tuple[pd.DataFrame, str, list[str]]:
    featured, target_col = add_features(df, horizon)
    needed = ["date", target_col]
    regressors = [col for col in DEFAULT_REGRESSORS if col in featured.columns] if use_regressors else []
    needed.extend(regressors)

    model_df = featured[needed].replace([np.inf, -np.inf], np.nan).dropna().copy()
    if max_rows > 0 and len(model_df) > max_rows:
        model_df = model_df.tail(max_rows).reset_index(drop=True)
    else:
        model_df.reset_index(drop=True, inplace=True)
    return model_df, target_col, regressors


def split_train_test(df: pd.DataFrame, test_size: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    if test_size <= 0:
        raise ValueError("test_size 必须大于 0")
    if len(df) <= test_size + 50:
        raise ValueError(f"可用样本太少: rows={len(df)}, test_size={test_size}")
    train_df = df.iloc[:-test_size].copy()
    test_df = df.iloc[-test_size:].copy()
    return train_df, test_df


def find_prediction_col(pred_df: pd.DataFrame, target_col: str) -> str:
    candidates = ["prediction", f"{target_col}_prediction", "pred", "mean", "forecast"]
    for col in candidates:
        if col in pred_df.columns:
            return col
    numeric_cols = [col for col in pred_df.columns if pd.api.types.is_numeric_dtype(pred_df[col])]
    if len(numeric_cols) == 1:
        return numeric_cols[0]
    raise ValueError(f"无法识别预测列。predicted_df columns={list(pred_df.columns)}")


def parse_prediction_quantile_columns(pred_df: pd.DataFrame) -> dict[float, str]:
    quantiles: dict[float, str] = {}
    for col in pred_df.columns:
        match = re.fullmatch(r"prediction[_-]([0-9]+(?:\.[0-9]+)?)", col)
        if match:
            quantiles[float(match.group(1))] = col
    return quantiles


def choose_interval_cols(pred_df: pd.DataFrame) -> tuple[str | None, str | None, float | None, float | None]:
    quantiles = parse_prediction_quantile_columns(pred_df)
    lower_qs = sorted(q for q in quantiles if q < 50)
    upper_qs = sorted(q for q in quantiles if q > 50)
    if lower_qs and upper_qs:
        lower_q = lower_qs[0]
        upper_q = upper_qs[-1]
        return quantiles[lower_q], quantiles[upper_q], lower_q, upper_q

    lower_candidates = ["prediction_lower", "lower", "yhat_lower", "pred_lower"]
    upper_candidates = ["prediction_upper", "upper", "yhat_upper", "pred_upper"]
    lower_col = next((col for col in lower_candidates if col in pred_df.columns), None)
    upper_col = next((col for col in upper_candidates if col in pred_df.columns), None)
    return lower_col, upper_col, None, None


def common_quantile_z(q: float) -> float | None:
    lookup = {
        0.5: -2.57583,
        1.0: -2.32635,
        2.5: -1.95996,
        5.0: -1.64485,
        10.0: -1.28155,
        16.0: -0.99446,
        25.0: -0.67449,
        75.0: 0.67449,
        84.0: 0.99446,
        90.0: 1.28155,
        95.0: 1.64485,
        97.5: 1.95996,
        99.0: 2.32635,
        99.5: 2.57583,
    }
    return lookup.get(round(float(q), 1)) or lookup.get(round(float(q), 0))


def normal_cdf(x: pd.Series | np.ndarray | float) -> pd.Series | np.ndarray | float:
    return 0.5 * (1.0 + np.vectorize(math.erf)(np.asarray(x) / math.sqrt(2.0)))


def attach_risk_probability(
    pred_df: pd.DataFrame,
    train_df: pd.DataFrame,
    target_col: str,
    prediction_col: str,
    lower_col: str | None,
    upper_col: str | None,
    lower_q: float | None,
    upper_q: float | None,
    risk_threshold: float,
) -> tuple[pd.DataFrame, str]:
    out = pred_df.copy()
    log_threshold = math.log1p(risk_threshold)

    sigma = None
    risk_method = "normal_approx_from_train_target_vol"

    if lower_col and upper_col and lower_q is not None and upper_q is not None:
        z_lower = common_quantile_z(lower_q)
        z_upper = common_quantile_z(upper_q)
        if z_lower is not None and z_upper is not None and z_upper > z_lower:
            sigma = (out[upper_col] - out[lower_col]) / (z_upper - z_lower)
            sigma = sigma.abs().replace(0, np.nan)
            risk_method = f"normal_approx_from_orbit_interval_{lower_q:g}_{upper_q:g}"

    if sigma is None:
        window = min(168, len(train_df))
        sigma_value = float(train_df[target_col].tail(window).std())
        if not math.isfinite(sigma_value) or sigma_value <= 0:
            sigma_value = float(train_df[target_col].std())
        if not math.isfinite(sigma_value) or sigma_value <= 0:
            sigma_value = 1e-6
        sigma = sigma_value

    z = (log_threshold - out[prediction_col]) / sigma
    out["risk_prob_return_le_threshold"] = normal_cdf(z)
    out["risk_threshold_simple_return"] = risk_threshold
    out["predicted_simple_return"] = np.expm1(out[prediction_col])
    return out, risk_method


def compute_metrics(
    pred_df: pd.DataFrame,
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    target_col: str,
    prediction_col: str,
    lower_col: str | None,
    upper_col: str | None,
    risk_method: str,
    config: ExperimentConfig,
) -> ExperimentMetrics:
    merged = pred_df.copy()
    if target_col not in merged.columns:
        merged = merged.merge(test_df[["date", target_col]], on="date", how="left")

    actual = merged[target_col]
    pred = merged[prediction_col]
    error = pred - actual
    actual_simple = np.expm1(actual)
    pred_simple = np.expm1(pred)

    risk_prob = merged["risk_prob_return_le_threshold"]
    high_risk_mask = risk_prob >= 0.60
    high_risk_count = int(high_risk_mask.sum())
    high_risk_actual_down_rate = None
    if high_risk_count > 0:
        high_risk_actual_down_rate = float((actual_simple[high_risk_mask] < 0).mean())

    interval_coverage = None
    if lower_col and upper_col and lower_col in merged.columns and upper_col in merged.columns:
        interval_coverage = float(((actual >= merged[lower_col]) & (actual <= merged[upper_col])).mean())

    return ExperimentMetrics(
        rows_used=len(train_df) + len(test_df),
        train_rows=len(train_df),
        test_rows=len(test_df),
        horizon=config.horizon,
        target_col=target_col,
        estimator=config.estimator,
        seasonality=config.seasonality,
        mae_log_return=float(error.abs().mean()),
        rmse_log_return=float(np.sqrt(np.square(error).mean())),
        mae_simple_return=float((pred_simple - actual_simple).abs().mean()),
        directional_accuracy=float((np.sign(pred) == np.sign(actual)).mean()),
        actual_down_rate=float((actual_simple < 0).mean()),
        predicted_down_rate=float((pred_simple < 0).mean()),
        average_predicted_risk_prob=float(risk_prob.mean()),
        high_risk_actual_down_rate=high_risk_actual_down_rate,
        high_risk_count=high_risk_count,
        interval_coverage=interval_coverage,
        risk_method=risk_method,
        prediction_col=prediction_col,
        lower_col=lower_col,
        upper_col=upper_col,
    )


def save_plot(pred_df: pd.DataFrame, target_col: str, prediction_col: str, output_path: str) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("未安装 matplotlib，跳过图表输出")
        return

    actual_simple = np.expm1(pred_df[target_col])
    pred_simple = np.expm1(pred_df[prediction_col])

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    plt.figure(figsize=(12, 5))
    plt.plot(pred_df["date"], actual_simple * 100, label="actual future return")
    plt.plot(pred_df["date"], pred_simple * 100, label="orbit prediction")
    plt.axhline(0, linewidth=1)
    plt.title("Orbit WLD hourly future-return prediction")
    plt.xlabel("date")
    plt.ylabel("future simple return (%)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def run_experiment(config: ExperimentConfig) -> tuple[str, str, str | None, ExperimentMetrics]:
    try:
        from orbit.models import DLT
    except ImportError as exc:
        raise ImportError(
            "未安装 orbit-ml。建议先执行: conda install -c conda-forge orbit-ml 或 pip install orbit-ml"
        ) from exc

    raw_df = load_klines(config.input)
    model_df, target_col, regressors = prepare_model_frame(
        raw_df,
        horizon=config.horizon,
        max_rows=config.max_rows,
        use_regressors=config.use_regressors,
    )
    train_df, test_df = split_train_test(model_df, config.test_size)

    model_kwargs = {
        "response_col": target_col,
        "date_col": "date",
        "seasonality": config.seasonality,
        "estimator": config.estimator,
        "seed": config.seed,
        "verbose": False,
    }
    if regressors:
        model_kwargs["regressor_col"] = regressors

    model = DLT(**model_kwargs)
    model.fit(df=train_df)
    predicted_df = model.predict(df=test_df)

    if "date" not in predicted_df.columns:
        predicted_df = predicted_df.copy()
        predicted_df["date"] = test_df["date"].values

    prediction_col = find_prediction_col(predicted_df, target_col)
    lower_col, upper_col, lower_q, upper_q = choose_interval_cols(predicted_df)

    if target_col not in predicted_df.columns:
        predicted_df = predicted_df.merge(test_df[["date", target_col]], on="date", how="left")

    predicted_df, risk_method = attach_risk_probability(
        predicted_df,
        train_df=train_df,
        target_col=target_col,
        prediction_col=prediction_col,
        lower_col=lower_col,
        upper_col=upper_col,
        lower_q=lower_q,
        upper_q=upper_q,
        risk_threshold=config.risk_threshold,
    )

    metrics = compute_metrics(
        predicted_df,
        train_df=train_df,
        test_df=test_df,
        target_col=target_col,
        prediction_col=prediction_col,
        lower_col=lower_col,
        upper_col=upper_col,
        risk_method=risk_method,
        config=config,
    )

    os.makedirs(config.output_dir, exist_ok=True)
    symbol = os.path.splitext(os.path.basename(config.input))[0]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"{symbol}_orbit_h{config.horizon}_{timestamp}"

    prediction_path = os.path.join(config.output_dir, f"{base_name}_predictions.csv")
    metrics_path = os.path.join(config.output_dir, f"{base_name}_metrics.json")
    plot_path = os.path.join(config.output_dir, f"{base_name}_plot.png")

    predicted_df.to_csv(prediction_path, index=False)
    payload = {
        "config": asdict(config),
        "regressors": regressors,
        "predicted_columns": list(predicted_df.columns),
        "metrics": asdict(metrics),
    }
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    save_plot(predicted_df, target_col, prediction_col, plot_path)
    return prediction_path, metrics_path, plot_path, metrics


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用 Orbit 对 WLD 小时数据做未来收益率分布预测实验")
    parser.add_argument("--input", default=os.path.join(BASE_DIR, "data", "WLDUSDT_1h.csv"), help="输入 CSV，默认 data/WLDUSDT_1h.csv")
    parser.add_argument("--output-dir", default=os.path.join(BASE_DIR, "reports", "orbit"), help="输出目录")
    parser.add_argument("--horizon", type=int, default=24, help="预测未来 N 小时收益率，默认 24")
    parser.add_argument("--test-size", type=int, default=240, help="最近 N 条样本作为测试集，默认 240")
    parser.add_argument("--max-rows", type=int, default=6000, help="最多使用最近 N 条有效样本，控制运行时间；<=0 表示使用全部")
    parser.add_argument("--seasonality", type=int, default=24, help="小时级数据默认日内周期 24")
    parser.add_argument("--estimator", default="stan-map", choices=["stan-map", "stan-mcmc", "pyro-svi"], help="Orbit 估计器，默认 stan-map，速度优先")
    parser.add_argument("--seed", type=int, default=2026, help="随机种子")
    parser.add_argument("--risk-threshold", type=float, default=-0.05, help="风险概率阈值：未来收益率 <= 该值，默认 -5%")
    parser.add_argument("--no-regressors", action="store_true", help="不使用 OHLCV 派生外生变量，只用目标序列本身")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = ExperimentConfig(
        input=args.input,
        output_dir=args.output_dir,
        horizon=args.horizon,
        test_size=args.test_size,
        max_rows=args.max_rows,
        seasonality=args.seasonality,
        estimator=args.estimator,
        seed=args.seed,
        risk_threshold=args.risk_threshold,
        use_regressors=not args.no_regressors,
    )

    prediction_path, metrics_path, plot_path, metrics = run_experiment(config)
    print("\nOrbit 实验完成")
    print(f"预测结果: {prediction_path}")
    print(f"指标摘要: {metrics_path}")
    print(f"图表文件: {plot_path}")
    print("\n核心指标:")
    print(f"  rows_used: {metrics.rows_used}")
    print(f"  train/test: {metrics.train_rows}/{metrics.test_rows}")
    print(f"  horizon: {metrics.horizon}h")
    print(f"  MAE(log return): {metrics.mae_log_return:.6f}")
    print(f"  RMSE(log return): {metrics.rmse_log_return:.6f}")
    print(f"  direction accuracy: {metrics.directional_accuracy:.2%}")
    print(f"  actual down rate: {metrics.actual_down_rate:.2%}")
    print(f"  predicted down rate: {metrics.predicted_down_rate:.2%}")
    print(f"  avg risk prob(return <= threshold): {metrics.average_predicted_risk_prob:.2%}")
    print(f"  high risk count(prob>=60%): {metrics.high_risk_count}")
    if metrics.high_risk_actual_down_rate is not None:
        print(f"  high risk actual down rate: {metrics.high_risk_actual_down_rate:.2%}")
    if metrics.interval_coverage is not None:
        print(f"  interval coverage: {metrics.interval_coverage:.2%}")
    print(f"  risk method: {metrics.risk_method}")


if __name__ == "__main__":
    main()
