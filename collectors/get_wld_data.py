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
}


def _request_klines(symbol: str, interval: str, limit: int,
                    end_time: int | None = None, retries: int = 3) -> list:
    """带重试的 Binance K线请求。"""
    params = {"symbol": symbol, "interval": interval, "limit": limit}
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
    df = pd.DataFrame(data, columns=ALL_COLS)
    df["date"] = pd.to_datetime(df["open_time"], unit="ms")
    for col in OHLCV_COLS:
        df[col] = df[col].astype(float)
    df["trades"] = df["trades"].astype(int)
    return df[KEEP_COLS]


def _latest_closed_end_time(interval: str) -> int | None:
    """返回最新已收盘 K 线的 endTime，避免采集未完成的当前 K 线。"""
    interval_ms = INTERVAL_MS.get(interval)
    if interval_ms is None:
        return None
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    current_open_ms = now_ms - (now_ms % interval_ms)
    return current_open_ms - 1


def get_binance_klines(symbol: str = "WLDUSDT", interval: str = "1d",
                       total: int = 1000) -> pd.DataFrame:
    """获取 Binance 已收盘 K线数据，自动向历史分页以突破 1000 条限制。

    Args:
        symbol:    交易对，如 WLDUSDT
        interval:  K线周期，如 1d / 4h / 1h / 15m
        total:     需要获取的总条数
    """
    all_frames: list[pd.DataFrame] = []
    fetched = 0
    end_time = _latest_closed_end_time(interval)
    if end_time is None:
        logger.warning("未知周期 %s，无法自动排除未收盘 K 线", interval)

    while fetched < total:
        batch = min(MAX_LIMIT, total - fetched)
        data = _request_klines(symbol, interval, batch, end_time)
        if not data:
            logger.error("返回数据为空，停止获取")
            break

        df = _parse_klines(data)
        all_frames.append(df)
        fetched += len(df)

        # 下一批向历史回溯，从当前批次最早 K 线之前继续取。
        end_time = data[0][0] - 1
        logger.info("已获取 %d / %d 条", fetched, total)

        if len(data) < batch:
            break  # 数据已到最早可用

    if not all_frames:
        return pd.DataFrame()

    result = pd.concat(all_frames, ignore_index=True)
    result.drop_duplicates(subset=["date"], inplace=True)
    result.sort_values("date", inplace=True)
    result.reset_index(drop=True, inplace=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="获取 Binance K线数据")
    parser.add_argument("-s", "--symbol", default="WLDUSDT", help="交易对")
    parser.add_argument("-i", "--interval", default="1d", help="K线周期 (1d/4h/1h/15m)")
    parser.add_argument("-n", "--total", type=int, default=1000, help="获取总条数")
    args = parser.parse_args()

    df = get_binance_klines(args.symbol, args.interval, args.total)
    if df.empty:
        logger.error("未获取到数据，退出")
    else:
        os.makedirs(DATA_DIR, exist_ok=True)
        out_path = os.path.join(DATA_DIR, f"{args.symbol}_{args.interval}.csv")
        df.to_csv(out_path, index=False)
        logger.info(
            "数据范围(UTC): %s ~ %s",
            df["date"].iloc[0],
            df["date"].iloc[-1],
        )
        logger.info("已保存 %d 条数据到 %s", len(df), out_path)
