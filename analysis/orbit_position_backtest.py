import argparse
import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import datetime

import numpy as np
import pandas as pd


REQUIRED_KLINE_COLS = ["date", "open", "high", "low", "close", "volume", "quote_volume", "trades"]


@dataclass
class BacktestConfig:
    predictions: str
    klines: str
    output_dir: str
    fee_rate: float
    slippage_rate: float
    initial_equity: float
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
    min_trade_delta: float
    execution_delay_bars: int


@dataclass
class BacktestMetrics:
    rows: int
    start_date: str | None
    end_date: str | None
    initial_equity: float
    final_equity: float
    total_return: float
    buy_and_hold_return: float | None
    max_drawdown: float
    trade_count: int
    turnover: float
    total_cost: float
    time_in_market: float
    average_position: float
    average_target_position: float
    win_bar_rate: float | None
    average_bar_return: float
    volatility_bar_return: float
    sharpe_like_hourly: float | None
    long_signal_count: int
    flat_signal_count: int
    risk_clear_count: int
    pred_clear_count: int


def load_predictions(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"预测文件不存在: {path}")

    df = pd.read_csv(path)
    required = ["date", "prediction"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"预测 CSV 缺少必要字段: {missing}")

    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["prediction"] = pd.to_numeric(out["prediction"], errors="coerce")

    if "predicted_simple_return" in out.columns:
        out["predicted_simple_return"] = pd.to_numeric(out["predicted_simple_return"], errors="coerce")
    else:
        out["predicted_simple_return"] = np.expm1(out["prediction"])

    if "risk_prob_return_le_threshold" not in out.columns:
        raise ValueError("预测 CSV 缺少 risk_prob_return_le_threshold，无法做风控仓位回测")
    out["risk_prob_return_le_threshold"] = pd.to_numeric(out["risk_prob_return_le_threshold"], errors="coerce")

    out.replace([np.inf, -np.inf], np.nan, inplace=True)
    out.dropna(subset=["date", "prediction", "predicted_simple_return", "risk_prob_return_le_threshold"], inplace=True)
    out.sort_values("date", inplace=True)
    out.drop_duplicates(subset=["date"], keep="last", inplace=True)
    out.reset_index(drop=True, inplace=True)
    return out


def load_klines(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"K线文件不存在: {path}")

    df = pd.read_csv(path)
    missing = [col for col in REQUIRED_KLINE_COLS if col not in df.columns]
    if missing:
        raise ValueError(f"K线 CSV 缺少必要字段: {missing}")

    out = df[REQUIRED_KLINE_COLS].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    for col in REQUIRED_KLINE_COLS:
        if col == "date":
            continue
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out.replace([np.inf, -np.inf], np.nan, inplace=True)
    out.dropna(subset=REQUIRED_KLINE_COLS, inplace=True)
    out.sort_values("date", inplace=True)
    out.drop_duplicates(subset=["date"], keep="last", inplace=True)
    out.reset_index(drop=True, inplace=True)
    return out


def decide_target_position(row: pd.Series, config: BacktestConfig) -> tuple[float, str]:
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


def attach_execution_prices(pred: pd.DataFrame, klines: pd.DataFrame, delay_bars: int) -> pd.DataFrame:
    if delay_bars < 1:
        raise ValueError("execution_delay_bars 必须 >= 1，避免使用当前K线价格造成前视偏差")

    k = klines[["date", "open", "close"]].copy()
    k_dates = k["date"].to_numpy()
    rows = []

    for item in pred.itertuples(index=False):
        signal_date = getattr(item, "date")
        insertion = np.searchsorted(k_dates, np.datetime64(signal_date), side="right")
        exec_idx = insertion + delay_bars - 1
        next_idx = exec_idx + 1
        if exec_idx >= len(k) or next_idx >= len(k):
            continue

        exec_row = k.iloc[exec_idx]
        next_row = k.iloc[next_idx]
        rows.append(
            {
                "date": signal_date,
                "execution_date": exec_row["date"],
                "next_execution_date": next_row["date"],
                "execution_open": float(exec_row["open"]),
                "next_open": float(next_row["open"]),
                "asset_return_1h": float(next_row["open"] / exec_row["open"] - 1.0),
            }
        )

    exec_df = pd.DataFrame(rows)
    if exec_df.empty:
        raise ValueError("无法把预测时间对齐到K线执行价格，请检查 date 是否匹配")

    merged = pred.merge(exec_df, on="date", how="inner")
    merged.sort_values("execution_date", inplace=True)
    merged.drop_duplicates(subset=["execution_date"], keep="last", inplace=True)
    merged.reset_index(drop=True, inplace=True)
    return merged


def max_drawdown(equity: pd.Series) -> float:
    running_max = equity.cummax()
    dd = equity / running_max - 1.0
    value = float(dd.min())
    return value if math.isfinite(value) else 0.0


def run_backtest(config: BacktestConfig) -> tuple[pd.DataFrame, BacktestMetrics]:
    pred = load_predictions(config.predictions)
    klines = load_klines(config.klines)
    df = attach_execution_prices(pred, klines, config.execution_delay_bars)

    target_positions = []
    signal_names = []
    for _, row in df.iterrows():
        position, signal = decide_target_position(row, config)
        target_positions.append(float(np.clip(position, 0.0, config.max_position)))
        signal_names.append(signal)

    df["target_position"] = target_positions
    df["signal"] = signal_names

    equity = config.initial_equity
    current_position = 0.0
    records = []
    cost_rate = config.fee_rate + config.slippage_rate

    for row in df.itertuples(index=False):
        target_position = float(getattr(row, "target_position"))
        if abs(target_position - current_position) < config.min_trade_delta:
            target_position = current_position

        trade_delta = target_position - current_position
        cost = abs(trade_delta) * cost_rate
        gross_return = target_position * float(getattr(row, "asset_return_1h"))
        net_return = gross_return - cost
        equity = equity * (1.0 + net_return)
        current_position = target_position

        records.append(
            {
                "signal_date": getattr(row, "date"),
                "execution_date": getattr(row, "execution_date"),
                "next_execution_date": getattr(row, "next_execution_date"),
                "execution_open": getattr(row, "execution_open"),
                "next_open": getattr(row, "next_open"),
                "asset_return_1h": getattr(row, "asset_return_1h"),
                "predicted_log_return": getattr(row, "prediction"),
                "predicted_simple_return": getattr(row, "predicted_simple_return"),
                "risk_prob_return_le_threshold": getattr(row, "risk_prob_return_le_threshold"),
                "signal": getattr(row, "signal"),
                "target_position": getattr(row, "target_position"),
                "executed_position": current_position,
                "trade_delta": trade_delta,
                "cost_rate": cost_rate,
                "cost_return": cost,
                "gross_strategy_return": gross_return,
                "net_strategy_return": net_return,
                "equity": equity,
            }
        )

    out = pd.DataFrame(records)
    if out.empty:
        raise ValueError("回测结果为空")

    out["drawdown"] = out["equity"] / out["equity"].cummax() - 1.0
    out["is_trade"] = out["trade_delta"].abs() >= config.min_trade_delta
    out["buy_and_hold_equity"] = config.initial_equity * (1.0 + out["asset_return_1h"]).cumprod()

    buy_and_hold_return = None
    if len(out) > 0:
        buy_and_hold_return = float(out["buy_and_hold_equity"].iloc[-1] / config.initial_equity - 1.0)

    returns = out["net_strategy_return"]
    vol = float(returns.std()) if len(returns) > 1 else 0.0
    avg = float(returns.mean())
    sharpe_like = None
    if vol > 0 and math.isfinite(vol):
        sharpe_like = avg / vol * math.sqrt(24 * 365)

    metrics = BacktestMetrics(
        rows=len(out),
        start_date=str(out["execution_date"].iloc[0]) if len(out) else None,
        end_date=str(out["execution_date"].iloc[-1]) if len(out) else None,
        initial_equity=config.initial_equity,
        final_equity=float(out["equity"].iloc[-1]),
        total_return=float(out["equity"].iloc[-1] / config.initial_equity - 1.0),
        buy_and_hold_return=buy_and_hold_return,
        max_drawdown=max_drawdown(out["equity"]),
        trade_count=int(out["is_trade"].sum()),
        turnover=float(out["trade_delta"].abs().sum()),
        total_cost=float(out["cost_return"].sum()),
        time_in_market=float((out["executed_position"] > 0).mean()),
        average_position=float(out["executed_position"].mean()),
        average_target_position=float(out["target_position"].mean()),
        win_bar_rate=float((returns > 0).mean()) if len(returns) else None,
        average_bar_return=avg,
        volatility_bar_return=vol,
        sharpe_like_hourly=sharpe_like,
        long_signal_count=int(out["signal"].isin(["long", "strong_long"]).sum()),
        flat_signal_count=int((out["signal"] == "flat").sum()),
        risk_clear_count=int((out["signal"] == "clear_by_risk").sum()),
        pred_clear_count=int((out["signal"] == "clear_by_prediction").sum()),
    )
    return out, metrics


def save_plot(result: pd.DataFrame, output_path: str) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    plt.figure(figsize=(12, 5))
    plt.plot(result["execution_date"], result["equity"], label="strategy equity")
    if "buy_and_hold_equity" in result.columns:
        plt.plot(result["execution_date"], result["buy_and_hold_equity"], label="buy and hold baseline")
    plt.title("Orbit position backtest")
    plt.xlabel("date")
    plt.ylabel("equity")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="把 Orbit 预测结果转换为动态仓位，并做非重叠的逐小时仓位回测")
    parser.add_argument("--predictions", required=True, help="orbit_wld_experiment.py 生成的 *_predictions.csv")
    parser.add_argument("--klines", default="data/WLDUSDT_1h.csv", help="原始小时K线 CSV")
    parser.add_argument("--output-dir", default="reports/orbit_backtest", help="输出目录")
    parser.add_argument("--fee-rate", type=float, default=0.001, help="单边手续费率，默认 0.1%")
    parser.add_argument("--slippage-rate", type=float, default=0.0005, help="单边滑点，默认 0.05%")
    parser.add_argument("--initial-equity", type=float, default=1000.0, help="初始资金，仅用于权益曲线缩放")
    parser.add_argument("--long-threshold", type=float, default=0.04, help="允许做多的预测收益阈值")
    parser.add_argument("--buy-risk-max", type=float, default=0.35, help="允许做多的最大风险概率")
    parser.add_argument("--strong-long-threshold", type=float, default=0.07, help="强做多预测收益阈值")
    parser.add_argument("--strong-risk-max", type=float, default=0.25, help="强做多允许的最大风险概率")
    parser.add_argument("--reduce-pred-threshold", type=float, default=0.02, help="低于该预测收益则降为 reduced_position")
    parser.add_argument("--reduce-risk-threshold", type=float, default=0.55, help="高于该风险概率则降为 reduced_position")
    parser.add_argument("--clear-pred-threshold", type=float, default=0.0, help="低于等于该预测收益则空仓")
    parser.add_argument("--clear-risk-threshold", type=float, default=0.75, help="高于等于该风险概率则空仓")
    parser.add_argument("--reduced-position", type=float, default=0.10, help="信号转弱时目标仓位")
    parser.add_argument("--base-position", type=float, default=0.20, help="普通做多目标仓位")
    parser.add_argument("--max-position", type=float, default=0.30, help="强信号最大目标仓位")
    parser.add_argument("--min-trade-delta", type=float, default=0.01, help="小于该仓位变化则不交易，减少抖动")
    parser.add_argument("--execution-delay-bars", type=int, default=1, help="信号后第几根K线开盘执行，默认下一根")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = BacktestConfig(
        predictions=args.predictions,
        klines=args.klines,
        output_dir=args.output_dir,
        fee_rate=args.fee_rate,
        slippage_rate=args.slippage_rate,
        initial_equity=args.initial_equity,
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
        min_trade_delta=args.min_trade_delta,
        execution_delay_bars=args.execution_delay_bars,
    )

    result, metrics = run_backtest(config)

    os.makedirs(config.output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pred_name = os.path.splitext(os.path.basename(config.predictions))[0]
    base = f"{pred_name}_position_backtest_{timestamp}"

    trades_path = os.path.join(config.output_dir, f"{base}_trades.csv")
    summary_path = os.path.join(config.output_dir, f"{base}_summary.json")
    plot_path = os.path.join(config.output_dir, f"{base}_equity.png")

    result.to_csv(trades_path, index=False)
    write_json(summary_path, {"config": asdict(config), "metrics": asdict(metrics)})
    save_plot(result, plot_path)

    print("\nOrbit 仓位回测完成")
    print(f"交易明细: {trades_path}")
    print(f"摘要指标: {summary_path}")
    print(f"权益曲线: {plot_path}")
    print("\n核心指标:")
    print(f"  rows: {metrics.rows}")
    print(f"  period: {metrics.start_date} -> {metrics.end_date}")
    print(f"  total_return: {metrics.total_return:.2%}")
    if metrics.buy_and_hold_return is not None:
        print(f"  buy_and_hold_return: {metrics.buy_and_hold_return:.2%}")
    print(f"  max_drawdown: {metrics.max_drawdown:.2%}")
    print(f"  trade_count: {metrics.trade_count}")
    print(f"  turnover: {metrics.turnover:.2f}")
    print(f"  total_cost: {metrics.total_cost:.2%}")
    print(f"  time_in_market: {metrics.time_in_market:.2%}")
    print(f"  average_position: {metrics.average_position:.2%}")
    print(f"  win_bar_rate: {metrics.win_bar_rate:.2%}" if metrics.win_bar_rate is not None else "  win_bar_rate: null")


if __name__ == "__main__":
    main()
