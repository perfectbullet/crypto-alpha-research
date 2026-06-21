import argparse
import csv
import json
import os
import sys
import time
from dataclasses import asdict
from itertools import product
from typing import Any

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from analysis.orbit_wld_experiment import ExperimentConfig, run_experiment


def parse_csv_ints(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def parse_csv_floats(raw: str) -> list[float]:
    return [float(x.strip()) for x in raw.split(",") if x.strip()]


def parse_csv_strings(raw: str) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def read_metrics_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def flatten_result(
    config: ExperimentConfig,
    prediction_path: str | None,
    metrics_path: str | None,
    plot_path: str | None,
    metrics_payload: dict[str, Any] | None,
    elapsed_sec: float,
    status: str,
    error: str | None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "status": status,
        "error": error,
        "elapsed_sec": round(elapsed_sec, 3),
        "prediction_path": prediction_path,
        "metrics_path": metrics_path,
        "plot_path": plot_path,
        **asdict(config),
    }
    if metrics_payload:
        row.update(metrics_payload.get("metrics", {}))
    return row


def score_row(row: dict[str, Any]) -> float:
    """A rough ranking score for quick screening.

    This is not a trading objective. It favors:
    - lower simple-return MAE
    - better directional accuracy
    - high-risk event lift if available
    - more high-risk samples so the signal is not too sparse
    """
    if row.get("status") != "ok":
        return float("-inf")
    mae = float(row.get("mae_simple_return") or 999)
    direction = float(row.get("directional_accuracy") or 0)
    actual_down_rate = float(row.get("actual_down_rate") or 0)
    high_risk_rate = row.get("high_risk_actual_down_rate")
    high_risk_count = int(row.get("high_risk_count") or 0)
    lift = 1.0
    if high_risk_rate is not None and actual_down_rate > 0:
        lift = float(high_risk_rate) / actual_down_rate
    sparse_penalty = 0.7 if high_risk_count < 20 else 1.0
    return (direction * 2.0 + lift * sparse_penalty) - mae * 5.0


def write_rows_csv(path: str, rows: list[dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量运行 Orbit WLD 参数组合实验")
    parser.add_argument("--input", default=os.path.join(BASE_DIR, "data", "WLDUSDT_1h.csv"), help="输入 CSV")
    parser.add_argument("--output-dir", default=os.path.join(BASE_DIR, "reports", "orbit_sweep"), help="sweep 汇总输出目录")
    parser.add_argument("--experiment-output-dir", default=os.path.join(BASE_DIR, "reports", "orbit"), help="单次 Orbit 实验输出目录")
    parser.add_argument("--horizons", default="6,12,24,72", help="预测窗口列表，例如 6,12,24,72")
    parser.add_argument("--test-sizes", default="240", help="测试集大小列表，例如 240,480")
    parser.add_argument("--max-rows-list", default="3000,6000,12000", help="训练样本上限列表，<=0 表示全量")
    parser.add_argument("--seasonalities", default="24,168", help="季节性列表，例如 24,168")
    parser.add_argument("--estimators", default="stan-map", help="估计器列表，可选 stan-map,stan-mcmc,pyro-svi")
    parser.add_argument("--risk-thresholds", default="-0.03,-0.05,-0.08", help="风险阈值列表，普通收益率")
    parser.add_argument("--regressor-modes", default="with,without", choices=None, help="with,without，是否使用派生外生变量")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--limit", type=int, default=0, help="最多运行多少组，<=0 表示不限制")
    parser.add_argument("--continue-on-error", action="store_true", help="单组失败时继续运行后续组合")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    horizons = parse_csv_ints(args.horizons)
    test_sizes = parse_csv_ints(args.test_sizes)
    max_rows_list = parse_csv_ints(args.max_rows_list)
    seasonalities = parse_csv_ints(args.seasonalities)
    estimators = parse_csv_strings(args.estimators)
    risk_thresholds = parse_csv_floats(args.risk_thresholds)
    regressor_modes = parse_csv_strings(args.regressor_modes)

    combos = list(product(horizons, test_sizes, max_rows_list, seasonalities, estimators, risk_thresholds, regressor_modes))
    if args.limit > 0:
        combos = combos[: args.limit]

    rows: list[dict[str, Any]] = []
    os.makedirs(args.output_dir, exist_ok=True)
    started = time.strftime("%Y%m%d_%H%M%S")
    summary_csv = os.path.join(args.output_dir, f"orbit_sweep_{started}.csv")
    summary_json = os.path.join(args.output_dir, f"orbit_sweep_{started}.json")

    print(f"准备运行 {len(combos)} 组 Orbit 参数组合")
    for idx, (horizon, test_size, max_rows, seasonality, estimator, risk_threshold, regressor_mode) in enumerate(combos, start=1):
        use_regressors = regressor_mode == "with"
        config = ExperimentConfig(
            input=args.input,
            output_dir=args.experiment_output_dir,
            horizon=horizon,
            test_size=test_size,
            max_rows=max_rows,
            seasonality=seasonality,
            estimator=estimator,
            seed=args.seed,
            risk_threshold=risk_threshold,
            use_regressors=use_regressors,
        )
        print(
            f"\n[{idx}/{len(combos)}] "
            f"h={horizon}, test={test_size}, max_rows={max_rows}, "
            f"seasonality={seasonality}, estimator={estimator}, "
            f"risk={risk_threshold}, regressors={use_regressors}"
        )
        t0 = time.time()
        prediction_path = metrics_path = plot_path = None
        metrics_payload = None
        status = "ok"
        error = None
        try:
            prediction_path, metrics_path, plot_path, _ = run_experiment(config)
            metrics_payload = read_metrics_json(metrics_path)
        except Exception as exc:
            status = "error"
            error = repr(exc)
            print(f"  失败: {error}")
            if not args.continue_on_error:
                raise
        elapsed = time.time() - t0
        row = flatten_result(config, prediction_path, metrics_path, plot_path, metrics_payload, elapsed, status, error)
        row["screening_score"] = score_row(row)
        rows.append(row)
        write_rows_csv(summary_csv, rows)
        with open(summary_json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        if status == "ok":
            print(f"  完成，用时 {elapsed:.1f}s，score={row['screening_score']:.4f}")
            print(f"  metrics: {metrics_path}")

    ranked = sorted(rows, key=lambda x: x.get("screening_score", float("-inf")), reverse=True)
    print("\nSweep 完成")
    print(f"汇总 CSV: {summary_csv}")
    print(f"汇总 JSON: {summary_json}")
    print("\nTop 10 参数组合:")
    for row in ranked[:10]:
        print(
            f"  score={row.get('screening_score'):.4f} "
            f"h={row.get('horizon')} max_rows={row.get('max_rows')} "
            f"seasonality={row.get('seasonality')} estimator={row.get('estimator')} "
            f"risk={row.get('risk_threshold')} regressors={row.get('use_regressors')} "
            f"dir={row.get('directional_accuracy')} mae={row.get('mae_simple_return')} "
            f"high_risk_rate={row.get('high_risk_actual_down_rate')}"
        )


if __name__ == "__main__":
    main()
