"""15m 短线 live signal 脚本。

基于 analysis/orbit_live_signal.py 改造：
- 复用其 run_live_signal / LiveSignalConfig，只在更外层把 horizon_minutes / seasonality_minutes
  换算成 K 线根数（bars）后传入。
- 输出文件名把 h{bars} 改写成 h{horizon_label}，并打印 horizon 的真实时长（120m / 2h），
  避免再把 horizon=8 误解成 8 小时。
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import asdict
from datetime import datetime

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from analysis.orbit_live_signal import LiveSignalConfig, run_live_signal, write_json  # noqa: E402
from analysis.orbit_timeframe_utils import (  # noqa: E402
    format_horizon_label,
    format_seasonality_label,
    minutes_to_bars,
    validate_horizon_minutes,
)


def _relabel_filename(path: str, horizon_bars: int, horizon_label: str) -> str:
    """把 live signal 输出文件名里的 h{bars} 段改成 h{horizon_label}。"""
    dirname, basename = os.path.split(path)
    old_seg = f"_orbit_live_h{horizon_bars}_"
    new_seg = f"_orbit_live_h{horizon_label}_"
    return os.path.join(dirname, basename.replace(old_seg, new_seg))


def _latest_beijing_str(date_value) -> str:
    """把 tz-naive UTC 时间转成北京时间字符串。"""
    ts = pd.Timestamp(date_value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("Asia/Shanghai").strftime("%Y-%m-%d %H:%M:%S Beijing")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用已筛选的 Orbit 15m 参数生成最新可执行短线仓位信号")
    parser.add_argument("--input", default=os.path.join(BASE_DIR, "data", "WLDUSDT_15m.csv"), help="输入 15m K 线 CSV")
    parser.add_argument("--output-dir", default=os.path.join(BASE_DIR, "reports", "orbit_live_15m"), help="输出目录")
    parser.add_argument("--interval-minutes", type=int, default=15, help="K 线周期（分钟），默认 15")
    parser.add_argument("--horizon-minutes", type=int, default=120, help="真实预测时长（分钟），默认 120")
    parser.add_argument("--max-rows", type=int, default=16000, help="最多使用最近 N 条有效训练样本；<=0 表示全量")
    parser.add_argument("--seasonality-minutes", type=int, default=1440, help="季节性周期（分钟），默认 1440")
    parser.add_argument("--estimator", default="stan-map", choices=["stan-map", "stan-mcmc", "pyro-svi"], help="Orbit 估计器")
    parser.add_argument("--seed", type=int, default=2026, help="随机种子")
    parser.add_argument("--risk-threshold", type=float, default=-0.02, help="风险事件阈值（普通收益率），默认 -2%%")
    parser.add_argument("--prediction-rows", type=int, default=1, help="输出最近 N 条未标注样本的预测")
    parser.add_argument("--current-position", type=float, default=0.0, help="当前实际仓位，0 表示空仓")
    parser.add_argument("--long-threshold", type=float, default=0.008, help="允许做多的预测收益阈值")
    parser.add_argument("--buy-risk-max", type=float, default=0.35, help="允许做多的最大风险概率")
    parser.add_argument("--strong-long-threshold", type=float, default=0.015, help="强做多预测收益阈值")
    parser.add_argument("--strong-risk-max", type=float, default=0.25, help="强做多允许的最大风险概率")
    parser.add_argument("--reduce-pred-threshold", type=float, default=0.003, help="低于该预测收益则降仓")
    parser.add_argument("--reduce-risk-threshold", type=float, default=0.60, help="高于该风险概率则降仓")
    parser.add_argument("--clear-pred-threshold", type=float, default=-0.005, help="低于等于该预测收益则空仓")
    parser.add_argument("--clear-risk-threshold", type=float, default=0.80, help="高于等于该风险概率则空仓")
    parser.add_argument("--reduced-position", type=float, default=0.30, help="信号转弱时目标仓位")
    parser.add_argument("--base-position", type=float, default=0.60, help="普通做多目标仓位")
    parser.add_argument("--max-position", type=float, default=1.00, help="强信号最大目标仓位")
    parser.add_argument("--no-regressors", action="store_true", help="不使用 OHLCV 派生外生变量")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    horizon_bars = validate_horizon_minutes(args.horizon_minutes, args.interval_minutes)
    seasonality_bars = minutes_to_bars(args.seasonality_minutes, args.interval_minutes)
    horizon_label = format_horizon_label(args.horizon_minutes)
    seasonality_label = format_seasonality_label(args.seasonality_minutes)

    config = LiveSignalConfig(
        input=args.input,
        output_dir=args.output_dir,
        horizon=horizon_bars,
        max_rows=args.max_rows,
        seasonality=seasonality_bars,
        estimator=args.estimator,
        seed=args.seed,
        risk_threshold=args.risk_threshold,
        prediction_rows=args.prediction_rows,
        current_position=args.current_position,
        current_position_source="cli:--current-position",
        trade_record_json=None,
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

    # 追加人类可读时间粒度字段到信号 CSV 和摘要。
    for col, value in {
        "interval_minutes": args.interval_minutes,
        "horizon_minutes": args.horizon_minutes,
        "horizon_bars": horizon_bars,
        "horizon_label": horizon_label,
    }.items():
        result[col] = value
    summary["interval_minutes"] = args.interval_minutes
    summary["horizon_minutes"] = args.horizon_minutes
    summary["horizon_bars"] = horizon_bars
    summary["horizon_label"] = horizon_label
    summary["seasonality_minutes"] = args.seasonality_minutes
    summary["seasonality_bars"] = seasonality_bars
    summary["seasonality_label"] = seasonality_label

    os.makedirs(config.output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    symbol = os.path.splitext(os.path.basename(config.input))[0]
    base = f"{symbol}_orbit_live_h{horizon_bars}_{timestamp}"
    csv_path = os.path.join(config.output_dir, f"{base}_signal.csv")
    json_path = os.path.join(config.output_dir, f"{base}_summary.json")

    result.to_csv(csv_path, index=False)
    write_json(json_path, summary)

    # 写完再重命名为带 horizon_label 的文件名。
    new_csv_path = _relabel_filename(csv_path, horizon_bars, horizon_label)
    new_json_path = _relabel_filename(json_path, horizon_bars, horizon_label)
    os.replace(csv_path, new_csv_path)
    os.replace(json_path, new_json_path)

    latest = result.tail(1).iloc[0]
    latest_date = pd.Timestamp(latest["date"])
    latest_utc_str = latest_date.strftime("%Y-%m-%d %H:%M:%S UTC")
    latest_beijing_str = _latest_beijing_str(latest["date"])

    print("\nOrbit 15m 最新仓位信号完成")
    print(f"信号明细: {new_csv_path}")
    print(f"摘要 JSON: {new_json_path}")
    print("\n最新信号:")
    print(f"  interval: {args.interval_minutes}m")
    print(f"  horizon: {args.horizon_minutes}m / {horizon_label}（{horizon_bars} 根 K 线）")
    print(f"  latest_signal_date UTC: {latest_utc_str}")
    print(f"  latest_signal_date Beijing: {latest_beijing_str}")
    print(f"  predicted_simple_return_{horizon_label}: {float(latest['predicted_simple_return']):.2%}")
    print(f"  risk_prob_return_le_{config.risk_threshold:.2%}: {float(latest['risk_prob_return_le_threshold']):.2%}")
    print(f"  signal: {latest['signal']}")
    print(f"  action: {latest['action']}")
    print(f"  current_position: {float(latest['current_position']):.2%}")
    print(f"  target_position: {float(latest['target_position']):.2%}")
    print(f"  trade_delta: {float(latest['trade_delta']):.2%}")


if __name__ == "__main__":
    main()
