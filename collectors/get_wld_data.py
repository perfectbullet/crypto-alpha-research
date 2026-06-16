import argparse
import logging
import os
import time
from datetime import datetime, timezone

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
MAX_LIMIT = 1000  # Binance 单次请求上限

OHLCV_COLS = ["open", "high", "low", "close", "volume", "quote_volume"]
ALL_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]
KEEP_COLS = ["date"] + OHLCV_COLS + ["trades"]
INTERVAL_MS = {
    "1m": 60 * 1000,
    "3m": 3 * 60 * 1000,
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "2h": 2 * 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "6h": 6 * 60 * 60 * 1000,
    "8h": 8 * 60 * 60 * 1000,
    "12h": 12 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
    "3d": 3 * 24 * 60 * 60 * 1000,
    "1w": 7 * 24 * 60 * 60 * 1000,
}


def _request_klines(
    symbol: str,
    interval: str,
    limit: int,
    start_time: int | None = None,
    end_time: int | None = None,
    retries: int = 3,
) -> list:
    """带重试的 Binance K线请求。"""
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_time is not None:
        params["startTime"] = start_time
    if end_time is not None:
        params["endTime"] = end_time

    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.warning("请求失败 (%d/%d): %s", attempt, retries, e)
            if attempt < retries:
                time.sleep(2 ** attempt)
    return []


def _parse_klines(data: list) -> pd.DataFrame:
    """将原始 K线数据解析为 DataFrame。"""
    if not data:
        return pd.DataFrame(columns=KEEP_COLS)

    df = pd.DataFrame(data, columns=ALL_COLS)
    df["date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_convert(None)
    for col in OHLCV_COLS:
        df[col] = df[col].astype(float)
    df["trades"] = df["trades"].astype(int)
    return df[KEEP_COLS]


def _normalize_klines_df(df: pd.DataFrame) -> pd.DataFrame:
    """统一 CSV/接口数据的字段、时间和数值类型。"""
    if df.empty:
        return pd.DataFrame(columns=KEEP_COLS)

    missing_cols = [col for col in KEEP_COLS if col not in df.columns]
    if missing_cols:
        raise ValueError(f"数据缺少必要字段: {missing_cols}")

    df = df[KEEP_COLS].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True).dt.tz_convert(None)
    df.dropna(subset=["date"], inplace=True)

    for col in OHLCV_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["trades"] = pd.to_numeric(df["trades"], errors="coerce").fillna(0).astype(int)

    df.dropna(subset=OHLCV_COLS, inplace=True)
    return df[KEEP_COLS]


def _merge_klines(*frames: pd.DataFrame) -> pd.DataFrame:
    """合并 K线数据，按 date 去重，新数据覆盖旧数据。"""
    valid_frames = [frame for frame in frames if frame is not None and not frame.empty]
    if not valid_frames:
        return pd.DataFrame(columns=KEEP_COLS)

    df = pd.concat(valid_frames, ignore_index=True)
    df = _normalize_klines_df(df)
    df.drop_duplicates(subset=["date"], keep="last", inplace=True)
    df.sort_values("date", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def _date_to_ms(value) -> int:
    """将 DataFrame 中的 UTC 日期转换为毫秒时间戳。"""
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return int(ts.timestamp() * 1000)


def _latest_closed_end_time(interval: str) -> int:
    """返回最新已收盘 K 线的 endTime，避免采集未完成的当前 K 线。"""
    interval_ms = INTERVAL_MS.get(interval)
    if interval_ms is None:
        supported = ", ".join(sorted(INTERVAL_MS))
        raise ValueError(f"暂不支持周期 {interval}，可选周期: {supported}")

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    current_open_ms = now_ms - (now_ms % interval_ms)
    return current_open_ms - 1


def _load_existing_csv(path: str) -> pd.DataFrame:
    """读取已有 CSV；不存在时返回空表。"""
    if not os.path.exists(path):
        return pd.DataFrame(columns=KEEP_COLS)

    df = pd.read_csv(path)
    df = _normalize_klines_df(df)
    logger.info("已读取本地已有数据 %d 条: %s", len(df), path)
    return df


def _save_csv_atomic(df: pd.DataFrame, path: str) -> None:
    """原子化保存，避免写入过程中中断导致 CSV 损坏。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    df.to_csv(tmp_path, index=False)
    os.replace(tmp_path, path)


def _fetch_backward(symbol: str, interval: str, total: int, end_time: int | None) -> pd.DataFrame:
    """从 end_time 开始向历史方向分页获取 K线。"""
    if total <= 0:
        return pd.DataFrame(columns=KEEP_COLS)

    all_frames: list[pd.DataFrame] = []
    fetched = 0

    while fetched < total:
        batch = min(MAX_LIMIT, total - fetched)
        data = _request_klines(symbol, interval, batch, end_time=end_time)
        if not data:
            logger.warning("历史方向返回数据为空，停止获取")
            break

        df = _parse_klines(data)
        all_frames.append(df)
        fetched += len(df)

        # 下一批向历史回溯，从当前批次最早 K 线之前继续取。
        end_time = int(data[0][0]) - 1
        logger.info("历史方向已获取 %d / %d 条", fetched, total)

        if len(data) < batch:
            break  # 数据已到最早可用

    return _merge_klines(*all_frames)


def _fetch_forward(
    symbol: str,
    interval: str,
    start_time: int,
    end_time: int,
    max_records: int | None = None,
) -> pd.DataFrame:
    """从 start_time 开始向未来方向分页获取 K线，用于增量更新。"""
    interval_ms = INTERVAL_MS[interval]
    all_frames: list[pd.DataFrame] = []
    fetched = 0
    cursor = start_time

    while cursor <= end_time:
        if max_records is not None and fetched >= max_records:
            break

        remain = MAX_LIMIT if max_records is None else max_records - fetched
        batch = min(MAX_LIMIT, remain)
        data = _request_klines(symbol, interval, batch, start_time=cursor, end_time=end_time)
        if not data:
            logger.info("增量方向没有更多数据")
            break

        df = _parse_klines(data)
        all_frames.append(df)
        fetched += len(df)

        last_open_time = int(data[-1][0])
        next_cursor = last_open_time + interval_ms
        logger.info("增量方向已获取 %d 条，最新时间戳: %s", fetched, df["date"].iloc[-1])

        if next_cursor <= cursor or len(data) < batch:
            break
        cursor = next_cursor

    return _merge_klines(*all_frames)


def get_binance_klines(symbol: str = "WLDUSDT", interval: str = "1d",
                       total: int = 1000) -> pd.DataFrame:
    """获取 Binance 最新已收盘 K线数据，自动向历史分页以突破 1000 条限制。

    Args:
        symbol:    交易对，如 WLDUSDT
        interval:  K线周期，如 1d / 4h / 1h / 15m
        total:     需要获取的总条数
    """
    end_time = _latest_closed_end_time(interval)
    return _fetch_backward(symbol, interval, total, end_time=end_time)


def update_binance_klines_csv(
    symbol: str = "WLDUSDT",
    interval: str = "1d",
    total: int = 1000,
    out_path: str | None = None,
    full_refresh: bool = False,
    trim_to_total: bool = True,
) -> pd.DataFrame:
    """增量更新 CSV，跳过本地已经存在的 K线数据。

    处理逻辑：
    1. CSV 不存在或 full_refresh=True：按原逻辑拉取最近 total 条。
    2. CSV 已存在：从本地最后一根 K线的下一周期开始拉取，只请求缺失的新数据。
    3. 如果本地数据少于 total，再从本地最早一根 K线之前回补历史数据。
    4. 合并后按 date 去重排序，默认只保留最近 total 条，保持原 -n 语义。
    """
    if out_path is None:
        out_path = os.path.join(DATA_DIR, f"{symbol}_{interval}.csv")

    latest_end_time = _latest_closed_end_time(interval)
    interval_ms = INTERVAL_MS[interval]
    existing_df = pd.DataFrame(columns=KEEP_COLS) if full_refresh else _load_existing_csv(out_path)

    if existing_df.empty:
        logger.info("未发现本地数据，开始全量获取最近 %d 条", total)
        merged_df = _fetch_backward(symbol, interval, total, end_time=latest_end_time)
    else:
        merged_df = existing_df

        last_open_time = _date_to_ms(merged_df["date"].max())
        next_open_time = last_open_time + interval_ms

        if next_open_time <= latest_end_time:
            logger.info("开始增量更新: startTime=%d, endTime=%d", next_open_time, latest_end_time)
            new_df = _fetch_forward(symbol, interval, next_open_time, latest_end_time)
            merged_df = _merge_klines(merged_df, new_df)
            logger.info("新增数据 %d 条", len(new_df))
        else:
            logger.info("本地数据已经是最新，无需调用接口获取新 K线")

        if total > 0 and len(merged_df) < total:
            need = total - len(merged_df)
            oldest_open_time = _date_to_ms(merged_df["date"].min())
            logger.info("本地数据不足 %d 条，继续向历史回补 %d 条", total, need)
            backfill_df = _fetch_backward(symbol, interval, need, end_time=oldest_open_time - 1)
            merged_df = _merge_klines(backfill_df, merged_df)

    if trim_to_total and total > 0 and len(merged_df) > total:
        merged_df = merged_df.tail(total).reset_index(drop=True)

    if not merged_df.empty:
        _save_csv_atomic(merged_df, out_path)
        logger.info(
            "数据范围(UTC): %s ~ %s",
            merged_df["date"].iloc[0],
            merged_df["date"].iloc[-1],
        )
        logger.info("已保存 %d 条数据到 %s", len(merged_df), out_path)

    return merged_df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="增量获取 Binance K线数据")
    parser.add_argument("-s", "--symbol", default="WLDUSDT", help="交易对")
    parser.add_argument("-i", "--interval", default="1d", help="K线周期 (1d/4h/1h/15m)")
    parser.add_argument("-n", "--total", type=int, default=1000, help="最终保留的最近 K线条数")
    parser.add_argument("-o", "--output", default=None, help="输出 CSV 路径，默认 data/{symbol}_{interval}.csv")
    parser.add_argument("--full-refresh", action="store_true", help="忽略本地 CSV，重新拉取最近 total 条")
    parser.add_argument("--no-trim", action="store_true", help="不裁剪到 total 条，保留本地全部历史数据")
    args = parser.parse_args()

    df = update_binance_klines_csv(
        symbol=args.symbol,
        interval=args.interval,
        total=args.total,
        out_path=args.output,
        full_refresh=args.full_refresh,
        trim_to_total=not args.no_trim,
    )
    if df.empty:
        logger.error("未获取到数据，退出")