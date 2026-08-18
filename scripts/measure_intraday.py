#!/usr/bin/env python3
"""日中の値動きを測り、「1日に何回発火するか」を数える。

パラメータを勘で決めないための道具。実際の日足・分足を取得し、
「アンカーから何％下」で何回買えて、そこから何％戻るまでに何時間かかるかを、
組み合わせごとに数える。売買はしない。読むだけ。

使いかた:

    .venv/bin/python scripts/measure_intraday.py --days=30

注意: 期間指定は1日1リクエストで取りに行くため、--days の数だけ CLI が
公開 API を叩く。長くしすぎない。
"""

from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nampinonychus import cli, config as config_module, timeutil  # noqa: E402

INTERVAL_MINUTES = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "1hour": 60}


@dataclass(frozen=True)
class Candle:
    high: float
    low: float
    close: float
    ts: int


@dataclass(frozen=True)
class Result:
    lookback_hours: float
    drop_pct: float
    entries: int
    entries_per_day: float
    exit_rate: float
    median_hours_to_exit: float | None


def fetch(client: cli.Client, pair: str, candle_type: str, days: int) -> list[Candle]:
    now = timeutil.now("Asia/Tokyo")
    start = now - timedelta(days=days)
    response = client.candles(
        pair,
        candle_type,
        date_from=start.strftime("%Y%m%d"),
        date_to=now.strftime("%Y%m%d"),
    )
    rows = response.data
    if not isinstance(rows, list) or not rows:
        raise SystemExit("ローソク足が取得できませんでした")
    candles = [
        Candle(high=float(r["high"]), low=float(r["low"]), close=float(r["close"]), ts=int(r["timestamp"]))
        for r in rows
    ]
    return sorted(candles, key=lambda c: c.ts)


def simulate(
    candles: list[Candle],
    interval_min: int,
    lookback_hours: float,
    drop_pct: float,
    take_profit_pct: float,
    cooldown_min: int,
    horizon_hours: float,
) -> Result:
    """指値が刺さる回数と、利確までの時間を数える。

    - アンカー: 直近 lookback_hours の高値（現在の足を含む）
    - 約定判定: その足の安値が指値以下（bitbank paper と同じ考えかた）
    - 利確判定: 以降 horizon_hours 以内に高値が目標へ届いたか
    """
    span = max(1, int(lookback_hours * 60 / interval_min))
    cooldown = max(1, int(cooldown_min / interval_min))
    horizon = max(1, int(horizon_hours * 60 / interval_min))

    entries: list[tuple[int, float]] = []
    blocked_until = 0
    for i in range(span, len(candles)):
        if i < blocked_until:
            continue
        anchor = max(c.high for c in candles[i - span + 1 : i + 1])
        limit = anchor * (1 - drop_pct / 100)
        if candles[i].low <= limit:
            entries.append((i, limit))
            blocked_until = i + cooldown

    exits = 0
    hours: list[float] = []
    for index, price in entries:
        target = price * (1 + take_profit_pct / 100)
        for j in range(index + 1, min(index + horizon + 1, len(candles))):
            if candles[j].high >= target:
                exits += 1
                hours.append((j - index) * interval_min / 60)
                break

    total_days = (candles[-1].ts - candles[0].ts) / 86_400_000 or 1
    return Result(
        lookback_hours=lookback_hours,
        drop_pct=drop_pct,
        entries=len(entries),
        entries_per_day=len(entries) / total_days,
        exit_rate=exits / len(entries) if entries else 0.0,
        median_hours_to_exit=statistics.median(hours) if hours else None,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="日中の発火頻度を測る（売買はしない）")
    parser.add_argument("--days", type=int, default=30, help="遡る日数（既定 30）")
    parser.add_argument("--type", default="15min", choices=sorted(INTERVAL_MINUTES), help="足の種類")
    parser.add_argument("--take-profit", type=float, default=0.5, help="利確の上昇率％（既定 0.5）")
    parser.add_argument("--cooldown-min", type=int, default=30, help="約定後の待機分（既定 30）")
    parser.add_argument("--horizon-hours", type=float, default=24.0, help="利確を待つ上限時間")
    args = parser.parse_args()

    config = config_module.load()
    client = cli.Client(config)
    interval = INTERVAL_MINUTES[args.type]

    # 手数料。指値は必ず maker。往復ぶんが利確幅から引かれる。
    spec_rows = client.pairs().data
    spec = next(r for r in spec_rows if r["name"] == config.pair)
    maker = float(spec["maker_fee_rate_quote"]) * 100
    taker = float(spec["taker_fee_rate_quote"]) * 100

    candles = fetch(client, config.pair, args.type, args.days)
    days = (candles[-1].ts - candles[0].ts) / 86_400_000
    ranges = [(c.high - c.low) / c.close * 100 for c in candles if c.close]
    ranges.sort()

    def pct(values: list[float], q: float) -> float:
        return values[min(len(values) - 1, int(len(values) * q))]

    print(f"ペア        : {config.pair}")
    print(f"期間        : {args.days} 日指定 / 実データ {days:.1f} 日ぶん（{args.type} × {len(candles)} 本）")
    print(f"手数料      : maker {maker:+.4f}% / taker {taker:+.4f}%")
    print(f"往復コスト  : {maker * 2:+.4f}%（指値で入って指値で出た場合）")
    print()
    print(f"{args.type}の値幅（高値-安値）: 中央値 {pct(ranges, 0.5):.3f}% / 上位25% {pct(ranges, 0.75):.3f}% / 上位10% {pct(ranges, 0.9):.3f}%")
    print()
    print(f"利確 +{args.take_profit}% / クールダウン {args.cooldown_min}分 として:")
    print()
    print("  アンカー   下落率    約定回数   1日あたり   利確到達   到達までの中央値")
    print("  --------   ------    --------   ---------   --------   ----------------")

    for lookback in (1, 2, 4, 8, 24):
        for drop in (0.3, 0.5, 0.8, 1.2, 2.0, 3.0):
            r = simulate(
                candles, interval, lookback, drop, args.take_profit, args.cooldown_min, args.horizon_hours
            )
            if r.entries == 0:
                continue
            hours = f"{r.median_hours_to_exit:.1f} 時間" if r.median_hours_to_exit is not None else "—"
            print(
                f"  {lookback:>3d} 時間   -{drop:>4.1f}%   {r.entries:>8d}   {r.entries_per_day:>9.2f}   "
                f"{r.exit_rate * 100:>7.0f}%   {hours:>16}"
            )
        print()

    print("読みかた:")
    print("- 「1日あたり」が欲しい取引回数に近い組み合わせを選ぶ")
    print("- 「利確到達」が低い組み合わせは、買ったまま戻らない時間が長い")
    print(f"- 利確幅は往復コスト（{maker * 2:+.4f}%）より十分大きくとる")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
