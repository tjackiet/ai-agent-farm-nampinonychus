"""市場と取引所の観測。

観測できなかった値は None のままにする。推測で埋めない（CLAUDE.md）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from . import timeutil
from .cli import Client
from .config import Config
from .orders import PairSpec, to_decimal

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


def anchor_price(candles: list[dict], lookback_days: int) -> Decimal:
    """直近 lookback_days 本の日足高値の最大値。

    当日の未確定足も含める。含めることで、7日高値を更新している最中は
    `no_chase`（現在価格がアンカー以上なら買わない）が働く。
    """
    if not candles:
        raise ValueError("日足が取得できませんでした")
    ordered = sorted(candles, key=lambda c: c["timestamp"])[-lookback_days:]
    return max(to_decimal(c["high"]) for c in ordered)


def observe_market(client: Client, config: Config, now: datetime) -> Market:
    response = client.ticker(config.pair)
    ticker = response.data
    if not isinstance(ticker, dict) or ticker.get("last") is None:
        raise ValueError("ticker の last が取得できませんでした")
    observed_at = timeutil.from_epoch_ms(float(ticker["timestamp"]), config.timezone)

    candles = client.candles(config.pair, "1day").data
    if not isinstance(candles, list):
        raise ValueError("日足の応答が配列ではありません")

    return Market(
        pair=config.pair,
        last=to_decimal(ticker["last"]),
        anchor=anchor_price(candles, config.anchor_lookback_days),
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
