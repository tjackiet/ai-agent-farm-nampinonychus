"""現在状態の導出と status.yaml の書き出し。

階段の状態は記憶せず、bitbank paper の実測から毎回導出する
（memory-policy.md「現在：bitbank paper の実測」）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Sequence

import yaml

from . import timeutil
from .cli import Client
from .config import Config, REPO_ROOT
from .orders import to_decimal

# 建玉ゼロとみなす許容誤差。刻み（0.0001）より十分小さくとる。
DUST = Decimal("0.00000001")


@dataclass(frozen=True)
class Trade:
    id: str
    side: str
    order_type: str
    amount: Decimal
    fill_price: Decimal
    fee_quote: Decimal
    filled_at: datetime


@dataclass(frozen=True)
class OpenOrder:
    id: str
    side: str
    price: Decimal
    amount: Decimal
    created_at: datetime


@dataclass(frozen=True)
class Position:
    amount: Decimal
    avg_cost_jpy: Decimal | None
    opened_at: datetime | None
    age_days: float | None

    @property
    def cost_basis_jpy(self) -> Decimal:
        if self.avg_cost_jpy is None:
            return Decimal(0)
        return self.amount * self.avg_cost_jpy


@dataclass(frozen=True)
class Ladder:
    step: int
    used_budget_jpy: Decimal
    last_fill_price_jpy: Decimal | None
    last_fill_at: datetime | None
    cooldown_until: datetime | None
    fills_today: int
    sold_in_round: Decimal


@dataclass(frozen=True)
class Account:
    initial_jpy: Decimal
    cash_total_jpy: Decimal
    cash_locked_jpy: Decimal
    cash_available_jpy: Decimal
    base_total: Decimal
    equity_jpy: Decimal

    @property
    def drawdown_pct(self) -> Decimal:
        if self.initial_jpy <= 0:
            return Decimal(0)
        return (self.equity_jpy - self.initial_jpy) / self.initial_jpy * Decimal(100)


@dataclass(frozen=True)
class State:
    position: Position
    ladder: Ladder
    account: Account
    pending_buy: tuple[OpenOrder, ...]
    pending_sell: tuple[OpenOrder, ...]
    realized_pnl_jpy: Decimal
    unrealized_pnl_jpy: Decimal
    position_mismatch: bool


def parse_trades(rows: Sequence[dict], pair: str, tz_name: str) -> tuple[Trade, ...]:
    trades = [
        Trade(
            id=str(row["id"]),
            side=str(row["side"]),
            order_type=str(row["type"]),
            amount=to_decimal(row["amount"]),
            fill_price=to_decimal(row["fillPrice"]),
            fee_quote=to_decimal(row.get("feeQuote", 0)),
            filled_at=timeutil.from_iso(str(row["filledAt"]), tz_name),
        )
        for row in rows
        if row.get("pair") == pair
    ]
    return tuple(sorted(trades, key=lambda t: t.filled_at))


def parse_open_orders(rows: Sequence[dict], pair: str, tz_name: str) -> tuple[OpenOrder, ...]:
    return tuple(
        OpenOrder(
            id=str(row["id"]),
            side=str(row["side"]),
            price=to_decimal(row["price"]),
            amount=to_decimal(row["amount"]),
            created_at=timeutil.from_iso(str(row["createdAt"]), tz_name),
        )
        for row in rows
        if row.get("pair") == pair
    )


def current_round(trades: Sequence[Trade]) -> tuple[Trade, ...]:
    """いまの建玉ラウンドの約定だけを取り出す。

    建玉がゼロになった時点でラウンドは終わり、次の買いから数え直す。
    """
    position = Decimal(0)
    round_trades: list[Trade] = []
    for trade in trades:
        if position <= DUST and trade.side == "buy":
            round_trades = []
        round_trades.append(trade)
        position += trade.amount if trade.side == "buy" else -trade.amount
        if position <= DUST:
            position = Decimal(0)
            round_trades = []
    return tuple(round_trades)


def derive_ladder(
    trades: Sequence[Trade], config: Config, now: datetime
) -> Ladder:
    round_trades = current_round(trades)
    buys = [t for t in round_trades if t.side == "buy"]
    sells = [t for t in round_trades if t.side == "sell"]
    last_buy = buys[-1] if buys else None
    cooldown = (
        last_buy.filled_at + timedelta(hours=config.cooldown_hours_after_fill)
        if last_buy
        else None
    )
    today = timeutil.date_key(now)
    fills_today = sum(
        1
        for t in trades
        if t.side == "buy" and timeutil.date_key(t.filled_at) == today
    )
    used = sum((t.amount * t.fill_price + t.fee_quote for t in buys), Decimal(0))
    return Ladder(
        step=len(buys),
        used_budget_jpy=used,
        last_fill_price_jpy=last_buy.fill_price if last_buy else None,
        last_fill_at=last_buy.filled_at if last_buy else None,
        cooldown_until=cooldown,
        fills_today=fills_today,
        sold_in_round=sum((t.amount for t in sells), Decimal(0)),
    )


def derive_position(
    trades: Sequence[Trade], pnl_row: dict | None, now: datetime
) -> Position:
    round_trades = current_round(trades)
    opened_at = round_trades[0].filled_at if round_trades else None
    amount = to_decimal(pnl_row["position"]) if pnl_row else Decimal(0)
    avg_cost = to_decimal(pnl_row["avgCost"]) if pnl_row and amount > DUST else None
    age_days = (now - opened_at).total_seconds() / 86400 if opened_at and amount > DUST else None
    return Position(
        amount=amount if amount > DUST else Decimal(0),
        avg_cost_jpy=avg_cost,
        opened_at=opened_at if amount > DUST else None,
        age_days=age_days,
    )


def derive(
    config: Config,
    now: datetime,
    last_price: Decimal,
    assets_rows: Sequence[dict],
    pnl_report: dict,
    order_rows: Sequence[dict],
    history_rows: Sequence[dict],
) -> State:
    """CLI の実測から現在状態を組み立てる。"""
    trades = parse_trades(history_rows, config.pair, config.timezone)
    orders = parse_open_orders(order_rows, config.pair, config.timezone)

    per_pair = pnl_report.get("perPair") if isinstance(pnl_report, dict) else None
    # 建玉ゼロかつ実現損益ゼロのペアは出力されない。無い＝建玉なし。
    pnl_row = per_pair.get(config.pair) if isinstance(per_pair, dict) else None

    quote = config.pair.split("_")[-1]
    base = config.pair.split("_")[0]
    cash_total = Decimal(0)
    cash_locked = Decimal(0)
    cash_available = Decimal(0)
    base_total = Decimal(0)
    for row in assets_rows:
        if row.get("asset") == quote:
            cash_total = to_decimal(row["total"])
            cash_locked = to_decimal(row["locked"])
            cash_available = to_decimal(row["available"])
        elif row.get("asset") == base:
            base_total = to_decimal(row["total"])

    position = derive_position(trades, pnl_row, now)
    ladder = derive_ladder(trades, config, now)
    account = Account(
        initial_jpy=to_decimal(config.initial_jpy),
        cash_total_jpy=cash_total,
        cash_locked_jpy=cash_locked,
        cash_available_jpy=cash_available,
        base_total=base_total,
        equity_jpy=cash_total + position.amount * last_price,
    )
    return State(
        position=position,
        ladder=ladder,
        account=account,
        pending_buy=tuple(o for o in orders if o.side == "buy"),
        pending_sell=tuple(o for o in orders if o.side == "sell"),
        realized_pnl_jpy=to_decimal(pnl_row["realizedPnl"]) if pnl_row else Decimal(0),
        unrealized_pnl_jpy=to_decimal(pnl_row["unrealizedPnl"]) if pnl_row else Decimal(0),
        position_mismatch=abs(base_total - position.amount) > DUST,
    )


# 表示用の気分。personality.md「状態別のセリフ」の見出しに対応する。
MOOD_BY_STATE = {
    "NOT_INITIALIZED": "待機",
    "IDLE": "待機",
    "LADDERING": "満足",
    "HOLDING": "静観",
    "HIBERNATING": "冬眠",
    "HALTED": "停止",
}


def count_closed_positions(trades: Sequence[Trade]) -> int:
    """建玉がゼロへ戻った回数。"""
    position = Decimal(0)
    closed = 0
    for trade in trades:
        position += trade.amount if trade.side == "buy" else -trade.amount
        if position <= DUST and trade.side == "sell":
            position = Decimal(0)
            closed += 1
    return closed


def _num(value: Decimal | None, digits: int | None = None) -> float | None:
    """YAML へ書く数値。観測できなかった値は None のままにする。"""
    if value is None:
        return None
    return round(float(value), digits) if digits is not None else float(value)


def build_status(
    config: Config,
    now: datetime,
    run_id: str,
    state_label: str,
    market: object,
    state: State | None,
    trades: Sequence[Trade],
    action: str | None,
    reason: str | None,
    price_source: str | None,
) -> dict:
    """status.yaml の内容を組み立てる。状態の正ではなくスナップショット。"""
    document: dict = {
        "schema_version": 1,
        "updated_at": timeutil.to_iso(now),
        "run_id": run_id,
        "state": state_label,
        "mood": MOOD_BY_STATE.get(state_label, "待機"),
        "account": {
            "initial_jpy": float(config.initial_jpy),
            "cash_jpy": None,
            "equity_jpy": None,
            "drawdown_pct": None,
        },
        "market": {
            "pair": config.pair,
            "last_price": None,
            "anchor_price": None,
            "drop_from_anchor_pct": None,
            "source": price_source,
            "fetched_at": None,
        },
        "position": {
            "amount": 0,
            "avg_cost_jpy": None,
            "cost_basis_jpy": 0,
            "unrealized_pnl_jpy": None,
            "unrealized_pnl_pct": None,
            "opened_at": None,
            "age_days": None,
        },
        "ladder": {
            "step": 0,
            "max_steps": config.ladder_max_steps,
            "used_budget_jpy": 0,
            "total_budget_jpy": float(config.ladder_total_budget_jpy),
            "last_fill_price_jpy": None,
            "last_fill_at": None,
            "cooldown_until": None,
        },
        "orders": {"pending_buy": [], "pending_sell": []},
        "last_decision": {
            "action": action,
            "reason": reason,
            "at": timeutil.to_iso(now),
        },
        "counters": {
            "fills_today": 0,
            "total_fills": len(trades),
            "closed_positions": count_closed_positions(trades),
            "realized_pnl_jpy": 0,
        },
        "notes": None,
    }

    if market is not None:
        document["market"].update(
            {
                "last_price": _num(getattr(market, "last", None)),
                "anchor_price": _num(getattr(market, "anchor", None)),
                "drop_from_anchor_pct": _num(
                    getattr(market, "drop_from_anchor_pct", None), 2
                ),
                "fetched_at": timeutil.to_iso(market.observed_at),
            }
        )

    if state is None:
        return document

    unrealized_pct = None
    if state.position.cost_basis_jpy > 0:
        unrealized_pct = _num(
            state.unrealized_pnl_jpy / state.position.cost_basis_jpy * Decimal(100), 2
        )

    document["account"].update(
        {
            "cash_jpy": _num(state.account.cash_total_jpy),
            "equity_jpy": _num(state.account.equity_jpy),
            "drawdown_pct": _num(state.account.drawdown_pct, 2),
        }
    )
    document["position"].update(
        {
            "amount": _num(state.position.amount),
            "avg_cost_jpy": _num(state.position.avg_cost_jpy),
            "cost_basis_jpy": _num(state.position.cost_basis_jpy),
            "unrealized_pnl_jpy": _num(state.unrealized_pnl_jpy),
            "unrealized_pnl_pct": unrealized_pct,
            "opened_at": timeutil.to_iso(state.position.opened_at)
            if state.position.opened_at
            else None,
            "age_days": _num(to_decimal(state.position.age_days), 2)
            if state.position.age_days is not None
            else None,
        }
    )
    document["ladder"].update(
        {
            "step": state.ladder.step,
            "used_budget_jpy": _num(state.ladder.used_budget_jpy),
            "last_fill_price_jpy": _num(state.ladder.last_fill_price_jpy),
            "last_fill_at": timeutil.to_iso(state.ladder.last_fill_at)
            if state.ladder.last_fill_at
            else None,
            "cooldown_until": timeutil.to_iso(state.ladder.cooldown_until)
            if state.ladder.cooldown_until
            else None,
        }
    )
    document["orders"] = {
        "pending_buy": [
            {
                "order_id": o.id,
                "price": _num(o.price),
                "amount": _num(o.amount),
                "placed_at": timeutil.to_iso(o.created_at),
            }
            for o in state.pending_buy
        ],
        "pending_sell": [
            {
                "order_id": o.id,
                "price": _num(o.price),
                "amount": _num(o.amount),
                "placed_at": timeutil.to_iso(o.created_at),
            }
            for o in state.pending_sell
        ],
    }
    document["counters"].update(
        {
            "fills_today": state.ladder.fills_today,
            "realized_pnl_jpy": _num(state.realized_pnl_jpy),
        }
    )
    return document


def write_status(document: dict, path: Path | str | None = None) -> Path:
    """status.yaml を書き出す。実行が成功したときだけ呼ぶ。"""
    target = Path(path) if path is not None else REPO_ROOT / "status.yaml"
    header = (
        "# ナンピノニクスの現在状態\n"
        "#\n"
        "# 本ファイルは状態の正ではありません。判断に使う状態は毎回 bitbank paper の\n"
        "# 実測から導出します（memory-policy.md）。本ファイルは人間と HTML ステータス\n"
        "# 画面が読むためのスナップショットです。\n"
        "#\n"
        "# 15分ごとの実行で毎回上書きされます。\n"
        "# null は「まだ観測していない」を意味し、推測値を入れてはいけません。\n"
    )
    body = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
    target.write_text(f"{header}\n{body}", encoding="utf-8")
    return target
