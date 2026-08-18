"""市場と取引所の観測。

観測できなかった値は None のままにする。推測で埋めない（CLAUDE.md）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from . import timeutil
from .cli import Client
from .config import Config
from .orders import PairSpec, to_decimal

# UTC の日ごとにファイルが分かれる足。境界をまたいでも lookback を満たすため、
# 前日ぶんも合わせて取得する。
INTRADAY_CANDLE_TYPES = frozenset({"1min", "5min", "15min", "30min", "1hour"})

# 取引所の稼働状態のうち、判断してよいもの。ここに無い値は理由を問わず HOLD。
TRADABLE_EXCHANGE_STATUS = frozenset({"NORMAL", "BUSY", "VERY_BUSY"})
# サーキットブレイクが発動していないことを表す mode。ここに無い値は HOLD。
NORMAL_CIRCUIT_MODES = frozenset({"NONE", "NORMAL"})


@dataclass(frozen=True)
class Guards:
    exchange_status: str | None
    circuit_mode: str | None

    @property
    def exchange_ok(self) -> bool:
        return self.exchange_status in TRADABLE_EXCHANGE_STATUS

    @property
    def circuit_ok(self) -> bool:
        return self.circuit_mode in NORMAL_CIRCUIT_MODES


@dataclass(frozen=True)
class Market:
    pair: str
    last: Decimal
    anchor: Decimal
    observed_at: datetime
    age_sec: float
    # 価格そのものの出典。判断に使った数値には、その数値を返したコマンドを添える。
    source_cmd: str

    @property
    def drop_from_anchor_pct(self) -> Decimal:
        if self.anchor <= 0:
            return Decimal(0)
        return (self.last - self.anchor) / self.anchor * Decimal(100)


def interval_minutes(candle_type: str) -> int:
    """足の種類から1本あたりの分数を求める。"""
    table = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "1hour": 60, "1day": 1440}
    if candle_type not in table:
        raise ValueError(f"扱えない足の種類です: {candle_type}")
    return table[candle_type]


def check_guards(client: Client, config: Config) -> Guards:
    """取引所の稼働状態とサーキットブレイクを見る。"""
    statuses = client.status().data
    status = None
    if isinstance(statuses, list):
        for row in statuses:
            if isinstance(row, dict) and row.get("pair") == config.pair:
                status = str(row.get("status"))
                break

    circuit = client.circuit_break(config.pair).data
    mode = str(circuit.get("mode")) if isinstance(circuit, dict) else None
    return Guards(exchange_status=status, circuit_mode=mode)


def fetch_anchor_candles(client: Client, config: Config, now: datetime) -> list[dict]:
    """アンカーの算出に使う足を取る。"""
    candle_type = config.anchor_candle_type
    if candle_type in INTRADAY_CANDLE_TYPES:
        today = now.astimezone(timezone.utc)
        yesterday = today - timedelta(days=1)
        response = client.candles(
            config.pair,
            candle_type,
            date_from=yesterday.strftime("%Y%m%d"),
            date_to=today.strftime("%Y%m%d"),
        )
    else:
        response = client.candles(config.pair, candle_type)
    candles = response.data
    if not isinstance(candles, list):
        raise ValueError("ローソク足の応答が配列ではありません")
    return candles


def anchor_price(
    candles: list[dict], lookback_minutes: int, interval_minutes: int, now: datetime
) -> Decimal:
    """直近 lookback_minutes ぶんの足の高値の最大値。

    現在進行中の未確定足も含める。含めることで、高値を更新している最中は
    `no_chase`（現在価格がアンカー以上なら買わない）が働く。
    """
    if not candles:
        raise ValueError("ローソク足が取得できませんでした")
    cutoff_ms = int(now.timestamp() * 1000) - lookback_minutes * 60_000
    recent = [c for c in candles if int(c["timestamp"]) >= cutoff_ms]
    # 欠損したデータで狭い高値を掴まないよう、期待本数の半分を下回れば判断しない。
    expected = max(1, lookback_minutes // interval_minutes)
    if len(recent) < max(1, expected // 2):
        raise ValueError(
            f"アンカーに使える足が {len(recent)} 本しかありません（期待 {expected} 本）"
        )
    return max(to_decimal(c["high"]) for c in recent)


def observe_market(client: Client, config: Config, now: datetime) -> Market:
    response = client.ticker(config.pair)
    ticker = response.data
    if not isinstance(ticker, dict) or ticker.get("last") is None:
        raise ValueError("ticker の last が取得できませんでした")
    observed_at = timeutil.from_epoch_ms(float(ticker["timestamp"]), config.timezone)

    candles = fetch_anchor_candles(client, config, now)

    return Market(
        pair=config.pair,
        last=to_decimal(ticker["last"]),
        anchor=anchor_price(
            candles,
            config.anchor_lookback_minutes,
            interval_minutes(config.anchor_candle_type),
            now,
        ),
        observed_at=observed_at,
        age_sec=(now - observed_at).total_seconds(),
        source_cmd=response.source.cmd,
    )


def observe_pair_spec(client: Client, config: Config) -> PairSpec:
    rows = client.pairs().data
    if not isinstance(rows, list):
        raise ValueError("pairs の応答が配列ではありません")
    for row in rows:
        if isinstance(row, dict) and row.get("name") == config.pair:
            return PairSpec(
                unit_amount=to_decimal(row["unit_amount"]),
                limit_max_amount=to_decimal(row["limit_max_amount"]),
                market_max_amount=to_decimal(row["market_max_amount"]),
                price_digits=int(row["price_digits"]),
                maker_fee_rate_quote=to_decimal(row["maker_fee_rate_quote"]),
                taker_fee_rate_quote=to_decimal(row["taker_fee_rate_quote"]),
                is_enabled=bool(row["is_enabled"]),
            )
    raise ValueError(f"pairs に {config.pair} がありません")
