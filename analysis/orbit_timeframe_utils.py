"""通用时间粒度工具模块。

1h 脚本里 horizon 字段实际含义是「向后多少根 K 线」，字段名却写成 future_log_return_{horizon}h。
这在 1h 数据里成立（1 根 = 1 小时），但在 15m 等更细粒度里会误导用户。

本模块统一管理「分钟 <-> K 线根数」的换算与人类可读标签，让 15m / 30m 等短线实验
对外只暴露 horizon_minutes（真实预测时长），内部再换算成 K 线根数喂给 Orbit。
"""

from __future__ import annotations

import pandas as pd

MAX_HORIZON_MINUTES_DEFAULT = 1440  # 用户最多只愿意等 1 天


def parse_csv_ints(raw: str) -> list[int]:
    """逗号分隔字符串 -> int 列表，例如 '15,30,45' -> [15, 30, 45]。"""
    if raw is None:
        return []
    return [int(x.strip()) for x in str(raw).split(",") if x.strip()]


def minutes_to_bars(minutes: int, interval_minutes: int) -> int:
    """把预测时长（分钟）换算成 K 线根数。

    minutes 必须能被 interval_minutes 整除，否则抛错（避免出现「半根 K 线」的预测窗口）。
    """
    if interval_minutes <= 0:
        raise ValueError(f"interval_minutes 必须大于 0，收到 {interval_minutes}")
    if minutes <= 0:
        raise ValueError(f"minutes 必须大于 0，收到 {minutes}")
    if minutes % interval_minutes != 0:
        raise ValueError(
            f"minutes={minutes} 必须能被 interval_minutes={interval_minutes} 整除"
        )
    return minutes // interval_minutes


def bars_to_minutes(bars: int, interval_minutes: int) -> int:
    """K 线根数 -> 分钟（horizon 真实时长）。"""
    if bars <= 0:
        raise ValueError(f"bars 必须大于 0，收到 {bars}")
    if interval_minutes <= 0:
        raise ValueError(f"interval_minutes 必须大于 0，收到 {interval_minutes}")
    return bars * interval_minutes


def format_horizon_label(minutes: int) -> str:
    """分钟 -> 人类可读 horizon 标签。

    15 -> "15m"
    60 -> "1h"
    120 -> "2h"
    90 -> "1h30m"
    1440 -> "1d"
    """
    if minutes <= 0:
        raise ValueError(f"minutes 必须大于 0，收到 {minutes}")
    if minutes < 60:
        return f"{minutes}m"
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    hours, rest = divmod(minutes, 60)
    return f"{hours}h{rest}m"


def format_seasonality_label(minutes: int) -> str:
    """分钟 -> 季节性周期标签，格式与 horizon 一致。

    1440 -> "1d"（日内周期）
    10080 -> "7d"（周内周期）
    """
    if minutes <= 0:
        raise ValueError(f"minutes 必须大于 0，收到 {minutes}")
    return format_horizon_label(minutes)


def validate_horizon_minutes(
    horizon_minutes: int,
    interval_minutes: int,
    max_horizon_minutes: int = MAX_HORIZON_MINUTES_DEFAULT,
) -> int:
    """校验 horizon_minutes 合法性，返回换算后的 K 线根数。

    校验项：
    - horizon_minutes、interval_minutes 为正
    - horizon_minutes <= max_horizon_minutes（默认 1440 分钟 = 1 天）
    - horizon_minutes 能被 interval_minutes 整除
    """
    if horizon_minutes <= 0:
        raise ValueError(f"horizon_minutes 必须大于 0，收到 {horizon_minutes}")
    if interval_minutes <= 0:
        raise ValueError(f"interval_minutes 必须大于 0，收到 {interval_minutes}")
    if horizon_minutes > max_horizon_minutes:
        raise ValueError(
            f"horizon_minutes={horizon_minutes} 超过上限 {max_horizon_minutes} 分钟（1 天），"
            "用户最多只愿意等 1 天"
        )
    return minutes_to_bars(horizon_minutes, interval_minutes)


def get_latest_times(df: pd.DataFrame) -> tuple[str, str]:
    """返回数据最新一根 K 线的 UTC / 北京时间字符串。

    数据采集器写入的 date 列是 tz-naive 的 UTC 时间，这里先 localize 成 UTC，
    再转成 Asia/Shanghai。
    """
    if "date" not in df.columns or df.empty:
        raise ValueError("DataFrame 缺少 date 列或为空，无法获取最新时间")
    latest = pd.Timestamp(df["date"].max())
    if latest.tzinfo is None:
        latest_utc = latest.tz_localize("UTC")
    else:
        latest_utc = latest.tz_convert("UTC")
    latest_beijing = latest_utc.tz_convert("Asia/Shanghai")
    return (
        latest_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        latest_beijing.strftime("%Y-%m-%d %H:%M:%S Beijing"),
    )


def filter_closed_klines(df: pd.DataFrame, interval_minutes: int) -> pd.DataFrame:
    """可选：排除当前未收盘 K 线。

    K 线在 open_time + interval_minutes 之后才算收盘。数据采集器通常只落盘已收盘 K 线，
    本函数是对「CSV 里可能混入半根在走的数据」的兜底过滤，不修改原 DataFrame。
    """
    if "date" not in df.columns or df.empty:
        return df
    now = pd.Timestamp.utcnow().tz_localize(None)
    cutoff = now - pd.Timedelta(minutes=interval_minutes)
    return df[df["date"] <= cutoff].copy()
