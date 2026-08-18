"""テスト用の CLI 応答フィクスチャと、差し替え用のランナー。"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from decimal import Decimal
from typing import Any, Sequence

from nampinonychus import config as config_module
from nampinonychus.observe import Guards, Market
from nampinonychus.orders import PairSpec
from nampinonychus.state import Account, Ladder, OpenOrder, Position, State

TZ = "Asia/Tokyo"


def load_config() -> config_module.Config:
    """正本の agent.yaml をそのまま使う（テストが設定の検証も兼ねる）。"""
    return config_module.load()


def at(text: str) -> datetime:
    from nampinonychus import timeutil

    return timeutil.from_iso(text, TZ)


PAIR_ROW = {
    "name": "btc_jpy",
    "base_asset": "btc",
    "quote_asset": "jpy",
    "maker_fee_rate_base": 0,
    "taker_fee_rate_base": 0.0012,
    "maker_fee_rate_quote": 0,
    "taker_fee_rate_quote": 0.0012,
    "unit_amount": 0.0001,
    "limit_max_amount": 1000,
    "market_max_amount": 500,
    "price_digits": 0,
    "amount_digits": 4,
    "is_enabled": True,
    "stop_order": False,
    "stop_order_and_cancel": False,
}


def pair_spec(**overrides: Any) -> PairSpec:
    values: dict[str, Any] = {
        "unit_amount": Decimal("0.0001"),
        "limit_max_amount": Decimal(1000),
        "market_max_amount": Decimal(500),
        "price_digits": 0,
        "maker_fee_rate_quote": Decimal(0),
        "taker_fee_rate_quote": Decimal("0.0012"),
        "is_enabled": True,
    }
    values.update(overrides)
    return PairSpec(**values)


def market(last: str = "14700000", anchor: str = "15000000", age_sec: float = 5.0) -> Market:
    return Market(
        pair="btc_jpy",
        last=Decimal(last),
        anchor=Decimal(anchor),
        observed_at=at("2026-08-18T09:00:00+09:00"),
        age_sec=age_sec,
    )


def guards(exchange: str = "NORMAL", circuit: str = "NONE") -> Guards:
    return Guards(exchange_status=exchange, circuit_mode=circuit)


def state(
    *,
    position: str = "0",
    avg_cost: str | None = None,
    step: int = 0,
    used_budget: str = "0",
    last_fill_price: str | None = None,
    last_fill_at: str | None = None,
    cooldown_until: str | None = None,
    fills_today: int = 0,
    sold_in_round: str = "0",
    cash: str = "1000000",
    cash_available: str | None = None,
    equity: str | None = None,
    opened_at: str | None = None,
    age_days: float | None = None,
    pending_buy: Sequence[OpenOrder] = (),
    pending_sell: Sequence[OpenOrder] = (),
    mismatch: bool = False,
) -> State:
    amount = Decimal(position)
    available = Decimal(cash_available if cash_available is not None else cash)
    return State(
        position=Position(
            amount=amount,
            avg_cost_jpy=Decimal(avg_cost) if avg_cost else None,
            opened_at=at(opened_at) if opened_at else None,
            age_days=age_days,
        ),
        ladder=Ladder(
            step=step,
            used_budget_jpy=Decimal(used_budget),
            last_fill_price_jpy=Decimal(last_fill_price) if last_fill_price else None,
            last_fill_at=at(last_fill_at) if last_fill_at else None,
            cooldown_until=at(cooldown_until) if cooldown_until else None,
            fills_today=fills_today,
            sold_in_round=Decimal(sold_in_round),
        ),
        account=Account(
            initial_jpy=Decimal(1000000),
            cash_total_jpy=Decimal(cash),
            cash_locked_jpy=Decimal(cash) - available,
            cash_available_jpy=available,
            base_total=amount,
            equity_jpy=Decimal(equity) if equity else Decimal(cash) + amount * Decimal("14700000"),
        ),
        pending_buy=tuple(pending_buy),
        pending_sell=tuple(pending_sell),
        realized_pnl_jpy=Decimal(0),
        unrealized_pnl_jpy=Decimal(0),
        position_mismatch=mismatch,
    )


def open_order(
    order_id: str, side: str, price: str, amount: str, created_at: str = "2026-08-18T00:00:00+09:00"
) -> OpenOrder:
    return OpenOrder(
        id=order_id,
        side=side,
        price=Decimal(price),
        amount=Decimal(amount),
        created_at=at(created_at),
    )


# テストの基準時刻（2026-08-18T09:00:00+09:00）をエポックミリ秒で持つ。
NOW_MS = 1787011200000


def daily_candles(highs: Sequence[int], start_ms: int | None = None) -> list[dict]:
    # 最後の1本が当日の足になるように並べる。
    first = start_ms if start_ms is not None else NOW_MS - 86_400_000 * (len(highs) - 1)
    return [
        {
            "open": high - 100000,
            "high": high,
            "low": high - 200000,
            "close": high - 50000,
            "vol": 10,
            "timestamp": first + index * 86_400_000,
        }
        for index, high in enumerate(highs)
    ]


class FakeCli:
    """argv からキーを作り、あらかじめ用意した応答を返す。"""

    def __init__(self, responses: dict[str, Any], errors: dict[str, str] | None = None) -> None:
        self.responses = responses
        self.errors = errors or {}
        self.calls: list[str] = []

    @staticmethod
    def key_of(argv: Sequence[str]) -> str:
        parts = [a for a in argv[1:] if not a.startswith("--")]
        return " ".join(parts[:2]) if parts and parts[0] == "paper" else (parts[0] if parts else "")

    def __call__(
        self, argv: Sequence[str], env: dict[str, str], timeout: int
    ) -> "subprocess.CompletedProcess[str]":
        self.calls.append(" ".join(argv))
        key = self.key_of(argv)
        if key in self.errors:
            body = {"success": False, "error": self.errors[key], "exitCode": 1}
            return subprocess.CompletedProcess(list(argv), 1, json.dumps(body), "")
        if key not in self.responses:
            raise AssertionError(f"応答が用意されていません: {key} ({' '.join(argv)})")
        body = {"success": True, "data": self.responses[key]}
        return subprocess.CompletedProcess(list(argv), 0, json.dumps(body), "")


def default_responses(
    *,
    last: int = 14_700_000,
    highs: Sequence[int] = (14_000_000, 14_500_000, 15_000_000, 14_900_000, 14_800_000, 14_750_000, 14_700_000),
    assets: Sequence[dict] | None = None,
    pnl: dict | None = None,
    active_orders: Sequence[dict] = (),
    history: Sequence[dict] = (),
    ticker_ms: int = NOW_MS - 5_000,
) -> dict[str, Any]:
    return {
        "status": [{"pair": "btc_jpy", "status": "NORMAL", "min_amount": "0.0001"}],
        "circuit-break": {"mode": "NONE", "fee_type": "NORMAL", "timestamp": ticker_ms},
        "ticker": {
            "sell": last + 1000,
            "buy": last - 1000,
            "high": last + 50000,
            "low": last - 50000,
            "open": last,
            "last": last,
            "vol": 100,
            "timestamp": ticker_ms,
        },
        "candles": daily_candles(list(highs)),
        "pairs": [PAIR_ROW],
        "paper tick": {"filled": [], "warnings": [], "lastTickAt": "2026-08-18T00:00:00.000Z"},
        "paper assets": list(assets)
        if assets is not None
        else [{"asset": "jpy", "total": 1000000, "locked": 0, "available": 1000000}],
        "paper pnl": pnl if pnl is not None else {"perPair": {}, "total": {"realizedPnl": 0, "unrealizedPnl": 0, "totalPnl": 0}},
        "paper active-orders": list(active_orders),
        "paper trade-history": list(history),
        "paper create-order": {"placed": {"id": "new-order"}},
        "paper cancel-order": {"canceled": {"id": "old-order"}},
    }
