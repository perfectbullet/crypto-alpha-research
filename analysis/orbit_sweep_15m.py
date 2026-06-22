"""15m 短线参数 sweep 脚本。

基于 analysis/orbit_sweep.py 改造，但：
- 只调用 15m 实验（run_15m_experiment），不直接调旧 1h 实验。
- 内部把 horizon_minutes / seasonality_minutes 换算成 K 线根数。
- 汇总 CSV 里补充 interval_minutes / horizon_minutes / horizon_bars / horizon_label /
  test_size_bars / test_size_days / seasonality 等人类可读字段。
- --export-gpt-json 开启时，把 Top 候选参数和关键指标打包成 gpt_packet.json。
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import subprocess
import sys
import time
from itertools import product
from typing import Any

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from analysis.orbit_wld_15m_experiment import (  # noqa: E402
    Experiment15MConfig,
    run_15m_experiment,
)
from analysis.orbit_timeframe_utils import (  # noqa: E402
    format_horizon_label,
    format_seasonality_label,
    get_latest_times,
    minutes_to_bars,
    parse_csv_ints,
    validate_horizon_minutes,
)
from analysis.orbit_export_gpt_packet import build_gpt_packet, write_gpt_packet  # noqa: E402

MINUTES_PER_DAY = 1440.0


def parse_csv_floats(raw: str) -> list[float]:
    return [float(x.strip()) for x in raw.split(",") if x.strip()]


def parse_csv_strings(raw: str) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def read_metrics_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def flatten_result(
    *,
    config: Experiment15MConfig,
    horizon_bars: int,
    horizon_label: str,
    seasonality_bars: int,
    seasonality_label: str,
    result: dict[str, Any] | None,
    metrics_payload: dict[str, Any] | None,
    elapsed_sec: float,
    status: str,
    error: str | None,
) -> dict[str, Any]:
    test_size_bars = config.test_size
    test_size_days = round(test_size_bars * config.interval_minutes / MINUTES_PER_DAY, 4)
    row: dict[str, Any] = {
        "status": status,
        "error": error,
        "elapsed_sec": round(elapsed_sec, 3),
        "interval_minutes": config.interval_minutes,
        "horizon_minutes": config.horizon_minutes,
        "horizon_bars": horizon_bars,
        "horizon_label": horizon_label,
        "test_size_bars": test_size_bars,
        "test_size_days": test_size_days,
        "max_rows": config.max_rows,
        "seasonality_minutes": config.seasonality_minutes,
        "seasonality_bars": seasonality_bars,
        "seasonality_label": seasonality_label,
        "estimator": config.estimator,
        "seed": config.seed,
        "risk_threshold": config.risk_threshold,
        "use_regressors": config.use_regressors,
        "prediction_path": result.get("prediction_path") if result else None,
        "metrics_path": result.get("metrics_path") if result else None,
        "plot_path": result.get("plot_path") if result else None,
    }
    if metrics_payload:
        row.update(metrics_payload.get("metrics", {}))
    return row


def score_row(row: dict[str, Any]) -> float:
    """与旧 sweep 一致的粗筛打分：方向准确率 + 高风险提升度 - 点预测误差。"""
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
    candidates = [
        row
        for row in rows
        if row.get("status") == "ok"
        and row.get("prediction_path")
        and int(row.get("high_risk_count") or 0) >= min_high_risk_count
        and float(row.get("directional_accuracy") or 0) >= min_directional_accuracy
    ]
    ranked = sorted(candidates, key=lambda x: x.get("screening_score", float("-inf")), reverse=True)
    return ranked[:top_n]


def find_eval_artifacts(eval_output_dir: str, prediction_path: str) -> tuple[str | None, str | None]:
    """深度评估后，定位该预测文件对应的 summary.json 和 calibration.csv。"""
    base_name = os.path.splitext(os.path.basename(prediction_path))[0]
    summary_matches = sorted(glob.glob(os.path.join(eval_output_dir, f"{base_name}_eval_*_summary.json")))
    calib_matches = sorted(glob.glob(os.path.join(eval_output_dir, f"{base_name}_eval_*_calibration.csv")))
    summary_path = summary_matches[-1] if summary_matches else None
    calibration_path = calib_matches[-1] if calib_matches else None
    return summary_path, calibration_path


def run_deep_evaluation(
    selected_rows: list[dict[str, Any]],
    eval_output_dir: str,
    eval_bins: str,
    eval_long_threshold: float,
    eval_short_threshold: float,
    eval_fee_rate: float,
    continue_on_error: bool,
) -> list[dict[str, Any]]:
    """复用 analysis/orbit_evaluate_predictions.py 对 Top-N 做深度评估。"""
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

        summary_path, calibration_path = (None, None)
        if status == "ok":
            summary_path, calibration_path = find_eval_artifacts(eval_output_dir, prediction_path)

        eval_row = dict(row)
        eval_row.update(
            {
                "eval_status": status,
                "eval_error": error,
                "eval_elapsed_sec": round(elapsed, 3),
                "eval_output_dir": eval_output_dir,
                "eval_summary_path": summary_path,
                "eval_calibration_path": calibration_path,
            }
        )
        evaluated.append(eval_row)
    return evaluated


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量运行 Orbit WLD 15m 短线参数组合实验")
    parser.add_argument("--input", default=os.path.join(BASE_DIR, "data", "WLDUSDT_15m.csv"), help="输入 CSV")
    parser.add_argument("--interval-minutes", type=int, default=15, help="K 线周期（分钟），默认 15")
    parser.add_argument("--output-dir", default=os.path.join(BASE_DIR, "reports", "orbit_sweep_15m"), help="sweep 汇总输出目录")
    parser.add_argument("--experiment-output-dir", default=os.path.join(BASE_DIR, "reports", "orbit_15m"), help="单次 15m 实验输出目录")
    parser.add_argument("--horizon-minutes", default="15,30,45,60,90,120,180,240,360,720,1440", help="预测时长列表（分钟），例如 15,30,60,120")
    parser.add_argument("--test-sizes", default="960,1920", help="测试集大小列表（K 线根数）")
    parser.add_argument("--max-rows-list", default="8000,16000,24000,0", help="训练样本上限列表，<=0 表示全量")
    parser.add_argument("--seasonality-minutes", default="1440,10080", help="季节性周期列表（分钟），例如 1440,10080")
    parser.add_argument("--estimators", default="stan-map", help="估计器列表")
    parser.add_argument("--risk-thresholds", default="-0.01,-0.015,-0.02,-0.03", help="风险阈值列表（普通收益率）")
    parser.add_argument("--regressor-modes", default="with", help="with,without，是否使用派生外生变量")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--limit", type=int, default=0, help="最多运行多少组，<=0 表示不限制")
    parser.add_argument("--continue-on-error", action="store_true", help="单组失败时继续运行后续组合")

    parser.add_argument("--auto-evaluate-top-n", type=int, default=20, help="sweep 完成后自动选 Top-N 组合并深度评估，<=0 关闭")
    parser.add_argument("--auto-select-min-high-risk-count", type=int, default=40, help="自动选择时要求 high_risk_count 至少为该值")
    parser.add_argument("--auto-select-min-directional-accuracy", type=float, default=0.0, help="自动选择时要求 directional_accuracy 至少为该值")
    parser.add_argument("--eval-output-dir", default=os.path.join(BASE_DIR, "reports", "orbit_eval_15m"), help="Top-N 深度评估输出目录")
    parser.add_argument("--eval-bins", default="0,0.2,0.4,0.6,0.8,1.0", help="概率校准分桶边界")
    parser.add_argument("--eval-long-threshold", type=float, default=0.006, help="深度评估做多信号阈值（普通收益率）")
    parser.add_argument("--eval-short-threshold", type=float, default=-0.006, help="深度评估做空信号阈值（普通收益率）")
    parser.add_argument("--eval-fee-rate", type=float, default=0.001, help="深度评估每个非空信号扣除的费用/滑点")
    parser.add_argument("--export-gpt-json", action="store_true", help="导出可一次性上传给 GPT 的 gpt_packet.json")
    parser.add_argument("--gpt-max-candidates", type=int, default=30, help="gpt_packet.json 里 top_candidates 上限")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    horizon_minutes_list = parse_csv_ints(args.horizon_minutes)
    test_sizes = parse_csv_ints(args.test_sizes)
    max_rows_list = parse_csv_ints(args.max_rows_list)
    seasonality_minutes_list = parse_csv_ints(args.seasonality_minutes)
    estimators = parse_csv_strings(args.estimators)
    risk_thresholds = parse_csv_floats(args.risk_thresholds)
    regressor_modes = parse_csv_strings(args.regressor_modes)

    # 提前校验所有 horizon_minutes / seasonality_minutes 合法，避免跑到一半才报错。
    horizon_bars_map: dict[int, int] = {}
    for horizon_minutes in horizon_minutes_list:
        horizon_bars_map[horizon_minutes] = validate_horizon_minutes(horizon_minutes, args.interval_minutes)
    seasonality_bars_map: dict[int, int] = {}
    for seasonality_minutes in seasonality_minutes_list:
        seasonality_bars_map[seasonality_minutes] = minutes_to_bars(seasonality_minutes, args.interval_minutes)

    combos = list(
        product(
            horizon_minutes_list,
            test_sizes,
            max_rows_list,
            seasonality_minutes_list,
            estimators,
            risk_thresholds,
            regressor_modes,
        )
    )
    if args.limit > 0:
        combos = combos[: args.limit]

    os.makedirs(args.output_dir, exist_ok=True)
    started = time.strftime("%Y%m%d_%H%M%S")
    summary_csv = os.path.join(args.output_dir, f"orbit_sweep_15m_{started}.csv")
    summary_json = os.path.join(args.output_dir, f"orbit_sweep_15m_{started}.json")
    selected_csv = os.path.join(args.output_dir, f"orbit_sweep_15m_{started}_selected_top.csv")
    evaluated_csv = os.path.join(args.output_dir, f"orbit_sweep_15m_{started}_evaluated_top.csv")

    print(f"准备运行 {len(combos)} 组 15m 参数组合（interval={args.interval_minutes}m）")
    rows: list[dict[str, Any]] = []
    for idx, (horizon_minutes, test_size, max_rows, seasonality_minutes, estimator, risk_threshold, regressor_mode) in enumerate(
        combos, start=1
    ):
        horizon_bars = horizon_bars_map[horizon_minutes]
        seasonality_bars = seasonality_bars_map[seasonality_minutes]
        horizon_label = format_horizon_label(horizon_minutes)
        seasonality_label = format_seasonality_label(seasonality_minutes)
        use_regressors = regressor_mode == "with"

        config = Experiment15MConfig(
            input=args.input,
            output_dir=args.experiment_output_dir,
            interval_minutes=args.interval_minutes,
            horizon_minutes=horizon_minutes,
            test_size=test_size,
            max_rows=max_rows,
            seasonality_minutes=seasonality_minutes,
            estimator=estimator,
            seed=args.seed,
            risk_threshold=risk_threshold,
            use_regressors=use_regressors,
        )
        print(
            f"\n[{idx}/{len(combos)}] interval={args.interval_minutes}m "
            f"horizon={horizon_minutes}m/{horizon_label}({horizon_bars}bars) "
            f"test={test_size} max_rows={max_rows} "
            f"seasonality={seasonality_minutes}m/{seasonality_label}({seasonality_bars}bars) "
            f"estimator={estimator} risk={risk_threshold} regressors={use_regressors}"
        )

        t0 = time.time()
        result = None
        metrics_payload = None
        status = "ok"
        error = None
        try:
            result = run_15m_experiment(config)
            metrics_payload = read_metrics_json(result["metrics_path"])
        except Exception as exc:
            status = "error"
            error = repr(exc)
            print(f"  失败: {error}")
            if not args.continue_on_error:
                raise
        elapsed = time.time() - t0

        row = flatten_result(
            config=config,
            horizon_bars=horizon_bars,
            horizon_label=horizon_label,
            seasonality_bars=seasonality_bars,
            seasonality_label=seasonality_label,
            result=result,
            metrics_payload=metrics_payload,
            elapsed_sec=elapsed,
            status=status,
            error=error,
        )
        row["screening_score"] = score_row(row)
        rows.append(row)
        write_rows_csv(summary_csv, rows)
        with open(summary_json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        if status == "ok":
            print(f"  完成，用时 {elapsed:.1f}s，score={row['screening_score']:.4f}")
            print(f"  metrics: {row['metrics_path']}")

    ranked = sorted(rows, key=lambda x: x.get("screening_score", float("-inf")), reverse=True)
    print("\n15m Sweep 完成")
    print(f"汇总 CSV: {summary_csv}")
    print(f"汇总 JSON: {summary_json}")
    print("\nTop 10 参数组合:")
    for row in ranked[:10]:
        print(
            f"  score={row.get('screening_score'):.4f} "
            f"horizon={row.get('horizon_minutes')}m/{row.get('horizon_label')} "
            f"max_rows={row.get('max_rows')} "
            f"seasonality={row.get('seasonality_minutes')}m/{row.get('seasonality_label')} "
            f"estimator={row.get('estimator')} risk={row.get('risk_threshold')} "
            f"regressors={row.get('use_regressors')} "
            f"dir={row.get('directional_accuracy')} mae={row.get('mae_simple_return')} "
            f"high_risk_rate={row.get('high_risk_actual_down_rate')}"
        )

    selected: list[dict[str, Any]] = []
    evaluated: list[dict[str, Any]] = []
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
        else:
            for row in selected:
                print(
                    f"  selected score={row.get('screening_score'):.4f} "
                    f"horizon={row.get('horizon_minutes')}m/{row.get('horizon_label')} "
                    f"max_rows={row.get('max_rows')} "
                    f"seasonality={row.get('seasonality_minutes')}m/{row.get('seasonality_label')} "
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

    if args.export_gpt_json:
        packet_path = export_gpt_packet(
            args=args,
            evaluated=evaluated or selected,
            horizon_minutes_list=horizon_minutes_list,
            seasonality_minutes_list=seasonality_minutes_list,
            test_sizes=test_sizes,
            max_rows_list=max_rows_list,
            risk_thresholds=risk_thresholds,
            estimators=estimators,
            regressor_modes=regressor_modes,
            started=started,
        )
        print(f"\nGPT packet JSON: {packet_path}")


def export_gpt_packet(
    *,
    args: argparse.Namespace,
    evaluated: list[dict[str, Any]],
    horizon_minutes_list: list[int],
    seasonality_minutes_list: list[int],
    test_sizes: list[int],
    max_rows_list: list[int],
    risk_thresholds: list[float],
    estimators: list[str],
    regressor_modes: list[str],
    started: str,
) -> str:
    """读取数据范围信息并写出 gpt_packet.json。"""
    from analysis.orbit_wld_experiment import load_klines

    stem = os.path.splitext(os.path.basename(args.input))[0]
    asset = stem.rsplit("_", 1)[0] if "_" in stem else stem
    interval_label = format_horizon_label(args.interval_minutes)

    data_rows = 0
    latest_utc, latest_beijing = "", ""
    try:
        raw_df = load_klines(args.input)
        data_rows = len(raw_df)
        latest_utc, latest_beijing = get_latest_times(raw_df)
    except Exception as exc:
        print(f"  读取数据范围失败，GPT packet 的 data 字段将为空: {exc}")

    sweep_config = {
        "horizon_minutes": horizon_minutes_list,
        "test_sizes": test_sizes,
        "max_rows_list": max_rows_list,
        "seasonality_minutes": seasonality_minutes_list,
        "estimators": estimators,
        "risk_thresholds": risk_thresholds,
        "regressor_modes": regressor_modes,
        "interval_minutes": args.interval_minutes,
        "seed": args.seed,
        "eval_long_threshold": args.eval_long_threshold,
        "eval_short_threshold": args.eval_short_threshold,
        "eval_fee_rate": args.eval_fee_rate,
    }

    packet = build_gpt_packet(
        asset=asset,
        interval=interval_label,
        interval_minutes=args.interval_minutes,
        input_path=args.input,
        data_rows=data_rows,
        latest_utc=latest_utc,
        latest_beijing=latest_beijing,
        sweep_config=sweep_config,
        candidates=evaluated,
        max_candidates=args.gpt_max_candidates,
    )
    packet_path = os.path.join(args.output_dir, f"orbit_sweep_15m_{started}_gpt_packet.json")
    write_gpt_packet(packet, packet_path)
    return packet_path


if __name__ == "__main__":
    main()
