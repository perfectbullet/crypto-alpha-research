import argparse
import json
import math
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from analysis.orbit_wld_experiment import (  # noqa: E402
    DEFAULT_REGRESSORS,
    add_features,
    find_prediction_col,
    load_klines,
    normal_cdf,
)


@dataclass
class LiveSignalConfig:
    input: str
    output_dir: str
    horizon: int
    max_rows: int
    seasonality: int
    estimator: str
    seed: int
    risk_threshold: float
    prediction_rows: int
    current_position: float
    long_threshold: float
    buy_risk_max: float
    strong_long_threshold: float
    strong_risk_max: float
    reduce_pred_threshold: float
    reduce_risk_threshold: float
    clear_pred_threshold: float
    clear_risk_threshold: float
    reduced_position: float
    base_position: float
    max_position: float
    no_regressors: bool


def build_train_and_live_frames(
    raw_df: pd.DataFrame,
    horizon: int,
    max_rows: int,
    prediction_rows: int,
    use_regressors: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, str, list[str]]:
    featured, target_col = add_features(raw_df, horizon)
    regressors = [col for col in DEFAULT_REGRESSORS if col in featured.columns] if use_regressors else []

    train_needed = ["date", target_col] + regressors
    train_df = featured[train_needed].replace([np.inf, -np.inf], np.nan).dropna().copy()
    if max_rows > 0 and len(train_df) > max_rows:
        train_df = train_df.tail(max_rows).reset_index(drop=True)
    else:
        train_df.reset_index(drop=True, inplace=True)

    if len(train_df) < 100:
        raise ValueError(f"训练样本过少: {len(train_df)}")

    live_needed = ["date"] + regressors
    live_df = featured[live_needed].replace([np.inf, -np.inf], np.nan).dropna().copy()

    # Only keep rows whose future target is not available in the training set. These are the real live candidates.
    train_end = train_df["date"].max()
    live_df = live_df[live_df["date"] > train_end].copy()
    if live_df.empty:
        raise ValueError(
            "没有可预测的最新未标注样本。通常需要 K 线数据末尾至少包含 horizon 个没有未来标签的小时。"
        )

    live_df = live_df.tail(prediction_rows).reset_index(drop=True)
    return train_df, live_df, target_col, regressors


def attach_live_risk_probability(
    pred_df: pd.DataFrame,
    train_df: pd.DataFrame,
    target_col: str,
    prediction_col: str,
    risk_threshold: float,
) -> tuple[pd.DataFrame, str]:
    out = pred_df.copy()
    log_threshold = math.log1p(risk_threshold)
    window = min(168, len(train_df))
    sigma = float(train_df[target_col].tail(window).std())
    if not math.isfinite(sigma) or sigma <= 0:
        sigma = float(train_df[target_col].std())
    if not math.isfinite(sigma) or sigma <= 0:
        sigma = 1e-6

    z = (log_threshold - out[prediction_col]) / sigma
    out["risk_prob_return_le_threshold"] = normal_cdf(z)
    out["risk_threshold_simple_return"] = risk_threshold
    out["predicted_simple_return"] = np.expm1(out[prediction_col])
    return out, "normal_approx_from_train_target_vol"


def decide_target_position(row: pd.Series, config: LiveSignalConfig) -> tuple[float, str]:
    pred_ret = float(row["predicted_simple_return"])
    risk_prob = float(row["risk_prob_return_le_threshold"])

    if risk_prob >= config.clear_risk_threshold:
        return 0.0, "clear_by_risk"
    if pred_ret <= config.clear_pred_threshold:
        return 0.0, "clear_by_prediction"

    if pred_ret < config.reduce_pred_threshold or risk_prob >= config.reduce_risk_threshold:
        return config.reduced_position, "reduced"

    if pred_ret >= config.strong_long_threshold and risk_prob <= config.strong_risk_max:
        return config.max_position, "strong_long"

    if pred_ret >= config.long_threshold and risk_prob <= config.buy_risk_max:
        return config.base_position, "long"

    return 0.0, "flat"


def classify_action(current_position: float, target_position: float) -> str:
    delta = target_position - current_position
    if abs(delta) < 1e-12:
        return "hold"
    if current_position == 0 and target_position > 0:
        return "buy"
    if target_position == 0 and current_position > 0:
        return "sell_all"
    if delta > 0:
        return "increase"
    return "reduce"


def run_live_signal(config: LiveSignalConfig) -> tuple[pd.DataFrame, dict]:
    try:
        from orbit.models import DLT
    except ImportError as exc:
        raise ImportError(
            "未安装 orbit-ml。建议先执行: conda install -c conda-forge orbit-ml 或 pip install orbit-ml"
        ) from exc

    raw_df = load_klines(config.input)
    train_df, live_df, target_col, regressors = build_train_and_live_frames(
        raw_df=raw_df,
        horizon=config.horizon,
        max_rows=config.max_rows,
        prediction_rows=config.prediction_rows,
        use_regressors=not config.no_regressors,
    )

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
    predicted_df = model.predict(df=live_df)
    if "date" not in predicted_df.columns:
        predicted_df = predicted_df.copy()
        predicted_df["date"] = live_df["date"].values

    prediction_col = find_prediction_col(predicted_df, target_col)
    predicted_df, risk_method = attach_live_risk_probability(
        pred_df=predicted_df,
        train_df=train_df,
        target_col=target_col,
        prediction_col=prediction_col,
        risk_threshold=config.risk_threshold,
    )

    decisions = []
    for _, row in predicted_df.iterrows():
        target_position, signal = decide_target_position(row, config)
        target_position = float(np.clip(target_position, 0.0, config.max_position))
        action = classify_action(config.current_position, target_position)
        decisions.append(
            {
                "signal": signal,
                "action": action,
                "current_position": config.current_position,
                "target_position": target_position,
                "trade_delta": target_position - config.current_position,
            }
        )

    decision_df = pd.DataFrame(decisions)
    out = pd.concat([predicted_df.reset_index(drop=True), decision_df], axis=1)

    summary = {
        "config": asdict(config),
        "train_rows": len(train_df),
        "live_rows": len(live_df),
        "train_start": str(train_df["date"].iloc[0]),
        "train_end": str(train_df["date"].iloc[-1]),
        "latest_signal_date": str(out["date"].iloc[-1]),
        "target_col": target_col,
        "regressors": regressors,
        "prediction_col": prediction_col,
        "risk_method": risk_method,
        "latest_signal": out.tail(1).to_dict(orient="records")[0],
    }
    return out, summary


def write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用已筛选的 Orbit 参数生成最新可执行仓位信号")
    parser.add_argument("--input", default=os.path.join(BASE_DIR, "data", "WLDUSDT_1h.csv"), help="输入小时K线 CSV")
    parser.add_argument("--output-dir", default=os.path.join(BASE_DIR, "reports", "orbit_live"), help="输出目录")
    parser.add_argument("--horizon", type=int, default=72, help="预测未来 N 小时收益率")
    parser.add_argument("--max-rows", type=int, default=6000, help="最多使用最近 N 条有效训练样本；<=0 表示全量")
    parser.add_argument("--seasonality", type=int, default=168, help="季节性周期")
    parser.add_argument("--estimator", default="stan-map", choices=["stan-map", "stan-mcmc", "pyro-svi"], help="Orbit 估计器")
    parser.add_argument("--seed", type=int, default=2026, help="随机种子")
    parser.add_argument("--risk-threshold", type=float, default=-0.06, help="风险事件阈值，普通收益率")
    parser.add_argument("--prediction-rows", type=int, default=1, help="输出最近 N 条未标注样本的预测")
    parser.add_argument("--current-position", type=float, default=0.0, help="当前实际仓位，0 表示空仓")
    parser.add_argument("--long-threshold", type=float, default=0.03, help="允许做多的预测收益阈值")
    parser.add_argument("--buy-risk-max", type=float, default=0.35, help="允许做多的最大风险概率")
    parser.add_argument("--strong-long-threshold", type=float, default=0.06, help="强做多预测收益阈值")
    parser.add_argument("--strong-risk-max", type=float, default=0.30, help="强做多允许的最大风险概率")
    parser.add_argument("--reduce-pred-threshold", type=float, default=0.01, help="低于该预测收益则降为 reduced_position")
    parser.add_argument("--reduce-risk-threshold", type=float, default=0.60, help="高于该风险概率则降为 reduced_position")
    parser.add_argument("--clear-pred-threshold", type=float, default=-0.01, help="低于等于该预测收益则空仓")
    parser.add_argument("--clear-risk-threshold", type=float, default=0.80, help="高于等于该风险概率则空仓")
    parser.add_argument("--reduced-position", type=float, default=0.0, help="信号转弱时目标仓位；空仓入场建议为 0")
    parser.add_argument("--base-position", type=float, default=0.30, help="普通做多目标仓位")
    parser.add_argument("--max-position", type=float, default=0.50, help="强信号最大目标仓位")
    parser.add_argument("--no-regressors", action="store_true", help="不使用 OHLCV 派生外生变量")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = LiveSignalConfig(
        input=args.input,
        output_dir=args.output_dir,
        horizon=args.horizon,
        max_rows=args.max_rows,
        seasonality=args.seasonality,
        estimator=args.estimator,
        seed=args.seed,
        risk_threshold=args.risk_threshold,
        prediction_rows=args.prediction_rows,
        current_position=args.current_position,
        long_threshold=args.long_threshold,
        buy_risk_max=args.buy_risk_max,
        strong_long_threshold=args.strong_long_threshold,
        strong_risk_max=args.strong_risk_max,
        reduce_pred_threshold=args.reduce_pred_threshold,
        reduce_risk_threshold=args.reduce_risk_threshold,
        clear_pred_threshold=args.clear_pred_threshold,
        clear_risk_threshold=args.clear_risk_threshold,
        reduced_position=args.reduced_position,
        base_position=args.base_position,
        max_position=args.max_position,
        no_regressors=args.no_regressors,
    )

    result, summary = run_live_signal(config)

    os.makedirs(config.output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    symbol = os.path.splitext(os.path.basename(config.input))[0]
    base = f"{symbol}_orbit_live_h{config.horizon}_{timestamp}"
    csv_path = os.path.join(config.output_dir, f"{base}_signal.csv")
    json_path = os.path.join(config.output_dir, f"{base}_summary.json")

    result.to_csv(csv_path, index=False)
    write_json(json_path, summary)

    latest = result.tail(1).iloc[0]
    print("\nOrbit 最新仓位信号完成")
    print(f"信号明细: {csv_path}")
    print(f"摘要 JSON: {json_path}")
    print("\n最新信号:")
    print(f"  date: {latest['date']}")
    print(f"  predicted_simple_return_{config.horizon}h: {latest['predicted_simple_return']:.2%}")
    print(f"  risk_prob_return_le_{config.risk_threshold:.2%}: {latest['risk_prob_return_le_threshold']:.2%}")
    print(f"  signal: {latest['signal']}")
    print(f"  action: {latest['action']}")
    print(f"  current_position: {latest['current_position']:.2%}")
    print(f"  target_position: {latest['target_position']:.2%}")
    print(f"  trade_delta: {latest['trade_delta']:.2%}")


if __name__ == "__main__":
    main()
