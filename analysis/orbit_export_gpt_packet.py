"""把 15m sweep 的 Top 候选参数和关键指标打包成一个可一次性上传给 GPT 的 JSON。

GPT packet 结构：
- experiment: 资产、周期、输入文件、生成时间
- data: 数据范围、行数、最新 K 线时间（UTC + 北京）
- sweep_config: 本次 sweep 的参数网格
- top_candidates: 每个候选的完整参数 + 点预测 / 概率 / 信号效用三类指标 + 输出路径
- notes: 解释 horizon_minutes vs horizon_bars，以及 signal utility 不是正式回测
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Iterable


def _read_json(path: str | None) -> dict[str, Any] | None:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _pick(source: dict[str, Any] | None, keys: Iterable[str]) -> dict[str, Any]:
    """从 source 里挑出 keys，缺失则填 None。"""
    if not source:
        return {key: None for key in keys}
    return {key: source.get(key) for key in keys}


def build_candidate(row: dict[str, Any], rank: int) -> dict[str, Any]:
    """把 sweep 里的一行 evaluated 结果转成 GPT packet 里的 candidate。"""
    eval_summary = _read_json(row.get("eval_summary_path"))

    point_keys = [
        "mae_simple_return",
        "rmse_simple_return",
        "directional_accuracy",
        "correlation_log_return",
        "actual_down_rate",
        "predicted_down_rate",
    ]
    prob_keys = [
        "brier_score",
        "log_loss",
        "calibration_mae",
        "average_predicted_probability",
        "actual_event_rate",
        "top_10pct_event_rate",
        "top_20pct_event_rate",
        "top_30pct_event_rate",
        "top_10pct_lift",
        "top_20pct_lift",
        "top_30pct_lift",
    ]
    signal_keys = [
        "average_long_return",
        "average_short_return",
        "hit_rate",
        "trade_count",
    ]

    candidate: dict[str, Any] = {
        "rank": rank,
        "horizon_minutes": row.get("horizon_minutes"),
        "horizon_bars": row.get("horizon_bars"),
        "horizon_label": row.get("horizon_label"),
        "test_size_bars": row.get("test_size_bars"),
        "test_size_days": row.get("test_size_days"),
        "max_rows": row.get("max_rows"),
        "seasonality_minutes": row.get("seasonality_minutes"),
        "seasonality_bars": row.get("seasonality_bars"),
        "seasonality_label": row.get("seasonality_label"),
        "risk_threshold": row.get("risk_threshold"),
        "screening_score": row.get("screening_score"),
        "point_metrics": _pick(
            eval_summary.get("point_metrics") if eval_summary else None, point_keys
        ),
        "probability_metrics": _pick(
            eval_summary.get("probability_metrics") if eval_summary else None, prob_keys
        ),
        "signal_utility_metrics": _pick(
            eval_summary.get("signal_utility_metrics") if eval_summary else None,
            signal_keys,
        ),
        "paths": {
            "predictions": row.get("prediction_path"),
            "metrics": row.get("metrics_path"),
            "summary": row.get("eval_summary_path"),
            "calibration": row.get("eval_calibration_path"),
        },
    }
    if eval_summary is None:
        candidate["eval_status"] = row.get("eval_status", "missing")
    return candidate


def build_gpt_packet(
    *,
    asset: str,
    interval: str,
    interval_minutes: int,
    input_path: str,
    data_rows: int,
    latest_utc: str,
    latest_beijing: str,
    sweep_config: dict[str, Any],
    candidates: list[dict[str, Any]],
    max_candidates: int = 20,
) -> dict[str, Any]:
    """组装完整的 GPT packet dict。"""
    top = [build_candidate(row, rank) for rank, row in enumerate(candidates[:max_candidates], start=1)]
    return {
        "experiment": {
            "asset": asset,
            "interval": interval,
            "interval_minutes": interval_minutes,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "input": input_path,
        },
        "data": {
            "rows": data_rows,
            "latest_utc": latest_utc,
            "latest_beijing": latest_beijing,
        },
        "sweep_config": sweep_config,
        "top_candidates": top,
        "notes": [
            "horizon_minutes 是真实预测时长（例如 120 表示预测未来 120 分钟 / 2 小时）；"
            "horizon_bars 是内部 K 线根数（horizon_minutes / interval_minutes），仅用于喂给 Orbit。",
            "seasonality_minutes 是季节性周期真实时长（如 1440=1 天，10080=1 周），seasonality_bars 是换算后的 K 线根数。",
            "signal_utility_metrics 只是按阈值把预测转成多空信号的粗略诊断，相邻 horizon 标签高度重叠，"
            "不是正式回测，不能直接当作可交易收益。",
            "point_metrics 衡量点预测误差与方向准确率；probability_metrics 衡量下行风险概率的校准与提升度（lift）；"
            "三者结合判断参数好坏，单一指标不足以下结论。",
        ],
    }


def write_gpt_packet(packet: dict[str, Any], path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(packet, f, ensure_ascii=False, indent=2)
    return path
