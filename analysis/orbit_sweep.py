import argparse
import csv
import json
import os
import subprocess
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


def select_top_rows(
    rows: list[dict[str, Any]],
    top_n: int,
    min_high_risk_count: int,
    min_directional_accuracy: float,
) -> list[dict[str, Any]]:
    """Select Top-N rows for deep evaluation.

    The selector keeps only successful runs with a prediction CSV, filters out
    sparse high-risk groups when requested, then ranks by screening_score.
    """
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if row.get("status") != "ok":
            continue
        if not row.get("prediction_path"):
            continue
        if int(row.get("high_risk_count") or 0) < min_high_risk_count:
            continue
        if float(row.get("directional_accuracy") or 0) < min_directional_accuracy:
            continue
        candidates.append(row)

    ranked = sorted(candidates, key=lambda x: x.get("screening_score", float("-inf")), reverse=True)
    return ranked[:top_n]


def run_deep_evaluation(
    selected_rows: list[dict[str, Any]],
    eval_output_dir: str,
    eval_bins: str,
    eval_long_threshold: float,
    eval_short_threshold: float,
    eval_fee_rate: float,
    continue_on_error: bool,
) -> list[dict[str, Any]]:
    """Run analysis/orbit_evaluate_predictions.py for selected rows."""
    eval_script = os.path.join(BASE_DIR, "analysis", "orbit_evaluate_predictions.py")
    os.makedirs(eval_output_dir, exist_ok=True)

    evaluated: list[dict[str, Any]] = []
    for idx, row in enumerate(selected_rows, start=1):
        prediction_path = str(row["prediction_path"])
        risk_threshold = row.get("risk_threshold")
        cmd = [
            sys.executable,
            eval_script,
            "--predictions",
            prediction_path,
            "--output-dir",
            eval_output_dir,
            "--bins",
            eval_bins,
            "--long-threshold",
            str(eval_long_threshold),
            "--short-threshold",
            str(eval_short_threshold),
            "--fee-rate",
            str(eval_fee_rate),
        ]
        if risk_threshold is not None:
            cmd.extend(["--risk-threshold", str(risk_threshold)])

        print(f"\n[eval {idx}/{len(selected_rows)}] {prediction_path}")
        t0 = time.time()
        status = "ok"
        error = None
        try:
            subprocess.run(cmd, check=True)
        except Exception as exc:
            status = "error"
            error = repr(exc)
            print(f"  深度评估失败: {error}")
            if not continue_on_error:
                raise
        elapsed = time.time() - t0
        eval_row = dict(row)
        eval_row.update(
            {
                "eval_status": status,
                "eval_error": error,
                "eval_elapsed_sec": round(elapsed, 3),
                "eval_output_dir": eval_output_dir,
            }
        )
        evaluated.append(eval_row)
    return evaluated


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

    parser.add_argument("--auto-evaluate-top-n", type=int, default=0, help="sweep 完成后自动选择 Top-N 组合并运行深度评估，<=0 表示关闭")
    parser.add_argument("--auto-select-min-high-risk-count", type=int, default=20, help="自动选择时要求 high_risk_count 至少为该值")
    parser.add_argument("--auto-select-min-directional-accuracy", type=float, default=0.0, help="自动选择时要求 directional_accuracy 至少为该值")
    parser.add_argument("--eval-output-dir", default=os.path.join(BASE_DIR, "reports", "orbit_eval"), help="Top-N 深度评估输出目录")
    parser.add_argument("--eval-bins", default="0,0.2,0.4,0.6,0.8,1.0", help="概率校准分桶边界")
    parser.add_argument("--eval-long-threshold", type=float, default=0.02, help="深度评估中做多信号阈值，普通收益率")
    parser.add_argument("--eval-short-threshold", type=float, default=-0.02, help="深度评估中做空信号阈值，普通收益率")
    parser.add_argument("--eval-fee-rate", type=float, default=0.001, help="深度评估中每个非空信号扣除的费用/滑点")
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
    selected_csv = os.path.join(args.output_dir, f"orbit_sweep_{started}_selected_top.csv")
    evaluated_csv = os.path.join(args.output_dir, f"orbit_sweep_{started}_evaluated_top.csv")

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

    if args.auto_evaluate_top_n > 0:
        selected = select_top_rows(
            rows=rows,
            top_n=args.auto_evaluate_top_n,
            min_high_risk_count=args.auto_select_min_high_risk_count,
            min_directional_accuracy=args.auto_select_min_directional_accuracy,
        )
        write_rows_csv(selected_csv, selected)
        print("\n自动选择 Top-N 参数组合")
        print(f"选择结果 CSV: {selected_csv}")
        if not selected:
            print("没有符合自动选择条件的参数组合，跳过深度评估")
            return

        for row in selected:
            print(
                f"  selected score={row.get('screening_score'):.4f} "
                f"h={row.get('horizon')} max_rows={row.get('max_rows')} "
                f"seasonality={row.get('seasonality')} estimator={row.get('estimator')} "
                f"risk={row.get('risk_threshold')} regressors={row.get('use_regressors')}"
            )

        evaluated = run_deep_evaluation(
            selected_rows=selected,
            eval_output_dir=args.eval_output_dir,
            eval_bins=args.eval_bins,
            eval_long_threshold=args.eval_long_threshold,
            eval_short_threshold=args.eval_short_threshold,
            eval_fee_rate=args.eval_fee_rate,
            continue_on_error=args.continue_on_error,
        )
        write_rows_csv(evaluated_csv, evaluated)
        print("\nTop-N 深度评估完成")
        print(f"深度评估汇总 CSV: {evaluated_csv}")
        print(f"深度评估输出目录: {args.eval_output_dir}")


if __name__ == "__main__":
    main()
