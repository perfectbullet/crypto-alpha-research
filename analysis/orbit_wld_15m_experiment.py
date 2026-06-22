"""15m 短线单次实验脚本。

复用 analysis.orbit_wld_experiment 的 run_experiment / ExperimentConfig，仅在更外层做两件事：
1. 把 horizon_minutes / seasonality_minutes 换算成 K 线根数（bars）后喂给旧 1h 实验。
2. 在输出里补充 interval_minutes / horizon_minutes / horizon_bars / horizon_label 等人类可读字段，
   并把文件名里的 h{bars} 改写成 h{horizon_label}，避免再把 horizon=8 误解成 8 小时。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from analysis.orbit_wld_experiment import ExperimentConfig, run_experiment  # noqa: E402
from analysis.orbit_timeframe_utils import (  # noqa: E402
    format_horizon_label,
    format_seasonality_label,
    get_latest_times,
    minutes_to_bars,
    validate_horizon_minutes,
)


@dataclass
class Experiment15MConfig:
    input: str
    output_dir: str
    interval_minutes: int
    horizon_minutes: int
    test_size: int
    max_rows: int
    seasonality_minutes: int
    estimator: str
    seed: int
    risk_threshold: float
    use_regressors: bool


def _relabel_filename(path: str, horizon_bars: int, horizon_label: str) -> str:
    """把单次实验输出文件名里的 h{horizon_bars} 段改成 h{horizon_label}。

    原文件名: WLDUSDT_15m_orbit_h8_20260622_140000_predictions.csv
    新文件名: WLDUSDT_15m_orbit_h2h_20260622_140000_predictions.csv
    只替换 basename 里的 `_orbit_h{bars}_` 片段，时间戳保持不变。
    """
    dirname, basename = os.path.split(path)
    old_seg = f"_orbit_h{horizon_bars}_"
    new_seg = f"_orbit_h{horizon_label}_"
    return os.path.join(dirname, basename.replace(old_seg, new_seg))


def _augment_predictions(prediction_path: str, extra: dict[str, Any]) -> None:
    """给 predictions.csv 追加 interval_minutes / horizon_minutes / horizon_bars / horizon_label。"""
    pred_df = pd.read_csv(prediction_path)
    for key, value in extra.items():
        pred_df[key] = value
    pred_df.to_csv(prediction_path, index=False)


def _augment_metrics(metrics_path: str, extra: dict[str, Any]) -> dict[str, Any]:
    """在 metrics JSON 里补充人类可读的时间粒度字段。"""
    with open(metrics_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    payload.update(extra)
    payload.setdefault("config", {}).update(extra)
    payload.setdefault("metrics", {}).update(extra)
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


def run_15m_experiment(config: Experiment15MConfig) -> dict[str, Any]:
    """运行一次 15m 实验。

    复用旧 1h run_experiment，把 horizon/seasonality 换算成 bars 后传入。
    返回 dict 包含：所有输出路径、派生字段、原始 metrics。
    """
    horizon_bars = validate_horizon_minutes(config.horizon_minutes, config.interval_minutes)
    seasonality_bars = minutes_to_bars(config.seasonality_minutes, config.interval_minutes)
    horizon_label = format_horizon_label(config.horizon_minutes)
    seasonality_label = format_seasonality_label(config.seasonality_minutes)

    base_config = ExperimentConfig(
        input=config.input,
        output_dir=config.output_dir,
        horizon=horizon_bars,
        test_size=config.test_size,
        max_rows=config.max_rows,
        seasonality=seasonality_bars,
        estimator=config.estimator,
        seed=config.seed,
        risk_threshold=config.risk_threshold,
        use_regressors=config.use_regressors,
    )

    prediction_path, metrics_path, plot_path, metrics = run_experiment(base_config)

    extra = {
        "interval_minutes": config.interval_minutes,
        "horizon_minutes": config.horizon_minutes,
        "horizon_bars": horizon_bars,
        "horizon_label": horizon_label,
        "seasonality_minutes": config.seasonality_minutes,
        "seasonality_bars": seasonality_bars,
        "seasonality_label": seasonality_label,
    }

    # 追加列 / 字段后再重命名，保证写入的是最终文件名。
    _augment_predictions(prediction_path, extra)
    metrics_payload = _augment_metrics(metrics_path, extra)

    new_prediction_path = _relabel_filename(prediction_path, horizon_bars, horizon_label)
    new_metrics_path = _relabel_filename(metrics_path, horizon_bars, horizon_label)
    os.replace(prediction_path, new_prediction_path)
    os.replace(metrics_path, new_metrics_path)

    new_plot_path = plot_path
    if plot_path and os.path.exists(plot_path):
        new_plot_path = _relabel_filename(plot_path, horizon_bars, horizon_label)
        os.replace(plot_path, new_plot_path)

    return {
        "prediction_path": new_prediction_path,
        "metrics_path": new_metrics_path,
        "plot_path": new_plot_path,
        "metrics": metrics_payload.get("metrics", asdict(metrics)),
        **extra,
        "config_15m": asdict(config),
        "base_horizon_config": asdict(base_config),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用 Orbit 对 WLD 15m 数据做短线未来收益率分布预测实验")
    parser.add_argument("--input", default=os.path.join(BASE_DIR, "data", "WLDUSDT_15m.csv"), help="输入 CSV，默认 data/WLDUSDT_15m.csv")
    parser.add_argument("--interval-minutes", type=int, default=15, help="K 线周期（分钟），默认 15")
    parser.add_argument("--horizon-minutes", type=int, default=120, help="真实预测时长（分钟），默认 120；必须能被 interval-minutes 整除，且 <=1440")
    parser.add_argument("--test-size", type=int, default=960, help="最近 N 根 K 线作为测试集，默认 960")
    parser.add_argument("--max-rows", type=int, default=16000, help="最多使用最近 N 条有效样本；<=0 表示全量")
    parser.add_argument("--seasonality-minutes", type=int, default=1440, help="季节性周期（分钟），默认 1440（1 天）")
    parser.add_argument("--estimator", default="stan-map", choices=["stan-map", "stan-mcmc", "pyro-svi"], help="Orbit 估计器，默认 stan-map")
    parser.add_argument("--seed", type=int, default=2026, help="随机种子")
    parser.add_argument("--risk-threshold", type=float, default=-0.02, help="风险事件阈值（普通收益率），默认 -2%%")
    parser.add_argument("--output-dir", default=os.path.join(BASE_DIR, "reports", "orbit_15m"), help="输出目录")
    parser.add_argument("--no-regressors", action="store_true", help="不使用 OHLCV 派生外生变量")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = Experiment15MConfig(
        input=args.input,
        output_dir=args.output_dir,
        interval_minutes=args.interval_minutes,
        horizon_minutes=args.horizon_minutes,
        test_size=args.test_size,
        max_rows=args.max_rows,
        seasonality_minutes=args.seasonality_minutes,
        estimator=args.estimator,
        seed=args.seed,
        risk_threshold=args.risk_threshold,
        use_regressors=not args.no_regressors,
    )

    result = run_15m_experiment(config)
    metrics = result["metrics"]

    from analysis.orbit_wld_experiment import load_klines  # 延迟导入，避免没装 orbit 时也无法 --help

    latest_utc, latest_beijing = "", ""
    try:
        latest_utc, latest_beijing = get_latest_times(load_klines(config.input))
    except Exception:
        pass

    print("\nOrbit 15m 实验完成")
    print(f"interval: {config.interval_minutes}m")
    print(f"horizon: {config.horizon_minutes}m / {result['horizon_label']}（{result['horizon_bars']} 根 K 线）")
    print(f"seasonality: {config.seasonality_minutes}m / {result['seasonality_label']}（{result['seasonality_bars']} 根）")
    if latest_utc:
        print(f"latest data UTC: {latest_utc}")
        print(f"latest data Beijing: {latest_beijing}")
    print(f"预测结果: {result['prediction_path']}")
    print(f"指标摘要: {result['metrics_path']}")
    print(f"图表文件: {result['plot_path']}")
    print("\n核心指标:")
    print(f"  rows_used: {metrics.get('rows_used')}")
    print(f"  train/test: {metrics.get('train_rows')}/{metrics.get('test_rows')}")
    print(f"  MAE(simple return): {float(metrics.get('mae_simple_return', 0)):.4%}")
    print(f"  directional accuracy: {float(metrics.get('directional_accuracy', 0)):.2%}")
    print(f"  actual down rate: {float(metrics.get('actual_down_rate', 0)):.2%}")
    print(f"  predicted down rate: {float(metrics.get('predicted_down_rate', 0)):.2%}")
    print(f"  avg risk prob(return <= {config.risk_threshold:.2%}): {float(metrics.get('average_predicted_risk_prob', 0)):.2%}")
    print(f"  high risk count(prob>=60%): {metrics.get('high_risk_count')}")


if __name__ == "__main__":
    main()
